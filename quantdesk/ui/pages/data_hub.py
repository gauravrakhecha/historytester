"""
quantdesk.ui.pages.data_hub — Data Coverage Catalog, Incremental Top-Up, and OpenAlgo Companion.
"""
from __future__ import annotations

import datetime
import os

import pandas as pd
import streamlit as st

from quantdesk.brokers.openalgo import normalize_host_url, search_openalgo_symbols
from quantdesk.core import settings_repo
from quantdesk.data.catalog import build_catalog_df, export_catalog_zip, top_up_entry
from quantdesk.data.master_list import get_unified_master_list
from quantdesk.integrations.openalgo_manager import (
    build_openalgo_install_commands,
    get_openalgo_status,
    read_sidecar_logs,
    start_sidecar,
    stop_sidecar,
)
from quantdesk.ui import components as C


def _get_master_for_broker(ctx) -> pd.DataFrame | None:
    """Return cached master DataFrame for ``ctx.broker``, loading once if needed."""
    if ctx.master_df is not None and not ctx.master_df.empty:
        return ctx.master_df
    mkey = f"_master_df_{ctx.broker}"
    if mkey not in st.session_state:
        try:
            st.session_state[mkey] = get_unified_master_list(ctx.broker)
        except Exception:  # noqa: BLE001
            return None
    df = st.session_state.get(mkey)
    return df if isinstance(df, pd.DataFrame) and not df.empty else None


def _resolve_row_meta(cat_row: dict, master_df: pd.DataFrame | None) -> dict | None:
    """Resolve instrument metadata from ``master_df`` by ``instrument_key`` or ``tradingsymbol``."""
    if master_df is None or master_df.empty:
        return None
    ikey = str(cat_row.get("instrument_key") or "").strip()
    if ikey and "instrument_key" in master_df.columns:
        matched = master_df[master_df["instrument_key"].astype(str) == ikey]
        if not matched.empty:
            return matched.iloc[0].to_dict()
    sym = str(cat_row.get("symbol") or "").strip()
    if sym:
        row = C.get_instrument_row(master_df, sym)
        if row is not None:
            return row.to_dict()
    return None


