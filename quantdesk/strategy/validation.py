"""
quantdesk.strategy.validation — security hardening for the no-code pipeline (v7.1).

The audit found a real RCE path: the Strategy Builder's JSON upload feeds an arbitrary preset
into ``session_state`` -> codegen -> ``compiler.exec()`` with no validation, and codegen
interpolates user-controlled fields (rule ``var2``, column refs, params) verbatim into the
source string. Two independent layers close it:

* :func:`assert_safe_ast` — a defense-in-depth AST scan of the FINAL generated source, run by the
  compiler right before ``exec()``. It rejects imports outside a tiny allowlist, dangerous
  builtins, and any dunder attribute/string constant (blocking ``().__class__.__bases__``-style
  sandbox escapes). Because EVERY run goes through ``compile_strategy``, this protects even
  programmatic callers (tests, the optimizer) — not just the upload path. This is the guarantee.

* :func:`validate_preset` — schema/type-validates an uploaded or server preset BEFORE it reaches
  ``session_state``: every rule/zone column reference must resolve to a real OHLCV/indicator
  column, every threshold/param must be numeric, and every type/operator/action/mode must be a
  known registry value. This is the friendly front door (clear rejection, no malformed codegen).
"""
from __future__ import annotations

import ast
import math
import re
from numbers import Real
from typing import Any

from quantdesk.strategy.indicators import (
    INDICATOR_PARAM_SPECS,
    INDICATOR_TYPES,
    FormulaValidationError,
    assert_safe_formula,
)
from quantdesk.strategy.models import (
    OHLCV,
    OPERATORS,
    RM_MODES,
    RULE_TIMINGS,
    STRAT_DIRECTIONS,
    ZONE_ACTIONS,
    output_columns_for,
    safe_name,
    selectable_columns,
)

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PresetValidationError(ValueError):
    """Raised when an uploaded/loaded preset is malformed or references unknown columns."""


class UnsafeCodeError(ValueError):
    """Raised when generated strategy source contains a disallowed construct."""


# ---------------------------------------------------------------- AST defense-in-depth
# Modules the generated code legitimately imports (top-level `import pandas_ta as pta`, plus the
# Hurst/HMM/scipy imports emitted INSIDE indicator helpers). Anything else is rejected.
_ALLOWED_IMPORT_ROOTS = {"numpy", "pandas", "pandas_ta", "ta", "hurst", "hmmlearn", "scipy"}

# Names that must never appear in generated source. NB: getattr/hasattr ARE emitted legitimately
# (e.g. getattr(self, '_prev_position_size', 0)); they stay allowed, but the dunder-string check
# below still blocks getattr(x, '__class__')-style escapes.
_DENIED_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals", "locals", "vars",
    "breakpoint", "memoryview", "setattr", "delattr", "os", "sys", "subprocess", "socket",
    "importlib", "builtins", "__builtins__", "execfile", "reload", "help",
}


