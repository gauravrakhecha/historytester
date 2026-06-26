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

import streamlit.components.v1 as components

from quantdesk.backtest import batch as batch_mod
from quantdesk.backtest import engine, metrics
from quantdesk.config import QUALITY_BLOCK_THRESHOLD, QUALITY_WARN_THRESHOLD
from quantdesk.core import db, settings_repo
from quantdesk.core import reproducibility as repro
from quantdesk.core.db import get_download_history
from quantdesk.costs import india
from quantdesk.data.loader import load_indexed
from quantdesk.data.quality import validate as validate_quality
from quantdesk.strategy.models import OHLCV, safe_name, selectable_columns
from quantdesk.viz import charts
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


def _quality_gate(df) -> tuple[bool, dict]:
    """Run the data-quality gate (#3). Returns (ok_to_run, report). Blocks below the threshold."""
    q = validate_quality(df.reset_index() if df.index.name == "Timestamp" else df)
    score = q["quality_score"]
    if score < QUALITY_BLOCK_THRESHOLD:
        st.error(f"⛔ Data quality {score}/100 is below the block threshold ({QUALITY_BLOCK_THRESHOLD}). "
                 f"Issues: {', '.join(q['issues']) or 'n/a'}. Fix/redownload the data before backtesting.")
        return False, q
    if score < QUALITY_WARN_THRESHOLD:
        st.warning(f"⚠️ Data quality {score}/100 (below {QUALITY_WARN_THRESHOLD}). "
                   f"Results may be unreliable. Issues: {', '.join(q['issues']) or 'n/a'}.")
    else:
        st.caption(f"✅ Data quality: {score}/100")
    return True, q


def _render_isoos(info: dict) -> None:
    """Show the In-Sample vs Out-of-Sample comparison for an optimized run (#1)."""
    if not info or info.get("no_oos") or "is_stats" not in info:
        if info and info.get("no_oos"):
            st.info(f"✨ Optimizer best params: {info.get('best_params')} "
                    "(dataset too short for an out-of-sample split — results are in-sample).")
        return
    is_s, oos_s = info["is_stats"], info["oos_stats"]
    st.warning("⚠️ **Optimized run** — the headline metrics below are the honest **out-of-sample** "
               "result on data the optimizer never saw. Compare against in-sample to judge overfitting.")
    st.success(f"✨ Best parameters (tuned on in-sample only): {info['best_params']}")

    def _row(label, key, fmt="{:.2f}"):
        a, b = is_s.get(key), oos_s.get(key)
        deg = ""
        try:
            if a not in (None, 0) and not pd.isna(a):
                deg = f"{(float(b) - float(a)) / abs(float(a)) * 100:+.0f}%"
        except Exception:
            deg = ""
        return {"Metric": label, "In-Sample": fmt.format(float(a)) if a is not None and not pd.isna(a) else "–",
                "Out-of-Sample": fmt.format(float(b)) if b is not None and not pd.isna(b) else "–",
                "Δ (OOS vs IS)": deg}

    rows = [
        _row("Return [%]", "Return [%]"),
        _row("Sharpe Ratio", "Sharpe Ratio"),
        _row("Max Drawdown [%]", "Max. Drawdown [%]"),
        _row("Win Rate [%]", "Win Rate [%]"),
        _row("# Trades", "# Trades", "{:.0f}"),
    ]
    st.markdown("##### In-Sample vs Out-of-Sample (overfitting check)")
    st.dataframe(pd.DataFrame(rows).set_index("Metric"), use_container_width=True)


