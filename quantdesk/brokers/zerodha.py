"""quantdesk.brokers.zerodha — Zerodha Kite candle fetch (upstox_app.py:542-556)."""
from __future__ import annotations

import pandas as pd

from quantdesk.brokers.base import CANDLE_COLUMNS, raise_for_broker_status


async def fetch_single(session, row, interval, start_dt, end_dt, auth_dict) -> pd.DataFrame:
    z_int = "minute" if interval == "1 Minute" else "day"
    z_start_str = start_dt.strftime("%Y-%m-%d+00:00:00").replace("+", "%20")
    z_end_str = end_dt.strftime("%Y-%m-%d+23:59:59").replace("+", "%20")
    url = (
        f"https://api.kite.trade/instruments/historical/"
        f"{row['instrument_key']}/{z_int}?from={z_start_str}&to={z_end_str}"
    )
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {auth_dict['api_key']}:{auth_dict['access_token']}",
    }
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            candles = data.get("data", {}).get("candles", [])
            if candles:
                df = pd.DataFrame(candles, columns=CANDLE_COLUMNS[: len(candles[0])])
                df["Timestamp"] = pd.to_datetime(df["Timestamp"]).dt.tz_localize(None)
                return df
            return pd.DataFrame()
        raise_for_broker_status("Zerodha", resp.status, resp.headers)
