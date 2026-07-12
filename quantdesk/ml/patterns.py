"""
quantdesk.ml.patterns — pure ML pattern mining with statistical rigor (v7 Phase B).

Extracted from the monolith's ML Pattern Finder (upstox_app.py:3081-3185). The original
interleaved ``st.*`` rendering and ProcessPoolExecutor feature extraction with the ML math,
trained on the SAME trades it scored, and promoted any decision-tree leaf that merely beat the
baseline by 10% with >=5 samples — i.e. it over-stated edge and surfaced noise as "patterns".

This module keeps the math pure (input: an enriched trades DataFrame with ``Entry_<feature>``
columns; output: a plain dict) but makes the discovered rules *trustworthy*:

* **Time-ordered holdout** (default 70/30) or **walk-forward k-fold** — rules are mined on the
  training trades and SCORED on out-of-sample trades, so reported win-rates are OOS, not in-sample.
* **Per-instrument normalization** — raw/absolute features (price, volume, EMA…) are z-scored
  within each source instrument before the matrix is pooled, so bulk cross-instrument aggregation
  is valid (a ₹1,300 stock and a 25,000 index no longer share one absolute threshold).
* **Significance gate** — each candidate leaf must clear a sample-size floor, survive a one-sided
  binomial test vs the OOS baseline under **Benjamini-Hochberg FDR**, AND have a **Wilson
  lower-bound** win-rate that still beats the baseline. Only those are promoted to ``rules``;
  weaker ones are surfaced as ``candidate_rules`` (exploratory observations).

XGBoost importance is descriptive only (computed on the full matrix) and is NOT part of the gate.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.tree import DecisionTreeClassifier, _tree, export_text
import xgboost as xgb

from quantdesk.config import ML_FDR_ALPHA, ML_HOLDOUT_FRAC, ML_SAMPLE_FLOOR

_Z_95 = 1.959963984540054  # standard-normal quantile for a 95% interval


# --------------------------------------------------------------------------- statistics helpers
def wilson_lower_bound(wins: int, n: int, z: float = _Z_95) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion (in [0, 1]).

    Conservative point estimate of the true win-rate: small samples are pulled hard toward 0, so a
    leaf needs *both* a high win-rate *and* enough trades to clear the baseline on this measure.
    """
    if n <= 0:
        return 0.0
    phat = wins / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def binom_sf(wins: int, n: int, p0: float) -> float:
    """One-sided p-value: P(X >= wins) under Binomial(n, p0). Tests "leaf beats baseline p0"."""
    if n <= 0:
        return 1.0
    p0 = min(max(p0, 1e-9), 1.0 - 1e-9)
    try:  # exact test (scipy is a hard dependency of sklearn/quantstats here)
        from scipy.stats import binomtest

        return float(binomtest(int(wins), int(n), p0, alternative="greater").pvalue)
    except Exception:  # pragma: no cover - normal approximation fallback
        mu = n * p0
        sd = math.sqrt(n * p0 * (1.0 - p0))
        if sd == 0:
            return 1.0
        z = (wins - 0.5 - mu) / sd  # continuity-corrected
        return 0.5 * math.erfc(z / math.sqrt(2.0))


def benjamini_hochberg(pvals: List[float], alpha: float) -> Tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg FDR control. Returns ``(reject, qvals)`` aligned to ``pvals``.

    Controls the expected proportion of false positives among the rules we promote — essential
    because a single tree exposes many leaves and "best of N" inflates significance.
    """
    p = np.asarray(pvals, dtype=float)
    m = p.size
    if m == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=float)
    order = np.argsort(p)
    ranked = p[order]
    q = ranked * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]  # enforce monotone non-decreasing q
    qvals = np.empty(m, dtype=float)
    qvals[order] = np.clip(q, 0.0, 1.0)
    return qvals <= alpha, qvals


# ------------------------------------------------------------------- decision-tree leaf helpers
def _leaf_conditions(tree: DecisionTreeClassifier, feature_names: List[str]) -> Dict[int, Tuple[str, list]]:
    """Map each leaf node id -> ``(human_rule_string, [(col_idx, '<='|'>', threshold), ...])``.

    ``col_idx`` is the feature's column position (the tree's split feature index), so the same
    predicate can be re-evaluated on any matrix with identical column order.
    """
    t = tree.tree_
    out: Dict[int, Tuple[str, list]] = {}

    def recurse(node: int, parts: List[str], conds: list):
        if t.feature[node] != _tree.TREE_UNDEFINED:
            ci = int(t.feature[node])
            name = feature_names[ci]
            thr = float(t.threshold[node])
            recurse(t.children_left[node], parts + [f"{name} <= {thr:.4g}"], conds + [(ci, "<=", thr)])
            recurse(t.children_right[node], parts + [f"{name} > {thr:.4g}"], conds + [(ci, ">", thr)])
        else:
            out[node] = (" AND ".join(parts) if parts else "(all trades)", conds)

    recurse(0, [], [])
    return out


def _eval_conditions(conds: list, X: np.ndarray) -> np.ndarray:
    """Boolean mask of rows in ``X`` (2-D float array) satisfying a leaf's conditions."""
    mask = np.ones(X.shape[0], dtype=bool)
    for ci, op, thr in conds:
        col = X[:, ci]
        mask &= (col <= thr) if op == "<=" else (col > thr)
    return mask


