"""
quantdesk.strategy.models — data contracts + shared helpers for the no-code builder.

These mirror the plain dicts the original kept in ``st.session_state`` (``indicators``,
``buy_rules``, ``sell_rules``, ``no_trade_rules``, ``range_zones`` and the risk-management
block). ``to_dict``/``from_dict`` round-trip to the EXACT JSON shape the original saved, so
existing ``strategies/*.json`` and ``indicator_presets/*.json`` files keep loading unchanged.

Two helpers here are the antidote to bugs the review found, where the original re-derived the
same logic inline in ~5 places (and the Plotter/ML pages missed multi-output indicators):

- ``safe_name`` — the variable-name sanitiser used by codegen and trade-log enrichment.
- ``selectable_columns`` — the single source of "every column a user can pick" (OHLCV +
  every indicator output), driven by ``config.INDICATOR_OUTPUT_SUFFIXES``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from quantdesk.config import INDICATOR_OUTPUT_SUFFIXES, INDICATOR_PLACEMENT

OHLCV = ["Close", "Open", "High", "Low", "Volume"]
OPERATORS = [">", "<", "==", ">=", "<=", "Crosses Above", "Crosses Below"]
ZONE_ACTIONS = [
    "Normal Trading",
    "Block New Entries",
    "Tighten Trailing Stop",
    "Close Position & Block",
]
RM_MODES = [
    "None (Use Sell Rules Only)",
    "Static % Stop Loss / Take Profit",
    "ATR Stop Loss (Volatility)",
    "Trailing Stop Loss (%)",
    "Indicator-Based Exit (Advanced)",
]
STRAT_DIRECTIONS = ["Long Only", "Short Only", "Long & Short"]


def safe_name(raw: str) -> str:
    """Replicate the original sanitiser: non-alnum -> '_', prefix '_' if it starts a digit."""
    name = "".join([c if c.isalnum() else "_" for c in str(raw)])
    if name and name[0].isdigit():
        name = "_" + name
    return name


def output_columns_for(indicator: Dict[str, Any]) -> List[str]:
    """Return the column name(s) a single indicator dict exposes.

    Single-output indicators expose their bare name; multi-output indicators expose
    ``name + suffix`` for each suffix in ``INDICATOR_OUTPUT_SUFFIXES`` (MACD, Bollinger
    Bands, Supertrend, Stochastic, Keltner, Donchian).
    """
    name = indicator["name"]
    suffixes = INDICATOR_OUTPUT_SUFFIXES.get(indicator.get("type", ""))
    if suffixes:
        return [f"{name}{sfx}" for sfx in suffixes]
    return [name]


def selectable_columns(indicators: List[Dict[str, Any]]) -> List[str]:
    """OHLCV + every output column of every indicator — the one list all pages must use."""
    cols = list(OHLCV)
    for ind in indicators or []:
        cols.extend(output_columns_for(ind))
    return cols


def classify_columns(indicators: List[Dict[str, Any]]) -> Dict[str, dict]:
    """Map each indicator output column → how the chart should place it.

    Returns ``{col: {"placement": "overlay"|"panel", "group": <indicator name>, "type": <type>}}``.
    Overlay columns share the price axis; panel columns are grouped per indicator instance so a
    multi-output indicator (MACD line+signal, Stochastic %K/%D) shares one sub-panel. Drives the
    redesigned chart layout (single source of truth, from ``config.INDICATOR_PLACEMENT``).
    """
    out: Dict[str, dict] = {}
    for ind in indicators or []:
        itype = ind.get("type", "")
        placement = INDICATOR_PLACEMENT.get(itype, "overlay")
        for col in output_columns_for(ind):
            out[col] = {"placement": placement, "group": ind["name"], "type": itype}
    return out


@dataclass
class RiskManagement:
    mode: str = "None (Use Sell Rules Only)"
    sl_pct: float = 2.0
    tp_pct: float = 5.0
    trail_pct: float = 2.0
    strat_dir: str = "Long Only"
    # v7.1 Task 3 — professional risk controls
    risk_pct: float = 1.0        # % of equity risked per trade (for "Risk % of Equity" sizing)
    max_dd_halt_pct: float = 0.0  # circuit-breaker: flatten + block entries below this peak-drawdown % (0 = off)
    # v7.1 Task 3 pt.2 — ATR (volatility) stops + time stop
    atr_period: int = 14          # ATR length for the volatility stop / ATR-risk sizing
    atr_stop_mult: float = 2.0    # stop distance = atr_stop_mult × ATR
    max_bars_in_trade: int = 0    # time stop: close a trade after this many bars (0 = off)
    # v7.8 Task 3 pt.3 — breakeven stop + partial exit / scale-out
    breakeven_trigger_pct: float = 0.0  # move stop to entry once price moves this % in favour (0 = off)
    partial_exit_pct: float = 0.0       # % of the position to bank at the first target (0 = off)
    partial_trigger_pct: float = 0.0    # favourable move % that triggers the partial exit (0 = off)
    # v8.1 — multi-timeframe higher-TF trend bias + intraday square-off (MIS)
    htf_bias: bool = False              # only take longs above / shorts below a higher-TF EMA
    htf_minutes: int = 60               # higher-timeframe resample interval (minutes)
    htf_ema_period: int = 50            # EMA length on the higher-timeframe series
    intraday_square_off: bool = False   # flatten any open position at each session's last bar

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "trail_pct": self.trail_pct,
            "strat_dir": self.strat_dir,
            "risk_pct": self.risk_pct,
            "max_dd_halt_pct": self.max_dd_halt_pct,
            "atr_period": self.atr_period,
            "atr_stop_mult": self.atr_stop_mult,
            "max_bars_in_trade": self.max_bars_in_trade,
            "breakeven_trigger_pct": self.breakeven_trigger_pct,
            "partial_exit_pct": self.partial_exit_pct,
            "partial_trigger_pct": self.partial_trigger_pct,
            "htf_bias": self.htf_bias,
            "htf_minutes": self.htf_minutes,
            "htf_ema_period": self.htf_ema_period,
            "intraday_square_off": self.intraday_square_off,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "RiskManagement":
        d = d or {}
        return cls(
            mode=d.get("mode", "None (Use Sell Rules Only)"),
            sl_pct=d.get("sl_pct", 2.0),
            tp_pct=d.get("tp_pct", 5.0),
            trail_pct=d.get("trail_pct", 2.0),
            strat_dir=d.get("strat_dir", "Long Only"),
            risk_pct=d.get("risk_pct", 1.0),
            max_dd_halt_pct=d.get("max_dd_halt_pct", 0.0),
            atr_period=d.get("atr_period", 14),
            atr_stop_mult=d.get("atr_stop_mult", 2.0),
            max_bars_in_trade=d.get("max_bars_in_trade", 0),
            breakeven_trigger_pct=d.get("breakeven_trigger_pct", 0.0),
            partial_exit_pct=d.get("partial_exit_pct", 0.0),
            partial_trigger_pct=d.get("partial_trigger_pct", 0.0),
            htf_bias=d.get("htf_bias", False),
            htf_minutes=d.get("htf_minutes", 60),
            htf_ema_period=d.get("htf_ema_period", 50),
            intraday_square_off=d.get("intraday_square_off", False),
        )


@dataclass
class StrategyConfig:
    """The full no-code strategy: identical JSON shape to the original preset files."""

    indicators: List[dict] = field(default_factory=list)
    buy_rules: List[dict] = field(default_factory=list)
    sell_rules: List[dict] = field(default_factory=list)
    no_trade_rules: List[dict] = field(default_factory=list)
    range_zones: List[dict] = field(default_factory=list)
    risk_management: RiskManagement = field(default_factory=RiskManagement)
    # position sizing (lived in session_state, not the preset JSON in the original)
    pos_size_type: str = "Fixed Value per Trade (₹) (Default)"
    fixed_pos_value: float = 50000.0
    lot_size: int = 1

    def to_dict(self) -> dict:
        """Preset JSON shape. Superset of the original: also persists position sizing so a
        reloaded preset doesn't silently revert to defaults (Fixed ₹50000, lot 1).

        The builder tags rows with a transient ``_id`` (stable widget keys); it's stripped
        here so saved presets stay clean and byte-stable across sessions."""
        def _clean(rows):
            return [{k: v for k, v in r.items() if k != "_id"} for r in rows]
        return {
            "indicators": _clean(self.indicators),
            "buy_rules": _clean(self.buy_rules),
            "sell_rules": _clean(self.sell_rules),
            "no_trade_rules": _clean(self.no_trade_rules),
            "range_zones": _clean(self.range_zones),
            "risk_management": self.risk_management.to_dict(),
            "pos_size_type": self.pos_size_type,
            "fixed_pos_value": self.fixed_pos_value,
            "lot_size": self.lot_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyConfig":
        return cls(
            indicators=d.get("indicators", []),
            buy_rules=d.get("buy_rules", []),
            sell_rules=d.get("sell_rules", []),
            no_trade_rules=d.get("no_trade_rules", []),
            range_zones=d.get("range_zones", []),
            risk_management=RiskManagement.from_dict(d.get("risk_management")),
            # back-compat: older presets lack these -> fall back to the dataclass defaults
            pos_size_type=d.get("pos_size_type", "Fixed Value per Trade (₹) (Default)"),
            fixed_pos_value=d.get("fixed_pos_value", 50000.0),
            lot_size=d.get("lot_size", 1),
        )

    def selectable_columns(self) -> List[str]:
        return selectable_columns(self.indicators)
