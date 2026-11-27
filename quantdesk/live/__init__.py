"""
quantdesk.live — OpenAlgo v1 live execution, pre-trade risk guard, and signal runner.

Zero dependency on the ``openalgo`` Python SDK; communicates strictly via OpenAlgo's
REST v1 Marshmallow JSON endpoints through :class:`OpenAlgoClient`.
"""
from __future__ import annotations

from quantdesk.live.openalgo_client import OpenAlgoClient, OpenAlgoClientError
from quantdesk.live.risk_guard import (
    LiveRiskConfig,
    LiveRiskState,
    RiskDecision,
    evaluate_pretrade_risk,
)
from quantdesk.live.signal_runner import (
    LiveDeploymentSpec,
    evaluate_strategy_on_bars,
    format_alert_payload,
    read_live_events,
    step_once,
)

__all__ = [
    "LiveDeploymentSpec",
    "LiveRiskConfig",
    "LiveRiskState",
    "OpenAlgoClient",
    "OpenAlgoClientError",
    "RiskDecision",
    "evaluate_pretrade_risk",
    "evaluate_strategy_on_bars",
    "format_alert_payload",
    "read_live_events",
    "step_once",
]
