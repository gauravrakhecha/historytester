"""v7.1 Task 3: pure position-sizing + stop primitives (quantdesk.risk)."""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.risk import sizing, stops  # noqa: E402

DATA = r"D:\simpleton\historydownloader\data\RELIANCE\RELIANCE_15Min_2022-06-01_to_2026-06-18.csv"


def _cfg(**rm_kwargs):
    from quantdesk.strategy.models import RiskManagement, StrategyConfig
    return StrategyConfig(
        indicators=[{"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}},
                    {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 30}}],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
        pos_size_type=rm_kwargs.pop("pos_size_type", "Fixed Value per Trade (₹) (Default)"),
        risk_management=RiskManagement(**rm_kwargs),
    )


# ------------------------------------------------------------------------------- sizing
def test_round_to_lot():
    assert sizing.round_to_lot(137, 25) == 125          # floor to lot multiple
    assert sizing.round_to_lot(10, 25) == 25            # never below one lot
    assert sizing.round_to_lot(0, 25) == 25
    assert sizing.round_to_lot(-5, 1) == 1
    assert sizing.round_to_lot(float("nan"), 1) == 1
    assert sizing.round_to_lot(50, 1) == 50


def test_fixed_and_percent_equity():
    assert sizing.fixed_value_qty(50000, 1000) == 50
    assert sizing.percent_equity_qty(100000, 50, 1000) == 50
    assert sizing.percent_equity_qty(100000, 0, 1000) == 1     # degenerate -> one lot
    assert sizing.fixed_value_qty(50000, 0) == 1               # bad price -> one lot


def test_risk_pct_qty_sizes_to_stop_distance():
    # risk 1% of 100k = ₹1000; stop 20 away -> 50 units (loss on stop == ₹1000)
    assert sizing.risk_pct_qty(100000, 1.0, 20.0) == 50
    # tighter stop -> bigger size (same ₹ risk)
    assert sizing.risk_pct_qty(100000, 1.0, 10.0) == 100
    # lot rounding
    assert sizing.risk_pct_qty(100000, 1.0, 20.0, lot_size=15) == 45   # 50 -> floor to 45
    # defensive
    assert sizing.risk_pct_qty(100000, 1.0, 0.0) == 1


def test_atr_risk_qty():
    # stop_distance = 2 * ATR(10) = 20 -> same as risk_pct_qty(...,20)
    assert sizing.atr_risk_qty(100000, 1.0, 10.0, 2.0) == 50


def test_kelly():
    assert abs(sizing.kelly_fraction(0.6, 2.0) - 0.4) < 1e-9      # 0.6 - 0.4/2
    assert abs(sizing.half_kelly_fraction(0.6, 2.0) - 0.2) < 1e-9
    assert sizing.kelly_fraction(0.4, 1.0) == 0.0                  # non-positive edge -> 0
    assert sizing.kelly_fraction(1.5, 2.0) == 0.0                  # invalid win rate -> 0
    assert 0.0 <= sizing.kelly_fraction(0.9, 3.0) <= 1.0


# -------------------------------------------------------------------------------- stops
def test_static_sl_tp_long_and_short():
    sl, tp = stops.static_sl_tp(100.0, 2.0, 5.0, is_short=False)
    assert abs(sl - 98.0) < 1e-9 and abs(tp - 105.0) < 1e-9
    sl, tp = stops.static_sl_tp(100.0, 2.0, 5.0, is_short=True)
    assert abs(sl - 102.0) < 1e-9 and abs(tp - 95.0) < 1e-9
    assert stops.static_sl_tp(100.0, 0, 0) == (None, None)         # both disabled


