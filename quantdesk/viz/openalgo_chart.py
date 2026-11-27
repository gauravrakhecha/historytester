"""
quantdesk.viz.openalgo_chart — Offline OpenAlgo Charts (Apache-2.0) adapter.

Builds JSON-safe payloads and self-contained HTML documents embedding the vendored
``openalgo-charts`` standalone IIFE bundle (zero external CDN requests, ``Asia/Kolkata``
timezone, dark theme matching the Streamlit app).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantdesk.config import INDICATOR_PANEL_GUIDES
from quantdesk.strategy.models import classify_columns

# Columns that are never plotted as indicator lines (aligned with quantdesk.viz.charts).
_SKIP_COLS = {
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Buy_Signal",
    "Sell_Signal",
    "OI",
    "Dividend",
    "Rights_Premium",
}

_LINE_PALETTE = (
    "#f59e0b",
    "#38bdf8",
    "#a855f7",
    "#ec4899",
    "#22d3ee",
    "#fb923c",
    "#84cc16",
    "#e879f9",
)

_ASSET_BUNDLE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "openalgo-charts"
    / "dist"
    / "openalgo-charts.standalone.js"
)
_BUNDLE_JS_CACHE: str | None = None


def _load_bundle_js() -> str:
    """Read and cache the vendored ``openalgo-charts.standalone.js`` IIFE bundle."""
    global _BUNDLE_JS_CACHE
    if _BUNDLE_JS_CACHE is None:
        if not _ASSET_BUNDLE_PATH.is_file():
            raise FileNotFoundError(
                f"Vendored openalgo-charts bundle not found at {_ASSET_BUNDLE_PATH}"
            )
        _BUNDLE_JS_CACHE = _ASSET_BUNDLE_PATH.read_text(encoding="utf-8")
    return _BUNDLE_JS_CACHE


def _finite_float(val: Any) -> float | None:
    """Return a finite Python float, or ``None`` for NaN/Inf/non-numeric values."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _index_to_unix_seconds(index: pd.Index) -> list[int | None]:
    """Convert a DataFrame index to UTC epoch seconds, treating naive timestamps as IST."""
    if len(index) == 0:
        return []
    dt_idx = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce"))
    if dt_idx.tz is None:
        dt_idx = dt_idx.tz_localize(
            "Asia/Kolkata", ambiguous="infer", nonexistent="shift_forward"
        )
    utc_idx = dt_idx.tz_convert("UTC")
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    secs = (utc_idx - epoch) // pd.Timedelta("1s")
    out: list[int | None] = []
    for is_na, val in zip(utc_idx.isna(), secs):
        out.append(None if is_na else int(val))
    return out


def _single_ts_to_unix_seconds(ts: Any) -> int | None:
    """Convert a single timestamp value to UTC epoch seconds (naive -> Asia/Kolkata)."""
    if ts is None or (isinstance(ts, float) and not math.isfinite(ts)):
        return None
    parsed = pd.to_datetime(ts, errors="coerce")
    if pd.isna(parsed):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("Asia/Kolkata")
    return int(parsed.tz_convert("UTC").timestamp())


def _build_line_points(
    times: list[int], values: pd.Series, row_positions: list[int]
) -> list[dict[str, Any]]:
    """Build ``openalgo-charts`` line data, using ``{time}`` whitespace objects for NaN gaps."""
    arr = values.to_numpy()
    points: list[dict[str, Any]] = []
    for t, pos in zip(times, row_positions):
        v = _finite_float(arr[pos])
        if v is None:
            points.append({"time": t})
        else:
            points.append({"time": t, "value": v})
    return points