def _render_catalog_tab(ctx) -> None:
    """Render Data Coverage Catalog, Incremental Top-Up, and ZIP Export."""
    c_tog, c_ref = st.columns([3, 1])
    with c_tog:
        include_quality = st.toggle(
            "Run NSE Quality & Gap Check",
            value=False,
            key="hub_quality_toggle",
        )
    with c_ref:
        if st.button("🔄 Refresh Catalog", key="hub_refresh_catalog_btn", width="stretch"):
            st.rerun()

    catalog_df = build_catalog_df(include_quality=include_quality)
    if catalog_df.empty:
        st.info(
            "No downloaded datasets found in catalog yet. Download data from Master/Single/Bulk Downloader "
            "or rebuild your database index in Data Management."
        )
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Datasets", int(len(catalog_df)))
    k2.metric("Unique Symbols", int(catalog_df["symbol"].nunique()))
    k3.metric("Files On Disk", int(catalog_df["file_exists"].sum()))
    k4.metric("Total Size (MB)", f"{catalog_df['size_kb'].sum() / 1024.0:.2f}")

    st.dataframe(
        catalog_df,
        width="stretch",
        hide_index=True,
        column_config={
            "file_path": st.column_config.TextColumn("File Path"),
            "downloaded_at": st.column_config.DatetimeColumn("Downloaded At", format="D MMM YYYY, h:mm a"),
        },
    )

    symbols = sorted(
        {str(s).strip() for s in catalog_df["symbol"].dropna().tolist() if str(s).strip()}
    )
    selected_symbols = st.multiselect(
        "Select Symbols for Top-Up or Export",
        options=symbols,
        key="hub_selected_symbols",
    )
    selected_rows = (
        catalog_df[catalog_df["symbol"].isin(selected_symbols)]
        if selected_symbols
        else catalog_df.iloc[0:0]
    )

    export_paths = (
        selected_rows[selected_rows["file_exists"]]["file_path"].dropna().astype(str).tolist()
        if not selected_rows.empty
        else []
    )
    zip_bytes = export_catalog_zip(export_paths) if export_paths else b""

    c_topup, c_zip = st.columns(2)
    topup_clicked = c_topup.button(
        "🔄 Top-Up Selected to Today",
        key="hub_topup_btn",
        width="stretch",
    )
    c_zip.download_button(
        "📥 Export Selected as ZIP",
        data=zip_bytes,
        file_name=f"quantdesk_catalog_{datetime.date.today().isoformat()}.zip",
        mime="application/zip",
        disabled=not bool(export_paths),
        key="hub_export_zip_btn",
        width="stretch",
    )

    if topup_clicked:
        if not selected_symbols:
            st.warning("Select one or more symbols from the catalog above first.")
            return
        if not (ctx.is_authenticated and ctx.broker in ("Upstox", "OpenAlgo")):
            st.info(
                "Authenticate with **Upstox** or **OpenAlgo** in the sidebar to run incremental top-ups."
            )
            return

        master_df = _get_master_for_broker(ctx)
        target_end = datetime.date.today()
        entries = selected_rows.to_dict("records")
        pbar = st.progress(0.0, text="Starting incremental top-up...")
        results: list[dict] = []

        for idx, cat_row in enumerate(entries):
            sym = str(cat_row.get("symbol") or "")
            interval = str(cat_row.get("interval") or "")
            pbar.progress(idx / max(1, len(entries)), text=f"Topping up {sym} ({interval})...")
            row_meta = _resolve_row_meta(cat_row, master_df)
            try:
                res = top_up_entry(
                    catalog_row=cat_row,
                    target_end_date=target_end,
                    auth_dict=ctx.auth_dict,
                    row_meta=row_meta,
                    parquet_dir=ctx.default_parquet_dir,
                    apply_corp_actions=bool(st.session_state.get("apply_corp_actions", False)),
                )
            except Exception as exc:  # noqa: BLE001
                res = {
                    "symbol": sym,
                    "interval": interval,
                    "added_rows": 0,
                    "total_rows": int(cat_row.get("bars") or 0),
                    "file_path": str(cat_row.get("file_path") or ""),
                    "complete": False,
                    "failed_ranges": [str(exc)],
                }
            results.append(res)

        pbar.progress(1.0, text="Incremental top-up complete.")
        res_df = pd.DataFrame(results)
        st.dataframe(res_df, width="stretch", hide_index=True)
        incomplete = [r for r in results if not r.get("complete")]
        if incomplete:
            st.warning(f"{len(incomplete)} dataset(s) reported incomplete ranges during top-up.")
        else:
            st.success(f"Topped up {len(results)} dataset(s) through {target_end.isoformat()}.")


