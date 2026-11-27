"""Local Data Converter page (upstox_app.py:1244-1321)."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from quantdesk.data.loader import normalize_timestamps
from quantdesk.data.resample import resample_dataframe


def render(ctx):
    st.title("📁 Local Offline Data Converter")
    st.markdown(
        "Select a local folder containing 1-minute CSV/Parquet data and instantly batch-convert "
        "all files into multiple specific/continuous timeframes."
    )

    col1, col2 = st.columns(2)
    with col1:
        source_folder = st.text_input("📁 Source Folder (Contains 1-Min CSV/Parquet)", value=ctx.default_parquet_dir)
        dest_folder = st.text_input("📁 Destination Base Folder", value=ctx.default_parquet_dir)
    with col2:
        conv_mode = st.radio("Conversion Mode", ["Timeframe Resampling", "1:1 Format (CSV to Parquet)"], key="loc_conv_mode", horizontal=True)
        tf_mode_loc = tf_input_loc = None
        tf_start_loc = tf_end_loc = 1
        if conv_mode == "Timeframe Resampling":
            tf_mode_loc = st.radio("Timeframe Mode", ["Specific Timeframes", "Continuous Range"], key="loc_tf_mode", horizontal=True)
            if tf_mode_loc == "Specific Timeframes":
                tf_input_loc = st.text_input("Timeframes (comma-separated)", value="3, 5, 10, 15, 30, 60", key="loc_tf_input")
            else:
                rc1, rc2 = st.columns(2)
                with rc1:
                    tf_start_loc = st.number_input("Start Range (Mins)", min_value=1, value=1, key="loc_start")
                with rc2:
                    tf_end_loc = st.number_input("End Range (Mins)", min_value=1, value=58, key="loc_end")

    if st.button("🚀 Start Offline Batch Conversion", type="primary", width="stretch"):
        if not os.path.exists(source_folder):
            st.error("Source folder does not exist.")
        elif not dest_folder:
            st.error("Please provide a destination folder.")
        else:
            tfs_loc = []
            if conv_mode == "Timeframe Resampling":
                if tf_mode_loc == "Specific Timeframes":
                    try:
                        tfs_loc = [int(x.strip()) for x in tf_input_loc.split(",") if x.strip().isdigit()]
                    except Exception:
                        st.error("Invalid timeframe format.")
                        st.stop()
                else:
                    tfs_loc = list(range(int(tf_start_loc), int(tf_end_loc) + 1))

            csv_files = []
            for root, _dirs, files in os.walk(source_folder):
                for file in files:
                    if file.endswith((".csv", ".parquet")):
                        csv_files.append(os.path.join(root, file))

            if not csv_files:
                st.warning("No CSV/Parquet files found in the source directory.")
            else:
                pbar = st.progress(0, text="0% - Starting Batch Conversion...")
                status = st.empty()
                converted_count = 0

                for i, file_path in enumerate(csv_files):
                    file_name = os.path.basename(file_path)
                    status.text(f"Processing {file_name} ({i+1}/{len(csv_files)})...")
                    try:
                        df = pd.read_parquet(file_path) if file_path.endswith(".parquet") else pd.read_csv(file_path)
                        if "Timestamp" not in df.columns and (
                            df.index.name == "Timestamp" or isinstance(df.index, pd.DatetimeIndex)
                        ):
                            df = df.reset_index()
                            if "Timestamp" not in df.columns and "index" in df.columns:
                                df = df.rename(columns={"index": "Timestamp"})
                        if "Timestamp" in df.columns:
                            df["Timestamp"] = normalize_timestamps(df["Timestamp"])
                            if conv_mode == "1:1 Format (CSV to Parquet)":
                                rel_dir = os.path.relpath(os.path.dirname(file_path), source_folder)
                                target_dir = dest_folder if rel_dir == "." else os.path.join(dest_folder, rel_dir)
                                out_name = (
                                    file_name[:-4] + ".parquet"
                                    if file_name.lower().endswith(".csv")
                                    else (file_name if file_name.lower().endswith(".parquet") else f"{file_name}.parquet")
                                )
                                os.makedirs(target_dir, exist_ok=True)
                                out_path = os.path.join(target_dir, out_name)
                                df.to_parquet(out_path, engine="pyarrow")
                                converted_count += 1
                            else:
                                for tf in tfs_loc:
                                    df_resampled = resample_dataframe(df, tf)
                                    tf_dir = os.path.join(dest_folder, f"{tf}Min")
                                    os.makedirs(tf_dir, exist_ok=True)
                                    out_name = file_name.replace("1Minute", f"{tf}Min").replace("1Min", f"{tf}Min").replace(".csv", ".parquet")
                                    if f"{tf}Min" not in out_name:
                                        out_name = f"{tf}Min_{out_name}"
                                    if not out_name.endswith(".parquet"):
                                        out_name += ".parquet"
                                    out_path = os.path.join(tf_dir, out_name)
                                    df_resampled.to_parquet(out_path, engine="pyarrow")
                                    converted_count += 1
                    except Exception as e:
                        st.toast(f"Failed {file_name}: {e}")

                    pct = (i + 1) / len(csv_files)
                    pbar.progress(pct, text=f"Conversion Progress: {int(pct * 100)}% ({i+1}/{len(csv_files)})")

                status.success(
                    f"✅ Offline Batch Conversion Complete! Created {converted_count} new timeframe files inside {dest_folder}."
                )
