"""
quantdesk.brokers.openalgo — OpenAlgo REST v1 broker adapter.

Provides:
- ``normalize_host_url`` / ``normalize_openalgo_exchange``
- ``ping_openalgo`` (sync connectivity + analyzer probe)
- ``search_openalgo_symbols`` (sync symbol search)
- ``fetch_single`` (async historical candle fetcher compatible with ``downloader.py``)

All OpenAlgo endpoints enforce strict Marshmallow schemas (HTTP 400 on extra keys),
so every JSON payload below sends only the exact keys accepted by each endpoint.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pandas as pd
import requests

from quantdesk.brokers.base import raise_for_broker_status
from quantdesk.core.logging import get_logger

log = get_logger("brokers.openalgo")

DEFAULT_HOST_URL = "http://127.0.0.1:5000"

# Upstox / QuantDesk segment & exchange names -> OpenAlgo exchange codes
_EXCHANGE_MAP: dict[str, str] = {
    "NSE": "NSE",
    "NSE_EQ": "NSE",
    "NFO": "NFO",
    "NSE_FO": "NFO",
    "NSE_FO_FUT": "NFO",
    "NSE_FO_OPT": "NFO",
    "NSE_FNO": "NFO",
    "BSE": "BSE",
    "BSE_EQ": "BSE",
    "BFO": "BFO",
    "BSE_FO": "BFO",
    "BSE_FNO": "BFO",
    "MCX": "MCX",
    "MCX_FO": "MCX",
    "MCX_COMM": "MCX",
    "CDS": "CDS",
    "NCD_FO": "CDS",
    "NSE_CURRENCY": "CDS",
    "NSE_INDEX": "NSE_INDEX",
    "BSE_INDEX": "BSE_INDEX",
}

_INTERVAL_MAP: dict[str, str] = {
    "1 minute": "1m",
    "1minute": "1m",
    "1min": "1m",
    "3 minute": "3m",
    "3 minutes": "3m",
    "3min": "3m",
    "5 minute": "5m",
    "5 minutes": "5m",
    "5min": "5m",
    "10 minute": "10m",
    "10 minutes": "10m",
    "10min": "10m",
    "15 minute": "15m",
    "15 minutes": "15m",
    "15min": "15m",
    "30 minute": "30m",
    "30 minutes": "30m",
    "30min": "30m",
    "60 minute": "1h",
    "60 minutes": "1h",
    "1 hour": "1h",
    "day": "D",
    "1 day": "D",
    "1d": "D",
    "daily": "D",
    "week": "W",
    "1w": "W",
    "month": "M",
}

_KNOWN_LOWER_INTERVALS = frozenset({"1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h"})
_KNOWN_UPPER_INTERVALS = frozenset({"D", "W", "M"})


def _clean_str(val: Any) -> str:
    """Convert scalar to stripped string, treating None/NaN as empty string."""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return "" if s.lower() == "nan" else s


def normalize_host_url(host_url: str | None) -> str:
    """Normalize an OpenAlgo base URL (default ``http://127.0.0.1:5000``, no trailing ``/``)."""
    raw = (host_url or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_HOST_URL
    lower = raw.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        raw = f"http://{raw}"
    return raw


def normalize_openalgo_exchange(row_or_exch: Any) -> str:
    """Map an instrument row or segment/exchange string to an OpenAlgo exchange code."""
    if isinstance(row_or_exch, Mapping) or (
        not isinstance(row_or_exch, str) and hasattr(row_or_exch, "get")
    ):
        for key in ("openalgo_exchange", "segment", "exchange"):
            candidate = _clean_str(row_or_exch.get(key))
            if candidate:
                mapped = _EXCHANGE_MAP.get(candidate.upper())
                if mapped is not None:
                    return mapped
        return "NSE"

    candidate = _clean_str(row_or_exch)
    if not candidate:
        return "NSE"
    return _EXCHANGE_MAP.get(candidate.upper(), "NSE")


def _normalize_interval(interval: Any) -> str:
    """Map QuantDesk interval labels to OpenAlgo interval codes, passing through known codes."""
    raw = _clean_str(interval)
    if not raw:
        return "D"
    if raw in _KNOWN_LOWER_INTERVALS or raw in _KNOWN_UPPER_INTERVALS:
        return raw
    low = raw.lower()
    if low in _INTERVAL_MAP:
        return _INTERVAL_MAP[low]
    if low in _KNOWN_LOWER_INTERVALS:
        return low
    up = raw.upper()
    if up in _KNOWN_UPPER_INTERVALS:
        return up
    return raw


def ping_openalgo(host_url: str, api_key: str, timeout: float = 5.0) -> tuple[bool, dict]:
    """Check OpenAlgo connectivity via ``POST /api/v1/ping`` and probe ``/api/v1/analyzer``.

    Never raises on network, timeout, or non-200 errors.
    """
    clean_host = normalize_host_url(host_url)
    clean_key = _clean_str(api_key)
    base_info: dict[str, Any] = {
        "latency_ms": None,
        "broker": "",
        "analyzer_active": False,
        "mode": "",
        "message": "",
    }
    if not clean_key:
        base_info["message"] = "OpenAlgo API key is required."
        return False, base_info

    ping_url = f"{clean_host}/api/v1/ping"
    t0 = time.perf_counter()
    try:
        resp = requests.post(ping_url, json={"apikey": clean_key}, timeout=timeout)
    except requests.exceptions.Timeout:
        base_info["message"] = f"Timeout connecting to OpenAlgo at {clean_host}."
        return False, base_info
    except requests.exceptions.ConnectionError:
        base_info["message"] = f"Cannot connect to OpenAlgo at {clean_host}."
        return False, base_info
    except requests.exceptions.RequestException as exc:
        base_info["message"] = f"OpenAlgo request failed: {exc}"
        return False, base_info

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    base_info["latency_ms"] = latency_ms

    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {}

    if resp.status_code != 200 or (isinstance(payload, dict) and payload.get("status") == "error"):
        err_msg = ""
        if isinstance(payload, dict):
            err_msg = _clean_str(payload.get("message") or payload.get("error"))
        base_info["message"] = err_msg or f"OpenAlgo HTTP {resp.status_code}"
        return False, base_info

    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else (
        payload if isinstance(payload, dict) else {}
    )
    broker = _clean_str(data.get("broker") or (payload.get("broker") if isinstance(payload, dict) else ""))
    message = _clean_str(
        data.get("message") or (payload.get("message") if isinstance(payload, dict) else "")
    ) or "pong"

    analyzer_active = False
    mode = "unknown"
    try:
        a_resp = requests.post(
            f"{clean_host}/api/v1/analyzer",
            json={"apikey": clean_key},
            timeout=timeout,
        )
        if a_resp.status_code == 200:
            a_payload = a_resp.json()
            if isinstance(a_payload, dict) and a_payload.get("status") != "error":
                a_data = a_payload.get("data") if isinstance(a_payload.get("data"), dict) else a_payload
                if "analyze_mode" in a_data and a_data["analyze_mode"] is not None:
                    analyzer_active = bool(a_data["analyze_mode"])
                elif "analyzer_active" in a_data and a_data["analyzer_active"] is not None:
                    analyzer_active = bool(a_data["analyzer_active"])
                else:
                    analyzer_active = _clean_str(a_data.get("mode")).lower() in ("analyze", "sandbox")
                raw_mode = _clean_str(a_data.get("mode"))
                mode = raw_mode or ("analyze" if analyzer_active else "live")
    except Exception as exc:  # noqa: BLE001
        log.debug("OpenAlgo analyzer probe failed (non-fatal): %s", exc)

    return True, {
        "latency_ms": latency_ms,
        "broker": broker,
        "analyzer_active": analyzer_active,
        "mode": mode,
        "message": message,
    }


def search_openalgo_symbols(
    host_url: str,
    api_key: str,
    query: str,
    exchange: str = "",
    timeout: float = 10.0,
) -> list[dict]:
    """Search instruments via ``POST /api/v1/search`` and normalize hit fields."""
    clean_host = normalize_host_url(host_url)
    clean_key = _clean_str(api_key)
    clean_query = _clean_str(query)
    if not clean_key or not clean_query:
        return []

    payload: dict[str, str] = {"apikey": clean_key, "query": clean_query}
    clean_exch = _clean_str(exchange)
    if clean_exch:
        payload["exchange"] = _EXCHANGE_MAP.get(clean_exch.upper(), clean_exch.upper())

    try:
        resp = requests.post(f"{clean_host}/api/v1/search", json=payload, timeout=timeout)
        if resp.status_code != 200:
            log.warning("OpenAlgo search HTTP %s for query=%r", resp.status_code, clean_query)
            return []
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenAlgo search error for query=%r: %s", clean_query, exc)
        return []

    if isinstance(body, dict) and body.get("status") == "error":
        log.warning("OpenAlgo search error response: %s", body.get("message", ""))
        return []

    raw_hits = body.get("data", []) if isinstance(body, dict) else body
    if not isinstance(raw_hits, list):
        return []

    normalized: list[dict] = []
    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        sym = _clean_str(item.get("tradingsymbol")) or _clean_str(item.get("symbol"))
        if not sym:
            continue
        lot_raw = item.get("lotsize") if item.get("lotsize") is not None else item.get("lot_size", 1)
        normalized.append(
            {
                "tradingsymbol": sym,
                "symbol": _clean_str(item.get("symbol")) or sym,
                "exchange": _clean_str(item.get("exchange")) or payload.get("exchange", ""),
                "name": _clean_str(item.get("name")) or sym,
                "instrumenttype": _clean_str(
                    item.get("instrumenttype") or item.get("instrument_type")
                ),
                "lotsize": lot_raw,
            }
        )
    return normalized


async def fetch_single(session, row, interval, start_dt, end_dt, auth_dict) -> pd.DataFrame:
    """Fetch one date-chunk of OHLCV history from OpenAlgo ``POST /api/v1/history``."""
    auth = auth_dict or {}
    host_url = normalize_host_url(auth.get("host_url"))
    apikey = _clean_str(auth.get("api_key"))
    oa_int = _normalize_interval(interval)
    symbol = (
        _clean_str(row.get("tradingsymbol"))
        or _clean_str(row.get("symbol"))
        or _clean_str(row.get("instrument_key"))
    )
    exchange = normalize_openalgo_exchange(row)
    start_str = start_dt.strftime("%Y-%m-%d") if hasattr(start_dt, "strftime") else str(start_dt)[:10]
    end_str = end_dt.strftime("%Y-%m-%d") if hasattr(end_dt, "strftime") else str(end_dt)[:10]

    payload = {
        "apikey": apikey,
        "symbol": symbol,
        "exchange": exchange,
        "interval": oa_int,
        "start_date": start_str,
        "end_date": end_str,
    }
    url = f"{host_url}/api/v1/history"
    async with session.post(url, json=payload) as resp:
        if resp.status == 200:
            body = await resp.json()
            if isinstance(body, dict) and body.get("status") == "error":
                log.warning(
                    "OpenAlgo history returned status=error for %s:%s (%s): %s",
                    exchange, symbol, oa_int, body.get("message", ""),
                )
                return pd.DataFrame()
            rows = body.get("data", []) if isinstance(body, dict) else []
            if not rows:
                return pd.DataFrame()

            raw = pd.DataFrame(rows)
            if "timestamp" not in raw.columns:
                return pd.DataFrame()

            ts_epoch = pd.to_numeric(raw["timestamp"], errors="coerce")
            ts_ist = (
                pd.to_datetime(ts_epoch, unit="s", utc=True)
                .dt.tz_convert("Asia/Kolkata")
                .dt.tz_localize(None)
            )
            out = pd.DataFrame(
                {
                    "Timestamp": ts_ist,
                    "Open": pd.to_numeric(raw["open"], errors="coerce"),
                    "High": pd.to_numeric(raw["high"], errors="coerce"),
                    "Low": pd.to_numeric(raw["low"], errors="coerce"),
                    "Close": pd.to_numeric(raw["close"], errors="coerce"),
                    "Volume": pd.to_numeric(
                        raw["volume"] if "volume" in raw.columns else 0,
                        errors="coerce",
                    ).fillna(0),
                }
            )
            if "oi" in raw.columns:
                oi = pd.to_numeric(raw["oi"], errors="coerce")
                if not oi.isna().all():
                    out["OI"] = oi

            out = out.dropna(subset=["Timestamp"]).sort_values("Timestamp").reset_index(drop=True)
            return out

        raise_for_broker_status("OpenAlgo", resp.status, resp.headers)
