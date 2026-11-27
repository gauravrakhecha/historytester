"""Comprehensive Wave 3 tests for quantdesk.live, OpenAlgo export, Flask blueprint, and Live Deploy UI."""
from __future__ import annotations

import ast
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _isolate_db_and_workflow(tmp_path: Path, monkeypatch) -> Path:
    from quantdesk import config as cfg_mod
    from quantdesk.core import db as db_mod, settings_repo

    db_path = tmp_path / "test_w3.db"
    wf_dir = tmp_path / ".workflow"
    wf_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg_mod, "DB_FILE", str(db_path))
    monkeypatch.setattr(cfg_mod, "WORKFLOW_DIR", wf_dir)
    monkeypatch.setattr(db_mod, "DB_FILE", str(db_path))
    monkeypatch.setattr(db_mod, "_initialised", False)
    monkeypatch.setattr(settings_repo, "_cache", None)
    db_mod.init_db()
    return db_path


def _make_golden_cross_df(trigger_buy_on_last: bool = True, periods: int = 120) -> pd.DataFrame:
    """Create a deterministic daily OHLCV DataFrame for Golden Cross (EMA_20 vs EMA_50).

    When ``trigger_buy_on_last=True``, EMA_20 stays <= EMA_50 up to bar ``-2`` and crosses
    strictly above EMA_50 on bar ``-1``, causing ``Backtest`` to emit a BUY order on the final bar.
    """
    idx = pd.date_range("2024-01-01", periods=periods, freq="B")
    closes = np.linspace(120.0, 80.0, periods, dtype=float)
    if trigger_buy_on_last:
        closes[-1] = 220.0
    else:
        closes[-1] = 79.0

    df = pd.DataFrame(
        {
            "Open": closes - 0.5,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": np.full(periods, 5000.0),
        },
        index=idx,
    )
    df.index.name = "Timestamp"
    return df


def _make_death_cross_df(periods: int = 150) -> pd.DataFrame:
    """Create a deterministic DataFrame that enters LONG earlier then exits SELL on the final bar."""
    idx = pd.date_range("2024-01-01", periods=periods, freq="B")
    closes = np.full(periods, 100.0, dtype=float)
    # 0..79: downtrend so EMA_20 < EMA_50
    closes[:80] = np.linspace(110.0, 95.0, 80)
    # 80..120: gentle uptrend so EMA_20 crosses above EMA_50 (long entry around ~99, stays within SL/TP)
    closes[80:121] = np.linspace(96.0, 101.5, 41)
    # 121..148: flat-to-slightly-softening so EMA_20 converges toward EMA_50 without hitting 3% SL
    closes[121:-1] = np.linspace(101.2, 99.6, periods - 122)
    # Final bar drops just enough (within 3% SL or triggering cross below) to close LONG
    closes[-1] = 97.5
    df = pd.DataFrame(
        {
            "Open": closes + 0.2,
            "High": closes + 0.5,
            "Low": closes - 0.5,
            "Close": closes,
            "Volume": np.full(periods, 4000.0),
        },
        index=idx,
    )
    df.index.name = "Timestamp"
    return df


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


# ---------------------------------------------------------------------------
# 0. Architectural decoupling & pyproject.toml checks
# ---------------------------------------------------------------------------

def test_zero_openalgo_and_top_level_flask_imports():
    targets = [
        ROOT / "quantdesk" / "live" / "__init__.py",
        ROOT / "quantdesk" / "live" / "openalgo_client.py",
        ROOT / "quantdesk" / "live" / "risk_guard.py",
        ROOT / "quantdesk" / "live" / "signal_runner.py",
        ROOT / "quantdesk" / "strategy" / "openalgo_export.py",
        ROOT / "quantdesk" / "integrations" / "openalgo_blueprint.py",
        ROOT / "quantdesk" / "ui" / "pages" / "live_deploy.py",
    ]
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "openalgo", f"Forbidden openalgo import in {path}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "openalgo", f"Forbidden openalgo import in {path}"

        # Top-level module statements must never eagerly import flask
        if path.name == "openalgo_blueprint.py":
            for stmt in tree.body:
                if isinstance(stmt, ast.Import):
                    assert all(a.name.split(".")[0] != "flask" for a in stmt.names)
                elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                    assert stmt.module.split(".")[0] != "flask"


