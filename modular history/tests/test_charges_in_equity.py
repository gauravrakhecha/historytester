"""v7.1 Task 2: the real India charges are now applied INSIDE the simulated equity curve.

Golden-number check that the in-engine per-leg fee model reconciles exactly to the itemised
charge calculator, plus an integration check that a charge-accurate run's metrics are actually
net of charges (Return < the zero-cost gross)."""
import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.costs.india import SEGMENTS, compute_trade_charges, make_fee_model  # noqa: E402

DATA = r"D:\simpleton\historydownloader\data\RELIANCE\RELIANCE_15Min_2022-06-01_to_2026-06-18.csv"


def _ema_code():
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.models import StrategyConfig
    cfg = StrategyConfig(
        indicators=[{"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}},
                    {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 30}}],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
    )
    return build_strategy_code(cfg)


# ---------------------------------------------------------------- golden-number reconciliation
@pytest.mark.parametrize("segment", SEGMENTS)
def test_fee_model_roundtrip_matches_itemised_calculator(segment):
    """Two legs of the in-engine fee at equal notional must equal the exact round-trip charge."""
    qty, price = 100, 1000.0  # notional 100,000 per leg
    fee = make_fee_model(segment)
    per_leg = fee(qty, price)
    roundtrip = compute_trade_charges(segment, qty * price, qty * price)["total"]
    assert abs(per_leg * 2 - roundtrip) < 0.05, (segment, per_leg * 2, roundtrip)
    # sign of order_size (buy/sell) must not change the leg fee (backtesting.py passes position sign)
    assert fee(-qty, price) == per_leg


def test_fee_model_delivery_hand_computed():
    # Equity Delivery, 100 sh @ ₹1000 (₹100,000/leg): per-leg = STT(0.1%) + stamp(0.015%/2) +
    # exch(0.00297%) + sebi(0.0001%) + GST 18% on (exch+sebi); brokerage 0.
    fee = make_fee_model("Equity Delivery")(100, 1000.0)
    assert abs(fee - 111.1226) < 0.01, fee


# ---------------------------------------------------------------- integration: charges in the curve
def test_charges_reduce_engine_returns():
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    df = load_indexed(DATA).iloc[-4000:]
    code = _ema_code()
    gross = engine.make_backtest(df, code, 100000, 0.0, 1.0).run()                       # zero cost
    net = engine.make_backtest(df, code, 100000, 0.0, 1.0,
                               segment="Equity Intraday", slippage=0.0).run()             # charges in-engine
    assert int(gross["# Trades"]) > 0
    assert int(net["# Trades"]) == int(gross["# Trades"])           # costs don't change fixed-size signals
    assert net["Return [%]"] < gross["Return [%]"]                  # charges genuinely reduce the return
    # the engine PnL is now net of charges
    assert float(net["_trades"]["PnL"].sum()) < float(gross["_trades"]["PnL"].sum())


def test_run_single_segment_matches_make_backtest_segment():
    """run_single(..., segment=) must feed the fee model through — headline == direct make_backtest."""
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    df = load_indexed(DATA).iloc[-4000:]
    code = _ema_code()
    direct = engine.make_backtest(df, code, 100000, 0.0, 1.0, segment="Futures", slippage=0.0).run()
    via, info = engine.run_single(df, code, 100000, 0.0, 1.0, segment="Futures", slippage=0.0)
    assert info is None
    assert abs(float(via["Return [%]"]) - float(direct["Return [%]"])) < 1e-6


def test_no_segment_is_backward_compatible():
    """Without a segment, the legacy flat-commission path is unchanged (default for tests/callers)."""
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    df = load_indexed(DATA).iloc[-3000:]
    flat = engine.make_backtest(df, _ema_code(), 100000, 0.0005, 1.0).run()
    stats, _ = engine.run_single(df, _ema_code(), 100000, 0.0005, 1.0)
    assert abs(float(flat["Return [%]"]) - float(stats["Return [%]"])) < 1e-6
