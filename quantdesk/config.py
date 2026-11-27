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
# Prefer QUANTDESK_DATA_DIR when set; otherwise use the legacy shared data folder
# only if it exists on this machine, falling back to ROOT_DIR / "data".
_LEGACY_DATA_DIR: Path = Path(r"D:\simpleton\historydownloader\data")
_env_data_dir = os.getenv("QUANTDESK_DATA_DIR")
if _env_data_dir:
    DEFAULT_DATA_DIR: str = _env_data_dir
elif _LEGACY_DATA_DIR.is_dir():
    DEFAULT_DATA_DIR = str(_LEGACY_DATA_DIR)
else:
    DEFAULT_DATA_DIR = str(ROOT_DIR / "data")
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
    "save_format": "Both",   # Parquet only | CSV only | Both
    "csv_data_dir": "",       # optional separate folder for CSV files (blank = alongside parquet)
}

# --- DATA-QUALITY GATE THRESHOLDS (Standing Rule 0.2a) ---------------------
# Below WARN: annotate the run with a warning. Below BLOCK: refuse to run.
QUALITY_WARN_THRESHOLD: float = float(os.getenv("QUANTDESK_QUALITY_WARN", "80"))
QUALITY_BLOCK_THRESHOLD: float = float(os.getenv("QUANTDESK_QUALITY_BLOCK", "40"))

# --- ML PATTERN-FINDER RIGOR (v7 Phase B) ----------------------------------
# Defaults for the ML Pattern Finder's statistical gate. A mined rule is only
# promoted to "validated" when, on the OUT-OF-SAMPLE (held-out) trades, it has at
# least ML_SAMPLE_FLOOR matches, survives Benjamini-Hochberg FDR at ML_FDR_ALPHA,
# and its Wilson lower-bound win-rate still beats the baseline. Anything weaker is
# surfaced as an "exploratory candidate" only. (Deliberately conservative — small
# samples lie; see docs/archive/ROADMAP_v7.md Phase B.)
ML_SAMPLE_FLOOR: int = int(os.getenv("QUANTDESK_ML_SAMPLE_FLOOR", "30"))
ML_HOLDOUT_FRAC: float = float(os.getenv("QUANTDESK_ML_HOLDOUT_FRAC", "0.3"))
ML_FDR_ALPHA: float = float(os.getenv("QUANTDESK_ML_FDR_ALPHA", "0.10"))

# --- RISK / POSITION SIZING (v7.8 Task 3 pt.3) -----------------------------
# Adaptive Kelly sizing estimates win-rate and payoff from the strategy's OWN
# realized trades as the backtest progresses. It only engages once at least this
# many trades have closed — before that a tiny sample would give a wild, unstable
# fraction, so sizing falls back to the Fixed-Value default during warmup.
KELLY_MIN_TRADES: int = int(os.getenv("QUANTDESK_KELLY_MIN_TRADES", "20"))

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

# Chart placement per indicator type (chart redesign): "overlay" = drawn ON the price axis
# (price-scale lines/bands); "panel" = drawn in its OWN stacked sub-panel (own scale, e.g.
# oscillators 0-100, MACD near 0, ATR in price units). Anything not listed defaults to overlay.
# This is the SINGLE source of truth the chart layout reads — see viz/charts.py.
INDICATOR_PLACEMENT = {
    # overlays (price scale)
    "SMA": "overlay", "EMA": "overlay", "WMA": "overlay", "ALMA": "overlay", "VWAP": "overlay",
    "Bollinger Bands": "overlay", "Supertrend": "overlay", "Keltner Channels": "overlay",
    "Donchian Channels": "overlay", "Parabolic SAR": "overlay",
    # own-scale panels (oscillators / unbounded / non-price)
    "RSI": "panel", "ADX": "panel", "Stochastic Oscillator": "panel", "MACD": "panel",
    "ATR": "panel", "ATR-Normalized Angle": "panel", "Angle of Indicator": "panel",
    "Hurst Exponent": "panel", "R-Squared": "panel", "HMM Regime": "panel",
    "Linear Regression Slope": "panel",
    # v7 normalized / cross-asset-comparable features (all own-scale -> panels)
    "Ratio to SMA": "panel", "Distance from MA (ATR)": "panel", "MA Slope (ATR)": "panel",
    "Z-Score": "panel", "Percentile Rank": "panel", "Return (n-bar)": "panel",
    # v8.2 visual custom building blocks & safe formula
    "Series Math": "panel",
    "Rolling Stat": "overlay",
    "Bar Shift": "overlay",
    "Custom Formula": "panel",
}

# Reference guide-lines drawn inside an indicator's panel (y-values).
INDICATOR_PANEL_GUIDES = {
    "RSI": [30, 70], "Stochastic Oscillator": [20, 80], "ADX": [25], "MACD": [0],
    "ATR-Normalized Angle": [0], "Angle of Indicator": [0], "Linear Regression Slope": [0],
    "Hurst Exponent": [0.5],
    # v7 normalized features
    "Ratio to SMA": [1.0], "Distance from MA (ATR)": [0], "MA Slope (ATR)": [0],
    "Z-Score": [-2, 0, 2], "Percentile Rank": [0.2, 0.8], "Return (n-bar)": [0],
}
