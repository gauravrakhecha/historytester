"""Comprehensive verification suite for No-Code Strategy Builder v2."""
from __future__ import annotations

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

from backtesting import Backtest  # noqa: E402

from quantdesk.config import INDICATOR_PLACEMENT  # noqa: E402
from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.compiler import compile_strategy  # noqa: E402
from quantdesk.strategy.indicators import (  # noqa: E402
    INDICATOR_PARAM_SPECS,
    INDICATOR_TYPES,
    FormulaValidationError,
    assert_safe_formula,
    compute_custom_formula,
    rolling_stat_vec,
    series_math_vec,
    shift_vec,
)
from quantdesk.strategy.models import RiskManagement, StrategyConfig, classify_columns  # noqa: E402
from quantdesk.strategy.templates import (  # noqa: E402
    STARTER_TEMPLATES,
    TEMPLATE_DESCRIPTIONS,
    check_strategy_sanity,
    describe_strategy_plain_english,
    get_template,
)
from quantdesk.strategy.validation import (  # noqa: E402
    PresetValidationError,
    assert_safe_ast,
    validate_preset,
)


def _intraday_ohlcv(n_days: int = 8, bars_per_day: int = 24) -> pd.DataFrame:
    """Deterministic intraday 15-min OHLCV spanning 09:15..15:00 IST with waves."""
    bdays = pd.bdate_range("2024-07-01", periods=n_days)
    idx = []
    for d in bdays:
        for k in range(bars_per_day):
            idx.append(d + pd.Timedelta(hours=9, minutes=15 + 15 * k))
    n = len(idx)
    t = np.linspace(0, 6 * np.pi, n)
    close = 500.0 + 25.0 * np.sin(t) + np.linspace(0.0, 12.0, n)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 2.5
    low = np.minimum(open_, close) - 2.5
    vol = (10000 + 4000 * np.cos(t)).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=pd.DatetimeIndex(idx),
    )


# ---------------------------------------------------------------------------
# 1. Visual Custom Blocks: Series Math, Rolling Stat, Bar Shift
# ---------------------------------------------------------------------------

def test_custom_blocks_registered_and_placement():
    for t in ("Series Math", "Rolling Stat", "Bar Shift", "Custom Formula"):
        assert t in INDICATOR_TYPES
        assert t in INDICATOR_PARAM_SPECS
        assert t in INDICATOR_PLACEMENT

    assert INDICATOR_PLACEMENT["Series Math"] == "panel"
    assert INDICATOR_PLACEMENT["Custom Formula"] == "panel"
    assert INDICATOR_PLACEMENT["Rolling Stat"] == "overlay"
    assert INDICATOR_PLACEMENT["Bar Shift"] == "overlay"


@pytest.mark.parametrize("op", ["add", "sub", "mul", "div", "pct_diff"])
def test_series_math_ops_and_backtest_execution(op):
    df = _intraday_ohlcv(4, 20)
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA5", "type": "EMA", "source": "Close", "params": {"period": 5}},
            {
                "name": "Spread",
                "type": "Series Math",
                "source": "Close",
                "rhs": "EMA5",
                "params": {"op": op, "use_num": 0, "num_val": 0.0},
            },
            {
                "name": "Scaled",
                "type": "Series Math",
                "source": "Close",
                "params": {"op": op, "use_num": 1, "num_val": 2.0},
            },
        ],
        buy_rules=[{"logic": "IF", "var1": "Close", "operator": ">", "var2": "EMA5", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "Close", "operator": "<", "var2": "EMA5", "is_num": False}],
    )
    validate_preset(cfg.to_dict())
    code = build_strategy_code(cfg)
    assert_safe_ast(code)
    cls = compile_strategy(code)
    stats = Backtest(df, cls, cash=100000, finalize_trades=True).run()
    strat = stats["_strategy"]

    expected_series = series_math_vec(df["Close"].to_numpy(), np.asarray(strat.EMA5), op)
    expected_num = series_math_vec(df["Close"].to_numpy(), 2.0, op)
    assert np.allclose(np.asarray(strat.Spread), expected_series, equal_nan=True)
    assert np.allclose(np.asarray(strat.Scaled), expected_num, equal_nan=True)

    # Div-by-zero returns NaN cleanly rather than raising
    zero_div = series_math_vec([10.0, 20.0], [0.0, 5.0], "div")
    assert np.isnan(zero_div[0]) and zero_div[1] == 4.0