def assert_safe_ast(source: str) -> None:
    """Raise :class:`UnsafeCodeError` if ``source`` contains a disallowed construct.

    Denylist + import-allowlist + a blanket dunder block on both attribute access and string
    constants. The dunder block is what neutralises the classic ``().__class__.__bases__[0].
    __subclasses__()`` and ``getattr(x, '__globals__')`` escapes even though Python injects the
    full builtins into the exec namespace.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:  # a botched injection usually surfaces as a syntax error first
        raise UnsafeCodeError(f"Generated strategy source failed to parse: {e}") from e

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _ALLOWED_IMPORT_ROOTS:
                    raise UnsafeCodeError(f"Disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _ALLOWED_IMPORT_ROOTS:
                raise UnsafeCodeError(f"Disallowed import from: {node.module}")
        elif isinstance(node, ast.Name):
            if node.id in _DENIED_NAMES:
                raise UnsafeCodeError(f"Disallowed name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise UnsafeCodeError(f"Disallowed dunder attribute: .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("__"):
                raise UnsafeCodeError("Disallowed dunder string literal")


# ---------------------------------------------------------------- preset validation
def _num(x: Any, field: str) -> float:
    """Assert ``x`` is a finite real number (bools excluded; numeric strings tolerated). Returns float."""
    if isinstance(x, bool):
        raise PresetValidationError(f"{field} must be a number, got {x!r}")
    if isinstance(x, Real):
        v = float(x)
    elif isinstance(x, str):
        try:
            v = float(x)
        except ValueError:
            raise PresetValidationError(f"{field} must be a number, got {x!r}") from None
    else:
        raise PresetValidationError(f"{field} must be a number, got {x!r}")
    if not math.isfinite(v):
        raise PresetValidationError(f"{field} must be finite, got {x!r}")
    return v


def _require_list_of_dicts(x: Any, field: str) -> list:
    if not isinstance(x, list):
        raise PresetValidationError(f"{field} must be a list.")
    for i, item in enumerate(x):
        if not isinstance(item, dict):
            raise PresetValidationError(f"{field}[{i}] must be an object.")
    return x


def validate_preset(data: Any) -> dict:
    """Validate a preset dict (uploaded JSON or a server file). Returns it, or raises.

    Security-critical guarantees enforced: indicator ``type`` is a known registry type; every
    param key is valid for that type and its value numeric; every rule/zone column reference
    resolves to a real OHLCV/indicator output column (so an injected ``var1`` can't slip through);
    numeric thresholds are numeric; operators/actions/modes/directions are known enum values.
    """
    if not isinstance(data, dict):
        raise PresetValidationError("Preset must be a JSON object.")

    inds = _require_list_of_dicts(data.get("indicators", []) or [], "indicators")
    defined = set(OHLCV)
    seen_names: set[str] = set()
    seen_safe_names: set[str] = set()
    for i, ind in enumerate(inds):
        itype = ind.get("type")
        if itype not in INDICATOR_TYPES:
            raise PresetValidationError(f"indicators[{i}].type {itype!r} is not a known indicator.")
        name = ind.get("name")
        if not isinstance(name, str) or not name:
            raise PresetValidationError(f"indicators[{i}].name must be a non-empty string.")
        sname = safe_name(name)
        if name in seen_names or sname in seen_safe_names:
            raise PresetValidationError(f"indicators[{i}].name {name!r} duplicates an existing indicator.")
        source = ind.get("source", "Close")
        if not isinstance(source, str) or source not in defined:
            raise PresetValidationError(f"indicators[{i}].source {source!r} is not a defined column.")
        if "placement" in ind and ind["placement"] is not None:
            if ind["placement"] not in ("overlay", "panel"):
                raise PresetValidationError(
                    f"indicators[{i}].placement must be 'overlay' or 'panel', got {ind['placement']!r}."
                )
        params = ind.get("params", {}) or {}
        if not isinstance(params, dict):
            raise PresetValidationError(f"indicators[{i}].params must be an object.")
        spec_map = {spec[0]: spec for spec in INDICATOR_PARAM_SPECS.get(itype, [])}
        for k, v in params.items():
            if k not in spec_map:
                raise PresetValidationError(f"indicators[{i}].params has unknown key {k!r} for {itype}.")
            _, kind, _, extra = spec_map[k]
            if kind == "select":
                choices = extra.get("choices", [])
                if not isinstance(v, str) or v not in choices:
                    raise PresetValidationError(
                        f"indicators[{i}].params.{k} must be one of {choices}, got {v!r}."
                    )
            else:
                num_v = _num(v, f"indicators[{i}].params.{k}")
                if itype in ("Series Math", "Rolling Stat", "Bar Shift"):
                    if "min" in extra and num_v < float(extra["min"]):
                        raise PresetValidationError(
                            f"indicators[{i}].params.{k} must be >= {extra['min']}, got {v!r}."
                        )
                    if "max" in extra and num_v > float(extra["max"]):
                        raise PresetValidationError(
                            f"indicators[{i}].params.{k} must be <= {extra['max']}, got {v!r}."
                        )
        if itype == "Series Math":
            use_num = int(params.get("use_num", 0) or 0)
            rhs = ind.get("rhs", "Close")
            if use_num == 0 or ("rhs" in ind and rhs not in (None, "")):
                if not isinstance(rhs, str) or rhs not in defined:
                    raise PresetValidationError(f"indicators[{i}].rhs {rhs!r} is not a defined column.")
        elif itype == "Custom Formula":
            formula = ind.get("formula")
            if not isinstance(formula, str) or not formula.strip():
                raise PresetValidationError(f"indicators[{i}].formula must be a non-empty string.")
            allowed_formula_vars = {c for c in defined if c.isidentifier()}
            for c in defined:
                sc = safe_name(c)
                if sc and sc.isidentifier():
                    allowed_formula_vars.add(sc)
            try:
                assert_safe_formula(formula, allowed_vars=allowed_formula_vars)
            except FormulaValidationError as e:
                raise PresetValidationError(f"indicators[{i}].formula is unsafe or invalid: {e}") from e
        seen_names.add(name)
        seen_safe_names.add(sname)
        defined.update(output_columns_for(ind))

    legal = set(selectable_columns(inds))  # OHLCV + every indicator output column

    def _check_rules(rules_field: str) -> None:
        rules = _require_list_of_dicts(data.get(rules_field, []) or [], rules_field)
        for j, r in enumerate(rules):
            if r.get("var1") not in legal:
                raise PresetValidationError(f"{rules_field}[{j}].var1 {r.get('var1')!r} is not a known column.")
            if r.get("operator") not in OPERATORS:
                raise PresetValidationError(f"{rules_field}[{j}].operator {r.get('operator')!r} is invalid.")
            if r.get("logic", "IF") not in ("IF", "AND", "OR"):
                raise PresetValidationError(f"{rules_field}[{j}].logic must be IF/AND/OR.")
            if r.get("is_num"):
                _num(r.get("var2"), f"{rules_field}[{j}].var2")
            elif r.get("var2") not in legal:
                raise PresetValidationError(f"{rules_field}[{j}].var2 {r.get('var2')!r} is not a known column.")
            if "group" in r and r["group"] is not None:
                g = r["group"]
                if isinstance(g, bool) or not isinstance(g, int) or g < 0:
                    raise PresetValidationError(f"{rules_field}[{j}].group must be a non-negative integer.")
            if "negate" in r and r["negate"] is not None and not isinstance(r["negate"], bool):
                raise PresetValidationError(f"{rules_field}[{j}].negate must be a bool.")
            if "timing" in r and r["timing"] is not None and r["timing"] not in RULE_TIMINGS:
                raise PresetValidationError(
                    f"{rules_field}[{j}].timing {r['timing']!r} must be one of {RULE_TIMINGS}."
                )
            if "timing_bars" in r and r["timing_bars"] is not None:
                tb = r["timing_bars"]
                if isinstance(tb, bool) or not isinstance(tb, int) or tb < 1:
                    raise PresetValidationError(f"{rules_field}[{j}].timing_bars must be an integer >= 1.")

    _check_rules("buy_rules")
    _check_rules("sell_rules")
    _check_rules("no_trade_rules")

    zones = _require_list_of_dicts(data.get("range_zones", []) or [], "range_zones")
    for k, z in enumerate(zones):
        if z.get("indicator") not in legal:
            raise PresetValidationError(f"range_zones[{k}].indicator {z.get('indicator')!r} is not a known column.")
        _num(z.get("min_val"), f"range_zones[{k}].min_val")
        _num(z.get("max_val"), f"range_zones[{k}].max_val")
        if z.get("action") not in ZONE_ACTIONS:
            raise PresetValidationError(f"range_zones[{k}].action {z.get('action')!r} is invalid.")
        if "tight_pct" in z:
            _num(z.get("tight_pct"), f"range_zones[{k}].tight_pct")

    rm = data.get("risk_management", {}) or {}
    if not isinstance(rm, dict):
        raise PresetValidationError("risk_management must be an object.")
    if rm.get("mode", RM_MODES[0]) not in RM_MODES:
        raise PresetValidationError(f"risk_management.mode {rm.get('mode')!r} is invalid.")
    for f in ("sl_pct", "tp_pct", "trail_pct", "risk_pct", "max_dd_halt_pct",
              "atr_period", "atr_stop_mult", "max_bars_in_trade",
              "breakeven_trigger_pct", "partial_exit_pct", "partial_trigger_pct"):
        if f in rm:
            val = _num(rm.get(f), f"risk_management.{f}")
            if val < 0:
                raise PresetValidationError(f"risk_management.{f} must be >= 0, got {val!r}")
    if "atr_period" in rm and _num(rm.get("atr_period"), "risk_management.atr_period") < 1:
        raise PresetValidationError(f"risk_management.atr_period must be >= 1, got {rm.get('atr_period')!r}")
    for bf in ("htf_bias", "intraday_square_off", "entry_time_filter"):
        if bf in rm and not isinstance(rm.get(bf), bool):
            raise PresetValidationError(f"risk_management.{bf} must be a bool, got {rm.get(bf)!r}")
    for tf in ("entry_start_time", "entry_end_time"):
        if tf in rm and rm.get(tf) is not None:
            tval = rm.get(tf)
            if not isinstance(tval, str) or not _HHMM_RE.match(tval):
                raise PresetValidationError(
                    f"risk_management.{tf} must be a 24h 'HH:MM' time string, got {tval!r}"
                )
    for hf in ("htf_minutes", "htf_ema_period"):
        if hf in rm:
            hval = _num(rm.get(hf), f"risk_management.{hf}")
            if hval < 1:
                raise PresetValidationError(f"risk_management.{hf} must be >= 1, got {hval!r}")
    if rm.get("strat_dir", STRAT_DIRECTIONS[0]) not in STRAT_DIRECTIONS:
        raise PresetValidationError(f"risk_management.strat_dir {rm.get('strat_dir')!r} is invalid.")

    if "fixed_pos_value" in data:
        _num(data.get("fixed_pos_value"), "fixed_pos_value")
    if "lot_size" in data:
        _num(data.get("lot_size"), "lot_size")
    if "pos_size_type" in data and not isinstance(data["pos_size_type"], str):
        raise PresetValidationError("pos_size_type must be a string.")

    return data
