"""
quantdesk.strategy.templates — starter strategy templates, plain-English translator, and sanity checker.

Provides beginner-friendly building blocks for Strategy Builder v2:
- ``STARTER_TEMPLATES``: 5 ready-to-run StrategyConfig dicts (all validated via ``validate_preset``).
- ``TEMPLATE_DESCRIPTIONS``: short plain-English explanations for each starter template.
- ``describe_strategy_plain_english(cfg)``: translates indicators, rules, filters, sizing, and risk
  into human-readable lines.
- ``check_strategy_sanity(cfg)``: detects common beginner traps and contradictory rule bounds.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from quantdesk.strategy.models import RiskManagement, StrategyConfig, selectable_columns

TEMPLATE_DESCRIPTIONS: Dict[str, str] = {
    "Golden Cross": "Classic trend-following: buys when fast EMA (20) crosses above slow EMA (50) and exits on cross below.",
    "RSI Oversold Bounce": "Mean-reversion bounce: buys when RSI (14) dips below 30 (oversold) and exits above 70 or via SL/TP.",
    "Bollinger Breakout": "Volatility breakout: buys when price breaks above the upper Bollinger Band and trails with a 2.5% stop.",
    "Supertrend + Volume Surge": "Momentum + volume confirmation: enters on bullish Supertrend with above-average volume and ATR stop.",
    "Intraday MIS Pullback": "Intraday pullback: buys dips (RSI < 40) in an uptrend (Close > EMA 20) between 09:30–14:45 with MIS square-off.",
}


def _make_templates() -> Dict[str, Dict[str, Any]]:
    raw: Dict[str, StrategyConfig] = {
        "Golden Cross": StrategyConfig(
            indicators=[
                {"name": "EMA_20", "type": "EMA", "source": "Close", "params": {"period": 20}},
                {"name": "EMA_50", "type": "EMA", "source": "Close", "params": {"period": 50}},
            ],
            buy_rules=[
                {"logic": "IF", "var1": "EMA_20", "operator": "Crosses Above", "var2": "EMA_50", "is_num": False},
            ],
            sell_rules=[
                {"logic": "IF", "var1": "EMA_20", "operator": "Crosses Below", "var2": "EMA_50", "is_num": False},
            ],
            risk_management=RiskManagement(
                mode="Static % Stop Loss / Take Profit",
                sl_pct=3.0,
                tp_pct=6.0,
                strat_dir="Long Only",
            ),
        ),
        "RSI Oversold Bounce": StrategyConfig(
            indicators=[
                {"name": "RSI_14", "type": "RSI", "source": "Close", "params": {"period": 14}},
            ],
            buy_rules=[
                {"logic": "IF", "var1": "RSI_14", "operator": "<", "var2": 30.0, "is_num": True},
            ],
            sell_rules=[
                {"logic": "IF", "var1": "RSI_14", "operator": ">", "var2": 70.0, "is_num": True},
            ],
            risk_management=RiskManagement(
                mode="Static % Stop Loss / Take Profit",
                sl_pct=2.5,
                tp_pct=5.0,
                strat_dir="Long Only",
            ),
        ),
        "Bollinger Breakout": StrategyConfig(
            indicators=[
                {"name": "BB_20", "type": "Bollinger Bands", "source": "Close", "params": {"period": 20, "std_dev": 2.0}},
                {"name": "EMA_20", "type": "EMA", "source": "Close", "params": {"period": 20}},
            ],
            buy_rules=[
                {"logic": "IF", "var1": "Close", "operator": "Crosses Above", "var2": "BB_20_high", "is_num": False},
            ],
            sell_rules=[
                {"logic": "IF", "var1": "Close", "operator": "Crosses Below", "var2": "EMA_20", "is_num": False},
            ],
            risk_management=RiskManagement(
                mode="Trailing Stop Loss (%)",
                trail_pct=2.5,
                strat_dir="Long Only",
            ),
        ),
        "Supertrend + Volume Surge": StrategyConfig(
            indicators=[
                {"name": "ST_7", "type": "Supertrend", "source": "Close", "params": {"period": 7, "multiplier": 3.0}},
                {"name": "Vol_Ratio", "type": "Ratio to SMA", "source": "Volume", "params": {"period": 20}},
            ],
            buy_rules=[
                {"logic": "IF", "var1": "ST_7_dir", "operator": "==", "var2": 1.0, "is_num": True},
                {"logic": "AND", "var1": "Vol_Ratio", "operator": ">", "var2": 1.2, "is_num": True},
            ],
            sell_rules=[
                {"logic": "IF", "var1": "ST_7_dir", "operator": "==", "var2": -1.0, "is_num": True},
            ],
            risk_management=RiskManagement(
                mode="ATR Stop Loss (Volatility)",
                atr_period=14,
                atr_stop_mult=2.0,
                tp_pct=0.0,
                strat_dir="Long Only",
            ),
        ),
        "Intraday MIS Pullback": StrategyConfig(
            indicators=[
                {"name": "EMA_20", "type": "EMA", "source": "Close", "params": {"period": 20}},
                {"name": "RSI_14", "type": "RSI", "source": "Close", "params": {"period": 14}},
            ],
            buy_rules=[
                {"logic": "IF", "var1": "Close", "operator": ">", "var2": "EMA_20", "is_num": False},
                {"logic": "AND", "var1": "RSI_14", "operator": "<", "var2": 40.0, "is_num": True},
            ],
            sell_rules=[
                {"logic": "IF", "var1": "RSI_14", "operator": ">", "var2": 65.0, "is_num": True},
            ],
            risk_management=RiskManagement(
                mode="Static % Stop Loss / Take Profit",
                sl_pct=1.0,
                tp_pct=2.0,
                strat_dir="Long Only",
                intraday_square_off=True,
                entry_time_filter=True,
                entry_start_time="09:30",
                entry_end_time="14:45",
            ),
        ),
    }
    return {name: cfg.to_dict() for name, cfg in raw.items()}


STARTER_TEMPLATES: Dict[str, Dict[str, Any]] = _make_templates()


def get_template(name: str) -> Dict[str, Any]:
    """Return a deep copy of a named starter template dict."""
    if name not in STARTER_TEMPLATES:
        raise KeyError(f"Unknown starter template: {name!r}")
    return copy.deepcopy(STARTER_TEMPLATES[name])


def _coerce_cfg(cfg: StrategyConfig | Dict[str, Any]) -> StrategyConfig:
    if isinstance(cfg, StrategyConfig):
        return cfg
    if isinstance(cfg, dict):
        return StrategyConfig.from_dict(cfg)
    raise TypeError(f"Expected StrategyConfig or dict, got {type(cfg).__name__}")


_OP_WORDS = {
    ">": "is above",
    "<": "is below",
    "==": "equals",
    ">=": "is at or above",
    "<=": "is at or below",
    "Crosses Above": "crosses above",
    "Crosses Below": "crosses below",
}

_MATH_SYMBOLS = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "pct_diff": "% diff vs",
}


def _describe_indicator(ind: Dict[str, Any]) -> str:
    name = ind.get("name", "Indicator")
    itype = ind.get("type", "")
    src = ind.get("source", "Close")
    params = ind.get("params", {}) or {}

    if itype == "Series Math":
        op = params.get("op", "add")
        sym = _MATH_SYMBOLS.get(op, op)
        rhs = params.get("num_val", 0.0) if int(params.get("use_num", 0) or 0) else ind.get("rhs", "Close")
        return f"{name}: {src} {sym} {rhs} (Series Math)"
    if itype == "Rolling Stat":
        stat = params.get("stat", "max")
        period = params.get("period", 20)
        return f"{name}: rolling {stat} of {src} over {period} bars"
    if itype == "Bar Shift":
        shift = params.get("shift", 1)
        return f"{name}: {src} shifted back {shift} bar(s)"
    if itype == "Custom Formula":
        formula = ind.get("formula", "Close")
        return f"{name}: formula `{formula}`"

    if params:
        p_str = ", ".join(f"{k}={v}" for k, v in params.items())
        return f"{name}: {itype}({p_str}) on {src}"
    return f"{name}: {itype} on {src}"


def _describe_single_rule(r: Dict[str, Any]) -> str:
    v1 = r.get("var1", "?")
    op = _OP_WORDS.get(r.get("operator", ">"), r.get("operator", ">"))
    v2 = r.get("var2", "?")
    phrase = f"{v1} {op} {v2}"

    timing = r.get("timing", "now") or "now"
    tb = int(r.get("timing_bars", 1) or 1)
    if timing == "bars_ago":
        phrase += f" ({tb} bar{'s' if tb != 1 else ''} ago)"
    elif timing == "within_last":
        phrase += f" (within last {tb} bar{'s' if tb != 1 else ''})"
    elif timing == "consecutive":
        phrase += f" (for {tb} consecutive bar{'s' if tb != 1 else ''})"

    if r.get("negate"):
        phrase = f"NOT ({phrase})"
    return phrase


def _describe_rule_list(rules: List[Dict[str, Any]], empty_msg: str) -> List[str]:
    if not rules:
        return [empty_msg]

    groups: List[Tuple[str, List[str]]] = []
    cur_gid: Optional[int] = None
    for idx, r in enumerate(rules):
        gid = int(r.get("group", 0) or 0)
        clause = _describe_single_rule(r)
        logic = r.get("logic", "IF" if idx == 0 else "AND")
        if cur_gid is None or gid != cur_gid:
            groups.append((logic, [clause]))
            cur_gid = gid
        else:
            groups[-1][1].append(f"{logic} {clause}")

    if len(groups) == 1:
        _, clauses = groups[0]
        return [f"Rule {i + 1}: {c}" for i, c in enumerate(clauses)]

    lines: List[str] = []
    for g_idx, (joiner, clauses) in enumerate(groups):
        prefix = "" if g_idx == 0 else f"{joiner} "
        lines.append(f"{prefix}Group {g_idx + 1}: ({' '.join(clauses)})")
    return lines


def describe_strategy_plain_english(cfg: StrategyConfig | Dict[str, Any]) -> Dict[str, List[str]]:
    """Return friendly plain-English summary lines grouped by strategy section."""
    sc = _coerce_cfg(cfg)
    rm = sc.risk_management

    indicators = (
        [_describe_indicator(ind) for ind in sc.indicators]
        if sc.indicators
        else ["No custom indicators (uses raw OHLCV columns only)."]
    )
    buy = _describe_rule_list(sc.buy_rules, "No BUY rules defined yet.")
    sell = _describe_rule_list(sc.sell_rules, "No explicit SELL rules (exits rely on Stop Loss / Risk settings).")

    filters: List[str] = []
    if sc.no_trade_rules:
        nt_desc = _describe_rule_list(sc.no_trade_rules, "")
        filters.append("Block new entries when: " + " ".join(nt_desc))
    for z in sc.range_zones:
        lbl = z.get("label") or z.get("indicator", "Zone")
        filters.append(
            f"Range Zone '{lbl}': when {z.get('indicator')} is in [{z.get('min_val')}, {z.get('max_val')}] -> {z.get('action')}"
        )
    if rm.htf_bias:
        filters.append(f"Higher-TF Trend Bias: only trade with {rm.htf_minutes}m EMA({rm.htf_ema_period}) direction.")
    if rm.entry_time_filter:
        filters.append(f"Intraday Entry Window: only allow new entries between {rm.entry_start_time} and {rm.entry_end_time}.")
    if rm.intraday_square_off:
        filters.append("Intraday MIS Square-off: flatten open positions at the last bar of each session.")
    if not filters:
        filters.append("No extra entry, time, or regime filters active.")

    lot_txt = f" (rounded to {sc.lot_size}-unit lots)" if sc.lot_size > 1 else ""
    if sc.pos_size_type == "Fixed Value per Trade (₹) (Default)":
        sizing = [f"Fixed notional ₹{sc.fixed_pos_value:,.0f} per trade{lot_txt}."]
    elif sc.pos_size_type == "Risk % of Equity per Trade":
        sizing = [f"Risk {rm.risk_pct:.2f}% of equity per trade based on stop distance{lot_txt}."]
    elif sc.pos_size_type in ("Half-Kelly (Adaptive)", "Full Kelly (Adaptive)"):
        sizing = [f"{sc.pos_size_type} position sizing from realized trade edge{lot_txt}."]
    else:
        sizing = [f"{sc.pos_size_type} position sizing{lot_txt}."]

    risk: List[str] = [f"Direction: {rm.strat_dir}."]
    if rm.mode == "Static % Stop Loss / Take Profit":
        risk.append(f"Static exits: Stop Loss {rm.sl_pct:.2f}% / Take Profit {rm.tp_pct:.2f}%.")
    elif rm.mode == "ATR Stop Loss (Volatility)":
        tp_note = f", Take Profit {rm.tp_pct:.2f}%" if rm.tp_pct > 0 else ""
        risk.append(f"Volatility stop: {rm.atr_stop_mult:.2f}× ATR({rm.atr_period}){tp_note}.")
    elif rm.mode == "Trailing Stop Loss (%)":
        risk.append(f"Trailing Stop Loss: {rm.trail_pct:.2f}% from peak/trough since entry.")
    else:
        risk.append(f"Exit mode: {rm.mode}.")

    if rm.breakeven_trigger_pct > 0:
        risk.append(f"Breakeven stop moves SL to entry after +{rm.breakeven_trigger_pct:.2f}% move.")
    if rm.partial_exit_pct > 0 and rm.partial_trigger_pct > 0:
        risk.append(f"Scale-out: book {rm.partial_exit_pct:.0f}% of position at +{rm.partial_trigger_pct:.2f}%.")
    if rm.max_bars_in_trade > 0:
        risk.append(f"Time stop: close trade after {rm.max_bars_in_trade} bars.")
    if rm.max_dd_halt_pct > 0:
        risk.append(f"Circuit breaker: halt trading if drawdown reaches {rm.max_dd_halt_pct:.1f}%.")

    return {
        "indicators": indicators,
        "buy": buy,
        "sell": sell,
        "filters": filters,
        "sizing": sizing,
        "risk": risk,
    }


def _check_contradictory_bounds(rules: List[Dict[str, Any]], section: str) -> List[str]:
    """Detect impossible numeric/series bounds inside contiguous AND-connected rule runs."""
    warnings: List[str] = []
    # Split into OR-separated runs (within the same group or across groups joined by OR).
    and_runs: List[List[Dict[str, Any]]] = []
    for idx, r in enumerate(rules):
        logic = r.get("logic", "IF" if idx == 0 else "AND")
        if idx == 0 or logic == "OR":
            and_runs.append([r])
        else:
            and_runs[-1].append(r)

    for run in and_runs:
        # Check same-timing, non-negated rules in an AND chain.
        num_bounds: Dict[Tuple[str, str, int], List[Tuple[str, float]]] = {}
        pair_ops: Dict[Tuple[str, str, str, int], set[str]] = {}
        for r in run:
            if r.get("negate"):
                continue
            timing = r.get("timing", "now") or "now"
            tb = int(r.get("timing_bars", 1) or 1)
            op = r.get("operator", "")
            v1 = str(r.get("var1", ""))
            if r.get("is_num"):
                try:
                    val = float(r.get("var2"))
                except (TypeError, ValueError):
                    continue
                if op in (">", ">=", "<", "<=", "=="):
                    num_bounds.setdefault((v1, timing, tb), []).append((op, val))
            else:
                v2 = str(r.get("var2", ""))
                if op in (">", ">=", "<", "<=", "=="):
                    pair_ops.setdefault((v1, v2, timing, tb), set()).add(op)

        for (v1, _, _), ops in num_bounds.items():
            lo = -float("inf")
            lo_inc = False
            hi = float("inf")
            hi_inc = False
            for op, val in ops:
                if op == ">":
                    if val > lo or (val == lo and lo_inc):
                        lo, lo_inc = val, False
                elif op == ">=":
                    if val > lo:
                        lo, lo_inc = val, True
                elif op == "<":
                    if val < hi or (val == hi and hi_inc):
                        hi, hi_inc = val, False
                elif op == "<=":
                    if val < hi:
                        hi, hi_inc = val, True
                elif op == "==":
                    if val > lo:
                        lo, lo_inc = val, True
                    if val < hi:
                        hi, hi_inc = val, True
            impossible = (lo > hi) or (lo == hi and not (lo_inc and hi_inc))
            if impossible:
                warnings.append(
                    f"Contradictory {section} condition: '{v1}' cannot satisfy all AND constraints simultaneously."
                )

        for (v1, v2, _, _), ops in pair_ops.items():
            if (">" in ops or ">=" in ops) and "<" in ops:
                warnings.append(
                    f"Contradictory {section} condition: '{v1}' cannot be both above and below '{v2}' at the same time."
                )
            elif ">" in ops and ("<" in ops or "<=" in ops):
                warnings.append(
                    f"Contradictory {section} condition: '{v1}' cannot be both above and at/below '{v2}' at the same time."
                )
    return warnings


def check_strategy_sanity(cfg: StrategyConfig | Dict[str, Any]) -> List[str]:
    """Flag common beginner traps, missing exits, and contradictory rule conditions."""
    sc = _coerce_cfg(cfg)
    rm = sc.risk_management
    issues: List[str] = []

    if not sc.buy_rules:
        issues.append("No BUY rules defined — the strategy will never open a trade.")

    has_stop_exit = (
        (rm.mode == "Static % Stop Loss / Take Profit" and (rm.sl_pct > 0 or rm.tp_pct > 0))
        or rm.mode in ("ATR Stop Loss (Volatility)", "Trailing Stop Loss (%)")
        or rm.max_bars_in_trade > 0
        or rm.intraday_square_off
    )
    if not sc.sell_rules and not has_stop_exit:
        issues.append(
            "No exit mechanism defined (no SELL rules, no Stop Loss/Take Profit, and no Time Stop) — "
            "positions may remain open until the end of the dataset."
        )

    if sc.pos_size_type == "Risk % of Equity per Trade":
        has_risk_stop = (
            rm.mode == "ATR Stop Loss (Volatility)"
            or (rm.mode == "Static % Stop Loss / Take Profit" and rm.sl_pct > 0)
        )
        if not has_risk_stop:
            issues.append(
                "Position sizing is 'Risk % of Equity per Trade' without a Static % or ATR Stop Loss — "
                "sizing will fall back to a fixed ₹ notional."
            )

    if (rm.partial_exit_pct > 0) != (rm.partial_trigger_pct > 0):
        issues.append(
            "Scale-out requires BOTH 'partial_exit_pct' and 'partial_trigger_pct' to be > 0."
        )

    if rm.entry_time_filter and rm.entry_start_time >= rm.entry_end_time:
        issues.append(
            f"Entry time window start ({rm.entry_start_time}) is not earlier than end ({rm.entry_end_time}) — "
            "all new entries will be blocked."
        )

    legal = set(selectable_columns(sc.indicators))
    for section_name, rules in (
        ("BUY", sc.buy_rules),
        ("SELL", sc.sell_rules),
        ("No-Trade", sc.no_trade_rules),
    ):
        for r in rules:
            if r.get("var1") not in legal:
                issues.append(f"{section_name} rule references unknown column '{r.get('var1')}'.")
            if not r.get("is_num") and r.get("var2") not in legal:
                issues.append(f"{section_name} rule references unknown column '{r.get('var2')}'.")
        issues.extend(_check_contradictory_bounds(rules, section_name))

    if sc.buy_rules and sc.sell_rules:
        clean_buy = [{k: v for k, v in r.items() if k != "_id"} for r in sc.buy_rules]
        clean_sell = [{k: v for k, v in r.items() if k != "_id"} for r in sc.sell_rules]
        if clean_buy == clean_sell:
            issues.append("BUY and SELL rules are identical — entries and exits will trigger on the exact same bars.")

    for z in sc.range_zones:
        try:
            if float(z.get("min_val", 0)) > float(z.get("max_val", 0)):
                lbl = z.get("label") or z.get("indicator", "Zone")
                issues.append(f"Range zone '{lbl}' has Min > Max and will never match.")
        except (TypeError, ValueError):
            pass

    return issues
