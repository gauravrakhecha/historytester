"""Tests for the multi-format download saver (Parquet / CSV / Both + separate CSV folder)."""
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.data import saver  # noqa: E402


def _df():
    return pd.DataFrame({
        "Timestamp": pd.to_datetime(["2023-01-01 09:15", "2023-01-01 09:16"]),
        "Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
        "Close": [1.0, 2.0], "Volume": [10, 20],
    })


def _patch(monkeypatch, fmt, csv_dir=""):
    monkeypatch.setattr(saver.settings_repo, "get",
                        lambda k, d=None: {"save_format": fmt, "csv_data_dir": csv_dir}.get(k, d))


def test_both_alongside(monkeypatch, tmp_path):
    _patch(monkeypatch, "Both", "")
    base = tmp_path / "data"
    pq = base / "RELIANCE" / "RELIANCE_15Min.parquet"
    written = saver.save_frame(_df(), pq, str(base))
    assert pq.exists() and pq.with_suffix(".csv").exists()
    assert len(written) == 2 and written[0].endswith(".parquet")
    # round-trips
    assert len(pd.read_csv(pq.with_suffix(".csv"))) == 2


def test_parquet_only(monkeypatch, tmp_path):
    _patch(monkeypatch, "Parquet only", "")
    pq = tmp_path / "d" / "X.parquet"
    written = saver.save_frame(_df(), pq, str(tmp_path / "d"))
    assert pq.exists() and not pq.with_suffix(".csv").exists()
    assert written == [str(pq)]


def test_csv_only(monkeypatch, tmp_path):
    _patch(monkeypatch, "CSV only", "")
    pq = tmp_path / "d" / "X.parquet"
    written = saver.save_frame(_df(), pq, str(tmp_path / "d"))
    assert not pq.exists() and pq.with_suffix(".csv").exists()
    assert written[0].endswith(".csv")


def test_separate_csv_dir_mirrors_structure(monkeypatch, tmp_path):
    base = tmp_path / "parq"
    csv_dir = tmp_path / "mycsv"
    _patch(monkeypatch, "Both", str(csv_dir))
    pq = base / "RELIANCE" / "15Min" / "RELIANCE_15Min.parquet"
    saver.save_frame(_df(), pq, str(base))
    mirrored = csv_dir / "RELIANCE" / "15Min" / "RELIANCE_15Min.csv"
    assert pq.exists() and mirrored.exists()  # parquet under base, csv mirrored under csv_dir


def test_per_call_csv_dir_overrides_setting(monkeypatch, tmp_path):
    # Setting points one place; the per-call csv_dir must win.
    _patch(monkeypatch, "Both", str(tmp_path / "setting_csv"))
    base = tmp_path / "parq"
    override = tmp_path / "override_csv"
    pq = base / "SYM" / "SYM_5Min.parquet"
    saver.save_frame(_df(), pq, str(base), csv_dir=str(override))
    assert (override / "SYM" / "SYM_5Min.csv").exists()
    assert not (tmp_path / "setting_csv" / "SYM" / "SYM_5Min.csv").exists()


def test_empty_csv_dir_forces_alongside(monkeypatch, tmp_path):
    # Setting has a separate dir, but per-call '' must force CSV alongside the parquet.
    _patch(monkeypatch, "Both", str(tmp_path / "setting_csv"))
    base = tmp_path / "parq"
    pq = base / "SYM" / "SYM_5Min.parquet"
    saver.save_frame(_df(), pq, str(base), csv_dir="")
    assert pq.with_suffix(".csv").exists()
    assert not (tmp_path / "setting_csv" / "SYM" / "SYM_5Min.csv").exists()


def test_primary_path_prefers_parquet():
    assert saver.primary_path(["a.parquet", "a.csv"], "x") == "a.parquet"
    assert saver.primary_path([], "fallback") == "fallback"
