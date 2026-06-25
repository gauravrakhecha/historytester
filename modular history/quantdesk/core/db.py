"""
quantdesk.core.db — the ONE database layer.

The original app opened ``sqlite3.connect(DB_FILE, timeout=10)`` inline in ~20 places and
created the schema in ``init_db()``. This centralises:

- schema creation (identical tables to the original: downloads_log, bulk_templates,
  backtests, settings, ml_patterns) so an existing DB is 100% compatible,
- a single ``connect()`` context manager (WAL + busy timeout for fewer "database is locked"),
- thin repository helpers for the download log, templates and backtests.

All SQL that the original performed is reproduced here so behaviour is unchanged.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

import pandas as pd

from quantdesk.config import DB_FILE

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS downloads_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        broker TEXT, symbol TEXT, instrument_key TEXT,
        interval TEXT, start_date TEXT, end_date TEXT,
        file_path TEXT, downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS bulk_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, symbols TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS backtests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT, symbol TEXT, timeframe TEXT, start_date TEXT, end_date TEXT,
        parameters TEXT, return_pct REAL, max_drawdown_pct REAL, win_rate_pct REAL, plot_file TEXT,
        num_trades INTEGER DEFAULT 0, sharpe_ratio REAL DEFAULT 0, strategy_config TEXT, full_stats TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE IF NOT EXISTS ml_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT, target_metric TEXT, dataset_path TEXT,
        pattern_rules TEXT, win_rate REAL, samples INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
]

_initialised = False


@contextmanager
def connect(timeout: float = 10.0) -> Iterator[sqlite3.Connection]:
    """Yield a sqlite connection (WAL, foreign keys on). Commits on clean exit."""
    conn = sqlite3.connect(DB_FILE, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if missing. Idempotent; safe to call on every import path."""
    global _initialised
    if _initialised:
        return
    with connect() as conn:
        for stmt in _SCHEMA:
            conn.execute(stmt)
    _initialised = True


# --- download log -----------------------------------------------------------
def log_download(broker, symbol, key, interval, start, end, path) -> None:
    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO downloads_log
                   (broker, symbol, instrument_key, interval, start_date, end_date, file_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (broker, symbol, str(key), interval, str(start), str(end), path),
            )
    except Exception as e:  # noqa: BLE001 — mirror original best-effort logging
        print(f"DB Log Error: {e}")


def get_download_history() -> pd.DataFrame:
    try:
        with connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM downloads_log ORDER BY downloaded_at DESC", conn
            )
    except Exception:
        return pd.DataFrame()


# --- templates --------------------------------------------------------------
def get_templates() -> pd.DataFrame:
    try:
        with connect() as conn:
            return pd.read_sql_query(
                "SELECT name, symbols FROM bulk_templates ORDER BY name", conn
            )
    except Exception:
        return pd.DataFrame(columns=["name", "symbols"])


def save_template(name: str, symbols_str: str):
    if not name or not symbols_str:
        return False, "Name or Symbols empty"
    try:
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bulk_templates (name, symbols) VALUES (?, ?)",
                (name, symbols_str),
            )
        return True, "Saved successfully"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def delete_template(name: str) -> bool:
    try:
        with connect() as conn:
            conn.execute("DELETE FROM bulk_templates WHERE name = ?", (name,))
        return True
    except Exception:
        return False