def _build_markers(
    out_df: pd.DataFrame,
    pos_to_time: dict[int, int],
    trades: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Extract sorted Buy/Sell chart markers from ``trades`` or fallback Signal columns."""
    markers: list[dict[str, Any]] = []

    has_trades = isinstance(trades, pd.DataFrame) and not trades.empty
    if has_trades:
        for idx, (_, t) in enumerate(trades.iterrows()):
            size_val = _finite_float(t.get("Size", 1))
            is_short = size_val is not None and size_val < 0

            entry_time: int | None = None
            if "EntryBar" in t and not pd.isna(t["EntryBar"]):
                entry_time = pos_to_time.get(int(t["EntryBar"]))
            if entry_time is None and "EntryTime" in t:
                entry_time = _single_ts_to_unix_seconds(t["EntryTime"])

            exit_time: int | None = None
            if "ExitBar" in t and not pd.isna(t["ExitBar"]):
                exit_time = pos_to_time.get(int(t["ExitBar"]))
            if exit_time is None and "ExitTime" in t:
                exit_time = _single_ts_to_unix_seconds(t["ExitTime"])

            entry_px = _finite_float(t.get("EntryPrice"))
            exit_px = _finite_float(t.get("ExitPrice"))

            if entry_time is not None:
                lbl = "SHORT" if is_short else "BUY"
                if entry_px is not None:
                    lbl = f"{lbl} @ {entry_px:.2f}"
                markers.append(
                    {
                        "id": f"trade-{idx}-entry",
                        "time": entry_time,
                        "position": "aboveBar" if is_short else "belowBar",
                        "shape": "arrowDown" if is_short else "arrowUp",
                        "size": "medium",
                        "color": "#ff5252" if is_short else "#00e676",
                        "text": lbl,
                    }
                )

            if exit_time is not None:
                lbl = "COVER" if is_short else "SELL"
                if exit_px is not None:
                    lbl = f"{lbl} @ {exit_px:.2f}"
                markers.append(
                    {
                        "id": f"trade-{idx}-exit",
                        "time": exit_time,
                        "position": "belowBar" if is_short else "aboveBar",
                        "shape": "arrowUp" if is_short else "arrowDown",
                        "size": "medium",
                        "color": "#00e676" if is_short else "#ff5252",
                        "text": lbl,
                    }
                )

    if not markers:
        if "Buy_Signal" in out_df.columns:
            buy_arr = out_df["Buy_Signal"].to_numpy()
            for pos, flag in enumerate(buy_arr):
                if flag == 1 and pos in pos_to_time:
                    markers.append(
                        {
                            "id": f"sig-buy-{pos}",
                            "time": pos_to_time[pos],
                            "position": "belowBar",
                            "shape": "arrowUp",
                            "size": "medium",
                            "color": "#00e676",
                            "text": "BUY",
                        }
                    )
        if "Sell_Signal" in out_df.columns:
            sell_arr = out_df["Sell_Signal"].to_numpy()
            for pos, flag in enumerate(sell_arr):
                if flag == 1 and pos in pos_to_time:
                    markers.append(
                        {
                            "id": f"sig-sell-{pos}",
                            "time": pos_to_time[pos],
                            "position": "aboveBar",
                            "shape": "arrowDown",
                            "size": "medium",
                            "color": "#ff5252",
                            "text": "SELL",
                        }
                    )

    markers.sort(key=lambda m: (m["time"], m["id"]))
    return markers


def build_openalgo_payload(
    out_df: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    indicators: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe chart payload for ``openalgo-charts``.

    Separates indicator columns via ``classify_columns`` into price ``overlays`` and stacked
    sub-``panels`` (with ``INDICATOR_PANEL_GUIDES``), converts timestamps (treating tz-naive
    indices as ``Asia/Kolkata``), emits OHLCV bars, volume histogram bars, and Buy/Sell
    markers, and converts NaNs in indicator series into ``{time}`` whitespace points.
    """
    if out_df is None or out_df.empty:
        return {
            "candles": [],
            "volume": [],
            "overlays": [],
            "panels": [],
            "markers": [],
            "meta": {
                "bar_count": 0,
                "trade_count": 0,
                "marker_count": 0,
                "timezone": "Asia/Kolkata",
            },
        }

    raw_times = _index_to_unix_seconds(out_df.index)
    opens = out_df["Open"].to_numpy() if "Open" in out_df.columns else np.full(len(out_df), np.nan)
    highs = out_df["High"].to_numpy() if "High" in out_df.columns else np.full(len(out_df), np.nan)
    lows = out_df["Low"].to_numpy() if "Low" in out_df.columns else np.full(len(out_df), np.nan)
    closes = out_df["Close"].to_numpy() if "Close" in out_df.columns else np.full(len(out_df), np.nan)
    has_vol = "Volume" in out_df.columns
    vols = out_df["Volume"].to_numpy() if has_vol else None

    # Keep valid OHLC bars, deduplicating identical timestamps (latest wins) and sorting asc.
    by_time: dict[int, tuple[int, dict[str, Any], dict[str, Any] | None]] = {}
    for pos, t in enumerate(raw_times):
        if t is None:
            continue
        o = _finite_float(opens[pos])
        h = _finite_float(highs[pos])
        l = _finite_float(lows[pos])
        c = _finite_float(closes[pos])
        if o is None or h is None or l is None or c is None:
            continue
        candle: dict[str, Any] = {
            "time": t,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        }
        vol_pt: dict[str, Any] | None = None
        if vols is not None:
            v = _finite_float(vols[pos])
            if v is not None:
                candle["volume"] = v
                vol_pt = {
                    "time": t,
                    "value": v,
                    "color": "#26a69a" if c >= o else "#ef5350",
                }
        by_time[t] = (pos, candle, vol_pt)

    sorted_times = sorted(by_time.keys())
    row_positions: list[int] = []
    candles: list[dict[str, Any]] = []
    volume: list[dict[str, Any]] = []
    pos_to_time: dict[int, int] = {}

    for t in sorted_times:
        pos, candle, vol_pt = by_time[t]
        row_positions.append(pos)
        pos_to_time[pos] = t
        candles.append(candle)
        if vol_pt is not None:
            volume.append(vol_pt)

    cls = classify_columns(indicators) if indicators else {}
    overlay_cols: list[str] = []
    panel_groups: dict[str, dict[str, Any]] = {}
    for col in out_df.columns:
        if col in _SKIP_COLS:
            continue
        info = cls.get(col)
        if info and info["placement"] == "panel":
            grp = panel_groups.setdefault(
                info["group"], {"type": info["type"], "cols": []}
            )
            grp["cols"].append(col)
        else:
            overlay_cols.append(col)

    color_idx = 0
    overlays: list[dict[str, Any]] = []
    for col in overlay_cols:
        color = _LINE_PALETTE[color_idx % len(_LINE_PALETTE)]
        color_idx += 1
        overlays.append(
            {
                "name": str(col),
                "color": color,
                "data": _build_line_points(sorted_times, out_df[col], row_positions),
            }
        )

    panels: list[dict[str, Any]] = []
    for group_name, grp in panel_groups.items():
        series_list: list[dict[str, Any]] = []
        for col in grp["cols"]:
            color = _LINE_PALETTE[color_idx % len(_LINE_PALETTE)]
            color_idx += 1
            series_list.append(
                {
                    "name": str(col),
                    "color": color,
                    "data": _build_line_points(sorted_times, out_df[col], row_positions),
                }
            )
        guides = [
            float(g)
            for g in INDICATOR_PANEL_GUIDES.get(grp["type"], [])
            if _finite_float(g) is not None
        ]
        panels.append(
            {
                "name": str(group_name),
                "type": str(grp["type"]),
                "guides": guides,
                "series": series_list,
            }
        )

    markers = _build_markers(out_df, pos_to_time, trades)
    trade_count = len(trades) if isinstance(trades, pd.DataFrame) else 0

    return {
        "candles": candles,
        "volume": volume,
        "overlays": overlays,
        "panels": panels,
        "markers": markers,
        "meta": {
            "bar_count": len(candles),
            "trade_count": trade_count,
            "marker_count": len(markers),
            "timezone": "Asia/Kolkata",
        },
    }


def render_openalgo_backtest(
    out_df: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    indicators: list[dict[str, Any]] | None = None,
    height: int = 700,
) -> str:
    """Return a self-contained offline HTML document rendering ``out_df`` with OpenAlgo Charts.

    Embeds the vendored ``openalgo-charts.standalone.js`` IIFE bundle inline (zero external CDN
    calls), configures ``Asia/Kolkata`` timezone and Streamlit-matching dark theme, and renders
    candlesticks, volume histogram, price overlays, stacked indicator sub-panels with guide
    lines, and Buy/Sell trade markers.
    """
    payload = build_openalgo_payload(out_df, trades=trades, indicators=indicators)
    bundle_js = _load_bundle_js().replace("</script>", "<\\/script>")
    payload_json = json.dumps(payload, allow_nan=False).replace("</", "<\\/")

    total_h = max(360, int(height))
    header_h = 40
    chart_h = max(300, total_h - header_h)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>QuantDesk — OpenAlgo Interactive Chart</title>
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
  #oa-wrap {{
    display: flex;
    flex-direction: column;
    width: 100%;
    height: {total_h}px;
    border: 1px solid #1e2334;
    border-radius: 6px;
    overflow: hidden;
    background: #0e1117;
  }}
  #oa-toolbar {{
    height: {header_h}px;
    min-height: {header_h}px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 0 12px;
    background: #131722;
    border-bottom: 1px solid #1e2334;
    font-size: 12px;
    white-space: nowrap;
  }}
  #oa-meta {{
    display: flex;
    align-items: center;
    gap: 10px;
    overflow: hidden;
  }}
  .oa-badge {{
    padding: 2px 7px;
    border-radius: 4px;
    background: #1b2232;
    color: #93c5fd;
    font-weight: 600;
    font-size: 11px;
  }}
  #oa-readout {{
    color: #cbd5e1;
    font-variant-numeric: tabular-nums;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  #oa-controls {{
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }}
  .oa-btn {{
    background: #1b2232;
    color: #e5e7eb;
    border: 1px solid #2a3046;
    border-radius: 4px;
    padding: 4px 9px;
    font-size: 11px;
    cursor: pointer;
  }}
  .oa-btn:hover {{ background: #263047; }}
  .oa-btn.active {{ border-color: #26a69a; color: #5eead4; }}
  #oa-chart {{
    width: 100%;
    height: {chart_h}px;
    position: relative;
  }}
  #oa-empty {{
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    color: #94a3b8;
    font-size: 14px;
  }}
