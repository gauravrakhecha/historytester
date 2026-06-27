"""
app_main.py — Unified Market Data Downloader & Quant Engine (modular edition) entrypoint.

Thin Streamlit shell: page config, broker auth sidebar, navigation, and dispatch to one
``render(ctx)`` page module per feature. All logic lives in the ``quantdesk`` package.

Run:  streamlit run app_main.py     (from this folder)

Improvement over the monolith: local tools (converter, logs, backup, settings, strategy
builder, plotter, backtester, results, ML) work WITHOUT a broker login. Only the live
downloaders / options chain require authentication. No feature was removed.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Ensure the package + spawned multiprocessing children can import quantdesk.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from quantdesk.core.db import init_db  # noqa: E402
from quantdesk.data.master_list import get_unified_master_list  # noqa: E402
from quantdesk.ui import components as C  # noqa: E402

st.set_page_config(
    page_title="Unified Market Data Downloader & Quant Engine", layout="wide", page_icon="⚡"
)

init_db()

selected_broker, auth_dict, is_authenticated = C.render_auth_sidebar()

st.sidebar.divider()
labels = [n[0] for n in C.NAV]
choice = st.sidebar.radio("Navigation", labels, key="nav_radio")
label, page_name, needs_broker = next(n for n in C.NAV if n[0] == choice)

# Load the master list only for broker pages (and only once authenticated).
# PERF: cache the frame in session_state so it's fetched ONCE per session — every subsequent
# tab switch reads it instantly (no re-fetch, no 53MB copy, no "Loading…" spinner flash).
master_df = None
master_meta = None
if needs_broker:
    if not is_authenticated:
        st.info(f"Step 1: Authenticate with {selected_broker} in the sidebar to use this page.")
        st.stop()
    _mkey = f"_master_df_{selected_broker}"
    if _mkey not in st.session_state:
        with st.spinner(f"Loading {selected_broker} master list (one-time)…"):
            st.session_state[_mkey] = get_unified_master_list(selected_broker)
    master_df = st.session_state[_mkey]
    if master_df is None or master_df.empty:
        del st.session_state[_mkey]  # allow a retry on the next run
        st.error("Master list failed to load. Check credentials/API.")
        st.stop()
    # Precompute the per-class split ONCE per session (selector speed — #5).
    _metakey = f"_master_meta_{selected_broker}"
    if _metakey not in st.session_state:
        st.session_state[_metakey] = C.build_master_meta(master_df)
    master_meta = st.session_state[_metakey]

from quantdesk.config import DEFAULTS  # noqa: E402
from quantdesk.core import settings_repo  # noqa: E402

ctx = C.PageContext(
    broker=selected_broker,
    auth_dict=auth_dict,
    is_authenticated=is_authenticated,
    master_df=master_df,
    default_parquet_dir=settings_repo.get("parquet_data_dir", DEFAULTS["parquet_data_dir"]),
    master_meta=master_meta if needs_broker else None,
)

page = importlib.import_module(f"quantdesk.ui.pages.{page_name}")
page.render(ctx)