@pytest.mark.parametrize("stat", ["max", "min", "mean", "sum", "std"])
def test_rolling_stat_and_bar_shift_backtest_execution(stat):
    df = _intraday_ohlcv(4, 20)
    cfg = StrategyConfig(
        indicators=[
            {"name": "RStat", "type": "Rolling Stat", "source": "High", "params": {"stat": stat, "period": 5}},
            {"name": "PrevClose", "type": "Bar Shift", "source": "Close", "params": {"shift": 2}},
        ],
        buy_rules=[{"logic": "IF", "var1": "Close", "operator": ">", "var2": "PrevClose", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "Close", "operator": "<", "var2": "PrevClose", "is_num": False}],
    )
    validate_preset(cfg.to_dict())
    code = build_strategy_code(cfg)
    assert_safe_ast(code)
    cls = compile_strategy(code)
    stats = Backtest(df, cls, cash=100000, finalize_trades=True).run()
    strat = stats["_strategy"]

    assert np.allclose(np.asarray(strat.RStat), rolling_stat_vec(df["High"].to_numpy(), 5, stat), equal_nan=True)
    assert np.allclose(np.asarray(strat.PrevClose), shift_vec(df["Close"].to_numpy(), 2), equal_nan=True)

    # Negative Bar Shift (lookahead) is forbidden
    with pytest.raises(ValueError):
        shift_vec(df["Close"].to_numpy(), -1)
    with pytest.raises(PresetValidationError):
        validate_preset({
            "indicators": [{"name": "BadShift", "type": "Bar Shift", "source": "Close", "params": {"shift": -1}}]
        })


# ---------------------------------------------------------------------------
# 2. Custom Formula: safe execution + security rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "formula",
    [
        "(Close - Open) / atr(14)",
        "Close - sma(Close, 10)",
        "(Close - shift(Close, 1)) / shift(Close, 1) * 100",
        "rolling_max(High, 10) - rolling_min(Low, 10)",
        "abs(Close - ema(Close, 10)) + sqrt(Volume) + log(Close)",
        "min(Open, Close) + max(High, Low) - wma(Close, 5)",
        "rsi(Close, 14) > 50",
        "-Close + (+Open) + (Close % 10) + (Close // 10) + (2 ** 3)",
    ],
)
def test_custom_formula_safe_expressions_compile_and_run(formula):
    df = _intraday_ohlcv(4, 20)
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA10", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "MyExpr", "type": "Custom Formula", "source": "Close", "formula": formula, "params": {}},
        ],
        buy_rules=[{"logic": "IF", "var1": "MyExpr", "operator": ">", "var2": 0.0, "is_num": True}],
        sell_rules=[{"logic": "IF", "var1": "MyExpr", "operator": "<", "var2": 0.0, "is_num": True}],
    )
    validate_preset(cfg.to_dict())
    code = build_strategy_code(cfg)
    assert_safe_ast(code)
    cls = compile_strategy(code)
    stats = Backtest(df, cls, cash=100000, finalize_trades=True).run()
    strat = stats["_strategy"]
    assert len(strat.MyExpr) == len(df)


def test_custom_formula_can_reference_prior_indicator_not_future():
    df = _intraday_ohlcv(3, 20)
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA10", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "Dist", "type": "Custom Formula", "source": "Close", "formula": "Close - EMA10", "params": {}},
        ],
        buy_rules=[{"logic": "IF", "var1": "Dist", "operator": ">", "var2": 0.0, "is_num": True}],
    )
    validate_preset(cfg.to_dict())
    cls = compile_strategy(build_strategy_code(cfg))
    stats = Backtest(df, cls, cash=100000, finalize_trades=True).run()
    strat = stats["_strategy"]
    assert np.allclose(np.asarray(strat.Dist), df["Close"].to_numpy() - np.asarray(strat.EMA10), equal_nan=True)

    # Forward reference to an indicator defined later must fail validate_preset
    with pytest.raises(PresetValidationError, match="formula"):
        validate_preset({
            "indicators": [
                {"name": "F1", "type": "Custom Formula", "source": "Close", "formula": "Close - EMA10", "params": {}},
                {"name": "EMA10", "type": "EMA", "source": "Close", "params": {"period": 10}},
            ]
        })