</style>
</head>
<body>
<div id="oa-wrap">
  <div id="oa-toolbar">
    <div id="oa-meta">
      <span class="oa-badge">IST · Asia/Kolkata</span>
      <span id="oa-readout">Bars: {payload["meta"]["bar_count"]} · Markers: {payload["meta"]["marker_count"]}</span>
    </div>
    <div id="oa-controls">
      <button id="btn-fit" class="oa-btn" type="button">Fit</button>
      <button id="btn-vol" class="oa-btn active" type="button">Volume</button>
      <button id="btn-markers" class="oa-btn active" type="button">Markers</button>
    </div>
  </div>
  <div id="oa-chart">
    <div id="oa-empty">No valid OHLCV bars available to render.</div>
  </div>
</div>
<script>{bundle_js}</script>
<script>
(function () {{
  const payload = {payload_json};
  const container = document.getElementById("oa-chart");
  const emptyEl = document.getElementById("oa-empty");
  const readoutEl = document.getElementById("oa-readout");
  const defaultReadout = "Bars: " + payload.meta.bar_count + " · Markers: " + payload.meta.marker_count;

  if (!payload.candles || payload.candles.length === 0) {{
    emptyEl.style.display = "flex";
    return;
  }}

  if (typeof OpenAlgoCharts === "undefined" || !OpenAlgoCharts.createChart) {{
    emptyEl.textContent = "OpenAlgoCharts bundle failed to initialize.";
    emptyEl.style.display = "flex";
    return;
  }}

  const {{ createChart, darkTheme }} = OpenAlgoCharts;
  const chart = createChart(container, {{
    theme: {{
      ...darkTheme,
      background: "#0e1117",
      grid: "#1b2232",
      axisLine: "#2a3046",
      paneSeparator: "#1e2334"
    }},
    timezone: "Asia/Kolkata",
    branding: false,
    grid: {{ vertLines: true, horzLines: true }}
  }});

  const candles = chart.addSeries("candlestick", {{ paneIndex: 0 }});
  candles.setData(payload.candles);

  let volSeries = null;
  let volVisible = true;
  if (payload.volume && payload.volume.length > 0) {{
    volSeries = chart.addSeries("histogram", {{
      paneIndex: 0,
      priceScaleId: "",
      priceFormat: {{ type: "volume" }},
      style: {{ base: 0 }}
    }});
    volSeries.priceScale().setOptions({{ marginTop: 0.78, marginBottom: 0 }});
    volSeries.setData(payload.volume);
  }} else {{
    document.getElementById("btn-vol").style.display = "none";
  }}

  for (const ov of payload.overlays || []) {{
    const s = chart.addSeries("line", {{
      paneIndex: 0,
      style: {{ color: ov.color, lineWidth: 1.5, title: ov.name }}
    }});
    s.setData(ov.data);
  }}

  const panels = payload.panels || [];
  if (typeof chart.setPaneWeight === "function") {{
    chart.setPaneWeight(0, 3.2);
  }}
  panels.forEach((panel, idx) => {{
    const paneIdx = idx + 1;
    for (const ps of panel.series || []) {{
      const s = chart.addSeries("line", {{
        paneIndex: paneIdx,
        style: {{ color: ps.color, lineWidth: 1.5, title: ps.name }}
      }});
      s.setData(ps.data);
    }}
    for (let gIdx = 0; gIdx < (panel.guides || []).length; gIdx++) {{
      const gy = panel.guides[gIdx];
      chart.addPriceLine(
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
    if (typeof chart.setPaneWeight === "function") {{
      chart.setPaneWeight(paneIdx, 1.25);
    }}
  }});

  const markersLayer = candles.createMarkers();
  let markersVisible = true;
  if (payload.markers && payload.markers.length > 0) {{
    markersLayer.setMarkers(payload.markers);
  }} else {{
    document.getElementById("btn-markers").style.display = "none";
  }}

  const fmtTimeIst = (unixSec) => {{
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

  chart.subscribeCrosshairMove((e) => {{
    if (!e || !e.bar) {{
      readoutEl.textContent = defaultReadout;
      return;
    }}
    const b = e.bar;
    const tStr = e.time != null ? fmtTimeIst(e.time) : "";
    const volStr = b.volume != null ? "  V:" + Number(b.volume).toLocaleString("en-IN") : "";
    readoutEl.textContent =
      tStr +
      "  O:" + Number(b.open).toFixed(2) +
      "  H:" + Number(b.high).toFixed(2) +
      "  L:" + Number(b.low).toFixed(2) +
      "  C:" + Number(b.close).toFixed(2) +
      volStr;
  }});

  document.getElementById("btn-fit").addEventListener("click", () => {{
    chart.fitContent();
  }});

  document.getElementById("btn-vol").addEventListener("click", (ev) => {{
    if (!volSeries) return;
    volVisible = !volVisible;
    volSeries.setData(volVisible ? payload.volume : []);
    ev.currentTarget.classList.toggle("active", volVisible);
  }});

  document.getElementById("btn-markers").addEventListener("click", (ev) => {{
    markersVisible = !markersVisible;
    markersLayer.setMarkers(markersVisible ? (payload.markers || []) : []);
    ev.currentTarget.classList.toggle("active", markersVisible);
  }});

  chart.fitContent();
}})();
</script>
</body>
</html>
"""
