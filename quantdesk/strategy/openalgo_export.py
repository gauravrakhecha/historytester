"""
quantdesk.strategy.openalgo_export — generate standalone OpenAlgo live trading scripts.

Exports a validated ``StrategyConfig`` (or preset dict) as a clean, self-contained,
runnable Python script that:
- Reads ``OPENALGO_API_KEY`` from ``os.getenv("OPENALGO_API_KEY")`` (never embeds secrets).
- Fetches OHLCV history from OpenAlgo ``POST /api/v1/history`` using strict v1 schemas.
- Evaluates indicators & strategy rules (via ``quantdesk.strategy`` when installed, with a
  built-in standalone pandas/numpy evaluator fallback).
- Enforces ``max_daily_loss`` and trading time window before placing orders.
- Supports ``--mode alert|sandbox|live`` and ``--once`` CLI flags.
- Validates generated source via ``ast.parse()`` before returning.
"""
from __future__ import annotations

import ast
import json
import math
from numbers import Real
from typing import Any

from quantdesk.brokers.openalgo import normalize_host_url, normalize_openalgo_exchange
from quantdesk.strategy.models import StrategyConfig
from quantdesk.strategy.validation import validate_preset

_VALID_PRODUCTS = frozenset({"MIS", "CNC", "NRML"})


