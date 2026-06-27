"""Single Instrument Data page (upstox_app.py:879-931)."""
from __future__ import annotations

import datetime
import os

import streamlit as st

from quantdesk.brokers.downloader import run_async_download
from quantdesk.core.db import log_download
from quantdesk.data.resample import resample_dataframe
from quantdesk.data.saver import format_summary, primary_path, save_frame
from quantdesk.ui import components as C


def render(ctx):
    st.title(f"📊 Single Instrument Async Download ({ctx.broker})")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("1. Asset Selection")
        selected_row = C.render_instrument_selector(ctx.master_df, "single", ctx.master_meta)

    with col2:
        st.subheader("2. Settings")
        st.checkbox("Sanitize Corporate Actions (Splits, Bonuses, Dividends)", value=True, key="apply_corp_actions_single")
        st.session_state["apply_corp_actions"] = st.session_state.get("apply_corp_actions_single", True)
        interval_select = st.selectbox("Interval", ["1 Minute", "Day", "Custom (Minutes)"])
        custom_val = 1
        if interval_select == "Custom (Minutes)":
            custom_val = st.number_input("Minutes (e.g. 7)", min_value=1, value=7)
            download_interval = "1 Minute"
            save_label = f"{custom_val}Minute"
        else:
            download_interval = interval_select
            save_label = interval_select.replace(" ", "")

        today = datetime.date.today()
        from_date = st.date_input("From Date", today - datetime.timedelta(days=7))
        to_date = st.date_input("To Date", today)
        local_path, csv_dir = C.render_save_folders("single", ctx.default_parquet_dir, "📁 Parquet Save Folder (blank = CSV download button)")

    if st.button("⚡ Fetch Fast", use_container_width=True):
        if selected_row is None:
            st.error("❌ Please select an instrument first.")
        else:
            pbar = st.progress(0, text="Initializing download...")
            apply_ca = st.session_state.get("apply_corp_actions", True)
            df_hist = run_async_download(
                ctx.broker, selected_row, download_interval, from_date, to_date, ctx.auth_dict,
                C.make_progress(pbar), apply_corp_actions=apply_ca,
            )

            if not df_hist.empty:
                if interval_select == "Custom (Minutes)":
                    df_hist = resample_dataframe(df_hist, custom_val)
                st.success(f"Success! Fetched {len(df_hist)} rows.")

                safe_name = selected_row["tradingsymbol"].replace(" ", "_")
                filename = f"{safe_name}_{save_label}_{from_date}_{to_date}.parquet"

                if local_path:
                    full_dir = os.path.join(local_path, safe_name, save_label)
                    os.makedirs(full_dir, exist_ok=True)
                    full_path = os.path.join(full_dir, filename)
                    written = save_frame(df_hist, full_path, local_path, csv_dir)
                    log_download(ctx.broker, selected_row["tradingsymbol"], selected_row["instrument_key"], save_label, from_date, to_date, primary_path(written, full_path))
                    st.success("✅ Saved: " + " , ".join(written))
                else:
                    st.download_button(
                        "💾 Download CSV",
                        df_hist.to_csv(index=False).encode("utf-8"),
                        file_name=filename.replace(".parquet", ".csv"),
                        mime="text/csv",
                    )
            else:
                st.error("No data found for this range.")
