"""Download Logs page (upstox_app.py:1323-1339)."""
from __future__ import annotations

import streamlit as st

from quantdesk.core.db import get_download_history


def render(ctx):
    st.title("🗄️ Download History Log")
    df = get_download_history()
    if not df.empty:
        col1, col2 = st.columns([3, 1])
        with col1:
            search_term = st.text_input("🔍 Search Logs", placeholder="Type Symbol, Date, or Path...")
        with col2:
            if st.button("🔄 Refresh Logs"):
                st.rerun()
        if search_term:
            mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
            df = df[mask]
        st.dataframe(
            df,
            width="stretch",
            column_config={
                "file_path": st.column_config.TextColumn("File Path", help="Location on your computer"),
                "downloaded_at": st.column_config.DatetimeColumn("Downloaded At", format="D MMM YYYY, h:mm a"),
            },
        )
    else:
        st.info("No downloads logged yet.")