def _require_nonempty_str(val: Any, name: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{name} must be a non-empty string, got {val!r}")
    return val.strip()


def _require_pos_int(val: Any, name: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int) or val < 1:
        raise ValueError(f"{name} must be an integer >= 1, got {val!r}")
    return int(val)


def _require_nonneg_float(val: Any, name: str) -> float:
    if isinstance(val, bool) or not isinstance(val, Real):
        raise ValueError(f"{name} must be a finite number >= 0, got {val!r}")
    out = float(val)
    if not math.isfinite(out) or out < 0:
        raise ValueError(f"{name} must be a finite number >= 0, got {val!r}")
    return out


_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""
Auto-generated QuantDesk -> OpenAlgo standalone trading runner.

Strategy : {strategy_name}
Symbol   : {exchange}:{symbol} ({interval})
Product  : {product} | Quantity: {quantity} | Max Daily Loss: INR {max_daily_loss:.2f}

Usage:
    set OPENALGO_API_KEY=your_api_key   (or export OPENALGO_API_KEY=...)
    python bot.py --mode alert --once
    python bot.py --mode sandbox --once
    python bot.py --mode live
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import logging
import math
import os
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

IST = ZoneInfo("Asia/Kolkata")

STRATEGY_NAME = {strategy_name_repr}
SYMBOL = {symbol_repr}
EXCHANGE = {exchange_repr}
INTERVAL = {interval_repr}
QUANTITY = {quantity_val}
PRODUCT = {product_repr}
DEFAULT_HOST_URL = {host_url_repr}
MAX_DAILY_LOSS = {max_daily_loss_val}

STRATEGY_CONFIG_JSON = {strategy_json_repr}
STRATEGY_CONFIG: dict[str, Any] = json.loads(STRATEGY_CONFIG_JSON)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("quantdesk.openalgo_bot")


def get_api_key() -> str:
    """Read OpenAlgo API key from environment (never hardcoded)."""
    return (os.getenv("OPENALGO_API_KEY") or "").strip()


def normalize_host(host_url: str) -> str:
    raw = (host_url or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_HOST_URL
    if not raw.lower().startswith(("http://", "https://")):
        return f"http://{{raw}}"
    return raw


def post_openalgo(
    host_url: str,
    endpoint: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST strict JSON payload to an OpenAlgo v1 endpoint."""
    url = f"{{normalize_host(host_url)}}{{endpoint}}"
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(str(data.get("message") or data.get("error") or "OpenAlgo API error"))
    return data if isinstance(data, dict) else {{"data": data}}


def fetch_history(
    host_url: str,
    api_key: str,
    symbol: str,
    exchange: str,
    interval: str,
    lookback_days: int = 45,
) -> pd.DataFrame:
    """Fetch OHLCV candles from OpenAlgo ``/api/v1/history`` normalized to tz-naive IST."""
    end_dt = datetime.now(IST).date()
    start_dt = end_dt - timedelta(days=max(1, int(lookback_days)))
    payload = {{
        "apikey": api_key,
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    }}
    body = post_openalgo(host_url, "/api/v1/history", payload, timeout=15.0)
    rows = body.get("data", [])
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    if "timestamp" not in raw.columns:
        return pd.DataFrame()
    ts_epoch = pd.to_numeric(raw["timestamp"], errors="coerce")
    ts_ist = (
        pd.to_datetime(ts_epoch, unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
    )
    df = pd.DataFrame(
        {{
            "Timestamp": ts_ist,
            "Open": pd.to_numeric(raw["open"], errors="coerce"),
            "High": pd.to_numeric(raw["high"], errors="coerce"),
            "Low": pd.to_numeric(raw["low"], errors="coerce"),
            "Close": pd.to_numeric(raw["close"], errors="coerce"),
            "Volume": pd.to_numeric(raw.get("volume", 0), errors="coerce").fillna(0.0),
        }}
    )
    df = df.dropna(subset=["Timestamp", "Open", "High", "Low", "Close"])
    df = df.sort_values("Timestamp").drop_duplicates(subset=["Timestamp"], keep="last")
    return df.set_index("Timestamp")


def check_analyzer_active(host_url: str, api_key: str) -> bool:
    """Verify OpenAlgo analyzer/sandbox mode is active before sandbox execution."""
    body = post_openalgo(host_url, "/api/v1/analyzer", {{"apikey": api_key}}, timeout=8.0)
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if "analyze_mode" in data and data["analyze_mode"] is not None:
        return bool(data["analyze_mode"])
    if "analyzer_active" in data and data["analyzer_active"] is not None:
        return bool(data["analyzer_active"])
    return str(data.get("mode", "")).strip().lower() in ("analyze", "sandbox")


def fetch_daily_pnl(host_url: str, api_key: str) -> float:
    """Query ``/api/v1/funds`` for realized + unrealized M2M P&L (0.0 if unavailable)."""
    try:
        body = post_openalgo(host_url, "/api/v1/funds", {{"apikey": api_key}}, timeout=8.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not query /api/v1/funds for daily loss check: %s", exc)
        return 0.0
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    total = 0.0
    for key in ("m2mrealized", "m2munrealized"):
        raw = data.get(key)
        if raw is not None:
            try:
                val = float(raw)
                if math.isfinite(val):
                    total += val
            except (TypeError, ValueError):
                pass
    return total


def within_time_window(
    now_ist: datetime,
    cfg: dict[str, Any],
    *,
    ignore_window: bool = False,
) -> tuple[bool, str]:
    """Check strategy ``entry_time_filter`` and standard NSE/IST session bounds."""
    if ignore_window:
        return True, "window ignored"
    hm = now_ist.strftime("%H:%M")
    rm = cfg.get("risk_management") or {{}}
    if rm.get("entry_time_filter"):
        start_hm = str(rm.get("entry_start_time") or "09:30")
        end_hm = str(rm.get("entry_end_time") or "14:45")
        if not (start_hm <= hm <= end_hm):
            return False, f"outside configured entry window {{start_hm}}-{{end_hm}} (now={{hm}})"
    if not ("09:15" <= hm <= "15:29"):
        return False, f"outside IST market session 09:15-15:29 (now={{hm}})"
    return True, f"within window ({{hm}})"


def check_daily_loss(daily_pnl: float, max_daily_loss: float) -> tuple[bool, str]:
    """Halt new entries if realized/unrealized daily loss breaches ``max_daily_loss``."""
    if max_daily_loss > 0 and daily_pnl <= -abs(max_daily_loss):
        return False, f"daily loss limit breached: pnl={{daily_pnl:.2f}} <= -{{abs(max_daily_loss):.2f}}"
    return True, "daily loss ok"


def _compute_embedded_indicators(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, pd.Series]:
    """Minimal standalone indicator evaluator when ``quantdesk`` is not installed."""
    cols: dict[str, pd.Series] = {{
        c: df[c].astype(float) for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns
    }}
    for ind in cfg.get("indicators") or []:
        name = ind["name"]
        itype = ind.get("type", "")
        src_name = ind.get("source", "Close")
        src = cols.get(src_name, cols["Close"])
        params = ind.get("params") or {{}}
        period = int(params.get("period", 14) or 14)

        if itype == "SMA":
            cols[name] = src.rolling(period).mean()
        elif itype == "EMA":
            cols[name] = src.ewm(span=period, adjust=False).mean()
        elif itype == "WMA":
            weights = np.arange(1, period + 1, dtype=float)
            cols[name] = src.rolling(period).apply(lambda x: float(np.dot(x, weights) / weights.sum()), raw=True)
        elif itype == "RSI":
            delta = src.diff()
            gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
            loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
            rs = gain / loss.replace(0.0, np.nan)
            cols[name] = 100.0 - (100.0 / (1.0 + rs))
        elif itype == "MACD":
            fast = int(params.get("fast", 12) or 12)
            slow = int(params.get("slow", 26) or 26)
            sig = int(params.get("sign", 9) or 9)
            line = src.ewm(span=fast, adjust=False).mean() - src.ewm(span=slow, adjust=False).mean()
            cols[f"{{name}}_line"] = line
            cols[f"{{name}}_signal"] = line.ewm(span=sig, adjust=False).mean()
        elif itype == "Bollinger Bands":
            std_dev = float(params.get("std_dev", 2.0) or 2.0)
            mid = src.rolling(period).mean()
            sd = src.rolling(period).std(ddof=0)
            cols[f"{{name}}_high"] = mid + std_dev * sd
            cols[f"{{name}}_low"] = mid - std_dev * sd
        elif itype == "ATR":
            prev_c = cols["Close"].shift(1)
            tr = pd.concat(
                [cols["High"] - cols["Low"], (cols["High"] - prev_c).abs(), (cols["Low"] - prev_c).abs()],
                axis=1,
            ).max(axis=1)
            cols[name] = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        elif itype == "Ratio to SMA":
            cols[name] = src / src.rolling(period).mean()
        elif itype == "Z-Score":
            cols[name] = (src - src.rolling(period).mean()) / src.rolling(period).std(ddof=0)
        elif itype == "Return (n-bar)":
            cols[name] = src / src.shift(period) - 1.0
        elif itype == "Bar Shift":
            periods = int(params.get("periods", 1) or 1)
            cols[name] = src.shift(periods)
    return cols


def _eval_rule_pair(s1: pd.Series, op: str, s2: pd.Series | float, idx: int) -> bool:
    if idx < 0 or idx >= len(s1):
        return False
    v1_cur = float(s1.iloc[idx])
    v2_cur = float(s2) if isinstance(s2, (int, float)) else float(s2.iloc[idx])
    if not (math.isfinite(v1_cur) and math.isfinite(v2_cur)):
        return False
    if op in ("Crosses Above", "Crosses Below"):
        if idx < 1:
            return False
        v1_prev = float(s1.iloc[idx - 1])
        v2_prev = float(s2) if isinstance(s2, (int, float)) else float(s2.iloc[idx - 1])
        if not (math.isfinite(v1_prev) and math.isfinite(v2_prev)):
            return False
        if op == "Crosses Above":
            return v1_prev <= v2_prev and v1_cur > v2_cur
        return v1_prev >= v2_prev and v1_cur < v2_cur
    if op == ">":
        return v1_cur > v2_cur
    if op == "<":
        return v1_cur < v2_cur
    if op == "==":
        return v1_cur == v2_cur
    if op == ">=":
        return v1_cur >= v2_cur
    if op == "<=":
        return v1_cur <= v2_cur
    return False


def _eval_rules_embedded(rules: list[dict[str, Any]], cols: dict[str, pd.Series], n: int) -> bool:
    if not rules or n < 2:
        return False
    last_idx = n - 1
    groups: list[tuple[str, bool]] = []
    cur_gid = None
    for r in rules:
        s1 = cols.get(r["var1"])
        if s1 is None:
            res = False
        else:
            s2 = float(r["var2"]) if r.get("is_num") else cols.get(r["var2"])
            if s2 is None:
                res = False
            else:
                timing = r.get("timing", "now") or "now"
                n_bars = max(1, int(r.get("timing_bars", 1) or 1))
                if timing == "bars_ago":
                    res = _eval_rule_pair(s1, r["operator"], s2, last_idx - n_bars)
                elif timing == "within_last":
                    res = any(
                        _eval_rule_pair(s1, r["operator"], s2, last_idx - k)
                        for k in range(min(n_bars, last_idx))
                    )
                elif timing == "consecutive":
                    res = (last_idx + 1 >= n_bars) and all(
                        _eval_rule_pair(s1, r["operator"], s2, last_idx - k)
                        for k in range(n_bars)
                    )
                else:
                    res = _eval_rule_pair(s1, r["operator"], s2, last_idx)
        if r.get("negate"):
            res = not res
        gid = int(r.get("group", 0) or 0)
        joiner = "AND" if r.get("logic", "IF") == "AND" else "OR"
        if cur_gid is None or gid != cur_gid:
            groups.append((joiner, res))
            cur_gid = gid
        else:
            prev_joiner, prev_val = groups[-1]
            combined = (prev_val and res) if joiner == "AND" else (prev_val or res)
            groups[-1] = (prev_joiner, combined)
    out = groups[0][1]
    for joiner, val in groups[1:]:
        out = (out and val) if joiner == "AND" else (out or val)
    return bool(out)


def evaluate_signal(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    """Evaluate strategy rules on ``df``. Prefers ``quantdesk.strategy`` for backtester parity."""
    if df is None or len(df) < 2:
        return {{"action": "HOLD", "bar_time": None, "close": None, "reason": "insufficient bars"}}

    last_bar = len(df) - 1
    bar_time = str(df.index[-1])
    close_px = float(df["Close"].iloc[-1])

    try:
        from backtesting import Backtest
        from quantdesk.strategy.codegen import build_strategy_code
        from quantdesk.strategy.compiler import compile_strategy
        from quantdesk.strategy.models import StrategyConfig

        scfg = StrategyConfig.from_dict(cfg)
        code = build_strategy_code(scfg)
        strat_cls = compile_strategy(code)
        bt = Backtest(df, strat_cls, cash=10_000_000.0, commission=0.0, finalize_trades=False)
        stats = bt.run()
        strat = stats.get("_strategy")
        orders = list(getattr(strat, "orders", []) or []) if strat is not None else []
        market_orders = [o for o in orders if not getattr(o, "is_contingent", False)]
        action = "HOLD"
        reason = "no signal on latest bar"
        if market_orders:
            last_order = market_orders[-1]
            action = "BUY" if float(last_order.size) > 0 else "SELL"
            reason = f"quantdesk order signal on bar {{last_bar}}"
        else:
            trades = stats.get("_trades")
            if isinstance(trades, pd.DataFrame) and not trades.empty:
                if (trades["EntryBar"] == last_bar).any():
                    row = trades[trades["EntryBar"] == last_bar].iloc[-1]
                    action = "BUY" if float(row["Size"]) > 0 else "SELL"
                    reason = f"quantdesk entry signal on bar {{last_bar}}"
                elif (trades["ExitBar"] == last_bar).any():
                    row = trades[trades["ExitBar"] == last_bar].iloc[-1]
                    action = "SELL" if float(row["Size"]) > 0 else "BUY"
                    reason = f"quantdesk exit signal on bar {{last_bar}}"
        return {{"action": action, "bar_time": bar_time, "close": close_px, "reason": reason}}
    except ImportError:
        pass

    cols = _compute_embedded_indicators(df, cfg)
    n = len(df)
    if _eval_rules_embedded(cfg.get("no_trade_rules") or [], cols, n):
        return {{"action": "HOLD", "bar_time": bar_time, "close": close_px, "reason": "no-trade filter active"}}
    if _eval_rules_embedded(cfg.get("buy_rules") or [], cols, n):
        return {{"action": "BUY", "bar_time": bar_time, "close": close_px, "reason": "buy rule triggered"}}
    if _eval_rules_embedded(cfg.get("sell_rules") or [], cols, n):
        return {{"action": "SELL", "bar_time": bar_time, "close": close_px, "reason": "sell rule triggered"}}
    return {{"action": "HOLD", "bar_time": bar_time, "close": close_px, "reason": "no signal"}}


def place_order(
    host_url: str,
    api_key: str,
    *,
    strategy: str,
    symbol: str,
    exchange: str,
    action: str,
    quantity: int,
    product: str,
    pricetype: str = "MARKET",
) -> dict[str, Any]:
    """Place an order via OpenAlgo ``POST /api/v1/placeorder`` (strict Marshmallow keys)."""
    payload = {{
        "apikey": api_key,
        "strategy": strategy,
        "symbol": symbol,
        "action": action,
        "exchange": exchange,
        "pricetype": pricetype,
        "product": product,
        "quantity": str(int(quantity)),
    }}
    return post_openalgo(host_url, "/api/v1/placeorder", payload, timeout=10.0)


def run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    """Run one fetch -> evaluate -> risk-check -> alert/execute cycle."""
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("OPENALGO_API_KEY environment variable is required.")

    df = fetch_history(
        args.host,
        api_key,
        args.symbol,
        args.exchange,
        args.interval,
        lookback_days=args.lookback_days,
    )
    sig = evaluate_signal(df, STRATEGY_CONFIG)
    action = sig["action"]

    now_ist = datetime.now(IST)
    win_ok, win_reason = within_time_window(
        now_ist, STRATEGY_CONFIG, ignore_window=args.ignore_time_window
    )
    daily_pnl = fetch_daily_pnl(args.host, api_key)
    loss_ok, loss_reason = check_daily_loss(daily_pnl, MAX_DAILY_LOSS)

    result: dict[str, Any] = {{
        "strategy": STRATEGY_NAME,
        "symbol": args.symbol,
        "exchange": args.exchange,
        "interval": args.interval,
        "mode": args.mode,
        "signal": sig,
        "daily_pnl": daily_pnl,
        "window_ok": win_ok,
        "loss_ok": loss_ok,
        "order_sent": False,
        "order_response": None,
    }}

    if action == "HOLD":
        log.info("HOLD (%s) close=%s", sig["reason"], sig["close"])
        return result

    if not win_ok:
        log.warning("Signal %s blocked by time window: %s", action, win_reason)
        result["blocked_reason"] = win_reason
        return result

    if not loss_ok:
        log.error("Signal %s halted by daily loss guard: %s", action, loss_reason)
        result["blocked_reason"] = loss_reason
        return result

    if args.mode == "alert":
        log.info("ALERT ONLY %s %s:%s qty=%s @ %s", action, args.exchange, args.symbol, args.quantity, sig["close"])
        return result

    if args.mode == "sandbox":
        if not check_analyzer_active(args.host, api_key):
            msg = "Sandbox mode requires OpenAlgo Analyzer to be active; refusing order."
            log.error(msg)
            result["blocked_reason"] = msg
            return result

    order_resp = place_order(
        args.host,
        api_key,
        strategy=STRATEGY_NAME,
        symbol=args.symbol,
        exchange=args.exchange,
        action=action,
        quantity=args.quantity,
        product=args.product,
    )
    result["order_sent"] = True
    result["order_response"] = order_resp
    log.info("Executed %s order (%s): %s", args.mode, action, order_resp)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"OpenAlgo standalone runner for {{STRATEGY_NAME}}")
    parser.add_argument("--mode", choices=["alert", "sandbox", "live"], default="alert")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle and exit.")
    parser.add_argument("--interval-sec", type=int, default=60, help="Polling interval in seconds.")
    parser.add_argument("--lookback-days", type=int, default=45, help="History lookback days.")
    parser.add_argument("--host", default=DEFAULT_HOST_URL, help="OpenAlgo base URL.")
    parser.add_argument("--symbol", default=SYMBOL, help="Trading symbol.")
    parser.add_argument("--exchange", default=EXCHANGE, help="OpenAlgo exchange code.")
    parser.add_argument("--interval", default=INTERVAL, help="Candle interval.")
    parser.add_argument("--quantity", type=int, default=QUANTITY, help="Order quantity.")
    parser.add_argument("--product", choices=["MIS", "CNC", "NRML"], default=PRODUCT, help="Order product.")
    parser.add_argument(
        "--ignore-time-window",
        action="store_true",
        help="Evaluate outside NSE market/entry time windows (useful for testing).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    while True:
        try:
            out = run_cycle(args)
            print(json.dumps(out, default=str))
        except Exception as exc:  # noqa: BLE001
            log.error("Cycle error: %s", exc)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(1, int(args.interval_sec)))


if __name__ == "__main__":
    sys.exit(main())
'''


def generate_openalgo_script(
    cfg: StrategyConfig | dict,
    *,
    symbol: str = "RELIANCE",
    exchange: str = "NSE",
    interval: str = "15m",
    quantity: int = 1,
    product: str = "MIS",
    strategy_name: str = "QuantDesk_Bot",
    host_url: str = "http://127.0.0.1:5000",
    max_daily_loss: float = 5000.0,
) -> str:
    """Validate ``cfg`` and generate a self-contained runnable OpenAlgo Python bot script."""
    raw_cfg = cfg.to_dict() if hasattr(cfg, "to_dict") else cfg
    validated_cfg = validate_preset(raw_cfg)

    clean_symbol = _require_nonempty_str(symbol, "symbol")
    clean_exchange_raw = _require_nonempty_str(exchange, "exchange")
    clean_exchange = normalize_openalgo_exchange(clean_exchange_raw)
    clean_interval = _require_nonempty_str(interval, "interval")
    clean_qty = _require_pos_int(quantity, "quantity")
    clean_product = _require_nonempty_str(product, "product").upper()
    if clean_product not in _VALID_PRODUCTS:
        raise ValueError(f"product must be one of {sorted(_VALID_PRODUCTS)}, got {product!r}")
    clean_name = _require_nonempty_str(strategy_name, "strategy_name")
    clean_host = normalize_host_url(host_url)
    clean_loss = _require_nonneg_float(max_daily_loss, "max_daily_loss")

    cfg_json = json.dumps(validated_cfg, indent=2, ensure_ascii=True)
    script = _SCRIPT_TEMPLATE.format(
        strategy_name=clean_name,
        symbol=clean_symbol,
        exchange=clean_exchange,
        interval=clean_interval,
        product=clean_product,
        quantity=clean_qty,
        max_daily_loss=clean_loss,
        strategy_name_repr=repr(clean_name),
        symbol_repr=repr(clean_symbol),
        exchange_repr=repr(clean_exchange),
        interval_repr=repr(clean_interval),
        quantity_val=clean_qty,
        product_repr=repr(clean_product),
        host_url_repr=repr(clean_host),
        max_daily_loss_val=repr(clean_loss),
        strategy_json_repr=repr(cfg_json),
    )

    ast.parse(script)
    return script
