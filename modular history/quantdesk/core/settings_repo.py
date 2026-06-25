"""
quantdesk.core.settings_repo — centralised access to the ``settings`` table.

The original app re-read settings with a fresh ``sqlite3.connect`` in the Settings page,
the Backtester, the Batch runner and the ML finder. This module is the single accessor,
with an in-process cache that is invalidated on save. Defaults come from ``config.DEFAULTS``.
"""
from __future__ import annotations

from typing import Dict

from quantdesk.config import DEFAULTS
from quantdesk.core.db import connect, init_db

_cache: Dict[str, str] | None = None


def get_all(force: bool = False) -> Dict[str, str]:
    """Return all settings (DB values overlaid on defaults). Cached after first read."""
    global _cache
    if _cache is not None and not force:
        return dict(_cache)
    init_db()
    merged = dict(DEFAULTS)
    try:
        with connect() as conn:
            cur = conn.execute("SELECT key, value FROM settings")
            merged.update(dict(cur.fetchall()))
    except Exception:
        pass
    _cache = merged
    return dict(merged)


def get(key: str, default: str | None = None) -> str:
    vals = get_all()
    if key in vals:
        return vals[key]
    return default if default is not None else DEFAULTS.get(key, "")


def get_float(key: str) -> float:
    return float(get(key))


def save(updates: Dict[str, str]) -> None:
    """Upsert a batch of settings and invalidate the cache."""
    global _cache
    init_db()
    with connect() as conn:
        for k, v in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v))
            )
    _cache = None


def backtest_params() -> dict:
    """Convenience bundle used by every backtest entry point (engine/batch/ml).

    Returns commission already combined with slippage, matching the original which did
    ``total_comm = comm + slippage`` before handing it to ``Backtest(commission=...)``.
    """
    s = get_all()
    comm = float(s.get("brokerage_commission", DEFAULTS["brokerage_commission"]))
    slippage = float(s.get("slippage", DEFAULTS["slippage"]))
    return {
        "commission": comm,
        "slippage": slippage,
        "total_commission": comm + slippage,
        "margin": float(s.get("brokerage_margin", DEFAULTS["brokerage_margin"])),
        "initial_capital": float(s.get("initial_capital", DEFAULTS["initial_capital"])),
    }
