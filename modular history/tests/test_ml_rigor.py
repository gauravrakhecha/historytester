"""v7 Phase B: ML rigor — holdout/k-fold, significance gate (Wilson + binomial + BH-FDR),
per-instrument normalization, and normalized-feature defaulting.

These are deliberately SYNTHETIC (no real-data dependency) so they're fast and portable.
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.ml import patterns as P  # noqa: E402
from quantdesk.ml.patterns import (  # noqa: E402
    benjamini_hochberg,
    binom_sf,
    mine_patterns,
    wilson_lower_bound,
)
from quantdesk.strategy.indicators import partition_features  # noqa: E402


# ----------------------------------------------------------------------- statistics primitives
def test_wilson_lower_bound_properties():
    # full bound is below the point estimate, in [0, 1], and TIGHTENS as n grows.
    assert 0.0 <= wilson_lower_bound(8, 10) < 0.8
    lb_small = wilson_lower_bound(80, 100)
    lb_large = wilson_lower_bound(800, 1000)
    assert lb_small < 0.8 and lb_large < 0.8
    assert lb_large > lb_small            # more evidence -> tighter (higher) lower bound
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(50, 50) < 1.0


def test_binom_sf_one_sided():
    # 50/50 coin: 50 heads in 100 is unremarkable; 80 heads is extreme.
    assert binom_sf(50, 100, 0.5) > 0.3
    assert binom_sf(80, 100, 0.5) < 1e-6
    # exactly at the baseline rate -> not significant
    assert binom_sf(30, 100, 0.5) > 0.99


def test_benjamini_hochberg_known_cases():
    # all five just-significant -> all reject; q-values all == alpha here.
    reject, q = benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05], 0.05)
    assert reject.all()
    assert np.allclose(q, 0.05)
    # one tiny p among large ones -> only the tiny one rejects.
    reject2, q2 = benjamini_hochberg([0.001, 0.5, 0.5, 0.5, 0.5], 0.05)
    assert reject2.tolist() == [True, False, False, False, False]
    assert q2[0] < 0.05 and (q2[1:] > 0.05).all()
    # empty input is handled
    r0, q0 = benjamini_hochberg([], 0.05)
    assert r0.size == 0 and q0.size == 0


# ------------------------------------------------------------- end-to-end mining with a real edge
def _edge_trades(n=1400, seed=7):
    """A comparable feature 'z' with a genuine edge: z < -1 -> ~85% win, else ~30% win."""
    rng = np.random.default_rng(seed)
    z = rng.uniform(-3.0, 3.0, n)
    p_win = np.where(z < -1.0, 0.85, 0.30)
    win = rng.random(n) < p_win
    ret = np.where(win, 0.02, -0.02)
    return pd.DataFrame({
        "ReturnPct": ret,
        "Entry_z": z,
        "EntryTime": pd.date_range("2020-01-01", periods=n, freq="h"),
    })


def test_known_edge_is_recovered_and_passes_significance():
    out = mine_patterns(_edge_trades(), ["z"], "Win (Return > 0)", min_trades=20)
    assert out["error"] is None, out["error"]
    assert out["n_train"] > 0 and out["n_test"] > 0
    assert out["rules"], "a strong edge should survive out-of-sample validation"
    top = out["rules"][0]
    # OOS win-rate clearly beats the OOS baseline; the rule is on 'z'; stats are populated.
    assert top["prob"] > out["baseline_win_rate_oos"] + 10
    assert top["samples"] >= 30
    assert top["wilson_lb"] > out["baseline_win_rate_oos"]
    assert top["q_value"] <= 0.10
    assert "z" in top["rule"]


def test_pure_noise_surfaces_no_validated_rule():
    rng = np.random.default_rng(123)
    n = 1400
    df = pd.DataFrame({
        "ReturnPct": np.where(rng.random(n) < 0.5, 0.02, -0.02),
        "Entry_a": rng.normal(size=n),
        "Entry_b": rng.normal(size=n),
        "EntryTime": pd.date_range("2020-01-01", periods=n, freq="h"),
    })
    out = mine_patterns(df, ["a", "b"], "Win (Return > 0)", min_trades=20)
    assert out["error"] is None
    assert out["rules"] == [], "no rule should pass the gate on pure noise"


def test_kfold_walkforward_recovers_edge():
    out = mine_patterns(_edge_trades(seed=11), ["z"], "Win (Return > 0)", min_trades=20, k_folds=5)
    assert out["error"] is None
    assert out["validation"].startswith("Walk-forward")
    assert out["rules"], "the edge should also survive walk-forward validation"
    assert out["rules"][0]["prob"] > out["baseline_win_rate_oos"]


# --------------------------------------------------------- per-instrument normalization mechanics
def test_zscore_per_instrument_standardizes_each_group():
    df = pd.DataFrame({
        "Source": ["A"] * 100 + ["B"] * 100,
        "Entry_Close": np.r_[np.linspace(1300, 1350, 100), np.linspace(25000, 26000, 100)],
    })
    P._zscore_per_instrument(df, ["Entry_Close"], "Source")
    for grp in ("A", "B"):
        col = df.loc[df["Source"] == grp, "Entry_Close"]
        assert abs(col.mean()) < 1e-9
        assert abs(col.std(ddof=1) - 1.0) < 1e-6   # pandas .transform('std') uses sample std
    # a single global threshold now separates the (relatively) low rows of BOTH instruments
    low = df["Entry_Close"] < -1.0
    assert (df.loc[low, "Source"] == "A").any() and (df.loc[low, "Source"] == "B").any()


def test_per_instrument_normalization_enables_cross_scale_edge():
    """Same RELATIVE edge ('low within the instrument wins') at two very different price scales.

    Pooling the RAW absolute price hides the edge (the scales don't overlap); z-scoring per
    instrument first makes one comparable rule recover it across both.
    """
    rng = np.random.default_rng(3)

    def inst(name, base, span, seed):
        r = rng.uniform(0.0, 1.0, 1000)            # relative position within the instrument
        p_win = np.where(r < 0.4, 0.85, 0.30)      # low-relative wins, same direction in both
        win = rng.random(1000) < p_win
        return pd.DataFrame({
            "Source": name,
            "Entry_Close": base + r * span,        # absolute level (A ~1300s, B ~25000s)
            "ReturnPct": np.where(win, 0.02, -0.02),
        })

    df = pd.concat([inst("A", 1300, 50, 1), inst("B", 25000, 1000, 2)], ignore_index=True)
    df = df.sample(frac=1.0, random_state=0).reset_index(drop=True)   # interleave for the holdout
    df["EntryTime"] = pd.date_range("2020-01-01", periods=len(df), freq="h")

    with_norm = mine_patterns(
        df.copy(), ["Close"], "Win (Return > 0)", min_trades=20,
        raw_features=["Close"], source_col="Source",
    )
    assert with_norm["rules"], "per-instrument normalization should recover the pooled edge"
    top = with_norm["rules"][0]
    assert top["prob"] > with_norm["baseline_win_rate_oos"] + 10


# -------------------------------------------------------------- feature classification / defaults
def test_partition_features_classifies_normalized_vs_raw():
    indicators = [
        {"name": "ZC", "type": "Z-Score", "source": "Close", "params": {"period": 50}},
        {"name": "VolR", "type": "Ratio to SMA", "source": "Volume", "params": {"period": 20}},
        {"name": "MyEMA", "type": "EMA", "source": "Close", "params": {"period": 20}},
        {"name": "RSI14", "type": "RSI", "source": "Close", "params": {"period": 14}},
    ]
    selected = ["Close", "Volume", "ZC", "VolR", "MyEMA", "RSI14"]
    comparable, raw = partition_features(selected, indicators)
    assert set(comparable) == {"ZC", "VolR", "RSI14"}   # v7 normalized + bounded oscillator
    assert set(raw) == {"Close", "Volume", "MyEMA"}     # OHLCV + price-scale indicator


# ------------------------------------------------------------------------------- API / back-compat
def test_public_api_shape_and_legacy_call():
    """The legacy positional signature still works and all documented keys are present."""
    out = mine_patterns(_edge_trades(seed=5), ["z"], "Win (Return > 0)", 20)
    for key in (
        "feature_cols", "baseline_win_rate", "baseline_win_rate_oos", "importance",
        "tree_text", "rules", "candidate_rules", "n_train", "n_test", "validation",
        "error", "warning",
    ):
        assert key in out, f"missing key {key}"
    assert isinstance(out["importance"], pd.DataFrame)
    assert 0.0 <= out["baseline_win_rate"] <= 100.0


def test_too_few_trades_errors_cleanly():
    df = pd.DataFrame({"ReturnPct": [0.01, -0.01, 0.02], "Entry_z": [1.0, 2.0, 3.0]})
    out = mine_patterns(df, ["z"], "Win (Return > 0)", min_trades=20)
    assert out["error"] is not None and out["rules"] == []
