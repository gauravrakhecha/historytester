"""
quantdesk.integrations.openalgo_blueprint — headless backtest runner + optional Flask Blueprint.

Strictly decoupled from Flask and OpenAlgo at import time:
- ``run_headless_backtest`` validates presets with ``validate_preset``, builds an OHLCV
  DataFrame from JSON records, runs ``engine.run_single``, enriches via
  ``build_terminal_payload``, and returns a JSON-safe summary dict.
- ``create_openalgo_blueprint`` lazy-imports ``flask`` only when invoked (raising a clear
  ``ImportError`` if Flask is not installed) and exposes ``GET /health`` and
  ``POST /api/backtest``.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from numbers import Real
from typing import Any

import pandas as pd

from quantdesk import __version__
from quantdesk.backtest import engine, metrics
from quantdesk.costs import india
from quantdesk.data.loader import normalize_timestamps
from quantdesk.strategy.codegen import build_strategy_code
from quantdesk.strategy.models import OHLCV, StrategyConfig, safe_name, selectable_columns
from quantdesk.strategy.validation import PresetValidationError, validate_preset
from quantdesk.viz.openalgo_terminal import build_terminal_payload

_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "Timestamp": ("Timestamp", "timestamp", "time", "datetime", "date"),
    "Open": ("Open", "open"),
    "High": ("High", "high"),
    "Low": ("Low", "low"),
    "Close": ("Close", "close"),
    "Volume": ("Volume", "volume"),
    "OI": ("OI", "oi"),
    "Dividend": ("Dividend", "dividend"),
    "Rights_Premium": ("Rights_Premium", "rights_premium"),
}


def _finite_float(val: Any, default: float | None = 0.0) -> float | None:
    if val is None or isinstance(val, bool):
        return default
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return num


def _validate_nonneg_float(val: Any, name: str, *, strictly_positive: bool = False) -> float:
    if isinstance(val, bool) or not isinstance(val, Real):
        raise ValueError(f"{name} must be a finite number, got {val!r}")
    out = float(val)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite, got {val!r}")
    if strictly_positive and out <= 0:
        raise ValueError(f"{name} must be > 0, got {val!r}")
    if not strictly_positive and out < 0:
        raise ValueError(f"{name} must be >= 0, got {val!r}")
    return out


def _extract_record_field(rec: Mapping[str, Any], canonical: str) -> Any:
    for alias in _KEY_ALIASES.get(canonical, (canonical,)):
        if alias in rec:
            return rec[alias]
    return None


def _records_to_ohlcv_df(ohlcv_records: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert a list of OHLCV dicts into a tz-naive IST DatetimeIndex DataFrame."""
    if not isinstance(ohlcv_records, list) or not ohlcv_records:
        raise ValueError("ohlcv_records must be a non-empty list of bar dicts.")

    rows: list[dict[str, Any]] = []
    for idx, rec in enumerate(ohlcv_records):
        if not isinstance(rec, Mapping):
            raise ValueError(f"ohlcv_records[{idx}] must be a mapping/dict.")
        ts = _extract_record_field(rec, "Timestamp")
        o = _extract_record_field(rec, "Open")
        h = _extract_record_field(rec, "High")
        l = _extract_record_field(rec, "Low")
        c = _extract_record_field(rec, "Close")
        if ts is None or o is None or h is None or l is None or c is None:
            raise ValueError(
                f"ohlcv_records[{idx}] must contain Timestamp, Open, High, Low, Close."
            )
        v = _extract_record_field(rec, "Volume")
        row: dict[str, Any] = {
            "Timestamp": ts,
            "Open": o,
            "High": h,
            "Low": l,
            "Close": c,
            "Volume": 0.0 if v is None else v,
        }
        for opt in ("OI", "Dividend", "Rights_Premium"):
            oval = _extract_record_field(rec, opt)
            if oval is not None:
                row[opt] = oval
        rows.append(row)

    df = pd.DataFrame(rows)
    if pd.api.types.is_numeric_dtype(df["Timestamp"]):
        df["Timestamp"] = (
            pd.to_datetime(pd.to_numeric(df["Timestamp"], errors="coerce"), unit="s", utc=True)
            .dt.tz_convert("Asia/Kolkata")
            .dt.tz_localize(None)
        )
    else:
        df["Timestamp"] = normalize_timestamps(df["Timestamp"])

    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Volume"] = df["Volume"].fillna(0.0)

    df = df.dropna(subset=["Timestamp", "Open", "High", "Low", "Close"])
    df = df.sort_values("Timestamp").drop_duplicates(subset=["Timestamp"], keep="last")
    if len(df) < 2:
        raise ValueError("At least 2 valid OHLCV bars are required to run a backtest.")
    return df.set_index("Timestamp")


