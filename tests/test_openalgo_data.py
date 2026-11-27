"""Comprehensive Wave 2 tests for OpenAlgo broker, catalog, sidecar manager, and Data Hub UI."""
from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers for socket-free async testing & DB isolation
# ---------------------------------------------------------------------------

def _run_coro_sync(coro):
    """Drive a coroutine to completion without creating an OS event-loop socketpair."""
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


def _isolate_db(tmp_path: Path, monkeypatch) -> Path:
    from quantdesk import config as C
    from quantdesk.core import db as db_mod, settings_repo

    db_path = tmp_path / "test_w2.db"
    monkeypatch.setattr(C, "DB_FILE", str(db_path))
    monkeypatch.setattr(db_mod, "DB_FILE", str(db_path))
    monkeypatch.setattr(db_mod, "_initialised", False)
    monkeypatch.setattr(settings_repo, "_cache", None)
    db_mod.init_db()
    return db_path


def _make_ohlcv(start: str, periods: int, freq: str = "D", base_close: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq=freq)
    closes = [base_close + i for i in range(periods)]
    return pd.DataFrame(
        {
            "Timestamp": idx,
            "Open": [c - 1.0 for c in closes],
            "High": [c + 2.0 for c in closes],
            "Low": [c - 2.0 for c in closes],
            "Close": closes,
            "Volume": [1000 + i * 10 for i in range(periods)],
        }
    )


# ---------------------------------------------------------------------------
# 1. quantdesk/brokers/openalgo.py + downloader.py + data/master_list.py
# ---------------------------------------------------------------------------

def test_normalize_host_url_and_exchange():
    from quantdesk.brokers.openalgo import normalize_host_url, normalize_openalgo_exchange

    assert normalize_host_url(None) == "http://127.0.0.1:5000"
    assert normalize_host_url("   ") == "http://127.0.0.1:5000"
    assert normalize_host_url("http://127.0.0.1:5000/") == "http://127.0.0.1:5000"
    assert normalize_host_url("127.0.0.1:5001/") == "http://127.0.0.1:5001"
    assert normalize_host_url("https://algo.example.com///") == "https://algo.example.com"

    cases = {
        "NSE_EQ": "NSE",
        "NSE_FO": "NFO",
        "NSE_FO_FUT": "NFO",
        "NSE_FO_OPT": "NFO",
        "BSE_EQ": "BSE",
        "BSE_FO": "BFO",
        "MCX_FO": "MCX",
        "MCX_COMM": "MCX",
        "NCD_FO": "CDS",
        "CDS": "CDS",
        "NSE_INDEX": "NSE_INDEX",
        "BSE_INDEX": "BSE_INDEX",
        "UNKNOWN_SEG": "NSE",
        "": "NSE",
    }
    for raw, expected in cases.items():
        assert normalize_openalgo_exchange(raw) == expected
    assert normalize_openalgo_exchange(None) == "NSE"

    # Row precedence: openalgo_exchange -> segment -> exchange
    assert normalize_openalgo_exchange({"openalgo_exchange": "MCX_COMM", "segment": "NSE_EQ"}) == "MCX"
    assert normalize_openalgo_exchange({"openalgo_exchange": float("nan"), "segment": "NSE_FO", "exchange": "NSE"}) == "NFO"
    assert normalize_openalgo_exchange({"exchange": "BSE_FO"}) == "BFO"
    assert normalize_openalgo_exchange({}) == "NSE"


