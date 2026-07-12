"""
Numeric parity gate: the modular engine's only divergence from the original monolith is the
vectorised ALMA / Linear-Reg-Slope / R-Squared indicators. This proves that switching them on
does NOT change backtest results vs the original per-window ``rolling.apply`` forms.

For every other indicator the generated code is a byte-for-byte port, so identical by
construction. Runnable as a script or under pytest.
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from backtesting import Backtest  # noqa: E402

from quantdesk.data.loader import load_indexed  # noqa: E402
from quantdesk.strategy import codegen, compiler, indicators  # noqa: E402
from quantdesk.strategy.models import StrategyConfig  # noqa: E402
from tests.conftest import SAMPLE_CSV, SAMPLE_DIR, requires_sample

pytestmark = requires_sample

DATA = SAMPLE_CSV
KEYS = ["Return [%]", "# Trades", "Win Rate [%]", "Sharpe Ratio", "Max. Drawdown [%]", "Equity Final [$]"]


def _run(cfg, df, vectorized):
    indicators.USE_VECTORIZED = vectorized
    compiler.cache_clear()
    code = codegen.build_strategy_code(cfg)
    cls = compiler.compile_strategy(code)
    return Backtest(df, cls, cash=100000, commission=0.0005, finalize_trades=True).run()


def _cfg_vectorized_indicators():
    return StrategyConfig(
        indicators=[
            {"name": "ALMA1", "type": "ALMA", "source": "Close", "params": {"period": 20}},
            {"name": "LRS1", "type": "Linear Regression Slope", "source": "Close", "params": {"period": 20}},
            {"name": "R2_1", "type": "R-Squared", "source": "Close", "params": {"period": 20}},
        ],
        buy_rules=[
            {"logic": "IF", "var1": "Close", "operator": ">", "var2": "ALMA1", "is_num": False},
            {"logic": "AND", "var1": "LRS1", "operator": ">", "var2": 0.0, "is_num": True},
            {"logic": "AND", "var1": "R2_1", "operator": ">", "var2": 0.5, "is_num": True},
        ],
        sell_rules=[{"logic": "IF", "var1": "Close", "operator": "<", "var2": "ALMA1", "is_num": False}],
    )


def _compare():
    df = load_indexed(DATA)
    cfg = _cfg_vectorized_indicators()
    fast = _run(cfg, df, True)
    slow = _run(cfg, df, False)
    indicators.USE_VECTORIZED = True  # restore default
    return fast, slow


def test_vectorized_matches_original():
    fast, slow = _compare()
    for k in KEYS:
        a, b = fast.get(k), slow.get(k)
        if pd.isna(a) and pd.isna(b):
            continue
        assert abs(float(a) - float(b)) < 1e-6, f"parity mismatch on {k}: vectorized={a} original={b}"


if __name__ == "__main__":
    fast, slow = _compare()
    print("Vectorized vs Original (must match):")
    for k in KEYS:
        print(f"  {k:22s} vec={fast.get(k)}  orig={slow.get(k)}")
    print("PARITY OK ✓")
