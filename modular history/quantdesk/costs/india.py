"""
quantdesk.costs.india — exact India statutory transaction charges (Decimal arithmetic).

The app's engine applies a single flat symmetric commission (brokerage+slippage). That is fine
as an in-engine cost proxy for the equity curve, but it cannot express India's real, asymmetric,
segment-specific charges — which is the whole "₹-accurate" value proposition. This module is the
honest charge calculator: given a backtest's executed trades and a segment, it itemises STT,
stamp duty, exchange txn, SEBI, brokerage and 18% GST per leg, so the UI can show a
Gross-vs-Net breakdown and an after-real-charges net return.

Rates (CLAUDE.md Section 12 / Budget-2024 futures STT):
  Equity Delivery:  STT 0.1% both sides | Stamp 0.015% buy | Exch 0.00297% | SEBI 0.0001%
                    | GST 18% on (brokerage+exch+SEBI) | Brokerage 0
  Equity Intraday:  STT 0.025% sell only | Stamp 0.003% buy | Exch 0.00297% | SEBI 0.0001%
                    | Brokerage min(₹20, 0.03%×turnover)/order | GST 18%
  Futures:          STT 0.02% sell only  | Stamp 0.002% buy | Exch 0.00173% | SEBI 0.0001%
                    | Brokerage ₹20 flat/order | GST 18%

All math uses ``decimal.Decimal``; results are returned as floats rounded to paise.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict

import pandas as pd

SEGMENTS = ["Equity Intraday", "Equity Delivery", "Futures"]

_D = Decimal


def _q(x) -> Decimal:
    return _D(str(x))


# Per-segment rate table (Decimal strings). brokerage handled separately.
_RATES = {
    "Equity Intraday": {
        "stt_buy": "0", "stt_sell": "0.00025",
        "stamp_buy": "0.00003",
        "exch": "0.0000297", "sebi": "0.000001", "gst": "0.18",
        "brokerage": "intraday",
    },
    "Equity Delivery": {
        "stt_buy": "0.001", "stt_sell": "0.001",
        "stamp_buy": "0.00015",
        "exch": "0.0000297", "sebi": "0.000001", "gst": "0.18",
        "brokerage": "zero",
    },
    "Futures": {
        "stt_buy": "0", "stt_sell": "0.0002",
        "stamp_buy": "0.00002",
        "exch": "0.0000173", "sebi": "0.000001", "gst": "0.18",
        "brokerage": "flat20",
    },
}

_BROKERAGE_FLAT = _D("20")
_BROKERAGE_INTRADAY_PCT = _D("0.0003")  # 0.03%


def _order_brokerage(mode: str, order_value: Decimal) -> Decimal:
    if mode == "zero":
        return _D("0")
    if mode == "flat20":
        return _BROKERAGE_FLAT
    if mode == "intraday":
        return min(_BROKERAGE_FLAT, _BROKERAGE_INTRADAY_PCT * order_value)
    return _D("0")


def compute_trade_charges(segment: str, buy_value, sell_value) -> Dict[str, float]:
    """Itemised charges for one round-trip given the buy-leg and sell-leg notional values.

    Returns a dict with keys: stt, stamp_duty, exchange, sebi, brokerage, gst, total (floats).
    """
    r = _RATES.get(segment, _RATES["Equity Intraday"])
    bv, sv = _q(buy_value), _q(sell_value)

    stt = _q(r["stt_buy"]) * bv + _q(r["stt_sell"]) * sv
    stamp = _q(r["stamp_buy"]) * bv
    exch = _q(r["exch"]) * (bv + sv)
    sebi = _q(r["sebi"]) * (bv + sv)
    brokerage = _order_brokerage(r["brokerage"], bv) + _order_brokerage(r["brokerage"], sv)
    gst = _q(r["gst"]) * (brokerage + exch + sebi)
    total = stt + stamp + exch + sebi + brokerage + gst

    def f(x):
        return float(round(x, 2))

    return {
        "stt": f(stt), "stamp_duty": f(stamp), "exchange": f(exch),
        "sebi": f(sebi), "brokerage": f(brokerage), "gst": f(gst), "total": f(total),
    }


def charges_for_trades(trades: pd.DataFrame, segment: str) -> Dict[str, float]:
    """Aggregate itemised charges across a backtesting.py ``_trades`` frame.

    Uses Size (negative => short), EntryPrice, ExitPrice. For a long trade the buy leg is the
    entry and the sell leg the exit; for a short trade it's reversed. Returns the summed
    breakdown plus ``total`` and ``num_trades``.
    """
    agg = {"stt": 0.0, "stamp_duty": 0.0, "exchange": 0.0, "sebi": 0.0, "brokerage": 0.0, "gst": 0.0, "total": 0.0}
    if trades is None or trades.empty:
        agg["num_trades"] = 0
        return agg
    for _, t in trades.iterrows():
        size = abs(float(t["Size"]))
        ep, xp = float(t["EntryPrice"]), float(t["ExitPrice"])
        if float(t["Size"]) >= 0:  # long: buy entry, sell exit
            buy_value, sell_value = ep * size, xp * size
        else:  # short: sell entry, buy exit
            sell_value, buy_value = ep * size, xp * size
        c = compute_trade_charges(segment, buy_value, sell_value)
        for k in agg:
            agg[k] += c[k]
    for k in agg:
        agg[k] = round(agg[k], 2)
    agg["num_trades"] = int(len(trades))
    return agg
