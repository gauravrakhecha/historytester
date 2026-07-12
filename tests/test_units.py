"""Unit tests for the modular backend (run with pytest)."""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tests.conftest import SAMPLE_CSV, SAMPLE_DIR, requires_sample

pytestmark = requires_sample

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = SAMPLE_CSV


def test_loader_and_resample():
    from quantdesk.data.loader import load_indexed, load_ohlcv
    from quantdesk.data.resample import resample_dataframe

    df = load_ohlcv(DATA)
    assert {"Open", "High", "Low", "Close", "Volume"}.issubset(df.columns)
    assert len(df) > 1000
    di = load_indexed(DATA)
    assert str(di.index.dtype).startswith("datetime")
    r60 = resample_dataframe(df, 60)
    assert 0 < len(r60) < len(df)


def test_quality_clean_data():
    """OHLC-clean, gap-free data scores high.

    NOTE: the real RELIANCE 15-minute fixture is OHLC-clean but has genuine
    multi-week calendar holes (recurring ~1-month Feb-March gaps), so the new
    calendar-aware gate legitimately scores it below 95 — the previous ">=95 on
    this file" assertion had merely encoded the old gate's blindness to missing
    days. The "clean => high score" property is therefore asserted on gap-free
    synthetic data; the real file is checked only for OHLC validity here, and its
    gaps are covered by test_quality_flags_missing_chunk.
    """
    from quantdesk.data.loader import load_ohlcv
    from quantdesk.data.quality import validate

    rep = validate(load_ohlcv(DATA))
    assert rep["bad_ohlc_count"] == 0

    clean = pd.DataFrame({
        "Timestamp": pd.date_range("2024-01-01 09:15", periods=500, freq="15min"),
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000,
    })
    crep = validate(clean)
    assert crep["bad_ohlc_count"] == 0
    assert crep["missing_trading_days"] == 0
    assert crep["quality_score"] >= 95


def test_quality_flags_missing_chunk():
    """A contiguous multi-week hole (a lost download chunk) is detected + penalised."""
    from quantdesk.data.quality import validate

    full = pd.DataFrame({
        "Timestamp": pd.bdate_range("2024-01-01", periods=180),
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000,
    })
    base = validate(full)
    assert base["missing_trading_days"] == 0

    ts = pd.to_datetime(full["Timestamp"])
    hole = (ts >= ts.iloc[60]) & (ts < ts.iloc[60] + pd.Timedelta(days=30))
    holey = full[~hole].reset_index(drop=True)
    rep = validate(holey)
    assert rep["missing_trading_days"] > 15
    assert rep["max_gap_run"] >= 3  # contiguous run == a hole, not provider noise
    assert rep["quality_score"] < base["quality_score"]
    assert any("missing trading day" in i for i in rep["issues"])


def test_models_selectable_columns_includes_multioutput():
    from quantdesk.strategy.models import selectable_columns

    cols = selectable_columns([{"name": "ST", "type": "Supertrend"}, {"name": "M", "type": "MACD"}])
    assert "ST_trend" in cols and "ST_dir" in cols and "M_line" in cols and "M_signal" in cols


@pytest.mark.parametrize("window", [10, 20, 50])
def test_vectorized_indicator_equality(window):
    from scipy.stats import linregress

    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy import indicators as I

    close = load_indexed(DATA)["Close"].values[:5000]

    def slope(x):
        m = ~np.isnan(x)
        return linregress(np.arange(len(x))[m], x[m])[0] if m.sum() >= 2 else np.nan

    orig = pd.Series(close).rolling(window).apply(slope, raw=True).values
    assert np.allclose(orig, I.lr_slope_vec(close, window), equal_nan=True, atol=1e-6)

    def alma(s, w, off=0.85, sig=6):
        m = off * (w - 1)
        sd = w / sig
        wt = np.exp(-((np.arange(w) - m) ** 2) / (2 * sd ** 2))
        wt /= wt.sum()
        return np.convolve(s, wt[::-1], mode="valid")

    oa = pd.Series(close).rolling(window).apply(lambda x: alma(x, window)[-1] if len(x) == window else np.nan, raw=True).values
    assert np.allclose(oa, I.alma_vec(close, window), equal_nan=True, atol=1e-6)


def test_codegen_compile_runs():
    from backtesting import Backtest

    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    from quantdesk.strategy.models import StrategyConfig

    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 30}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
    )
    cls = compile_strategy(build_strategy_code(cfg))
    stats = Backtest(load_indexed(DATA), cls, cash=100000, commission=0.0005, finalize_trades=True).run()
    assert stats["# Trades"] > 0


def test_presets_roundtrip(tmp_path, monkeypatch):
    from quantdesk import config
    from quantdesk.strategy import presets
    from quantdesk.strategy.models import StrategyConfig

    monkeypatch.setattr(config, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(presets, "STRATEGIES_DIR", tmp_path)
    cfg = StrategyConfig(indicators=[{"name": "X", "type": "RSI", "source": "Close", "params": {"period": 14}}])
    fn = presets.save_strategy("rt", cfg)
    assert presets.load_strategy(fn).to_dict() == cfg.to_dict()


def test_ml_mine_patterns_synthetic():
    from quantdesk.ml.patterns import mine_patterns

    rng = np.random.default_rng(0)
    n = 200
    rsi = rng.uniform(20, 80, n)
    # wins when RSI in a band -> the tree should find a rule
    ret = np.where((rsi > 40) & (rsi < 60), rng.uniform(0.2, 2.0, n), rng.uniform(-2.0, 0.2, n))
    trades = pd.DataFrame({"Return [%]": ret, "Entry_RSI": rsi, "Entry_EMA": rng.uniform(100, 200, n)})
    out = mine_patterns(trades, ["RSI", "EMA"], "Win (Return > 0)", min_trades=20)
    assert out["error"] is None
    assert 0 < out["baseline_win_rate"] < 100
    assert not out["importance"].empty
