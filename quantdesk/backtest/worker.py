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
from quantdesk.config import QUALITY_BLOCK_THRESHOLD
from quantdesk.data.quality import validate as validate_quality
from quantdesk.strategy.compiler import compile_strategy


def run_worker(args):
    """args = (file_path, code, init_cash, comm, marg, feature_vars[, segment[, slippage]]).

    Returns a result dict. Preserves 6-tuple compatibility while supporting optional
    segment/slippage charges in-engine.
    """
    file_path, code, init_cash, comm, marg, feature_vars, *rest = args
    segment = rest[0] if len(rest) > 0 else None
    slippage = float(rest[1] or 0.0) if len(rest) > 1 else 0.0
    from backtesting import Backtest  # local import keeps spawn import set minimal

    try:
        if file_path.endswith(".parquet"):
            df = pd.read_parquet(file_path, engine="pyarrow")
        else:
            df = pd.read_csv(file_path)

        if df.empty or len(df) < 50:
            return {"File": os.path.basename(file_path), "Error": "Too few rows"}

        if "Timestamp" in df.columns:
            from quantdesk.data.loader import normalize_timestamps
            df["Timestamp"] = normalize_timestamps(df["Timestamp"])  # one tz convention
            df.set_index("Timestamp", inplace=True)
        elif df.index.name == "Timestamp" or isinstance(df.index, pd.DatetimeIndex):
            from quantdesk.data.loader import normalize_timestamps
            df.index = normalize_timestamps(pd.Series(df.index))
            df.index.name = "Timestamp"

        # Data-quality gate (#3): block obviously-broken data, annotate the rest.
        q = validate_quality(df.reset_index() if df.index.name == "Timestamp" else df)
        if q["quality_score"] < QUALITY_BLOCK_THRESHOLD:
            return {"File": os.path.basename(file_path), "Error": f"Data quality {q['quality_score']} below block threshold"}

        strategy_class = compile_strategy(code)

        if segment:
            from quantdesk.costs.india import charges_for_trades, make_fee_model
            bt = Backtest(
                df, strategy_class, cash=init_cash,
                commission=make_fee_model(segment),
                spread=slippage, margin=marg, finalize_trades=True,
            )
        else:
            bt = Backtest(df, strategy_class, cash=init_cash, commission=comm, margin=marg, finalize_trades=True)
        stats = bt.run()
        strat_instance = stats.get("_strategy", None)

        ann_ret = safe_stat(stats, "Return (Ann.) [%]")
        if "Return (Ann.) [%]" not in stats or pd.isna(stats["Return (Ann.) [%]"]):
            duration_days = (stats["End"] - stats["Start"]).days if ("End" in stats and "Start" in stats) else 0
            if duration_days > 0:
                ann_ret = ((stats["Return [%]"] / 100 + 1) ** (365.25 / duration_days) - 1) * 100

        try:
            start_date = str(df.index.min().date())
            end_date = str(df.index.max().date())
        except Exception:
            start_date, end_date = "", ""

        res = {
            "File": os.path.basename(file_path),
            "Start Date": start_date,
            "End Date": end_date,
            "Initial Capital [₹]": init_cash,
            "Return [%]": safe_stat(stats, "Return [%]"),
            "Return (Ann.) [%]": round(ann_ret, 3),
            "Max. Drawdown [%]": safe_stat(stats, "Max. Drawdown [%]"),
            "Win Rate [%]": safe_stat(stats, "Win Rate [%]"),
            "Total Trades": int(safe_stat(stats, "# Trades")),
            "Sharpe Ratio": safe_stat(stats, "Sharpe Ratio"),
            "Sortino Ratio": safe_stat(stats, "Sortino Ratio"),
            "Calmar Ratio": safe_stat(stats, "Calmar Ratio"),
            # Buried metrics backtesting.py already computes (#5):
            "Profit Factor": safe_stat(stats, "Profit Factor"),
            "Expectancy [%]": safe_stat(stats, "Expectancy [%]"),
            "SQN": safe_stat(stats, "SQN"),
            "Buy & Hold Return [%]": safe_stat(stats, "Buy & Hold Return [%]"),
            "Data Quality": q["quality_score"],
            "Dividends Earned [₹]": getattr(strat_instance, "dividends_earned", 0.0) if strat_instance else 0.0,
            "Rights Premium Earned [₹]": getattr(strat_instance, "rights_premium_earned", 0.0) if strat_instance else 0.0,
            "Equity Final [₹]": safe_stat(stats, "Equity Final [$]"),
        }
        if segment:
            res["Total Charges"] = charges_for_trades(stats.get("_trades"), segment)["total"]

        # persistence-only fields so batch rows reach the leaderboard + are reproducible
        # (dropped from the on-screen table by run_batch). Commission/fee-model is applied
        # in-sim, so Return [%] IS net of applied charges.
        try:
            import json as _json
            from quantdesk.backtest.metrics import equity_curve_daily
            from quantdesk.core import reproducibility as repro
            res["Net Return [%]"] = res["Return [%]"]
            try:
                res["Dataset Hash"] = repro.hash_dataframe(df)
            except Exception:
                res["Dataset Hash"] = repro.hash_file(file_path)
            eq = equity_curve_daily(stats)
            res["Equity Curve JSON"] = _json.dumps(
                [[str(pd.Timestamp(k).date()), round(float(v), 2)] for k, v in eq.items()])
        except Exception:  # pragma: no cover - persistence extras never fail the run
            res.setdefault("Net Return [%]", res.get("Return [%]"))
            res.setdefault("Dataset Hash", None)
            res.setdefault("Equity Curve JSON", None)

        trades_df = pd.DataFrame()
        if feature_vars and "_trades" in stats and not stats["_trades"].empty:
            trades = stats["_trades"].copy()
            trades_df = enrich_trades(trades, strat_instance, feature_vars)

        return {"File": os.path.basename(file_path), "Stats": res, "Trades": trades_df}
    except Exception as e:  # noqa: BLE001 — surface per-file failure like the original
        try:  # a per-run error report makes an overnight batch failure diagnosable
            from quantdesk.core.logging import write_error_report
            write_error_report({"file": file_path, "init_cash": init_cash, "commission": comm}, e)
        except Exception:
            pass
        return {"File": os.path.basename(file_path), "Error": str(e)[:100]}


# Backwards-compatible alias matching the original function name.
_backtest_worker = run_worker