def test_openalgo_strict_marshmallow_payloads(monkeypatch):
    from quantdesk.brokers import openalgo as oa

    sent: list[tuple[str, dict]] = []

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, json=None, timeout=None):
        sent.append((url, dict(json or {})))
        if url.endswith("/api/v1/ping"):
            return _Resp(200, {"status": "success", "data": {"broker": "zerodha", "message": "pong"}})
        if url.endswith("/api/v1/analyzer"):
            return _Resp(200, {"status": "success", "data": {"analyze_mode": True}})
        if url.endswith("/api/v1/search"):
            return _Resp(200, {"status": "success", "data": [{"symbol": "RELIANCE", "exchange": "NSE"}]})
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(oa.requests, "post", fake_post)

    ok, info = oa.ping_openalgo("http://127.0.0.1:5000/", "my-oa-key")
    assert ok is True
    assert info["broker"] == "zerodha"
    assert info["analyzer_active"] is True

    hits_no_exch = oa.search_openalgo_symbols("http://127.0.0.1:5000", "my-oa-key", "RELIANCE")
    assert len(hits_no_exch) == 1
    assert hits_no_exch[0]["tradingsymbol"] == "RELIANCE"

    oa.search_openalgo_symbols("http://127.0.0.1:5000", "my-oa-key", "NIFTY", exchange="NSE_FO")

    assert sent[0] == ("http://127.0.0.1:5000/api/v1/ping", {"apikey": "my-oa-key"})
    assert sent[1] == ("http://127.0.0.1:5000/api/v1/analyzer", {"apikey": "my-oa-key"})
    assert sent[2] == ("http://127.0.0.1:5000/api/v1/search", {"apikey": "my-oa-key", "query": "RELIANCE"})
    assert sent[3] == (
        "http://127.0.0.1:5000/api/v1/search",
        {"apikey": "my-oa-key", "query": "NIFTY", "exchange": "NFO"},
    )

    # Strict Marshmallow payload check for /api/v1/history
    hist_calls: list[tuple[str, dict]] = []

    class _AsyncResp:
        status = 200
        headers = {}

        async def json(self):
            return {"status": "success", "data": []}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _AsyncSession:
        def post(self, url, json=None):
            hist_calls.append((url, dict(json or {})))
            return _AsyncResp()

    _run_coro_sync(
        oa.fetch_single(
            _AsyncSession(),
            {"tradingsymbol": "TCS", "segment": "NSE_EQ"},
            "1 Minute",
            dt.date(2024, 1, 2),
            dt.date(2024, 1, 5),
            {"host_url": "http://127.0.0.1:5000/", "api_key": "my-oa-key"},
        )
    )
    assert len(hist_calls) == 1
    hist_url, hist_payload = hist_calls[0]
    assert hist_url == "http://127.0.0.1:5000/api/v1/history"
    assert set(hist_payload.keys()) == {"apikey", "symbol", "exchange", "interval", "start_date", "end_date"}
    assert hist_payload == {
        "apikey": "my-oa-key",
        "symbol": "TCS",
        "exchange": "NSE",
        "interval": "1m",
        "start_date": "2024-01-02",
        "end_date": "2024-01-05",
    }


def test_ping_openalgo_success_and_failure_modes(monkeypatch):
    import requests as req_mod
    from quantdesk.brokers import openalgo as oa

    # Empty key fails without touching network
    monkeypatch.setattr(
        oa.requests,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Should not call HTTP when api_key is blank")),
    )
    ok, info = oa.ping_openalgo("http://127.0.0.1:5000", "  ")
    assert ok is False
    assert "required" in info["message"].lower()

    # Live mode success
    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def live_post(url, json=None, timeout=None):
        if url.endswith("/ping"):
            return _Resp(200, {"status": "success", "data": {"broker": "upstox", "message": "pong"}})
        return _Resp(200, {"status": "success", "data": {"analyze_mode": False, "mode": "live"}})

    monkeypatch.setattr(oa.requests, "post", live_post)
    ok, info = oa.ping_openalgo("http://127.0.0.1:5000", "k1")
    assert ok is True
    assert info["broker"] == "upstox"
    assert info["analyzer_active"] is False
    assert info["mode"] == "live"
    assert isinstance(info["latency_ms"], float)

    # ConnectionError, Timeout, and HTTP 401
    for exc in (req_mod.exceptions.ConnectionError("refused"), req_mod.exceptions.Timeout("slow")):
        monkeypatch.setattr(oa.requests, "post", lambda *a, _e=exc, **kw: (_ for _ in ()).throw(_e))
        ok, info = oa.ping_openalgo("http://127.0.0.1:5000", "k1")
        assert ok is False
        assert info["message"]

    monkeypatch.setattr(
        oa.requests,
        "post",
        lambda *a, **kw: _Resp(401, {"status": "error", "message": "Invalid API Key"}),
    )
    ok, info = oa.ping_openalgo("http://127.0.0.1:5000", "bad")
    assert ok is False
    assert "Invalid API Key" in info["message"]


