"""Tests for QuantDesk Pro Chart Backtester Terminal (payload, HTML widget, and Streamlit UI)."""
from __future__ import annotations

import json
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

from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.models import StrategyConfig  # noqa: E402
from quantdesk.ui.components import NAV  # noqa: E402
from quantdesk.viz import charts as viz_charts  # noqa: E402
from quantdesk.viz.openalgo_terminal import (  # noqa: E402
    build_terminal_payload,
    render_openalgo_terminal,
)

ASSETS_DIR = ROOT / "quantdesk" / "viz" / "assets" / "openalgo-charts"
STANDALONE_JS = ASSETS_DIR / "dist" / "openalgo-charts.standalone.js"

INDICATORS_MIXED = [
    {"name": "EMA20", "type": "EMA", "source": "Close", "params": {"period": 20}},
    {"name": "RSI1", "type": "RSI", "source": "Close", "params": {"period": 14}},
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


def _sample_out_df(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-06-03 09:15", periods=n, freq="15min")
    rng = np.random.default_rng(7)
    base = 2500.0 + np.cumsum(rng.normal(0.0, 5.0, n))
    df = pd.DataFrame(
        {
            "Open": base,
            "High": base + 6.0,
            "Low": base - 6.0,
            "Close": base + 1.5,
            "Volume": rng.integers(1000, 10000, n),
            "EMA20": base + 0.5,
            "RSI1": np.linspace(25.0, 75.0, n),
            "Buy_Signal": 0,
            "Sell_Signal": 0,
        },
        index=idx,
    )
    df.iloc[5, df.columns.get_loc("Buy_Signal")] = 1
    df.iloc[25, df.columns.get_loc("Sell_Signal")] = 1
    return df


def _sample_stats_and_trades(df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    n = len(df)
    eq_vals = np.linspace(100000.0, 108000.0, n)
    # Introduce a mid-run dip so drawdown is negative and non-zero
    eq_vals[20:30] = np.linspace(103500.0, 99500.0, 10)
    peak = np.maximum.accumulate(eq_vals)
    dd_frac = (peak - eq_vals) / peak  # backtesting.py stores non-negative fraction [0, 1]

    eq_curve = pd.DataFrame(
        {"Equity": eq_vals, "DrawdownPct": dd_frac},
        index=df.index,
    )
    trades = pd.DataFrame(
        [
            # Deliberately out of chronological order to verify sorting by entry time
            {
                "Size": -5,
                "EntryBar": 30,
                "ExitBar": 45,
                "EntryPrice": 2520.0,
                "ExitPrice": 2540.0,
                "PnL": -100.0,
                "ReturnPct": -0.0079365,
                "TotalCharges": 24.5,
                "R_multiple": -0.8,
                "SL": 2545.0,
                "TP": 2480.0,
            },
            {
                "Size": 10,
                "EntryBar": 5,
                "ExitBar": 18,
                "EntryPrice": 2495.0,
                "ExitPrice": 2535.0,
                "PnL": 400.0,
                "ReturnPct": 0.016032,
                "TotalCharges": 31.2,
                "R_multiple": 1.6,
                "SL": 2470.0,
                "TP": 2550.0,
            },
        ]
    )
    stats = {
        "_equity_curve": eq_curve,
        "_trades": trades,
    }
    return stats, trades


def _extract_embedded_terminal_vars(html: str) -> tuple[dict, int | None]:
    tail = html.rsplit("<script>\n(function () {", 1)[-1]
    m_payload = re.search(
        r"const payload = ([^\n]+);\s*\n\s*const initialFocusTradeId = ([^\n;]+);",
        tail,
    )
    assert m_payload is not None, "Could not locate embedded payload JSON / focus ID in terminal HTML"
    raw_payload = m_payload.group(1).replace("<\\/", "</")
    return json.loads(raw_payload), json.loads(m_payload.group(2))


# ---------------------------------------------------------------------------
# 1. build_terminal_payload unit tests
# ---------------------------------------------------------------------------


def test_build_terminal_payload_alignment_enrichment_and_finite_json():
    df = _sample_out_df(60)
    stats, trades = _sample_stats_and_trades(df)

    payload = build_terminal_payload(
        df,
        stats=stats,
        trades=None,  # should resolve stats["_trades"]
        indicators=INDICATORS_MIXED,
        initial_capital=100000.0,
    )

    # Strict finite JSON check
    dumped = json.dumps(payload, allow_nan=False)
    assert "NaN" not in dumped and "Infinity" not in dumped

    assert len(payload["candles"]) == 60
    assert len(payload["volume"]) == 60
    assert [ov["name"] for ov in payload["overlays"]] == ["EMA20"]
    assert [p["name"] for p in payload["panels"]] == ["RSI1"]
    assert len(payload["markers"]) == 4

    # Equity & drawdown aligned 1-to-1 with candle timestamps
    candle_times = [c["time"] for c in payload["candles"]]
    assert [pt["time"] for pt in payload["equity"]] == candle_times
    assert [pt["time"] for pt in payload["drawdown"]] == candle_times
    assert len(payload["replay_metrics"]) == 60
    assert [m["time"] for m in payload["replay_metrics"]] == candle_times

    # Drawdown normalized into negative percentage points (0 at start, negative during dip)
    assert payload["drawdown"][0]["value"] == 0.0
    assert min(pt["value"] for pt in payload["drawdown"]) < -3.0
    assert payload["meta"]["has_equity_curve"] is True

    # trades_list sorted chronologically by entry time with 1-based IDs & enriched fields
    tlist = payload["trades_list"]
    assert len(tlist) == 2
    assert [t["id"] for t in tlist] == [1, 2]

    t1, t2 = tlist[0], tlist[1]
    assert t1["side"] == "Long"
    assert t1["pnl"] == 400.0
    assert t1["return_pct"] == pytest.approx(1.603, abs=1e-3)
    assert t1["entry_ts"] == candle_times[5]
    assert t1["exit_ts"] == candle_times[18]
    assert t1["charges"] == 31.2
    assert t1["r_multiple"] == 1.6
    assert t1["sl"] == 2470.0
    assert t1["tp"] == 2550.0

    assert t2["side"] == "Short"
    assert t2["pnl"] == -100.0
    assert t2["return_pct"] == pytest.approx(-0.794, abs=1e-3)
    assert t2["entry_ts"] == candle_times[30]
    assert t2["exit_ts"] == candle_times[45]
    assert t2["entry_ts"] > t1["entry_ts"]

    # Replay metrics accumulate closed trades and win rate accurately across bars
    assert payload["replay_metrics"][0]["closed_trades"] == 0
    assert payload["replay_metrics"][18]["closed_trades"] == 1
    assert payload["replay_metrics"][18]["win_rate_pct"] == 100.0
    assert payload["replay_metrics"][45]["closed_trades"] == 2
    assert payload["replay_metrics"][45]["win_rate_pct"] == 50.0
    assert payload["meta"]["trade_count"] == 2
    assert payload["meta"]["win_rate_pct"] == 50.0


def test_build_terminal_payload_graceful_fallbacks_and_nans():
    df = _sample_out_df(30)
    # Inject NaNs/Infs into OHLCV & indicator columns
    df.iloc[2, df.columns.get_loc("EMA20")] = np.nan
    df.iloc[4, df.columns.get_loc("RSI1")] = np.inf
    df.iloc[6, df.columns.get_loc("Volume")] = np.nan
    df.iloc[10, df.columns.get_loc("Close")] = np.nan  # bar 10 skipped

    # 1. stats=None with trades containing NaN optional fields -> fallback step equity
    trades_nan = pd.DataFrame(
        [
            {
                "Size": 5,
                "EntryBar": 3,
                "ExitBar": 12,
                "EntryPrice": 2500.0,
                "ExitPrice": 2550.0,
                "PnL": 250.0,
                "SL": np.nan,
                "TP": np.inf,
            }
        ]
    )
    p_no_stats = build_terminal_payload(
        df,
        stats=None,
        trades=trades_nan,
        indicators=INDICATORS_MIXED,
        initial_capital=50000.0,
    )
    dumped = json.dumps(p_no_stats, allow_nan=False)
    assert "NaN" not in dumped and "Infinity" not in dumped
    assert p_no_stats["meta"]["has_equity_curve"] is False
    assert len(p_no_stats["candles"]) == 29
    assert p_no_stats["equity"][0]["value"] == 50000.0
    assert p_no_stats["equity"][-1]["value"] == 50250.0
    assert p_no_stats["trades_list"][0]["return_pct"] == pytest.approx(2.0, abs=1e-3)
    assert "sl" not in p_no_stats["trades_list"][0]
    assert "tp" not in p_no_stats["trades_list"][0]

    # 2. Equity curve containing NaN / Inf rows should carry forward cleanly
    bad_eq = pd.DataFrame(
        {
            "Equity": [100000.0, np.nan, np.inf] + [101000.0] * (len(df) - 3),
            "DrawdownPct": [0.0, np.nan, np.inf] + [0.0] * (len(df) - 3),
        },
        index=df.index,
    )
    p_bad_eq = build_terminal_payload(
        df,
        stats={"_equity_curve": bad_eq},
        trades=pd.DataFrame(),
        indicators=INDICATORS_MIXED,
    )
    json.dumps(p_bad_eq, allow_nan=False)
    assert p_bad_eq["meta"]["has_equity_curve"] is True

    # 3. Empty DataFrame + stats=None
    p_empty = build_terminal_payload(pd.DataFrame(), stats=None, trades=pd.DataFrame())
    json.dumps(p_empty, allow_nan=False)
    assert p_empty["candles"] == []
    assert p_empty["equity"] == []
    assert p_empty["drawdown"] == []
    assert p_empty["trades_list"] == []
    assert p_empty["replay_metrics"] == []
    assert p_empty["meta"]["bar_count"] == 0
    assert p_empty["meta"]["trade_count"] == 0


# ---------------------------------------------------------------------------
# 2. render_openalgo_terminal unit tests
# ---------------------------------------------------------------------------


def test_render_openalgo_terminal_offline_html_replay_and_focus():
    assert viz_charts.build_terminal_payload is build_terminal_payload
    assert viz_charts.render_openalgo_terminal is render_openalgo_terminal

    df = _sample_out_df(60)
    stats, trades = _sample_stats_and_trades(df)
    html = render_openalgo_terminal(
        df,
        stats=stats,
        trades=trades,
        indicators=INDICATORS_MIXED,
        initial_capital=100000.0,
        height=820,
        initial_focus_trade=2,
    )

    assert "<!DOCTYPE html>" in html
    assert "OpenAlgoCharts" in html
    assert "Asia/Kolkata" in html
    assert "#0e1117" in html

    # Bundled JS embedded inline; zero external script/link/img or CDN URLs
    bundle_head = STANDALONE_JS.read_text(encoding="utf-8")[:120]
    assert bundle_head in html
    assert not re.search(r"<script[^>]+src\s*=", html, flags=re.IGNORECASE)
    assert not re.search(r"<link[^>]+href\s*=", html, flags=re.IGNORECASE)
    assert not re.search(r"<img[^>]+src\s*=", html, flags=re.IGNORECASE)
    lowered = html.lower()
    for cdn in _FORBIDDEN_CDN_HOSTS:
        assert cdn not in lowered, f"Unexpected CDN host {cdn!r}"
    urls = set(re.findall(r"https?://[^\s\"'<>`\\]+", html))
    unexpected = urls - _ALLOWED_NON_NETWORK_URLS
    assert not unexpected, f"Unexpected external URLs in terminal HTML: {unexpected}"

    # Replay controls, HUD, Equity/DD pane wiring, and PriceLine API
    for required_id in (
        "btn-replay-start",
        "btn-replay-play",
        "btn-replay-step",
        "sel-replay-speed",
        "replay-scrubber",
        "hud-time",
        "hud-ohlc",
        "hud-equity",
        "hud-ret",
        "hud-dd",
        "hud-trades",
        "hud-win",
        "btn-prev-trade",
        "sel-trade",
        "btn-next-trade",
        "oa-chart-main",
        "oa-chart-equity",
        "createLinkGroup",
        "addPriceLine",
        "setVisibleLogicalRange",
    ):
        assert required_id in html, f"Missing expected terminal element/API: {required_id}"

    embedded_payload, focus_id = _extract_embedded_terminal_vars(html)
    assert focus_id == 2
    assert len(embedded_payload["trades_list"]) == 2
    assert embedded_payload["trades_list"][0]["sl"] == 2470.0
    assert embedded_payload["trades_list"][0]["tp"] == 2550.0

    # Invalid/out-of-range initial_focus_trade degrades cleanly to null
    html_invalid_focus = render_openalgo_terminal(
        df, stats=stats, trades=trades, indicators=INDICATORS_MIXED, initial_focus_trade=999
    )
    _, focus_invalid = _extract_embedded_terminal_vars(html_invalid_focus)
    assert focus_invalid is None

    # Script-closing injection in overlay column is escaped
    df_xss = df.copy()
    df_xss["</script><xss>"] = df_xss["Close"]
    html_xss = render_openalgo_terminal(df_xss, stats=stats, trades=trades, indicators=[])
    assert "</script><xss>" not in html_xss
    assert "<\\/script><xss>" in html_xss

    # Empty DataFrame degrades gracefully
    html_empty = render_openalgo_terminal(pd.DataFrame(), stats=None, trades=None)
    assert "No valid OHLCV bars available to render." in html_empty


# ---------------------------------------------------------------------------
# 3. Streamlit AppTest integration (Parquet seed, Chart Terminal & Backtester)
# ---------------------------------------------------------------------------


def _make_synthetic_ohlcv_parquet(path: Path, n_bars: int = 350) -> None:
    """Create clean NSE-style 15-min OHLCV Parquet bars spanning business days with sine oscillations."""
    bdays = pd.bdate_range("2024-06-03", periods=15)
    timestamps = []
    for d in bdays:
        for k in range(25):
            timestamps.append(d + pd.Timedelta(hours=9, minutes=15 + 15 * k))
            if len(timestamps) >= n_bars:
                break
        if len(timestamps) >= n_bars:
            break

    t = np.linspace(0, 6 * np.pi, len(timestamps))
    close = 1000.0 + 45.0 * np.sin(t) + np.linspace(0.0, 12.0, len(timestamps))
    open_px = np.roll(close, 1)
    open_px[0] = close[0]
    high = np.maximum(open_px, close) + 3.0
    low = np.minimum(open_px, close) - 3.0
    vol = np.full(len(timestamps), 15000, dtype=int)

    df = pd.DataFrame(
        {
            "Timestamp": timestamps,
            "Open": np.round(open_px, 2),
            "High": np.round(high, 2),
            "Low": np.round(low, 2),
            "Close": np.round(close, 2),
            "Volume": vol,
        }
    )
    df.to_parquet(path, engine="pyarrow", index=False)


def _point_db_to_scratch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from quantdesk import config
    from quantdesk.core import db

    numba_dir = tmp_path / "numba_cache"
    numba_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_dir))

    db_path = tmp_path / "scratch_terminal.db"
    monkeypatch.setenv("MODULAR_DB_FILE", str(db_path))
    monkeypatch.setattr(config, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "_initialised", False)
    db.init_db()
    return db_path


def _seed_parquet_dataset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from quantdesk.core import db

    db_path = _point_db_to_scratch(monkeypatch, tmp_path)
    pq_path = tmp_path / "SYNTH_TERM_15Min.parquet"
    _make_synthetic_ohlcv_parquet(pq_path)
    db.log_download(
        broker="TestSeed",
        symbol="SYNTHPQ",
        key="NSE_EQ|SYNTHPQ",
        interval="15Min",
        start="2024-06-03",
        end="2024-06-21",
        path=str(pq_path),
    )
    return db_path


def _seed_builder_strategy(at) -> None:
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_fast", "type": "EMA", "source": "Close", "params": {"period": 5}},
            {"name": "EMA_slow", "type": "EMA", "source": "Close", "params": {"period": 15}},
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
    at.session_state["strategy_label"] = "EMA_fast + EMA_slow"


def test_chart_terminal_nav_and_empty_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from streamlit.testing.v1 import AppTest

    assert ("📊 Chart Backtester (Terminal)", "chart_terminal", False) in NAV

    _point_db_to_scratch(monkeypatch, tmp_path)
    at = AppTest.from_file(str(ROOT / "app_main.py"), default_timeout=180)
    at.run()
    at.radio(key="nav_radio").set_value("📊 Chart Backtester (Terminal)").run()
    assert not at.exception, f"Empty DB Chart Terminal failed: {at.exception}"
    assert any("No downloaded datasets" in m.value for m in at.info)


def test_chart_terminal_starter_template_and_backtester_unaffected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from streamlit.testing.v1 import AppTest

    db_path = _seed_parquet_dataset(monkeypatch, tmp_path)
    app_path = str(ROOT / "app_main.py")

    # 1. Chart Backtester (Terminal) with Starter Template ("Golden Cross")
    at = AppTest.from_file(app_path, default_timeout=240)
    at.run()
    at.radio(key="nav_radio").set_value("📊 Chart Backtester (Terminal)").run()
    assert not at.exception, f"Chart Terminal initial render failed: {at.exception}"

    # No Builder strategy seeded -> Starter Template is active
    src_radio = at.radio(key="ct_strategy_source")
    assert src_radio.value == "Starter Template"
    at.selectbox(key="ct_template_name").set_value("Golden Cross").run()
    assert not at.exception

    run_btns = [b for b in at.button if b.key == "ct_run_btn"]
    assert run_btns, "Expected '🚀 Run on Chart' button (key='ct_run_btn')"
    run_btns[0].click().run()
    assert not at.exception, f"Chart Terminal run failed: {at.exception}"

    # Verify KPI metrics & embedded terminal iframe
    metric_labels = [m.label for m in at.metric]
    assert metric_labels == [
        "Return %",
        "Max Drawdown %",
        "Sharpe Ratio",
        "Win Rate %",
        "# Trades",
        "Net Return %",
        "Total Charges ₹",
    ]
    iframes = at.get("iframe")
    assert len(iframes) >= 1, "Expected HTML iframe for Chart Terminal"
    iframe_srcdoc = getattr(iframes[0], "srcdoc", "")
    if iframe_srcdoc:
        assert "OpenAlgoCharts" in iframe_srcdoc
        assert "replay-scrubber" in iframe_srcdoc

    # Verify Trade Focus selector works cleanly and updates initialFocusTradeId
    focus_sb = at.selectbox(key="ct_focus_select")
    assert len(focus_sb.options) >= 2, "Expected at least 1 trade from Golden Cross on synthetic Parquet"
    focus_sb.set_value(1).run()
    assert not at.exception, f"Trade Focus selection failed: {at.exception}"
    iframes_focused = at.get("iframe")
    assert len(iframes_focused) >= 1
    focused_srcdoc = getattr(iframes_focused[0], "srcdoc", "")
    if focused_srcdoc:
        assert "const initialFocusTradeId = 1;" in focused_srcdoc

    # Reset back to All Bars (None)
    at.selectbox(key="ct_focus_select").set_value(None).run()
    assert not at.exception

    # 2. Confirm existing "🚀 Backtester" page remains unaffected
    at_bt = AppTest.from_file(app_path, default_timeout=240)
    _seed_builder_strategy(at_bt)
    at_bt.run()
    at_bt.radio(key="nav_radio").set_value("🚀 Backtester").run()
    assert not at_bt.exception, f"Backtester render failed: {at_bt.exception}"

    bt_btns = [b for b in at_bt.button if "Run Backtest" in b.label]
    assert bt_btns, "Run Backtest button missing"
    bt_btns[0].click().run()
    assert not at_bt.exception, f"Backtester run failed: {at_bt.exception}"
    assert len(at_bt.metric) >= 4
    assert any("Backtest saved to database" in m.value for m in at_bt.success)
    assert len(at_bt.get("iframe")) >= 1

    with sqlite3.connect(db_path) as conn:
        bt_rows = conn.execute("SELECT COUNT(*) FROM backtests").fetchone()[0]
    assert bt_rows >= 1
