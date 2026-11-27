"""Strategy & Indicator Plotter page (upstox_app.py:2268-2378)."""
from __future__ import annotations

import os
import traceback

import streamlit as st
import streamlit.components.v1 as components

from quantdesk.core.db import get_download_history
from quantdesk.data.loader import load_indexed
from quantdesk.strategy.compiler import compile_strategy
from quantdesk.strategy.models import OHLCV, classify_columns, safe_name, selectable_columns
from quantdesk.viz.charts import render_openalgo_backtest, render_plotly_backtest


def _openalgo_chart_height(indicators) -> int:
    if not indicators:
        return 700
    panels = {
        info["group"]
        for info in classify_columns(indicators).values()
        if info.get("placement") == "panel"
    }
    return min(1060, 700 + 120 * len(panels))


def render(ctx):
    st.header("📈 Strategy & Indicator Plotter")
    st.markdown(
        "Visualize indicator values and verify buy/sell signals on your data **before** running a "
        "backtest. You can also export the plotted data to CSV."
    )

    history = get_download_history()
    code = st.session_state.get("custom_strategy_code", "")

    if not code:
        st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        return
    if history.empty:
        st.warning("No downloaded data found. Please download data first.")
        return

    options = history["file_path"].tolist()
    labels = [f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}" for _, row in history.iterrows()]
    selected_idx = st.selectbox("Select Dataset", range(len(options)), format_func=lambda x: labels[x], key="signal_inspector_ds")
    selected_file = options[selected_idx]

    chart_engine = st.radio(
        "Chart Engine",
        ["OpenAlgo Interactive (Default)", "Plotly"],
        index=0,
        horizontal=True,
        key="plotter_chart_engine",
        help="OpenAlgo Interactive renders fast offline canvas candlesticks, overlays, sub-panels, and trade markers.",
    )

    if st.button("🔍 Generate Signal Sheet", width="stretch"):
        if not os.path.exists(selected_file):
            st.error("Data file not found on disk.")
            return
        with st.spinner("Calculating indicators and signals..."):
            try:
                # Lazy import: `backtesting` pulls in bokeh (~2.5s cold). Importing it here, under the
                # spinner, keeps switching TO this page instant — the cost lands on the click, where
                # the user already expects work, instead of freezing the page transition.
                from backtesting import Backtest

                df = load_indexed(selected_file)

                strategy_class = compile_strategy(code)
                bt = Backtest(df, strategy_class, cash=100000, finalize_trades=True)
                stats = bt.run()
                strat_instance = stats["_strategy"]

                out_df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

                indicators = st.session_state.get("indicators", [])
                indicator_names = [c for c in selectable_columns(indicators) if c not in OHLCV]
                for var_name in indicator_names:
                    attr = safe_name(var_name)
                    if hasattr(strat_instance, attr):
                        arr = getattr(strat_instance, attr)
                        if hasattr(arr, "__len__") and len(arr) == len(out_df):
                            out_df[var_name] = list(arr)

                trades = stats["_trades"]
                out_df["Buy_Signal"] = 0
                out_df["Sell_Signal"] = 0
                if not trades.empty:
                    for _, t in trades.iterrows():
                        if t["EntryBar"] < len(out_df):
                            out_df.iloc[t["EntryBar"], out_df.columns.get_loc("Buy_Signal")] = 1
                        if t["ExitBar"] < len(out_df):
                            out_df.iloc[t["ExitBar"], out_df.columns.get_loc("Sell_Signal")] = 1

                for col in out_df.select_dtypes(include=["float"]).columns:
                    out_df[col] = out_df[col].round(3)

                st.success(f"Signal sheet generated: {len(out_df)} rows, {len(indicator_names)} indicators, {len(trades)} trades found.")
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Total Bars", len(out_df))
                mc2.metric("Buy Signals", int(out_df["Buy_Signal"].sum()))
                mc3.metric("Sell Signals", int(out_df["Sell_Signal"].sum()))
                st.dataframe(out_df, width="stretch", height=500)

                st.download_button(
                    label="📥 Export Signal Sheet to CSV",
                    data=out_df.to_csv().encode("utf-8"),
                    file_name="signal_inspector_output.csv",
                    mime="text/csv",
                    width="stretch",
                )

                if chart_engine == "Plotly":
                    st.subheader("Interactive Signal Visualization (Plotly)")
                    try:
                        st.plotly_chart(render_plotly_backtest(out_df, trades, indicators), width="stretch")
                    except Exception as plot_e:
                        st.warning(f"Could not generate interactive chart: {plot_e}")
                else:
                    st.subheader("Interactive Signal Visualization (OpenAlgo)")
                    try:
                        chart_height = _openalgo_chart_height(indicators)
                        html = render_openalgo_backtest(out_df, trades, indicators, height=chart_height)
                        components.html(html, height=chart_height, scrolling=False)
                    except Exception as plot_e:
                        st.warning(f"Could not generate interactive chart: {plot_e}")
            except Exception as e:
                st.error(f"Error generating signal sheet: {e}")
                st.code(traceback.format_exc(), language="text")
