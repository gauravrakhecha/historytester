"""
quantdesk.live.signal_runner — Research-parity signal evaluation, step runner, and CLI.

Evaluates a validated :class:`~quantdesk.strategy.models.StrategyConfig` on OHLCV bars
using the exact same ``build_strategy_code`` + ``Backtest`` execution engine used in
research, then routes signals through :mod:`quantdesk.live.risk_guard` and
:class:`quantdesk.live.openalgo_client.OpenAlgoClient`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import quantdesk.config as _qconfig
from quantdesk.brokers.openalgo import DEFAULT_HOST_URL, normalize_openalgo_exchange
from quantdesk.core.logging import get_logger
from quantdesk.data.loader import normalize_timestamps
from quantdesk.live.openalgo_client import OpenAlgoClient
from quantdesk.live.risk_guard import (
    LiveRiskConfig,
    LiveRiskState,
    RiskDecision,
    evaluate_pretrade_risk,
)
from quantdesk.strategy.codegen import build_strategy_code
from quantdesk.strategy.compiler import compile_strategy
from quantdesk.strategy.models import StrategyConfig, output_columns_for, safe_name
from quantdesk.strategy.presets import load_strategy_raw
from quantdesk.strategy.templates import STARTER_TEMPLATES, get_template
from quantdesk.strategy.validation import validate_preset

log = get_logger("live.signal_runner")
_IST = ZoneInfo("Asia/Kolkata")


@dataclass
class LiveDeploymentSpec:
    """Configuration for a single live/sandbox/alert strategy deployment."""

    strategy_cfg: StrategyConfig | dict[str, Any]
    symbol: str
    exchange: str = "NSE"
    interval: str = "15m"
    quantity: int = 1
    product: str = "MIS"
    pricetype: str = "MARKET"
    strategy_name: str = "QuantDesk"
    lookback_days: int = 10
    initial_capital: float = 100000.0
    risk_config: LiveRiskConfig = field(default_factory=LiveRiskConfig)


def _coerce_strategy_config(cfg: StrategyConfig | dict[str, Any]) -> StrategyConfig:
    raw = cfg.to_dict() if isinstance(cfg, StrategyConfig) else cfg
    validated = validate_preset(raw)
    return StrategyConfig.from_dict(validated)


def _normalize_bars_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        if "Timestamp" in out.columns:
            out["Timestamp"] = normalize_timestamps(out["Timestamp"])
            out = out.dropna(subset=["Timestamp"]).set_index("Timestamp")
        else:
            out.index = pd.to_datetime(out.index, errors="coerce")
            out = out[~out.index.isna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_convert(_IST).tz_localize(None)
    out = out.sort_index()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        elif col == "Volume":
            out[col] = 0.0
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def _finite_float_or_none(val: Any) -> float | None:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _extract_last_indicators(strat_instance: Any, cfg: StrategyConfig) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    if strat_instance is None:
        return out
    for ind in cfg.indicators or []:
        for col in output_columns_for(ind):
            attr = safe_name(col)
            arr = getattr(strat_instance, attr, None)
            if arr is None:
                continue
            try:
                if len(arr) > 0:
                    out[col] = _finite_float_or_none(np.asarray(arr)[-1])
            except Exception:  # noqa: BLE001
                continue
    return out


def evaluate_strategy_on_bars(
    cfg: StrategyConfig | dict[str, Any],
    df: pd.DataFrame,
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    """Run ``cfg`` via ``build_strategy_code`` + ``Backtest`` and inspect the latest bar's signal.

    Uses ``finalize_trades=False`` so orders emitted on the final completed bar ``len(df) - 1``
    remain visible in ``strategy.orders`` while active open positions stay in ``strategy.position``.
    """
    base_result: dict[str, Any] = {
        "bar_time": "",
        "close": None,
        "action": "HOLD",
        "is_exit": False,
        "position_side": "FLAT",
        "indicators": {},
        "error": None,
    }

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        base_result["error"] = "No OHLCV bars provided."
        return base_result

    try:
        clean_df = _normalize_bars_df(df)
    except Exception as exc:  # noqa: BLE001
        base_result["error"] = f"Failed to normalize OHLCV bars: {exc}"
        return base_result

    if len(clean_df) < 2:
        if len(clean_df) == 1:
            base_result["bar_time"] = pd.Timestamp(clean_df.index[-1]).isoformat()
            base_result["close"] = _finite_float_or_none(clean_df["Close"].iloc[-1])
        base_result["error"] = "Insufficient bars for evaluation (need >= 2)."
        return base_result

    bar_time = pd.Timestamp(clean_df.index[-1]).isoformat()
    last_close = _finite_float_or_none(clean_df["Close"].iloc[-1])
    base_result["bar_time"] = bar_time
    base_result["close"] = last_close

    try:
        from backtesting import Backtest

        cfg_obj = _coerce_strategy_config(cfg)
        code = build_strategy_code(cfg_obj)
        strat_cls = compile_strategy(code)

        bt = Backtest(
            clean_df,
            strat_cls,
            cash=float(initial_capital),
            commission=0.0,
            finalize_trades=False,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            stats = bt.run()
    except Exception as exc:  # noqa: BLE001
        base_result["error"] = str(exc)
        return base_result

    strat = stats.get("_strategy")
    pos_size = float(getattr(getattr(strat, "position", None), "size", 0.0) or 0.0)
    if pos_size > 0:
        position_side = "LONG"
    elif pos_size < 0:
        position_side = "SHORT"
    else:
        position_side = "FLAT"

    # Orders placed during next() on the final bar (len(df) - 1) are non-contingent pending orders.
    pending_orders = [
        o for o in getattr(strat, "orders", ()) if not getattr(o, "is_contingent", False)
    ]

    action = "HOLD"
    is_exit = False
    if pending_orders:
        net_size = sum(float(getattr(o, "size", 0.0) or 0.0) for o in pending_orders)
        if net_size > 0:
            action = "BUY"
            is_exit = pos_size < 0 and net_size <= abs(pos_size)
        elif net_size < 0:
            action = "SELL"
            is_exit = pos_size > 0 and abs(net_size) <= pos_size
    else:
        # Check if a stop/TP or intraday square-off closed an open trade inside the final bar.
        trades_df = stats.get("_trades")
        last_bar_idx = len(clean_df) - 1
        if isinstance(trades_df, pd.DataFrame) and not trades_df.empty and "ExitBar" in trades_df.columns:
            exited_last = trades_df[trades_df["ExitBar"] == last_bar_idx]
            if not exited_last.empty:
                last_exit_size = float(exited_last["Size"].iloc[-1])
                action = "SELL" if last_exit_size > 0 else "BUY"
                is_exit = True

    return {
        "bar_time": bar_time,
        "close": last_close,
        "action": action,
        "is_exit": is_exit,
        "position_side": position_side,
        "indicators": _extract_last_indicators(strat, cfg_obj),
        "error": None,
    }


def format_alert_payload(
    spec: LiveDeploymentSpec,
    signal: dict[str, Any],
    risk: RiskDecision,
    *,
    order_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a structured alert/event payload for webhooks and JSONL logs."""
    return {
        "timestamp": datetime.now(_IST).isoformat(timespec="seconds"),
        "strategy": spec.strategy_name,
        "symbol": spec.symbol,
        "exchange": normalize_openalgo_exchange(spec.exchange),
        "interval": spec.interval,
        "execution_mode": spec.risk_config.execution_mode,
        "bar_time": signal.get("bar_time", ""),
        "close": signal.get("close"),
        "action": signal.get("action", "HOLD"),
        "is_exit": bool(signal.get("is_exit", False)),
        "position_side": signal.get("position_side", "FLAT"),
        "quantity": int(spec.quantity),
        "product": spec.product,
        "indicators": signal.get("indicators", {}),
        "risk_allowed": bool(risk.allowed),
        "risk_reason": risk.reason,
        "order_result": order_result,
        "signal_error": signal.get("error"),
    }


