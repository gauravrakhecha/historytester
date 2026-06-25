"""
quantdesk.config — single source of truth for environment, paths and constants.

The original ``upstox_app.py`` scattered these across the module top-level:
- ``DB_FILE`` from ``os.getenv`` (default ``trading_data.db``)
- ``default_parquet_dir`` from the settings table (default ``C:/TradingData``)
- ``NIFTY_50_LIST`` / ``BANK_NIFTY_LIST`` index presets

Here they are centralised and made path-safe (``pathlib``). The modular app is
self-contained inside the ``modular history`` folder and uses its OWN database so it
never clobbers the original ``upstox_data.db``.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --- ROOTS ------------------------------------------------------------------
# .../modular history/quantdesk/config.py  ->  ROOT = .../modular history
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
# .../historydownloader (parent of "modular history") — where the original .env lives
LEGACY_DIR: Path = ROOT_DIR.parent

# Load env: legacy .env first (holds the broker API keys), then a local override.
load_dotenv(LEGACY_DIR / ".env")
load_dotenv(ROOT_DIR / ".env", override=True)

# --- DATABASE ---------------------------------------------------------------
# Own DB inside the modular folder by default (never touches upstox_data.db),
# but still overridable via the MODULAR_DB_FILE env var.
DB_FILE: str = os.getenv("MODULAR_DB_FILE", str(ROOT_DIR / "quantdesk_data.db"))

# --- DEFAULT DATA / OUTPUT PATHS -------------------------------------------
# The user's existing downloaded data (used for verification & as a sensible default).
DEFAULT_DATA_DIR: str = os.getenv(
    "QUANTDESK_DATA_DIR", r"D:\simpleton\historydownloader\data"
)
STRATEGIES_DIR: Path = ROOT_DIR / "strategies"
INDICATOR_PRESETS_DIR: Path = ROOT_DIR / "indicator_presets"
PLOTS_DIR: Path = ROOT_DIR / "plots"
WORKFLOW_DIR: Path = ROOT_DIR / ".workflow"

for _p in (STRATEGIES_DIR, INDICATOR_PRESETS_DIR, PLOTS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# --- BACKTEST DEFAULTS (mirrors original Settings page defaults) ------------
DEFAULTS = {
    "brokerage_commission": "0.0003",
    "slippage": "0.0002",
    "brokerage_margin": "1.0",
    "initial_capital": "100000",
    "plot_dir": str(PLOTS_DIR),
    "parquet_data_dir": DEFAULT_DATA_DIR,
}

# --- BROKER API (read from .env; never hard-coded) -------------------------
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8501")

# --- INDEX PRESETS (verbatim from upstox_app.py) ---------------------------
NIFTY_50_LIST = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL",
    "BHARTIARTL", "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
    "ITC", "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK",
    "LT", "LTIM", "M&M", "MARUTI", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS",
    "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO",
]

BANK_NIFTY_LIST = [
    "AUBANK", "AXISBANK", "BANDHANBNK", "BANKBARODA", "FEDERALBNK",
    "HDFCBANK", "ICICIBANK", "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK",
    "PNB", "SBIN",
]

# Indicator output-column suffix map — the SINGLE definition of which columns each
# multi-output indicator exposes. The original app re-derived this ad-hoc in ~5 places
# and missed Supertrend/Stochastic/Keltner/Donchian on the Plotter & ML pages (a bug).
# Every page now sources selectable columns from here. See strategy/indicators.py.
INDICATOR_OUTPUT_SUFFIXES = {
    "MACD": ["_line", "_signal"],
    "Bollinger Bands": ["_high", "_low"],
    "Supertrend": ["_trend", "_dir"],
    "Stochastic Oscillator": ["_k", "_d"],
    "Keltner Channels": ["_upper", "_mid", "_lower"],
    "Donchian Channels": ["_upper", "_mid", "_lower"],
}