def test_openalgo_fetch_single_ist_conversion_and_401_fast_fail(monkeypatch):
    from quantdesk.brokers import downloader as D
    from quantdesk.brokers import openalgo as oa
    from quantdesk.brokers.base import NonRetryableError

    assert D._FETCHERS["OpenAlgo"] is oa.fetch_single

    # 2024-01-02 03:45:00 UTC == 2024-01-02 09:15:00 IST
    # 2024-01-02 04:00:00 UTC == 2024-01-02 09:30:00 IST
    ep1 = int(pd.Timestamp("2024-01-02 03:45:00", tz="UTC").timestamp())
    ep2 = int(pd.Timestamp("2024-01-02 04:00:00", tz="UTC").timestamp())

    class _FakeResp:
        def __init__(self, status, body):
            self.status = status
            self._body = body
            self.headers = {}

        async def json(self):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeSession:
        def __init__(self, status=200, body=None):
            self._status = status
            self._body = body or {}

        def post(self, url, json=None):
            return _FakeResp(self._status, self._body)

    # Returned out-of-order to verify ascending sort + OI inclusion
    df = _run_coro_sync(
        oa.fetch_single(
            _FakeSession(
                200,
                {
                    "status": "success",
                    "data": [
                        {"timestamp": ep2, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 200, "oi": 5000},
                        {"timestamp": ep1, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 150, "oi": 4900},
                    ],
                },
            ),
            {"tradingsymbol": "NIFTY24JANFUT", "segment": "NSE_FO"},
            "1 Minute",
            dt.date(2024, 1, 2),
            dt.date(2024, 1, 2),
            {"host_url": "http://127.0.0.1:5000", "api_key": "k"},
        )
    )
    assert list(df.columns) == ["Timestamp", "Open", "High", "Low", "Close", "Volume", "OI"]
    assert df["Timestamp"].dt.tz is None
    assert df["Timestamp"].tolist() == [
        pd.Timestamp("2024-01-02 09:15:00"),
        pd.Timestamp("2024-01-02 09:30:00"),
    ]
    assert df["OI"].tolist() == [4900, 5000]

    # HTTP 401 fast-fails with NonRetryableError in fetch_single AND in 1 attempt via fetch_chunk_async
    with pytest.raises(NonRetryableError):
        _run_coro_sync(
            oa.fetch_single(
                _FakeSession(401, {"status": "error"}),
                {"tradingsymbol": "RELIANCE", "segment": "NSE_EQ"},
                "Day",
                dt.date(2024, 1, 2),
                dt.date(2024, 1, 5),
                {"host_url": "http://127.0.0.1:5000", "api_key": "bad"},
            )
        )

    class _DummySem:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    from quantdesk.brokers.base import ChunkDownloadError

    with pytest.raises(ChunkDownloadError) as exc_info:
        _run_coro_sync(
            D.fetch_chunk_async(
                _FakeSession(401, {"status": "error"}),
                _DummySem(),
                "OpenAlgo",
                {"tradingsymbol": "RELIANCE", "segment": "NSE_EQ"},
                "Day",
                dt.date(2024, 1, 2),
                dt.date(2024, 1, 5),
                {"host_url": "http://127.0.0.1:5000", "api_key": "bad"},
            )
        )
    assert exc_info.value.attempts == 1
    assert isinstance(exc_info.value.last_error, NonRetryableError)

    # Socket-free fetch_chunk_async success path (no OI when omitted)
    df_chunk = _run_coro_sync(
        D.fetch_chunk_async(
            _FakeSession(
                200,
                {
                    "status": "success",
                    "data": [
                        {"timestamp": ep1, "open": 2500, "high": 2510, "low": 2490, "close": 2505, "volume": 1000}
                    ],
                },
            ),
            _DummySem(),
            "OpenAlgo",
            {"tradingsymbol": "RELIANCE", "segment": "NSE_EQ"},
            "Day",
            dt.date(2024, 1, 2),
            dt.date(2024, 1, 2),
            {"host_url": "http://127.0.0.1:5000", "api_key": "k"},
        )
    )
    assert len(df_chunk) == 1
    assert df_chunk["Timestamp"].dt.tz is None
    assert df_chunk["Timestamp"].iloc[0] == pd.Timestamp("2024-01-02 09:15:00")
    assert "OI" not in df_chunk.columns


