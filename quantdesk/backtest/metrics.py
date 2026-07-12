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

import math

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
        # backtesting.py DrawdownPct is a NON-NEGATIVE magnitude, so the worst is the MAX
        # (using .min() reported ~0% for every year). Present as a negative number.
        mdd = -group["DrawdownPct"].max() * 100
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


def enrich_trade_analytics(trades: pd.DataFrame, strat_instance) -> pd.DataFrame:
    """Add MAE_pct / MFE_pct / R_multiple columns to a backtesting.py trades frame.

    MAE (max adverse excursion) and MFE (max favourable excursion) are the worst
    and best UNREALISED moves during each trade, measured from the entry price
    over the trade's bar window (High/Low), sign-aware for long vs short. The
    R-multiple normalises the realised return by the worst adverse excursion —
    a "how many units of pain did each unit of gain cost" measure. Backtesting.py
    doesn't expose these per trade; we reconstruct them from the price series.
    """
    if trades is None or trades.empty or strat_instance is None:
        return trades
    try:
        highs = np.asarray(strat_instance.data.High, dtype="float64")
        lows = np.asarray(strat_instance.data.Low, dtype="float64")
    except Exception:  # pragma: no cover - no price series available
        return trades
    n = len(highs)
    out = trades.copy()
    mae = np.full(len(out), np.nan)
    mfe = np.full(len(out), np.nan)
    eb = out["EntryBar"].to_numpy()
    xb = out["ExitBar"].to_numpy()
    ep = out["EntryPrice"].to_numpy(dtype="float64")
    size = out["Size"].to_numpy()
    for i in range(len(out)):
        a, b = int(eb[i]), int(min(xb[i], n - 1))
        if b < a or a < 0 or ep[i] <= 0:
            continue
        hi, lo = highs[a:b + 1].max(), lows[a:b + 1].min()
        if size[i] > 0:  # long
            mfe[i] = (hi - ep[i]) / ep[i] * 100.0
            mae[i] = (lo - ep[i]) / ep[i] * 100.0     # <= 0
        else:            # short
            mfe[i] = (ep[i] - lo) / ep[i] * 100.0
            mae[i] = (ep[i] - hi) / ep[i] * 100.0     # <= 0
    out["MFE_pct"] = mfe
    out["MAE_pct"] = mae
    ret_pct = out["ReturnPct"].to_numpy(dtype="float64") * 100.0 if "ReturnPct" in out else np.full(len(out), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(np.abs(mae) > 1e-9, ret_pct / np.abs(mae), np.nan)
    out["R_multiple"] = np.round(r, 3)
    return out


def trade_kelly(trades: pd.DataFrame) -> dict:
    """Kelly fraction, payoff ratio and expectancy from a trades frame.

    Uses per-trade ReturnPct (scale-independent). full_kelly = p - (1-p)/b where
    p = win rate and b = avg_win / avg_loss; half_kelly is the conservative
    default. A non-positive edge yields a non-positive Kelly (don't size up).
    """
    zero = {"win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "payoff_ratio": 0.0,
            "full_kelly": 0.0, "half_kelly": 0.0, "expectancy_pct": 0.0, "num_trades": 0}
    if trades is None or trades.empty or "ReturnPct" not in trades:
        return zero
    r = trades["ReturnPct"].to_numpy(dtype="float64") * 100.0
    r = r[~np.isnan(r)]
    if len(r) == 0:
        return zero
    wins, losses = r[r > 0], -r[r < 0]
    p = len(wins) / len(r)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    b = avg_win / avg_loss if avg_loss > 0 else 0.0
    full = (p - (1 - p) / b) if b > 0 else (p if avg_loss == 0 and avg_win > 0 else 0.0)
    return {
        "win_rate": round(p * 100.0, 2), "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3), "payoff_ratio": round(b, 3),
        "full_kelly": round(full, 4), "half_kelly": round(full / 2.0, 4),
        "expectancy_pct": round(float(r.mean()), 4), "num_trades": int(len(r)),
    }


def tax_breakdown(trades: pd.DataFrame, segment: str) -> pd.DataFrame:
    """Per-trade India statutory-charge breakdown (the 'tax format' export).

    One row per trade with STT / stamp / exchange / SEBI / brokerage / GST and
    gross vs net P&L. Buy/sell legs are assigned by direction so the sell-side
    STT lands on the correct leg for shorts. Engine P&L is already net of
    charges (in-sim fee model), so gross = net + total charges.
    """
    from quantdesk.costs.india import compute_trade_charges
    if trades is None or trades.empty:
        return pd.DataFrame()
    rows = []
    for _, t in trades.iterrows():
        size = abs(float(t.get("Size", 0) or 0))
        ep, xp = float(t.get("EntryPrice", 0) or 0), float(t.get("ExitPrice", 0) or 0)
        entry_val, exit_val = ep * size, xp * size
        if float(t.get("Size", 0) or 0) >= 0:            # long: buy at entry, sell at exit
            buy_val, sell_val = entry_val, exit_val
        else:                                            # short: sell at entry, buy at exit
            buy_val, sell_val = exit_val, entry_val
        ch = compute_trade_charges(segment, buy_val, sell_val)
        net = round(float(t.get("PnL", 0) or 0), 2)
        rows.append({
            "EntryTime": t.get("EntryTime"), "ExitTime": t.get("ExitTime"),
            "Size": t.get("Size"), "EntryPrice": ep, "ExitPrice": xp,
            "BuyValue": round(buy_val, 2), "SellValue": round(sell_val, 2),
            "STT": ch["stt"], "StampDuty": ch["stamp_duty"], "Exchange": ch["exchange"],
            "SEBI": ch["sebi"], "Brokerage": ch["brokerage"], "GST": ch["gst"],
            "TotalCharges": ch["total"], "GrossPnL": round(net + ch["total"], 2), "NetPnL": net,
        })
    return pd.DataFrame(rows)


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


# ============================================================================================
# v7 Phase C — optimizer validation: Probabilistic / Deflated Sharpe + Monte-Carlo bootstrap.
# Sharpe alone is misleading after an optimization (best-of-N inflates it) and on a short sample
# (a high Sharpe over few periods is mostly luck). PSR asks "what's the probability the TRUE Sharpe
# beats a benchmark, given this sample's length, skew and fat tails?"; DSR additionally deflates the
# benchmark for the number of configurations tried. References: Bailey & López de Prado (2014).
# ============================================================================================
def sharpe_moments(returns: pd.Series) -> dict:
    """Per-period (NON-annualized) Sharpe + sample size + skew/kurtosis from a returns series.

    PSR/DSR require the per-observation Sharpe and the number of observations (NOT the annualized
    Sharpe), so always derive these from the same returns series you report on (e.g. daily).
    """
    r = pd.Series(returns, dtype="float64").dropna()
    n = int(len(r))
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    sr = float(r.mean() / sd) if sd > 0 else 0.0
    skew = float(r.skew()) if n > 2 else 0.0
    # pandas .kurtosis() is EXCESS kurtosis (0 for normal); PSR uses full kurtosis (3 for normal).
    kurt = (float(r.kurtosis()) + 3.0) if n > 3 else 3.0
    return {"sr": sr, "n": n, "skew": skew, "kurtosis": kurt, "std": sd}


def probabilistic_sharpe_ratio(sr: float, n: int, skew: float = 0.0, kurtosis: float = 3.0,
                               benchmark_sr: float = 0.0) -> float:
    """P(true Sharpe > benchmark_sr) given the observed per-period Sharpe ``sr`` over ``n`` periods.

    In [0, 1]; higher is better. ~0.95 is a common "trust it" bar. Returns NaN if undefined.
    """
    if n is None or n < 2:
        return float("nan")
    denom = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    if denom <= 0:
        return float("nan")
    try:
        from scipy.stats import norm
        z = (sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom)
        return float(norm.cdf(z))
    except Exception:  # pragma: no cover - normal-CDF fallback
        z = (sr - benchmark_sr) * math.sqrt(n - 1) / math.sqrt(denom)
        return 0.5 * math.erfc(-z / math.sqrt(2.0))


def expected_max_sharpe(n_trials: int, trials_sr_std: float, trials_sr_mean: float = 0.0) -> float:
    """Expected MAXIMUM per-period Sharpe across ``n_trials`` independent trials (Bailey/LdP).

    This is the benchmark a selected ("best of N") Sharpe must beat to be credible.
    """
    N = max(int(n_trials), 1)
    if N < 2 or trials_sr_std <= 0:
        return trials_sr_mean
    try:
        from scipy.stats import norm
        g = float(np.euler_gamma)
        a = float(norm.ppf(1.0 - 1.0 / N))
        b = float(norm.ppf(1.0 - 1.0 / (N * math.e)))
        return trials_sr_mean + trials_sr_std * ((1.0 - g) * a + g * b)
    except Exception:  # pragma: no cover
        return trials_sr_mean


def deflated_sharpe_ratio(sr: float, n: int, n_trials: int, trials_sr_std: float,
                          skew: float = 0.0, kurtosis: float = 3.0,
                          trials_sr_mean: float = 0.0) -> float:
    """Deflated Sharpe: PSR against the EXPECTED-MAX Sharpe of ``n_trials`` configurations.

    Corrects for the fact that picking the best of many backtests inflates the winner's Sharpe.
    """
    sr0 = expected_max_sharpe(n_trials, trials_sr_std, trials_sr_mean)
    return probabilistic_sharpe_ratio(sr, n, skew, kurtosis, benchmark_sr=sr0)


def validation_stats(stats, *, n_trials: int = 1, trial_sharpes=None) -> dict:
    """Bundle PSR (always) and DSR (when trial Sharpes are known) for a finished backtest.

    ``stats`` is a backtesting.py results object; PSR/DSR are computed from DAILY equity returns so
    they're consistent with the rest of the app's QuantStats numbers.
    """
    out = {"psr": float("nan"), "dsr": float("nan"), "sr": float("nan"), "n": 0, "n_trials": n_trials}
    try:
        rets = equity_returns(stats, resample_daily=True)
    except Exception:
        return out
    m = sharpe_moments(rets)
    out.update({"sr": m["sr"], "n": m["n"]})
    out["psr"] = probabilistic_sharpe_ratio(m["sr"], m["n"], m["skew"], m["kurtosis"], 0.0)
    if trial_sharpes is not None:
        ts = pd.Series(trial_sharpes, dtype="float64").dropna()
        if len(ts) >= 2:
            out["dsr"] = deflated_sharpe_ratio(
                m["sr"], m["n"], n_trials=len(ts), trials_sr_std=float(ts.std(ddof=1)),
                skew=m["skew"], kurtosis=m["kurtosis"],
            )
    return out


def monte_carlo_bootstrap(trade_returns, init_cash: float = 100000.0, n_sims: int = 1000,
                          seed: int = 42) -> dict:
    """Bootstrap the per-trade return sequence (resample WITH replacement) -> outcome distribution.

    ``trade_returns`` are per-trade fractional returns (backtesting.py ``ReturnPct``). Returns P5/P50/
    P95 of final equity, total return %, and worst drawdown %, plus a per-step ``bands`` DataFrame
    (p5/p50/p95 + the realized curve) for a fan chart. Drawdown bands answer "how bad could the path
    have gotten by luck of ordering?".
    """
    r = np.asarray(pd.Series(trade_returns, dtype="float64").dropna(), dtype="float64")
    m = int(len(r))
    out = {"n_sims": int(n_sims), "n_trades": m, "init_cash": float(init_cash), "error": None}
    if m < 2:
        out["error"] = "Need at least 2 trades for a bootstrap."
        return out
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, m, size=(int(n_sims), m))
    equity = init_cash * np.cumprod(1.0 + r[idx], axis=1)            # (n_sims, m)
    final = equity[:, -1]
    running_max = np.maximum.accumulate(equity, axis=1)
    max_dd = ((equity - running_max) / running_max).min(axis=1) * 100.0  # negative %
    realized = init_cash * np.cumprod(1.0 + r)

    def _p(a, p):
        return float(np.percentile(a, p))

    out.update({
        "final_p5": _p(final, 5), "final_p50": _p(final, 50), "final_p95": _p(final, 95),
        "final_mean": float(final.mean()),
        "ret_p5": (_p(final, 5) / init_cash - 1) * 100, "ret_p50": (_p(final, 50) / init_cash - 1) * 100,
        "ret_p95": (_p(final, 95) / init_cash - 1) * 100,
        "mdd_p5": _p(max_dd, 5), "mdd_p50": _p(max_dd, 50), "mdd_p95": _p(max_dd, 95),
        "realized_final": float(realized[-1]),
        "realized_return_pct": (realized[-1] / init_cash - 1) * 100,
        "bands": pd.DataFrame({
            "p5": np.percentile(equity, 5, axis=0),
            "p50": np.percentile(equity, 50, axis=0),
            "p95": np.percentile(equity, 95, axis=0),
            "realized": realized,
        }, index=np.arange(1, m + 1)),
    })
    return out


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
