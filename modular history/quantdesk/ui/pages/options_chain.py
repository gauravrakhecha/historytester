"""Options Chain (Upstox) page (upstox_app.py:1035-1242)."""
from __future__ import annotations

import datetime
import io
import os
import zipfile

import pandas as pd
import streamlit as st

from quantdesk.brokers.upstox import (
    get_available_expiries,
    get_expired_contracts,
    run_options_download,
)
from quantdesk.core.db import log_download
from quantdesk.data.resample import resample_dataframe
from quantdesk.ui import components as C


def render(ctx):
    st.title("📉 Options Chain Historical Data")
    if ctx.broker != "Upstox":
        st.warning("⚠️ Full Options chain endpoints are specific to Upstox. Switch your broker to Upstox to use this tab.")
        return

    master_df = ctx.master_df
    token = ctx.auth_dict["access_token"]
    selected_instrument_key = None
    selected_symbol_name = None

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("1. Underlying Asset")
        if not master_df.empty:
            type_filter = st.selectbox("Asset Class", ["Index", "Stock", "ETF", "Commodity", "Currency"])
            filtered_df = master_df[master_df["asset_class"] == type_filter]

            if type_filter in ["Commodity", "Currency"]:
                if not filtered_df.empty:
                    unique_names = sorted(filtered_df["tradingsymbol"].dropna().unique().tolist())
                    selected_name_root = st.selectbox("Symbol", unique_names)
                    proxy_rows = filtered_df[filtered_df["tradingsymbol"] == selected_name_root]
                    selected_symbol_name = selected_name_root
                    if not proxy_rows.empty:
                        selected_instrument_key = proxy_rows.iloc[0]["instrument_key"]
                        st.caption(f"Using proxy key: {selected_instrument_key}")
                    else:
                        st.error("No contracts found.")
                else:
                    st.error(f"No {type_filter} data found.")
            else:
                search_q = st.text_input("Search Underlying (e.g. NIFTY, RELIANCE)", key="opt_search")
                if search_q:
                    filtered_df = filtered_df[filtered_df["tradingsymbol"].str.contains(search_q.upper(), na=False)]
                if filtered_df.empty:
                    st.warning("No instruments found.")
                else:
                    if len(filtered_df) > 100:
                        filtered_df = filtered_df.head(100)
                    selected_label = st.selectbox("Select Underlying", filtered_df["label"].tolist())
                    row = filtered_df[filtered_df["label"] == selected_label].iloc[0]
                    selected_instrument_key = row["instrument_key"]
                    selected_symbol_name = row["tradingsymbol"]

    with col2:
        st.subheader("2. Expiry Selection")
        expiry_mode = st.radio("Expiry Source", ["📡 Fetch from API", "📅 Manual Date"], horizontal=True)
        selected_expiry = None
        if expiry_mode == "📡 Fetch from API":
            if selected_instrument_key:
                with st.spinner("Fetching expiries..."):
                    expiries, err = get_available_expiries(selected_instrument_key, token)
                if expiries:
                    selected_expiry = st.selectbox("Select Expiry", expiries)
                else:
                    if err:
                        st.warning(f"⚠️ {err}")
                    st.warning("⚠️ No expiries found via API. Please switch to '📅 Manual Date'.")
        else:
            selected_expiry = st.date_input("Select Expiry Date").strftime("%Y-%m-%d")

    if selected_expiry and st.button("Fetch Option Chain"):
        with st.spinner(f"Fetching chain for {selected_expiry}..."):
            contracts, err = get_expired_contracts(selected_instrument_key, selected_expiry, token)
            if contracts:
                df_contracts = pd.DataFrame(contracts)
                if "trading_symbol" in df_contracts.columns:
                    def get_opt_type(sym):
                        s = str(sym).upper()
                        if "CE" in s:
                            return "CE"
                        if "PE" in s:
                            return "PE"
                        if "FUT" in s:
                            return "FUT"
                        return "Other"
                    df_contracts["option_type"] = df_contracts["trading_symbol"].apply(get_opt_type)
                st.session_state.contracts_df = df_contracts
                st.session_state.current_expiry = selected_expiry
                st.session_state.current_symbol = selected_symbol_name
                st.success(f"Found {len(contracts)} contracts!")
            else:
                st.error(f"No contracts found for {selected_expiry}." + (f" ({err})" if err else ""))

    if "contracts_df" in st.session_state:
        df = st.session_state.contracts_df
        st.divider()
        st.subheader("3. Select Contracts")
        avail_types = df["option_type"].unique().tolist() if "option_type" in df.columns else []
        selected_types = st.multiselect("Filter Type", avail_types, default=avail_types)
        df_view = df[df["option_type"].isin(selected_types)] if "option_type" in df.columns else df

        bc1, bc2 = st.columns(2)
        with bc1:
            spot = 0.0
            if not df_view.empty and "strike_price" in df_view.columns:
                valid_strikes = [s for s in sorted(df_view["strike_price"].unique()) if s > 0]
                if valid_strikes:
                    spot = st.number_input("Center Spot Price", value=float(valid_strikes[len(valid_strikes) // 2]), step=50.0)
        with bc2:
            count = st.number_input("Strikes to Download (Approx)", value=20, step=2)

        if not df_view.empty:
            if "strike_price" in df_view.columns:
                futs = df_view[df_view["strike_price"] == 0]
                opts = df_view[df_view["strike_price"] > 0]
                if not opts.empty and spot > 0:
                    strikes = sorted(opts["strike_price"].unique())
                    closest = min(strikes, key=lambda x: abs(x - spot))
                    c_idx = strikes.index(closest)
                    start, end = max(0, c_idx - count // 2), min(len(strikes), c_idx + count // 2 + 1)
                    opts = opts[opts["strike_price"].isin(strikes[start:end])]
                bulk_df = pd.concat([futs, opts])
            else:
                bulk_df = df_view
            st.dataframe(bulk_df)

            st.divider()
            st.subheader("4. Download & Resample Settings")
            dc1, dc2 = st.columns(2)
            with dc1:
                exp_d = datetime.datetime.strptime(st.session_state.current_expiry, "%Y-%m-%d").date()
                opt_from_d = st.date_input("Data From", exp_d - datetime.timedelta(days=5))
                opt_to_d = st.date_input("Data To", exp_d)
            with dc2:
                tf_mode_opt = st.radio("Timeframe Mode", ["1 Minute Only", "Specific Timeframes", "Continuous Range"], horizontal=True, key="opt_tf_mode")
                tf_input_opt = "1, 3, 5, 15"
                tf_start_opt, tf_end_opt = 1, 15
                if tf_mode_opt == "Specific Timeframes":
                    tf_input_opt = st.text_input("Timeframes (comma-separated mins)", value="1, 3, 5, 15", key="opt_tf_input")
                elif tf_mode_opt == "Continuous Range":
                    r1, r2 = st.columns(2)
                    with r1:
                        tf_start_opt = st.number_input("Start (Mins)", min_value=1, value=1, key="opt_start")
                    with r2:
                        tf_end_opt = st.number_input("End (Mins)", min_value=1, value=15, key="opt_end")

            local_path = st.text_input("📂 Save to Folder (Leave blank for ZIP)", value=ctx.default_parquet_dir, key="opt_local_path")

            if st.button("🚀 Download & Extract Timeframes", use_container_width=True, type="primary"):
                items = bulk_df.to_dict("records")
                tfs_opt = [1]
                if tf_mode_opt == "Specific Timeframes":
                    try:
                        tfs_opt = [int(x.strip()) for x in tf_input_opt.split(",") if x.strip().isdigit()]
                    except Exception:
                        st.error("Invalid timeframe format.")
                        st.stop()
                elif tf_mode_opt == "Continuous Range":
                    tfs_opt = list(range(int(tf_start_opt), int(tf_end_opt) + 1))

                bar = st.progress(0, text="0% - Initializing Options Download...")
                status = st.empty()

                downloaded_data = run_options_download(items, token, opt_from_d, opt_to_d, C.make_progress(bar))

                if local_path:
                    try:
                        safe_sym = st.session_state.current_symbol.replace(" ", "_")
                        base_folder = os.path.join(local_path, safe_sym, st.session_state.current_expiry)
                        os.makedirs(base_folder, exist_ok=True)
                        saved_count = 0
                        for c, data in downloaded_data:
                            if data:
                                df_1m = pd.DataFrame(data, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume", "OI"])
                                df_1m["Timestamp"] = pd.to_datetime(df_1m["Timestamp"])
                                for tf in tfs_opt:
                                    df_tf = resample_dataframe(df_1m, tf) if tf > 1 else df_1m
                                    tf_folder = os.path.join(base_folder, f"{tf}Min")
                                    os.makedirs(tf_folder, exist_ok=True)
                                    f_path = os.path.join(tf_folder, f"{c['trading_symbol']}.parquet")
                                    df_tf.to_parquet(f_path, engine="pyarrow")
                                    log_download("Upstox", c["trading_symbol"], c["instrument_key"], f"{tf} Minute", opt_from_d, opt_to_d, f_path)
                                    saved_count += 1
                        status.success(f"✅ Saved {saved_count} total Parquet files across {len(tfs_opt)} timeframes inside {base_folder}")
                    except Exception as e:
                        st.error(f"Error saving locally: {e}")
                else:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                        for c, data in downloaded_data:
                            if data:
                                df_1m = pd.DataFrame(data, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume", "OI"])
                                df_1m["Timestamp"] = pd.to_datetime(df_1m["Timestamp"])
                                for tf in tfs_opt:
                                    df_tf = resample_dataframe(df_1m, tf) if tf > 1 else df_1m
                                    pq_buf = io.BytesIO()
                                    df_tf.to_parquet(pq_buf, engine="pyarrow")
                                    zf.writestr(f"{tf}Min/{c['trading_symbol']}.parquet", pq_buf.getvalue())
                    status.success("Done creating ZIP archive!")
                    st.download_button("Download Generated ZIP", buf.getvalue(), f"Options_{st.session_state.current_expiry}_Timeframes.zip", "application/zip")
