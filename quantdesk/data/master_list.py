"""
quantdesk.data.master_list — unified instrument master list + asset classifiers.

Port of upstox_app.py:286-393. The original used ``@st.cache_data`` and ``st.error``; here we
use the Streamlit-or-not cache shim and the logger so the module is importable outside the app.
The Upstox master files are PUBLIC gzipped JSON (no auth) — so this is testable without login.
"""
from __future__ import annotations

import io
import time as _time

import numpy as np
import pandas as pd
import requests

from quantdesk.config import ROOT_DIR
from quantdesk.core.cache import cache_resource
from quantdesk.core.logging import get_logger

log = get_logger("data.master_list")

_HEADERS = {"User-Agent": "Mozilla/5.0"}

# On-disk master-list cache (PERF): survives app restarts so startup doesn't re-download ~10s of
# instrument files every time. Refreshed when older than 24h.
_CACHE_DIR = ROOT_DIR / ".master_cache"
_DISK_TTL = 24 * 3600
try:
    _CACHE_DIR.mkdir(exist_ok=True)
except Exception:  # noqa: BLE001
    pass


def _disk_load(broker: str):
    p = _CACHE_DIR / f"master_{broker}.parquet"
    try:
        if p.exists() and (_time.time() - p.stat().st_mtime) < _DISK_TTL:
            return pd.read_parquet(p)
    except Exception:  # noqa: BLE001
        return None
    return None


