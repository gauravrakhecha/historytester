"""
quantdesk.data.saver — write downloaded OHLCV frames in the user's chosen format(s).

The downloaders historically wrote only Parquet. This centralises saving so a single
``save_format`` setting (Parquet only / CSV only / Both) and an optional separate
``csv_data_dir`` apply everywhere (Master / Single / Bulk / Options downloaders).

Behaviour:
- Parquet is written to the given ``parquet_path`` (the existing layout).
- CSV is written either ALONGSIDE the parquet (same folder, ``.csv``) when no separate CSV
  directory is configured, or MIRRORED under ``csv_data_dir`` preserving the relative structure
  beneath ``base_dir`` when one is set.
- Returns the list of written paths; ``primary_path`` (first, preferring parquet) is what the
  download log should record so the rest of the app keeps finding the file.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Optional

import pandas as pd

from quantdesk.core import settings_repo

SAVE_FORMATS = ["Parquet only", "CSV only", "Both"]


def _effective_csv_dir(csv_dir: Optional[str]) -> str:
    """Resolve the CSV directory: an explicit per-call value (``''`` => alongside) wins; otherwise
    fall back to the Settings value. ``None`` means 'not specified — use the setting'."""
    return csv_dir if csv_dir is not None else settings_repo.get("csv_data_dir", "")


def _csv_target(parquet_path: Path, base_dir: Optional[str], csv_dir: Optional[str]) -> Path:
    """Resolve where the CSV for ``parquet_path`` should go (separate dir mirror, or alongside)."""
    eff = _effective_csv_dir(csv_dir)
    if not eff:
        return parquet_path.with_suffix(".csv")  # alongside the parquet
    # mirror the relative structure under the separate CSV directory
    try:
        rel = parquet_path.relative_to(base_dir) if base_dir else Path(parquet_path.name)
    except Exception:
        rel = Path(*parquet_path.parts[-2:])  # fall back to keeping last subfolder + filename
    return (Path(eff) / rel).with_suffix(".csv")


def _atomic_write(target: Path, writer) -> None:
    """Write ``target`` via a same-directory temp file and ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.stem}.",
        suffix=f"{target.suffix}.tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        writer(str(tmp_path))
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def save_frame(df: pd.DataFrame, parquet_path, base_dir: Optional[str] = None,
               csv_dir: Optional[str] = None) -> List[str]:
    """Save ``df`` per the configured format(s). Returns the written file paths (parquet first).

    ``csv_dir`` overrides the Settings CSV directory for this call: a path mirrors the structure
    there, an empty string ``''`` forces 'alongside the Parquet', and ``None`` uses the setting.
    """
    if df is None or df.empty:
        return []

    fmt = settings_repo.get("save_format", "Both")
    pq = Path(parquet_path)
    written: List[str] = []

    if fmt in ("Parquet only", "Both"):
        _atomic_write(pq, lambda p: df.to_parquet(p, engine="pyarrow"))
        written.append(str(pq))

    if fmt in ("CSV only", "Both"):
        csv_path = _csv_target(pq, base_dir, csv_dir)
        _atomic_write(csv_path, lambda p: df.to_csv(p, index=False))
        written.append(str(csv_path))

    return written


def primary_path(written: List[str], fallback: str) -> str:
    """The path to record in the download log (first written; parquet preferred)."""
    return written[0] if written else fallback


def format_summary(csv_dir: Optional[str] = None) -> str:
    """One-line, plain-language description of what/where downloads will be saved."""
    fmt = settings_repo.get("save_format", "Both")
    eff_csv = _effective_csv_dir(csv_dir)
    csv_where = f"`{eff_csv}`" if eff_csv else "alongside the Parquet files"
    if fmt == "Parquet only":
        return "💾 Save format: **Parquet only** (→ your Parquet Data Directory)."
    if fmt == "CSV only":
        return f"💾 Save format: **CSV only** (→ {csv_where})."
    return f"💾 Save format: **Both** — Parquet → Parquet Data Directory, CSV → {csv_where}. (Change in ⚙️ Settings.)"