def _render_openalgo_tab(ctx) -> None:
    """Render OpenAlgo Companion status KPIs, Live Search, and optional Sidecar Setup."""
    raw_host = (
        (ctx.auth_dict.get("host_url") if ctx.auth_dict.get("broker") == "OpenAlgo" else None)
        or st.session_state.get("openalgo_host_input")
        or os.getenv("OPENALGO_HOST_URL")
        or settings_repo.get("openalgo_host_url", "http://127.0.0.1:5000")
        or "http://127.0.0.1:5000"
    )
    oa_host = normalize_host_url(raw_host)
    oa_key = (
        (ctx.auth_dict.get("api_key") if ctx.auth_dict.get("broker") == "OpenAlgo" else None)
        or st.session_state.get("openalgo_api_key_input")
        or os.getenv("OPENALGO_API_KEY")
        or settings_repo.get("openalgo_api_key", "")
        or ""
    ).strip()

    status = get_openalgo_status(oa_host, oa_key)

    c_info, c_link, c_ref = st.columns([2, 2, 1])
    c_info.caption(f"Endpoint: `{status['host_url']}`")
    c_link.link_button(
        "🌐 Open OpenAlgo Web/Desktop Login",
        status["host_url"],
        width="stretch",
    )
    if c_ref.button("🔄 Refresh Status", key="hub_oa_refresh_btn", width="stretch"):
        st.rerun()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Reachable", "Yes" if status["reachable"] else "No")
    lat = status.get("latency_ms")
    k2.metric("Ping Latency", f"{lat:.1f} ms" if isinstance(lat, (int, float)) else "—")
    k3.metric("Connected Broker", status.get("broker") or "—")
    if status.get("ping_ok"):
        mode_lbl = "Sandbox" if status.get("analyzer_active") else (status.get("mode") or "Live")
    else:
        mode_lbl = status.get("mode") or "Unknown"
    k4.metric("Sandbox / Live", mode_lbl)
    sidecar_lbl = (
        "Running"
        if status.get("sidecar_running")
        else ("Installed (Stopped)" if status.get("sidecar_installed") else "Not Installed")
    )
    k5.metric("Sidecar Status", sidecar_lbl)

    if status.get("ping_ok"):
        st.success(status.get("message") or "OpenAlgo connected.")
    elif status.get("reachable"):
        st.info(status.get("message") or "OpenAlgo server is reachable.")
    else:
        st.warning(status.get("message") or "OpenAlgo server is not reachable.")

    st.divider()
    st.subheader("🔍 Live Symbol Search")
    c_q, c_ex, c_go = st.columns([3, 1, 1])
    query = c_q.text_input(
        "Symbol / Query",
        placeholder="e.g. RELIANCE, NIFTY, SBIN",
        key="hub_oa_search_query",
    )
    exchange = c_ex.selectbox(
        "Exchange",
        ["", "NSE", "NFO", "BSE", "BFO", "MCX", "CDS", "NSE_INDEX", "BSE_INDEX"],
        key="hub_oa_search_exchange",
    )
    search_clicked = c_go.button("🔎 Search", key="hub_oa_search_btn", width="stretch")

    if search_clicked:
        if not oa_key:
            st.info("Configure an OpenAlgo API Key in the sidebar to search symbols.")
        elif not status.get("reachable"):
            st.warning(f"OpenAlgo server is not reachable at {status['host_url']}.")
        elif not query.strip():
            st.warning("Enter a symbol query to search.")
        else:
            hits = search_openalgo_symbols(
                status["host_url"],
                oa_key,
                query.strip(),
                exchange=exchange,
            )
            if hits:
                st.dataframe(pd.DataFrame(hits), width="stretch", hide_index=True)
            else:
                st.info("No matching instruments returned by OpenAlgo.")

    st.divider()
    with st.expander("🛠️ Optional Isolated Sidecar Setup (`services/openalgo/`)", expanded=False):
        st.caption(
            "Runs OpenAlgo inside its own isolated virtual environment (`services/openalgo/.venv`), "
            "completely separate from QuantDesk's `.venv`."
        )
        cmds = build_openalgo_install_commands()
        st.code("\n".join(cmds), language="powershell")
        if status.get("sidecar_installed"):
            c_start, c_stop, c_logs = st.columns(3)
            if c_start.button("▶️ Start Sidecar", key="hub_sidecar_start_btn", width="stretch"):
                ok_s, msg_s = start_sidecar()
                (st.success if ok_s else st.error)(msg_s)
            if c_stop.button("⏹️ Stop Sidecar", key="hub_sidecar_stop_btn", width="stretch"):
                ok_s, msg_s = stop_sidecar()
                (st.success if ok_s else st.warning)(msg_s)
            if c_logs.button("📜 Tail Sidecar Logs", key="hub_sidecar_logs_btn", width="stretch"):
                lines = read_sidecar_logs(tail_lines=80)
                st.code("\n".join(lines) if lines else "(No log output yet)", language="text")
        else:
            st.info(
                f"Local sidecar is not installed at `{status.get('sidecar_dir')}`. "
                "You can install it with the commands above or connect to any OpenAlgo Web/Desktop instance."
            )


def render(ctx) -> None:
    """Render the Data Hub & OpenAlgo Companion page."""
    st.title("📡 Data Hub & OpenAlgo")
    tab_catalog, tab_openalgo = st.tabs(
        ["📦 Data Coverage Catalog & Incremental Top-Up", "🔌 OpenAlgo Companion & Live Search"]
    )
    with tab_catalog:
        _render_catalog_tab(ctx)
    with tab_openalgo:
        _render_openalgo_tab(ctx)
