"""
quantdesk.risk.sizing — position-sizing primitives (pure, unit-tested).

All functions return an integer quantity already rounded DOWN to a whole multiple of ``lot_size``
(never below one lot), so the caller can pass the result straight to ``self.buy(size=...)``. They
are defensive: non-finite / non-positive prices, equity, or stop distances yield ``lot_size`` (one
lot) rather than raising, so a single bad bar can't crash a backtest mid-run.
"""
from __future__ import annotations

import math


def round_to_lot(qty: float, lot_size: int = 1) -> int:
    """Floor ``qty`` to a whole multiple of ``lot_size`` (minimum one lot)."""
    lot = max(1, int(lot_size))
    if not math.isfinite(qty) or qty <= 0:
        return lot
    n = int(qty) - (int(qty) % lot)
    return max(lot, n)


def _safe(*vals) -> bool:
    """True if every value is finite and strictly positive."""
    return all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in vals)


def fixed_value_qty(fixed_value: float, price: float, lot_size: int = 1) -> int:
    """Buy a fixed ₹ notional per trade: ``qty = fixed_value / price``."""
    if not _safe(fixed_value, price):
        return max(1, int(lot_size))
    return round_to_lot(fixed_value / price, lot_size)


def percent_equity_qty(equity: float, pct: float, price: float, lot_size: int = 1) -> int:
    """Deploy ``pct`` percent of current equity as notional: ``qty = equity*pct/100 / price``."""
    if not _safe(equity, pct, price):
        return max(1, int(lot_size))
    return round_to_lot((equity * pct / 100.0) / price, lot_size)


def risk_pct_qty(equity: float, risk_pct: float, stop_distance: float, lot_size: int = 1) -> int:
    """Risk a fixed % of equity per trade given a stop distance (₹ per share/unit).

    Sizes so that being stopped out loses ~``risk_pct``% of equity: ``qty = equity*risk_pct/100 /
    stop_distance``. This is the core of professional position sizing — the trade size adapts to how
    far the stop is, so every trade risks the same fraction of capital regardless of volatility.
    """
    if not _safe(equity, risk_pct, stop_distance):
        return max(1, int(lot_size))
    return round_to_lot((equity * risk_pct / 100.0) / stop_distance, lot_size)


def atr_risk_qty(equity: float, risk_pct: float, atr: float, atr_mult: float, lot_size: int = 1) -> int:
    """Risk-% sizing with an ATR-derived stop distance (``stop_distance = atr_mult * atr``)."""
    if not _safe(atr, atr_mult):
        return max(1, int(lot_size))
    return risk_pct_qty(equity, risk_pct, atr_mult * atr, lot_size)


def kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Full-Kelly capital fraction: ``f = p - (1-p)/b`` (p=win rate 0..1, b=avg_win/avg_loss).

    Clamped to [0, 1]; returns 0 for a non-positive edge. (Kelly is aggressive — most desks trade a
    fraction of it; see :func:`half_kelly_fraction`.)
    """
    if not (isinstance(win_rate, (int, float)) and 0.0 <= win_rate <= 1.0):
        return 0.0
    if not (isinstance(payoff_ratio, (int, float)) and math.isfinite(payoff_ratio) and payoff_ratio > 0):
        return 0.0
    f = win_rate - (1.0 - win_rate) / payoff_ratio
    return float(min(1.0, max(0.0, f)))


def half_kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Half-Kelly — the common, lower-variance default (Kelly/2)."""
    return kelly_fraction(win_rate, payoff_ratio) / 2.0
