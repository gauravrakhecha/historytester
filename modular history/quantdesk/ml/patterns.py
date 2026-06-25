"""
quantdesk.ml.patterns — pure ML pattern mining (XGBoost importance + decision-tree rules).

Extracted from the monolith's ML Pattern Finder (upstox_app.py:3081-3185). The original
interleaved ``st.*`` rendering and ProcessPoolExecutor feature extraction with the ML math.
Here the math is pure: input is a trades DataFrame already enriched with ``Entry_<feature>``
columns (the UI page builds that via the backtest worker), output is a plain dict. Reproduces
the exact model params and the high-probability rule-extraction threshold.
"""
from __future__ import annotations

import re
from typing import List

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier, _tree, export_text
import xgboost as xgb


def _get_high_prob_rules(tree, feature_names, baseline_mean) -> List[dict]:
    """Walk decision-tree leaves; keep rules beating baseline*1.1 with >=5 samples."""
    tree_ = tree.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!" for i in tree_.feature
    ]
    paths: List[dict] = []

    def recurse(node, path):
        if tree_.feature[node] != _tree.TREE_UNDEFINED:
            name = feature_name[node]
            threshold = tree_.threshold[node]
            p1, p2 = list(path), list(path)
            p1.append(f"{name} <= {threshold:.2f}")
            recurse(tree_.children_left[node], p1)
            p2.append(f"{name} > {threshold:.2f}")
            recurse(tree_.children_right[node], p2)
        else:
            samples = tree_.n_node_samples[node]
            values = tree_.value[node][0]
            total_samples = sum(values)
            prob_win = values[1] / total_samples if total_samples > 0 else 0
            if prob_win > (baseline_mean * 1.1) and samples >= 5:
                paths.append({"rule": " AND ".join(path), "prob": prob_win * 100, "samples": int(samples)})

    recurse(0, [])
    return sorted(paths, key=lambda x: x["prob"], reverse=True)


def mine_patterns(
    trades_df: pd.DataFrame,
    selected_features: List[str],
    target_metric: str,
    min_trades: int = 20,
) -> dict:
    """Mine high-probability indicator patterns from enriched trades.

    Returns a dict: ``feature_cols``, ``baseline_win_rate``, ``importance`` (DataFrame),
    ``tree_text``, ``rules`` (list of {rule, prob, samples}), and ``error``/``warning``.
    """
    out = {
        "feature_cols": [],
        "baseline_win_rate": None,
        "importance": pd.DataFrame(columns=["Feature", "Importance"]),
        "tree_text": "",
        "rules": [],
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

    ml_df = trades_df.dropna(subset=feature_cols).copy()
    X = ml_df[feature_cols]
    X.columns = [re.sub(r"[\[\]<>]", "_", col) for col in X.columns]

    if target_metric == "Win (Return > 0)":
        y = (ml_df["Return [%]"] > 0).astype(int)
    else:
        y = (ml_df["Return [%]"] > 1.0).astype(int)

    out["baseline_win_rate"] = float(y.mean() * 100)

    if len(ml_df) < 20 or len(X.columns) == 0:
        out["error"] = (
            "Not enough valid data rows after processing features. Try fewer features or a "
            "larger time period."
        )
        return out
    if y.sum() < 2 or (len(y) - y.sum()) < 2:
        out["error"] = (
            "Not enough variance in the target metric (too few wins or too few losses). "
            "The AI cannot learn to differentiate if all trades have the same outcome."
        )
        return out

    # XGBoost feature importance
    model_xgb = xgb.XGBClassifier(
        n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42
    )
    model_xgb.fit(X, y)
    importances = model_xgb.feature_importances_
    feat_imp_df = pd.DataFrame({"Feature": X.columns, "Importance": importances}).sort_values(
        "Importance", ascending=True
    )
    feat_imp_df = feat_imp_df[feat_imp_df["Importance"] > 0.01]
    out["importance"] = feat_imp_df

    # Decision tree -> human-readable rules
    dt = DecisionTreeClassifier(
        max_depth=3, min_samples_leaf=max(5, int(len(ml_df) * 0.05)), random_state=42
    )
    dt.fit(X, y)
    names = [f.replace("Entry_", "") for f in X.columns]
    out["tree_text"] = export_text(dt, feature_names=names)
    out["rules"] = _get_high_prob_rules(dt, names, float(y.mean()))
    if not out["rules"]:
        out["warning"] = (
            "The AI couldn't find any simple indicator combinations that significantly beat your "
            "baseline win rate. The strategy might depend on price action rather than these "
            "specific indicators."
        )
    return out
