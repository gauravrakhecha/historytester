"""Tests for the offline OpenAlgo Charts integration (assets, adapter, and Streamlit UI)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantdesk.config import INDICATOR_PANEL_GUIDES  # noqa: E402
from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.models import StrategyConfig  # noqa: E402
from quantdesk.viz import charts as viz_charts  # noqa: E402
from quantdesk.viz.openalgo_chart import (  # noqa: E402
    build_openalgo_payload,
    render_openalgo_backtest,
)

ASSETS_DIR = ROOT / "quantdesk" / "viz" / "assets" / "openalgo-charts"
STANDALONE_JS = ASSETS_DIR / "dist" / "openalgo-charts.standalone.js"
MJS_BUNDLE = ASSETS_DIR / "dist" / "openalgo-charts.mjs"

INDICATORS_MIXED = [
    {"name": "EMA20", "type": "EMA", "source": "Close", "params": {"period": 20}},
    {"name": "BB", "type": "Bollinger Bands", "source": "Close", "params": {"period": 20, "std_dev": 2.0}},
    {"name": "RSI1", "type": "RSI", "source": "Close", "params": {"period": 14}},
    {"name": "MACD1", "type": "MACD", "source": "Close", "params": {"fast": 12, "slow": 26, "sign": 9}},
    {"name": "STOCH", "type": "Stochastic Oscillator", "source": "Close", "params": {"k": 14, "d": 3, "smooth_k": 3}},
]

_ALLOWED_NON_NETWORK_URLS = {
    "http://www.w3.org/2000/svg",
    "https://openalgo.in",
    "https://github.com/marketcalls/openalgo-charts",
}

_FORBIDDEN_CDN_HOSTS = (
    "unpkg.com",
    "jsdelivr.net",
    "cdnjs.cloudflare.com",
    "cdn.plot.ly",
    "cdn.bokeh.org",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "ajax.googleapis.com",
    "stackpath.bootstrapcdn.com",
)


def _sample_out_df(n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02 09:15", periods=n, freq="15min")
    rng = np.random.default_rng(42)
    base = 2500.0 + np.cumsum(rng.normal(0.0, 4.0, n))
    df = pd.DataFrame(
        {
            "Open": base,
            "High": base + 6.0,
            "Low": base - 6.0,
            "Close": base + 1.5,
            "Volume": rng.integers(1000, 10000, n),
            "EMA20": base + 0.5,
            "BB_high": base + 15.0,
            "BB_low": base - 15.0,
            "RSI1": np.linspace(25.0, 75.0, n),
            "MACD1_line": rng.normal(0.0, 1.5, n),
            "MACD1_signal": rng.normal(0.0, 1.0, n),
            "STOCH_k": np.linspace(15.0, 85.0, n),
            "STOCH_d": np.linspace(20.0, 80.0, n),
            "Buy_Signal": 0,
            "Sell_Signal": 0,
            "OI": np.full(n, 5000),
        },
        index=idx,
    )
    df.iloc[10, df.columns.get_loc("Buy_Signal")] = 1
    df.iloc[30, df.columns.get_loc("Sell_Signal")] = 1
    return df


def _extract_embedded_payload(html: str) -> dict:
    tail = html.rsplit("<script>\n(function () {", 1)[-1]
    m = re.search(
        r'const payload = ([^\n]+);\s*\n\s*const container = document\.getElementById\("oa-chart"\);',
        tail,
    )
    assert m is not None, "Could not locate embedded payload JSON in rendered HTML"
    raw = m.group(1).replace("<\\/", "</")
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 1. Bundled assets & zero-CDN offline HTML
# ---------------------------------------------------------------------------

def test_vendored_openalgo_assets_exist_and_valid():
    assert ASSETS_DIR.is_dir()
    assert STANDALONE_JS.is_file() and STANDALONE_JS.stat().st_size > 100_000
    assert MJS_BUNDLE.is_file() and MJS_BUNDLE.stat().st_size > 100_000
    assert (ASSETS_DIR / "LICENSE").is_file()
    assert (ASSETS_DIR / "NOTICE").is_file()
    assert (ASSETS_DIR / "package.json").is_file()

    license_txt = (ASSETS_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_txt

    pkg = json.loads((ASSETS_DIR / "package.json").read_text(encoding="utf-8"))
    assert pkg.get("name") == "openalgo-charts"

    js_txt = STANDALONE_JS.read_text(encoding="utf-8")
    assert "OpenAlgoCharts" in js_txt
    assert "</script>" not in js_txt
    assert not re.search(r"^\s*(import|export)\s+", js_txt, flags=re.MULTILINE)


def test_rendered_html_is_offline_with_zero_cdn_urls():
    df = _sample_out_df(60)
    html = render_openalgo_backtest(df, trades=None, indicators=INDICATORS_MIXED, height=760)

    assert "<!DOCTYPE html>" in html
    assert "OpenAlgoCharts" in html
    assert "Asia/Kolkata" in html
    assert "#0e1117" in html

    # No external <script src=...>, <link href=...>, or <img src=...> tags.
    assert not re.search(r"<script[^>]+src\s*=", html, flags=re.IGNORECASE)
    assert not re.search(r"<link[^>]+href\s*=", html, flags=re.IGNORECASE)
    assert not re.search(r"<img[^>]+src\s*=", html, flags=re.IGNORECASE)

    # Zero CDN hosts anywhere in the HTML.
    lowered = html.lower()
    for cdn in _FORBIDDEN_CDN_HOSTS:
        assert cdn not in lowered, f"Unexpected CDN host {cdn!r} in rendered HTML"

    # Any http(s):// strings in the embedded bundle must only be known non-network metadata/SVG NS URIs.
    urls = set(re.findall(r"https?://[^\s\"'<>`\\]+", html))
    unexpected = urls - _ALLOWED_NON_NETWORK_URLS
    assert not unexpected, f"Unexpected external URLs in offline HTML: {unexpected}"

    # Re-exports on quantdesk.viz.charts must work identically and keep render_plotly_backtest intact.
    assert viz_charts.build_openalgo_payload is build_openalgo_payload
    assert viz_charts.render_openalgo_backtest is render_openalgo_backtest
    fig = viz_charts.render_plotly_backtest(df, trades=None, indicators=INDICATORS_MIXED)
    assert any(t.type == "candlestick" for t in fig.data)


# ---------------------------------------------------------------------------
# 2. Payload & HTML adapter behavior
# ---------------------------------------------------------------------------

def test_payload_overlays_vs_panels_and_indicator_guides():
    df = _sample_out_df(60)
    payload = build_openalgo_payload(df, trades=None, indicators=INDICATORS_MIXED)

    assert payload["meta"]["bar_count"] == 60
    assert payload["meta"]["timezone"] == "Asia/Kolkata"
    assert len(payload["candles"]) == 60
    assert len(payload["volume"]) == 60

    overlay_names = [ov["name"] for ov in payload["overlays"]]
    assert overlay_names == ["EMA20", "BB_high", "BB_low"]
    for skip_col in ("Open", "High", "Low", "Close", "Volume", "Buy_Signal", "Sell_Signal", "OI"):
        assert skip_col not in overlay_names

    panel_map = {p["name"]: p for p in payload["panels"]}
    assert list(panel_map.keys()) == ["RSI1", "MACD1", "STOCH"]

    assert [s["name"] for s in panel_map["RSI1"]["series"]] == ["RSI1"]
    assert panel_map["RSI1"]["guides"] == [float(g) for g in INDICATOR_PANEL_GUIDES["RSI"]]

    assert [s["name"] for s in panel_map["MACD1"]["series"]] == ["MACD1_line", "MACD1_signal"]
    assert panel_map["MACD1"]["guides"] == [float(g) for g in INDICATOR_PANEL_GUIDES["MACD"]]

    assert [s["name"] for s in panel_map["STOCH"]["series"]] == ["STOCH_k", "STOCH_d"]
    assert panel_map["STOCH"]["guides"] == [float(g) for g in INDICATOR_PANEL_GUIDES["Stochastic Oscillator"]]

    html = render_openalgo_backtest(df, trades=None, indicators=INDICATORS_MIXED, height=820)
    embedded = _extract_embedded_payload(html)
    assert [p["name"] for p in embedded["panels"]] == ["RSI1", "MACD1", "STOCH"]
    assert "addPriceLine" in html


def test_payload_nans_infs_timestamps_and_empty_dataframe():
    # Out-of-order + duplicate timestamps, NaN/Inf indicators, NaN OHLC row, NaN volume row.
    idx = pd.DatetimeIndex(
        [
            "2024-01-02 09:45",
            "2024-01-02 09:15",
            "2024-01-02 09:30",
            "2024-01-02 09:30",  # duplicate timestamp -> latest row wins
            "2024-01-02 10:00",
        ]
    )
    df = pd.DataFrame(
        {
            "Open": [102.0, 100.0, 101.0, 101.5, 103.0],
            "High": [104.0, 102.0, 103.0, 103.5, 105.0],
            "Low": [101.0, 99.0, 100.0, 100.5, 102.0],
            "Close": [103.0, 101.0, 102.0, 102.5, np.nan],  # 10:00 has NaN Close -> skipped
            "Volume": [500.0, np.nan, 300.0, 350.0, 600.0],  # 09:15 NaN Volume omitted
            "EMA20": [np.inf, np.nan, 101.0, 101.8, 104.0],
            "RSI1": [55.0, np.nan, 48.0, 50.0, 60.0],
        },
        index=idx,
    )
    inds = [
        {"name": "EMA20", "type": "EMA", "source": "Close", "params": {"period": 20}},
        {"name": "RSI1", "type": "RSI", "source": "Close", "params": {"period": 14}},
    ]
    payload = build_openalgo_payload(df, trades=pd.DataFrame(), indicators=inds)

    # Strict JSON (no NaN/Infinity tokens)
    dumped = json.dumps(payload, allow_nan=False)
    assert "NaN" not in dumped and "Infinity" not in dumped

    # 3 unique valid timestamps sorted ascending: 09:15, 09:30 (deduped to 101.5), 09:45
    times = [c["time"] for c in payload["candles"]]
    assert times == sorted(times)
    assert len(times) == 3
    expected_0915_utc = int(pd.Timestamp("2024-01-02 03:45:00", tz="UTC").timestamp())
    assert times[0] == expected_0915_utc
    assert payload["candles"][1]["open"] == 101.5
    # Volume omits 09:15 NaN bar
    assert [v["time"] for v in payload["volume"]] == times[1:]

    # Leading NaN at 09:15 and Inf at 09:45 become {time} whitespace objects
    ema_pts = payload["overlays"][0]["data"]
    assert ema_pts[0] == {"time": times[0]}
    assert ema_pts[1] == {"time": times[1], "value": 101.8}
    assert ema_pts[2] == {"time": times[2]}

    rsi_pts = payload["panels"][0]["series"][0]["data"]
    assert rsi_pts[0] == {"time": times[0]}
    assert rsi_pts[1] == {"time": times[1], "value": 50.0}

    # Empty DataFrame degrades gracefully
    empty_payload = build_openalgo_payload(pd.DataFrame(), trades=pd.DataFrame(), indicators=inds)
    assert empty_payload["candles"] == []
    assert empty_payload["meta"]["bar_count"] == 0
    empty_html = render_openalgo_backtest(pd.DataFrame(), trades=None, indicators=inds)
    assert "No valid OHLCV bars available to render." in empty_html


def test_payload_markers_from_trades_and_empty_trades_fallback():
    df = _sample_out_df(40)

    # 1. Explicit trades (1 Long via EntryBar/ExitBar + 1 Short via EntryTime/ExitTime)
    trades = pd.DataFrame(
        [
            {
                "Size": 10,
                "EntryBar": 5,
                "ExitBar": 15,
                "EntryPrice": 2505.25,
                "ExitPrice": 2520.50,
            },
            {
                "Size": -5,
                "EntryTime": df.index[20],
                "ExitTime": df.index[28],
                "EntryPrice": 2515.0,
                "ExitPrice": 2498.0,
            },
        ]
    )
    payload = build_openalgo_payload(df, trades=trades, indicators=INDICATORS_MIXED)
    markers = payload["markers"]
    assert len(markers) == 4
    assert [m["time"] for m in markers] == sorted(m["time"] for m in markers)
    assert markers[0]["position"] == "belowBar" and markers[0]["shape"] == "arrowUp"
    assert markers[0]["text"].startswith("BUY @ 2505.25")
    assert markers[1]["position"] == "aboveBar" and markers[1]["shape"] == "arrowDown"
    assert markers[1]["text"].startswith("SELL @ 2520.50")
    assert markers[2]["position"] == "aboveBar" and markers[2]["shape"] == "arrowDown"
    assert markers[2]["text"].startswith("SHORT @ 2515.00")
    assert markers[3]["position"] == "belowBar" and markers[3]["shape"] == "arrowUp"
    assert markers[3]["text"].startswith("COVER @ 2498.00")

    # 2. Empty trades falls back to Buy_Signal / Sell_Signal
    payload_fallback = build_openalgo_payload(df, trades=pd.DataFrame(), indicators=INDICATORS_MIXED)
    assert [m["text"] for m in payload_fallback["markers"]] == ["BUY", "SELL"]

    # 3. No trades and no signal columns -> empty markers
    df_nosig = df.drop(columns=["Buy_Signal", "Sell_Signal"])
    payload_none = build_openalgo_payload(df_nosig, trades=None, indicators=INDICATORS_MIXED)
    assert payload_none["markers"] == []

    # 4. Script-closing tag in an overlay name is safely escaped
    df_xss = df_nosig.copy()
    df_xss["</script><evil>"] = df_xss["Close"]
    html_xss = render_openalgo_backtest(df_xss, trades=None, indicators=[])
    assert "</script><evil>" not in html_xss
    assert "<\\/script><evil>" in html_xss


# ---------------------------------------------------------------------------
# 3. Streamlit AppTest integration (plotter.py & backtester.py)
# ---------------------------------------------------------------------------

def _make_synthetic_ohlcv_csv(path: Path, n_bars: int = 320) -> None:
    """Create clean NSE-style 15-min OHLCV bars spanning business days with sine oscillations."""
    # 25 bars per business day (09:15 .. 15:15 IST) so quality score stays high and crossovers trigger.
    bdays = pd.bdate_range("2024-06-03", periods=14)
    timestamps = []
    for d in bdays:
        for k in range(25):
            timestamps.append(d + pd.Timedelta(hours=9, minutes=15 + 15 * k))
            if len(timestamps) >= n_bars:
                break
        if len(timestamps) >= n_bars:
            break

    t = np.linspace(0, 6 * np.pi, len(timestamps))
    close = 1000.0 + 40.0 * np.sin(t) + np.linspace(0.0, 10.0, len(timestamps))
    open_px = np.roll(close, 1)
    open_px[0] = close[0]
    high = np.maximum(open_px, close) + 3.0
    low = np.minimum(open_px, close) - 3.0
    vol = np.full(len(timestamps), 15000, dtype=int)

    df = pd.DataFrame(
        {
            "Timestamp": [ts.strftime("%Y-%m-%d %H:%M:%S") for ts in timestamps],
            "Open": np.round(open_px, 2),
            "High": np.round(high, 2),
            "Low": np.round(low, 2),
            "Close": np.round(close, 2),
            "Volume": vol,
        }
    )
    df.to_csv(path, index=False)


def _seed_scratch_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point QuantDesk DB & Numba cache to a temporary scratch DB and seed 1 OHLCV dataset."""
    from quantdesk import config
    from quantdesk.core import db

    scratch_root = tmp_path
    numba_dir = scratch_root / "numba_cache"
    numba_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_dir))

    db_path = scratch_root / "scratch_quantdesk.db"
    csv_path = scratch_root / "SYNTH_15Min.csv"
    _make_synthetic_ohlcv_csv(csv_path)

    monkeypatch.setenv("MODULAR_DB_FILE", str(db_path))
    monkeypatch.setattr(config, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "_initialised", False)

    db.init_db()
    db.log_download(
        broker="TestSeed",
        symbol="SYNTH",
        key="NSE_EQ|SYNTH",
        interval="15Min",
        start="2024-06-03",
        end="2024-06-20",
        path=str(csv_path),
    )
    return db_path


