"""Pytest config: put the app root on sys.path so `import quantdesk` works in tests."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Shared sample data path for tests. Portable: override with the QUANTDESK_TEST_CSV
# env var; otherwise fall back to the maintainer's local file. Tests that need it
# call `require_sample_csv()` (or use the `sample_csv` fixture) so they SKIP with a
# clear message on a machine that doesn't have the file, instead of erroring.
SAMPLE_CSV = os.environ.get(
    "QUANTDESK_TEST_CSV",
    r"D:\simpleton\historydownloader\data\RELIANCE\RELIANCE_15Min_2022-06-01_to_2026-06-18.csv",
)
# Data ROOT for tests that scan a folder (batch / 1-minute glob). Override with
# QUANTDESK_TEST_DIR; defaults to the maintainer's local data folder.
SAMPLE_DIR = os.environ.get("QUANTDESK_TEST_DIR", r"D:\simpleton\historydownloader\data")
# Skip a whole module when its data isn't on this machine (portable CI).
requires_sample = pytest.mark.skipif(
    not Path(SAMPLE_CSV).exists(),
    reason="sample CSV not present; set QUANTDESK_TEST_CSV to run data-backed tests",
)


def require_sample_csv() -> str:
    """Return SAMPLE_CSV, or skip the test if the file isn't present on this machine."""
    if not Path(SAMPLE_CSV).exists():
        pytest.skip(
            f"sample data not found at {SAMPLE_CSV}; set QUANTDESK_TEST_CSV to a "
            f"RELIANCE-style OHLCV CSV to run this test."
        )
    return SAMPLE_CSV


@pytest.fixture()
def sample_csv() -> str:
    return require_sample_csv()
