"""
quantdesk.data.catalog — Data Coverage Catalog, incremental top-up, and ZIP export.

Summarises what OHLCV datasets exist in ``downloads_log`` and on disk, re-fetches
only the trailing incremental window (with a configurable lookback guard so
provisional intraday/same-day bars fetched before NSE close are overwritten
cleanly), and bundles selected files into an in-memory ZIP archive.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from quantdesk.brokers.downloader import run_async_download
from quantdesk.config import DEFAULT_DATA_DIR
from quantdesk.core import db
from quantdesk.core.logging import get_logger
from quantdesk.data.loader import load_ohlcv, normalize_timestamps
from quantdesk.data.quality import validate
from quantdesk.data.resample import resample_dataframe
from quantdesk.data.saver import primary_path, save_frame

log = get_logger("data.catalog")

_BASE_CATALOG_COLS = [
    "broker",
    "symbol",
    "instrument_key",
    "interval",
    "logged_start",
    "logged_end",
    "actual_start",
    "actual_end",
    "bars",
    "file_exists",
    "size_kb",
    "file_path",
    "downloaded_at",
]

_INTERVAL_RE = re.compile(r"^(\d+)\s*(?:minute|minutes|min|m)$", re.IGNORECASE)


def _coerce_date(value: Any) -> date | None:
    """Parse a date / datetime / Timestamp / ISO string into ``datetime.date``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date()
    text = str(value).strip()
    if not text or text.lower() in ("none", "nan", "nat"):
        return None
    try:
        ts = pd.to_datetime(text, errors="coerce")
        return None if pd.isna(ts) else ts.date()
    except Exception:  # noqa: BLE001
        return None


def _parse_interval(interval_raw: str) -> tuple[str, int | None, str]:
    """Map stored interval labels (e.g. ``1Minute``, ``15Minute``, ``Day``) to
    ``(download_interval, resample_minutes, save_label)``.
    """
    raw = str(interval_raw or "Day").strip()
    low = raw.lower()
    if low in ("day", "1d", "d", "daily"):
        return "Day", None, "Day"
    if low in ("1 minute", "1minute", "1m", "1min"):
        return "1 Minute", None, "1Minute"
    m = _INTERVAL_RE.match(raw)
    if m:
        minutes = int(m.group(1))
        if minutes <= 1:
            return "1 Minute", None, "1Minute"
        return "1 Minute", minutes, f"{minutes}Minute"
    return raw, None, raw.replace(" ", "")


