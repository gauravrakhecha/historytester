"""
quantdesk.backtest.optimizer — grid search + Optuna Bayesian optimisation.

Port of the optimiser block (upstox_app.py:2515-2542). Two engines:
- **Optuna** (Bayesian): suggest params per trial, maximise the chosen metric, then run once
  more with the best params to return a full stats object.
- **Grid Search**: ``backtesting.Backtest.optimize`` over the cartesian product.

Fix vs original: the Optuna integer upper bound is clamped to ``max(start, stop-step)`` so a
single-value range can't produce ``high < low``.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd


def _optuna_optimize(bt, opt_kwargs, maximize, n_trials, progress_cb=None):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    _SENTINEL = -999.0

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
        val = res.get(maximize, 0)
        return float(val) if not pd.isna(val) else _SENTINEL

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
    return stats, dict(study.best_params)


def _grid_optimize(bt, opt_kwargs, maximize):
    stats = bt.optimize(maximize=maximize, **opt_kwargs)
    strat = stats.get("_strategy", None)
    best = {k: getattr(strat, k, None) for k in opt_kwargs} if strat is not None else {}
    return stats, best


def optimize(
    bt,
    mode: str,
    opt_kwargs: dict,
    maximize: str,
    n_trials: int = 50,
    progress_cb: Optional[Callable[[float, str], None]] = None,
):
    """Run the chosen optimiser. Returns ``(stats, best_params)``."""
    if mode.startswith("Optuna"):
        return _optuna_optimize(bt, opt_kwargs, maximize, n_trials, progress_cb)
    return _grid_optimize(bt, opt_kwargs, maximize)