def _seed_strategy_session(at) -> None:
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_fast", "type": "EMA", "source": "Close", "params": {"period": 5}},
            {"name": "EMA_slow", "type": "EMA", "source": "Close", "params": {"period": 15}},
            {"name": "RSI_14", "type": "RSI", "source": "Close", "params": {"period": 14}},
        ],
        buy_rules=[
            {"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Above", "var2": "EMA_slow", "is_num": False}
        ],
        sell_rules=[
            {"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Below", "var2": "EMA_slow", "is_num": False}
        ],
    )
    at.session_state["custom_strategy_code"] = build_strategy_code(cfg)
    at.session_state["indicators"] = cfg.indicators
    at.session_state["buy_rules"] = cfg.buy_rules
    at.session_state["sell_rules"] = cfg.sell_rules
    at.session_state["strategy_label"] = "EMA_fast + EMA_slow + RSI_14"


def test_plotter_and_backtester_apptest_openalgo_and_plotly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from streamlit.testing.v1 import AppTest

    from quantdesk.backtest import engine

    db_path = _seed_scratch_db(monkeypatch, tmp_path)
    app_path = str(ROOT / "app_main.py")

    # Ensure engine.native_bokeh_html wasn't deleted (still callable/present for legacy tests).
    assert callable(getattr(engine, "native_bokeh_html", None))

    # --- 1. Plotter page: Signal Sheet in OpenAlgo Interactive (Default) + Plotly ---
    at_plot = AppTest.from_file(app_path, default_timeout=180)
    _seed_strategy_session(at_plot)
    at_plot.run()
    at_plot.radio(key="nav_radio").set_value("📈 Strategy & Indicator Plotter").run()
    assert not at_plot.exception, f"Plotter render failed: {at_plot.exception}"

    engine_radio = at_plot.radio(key="plotter_chart_engine")
    assert engine_radio.value == "OpenAlgo Interactive (Default)"
    assert list(engine_radio.options) == ["OpenAlgo Interactive (Default)", "Plotly"]

    gen_btns = [b for b in at_plot.button if "Generate Signal Sheet" in b.label]
    assert gen_btns, "Generate Signal Sheet button missing"
    gen_btns[0].click().run()
    assert not at_plot.exception, f"Signal Sheet (OpenAlgo) failed: {at_plot.exception}"
    assert len(at_plot.metric) >= 3
    assert any("Signal sheet generated:" in m.value for m in at_plot.success)
    assert any("OpenAlgo" in s.value for s in at_plot.subheader)
    iframes = at_plot.get("iframe")
    assert len(iframes) >= 1, "Expected components.html iframe for OpenAlgo Plotter chart"

    # Toggle to Plotly and re-run Signal Sheet
    at_plot.radio(key="plotter_chart_engine").set_value("Plotly").run()
    gen_btns = [b for b in at_plot.button if "Generate Signal Sheet" in b.label]
    gen_btns[0].click().run()
    assert not at_plot.exception, f"Signal Sheet (Plotly) failed: {at_plot.exception}"
    assert any("Plotly" in s.value for s in at_plot.subheader)

    # --- 2. Backtester page: Run Backtest in OpenAlgo Interactive (Default) + Plotly ---
    at_bt = AppTest.from_file(app_path, default_timeout=240)
    _seed_strategy_session(at_bt)
    at_bt.run()
    at_bt.radio(key="nav_radio").set_value("🚀 Backtester").run()
    assert not at_bt.exception, f"Backtester render failed: {at_bt.exception}"

    bt_radio = at_bt.radio(key="backtester_chart_engine")
    assert bt_radio.value == "OpenAlgo Interactive (Default)"
    assert list(bt_radio.options) == ["OpenAlgo Interactive (Default)", "Plotly"]
    assert not any("Pro" in cb.label and "chart" in cb.label.lower() for cb in at_bt.checkbox)

    run_btns = [b for b in at_bt.button if "Run Backtest" in b.label]
    assert run_btns, "Run Backtest button missing"
    run_btns[0].click().run()
    assert not at_bt.exception, f"Backtester (OpenAlgo) failed: {at_bt.exception}"
    assert len(at_bt.metric) >= 4
    assert any("Backtest saved to database" in m.value for m in at_bt.success)
    assert any("OpenAlgo" in s.value for s in at_bt.subheader)
    bt_iframes = at_bt.get("iframe")
    assert len(bt_iframes) >= 1, "Expected components.html iframe for OpenAlgo Backtester chart"

    # Toggle to Plotly and re-run Backtest
    at_bt.radio(key="backtester_chart_engine").set_value("Plotly").run()
    run_btns = [b for b in at_bt.button if "Run Backtest" in b.label]
    run_btns[0].click().run()
    assert not at_bt.exception, f"Backtester (Plotly) failed: {at_bt.exception}"
    assert any("Backtest saved to database" in m.value for m in at_bt.success)
    assert any("Plotly" in s.value for s in at_bt.subheader)

    # Verify DB recorded both plot_file modes cleanly in the scratch DB
    with sqlite3.connect(db_path) as conn:
        plot_files = [
            row[0]
            for row in conn.execute("SELECT plot_file FROM backtests ORDER BY id ASC").fetchall()
        ]
    assert plot_files == ["openalgo_interactive", "plotly_native"]
