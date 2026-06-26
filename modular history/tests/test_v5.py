"""Unit tests for the v5 NOW-bucket additions (run with pytest)."""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


def test_india_charges_intraday_worked_example():
    from quantdesk.costs.india import compute_trade_charges
    c = compute_trade_charges("Equity Intraday", 100000, 101000)
    assert abs(c["stt"] - 25.25) < 0.01          # 0.025% sell-side on 101000
    assert abs(c["stamp_duty"] - 3.0) < 0.01     # 0.003% buy-side on 100000
    assert abs(c["brokerage"] - 40.0) < 0.01     # ₹20 cap each leg
    assert abs(c["total"] - 82.73) < 0.05


def test_india_charges_segment_asymmetry():
    from quantdesk.costs.india import compute_trade_charges
    deliv = compute_trade_charges("Equity Delivery", 100000, 100000)
    fut = compute_trade_charges("Futures", 100000, 100000)
    assert deliv["brokerage"] == 0.0            # delivery is zero-brokerage
    assert deliv["stt"] > fut["stt"]            # delivery STT both sides > futures sell-only
    assert abs(fut["brokerage"] - 40.0) < 0.01  # ₹20 flat each leg


def test_resample_preserves_dividends_and_no_overnight_bar():
    from quantdesk.data.loader import load_ohlcv
    from quantdesk.data.resample import resample_dataframe
    import glob
    one_min = next(iter(glob.glob(r"D:\simpleton\historydownloader\data\**\*1Min*.*", recursive=True)), None)
    if not one_min:
        pytest.skip("no 1-min file available")
    df = load_ohlcv(one_min)
    df["Dividend"] = 0.0
    df.loc[df.index[5], "Dividend"] = 7.0
    r = resample_dataframe(df, 15)
    assert "Dividend" in r.columns and abs(r["Dividend"].sum() - 7.0) < 1e-6
    # no resampled bar spans two calendar days (per-session anchoring)
    assert (r["Timestamp"].dt.normalize().nunique() == r.groupby(r["Timestamp"].dt.normalize()).ngroups)


def test_quality_gate_blocks_bad_data():
    from quantdesk.config import QUALITY_BLOCK_THRESHOLD
    from quantdesk.data.quality import validate
    n = 200
    good = pd.DataFrame({"Timestamp": pd.date_range("2024-01-01", periods=n, freq="15min"),
                         "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000})
    assert validate(good)["quality_score"] >= 95
    bad = good.copy()
    bad["High"] = 90.0  # high < low everywhere
    assert validate(bad)["quality_score"] < QUALITY_BLOCK_THRESHOLD


def test_extract_scalar_metrics_has_buried():
    from backtesting import Backtest
    from quantdesk.backtest import metrics
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.compiler import compile_strategy
    stats = Backtest(load_indexed(DATA), compile_strategy(_ema_code()), cash=100000,
                     commission=0.0005, finalize_trades=True).run()
    m = metrics.extract_scalar_metrics(stats)
    for k in ["profit_factor", "expectancy_pct", "sqn", "buy_hold_return_pct"]:
        assert k in m
    assert len(metrics.equity_returns(stats)) > 10


def test_is_oos_split_returns_both():
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    stats, info = engine.run_single(
        load_indexed(DATA), _ema_code(), 100000, 0.0005, 1.0,
        optimize_mode="Grid Search (Exhaustive)",
        opt_kwargs={"EMA_f_period": range(5, 16, 5), "EMA_s_period": range(20, 41, 10)},
        maximize="Return [%]",
    )
    assert info and "is_stats" in info and "oos_stats" in info and not info.get("no_oos")
    assert "best_params" in info


def test_repro_hashes_stable():
    from quantdesk.core import reproducibility as repro
    assert repro.hash_text("x") == repro.hash_text("x")
    assert repro.hash_config({"a": 1, "b": 2}) == repro.hash_config({"b": 2, "a": 1})
    assert len(repro.code_version()) > 0


def test_charges_for_trades_aggregates():
    from quantdesk.costs.india import charges_for_trades
    trades = pd.DataFrame({"Size": [10, -5], "EntryPrice": [100.0, 200.0], "ExitPrice": [110.0, 190.0]})
    agg = charges_for_trades(trades, "Equity Intraday")
    assert agg["num_trades"] == 2 and agg["total"] > 0