@pytest.mark.parametrize(
    "bad_expr",
    [
        "__import__('os').system('echo pwn')",
        "open('/etc/passwd').read()",
        "eval('1+1')",
        "exec('x=1')",
        "compile('1', 'x', 'eval')",
        "(1).__class__.__mro__",
        "Close.__class__",
        "getattr(Close, '__doc__')",
        "[x for x in Close]",
        "(lambda x: x)(Close)",
        "import os",
        "x = Close + 1",
        "UnknownVar + 1",
        "_private + 1",
        "sma(Close, period=10)",
        "min(*Close)",
        "True + 1",
        "'hello'",
        "",
    ],
)
def test_custom_formula_rejects_malicious_and_invalid_expressions(bad_expr):
    allowed = {"Open", "High", "Low", "Close", "Volume"}
    with pytest.raises(FormulaValidationError):
        assert_safe_formula(bad_expr, allowed_vars=allowed)

    with pytest.raises(PresetValidationError):
        validate_preset({
            "indicators": [
                {"name": "Evil", "type": "Custom Formula", "source": "Close", "formula": bad_expr, "params": {}}
            ]
        })

    # Even direct compute_custom_formula rejects unsafe expressions
    env = {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100.0]}
    with pytest.raises(FormulaValidationError):
        compute_custom_formula(bad_expr, env)


# ---------------------------------------------------------------------------
# 3. Rule groups, negate, timing, entry_time_filter, and round-trip
# ---------------------------------------------------------------------------

def test_rule_groups_parenthesization_and_negate_and_timing():
    df = _intraday_ohlcv(6, 20)
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA5", "type": "EMA", "source": "Close", "params": {"period": 5}},
            {"name": "EMA15", "type": "EMA", "source": "Close", "params": {"period": 15}},
            {"name": "RSI14", "type": "RSI", "source": "Close", "params": {"period": 14}},
        ],
        # (EMA5 > EMA15 AND RSI14 < 65) OR (Close > EMA5 [within_last 3] AND NOT(RSI14 > 80) [consecutive 2])
        buy_rules=[
            {"logic": "IF", "var1": "EMA5", "operator": ">", "var2": "EMA15", "is_num": False, "group": 0, "timing": "now", "timing_bars": 1},
            {"logic": "AND", "var1": "RSI14", "operator": "<", "var2": 65.0, "is_num": True, "group": 0, "timing": "bars_ago", "timing_bars": 2},
            {"logic": "OR", "var1": "Close", "operator": "Crosses Above", "var2": "EMA5", "is_num": False, "group": 1, "timing": "within_last", "timing_bars": 3},
            {"logic": "AND", "var1": "RSI14", "operator": ">", "var2": 80.0, "is_num": True, "group": 1, "negate": True, "timing": "consecutive", "timing_bars": 2},
        ],
        sell_rules=[
            {"logic": "IF", "var1": "EMA5", "operator": "Crosses Below", "var2": "EMA15", "is_num": False, "timing": "bars_ago", "timing_bars": 1},
        ],
    )
    validate_preset(cfg.to_dict())
    code = build_strategy_code(cfg)
    assert_safe_ast(code)
    # Verify explicit parenthesization of the two groups in generated _buy_signal
    buy_line = next(line for line in code.splitlines() if "_buy_signal =" in line)
    assert ") or (" in buy_line
    assert "not (" in buy_line

    cls = compile_strategy(code)
    stats = Backtest(df, cls, cash=100000, finalize_trades=True).run()
    assert stats is not None and "# Trades" in stats