def test_atr_and_breakeven_and_trailing():
    assert abs(stops.atr_stop(100.0, 5.0, 2.0, is_short=False) - 90.0) < 1e-9
    assert abs(stops.atr_stop(100.0, 5.0, 2.0, is_short=True) - 110.0) < 1e-9
    assert stops.atr_stop(100.0, 0.0, 2.0) is None
    assert stops.breakeven_stop(100.0) == 100.0
    assert abs(stops.trailing_stop(110.0, 2.0, is_short=False) - 107.8) < 1e-9
    assert abs(stops.trailing_stop(90.0, 2.0, is_short=True) - 91.8) < 1e-9
    assert stops.trailing_stop(110.0, 0) is None


# ------------------------------------------------------- codegen wiring (compiles through AST gate)
def test_risk_pct_sizing_codegen_compiles_and_runs():
    from backtesting import Backtest
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    cfg = _cfg(pos_size_type="Risk % of Equity per Trade",
               mode="Static % Stop Loss / Take Profit", sl_pct=2.0, tp_pct=5.0, risk_pct=1.0)
    code = build_strategy_code(cfg)
    assert "risk_pct_qty(" in code                      # emits the pure helper, not inline math
    stats = Backtest(load_indexed(DATA).iloc[-3000:], compile_strategy(code),
                     cash=100000, commission=0.0005, finalize_trades=True).run()
    assert "# Trades" in stats.index and int(stats["# Trades"]) > 0


def test_max_dd_circuit_breaker_codegen_compiles_and_runs():
    from backtesting import Backtest
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    cfg = _cfg(max_dd_halt_pct=15.0)
    code = build_strategy_code(cfg)
    assert "_peak_equity" in code and "circuit breaker" in code.lower()
    stats = Backtest(load_indexed(DATA).iloc[-3000:], compile_strategy(code),
                     cash=100000, commission=0.0005, finalize_trades=True).run()
    assert "# Trades" in stats.index


def test_default_sizing_unchanged_when_risk_features_off():
    """No risk-% / no breaker -> generated code path is the legacy fixed-size one (parity)."""
    from quantdesk.strategy.codegen import build_strategy_code
    code = build_strategy_code(_cfg())
    assert "risk_pct_qty(" not in code and "_peak_equity" not in code
    assert "_risk_atr" not in code and "entry_bar" not in code
    # v7.8 pt.3 features are all opt-in — none must leak into a default strategy
    assert "breakeven_stop(" not in code and "_scaled_out" not in code
    assert "closed_trades" not in code and "kelly_fraction(" not in code


# ----------------------------------------------------- Task 3 pt.2: ATR stops + time stops
def _run(code):
    from backtesting import Backtest
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.compiler import compile_strategy
    return Backtest(load_indexed(DATA).iloc[-3000:], compile_strategy(code),
                    cash=100000, commission=0.0005, finalize_trades=True).run()


def test_atr_stop_codegen_compiles_and_runs():
    from quantdesk.strategy.codegen import build_strategy_code
    code = build_strategy_code(_cfg(mode="ATR Stop Loss (Volatility)", atr_period=14, atr_stop_mult=2.0))
    assert "_risk_atr" in code and "atr_stop(" in code
    assert int(_run(code)["# Trades"]) > 0


def test_atr_risk_sizing_codegen():
    from quantdesk.strategy.codegen import build_strategy_code
    code = build_strategy_code(_cfg(pos_size_type="Risk % of Equity per Trade",
                                    mode="ATR Stop Loss (Volatility)", risk_pct=1.0, atr_stop_mult=2.0))
    assert "atr_risk_qty(" in code                 # sizing uses the ATR-derived stop distance
    assert int(_run(code)["# Trades"]) > 0


def test_time_stop_codegen_caps_holding_period():
    from quantdesk.strategy.codegen import build_strategy_code
    code = build_strategy_code(_cfg(max_bars_in_trade=20))
    assert "entry_bar" in code and "Time stop" in code
    stats = _run(code)
    assert int(stats["# Trades"]) > 0
    # no trade is held longer than the cap (a couple bars of slack for the close bar)
    dur = stats["_trades"]["ExitBar"] - stats["_trades"]["EntryBar"]
    assert dur.max() <= 22


