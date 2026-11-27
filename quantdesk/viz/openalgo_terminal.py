"""
quantdesk.viz.openalgo_terminal — Offline QuantDesk Pro Trading Terminal HTML/JS widget.

Builds JSON-safe payloads (OHLCV, volume, indicator overlays/panels, trade markers,
bar-aligned equity/drawdown series, trade list, and per-bar replay metrics) and renders
a self-contained offline HTML terminal embedding the vendored ``openalgo-charts`` IIFE
bundle with Bar-by-Bar Replay, Trade Focus/Jump with Entry/Exit/SL/TP price lines,
synced Equity & Drawdown % pane, live HUD, and CSV export.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from quantdesk.viz.openalgo_chart import (
    _finite_float,
    _index_to_unix_seconds,
    _load_bundle_js,
    _single_ts_to_unix_seconds,
    build_openalgo_payload,
)

_IST_TZ = "Asia/Kolkata"


def _resolve_trades_df(stats: Any, trades: pd.DataFrame | None) -> pd.DataFrame | None:
    """Return explicit ``trades`` when supplied, else ``stats['_trades']`` if available."""
    if trades is not None:
        return trades if isinstance(trades, pd.DataFrame) else None
    if stats is not None:
        try:
            st_trades = stats["_trades"] if "_trades" in stats else None
            if isinstance(st_trades, pd.DataFrame):
                return st_trades
        except Exception:
            pass
    return None


def _resolve_equity_df(stats: Any) -> pd.DataFrame | None:
    """Safely extract ``stats['_equity_curve']`` as a non-empty DataFrame, or ``None``."""
    if stats is None:
        return None
    try:
        eq = stats["_equity_curve"] if "_equity_curve" in stats else None
        if isinstance(eq, pd.DataFrame) and not eq.empty:
            return eq
    except Exception:
        pass
    return None


def _format_ts_ist(ts_sec: int | None, raw_val: Any = None) -> str:
    """Format a timestamp cleanly in IST (``YYYY-MM-DD HH:MM``)."""
    if raw_val is not None:
        try:
            parsed = pd.to_datetime(raw_val, errors="coerce")
            if not pd.isna(parsed):
                if parsed.tzinfo is None:
                    parsed = parsed.tz_localize(_IST_TZ)
                else:
                    parsed = parsed.tz_convert(_IST_TZ)
                return parsed.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    if ts_sec is not None:
        try:
            dt = pd.Timestamp(ts_sec, unit="s", tz="UTC").tz_convert(_IST_TZ)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(ts_sec)
    return ""


def _optional_float(row: pd.Series, candidates: tuple[str, ...]) -> float | None:
    """Return the first finite float found among candidate column names on ``row``."""
    for col in candidates:
        if col in row.index:
            val = _finite_float(row.get(col))
            if val is not None:
                return val
    return None


def _serialize_trades(
    trades: pd.DataFrame | None,
    raw_times: list[int | None],
) -> list[dict[str, Any]]:
    """Serialize trades sorted by entry time with 1-based ``id`` and finite fields."""
    if trades is None or not isinstance(trades, pd.DataFrame) or trades.empty:
        return []

    records: list[tuple[int, int, int, dict[str, Any]]] = []
    n_raw = len(raw_times)

    for orig_idx, (_, row) in enumerate(trades.iterrows()):
        size_val = _finite_float(row.get("Size"))
        is_short = size_val is not None and size_val < 0
        side = "Short" if is_short else "Long"

        entry_ts: int | None = None
        if "EntryBar" in row.index and not pd.isna(row["EntryBar"]):
            try:
                eb = int(row["EntryBar"])
                if 0 <= eb < n_raw:
                    entry_ts = raw_times[eb]
            except (TypeError, ValueError):
                pass
        if entry_ts is None and "EntryTime" in row.index:
            entry_ts = _single_ts_to_unix_seconds(row["EntryTime"])

        exit_ts: int | None = None
        if "ExitBar" in row.index and not pd.isna(row["ExitBar"]):
            try:
                xb = int(row["ExitBar"])
                if 0 <= xb < n_raw:
                    exit_ts = raw_times[xb]
            except (TypeError, ValueError):
                pass
        if exit_ts is None and "ExitTime" in row.index:
            exit_ts = _single_ts_to_unix_seconds(row["ExitTime"])

        entry_price = _finite_float(row.get("EntryPrice"))
        exit_price = _finite_float(row.get("ExitPrice"))
        pnl = _optional_float(row, ("PnL", "NetPnL", "pnl"))

        ret_pct = _optional_float(row, ("ReturnPct", "return_pct"))
        if "ReturnPct" in row.index and ret_pct is not None:
            # backtesting.py stores ReturnPct as a fraction (0.025 == 2.5%)
            ret_pct = ret_pct * 100.0
        elif ret_pct is None and entry_price and exit_price and entry_price > 0:
            direction = -1.0 if is_short else 1.0
            ret_pct = ((exit_price - entry_price) / entry_price) * 100.0 * direction

        item: dict[str, Any] = {
            "id": 0,  # assigned after sorting
            "side": side,
            "entry_time": _format_ts_ist(entry_ts, row.get("EntryTime") if "EntryTime" in row.index else None),
            "exit_time": _format_ts_ist(exit_ts, row.get("ExitTime") if "ExitTime" in row.index else None),
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "entry_price": round(entry_price, 4) if entry_price is not None else None,
            "exit_price": round(exit_price, 4) if exit_price is not None else None,
            "size": round(size_val, 4) if size_val is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "return_pct": round(ret_pct, 3) if ret_pct is not None else None,
        }

        charges = _optional_float(row, ("TotalCharges", "Charges", "charges"))
        if charges is not None:
            item["charges"] = round(charges, 2)

        r_mult = _optional_float(row, ("R_multiple", "R_Multiple", "r_multiple", "R"))
        if r_mult is not None:
            item["r_multiple"] = round(r_mult, 3)

        sl = _optional_float(row, ("SL", "sl"))
        if sl is not None:
            item["sl"] = round(sl, 4)

        tp = _optional_float(row, ("TP", "tp"))
        if tp is not None:
            item["tp"] = round(tp, 4)

        sort_entry = entry_ts if entry_ts is not None else (1 << 60)
        sort_exit = exit_ts if exit_ts is not None else (1 << 60)
        records.append((sort_entry, sort_exit, orig_idx, item))

    records.sort(key=lambda r: (r[0], r[1], r[2]))
    out: list[dict[str, Any]] = []
    for idx, (_, _, _, item) in enumerate(records, start=1):
        item["id"] = idx
        out.append(item)
    return out


def _normalize_drawdown_series(raw_vals: list[float | None]) -> list[float | None]:
    """Normalize DrawdownPct values into negative percentage points (e.g. -4.25)."""
    finite = [v for v in raw_vals if v is not None]
    if not finite:
        return raw_vals
    vmin = min(finite)
    vmax = max(finite)
    # backtesting.py emits DrawdownPct as a non-negative fraction [0.0, 1.0].
    if vmin >= 0.0 and vmax <= 1.0 + 1e-9:
        return [None if v is None else -v * 100.0 for v in raw_vals]
    if vmin >= 0.0:
        return [None if v is None else -v for v in raw_vals]
    return raw_vals


def _build_aligned_equity_and_dd(
    candle_times: list[int],
    eq_df: pd.DataFrame | None,
    trades_list: list[dict[str, Any]],
    init_cap: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Build bar-aligned ``equity`` and ``drawdown`` series with zero NaN/Inf."""
    if not candle_times:
        return [], [], False

    has_eq_curve = False
    eq_by_ts: dict[int, float] = {}
    dd_by_ts: dict[int, float] = {}

    if eq_df is not None and not eq_df.empty and "Equity" in eq_df.columns:
        eq_times = _index_to_unix_seconds(eq_df.index)
        raw_eq = [_finite_float(v) for v in eq_df["Equity"].to_numpy()]
        raw_dd: list[float | None] = []
        has_dd_col = "DrawdownPct" in eq_df.columns
        if has_dd_col:
            raw_dd = _normalize_drawdown_series(
                [_finite_float(v) for v in eq_df["DrawdownPct"].to_numpy()]
            )

        for idx, t in enumerate(eq_times):
            if t is None:
                continue
            ev = raw_eq[idx]
            if ev is not None:
                eq_by_ts[t] = ev
                has_eq_curve = True
            if has_dd_col and idx < len(raw_dd) and raw_dd[idx] is not None:
                dd_by_ts[t] = float(raw_dd[idx])

    equity_series: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []

    if has_eq_curve:
        # Seed initial carry-forward with the earliest finite curve value or initial capital.
        first_known = next((eq_by_ts[t] for t in candle_times if t in eq_by_ts), init_cap)
        last_eq = first_known
        peak_eq = max(last_eq, init_cap) if last_eq > 0 else init_cap

        for t in candle_times:
            eq_val = eq_by_ts.get(t, last_eq)
            if not math.isfinite(eq_val):
                eq_val = last_eq
            last_eq = eq_val
            if eq_val > peak_eq:
                peak_eq = eq_val

            if t in dd_by_ts and math.isfinite(dd_by_ts[t]):
                dd_val = dd_by_ts[t]
            elif peak_eq > 0:
                dd_val = min(0.0, ((eq_val / peak_eq) - 1.0) * 100.0)
            else:
                dd_val = 0.0

            equity_series.append({"time": t, "value": round(eq_val, 2)})
            drawdown_series.append({"time": t, "value": round(dd_val, 3)})
        return equity_series, drawdown_series, True

    # Graceful fallback when stats["_equity_curve"] is unavailable: reconstruct realized step equity.
    closed_events: list[tuple[int, float]] = []
    for tr in trades_list:
        ex_ts = tr.get("exit_ts")
        pnl = tr.get("pnl")
        if ex_ts is not None and pnl is not None and math.isfinite(float(pnl)):
            closed_events.append((int(ex_ts), float(pnl)))
    closed_events.sort(key=lambda x: x[0])

    ev_idx = 0
    n_ev = len(closed_events)
    running_eq = init_cap
    peak_eq = init_cap

    for t in candle_times:
        while ev_idx < n_ev and closed_events[ev_idx][0] <= t:
            running_eq += closed_events[ev_idx][1]
            ev_idx += 1
        if running_eq > peak_eq:
            peak_eq = running_eq
        dd_val = min(0.0, ((running_eq / peak_eq) - 1.0) * 100.0) if peak_eq > 0 else 0.0
        equity_series.append({"time": t, "value": round(running_eq, 2)})
        drawdown_series.append({"time": t, "value": round(dd_val, 3)})

    return equity_series, drawdown_series, False