def test_pyproject_toml_valid():
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    proj = data["project"]
    import quantdesk

    assert proj["name"] == "quantdesk"
    assert proj["version"] == quantdesk.__version__ == "9.0.0"
    assert proj["requires-python"] == ">=3.11"
    assert "openalgo" in proj["optional-dependencies"]
    assert any("flask" in dep.lower() for dep in proj["optional-dependencies"]["openalgo"])
    assert proj["scripts"]["quantdesk-live"] == "quantdesk.live.signal_runner:main"


# ---------------------------------------------------------------------------
# 1. quantdesk/live/openalgo_client.py
# ---------------------------------------------------------------------------

def test_openalgo_client_strict_marshmallow_payloads():
    from quantdesk.live.openalgo_client import OpenAlgoClient

    calls: list[tuple[str, dict]] = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, dict(json or {})))
        if url.endswith("/api/v1/analyzer"):
            return _FakeResponse(200, {"status": "success", "data": {"analyze_mode": True}})
        if url.endswith("/api/v1/history"):
            return _FakeResponse(200, {"status": "success", "data": []})
        return _FakeResponse(200, {"status": "success", "data": {"orderid": "ORD123"}})

    client = OpenAlgoClient("127.0.0.1:5000/", "oa-key-123", post_fn=fake_post)
    assert client.host_url == "http://127.0.0.1:5000"

    assert client.funds()["status"] == "success"
    assert client.orderbook()["status"] == "success"
    assert client.positionbook()["status"] == "success"
    assert client.tradebook()["status"] == "success"
    az = client.analyzer_status()
    assert az["analyzer_active"] is True
    assert az["mode"] == "analyze"

    # MARKET order: exact 8 keys, no optional price/trigger_price/disclosed_quantity
    res_mkt = client.place_order("RELIANCE", "NSE_EQ", "buy", 5, product="mis", pricetype="market")
    assert res_mkt["status"] == "success"

    # LIMIT order: includes price
    client.place_order("TCS", "NSE", "SELL", 2, product="CNC", pricetype="LIMIT", price=3950.5)

    # SL order: includes price, trigger_price, disclosed_quantity
    client.place_order(
        "NIFTY24JANFUT",
        "NSE_FO",
        "BUY",
        50,
        product="NRML",
        pricetype="SL",
        price=22105.0,
        trigger_price=22100.0,
        disclosed_quantity=10,
        strategy="QD_Alpha",
    )

    client.close_position("QD_Alpha")
    client.cancel_all_orders("QD_Alpha")
    client.fetch_recent_bars("RELIANCE", "NSE_EQ", interval="15m", lookback_days=5)

    expected_auth = {"apikey": "oa-key-123"}
    assert calls[0] == ("http://127.0.0.1:5000/api/v1/funds", expected_auth)
    assert calls[1] == ("http://127.0.0.1:5000/api/v1/orderbook", expected_auth)
    assert calls[2] == ("http://127.0.0.1:5000/api/v1/positionbook", expected_auth)
    assert calls[3] == ("http://127.0.0.1:5000/api/v1/tradebook", expected_auth)
    assert calls[4] == ("http://127.0.0.1:5000/api/v1/analyzer", expected_auth)

    mkt_url, mkt_body = calls[5]
    assert mkt_url == "http://127.0.0.1:5000/api/v1/placeorder"
    assert set(mkt_body.keys()) == {
        "apikey",
        "strategy",
        "symbol",
        "action",
        "exchange",
        "pricetype",
        "product",
        "quantity",
    }
    assert mkt_body["action"] == "BUY"
    assert mkt_body["exchange"] == "NSE"
    assert mkt_body["product"] == "MIS"
    assert mkt_body["pricetype"] == "MARKET"
    assert str(mkt_body["quantity"]) == "5"

    lim_url, lim_body = calls[6]
    assert lim_url == "http://127.0.0.1:5000/api/v1/placeorder"
    assert set(lim_body.keys()) == {
        "apikey",
        "strategy",
        "symbol",
        "action",
        "exchange",
        "pricetype",
        "product",
        "quantity",
        "price",
    }
    assert float(lim_body["price"]) == 3950.5

    sl_url, sl_body = calls[7]
    assert sl_url == "http://127.0.0.1:5000/api/v1/placeorder"
    assert sl_body["exchange"] == "NFO"
    assert set(sl_body.keys()) == {
        "apikey",
        "strategy",
        "symbol",
        "action",
        "exchange",
        "pricetype",
        "product",
        "quantity",
        "price",
        "trigger_price",
        "disclosed_quantity",
    }

    assert calls[8] == (
        "http://127.0.0.1:5000/api/v1/closeposition",
        {"apikey": "oa-key-123", "strategy": "QD_Alpha"},
    )
    assert calls[9] == (
        "http://127.0.0.1:5000/api/v1/cancelallorder",
        {"apikey": "oa-key-123", "strategy": "QD_Alpha"},
    )

    hist_url, hist_body = calls[10]
    assert hist_url == "http://127.0.0.1:5000/api/v1/history"
    assert set(hist_body.keys()) == {"apikey", "symbol", "exchange", "interval", "start_date", "end_date"}
    assert hist_body["exchange"] == "NSE"
    assert hist_body["interval"] == "15m"


