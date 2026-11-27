"""
quantdesk.ui.pages.live_deploy — OpenAlgo Live Telemetry, Strategy Deployer & Script Exporter.

Provides three tabs without blocking Streamlit reruns:
1. OpenAlgo Account & Telemetry Monitor (Funds, Analyzer Sandbox/Live, Positions, Orders, Trades).
2. Strategy Deployer, Risk Guard & Standalone OpenAlgo Script Exporter (1-cycle step check with
   Local Dataset Dry-Run fallback, `.py` export, and isolated background `signal_runner` PID control).
3. Live Signals & Alert History (JSONL audit log viewer).
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from quantdesk import config as cfg_mod
from quantdesk.brokers.openalgo import normalize_host_url
from quantdesk.core import settings_repo
from quantdesk.core.db import get_download_history
from quantdesk.data.loader import load_indexed
from quantdesk.live import (
    LiveDeploymentSpec,
    LiveRiskConfig,
    LiveRiskState,
    OpenAlgoClient,
    read_live_events,
    step_once,
)
from quantdesk.strategy import presets
from quantdesk.strategy.models import safe_name
from quantdesk.strategy.openalgo_export import generate_openalgo_script
from quantdesk.strategy.templates import STARTER_TEMPLATES, get_template
from quantdesk.strategy.validation import PresetValidationError, validate_preset

_EXCHANGES = ["NSE", "NFO", "BSE", "MCX", "CDS"]
_INTERVALS = ["1m", "5m", "15m", "1h", "D"]
_PRODUCTS = ["MIS", "CNC", "NRML"]
_EXEC_MODES = ["alert_only", "sandbox", "live"]
_STRAT_SOURCES = [
    "Active Strategy (from Builder)",
    "Starter Template",
    "Saved Strategy JSON",
]


def _resolve_openalgo_creds(ctx) -> tuple[str, str]:
    """Resolve OpenAlgo host URL and API key from sidebar auth, session, env, or settings."""
    auth_dict = getattr(ctx, "auth_dict", {}) or {}
    broker = getattr(ctx, "broker", "")
    raw_host = (
        (auth_dict.get("host_url") if broker == "OpenAlgo" else None)
        or st.session_state.get("openalgo_host_input")
        or os.getenv("OPENALGO_HOST_URL")
        or settings_repo.get("openalgo_host_url", "http://127.0.0.1:5000")
        or "http://127.0.0.1:5000"
    )
    raw_key = (
        (auth_dict.get("api_key") if broker == "OpenAlgo" else None)
        or st.session_state.get("openalgo_api_key_input")
        or os.getenv("OPENALGO_API_KEY")
        or settings_repo.get("openalgo_api_key", "")
        or ""
    )
    return normalize_host_url(str(raw_host)), str(raw_key).strip()


def _pick_float(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Extract the first finite float matching ``keys`` from payload or payload['data']."""
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return None
    sources = [payload]
    if isinstance(payload.get("data"), dict):
        sources.insert(0, payload["data"])
    for src in sources:
        for k in keys:
            if k in src and src[k] not in (None, ""):
                try:
                    return float(src[k])
                except (TypeError, ValueError):
                    continue
    return None


def _format_inr(val: float | None) -> str:
    return f"₹{val:,.2f}" if val is not None else "—"


