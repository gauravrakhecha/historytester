"""
quantdesk.risk — position sizing + stop-loss primitives (v7.1 Task 3).

Pure, unit-tested functions with no engine/Streamlit dependencies. The no-code codegen emits calls
into these (they're injected into the compile namespace like the vectorised indicator helpers), so
the *same* tested logic runs inside the backtest — instead of sizing/stop math being inlined as
strings. This is what lifts the app from fixed-₹/lot sizing (the audit's toy-tier Risk score) to
risk-based sizing, ATR/breakeven/time stops, and a max-drawdown circuit-breaker.
"""
from quantdesk.risk.sizing import (
    atr_risk_qty,
    fixed_value_qty,
    half_kelly_fraction,
    kelly_fraction,
    percent_equity_qty,
    risk_pct_qty,
    round_to_lot,
)
from quantdesk.risk.stops import atr_stop, breakeven_stop, static_sl_tp, trailing_stop

__all__ = [
    "round_to_lot", "fixed_value_qty", "percent_equity_qty", "risk_pct_qty", "atr_risk_qty",
    "kelly_fraction", "half_kelly_fraction",
    "static_sl_tp", "atr_stop", "breakeven_stop", "trailing_stop",
]
