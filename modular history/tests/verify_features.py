"""
Deeper in-UI feature verification (in-process flows only; multiprocessing batch/ML are
verified separately). Drives the actual buttons via AppTest and checks for success.
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.models import StrategyConfig  # noqa: E402

APP = str(ROOT / "app_main.py")
results = []


def _cfg():
    return StrategyConfig(
        indicators=[
            {"name": "EMA_fast", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "EMA_slow", "type": "EMA", "source": "Close", "params": {"period": 30}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Above", "var2": "EMA_slow", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Below", "var2": "EMA_slow", "is_num": False}],
    )


def _seed(at):
    cfg = _cfg()
    at.session_state["custom_strategy_code"] = build_strategy_code(cfg)
    at.session_state["indicators"] = cfg.indicators
    at.session_state["buy_rules"] = cfg.buy_rules
    at.session_state["sell_rules"] = cfg.sell_rules
    at.session_state["strategy_label"] = "EMA_fast + EMA_slow"


def _click(at, label):
    for b in at.button:
        if label in b.label:
            b.click()
            return True
    return False


def check(name, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"[ok ] {name}")
    except AssertionError as e:
        results.append((name, False, str(e)))
        print(f"[FAIL] {name}: {e}")
    except Exception as e:  # noqa: BLE001
        results.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"[ERR ] {name}: {type(e).__name__}: {e}")


def plotter_signal_sheet():
    at = AppTest.from_file(APP, default_timeout=200)
    _seed(at)
    at.run()
    at.radio(key="nav_radio").set_value("📈 Strategy & Indicator Plotter").run()
    assert _click(at, "Generate Signal Sheet"), "button missing"
    at.run()
    assert not at.exception, f"exception: {at.exception}"
    assert len(at.metric) >= 3, f"expected metric cards, got {len(at.metric)}"


def strategy_builder_compile():
    at = AppTest.from_file(APP, default_timeout=120)
    _seed(at)
    at.run()
    at.radio(key="nav_radio").set_value("🛠️ Strategy Builder").run()
    assert _click(at, "Compile & Save Strategy"), "compile button missing"
    at.run()
    assert not at.exception, f"exception: {at.exception}"
    assert any("Compiled successfully" in m.value for m in at.success), "no compile success message"


def settings_save():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="nav_radio").set_value("⚙️ Settings").run()
    # form_submit_button shows up in at.button
    assert _click(at, "Save Settings"), "save button missing"
    at.run()
    assert not at.exception, f"exception: {at.exception}"
    assert any("saved successfully" in m.value.lower() for m in at.success), "no save success message"


def backtester_optimizer():
    at = AppTest.from_file(APP, default_timeout=400)
    _seed(at)
    at.run()
    at.radio(key="nav_radio").set_value("🚀 Backtester").run()
    at.checkbox(key="opt_cb_EMA_fast_period") if False else None  # ensure key exists after enable
    # enable optimizer
    for cb in at.checkbox:
        if "Enable Grid Search Optimizer" in cb.label:
            cb.set_value(True)
    at.run()
    # choose a param to optimize
    set_any = False
    for cb in at.checkbox:
        if "EMA_fast" in cb.label and "Optimize" in cb.label:
            cb.set_value(True)
            set_any = True
    at.run()
    assert set_any, "could not find EMA_fast optimize checkbox"
    assert not at.exception, f"exception after enabling optimizer: {at.exception}"
    assert _click(at, "Run Backtest"), "run button missing"
    at.run()
    assert not at.exception, f"optimizer run exception: {at.exception}"
    assert any("best parameters" in m.value.lower() for m in at.success) or len(at.metric) >= 4, "optimizer produced no result"


check("plotter_signal_sheet", plotter_signal_sheet)
check("strategy_builder_compile", strategy_builder_compile)
check("settings_save", settings_save)
check("backtester_optimizer", backtester_optimizer)

print("-" * 60)
bad = [r for r in results if not r[1]]
if bad:
    print("FAILURES:", [(n, e) for n, _, e in bad])
    sys.exit(1)
print("ALL DEEP UI FEATURE FLOWS VERIFIED ✓")
