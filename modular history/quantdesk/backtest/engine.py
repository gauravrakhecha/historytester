"""
quantdesk.backtest.engine — single-file backtest run (with optional optimisation).

Wraps ``backtesting.Backtest`` the same way the monolith did (upstox_app.py:2478-2545):
cash = initial capital, commission = brokerage + slippage, plus margin. Returns the raw stats
plus the strategy instance so the UI can read indicators / dividends. Optimisation is delegated
to :mod:`quantdesk.backtest.optimizer`.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
from backtesting import Backtest

from quantdesk.backtest import optimizer as _opt
from quantdesk.strategy.compiler import compile_strategy


def make_backtest(df: pd.DataFrame, code: str, init_cash: float, total_commission: float, margin: float) -> Backtest:
    """Compile ``code`` and build a Backtest (commission already includes slippage)."""
    cls = compile_strategy(code)
    return Backtest(df, cls, cash=init_cash, commission=total_commission, margin=margin, finalize_trades=True)


def run_single(
    df: pd.DataFrame,
    code: str,
    init_cash: float,
    total_commission: float,
    margin: float,
    *,
    optimize_mode: Optional[str] = None,
    opt_kwargs: Optional[dict] = None,
    maximize: Optional[str] = None,
    n_trials: int = 50,
    progress_cb: Optional[Callable[[float, str], None]] = None,
):
    """Run one backtest (optionally optimised). Returns ``(stats, best_params)``.

    ``best_params`` is ``None`` for a plain run. ``stats`` is the backtesting.py result Series
    (carrying ``_strategy`` / ``_equity_curve`` / ``_trades``).
    """
    bt = make_backtest(df, code, init_cash, total_commission, margin)
    if optimize_mode and opt_kwargs:
        stats, best = _opt.optimize(bt, optimize_mode, opt_kwargs, maximize, n_trials, progress_cb)
        return stats, best
    return bt.run(), None
