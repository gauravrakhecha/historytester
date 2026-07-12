"""
quantdesk.risk.stops — stop-loss / take-profit price primitives (pure, unit-tested).

Each returns an absolute price level (or ``None`` when disabled). ``is_short`` flips the geometry:
for a long, the stop sits BELOW and the target ABOVE the reference; for a short, the reverse. All
are defensive against non-finite inputs (return ``None`` rather than raising).
"""
from __future__ import annotations

import math


def _ok(*vals) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def static_sl_tp(entry: float, sl_pct: float, tp_pct: float, is_short: bool = False):
    """Percent stop-loss / take-profit off ``entry``. Returns ``(sl, tp)`` (either may be None).

    ``sl_pct``/``tp_pct`` are percents (e.g. 2.0 = 2%). A non-positive percent disables that leg.
    """
    sl = tp = None
    if not _ok(entry) or entry <= 0:
        return None, None
    if _ok(sl_pct) and sl_pct > 0:
        sl = entry * (1 + sl_pct / 100.0) if is_short else entry * (1 - sl_pct / 100.0)
    if _ok(tp_pct) and tp_pct > 0:
        tp = entry * (1 - tp_pct / 100.0) if is_short else entry * (1 + tp_pct / 100.0)
    return sl, tp


def atr_stop(entry: float, atr: float, mult: float, is_short: bool = False):
    """ATR-based stop: ``entry -/+ mult*ATR`` (below for long, above for short). None if invalid."""
    if not _ok(entry, atr, mult) or atr <= 0 or mult <= 0 or entry <= 0:
        return None
    dist = mult * atr
    return entry + dist if is_short else entry - dist


def breakeven_stop(entry: float, is_short: bool = False):
    """Break-even stop = the entry price (used once a trade has moved enough in favour)."""
    if not _ok(entry) or entry <= 0:
        return None
    return float(entry)


def trailing_stop(extreme: float, trail_pct: float, is_short: bool = False):
    """Trailing stop off the favourable extreme since entry (highest for long, lowest for short).

    ``extreme`` is the running high (long) or low (short); ``trail_pct`` is a percent. For a long the
    stop is ``extreme*(1 - trail_pct/100)``; for a short ``extreme*(1 + trail_pct/100)``.
    """
    if not _ok(extreme, trail_pct) or extreme <= 0 or trail_pct <= 0:
        return None
    return extreme * (1 + trail_pct / 100.0) if is_short else extreme * (1 - trail_pct / 100.0)