def _render_charges_panel(stats, trades, segment: str, init_cash: float) -> dict:
    """Real India statutory charges as a post-trade pass (#2). Returns the breakdown dict."""
    st.subheader("💸 Real India Charges — Gross vs Net")
    if trades is None or trades.empty:
        st.info("No trades to charge.")
        return {}
    ch = india.charges_for_trades(trades, segment)
    gross_pnl = float(trades["PnL"].sum()) if "PnL" in trades.columns else 0.0
    net_pnl = gross_pnl - ch["total"]
    gross_ret = gross_pnl / init_cash * 100
    net_ret = net_pnl / init_cash * 100
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gross P&L", f"₹{gross_pnl:,.0f}")
    c2.metric("Total Charges", f"₹{ch['total']:,.0f}", help="STT + stamp + exchange + SEBI + brokerage + 18% GST")
    c3.metric("Net P&L (after charges)", f"₹{net_pnl:,.0f}", delta=f"{net_ret-gross_ret:.2f}% vs gross")
    c4.metric("Net Return", f"{net_ret:.2f}%")
    bd = pd.DataFrame(
        [{"Charge": k.replace("_", " ").title(), "₹": ch[k]}
         for k in ["stt", "stamp_duty", "exchange", "sebi", "brokerage", "gst", "total"]]
    ).set_index("Charge")
    st.dataframe(bd, use_container_width=True)
    st.caption(f"Segment: **{segment}** · {ch['num_trades']} trades. Charges computed per leg in Decimal. "
               "This is the honest ₹-accurate cost; the engine's flat commission is a separate equity-curve proxy.")
    ch["gross_pnl"], ch["net_pnl"], ch["net_return_pct"] = gross_pnl, net_pnl, net_ret
    return ch


