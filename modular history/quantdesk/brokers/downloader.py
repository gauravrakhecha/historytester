"""
quantdesk.brokers.downloader — async download orchestrator.

Ports ``fetch_chunk_async`` / ``download_long_range_async`` / ``run_async_download``
(upstox_app.py:522-644). Behaviour preserved: semaphore(5), 30-day chunks for 1-minute data
(365-day otherwise), as-completed gathering, RAM-aware Parquet spooling (psutil > 85%),
de-dup + sort, and the optional Upstox corporate-actions adjustment.

Change vs monolith: the Streamlit ``progress_bar`` argument is replaced with a generic
``progress_cb(pct: float, text: str)`` callable so this module stays Streamlit-free. The UI
passes a callback that drives ``st.progress``.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import timedelta
from typing import Callable, Optional

import aiohttp
import pandas as pd
import psutil

from quantdesk.brokers import dhan as dhan_broker
from quantdesk.brokers import upstox as upstox_broker
from quantdesk.brokers import zerodha as zerodha_broker
from quantdesk.brokers.base import RetryableError
from quantdesk.data.corporate_actions import (
    apply_corporate_actions,
    fetch_corporate_actions_upstox,
)
from quantdesk.core.logging import get_logger

log = get_logger("brokers.downloader")

_FETCHERS = {
    "Upstox": upstox_broker.fetch_single,
    "Zerodha": zerodha_broker.fetch_single,
    "Dhan": dhan_broker.fetch_single,
}

ProgressCB = Optional[Callable[[float, str], None]]


async def fetch_chunk_async(session, sem, broker, row, interval, start_dt, end_dt, auth_dict):
    """One date-chunk fetch with semaphore + 3-attempt retry (empty frame on total failure)."""
    fetcher = _FETCHERS.get(broker)
    if fetcher is None:
        return pd.DataFrame()
    async with sem:
        for attempt in range(3):
            try:
                return await fetcher(session, row, interval, start_dt, end_dt, auth_dict)
            except (RetryableError, Exception):  # noqa: BLE001 — mirror original broad retry
                await asyncio.sleep(0.5 * (attempt + 1))
        return pd.DataFrame()


async def download_long_range_async(
    broker,
    row,
    interval,
    start_date,
    end_date,
    auth_dict,
    progress_cb: ProgressCB = None,
    apply_corp_actions: bool = False,
) -> pd.DataFrame:
    """Download a full date range in chunks, concurrently, with RAM-safe spooling."""
    delta = timedelta(days=30) if interval == "1 Minute" else timedelta(days=365)
    tasks, chunks = [], []
    curr = start_date
    while curr <= end_date:
        nxt = min(curr + delta, end_date)
        chunks.append((curr, nxt))
        curr = nxt + timedelta(days=1)

    dfs = []
    temp_files = []  # PyArrow RAM protection
    sem = asyncio.Semaphore(5)

    async with aiohttp.ClientSession() as session:
        for c_start, c_end in chunks:
            tasks.append(
                fetch_chunk_async(session, sem, broker, row, interval, c_start, c_end, auth_dict)
            )

        for i, coro in enumerate(asyncio.as_completed(tasks)):
            res = await coro
            if not res.empty:
                dfs.append(res)
                # Dump to Parquet if RAM is critical
                if psutil.virtual_memory().percent > 85 and len(dfs) > 1:
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".parquet")
                    pd.concat(dfs, ignore_index=True).to_parquet(tf.name, engine="pyarrow")
                    temp_files.append(tf.name)
                    dfs = []

            if progress_cb:
                pct = min(1.0, (i + 1) / len(tasks))
                progress_cb(pct, f"Downloading Data: {int(pct * 100)}% Complete")

    if temp_files:
        if dfs:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".parquet")
            pd.concat(dfs, ignore_index=True).to_parquet(tf.name, engine="pyarrow")
            temp_files.append(tf.name)
            dfs = []
        final_df = pd.concat(
            [pd.read_parquet(f, engine="pyarrow") for f in temp_files], ignore_index=True
        )
        for f in temp_files:
            os.remove(f)
    elif dfs:
        final_df = pd.concat(dfs, ignore_index=True)
    else:
        return pd.DataFrame()

    final_df.drop_duplicates(subset=["Timestamp"], keep="first", inplace=True)
    final_df.sort_values("Timestamp", inplace=True)

    if apply_corp_actions and broker == "Upstox":
        async with aiohttp.ClientSession() as ca_session:
            corp_actions = await fetch_corporate_actions_upstox(
                ca_session, row["instrument_key"], auth_dict["access_token"]
            )
            final_df = apply_corporate_actions(final_df, corp_actions)

    return final_df


def run_async_download(*args, **kwargs) -> pd.DataFrame:
    """Sync wrapper: run ``download_long_range_async`` on a fresh event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(download_long_range_async(*args, **kwargs))
    finally:
        loop.close()
