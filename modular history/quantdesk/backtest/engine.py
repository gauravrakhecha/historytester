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
from quantdesk.config import QUALITY_BLOCK_THRESHOLD
from quantdesk.data.quality import validate as _validate_quality
from quantdesk.strategy.compiler import compile_strategy


def _enforce_quality(df: pd.DataFrame) -> None:
    """Standing-rule gate inside the engine so EVERY caller is protected (not just UI/worker)."""
    frame = df.reset_index() if df.index.name == "Timestamp" else df
    q = _validate_quality(frame)
    if q["quality_score"] < QUALITY_BLOCK_THRESHOLD:
        raise ValueError(
            f"Data quality {q['quality_score']}/100 is below the block threshold "
            f"({QUALITY_BLOCK_THRESHOLD}); refusing to backtest. Issues: {', '.join(q['issues']) or 'n/a'}"
        )


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
    oos_fraction: float = 0.3,
    progress_cb: Optional[Callable[[float, str], None]] = None,
):
    """Run one backtest. Returns ``(stats, info)``.

    Plain run: ``info`` is ``None`` and ``stats`` is the full-period result.

    Optimised run (#1 — honest results): the optimiser tunes ONLY on the in-sample first
    ``1-oos_fraction`` of the data; the frozen best params are then evaluated once on the
    held-out out-of-sample tail. The **OOS** stats become the headline ``stats`` (what the
    strategy would realistically have done on unseen data), and ``info`` carries
    ``{best_params, is_stats, oos_stats, split_index, oos_fraction}`` for the comparison panel.
    If the OOS tail is too small, it falls back to a full-period optimise (``info['no_oos']``).
    """
    _enforce_quality(df)
    if optimize_mode and opt_kwargs:
        split = int(len(df) * (1.0 - oos_fraction))
        is_df, oos_df = df.iloc[:split], df.iloc[split:]
        if len(is_df) < 50 or len(oos_df) < 30:
            bt = make_backtest(df, code, init_cash, total_commission, margin)
            stats, best = _opt.optimize(bt, optimize_mode, opt_kwargs, maximize, n_trials, progress_cb)
            return stats, {"best_params": best, "no_oos": True}
        bt_is = make_backtest(is_df, code, init_cash, total_commission, margin)
        _, best = _opt.optimize(bt_is, optimize_mode, opt_kwargs, maximize, n_trials, progress_cb)
        # Re-evaluate the FROZEN best params on each segment with a fresh compiled class.
        is_stats = make_backtest(is_df, code, init_cash, total_commission, margin).run(**best)
        oos_stats = make_backtest(oos_df, code, init_cash, total_commission, margin).run(**best)
        info = {
            "best_params": best, "is_stats": is_stats, "oos_stats": oos_stats,
            "split_index": split, "oos_fraction": oos_fraction, "no_oos": False,
        }
        return oos_stats, info
    return make_backtest(df, code, init_cash, total_commission, margin).run(), None