def _build_replay_metrics(
    candle_times: list[int],
    equity_series: list[dict[str, Any]],
    drawdown_series: list[dict[str, Any]],
    trades_list: list[dict[str, Any]],
    init_cap: float,
) -> list[dict[str, Any]]:
    """Compute per-bar cumulative stats so client-side replay updates HUD instantaneously."""
    if not candle_times:
        return []

    closed: list[tuple[int, bool]] = []
    for tr in trades_list:
        ex_ts = tr.get("exit_ts")
        if ex_ts is None:
            continue
        pnl = tr.get("pnl")
        ret = tr.get("return_pct")
        is_win = (pnl is not None and float(pnl) > 0) or (pnl is None and ret is not None and float(ret) > 0)
        closed.append((int(ex_ts), bool(is_win)))
    closed.sort(key=lambda x: x[0])

    c_idx = 0
    n_closed_total = len(closed)
    closed_count = 0
    win_count = 0
    out: list[dict[str, Any]] = []

    for i, t in enumerate(candle_times):
        while c_idx < n_closed_total and closed[c_idx][0] <= t:
            closed_count += 1
            if closed[c_idx][1]:
                win_count += 1
            c_idx += 1

        eq_val = float(equity_series[i]["value"])
        dd_val = float(drawdown_series[i]["value"])
        cum_ret = ((eq_val - init_cap) / init_cap) * 100.0 if init_cap > 0 else 0.0
        win_rate = (win_count / closed_count) * 100.0 if closed_count > 0 else 0.0

        out.append(
            {
                "time": t,
                "equity": round(eq_val, 2),
                "dd_pct": round(dd_val, 3),
                "cum_ret_pct": round(cum_ret, 3),
                "closed_trades": closed_count,
                "win_rate_pct": round(win_rate, 2),
            }
        )
    return out


