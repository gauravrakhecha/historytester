"""
quantdesk.brokers.base — broker adapter seam.

The original ``fetch_chunk_async`` branched on broker inline. Here each broker exposes an
``async def fetch_single(session, row, interval, start_dt, end_dt, auth_dict) -> DataFrame``
that performs ONE request attempt and returns a (possibly empty) OHLCV frame, raising
``RetryableError`` on a non-200 / network hiccup. The downloader orchestrator owns the
semaphore + 3-attempt retry, preserving the monolith's exact behaviour.
"""
from __future__ import annotations


class RetryableError(Exception):
    """Raised by a broker fetch when the orchestrator should retry the chunk."""


# Columns the API candle arrays map to (Upstox/Zerodha order). OI may be absent.
CANDLE_COLUMNS = ["Timestamp", "Open", "High", "Low", "Close", "Volume", "OI"]
