"""Backtest Results database browser page (upstox_app.py:2909-3002)."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from quantdesk.core.db import connect


def render(ctx):
    st.header("📊 Backtest Results Database")
    with connect() as conn:
        try:
            df = pd.read_sql_query(
                "SELECT id, strategy_name, symbol, timeframe, start_date, end_date, return_pct, "
                "max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio, created_at "
                "FROM backtests ORDER BY created_at DESC",
                conn,
            )
            if df.empty:
                st.info("No backtests run yet.")
                return

            for col in df.select_dtypes(include=["float"]).columns:
                df[col] = df[col].round(3)
            st.dataframe(df, use_container_width=True, height=400)

            st.subheader("🔍 Inspect a Specific Run")
            run_labels = [f"#{row.id} | {row.strategy_name} | {row.symbol} | {row.timeframe} | Return: {row.return_pct}%" for _, row in df.iterrows()]
            selected_run_idx = st.selectbox("Select Run", range(len(run_labels)), format_func=lambda x: run_labels[x])
            run_id = df.iloc[selected_run_idx]["id"]

            if run_id:
                c = conn.cursor()
                c.execute("SELECT full_stats, strategy_config FROM backtests WHERE id=?", (run_id,))
                res = c.fetchone()

                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("#### 📊 Full Performance Metrics")
                    if res and res[0]:
                        try:
                            st.json(json.loads(res[0]))
                        except Exception:
                            st.warning("Could not parse stored metrics.")
                    else:
                        st.info("No detailed metrics stored for this run.")

                with col_b:
                    st.markdown("#### 🛠️ Strategy Configuration Used")
                    if res and res[1]:
                        try:
                            config = json.loads(res[1])
                            if config.get("indicators"):
                                st.markdown("**Indicators:**")
                                for ind in config["indicators"]:
                                    st.markdown(f"- **{ind['name']}** ({ind['type']}) — Source: {ind['source']}, Params: {ind.get('params', {})}")
                            for label, key in [("Buy Rules", "buy_rules"), ("Sell Rules", "sell_rules"), ("🚫 No-Trade Zone Rules", "no_trade_rules")]:
                                if config.get(key):
                                    st.markdown(f"**{label}:**")
                                    for r in config[key]:
                                        v2 = r["var2"] if not r.get("is_num") else f"_{r['var2']}_"
                                        st.markdown(f"- {r.get('logic', 'IF')} {r['var1']} {r['operator']} {v2}")
                            if config.get("range_zones"):
                                st.markdown("**🎯 Advanced Range Zones:**")
                                for z in config["range_zones"]:
                                    st.markdown(f"- **{z.get('label', 'Zone')}** ({z['indicator']} in [{z['min_val']}, {z['max_val']}]) ➔ {z['action']}")
                            rm = config.get("risk_management", {})
                            if rm:
                                st.markdown(f"**Strategy Direction:** {rm.get('strat_dir', 'Long Only')}")
                                st.markdown(f"**Risk Management:** {rm.get('mode', 'N/A')}")
                                if rm.get("sl_pct", 0) > 0:
                                    st.markdown(f"- Stop Loss: {rm['sl_pct']}%")
                                if rm.get("tp_pct", 0) > 0:
                                    st.markdown(f"- Take Profit: {rm['tp_pct']}%")
                                if rm.get("trail_pct", 0) > 0:
                                    st.markdown(f"- Trailing Stop: {rm['trail_pct']}%")
                        except Exception:
                            st.warning("Could not parse stored strategy config.")
                    else:
                        st.info("No strategy config stored for this run.")

            st.markdown("---")
            st.subheader("🗑️ Manage Results")
            delete_id = st.number_input("Enter Run ID to delete", min_value=1, step=1)
            if st.button("Delete Run"):
                try:
                    c = conn.cursor()
                    c.execute("DELETE FROM backtests WHERE id=?", (delete_id,))
                    conn.commit()
                    st.success(f"Run #{delete_id} deleted.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to delete: {e}")
        except Exception:
            st.info("No backtests run yet.")