def _extract_records(payload: dict[str, Any], nested_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Normalize OpenAlgo book payloads (list or nested dict) into a list of dicts."""
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in nested_keys:
            val = data.get(k)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
        return [data] if data else []
    for k in nested_keys:
        val = payload.get(k)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
    return []


def _fetch_telemetry(host_url: str, api_key: str) -> dict[str, Any]:
    """Fetch OpenAlgo account & book telemetry with graceful offline degradation."""
    client = OpenAlgoClient(host_url=host_url, api_key=api_key, timeout=3.0)
    return {
        "funds": client.funds(),
        "analyzer": client.analyzer_status(),
        "positions": client.positionbook(),
        "orders": client.orderbook(),
        "trades": client.tradebook(),
    }


def _render_book_tab(payload: dict[str, Any], nested_keys: tuple[str, ...], empty_msg: str) -> None:
    if not isinstance(payload, dict):
        st.info(empty_msg)
        return
    if payload.get("status") == "error":
        st.info(payload.get("error") or "OpenAlgo telemetry unavailable.")
        return
    rows = _extract_records(payload, nested_keys)
    if not rows:
        st.info(empty_msg)
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_telemetry_tab(ctx, oa_host: str, oa_key: str) -> None:
    """Tab 1: OpenAlgo Account KPIs and Position/Order/Trade books."""
    c_ep, c_ref = st.columns([3, 1])
    c_ep.caption(f"OpenAlgo Endpoint: `{oa_host}`")
    refresh_clicked = c_ref.button("🔄 Refresh Telemetry", key="ld_refresh_btn", width="stretch")

    if not oa_key:
        st.info(
            "OpenAlgo API key is not configured. Select **OpenAlgo** in the sidebar and click "
            "**Connect OpenAlgo**, or set `OPENALGO_API_KEY` to stream live account telemetry."
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Available Cash", "—")
        k2.metric("Realized MTM", "—")
        k3.metric("Unrealized MTM", "—")
        k4.metric("Mode", "Not Connected")
        sub_pos, sub_ord, sub_trd = st.tabs(["📌 Open Positions", "📋 Order Book", "🧾 Trade Book"])
        with sub_pos:
            st.info("Connect OpenAlgo to view open positions.")
        with sub_ord:
            st.info("Connect OpenAlgo to view order book.")
        with sub_trd:
            st.info("Connect OpenAlgo to view trade book.")
        return

    cache_key = "_ld_telemetry_cache"
    cached = st.session_state.get(cache_key)
    if (
        refresh_clicked
        or not isinstance(cached, dict)
        or cached.get("host") != oa_host
        or cached.get("key") != oa_key
    ):
        cached = {
            "host": oa_host,
            "key": oa_key,
            "data": _fetch_telemetry(oa_host, oa_key),
        }
        st.session_state[cache_key] = cached

    tele = cached["data"]
    funds_res = tele.get("funds", {})
    az_res = tele.get("analyzer", {})

    if funds_res.get("status") == "error":
        st.warning(
            f"OpenAlgo telemetry offline or unreachable: {funds_res.get('error', 'Connection error')}"
        )

    avail_cash = _pick_float(funds_res, ("availablecash", "available_cash", "cash", "net"))
    realized_mtm = _pick_float(funds_res, ("m2mrealized", "realized_mtm", "realizedpnl"))
    unrealized_mtm = _pick_float(funds_res, ("m2munrealized", "unrealized_mtm", "unrealizedpnl"))

    if az_res.get("status") == "error":
        mode_label = "Offline"
    elif az_res.get("analyzer_active"):
        mode_label = "Sandbox (Analyzer)"
    else:
        mode_label = "Live"

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Available Cash", _format_inr(avail_cash))
    k2.metric("Realized MTM", _format_inr(realized_mtm))
    k3.metric("Unrealized MTM", _format_inr(unrealized_mtm))
    k4.metric("Mode: Sandbox vs Live", mode_label)

    sub_pos, sub_ord, sub_trd = st.tabs(["📌 Open Positions", "📋 Order Book", "🧾 Trade Book"])
    with sub_pos:
        _render_book_tab(
            tele.get("positions", {}),
            ("positions", "net", "day", "data"),
            "No open positions reported by OpenAlgo.",
        )
    with sub_ord:
        _render_book_tab(
            tele.get("orders", {}),
            ("orders", "orderbook", "data"),
            "No orders in OpenAlgo order book.",
        )
    with sub_trd:
        _render_book_tab(
            tele.get("trades", {}),
            ("trades", "tradebook", "data"),
            "No trades in OpenAlgo trade book.",
        )


def _builder_dict_from_session() -> dict[str, Any]:
    """Build a raw strategy preset dict from Strategy Builder session state."""
    return {
        "indicators": st.session_state.get("indicators", []),
        "buy_rules": st.session_state.get("buy_rules", []),
        "sell_rules": st.session_state.get("sell_rules", []),
        "no_trade_rules": st.session_state.get("no_trade_rules", []),
        "range_zones": st.session_state.get("range_zones", []),
        "risk_management": {
            "mode": st.session_state.get("rm_mode", "None (Use Sell Rules Only)"),
            "sl_pct": float(st.session_state.get("sl_pct", 2.0)),
            "tp_pct": float(st.session_state.get("tp_pct", 5.0)),
            "trail_pct": float(st.session_state.get("trail_pct", 2.0)),
            "strat_dir": st.session_state.get("strat_dir", "Long Only"),
            "risk_pct": float(st.session_state.get("risk_pct", 1.0)),
            "max_dd_halt_pct": float(st.session_state.get("max_dd_halt_pct", 0.0)),
            "atr_period": int(st.session_state.get("atr_period", 14)),
            "atr_stop_mult": float(st.session_state.get("atr_stop_mult", 2.0)),
            "max_bars_in_trade": int(st.session_state.get("max_bars_in_trade", 0)),
            "breakeven_trigger_pct": float(st.session_state.get("breakeven_trigger_pct", 0.0)),
            "partial_exit_pct": float(st.session_state.get("partial_exit_pct", 0.0)),
            "partial_trigger_pct": float(st.session_state.get("partial_trigger_pct", 0.0)),
            "htf_bias": bool(st.session_state.get("htf_bias", False)),
            "htf_minutes": int(st.session_state.get("htf_minutes", 60)),
            "htf_ema_period": int(st.session_state.get("htf_ema_period", 50)),
            "intraday_square_off": bool(st.session_state.get("intraday_square_off", False)),
            "entry_time_filter": bool(st.session_state.get("entry_time_filter", False)),
            "entry_start_time": str(st.session_state.get("entry_start_time", "09:30")),
            "entry_end_time": str(st.session_state.get("entry_end_time", "14:45")),
        },
        "pos_size_type": st.session_state.get("pos_size_type", "Fixed Value per Trade (₹) (Default)"),
        "fixed_pos_value": float(st.session_state.get("fixed_pos_value", 50000.0)),
        "lot_size": int(st.session_state.get("lot_size", 1)),
    }


def _list_local_datasets() -> list[dict[str, str]]:
    """Return existing local OHLCV datasets from ``downloads_log`` (newest first)."""
    hist = get_download_history()
    if hist is None or hist.empty:
        return []
    seen_paths: set[str] = set()
    datasets: list[dict[str, str]] = []
    for _, row in hist.iterrows():
        fp = str(row.get("file_path") or "").strip()
        if not fp or fp in seen_paths or not Path(fp).is_file():
            continue
        seen_paths.add(fp)
        sym = str(row.get("symbol") or "UNKNOWN").strip()
        iv = str(row.get("interval") or "").strip()
        brk = str(row.get("broker") or "").strip()
        fname = Path(fp).name
        datasets.append(
            {
                "label": f"{sym} ({iv} • {brk or 'Local'} • {fname})",
                "symbol": sym,
                "interval": iv,
                "file_path": fp,
            }
        )
    return datasets


def _runner_pid_path() -> Path:
    return cfg_mod.WORKFLOW_DIR / "live_runner.pid"


def _runner_log_path() -> Path:
    return cfg_mod.WORKFLOW_DIR / "live_runner.log"


def _is_pid_running(pid: int) -> bool:
    """Check cross-platform whether process ``pid`` is currently alive."""
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok and exit_code.value == STILL_ACTIVE)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _get_running_pid() -> int | None:
    """Return active background runner PID from ``live_runner.pid``, cleaning stale files."""
    pid_file = _runner_pid_path()
    if not pid_file.is_file():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None
    if _is_pid_running(pid):
        return pid
    pid_file.unlink(missing_ok=True)
    return None


def _start_background_runner(
    *,
    preset_arg: str,
    symbol: str,
    exchange: str,
    interval: str,
    quantity: int,
    product: str,
    mode: str,
    daily_loss_limit: float,
    max_trades: int,
    max_positions: int,
    webhook_url: str,
    live_confirmed: bool,
    oa_host: str,
    oa_key: str,
) -> tuple[bool, str]:
    """Spawn an isolated ``python -m quantdesk.live.signal_runner`` process."""
    existing = _get_running_pid()
    if existing is not None:
        return False, f"Background runner already active (PID {existing})."

    cfg_mod.WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "quantdesk.live.signal_runner",
        "--preset",
        preset_arg,
        "--symbol",
        symbol,
        "--exchange",
        exchange,
        "--interval",
        interval,
        "--mode",
        mode,
        "--quantity",
        str(int(quantity)),
        "--product",
        product,
        "--daily-loss-limit",
        str(float(daily_loss_limit)),
        "--max-trades",
        str(int(max_trades)),
        "--max-positions",
        str(int(max_positions)),
    ]
    if webhook_url.strip():
        cmd.extend(["--webhook-url", webhook_url.strip()])
    if mode == "live" and live_confirmed:
        cmd.append("--live-confirmed")

    env = os.environ.copy()
    env["OPENALGO_HOST_URL"] = oa_host
    if oa_key:
        env["OPENALGO_API_KEY"] = oa_key

    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        popen_kwargs["start_new_session"] = True

    log_path = _runner_log_path()
    try:
        with open(log_path, "a", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cfg_mod.ROOT_DIR),
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                **popen_kwargs,
            )
        _runner_pid_path().write_text(str(proc.pid), encoding="utf-8")
        return True, f"Started background runner (PID {proc.pid})."
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to start background runner: {exc}"


def _stop_background_runner() -> tuple[bool, str]:
    """Stop the background ``signal_runner`` process tracked in ``live_runner.pid``."""
    pid = _get_running_pid()
    pid_file = _runner_pid_path()
    if pid is None:
        pid_file.unlink(missing_ok=True)
        return False, "No active background runner found."
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        if _is_pid_running(pid):
            return False, f"Failed to stop PID {pid}: {exc}"
    pid_file.unlink(missing_ok=True)
    return True, f"Stopped background runner (PID {pid})."


def _resolve_selected_strategy(source: str) -> tuple[dict[str, Any] | None, str, str, str | None]:
    """Return ``(validated_cfg_dict, strategy_name, cli_preset_arg, error_msg)``."""
    if source == "Active Strategy (from Builder)":
        if not st.session_state.get("custom_strategy_code") or not st.session_state.get("indicators"):
            return (
                None,
                "",
                "",
                "No active compiled strategy found in Strategy Builder. Build one there or select a Starter Template.",
            )
        try:
            validated = validate_preset(_builder_dict_from_session())
        except PresetValidationError as exc:
            return None, "", "", f"Active Builder strategy validation failed: {exc}"
        label = str(st.session_state.get("strategy_label") or "QuantDesk_Active")
        return validated, label, "__active__", None

    if source == "Starter Template":
        tpl_names = list(STARTER_TEMPLATES.keys())
        tpl_name = st.selectbox("Starter Template", tpl_names, key="ld_starter_template")
        try:
            validated = validate_preset(get_template(tpl_name))
        except Exception as exc:  # noqa: BLE001
            return None, "", "", f"Failed to load starter template {tpl_name}: {exc}"
        return validated, tpl_name, tpl_name, None

    # Saved Strategy JSON (safe listing from config.STRATEGIES_DIR)
    saved_list = sorted(presets.list_strategies())
    if not saved_list:
        return (
            None,
            "",
            "",
            f"No saved strategy JSON presets found in `{cfg_mod.STRATEGIES_DIR}`.",
        )
    saved_file = st.selectbox("Saved Strategy JSON", saved_list, key="ld_saved_preset")
    try:
        validated = validate_preset(presets.load_strategy_raw(saved_file))
    except (PresetValidationError, ValueError, json.JSONDecodeError, OSError) as exc:
        return None, "", "", f"Invalid saved preset `{saved_file}`: {exc}"
    return validated, saved_file.removesuffix(".json"), saved_file, None


def _get_session_risk_state() -> LiveRiskState:
    state = st.session_state.get("_ld_risk_state")
    if not isinstance(state, LiveRiskState):
        state = LiveRiskState()
        st.session_state["_ld_risk_state"] = state
    return state


def _render_deployer_tab(oa_host: str, oa_key: str) -> None:
    """Tab 2: Strategy Source, Deployment & Risk Controls, Step Runner, Export & PID Runner."""
    has_active = bool(
        st.session_state.get("custom_strategy_code") and st.session_state.get("indicators")
    )
    default_src_idx = 0 if has_active else 1
    source = st.radio(
        "Strategy Source",
        _STRAT_SOURCES,
        index=default_src_idx,
        horizontal=True,
        key="ld_strategy_source",
    )

    strat_cfg, strat_name, cli_preset_arg, strat_err = _resolve_selected_strategy(source)
    if strat_err:
        st.warning(strat_err)

    st.markdown("#### Deployment & Risk Controls")
    c1, c2, c3, c4, c5 = st.columns(5)
    symbol = c1.text_input("Symbol", value="RELIANCE", key="ld_symbol").strip().upper()
    exchange = c2.selectbox("Exchange", _EXCHANGES, index=0, key="ld_exchange")
    interval = c3.selectbox("Interval", _INTERVALS, index=2, key="ld_interval")
    quantity = int(c4.number_input("Quantity", min_value=1, value=1, step=1, key="ld_quantity"))
    product = c5.selectbox("Product", _PRODUCTS, index=0, key="ld_product")

    r1, r2, r3, r4 = st.columns(4)
    exec_mode = r1.selectbox("Execution Mode", _EXEC_MODES, index=0, key="ld_exec_mode")
    daily_loss_limit = float(
        r2.number_input(
            "Daily Max Loss ₹",
            min_value=0.0,
            value=5000.0,
            step=500.0,
            key="ld_daily_loss_limit",
        )
    )
    max_positions = int(
        r3.number_input("Max Open Positions", min_value=1, value=3, step=1, key="ld_max_positions")
    )
    max_trades = int(
        r4.number_input("Max Trades/Day", min_value=1, value=10, step=1, key="ld_max_trades")
    )

    live_confirmed = False
    if exec_mode == "live":
        st.warning("⚠️ **LIVE mode** routes real-money orders through OpenAlgo when signals trigger.")
        live_confirmed = st.checkbox(
            "I confirm LIVE real-money order routing (`live_confirmed`)",
            value=False,
            key="ld_live_confirmed",
        )

    webhook_url = st.text_input(
        "Optional Alert Webhook URL",
        value="",
        placeholder="https://hooks.slack.com/services/... or Discord/Telegram relay",
        key="ld_webhook_url",
    )

    local_datasets = _list_local_datasets()
    selected_ds: dict[str, str] | None = None
    if local_datasets:
        ds_labels = [d["label"] for d in local_datasets]
        matching_idx = next(
            (i for i, d in enumerate(local_datasets) if d["symbol"].upper() == symbol),
            0,
        )
        chosen_label = st.selectbox(
            "Local Dataset Fallback (used automatically for Local Dataset Dry-Run if OpenAlgo is offline)",
            options=ds_labels,
            index=matching_idx,
            key="ld_local_dataset",
        )
        selected_ds = next((d for d in local_datasets if d["label"] == chosen_label), local_datasets[0])

    st.divider()

    # Action bar: 1-Cycle Check, Export Script, Start/Stop Background Runner
    script_src: str | None = None
    script_err: str | None = None
    if strat_cfg is not None and symbol:
        try:
            script_src = generate_openalgo_script(
                strat_cfg,
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                quantity=quantity,
                product=product,
                strategy_name=safe_name(strat_name or "QuantDesk_Bot") or "QuantDesk_Bot",
                host_url=oa_host,
                max_daily_loss=daily_loss_limit,
            )
        except Exception as exc:  # noqa: BLE001
            script_err = str(exc)

    running_pid = _get_running_pid()

    b1, b2, b3 = st.columns(3)
    step_clicked = b1.button(
        "🧪 Run 1-Cycle Signal & Risk Check",
        key="ld_step_btn",
        type="primary",
        disabled=(strat_cfg is None or not symbol),
        width="stretch",
    )
    b2.download_button(
        "📥 Export Standalone OpenAlgo Script (.py)",
        data=script_src or "",
        file_name=f"{safe_name(symbol or 'strategy')}_{interval}_openalgo.py",
        mime="text/x-python",
        disabled=not bool(script_src),
        key="ld_export_script_btn",
        width="stretch",
    )
    if running_pid is not None:
        stop_clicked = b3.button(
            f"⏹️ Stop Background Runner (PID {running_pid})",
            key="ld_stop_runner_btn",
            width="stretch",
        )
        start_clicked = False
    else:
        start_clicked = b3.button(
            "▶️ Start Background Runner",
            key="ld_start_runner_btn",
            disabled=(strat_cfg is None or not symbol),
            width="stretch",
        )
        stop_clicked = False

    if script_err:
        st.error(f"Could not generate standalone OpenAlgo script: {script_err}")

    if step_clicked and strat_cfg is not None and symbol:
        client = OpenAlgoClient(host_url=oa_host, api_key=oa_key, timeout=5.0)
        bars_df = pd.DataFrame()
        data_source_label = "OpenAlgo Live History"

        if oa_key:
            bars_df = client.fetch_recent_bars(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                lookback_days=15,
            )

        if bars_df is None or bars_df.empty:
            if selected_ds is not None:
                try:
                    bars_df = load_indexed(selected_ds["file_path"])
                    data_source_label = f"Local Dataset Dry-Run — `{selected_ds['label']}`"
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed loading local dataset fallback: {exc}")
                    bars_df = pd.DataFrame()
            else:
                st.warning(
                    "OpenAlgo history unavailable and no local Parquet dataset found in `downloads_log`."
                )

        if bars_df is not None and not bars_df.empty:
            spec = LiveDeploymentSpec(
                strategy_cfg=strat_cfg,
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                quantity=quantity,
                product=product,
                strategy_name=safe_name(strat_name or "QuantDesk") or "QuantDesk",
                risk_config=LiveRiskConfig(
                    execution_mode=exec_mode,
                    daily_loss_limit=daily_loss_limit,
                    max_trades_per_day=max_trades,
                    max_open_positions=max_positions,
                    live_confirmed=live_confirmed,
                ),
            )
            risk_state = _get_session_risk_state()
            event = step_once(
                spec,
                client,
                risk_state,
                bars_df=bars_df,
                webhook_url=webhook_url,
            )
            event["_data_source"] = data_source_label
            event["_bars_count"] = int(len(bars_df))
            st.session_state["ld_last_step_event"] = event

    last_event = st.session_state.get("ld_last_step_event")
    if isinstance(last_event, dict):
        src_lbl = last_event.get("_data_source", "Signal Check")
        bars_cnt = last_event.get("_bars_count", 0)
        st.info(f"📊 **1-Cycle Check Source:** {src_lbl} ({bars_cnt} bars)")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Signal Action", str(last_event.get("action", "HOLD")))
        m2.metric("Last Close", _format_inr(last_event.get("close")))
        m3.metric("Position Side", str(last_event.get("position_side", "FLAT")))
        m4.metric("Risk Gate", "ALLOWED" if last_event.get("risk_allowed") else "BLOCKED")
        st.caption(
            f"Bar: `{last_event.get('bar_time', '')}` | Mode: `{last_event.get('execution_mode', '')}` | "
            f"Reason: {last_event.get('risk_reason', '')}"
        )
        if last_event.get("signal_error"):
            st.error(f"Signal Evaluation Error: {last_event['signal_error']}")
        if last_event.get("order_result"):
            st.json(last_event["order_result"])

    if start_clicked and strat_cfg is not None and symbol:
        if exec_mode == "live" and not live_confirmed:
            st.error("Check the `live_confirmed` confirmation checkbox before starting in LIVE mode.")
        else:
            preset_arg = cli_preset_arg
            if preset_arg == "__active__":
                cfg_mod.WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
                active_path = cfg_mod.WORKFLOW_DIR / "active_live_preset.json"
                active_path.write_text(json.dumps(strat_cfg, indent=2), encoding="utf-8")
                preset_arg = str(active_path)
            ok, msg = _start_background_runner(
                preset_arg=preset_arg,
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                quantity=quantity,
                product=product,
                mode=exec_mode,
                daily_loss_limit=daily_loss_limit,
                max_trades=max_trades,
                max_positions=max_positions,
                webhook_url=webhook_url,
                live_confirmed=live_confirmed,
                oa_host=oa_host,
                oa_key=oa_key,
            )
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    if stop_clicked:
        ok, msg = _stop_background_runner()
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.warning(msg)

    if running_pid is not None:
        st.caption(f"🟢 Background runner active — PID `{running_pid}` (`{_runner_pid_path()}`)")
    else:
        st.caption("⚪ Background runner stopped.")

    if script_src:
        with st.expander("Preview Standalone OpenAlgo Script (.py)", expanded=False):
            st.code(script_src, language="python")


def _render_history_tab() -> None:
    """Tab 3: Live Signals & Alert History JSONL log viewer."""
    c_info, c_ref = st.columns([3, 1])
    c_info.caption(f"Audit log: `{cfg_mod.WORKFLOW_DIR / 'live_events.jsonl'}`")
    if c_ref.button("🔄 Refresh History", key="ld_refresh_events_btn", width="stretch"):
        st.rerun()

    events = read_live_events(limit=200)
    if not events:
        st.info("No live signal or alert events logged yet. Run a 1-cycle check in Tab 2.")
        return

    rows: list[dict[str, Any]] = []
    for ev in events:
        row = dict(ev)
        for complex_col in ("indicators", "order_result"):
            if complex_col in row and not isinstance(row[complex_col], (str, int, float, bool, type(None))):
                row[complex_col] = json.dumps(row[complex_col], default=str)
        rows.append(row)

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render(ctx) -> None:
    """Render the Live Monitor & Deploy page."""
    st.title("🚦 Live Monitor & Deploy")
    st.caption(
        "Monitor OpenAlgo account telemetry, run non-blocking 1-cycle strategy checks, export "
        "standalone OpenAlgo scripts, or manage the background signal runner."
    )

    oa_host, oa_key = _resolve_openalgo_creds(ctx)

    tab1, tab2, tab3 = st.tabs(
        [
            "📡 OpenAlgo Account & Telemetry Monitor",
            "🚀 Strategy Deployer, Risk Guard & Script Exporter",
            "📜 Live Signals & Alert History",
        ]
    )
    with tab1:
        _render_telemetry_tab(ctx, oa_host, oa_key)
    with tab2:
        _render_deployer_tab(oa_host, oa_key)
    with tab3:
        _render_history_tab()
