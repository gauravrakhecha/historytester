"""
Helper functions for the No-Code Strategy Builder v2 UI.

Keeps ``quantdesk/ui/pages/strategy_builder.py`` focused on layout by isolating:
- beginner indicator subsets & plain-English descriptions
- readable default indicator naming (e.g. ``EMA_10``, ``RSI_14``)
- custom formula examples & safe reference list
- live plain-English summary + sanity alert rendering
- 1-click "Quick Test Strategy (Preview)" runner (using latest downloaded dataset or synthetic fallback)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import streamlit as st

from quantdesk.core import settings_repo
from quantdesk.core.db import get_download_history
from quantdesk.data.loader import load_indexed
from quantdesk.strategy.codegen import build_strategy_code
from quantdesk.strategy.compiler import compile_strategy
from quantdesk.strategy.indicators import SAFE_FORMULA_FUNCS
from quantdesk.strategy.models import StrategyConfig, safe_name
from quantdesk.strategy.templates import (
    check_strategy_sanity,
    describe_strategy_plain_english,
)

BEGINNER_INDICATORS: List[str] = [
    "EMA",
    "SMA",
    "RSI",
    "Bollinger Bands",
    "MACD",
    "Supertrend",
    "ATR",
    "VWAP",
]

CUSTOM_BLOCK_TYPES: List[str] = [
    "Series Math",
    "Rolling Stat",
    "Bar Shift",
    "Custom Formula",
]

INDICATOR_HELP: Dict[str, str] = {
    "SMA": "Simple Moving Average: average price over the last N bars. Smooths out noise to show trend.",
    "EMA": "Exponential Moving Average: reacts faster to recent prices than SMA. Great for trend crossovers.",
    "WMA": "Weighted Moving Average: puts extra weight on the newest bars.",
    "ALMA": "Arnaud Legoux Moving Average: low-lag Gaussian-weighted moving average.",
    "RSI": "Relative Strength Index (0–100): below 30 often means oversold (cheap); above 70 means overbought (stretched).",
    "MACD": "Trend & momentum indicator with two lines (MACD line and Signal line). Crossovers signal shifts.",
    "Bollinger Bands": "Upper & lower volatility bands around a moving average. Breakouts or bounces happen at the bands.",
    "ATR": "Average True Range: measures how much price normally moves per bar (volatility).",
    "Supertrend": "Trend-following line + direction (+1 bullish, -1 bearish) that flips when price reverses.",
    "VWAP": "Volume-Weighted Average Price: average price weighted by volume (popular intraday benchmark).",
    "Stochastic Oscillator": "Momentum oscillator (0–100) with %K and %D lines; <20 oversold, >80 overbought.",
    "ADX": "Average Directional Index: measures trend strength (above 25 = strong trend).",
    "Parabolic SAR": "Trailing stop-and-reverse dots that follow price during trends.",
    "Keltner Channels": "Volatility channels around an EMA using ATR.",
    "Donchian Channels": "Highest high and lowest low over the last N bars (classic breakout bands).",
    "Ratio to SMA": "Current value divided by its N-bar SMA (e.g. Volume > 1.5x its 20-bar average).",
    "Distance from MA (ATR)": "How far price is from its EMA, measured in ATR volatility units.",
    "MA Slope (ATR)": "Speed/slope of the EMA normalized by ATR.",
    "Z-Score": "How many standard deviations the value is above/below its rolling mean.",
    "Percentile Rank": "Where the current value ranks (0.0 to 1.0) over the last N bars.",
    "Return (n-bar)": "Percentage return over the last N bars.",
    "Series Math": "Combine any two columns (or a column + number) using +, -, *, /, or % difference.",
    "Rolling Stat": "Compute rolling Max, Min, Mean, Sum, or Std Dev over N bars.",
    "Bar Shift": "Read a column's value from N bars ago (no lookahead).",
    "Custom Formula": "Write a safe 1-line math expression using OHLCV, prior indicators, and built-in functions.",
}

SERIES_MATH_OP_LABELS: Dict[str, str] = {
    "add": "Add (A + B)",
    "sub": "Subtract (A - B)",
    "mul": "Multiply (A × B)",
    "div": "Divide (A ÷ B)",
    "pct_diff": "% Difference ((A - B) / B × 100)",
}

ROLLING_STAT_LABELS: Dict[str, str] = {
    "max": "Rolling Maximum",
    "min": "Rolling Minimum",
    "mean": "Rolling Mean (Average)",
    "sum": "Rolling Sum",
    "std": "Rolling Std Dev",
}

TIMING_LABELS: Dict[str, str] = {
    "now": "Now (this bar)",
    "bars_ago": "N bars ago",
    "within_last": "Within last N bars",
    "consecutive": "For N consecutive bars",
}

FORMULA_EXAMPLES: List[tuple[str, str]] = [
    ("(Close - Open) / atr(14)", "Candle body size measured in ATR units"),
    ("Close - sma(Close, 20)", "Distance above/below 20-bar SMA"),
    ("rolling_max(High, 20) - rolling_min(Low, 20)", "20-bar price channel width"),
    ("(Close - shift(Close, 1)) / shift(Close, 1) * 100", "1-bar percentage change"),
    ("rsi(Close, 14) - ema(rsi(Close, 14), 5)", "RSI smoothed momentum"),
]

_SHORT_PREFIX: Dict[str, str] = {
    "Bollinger Bands": "BB",
    "Supertrend": "ST",
    "Stochastic Oscillator": "Stoch",
    "Parabolic SAR": "PSAR",
    "Keltner Channels": "KC",
    "Donchian Channels": "Donchian",
    "Linear Regression Slope": "LRSlope",
    "R-Squared": "R2",
    "ATR-Normalized Angle": "AngleATR",
    "Angle of Indicator": "IndAngle",
    "Hurst Exponent": "Hurst",
    "HMM Regime": "HMM",
    "Ratio to SMA": "RatioSMA",
    "Distance from MA (ATR)": "DistATR",
    "MA Slope (ATR)": "SlopeATR",
    "Z-Score": "ZScore",
    "Percentile Rank": "PctRank",
    "Return (n-bar)": "Ret",
    "Series Math": "Math",
    "Rolling Stat": "Roll",
    "Bar Shift": "Shift",
    "Custom Formula": "Formula",
}


def suggest_indicator_name(
    ind_type: str,
    params: Dict[str, Any],
    existing_names: List[str],
) -> str:
    """Suggest a readable, collision-free default indicator name (e.g. ``EMA_10``, ``RSI_14``)."""
    prefix = _SHORT_PREFIX.get(ind_type, safe_name(ind_type))
    if ind_type == "Series Math":
        base = f"Math_{params.get('op', 'add')}"
    elif ind_type == "Rolling Stat":
        base = f"Roll_{params.get('stat', 'max')}_{params.get('period', 20)}"
    elif ind_type == "Bar Shift":
        base = f"Shift_{params.get('shift', 1)}"
    elif ind_type == "Custom Formula":
        base = "Formula_1"
    elif "period" in params:
        base = f"{prefix}_{params['period']}"
    elif "fast" in params and "slow" in params:
        base = f"{prefix}_{params['fast']}_{params['slow']}"
    elif "k_period" in params:
        base = f"{prefix}_{params['k_period']}"
    else:
        base = prefix

    existing_set = set(existing_names)
    existing_safe = {safe_name(n) for n in existing_names}
    if base not in existing_set and safe_name(base) not in existing_safe:
        return base
    idx = 2
    while True:
        cand = f"{base}_{idx}"
        if cand not in existing_set and safe_name(cand) not in existing_safe:
            return cand
        idx += 1


def make_synthetic_preview_df(n: int = 300) -> pd.DataFrame:
    """Generate a clean synthetic OHLCV dataset when no downloaded files exist yet."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-02 09:15", periods=n, freq="15min")
    steps = rng.normal(0.05, 1.4, size=n) + 0.6 * np.sin(np.linspace(0, 18, n))
    close = np.maximum(20.0, 100.0 + np.cumsum(steps))
    open_ = np.r_[close[0], close[:-1]]
    spread = rng.uniform(0.3, 1.5, size=n)
    high = np.maximum(open_, close) + spread
    low = np.maximum(1.0, np.minimum(open_, close) - spread)
    volume = rng.integers(1000, 15000, size=n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def run_quick_preview(cfg: StrategyConfig) -> Dict[str, Any]:
    """Compile ``cfg`` and run a fast preview backtest on the latest downloaded dataset (or synthetic fallback)."""
    from backtesting import Backtest

    df = None
    dataset_label = ""
    try:
        hist = get_download_history()
        if hist is not None and not hist.empty:
            for _, row in hist.iterrows():
                fpath = str(row.get("file_path", "") or "")
                if fpath and os.path.exists(fpath):
                    loaded = load_indexed(fpath)
                    if loaded is not None and len(loaded) >= 30:
                        df = loaded.iloc[-2000:].copy()
                        dataset_label = (
                            f"{row.get('symbol', 'Data')} ({row.get('interval', '')}, "
                            f"last {len(df)} bars)"
                        )
                        break
    except Exception:  # noqa: BLE001 — fall back cleanly to synthetic preview
        df = None

    if df is None:
        df = make_synthetic_preview_df(300)
        dataset_label = "Synthetic sample data (300 bars — download data for real-symbol preview)"

    params = settings_repo.backtest_params()
    code = build_strategy_code(cfg)
    strat_cls = compile_strategy(code)
    stats = Backtest(
        df,
        strat_cls,
        cash=float(params["initial_capital"]),
        commission=float(params["total_commission"]),
        margin=float(params["margin"]),
        finalize_trades=True,
    ).run()

    return {
        "dataset_label": dataset_label,
        "trades": int(stats.get("# Trades", 0) or 0),
        "return_pct": float(stats.get("Return [%]", 0.0) or 0.0),
        "win_rate_pct": float(stats.get("Win Rate [%]", 0.0) or 0.0),
        "max_dd_pct": float(stats.get("Max. Drawdown [%]", 0.0) or 0.0),
        "code": code,
    }


def render_plain_english_card(cfg: StrategyConfig) -> None:
    """Render the live Plain-English strategy summary card."""
    desc = describe_strategy_plain_english(cfg)
    with st.container(border=True):
        st.markdown("#### 📖 Live Plain-English Summary")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**📊 Indicators**")
            for line in desc["indicators"]:
                st.markdown(f"- {line}")
            st.markdown("**🟢 Buy When**")
            for line in desc["buy"]:
                st.markdown(f"- {line}")
            st.markdown("**🔴 Sell When**")
            for line in desc["sell"]:
                st.markdown(f"- {line}")
        with c2:
            st.markdown("**🛡️ Filters & Regime**")
            for line in desc["filters"]:
                st.markdown(f"- {line}")
            st.markdown("**💰 Position Sizing**")
            for line in desc["sizing"]:
                st.markdown(f"- {line}")
            st.markdown("**⚖️ Risk & Exits**")
            for line in desc["risk"]:
                st.markdown(f"- {line}")


def render_sanity_alerts(cfg: StrategyConfig) -> None:
    """Render strategy sanity warnings or a green all-clear badge."""
    issues = check_strategy_sanity(cfg)
    if issues:
        for msg in issues:
            st.warning(f"⚠️ {msg}")
    else:
        st.success("✅ Sanity check passed: entry, exit, and risk rules look consistent.")


def formula_functions_caption() -> str:
    """Readable summary of functions permitted in Custom Formula."""
    return ", ".join(sorted(SAFE_FORMULA_FUNCS))
