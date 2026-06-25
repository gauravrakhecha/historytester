"""Pytest config: put the app root on sys.path so `import quantdesk` works in tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Shared sample data path for tests.
SAMPLE_CSV = r"D:\simpleton\historydownloader\data\RELIANCE\RELIANCE_15Min_2022-06-01_to_2026-06-18.csv"