def build_terminal_payload(
    out_df: pd.DataFrame,
    stats: Any = None,
    trades: pd.DataFrame | None = None,
    indicators: list[dict[str, Any]] | None = None,
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    """Build a JSON-safe payload for the QuantDesk Pro Trading Terminal.

    Reuses ``build_openalgo_payload`` for OHLCV candles, volume, overlays, stacked
    indicator panels, and markers, then appends bar-aligned ``equity`` and ``drawdown``
    series, sorted ``trades_list`` (1-based IDs), and per-bar ``replay_metrics``.
    """
    cap = _finite_float(initial_capital)
    init_cap = cap if (cap is not None and cap > 0) else 100000.0

    resolved_trades = _resolve_trades_df(stats, trades)
    base_payload = build_openalgo_payload(
        out_df,
        trades=resolved_trades,
        indicators=indicators,
    )

    candles = base_payload["candles"]
    candle_times = [int(c["time"]) for c in candles]

    raw_times = (
        _index_to_unix_seconds(out_df.index)
        if out_df is not None and not out_df.empty
        else []
    )
    trades_list = _serialize_trades(resolved_trades, raw_times)

    eq_df = _resolve_equity_df(stats)
    equity_series, drawdown_series, has_eq_curve = _build_aligned_equity_and_dd(
        candle_times, eq_df, trades_list, init_cap
    )
    replay_metrics = _build_replay_metrics(
        candle_times, equity_series, drawdown_series, trades_list, init_cap
    )

    last_metric = replay_metrics[-1] if replay_metrics else None
    max_dd = min((m["dd_pct"] for m in replay_metrics), default=0.0)

    meta = dict(base_payload.get("meta", {}))
    meta.update(
        {
            "trade_count": len(trades_list),
            "initial_capital": round(init_cap, 2),
            "final_equity": last_metric["equity"] if last_metric else round(init_cap, 2),
            "total_return_pct": last_metric["cum_ret_pct"] if last_metric else 0.0,
            "max_drawdown_pct": round(max_dd, 3),
            "win_rate_pct": last_metric["win_rate_pct"] if last_metric else 0.0,
            "has_equity_curve": has_eq_curve,
        }
    )

    return {
        "candles": candles,
        "volume": base_payload["volume"],
        "overlays": base_payload["overlays"],
        "panels": base_payload["panels"],
        "markers": base_payload["markers"],
        "equity": equity_series,
        "drawdown": drawdown_series,
        "trades_list": trades_list,
        "replay_metrics": replay_metrics,
        "meta": meta,
    }


def _resolve_initial_focus_id(
    initial_focus_trade: Any,
    trades_list: list[dict[str, Any]],
) -> int | None:
    """Resolve an optional 1-based trade ID to focus on load."""
    if initial_focus_trade is None or not trades_list:
        return None
    target_id: int | None = None
    if isinstance(initial_focus_trade, dict):
        raw = initial_focus_trade.get("id")
        if raw is not None:
            try:
                target_id = int(raw)
            except (TypeError, ValueError):
                return None
    else:
        try:
            target_id = int(initial_focus_trade)
        except (TypeError, ValueError):
            return None
    if target_id is None:
        return None
    for tr in trades_list:
        if tr["id"] == target_id:
            return target_id
    return None


def render_openalgo_terminal(
    out_df: pd.DataFrame,
    stats: Any = None,
    trades: pd.DataFrame | None = None,
    indicators: list[dict[str, Any]] | None = None,
    initial_capital: float = 100000.0,
    height: int = 780,
    initial_focus_trade: int | None = None,
) -> str:
    """Return a self-contained offline HTML QuantDesk Pro Trading Terminal.

    Embeds the vendored ``openalgo-charts.standalone.js`` bundle inline (zero external CDN
    URLs), renders candlesticks + volume + price overlays + stacked indicator panels + synced
    Equity & Drawdown % pane (``createLinkGroup``), and provides interactive Bar-by-Bar Replay,
    Trade Focus/Jump with Entry/Exit/SL/TP price lines, live HUD, and CSV export.
    """
    payload = build_terminal_payload(
        out_df,
        stats=stats,
        trades=trades,
        indicators=indicators,
        initial_capital=initial_capital,
    )
    focus_id = _resolve_initial_focus_id(initial_focus_trade, payload["trades_list"])
    bundle_js = _load_bundle_js().replace("</script>", "<\\/script>")
    payload_json = json.dumps(payload, allow_nan=False).replace("</", "<\\/")
    focus_json = json.dumps(focus_id)

    total_h = max(480, int(height))
    toolbar_h = 44
    hud_h = 34
    stage_h = max(380, total_h - toolbar_h - hud_h)
    eq_h = max(130, int(round(stage_h * 0.27)))

    bar_count = payload["meta"]["bar_count"]
    trade_count = payload["meta"]["trade_count"]
    init_cap = payload["meta"]["initial_capital"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>QuantDesk Pro Terminal</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 100%;
    height: {total_h}px;
    overflow: hidden;
    background: #0e1117;
    color: #e5e7eb;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  #qt-wrap {{
    display: flex;
    flex-direction: column;
    width: 100%;
    height: {total_h}px;
    border: 1px solid #1e2334;
    border-radius: 6px;
    overflow: hidden;
    background: #0e1117;
  }}
  #qt-toolbar {{
    height: {toolbar_h}px;
    min-height: {toolbar_h}px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 0 10px;
    background: #131722;
    border-bottom: 1px solid #1e2334;
    font-size: 11px;
    white-space: nowrap;
    overflow-x: auto;
  }}
  .qt-group {{
    display: flex;
    align-items: center;
    gap: 5px;
    flex-shrink: 0;
  }}
  .qt-sep {{
    width: 1px;
    height: 20px;
    background: #232a3d;
    margin: 0 2px;
    flex-shrink: 0;
  }}
  .qt-badge {{
    padding: 2px 7px;
    border-radius: 4px;
    background: #1b2232;
    color: #93c5fd;
    font-weight: 600;
    font-size: 11px;
  }}
  .qt-btn {{
    background: #1b2232;
    color: #e5e7eb;
    border: 1px solid #2a3046;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
    line-height: 1.2;
    cursor: pointer;
  }}
  .qt-btn:hover {{ background: #263047; }}
  .qt-btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
  .qt-btn.active {{ border-color: #26a69a; color: #5eead4; }}
  .qt-btn.playing {{ border-color: #f59e0b; color: #fcd34d; }}
  .qt-select {{
    background: #1b2232;
    color: #e5e7eb;
    border: 1px solid #2a3046;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11px;
    max-width: 240px;
    cursor: pointer;
  }}
  #replay-scrubber {{
    width: 110px;
    accent-color: #26a69a;
    cursor: pointer;
  }}
  #replay-pos {{
    color: #94a3b8;
    font-variant-numeric: tabular-nums;
    min-width: 56px;
    text-align: right;
  }}
  #qt-hud {{
    height: {hud_h}px;
    min-height: {hud_h}px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 0 12px;
    background: #10141e;
    border-bottom: 1px solid #1e2334;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    overflow: hidden;
    white-space: nowrap;
  }}
  #qt-hud-items {{
    display: flex;
    align-items: center;
    gap: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .hud-kv {{ display: inline-flex; align-items: center; gap: 4px; }}
  .hud-k {{ color: #64748b; font-weight: 600; }}
  .hud-v {{ color: #e2e8f0; }}
  .hud-pos {{ color: #26a69a; font-weight: 600; }}
  .hud-neg {{ color: #ef5350; font-weight: 600; }}
  #hud-trade-focus {{
    display: none;
    padding: 2px 8px;
    border-radius: 4px;
    background: #172033;
    border: 1px solid #2e4063;
    color: #93c5fd;
    font-size: 11px;
    flex-shrink: 0;
  }}
  #qt-stage {{
    display: flex;
    flex-direction: column;
    width: 100%;
    height: {stage_h}px;
    position: relative;
    overflow: hidden;
  }}
  #oa-chart-main {{
    width: 100%;
    flex: 1 1 auto;
    min-height: 180px;
    position: relative;
  }}
  #oa-chart-equity {{
    width: 100%;
    height: {eq_h}px;
    min-height: 110px;
    border-top: 1px solid #1e2334;
    position: relative;
  }}
  #oa-eq-tag {{
    position: absolute;
    top: 4px;
    left: 10px;
    z-index: 4;
    font-size: 10px;
    color: #94a3b8;
    background: rgba(14, 17, 23, 0.78);
    padding: 2px 6px;
    border-radius: 3px;
    pointer-events: none;
  }}
  #oa-empty {{
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    color: #94a3b8;
    font-size: 14px;
    z-index: 10;
    background: #0e1117;
  }}
