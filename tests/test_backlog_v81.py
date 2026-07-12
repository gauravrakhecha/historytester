"""Tests for the v8.1 backlog-clearing wave (loader tz, error reports, analytics, batch persistence)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.conftest import requires_sample  # noqa: F401 (kept for symmetry; these are synthetic)


def test_loader_normalizes_tz_aware_and_naive_consistently():
    from quantdesk.data.loader import normalize_timestamps
    aware = pd.Series(pd.to_datetime(
        ["2024-01-01T09:15:00+05:30", "2024-01-01T09:30:00+05:30"]))
    naive = pd.Series(pd.to_datetime(["2024-01-01 09:15:00", "2024-01-01 09:30:00"]))
    na, nn = normalize_timestamps(aware), normalize_timestamps(naive)
    assert na.dt.tz is None and nn.dt.tz is None          # both tz-naive now
    assert list(na) == list(nn)                            # +05:30 kept as IST wall-clock


def test_write_error_report(tmp_path, monkeypatch):
    from quantdesk import config as C
    from quantdesk.core import logging as L
    monkeypatch.setattr(C, "WORKFLOW_DIR", tmp_path)  # function reads it from config at call time
    try:
        raise ValueError("boom in a worker")
    except ValueError as e:
        p = L.write_error_report({"file": "X.csv", "cash": 100000}, e, run_id="err_test")
    assert p and Path(p).exists()
    rep = json.loads(Path(p).read_text(encoding="utf-8"))
    assert rep["error_type"] == "ValueError" and "boom" in rep["error"]
    assert "traceback" in rep and rep["context"]["file"] == "X.csv"


@requires_sample
def test_trade_analytics_and_kelly():
    from backtesting import Backtest

    from quantdesk.backtest.metrics import enrich_trade_analytics, trade_kelly
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy
    from quantdesk.strategy.models import StrategyConfig
    from tests.conftest import SAMPLE_CSV

    cfg = StrategyConfig(
        indicators=[{"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}},
                    {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 30}}],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
    )
    stats = Backtest(load_indexed(SAMPLE_CSV).iloc[-4000:], compile_strategy(build_strategy_code(cfg)),
                     cash=100000, commission=0.0005, finalize_trades=True).run()
    trades = enrich_trade_analytics(stats["_trades"].copy(), stats["_strategy"])
    assert {"MAE_pct", "MFE_pct", "R_multiple"} <= set(trades.columns)
    assert (trades["MAE_pct"].dropna() <= 1e-6).all()     # adverse excursion is <= 0
    assert (trades["MFE_pct"].dropna() >= -1e-6).all()    # favourable excursion is >= 0
    k = trade_kelly(trades)
    assert 0 <= k["win_rate"] <= 100
    assert set(k) >= {"full_kelly", "half_kelly", "payoff_ratio", "expectancy_pct"}
    assert k["half_kelly"] == pytest.approx(k["full_kelly"] / 2, abs=1e-6)


def test_batch_persists_repro_and_equity_fields(tmp_path, monkeypatch):
    from quantdesk.backtest.batch import save_batch_to_db
    from quantdesk.core import db
    monkeypatch.setattr(db, "DB_FILE", str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "_initialised", False)
    results = [{
        "File": "X.csv", "Return [%]": 5.0, "Max. Drawdown [%]": -2.0, "Win Rate [%]": 55.0,
        "Total Trades": 10, "Sharpe Ratio": 1.2, "Net Return [%]": 4.8,
        "Dataset Hash": "abc123", "Equity Curve JSON": '{"2024-01-01": 100000.0}',
    }]
    save_batch_to_db(results, "TestStrat", [{"name": "EMA", "type": "EMA"}], code="x")
    with db.connect() as c:
        row = c.execute("SELECT dataset_hash, net_return_pct, equity_curve_json, segment "
                        "FROM backtests").fetchone()
    assert row[0] == "abc123" and row[1] == 4.8
    assert "2024-01-01" in row[2] and "Batch" in row[3]


def test_tax_breakdown_columns_and_reconciliation():
    from quantdesk.backtest.metrics import tax_breakdown
    trades = pd.DataFrame({
        "Size": [10, -5], "EntryPrice": [100.0, 200.0], "ExitPrice": [110.0, 190.0],
        "PnL": [95.0, 45.0], "EntryTime": ["t0", "t2"], "ExitTime": ["t1", "t3"],
    })
    tx = tax_breakdown(trades, "Equity Intraday")
    assert len(tx) == 2
    for c in ("STT", "StampDuty", "Exchange", "SEBI", "Brokerage", "GST",
              "TotalCharges", "GrossPnL", "NetPnL"):
        assert c in tx.columns
    # gross = net + charges (engine P&L is already net); charges are positive
    assert (tx["TotalCharges"] > 0).all()
    assert np.allclose(tx["GrossPnL"], tx["NetPnL"] + tx["TotalCharges"], atol=0.01)


def test_strategyconfig_to_dict_strips_row_id():
    from quantdesk.strategy.models import StrategyConfig
    cfg = StrategyConfig(
        indicators=[{"name": "X", "type": "RSI", "source": "Close", "params": {"period": 14}, "_id": "abcd1234"}],
        buy_rules=[{"logic": "IF", "var1": "Close", "operator": ">", "var2": "X", "is_num": False, "_id": "ef01"}],
    )
    d = cfg.to_dict()
    assert all("_id" not in r for r in d["indicators"])
    assert all("_id" not in r for r in d["buy_rules"])
    # round-trips cleanly
    assert StrategyConfig.from_dict(d).to_dict() == d


def _htf_sq_cfg(**rm):
    from quantdesk.strategy.models import RiskManagement, StrategyConfig
    return StrategyConfig(
        indicators=[{"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}},
                    {"name": "EMA_s", "type": "EMA", "source": "Close", "params": {"period": 30}}],
        buy_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Above", "var2": "EMA_s", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_f", "operator": "Crosses Below", "var2": "EMA_s", "is_num": False}],
        risk_management=RiskManagement(**rm),
    )


def _intraday_df(days=8, per_day=25):
    rng = np.random.default_rng(1)
    frames = []
    price = 100.0
    for d in pd.bdate_range("2024-01-01", periods=days):
        ts = pd.date_range(d.strftime("%Y-%m-%d") + " 09:15", periods=per_day, freq="15min")
        closes = price * np.cumprod(1 + rng.normal(0.0004, 0.003, per_day))
        price = float(closes[-1])
        o = closes * (1 + rng.normal(0, 0.0005, per_day))
        frames.append(pd.DataFrame({
            "Open": o, "High": np.maximum(o, closes) * 1.001,
            "Low": np.minimum(o, closes) * 0.999, "Close": closes,
            "Volume": 1000}, index=ts))
    df = pd.concat(frames)
    df.index.name = "Timestamp"
    return df


def test_codegen_htf_bias_and_square_off_compile_and_run():
    from backtesting import Backtest

    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.compiler import compile_strategy

    df = _intraday_df()
    # HTF bias
    code_htf = build_strategy_code(_htf_sq_cfg(htf_bias=True, htf_minutes=60, htf_ema_period=20))
    assert "_htf_ema" in code_htf and "_htf_long_ok" in code_htf
    stats_htf = Backtest(df, compile_strategy(code_htf), cash=100000, commission=0.0005,
                         finalize_trades=True).run()
    assert stats_htf["# Trades"] >= 0  # runs without error

    # Intraday square-off: no trade may span two calendar days
    code_sq = build_strategy_code(_htf_sq_cfg(intraday_square_off=True))
    assert "_session_last" in code_sq
    stats_sq = Backtest(df, compile_strategy(code_sq), cash=100000, commission=0.0005,
                        finalize_trades=True).run()
    tr = stats_sq["_trades"]
    if not tr.empty:
        ent = pd.to_datetime(tr["EntryTime"]).dt.normalize()
        ext = pd.to_datetime(tr["ExitTime"]).dt.normalize()
        assert (ent == ext).all(), "square-off must prevent overnight holds"