def test_master_list_openalgo_support(monkeypatch):
    from quantdesk.data import master_list as ml

    def _clear_resource_cache(fn):
        for cell in fn.__closure__ or ():
            val = cell.cell_contents
            if isinstance(val, dict) and "store" in val:
                val["store"].clear()
                val["impl"] = None

    _clear_resource_cache(ml.get_unified_master_list)

    # 1. Offline fallback when Upstox master is unavailable
    monkeypatch.setattr(ml, "_disk_load", lambda broker: None)
    monkeypatch.setattr(ml, "_disk_save", lambda broker, df: None)
    monkeypatch.setattr(ml, "_load_upstox_master", lambda: pd.DataFrame())

    fb = ml.get_unified_master_list("OpenAlgo")
    assert not fb.empty
    assert "openalgo_exchange" in fb.columns
    assert set(fb["openalgo_exchange"].unique()) == {"NSE"}
    assert "RELIANCE" in fb["tradingsymbol"].values

    _clear_resource_cache(ml.get_unified_master_list)

    # 2. Vectorised exchange mapping over Upstox-shaped master rows
    sample_upstox = pd.DataFrame(
        {
            "tradingsymbol": ["RELIANCE", "NIFTY24JANFUT", "GOLD24FEBFUT", "Nifty 50"],
            "name": ["Reliance", "Nifty Fut", "Gold Fut", "Nifty 50"],
            "segment": ["NSE_EQ", "NSE_FO", "MCX_FO", "NSE_INDEX"],
            "exchange": ["NSE", "NSE", "MCX", "NSE"],
            "instrument_type": ["EQUITY", "FUT", "FUT", "INDEX"],
            "instrument_key": ["NSE_EQ|INE002A01018", "NSE_FO|123", "MCX_FO|456", "NSE_INDEX|Nifty 50"],
            "asset_class": ["Stock", "Futures", "Commodity", "Index"],
        }
    )
    monkeypatch.setattr(ml, "_load_upstox_master", lambda: sample_upstox)
    mapped = ml.get_unified_master_list("OpenAlgo")
    assert mapped["openalgo_exchange"].tolist() == ["NSE", "NFO", "MCX", "NSE_INDEX"]

    _clear_resource_cache(ml.get_unified_master_list)


# ---------------------------------------------------------------------------
# 2. quantdesk/data/catalog.py
# ---------------------------------------------------------------------------

def test_catalog_build_and_quality_on_seeded_db(tmp_path, monkeypatch):
    from quantdesk.core import db
    from quantdesk.data.catalog import build_catalog_df

    _isolate_db(tmp_path, monkeypatch)

    pq_path = tmp_path / "RELIANCE_Day.parquet"
    df = _make_ohlcv("2024-01-01", 10, freq="B", base_close=2500.0)
    df.to_parquet(pq_path, index=False)

    # Insert two rows for same (broker, symbol, interval) + one missing-file row
    db.log_download("Upstox", "RELIANCE", "NSE_EQ|INE002A01018", "Day", "2024-01-01", "2024-01-05", str(pq_path))
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO downloads_log
               (broker, symbol, instrument_key, interval, start_date, end_date, file_path, downloaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("Upstox", "RELIANCE", "NSE_EQ|INE002A01018", "Day", "2024-01-01", "2024-01-12", str(pq_path), "2099-01-01 00:00:00"),
        )
    db.log_download("OpenAlgo", "MISSING_SYM", "NSE_EQ|MISSING", "Day", "2024-01-01", "2024-01-05", str(tmp_path / "missing.parquet"))

    cat = build_catalog_df(include_quality=False)
    assert len(cat) == 2
    rel = cat[cat["symbol"] == "RELIANCE"].iloc[0]
    assert rel["bars"] == 10
    assert bool(rel["file_exists"]) is True
    assert rel["size_kb"] > 0
    assert rel["actual_start"] == dt.date(2024, 1, 1)
    assert rel["actual_end"] == dt.date(2024, 1, 12)
    assert str(rel["logged_end"]) == "2024-01-12"

    miss = cat[cat["symbol"] == "MISSING_SYM"].iloc[0]
    assert bool(miss["file_exists"]) is False
    assert miss["bars"] == 0

    cat_q = build_catalog_df(include_quality=True)
    assert "quality_score" in cat_q.columns
    assert "missing_days" in cat_q.columns
    rel_q = cat_q[cat_q["symbol"] == "RELIANCE"].iloc[0]
    assert float(rel_q["quality_score"]) > 0


