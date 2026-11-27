"""
quantdesk.live.risk_guard — Pre-trade risk limits and live execution safety gates.

All order-emitting actions (including ``alert_only`` notifications, ``sandbox`` analyzer
runs, and ``live`` broker routing) pass through :func:`evaluate_pretrade_risk`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")
VALID_EXECUTION_MODES = frozenset({"alert_only", "sandbox", "live"})


def _today_ist_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


@dataclass
class LiveRiskConfig:
    """Static pre-trade risk limits for a live deployment."""

    execution_mode: str = "alert_only"
    daily_loss_limit: float = 5000.0
    max_trades_per_day: int = 10
    max_open_positions: int = 3
    cooldown_seconds: int = 60
    live_confirmed: bool = False


@dataclass
class LiveRiskState:
    """Mutable session risk counters tracked across evaluation cycles."""

    date_str: str = field(default_factory=_today_ist_str)
    daily_pnl: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    last_signal_bar_ts: str = ""
    last_order_monotonic: float = 0.0


@dataclass(frozen=True)
class RiskDecision:
    """Result of evaluating pre-trade risk controls."""

    allowed: bool
    reason: str


def evaluate_pretrade_risk(
    action: str,
    bar_ts: str,
    cfg: LiveRiskConfig,
    state: LiveRiskState,
    now_mono: float | None = None,
    is_exit: bool = False,
) -> RiskDecision:
    """Evaluate whether a strategy signal may proceed to alerting/order placement.

    Enforces:
    1. Action validity (``HOLD`` is never emitted).
    2. Valid ``execution_mode`` and explicit ``live_confirmed=True`` gate for ``live``.
    3. Duplicate-bar replay guard (``bar_ts == state.last_signal_bar_ts``).
    4. Daily loss circuit-breaker (``daily_pnl <= -abs(daily_loss_limit)``).
    5. ``max_trades_per_day`` and ``max_open_positions`` for opening actions
       (bypassed when ``is_exit=True`` so open positions can still close).
    6. Inter-order ``cooldown_seconds``.
    """
    act = str(action or "HOLD").strip().upper()
    if act == "HOLD":
        return RiskDecision(False, "No signal (HOLD)")
    if act not in ("BUY", "SELL"):
        return RiskDecision(False, f"Unsupported action: {act}")

    mode = str(cfg.execution_mode or "").strip()
    if mode not in VALID_EXECUTION_MODES:
        return RiskDecision(False, f"Invalid execution_mode: {mode!r}")

    if mode == "live" and not cfg.live_confirmed:
        return RiskDecision(False, "Live execution blocked: live_confirmed must be True")

    bar_key = str(bar_ts or "").strip()
    if bar_key and state.last_signal_bar_ts and bar_key == str(state.last_signal_bar_ts).strip():
        return RiskDecision(False, "Duplicate signal for same bar")

    if cfg.daily_loss_limit > 0 and state.daily_pnl <= -abs(float(cfg.daily_loss_limit)):
        return RiskDecision(
            False,
            f"Daily loss limit breached (daily_pnl={state.daily_pnl:.2f} <= -{abs(cfg.daily_loss_limit):.2f})",
        )

    if not is_exit:
        if cfg.max_trades_per_day > 0 and state.trades_today >= int(cfg.max_trades_per_day):
            return RiskDecision(
                False,
                f"Max trades per day reached ({state.trades_today}/{cfg.max_trades_per_day})",
            )
        if cfg.max_open_positions > 0 and state.open_positions >= int(cfg.max_open_positions):
            return RiskDecision(
                False,
                f"Max open positions reached ({state.open_positions}/{cfg.max_open_positions})",
            )

    if cfg.cooldown_seconds > 0 and state.last_order_monotonic > 0:
        now = time.monotonic() if now_mono is None else float(now_mono)
        elapsed = now - float(state.last_order_monotonic)
        if elapsed < float(cfg.cooldown_seconds):
            return RiskDecision(
                False,
                f"Order cooldown active ({elapsed:.1f}s < {cfg.cooldown_seconds}s)",
            )

    return RiskDecision(True, f"Risk checks passed ({mode})")
