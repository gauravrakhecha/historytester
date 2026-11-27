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
import random
import tempfile
from datetime import timedelta
from typing import Callable, List, NamedTuple, Optional, Tuple

import aiohttp
import pandas as pd
import psutil

from quantdesk.brokers import dhan as dhan_broker
from quantdesk.brokers import openalgo as openalgo_broker
from quantdesk.brokers import upstox as upstox_broker
from quantdesk.brokers import zerodha as zerodha_broker
from quantdesk.brokers.base import ChunkDownloadError, NonRetryableError, RetryableError
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
    "OpenAlgo": openalgo_broker.fetch_single,
}

ProgressCB = Optional[Callable[[float, str], None]]

# Retry tuning. Exponential back-off with full jitter (0.5, 1, 2 s ceilings),
# capped, and superseded by an explicit HTTP 429 ``Retry-After`` when present.
_MAX_ATTEMPTS = 3
_BASE_BACKOFF = 0.5
_MAX_BACKOFF = 30.0

DateRange = Tuple[object, object]


class DownloadResult(NamedTuple):
    """Outcome of a long-range download.

    ``df`` is the (de-duplicated, sorted) data actually retrieved. ``failed_ranges``
    lists every chunk that exhausted its retries — a NON-EMPTY list means the data
    has holes and callers must NOT present it as complete. ``requested_start`` /
    ``requested_end`` echo what was asked for so callers can compare against the
    frame's actual coverage.
    """

    df: pd.DataFrame
    failed_ranges: List[DateRange]
    requested_start: object
    requested_end: object

    @property
    def complete(self) -> bool:
        """True only if every chunk was retrieved (no holes)."""
        return not self.failed_ranges


def _backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """Seconds to sleep before the next retry.

    Honours an explicit ``Retry-After`` (rate-limit) when the broker supplies one;
    otherwise exponential back-off with full jitter, capped at ``_MAX_BACKOFF``.
    """
    if retry_after is not None and retry_after > 0:
        return min(retry_after, _MAX_BACKOFF)
    ceiling = min(_MAX_BACKOFF, _BASE_BACKOFF * (2 ** attempt))
    return random.uniform(0.0, ceiling)


async def fetch_chunk_async(session, sem, broker, row, interval, start_dt, end_dt, auth_dict):
    """One date-chunk fetch with a semaphore + bounded retry.

    Returns the fetched frame (possibly legitimately empty). Raises
    :class:`ChunkDownloadError` — carrying the exact ``[start, end]`` range — when
    all retries are exhausted, so the orchestrator can record the hole instead of
    silently dropping it.
    """
    fetcher = _FETCHERS.get(broker)
    if fetcher is None:
        raise ChunkDownloadError(
            start_dt, end_dt, attempts=0, last_error=ValueError(f"no fetcher for broker {broker!r}")
        )
    last_err: BaseException | None = None
    attempts = 0
    async with sem:
        for attempt in range(_MAX_ATTEMPTS):
            attempts = attempt + 1
            try:
                return await fetcher(session, row, interval, start_dt, end_dt, auth_dict)
            except Exception as exc:  # noqa: BLE001 — broad retry, mirror original behaviour
                last_err = exc
                if isinstance(exc, NonRetryableError) or not getattr(exc, "retryable", True):
                    break
                if attempt < _MAX_ATTEMPTS - 1:
                    retry_after = getattr(exc, "retry_after", None)
                    await asyncio.sleep(_backoff_seconds(attempt, retry_after))
    log.warning(
        "Chunk %s..%s (%s %s) failed after %d attempt(s): %r",
        start_dt, end_dt, broker, interval, attempts, last_err,
    )
    raise ChunkDownloadError(start_dt, end_dt, attempts=attempts, last_error=last_err)


async def download_long_range_async(
    broker,
    row,
    interval,
    start_date,
    end_date,
    auth_dict,
    progress_cb: ProgressCB = None,
    apply_corp_actions: bool = False,
) -> DownloadResult:
    """Download a full date range in chunks, concurrently, with RAM-safe spooling.

    Returns a :class:`DownloadResult`: the retrieved frame PLUS the list of any
    chunks that failed all retries. Failed chunks are logged and surfaced — never
    silently swallowed.
    """
    delta = timedelta(days=30) if interval == "1 Minute" else timedelta(days=365)
    tasks, chunks = [], []
    curr = start_date
    while curr <= end_date:
        nxt = min(curr + delta, end_date)
        chunks.append((curr, nxt))
        curr = nxt + timedelta(days=1)

    dfs = []
    temp_files = []  # PyArrow RAM protection
    failed_ranges: List[DateRange] = []
    sem = asyncio.Semaphore(5)
    total = len(chunks)
    done = 0

    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for c_start, c_end in chunks:
            tasks.append(
                fetch_chunk_async(session, sem, broker, row, interval, c_start, c_end, auth_dict)
            )

        for coro in asyncio.as_completed(tasks):
            try:
                res = await coro
            except ChunkDownloadError as exc:
                failed_ranges.append((exc.start, exc.end))
                res = None
            done += 1
            if res is not None and not res.empty:
                dfs.append(res)
                # Dump to Parquet if RAM is critical
                if psutil.virtual_memory().percent > 85 and len(dfs) > 1:
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".parquet")
                    pd.concat(dfs, ignore_index=True).to_parquet(tf.name, engine="pyarrow")
                    temp_files.append(tf.name)
                    dfs = []

            if progress_cb:
                pct = min(1.0, done / total)
                suffix = f" ({len(failed_ranges)} chunk(s) FAILED)" if failed_ranges else ""
                progress_cb(pct, f"Downloading Data: {int(pct * 100)}% Complete{suffix}")

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
        if failed_ranges:
            log.warning(
                "Download for %s (%s) returned NO data; %d/%d chunk(s) failed.",
                row.get("instrument_key", "?") if hasattr(row, "get") else "?",
                interval, len(failed_ranges), total,
            )
        return DownloadResult(pd.DataFrame(), failed_ranges, start_date, end_date)

    final_df.drop_duplicates(subset=["Timestamp"], keep="first", inplace=True)
    final_df.sort_values("Timestamp", inplace=True)

    if apply_corp_actions and broker == "Upstox":
        async with aiohttp.ClientSession(timeout=timeout) as ca_session:
            corp_actions = await fetch_corporate_actions_upstox(
                ca_session, row["instrument_key"], auth_dict["access_token"]
            )
            final_df = apply_corporate_actions(final_df, corp_actions)

    if failed_ranges:
        log.warning(
            "PARTIAL download for %s (%s): %d/%d chunk(s) failed — data has gaps: %s",
            row.get("instrument_key", "?") if hasattr(row, "get") else "?",
            interval, len(failed_ranges), total,
            ", ".join(f"{s}..{e}" for s, e in failed_ranges),
        )

    return DownloadResult(final_df, failed_ranges, start_date, end_date)


def run_async_download(*args, **kwargs) -> DownloadResult:
    """Sync wrapper: run ``download_long_range_async`` on a fresh event loop.

    Returns a :class:`DownloadResult` (``.df`` + ``.failed_ranges``). Callers that
    only need the frame can use ``result.df``; they MUST check ``result.complete``
    before treating the data as a full download.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(download_long_range_async(*args, **kwargs))
    finally:
        loop.close()