def _render_quantstats(stats) -> None:
    """QuantStats tearsheet + curated risk block behind an Advanced expander (#4)."""
    with st.expander("📑 Advanced — QuantStats tearsheet & risk analytics", expanded=False):
        if not charts.QUANTSTATS_AVAILABLE:
            st.info("QuantStats is not installed. `pip install quantstats` to enable the tearsheet.")
            return
        rets = metrics.equity_returns(stats, resample_daily=True)
        if len(rets) < 3:
            st.info("Not enough daily data points for a tearsheet.")
            return
        qm = charts.quantstats_metrics(rets)
        if qm:
            cols = st.columns(len(qm))
            for col, (k, v) in zip(cols, qm.items()):
                col.metric(k, v)
        html = charts.quantstats_tearsheet_html(rets)
        if html:
            components.html(html, height=600, scrolling=True)
            st.download_button("📥 Download tearsheet (HTML)", data=html.encode("utf-8"),
                               file_name="quantstats_tearsheet.html", mime="text/html")
        else:
            st.warning("Could not render the QuantStats tearsheet for this run.")


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
    segment = st.selectbox(
        "Charges segment (for real India cost breakdown)", india.SEGMENTS, index=0,
        help="Used only for the honest Gross-vs-Net charge panel (STT/stamp/GST differ by segment).",
    )

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

                # #3 data-quality gate — block clearly-broken data, warn on marginal.
                ok, qreport = _quality_gate(df)
                if not ok:
                    return

                optimize_mode = opt_mode if (enable_optimizer and opt_kwargs_dynamic) else None
                maximize_key = _MAXIMIZE_MAP.get(opt_maximize, "Return [%]")
                pbar = st.progress(0, text="Running...") if optimize_mode else None

                def _cb(p, t):
                    if pbar:
                        pbar.progress(min(1.0, p), text=t)

                stats, info = engine.run_single(
                    df, code, init_cash, params["total_commission"], params["margin"],
                    optimize_mode=optimize_mode, opt_kwargs=opt_kwargs_dynamic,
                    maximize=maximize_key, n_trials=int(n_trials), progress_cb=_cb if optimize_mode else None,
                )
                _render_isoos(info)  # #1 IS/OOS comparison for optimized runs

                st.subheader("Performance Metrics")
                stats["Return (Ann.) [%]"] = metrics.annualized_return(stats)
                strat_instance = stats["_strategy"]
                clean_stats = metrics.clean_stats_series(stats, init_cash, strat_instance)
                sm = metrics.extract_scalar_metrics(stats)  # #5 incl. buried metrics

                st.markdown("##### Key Metrics")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Initial Capital", f"₹{init_cash:,.2f}")
                m2.metric("Return (Ann.)", f"{round(stats.get('Return (Ann.) [%]', 0), 2)}%")
                m3.metric("Max Drawdown", f"{round(stats.get('Max. Drawdown [%]', 0), 2)}%")
                m4.metric("Win Rate", f"{round(stats.get('Win Rate [%]', 0), 2)}%")
                m5, m6, m7, m8 = st.columns(4)
                m5.metric("Total Trades", int(stats.get("# Trades", 0)))
                sharpe = stats.get("Sharpe Ratio", 0)
                m6.metric("Sharpe Ratio", round(sharpe, 2) if not pd.isna(sharpe) else "N/A")
                m7.metric("Sortino", sm["sortino"])
                m8.metric("Calmar", sm["calmar"])
                # #5 previously-buried metrics, now surfaced
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Profit Factor", sm["profit_factor"], help=">1 means winners outweigh losers")
                b2.metric("Expectancy [%]", sm["expectancy_pct"], help="Avg % return per trade")
                b3.metric("SQN", sm["sqn"], help="System Quality Number (>2.5 good, >3 excellent)")
                b4.metric("Buy & Hold [%]", sm["buy_hold_return_pct"], help="Benchmark: holding the asset")
                if int(stats.get("# Trades", 0)) < 30:
                    st.caption("⚠️ Fewer than 30 trades — metrics are small-sample and noisy; treat with caution.")
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

                # #2 Real India charges — Gross vs Net
                charges = _render_charges_panel(stats, trades, segment, init_cash)

                # #4 QuantStats tearsheet + risk analytics
                _render_quantstats(stats)

                # --- Save to DB (with buried metrics #5, charges #2, repro hashes #9, equity #6) ---
                strat_label = st.session_state.get("strategy_label", "Custom")
                config_dict = {
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
                try:
                    eqd = metrics.equity_curve_daily(stats)
                    equity_json = json.dumps([[str(d.date()), round(float(v), 2)] for d, v in eqd.items()])
                except Exception:
                    equity_json = "[]"
                db.insert_backtest({
                    "strategy_name": strat_label,
                    "symbol": selected_row.symbol, "timeframe": selected_row.interval,
                    "start_date": str(selected_row.start_date), "end_date": str(selected_row.end_date),
                    "parameters": json.dumps(info.get("best_params", {}) if info else {}),
                    "return_pct": sm["return_pct"], "max_drawdown_pct": sm["max_drawdown_pct"],
                    "win_rate_pct": sm["win_rate_pct"], "num_trades": sm["num_trades"],
                    "sharpe_ratio": sm["sharpe"], "plot_file": "plotly_native",
                    "full_stats": clean_stats.to_json(), "strategy_config": json.dumps(config_dict),
                    "profit_factor": sm["profit_factor"], "expectancy_pct": sm["expectancy_pct"],
                    "sqn": sm["sqn"], "buy_hold_return_pct": sm["buy_hold_return_pct"],
                    "quality_score": qreport.get("quality_score"), "segment": segment,
                    "net_return_pct": charges.get("net_return_pct"), "total_charges": charges.get("total"),
                    "dataset_hash": repro.hash_file(selected_file),
                    "config_hash": repro.hash_config(config_dict),
                    "strategy_hash": repro.hash_text(code), "code_hash": repro.code_version(),
                    "equity_curve_json": equity_json,
                })
                st.success("Backtest saved to database (with charges, quality, repro hashes & equity curve).")
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
            st.caption("ℹ️ These are **independent single-asset runs** (each used the full capital) — the "
                       "AVERAGE row is a mean across runs, **not** a combined-portfolio result. "
                       "Equity-only runs may be **unadjusted for corporate actions** depending on how the file was downloaded.")
            st.dataframe(table.style.format(precision=3), use_container_width=True)
            batch_mod.save_batch_to_db(results, st.session_state.get("strategy_label", "Batch Custom"),
                                       st.session_state.get("indicators", []), code=code)
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