def _extract_metrics_summary(
    stats: Any,
    terminal_meta: dict[str, Any],
    trades_df: pd.DataFrame,
    *,
    initial_capital: float,
    segment: str | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "return_pct": _finite_float(metrics.safe_stat(stats, "Return [%]", 0.0), 0.0),
        "annualized_return_pct": _finite_float(metrics.annualized_return(stats), 0.0),
        "buy_hold_return_pct": _finite_float(metrics.safe_stat(stats, "Buy & Hold Return [%]", 0.0), 0.0),
        "max_drawdown_pct": _finite_float(metrics.safe_stat(stats, "Max. Drawdown [%]", 0.0), 0.0),
        "avg_drawdown_pct": _finite_float(metrics.safe_stat(stats, "Avg. Drawdown [%]", 0.0), 0.0),
        "sharpe_ratio": _finite_float(metrics.safe_stat(stats, "Sharpe Ratio", 0.0), 0.0),
        "sortino_ratio": _finite_float(metrics.safe_stat(stats, "Sortino Ratio", 0.0), 0.0),
        "calmar_ratio": _finite_float(metrics.safe_stat(stats, "Calmar Ratio", 0.0), 0.0),
        "sqn": _finite_float(metrics.safe_stat(stats, "SQN", 0.0), 0.0),
        "profit_factor": _finite_float(metrics.safe_stat(stats, "Profit Factor", 0.0), 0.0),
        "win_rate_pct": _finite_float(metrics.safe_stat(stats, "Win Rate [%]", 0.0), 0.0),
        "exposure_time_pct": _finite_float(metrics.safe_stat(stats, "Exposure Time [%]", 0.0), 0.0),
        "num_trades": int(_finite_float(metrics.safe_stat(stats, "# Trades", 0), 0) or 0),
        "equity_final": _finite_float(metrics.safe_stat(stats, "Equity Final [$]", initial_capital), initial_capital),
        "equity_peak": _finite_float(metrics.safe_stat(stats, "Equity Peak [$]", initial_capital), initial_capital),
        "initial_capital": round(float(initial_capital), 2),
        "final_equity": _finite_float(terminal_meta.get("final_equity"), initial_capital),
        "total_return_pct": _finite_float(terminal_meta.get("total_return_pct"), 0.0),
        "trade_count": int(terminal_meta.get("trade_count", 0) or 0),
    }
    if segment and not trades_df.empty:
        charges = india.charges_for_trades(trades_df, segment)
        summary["total_charges"] = _finite_float(charges.get("total"), 0.0)
    return summary


def run_headless_backtest(
    preset: dict[str, Any],
    ohlcv_records: list[dict[str, Any]],
    *,
    initial_capital: float = 100000.0,
    commission: float = 0.0005,
    segment: str | None = None,
    slippage: float = 0.0,
) -> dict[str, Any]:
    """Validate ``preset``, run ``engine.run_single`` on ``ohlcv_records``, and return JSON-safe results."""
    validated_preset = validate_preset(preset)
    init_cap = _validate_nonneg_float(initial_capital, "initial_capital", strictly_positive=True)
    comm = _validate_nonneg_float(commission, "commission")
    slip = _validate_nonneg_float(slippage, "slippage")

    df = _records_to_ohlcv_df(ohlcv_records)
    cfg = StrategyConfig.from_dict(validated_preset)
    code = build_strategy_code(cfg)

    stats, _ = engine.run_single(
        df,
        code,
        init_cap,
        comm,
        1.0,
        segment=segment,
        slippage=slip,
    )

    raw_trades = stats.get("_trades") if hasattr(stats, "get") else None
    trades_df = raw_trades.copy() if isinstance(raw_trades, pd.DataFrame) else pd.DataFrame()
    strat_instance = stats.get("_strategy") if hasattr(stats, "get") else None

    out_df = df.copy()
    out_df["Buy_Signal"] = 0
    out_df["Sell_Signal"] = 0
    if not trades_df.empty:
        buy_col = out_df.columns.get_loc("Buy_Signal")
        sell_col = out_df.columns.get_loc("Sell_Signal")
        for _, tr in trades_df.iterrows():
            eb = int(tr["EntryBar"])
            xb = int(tr["ExitBar"])
            if 0 <= eb < len(out_df):
                out_df.iloc[eb, buy_col] = 1
            if 0 <= xb < len(out_df):
                out_df.iloc[xb, sell_col] = 1

    if strat_instance is not None:
        for var_name in [c for c in selectable_columns(cfg.indicators) if c not in OHLCV]:
            attr = safe_name(var_name)
            if hasattr(strat_instance, attr):
                arr = getattr(strat_instance, attr)
                if hasattr(arr, "__len__") and len(arr) == len(out_df):
                    out_df[var_name] = list(arr)

    terminal = build_terminal_payload(
        out_df,
        stats=stats,
        trades=trades_df,
        indicators=cfg.indicators,
        initial_capital=init_cap,
    )

    metrics_summary = _extract_metrics_summary(
        stats,
        terminal["meta"],
        trades_df,
        initial_capital=init_cap,
        segment=segment,
    )

    return {
        "metrics": metrics_summary,
        "trades": terminal["trades_list"],
        "equity": terminal["equity"],
        "drawdown": terminal["drawdown"],
        "code": code,
        "meta": terminal["meta"],
    }


def create_openalgo_blueprint(url_prefix: str = "/quantdesk"):
    """Create a Flask Blueprint exposing ``GET /health`` and ``POST /api/backtest``."""
    try:
        from flask import Blueprint, jsonify, request
    except ImportError as exc:
        raise ImportError(
            "Flask is required to create the OpenAlgo Flask Blueprint. "
            "Install it with: pip install quantdesk[openalgo]"
        ) from exc

    bp = Blueprint("quantdesk", __name__, url_prefix=url_prefix)

    @bp.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "quantdesk", "version": __version__}), 200

    @bp.route("/api/backtest", methods=["POST"])
    def api_backtest():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"status": "error", "error": "Request body must be a JSON object."}), 400

        preset = body.get("preset")
        records = body.get("ohlcv_records") if "ohlcv_records" in body else body.get("ohlcv")
        try:
            result = run_headless_backtest(
                preset,
                records,
                initial_capital=body.get("initial_capital", 100000.0),
                commission=body.get("commission", 0.0005),
                segment=body.get("segment"),
                slippage=body.get("slippage", 0.0),
            )
        except (PresetValidationError, ValueError, TypeError) as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"status": "error", "error": str(exc)}), 500

        return jsonify({"status": "success", **result}), 200

    return bp
