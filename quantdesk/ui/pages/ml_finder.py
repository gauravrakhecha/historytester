"""ML Pattern Finder page (upstox_app.py:3004-3207) — v7 Phase B: OOS-validated rules."""
from __future__ import annotations

import os
import traceback

import pandas as pd
import streamlit as st

from quantdesk.backtest import batch as batch_mod
from quantdesk.core import settings_repo
from quantdesk.core.db import connect, get_download_history
from quantdesk.ml.patterns import mine_patterns
from quantdesk.strategy.indicators import partition_features
from quantdesk.strategy.models import OHLCV, selectable_columns
from quantdesk.viz.charts import feature_importance_bar


def render(ctx):
    st.header("🤖 Machine Learning Pattern Finder")
    st.markdown(
        "Uses **XGBoost** and **Decision Trees** to find hidden, high-probability setups in your "
        "strategy by analysing indicator values at the moment each trade is entered. Discovered "
        "rules are validated on **out-of-sample** trades before being trusted."
    )

    history = get_download_history()
    code = st.session_state.get("custom_strategy_code", "")
    if not code:
        st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        return
    if history.empty:
        st.warning(
            "No downloaded data found. Run **Data Management -> Start Recovery Scan** "
            "if you already have data on disk, or download data using the Downloaders first."
        )
        return

    ml_mode = st.radio("Analysis Mode", ["Single Dataset", "Bulk Folder Aggregation"], horizontal=True)
    files_to_process = []

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        if ml_mode == "Single Dataset":
            options = history["file_path"].tolist()
            labels = [f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}" for _, row in history.iterrows()]
            selected_idx = st.selectbox("Select Dataset to Mine for Patterns", range(len(options)), format_func=lambda x: labels[x])
            files_to_process = [options[selected_idx]]
        else:
            batch_folder = st.text_input("Folder containing multiple datasets (Parquet/CSV)", value=ctx.default_parquet_dir)
            if batch_folder and os.path.exists(batch_folder):
                files_to_process = batch_mod.find_data_files(batch_folder)
    with col2:
        target_metric = st.selectbox("Define 'Success'", ["Win (Return > 0)", "High R:R (Return > 1%)"])
    with col3:
        min_trades_filter = st.number_input("Min Trades Filter", min_value=5, value=20, help="Minimum trades required to build the ML model safely.")

    # Feature selection — DEFAULT to cross-asset-comparable features (v7 Phase B). Absolute
    # features (price/volume/EMA…) don't generalize across instruments and are z-scored per
    # instrument before pooling; we still warn so the user understands what's happening.
    indicators = st.session_state.get("indicators", [])
    all_vars = [c for c in selectable_columns(indicators) if c not in OHLCV] + list(OHLCV)
    comparable_all, raw_all = partition_features(all_vars, indicators)
    default_features = comparable_all if comparable_all else all_vars
    selected_features = st.multiselect(
        "Select Indicators to feed the AI",
        all_vars,
        default=default_features,
        help="Defaults to normalized / cross-asset-comparable features. These carry the same "
             "meaning across instruments, so learned rules generalize.",
    )
    if not comparable_all:
        st.info(
            "💡 None of your indicators are normalized cross-asset features. Add one from the v7 "
            "set in **Strategy Builder** — e.g. **Z-Score**, **Ratio to SMA** (Volume source), "
            "**Distance from MA (ATR)**, **MA Slope (ATR)**, **Percentile Rank**, **Return (n-bar)** "
            "— so the AI learns rules that generalize across instruments."
        )
    sel_comparable, sel_raw = partition_features(selected_features, indicators)
    if sel_raw:
        st.warning(
            "⚠️ Raw/absolute features selected: **" + ", ".join(sel_raw) + "**. A threshold like "
            "`Close > 1330` can't generalize across instruments, so these are **standardized per "
            "instrument (z-score)** before aggregation. Prefer the normalized features above."
        )

    with st.expander("⚙️ Validation settings", expanded=False):
        vcol1, vcol2 = st.columns(2)
        with vcol1:
            validation = st.selectbox(
                "Validation method",
                ["Time-ordered holdout (70/30)", "Walk-forward (5-fold)"],
                help="Rules are mined on training trades and scored on unseen (later) trades.",
            )
        with vcol2:
            sample_floor = st.number_input(
                "Min out-of-sample trades per rule", min_value=5, value=30,
                help="A rule needs at least this many unseen trades before it can be promoted.",
            )
    k_folds = 5 if validation.startswith("Walk-forward") else 0

    if files_to_process and st.button("🔍 Mine Strategy for AI Patterns", width="stretch", type="primary"):
        existing_files = [f for f in files_to_process if f and os.path.isfile(f)]
        if not existing_files:
            st.error(
                "None of the selected dataset file(s) exist on disk. "
                "Run **Data Management -> Start Recovery Scan** or re-download the data."
            )
            return
        missing_count = len(files_to_process) - len(existing_files)
        if missing_count > 0:
            st.warning(f"Skipping {missing_count} missing dataset file(s) not found on disk.")
        with st.spinner("Running deep backtest and extracting indicator features (multi-core)..."):
            try:
                params = settings_repo.backtest_params()
                all_trades = batch_mod.extract_trades(
                    existing_files, code, params["initial_capital"], params["total_commission"],
                    params["margin"], selected_features,
                )
                if not all_trades:
                    st.error("No trades executed across any datasets.")
                    return
                trades_df = pd.concat(all_trades, ignore_index=True)
                st.info(f"Aggregated {len(trades_df)} historical trades for Machine Learning.")

                result = mine_patterns(
                    trades_df, selected_features, target_metric, int(min_trades_filter),
                    raw_features=sel_raw, k_folds=k_folds, sample_floor=int(sample_floor),
                )
                if result["error"]:
                    st.error(result["error"])
                    return

                m1, m2, m3 = st.columns(3)
                m1.metric(
                    label=f"Baseline Win Rate ({target_metric})",
                    value=f"{result['baseline_win_rate']:.1f}%",
                    help="Raw probability of success across ALL trades before the ML filter.",
                )
                if result["baseline_win_rate_oos"] is not None:
                    m2.metric("Out-of-sample baseline", f"{result['baseline_win_rate_oos']:.1f}%",
                              help="Baseline win-rate on the held-out (unseen) trades — rules must beat this.")
                m3.metric("Train / Test trades", f"{result['n_train']} / {result['n_test']}",
                          help=f"Validation: {result['validation']}.")

                st.subheader("📊 Which Indicators Matter Most?")
                if not result["importance"].empty:
                    st.plotly_chart(feature_importance_bar(result["importance"]), width="stretch")
                else:
                    st.caption("No single indicator stood out (importance is exploratory only).")

                st.subheader("✅ Validated Patterns (out-of-sample)")
                st.caption(
                    f"Mined on training trades, scored on unseen trades ({result['validation']}), and "
                    "promoted only if they clear the sample-size floor, survive Benjamini-Hochberg "
                    "FDR correction, and keep a conservative (Wilson) edge over the baseline."
                )
                if result["rules"]:
                    for i, r in enumerate(result["rules"]):
                        st.success(
                            f"**Pattern {i+1}** — OOS Win Rate **{r['prob']:.1f}%** "
                            f"(conservative ≥ {r['wilson_lb']:.1f}%) over **{r['samples']}** unseen trades · "
                            f"in-sample {r['train_prob']:.1f}% · FDR q={r['q_value']:.3f}\n\n👉 **IF** {r['rule']}"
                        )
                    st.session_state["last_ml_rules"] = result["rules"]
                    st.session_state["last_ml_target"] = target_metric
                else:
                    st.warning(result["warning"] or "No pattern survived out-of-sample validation.")
                    st.session_state["last_ml_rules"] = None

                if result["candidate_rules"]:
                    with st.expander(f"🔬 Exploratory candidates that FAILED validation ({len(result['candidate_rules'])})", expanded=False):
                        st.caption("Looked promising in-sample but did not prove out — treat as noise until they do.")
                        for r in result["candidate_rules"]:
                            st.info(
                                f"In-sample {r['train_prob']:.1f}% → OOS {r['prob']:.1f}% over {r['samples']} unseen "
                                f"trades · rejected: {r.get('reason', 'n/a')}\n\n• IF {r['rule']}"
                            )

                with st.expander("View Raw Decision Tree Logic", expanded=False):
                    st.code(result["tree_text"], language="text")
            except Exception as e:
                st.error(f"Error during ML analysis: {e}")
                st.code(traceback.format_exc(), language="text")

    if st.session_state.get("last_ml_rules"):
        st.divider()
        if st.button("💾 Save Validated Patterns to Database"):
            try:
                with connect() as conn:
                    strat_label = st.session_state.get("strategy_label", "Custom")
                    for r in st.session_state["last_ml_rules"]:
                        conn.execute(
                            """INSERT INTO ml_patterns
                               (strategy_name, target_metric, dataset_path, pattern_rules, win_rate, samples)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (strat_label, st.session_state["last_ml_target"], "Bulk", r["rule"], r["prob"], r["samples"]),
                        )
                st.success("Patterns saved successfully!")
                st.session_state["last_ml_rules"] = None
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save patterns: {e}")
