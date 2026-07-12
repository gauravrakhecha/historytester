"""v7 Phase A: normalized cross-asset feature library (new indicator types)."""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import ta

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.strategy import indicators as I  # noqa: E402
from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.compiler import compile_strategy  # noqa: E402
from quantdesk.strategy.models import StrategyConfig, classify_columns  # noqa: E402
from quantdesk.data.loader import load_indexed  # noqa: E402
from tests.conftest import SAMPLE_CSV, SAMPLE_DIR, requires_sample

pytestmark = requires_sample

DATA = SAMPLE_CSV


def _df():
    return load_indexed(DATA).iloc[-3000:]


def test_helpers_match_formulas():
    df = _df()
    close, vol = df["Close"].values, df["Volume"].values
    high, low = df["High"].values, df["Low"].values

    # ratio to SMA
    exp = (pd.Series(vol, dtype="float64") / ta.trend.sma_indicator(pd.Series(vol, dtype="float64"), 20)).values
    assert np.allclose(I.ratio_to_sma_vec(vol, 20), exp, equal_nan=True)

    # z-score
    s = pd.Series(close, dtype="float64")
    exp = ((s - s.rolling(50).mean()) / s.rolling(50).std(ddof=0)).values
    assert np.allclose(I.zscore_vec(close, 50), exp, equal_nan=True)

    # n-bar return
    assert np.allclose(I.ret_n_vec(close, 5), (s / s.shift(5) - 1.0).values, equal_nan=True)

    # percentile rank in [0,1]
    pr = I.pctile_rank_vec(close, 50)
    pr_valid = pr[~np.isnan(pr)]
    assert pr_valid.min() >= 0.0 and pr_valid.max() <= 1.0

    # distance from MA and slope, in ATR units (finite, unit-free)
    d = I.dist_from_ma_atr_vec(close, high, low, close, 20, 14)
    sl = I.ma_slope_atr_vec(close, high, low, close, 20, 5, 14)
    assert np.isfinite(d[~np.isnan(d)]).all() and np.isfinite(sl[~np.isnan(sl)]).all()


def test_new_types_are_panels():
    inds = [{"name": "ZC", "type": "Z-Score", "source": "Close", "params": {"period": 50}}]
    assert classify_columns(inds)["ZC"]["placement"] == "panel"


def test_codegen_compile_backtest_with_normalized_features():
    from backtesting import Backtest
    cfg = StrategyConfig(
        indicators=[
            {"name": "VolR", "type": "Ratio to SMA", "source": "Volume", "params": {"period": 20}},
            {"name": "ZC", "type": "Z-Score", "source": "Close", "params": {"period": 50}},
            {"name": "Slope", "type": "MA Slope (ATR)", "source": "Close", "params": {"period": 20, "lookback": 5, "atr_period": 14}},
        ],
        buy_rules=[
            {"logic": "IF", "var1": "ZC", "operator": "<", "var2": -1.0, "is_num": True},
            {"logic": "AND", "var1": "VolR", "operator": ">", "var2": 1.2, "is_num": True},
            {"logic": "AND", "var1": "Slope", "operator": ">", "var2": 0.0, "is_num": True},
        ],
        sell_rules=[{"logic": "IF", "var1": "ZC", "operator": ">", "var2": 1.0, "is_num": True}],
    )
    cls = compile_strategy(build_strategy_code(cfg))
    stats = Backtest(_df(), cls, cash=100000, commission=0.0005, finalize_trades=True).run()
    assert "# Trades" in stats.index  # ran without error


def test_features_are_cross_asset_comparable():
    """Same feature on a ~1300 series and a ~25000 series yields comparable ranges (the whole point)."""
    df = _df()
    low_price = df["Close"].values                       # RELIANCE ~1300
    high_price = df["Close"].values / df["Close"].mean() * 25000.0  # rescaled to ~NIFTY 25000

    z_lo = I.zscore_vec(low_price, 50)
    z_hi = I.zscore_vec(high_price, 50)
    # z-score is scale-invariant -> essentially identical regardless of absolute price level
    m = ~np.isnan(z_lo) & ~np.isnan(z_hi)
    assert np.allclose(z_lo[m], z_hi[m], atol=1e-6)

    r_lo = I.ret_n_vec(low_price, 5)
    r_hi = I.ret_n_vec(high_price, 5)
    m2 = ~np.isnan(r_lo) & ~np.isnan(r_hi)
    assert np.allclose(r_lo[m2], r_hi[m2], atol=1e-9)  # returns are scale-free