def _disk_save(broker: str, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(_CACHE_DIR / f"master_{broker}.parquet")
    except Exception:  # noqa: BLE001
        pass


def _upstox_classes_vec(df: pd.DataFrame) -> np.ndarray:
    """Vectorised equivalent of ``get_upstox_class`` applied row-wise (≈30× faster on 141k rows).

    Uses ``np.select`` with the SAME precedence as the row function (equity→index→fut→opt→com→cur),
    so the output is identical (verified in tests). Avoids a 3s per-build ``apply(axis=1)``.
    """
    seg = df.get("segment", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    inst = df.get("instrument_type", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    sym = df.get("tradingsymbol", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    name = df.get("name", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()

    is_etf = sym.str.contains("ETF") | sym.str.contains("BEES") | name.str.contains("ETF")
    equity = (inst == "EQUITY") | seg.str.contains("EQ")
    conditions = [
        equity & is_etf,
        equity & ~is_etf,
        seg.str.contains("INDEX"),
        inst == "FUT",
        inst.isin(["CE", "PE"]) | inst.str.contains("OPT"),
        seg.str.contains("COM") | seg.str.contains("MCX"),
        seg.str.contains("CUR") | seg.str.contains("NCD"),
    ]
    choices = ["ETF", "Stock", "Index", "Futures", "Options", "Commodity", "Currency"]
    return np.select(conditions, choices, default="Other")


# --- asset classifiers (verbatim) ------------------------------------------
def get_upstox_class(row) -> str:
    seg = str(row.get("segment", "")).upper()
    inst = str(row.get("instrument_type", "")).upper()
    sym = str(row.get("tradingsymbol", "")).upper()
    name = str(row.get("name", "")).upper()
    if inst == "EQUITY" or "EQ" in seg:
        if "ETF" in sym or "BEES" in sym or "ETF" in name:
            return "ETF"
        return "Stock"
    if "INDEX" in seg:
        return "Index"
    if inst == "FUT":
        return "Futures"
    if inst in ["CE", "PE"] or "OPT" in inst:
        return "Options"
    if "COM" in seg or "MCX" in seg:
        return "Commodity"
    if "CUR" in seg or "NCD" in seg:
        return "Currency"
    return "Other"


def get_zerodha_class(row) -> str:
    inst = str(row.get("instrument_type", "")).upper()
    seg = str(row.get("segment", "")).upper()
    sym = str(row.get("tradingsymbol", "")).upper()
    name = str(row.get("name", "")).upper()
    if inst == "EQ" or "EQ" in seg:
        if "ETF" in sym or "BEES" in sym or "ETF" in name:
            return "ETF"
        return "Stock"
    if seg == "INDICES":
        return "Index"
    if inst == "FUT":
        return "Futures"
    if inst in ["CE", "PE"] or "OPT" in inst:
        return "Options"
    if "MCX" in seg:
        return "Commodity"
    if "CDS" in seg:
        return "Currency"
    return "Other"


def get_dhan_class(row) -> str:
    inst = str(row.get("instrument_type", "")).upper()
    sym = str(row.get("tradingsymbol", "")).upper()
    name = str(row.get("name", "")).upper()
    exch = str(row.get("exchange", "")).upper()
    if inst in ["EQUITY"] or "EQ" in exch:
        if "ETF" in sym or "BEES" in sym or "ETF" in name:
            return "ETF"
        return "Stock"
    if inst in ["INDEX"]:
        return "Index"
    if inst in ["FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"]:
        return "Futures"
    if inst in ["OPTIDX", "OPTSTK", "OPTCOM", "OPTCUR"]:
        return "Options"
    return "Other"


def get_dhan_exchange_segment(exch, inst) -> str:
    exch, inst = str(exch).upper(), str(inst).upper()
    if exch == "NSE":
        if inst in ["EQUITY", "INDEX"]:
            return "NSE_EQ"
        elif inst in ["FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK"]:
            return "NSE_FNO"
        elif inst in ["FUTCUR", "OPTCUR"]:
            return "NSE_CURRENCY"
    elif exch == "BSE":
        if inst in ["EQUITY", "INDEX"]:
            return "BSE_EQ"
    elif exch == "MCX":
        return "MCX_COMM"
    return "NSE_EQ"


@cache_resource
def get_unified_master_list(broker: str) -> pd.DataFrame:
    """Fetch & normalise the instrument master for a broker.

    Cached with ``cache_resource`` (PERF): the master list is a ~53MB read-only reference table,
    so we share ONE object across reruns/sessions instead of ``cache_data`` deep-copying it on
    every rerun (~176ms each). Callers only ever filter it (never mutate), so sharing is safe.
    A 24h on-disk parquet cache also makes app startup near-instant after the first fetch.
    """
    cached = _disk_load(broker)
    if cached is not None and not cached.empty:
        return cached

    if broker == "Upstox":
        master_frames = []
        urls = [
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE_FO.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/NCD_FO.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE_COM.json.gz",
        ]
        for url in urls:
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=30)
                if resp.status_code == 200:
                    master_frames.append(
                        pd.read_json(io.BytesIO(resp.content), compression="gzip")
                    )
            except Exception as e:  # noqa: BLE001
                log.warning("Master list fetch failed for %s: %s", url, e)
        if master_frames:
            df = pd.concat(master_frames, ignore_index=True)
            if "trading_symbol" in df.columns:
                df.rename(columns={"trading_symbol": "tradingsymbol"}, inplace=True)
            df["label"] = df["tradingsymbol"] + " (" + df.get("name", "") + ")"
            df["asset_class"] = _upstox_classes_vec(df)  # vectorised (was apply, axis=1)
            _disk_save(broker, df)
            return df

    elif broker == "Zerodha":
        try:
            resp = requests.get(
                "https://api.kite.trade/instruments", headers=_HEADERS, timeout=5
            )
            df = pd.read_csv(io.BytesIO(resp.content))
            df.rename(columns={"instrument_token": "instrument_key"}, inplace=True)
            df["label"] = df["tradingsymbol"] + " (" + df["name"].fillna(df["tradingsymbol"]) + ")"
            df["asset_class"] = df.apply(get_zerodha_class, axis=1)
            _disk_save(broker, df)
            return df
        except Exception as e:  # noqa: BLE001
            log.error("Zerodha Master Error: %s", e)

    elif broker == "Dhan":
        try:
            resp = requests.get(
                "https://images.dhan.co/api-data/api-scrip-master.csv",
                headers=_HEADERS,
                timeout=5,
            )
            df = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
            df.rename(
                columns={
                    "SEM_SMST_SECURITY_ID": "instrument_key",
                    "SEM_CUSTOM_SYMBOL": "tradingsymbol",
                    "SEM_EXM_EXCH_ID": "exchange",
                    "SEM_INSTRUMENT_NAME": "instrument_type",
                    "SEM_ENGLISH_NAME": "name",
                },
                inplace=True,
            )
            df["dhan_exchange_segment"] = df.apply(
                lambda r: get_dhan_exchange_segment(r["exchange"], r["instrument_type"]), axis=1
            )
            df["label"] = df["tradingsymbol"] + " (" + df["name"].astype(str) + ")"
            df["asset_class"] = df.apply(get_dhan_class, axis=1)
            _disk_save(broker, df)
            return df
        except Exception as e:  # noqa: BLE001
            log.error("Dhan Master Error: %s", e)

    return pd.DataFrame()
