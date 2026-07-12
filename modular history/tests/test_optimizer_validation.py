"""v7 Phase C: optimizer validation — PSR/DSR, parameter-stability heatmap, walk-forward, Monte-Carlo."""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.backtest import metrics  # noqa: E402
from quantdesk.backtest import optimizer as opt  # noqa: E402
from quantdesk.backtest import wfo  # noqa: E402
from tests.conftest import SAMPLE_CSV, SAMPLE_DIR, requires_sample

pytestmark = requires_sample

DATA = SAMPLE_CSV


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


# ---------------------------------------------------------------------- PSR / DSR formula sanity
def test_psr_properties():
    # PSR in [0,1]; positive Sharpe over more data -> more confident; zero Sharpe -> ~0.5.
    p_small = metrics.probabilistic_sharpe_ratio(0.1, 50)
    p_large = metrics.probabilistic_sharpe_ratio(0.1, 1000)
    assert 0.0 <= p_small <= 1.0 and 0.0 <= p_large <= 1.0
    assert p_large > p_small
    assert abs(metrics.probabilistic_sharpe_ratio(0.0, 500) - 0.5) < 1e-6
    assert metrics.probabilistic_sharpe_ratio(0.2, 1000) > 0.99


def test_expected_max_sharpe_grows_with_trials_and_spread():
    # one trial -> just the mean (no selection bias); more trials / more spread -> higher bar.
    assert abs(metrics.expected_max_sharpe(1, 0.05) - 0.0) < 1e-12
    assert metrics.expected_max_sharpe(50, 0.05) > metrics.expected_max_sharpe(5, 0.05) > 0
    assert metrics.expected_max_sharpe(50, 0.10) > metrics.expected_max_sharpe(50, 0.05)


def test_deflated_sharpe_below_psr_and_monotone():
    sr, n, std = 0.1, 500, 0.05
    psr0 = metrics.probabilistic_sharpe_ratio(sr, n)            # vs benchmark 0
    dsr2 = metrics.deflated_sharpe_ratio(sr, n, 2, std)
    dsr50 = metrics.deflated_sharpe_ratio(sr, n, 50, std)
    assert dsr2 <= psr0 + 1e-9          # deflation can only lower (or equal) the probability
    assert dsr50 < dsr2                  # trying more configs raises the bar -> lower DSR
    assert 0.0 <= dsr50 <= 1.0


def test_sharpe_moments_basic():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.001, 0.02, 5000))
    m = metrics.sharpe_moments(r)
    assert abs(m["sr"] - r.mean() / r.std(ddof=1)) < 1e-9
    assert m["n"] == 5000
    assert abs(m["kurtosis"] - 3.0) < 0.4          # ~normal -> full kurtosis near 3


# ------------------------------------------------------------------ Monte-Carlo bootstrap bands
def test_monte_carlo_bands_bracket_realized_and_are_ordered():
    rng = np.random.default_rng(1)
    rets = rng.normal(0.004, 0.03, 200)
    mc = metrics.monte_carlo_bootstrap(rets, init_cash=100000, n_sims=2000, seed=42)
    assert mc["error"] is None and mc["n_trades"] == 200
    # percentile ordering at every step
    b = mc["bands"]
    assert (b["p5"] <= b["p50"] + 1e-6).all() and (b["p50"] <= b["p95"] + 1e-6).all()
    # the realized final equity sits inside the P5–P95 envelope of bootstrapped outcomes
    assert mc["final_p5"] <= mc["realized_final"] <= mc["final_p95"]
    # drawdown percentiles: P5 is the WORST (most negative)
    assert mc["mdd_p5"] <= mc["mdd_p50"] <= mc["mdd_p95"] <= 0.0


def test_monte_carlo_needs_two_trades():
    mc = metrics.monte_carlo_bootstrap([0.01], n_sims=10)
    assert mc["error"] is not None


# ----------------------------------------------------------- grid heatmap + optimizer meta shape
def test_grid_optimize_returns_heatmap_meta():
    from backtesting import Backtest
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.compiler import compile_strategy
    df = load_indexed(DATA).iloc[-3000:]
    bt = Backtest(df, compile_strategy(_ema_code()), cash=100000, commission=0.0005, finalize_trades=True)
    stats, best, meta = opt.optimize(
        bt, "Grid Search (Exhaustive)",
        {"EMA_f_period": range(5, 16, 5), "EMA_s_period": range(20, 41, 10)},
        "Return [%]",
    )
    assert meta["engine"] == "grid"
    assert isinstance(meta["heatmap"], pd.Series) and len(meta["heatmap"]) == 9  # 3 x 3 grid
    assert list(meta["heatmap"].index.names) == ["EMA_f_period", "EMA_s_period"]
    assert "EMA_f_period" in best and "EMA_s_period" in best


def test_parameter_stability_heatmap_figures():
    from quantdesk.viz import charts
    s1 = pd.Series([1.0, 2.0, 3.0], index=pd.Index([5, 10, 15], name="p"))
    fig1 = charts.parameter_stability_heatmap(s1, "Return [%]")
    assert fig1 is not None and len(fig1.data) >= 1
    idx = pd.MultiIndex.from_product([[5, 10], [20, 30]], names=["a", "b"])
    s2 = pd.Series([1.0, 2.0, 3.0, 4.0], index=idx)
    fig2 = charts.parameter_stability_heatmap(s2, "Return [%]")
    assert fig2.data[0].type == "heatmap"


def test_engine_optimized_run_carries_opt_meta():
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    _stats, info = engine.run_single(
        load_indexed(DATA), _ema_code(), 100000, 0.0005, 1.0,
        optimize_mode="Grid Search (Exhaustive)",
        opt_kwargs={"EMA_f_period": range(5, 16, 5), "EMA_s_period": range(20, 41, 10)},
        maximize="Return [%]",
    )
    assert info and "opt_meta" in info
    assert isinstance(info["opt_meta"]["heatmap"], pd.Series)


# ----------------------------------------------------------------------- walk-forward stitching
def test_walk_forward_stitches_n_oos_segments():
    from quantdesk.data.loader import load_indexed
    df = load_indexed(DATA).iloc[-6000:]
    res = wfo.walk_forward(
        df, _ema_code(), 100000, 0.0005, 1.0,
        opt_kwargs={"EMA_f_period": range(5, 16, 5), "EMA_s_period": range(20, 41, 10)},
        maximize="Return [%]", mode="Grid Search (Exhaustive)",
        n_windows=3, oos_frac=0.45, anchored=True, n_trials=10,
    )
    assert res["error"] is None
    assert res["n_windows"] == 3                       # all three OOS windows produced
    assert len(res["windows"]) == 3
    eq = res["stitched_equity"]
    assert len(eq) > 100                               # stitched curve has the OOS bars
    assert eq.index.is_monotonic_increasing            # chronological, non-overlapping segments
    assert "WFE" in res["windows"].columns
    assert not pd.isna(res["combined_oos_return_pct"])


def test_walk_forward_rejects_tiny_data():
    import pytest
    from quantdesk.data.loader import load_indexed
    df = load_indexed(DATA).iloc[-200:]
    with pytest.raises(ValueError):
        wfo.walk_forward(
            df, _ema_code(), 100000, 0.0005, 1.0,
            opt_kwargs={"EMA_f_period": range(5, 16, 5)},
            maximize="Return [%]", mode="Grid Search (Exhaustive)", n_windows=4,
        )
