"""
Backtest Results database browser + leaderboard page (upstox_app.py:2909-3002, extended).

v5 (#6): cross-run comparison/leaderboard (multiselect → side-by-side metrics + overlaid equity
curves), and the per-run inspector now shows a plain-language, green/amber/red interpretation
instead of a raw ``st.json`` dump. The legacy detailed-config view and delete are preserved.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from quantdesk.core.db import connect
from quantdesk.viz.charts import equity_overlay

# (metric_key, label, good>=, amber>=, higher_is_better)
_THRESHOLDS = [
    ("sharpe_ratio", "Sharpe", 1.0, 0.5, True),
    ("sqn", "SQN", 2.5, 1.6, True),
    ("profit_factor", "Profit Factor", 1.5, 1.0, True),
    ("win_rate_pct", "Win Rate %", 50, 40, True),
    ("max_drawdown_pct", "Max Drawdown %", -20, -40, False),  # closer to 0 is better
]


def _badge(metric_key, value, good, amber, higher_is_better) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "⚪ n/a"
    if higher_is_better:
        emoji = "🟢" if v >= good else ("🟡" if v >= amber else "🔴")
    else:  # for drawdown: -10 is better than -50; "good" = -20 (>= -20 is fine)
        emoji = "🟢" if v >= good else ("🟡" if v >= amber else "🔴")
    return f"{emoji} {v:g}"


def _equity_series_from_json(blob) -> pd.Series | None:
    if not blob:
        return None
    try:
        data = json.loads(blob)
        if not data:
            return None
        pairs = list(data.items()) if isinstance(data, dict) else data
        idx = pd.to_datetime([d for d, _ in pairs])
        return pd.Series([float(v) for _, v in pairs], index=idx).sort_index()
    except Exception:
        return None


def render(ctx):
    st.header("📊 Backtest Results Database")
    with connect() as conn:
        try:
            df = pd.read_sql_query(
                "SELECT id, strategy_name, symbol, timeframe, return_pct, net_return_pct, "
                "max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio, sqn, profit_factor, "
                "buy_hold_return_pct, quality_score, total_charges, created_at "
                "FROM backtests ORDER BY created_at DESC",
                conn,
            )
        except Exception:
            st.info("No backtests run yet.")
            return

        if df.empty:
            st.info("No backtests run yet.")
            return

        for col in df.select_dtypes(include=["float"]).columns:
            df[col] = df[col].round(3)
        st.dataframe(df, width="stretch", height=360)

        # --- #6 Leaderboard / cross-run comparison ---
        st.subheader("🏆 Compare Runs")
        run_opts = {f"#{r.id} | {r.strategy_name} | {r.symbol} {r.timeframe} | ret {r.return_pct}%": int(r.id)
                    for _, r in df.iterrows()}
        picked = st.multiselect("Select runs to compare", list(run_opts.keys()),
                                default=list(run_opts.keys())[: min(3, len(run_opts))])
        picked_ids = [run_opts[p] for p in picked]
        if picked_ids:
            comp = df[df["id"].isin(picked_ids)].set_index("id")
            metric_cols = ["strategy_name", "symbol", "timeframe", "return_pct", "net_return_pct",
                           "buy_hold_return_pct", "max_drawdown_pct", "win_rate_pct", "sharpe_ratio",
                           "sqn", "profit_factor", "num_trades", "quality_score", "total_charges"]
            st.dataframe(comp[[c for c in metric_cols if c in comp.columns]].T, width="stretch")

            curves = {}
            with connect() as c2:
                for rid in picked_ids:
                    row = c2.execute("SELECT equity_curve_json FROM backtests WHERE id=?", (rid,)).fetchone()
                    s = _equity_series_from_json(row[0] if row else None)
                    if s is not None:
                        curves[f"#{rid}"] = s
            if curves:
                st.plotly_chart(equity_overlay(curves), width="stretch")
            else:
                st.caption("No stored equity curves for the selected runs (run new backtests to populate).")

        # --- per-run inspector (plain-language interpretation) ---
        st.subheader("🔍 Inspect a Specific Run")
        run_labels = [f"#{row.id} | {row.strategy_name} | {row.symbol} | {row.timeframe} | Return: {row.return_pct}%"
                      for _, row in df.iterrows()]
        sel_idx = st.selectbox("Select Run", range(len(run_labels)), format_func=lambda x: run_labels[x])
        run = df.iloc[sel_idx]
        run_id = int(run["id"])

        st.markdown("##### How healthy is this run?")
        cols = st.columns(len(_THRESHOLDS))
        for col, (key, label, good, amber, hib) in zip(cols, _THRESHOLDS):
            col.metric(label, _badge(key, run.get(key), good, amber, hib))
        st.caption("🟢 healthy · 🟡 caution · 🔴 warning. Rules of thumb: Sharpe >1 good / >2 excellent; "
                   "SQN >2.5 good; Profit Factor >1.5; keep Max Drawdown shallow.")
        if run.get("net_return_pct") is not None and not pd.isna(run.get("net_return_pct")):
            gross, net, bh = run.get("return_pct"), run.get("net_return_pct"), run.get("buy_hold_return_pct")
            msg = f"After real India charges, this run returned **{net:g}%** (gross {gross:g}%)."
            if bh is not None and not pd.isna(bh):
                verdict = "beat" if (net or 0) > (bh or 0) else "trailed"
                msg += f" It **{verdict}** buy & hold ({bh:g}%)."
            st.info(msg)
        if (run.get("num_trades") or 0) < 30:
            st.warning("⚠️ Fewer than 30 trades — treat these metrics as small-sample/noisy.")

        with connect() as conn2:
            res = conn2.execute(
                "SELECT strategy_config, dataset_hash, config_hash, strategy_hash, code_hash, segment "
                "FROM backtests WHERE id=?", (run_id,)
            ).fetchone()
        if res:
            with st.expander("🛠️ Strategy configuration & reproducibility", expanded=False):
                h1, h2 = st.columns(2)
                h1.caption(f"segment: `{res[5]}`")
                h1.caption(f"dataset_hash: `{res[1]}`  ·  code_hash: `{res[4]}`")
                h2.caption(f"config_hash: `{res[2]}`  ·  strategy_hash: `{res[3]}`")
                if res[0]:
                    try:
                        config = json.loads(res[0])
                        if config.get("indicators"):
                            st.markdown("**Indicators:** " + ", ".join(
                                f"{i['name']} ({i['type']})" for i in config["indicators"]))
                        for label, key in [("Buy", "buy_rules"), ("Sell", "sell_rules"), ("No-Trade", "no_trade_rules")]:
                            if config.get(key):
                                st.markdown(f"**{label} rules:**")
                                for r in config[key]:
                                    v2 = r["var2"] if not r.get("is_num") else f"_{r['var2']}_"
                                    st.markdown(f"- {r.get('logic', 'IF')} {r['var1']} {r['operator']} {v2}")
                        rm = config.get("risk_management", {})
                        if rm:
                            st.markdown(f"**Direction:** {rm.get('strat_dir', 'Long Only')} · **Risk:** {rm.get('mode', 'N/A')}")
                    except Exception:
                        st.warning("Could not parse stored strategy config.")

        st.markdown("---")
        st.subheader("🗑️ Manage Results")
        # default the delete target to the run currently being inspected above
        delete_id = st.number_input("Run ID to delete", min_value=1, step=1, value=int(run_id))
        confirm = st.checkbox(
            f"Yes, permanently delete run #{int(delete_id)} — this cannot be undone.",
            key="confirm_delete_run")
        if st.button("Delete Run", type="primary", disabled=not confirm):
            try:
                with connect() as conn3:
                    cur = conn3.execute("DELETE FROM backtests WHERE id=?", (int(delete_id),))
                    deleted = cur.rowcount
                if deleted:
                    st.success(f"Run #{int(delete_id)} deleted.")
                    st.rerun()
                else:
                    st.warning(f"No run with ID #{int(delete_id)} — nothing deleted.")
            except Exception as e:
                st.error(f"Failed to delete: {e}")