def test_compute_incremental_range_rewind_guard():
    from quantdesk.data.catalog import compute_incremental_range

    assert compute_incremental_range(dt.date(2024, 5, 10), dt.date(2024, 5, 15)) == (
        dt.date(2024, 5, 8),
        dt.date(2024, 5, 15),
    )
    # Capped when target_end < actual_end - lookback_guard_days
    assert compute_incremental_range(dt.date(2024, 5, 15), dt.date(2024, 5, 10)) == (
        dt.date(2024, 5, 10),
        dt.date(2024, 5, 10),
    )
    # None actual_end falls back to target_end - guard
    assert compute_incremental_range(None, dt.date(2024, 5, 15), lookback_guard_days=3) == (
        dt.date(2024, 5, 12),
        dt.date(2024, 5, 15),
    )


def test_top_up_entry_overwrites_provisional_bar_and_updates_log(tmp_path, monkeypatch):
    from quantdesk.brokers.downloader import DownloadResult
    from quantdesk.core import db
    from quantdesk.data import catalog

    db_path = _isolate_db(tmp_path, monkeypatch)

    pq_path = tmp_path / "TCS_Day.parquet"
    existing = pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(["2024-05-07", "2024-05-08", "2024-05-10"]),
            "Open": [3800.0, 3810.0, 3820.0],
            "High": [3815.0, 3825.0, 3830.0],
            "Low": [3790.0, 3800.0, 3810.0],
            "Close": [3805.0, 3815.0, 3822.0],  # provisional 2024-05-10 bar
            "Volume": [100, 110, 50],
        }
    )
    existing.to_parquet(pq_path, index=False)
    db.log_download("OpenAlgo", "TCS", "NSE_EQ|TCS", "Day", "2024-05-07", "2024-05-10", str(pq_path))

    captured = {}

    def fake_run_async_download(broker, row, interval, start_dt, end_dt, auth_dict, **kwargs):
        captured["broker"] = broker
        captured["start_dt"] = start_dt
        captured["end_dt"] = end_dt
        fresh = pd.DataFrame(
            {
                "Timestamp": pd.to_datetime(["2024-05-08", "2024-05-10", "2024-05-13"]),
                "Open": [3810.0, 3820.0, 3850.0],
                "High": [3825.0, 3860.0, 3890.0],
                "Low": [3800.0, 3810.0, 3840.0],
                "Close": [3815.0, 3855.0, 3880.0],  # 2024-05-10 overwritten cleanly!
                "Volume": [110, 5000, 6200],
            }
        )
        return DownloadResult(fresh, [], start_dt, end_dt)

    monkeypatch.setattr(catalog, "run_async_download", fake_run_async_download)

    cat_row = {
        "broker": "OpenAlgo",
        "symbol": "TCS",
        "instrument_key": "NSE_EQ|TCS",
        "interval": "Day",
        "actual_end": dt.date(2024, 5, 10),
        "file_path": str(pq_path),
    }
    res = catalog.top_up_entry(
        cat_row,
        target_end_date=dt.date(2024, 5, 13),
        auth_dict={"broker": "OpenAlgo", "host_url": "http://127.0.0.1:5000", "api_key": "k"},
        parquet_dir=str(tmp_path),
    )

    assert captured["start_dt"] == dt.date(2024, 5, 8)
    assert captured["end_dt"] == dt.date(2024, 5, 13)
    assert res["complete"] is True
    assert res["failed_ranges"] == []
    assert res["added_rows"] == 1
    assert res["total_rows"] == 4

    updated = pd.read_parquet(res["file_path"])
    assert len(updated) == 4
    assert updated["Timestamp"].is_unique
    assert updated["Timestamp"].is_monotonic_increasing
    row_may10 = updated[updated["Timestamp"] == pd.Timestamp("2024-05-10")].iloc[0]
    assert row_may10["Close"] == 3855.0
    assert row_may10["Volume"] == 5000

    with sqlite3.connect(db_path) as conn:
        logs = conn.execute("SELECT start_date, end_date, file_path FROM downloads_log WHERE symbol='TCS'").fetchall()
    assert len(logs) == 2
    assert logs[-1][0] == "2024-05-07"
    assert logs[-1][1] == "2024-05-13"


