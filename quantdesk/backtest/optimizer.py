"""
quantdesk.backtest.optimizer — grid search + Optuna Bayesian optimisation.

Port of the optimiser block (upstox_app.py:2515-2542). Two engines:
- **Optuna** (Bayesian): suggest params per trial, maximise the chosen metric, then run once
  more with the best params to return a full stats object.
- **Grid Search**: ``backtesting.Backtest.optimize`` over the cartesian product.

Fix vs original: the Optuna integer upper bound is clamped to ``max(start, stop-step)`` so a
single-value range can't produce ``high < low``.

v7 Phase C — validation: every run now also returns a ``meta`` dict for downstream rigor:
- grid mode surfaces the previously-discarded ``return_heatmap`` (parameter-stability surface);
- Optuna mode records each trial's params/objective/per-period Sharpe (the trial-Sharpe
  variance feeds the Deflated Sharpe Ratio) and exposes the ``"__PSR__"`` objective.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

# Sentinel objective: maximise the Probabilistic Sharpe Ratio (computed per trial from returns).
PSR_OBJECTIVE = "__PSR__"


def _trial_sharpe_and_psr(res):
    """Per-period (daily) Sharpe and PSR for one finished trial's stats object."""
    from quantdesk.backtest import metrics
    try:
        rets = metrics.equity_returns(res, resample_daily=True)
        mo = metrics.sharpe_moments(rets)
        psr = metrics.probabilistic_sharpe_ratio(mo["sr"], mo["n"], mo["skew"], mo["kurtosis"], 0.0)
        return mo["sr"], psr
    except Exception:
        return float("nan"), float("nan")


def _optuna_optimize(bt, opt_kwargs, maximize, n_trials, progress_cb=None):
    import numpy as np
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    _SENTINEL = -999.0
    is_psr = maximize == PSR_OBJECTIVE
    trial_sharpes: list = []

    def objective(trial):
        trial_kwargs = {}
        for k, v in opt_kwargs.items():
            if isinstance(v, range):
                high = max(v.start, v.stop - v.step)
                trial_kwargs[k] = trial.suggest_int(k, v.start, high, step=v.step)
            else:
                vals = list(v)
                # An int-natured param given as a float list (e.g. periods) must use suggest_int,
                # else Optuna feeds e.g. period=13.74 into indicators -> every trial silently fails.
                if vals and all(float(x).is_integer() for x in vals):
                    trial_kwargs[k] = trial.suggest_int(k, int(min(vals)), int(max(vals)))
                else:
                    trial_kwargs[k] = trial.suggest_float(k, min(vals), max(vals))
        res = bt.run(**trial_kwargs)
        sr, psr = _trial_sharpe_and_psr(res)
        trial_sharpes.append(sr)
        trial.set_user_attr("sharpe", sr)
        if is_psr:
            val = psr
        else:
            val = res.get(maximize, 0)
        return float(val) if val is not None and not pd.isna(val) else _SENTINEL

    study = optuna.create_study(direction="maximize")
    if progress_cb:
        def _cb(study_, trial_):
            progress_cb(len(study_.trials) / n_trials, f"Optuna trial {len(study_.trials)}/{n_trials}")

        study.optimize(objective, n_trials=n_trials, callbacks=[_cb])
    else:
        study.optimize(objective, n_trials=n_trials)

    # Don't return a fake "best" when every trial failed (sentinel) — surface it.
    if study.best_value is None or study.best_value <= _SENTINEL + 1.0:
        raise RuntimeError(
            "Optimizer found no valid parameter configuration (all trials failed). "
            "Check the parameter ranges/types for the selected indicators."
        )
    stats = bt.run(**study.best_params)

    # Trials table (params + objective + per-trial Sharpe) for the parameter-stability view.
    rows = []
    for t in study.trials:
        if t.value is None or t.value <= _SENTINEL + 1.0:
            continue
        row = dict(t.params)
        row["objective"] = float(t.value)
        row["sharpe"] = float(t.user_attrs.get("sharpe", float("nan")))
        rows.append(row)
    trials_df = pd.DataFrame(rows)
    clean_sharpes = [s for s in trial_sharpes if s is not None and not pd.isna(s)]
    meta = {
        "engine": "optuna",
        "heatmap": None,
        "trials": trials_df,
        "trial_sharpes": clean_sharpes or None,
        "n_trials": len(clean_sharpes),
        "maximize": maximize,
    }
    return stats, dict(study.best_params), meta


def _grid_optimize(bt, opt_kwargs, maximize):
    # PSR isn't a backtesting.py stat key; grid mode falls back to Sharpe (DSR needs Optuna).
    grid_maximize = "Sharpe Ratio" if maximize == PSR_OBJECTIVE else maximize
    result = bt.optimize(maximize=grid_maximize, return_heatmap=True, **opt_kwargs)
    if isinstance(result, tuple) and len(result) == 2:
        stats, heatmap = result
    else:  # older backtesting.py without return_heatmap support
        stats, heatmap = result, None
    strat = stats.get("_strategy", None)
    best = {k: getattr(strat, k, None) for k in opt_kwargs} if strat is not None else {}
    meta = {
        "engine": "grid",
        "heatmap": heatmap,
        "trials": None,
        "trial_sharpes": None,
        "n_trials": int(len(heatmap)) if heatmap is not None else None,
        "maximize": grid_maximize,
    }
    return stats, best, meta


def optimize(
    bt,
    mode: str,
    opt_kwargs: dict,
    maximize: str,
    n_trials: int = 50,
    progress_cb: Optional[Callable[[float, str], None]] = None,
):
    """Run the chosen optimiser. Returns ``(stats, best_params, meta)``.

    ``meta`` carries validation artefacts: ``heatmap`` (grid), ``trials``/``trial_sharpes``
    (Optuna), ``n_trials`` and ``maximize`` — used for the parameter-stability surface and the
    Deflated Sharpe Ratio.
    """
    if mode.startswith("Optuna"):
        return _optuna_optimize(bt, opt_kwargs, maximize, n_trials, progress_cb)
    return _grid_optimize(bt, opt_kwargs, maximize)