def test_openalgo_client_validation_errors():
    from quantdesk.live.openalgo_client import OpenAlgoClient, OpenAlgoClientError

    client = OpenAlgoClient("http://127.0.0.1:5000", "k1", post_fn=lambda *a, **kw: _FakeResponse(200, {}))

    with pytest.raises(OpenAlgoClientError):
        client.place_order("", "NSE", "BUY", 1)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "HOLD", 1)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 0)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", -3)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 1.5)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", True)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 1, pricetype="GTC")
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 1, product="BO")
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 1, pricetype="LIMIT", price=None)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 1, pricetype="SL", price=100.0, trigger_price=None)
    with pytest.raises(OpenAlgoClientError):
        client.place_order("RELIANCE", "NSE", "BUY", 1, pricetype="SL-M", trigger_price=None)


def test_openalgo_client_offline_and_ist_bar_normalization():
    from quantdesk.live.openalgo_client import OpenAlgoClient

    # Offline ConnectionError & Timeout degrade gracefully
    for exc in (
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.Timeout("timed out"),
    ):
        c_down = OpenAlgoClient(
            "http://127.0.0.1:5000",
            "k1",
            post_fn=lambda *a, _e=exc, **kw: (_ for _ in ()).throw(_e),
        )
        assert c_down.funds()["status"] == "error"
        assert c_down.orderbook()["status"] == "error"
        assert c_down.positionbook()["status"] == "error"
        assert c_down.tradebook()["status"] == "error"
        az = c_down.analyzer_status()
        assert az["status"] == "error"
        assert az["analyzer_active"] is False
        bars = c_down.fetch_recent_bars("RELIANCE", "NSE")
        assert bars.empty
        assert list(bars.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert bars.index.name == "Timestamp"

    # Invalid JSON & HTTP 401
    c_bad_json = OpenAlgoClient(
        "http://127.0.0.1:5000",
        "k1",
        post_fn=lambda *a, **kw: _FakeResponse(502, ValueError("bad json")),
    )
    res_bad = c_bad_json.funds()
    assert res_bad["status"] == "error"
    assert "Invalid JSON" in res_bad["error"]

    # Blank API key does not call network
    c_nokey = OpenAlgoClient(
        "http://127.0.0.1:5000",
        "",
        post_fn=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not post")),
    )
    assert c_nokey.funds()["status"] == "error"
    assert c_nokey.fetch_recent_bars("RELIANCE", "NSE").empty

    # IST conversion, sorting, and duplicate bar deduplication (keep="last")
    # 2024-01-02 03:45:00 UTC == 2024-01-02 09:15:00 IST
    # 2024-01-02 04:00:00 UTC == 2024-01-02 09:30:00 IST
    ep1 = int(pd.Timestamp("2024-01-02 03:45:00", tz="UTC").timestamp())
    ep2 = int(pd.Timestamp("2024-01-02 04:00:00", tz="UTC").timestamp())
    c_hist = OpenAlgoClient(
        "http://127.0.0.1:5000",
        "k1",
        post_fn=lambda *a, **kw: _FakeResponse(
            200,
            {
                "status": "success",
                "data": [
                    {"timestamp": ep2, "open": 102, "high": 104, "low": 101, "close": 103, "volume": 200},
                    {"timestamp": ep1, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 100},
                    {"timestamp": ep2, "open": 102, "high": 105, "low": 101, "close": 104.5, "volume": 350},
                ],
            },
        ),
    )
    df = c_hist.fetch_recent_bars("RELIANCE", "NSE", interval="15m", lookback_days=3)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.name == "Timestamp"
    assert df.index.tz is None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.tolist() == [
        pd.Timestamp("2024-01-02 09:15:00"),
        pd.Timestamp("2024-01-02 09:30:00"),
    ]
    assert df.loc[pd.Timestamp("2024-01-02 09:30:00"), "Close"] == 104.5
    assert df.loc[pd.Timestamp("2024-01-02 09:30:00"), "Volume"] == 350.0


# ---------------------------------------------------------------------------
# 2. quantdesk/live/risk_guard.py
# ---------------------------------------------------------------------------

def test_risk_guard_blocks_all_breaches_and_allows_valid_modes():
    from quantdesk.live.risk_guard import LiveRiskConfig, LiveRiskState, evaluate_pretrade_risk

    bar_ts = "2024-01-02T09:30:00"

    # HOLD blocked
    d_hold = evaluate_pretrade_risk("HOLD", bar_ts, LiveRiskConfig(), LiveRiskState())
    assert not d_hold.allowed

    # Valid alert_only / sandbox / confirmed live allowed
    assert evaluate_pretrade_risk("BUY", bar_ts, LiveRiskConfig(execution_mode="alert_only"), LiveRiskState()).allowed
    assert evaluate_pretrade_risk("SELL", bar_ts, LiveRiskConfig(execution_mode="sandbox"), LiveRiskState()).allowed
    assert evaluate_pretrade_risk(
        "BUY",
        bar_ts,
        LiveRiskConfig(execution_mode="live", live_confirmed=True),
        LiveRiskState(),
    ).allowed

    # Unconfirmed live blocked
    d_unconf = evaluate_pretrade_risk(
        "BUY",
        bar_ts,
        LiveRiskConfig(execution_mode="live", live_confirmed=False),
        LiveRiskState(),
    )
    assert not d_unconf.allowed
    assert "live_confirmed" in d_unconf.reason

    # Daily max-loss breach blocked
    d_loss = evaluate_pretrade_risk(
        "BUY",
        bar_ts,
        LiveRiskConfig(execution_mode="sandbox", daily_loss_limit=5000.0),
        LiveRiskState(daily_pnl=-5000.0),
    )
    assert not d_loss.allowed
    assert "loss" in d_loss.reason.lower()

    # Max trades/day blocked on entry, allowed on exit
    cfg_lim = LiveRiskConfig(execution_mode="sandbox", max_trades_per_day=5, max_open_positions=2, cooldown_seconds=0)
    st_trades = LiveRiskState(trades_today=5, open_positions=1)
    assert not evaluate_pretrade_risk("BUY", bar_ts, cfg_lim, st_trades, is_exit=False).allowed
    assert evaluate_pretrade_risk("SELL", bar_ts, cfg_lim, st_trades, is_exit=True).allowed

    # Max open positions blocked on entry, allowed on exit
    st_pos = LiveRiskState(trades_today=1, open_positions=2)
    d_pos = evaluate_pretrade_risk("BUY", bar_ts, cfg_lim, st_pos, is_exit=False)
    assert not d_pos.allowed
    assert "positions" in d_pos.reason.lower()
    assert evaluate_pretrade_risk("SELL", bar_ts, cfg_lim, st_pos, is_exit=True).allowed

    # Duplicate-bar replay blocked
    d_dup = evaluate_pretrade_risk(
        "BUY",
        bar_ts,
        LiveRiskConfig(execution_mode="sandbox", cooldown_seconds=0),
        LiveRiskState(last_signal_bar_ts=bar_ts),
    )
    assert not d_dup.allowed
    assert "duplicate" in d_dup.reason.lower()

    # Cooldown violation blocked until elapsed
    cfg_cd = LiveRiskConfig(execution_mode="sandbox", cooldown_seconds=60)
    st_cd = LiveRiskState(last_order_monotonic=100.0)
    d_cd_block = evaluate_pretrade_risk("BUY", bar_ts, cfg_cd, st_cd, now_mono=130.0)
    assert not d_cd_block.allowed
    assert "cooldown" in d_cd_block.reason.lower()
    d_cd_ok = evaluate_pretrade_risk("BUY", bar_ts, cfg_cd, st_cd, now_mono=165.0)
    assert d_cd_ok.allowed


# ---------------------------------------------------------------------------
# 3. quantdesk/live/signal_runner.py
# ---------------------------------------------------------------------------

def test_signal_runner_golden_cross_parity_step_modes_and_jsonl(tmp_path, monkeypatch):
    import quantdesk.config as qconfig
    from quantdesk.live.openalgo_client import OpenAlgoClient
    from quantdesk.live.risk_guard import LiveRiskConfig, LiveRiskState
    from quantdesk.live.signal_runner import (
        LiveDeploymentSpec,
        evaluate_strategy_on_bars,
        main as runner_main,
        read_live_events,
        step_once,
    )
    from quantdesk.strategy.templates import get_template

    _isolate_db_and_workflow(tmp_path, monkeypatch)
    golden = get_template("Golden Cross")

    # BUY crossover parity
    buy_df = _make_golden_cross_df(trigger_buy_on_last=True)
    sig_buy = evaluate_strategy_on_bars(golden, buy_df)
    assert sig_buy["error"] is None
    assert sig_buy["action"] == "BUY"
    assert sig_buy["position_side"] == "FLAT"
    assert "EMA_20" in sig_buy["indicators"] and "EMA_50" in sig_buy["indicators"]

    # HOLD when no crossover on final bar
    hold_df = _make_golden_cross_df(trigger_buy_on_last=False)
    sig_hold = evaluate_strategy_on_bars(golden, hold_df)
    assert sig_hold["error"] is None
    assert sig_hold["action"] == "HOLD"

    # SELL crossover parity
    sell_df = _make_death_cross_df()
    sig_sell = evaluate_strategy_on_bars(golden, sell_df)
    assert sig_sell["error"] is None
    assert sig_sell["action"] == "SELL"

    # Insufficient bars returns clean error
    sig_err = evaluate_strategy_on_bars(golden, buy_df.iloc[:1])
    assert sig_err["action"] == "HOLD"
    assert sig_err["error"] is not None

    # step_once in alert_only: zero order POSTs, logs to WORKFLOW_DIR/live_events.jsonl
    posted_urls: list[str] = []

    def fail_on_placeorder(url, json=None, timeout=None):
        posted_urls.append(url)
        if url.endswith("/api/v1/placeorder"):
            raise AssertionError("alert_only must never call /api/v1/placeorder")
        return _FakeResponse(200, {"status": "success", "data": {}})

    alert_client = OpenAlgoClient("http://127.0.0.1:5000", "oa-key", post_fn=fail_on_placeorder)
    alert_spec = LiveDeploymentSpec(
        strategy_cfg=golden,
        symbol="RELIANCE",
        exchange="NSE",
        interval="D",
        quantity=2,
        risk_config=LiveRiskConfig(execution_mode="alert_only"),
    )
    alert_state = LiveRiskState()
    ev_alert = step_once(alert_spec, alert_client, alert_state, bars_df=buy_df)
    assert ev_alert["action"] == "BUY"
    assert ev_alert["risk_allowed"] is True
    assert ev_alert["order_placed"] is False
    assert ev_alert["order_result"]["status"] == "skipped"
    assert posted_urls == []

    # Verify default WORKFLOW_DIR JSONL log created & readable
    default_log = qconfig.WORKFLOW_DIR / "live_events.jsonl"
    assert default_log.is_file()
    events = read_live_events(limit=10)
    assert len(events) == 1
    assert events[0]["action"] == "BUY"

    # Duplicate bar replay halted on same state
    ev_dup = step_once(alert_spec, alert_client, alert_state, bars_df=buy_df)
    assert ev_dup["risk_allowed"] is False
    assert "duplicate" in ev_dup["risk_reason"].lower()

    # Risk halt on daily loss breach in sandbox -> zero order POSTs
    halted_state = LiveRiskState(daily_pnl=-6000.0)
    sb_calls: list[tuple[str, dict]] = []

    def sandbox_post(url, json=None, timeout=None):
        sb_calls.append((url, dict(json or {})))
        if url.endswith("/api/v1/analyzer"):
            return _FakeResponse(200, {"status": "success", "data": {"analyze_mode": True}})
        if url.endswith("/api/v1/placeorder"):
            return _FakeResponse(200, {"status": "success", "orderid": "SB-001"})
        if url.endswith("/api/v1/history"):
            return _FakeResponse(200, {"status": "success", "data": []})
        return _FakeResponse(200, {"status": "success"})

    sb_client = OpenAlgoClient("http://127.0.0.1:5000", "oa-key", post_fn=sandbox_post)
    sb_spec = LiveDeploymentSpec(
        strategy_cfg=golden,
        symbol="RELIANCE",
        exchange="NSE",
        interval="D",
        quantity=3,
        risk_config=LiveRiskConfig(execution_mode="sandbox", daily_loss_limit=5000.0),
    )
    ev_halt = step_once(sb_spec, sb_client, halted_state, bars_df=buy_df)
    assert ev_halt["risk_allowed"] is False
    assert ev_halt["order_placed"] is False
    assert sb_calls == []

    # Sandbox mode with INACTIVE analyzer blocks place_order
    inactive_client = OpenAlgoClient(
        "http://127.0.0.1:5000",
        "oa-key",
        post_fn=lambda url, json=None, timeout=None: _FakeResponse(
            200, {"status": "success", "data": {"analyze_mode": False, "mode": "live"}}
        ),
    )
    ev_no_az = step_once(sb_spec, inactive_client, LiveRiskState(), bars_df=buy_df)
    assert ev_no_az["risk_allowed"] is True
    assert ev_no_az["order_placed"] is False
    assert ev_no_az["order_result"]["status"] == "error"
    assert "sandbox" in ev_no_az["order_result"]["error"].lower()

    # Sandbox mode with ACTIVE analyzer places mocked order + updates risk_state + posts webhook
    webhook_hits: list[dict] = []
    monkeypatch.setattr(
        "quantdesk.live.signal_runner.requests.post",
        lambda url, json=None, timeout=None: (webhook_hits.append({"url": url, "json": json}) or _FakeResponse(200, {})),
    )
    sb_state = LiveRiskState()
    ev_sb = step_once(
        sb_spec,
        sb_client,
        sb_state,
        bars_df=buy_df,
        webhook_url="https://example.test/webhook",
    )
    assert ev_sb["risk_allowed"] is True
    assert ev_sb["order_placed"] is True
    assert ev_sb["order_result"]["orderid"] == "SB-001"
    assert ev_sb["webhook_status"] == "http_200"
    assert sb_state.trades_today == 1
    assert sb_state.open_positions == 1
    assert [u for u, _ in sb_calls] == [
        "http://127.0.0.1:5000/api/v1/analyzer",
        "http://127.0.0.1:5000/api/v1/placeorder",
    ]
    assert sb_calls[1][1]["action"] == "BUY"
    assert str(sb_calls[1][1]["quantity"]) == "3"
    assert len(webhook_hits) == 1

    # Newest-first ordering from read_live_events
    all_events = read_live_events(limit=10)
    assert len(all_events) == 5
    assert all_events[0]["order_placed"] is True

    # CLI --once entrypoint runs cleanly
    monkeypatch.setenv("OPENALGO_API_KEY", "oa-key")
    monkeypatch.setattr("quantdesk.live.signal_runner.OpenAlgoClient", lambda host_url, api_key: sb_client)
    rc = runner_main(["--preset", "Golden Cross", "--symbol", "RELIANCE", "--mode", "alert_only", "--once"])
    assert rc == 0


# ---------------------------------------------------------------------------
# 4. quantdesk/strategy/openalgo_export.py & openalgo_blueprint.py
# ---------------------------------------------------------------------------

def test_openalgo_export_ast_clean_no_secrets_and_validates(monkeypatch):
    from quantdesk.strategy.models import StrategyConfig
    from quantdesk.strategy.openalgo_export import generate_openalgo_script
    from quantdesk.strategy.templates import get_template
    from quantdesk.strategy.validation import PresetValidationError

    golden_dict = get_template("Golden Cross")
    golden_cfg = StrategyConfig.from_dict(golden_dict)

    script = generate_openalgo_script(
        golden_cfg,
        symbol="RELIANCE",
        exchange="NSE_EQ",
        interval="15m",
        quantity=2,
        product="MIS",
        strategy_name="QD_Golden",
        host_url="http://127.0.0.1:5000/",
        max_daily_loss=2500.0,
    )
    tree = ast.parse(script)

    # Zero hardcoded secrets; reads OPENALGO_API_KEY from os.getenv
    assert 'os.getenv("OPENALGO_API_KEY")' in script
    assert "oa-secret" not in script
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and "KEY" in tgt.id.upper() and isinstance(node.value, ast.Constant):
                    assert not node.value.value, f"Hardcoded key literal in {tgt.id}"

    # Execute generated module in isolated namespace and verify evaluate_signal + daily loss check
    ns: dict = {}
    exec(compile(tree, "<exported_openalgo>", "exec"), ns)
    sig = ns["evaluate_signal"](_make_golden_cross_df(True), ns["STRATEGY_CONFIG"])
    assert sig["action"] == "BUY"
    ok_loss, _ = ns["check_daily_loss"](-3000.0, 2500.0)
    assert ok_loss is False

    # Rejects unsafe preset
    bad_preset = dict(golden_dict)
    bad_preset["indicators"] = [
        {"name": "Evil", "type": "Custom Formula", "source": "Close", "formula": "__import__('os').system('dir')", "params": {}}
    ]
    with pytest.raises(PresetValidationError):
        generate_openalgo_script(bad_preset)


def test_openalgo_blueprint_headless_backtest_and_lazy_flask(monkeypatch):
    from quantdesk.integrations.openalgo_blueprint import (
        create_openalgo_blueprint,
        run_headless_backtest,
    )
    from quantdesk.strategy.templates import get_template
    from quantdesk.strategy.validation import PresetValidationError

    golden = get_template("Golden Cross")
    df = _make_golden_cross_df(trigger_buy_on_last=True)
    # Use a couple waves so trades open & close cleanly
    df2 = pd.concat([df, _make_death_cross_df()])
    df2.index = pd.date_range("2024-01-01", periods=len(df2), freq="B")

    records = [
        {
            "timestamp": ts.isoformat(),
            "open": float(r["Open"]),
            "high": float(r["High"]),
            "low": float(r["Low"]),
            "close": float(r["Close"]),
            "volume": float(r["Volume"]),
        }
        for ts, r in df2.iterrows()
    ]
    res = run_headless_backtest(golden, records, initial_capital=250000.0, commission=0.0005)
    assert set(res.keys()) >= {"metrics", "trades", "equity", "drawdown", "code", "meta"}
    assert isinstance(res["metrics"]["return_pct"], float)
    assert isinstance(res["equity"], list) and len(res["equity"]) > 0
    assert isinstance(res["trades"], list) and len(res["trades"]) >= 1
    assert "class VisualStrategy" in res["code"]
    # Must be strictly JSON serializable
    dumped = json.dumps(res)
    assert len(dumped) > 100

    # Malicious preset rejected with PresetValidationError
    bad_preset = dict(golden)
    bad_preset["indicators"] = [
        {"name": "Bad", "type": "Custom Formula", "source": "Close", "formula": "open('/etc/passwd')", "params": {}}
    ]
    with pytest.raises(PresetValidationError):
        run_headless_backtest(bad_preset, records)

    # Lazy Flask ImportError when flask is unavailable
    monkeypatch.setitem(sys.modules, "flask", None)
    with pytest.raises(ImportError, match="Flask is required"):
        create_openalgo_blueprint()


def test_openalgo_blueprint_flask_routes_with_mocked_flask(monkeypatch):
    from quantdesk.integrations.openalgo_blueprint import create_openalgo_blueprint
    from quantdesk.strategy.templates import get_template

    routes: dict[tuple[str, tuple[str, ...]], object] = {}

    class _FakeBlueprint:
        def __init__(self, name, import_name, url_prefix=""):
            self.name = name
            self.url_prefix = url_prefix
            self.routes = routes

        def route(self, rule, methods=None):
            m = tuple(methods or ("GET",))

            def dec(fn):
                routes[(rule, m)] = fn
                return fn

            return dec

    fake_req = SimpleNamespace(get_json=lambda silent=True: None)
    fake_flask = SimpleNamespace(
        Blueprint=_FakeBlueprint,
        jsonify=lambda obj: obj,
        request=fake_req,
    )
    monkeypatch.setitem(sys.modules, "flask", fake_flask)

    bp = create_openalgo_blueprint(url_prefix="/qd")
    assert bp.url_prefix == "/qd"

    health_body, health_code = routes[("/health", ("GET",))]()
    assert health_code == 200
    assert health_body["status"] == "ok"

    # Malicious preset via POST /api/backtest -> HTTP 400
    bad_preset = dict(get_template("Golden Cross"))
    bad_preset["indicators"] = [
        {"name": "Evil", "type": "Custom Formula", "source": "Close", "formula": "__import__('sys')", "params": {}}
    ]
    fake_req.get_json = lambda silent=True: {
        "preset": bad_preset,
        "ohlcv_records": [
            {"timestamp": "2024-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10},
            {"timestamp": "2024-01-02", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 10},
        ],
    }
    err_body, err_code = routes[("/api/backtest", ("POST",))]()
    assert err_code == 400
    assert err_body["status"] == "error"


# ---------------------------------------------------------------------------
# 5. quantdesk/ui/pages/live_deploy.py & components.py (Streamlit AppTest)
# ---------------------------------------------------------------------------

def test_live_deploy_nav_and_apptest_dry_run_and_export(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from quantdesk.core import db as db_mod
    from quantdesk.live.signal_runner import read_live_events
    from quantdesk.ui.components import NAV

    assert len(NAV) == 16
    assert ("🚦 Live Monitor & Deploy", "live_deploy", False) in NAV

    _isolate_db_and_workflow(tmp_path, monkeypatch)
    monkeypatch.setattr("quantdesk.ui.components._load_dotenv_if_present", lambda: None)
    monkeypatch.delenv("OPENALGO_HOST_URL", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)

    # Seed scratch Parquet dataset that produces a Golden Cross BUY signal on the last bar
    pq_path = tmp_path / "RELIANCE_Day.parquet"
    gc_df = _make_golden_cross_df(trigger_buy_on_last=True).reset_index()
    gc_df.to_parquet(pq_path, index=False)
    db_mod.log_download("OpenAlgo", "RELIANCE", "NSE_EQ|RELIANCE", "Day", "2024-01-01", "2024-11-18", str(pq_path))

    app_path = str(ROOT / "app_main.py")
    at = AppTest.from_file(app_path, default_timeout=90)
    at.run()

    at.radio(key="nav_radio").set_value("🚦 Live Monitor & Deploy").run()
    assert not at.exception
    assert any("Live Monitor & Deploy" in t.value for t in at.title)

    # Refresh telemetry gracefully offline
    at.button(key="ld_refresh_btn").click().run()
    assert not at.exception

    # Choose Starter Template -> Golden Cross, alert_only mode, run 1-cycle dry-run
    at.radio(key="ld_strategy_source").set_value("Starter Template").run()
    at.selectbox(key="ld_starter_template").set_value("Golden Cross").run()
    assert at.selectbox(key="ld_exec_mode").value == "alert_only"

    # Script export button is enabled and code preview parses cleanly via ast.parse with no hardcoded secrets
    export_btns = [b for b in at.button if "Export Standalone OpenAlgo Script" in b.label]
    if export_btns:
        assert not export_btns[0].disabled
    assert len(at.code) >= 1
    exported_src = at.code[0].value
    ast.parse(exported_src)
    assert 'os.getenv("OPENALGO_API_KEY")' in exported_src

    # Click Run 1-Cycle Signal & Risk Check
    at.button(key="ld_step_btn").click().run()
    assert not at.exception
    assert any("Local Dataset Dry-Run" in i.value for i in at.info)

    step_ev = at.session_state["ld_last_step_event"]
    assert step_ev["execution_mode"] == "alert_only"
    assert step_ev["action"] == "BUY"
    assert step_ev["order_placed"] is False
    assert step_ev["risk_allowed"] is True

    metric_labels = [m.label for m in at.metric]
    for expected in ("Signal Action", "Last Close", "Position Side", "Risk Gate"):
        assert expected in metric_labels

    # JSONL event recorded in isolated scratch WORKFLOW_DIR and shown in Tab 3
    logged = read_live_events(limit=10)
    assert len(logged) == 1
    assert logged[0]["symbol"] == "RELIANCE"
    assert len(at.dataframe) >= 1

    # Selecting live mode reveals live_confirmed safety checkbox
    at.selectbox(key="ld_exec_mode").set_value("live").run()
    assert not at.exception
    assert at.checkbox(key="ld_live_confirmed").value is False
