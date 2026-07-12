"""Tests for the download orchestrator's failed-chunk surfacing (silent-chunk-loss fix)."""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _bar(start):
    return pd.DataFrame({
        "Timestamp": [pd.Timestamp(start)],
        "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1],
    })


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    from quantdesk.brokers import downloader as D
    monkeypatch.setattr(D, "_BASE_BACKOFF", 0.0)
    monkeypatch.setattr(D, "_MAX_BACKOFF", 0.0)


def test_partial_failure_is_surfaced_not_swallowed(monkeypatch):
    """A chunk that fails every retry appears in ``failed_ranges`` and data still flows."""
    from quantdesk.brokers import downloader as D
    from quantdesk.brokers.base import RetryableError

    async def flaky(session, row, interval, start, end, auth):
        if start.month == 3:  # fail the March chunk(s) only
            raise RetryableError("boom", status=500)
        return _bar(start)

    monkeypatch.setitem(D._FETCHERS, "FAKE", flaky)
    res = D.run_async_download(
        "FAKE", {"instrument_key": "X"}, "1 Minute",
        dt.date(2024, 1, 1), dt.date(2024, 6, 1), {"access_token": "t"},
    )
    assert not res.complete
    assert len(res.failed_ranges) >= 1
    assert any(s.month == 3 for s, _ in res.failed_ranges)
    assert not res.df.empty  # surviving chunks still returned data


def test_total_failure_returns_empty_with_ranges(monkeypatch):
    """When every chunk fails, the frame is empty AND every range is reported."""
    from quantdesk.brokers import downloader as D
    from quantdesk.brokers.base import RetryableError

    async def always_fail(session, row, interval, start, end, auth):
        raise RetryableError("down", status=503)

    monkeypatch.setitem(D._FETCHERS, "FAKE", always_fail)
    res = D.run_async_download(
        "FAKE", {"instrument_key": "X"}, "1 Minute",
        dt.date(2024, 1, 1), dt.date(2024, 3, 1), {"access_token": "t"},
    )
    assert res.df.empty
    assert not res.complete
    assert len(res.failed_ranges) >= 1


def test_legit_empty_is_not_a_failure(monkeypatch):
    """A genuinely empty chunk (no data) is NOT reported as a failure."""
    from quantdesk.brokers import downloader as D

    async def empty(session, row, interval, start, end, auth):
        return pd.DataFrame()

    monkeypatch.setitem(D._FETCHERS, "FAKE", empty)
    res = D.run_async_download(
        "FAKE", {"instrument_key": "X"}, "1 Minute",
        dt.date(2024, 1, 1), dt.date(2024, 3, 1), {"access_token": "t"},
    )
    assert res.df.empty
    assert res.complete  # empty != failed
    assert res.failed_ranges == []


def test_retry_after_is_honoured(monkeypatch):
    """A 429 with Retry-After drives the back-off, then the chunk succeeds on retry."""
    from quantdesk.brokers import downloader as D
    from quantdesk.brokers.base import RetryableError

    seen = {"retry_after": None}
    attempts = {"n": 0}

    async def rate_limited(session, row, interval, start, end, auth):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RetryableError("429", status=429, retry_after=0.0)
        return _bar(start)

    real_backoff = D._backoff_seconds

    def spy(attempt, retry_after=None):
        seen["retry_after"] = retry_after
        return real_backoff(attempt, retry_after)

    monkeypatch.setattr(D, "_backoff_seconds", spy)
    monkeypatch.setitem(D._FETCHERS, "FAKE", rate_limited)
    res = D.run_async_download(
        "FAKE", {"instrument_key": "X"}, "Day",
        dt.date(2024, 1, 1), dt.date(2024, 1, 15), {"access_token": "t"},
    )
    assert res.complete
    assert not res.df.empty
    assert seen["retry_after"] == 0.0  # the Retry-After value reached the back-off
