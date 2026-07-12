"""quantdesk.brokers.dhan — Dhan candle fetch (upstox_app.py:558-578)."""
from __future__ import annotations

import pandas as pd

from quantdesk.brokers.base import RetryableError


async def fetch_single(session, row, interval, start_dt, end_dt, auth_dict) -> pd.DataFrame:
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    is_intra = interval == "1 Minute"
    url = (
        "https://api.dhan.co/v2/charts/intraday"
        if is_intra
        else "https://api.dhan.co/v2/charts/historical"
    )
    headers = {
        "access-token": auth_dict["access_token"],
        "client-id": auth_dict["client_id"],
        "Content-Type": "application/json",
    }
    payload = {
        "securityId": str(row["instrument_key"]),
        "exchangeSegment": row.get("dhan_exchange_segment", "NSE_EQ"),
        "instrument": row.get("instrument_type", "EQUITY"),
        "fromDate": start_str,
        "toDate": end_str,
    }
    async with session.post(url, headers=headers, json=payload) as resp:
        if resp.status == 200:
            data = await resp.json()
            d = data.get("data", {})
            if "start_Time" in d and d["start_Time"]:
                min_len = min(
                    len(d.get(k, [])) for k in ["start_Time", "open", "high", "low", "close", "volume"]
                )
                if min_len > 0:
                    return pd.DataFrame(
                        {
                            "Timestamp": pd.to_datetime(d["start_Time"][:min_len]),
                            "Open": d.get("open", [])[:min_len],
                            "High": d.get("high", [])[:min_len],
                            "Low": d.get("low", [])[:min_len],
                            "Close": d.get("close", [])[:min_len],
                            "Volume": d.get("volume", [])[:min_len],
                        }
                    )
            return pd.DataFrame()
        raise RetryableError(f"Dhan HTTP {resp.status}")
