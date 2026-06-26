"""
quantdesk.backtest.metrics — stat extraction, annualised return, yearly perf, trade enrichment.

Ports the scattered metric helpers of the monolith:
- ``safe_stat`` (upstox_app.py:725-730)
- annualised-return fallback (733-736 / 2548-2553)
- year-wise performance from the equity curve (2593-2611)
- trade-log enrichment with Entry_<indicator> snapshots (755-771 / 2617-2645) — VECTORISED
  (the original built Python lists per indicator per trade).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantdesk.strategy.models import safe_name

OHLCV = ("Close", "Open", "High", "Low", "Volume")


def safe_stat(stats, key, default=0):
    try:
        v = stats[key]
        return round(float(v), 3) if not pd.isna(v) else default
    except Exception:
        return default


def annualized_return(stats) -> float:
    """Return the annualised %; recompute from duration if backtesting.py left it NaN."""
    try:
        if "Return (Ann.) [%]" in stats and not pd.isna(stats["Return (Ann.) [%]"]):
            return round(float(stats["Return (Ann.) [%]"]), 3)
    except Exception:
        pass
    try:
        duration_days = (stats["End"] - stats["Start"]).days
        if duration_days > 0:
            return round(((stats["Return [%]"] / 100 + 1) ** (365.25 / duration_days) - 1) * 100, 3)
    except Exception:
        pass
    return 0.0


def yearly_performance(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """Per-calendar-year Return% and Max Drawdown% from an equity curve (index=datetime)."""
    rows = []
    for year, group in equity_curve.groupby(equity_curve.index.year):
        start_eq = group["Equity"].iloc[0]
        end_eq = group["Equity"].iloc[-1]
        ret = ((end_eq / start_eq) - 1) * 100
        mdd = group["DrawdownPct"].min() * 100
        if mdd > 0:
            mdd = -mdd
        rows.append({"Year": str(year), "Return [%]": round(ret, 2), "Max Drawdown [%]": round(mdd, 2)})
    return pd.DataFrame(rows).set_index("Year") if rows else pd.DataFrame()


def _entry_feature_array(strat_instance, var: str):
    """Resolve the per-bar array for ``var`` from a strategy instance (OHLCV or indicator)."""
    if var in OHLCV:
        if hasattr(strat_instance.data, var):
            return np.asarray(getattr(strat_instance.data, var))
        return None
    attr = safe_name(var)
    if hasattr(strat_instance, attr):
        arr = getattr(strat_instance, attr)
        if isinstance(arr, (np.ndarray, pd.Series, list)) or hasattr(arr, "__len__"):
            return np.asarray(arr)
    return None


def enrich_trades(trades: pd.DataFrame, strat_instance, variables) -> pd.DataFrame:
    """Add ``Entry_<var>`` columns sampling each indicator's value at the trade's EntryBar.

    Vectorised replacement of the original per-row list comprehension. Returns ``trades``.
    """
    if trades.empty or not variables:
        return trades
    entry_bars = trades["EntryBar"].to_numpy()
    for var in variables:
        arr = _entry_feature_array(strat_instance, var)
        if arr is None:
            continue
        vals = np.full(len(entry_bars), np.nan, dtype="float64")
        valid = entry_bars < len(arr)
        vals[valid] = np.asarray(arr, dtype="float64")[entry_bars[valid].astype(int)]
        trades[f"Entry_{var}"] = vals
    return trades


def extract_scalar_metrics(stats) -> dict:
    """Curated scalar metrics, INCLUDING ones backtesting.py computes but the app buried (#5):
    Profit Factor, Expectancy [%], SQN, Buy & Hold Return [%]."""
    return {
        "return_pct": safe_stat(stats, "Return [%]"),
        "ann_return_pct": annualized_return(stats),
        "max_drawdown_pct": safe_stat(stats, "Max. Drawdown [%]"),
        "win_rate_pct": safe_stat(stats, "Win Rate [%]"),
        "num_trades": int(safe_stat(stats, "# Trades")),
        "sharpe": safe_stat(stats, "Sharpe Ratio"),
        "sortino": safe_stat(stats, "Sortino Ratio"),
        "calmar": safe_stat(stats, "Calmar Ratio"),
        "profit_factor": safe_stat(stats, "Profit Factor"),
        "expectancy_pct": safe_stat(stats, "Expectancy [%]"),
        "sqn": safe_stat(stats, "SQN"),
        "buy_hold_return_pct": safe_stat(stats, "Buy & Hold Return [%]"),
    }


def _equity_series(stats) -> pd.Series:
    eq = stats["_equity_curve"]["Equity"]
    eq.index = pd.to_datetime(eq.index)
    return eq


def equity_returns(stats, resample_daily: bool = True) -> pd.Series:
    """Returns series for QuantStats. CRITICAL: QuantStats annualises assuming DAILY periodicity,
    so intraday equity is resampled to daily first (feeding 5-min returns inflates Sharpe/CAGR)."""
    eq = _equity_series(stats)
    if resample_daily:
        eq = eq.resample("1D").last().dropna()
    rets = eq.pct_change().dropna()
    rets.name = "Strategy"
    return rets


def equity_curve_daily(stats) -> pd.Series:
    """Daily equity series (for persistence + the Results leaderboard overlay)."""
    return _equity_series(stats).resample("1D").last().dropna()


def clean_stats_series(stats, init_cash, strat_instance) -> pd.Series:
    """Drop internals, format dates, add Initial Capital + dividends/rights, rename $→₹."""
    clean = stats.drop(["_strategy", "_equity_curve", "_trades"], errors="ignore")
    if "Start" in clean:
        clean["Start"] = str(clean["Start"])
    if "End" in clean:
        clean["End"] = str(clean["End"])
    clean["Initial Capital [₹]"] = init_cash
    if hasattr(strat_instance, "dividends_earned"):
        clean["Dividends Earned [₹]"] = strat_instance.dividends_earned
    if hasattr(strat_instance, "rights_premium_earned"):
        clean["Rights Premium Earned [₹]"] = strat_instance.rights_premium_earned
    clean = clean.apply(lambda x: round(x, 3) if isinstance(x, (int, float)) else x)
    clean.index = [idx.replace("[$]", "[₹]") for idx in clean.index]
    return clean
