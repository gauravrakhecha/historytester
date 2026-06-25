"""
quantdesk.backtest.worker — top-level worker for ProcessPoolExecutor (Streamlit-free).

Port of the monolith's ``_backtest_worker`` (upstox_app.py:696-775). It runs in a SEPARATE
process (folder batch + ML feature extraction), so it must be importable without Streamlit and
picklable by reference. It compiles the generated strategy, runs the backtest, and returns a
plain dict of stats (+ optionally an enriched trades frame for ML).
"""
from __future__ import annotations

import os

import numpy as np  # noqa: F401 — used indirectly + kept for parity of the worker import set
import pandas as pd

from quantdesk.backtest.metrics import enrich_trades, safe_stat
from quantdesk.strategy.compiler import compile_strategy


def run_worker(args):
    """args = (file_path, code, init_cash, comm, marg, feature_vars). Returns a result dict."""
    file_path, code, init_cash, comm, marg, feature_vars = args
    from backtesting import Backtest  # local import keeps spawn import set minimal

    try:
        if file_path.endswith(".parquet"):
            df = pd.read_parquet(file_path, engine="pyarrow")
        else:
            df = pd.read_csv(file_path)

        if df.empty or len(df) < 50:
            return {"File": os.path.basename(file_path), "Error": "Too few rows"}

        if "Timestamp" in df.columns:
            df["Timestamp"] = pd.to_datetime(df["Timestamp"])
            df.set_index("Timestamp", inplace=True)

        strategy_class = compile_strategy(code)

        bt = Backtest(df, strategy_class, cash=init_cash, commission=comm, margin=marg, finalize_trades=True)
        stats = bt.run()
        strat_instance = stats.get("_strategy", None)

        ann_ret = safe_stat(stats, "Return (Ann.) [%]")
        if "Return (Ann.) [%]" not in stats or pd.isna(stats["Return (Ann.) [%]"]):
            duration_days = (stats["End"] - stats["Start"]).days if ("End" in stats and "Start" in stats) else 0
            if duration_days > 0:
                ann_ret = ((stats["Return [%]"] / 100 + 1) ** (365.25 / duration_days) - 1) * 100

        res = {
            "File": os.path.basename(file_path),
            "Initial Capital [₹]": init_cash,
            "Return [%]": safe_stat(stats, "Return [%]"),
            "Return (Ann.) [%]": round(ann_ret, 3),
            "Max. Drawdown [%]": safe_stat(stats, "Max. Drawdown [%]"),
            "Win Rate [%]": safe_stat(stats, "Win Rate [%]"),
            "Total Trades": int(safe_stat(stats, "# Trades")),
            "Sharpe Ratio": safe_stat(stats, "Sharpe Ratio"),
            "Sortino Ratio": safe_stat(stats, "Sortino Ratio"),
            "Calmar Ratio": safe_stat(stats, "Calmar Ratio"),
            "Dividends Earned [₹]": getattr(strat_instance, "dividends_earned", 0.0) if strat_instance else 0.0,
            "Rights Premium Earned [₹]": getattr(strat_instance, "rights_premium_earned", 0.0) if strat_instance else 0.0,
            "Equity Final [₹]": safe_stat(stats, "Equity Final [$]"),
        }

        trades_df = pd.DataFrame()
        if feature_vars and "_trades" in stats and not stats["_trades"].empty:
            trades = stats["_trades"].copy()
            trades_df = enrich_trades(trades, strat_instance, feature_vars)

        return {"File": os.path.basename(file_path), "Stats": res, "Trades": trades_df}
    except Exception as e:  # noqa: BLE001 — surface per-file failure like the original
        return {"File": os.path.basename(file_path), "Error": str(e)[:100]}


# Backwards-compatible alias matching the original function name.
_backtest_worker = run_worker
