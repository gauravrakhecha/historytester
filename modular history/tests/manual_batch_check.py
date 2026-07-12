"""Manual multiprocessing batch check (run as a script; spawn needs a real __main__)."""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.backtest import batch  # noqa: E402
from quantdesk.strategy.codegen import build_strategy_code  # noqa: E402
from quantdesk.strategy.models import StrategyConfig  # noqa: E402
from tests.conftest import SAMPLE_CSV, SAMPLE_DIR, requires_sample

pytestmark = requires_sample

DATA = SAMPLE_DIR


def main():
    cfg = StrategyConfig(
        indicators=[
            {"name": "EMA_fast", "type": "EMA", "source": "Close", "params": {"period": 10}},
            {"name": "EMA_slow", "type": "EMA", "source": "Close", "params": {"period": 30}},
        ],
        buy_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Above", "var2": "EMA_slow", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "EMA_fast", "operator": "Crosses Below", "var2": "EMA_slow", "is_num": False}],
    )
    code = build_strategy_code(cfg)
    files = [
        f for f in batch.find_data_files(DATA) if ("RELIANCE_15Min" in f or "RECLTD_10Min" in f)
    ][:2]
    print("files:", [Path(f).name for f in files])
    out = batch.run_batch(files, code, 100000, 0.0005, 1.0, "Long Only")
    print("results:", len(out["results"]), "failed:", len(out["failed"]))
    print(out["table"][["File", "Return [%]", "Total Trades", "Sharpe Ratio"]].to_string(index=False))
    assert len(out["results"]) == len(files), "some files failed in batch"
    print("BATCH MULTIPROCESSING OK")


if __name__ == "__main__":
    main()