# ----------------------------------------------------------------------- feature preprocessing
def _zscore_per_instrument(ml_df: pd.DataFrame, raw_cols: List[str], source_col: str) -> None:
    """In-place: standardize each raw ``Entry_<feature>`` column WITHIN each source instrument.

    Makes pooled cross-instrument aggregation valid for absolute features. Degenerate groups
    (one row, or zero variance) map to 0.0 rather than NaN/inf so the rows survive the dropna.
    """
    if source_col not in ml_df.columns:
        return
    grp = ml_df.groupby(source_col, observed=True)
    for col in raw_cols:
        if col not in ml_df.columns:
            continue
        mean = grp[col].transform("mean")
        std = grp[col].transform("std")  # sample std; constant within a group is fine
        z = (ml_df[col] - mean) / std
        ml_df[col] = z.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _make_target(rets: pd.Series, target_metric: str, ret_col: str) -> np.ndarray:
    hi_thresh = 0.01 if ret_col == "ReturnPct" else 1.0  # ">1%" => 0.01 as a fraction
    if target_metric == "Win (Return > 0)":
        return (rets > 0.0).astype(int).to_numpy()
    return (rets > hi_thresh).astype(int).to_numpy()


# -------------------------------------------------------------------------------- public entry
def mine_patterns(
    trades_df: pd.DataFrame,
    selected_features: List[str],
    target_metric: str,
    min_trades: int = 20,
    *,
    raw_features: Optional[List[str]] = None,
    source_col: str = "Source",
    time_col: str = "EntryTime",
    sample_floor: int = ML_SAMPLE_FLOOR,
    holdout_frac: float = ML_HOLDOUT_FRAC,
    fdr_alpha: float = ML_FDR_ALPHA,
    k_folds: int = 0,
    random_state: int = 42,
) -> dict:
    """Mine indicator patterns from enriched trades and gate them for out-of-sample significance.

    Parameters (beyond the originals)
    ---------------------------------
    raw_features : feature names (without the ``Entry_`` prefix) that are raw/absolute and should
        be z-scored per instrument before pooling. The Phase-A normalized features need not appear.
    source_col : column identifying each trade's source instrument (added by ``batch.extract_trades``).
    time_col : column used to time-order trades for the holdout (falls back to row order if absent).
    sample_floor : minimum OUT-OF-SAMPLE matches for a rule to be eligible for promotion.
    holdout_frac : fraction of (time-ordered) trades reserved as the OOS test set.
    fdr_alpha : Benjamini-Hochberg target false-discovery rate.
    k_folds : if >= 2, use anchored walk-forward CV (``TimeSeriesSplit``) instead of one holdout;
        candidate rules are pooled across folds before the significance gate.

    Returns a dict with: ``feature_cols``, ``baseline_win_rate`` (full set, headline),
    ``baseline_win_rate_oos``, ``importance`` (DataFrame), ``tree_text``, ``rules`` (validated,
    OOS), ``candidate_rules`` (exploratory), ``n_train``/``n_test``, ``validation`` label,
    and ``error``/``warning``.
    """
    out = {
        "feature_cols": [],
        "baseline_win_rate": None,
        "baseline_win_rate_oos": None,
        "importance": pd.DataFrame(columns=["Feature", "Importance"]),
        "tree_text": "",
        "rules": [],
        "candidate_rules": [],
        "n_train": 0,
        "n_test": 0,
        "validation": "",
        "error": None,
        "warning": None,
    }

    if trades_df is None or trades_df.empty:
        out["error"] = "No trades executed across any datasets."
        return out
    if len(trades_df) < min_trades:
        out["error"] = (
            f"Not enough trades ({len(trades_df)}) to train a machine learning model reliably. "
            "Try a longer dataset or adjusting your strategy rules."
        )
        return out

    feature_cols = [f"Entry_{v}" for v in selected_features if f"Entry_{v}" in trades_df.columns]
    if not feature_cols:
        out["error"] = (
            "None of the selected features could be extracted from the backtest results. "
            "Check your indicator names."
        )
        return out
    out["feature_cols"] = feature_cols

    work = trades_df.copy()

    # (1) Per-instrument normalization of raw/absolute features BEFORE pooling.
    raw_cols = [f"Entry_{v}" for v in (raw_features or []) if f"Entry_{v}" in work.columns]
    if raw_cols:
        _zscore_per_instrument(work, raw_cols, source_col)

    # (2) Time-order so the holdout / walk-forward respects chronology.
    if time_col in work.columns:
        work = work.sort_values(time_col, kind="stable")
    work = work.reset_index(drop=True)

    ml_df = work.dropna(subset=feature_cols).copy()
    if len(ml_df) < min_trades:
        out["error"] = (
            "Not enough valid data rows after processing features. Try fewer features or a "
            "larger time period."
        )
        return out

    X = ml_df[feature_cols].copy()
    X.columns = [re.sub(r"[\[\]<>]", "_", col) for col in X.columns]
    names = [c.replace("Entry_", "") for c in X.columns]
    Xv = X.to_numpy(dtype="float64")

    # backtesting.py per-trade frames use 'ReturnPct' (a fraction); overall stats use 'Return [%]'.
    ret_col = "ReturnPct" if "ReturnPct" in ml_df.columns else "Return [%]"
    y = _make_target(ml_df[ret_col], target_metric, ret_col)
    out["baseline_win_rate"] = float(y.mean() * 100)

    if y.sum() < 2 or (len(y) - y.sum()) < 2:
        out["error"] = (
            "Not enough variance in the target metric (too few wins or too few losses). "
            "The AI cannot learn to differentiate if all trades have the same outcome."
        )
        return out

    # (3) XGBoost importance — DESCRIPTIVE ONLY (full matrix; not part of the OOS gate).
    try:
        model_xgb = xgb.XGBClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.1, random_state=random_state
        )
        model_xgb.fit(X, y)
        feat_imp_df = pd.DataFrame(
            {"Feature": X.columns, "Importance": model_xgb.feature_importances_}
        ).sort_values("Importance", ascending=True)
        out["importance"] = feat_imp_df[feat_imp_df["Importance"] > 0.01]
    except Exception:  # pragma: no cover - importance is non-critical, never fail the run for it
        pass

    leaf_min = max(5, int(len(ml_df) * 0.05))

    def _new_tree() -> DecisionTreeClassifier:
        return DecisionTreeClassifier(max_depth=3, min_samples_leaf=leaf_min, random_state=random_state)

    # (4) Mine candidate rules on TRAIN, score them on OUT-OF-SAMPLE trades.
    if k_folds and k_folds >= 2:
        candidates, n_train, n_test, tree_text, oos_baseline = _mine_kfold(
            Xv, y, names, _new_tree, k_folds
        )
        out["validation"] = f"Walk-forward ({k_folds}-fold, time-ordered)"
    else:
        candidates, n_train, n_test, tree_text, oos_baseline = _mine_holdout(
            Xv, y, names, _new_tree, holdout_frac
        )
        out["validation"] = f"Time-ordered holdout ({int(round((1 - holdout_frac) * 100))}/" \
                            f"{int(round(holdout_frac * 100))})"

    out["tree_text"] = tree_text
    out["n_train"] = n_train
    out["n_test"] = n_test
    out["baseline_win_rate_oos"] = float(oos_baseline * 100) if oos_baseline is not None else None

    if not candidates:
        out["warning"] = (
            "The AI couldn't find any indicator combinations that beat your baseline win rate on "
            "the training trades. The strategy may depend on price action rather than these "
            "specific indicators."
        )
        return out

    # (5) Significance gate: sample floor -> one-sided binomial vs OOS baseline -> BH-FDR ->
    #     Wilson lower-bound must still beat baseline. Promote survivors; demote the rest.
    eligible = [c for c in candidates if c["samples"] >= sample_floor]
    if eligible:
        pvals = [binom_sf(c["wins"], c["samples"], oos_baseline) for c in eligible]
        reject, qvals = benjamini_hochberg(pvals, fdr_alpha)
        base_pct = oos_baseline * 100
        for c, p, q, rej in zip(eligible, pvals, qvals, reject):
            c["p_value"] = float(p)
            c["q_value"] = float(q)
            c["wilson_lb"] = wilson_lower_bound(c["wins"], c["samples"]) * 100
            c["passed"] = bool(rej and c["wilson_lb"] > base_pct)

    promoted, demoted = [], []
    eligible_ids = {id(c) for c in eligible}
    for c in candidates:
        if c.get("passed"):
            promoted.append(c)
        else:
            if id(c) not in eligible_ids:
                c["reason"] = f"fewer than {sample_floor} out-of-sample trades"
            elif c.get("q_value", 1.0) > fdr_alpha:
                c["reason"] = "not significant after multiple-testing (FDR) correction"
            else:
                c["reason"] = "conservative (Wilson) win-rate did not beat the baseline"
            demoted.append(c)

    promoted.sort(key=lambda r: r.get("wilson_lb", r["prob"]), reverse=True)
    demoted.sort(key=lambda r: r["prob"], reverse=True)
    out["rules"] = promoted
    out["candidate_rules"] = demoted

    if not promoted:
        out["warning"] = (
            "Candidate patterns were found in-sample, but NONE survived out-of-sample validation "
            "(holdout + significance test). They're shown below as exploratory only — treat them "
            "as noise until they prove out on more data."
        )
    return out