</style>
</head>
<body>
<div id="qt-wrap">
  <div id="qt-toolbar">
    <div class="qt-group">
      <span class="qt-badge">IST · Asia/Kolkata</span>
      <div class="qt-sep"></div>
      <button id="btn-replay-start" class="qt-btn" type="button" title="Jump to first bar">⏮ Start</button>
      <button id="btn-replay-play" class="qt-btn" type="button" title="Play / Pause replay">▶ Play</button>
      <button id="btn-replay-step" class="qt-btn" type="button" title="Advance one bar">⏭ Step</button>
      <select id="sel-replay-speed" class="qt-select" title="Replay speed">
        <option value="1">1x</option>
        <option value="5" selected>5x</option>
        <option value="15">15x</option>
        <option value="50">50x</option>
      </select>
      <input id="replay-scrubber" type="range" min="0" max="{max(0, bar_count - 1)}" value="{max(0, bar_count - 1)}" title="Bar scrubber" />
      <span id="replay-pos">{bar_count}/{bar_count}</span>
    </div>
    <div class="qt-group">
      <button id="btn-prev-trade" class="qt-btn" type="button" title="Previous trade">◀ Prev Trade</button>
      <select id="sel-trade" class="qt-select" title="Focus trade">
        <option value="">All Trades ({trade_count})</option>
      </select>
      <button id="btn-next-trade" class="qt-btn" type="button" title="Next trade">Next Trade ▶</button>
      <div class="qt-sep"></div>
      <button id="btn-fit" class="qt-btn" type="button" title="Fit all visible bars">Fit</button>
      <button id="btn-vol" class="qt-btn active" type="button" title="Toggle volume">Volume</button>
      <button id="btn-markers" class="qt-btn active" type="button" title="Toggle trade markers">Markers</button>
      <button id="btn-equity" class="qt-btn active" type="button" title="Toggle Equity & Drawdown pane">Equity/DD</button>
      <button id="btn-csv" class="qt-btn" type="button" title="Export CSV">CSV</button>
    </div>
  </div>

  <div id="qt-hud">
    <div id="qt-hud-items">
      <span class="hud-kv"><span class="hud-k">Time</span><span id="hud-time" class="hud-v">—</span></span>
      <span class="hud-kv"><span class="hud-k">OHLC</span><span id="hud-ohlc" class="hud-v">—</span></span>
      <span class="hud-kv"><span class="hud-k">Equity ₹</span><span id="hud-equity" class="hud-v">₹{init_cap:,.2f}</span></span>
      <span class="hud-kv"><span class="hud-k">Return %</span><span id="hud-ret" class="hud-v">0.00%</span></span>
      <span class="hud-kv"><span class="hud-k">DD %</span><span id="hud-dd" class="hud-v">0.00%</span></span>
      <span class="hud-kv"><span class="hud-k">Trades</span><span id="hud-trades" class="hud-v">0</span></span>
      <span class="hud-kv"><span class="hud-k">Win %</span><span id="hud-win" class="hud-v">0.0%</span></span>
    </div>
    <div id="hud-trade-focus"></div>
  </div>

  <div id="qt-stage">
    <div id="oa-chart-main"></div>
    <div id="oa-chart-equity">
      <div id="oa-eq-tag">Equity (₹) · Drawdown (%)</div>
    </div>
    <div id="oa-empty">No valid OHLCV bars available to render.</div>
  </div>
