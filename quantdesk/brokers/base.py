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
    """Raised by a broker fetch when the orchestrator should retry the chunk.

    Optionally carries the HTTP ``status``, a parsed ``retry_after`` (seconds)
    so the orchestrator can honour an explicit rate-limit back-off (HTTP 429),
    and ``retryable`` (default ``True``) to allow fast-failing when ``False``.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        retry_after: float | None = None,
        retryable: bool = True,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.retryable = retryable


class NonRetryableError(Exception):
    """Raised by a broker fetch when retrying cannot succeed (e.g. HTTP 400/401/403/404)."""

    def __init__(self, message: str = "", *, status: int | None = None):
        super().__init__(message)
        self.status = status
        self.retryable = False


_NON_RETRYABLE_STATUSES = frozenset({400, 401, 403, 404})


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form) into seconds.

    Only the numeric delta-seconds form is honoured; the rarely-used HTTP-date
    form degrades to ``None`` (the orchestrator then falls back to its own
    exponential back-off), which is safe.
    """
    if not value:
        return None
    try:
        secs = float(value.strip())
        return secs if secs >= 0 else None
    except (ValueError, AttributeError):
        return None


def raise_for_broker_status(broker: str, status: int, headers=None) -> None:
    """Raise ``NonRetryableError`` on 400/401/403/404 or ``RetryableError`` otherwise."""
    if status in _NON_RETRYABLE_STATUSES:
        raise NonRetryableError(f"{broker} HTTP {status}", status=status)
    retry_after = parse_retry_after(headers.get("Retry-After")) if headers is not None else None
    raise RetryableError(
        f"{broker} HTTP {status}",
        status=status,
        retry_after=retry_after,
    )


class ChunkDownloadError(Exception):
    """Raised by the download orchestrator when a single date-chunk fails all retries.

    Carries the exact ``[start, end]`` range so a PARTIAL download can be reported
    to the caller instead of silently returning a frame with a hole in it. This is
    the fix for the "silent chunk loss" bug: a failed chunk used to be swallowed as
    an empty frame indistinguishable from a legitimately empty range.
    """

    def __init__(self, start, end, *, attempts: int, last_error: BaseException | None = None):
        self.start = start
        self.end = end
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"chunk {start:%Y-%m-%d}..{end:%Y-%m-%d} failed after "
            f"{attempts} attempt(s): {last_error!r}"
        )


# Columns the API candle arrays map to (Upstox/Zerodha order). OI may be absent.
CANDLE_COLUMNS = ["Timestamp", "Open", "High", "Low", "Close", "Volume", "OI"]
