"""
quantdesk.backtest.engine — single-file backtest run (with optional optimisation).

Wraps ``backtesting.Backtest`` the same way the monolith did (upstox_app.py:2478-2545):
cash = initial capital, commission = brokerage + slippage, plus margin. Returns the raw stats
plus the strategy instance so the UI can read indicators / dividends. Optimisation is delegated
to :mod:`quantdesk.backtest.optimizer`.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

import pandas as pd

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


def make_backtest(df: pd.DataFrame, code: str, init_cash: float, total_commission: float, margin: float,
                  *, segment: Optional[str] = None, slippage: float = 0.0) -> "Backtest":
    """Compile ``code`` and build a Backtest.

    v7.1 charge-accurate mode: when ``segment`` is given, the REAL India per-leg statutory charges are
    applied INSIDE the simulation (``costs.india.make_fee_model``) and ``slippage`` becomes the bid-ask
    ``spread`` — so Return/Sharpe/DSR/WFO/MC and the optimizer objective are all net of real charges.
    Without ``segment`` the legacy flat ``total_commission`` proxy is used (back-compat for callers/tests).
    """
    # Lazy import: `backtesting` (and its bokeh dependency) is a ~2.5s cold import. Deferring it to
    # here keeps importing this module — and therefore opening the Backtester page — instant; the
    # cost is paid once, on the first actual run, under the page's "Running…" spinner.
    from backtesting import Backtest

    cls = compile_strategy(code)
    if segment:
        from quantdesk.costs.india import make_fee_model
        return Backtest(df, cls, cash=init_cash, commission=make_fee_model(segment),
                        spread=float(slippage or 0.0), margin=margin, finalize_trades=True)
    return Backtest(df, cls, cash=init_cash, commission=total_commission, margin=margin, finalize_trades=True)


def run_single(
    df: pd.DataFrame,
    code: str,
    init_cash: float,
    total_commission: float,
    margin: float,
    *,
    segment: Optional[str] = None,
    slippage: float = 0.0,
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

    def _mk(frame):
        return make_backtest(frame, code, init_cash, total_commission, margin,
                             segment=segment, slippage=slippage)

    if optimize_mode and opt_kwargs:
        split = int(len(df) * (1.0 - oos_fraction))
        is_df, oos_df = df.iloc[:split], df.iloc[split:]
        if len(is_df) < 50 or len(oos_df) < 30:
            stats, best, meta = _opt.optimize(_mk(df), optimize_mode, opt_kwargs, maximize, n_trials, progress_cb)
            return stats, {"best_params": best, "no_oos": True, "opt_meta": meta}
        _, best, meta = _opt.optimize(_mk(is_df), optimize_mode, opt_kwargs, maximize, n_trials, progress_cb)
        # Re-evaluate the FROZEN best params on each segment with a fresh compiled class.
        is_stats = _mk(is_df).run(**best)
        oos_stats = _mk(oos_df).run(**best)
        info = {
            "best_params": best, "is_stats": is_stats, "oos_stats": oos_stats,
            "split_index": split, "oos_fraction": oos_fraction, "no_oos": False,
            "opt_meta": meta,  # heatmap / trials / trial_sharpes for the validation panel (#C)
        }
        return oos_stats, info
    return _mk(df).run(), None


def native_bokeh_html(df: pd.DataFrame, code: str, init_cash: float, total_commission: float, margin: float,
                      *, segment: Optional[str] = None, slippage: float = 0.0) -> str:
    """Render backtesting.py's NATIVE Bokeh chart (price+indicators+equity+drawdown+trades) to a
    standalone HTML string for embedding via st.components.html (the "Pro chart" option).

    This is the one genuine Bokeh win the debate identified — reuse the engine's polished, linked,
    auto-downsampled plot — captured without pivoting the owned Plotly charts. Runs one extra
    backtest on demand (opt-in). Raises on failure so the UI can show a clear message.
    """
    import tempfile

    bt = make_backtest(df, code, init_cash, total_commission, margin, segment=segment, slippage=slippage)
    stats = bt.run()
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    try:
        bt.plot(results=stats, filename=tmp.name, open_browser=False, resample=True)
        with open(tmp.name, "r", encoding="utf-8") as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass
