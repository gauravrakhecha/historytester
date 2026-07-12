"""
End-to-end UI verification via Streamlit's headless AppTest harness.

Drives app_main.py the way a user would: it renders every page in a real script-run context
(catching any render() exception) and then actually clicks "Run Backtest" on the Backtester
page to verify the full UI -> engine -> metrics path. Run as a script from the app root.
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
from quantdesk.ui.components import NAV  # noqa: E402

APP = str(ROOT / "app_main.py")


def _seed(at):
    """Pre-load a compiled EMA-crossover strategy so quant pages pass their guards."""
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_fast", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "EMA_slow", "type": "EMA", "source": "Close", "params": {"period": 30}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Above", "var2": "EMA_slow", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Below", "var2": "EMA_slow", "is_num": False}],
    )
    at.session_state["custom_strategy_code"] = build_strategy_code(cfg)
    at.session_state["indicators"] = cfg.indicators
    at.session_state["buy_rules"] = cfg.buy_rules
    at.session_state["sell_rules"] = cfg.sell_rules
    at.session_state["strategy_label"] = "EMA_fast + EMA_slow"


def main():
    failures = []

    # Phase A: every page renders without exception
    for label, page, needs_broker in NAV:
        at = AppTest.from_file(APP, default_timeout=120)
        _seed(at)
        at.run()
        try:
            at.radio(key="nav_radio").set_value(label).run()
        except Exception as e:  # noqa: BLE001
            failures.append(f"{page}: navigation raised {type(e).__name__}: {e}")
            continue
        if at.exception:
            failures.append(f"{page}: render exception: {at.exception}")
        else:
            tag = "auth-gated OK" if needs_broker else "rendered OK"
            print(f"[page] {page:22s} {tag}")

    # Phase B: actually run a backtest through the UI
    at = AppTest.from_file(APP, default_timeout=300)
    _seed(at)
    at.run()
    at.radio(key="nav_radio").set_value("🚀 Backtester").run()
    run_btns = [b for b in at.button if "Run Backtest" in b.label]
    if not run_btns:
        failures.append("Backtester: 'Run Backtest' button not found")
    else:
        run_btns[0].click().run()
        if at.exception:
            failures.append(f"Backtester run exception: {at.exception}")
        else:
            ok = any("Backtest saved to database" in m.value for m in at.success) if at.success else False
            metric_ok = len(at.metric) >= 4
            print(f"[run ] backtester executed: success_msg={ok} metrics={len(at.metric)}")
            if not (ok or metric_ok):
                failures.append("Backtester run produced neither success message nor metrics")

    print("-" * 60)
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("ALL UI PAGES VERIFIED + BACKTEST RAN THROUGH THE UI ✓")


if __name__ == "__main__":
    main()
