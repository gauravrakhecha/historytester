"""
quantdesk.data.corporate_actions — split / bonus / dividend / rights sanitisation.

Verbatim port of the original engine (upstox_app.py:428-519):
- ``fetch_corporate_actions_upstox`` pulls events from the Upstox Fundamentals API,
- ``apply_corporate_actions`` back-adjusts OHLC for splits/bonuses, and injects cash
  dividends + rights premium (TERP) into dedicated columns the strategy reads.

Math is reproduced exactly so backtest P&L is unchanged.
"""
from __future__ import annotations

import asyncio

import pandas as pd

from quantdesk.core.logging import get_logger

log = get_logger("data.corporate_actions")


async def fetch_corporate_actions_upstox(session, instrument_key, token) -> list:
    """Fetch corporate-action events for an instrument's ISIN (returns [] on failure)."""
    if not isinstance(instrument_key, str) or "|" not in instrument_key:
        return []
    isin = instrument_key.split("|")[1]
    url = f"https://api.upstox.com/v2/fundamentals/{isin}/corporate-actions"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    for attempt in range(3):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
        except Exception:
            await asyncio.sleep(0.5 * (attempt + 1))
    return []


def apply_corporate_actions(df: pd.DataFrame, corp_actions: list) -> pd.DataFrame:
    """Adjust ``df`` in place for splits/bonuses and add Dividend / Rights_Premium columns.

    Splits & bonuses divide pre-ex-date OHLC by the multiplier (and scale volume up);
    cash dividends and rights premiums are written to the first bar on/after the ex-date.
    """
    if df.empty:
        return df
    df["Dividend"] = 0.0
    df["Rights_Premium"] = 0.0

    if not corp_actions:
        return df

    splits_bonuses = []
    for ev in corp_actions:
        name = str(ev.get("name", "")).upper()
        amount = ev.get("amount")
        ratio_str = ev.get("ratio")
        ex_date_str = ev.get("expiry_date")
        if not ex_date_str:
            continue

        try:
            ex_date = pd.to_datetime(ex_date_str, format="%d %b %Y")
        except Exception:
            continue

        # Cash Dividends
        if "DIVIDEND" in name and amount:
            mask = df["Timestamp"].dt.date >= ex_date.date()
            if mask.any():
                idx = df[mask].index[0]
                df.at[idx, "Dividend"] += float(amount)

        # Rights Cash Conversion (Theoretical Ex-Rights Price)
        elif "RIGHTS" in name and ratio_str and ":" in str(ratio_str):
            try:
                a, b = map(float, str(ratio_str).split(":"))
                if a > 0 and b > 0:
                    mask_prev = df["Timestamp"].dt.date < ex_date.date()
                    if mask_prev.any():
                        prev_close = df[mask_prev].iloc[-1]["Close"]
                        sub_price = float(amount) if amount else 0.0
                        if prev_close > sub_price:
                            ex_rights_price = (b * prev_close + a * sub_price) / (b + a)
                            right_value = prev_close - ex_rights_price

                            ex_mask = df["Timestamp"].dt.date >= ex_date.date()
                            if ex_mask.any():
                                idx = df[ex_mask].index[0]
                                df.at[idx, "Rights_Premium"] += right_value
            except Exception:
                pass

        # Splits & Bonus
        elif ("SPLIT" in name or "BONUS" in name) and ratio_str and ":" in str(ratio_str):
            try:
                a, b = map(float, str(ratio_str).split(":"))
                if a <= 0 or b <= 0:
                    continue
                multiplier = 1.0
                if "BONUS" in name:
                    multiplier = (a + b) / b
                else:
                    multiplier = max(a, b) / min(a, b)

                if multiplier != 1.0:
                    splits_bonuses.append({"date": ex_date.date(), "mult": multiplier})
            except Exception:
                pass

    if splits_bonuses:
        splits_bonuses.sort(key=lambda x: x["date"], reverse=True)
        # Cast to float64 first: split/bonus adjustment is fractional, and Volume arrives as int64
        # from the broker JSON. pandas >= 3.0 raises on in-place float assignment into an int column
        # (it no longer silently upcasts), so we widen the affected columns up front.
        for _col in ("Open", "High", "Low", "Close", "Volume"):
            if _col in df.columns:
                df[_col] = df[_col].astype("float64")
        for sb in splits_bonuses:
            mask = df["Timestamp"].dt.date < sb["date"]
            df.loc[mask, "Open"] /= sb["mult"]
            df.loc[mask, "High"] /= sb["mult"]
            df.loc[mask, "Low"] /= sb["mult"]
            df.loc[mask, "Close"] /= sb["mult"]
            if "Volume" in df.columns:
                df.loc[mask, "Volume"] *= sb["mult"]

    return df