</div>

<script>{bundle_js}</script>
<script>
(function () {{
  const payload = {payload_json};
  const initialFocusTradeId = {focus_json};

  const mainEl = document.getElementById("oa-chart-main");
  const eqEl = document.getElementById("oa-chart-equity");
  const emptyEl = document.getElementById("oa-empty");

  const hudTime = document.getElementById("hud-time");
  const hudOhlc = document.getElementById("hud-ohlc");
  const hudEquity = document.getElementById("hud-equity");
  const hudRet = document.getElementById("hud-ret");
  const hudDd = document.getElementById("hud-dd");
  const hudTrades = document.getElementById("hud-trades");
  const hudWin = document.getElementById("hud-win");
  const hudTradeFocus = document.getElementById("hud-trade-focus");

  const btnStart = document.getElementById("btn-replay-start");
  const btnPlay = document.getElementById("btn-replay-play");
  const btnStep = document.getElementById("btn-replay-step");
  const selSpeed = document.getElementById("sel-replay-speed");
  const scrubber = document.getElementById("replay-scrubber");
  const replayPosEl = document.getElementById("replay-pos");

  const btnPrevTrade = document.getElementById("btn-prev-trade");
  const selTrade = document.getElementById("sel-trade");
  const btnNextTrade = document.getElementById("btn-next-trade");

  const btnFit = document.getElementById("btn-fit");
  const btnVol = document.getElementById("btn-vol");
  const btnMarkers = document.getElementById("btn-markers");
  const btnEquity = document.getElementById("btn-equity");
  const btnCsv = document.getElementById("btn-csv");

  const allCandles = payload.candles || [];
  const totalBars = allCandles.length;
  if (totalBars === 0) {{
    emptyEl.style.display = "flex";
    return;
  }}
  if (typeof OpenAlgoCharts === "undefined" || !OpenAlgoCharts.createChart) {{
    emptyEl.textContent = "OpenAlgoCharts bundle failed to initialize.";
    emptyEl.style.display = "flex";
    return;
  }}

  const {{ createChart, darkTheme, createLinkGroup }} = OpenAlgoCharts;
  const themeCfg = {{
    ...darkTheme,
    background: "#0e1117",
    grid: "#1b2232",
    axisLine: "#2a3046",
    paneSeparator: "#1e2334"
  }};

  const priceChart = createChart(mainEl, {{
    theme: themeCfg,
    timezone: "Asia/Kolkata",
    branding: false,
    grid: {{ vertLines: true, horzLines: true }}
  }});

  const candleSeries = priceChart.addSeries("candlestick", {{ paneIndex: 0 }});
  candleSeries.setData(allCandles);

  const allVolume = payload.volume || [];
  let volSeries = null;
  let volVisible = true;
  if (allVolume.length > 0) {{
    volSeries = priceChart.addSeries("histogram", {{
      paneIndex: 0,
      priceScaleId: "",
      priceFormat: {{ type: "volume" }},
      style: {{ base: 0 }}
    }});
    volSeries.priceScale().setOptions({{ marginTop: 0.78, marginBottom: 0 }});
    volSeries.setData(allVolume);
  }} else {{
    btnVol.style.display = "none";
  }}

  const overlayHandles = [];
  for (const ov of payload.overlays || []) {{
    const s = priceChart.addSeries("line", {{
      paneIndex: 0,
      style: {{ color: ov.color, lineWidth: 1.5, title: ov.name }}
    }});
    s.setData(ov.data);
    overlayHandles.push({{ series: s, data: ov.data }});
  }}

  const panels = payload.panels || [];
  if (typeof priceChart.setPaneWeight === "function") {{
    priceChart.setPaneWeight(0, 3.2);
  }}
  const panelHandles = [];
  panels.forEach((panel, idx) => {{
    const paneIdx = idx + 1;
    for (const ps of panel.series || []) {{
      const s = priceChart.addSeries("line", {{
        paneIndex: paneIdx,
        style: {{ color: ps.color, lineWidth: 1.5, title: ps.name }}
      }});
      s.setData(ps.data);
      panelHandles.push({{ series: s, data: ps.data }});
    }}
    for (let gIdx = 0; gIdx < (panel.guides || []).length; gIdx++) {{
      const gy = panel.guides[gIdx];
      priceChart.addPriceLine(
        {{
          price: gy,
          color: "#6b7280",
          lineWidth: 1,
          lineStyle: "dotted",
          label: String(gy),
          id: "guide-" + paneIdx + "-" + gIdx
        }},
        paneIdx
      );
    }}
    if (typeof priceChart.setPaneWeight === "function") {{
      priceChart.setPaneWeight(paneIdx, 1.25);
    }}
  }});

  const markersLayer = candleSeries.createMarkers();
  const allMarkers = payload.markers || [];
  let markersVisible = true;
  if (allMarkers.length > 0) {{
    markersLayer.setMarkers(allMarkers);
  }} else {{
    btnMarkers.style.display = "none";
  }}

  const allEquity = payload.equity || [];
  const allDrawdown = payload.drawdown || [];
  const allMetrics = payload.replay_metrics || [];
  const initCap = Number(payload.meta?.initial_capital || 100000);

  let eqChart = null;
  let eqSeries = null;
  let ddSeries = null;
  let eqVisible = true;
  let stackedEqPaneIdx = null;

  try {{
    eqChart = createChart(eqEl, {{
      theme: themeCfg,
      timezone: "Asia/Kolkata",
      branding: false,
      grid: {{ vertLines: true, horzLines: true }}
    }});
    eqSeries = eqChart.addSeries("area", {{
      paneIndex: 0,
      style: {{
        color: "#26a69a",
        areaTopColor: "rgba(38, 166, 154, 0.28)",
        areaBottomColor: "rgba(38, 166, 154, 0.02)",
        lineWidth: 1.8,
        title: "Equity (₹)"
      }}
    }});
    eqSeries.setData(allEquity);
    eqChart.addPriceLine(
      {{
        price: initCap,
        color: "#64748b",
        lineWidth: 1,
        lineStyle: "dotted",
        label: "Cap " + initCap.toFixed(0),
        id: "eq-init-cap"
      }},
      0
    );
    ddSeries = eqChart.addSeries("line", {{
      paneIndex: 0,
      priceScaleId: "left",
      style: {{ color: "#ef5350", lineWidth: 1.3, title: "DD %" }}
    }});
    ddSeries.setData(allDrawdown);

    if (typeof createLinkGroup === "function") {{
      const linkGroup = createLinkGroup({{ crosshair: true, viewport: true }});
      linkGroup.add(priceChart);
      linkGroup.add(eqChart);
    }}
  }} catch (_err) {{
    // Fallback: stack Equity & DD inside priceChart if secondary chart fails
    eqEl.style.display = "none";
    stackedEqPaneIdx = panels.length + 1;
    eqSeries = priceChart.addSeries("area", {{
      paneIndex: stackedEqPaneIdx,
      style: {{
        color: "#26a69a",
        areaTopColor: "rgba(38, 166, 154, 0.28)",
        areaBottomColor: "rgba(38, 166, 154, 0.02)",
        lineWidth: 1.8,
        title: "Equity (₹)"
      }}
    }});
    eqSeries.setData(allEquity);
    ddSeries = priceChart.addSeries("line", {{
      paneIndex: stackedEqPaneIdx,
      priceScaleId: "left",
      style: {{ color: "#ef5350", lineWidth: 1.3, title: "DD %" }}
    }});
    ddSeries.setData(allDrawdown);
    if (typeof priceChart.setPaneWeight === "function") {{
      priceChart.setPaneWeight(stackedEqPaneIdx, 1.35);
    }}
  }}

  const fmtTimeIst = (unixSec) => {{
    if (unixSec == null) return "—";
    try {{
      return new Intl.DateTimeFormat("en-IN", {{
        timeZone: "Asia/Kolkata",
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
      }}).format(new Date(unixSec * 1000));
    }} catch (_) {{
      return String(unixSec);
    }}
  }};

  const fmtInr = (val) => {{
    const n = Number(val);
    if (!Number.isFinite(n)) return "—";
    return "₹" + n.toLocaleString("en-IN", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
  }};

  const timeToBarIdx = new Map();
  for (let i = 0; i < totalBars; i++) {{
    timeToBarIdx.set(allCandles[i].time, i);
  }}

  const findNearestBarIdx = (ts) => {{
    if (ts == null) return -1;
    if (timeToBarIdx.has(ts)) return timeToBarIdx.get(ts);
    let lo = 0;
    let hi = totalBars - 1;
    while (lo < hi) {{
      const mid = (lo + hi) >> 1;
      if (allCandles[mid].time < ts) lo = mid + 1;
      else hi = mid;
    }}
    return lo;
  }};

  let cursor = totalBars - 1;
  let playing = false;
  let playTimer = null;

  const updateHudAt = (idx) => {{
    const safeIdx = Math.max(0, Math.min(totalBars - 1, idx));
    const c = allCandles[safeIdx];
    const m = allMetrics[safeIdx] || null;

    hudTime.textContent = fmtTimeIst(c.time);
    hudOhlc.textContent =
      "O:" + Number(c.open).toFixed(2) +
      " H:" + Number(c.high).toFixed(2) +
      " L:" + Number(c.low).toFixed(2) +
      " C:" + Number(c.close).toFixed(2);

    if (m) {{
      hudEquity.textContent = fmtInr(m.equity);
      const rVal = Number(m.cum_ret_pct);
      hudRet.textContent = (rVal >= 0 ? "+" : "") + rVal.toFixed(2) + "%";
      hudRet.className = "hud-v " + (rVal >= 0 ? "hud-pos" : "hud-neg");
      const dVal = Number(m.dd_pct);
      hudDd.textContent = dVal.toFixed(2) + "%";
      hudDd.className = "hud-v " + (dVal < -0.001 ? "hud-neg" : "");
      hudTrades.textContent = String(m.closed_trades);
      hudWin.textContent = Number(m.win_rate_pct).toFixed(1) + "%";
    }}
  }};

  const applyReplayCursor = (nextCursor, refit = false) => {{
    cursor = Math.max(0, Math.min(totalBars - 1, nextCursor));
    const count = cursor + 1;
    const lastTime = allCandles[cursor].time;

    candleSeries.setData(allCandles.slice(0, count));
    if (volSeries) {{
      volSeries.setData(volVisible ? allVolume.filter((v) => v.time <= lastTime) : []);
    }}
    for (const ov of overlayHandles) {{
      ov.series.setData(ov.data.slice(0, count));
    }}
    for (const ph of panelHandles) {{
      ph.series.setData(ph.data.slice(0, count));
    }}
    if (eqSeries) {{
      eqSeries.setData(allEquity.slice(0, count));
    }}
    if (ddSeries) {{
      ddSeries.setData(allDrawdown.slice(0, count));
    }}
    if (markersLayer) {{
      markersLayer.setMarkers(
        markersVisible ? allMarkers.filter((m) => m.time <= lastTime) : []
      );
    }}

    scrubber.value = String(cursor);
    replayPosEl.textContent = count + "/" + totalBars;
    updateHudAt(cursor);

    if (refit) {{
      priceChart.fitContent();
      if (eqChart && eqVisible) eqChart.fitContent();
    }}
  }};

  const stopPlay = () => {{
    playing = false;
    if (playTimer !== null) {{
      clearInterval(playTimer);
      playTimer = null;
    }}
    btnPlay.textContent = "▶ Play";
    btnPlay.classList.remove("playing");
  }};

  const startPlay = () => {{
    if (cursor >= totalBars - 1) {{
      applyReplayCursor(0, true);
    }}
    playing = true;
    btnPlay.textContent = "⏸ Pause";
    btnPlay.classList.add("playing");

    const speed = Math.max(1, Number(selSpeed.value) || 5);
    const baseMs = 500;
    const rawMs = baseMs / speed;
    const intervalMs = Math.max(20, Math.round(rawMs));
    const stepBars = rawMs < 20 ? Math.max(1, Math.round(20 / rawMs)) : 1;

    playTimer = setInterval(() => {{
      if (cursor >= totalBars - 1) {{
        stopPlay();
        return;
      }}
      applyReplayCursor(Math.min(totalBars - 1, cursor + stepBars), false);
      if (cursor >= totalBars - 1) {{
        stopPlay();
      }}
    }}, intervalMs);
  }};

  btnStart.addEventListener("click", () => {{
    stopPlay();
    applyReplayCursor(0, true);
  }});

  btnPlay.addEventListener("click", () => {{
    if (playing) stopPlay();
    else startPlay();
  }});

  btnStep.addEventListener("click", () => {{
    stopPlay();
    if (cursor < totalBars - 1) {{
      applyReplayCursor(cursor + 1, false);
    }}
  }});

  selSpeed.addEventListener("change", () => {{
    if (playing) {{
      stopPlay();
      startPlay();
    }}
  }});

  scrubber.addEventListener("input", () => {{
    stopPlay();
    applyReplayCursor(Number(scrubber.value), false);
  }});

  // --- Trade Focus / Jump ---
  const tradesList = payload.trades_list || [];
  const tradeById = new Map();
  for (const tr of tradesList) {{
    tradeById.set(tr.id, tr);
    const opt = document.createElement("option");
    opt.value = String(tr.id);
    const pnl = tr.pnl != null ? (tr.pnl >= 0 ? "+₹" + tr.pnl.toFixed(0) : "-₹" + Math.abs(tr.pnl).toFixed(0)) : "";
    const ret = tr.return_pct != null ? " (" + (tr.return_pct >= 0 ? "+" : "") + tr.return_pct.toFixed(2) + "%)" : "";
    opt.textContent = "#" + tr.id + " " + tr.side + " " + pnl + ret;
    selTrade.appendChild(opt);
  }}
  if (tradesList.length === 0) {{
    btnPrevTrade.disabled = true;
    btnNextTrade.disabled = true;
    selTrade.disabled = true;
  }}

  let focusLines = [];
  let currentFocusIdx = -1;

  const clearFocusLines = () => {{
    for (const line of focusLines) {{
      try {{
        priceChart.removePrimitive(line);
      }} catch (_) {{}}
    }}
    focusLines = [];
    hudTradeFocus.style.display = "none";
    hudTradeFocus.textContent = "";
  }};

  const focusTradeById = (tid) => {{
    clearFocusLines();
    if (tid == null || !tradeById.has(tid)) {{
      currentFocusIdx = -1;
      selTrade.value = "";
      return;
    }}
    const tr = tradeById.get(tid);
    currentFocusIdx = tradesList.findIndex((x) => x.id === tid);
    selTrade.value = String(tid);

    const entryIdx = findNearestBarIdx(tr.entry_ts);
    const exitIdx = tr.exit_ts != null ? findNearestBarIdx(tr.exit_ts) : entryIdx;
    const neededCursor = Math.max(entryIdx, exitIdx);
    if (neededCursor > cursor) {{
      stopPlay();
      applyReplayCursor(neededCursor, false);
    }}

    if (tr.entry_price != null) {{
      focusLines.push(
        priceChart.addPriceLine(
          {{
            price: tr.entry_price,
            color: "#00e676",
            lineWidth: 1.5,
            lineStyle: "dashed",
            label: "Entry " + Number(tr.entry_price).toFixed(2),
            leftLabel: "#" + tr.id + " " + tr.side + " Entry",
            id: "focus-entry-" + tr.id
          }},
          0
        )
      );
    }}
    if (tr.exit_price != null) {{
      const pnlStr = tr.pnl != null ? (tr.pnl >= 0 ? "+₹" + Number(tr.pnl).toFixed(2) : "-₹" + Math.abs(Number(tr.pnl)).toFixed(2)) : "";
      focusLines.push(
        priceChart.addPriceLine(
          {{
            price: tr.exit_price,
            color: "#ff5252",
            lineWidth: 1.5,
            lineStyle: "dashed",
            label: "Exit " + Number(tr.exit_price).toFixed(2),
            leftLabel: "#" + tr.id + " Exit " + pnlStr,
            id: "focus-exit-" + tr.id
          }},
          0
        )
      );
    }}
    if (tr.sl != null) {{
      focusLines.push(
        priceChart.addPriceLine(
          {{
            price: tr.sl,
            color: "#f59e0b",
            lineWidth: 1,
            lineStyle: "dotted",
            label: "SL " + Number(tr.sl).toFixed(2),
            leftLabel: "SL",
            id: "focus-sl-" + tr.id
          }},
          0
        )
      );
    }}
    if (tr.tp != null) {{
      focusLines.push(
        priceChart.addPriceLine(
          {{
            price: tr.tp,
            color: "#38bdf8",
            lineWidth: 1,
            lineStyle: "dotted",
            label: "TP " + Number(tr.tp).toFixed(2),
            leftLabel: "TP",
            id: "focus-tp-" + tr.id
          }},
          0
        )
      );
    }}

    if (entryIdx >= 0) {{
      const loIdx = Math.min(entryIdx, exitIdx >= 0 ? exitIdx : entryIdx);
      const hiIdx = Math.max(entryIdx, exitIdx >= 0 ? exitIdx : entryIdx);
      const span = Math.max(10, hiIdx - loIdx + 1);
      const pad = Math.max(8, Math.round(span * 0.5));
      const fromRange = Math.max(-0.5, loIdx - pad);
      const toRange = Math.min(cursor + 2, hiIdx + pad);
      if (typeof priceChart.setVisibleLogicalRange === "function" && toRange > fromRange) {{
        priceChart.setVisibleLogicalRange({{ from: fromRange, to: toRange }});
      }}
    }}

    const pnlDisp = tr.pnl != null ? (tr.pnl >= 0 ? "+₹" + Number(tr.pnl).toFixed(2) : "-₹" + Math.abs(Number(tr.pnl)).toFixed(2)) : "Open";
    const retDisp = tr.return_pct != null ? " (" + (tr.return_pct >= 0 ? "+" : "") + Number(tr.return_pct).toFixed(2) + "%)" : "";
    const rDisp = tr.r_multiple != null ? " · R:" + Number(tr.r_multiple).toFixed(2) : "";
    hudTradeFocus.textContent = "Focus #" + tr.id + " " + tr.side + " · " + pnlDisp + retDisp + rDisp;
    hudTradeFocus.style.display = "inline-block";
  }};

  selTrade.addEventListener("change", () => {{
    const val = selTrade.value;
    focusTradeById(val ? Number(val) : null);
  }});

  btnPrevTrade.addEventListener("click", () => {{
    if (tradesList.length === 0) return;
    const nextIdx = currentFocusIdx <= 0 ? tradesList.length - 1 : currentFocusIdx - 1;
    focusTradeById(tradesList[nextIdx].id);
  }});

  btnNextTrade.addEventListener("click", () => {{
    if (tradesList.length === 0) return;
    const nextIdx = currentFocusIdx >= tradesList.length - 1 ? 0 : currentFocusIdx + 1;
    focusTradeById(tradesList[nextIdx].id);
  }});

  // --- Crosshair HUD hover ---
  const onHover = (e) => {{
    if (!e || e.time == null) {{
      updateHudAt(cursor);
      return;
    }}
    const idx = timeToBarIdx.get(e.time);
    if (idx !== undefined && idx <= cursor) {{
      updateHudAt(idx);
    }}
  }};
  priceChart.subscribeCrosshairMove(onHover);
  if (eqChart) {{
    eqChart.subscribeCrosshairMove(onHover);
  }}

  // --- View & Export buttons ---
  btnFit.addEventListener("click", () => {{
    priceChart.fitContent();
    if (eqChart && eqVisible) eqChart.fitContent();
  }});

  btnVol.addEventListener("click", () => {{
    if (!volSeries) return;
    volVisible = !volVisible;
    const lastTime = allCandles[cursor].time;
    volSeries.setData(volVisible ? allVolume.filter((v) => v.time <= lastTime) : []);
    btnVol.classList.toggle("active", volVisible);
  }});

  btnMarkers.addEventListener("click", () => {{
    markersVisible = !markersVisible;
    const lastTime = allCandles[cursor].time;
    markersLayer.setMarkers(
      markersVisible ? allMarkers.filter((m) => m.time <= lastTime) : []
    );
    btnMarkers.classList.toggle("active", markersVisible);
  }});

  btnEquity.addEventListener("click", () => {{
    eqVisible = !eqVisible;
    btnEquity.classList.toggle("active", eqVisible);
    if (eqChart) {{
      eqEl.style.display = eqVisible ? "block" : "none";
      requestAnimationFrame(() => {{
        priceChart.applySize(mainEl.clientWidth, mainEl.clientHeight);
        if (eqVisible && eqChart) {{
          eqChart.applySize(eqEl.clientWidth, eqEl.clientHeight);
          eqChart.fitContent();
        }}
      }});
    }} else if (stackedEqPaneIdx !== null && typeof priceChart.setPaneCollapsed === "function") {{
      priceChart.setPaneCollapsed(stackedEqPaneIdx, !eqVisible);
    }}
  }});

  const csvCell = (val) => {{
    if (val === null || val === undefined) return "";
    const s = String(val);
    return /[",\\n\\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }};

  btnCsv.addEventListener("click", () => {{
    let csvContent = "";
    let fileName = "quantdesk_export.csv";
    if (tradesList.length > 0) {{
      const cols = ["id", "side", "entry_time", "exit_time", "entry_price", "exit_price", "size", "pnl", "return_pct", "charges", "r_multiple", "sl", "tp"];
      const lines = [cols.join(",")];
      for (const tr of tradesList) {{
        lines.push(cols.map((k) => csvCell(tr[k])).join(","));
      }}
      csvContent = lines.join("\\n");
      fileName = "quantdesk_trades.csv";
    }} else {{
      const cols = ["time_ist", "unix_ts", "open", "high", "low", "close", "volume", "equity", "dd_pct"];
      const lines = [cols.join(",")];
      for (let i = 0; i < totalBars; i++) {{
        const c = allCandles[i];
        const m = allMetrics[i] || {{}};
        lines.push([
          csvCell(fmtTimeIst(c.time)),
          c.time,
          c.open,
          c.high,
          c.low,
          c.close,
          c.volume ?? "",
          m.equity ?? "",
          m.dd_pct ?? ""
        ].join(","));
      }}
      csvContent = lines.join("\\n");
      fileName = "quantdesk_bars.csv";
    }}
    const blob = new Blob([csvContent], {{ type: "text/csv;charset=utf-8;" }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }});

  // Initialize full view + optional initial focus trade
  updateHudAt(cursor);
  priceChart.fitContent();
  if (eqChart) eqChart.fitContent();
  if (initialFocusTradeId != null) {{
    focusTradeById(initialFocusTradeId);
  }}
}})();
</script>
</body>
</html>
"""
