"""Settings page (upstox_app.py:1380-1435)."""
from __future__ import annotations

import os

import streamlit as st

from quantdesk.core import settings_repo


def render(ctx):
    st.header("⚙️ Settings & Configuration")
    existing = settings_repo.get_all(force=True)

    with st.form("settings_form"):
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Backtesting Defaults")
            commission = st.text_input(
                "Brokerage Commission (fraction per trade)",
                value=existing.get("brokerage_commission", "0.0003"),
                help="Decimal fraction. 0.0003 = 0.03% per trade (typical Indian discount broker). 0.002 = 0.2%.",
            )
            slippage = st.text_input(
                "Slippage / Spread Penalty (fraction per trade)",
                value=existing.get("slippage", "0.0002"),
                help="Decimal fraction. Adds simulated market impact to every trade. 0.0002 = 0.02%.",
            )
            margin = st.text_input("Margin (e.g. 1.0 for no leverage, 0.2 for 5x)", value=existing.get("brokerage_margin", "1.0"))
            initial_capital = st.text_input("Initial Capital (₹)", value=existing.get("initial_capital", "100000"))
            st.markdown(
                """
                **Commission Guide for Indian Markets:**
                | Broker | Commission |
                |--------|-----------|
                | Zerodha (Equity Intraday) | 0.0003 (0.03%) |
                | Zerodha (Equity Delivery) | 0 |
                | Upstox (Equity) | 0.0003 (0.03%) |
                | Angel One | 0.0003 (0.03%) |
                | ICICI Direct | 0.0055 (0.55%) |
                """
            )
        with col2:
            st.subheader("Paths")
            plot_dir = st.text_input("Plot Save Directory", value=existing.get("plot_dir", "./plots"))
            parquet_data_dir = st.text_input(
                "Parquet Data Directory",
                value=existing.get("parquet_data_dir", ctx.default_parquet_dir),
                help="Default path for all data downloads and bulk operations.",
            )

        if st.form_submit_button("Save Settings"):
            os.makedirs(plot_dir, exist_ok=True)
            os.makedirs(parquet_data_dir, exist_ok=True)
            settings_repo.save(
                {
                    "brokerage_commission": commission,
                    "slippage": slippage,
                    "brokerage_margin": margin,
                    "plot_dir": plot_dir,
                    "parquet_data_dir": parquet_data_dir,
                    "initial_capital": initial_capital,
                }
            )
            st.success("Settings saved successfully!")
