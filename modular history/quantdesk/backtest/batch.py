"""
quantdesk.backtest.batch — multi-core folder batch backtesting.

Port of the Folder Batch Mode (upstox_app.py:2748-2833): scan handled by the caller, this runs
every file through :func:`quantdesk.backtest.worker.run_worker` in a ProcessPoolExecutor,
aggregates a results table with an AVERAGE row, and persists each result to the ``backtests``
table. Streamlit-free: progress is reported through ``progress_cb``.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, List, Optional

import pandas as pd

from quantdesk.backtest.worker import run_worker
from quantdesk.core.db import connect
from quantdesk.core.logging import get_logger

log = get_logger("backtest.batch")


def find_data_files(folder: str) -> List[str]:
    """Recursively list .csv/.parquet files under ``folder``."""
    files = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            if name.endswith((".csv", ".parquet")):
                files.append(os.path.join(root, name))
    return files


def run_batch(
    files: List[str],
    code: str,
    init_cash: float,
    total_commission: float,
    margin: float,
    strat_dir_name: str = "Long Only",
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """Run the strategy across all files concurrently.

    Returns ``{"results": [...], "failed": [...], "table": DataFrame}`` where ``table`` includes
    the trailing AVERAGE row (mirrors the original display).
    """
    args_list = [(f, code, init_cash, total_commission, margin, []) for f in files]
    results, failed = [], []

    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(run_worker, arg) for arg in args_list]
        for idx, future in enumerate(as_completed(futures)):
            res = future.result()
            if "Error" in res:
                failed.append(res)
            else:
                results.append(res["Stats"])
            if progress_cb:
                pct = (idx + 1) / len(files)
                progress_cb(pct, f"Batch Backtest: {int(pct * 100)}% ({idx + 1}/{len(files)})")

    table = pd.DataFrame()
    if results:
        table = pd.DataFrame(results)
        table.insert(1, "Strategy Direction", strat_dir_name)
        summary = table.select_dtypes(include=["float", "int"]).mean().round(3).to_frame().T
        summary["File"] = "📊 AVERAGE"
        summary["Strategy Direction"] = strat_dir_name
        table = pd.concat([table, summary], ignore_index=True)

    return {"results": results, "failed": failed, "table": table}


def extract_trades(
    files: List[str],
    code: str,
    init_cash: float,
    total_commission: float,
    margin: float,
    feature_vars: List[str],
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> List["pd.DataFrame"]:
    """Run the strategy across files and return each non-empty enriched trades frame.

    Used by the ML Pattern Finder to gather ``Entry_<feature>`` snapshots across datasets
    (multi-core), mirroring the monolith's parallel feature extraction (upstox_app.py:3064-3073).
    """
    args_list = [(f, code, init_cash, total_commission, margin, feature_vars) for f in files]
    all_trades: List[pd.DataFrame] = []
    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(run_worker, arg) for arg in args_list]
        for idx, future in enumerate(as_completed(futures)):
            res = future.result()
            if "Trades" in res and not res["Trades"].empty:
                all_trades.append(res["Trades"])
            if progress_cb:
                progress_cb((idx + 1) / len(files), f"Extracting features {idx + 1}/{len(files)}")
    return all_trades


def save_batch_to_db(results: List[dict], strategy_label: str, indicators: list) -> None:
    """Persist each batch result row to the ``backtests`` table (parity with the original)."""
    strat_config_json = json.dumps({"indicators": indicators})
    with connect() as conn:
        c = conn.cursor()
        for res in results:
            c.execute(
                """INSERT INTO backtests
                   (strategy_name, symbol, timeframe, start_date, end_date, parameters,
                    return_pct, max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio,
                    plot_file, full_stats, strategy_config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_label, res["File"], "Batch", "", "", "{}",
                    res["Return [%]"], res["Max. Drawdown [%]"], res["Win Rate [%]"],
                    res["Total Trades"], res["Sharpe Ratio"], "none", "{}", strat_config_json,
                ),
            )
