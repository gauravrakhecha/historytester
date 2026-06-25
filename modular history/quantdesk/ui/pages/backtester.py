"""
Backtester & Optimizer page (upstox_app.py:2380-2907).

Single-file + folder-batch modes, grid/Optuna optimisation, full metrics, year-wise table,
enriched trade log, Plotly chart, and DB persistence. Wired to the verified backend
(engine/optimizer/batch/metrics/viz). Local — no broker auth required.
"""
from __future__ import annotations

import json
import os
import re
import traceback

import numpy as np
import pandas as pd
import streamlit as st

from quantdesk.backtest import batch as batch_mod
from quantdesk.backtest import engine, metrics
from quantdesk.core import settings_repo
from quantdesk.core.db import connect, get_download_history
from quantdesk.data.loader import load_indexed
from quantdesk.strategy.models import selectable_columns
from quantdesk.viz.charts import render_plotly_backtest

# Optimiser "maximize" choices -> backtesting.py stat keys (₹ label maps to the $ key).
_MAXIMIZE_MAP = {
    "Return [%]": "Return [%]",
    "Sharpe Ratio": "Sharpe Ratio",
    "Sortino Ratio": "Sortino Ratio",
    "Calmar Ratio": "Calmar Ratio",
    "Win Rate [%]": "Win Rate [%]",
    "Equity Final [₹]": "Equity Final [$]",
}


def _lot_override(code: str) -> str:
    parsed_lot = 1
    m = re.search(r"lot_size\s*=\s*(\d+)", code)
    if m:
        parsed_lot = int(m.group(1))
    new_lot = st.number_input("Override Lot Size", value=parsed_lot, min_value=1, step=1, help="Change lot size for this backtest without recompiling.")
    if new_lot != parsed_lot:
        code = re.sub(r"lot_size\s*=\s*\d+", f"lot_size = {new_lot}", code)
    return code


def _build_optimizable():
    optimizable = []
    for ind in st.session_state.get("indicators", []):
        name = "".join([c if c.isalnum() else "_" for c in ind["name"]])
        if name and name[0].isdigit():
            name = "_" + name
        for k, v in ind.get("params", {}).items():
            optimizable.append({"label": f"{ind['name']} - {k.capitalize()}", "var": f"{name}_{k}", "val": v})
    for i, r in enumerate(st.session_state.get("buy_rules", [])):
        if r.get("is_num"):
            optimizable.append({"label": f"Buy Rule {i+1} Threshold", "var": f"buy_rule_{i}_val", "val": r["var2"]})
    for i, r in enumerate(st.session_state.get("sell_rules", [])):
        if r.get("is_num"):
            optimizable.append({"label": f"Sell Rule {i+1} Threshold", "var": f"sell_rule_{i}_val", "val": r["var2"]})
    for i, r in enumerate(st.session_state.get("no_trade_rules", [])):
        if r.get("is_num"):
            optimizable.append({"label": f"No-Trade Rule {i+1} Threshold", "var": f"no_trade_rule_{i}_val", "val": r["var2"]})
    for i, z in enumerate(st.session_state.get("range_zones", [])):
        optimizable.append({"label": f"Zone {i+1} Min", "var": f"zone_{i}_min", "val": z["min_val"]})
        optimizable.append({"label": f"Zone {i+1} Max", "var": f"zone_{i}_max", "val": z["max_val"]})
        if z["action"] == "Tighten Trailing Stop":
            optimizable.append({"label": f"Zone {i+1} Tight SL %", "var": f"zone_{i}_tight_pct", "val": z.get("tight_pct", 1.0)})
    return optimizable


