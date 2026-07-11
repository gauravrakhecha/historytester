"""
quantdesk.data.quality — lightweight, non-blocking data sanity report (ENHANCEMENT).

No equivalent existed in the monolith. This is a small, fast, vectorised check the UI can
surface before a backtest so obviously-broken data is flagged. It never blocks a run.

Checks: duplicate/total timestamps, bad OHLC (high<low, negative prices, close outside
[low, high]), zero-volume bars, AND — added to close the "silent chunk loss" blind spot —
MISSING TRADING DAYS against the NSE calendar (weekends + holidays excluded). A contiguous
hole from a failed download chunk therefore lowers ``quality_score`` and raises an issue,
instead of passing as clean because every bar that *is* present is individually valid.
"""
from __future__ import annotations

import csv
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

from quantdesk.core.logging import get_logger

log = get_logger("data.quality")

# A run of >= this many CONSECUTIVE missing NSE trading days is treated as a real hole
# (e.g. a failed download chunk), not data-provider noise: once weekends and holidays are
# removed, legitimate NSE closures never span this many *trading* days in a row.
_HOLE_RUN_THRESHOLD = 3


def _holidays_path() -> Path:
    """Bundled NSE trading-holiday list (a single ``date`` column)."""
    return Path(__file__).resolve().parent / "nse_holidays.csv"


@lru_cache(maxsize=2)
def load_nse_holidays(path: str | None = None) -> frozenset:
    """Load NSE trading holidays as a set of ``date``.

    A missing/unreadable/malformed file degrades safely to an empty set: holiday
    awareness then simply doesn't subtract any days, which can only INCREASE the
    missing-day count, never silently hide a real gap.
    """
    p = Path(path) if path else _holidays_path()
    if not p.exists():
        log.warning("NSE holiday file not found: %s — holidays not excluded.", p)
        return frozenset()
    out: set[date] = set()
    try:
        with p.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                raw = (row.get("date") or "").strip()
                if not raw:
                    continue
                try:
                    out.add(pd.Timestamp(raw).date())
                except (ValueError, TypeError):
                    log.warning("Skipping unparseable holiday date %r in %s", raw, p)
    except OSError:  # pragma: no cover - unreadable file degrades safely
        return frozenset()
    return frozenset(out)


def _expected_trading_days(days_present: set, holidays: frozenset) -> int:
    """Count of expected NSE trading days (Mon-Fri minus holidays) spanning the data."""
    if len(days_present) < 2:
        return len(days_present)
    first, last = min(days_present), max(days_present)
    return sum(
        1 for d in pd.bdate_range(start=first, end=last) if d.date() not in holidays
    )


def _missing_trading_days(days_present: set, holidays: frozenset) -> tuple[int, int, int]:
    """Return ``(gap_runs, total_missing, max_run)`` of expected trading days absent.

    Expected trading days are business days (Mon-Fri) between the first and last day
    that HAS bars, with NSE holidays removed — so a holiday-gapped series is not
    penalised. Consecutive missing weekdays collapse into a single "run"; ``max_run``
    is the longest such run (the strongest signal of a lost download chunk).
    """
    if len(days_present) < 2:
        return 0, 0, 0
    first, last = min(days_present), max(days_present)
    expected = pd.bdate_range(start=first, end=last)
    missing = sorted(
        d.date() for d in expected
        if d.date() not in days_present and d.date() not in holidays
    )
    if not missing:
        return 0, 0, 0
    gap_runs, cur_run, max_run = 1, 1, 1
    for prev, cur in zip(missing[:-1], missing[1:]):
        # next expected trading weekday after `prev` (skip weekends + holidays)
        nxt = prev + pd.Timedelta(days=1)
        while nxt.weekday() >= 5 or nxt in holidays:
            nxt += pd.Timedelta(days=1)
        if cur == nxt:
            cur_run += 1
        else:
            gap_runs += 1
            cur_run = 1
        max_run = max(max_run, cur_run)
    return gap_runs, len(missing), max_run


def validate(df: pd.DataFrame, interval: str | None = None) -> dict:
    """Return a quality report dict with a 0-100 ``quality_score``.

    Checks duplicate/total timestamps, bad OHLC, zero-volume bars, and missing NSE
    trading days (calendar-aware). ``interval`` is accepted for API symmetry but the
    missing-day check is timestamp-driven and does not require it. Penalties are
    proportional to the fraction of bad rows / missing days.
    """
    report = {
        "rows": int(len(df)),
        "duplicate_timestamps": 0,
        "bad_ohlc_count": 0,
        "zero_volume_count": 0,
        "missing_trading_days": 0,
        "gap_runs": 0,
        "max_gap_run": 0,
        "quality_score": 100.0,
        "issues": [],
    }
    if df is None or df.empty:
        report["quality_score"] = 0.0
        report["issues"].append("empty dataset")
        return report

    n = len(df)
    if "Timestamp" in df.columns:
        report["duplicate_timestamps"] = int(df["Timestamp"].duplicated().sum())

    has_ohlc = all(c in df.columns for c in ("Open", "High", "Low", "Close"))
    if has_ohlc:
        o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
        bad = (h < l) | (o < 0) | (h < 0) | (l < 0) | (c < 0) | (c > h) | (c < l)
        report["bad_ohlc_count"] = int(bad.sum())

    if "Volume" in df.columns:
        report["zero_volume_count"] = int((df["Volume"] <= 0).sum())

    # --- missing trading days (NSE calendar) — the lost-chunk detector ----------
    gap_penalty = 0.0
    if "Timestamp" in df.columns:
        ts = pd.to_datetime(df["Timestamp"], errors="coerce").dropna()
        days_present = set(ts.dt.date)
        holidays = load_nse_holidays()
        gap_runs, missing_days, max_run = _missing_trading_days(days_present, holidays)
        report["missing_trading_days"] = missing_days
        report["gap_runs"] = gap_runs
        report["max_gap_run"] = max_run
        if missing_days:
            expected_days = _expected_trading_days(days_present, holidays) or 1
            frac = missing_days / expected_days
            if max_run >= _HOLE_RUN_THRESHOLD:
                # a contiguous multi-day hole = strong incomplete-download signal
                gap_penalty = min(60.0, 15.0 + frac * 200.0)
            else:
                # scattered single missing days = milder (provider noise)
                gap_penalty = min(20.0, frac * 100.0)

    penalty = 0.0
    if report["duplicate_timestamps"]:
        penalty += 100.0 * report["duplicate_timestamps"] / n
        report["issues"].append(f"{report['duplicate_timestamps']} duplicate timestamps")
    if report["bad_ohlc_count"]:
        penalty += 100.0 * report["bad_ohlc_count"] / n
        report["issues"].append(f"{report['bad_ohlc_count']} bad OHLC bars")
    if report["zero_volume_count"]:
        # zero volume is common/benign on some instruments -> light penalty
        penalty += 20.0 * report["zero_volume_count"] / n
    if gap_penalty:
        penalty += gap_penalty
        span = (
            f"{report['max_gap_run']} consecutive"
            if report["max_gap_run"] >= _HOLE_RUN_THRESHOLD
            else "scattered"
        )
        report["issues"].append(
            f"{report['missing_trading_days']} missing trading day(s) "
            f"({span}, {report['gap_runs']} gap run(s)) — possible incomplete download"
        )

    report["quality_score"] = round(max(0.0, 100.0 - penalty), 1)
    return report