def _rule_record(rule_str: str, train_n: int, train_wins: int, test_n: int, test_wins: int) -> dict:
    """One candidate rule with both in-sample (train) and out-of-sample (test) stats."""
    return {
        "rule": rule_str,
        "prob": (test_wins / test_n * 100) if test_n else 0.0,  # OOS win-rate (headline)
        "samples": int(test_n),                                  # OOS sample count
        "wins": int(test_wins),
        "train_prob": (train_wins / train_n * 100) if train_n else 0.0,
        "train_samples": int(train_n),
        "wilson_lb": None,
        "p_value": None,
        "q_value": None,
        "passed": False,
    }


def _mine_holdout(Xv, y, names, new_tree, holdout_frac):
    """Single time-ordered holdout. Returns (candidates, n_train, n_test, tree_text, oos_baseline)."""
    n = len(y)
    n_test = int(round(n * holdout_frac))
    n_test = max(1, min(n_test, n - 1))
    n_train = n - n_test
    Xtr, Xte = Xv[:n_train], Xv[n_train:]
    ytr, yte = y[:n_train], y[n_train:]

    tree = new_tree()
    tree.fit(Xtr, ytr)
    tree_text = export_text(tree, feature_names=list(names))
    oos_baseline = float(yte.mean()) if n_test else 0.0
    base_train = float(ytr.mean())

    candidates = []
    for _node, (rule_str, conds) in _leaf_conditions(tree, names).items():
        tr_mask = _eval_conditions(conds, Xtr)
        train_n = int(tr_mask.sum())
        if train_n < 5:
            continue
        train_wr = ytr[tr_mask].mean() if train_n else 0.0
        if train_wr <= base_train * 1.1:  # only mine leaves that look like an edge in-sample
            continue
        te_mask = _eval_conditions(conds, Xte)
        test_n = int(te_mask.sum())
        test_wins = int(yte[te_mask].sum())
        candidates.append(_rule_record(rule_str, train_n, int(ytr[tr_mask].sum()), test_n, test_wins))
    return candidates, n_train, n_test, tree_text, oos_baseline