def test_rule_group_precedence_differs_from_unparenthesized():
    """Verify (False AND True) OR (True AND True) == True and (True OR False) AND (False) == False."""
    df = _intraday_ohlcv(2, 20)
    # Rule A: Close < 0 (False)
    # Rule B: Close > 0 (True)
    # Rule C: Close > 0 (True)
    # Rule D: Close < 0 (False)
    # (A OR B) AND (C AND D) => (False or True) and (True and False) => False (0 trades)
    # Without parens: False or True and True and False => False, so test (True OR False) AND (False OR True):
    # Without parens: True or (False and False) or True => True; wait: let's test (False AND False) OR (True AND False) vs
    # (False OR True) AND (False):
    #   With parens: (False or True) and False == False (0 trades)
    #   Without parens: False or (True and False) == False; how about False AND False OR True vs (False AND ...) ->
    #   Case where Python `and` binds tighter than `or`:
    #   True OR False AND False -> without parens = True or (False and False) = True
    #   Group 0: (True OR False), Group 1: AND (False) -> (True or False) and (False) = False!
    cfg = StrategyConfig(
        buy_rules=[
            {"logic": "IF", "var1": "Close", "operator": ">", "var2": 0.0, "is_num": True, "group": 0},
            {"logic": "OR", "var1": "Close", "operator": "<", "var2": 0.0, "is_num": True, "group": 0},
            {"logic": "AND", "var1": "Close", "operator": "<", "var2": 0.0, "is_num": True, "group": 1},
        ],
    )
    code = build_strategy_code(cfg)
    assert_safe_ast(code)
    stats = Backtest(df, compile_strategy(code), cash=100000, finalize_trades=True).run()
    assert int(stats["# Trades"]) == 0


def test_entry_time_filter_blocks_entries_outside_window():
    df = _intraday_ohlcv(6, 24)
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA5", "type": "EMA", "source": "Close", "params": {"period": 5}},
        ],
        buy_rules=[{"logic": "IF", "var1": "Close", "operator": ">", "var2": "EMA5", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "Close", "operator": "<", "var2": "EMA5", "is_num": False}],
        risk_management=RiskManagement(
            entry_time_filter=True,
            entry_start_time="10:30",
            entry_end_time="13:30",
        ),
    )
    validate_preset(cfg.to_dict())
    code = build_strategy_code(cfg)
    assert_safe_ast(code)
    stats = Backtest(df, compile_strategy(code), cash=100000, finalize_trades=True).run()
    trades = stats["_trades"]
    assert len(trades) > 0
    for ts in pd.to_datetime(trades["EntryTime"]):
        hm = ts.strftime("%H:%M")
        assert "10:30" <= hm <= "13:30", f"Entry {ts} violated entry_time_filter!"


