"""Bulk Data Downloader page (upstox_app.py:933-1033)."""
from __future__ import annotations

import datetime
import os
import time

import streamlit as st

from quantdesk.brokers.downloader import run_async_download
from quantdesk.config import BANK_NIFTY_LIST, NIFTY_50_LIST
from quantdesk.core.db import delete_template, get_templates, log_download, save_template
from quantdesk.data.resample import resample_dataframe
from quantdesk.data.saver import format_summary, primary_path, save_frame
from quantdesk.ui import components as C


def render(ctx):
    st.title("📦 Bulk Data Downloader")
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("1. List Setup")
        bulk_option = st.radio("List Source", ["Custom / Sector List", "📂 Saved Templates", "NIFTY 50", "NIFTY BANK"])
        default_text = ""
        current_template_name = None

        if bulk_option == "NIFTY 50":
            default_text = ", ".join(NIFTY_50_LIST)
        elif bulk_option == "NIFTY BANK":
            default_text = ", ".join(BANK_NIFTY_LIST)
        elif bulk_option == "📂 Saved Templates":
            templates = get_templates()
            if not templates.empty:
                sel_temp = st.selectbox("Select Template", templates["name"].tolist())
                if sel_temp:
                    default_text = templates[templates["name"] == sel_temp]["symbols"].iloc[0]
                    current_template_name = sel_temp
            else:
                st.info("No templates found.")

        symbol_text = st.text_area("Symbols (comma separated)", value=default_text, height=200)

        with st.expander("💾 Manage Templates"):
            t_col1, t_col2 = st.columns(2)
            with t_col1:
                new_temp_name = st.text_input("New Template Name")
                if st.button("Save Template"):
                    if new_temp_name and symbol_text:
                        ok, msg = save_template(new_temp_name, symbol_text)
                        if ok:
                            st.success("Saved!")
                        else:
                            st.error(msg)
                    else:
                        st.error("Enter name and symbols.")
            with t_col2:
                if current_template_name:
                    st.write(f"Actions for: **{current_template_name}**")
                    if st.button("🗑️ Delete Template", type="primary"):
                        if delete_template(current_template_name):
                            st.success("Deleted! Refreshing...")
                            time.sleep(1)
                            st.rerun()

    with col2:
        st.subheader("2. Settings")
        st.checkbox("Sanitize Corporate Actions (Splits, Bonuses, Dividends)", value=True, key="apply_corp_actions_bulk")
        st.session_state["apply_corp_actions"] = st.session_state.get("apply_corp_actions_bulk", True)
        interval_options = ["1 Minute", "Day", "Custom (Minutes)"]
        selected_intervals = st.multiselect("Intervals to Download", interval_options, default=["1 Minute"])
        custom_val = 1
        if "Custom (Minutes)" in selected_intervals:
            custom_val = st.number_input("Minutes for Custom", min_value=1, value=7)

        today = datetime.date.today()
        from_date = st.date_input("From Date", today - datetime.timedelta(days=30))
        to_date = st.date_input("To Date", today)
        local_path, csv_dir = C.render_save_folders("bulk", ctx.default_parquet_dir, "📁 Parquet Folder Path")

        if st.button("🚀 Start Bulk Async Download", use_container_width=True):
            if not selected_intervals:
                st.error("Please select at least one interval.")
            else:
                raw_symbols = sorted(list(set([s.strip() for s in symbol_text.replace("\n", ",").split(",") if s.strip()])))
                status_area = st.empty()
                pbar = st.progress(0, text="0% - Starting Bulk Download...")
                os.makedirs(local_path, exist_ok=True)

                success_count, fail_count, partial_count = 0, 0, 0
                for i, sym in enumerate(raw_symbols):
                    status_area.text(f"⏳ ({i+1}/{len(raw_symbols)}) Async Fetching {sym}...")
                    row = C.get_instrument_row(ctx.master_df, sym)

                    if row is not None:
                        apply_ca = st.session_state.get("apply_corp_actions", True)
                        for intv in selected_intervals:
                            if intv == "Custom (Minutes)":
                                dl_intv = "1 Minute"
                                sv_lbl = f"{custom_val}Minute"
                            else:
                                dl_intv = intv
                                sv_lbl = intv.replace(" ", "")

                            result = run_async_download(ctx.broker, row, dl_intv, from_date, to_date, ctx.auth_dict, apply_corp_actions=apply_ca)
                            df = result.df
                            if not df.empty:
                                actual_from = df["Timestamp"].min().date()
                                actual_to = df["Timestamp"].max().date()
                                if intv == "Custom (Minutes)":
                                    df = resample_dataframe(df, custom_val)
                                safe_sym = sym.replace(" ", "_")
                                save_dir = os.path.join(local_path, safe_sym, sv_lbl)
                                os.makedirs(save_dir, exist_ok=True)
                                full_path = os.path.join(save_dir, f"{safe_sym}_{sv_lbl}_{actual_from}_{actual_to}.parquet")
                                written = save_frame(df, full_path, local_path, csv_dir)
                                log_download(ctx.broker, sym, row["instrument_key"], sv_lbl, actual_from, actual_to, primary_path(written, full_path))
                                if result.complete:
                                    success_count += 1
                                else:
                                    partial_count += 1
                            else:
                                fail_count += 1
                    else:
                        fail_count += 1
                    pct = (i + 1) / len(raw_symbols)
                    pbar.progress(pct, text=f"Batch Progress: {int(pct * 100)}% - Processed {i+1} of {len(raw_symbols)}")

                _summary = f"✅ Batch Complete! Complete: {success_count}, Incomplete: {partial_count}, Failed: {fail_count}"
                if partial_count:
                    status_area.warning(_summary + " — incomplete files have missing date ranges (retries failed); re-fetch those symbols.")
                else:
                    status_area.success(_summary)
                st.caption(format_summary(csv_dir))