def _mine_kfold(Xv, y, names, new_tree, k_folds):
    """Anchored walk-forward (TimeSeriesSplit). Pools candidate rules across folds by predicate.

    Returns (candidates, n_train, n_test, tree_text, oos_baseline) where train/test counts are the
    last fold's split and oos_baseline is the pooled out-of-fold win-rate.
    """
    from sklearn.model_selection import TimeSeriesSplit

    n = len(y)
    k = max(2, min(k_folds, n - 1))
    tscv = TimeSeriesSplit(n_splits=k)

    pool: Dict[str, dict] = {}
    last_text = ""
    last_train = last_test = 0
    oos_wins = oos_total = 0
    for tr_idx, te_idx in tscv.split(Xv):
        Xtr, Xte = Xv[tr_idx], Xv[te_idx]
        ytr, yte = y[tr_idx], y[te_idx]
        last_train, last_test = len(tr_idx), len(te_idx)
        oos_wins += int(yte.sum())
        oos_total += len(te_idx)
        if ytr.sum() < 2 or (len(ytr) - ytr.sum()) < 2:
            continue
        tree = new_tree()
        tree.fit(Xtr, ytr)
        last_text = export_text(tree, feature_names=list(names))
        base_train = float(ytr.mean())
        for _node, (rule_str, conds) in _leaf_conditions(tree, names).items():
            tr_mask = _eval_conditions(conds, Xtr)
            train_n = int(tr_mask.sum())
            if train_n < 5 or ytr[tr_mask].mean() <= base_train * 1.1:
                continue
            te_mask = _eval_conditions(conds, Xte)
            agg = pool.setdefault(
                rule_str,
                {"train_n": 0, "train_wins": 0, "test_n": 0, "test_wins": 0},
            )
            agg["train_n"] += train_n
            agg["train_wins"] += int(ytr[tr_mask].sum())
            agg["test_n"] += int(te_mask.sum())
            agg["test_wins"] += int(yte[te_mask].sum())

    oos_baseline = (oos_wins / oos_total) if oos_total else 0.0
    candidates = [
        _rule_record(rule_str, a["train_n"], a["train_wins"], a["test_n"], a["test_wins"])
        for rule_str, a in pool.items()
    ]
    return candidates, last_train, last_test, last_text, oos_baseline