def _render_single(ctx, history):
    if history.empty:
        st.warning("No downloaded data found. Please download data first.")
        return
    options = history["file_path"].tolist()
    labels = [f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}" for _, row in history.iterrows()]
    selected_idx = st.selectbox("Select Dataset", range(len(options)), format_func=lambda x: labels[x])
    selected_file = options[selected_idx]
    selected_row = history.iloc[selected_idx]
    st.write(f"**Selected File:** `{selected_file}`")

    code = st.session_state.get("custom_strategy_code", "")
    if not code:
        st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        return
    with st.expander("View Active Compiled Strategy"):
        st.code(code, language="python")

    st.subheader("Runtime Overrides")
    code = _lot_override(code)

    st.subheader("Optimizer Settings")
    enable_optimizer = st.checkbox("Enable Grid Search Optimizer")
    opt_kwargs_dynamic, opt_maximize, opt_mode, n_trials = {}, "", "", 50

    if enable_optimizer:
        st.info("Select parameters to optimize. The backtester simulates combinations to find the most profitable settings.")
        opt_mode = st.radio("Optimization Engine", ["Optuna (AI Bayesian Search - Recommended)", "Grid Search (Exhaustive)"], horizontal=True)
        if opt_mode.startswith("Optuna"):
            st.caption("Optuna uses ML to find profitable parameters in a fraction of the time.")
            n_trials = st.number_input("Number of AI Trials (Guesses)", min_value=10, value=50, max_value=1000)

        optimizable = _build_optimizable()
        st.markdown("### Select Parameters to Optimize")
        if not optimizable:
            st.warning("No optimizable parameters found. Add indicators with numeric params or rules with static numbers.")
        else:
            for opt in optimizable:
                col1, col2 = st.columns([1, 2])
                do_opt = col1.checkbox(f"Optimize {opt['label']} (Current: {opt['val']})", key=f"opt_cb_{opt['var']}")
                if do_opt:
                    c1, c2, c3 = col2.columns(3)
                    start = c1.number_input("Start", value=float(opt["val"]), key=f"s_{opt['var']}")
                    end = c2.number_input("End", value=float(opt["val"]) * 1.5, key=f"e_{opt['var']}")
                    step = c3.number_input("Step", value=1.0 if isinstance(opt["val"], int) else 0.5, key=f"step_{opt['var']}")
                    if isinstance(opt["val"], int) and start.is_integer() and end.is_integer() and step.is_integer():
                        opt_kwargs_dynamic[opt["var"]] = range(int(start), int(end), int(step))
                    else:
                        opt_kwargs_dynamic[opt["var"]] = np.arange(float(start), float(end), float(step)).tolist()
        opt_maximize = st.selectbox("Maximize Metric", list(_MAXIMIZE_MAP.keys()))

    if st.button("Run Backtest", use_container_width=True):
        if not os.path.exists(selected_file):
            st.error("Data file not found on disk.")
            return
        with st.spinner("Running Backtest..."):
            try:
                df = load_indexed(selected_file)
                params = settings_repo.backtest_params()
                init_cash = params["initial_capital"]

                optimize_mode = opt_mode if (enable_optimizer and opt_kwargs_dynamic) else None
                maximize_key = _MAXIMIZE_MAP.get(opt_maximize, "Return [%]")
                pbar = st.progress(0, text="Running...") if optimize_mode else None

                def _cb(p, t):
                    if pbar:
                        pbar.progress(min(1.0, p), text=t)

                stats, best = engine.run_single(
                    df, code, init_cash, params["total_commission"], params["margin"],
                    optimize_mode=optimize_mode, opt_kwargs=opt_kwargs_dynamic,
                    maximize=maximize_key, n_trials=int(n_trials), progress_cb=_cb if optimize_mode else None,
                )
                if best is not None:
                    st.success(f"✨ Optimizer found best parameters: {best}")

                st.subheader("Performance Metrics")
                stats["Return (Ann.) [%]"] = metrics.annualized_return(stats)
                strat_instance = stats["_strategy"]
                clean_stats = metrics.clean_stats_series(stats, init_cash, strat_instance)

                st.markdown("##### Key Metrics")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Initial Capital", f"₹{init_cash:,.2f}")
                m2.metric("Return (Ann.)", f"{round(stats.get('Return (Ann.) [%]', 0), 2)}%")
                m3.metric("Max Drawdown", f"{round(stats.get('Max. Drawdown [%]', 0), 2)}%")
                m4.metric("Win Rate", f"{round(stats.get('Win Rate [%]', 0), 2)}%")
                m5, m6, m7, m8 = st.columns(4)
                m5.metric("Start Date", str(clean_stats.get("Start", "N/A")).split()[0])
                m6.metric("End Date", str(clean_stats.get("End", "N/A")).split()[0])
                m7.metric("Total Trades", int(stats.get("# Trades", 0)))
                sharpe = stats.get("Sharpe Ratio", 0)
                m8.metric("Sharpe Ratio", round(sharpe, 2) if not pd.isna(sharpe) else "N/A")
                st.divider()

                # Cast to str for display: the stats Series mixes floats with Timedelta
                # (Duration, Max Drawdown Duration) which Arrow can't serialise directly.
                st.dataframe(pd.DataFrame(clean_stats.astype(str), columns=["Value"]), use_container_width=True)

                st.subheader("Year-wise Performance")
                try:
                    yearly_df = metrics.yearly_performance(stats["_equity_curve"])
                    if not yearly_df.empty:
                        st.dataframe(yearly_df, use_container_width=True)
                except Exception:
                    st.warning("Could not compute year-wise performance.")

                st.subheader("Detailed Trade Log (with Indicator Snapshots)")
                trades = stats["_trades"].copy()
                if not trades.empty:
                    variables = selectable_columns(st.session_state.get("indicators", []))
                    trades = metrics.enrich_trades(trades, strat_instance, variables)
                    for col in trades.select_dtypes(include=["float"]).columns:
                        trades[col] = trades[col].round(3)
                    st.dataframe(trades, use_container_width=True)
                    st.download_button(
                        label="📥 Export Detailed Trade Log to CSV",
                        data=trades.to_csv(index=False).encode("utf-8"),
                        file_name="enriched_trade_log.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.info("No trades executed.")

                st.subheader("Interactive Strategy Chart (Plotly)")
                try:
                    out_df = df.copy()
                    out_df["Buy_Signal"] = 0
                    out_df["Sell_Signal"] = 0
                    if not trades.empty:
                        for _, t in trades.iterrows():
                            if t["EntryBar"] < len(out_df):
                                out_df.iloc[t["EntryBar"], out_df.columns.get_loc("Buy_Signal")] = 1
                            if t["ExitBar"] < len(out_df):
                                out_df.iloc[t["ExitBar"], out_df.columns.get_loc("Sell_Signal")] = 1
                    from quantdesk.strategy.models import OHLCV, safe_name
                    for var_name in [c for c in selectable_columns(st.session_state.get("indicators", [])) if c not in OHLCV]:
                        attr = safe_name(var_name)
                        if hasattr(strat_instance, attr):
                            arr = getattr(strat_instance, attr)
                            if hasattr(arr, "__len__") and len(arr) == len(out_df):
                                out_df[var_name] = list(arr)
                    st.plotly_chart(render_plotly_backtest(out_df, trades), use_container_width=True)
                except Exception as plot_e:
                    st.warning(f"Could not generate Plotly chart: {plot_e}")

                # Save to DB
                strat_label = st.session_state.get("strategy_label", "Custom")
                strat_config_json = json.dumps(
                    {
                        "indicators": st.session_state.get("indicators", []),
                        "buy_rules": st.session_state.get("buy_rules", []),
                        "sell_rules": st.session_state.get("sell_rules", []),
                        "no_trade_rules": st.session_state.get("no_trade_rules", []),
                        "range_zones": st.session_state.get("range_zones", []),
                        "risk_management": {
                            "mode": st.session_state.get("rm_mode", "None (Use Sell Rules Only)"),
                            "sl_pct": st.session_state.get("sl_pct", 0),
                            "tp_pct": st.session_state.get("tp_pct", 0),
                            "trail_pct": st.session_state.get("trail_pct", 0),
                            "strat_dir": st.session_state.get("strat_dir", "Long Only"),
                        },
                    }
                )
                stats_json = clean_stats.to_json()
                with connect() as conn:
                    c = conn.cursor()
                    try:
                        ret_pct = round(float(stats["Return [%]"]), 3) if "Return [%]" in stats.index else 0.0
                        mdd_pct = round(float(stats["Max. Drawdown [%]"]), 3) if "Max. Drawdown [%]" in stats.index else 0.0
                        wr_pct = round(float(stats["Win Rate [%]"]), 3) if "Win Rate [%]" in stats.index else 0.0
                        n_trades = int(stats["# Trades"]) if "# Trades" in stats.index else 0
                        sharpe_v = round(float(stats["Sharpe Ratio"]), 3) if "Sharpe Ratio" in stats.index and not pd.isna(stats["Sharpe Ratio"]) else 0.0
                    except Exception:
                        ret_pct, mdd_pct, wr_pct, n_trades, sharpe_v = 0.0, 0.0, 0.0, 0, 0.0
                    c.execute(
                        """INSERT INTO backtests
                           (strategy_name, symbol, timeframe, start_date, end_date, parameters,
                            return_pct, max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio,
                            plot_file, full_stats, strategy_config)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (strat_label, selected_row.symbol, selected_row.interval, selected_row.start_date, selected_row.end_date,
                         "{}", ret_pct, mdd_pct, wr_pct, n_trades, sharpe_v, "plotly_native", stats_json, strat_config_json),
                    )
                st.success("Backtest saved to database!")
            except Exception as e:
                st.error(f"Error during backtest: {e}")
                st.code(traceback.format_exc(), language="text")


def _render_batch(ctx, history):
    st.info("Batch Mode: Run the active strategy across all .csv/.parquet files in a directory using multi-core processing.")
    batch_folder = st.text_input("Enter Full Path to Folder containing data files", value=ctx.default_parquet_dir)
    code = st.session_state.get("custom_strategy_code", "")
    if not code:
        st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        return
    with st.expander("View Active Compiled Strategy"):
        st.code(code, language="python")
    st.subheader("Runtime Overrides")
    code = _lot_override(code)

    if st.button("Run Batch Backtest", use_container_width=True):
        if not batch_folder or not os.path.exists(batch_folder):
            st.error("Invalid folder path.")
            return
        files = batch_mod.find_data_files(batch_folder)
        if not files:
            st.warning("No .csv or .parquet files found in the directory.")
            return
        st.write(f"Found {len(files)} files. Starting multi-core batch run...")
        pbar = st.progress(0, text="0% - Starting Batch Backtest...")
        params = settings_repo.backtest_params()
        strat_dir_name = st.session_state.get("strat_dir", "Long Only")

        def _cb(p, t):
            pbar.progress(min(1.0, p), text=t)

        out = batch_mod.run_batch(files, code, params["initial_capital"], params["total_commission"], params["margin"], strat_dir_name, _cb)
        results, failed, table = out["results"], out["failed"], out["table"]

        if results:
            st.success(f"Batch completed: {len(results)} successful, {len(failed)} failed.")
            st.dataframe(table.style.format(precision=3), use_container_width=True)
            batch_mod.save_batch_to_db(results, st.session_state.get("strategy_label", "Batch Custom"), st.session_state.get("indicators", []))
            st.download_button(
                label="📥 Export Batch Results to CSV",
                data=table.to_csv(index=False).encode("utf-8"),
                file_name="batch_backtest_results.csv",
                mime="text/csv",
                use_container_width=True,
            )
            if failed:
                with st.expander(f"⚠️ {len(failed)} Files Failed — Click to see details"):
                    st.dataframe(pd.DataFrame(failed), use_container_width=True)
        else:
            st.error("Batch completed but no files were successfully backtested.")
            if failed:
                st.dataframe(pd.DataFrame(failed), use_container_width=True)


def render(ctx):
    st.header("🚀 Backtester & Optimizer (Plotly Edition)")
    bt_mode = st.radio("Backtest Mode", ["Single File", "Folder Batch Mode"], horizontal=True)
    st.markdown("---")
    history = get_download_history()
    if bt_mode == "Single File":
        _render_single(ctx, history)
    else:
        _render_batch(ctx, history)

    st.markdown("---")
    with st.expander("📖 How to Use the Backtester & Optimizer — Complete Guide", expanded=False):
        st.markdown(
            """
Before a full backtest, use the **Strategy & Indicator Plotter** to verify indicator calculations.

### 📊 Understanding the Results
| Metric | What It Means |
|--------|---------------|
| **Return [%]** | Total percentage return over the test period |
| **Max. Drawdown [%]** | Worst peak-to-trough decline (lower = better) |
| **Win Rate [%]** | Percentage of trades that were profitable |
| **Sharpe Ratio** | Risk-adjusted return (>1 good, >2 excellent) |
| **Sortino Ratio** | Like Sharpe but penalizes only downside volatility |
| **Calmar Ratio** | Return divided by max drawdown |
| **# Trades** | Total number of completed trades |

### ⚙️ Optimizer
Check the box next to any indicator parameter and enter a range (e.g. `10, 50, 5`). The engine
tests combinations (Optuna Bayesian or exhaustive Grid) to find the best settings.

### 📦 Folder Batch Mode
Point the app to a folder of data files; the engine runs your strategy on each (multi-core) and
shows a comparison table with an AVERAGE row. Failed files are listed separately.

All amounts are in **₹** — configure initial capital in Settings.
            """
        )
