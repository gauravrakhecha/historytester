"""Regression tests for the multi-agent audit fixes (v5.1)."""
import sys
import warnings
from pathlib import Path

import glob  # noqa: E402
import os  # noqa: E402

import numpy as np
import pandas as pd
import pytest

from tests.conftest import SAMPLE_CSV, SAMPLE_DIR, requires_sample

pytestmark = requires_sample

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_REL = SAMPLE_CSV
_ONEMIN = next(iter(glob.glob(os.path.join(SAMPLE_DIR, "**", "*1Min*.csv"), recursive=True)
                    + glob.glob(os.path.join(SAMPLE_DIR, "**", "*1Min*.parquet"), recursive=True)), None)


def _ema_cfg(**rm):
    from quantdesk.strategy.models import RiskManagement, StrategyConfig
    return StrategyConfig(
        indicators=[
            {"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 30}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
        risk_management=RiskManagement(**rm) if rm else RiskManagement(),
    )


def test_preset_sizing_roundtrip():
    from quantdesk.strategy.models import StrategyConfig
    cfg = _ema_cfg()
    cfg.pos_size_type = "Compound 100% Equity"
    cfg.fixed_pos_value = 12345.0
    cfg.lot_size = 50
    back = StrategyConfig.from_dict(cfg.to_dict())
    assert back.pos_size_type == "Compound 100% Equity"
    assert back.fixed_pos_value == 12345.0
    assert back.lot_size == 50


def test_yearly_drawdown_is_negative_worst():
    from quantdesk.backtest.metrics import yearly_performance
    idx = pd.date_range("2022-01-01", periods=300, freq="D")
    eq = pd.Series(np.linspace(100000, 110000, 300), index=idx)
    dd = pd.Series(0.0, index=idx)
    dd.iloc[100:110] = [0.05, 0.10, 0.20, 0.15, 0.08, 0.03, 0.0, 0.0, 0.0, 0.0]  # worst 20%
    yp = yearly_performance(pd.DataFrame({"Equity": eq, "DrawdownPct": dd}))
    assert yp.loc["2022", "Max Drawdown [%]"] == pytest.approx(-20.0, abs=0.01)


def test_resample_vectorized_matches_perday():
    from quantdesk.data.loader import load_ohlcv
    from quantdesk.data.resample import resample_dataframe
    assert _ONEMIN, "no 1-min file found"
    df = load_ohlcv(_ONEMIN)
    out = resample_dataframe(df, 15)
    # reference: explicit per-day origin='start' resample
    ref_parts = []
    d = df.set_index("Timestamp")
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    agg = {k: v for k, v in agg.items() if k in d.columns}
    for _, day in d.groupby(d.index.normalize()):
        r = day.resample("15min", origin="start").agg(agg).dropna(how="any")
        if not r.empty:
            ref_parts.append(r)
    ref = pd.concat(ref_parts).reset_index()
    assert len(out) == len(ref)
    assert np.allclose(out["Close"].values, ref["Close"].values)
    assert np.allclose(out["Volume"].values, ref["Volume"].values)
    # no bar bridges the overnight gap: every bin's bars share one calendar day (implicit in build)
    assert (out["Timestamp"].dt.date.nunique() == d.index.normalize().nunique())


def test_codegen_short_trailing_ratchets_and_tighten_zone_wired():
    from quantdesk.strategy.codegen import build_strategy_code
    code = build_strategy_code(_ema_cfg(mode="Trailing Stop Loss (%)", trail_pct=2.0, strat_dir="Short Only"))
    # short fresh-entry guard must be >= 0 (signed size), not <= 0
    assert "_prev_position_size', 0) >= 0" in code
    # tighten-zone must write/read the SAME attribute (self._zone_tight_sl)
    from quantdesk.strategy.models import StrategyConfig, RiskManagement
    zcfg = StrategyConfig(
        indicators=[{"name": "RSI1", "type": "RSI", "source": "Close", "params": {"period": 14}}],
        buy_rules=[{"logic": "IF", "var1": "Close", "operator": ">", "var2": "RSI1", "is_num": False}],
        range_zones=[{"indicator": "RSI1", "min_val": 60, "max_val": 80, "label": "hot",
                      "action": "Tighten Trailing Stop", "tight_pct": 1.0}],
        risk_management=RiskManagement(mode="Trailing Stop Loss (%)", trail_pct=2.0),
    )
    zc = build_strategy_code(zcfg)
    assert "self._zone_tight_sl = self.zone_0_tight_pct" in zc
    assert "self._zone_tight_sl = 0.0" in zc


def test_short_trailing_runs_and_trades():
    from backtesting import Backtest
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    code = build_strategy_code(_ema_cfg(mode="Trailing Stop Loss (%)", trail_pct=2.0, strat_dir="Short Only"))
    stats = Backtest(load_indexed(DATA_REL), compile_strategy(code), cash=100000, commission=0.0005, finalize_trades=True).run()
    assert stats["# Trades"] > 0


def test_ml_mine_handles_real_trade_columns():
    """ML finder must read 'ReturnPct' (per-trade), not 'Return [%]' (overall) — no KeyError."""
    from backtesting import Backtest
    from quantdesk.backtest.metrics import enrich_trades
    from quantdesk.data.loader import load_indexed
    from quantdesk.ml.patterns import mine_patterns
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    code = build_strategy_code(_ema_cfg())
    stats = Backtest(load_indexed(DATA_REL), compile_strategy(code), cash=100000, commission=0.0005, finalize_trades=True).run()
    trades = enrich_trades(stats["_trades"].copy(), stats["_strategy"], ["EMA_f", "Close"])
    out = mine_patterns(trades, ["EMA_f", "Close"], "Win (Return > 0)", min_trades=20)
    assert out["error"] is None, out["error"]
    assert out["baseline_win_rate"] is not None and 0 <= out["baseline_win_rate"] <= 100


def test_optuna_handles_integer_float_list():
    """A float list of whole numbers for an int param must use suggest_int (no -999 mask / crash)."""
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    code = build_strategy_code(_ema_cfg())
    df = load_indexed(DATA_REL)
    stats, info = engine.run_single(
        df, code, 100000, 0.0005, 1.0,
        optimize_mode="Optuna (AI Bayesian Search - Recommended)",
        opt_kwargs={"EMA_f_period": [5.0, 10.0, 15.0], "EMA_s_period": [20.0, 30.0, 40.0]},
        maximize="Return [%]", n_trials=4,
    )
    bp = info["best_params"]
    assert float(bp["EMA_f_period"]).is_integer() and float(bp["EMA_s_period"]).is_integer()


def test_india_charges_worked_example():
    from quantdesk.costs.india import compute_trade_charges
    c = compute_trade_charges("Equity Intraday", 100000, 101000)
    assert c["stt"] == pytest.approx(25.25, abs=0.01)
    assert c["brokerage"] == pytest.approx(40.0, abs=0.01)
    assert c["total"] == pytest.approx(82.73, abs=0.1)
