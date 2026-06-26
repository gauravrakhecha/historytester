"""
quantdesk.core.reproducibility — content hashes so a stored backtest can be trusted/re-derived.

Standing rule from the parent project: every run records four hashes — dataset, config,
strategy, code — so "same four hashes ⇒ identical result". Cheap to add now, near-impossible to
retrofit. All hashes are short (12-char) hex digests.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd


def _short(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def hash_text(s: str) -> str:
    return _short(s.encode("utf-8"))


def hash_config(d: dict) -> str:
    return _short(json.dumps(d, sort_keys=True, default=str).encode("utf-8"))


def hash_dataframe(df: pd.DataFrame) -> str:
    """Stable hash of a dataframe's content (order-sensitive)."""
    try:
        return _short(pd.util.hash_pandas_object(df, index=True).values.tobytes())
    except Exception:
        return _short(repr(df.shape).encode("utf-8"))


def hash_file(path: str) -> str:
    """Hash a data file by path + size + mtime (cheap; avoids reading huge files)."""
    p = Path(path)
    try:
        st = p.stat()
        return hash_text(f"{p.name}|{st.st_size}|{int(st.st_mtime)}")
    except Exception:
        return hash_text(str(path))


def code_version() -> str:
    """Git short SHA of the repo if available, else the package version tag."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "modular-v4"
