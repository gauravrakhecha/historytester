"""
quantdesk.data.loader — read cached OHLCV files (CSV / Parquet).

The original read data inline in several pages, e.g.::

    df = pd.read_parquet(path) if path.endswith('.parquet') else pd.read_csv(path)
    df['Timestamp'] = pd.to_datetime(df['Timestamp']); df.set_index('Timestamp', inplace=True)

This centralises it and adds a small (path, mtime)-keyed in-process cache so the same file
loaded repeatedly across pages / Optuna trials isn't re-parsed from disk every time. Callers
always receive a fresh copy, so mutation never poisons the cache.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import pandas as pd

from quantdesk.core.logging import get_logger

log = get_logger("data.loader")

_cache: Dict[Tuple[str, float], pd.DataFrame] = {}
_MAX_ENTRIES = 32


def _read_raw(path: str) -> pd.DataFrame:
    p = str(path)
    if p.lower().endswith(".parquet"):
        return pd.read_parquet(p, engine="pyarrow")
    return pd.read_csv(p)


def normalize_timestamps(ts: pd.Series) -> pd.Series:
    """Normalise a Timestamp column to ONE convention: tz-naive IST wall-clock.

    External CSVs are inconsistent — some carry an explicit ``+05:30`` offset
    (tz-aware), others are naive but already IST. Mixing the two breaks index
    comparisons and backtesting.py's datetime index. We standardise: a tz-aware
    series is converted to Asia/Kolkata and the tz dropped (keeping the IST
    wall-clock); a naive series is left as-is (already IST). Every loaded frame
    therefore has an identical, comparable tz-naive IST index.
    """
    ts = pd.to_datetime(ts, errors="coerce")
    try:
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    except (AttributeError, TypeError):  # all-NaT / non-datetimelike — leave as-is
        pass
    return ts


def _cached_frame(path: str) -> pd.DataFrame:
    """Return the parsed frame for ``path`` from cache (keyed on file mtime)."""
    p = Path(path)
    key = (str(p), p.stat().st_mtime if p.exists() else 0.0)
    if key in _cache:
        return _cache[key]
    df = _read_raw(str(p))
    if "Timestamp" in df.columns:
        df["Timestamp"] = normalize_timestamps(df["Timestamp"])
    if len(_cache) >= _MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = df
    return df


def load_ohlcv(path: str) -> pd.DataFrame:
    """Load a file and return an OHLCV(+OI) frame with a ``Timestamp`` COLUMN.

    Used by the converter / quality / resample paths that expect a Timestamp column.
    """
    return _cached_frame(path).copy()


def load_indexed(path: str) -> pd.DataFrame:
    """Load a file and return a frame indexed by a DatetimeIndex (Timestamp as index).

    This is what ``backtesting.Backtest`` needs (it requires a datetime index). Mirrors the
    monolith's ``df.set_index('Timestamp')`` step at the backtest call sites.
    """
    df = _cached_frame(path).copy()
    if "Timestamp" in df.columns:
        df = df.set_index("Timestamp")
    return df


def clear_cache() -> None:
    _cache.clear()
