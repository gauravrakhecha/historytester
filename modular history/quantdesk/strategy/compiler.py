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
import pandas_ta as pta
import ta
from backtesting import Strategy
from backtesting.lib import crossover

from quantdesk.strategy.indicators import alma_vec, lr_slope_vec, r2_vec

_bytecode_cache: dict[int, object] = {}


def _namespace() -> dict:
    return {
        "Strategy": Strategy, "crossover": crossover, "ta": ta, "pd": pd, "np": np, "pta": pta,
        # vectorised indicator helpers referenced by generated code (speed, proven-equal)
        "alma_vec": alma_vec, "lr_slope_vec": lr_slope_vec, "r2_vec": r2_vec,
    }


def compile_strategy(code: str):
    """Compile ``code`` and return its ``Strategy`` subclass (fresh class each call)."""
    key = hash(code)
    code_obj = _bytecode_cache.get(key)
    if code_obj is None:
        code_obj = compile(code, "<visual_strategy>", "exec")
        if len(_bytecode_cache) > 128:
            _bytecode_cache.clear()
        _bytecode_cache[key] = code_obj

    ns = _namespace()
    exec(code_obj, ns)  # noqa: S102 — trusted, self-generated no-code strategy source
    for obj in ns.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            return obj
    raise ValueError("No Strategy subclass found in generated code")


def cache_clear() -> None:
    _bytecode_cache.clear()