def build_catalog_df(include_quality: bool = False) -> pd.DataFrame:
    """Build a deduplicated coverage catalog across ``downloads_log`` and disk.

    Deduplicates to the latest row per ``(broker, symbol, interval)``, inspects
    file existence and size in KB, reads actual bar count and date bounds via
    :func:`quantdesk.data.loader.load_ohlcv`, and optionally computes
    ``quality_score`` and ``missing_days`` via :func:`quantdesk.data.quality.validate`.
    """
    cols = list(_BASE_CATALOG_COLS)
    if include_quality:
        cols.extend(["quality_score", "missing_days"])

    hist = db.get_download_history()
    if hist is None or hist.empty:
        return pd.DataFrame(columns=cols)

    work = hist.copy()
    for key_col in ("broker", "symbol", "interval"):
        if key_col not in work.columns:
            work[key_col] = ""
        work[key_col] = work[key_col].fillna("").astype(str)

    sort_cols = [c for c in ("downloaded_at", "id") if c in work.columns]
    if sort_cols:
        work = work.sort_values(sort_cols, ascending=False, na_position="last")

    dedup = work.drop_duplicates(subset=["broker", "symbol", "interval"], keep="first")

    rows: list[dict[str, Any]] = []
    for _, r in dedup.iterrows():
        file_path = str(r.get("file_path") or "").strip()
        p = Path(file_path) if file_path else None
        exists = bool(p is not None and p.is_file())
        size_kb = round(p.stat().st_size / 1024.0, 2) if exists else 0.0

        bars = 0
        actual_start: date | None = None
        actual_end: date | None = None
        quality_score: float | None = None
        missing_days: int | None = None

        if exists:
            try:
                df = load_ohlcv(file_path)
                bars = int(len(df))
                if not df.empty and "Timestamp" in df.columns:
                    ts = pd.to_datetime(df["Timestamp"], errors="coerce").dropna()
                    if not ts.empty:
                        actual_start = ts.min().date()
                        actual_end = ts.max().date()
                if include_quality:
                    q = validate(df, interval=r.get("interval"))
                    quality_score = q.get("quality_score")
                    missing_days = q.get("missing_trading_days")
            except Exception as exc:  # noqa: BLE001
                log.warning("Catalog failed reading %s: %s", file_path, exc)
                if include_quality:
                    quality_score = 0.0
                    missing_days = 0

        entry: dict[str, Any] = {
            "broker": r.get("broker", ""),
            "symbol": r.get("symbol", ""),
            "instrument_key": str(r.get("instrument_key") or ""),
            "interval": r.get("interval", ""),
            "logged_start": r.get("start_date"),
            "logged_end": r.get("end_date"),
            "actual_start": actual_start,
            "actual_end": actual_end,
            "bars": bars,
            "file_exists": exists,
            "size_kb": size_kb,
            "file_path": file_path,
            "downloaded_at": r.get("downloaded_at"),
        }
        if include_quality:
            entry["quality_score"] = quality_score
            entry["missing_days"] = missing_days
        rows.append(entry)

    out = pd.DataFrame(rows, columns=cols)
    if not out.empty:
        out = out.sort_values(["symbol", "interval", "broker"]).reset_index(drop=True)
    return out


def compute_incremental_range(
    actual_end_date,
    target_end_date,
    lookback_guard_days: int = 2,
) -> tuple[date, date]:
    """Return ``(start_date, target_end_date)`` rewinding by ``lookback_guard_days``.

    Rewinds ``max_guard_start = min(target_end_date, actual_end_date - timedelta(days=lookback_guard_days))``
    so incomplete same-day provisional bars fetched before NSE close (15:35 IST)
    are cleanly re-fetched and overwritten on top-up.
    """
    target_end = _coerce_date(target_end_date) or date.today()
    actual_end = _coerce_date(actual_end_date)
    guard = max(0, int(lookback_guard_days))

    if actual_end is None:
        return target_end - timedelta(days=guard), target_end

    max_guard_start = min(target_end, actual_end - timedelta(days=guard))
    return max_guard_start, target_end