def _default_event_log_path() -> Path:
    return _qconfig.WORKFLOW_DIR / "live_events.jsonl"


def _append_event(event: dict[str, Any], event_log_path: str | Path | None = None) -> Path:
    path = Path(event_log_path) if event_log_path is not None else _default_event_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")
    return path


def read_live_events(
    limit: int = 100,
    event_log_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read logged live signal/order events from JSONL (newest first)."""
    if limit <= 0:
        return []
    path = Path(event_log_path) if event_log_path is not None else _default_event_log_path()
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
    return list(reversed(events[-limit:]))


def step_once(
    spec: LiveDeploymentSpec,
    client: OpenAlgoClient | None,
    risk_state: LiveRiskState,
    bars_df: pd.DataFrame | None = None,
    webhook_url: str = "",
    event_log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run one evaluation -> risk-guard -> alert/order -> JSONL audit cycle."""
    if bars_df is not None:
        df = bars_df
    elif client is not None:
        df = client.fetch_recent_bars(
            symbol=spec.symbol,
            exchange=spec.exchange,
            interval=spec.interval,
            lookback_days=spec.lookback_days,
        )
    else:
        df = pd.DataFrame()

    signal = evaluate_strategy_on_bars(
        spec.strategy_cfg,
        df,
        initial_capital=spec.initial_capital,
    )
    risk = evaluate_pretrade_risk(
        signal.get("action", "HOLD"),
        signal.get("bar_time", ""),
        spec.risk_config,
        risk_state,
        is_exit=bool(signal.get("is_exit", False)),
    )

    mode = spec.risk_config.execution_mode
    order_result: dict[str, Any] | None = None
    order_placed = False

    if risk.allowed:
        if signal.get("bar_time"):
            risk_state.last_signal_bar_ts = str(signal["bar_time"])

        if mode == "alert_only":
            order_result = {
                "status": "skipped",
                "mode": "alert_only",
                "reason": "Alert-only mode (no order placed)",
            }
        elif mode in ("sandbox", "live"):
            if client is None:
                order_result = {
                    "status": "error",
                    "error": "OpenAlgoClient is required for sandbox/live execution.",
                }
            else:
                if mode == "sandbox":
                    az = client.analyzer_status()
                    az_data = az.get("data") if isinstance(az.get("data"), dict) else az
                    az_active = bool(
                        az.get("status") != "error"
                        and (
                            az.get("analyzer_active")
                            or az_data.get("analyzer_active")
                            or az_data.get("analyze_mode")
                            or str(az_data.get("mode", "")).lower() in ("analyze", "sandbox")
                        )
                    )
                    if not az_active:
                        err_detail = az.get("error") or "OpenAlgo Analyzer (sandbox) is not active."
                        order_result = {
                            "status": "error",
                            "error": f"Sandbox guard blocked order: {err_detail}",
                            "analyzer": az,
                        }

                if order_result is None:
                    order_result = client.place_order(
                        symbol=spec.symbol,
                        exchange=spec.exchange,
                        action=signal["action"],
                        quantity=int(spec.quantity),
                        product=spec.product,
                        pricetype=spec.pricetype,
                        strategy=spec.strategy_name,
                    )
                    if isinstance(order_result, dict) and order_result.get("status") != "error":
                        order_placed = True
                        risk_state.trades_today += 1
                        risk_state.last_order_monotonic = time.monotonic()
                        if signal.get("is_exit"):
                            risk_state.open_positions = max(0, risk_state.open_positions - 1)
                        else:
                            risk_state.open_positions += 1

    event = format_alert_payload(spec, signal, risk, order_result=order_result)
    event["order_placed"] = order_placed

    clean_webhook = str(webhook_url or "").strip()
    if clean_webhook and risk.allowed:
        try:
            resp = requests.post(clean_webhook, json=event, timeout=5.0)
            event["webhook_status"] = f"http_{getattr(resp, 'status_code', 200)}"
        except Exception as exc:  # noqa: BLE001
            event["webhook_status"] = f"error: {exc}"

    _append_event(event, event_log_path=event_log_path)
    return event


def _load_preset_arg(preset_arg: str) -> dict[str, Any]:
    if preset_arg in STARTER_TEMPLATES:
        return validate_preset(get_template(preset_arg))
    candidate = Path(preset_arg)
    if candidate.is_file():
        with open(candidate, "r", encoding="utf-8") as fh:
            return validate_preset(json.load(fh))
    return validate_preset(load_strategy_raw(preset_arg))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m quantdesk.live.signal_runner",
        description="QuantDesk OpenAlgo v1 live signal evaluator & order runner.",
    )
    parser.add_argument("--preset", required=True, help="Starter template name, JSON path, or saved preset name.")
    parser.add_argument("--symbol", required=True, help="Trading symbol (e.g. RELIANCE).")
    parser.add_argument("--exchange", default="NSE", help="Exchange code (default: NSE).")
    parser.add_argument("--interval", default="15m", help="Bar interval (default: 15m).")
    parser.add_argument(
        "--mode",
        choices=("alert_only", "sandbox", "live"),
        default="alert_only",
        help="Execution mode (default: alert_only).",
    )
    parser.add_argument("--quantity", type=int, default=1, help="Order quantity (default: 1).")
    parser.add_argument("--product", default="MIS", choices=("MIS", "CNC", "NRML"), help="Order product.")
    parser.add_argument("--host-url", default="", help="OpenAlgo host URL (default: env or http://127.0.0.1:5000).")
    parser.add_argument("--webhook-url", default="", help="Optional webhook URL for signal alerts.")
    parser.add_argument("--daily-loss-limit", type=float, default=5000.0)
    parser.add_argument("--max-trades", type=int, default=10)
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--cooldown-seconds", type=int, default=60)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--live-confirmed", action="store_true", help="Required explicit opt-in for --mode live.")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    cfg_dict = _load_preset_arg(args.preset)
    host_url = args.host_url or os.getenv("OPENALGO_HOST_URL", DEFAULT_HOST_URL)
    api_key = os.getenv("OPENALGO_API_KEY", "")

    client = OpenAlgoClient(host_url=host_url, api_key=api_key)
    spec = LiveDeploymentSpec(
        strategy_cfg=cfg_dict,
        symbol=args.symbol,
        exchange=args.exchange,
        interval=args.interval,
        quantity=max(1, int(args.quantity)),
        product=args.product,
        risk_config=LiveRiskConfig(
            execution_mode=args.mode,
            daily_loss_limit=args.daily_loss_limit,
            max_trades_per_day=args.max_trades,
            max_open_positions=args.max_positions,
            cooldown_seconds=args.cooldown_seconds,
            live_confirmed=bool(args.live_confirmed),
        ),
    )
    state = LiveRiskState()

    while True:
        event = step_once(spec, client, state, webhook_url=args.webhook_url)
        print(json.dumps(event, default=str), flush=True)
        if args.once:
            break
        time.sleep(max(1.0, float(args.poll_seconds)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
