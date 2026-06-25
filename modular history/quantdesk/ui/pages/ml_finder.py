"""ML Pattern Finder page (upstox_app.py:3004-3207)."""
from __future__ import annotations

import os
import traceback

import pandas as pd
import streamlit as st

from quantdesk.backtest import batch as batch_mod
from quantdesk.core import settings_repo
from quantdesk.core.db import connect, get_download_history
from quantdesk.ml.patterns import mine_patterns
from quantdesk.strategy.models import OHLCV, selectable_columns
from quantdesk.viz.charts import feature_importance_bar


def render(ctx):
    st.header("🤖 Machine Learning Pattern Finder")
    st.markdown(
        "Uses **XGBoost** and **Decision Trees** to find hidden, high-probability setups in your "
        "strategy by analysing indicator values at the moment each trade is entered."
    )

    history = get_download_history()
    code = st.session_state.get("custom_strategy_code", "")
    if not code:
        st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        return
    if history.empty:
        st.warning("No downloaded data found. Please download data first.")
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

    available_vars = [c for c in selectable_columns(st.session_state.get("indicators", [])) if c not in OHLCV] + list(OHLCV)
    selected_features = st.multiselect("Select Indicators to feed the AI", available_vars, default=available_vars)

    if files_to_process and st.button("🔍 Mine Strategy for AI Patterns", use_container_width=True, type="primary"):
        with st.spinner("Running deep backtest and extracting indicator features (multi-core)..."):
            try:
                params = settings_repo.backtest_params()
                all_trades = batch_mod.extract_trades(
                    files_to_process, code, params["initial_capital"], params["total_commission"],
                    params["margin"], selected_features,
                )
                if not all_trades:
                    st.error("No trades executed across any datasets.")
                    return
                trades_df = pd.concat(all_trades, ignore_index=True)
                st.info(f"Aggregated {len(trades_df)} historical trades for Machine Learning.")

                result = mine_patterns(trades_df, selected_features, target_metric, int(min_trades_filter))
                if result["error"]:
                    st.error(result["error"])
                    return

                st.metric(
                    label=f"Baseline Pattern Win Rate ({target_metric})",
                    value=f"{result['baseline_win_rate']:.1f}%",
                    help="Raw probability of success before the ML filter.",
                )

                st.subheader("📊 Which Indicators Matter Most?")
                if not result["importance"].empty:
                    st.plotly_chart(feature_importance_bar(result["importance"]), use_container_width=True)

                st.subheader("📜 AI-Discovered High Probability Rules")
                st.markdown("The AI built a decision tree to find indicator ranges that increase your win rate. **Consider adding these to your Strategy Builder Range Zones!**")
                with st.expander("View Raw Decision Tree Logic", expanded=False):
                    st.code(result["tree_text"], language="text")

                if result["rules"]:
                    for i, r in enumerate(result["rules"]):
                        st.success(f"**Pattern {i+1}:** Win Rate **{r['prob']:.1f}%** (Found in {r['samples']} trades)\n\n👉 **IF** {r['rule']}")
                    st.session_state["last_ml_rules"] = result["rules"]
                    st.session_state["last_ml_target"] = target_metric
                elif result["warning"]:
                    st.info(result["warning"])
            except Exception as e:
                st.error(f"Error during ML analysis: {e}")
                st.code(traceback.format_exc(), language="text")

    if st.session_state.get("last_ml_rules"):
        st.divider()
        if st.button("💾 Save Discovered Patterns to Database"):
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