def top_up_entry(
    catalog_row: dict,
    target_end_date,
    auth_dict: dict,
    row_meta: dict | None = None,
    parquet_dir: str | None = None,
    csv_dir: str | None = None,
    apply_corp_actions: bool = False,
    progress_cb=None,
) -> dict:
    """Incrementally top up one catalog entry through ``target_end_date``.

    Downloads ``[inc_start .. target_end_date]`` via ``run_async_download``, merges
    with any existing file deduplicating on ``Timestamp`` with ``keep="last"``
    (new bars overwrite provisional bars), sorts ascending, saves atomically via
    ``save_frame``, and logs the updated range via ``db.log_download``.
    """
    cat = dict(catalog_row) if catalog_row is not None else {}
    auth = dict(auth_dict or {})

    symbol = str(cat.get("symbol") or "").strip()
    interval_raw = str(cat.get("interval") or "Day").strip()
    broker = str(auth.get("broker") or cat.get("broker") or "Upstox").strip()
    instrument_key = str(cat.get("instrument_key") or "").strip()
    existing_path = str(cat.get("file_path") or "").strip()

    download_interval, resample_minutes, save_label = _parse_interval(interval_raw)

    existing_df = pd.DataFrame()
    if existing_path and Path(existing_path).is_file():
        try:
            existing_df = load_ohlcv(existing_path)
            if not existing_df.empty and "Timestamp" in existing_df.columns:
                existing_df["Timestamp"] = normalize_timestamps(existing_df["Timestamp"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not read existing file %s during top-up: %s", existing_path, exc)
            existing_df = pd.DataFrame()

    actual_end = (
        _coerce_date(cat.get("actual_end"))
        or _coerce_date(cat.get("logged_end"))
        or _coerce_date(cat.get("end_date"))
    )
    if actual_end is None and not existing_df.empty and "Timestamp" in existing_df.columns:
        ts_valid = existing_df["Timestamp"].dropna()
        if not ts_valid.empty:
            actual_end = ts_valid.max().date()

    target_end = _coerce_date(target_end_date) or date.today()
    inc_start, inc_end = compute_incremental_range(actual_end, target_end)

    row = dict(row_meta) if row_meta else {}
    row.setdefault("tradingsymbol", symbol)
    row.setdefault("symbol", symbol)
    if instrument_key:
        row.setdefault("instrument_key", instrument_key)
    else:
        row.setdefault("instrument_key", symbol)

    result = run_async_download(
        broker,
        row,
        download_interval,
        inc_start,
        inc_end,
        auth,
        progress_cb=progress_cb,
        apply_corp_actions=apply_corp_actions,
    )

    new_df = result.df.copy() if result.df is not None else pd.DataFrame()
    if resample_minutes is not None and not new_df.empty:
        new_df = resample_dataframe(new_df, resample_minutes)
    if not new_df.empty and "Timestamp" in new_df.columns:
        new_df["Timestamp"] = normalize_timestamps(new_df["Timestamp"])

    prev_rows = int(len(existing_df))

    if new_df.empty:
        return {
            "symbol": symbol,
            "interval": save_label,
            "added_rows": 0,
            "total_rows": prev_rows,
            "file_path": existing_path,
            "complete": bool(result.complete),
            "failed_ranges": list(result.failed_ranges),
        }

    if not existing_df.empty:
        merged = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        merged = new_df.copy()

    if "Timestamp" in merged.columns:
        merged["Timestamp"] = normalize_timestamps(merged["Timestamp"])
        merged = merged.dropna(subset=["Timestamp"])
        merged = merged.drop_duplicates(subset=["Timestamp"], keep="last")
        merged = merged.sort_values("Timestamp").reset_index(drop=True)

    total_rows = int(len(merged))
    added_rows = max(0, total_rows - prev_rows)

    actual_from = merged["Timestamp"].min().date()
    actual_to = merged["Timestamp"].max().date()
    safe_sym = symbol.replace(" ", "_") or "instrument"

    if existing_path and Path(existing_path).parent.exists():
        target_pq = Path(existing_path).with_suffix(".parquet")
        base_dir = parquet_dir or str(target_pq.parent)
    else:
        root_dir = Path(parquet_dir or DEFAULT_DATA_DIR)
        target_pq = root_dir / safe_sym / save_label / f"{safe_sym}_{save_label}_{actual_from}_{actual_to}.parquet"
        base_dir = str(root_dir)

    written = save_frame(merged, str(target_pq), base_dir=base_dir, csv_dir=csv_dir)
    saved_path = primary_path(written, str(target_pq))

    db.log_download(
        broker,
        symbol,
        row.get("instrument_key", symbol),
        save_label,
        actual_from,
        actual_to,
        saved_path,
    )

    return {
        "symbol": symbol,
        "interval": save_label,
        "added_rows": added_rows,
        "total_rows": total_rows,
        "file_path": saved_path,
        "complete": bool(result.complete),
        "failed_ranges": list(result.failed_ranges),
    }


def export_catalog_zip(file_paths: list[str]) -> bytes:
    """Bundle existing valid files into an in-memory deflated ZIP archive."""
    buf = io.BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, fp in enumerate(file_paths or []):
            if not fp:
                continue
            p = Path(str(fp))
            if not p.is_file():
                continue
            arcname = p.name
            if arcname in seen_names:
                arcname = f"{p.parent.name}_{p.name}"
            if arcname in seen_names:
                arcname = f"{idx}_{p.name}"
            seen_names.add(arcname)
            zf.write(p, arcname=arcname)
    return buf.getvalue()