# ------------------------------------------- Task 3 pt.3: breakeven / partial exit / Kelly
def test_breakeven_stop_codegen_compiles_and_runs():
    """Breakeven emits a stop-to-entry ratchet and measurably alters exits vs the same SL/TP alone."""
    from quantdesk.strategy.codegen import build_strategy_code
    base = _cfg(mode="Static % Stop Loss / Take Profit", sl_pct=2.0, tp_pct=8.0)
    be = _cfg(mode="Static % Stop Loss / Take Profit", sl_pct=2.0, tp_pct=8.0, breakeven_trigger_pct=1.5)
    be_code = build_strategy_code(be)
    assert "breakeven_stop(" in be_code and "_be_t.sl = _be" in be_code
    assert "breakeven_stop(" not in build_strategy_code(base)   # gated off by default
    base_stats, be_stats = _run(build_strategy_code(base)), _run(be_code)
    assert int(be_stats["# Trades"]) > 0
    # relocating the stop to breakeven changes at least one exit -> a different net return
    assert abs(float(be_stats["Return [%]"]) - float(base_stats["Return [%]"])) > 1e-9


def test_partial_exit_scales_out_and_adds_trade_records():
    """Scale-out closes part of the position at the first target -> extra closed-trade records."""
    from quantdesk.strategy.codegen import build_strategy_code
    base = _cfg(mode="Static % Stop Loss / Take Profit", sl_pct=2.0, tp_pct=8.0)
    pe = _cfg(mode="Static % Stop Loss / Take Profit", sl_pct=2.0, tp_pct=8.0,
              partial_exit_pct=50.0, partial_trigger_pct=3.0)
    pe_code = build_strategy_code(pe)
    assert "self.position.close(portion=" in pe_code and "_scaled_out" in pe_code
    base_n, pe_n = int(_run(build_strategy_code(base))["# Trades"]), int(_run(pe_code)["# Trades"])
    assert pe_n > base_n     # each scale-out splits one position into two closed-trade rows


def test_kelly_sizing_codegen_compiles_and_runs():
    from quantdesk.strategy.codegen import build_strategy_code
    half = build_strategy_code(_cfg(pos_size_type="Half-Kelly (Adaptive)"))
    full = build_strategy_code(_cfg(pos_size_type="Full Kelly (Adaptive)"))
    assert "half_kelly_fraction(" in half and "closed_trades" in half
    assert "kelly_fraction(" in full and "half_kelly_fraction(" not in full
    assert int(_run(half)["# Trades"]) > 0 and int(_run(full)["# Trades"]) > 0


def test_kelly_sizes_up_on_a_positive_edge():
    """On a winning strategy with mixed wins/losses, adaptive Kelly deploys > 1 lot post-warmup."""
    import numpy as np
    import pandas as pd
    from backtesting import Backtest
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    from quantdesk.strategy.models import RiskManagement, StrategyConfig

    n = 6000
    idx = pd.date_range("2022-01-03 09:15", periods=n, freq="15min")
    close = np.linspace(100, 260, n) + 8 * np.sin(np.linspace(0, 260, n)) \
        + np.random.default_rng(0).normal(0, 2.5, n)   # net-up trend, some losers -> real Kelly edge
    df = pd.DataFrame({"Open": close - 0.2, "High": close + 1.0, "Low": close - 1.0,
                       "Close": close, "Volume": np.full(n, 1000)}, index=idx)
    cfg = StrategyConfig(
        indicators=[{"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 5}},
                    {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 20}}],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
        pos_size_type="Half-Kelly (Adaptive)", risk_management=RiskManagement(strat_dir="Long Only"),
    )
    stats = Backtest(df, compile_strategy(build_strategy_code(cfg)), cash=1_000_000,
                     commission=0.0005, finalize_trades=True).run()
    sizes = stats["_trades"]["Size"].to_numpy()
    assert len(sizes) > 20                       # enough closed trades to pass the warmup floor
    assert sizes[20:].max() > 1                  # Kelly engaged and sized up from the minimum lot
