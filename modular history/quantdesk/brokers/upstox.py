"""
quantdesk.brokers.upstox — Upstox candle fetch + options endpoints.

Ports the Upstox branch of ``fetch_chunk_async`` (upstox_app.py:528-540) and the options
helpers ``get_available_expiries`` (659-677) and ``get_expired_contracts`` (679-693). The
options helpers returned via ``st.error/st.warning`` in the monolith; here they return
``(data, error_message)`` so the UI layer decides how to display it.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pandas as pd
import requests

from quantdesk.brokers.base import CANDLE_COLUMNS, RetryableError
from quantdesk.core.logging import get_logger

log = get_logger("brokers.upstox")


async def fetch_single(session, row, interval, start_dt, end_dt, auth_dict) -> pd.DataFrame:
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    v3_interval = "1minute" if interval == "1 Minute" else "day"
    url = (
        f"https://api.upstox.com/v2/historical-candle/"
        f"{row['instrument_key']}/{v3_interval}/{end_str}/{start_str}"
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {auth_dict['access_token']}"}
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            candles = data.get("data", {}).get("candles", [])
            if candles:
                df = pd.DataFrame(candles, columns=CANDLE_COLUMNS[: len(candles[0])])
                df["Timestamp"] = pd.to_datetime(df["Timestamp"]).dt.tz_localize(None)
                return df
            return pd.DataFrame()
        raise RetryableError(f"Upstox HTTP {resp.status}")


# --- options endpoints (sync; used by the Options Chain page) ----------------
def get_available_expiries(instrument_key, token):
    """Return (expiries_list, error_message_or_None)."""
    url = "https://api.upstox.com/v2/expired-instruments/expiries"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, params={"instrument_key": instrument_key})
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                data.sort(reverse=True)
            return data, None
        try:
            msg = resp.json().get("errors", [{}])[0].get("message", "Unknown Error")
        except Exception:
            msg = f"API Error: {resp.status_code}"
        return [], msg
    except Exception as e:  # noqa: BLE001
        return [], f"Connection Error: {e}"


def get_expired_contracts(instrument_key, expiry_date, token):
    """Return (contracts_list, error_message_or_None)."""
    url = "https://api.upstox.com/v2/expired-instruments/option/contract"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(
            url, headers=headers,
            params={"instrument_key": instrument_key, "expiry_date": expiry_date},
        )
        if resp.status_code == 200:
            return resp.json().get("data", []), None
        try:
            msg = resp.json().get("errors", [{}])[0].get("message", "Unknown API Error")
        except Exception:
            msg = f"API Error: {resp.status_code}"
        return [], msg
    except Exception as e:  # noqa: BLE001
        return [], f"Connection Error: {e}"


# --- historical options candle download (used by the Options Chain page) -----
async def _fetch_opt_history(session, c, auth_token, from_d, to_d):
    url = (
        f"https://api.upstox.com/v2/expired-instruments/historical-candle/"
        f"{c['instrument_key']}/1minute/{to_d}/{from_d}"
    )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {auth_token}"}
    for _attempt in range(3):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return c, data.get("data", {}).get("candles", [])
        except Exception:
            await asyncio.sleep(0.5)
    return c, []


def run_options_download(items, auth_token, from_d, to_d, progress_cb=None):
    """Download 1-minute candles for many option contracts concurrently.

    Returns a list of ``(contract_dict, candles_list)``. Mirrors the monolith's inline
    Options Chain async download (upstox_app.py:1171-1204) but as a reusable helper.
    """
    total = len(items)

    async def _download_all():
        results = []
        sem = asyncio.Semaphore(10)

        async with aiohttp.ClientSession() as session:
            async def bound_fetch(c):
                async with sem:
                    return await _fetch_opt_history(session, c, auth_token, from_d, to_d)

            tasks = [bound_fetch(c) for c in items]
            for i, coro in enumerate(asyncio.as_completed(tasks)):
                res = await coro
                results.append(res)
                if progress_cb and total:
                    pct = (i + 1) / total
                    progress_cb(pct, f"Fetching Options: {int(pct * 100)}% ({i+1}/{total})")
        return results

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_download_all())
    finally:
        loop.close()
