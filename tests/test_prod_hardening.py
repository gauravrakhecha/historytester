"""Focused, fast, synthetic regression tests covering all Phase 1 production-hardening fixes."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers for socket-free async testing (works inside restricted sandboxes)
# ---------------------------------------------------------------------------

class _DummySem:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _run_coro_sync(coro):
    """Drive a coroutine to completion without creating an OS event-loop socketpair."""
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


# ---------------------------------------------------------------------------
# 1. HTF EMA causality & AST safety (quantdesk/strategy/codegen.py)
# ---------------------------------------------------------------------------

def test_htf_ema_causality_and_safe_ast():
    from backtesting import Backtest

    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    from quantdesk.strategy.models import RiskManagement, StrategyConfig
    from quantdesk.strategy.validation import assert_safe_ast

    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_fast", "type": "EMA", "source": "Close", "params": {"period": 3}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": ">", "var2": 0, "is_num": True}],
        risk_management=RiskManagement(htf_bias=True, htf_minutes=60, htf_ema_period=2),
    )
    code = build_strategy_code(cfg)
    assert_safe_ast(code)

    # 15-min bars across three 60-min buckets: 09:xx, 10:xx, 11:xx
    idx = pd.date_range("2024-01-01 09:15", "2024-01-01 11:45", freq="15min")
    base = pd.DataFrame(
        {
            "Open": np.full(len(idx), 100.0),
            "High": np.full(len(idx), 102.0),
            "Low": np.full(len(idx), 98.0),
            "Close": np.linspace(100.0, 110.0, len(idx)),
            "Volume": np.full(len(idx), 1000),
        },
        index=idx,
    )

    mutated = base.copy()
    # Mutate 09:45 (last bar in 09:00 bucket) and 10:45 (last bar in 10:00 bucket)
    mutated.loc[pd.Timestamp("2024-01-01 09:45"), ["Close", "High"]] = [5000.0, 5005.0]
    mutated.loc[pd.Timestamp("2024-01-01 10:45"), ["Close", "High"]] = [9000.0, 9005.0]

    cls1 = compile_strategy(code)
    cls2 = compile_strategy(code)
    s1 = Backtest(base, cls1, cash=100000).run()
    s2 = Backtest(mutated, cls2, cash=100000).run()

    htf1 = np.asarray(s1["_strategy"]._htf_ema, dtype=float)
    htf2 = np.asarray(s2["_strategy"]._htf_ema, dtype=float)

    # Earlier bars inside the 09:00 bucket (09:15, 09:30) must NOT change when 09:45 changes.
    pos_0930 = idx.get_loc(pd.Timestamp("2024-01-01 09:30"))
    assert np.array_equal(htf1[: pos_0930 + 1], htf2[: pos_0930 + 1], equal_nan=True)

    # Earlier bars inside the 10:00 bucket (10:00..10:30) only see the completed 09:00 bucket;
    # mutating 10:45 (same 10:00 bucket) must NOT alter 10:00, 10:15, or 10:30.
    mutated_only_1045 = base.copy()
    mutated_only_1045.loc[pd.Timestamp("2024-01-01 10:45"), ["Close", "High"]] = [9000.0, 9005.0]
    s3 = Backtest(mutated_only_1045, compile_strategy(code), cash=100000).run()
    htf3 = np.asarray(s3["_strategy"]._htf_ema, dtype=float)

    pos_1030 = idx.get_loc(pd.Timestamp("2024-01-01 10:30"))
    pos_1100 = idx.get_loc(pd.Timestamp("2024-01-01 11:00"))
    assert np.array_equal(htf1[: pos_1030 + 1], htf3[: pos_1030 + 1], equal_nan=True)
    # And the next completed bucket (11:00+) DOES reflect the 10:45 close.
    assert htf3[pos_1100] > htf1[pos_1100]


# ---------------------------------------------------------------------------
# 2. Preset validation & v8.1 round-trip (quantdesk/strategy/validation.py)
# ---------------------------------------------------------------------------

def test_validate_preset_rejects_bad_sources_collisions_and_v81_rm():
    from quantdesk.strategy.models import RiskManagement, StrategyConfig
    from quantdesk.strategy.validation import PresetValidationError, validate_preset

    # Unknown indicator source
    with pytest.raises(PresetValidationError, match="source"):
        validate_preset({
            "indicators": [{"name": "E1", "type": "EMA", "source": "NoSuchCol", "params": {"period": 10}}]
        })

    # Forward-referenced indicator source (E1 references E2 defined later)
    with pytest.raises(PresetValidationError, match="source"):
        validate_preset({
            "indicators": [
                {"name": "E1", "type": "EMA", "source": "E2", "params": {"period": 10}},
                {"name": "E2", "type": "EMA", "source": "Close", "params": {"period": 20}},
            ]
        })

    # Duplicate indicator name
    with pytest.raises(PresetValidationError, match="duplicates"):
        validate_preset({
            "indicators": [
                {"name": "E1", "type": "EMA", "source": "Close", "params": {"period": 10}},
                {"name": "E1", "type": "SMA", "source": "Close", "params": {"period": 20}},
            ]
        })

    # safe_name collision ("EMA 1" and "EMA_1" both sanitize to "EMA_1")
    with pytest.raises(PresetValidationError, match="duplicates"):
        validate_preset({
            "indicators": [
                {"name": "EMA 1", "type": "EMA", "source": "Close", "params": {"period": 10}},
                {"name": "EMA_1", "type": "SMA", "source": "Close", "params": {"period": 20}},
            ]
        })

    # Invalid v8.1 risk_management fields
    for bad_rm in [
        {"htf_bias": "yes"},
        {"intraday_square_off": 1},
        {"htf_minutes": 0},
        {"htf_minutes": -15},
        {"htf_ema_period": 0},
        {"sl_pct": -1.0},
    ]:
        with pytest.raises(PresetValidationError):
            validate_preset({"risk_management": bad_rm})

    # Valid v8.1 preset round-trips cleanly
    cfg = StrategyConfig(
        indicators=[
            {"name": "FastEMA", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "SmoothFast", "type": "SMA", "source": "FastEMA", "params": {"period": 5}},
        ],
        buy_rules=[{"logic": "IF", "var1": "FastEMA", "operator": ">", "var2": "SmoothFast", "is_num": False}],
        risk_management=RiskManagement(
            mode="Static % Stop Loss / Take Profit",
            sl_pct=1.5,
            tp_pct=3.0,
            htf_bias=True,
            htf_minutes=60,
            htf_ema_period=20,
            intraday_square_off=True,
        ),
    )
    validated = validate_preset(cfg.to_dict())
    roundtripped = StrategyConfig.from_dict(validated).to_dict()
    assert roundtripped == cfg.to_dict()


# ---------------------------------------------------------------------------
# 3. Path traversal protection (quantdesk/strategy/presets.py)
# ---------------------------------------------------------------------------

def test_presets_reject_path_traversal(tmp_path, monkeypatch):
    from quantdesk.strategy import presets

    strat_dir = tmp_path / "strategies"
    ind_dir = tmp_path / "indicator_presets"
    strat_dir.mkdir()
    ind_dir.mkdir()
    monkeypatch.setattr(presets, "STRATEGIES_DIR", strat_dir)
    monkeypatch.setattr(presets, "INDICATOR_PRESETS_DIR", ind_dir)

    bad_names = ["../x", "a/b", "a\\b", ".json", "evil\x00name", "", "   "]
    for bad in bad_names:
        with pytest.raises(ValueError):
            presets._safe_path(strat_dir, bad)
        with pytest.raises(ValueError):
            presets.load_strategy(bad)
        with pytest.raises(ValueError):
            presets.save_strategy(bad, {})
        with pytest.raises(ValueError):
            presets.delete_strategy(bad)
        with pytest.raises(ValueError):
            presets.load_indicator_preset(bad)
        with pytest.raises(ValueError):
            presets.save_indicator_preset(bad, [])
        with pytest.raises(ValueError):
            presets.delete_indicator_preset(bad)

    # Valid round-trip stays strictly inside monkeypatched dir
    saved = presets.save_strategy("clean_test", {"indicators": []})
    assert saved == "clean_test.json"
    assert presets.load_strategy_raw("clean_test") == {"indicators": []}


# ---------------------------------------------------------------------------
# 4. Worker, batch persistence, and results equity parsing
# ---------------------------------------------------------------------------

def test_results_equity_series_from_json_parses_dict_and_list():
    from quantdesk.ui.pages.results import _equity_series_from_json

    s_dict = _equity_series_from_json(json.dumps({"2024-01-02": 100500.0, "2024-01-01": 100000.0}))
    s_list = _equity_series_from_json(json.dumps([["2024-01-01", 100000.0], ["2024-01-02", 100500.0]]))

    assert s_dict is not None and s_list is not None
    assert list(s_dict.index) == list(s_list.index)
    assert list(s_dict.values) == [100000.0, 100500.0]
    assert list(s_list.values) == [100000.0, 100500.0]
    assert _equity_series_from_json(None) is None
    assert _equity_series_from_json("") is None
    assert _equity_series_from_json("{bad json") is None


def test_worker_6_and_8_tuple_tz_normalization_and_batch_save(tmp_path, monkeypatch):
    from quantdesk.backtest.batch import save_batch_to_db
    from quantdesk.backtest.worker import run_worker
    from quantdesk.core import db
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.models import StrategyConfig

    # Build >=60 daily bars in 2024 (no gaps) with tz-aware +05:30 timestamps
    dates = pd.bdate_range("2024-01-01", periods=70, tz="Asia/Kolkata")
    close = np.concatenate([
        np.linspace(120, 95, 25),
        np.linspace(96, 135, 25),
        np.linspace(134, 105, 20),
    ])
    df = pd.DataFrame({
        "Timestamp": dates.astype(str),
        "Open": close - 0.5,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Close": close,
        "Volume": np.full(len(dates), 5000),
    })
    csv_path = tmp_path / "SYNTH_1D_2024-01-01_2024-04-08.csv"
    df.to_csv(csv_path, index=False)

    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 5}},
            {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 15}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
    )
    code = build_strategy_code(cfg)

    res6 = run_worker((str(csv_path), code, 100000, 0.0005, 1.0, []))
    assert "Error" not in res6, res6
    st6 = res6["Stats"]
    assert st6["Start Date"] == "2024-01-01"
    assert st6["End Date"] == str(dates[-1].tz_localize(None).date())
    assert st6["Dataset Hash"]
    assert "Total Charges" not in st6
    eq6 = json.loads(st6["Equity Curve JSON"])
    assert isinstance(eq6, list) and len(eq6) > 0 and isinstance(eq6[0], list)

    res8 = run_worker((str(csv_path), code, 100000, 0.0005, 1.0, [], "Equity Intraday", 0.0002))
    assert "Error" not in res8, res8
    st8 = res8["Stats"]
    assert st8["Total Trades"] >= 1
    assert st8["Total Charges"] > 0
    assert st8["Start Date"] == "2024-01-01"
    assert st8["Dataset Hash"] == st6["Dataset Hash"]

    # Persist via save_batch_to_db and verify DB columns
    monkeypatch.setattr(db, "DB_FILE", str(tmp_path / "batch.db"))
    monkeypatch.setattr(db, "_initialised", False)
    save_batch_to_db([st8], "BatchTest", cfg.indicators, code=code, segment="Equity Intraday")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT start_date, end_date, total_charges, segment FROM backtests"
        ).fetchone()
    assert row == (st8["Start Date"], st8["End Date"], st8["Total Charges"], "Equity Intraday")


# ---------------------------------------------------------------------------
# 5. Backtester _lot_override regex scope (quantdesk/ui/pages/backtester.py)
# ---------------------------------------------------------------------------

def test_lot_override_only_updates_class_attribute(monkeypatch):
    from quantdesk.ui.pages import backtester

    seen_kwargs = {}

    def fake_number_input(label, **kwargs):
        seen_kwargs.update(kwargs)
        return 25

    monkeypatch.setattr(backtester.st, "number_input", fake_number_input)

    src = (
        "# lot_size = 999 (comment must remain untouched)\n"
        "class VisualStrategy(Strategy):\n"
        "    lot_size = 10\n"
        "    def init(self):\n"
        "        self.note = 'lot_size = 777'\n"
    )
    out = backtester._lot_override(src)
    assert seen_kwargs["value"] == 10
    assert "# lot_size = 999 (comment must remain untouched)" in out
    assert "    lot_size = 25\n" in out
    assert "self.note = 'lot_size = 777'" in out


# ---------------------------------------------------------------------------
# 6. Brokers error mapping, fast-fail, and Dhan tz normalization
# ---------------------------------------------------------------------------

def test_raise_for_broker_status_mapping():
    from quantdesk.brokers.base import NonRetryableError, RetryableError, raise_for_broker_status

    for code in (400, 401, 403, 404):
        with pytest.raises(NonRetryableError) as exc_info:
            raise_for_broker_status("Upstox", code, {"Retry-After": "5"})
        assert exc_info.value.status == code
        assert exc_info.value.retryable is False

    with pytest.raises(RetryableError) as exc_429:
        raise_for_broker_status("Zerodha", 429, {"Retry-After": "12"})
    assert exc_429.value.status == 429
    assert exc_429.value.retry_after == 12.0

    with pytest.raises(RetryableError) as exc_503:
        raise_for_broker_status("Dhan", 503, {})
    assert exc_503.value.status == 503
    assert exc_503.value.retry_after is None


def test_fetch_chunk_async_fast_fails_on_non_retryable(monkeypatch):
    from quantdesk.brokers import downloader as D
    from quantdesk.brokers.base import ChunkDownloadError, NonRetryableError

    calls = {"n": 0}

    async def fail_401(session, row, interval, start_dt, end_dt, auth_dict):
        calls["n"] += 1
        raise NonRetryableError("Upstox HTTP 401", status=401)

    monkeypatch.setitem(D._FETCHERS, "FAKE", fail_401)
    with pytest.raises(ChunkDownloadError) as exc_info:
        _run_coro_sync(
            D.fetch_chunk_async(
                None, _DummySem(), "FAKE", {"instrument_key": "X"}, "1 Day",
                dt.date(2024, 1, 1), dt.date(2024, 1, 10), {"access_token": "bad"},
            )
        )
    assert calls["n"] == 1
    assert exc_info.value.attempts == 1
    assert isinstance(exc_info.value.last_error, NonRetryableError)


def test_dhan_fetch_single_normalizes_tz_aware_timestamps():
    from quantdesk.brokers.dhan import fetch_single

    class _FakeResp:
        status = 200
        headers = {}

        async def json(self):
            return {
                "data": {
                    "start_Time": ["2024-01-01T09:15:00+05:30", "2024-01-01T09:16:00+05:30"],
                    "open": [100.0, 101.0],
                    "high": [101.0, 102.0],
                    "low": [99.0, 100.0],
                    "close": [100.5, 101.5],
                    "volume": [1000, 1200],
                }
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeSession:
        def post(self, url, headers=None, json=None):
            return _FakeResp()

    df = _run_coro_sync(
        fetch_single(
            _FakeSession(),
            {"instrument_key": "1333"},
            "1 Minute",
            dt.date(2024, 1, 1),
            dt.date(2024, 1, 1),
            {"access_token": "tok", "client_id": "cid"},
        )
    )
    assert len(df) == 2
    assert df["Timestamp"].dt.tz is None


# ---------------------------------------------------------------------------
# 7. Data quality, saver empty guard, recovery ISO string
# ---------------------------------------------------------------------------

def test_quality_flags_uncovered_future_year_without_score_penalty():
    from quantdesk.data.quality import validate

    # Mon-Fri range in 2030 (beyond nse_holidays.csv coverage) with zero gaps
    dates = pd.bdate_range("2030-01-07", "2030-01-11")
    df = pd.DataFrame({
        "Timestamp": dates,
        "Open": [100.0] * len(dates),
        "High": [102.0] * len(dates),
        "Low": [99.0] * len(dates),
        "Close": [101.0] * len(dates),
        "Volume": [1000] * len(dates),
    })
    rep = validate(df)
    assert rep["quality_score"] == 100.0
    assert any("nse_holidays.csv" in msg and "2030" in msg for msg in rep["issues"])


def test_saver_empty_and_none_return_empty_without_files(tmp_path):
    from quantdesk.data.saver import save_frame

    pq = tmp_path / "should_not_exist.parquet"
    assert save_frame(pd.DataFrame(), pq) == []
    assert save_frame(None, pq) == []
    assert list(tmp_path.iterdir()) == []


def test_recovery_inserts_iso_downloaded_at_without_deprecation_warning(tmp_path, monkeypatch):
    from quantdesk.core import db
    from quantdesk.data.recovery import rebuild_db_from_folder

    monkeypatch.setattr(db, "DB_FILE", str(tmp_path / "rec.db"))
    monkeypatch.setattr(db, "_initialised", False)
    db.init_db()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "TCS_15Min_2024-01-01_to_2024-01-31.csv").write_text(
        "Timestamp,Open,High,Low,Close,Volume\n", encoding="utf-8"
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        added, errs = rebuild_db_from_folder(str(data_dir))

    assert (added, errs) == (1, 0)
    dep_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning) and "datetime" in str(w.message).lower()
    ]
    assert dep_warnings == []

    with sqlite3.connect(db.DB_FILE) as conn:
        ts_val = conn.execute("SELECT downloaded_at FROM downloads_log").fetchone()[0]
    assert isinstance(ts_val, str) and len(ts_val) == 19 and " " in ts_val


# ---------------------------------------------------------------------------
# 8. Settings page numeric validation via AppTest
# ---------------------------------------------------------------------------

def test_settings_apptest_rejects_invalid_numeric_inputs(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from quantdesk.core import db, settings_repo

    db_path = tmp_path / "settings_test.db"
    monkeypatch.setattr(db, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "_initialised", False)
    monkeypatch.setattr(settings_repo, "_cache", None)
    db.init_db()

    app_path = Path(__file__).resolve().parent.parent / "app_main.py"
    at = AppTest.from_file(str(app_path), default_timeout=120)
    at.run()
    at.radio(key="nav_radio").set_value("⚙️ Settings").run()

    # Update Commission, Margin, Initial Capital by label
    for ti in at.text_input:
        if "Brokerage Commission" in ti.label:
            ti.set_value("abc")
        elif "Margin" in ti.label:
            ti.set_value("2.0")
        elif "Initial Capital" in ti.label:
            ti.set_value("-5")

    save_btns = [b for b in at.button if "Save Settings" in b.label]
    assert save_btns, "Save Settings button not found"
    save_btns[0].click().run()

    assert not at.exception
    assert not at.success
    assert at.error
    err_text = "\n".join(e.value for e in at.error)
    assert "Brokerage Commission" in err_text
    assert "Margin" in err_text
    assert "Initial Capital" in err_text

    # Confirm nothing was persisted to the DB
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    assert rows == []
