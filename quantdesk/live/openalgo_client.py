"""
quantdesk.live.openalgo_client — Strict OpenAlgo REST v1 client (no ``openalgo`` SDK import).

Every endpoint enforces strict Marshmallow schemas (HTTP 400 on extra keys), so every
request payload contains ONLY the exact documented keys for that endpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from quantdesk.brokers.openalgo import (
    DEFAULT_HOST_URL,
    _clean_str,
    _normalize_interval,
    normalize_host_url,
    normalize_openalgo_exchange,
)
from quantdesk.core.logging import get_logger

log = get_logger("live.openalgo_client")

_IST = ZoneInfo("Asia/Kolkata")
_VALID_ACTIONS = frozenset({"BUY", "SELL"})
_VALID_PRICETYPES = frozenset({"MARKET", "LIMIT", "SL", "SL-M"})
_VALID_PRODUCTS = frozenset({"MIS", "CNC", "NRML"})
_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


class OpenAlgoClientError(ValueError):
    """Raised on invalid OpenAlgo client arguments or order parameters."""


def _empty_bars_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=_OHLCV_COLS)
    df.index = pd.DatetimeIndex([], name="Timestamp")
    return df


def _validate_positive_int(val: Any, field: str) -> int:
    if isinstance(val, bool):
        raise OpenAlgoClientError(f"{field} must be an integer >= 1, got {val!r}")
    if isinstance(val, float):
        if not val.is_integer():
            raise OpenAlgoClientError(f"{field} must be a whole integer >= 1, got {val!r}")
        num = int(val)
    else:
        try:
            num = int(val)
        except (TypeError, ValueError) as exc:
            raise OpenAlgoClientError(f"{field} must be an integer >= 1, got {val!r}") from exc
    if num < 1:
        raise OpenAlgoClientError(f"{field} must be >= 1, got {num}")
    return num


def _validate_finite_float(val: Any, field: str) -> float:
    if isinstance(val, bool):
        raise OpenAlgoClientError(f"{field} must be a finite number, got {val!r}")
    try:
        num = float(val)
    except (TypeError, ValueError) as exc:
        raise OpenAlgoClientError(f"{field} must be a number, got {val!r}") from exc
    if not pd.notna(num) or abs(num) == float("inf") or num <= 0:
        raise OpenAlgoClientError(f"{field} must be a finite number > 0, got {val!r}")
    return num


class OpenAlgoClient:
    """Thin, strict REST v1 client for OpenAlgo Web/Desktop."""

    def __init__(
        self,
        host_url: str = DEFAULT_HOST_URL,
        api_key: str = "",
        timeout: float = 10.0,
        post_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.host_url: str = normalize_host_url(host_url)
        self.api_key: str = _clean_str(api_key)
        self.timeout: float = float(timeout)
        self._post_fn: Callable[..., Any] = post_fn or requests.post

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST strict JSON payload to ``endpoint`` and normalize errors into a dict."""
        if not self.api_key:
            return {"status": "error", "error": "OpenAlgo API key is required."}

        url = f"{self.host_url}{endpoint}"
        try:
            resp = self._post_fn(url, json=payload, timeout=self.timeout)
        except requests.exceptions.Timeout:
            return {"status": "error", "error": f"Timeout calling {endpoint} on {self.host_url}."}
        except requests.exceptions.ConnectionError:
            return {"status": "error", "error": f"Cannot connect to OpenAlgo at {self.host_url}."}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"Request failed for {endpoint}: {exc}"}

        status_code = getattr(resp, "status_code", 200)
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"Invalid JSON from {endpoint} (HTTP {status_code}): {exc}",
                "http_status": status_code,
            }

        if not isinstance(body, dict):
            if status_code >= 400:
                return {"status": "error", "error": f"OpenAlgo HTTP {status_code}", "data": body}
            return {"status": "success", "data": body}

        if status_code >= 400 or body.get("status") == "error":
            err = _clean_str(body.get("error") or body.get("message")) or f"OpenAlgo HTTP {status_code}"
            out = dict(body)
            out["status"] = "error"
            out["error"] = err
            return out

        return body

    # --- Account & telemetry (graceful on offline / error) ------------------
    def _auth_only_post(self, endpoint: str) -> dict[str, Any]:
        return self._post_json(endpoint, {"apikey": self.api_key})

    def funds(self) -> dict[str, Any]:
        return self._auth_only_post("/api/v1/funds")

    def orderbook(self) -> dict[str, Any]:
        return self._auth_only_post("/api/v1/orderbook")

    def positionbook(self) -> dict[str, Any]:
        return self._auth_only_post("/api/v1/positionbook")

    def tradebook(self) -> dict[str, Any]:
        return self._auth_only_post("/api/v1/tradebook")

    def analyzer_status(self) -> dict[str, Any]:
        res = self._auth_only_post("/api/v1/analyzer")
        if res.get("status") == "error":
            return {
                **res,
                "analyzer_active": False,
                "mode": "unknown",
            }
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        if "analyze_mode" in data and data["analyze_mode"] is not None:
            active = bool(data["analyze_mode"])
        elif "analyzer_active" in data and data["analyzer_active"] is not None:
            active = bool(data["analyzer_active"])
        else:
            active = _clean_str(data.get("mode")).lower() in ("analyze", "sandbox")
        mode = _clean_str(data.get("mode")) or ("analyze" if active else "live")
        out = dict(res)
        out.setdefault("analyzer_active", active)
        out.setdefault("mode", mode)
        return out

    # --- Order management ---------------------------------------------------
    def place_order(
        self,
        symbol: str,
        exchange: str,
        action: str,
        quantity: int,
        product: str = "MIS",
        pricetype: str = "MARKET",
        price: float | None = None,
        trigger_price: float | None = None,
        strategy: str = "QuantDesk",
        disclosed_quantity: int | None = None,
    ) -> dict[str, Any]:
        """Place an order via ``POST /api/v1/placeorder`` with strict Marshmallow keys."""
        clean_symbol = _clean_str(symbol)
        if not clean_symbol:
            raise OpenAlgoClientError("symbol must be a non-empty string")

        clean_action = _clean_str(action).upper()
        if clean_action not in _VALID_ACTIONS:
            raise OpenAlgoClientError(
                f"Invalid action {action!r}; expected one of {sorted(_VALID_ACTIONS)}"
            )

        clean_pricetype = _clean_str(pricetype).upper()
        if clean_pricetype not in _VALID_PRICETYPES:
            raise OpenAlgoClientError(
                f"Invalid pricetype {pricetype!r}; expected one of {sorted(_VALID_PRICETYPES)}"
            )

        clean_product = _clean_str(product).upper()
        if clean_product not in _VALID_PRODUCTS:
            raise OpenAlgoClientError(
                f"Invalid product {product!r}; expected one of {sorted(_VALID_PRODUCTS)}"
            )

        qty = _validate_positive_int(quantity, "quantity")
        clean_exchange = normalize_openalgo_exchange(exchange)
        clean_strategy = _clean_str(strategy) or "QuantDesk"

        if clean_pricetype in ("LIMIT", "SL") and price is None:
            raise OpenAlgoClientError(f"price is required for {clean_pricetype} orders")
        if clean_pricetype in ("SL", "SL-M") and trigger_price is None:
            raise OpenAlgoClientError(f"trigger_price is required for {clean_pricetype} orders")

        payload: dict[str, Any] = {
            "apikey": self.api_key,
            "strategy": clean_strategy,
            "symbol": clean_symbol,
            "action": clean_action,
            "exchange": clean_exchange,
            "pricetype": clean_pricetype,
            "product": clean_product,
            "quantity": str(qty),
        }

        if clean_pricetype in ("LIMIT", "SL", "SL-M"):
            if price is not None:
                payload["price"] = str(_validate_finite_float(price, "price"))
            if trigger_price is not None:
                payload["trigger_price"] = str(_validate_finite_float(trigger_price, "trigger_price"))
            if disclosed_quantity is not None:
                if isinstance(disclosed_quantity, bool):
                    raise OpenAlgoClientError("disclosed_quantity must be an integer >= 0")
                try:
                    disc = int(disclosed_quantity)
                except (TypeError, ValueError) as exc:
                    raise OpenAlgoClientError(
                        f"disclosed_quantity must be an integer >= 0, got {disclosed_quantity!r}"
                    ) from exc
                if disc < 0:
                    raise OpenAlgoClientError(f"disclosed_quantity must be >= 0, got {disc}")
                payload["disclosed_quantity"] = str(disc)

        return self._post_json("/api/v1/placeorder", payload)

    def close_position(self, strategy: str = "QuantDesk") -> dict[str, Any]:
        """Close all open positions tagged with ``strategy``."""
        return self._post_json(
            "/api/v1/closeposition",
            {"apikey": self.api_key, "strategy": _clean_str(strategy) or "QuantDesk"},
        )

    def cancel_all_orders(self, strategy: str = "QuantDesk") -> dict[str, Any]:
        """Cancel all pending orders tagged with ``strategy``."""
        return self._post_json(
            "/api/v1/cancelallorder",
            {"apikey": self.api_key, "strategy": _clean_str(strategy) or "QuantDesk"},
        )

    # --- Market history -----------------------------------------------------
    def fetch_recent_bars(
        self,
        symbol: str,
        exchange: str,
        interval: str = "15m",
        lookback_days: int = 10,
    ) -> pd.DataFrame:
        """Fetch recent OHLCV bars from ``POST /api/v1/history`` as tz-naive IST DataFrame."""
        clean_symbol = _clean_str(symbol)
        if not clean_symbol or not self.api_key:
            return _empty_bars_frame()

        days = max(1, int(lookback_days or 10))
        end_dt = datetime.now(_IST).date()
        start_dt = end_dt - timedelta(days=days)

        payload = {
            "apikey": self.api_key,
            "symbol": clean_symbol,
            "exchange": normalize_openalgo_exchange(exchange),
            "interval": _normalize_interval(interval),
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        }
        body = self._post_json("/api/v1/history", payload)
        if body.get("status") == "error":
            log.warning(
                "OpenAlgo history error for %s:%s (%s): %s",
                payload["exchange"],
                clean_symbol,
                payload["interval"],
                body.get("error", ""),
            )
            return _empty_bars_frame()

        rows = body.get("data", [])
        if not isinstance(rows, list) or not rows:
            return _empty_bars_frame()

        raw = pd.DataFrame(rows)
        if "timestamp" not in raw.columns:
            return _empty_bars_frame()

        ts_num = pd.to_numeric(raw["timestamp"], errors="coerce")
        if ts_num.notna().any():
            ts_parsed = pd.to_datetime(ts_num, unit="s", utc=True, errors="coerce")
            # Fill any non-numeric rows (if mixed) using string datetime parser
            if ts_num.isna().any():
                str_ts = pd.to_datetime(raw.loc[ts_num.isna(), "timestamp"], errors="coerce", utc=True)
                ts_parsed = ts_parsed.fillna(str_ts)
            ts_ist = ts_parsed.dt.tz_convert(_IST).dt.tz_localize(None)
        else:
            ts_parsed = pd.to_datetime(raw["timestamp"], errors="coerce")
            if getattr(ts_parsed.dt, "tz", None) is not None:
                ts_ist = ts_parsed.dt.tz_convert(_IST).dt.tz_localize(None)
            else:
                ts_ist = ts_parsed

        out = pd.DataFrame(
            {
                "Timestamp": ts_ist,
                "Open": pd.to_numeric(raw.get("open"), errors="coerce"),
                "High": pd.to_numeric(raw.get("high"), errors="coerce"),
                "Low": pd.to_numeric(raw.get("low"), errors="coerce"),
                "Close": pd.to_numeric(raw.get("close"), errors="coerce"),
                "Volume": pd.to_numeric(
                    raw["volume"] if "volume" in raw.columns else 0, errors="coerce"
                ).fillna(0.0),
            }
        )
        out = (
            out.dropna(subset=["Timestamp", "Open", "High", "Low", "Close"])
            .sort_values("Timestamp")
            .drop_duplicates(subset=["Timestamp"], keep="last")
            .set_index("Timestamp")
        )
        out.index.name = "Timestamp"
        return out[_OHLCV_COLS]
