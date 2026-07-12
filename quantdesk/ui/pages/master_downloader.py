"""Master Data Downloader & Converter page (upstox_app.py:815-877)."""
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
    st.title("🗃️ Master Data Downloader & Converter")
    st.markdown("**⚡ Async Engine Active** | Downloads comprehensive 1-min data and instantly splits it into customized timeframes.")

    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("1. Asset Configuration")
        selected_row = C.render_instrument_selector(ctx.master_df, "master", ctx.master_meta)
        if selected_row is not None:
            st.code(f"Key: {selected_row['instrument_key']}\nSymbol: {selected_row['tradingsymbol']}\nClass: {selected_row['asset_class']}")

    with col2:
        st.subheader("2. Settings")
        st.checkbox("Sanitize Corporate Actions (Splits, Bonuses, Dividends)", value=True, key="apply_corp_actions")
        today = datetime.date.today()
        from_date = st.date_input("From Date", today - datetime.timedelta(days=365))
        to_date = st.date_input("To Date", today)

        tf_mode = st.radio("Timeframe Mode", ["Specific Timeframes", "Continuous Range"], horizontal=True)
        tf_input = "1, 3, 5, 10, 15, 30, 60"
        tf_start, tf_end = 1, 58
        if tf_mode == "Specific Timeframes":
            tf_input = st.text_input("Timeframes to Output (comma-separated minutes)", value="1, 3, 5, 10, 15, 30, 60")
        else:
            rc1, rc2 = st.columns(2)
            with rc1:
                tf_start = st.number_input("Start Range (Mins)", min_value=1, value=1)
            with rc2:
                tf_end = st.number_input("End Range (Mins)", min_value=1, value=58)

        local_path, csv_dir = C.render_save_folders("master", ctx.default_parquet_dir, "📁 Parquet Base Save Folder")

    if st.button("🚀 Download Master & Extract Timeframes", type="primary", use_container_width=True):
        if selected_row is None:
            st.error("❌ Please select an instrument first.")
        elif from_date > to_date:
            st.error("❌ Invalid Date Range.")
        else:
            if tf_mode == "Specific Timeframes":
                try:
                    tfs = [int(x.strip()) for x in tf_input.split(",") if x.strip().isdigit()]
                except ValueError:
                    st.error("❌ Invalid timeframes format.")
                    st.stop()
            else:
                if tf_start > tf_end:
                    st.error("❌ Start range cannot be greater than end range.")
                    st.stop()
                tfs = list(range(int(tf_start), int(tf_end) + 1))

            status = st.empty()
            pbar = st.progress(0, text="0% - Starting Download...")
            apply_ca = st.session_state.get("apply_corp_actions", True)
            status.info(f"⚡ Fetching Base 1-Minute Data for {selected_row['tradingsymbol']} (Concurrent chunks...)")
            result = run_async_download(
                ctx.broker, selected_row, "1 Minute", from_date, to_date, ctx.auth_dict,
                C.make_progress(pbar), apply_corp_actions=apply_ca,
            )
            df_master = result.df

            if df_master.empty:
                status.error("No data returned. Check limits/date range.")
            else:
                actual_from = df_master["Timestamp"].min().date()
                actual_to = df_master["Timestamp"].max().date()
                if result.failed_ranges:
                    ranges_txt = ", ".join(f"{s} → {e}" for s, e in result.failed_ranges)
                    status.warning(
                        f"⚠️ Base data INCOMPLETE: {len(result.failed_ranges)} range(s) failed "
                        f"({ranges_txt}). Every resampled timeframe below inherits these gaps."
                    )
                else:
                    status.success(f"✅ Base Extracted: {len(df_master)} rows. Resampling {len(tfs)} timeframes...")
                safe_sym = selected_row["tradingsymbol"].replace(" ", "_").replace(":", "")
                symbol_dir = os.path.join(local_path, safe_sym)
                os.makedirs(symbol_dir, exist_ok=True)

                for tf in tfs:
                    df_resampled = resample_dataframe(df_master, tf)
                    filename = f"{safe_sym}_{tf}Min_{actual_from}_to_{actual_to}.parquet"
                    save_path = os.path.join(symbol_dir, filename)
                    written = save_frame(df_resampled, save_path, local_path, csv_dir)
                    log_download(ctx.broker, selected_row["tradingsymbol"], selected_row["instrument_key"], f"{tf} Minute", actual_from, actual_to, primary_path(written, save_path))

                status.success(f"🎉 Fully Complete! Generated {len(tfs)} timeframe files for each enabled format.")
                st.caption(format_summary(csv_dir) + f"  Parquet symbol folder: `{symbol_dir}`")
                st.dataframe(df_master.head(), use_container_width=True)