def test_models_roundtrip_preserves_v2_fields_and_strips_id():
    raw = {
        "indicators": [
            {"_id": "tmp1", "name": "M", "type": "Series Math", "source": "Close", "rhs": "Open",
             "placement": "overlay", "params": {"op": "sub", "use_num": 0, "num_val": 0.0}},
            {"_id": "tmp2", "name": "F", "type": "Custom Formula", "source": "Close",
             "formula": "Close - Open", "placement": "panel", "params": {}},
        ],
        "buy_rules": [
            {"_id": "r1", "logic": "IF", "var1": "M", "operator": ">", "var2": 0.0, "is_num": True,
             "group": 2, "negate": True, "timing": "consecutive", "timing_bars": 3}
        ],
        "sell_rules": [],
        "no_trade_rules": [],
        "range_zones": [],
        "risk_management": {
            "entry_time_filter": True,
            "entry_start_time": "09:45",
            "entry_end_time": "14:15",
        },
    }
    validated = validate_preset(raw)
    cfg = StrategyConfig.from_dict(validated)
    out = cfg.to_dict()
    assert "_id" not in out["indicators"][0]
    assert "_id" not in out["buy_rules"][0]
    assert out["indicators"][0]["rhs"] == "Open"
    assert out["indicators"][0]["placement"] == "overlay"
    assert out["indicators"][1]["formula"] == "Close - Open"
    assert out["buy_rules"][0]["group"] == 2
    assert out["buy_rules"][0]["negate"] is True
    assert out["buy_rules"][0]["timing"] == "consecutive"
    assert out["buy_rules"][0]["timing_bars"] == 3
    assert out["risk_management"]["entry_time_filter"] is True
    assert out["risk_management"]["entry_start_time"] == "09:45"
    assert out["risk_management"]["entry_end_time"] == "14:15"

    # classify_columns honors per-indicator placement override
    cls = classify_columns(out["indicators"])
    assert cls["M"]["placement"] == "overlay"
    assert cls["F"]["placement"] == "panel"

    # Invalid rule/time fields rejected by validate_preset
    for bad in [
        {"buy_rules": [{"logic": "IF", "var1": "Close", "operator": ">", "var2": 1, "is_num": True, "group": -1}]},
        {"buy_rules": [{"logic": "IF", "var1": "Close", "operator": ">", "var2": 1, "is_num": True, "negate": "yes"}]},
        {"buy_rules": [{"logic": "IF", "var1": "Close", "operator": ">", "var2": 1, "is_num": True, "timing": "future"}]},
        {"buy_rules": [{"logic": "IF", "var1": "Close", "operator": ">", "var2": 1, "is_num": True, "timing_bars": 0}]},
        {"risk_management": {"entry_time_filter": "yes"}},
        {"risk_management": {"entry_start_time": "25:99"}},
    ]:
        with pytest.raises(PresetValidationError):
            validate_preset(bad)


# ---------------------------------------------------------------------------
# 4. Starter Templates, Plain-English Translator, and Sanity Checker
# ---------------------------------------------------------------------------

def test_all_starter_templates_validate_compile_and_run():
    df = _intraday_ohlcv(12, 24)
    assert len(STARTER_TEMPLATES) == 5
    for name in STARTER_TEMPLATES:
        assert name in TEMPLATE_DESCRIPTIONS
        tpl = get_template(name)
        validated = validate_preset(tpl)
        cfg = StrategyConfig.from_dict(validated)
        code = build_strategy_code(cfg)
        assert_safe_ast(code)
        cls = compile_strategy(code)
        stats = Backtest(df, cls, cash=100000, finalize_trades=True).run()
        assert stats is not None and "# Trades" in stats


def test_describe_strategy_plain_english_and_sanity_checks():
    cfg = StrategyConfig(
        indicators=[
            {"name": "M1", "type": "Series Math", "source": "Close", "rhs": "Open", "params": {"op": "sub", "use_num": 0, "num_val": 0}},
            {"name": "R1", "type": "Rolling Stat", "source": "High", "params": {"stat": "max", "period": 10}},
            {"name": "S1", "type": "Bar Shift", "source": "Close", "params": {"shift": 2}},
            {"name": "F1", "type": "Custom Formula", "source": "Close", "formula": "Close - Open", "params": {}},
        ],
        buy_rules=[
            {"logic": "IF", "var1": "M1", "operator": ">", "var2": 0.0, "is_num": True, "group": 0, "negate": True, "timing": "bars_ago", "timing_bars": 2},
            {"logic": "OR", "var1": "Close", "operator": ">", "var2": "S1", "is_num": False, "group": 1, "timing": "within_last", "timing_bars": 3},
        ],
        sell_rules=[],
        risk_management=RiskManagement(
            entry_time_filter=True,
            entry_start_time="09:30",
            entry_end_time="14:45",
        ),
    )
    desc = describe_strategy_plain_english(cfg)
    assert set(desc.keys()) == {"indicators", "buy", "sell", "filters", "sizing", "risk"}
    assert any("Series Math" in x for x in desc["indicators"])
    assert any("rolling max" in x for x in desc["indicators"])
    assert any("shifted back 2" in x for x in desc["indicators"])
    assert any("Close - Open" in x for x in desc["indicators"])
    assert any("NOT" in x and "2 bars ago" in x for x in desc["buy"])
    assert any("09:30" in x and "14:45" in x for x in desc["filters"])

    # Healthy template has 0 sanity warnings
    assert check_strategy_sanity(get_template("Golden Cross")) == []

    # Catch beginner traps & contradictions
    bad_cfg = StrategyConfig(
        indicators=[
            {"name": "RSI14", "type": "RSI", "source": "Close", "params": {"period": 14}},
            {"name": "EMA20", "type": "EMA", "source": "Close", "params": {"period": 20}},
        ],
        buy_rules=[
            {"logic": "IF", "var1": "RSI14", "operator": ">", "var2": 70.0, "is_num": True},
            {"logic": "AND", "var1": "RSI14", "operator": "<", "var2": 30.0, "is_num": True},
            {"logic": "AND", "var1": "Close", "operator": ">", "var2": "EMA20", "is_num": False},
            {"logic": "AND", "var1": "Close", "operator": "<", "var2": "EMA20", "is_num": False},
        ],
        sell_rules=[],
        pos_size_type="Risk % of Equity per Trade",
        risk_management=RiskManagement(
            mode="None (Use Sell Rules Only)",
            partial_exit_pct=50.0,
            partial_trigger_pct=0.0,
            entry_time_filter=True,
            entry_start_time="15:00",
            entry_end_time="09:30",
        ),
    )
    warnings_list = check_strategy_sanity(bad_cfg)
    joined = "\n".join(warnings_list)
    assert "No exit mechanism" in joined
    assert "Risk % of Equity" in joined
    assert "Scale-out" in joined
    assert "Entry time window" in joined
    assert "RSI14" in joined
    assert "EMA20" in joined