def test_export_catalog_zip_returns_valid_zip(tmp_path):
    from quantdesk.data.catalog import export_catalog_zip

    f1 = tmp_path / "dir_a" / "SBIN.parquet"
    f2 = tmp_path / "dir_b" / "SBIN.parquet"
    f3 = tmp_path / "INFY.csv"
    f1.parent.mkdir()
    f2.parent.mkdir()
    _make_ohlcv("2024-01-01", 3).to_parquet(f1, index=False)
    _make_ohlcv("2024-01-05", 2).to_parquet(f2, index=False)
    f3.write_text("Timestamp,Open,High,Low,Close,Volume\n", encoding="utf-8")

    raw_zip = export_catalog_zip([str(f1), str(f2), str(f3), str(tmp_path / "does_not_exist.parquet")])
    assert isinstance(raw_zip, bytes) and len(raw_zip) > 0

    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        names = zf.namelist()
        assert len(names) == 3
        assert "INFY.csv" in names
        assert "SBIN.parquet" in names
        assert "dir_b_SBIN.parquet" in names


# ---------------------------------------------------------------------------
# 3. quantdesk/integrations/openalgo_manager.py
# ---------------------------------------------------------------------------

def test_openalgo_manager_status_and_isolated_install_commands(tmp_path, monkeypatch):
    import ast
    from quantdesk.brokers import openalgo as oa_broker
    from quantdesk.integrations import openalgo_manager as mgr

    # Loose-coupling check: zero `import openalgo` / `from openalgo ...` AST nodes
    tree = ast.parse(Path(mgr.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "openalgo" for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "openalgo"

    sidecar_dir = tmp_path / "services" / "openalgo"

    # Unreachable report
    monkeypatch.setattr(mgr, "is_port_open", lambda host, port, timeout=0.6: False)
    down = mgr.get_openalgo_status("http://127.0.0.1:5000", "key123", services_dir=sidecar_dir)
    assert down["reachable"] is False
    assert down["ping_ok"] is False
    assert down["sidecar_installed"] is False
    assert down["sidecar_running"] is False
    assert "not reachable" in down["message"].lower()

    # Mocked healthy report with isolated sidecar venv present
    (sidecar_dir / ".venv").mkdir(parents=True)
    monkeypatch.setattr(mgr, "is_port_open", lambda host, port, timeout=0.6: True)
    monkeypatch.setattr(
        oa_broker,
        "ping_openalgo",
        lambda host, key: (
            True,
            {
                "latency_ms": 14.2,
                "broker": "zerodha",
                "analyzer_active": True,
                "mode": "analyze",
                "message": "pong",
            },
        ),
    )
    up = mgr.get_openalgo_status("http://127.0.0.1:5000", "key123", services_dir=sidecar_dir)
    assert up["reachable"] is True
    assert up["ping_ok"] is True
    assert up["latency_ms"] == 14.2
    assert up["broker"] == "zerodha"
    assert up["analyzer_active"] is True
    assert up["sidecar_installed"] is True
    assert up["sidecar_running"] is True

    # Install commands target services/openalgo/.venv and never QuantDesk's .venv
    cmds = mgr.build_openalgo_install_commands()
    joined = "\n".join(cmds)
    expected_sidecar = str(Path("services") / "openalgo")
    assert expected_sidecar in joined
    assert str(Path("services") / "openalgo" / ".venv") in joined
    assert str(mgr.ROOT_DIR / ".venv") not in joined


# ---------------------------------------------------------------------------
# 4. quantdesk/ui/components.py + quantdesk/ui/pages/data_hub.py (AppTest)
# ---------------------------------------------------------------------------

def test_sidebar_broker_radio_and_openalgo_connect_apptest(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from quantdesk.brokers import openalgo as oa_broker
    from quantdesk.core import settings_repo

    _isolate_db(tmp_path, monkeypatch)
    monkeypatch.setattr("quantdesk.ui.components._load_dotenv_if_present", lambda: None)
    monkeypatch.delenv("OPENALGO_HOST_URL", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)

    app_path = str(Path(__file__).resolve().parents[1] / "app_main.py")
    at = AppTest.from_file(app_path, default_timeout=60)
    at.run()

    broker_radio = at.sidebar.radio(key="broker_radio")
    assert list(broker_radio.options) == ["Upstox", "OpenAlgo"]

    # Switch to OpenAlgo & test failed ping
    broker_radio.set_value("OpenAlgo").run()
    monkeypatch.setattr(
        oa_broker,
        "ping_openalgo",
        lambda host, key: (False, {"message": "OpenAlgo HTTP 401"}),
    )
    at.sidebar.text_input(key="openalgo_host_input").set_value("http://127.0.0.1:5000/").run()
    at.sidebar.text_input(key="openalgo_api_key_input").set_value("bad_key").run()
    at.sidebar.button(key="openalgo_connect_btn").click().run()
    assert not at.exception
    assert any("HTTP 401" in e.value for e in at.sidebar.error)
    assert at.session_state["auth_dict"] == {}

    # Mock healthy ping -> sets auth_dict + persists host & key
    monkeypatch.setattr(
        oa_broker,
        "ping_openalgo",
        lambda host, key: (
            True,
            {"latency_ms": 9.5, "broker": "dhan", "analyzer_active": True, "mode": "analyze", "message": "pong"},
        ),
    )
    at.sidebar.text_input(key="openalgo_api_key_input").set_value("oa_secret_key").run()
    at.sidebar.button(key="openalgo_connect_btn").click().run()
    assert not at.exception
    assert at.session_state["auth_dict"] == {
        "broker": "OpenAlgo",
        "host_url": "http://127.0.0.1:5000",
        "api_key": "oa_secret_key",
    }
    assert settings_repo.get("openalgo_host_url") == "http://127.0.0.1:5000"
    assert settings_repo.get("openalgo_api_key") == "oa_secret_key"
    assert any("OpenAlgo Authenticated" in s.value for s in at.sidebar.success)


def test_data_hub_page_apptest_catalog_zip_and_status(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from quantdesk.brokers import openalgo as oa_broker
    from quantdesk.core import db
    from quantdesk.integrations import openalgo_manager as mgr
    from quantdesk.ui.pages import data_hub

    _isolate_db(tmp_path, monkeypatch)
    monkeypatch.setattr("quantdesk.ui.components._load_dotenv_if_present", lambda: None)
    monkeypatch.delenv("OPENALGO_HOST_URL", raising=False)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)

    pq_path = tmp_path / "RELIANCE_Day.parquet"
    _make_ohlcv("2024-02-01", 8, freq="B", base_close=2900.0).to_parquet(pq_path, index=False)
    db.log_download("OpenAlgo", "RELIANCE", "NSE_EQ|RELIANCE", "Day", "2024-02-01", "2024-02-12", str(pq_path))

    monkeypatch.setattr(mgr, "is_port_open", lambda host, port, timeout=0.6: True)
    monkeypatch.setattr(
        oa_broker,
        "ping_openalgo",
        lambda host, key: (
            True,
            {"latency_ms": 11.0, "broker": "zerodha", "analyzer_active": False, "mode": "live", "message": "pong"},
        ),
    )
    monkeypatch.setattr(
        data_hub,
        "search_openalgo_symbols",
        lambda host, key, query, exchange="": [
            {"tradingsymbol": "RELIANCE", "symbol": "RELIANCE", "exchange": "NSE", "name": "Reliance", "instrumenttype": "EQ", "lotsize": 1}
        ],
    )

    app_path = str(Path(__file__).resolve().parents[1] / "app_main.py")
    at = AppTest.from_file(app_path, default_timeout=60)
    at.session_state["auth_dict"] = {
        "broker": "OpenAlgo",
        "host_url": "http://127.0.0.1:5000",
        "api_key": "oa_key",
    }
    at.run()
    at.radio(key="nav_radio").set_value("📡 Data Hub & OpenAlgo").run()

    assert not at.exception
    assert any("Data Hub & OpenAlgo" in t.value for t in at.title)
    assert len(at.dataframe) >= 1

    # Select symbol to populate ZIP download bytes
    at.multiselect(key="hub_selected_symbols").set_value(["RELIANCE"]).run()
    assert not at.exception

    # Verify Status KPIs rendered cleanly
    metric_labels = [m.label for m in at.metric]
    assert "Datasets" in metric_labels
    assert "Reachable" in metric_labels
    assert "Connected Broker" in metric_labels

    # Live symbol search inside OpenAlgo Companion tab
    at.text_input(key="hub_oa_search_query").set_value("RELIANCE").run()
    at.button(key="hub_oa_search_btn").click().run()
    assert not at.exception
    assert len(at.dataframe) >= 2
