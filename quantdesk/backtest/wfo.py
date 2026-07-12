"""
quantdesk.backtest.wfo — walk-forward optimisation (v7 Phase C).

A single in-sample/out-of-sample split (``engine.run_single``) is fragile to *where* you cut the
data. Walk-forward is the industry-standard cure: slide an optimise-then-evaluate window across the
series, **re-optimising on each in-sample window and trading the frozen best params on the next,
unseen out-of-sample window**. Stitching the OOS segments yields one honest equity curve, and the
**Walk-Forward Efficiency** (OOS return ÷ IS return) quantifies how much of the in-sample edge
survives out of sample (≈1 = robust; well below 1 = overfit).

Reuses :func:`engine.make_backtest` + :func:`optimizer.optimize` per window (no new engine code).
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

from quantdesk.backtest import metrics
from quantdesk.backtest import optimizer as _opt
from quantdesk.backtest.engine import make_backtest


def _oos_equity(stats, init_cash: float) -> pd.Series:
    """Datetime-indexed OOS equity series for one window (falls back to a flat line)."""
    try:
        eq = stats["_equity_curve"]["Equity"].copy()
        eq.index = pd.to_datetime(eq.index)
        return eq
    except Exception:
        return pd.Series([init_cash], dtype="float64")


def walk_forward(
    df: pd.DataFrame,
    code: str,
    init_cash: float,
    total_commission: float,
    margin: float,
    *,
    opt_kwargs: dict,
    maximize: str,
    mode: str = "Grid Search (Exhaustive)",
    n_windows: int = 4,
    oos_frac: float = 0.4,
    anchored: bool = True,
    n_trials: int = 30,
    segment: Optional[str] = None,
    slippage: float = 0.0,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """Run anchored/rolling walk-forward optimisation. Returns a result dict.

    Parameters
    ----------
    opt_kwargs : the same param-range dict ``engine.run_single`` uses (must be non-empty).
    maximize : the metric/objective optimised on each in-sample window.
    mode : "Grid Search (Exhaustive)" or "Optuna (...)".
    n_windows : number of OOS windows (>= 2).
    oos_frac : fraction of the WHOLE series used as combined out-of-sample (split across windows).
    anchored : True = IS always starts at bar 0 and grows; False = fixed-length rolling IS window.

    Returns ``{windows: DataFrame, stitched_equity: Series, wfe_median, wfe_mean,
    combined_oos_return_pct, is_return_mean_pct, oos_return_mean_pct, n_windows, anchored, error}``.
    """
    if not opt_kwargs:
        raise ValueError("Walk-forward needs at least one parameter range to optimise.")
    L = len(df)
    n_windows = max(2, int(n_windows))
    total_oos = int(L * oos_frac)
    oos_len = total_oos // n_windows
    is_len_first = L - total_oos
    if oos_len < 30 or is_len_first < 50:
        raise ValueError(
            f"Dataset too short for {n_windows}-window walk-forward (have {L} bars; need each OOS "
            f">=30 and first IS >=50). Use fewer windows, a smaller OOS fraction, or more data."
        )

    rows, segments = [], []
    for i in range(n_windows):
        oos_start = is_len_first + i * oos_len
        oos_end = L if i == n_windows - 1 else oos_start + oos_len
        is_end = oos_start
        is_start = 0 if anchored else max(0, is_end - is_len_first)
        is_df, oos_df = df.iloc[is_start:is_end], df.iloc[oos_start:oos_end]
        if len(is_df) < 50 or len(oos_df) < 30:
            continue
        try:
            bt_is = make_backtest(is_df, code, init_cash, total_commission, margin, segment=segment, slippage=slippage)
            _, best, _meta = _opt.optimize(bt_is, mode, opt_kwargs, maximize, n_trials, None)
            is_stats = make_backtest(is_df, code, init_cash, total_commission, margin, segment=segment, slippage=slippage).run(**best)
            oos_stats = make_backtest(oos_df, code, init_cash, total_commission, margin, segment=segment, slippage=slippage).run(**best)
        except Exception as e:  # noqa: BLE001 - record per-window failure, keep going
            rows.append({"Window": i + 1, "Error": str(e)[:120]})
            continue

        is_ann = metrics.annualized_return(is_stats)
        oos_ann = metrics.annualized_return(oos_stats)
        wfe = (oos_ann / is_ann) if (is_ann and abs(is_ann) > 1e-9) else float("nan")
        rows.append({
            "Window": i + 1,
            "IS bars": len(is_df), "OOS bars": len(oos_df),
            "Best Params": best,
            "IS Return [%]": round(float(is_stats.get("Return [%]", 0)), 2),
            "OOS Return [%]": round(float(oos_stats.get("Return [%]", 0)), 2),
            "IS Ann [%]": round(float(is_ann), 2), "OOS Ann [%]": round(float(oos_ann), 2),
            "OOS Sharpe": round(float(metrics.safe_stat(oos_stats, "Sharpe Ratio")), 2),
            "OOS Trades": int(metrics.safe_stat(oos_stats, "# Trades")),
            "WFE": round(float(wfe), 2) if not np.isnan(wfe) else None,
        })
        segments.append(_oos_equity(oos_stats, init_cash))
        if progress_cb:
            progress_cb((i + 1) / n_windows, f"Walk-forward window {i + 1}/{n_windows}")

    # Stitch OOS equity: chain each window's growth onto the running balance.
    running = float(init_cash)
    stitched_parts = []
    for eq in segments:
        if eq is None or len(eq) == 0 or float(eq.iloc[0]) == 0:
            continue
        seg = running * (eq / float(eq.iloc[0]))
        stitched_parts.append(seg)
        running = float(seg.iloc[-1])
    stitched = pd.concat(stitched_parts) if stitched_parts else pd.Series(dtype="float64")
    stitched = stitched[~stitched.index.duplicated(keep="first")].sort_index()

    windows_df = pd.DataFrame(rows)
    ok = windows_df[windows_df.get("WFE").notna()] if "WFE" in windows_df.columns else pd.DataFrame()
    wfe_vals = pd.to_numeric(ok["WFE"], errors="coerce").dropna() if not ok.empty else pd.Series(dtype="float64")
    is_ret = pd.to_numeric(windows_df.get("IS Return [%]"), errors="coerce").dropna() if "IS Return [%]" in windows_df else pd.Series(dtype="float64")
    oos_ret = pd.to_numeric(windows_df.get("OOS Return [%]"), errors="coerce").dropna() if "OOS Return [%]" in windows_df else pd.Series(dtype="float64")

    if not segments:
        raise RuntimeError("Walk-forward produced no usable OOS windows (every window failed).")

    return {
        "windows": windows_df,
        "stitched_equity": stitched,
        "wfe_median": float(wfe_vals.median()) if len(wfe_vals) else float("nan"),
        "wfe_mean": float(wfe_vals.mean()) if len(wfe_vals) else float("nan"),
        "combined_oos_return_pct": (running / init_cash - 1) * 100 if stitched_parts else float("nan"),
        "is_return_mean_pct": float(is_ret.mean()) if len(is_ret) else float("nan"),
        "oos_return_mean_pct": float(oos_ret.mean()) if len(oos_ret) else float("nan"),
        "n_windows": len(segments),
        "anchored": anchored,
        "error": None,
    }
