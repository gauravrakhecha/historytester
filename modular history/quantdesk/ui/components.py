"""
quantdesk.ui.components — shared Streamlit widgets + page context.

Houses the auth sidebar (upstox_app.py:231-283), the instrument selector (395-425), the
``get_instrument_row`` helper (807-813), a progress-callback bridge, and the navigation list.
This is the only UI-side place that imports Streamlit besides the page modules and app_main.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd
import requests
import streamlit as st

from quantdesk import config

# Navigation entries (label -> page module name under quantdesk.ui.pages)
NAV = [
    ("🗃️ Master Data Downloader", "master_downloader", True),
    ("📊 Single Instrument Data", "single_instrument", True),
    ("📦 Bulk Data Downloader", "bulk_downloader", True),
    ("📉 Options Chain (Upstox)", "options_chain", True),
    ("📁 Local Data Converter", "local_converter", False),
    ("🗄️ Download Logs", "download_logs", False),
    ("🛠️ Data Management", "data_management", False),
    ("⚙️ Settings", "settings", False),
    ("🛠️ Strategy Builder", "strategy_builder", False),
    ("📈 Strategy & Indicator Plotter", "plotter", False),
    ("🚀 Backtester", "backtester", False),
    ("📊 Backtest Results", "results", False),
    ("🤖 ML Pattern Finder", "ml_finder", False),
]


@dataclass
class PageContext:
    broker: str
    auth_dict: dict
    is_authenticated: bool
    master_df: pd.DataFrame
    default_parquet_dir: str


def make_progress(pbar):
    """Bridge a backend ``progress_cb(pct, text)`` to a Streamlit progress bar."""

    def cb(pct: float, text: str):
        try:
            pbar.progress(min(1.0, pct), text=text)
        except Exception:
            pass

    return cb


def render_auth_sidebar() -> tuple[str, dict, bool]:
    """Render the broker auth sidebar and return (broker, auth_dict, is_authenticated)."""
    st.sidebar.title("🔐 Broker Setup")
    selected_broker = st.sidebar.radio("Select Broker", ["Upstox", "Zerodha", "Dhan"], key="broker_radio")

    if "auth_dict" not in st.session_state:
        st.session_state.auth_dict = {}

    if selected_broker == "Upstox":
        api_key = st.sidebar.text_input("Upstox API Key", value=os.getenv("UPSTOX_API_KEY", ""), type="password")
        api_secret = st.sidebar.text_input("Upstox API Secret", value=os.getenv("UPSTOX_API_SECRET", ""), type="password")
        redirect_uri = st.sidebar.text_input("Redirect URI", value=os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8501"))
        auth_code = st.sidebar.text_input("Auth Code (from URL)", value=st.query_params.get("code", ""))

        if st.sidebar.button("Login (Upstox)"):
            if not auth_code:
                import urllib.parse

                base_url = "https://api.upstox.com/v2/login/authorization/dialog"
                login_url = f"{base_url}?{urllib.parse.urlencode({'response_type': 'code', 'client_id': api_key, 'redirect_uri': redirect_uri})}"
                st.sidebar.markdown(f"[**👉 Get Auth Code**]({login_url})", unsafe_allow_html=True)
                st.sidebar.warning("Paste code and click Login again.")
            else:
                data = {"code": auth_code, "client_id": api_key, "client_secret": api_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
                try:
                    resp = requests.post(
                        "https://api.upstox.com/v2/login/authorization/token",
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data=data, timeout=10,
                    )
                    if resp.status_code == 200:
                        st.session_state.auth_dict = {"broker": "Upstox", "access_token": resp.json().get("access_token")}
                        st.sidebar.success("Upstox Authenticated!")
                        st.query_params.clear()
                        st.rerun()
                    else:
                        st.sidebar.error(f"Login Failed! Code: {resp.status_code} \nResponse: {resp.text}")
                except requests.exceptions.ConnectionError:
                    st.sidebar.error("Network Error: Failed to reach Upstox API. Check your internet/DNS.")
                except Exception as e:
                    st.sidebar.error(f"Login Request Failed: {e}")

    elif selected_broker == "Zerodha":
        api_key = st.sidebar.text_input("Kite API Key", value=os.getenv("ZERODHA_API_KEY", ""), type="password")
        access_token = st.sidebar.text_input("Kite Access Token", value=os.getenv("ZERODHA_ACCESS_TOKEN", ""), type="password")
        if st.sidebar.button("Set Zerodha Creds"):
            if api_key and access_token:
                st.session_state.auth_dict = {"broker": "Zerodha", "api_key": api_key, "access_token": access_token}
                st.sidebar.success("Zerodha Setup Complete!")
                st.rerun()

    elif selected_broker == "Dhan":
        client_id = st.sidebar.text_input("Dhan Client ID", value=os.getenv("DHAN_CLIENT_ID", ""))
        access_token = st.sidebar.text_input("Dhan Access Token", value=os.getenv("DHAN_ACCESS_TOKEN", ""), type="password")
        if st.sidebar.button("Set Dhan Creds"):
            if client_id and access_token:
                st.session_state.auth_dict = {"broker": "Dhan", "client_id": client_id, "access_token": access_token}
                st.sidebar.success("Dhan Setup Complete!")
                st.rerun()

    is_auth = st.session_state.auth_dict.get("broker") == selected_broker
    return selected_broker, st.session_state.auth_dict, is_auth


def render_instrument_selector(master_df: pd.DataFrame, key_prefix: str):
    """Filter the master list dynamically (asset class -> search -> select). Returns a row or None."""
    col_a, col_b = st.columns(2)
    classes = ["Stock", "ETF", "Index", "Futures", "Options", "Commodity", "Currency", "Other"]
    avail_classes = [c for c in classes if c in master_df["asset_class"].unique()]

    with col_a:
        aclass = st.selectbox("1. Filter by Asset Class", avail_classes, key=f"{key_prefix}_class")
    with col_b:
        search_q = st.text_input("2. Search Ticker / Symbol", placeholder="e.g. RELIANCE", key=f"{key_prefix}_search")

    filtered = master_df[master_df["asset_class"] == aclass]
    if search_q:
        filtered = filtered[filtered["tradingsymbol"].str.contains(search_q.upper(), na=False)]
    if filtered.empty:
        st.warning("No instruments match your search.")
        return None
    if len(filtered) > 500:
        st.caption(f"Showing top 500 out of {len(filtered)} results. **Keep typing in the Search box to narrow it down.**")
        filtered = filtered.head(500)

    selected_label = st.selectbox("3. Select Instrument", filtered["label"].tolist(), key=f"{key_prefix}_inst")
    if selected_label:
        return filtered[filtered["label"] == selected_label].iloc[0]
    return None


def get_instrument_row(master_df: pd.DataFrame, symbol: str):
    """Resolve a tradingsymbol to a master-list row (prefers NSE_EQ)."""
    res = master_df[master_df["tradingsymbol"] == symbol.strip().upper()]
    if not res.empty:
        if "segment" in res.columns and "NSE_EQ" in res["segment"].values:
            return res[res["segment"] == "NSE_EQ"].iloc[0]
        return res.iloc[0]
    return None
