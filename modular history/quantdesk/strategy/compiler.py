"""
quantdesk.strategy.compiler — turn generated source into a backtesting.py Strategy class.

Mirrors the monolith's ``exec(code, namespace)`` + "find the Strategy subclass" step
(upstox_app.py:716-720 / 2489-2496).

Speed without risking parity: we cache the *compiled bytecode* (the expensive parse step),
NOT the resulting class. Each call still ``exec``s into a FRESH namespace and returns a brand
new class, so class-level attributes can never bleed between runs (important after an optimizer
session mutates them). Identical source -> instant re-exec; behaviour is identical to the
original which re-exec'd every time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import ta

from quantdesk.strategy.indicators import (
    alma_vec,
    dist_from_ma_atr_vec,
    lr_slope_vec,
    ma_slope_atr_vec,
    pctile_rank_vec,
    r2_vec,
    ratio_to_sma_vec,
    ret_n_vec,
    zscore_vec,
)
from quantdesk.strategy.validation import assert_safe_ast
from quantdesk.risk.sizing import atr_risk_qty, fixed_value_qty, percent_equity_qty, risk_pct_qty
from quantdesk.risk.stops import atr_stop as _atr_stop

_bytecode_cache: dict[int, object] = {}


def _namespace() -> dict:
    # Heavy imports kept OUT of module scope so importing this module — and the pages that import it
    # (e.g. the Plotter) — stays instant. `backtesting` (~2.3s, pulls bokeh) and `pandas_ta` (~1.8s)
    # are only needed at compile time, which always runs under a caller-side spinner.
    import pandas_ta as pta
    from backtesting import Strategy
    from backtesting.lib import crossover

    return {
        "Strategy": Strategy, "crossover": crossover, "ta": ta, "pd": pd, "np": np, "pta": pta,
        # vectorised indicator helpers referenced by generated code (speed, proven-equal)
        "alma_vec": alma_vec, "lr_slope_vec": lr_slope_vec, "r2_vec": r2_vec,
        # v7 normalized / cross-asset-comparable feature helpers
        "ratio_to_sma_vec": ratio_to_sma_vec, "dist_from_ma_atr_vec": dist_from_ma_atr_vec,
        "ma_slope_atr_vec": ma_slope_atr_vec, "zscore_vec": zscore_vec,
        "pctile_rank_vec": pctile_rank_vec, "ret_n_vec": ret_n_vec,
        # v7.1 Task 3 — risk-based position sizing helpers (pure, unit-tested; see quantdesk.risk)
        "fixed_value_qty": fixed_value_qty, "percent_equity_qty": percent_equity_qty,
        "risk_pct_qty": risk_pct_qty, "atr_risk_qty": atr_risk_qty, "atr_stop": _atr_stop,
    }


def compile_strategy(code: str):
    """Compile ``code`` and return its ``Strategy`` subclass (fresh class each call)."""
    key = hash(code)
    code_obj = _bytecode_cache.get(key)
    if code_obj is None:
        # Security gate (v7.1): reject any disallowed construct (imports/builtins/dunder escapes)
        # BEFORE exec — the one place every run funnels through, so it also covers programmatic
        # callers, not just the preset-upload path. Cached with the bytecode (same source = safe).
        assert_safe_ast(code)
        code_obj = compile(code, "<visual_strategy>", "exec")
        if len(_bytecode_cache) > 128:
            _bytecode_cache.clear()
        _bytecode_cache[key] = code_obj

    ns = _namespace()
    Strategy = ns["Strategy"]  # resolved via the lazy namespace (no module-level backtesting import)
    exec(code_obj, ns)  # noqa: S102 — trusted, self-generated no-code strategy source
    for obj in ns.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            return obj
    raise ValueError("No Strategy subclass found in generated code")


def cache_clear() -> None:
    _bytecode_cache.clear()
