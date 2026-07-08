"""v7.1 security: AST sandbox on generated strategy source + preset validation (closes the
audit's preset-upload RCE). Synthetic + fast; no real-data dependency."""
import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.compiler import compile_strategy  # noqa: E402
from quantdesk.strategy.models import StrategyConfig  # noqa: E402
from quantdesk.strategy.validation import (  # noqa: E402
    PresetValidationError,
    UnsafeCodeError,
    assert_safe_ast,
    validate_preset,
)


# ----------------------------------------------------------------- AST sandbox: escapes rejected
@pytest.mark.parametrize("snippet", [
    "import os",
    "from os import system",
    "from subprocess import run",
    "__import__('os').system('echo hi')",
    "x = eval('1+1')",
    "exec('y=1')",
    "z = open('/etc/passwd')",
    "c = compile('1', '<s>', 'eval')",
    "b = ().__class__.__bases__",
    "g = (1).__class__.__mro__",
    "h = getattr(x, '__globals__')",
    "s = '__builtins__'",
])
def test_assert_safe_ast_rejects_escapes(snippet):
    src = "class VisualStrategy(Strategy):\n    def next(self):\n        " + snippet + "\n"
    with pytest.raises(UnsafeCodeError):
        assert_safe_ast(src)


def test_assert_safe_ast_allows_real_generated_source():
    """A genuine multi-indicator strategy (incl. Hurst/HMM whose helpers import hurst/hmmlearn/
    numpy/pandas) must pass — the import allowlist has zero false positives."""
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMAf", "type": "EMA", "source": "Close", "params": {"period": 20}},
            {"name": "R", "type": "RSI", "source": "Close", "params": {"period": 14}},
            {"name": "MAC", "type": "MACD", "source": "Close", "params": {"fast": 12, "slow": 26, "sign": 9}},
            {"name": "ZC", "type": "Z-Score", "source": "Close", "params": {"period": 50}},
            {"name": "HU", "type": "Hurst Exponent", "source": "Close", "params": {"period": 100}},
            {"name": "HM", "type": "HMM Regime", "source": "Close", "params": {"period": 100, "components": 2}},
        ],
        buy_rules=[{"logic": "IF", "var1": "R", "operator": "<", "var2": 30.0, "is_num": True}],
        sell_rules=[{"logic": "IF", "var1": "R", "operator": ">", "var2": 70.0, "is_num": True}],
    )
    src = build_strategy_code(cfg)
    assert "import" in src  # sanity: it does contain (allowlisted) imports
    assert_safe_ast(src)  # must not raise


# ------------------------------------------------------------- compiler blocks malicious source
def test_compile_strategy_blocks_injected_source():
    malicious = ("import os\nclass VisualStrategy(Strategy):\n"
                 "    def init(self): pass\n    def next(self): pass\n")
    with pytest.raises(UnsafeCodeError):
        compile_strategy(malicious)


def test_compile_strategy_blocks_dunder_escape():
    esc = ("class VisualStrategy(Strategy):\n    def next(self):\n"
           "        payload = ().__class__.__bases__[0].__subclasses__()\n")
    with pytest.raises(UnsafeCodeError):
        compile_strategy(esc)


# ----------------------------------------------------------------- preset validation
def _good_preset():
    return {
        "indicators": [{"name": "E", "type": "EMA", "source": "Close", "params": {"period": 20}}],
        "buy_rules": [{"logic": "IF", "var1": "E", "operator": ">", "var2": 100.0, "is_num": True}],
        "sell_rules": [{"logic": "IF", "var1": "Close", "operator": "<", "var2": "E", "is_num": False}],
        "no_trade_rules": [],
        "range_zones": [],
        "risk_management": {"mode": "None (Use Sell Rules Only)", "strat_dir": "Long Only"},
        "pos_size_type": "Fixed Value per Trade (₹) (Default)", "fixed_pos_value": 50000.0, "lot_size": 1,
    }


def test_validate_preset_accepts_valid():
    assert validate_preset(_good_preset()) is not None


def test_validate_preset_rejects_code_in_numeric_threshold():
    bad = _good_preset()
    bad["buy_rules"][0]["var2"] = "0\n    __import__('os').system('x')"  # is_num True -> must be numeric
    with pytest.raises(PresetValidationError):
        validate_preset(bad)


def test_validate_preset_rejects_unknown_column_ref():
    bad = _good_preset()
    bad["buy_rules"][0] = {"logic": "IF", "var1": "Close; import os", "operator": ">", "var2": 1.0, "is_num": True}
    with pytest.raises(PresetValidationError):
        validate_preset(bad)


def test_validate_preset_rejects_unknown_indicator_type():
    bad = _good_preset()
    bad["indicators"][0]["type"] = "os.system"
    with pytest.raises(PresetValidationError):
        validate_preset(bad)


def test_validate_preset_rejects_non_dict_and_bad_shapes():
    with pytest.raises(PresetValidationError):
        validate_preset("not a dict")
    with pytest.raises(PresetValidationError):
        validate_preset({"indicators": "not a list"})


def test_validate_preset_roundtrips_app_export():
    """A config the app itself produces must validate (no false positives)."""
    cfg = StrategyConfig(
        indicators=[{"name": "EMAf", "type": "EMA", "source": "Close", "params": {"period": 10}},
                    {"name": "EMAs", "type": "EMA", "source": "Close", "params": {"period": 30}}],
        buy_rules=[{"logic": "IF", "var1": "EMAf", "operator": "Crosses Above", "var2": "EMAs", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMAf", "operator": "Crosses Below", "var2": "EMAs", "is_num": False}],
    )
    assert validate_preset(cfg.to_dict()) is not None
