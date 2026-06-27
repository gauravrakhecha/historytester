"""Corporate-actions math + dtype-safety tests (was zero-coverage; caught the int64 Volume crash)."""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.data.corporate_actions import apply_corporate_actions  # noqa: E402


def _frame():
    ts = pd.to_datetime([f"2023-01-{d:02d}" for d in range(1, 11)])
    return pd.DataFrame({
        "Timestamp": ts,
        "Open": np.full(10, 100.0),
        "High": np.full(10, 110.0),
        "Low": np.full(10, 90.0),
        "Close": np.full(10, 100.0),
        "Volume": np.arange(1000, 11000, 1000, dtype="int64"),  # int64 — reproduces the crash
    })


def test_split_adjusts_ohlc_and_volume_with_int64_volume():
    df = _frame()
    assert df["Volume"].dtype == np.int64
    # 2:1 split, ex-date 06 Jan 2023 -> pre-ex bars (1-5 Jan) divide price by 2, multiply volume by 2
    out = apply_corporate_actions(df, [{"name": "STOCK SPLIT", "ratio": "2:1", "expiry_date": "06 Jan 2023"}])
    pre = out["Timestamp"].dt.date < pd.Timestamp("2023-01-06").date()
    post = ~pre
    assert np.allclose(out.loc[pre, "Close"], 50.0)      # 100 / 2
    assert np.allclose(out.loc[post, "Close"], 100.0)    # unchanged
    assert np.allclose(out.loc[pre, "Volume"], np.arange(1000, 6000, 1000) * 2)
    assert out["Volume"].dtype == np.float64             # widened, no TypeError


def test_bonus_ratio_multiplier():
    df = _frame()
    # 1:1 bonus -> mult = (a+b)/b = (1+1)/1 = 2
    out = apply_corporate_actions(df, [{"name": "BONUS", "ratio": "1:1", "expiry_date": "06 Jan 2023"}])
    pre = out["Timestamp"].dt.date < pd.Timestamp("2023-01-06").date()
    assert np.allclose(out.loc[pre, "Close"], 50.0)


def test_cash_dividend_injected_on_ex_date():
    df = _frame()
    out = apply_corporate_actions(df, [{"name": "DIVIDEND", "amount": 7.5, "expiry_date": "04 Jan 2023"}])
    assert "Dividend" in out.columns
    first_ex = out[out["Timestamp"].dt.date >= pd.Timestamp("2023-01-04").date()].index[0]
    assert out.at[first_ex, "Dividend"] == pytest.approx(7.5)
    assert out["Dividend"].sum() == pytest.approx(7.5)


def test_no_actions_adds_empty_cashflow_columns():
    out = apply_corporate_actions(_frame(), [])
    assert (out["Dividend"] == 0).all() and (out["Rights_Premium"] == 0).all()