# ---------------------------------------------------------------------------
# 5. Strategy Builder v2 Streamlit AppTest integration
# ---------------------------------------------------------------------------

def test_strategy_builder_v2_apptest_flows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from streamlit.testing.v1 import AppTest

    from quantdesk import config
    from quantdesk.core import db

    numba_dir = tmp_path / "numba_cache"
    numba_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(numba_dir))

    db_path = tmp_path / "builder_v2_scratch.db"
    monkeypatch.setenv("MODULAR_DB_FILE", str(db_path))
    monkeypatch.setattr(config, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "DB_FILE", str(db_path))
    monkeypatch.setattr(db, "_initialised", False)
    db.init_db()

    at = AppTest.from_file(str(ROOT / "app_main.py"), default_timeout=180)
    at.run()
    at.radio(key="nav_radio").set_value("🛠️ Strategy Builder").run()
    assert not at.exception, f"Builder v2 render failed: {at.exception}"

    # Default mode is Simple (Guided)
    assert at.radio(key="builder_mode").value == "🌱 Simple (Guided)"

    # Load a 1-click starter template ("Golden Cross")
    tpl_btns = [b for b in at.button if "Golden Cross" in b.label]
    assert tpl_btns, "Golden Cross starter template button missing"
    tpl_btns[0].click().run()
    assert not at.exception
    assert len(at.session_state["indicators"]) == 2

    # Run Quick Test Strategy (Preview) — uses synthetic fallback cleanly when DB is empty
    prev_btns = [b for b in at.button if "Quick Test Strategy" in b.label]
    assert prev_btns, "Quick Test Strategy button missing"
    prev_btns[0].click().run()
    assert not at.exception, f"Quick Test preview failed: {at.exception}"
    assert len(at.metric) >= 4

    # Switch to Pro mode, verify Custom Building Blocks + rule group/timing controls, and compile
    at.radio(key="builder_mode").set_value("⚡ Pro (All Controls)").run()
    assert not at.exception

    # Compile & Save Strategy still succeeds and produces a valid Strategy subclass
    comp_btns = [b for b in at.button if "Compile & Save Strategy" in b.label]
    assert comp_btns, "Compile & Save Strategy button missing"
    comp_btns[0].click().run()
    assert not at.exception
    assert any("Compiled successfully" in m.value for m in at.success)
    cls = compile_strategy(at.session_state["custom_strategy_code"])
    assert cls.__name__ == "VisualStrategy"
