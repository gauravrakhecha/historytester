"""
No-Code Strategy Builder v2 page.

Supports both:
- 🌱 Simple (Guided): 1-click starter templates, beginner-curated indicators with plain-English `?`
  explanations, clean Buy/Sell rules, core Risk Management, Live Plain-English Summary,
  Sanity Warnings, and 1-click Quick Test Preview.
- ⚡ Pro (All Controls): all 27+ indicators, Lego custom blocks (Series Math, Rolling Stat,
  Bar Shift, Custom Formula), Scratch-style rule Groups `(A AND B) OR (C AND D)`, NOT toggle,
  rule Timing (`now`, `bars_ago`, `within_last`, `consecutive`), No-Trade Zone, Range Zones,
  Intraday Entry Window, HTF Bias, Breakeven, Scale-out, Adaptive Kelly, and Preset Import/Export.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from quantdesk import config as cfg_mod
from quantdesk.strategy import presets, templates
from quantdesk.strategy.codegen import build_strategy_code
from quantdesk.strategy.indicators import (
    INDICATOR_PARAM_SPECS,
    INDICATOR_TYPES,
    FormulaValidationError,
    assert_safe_formula,
)
from quantdesk.strategy.models import (
    OPERATORS,
    RULE_TIMINGS,
    STRAT_DIRECTIONS,
    StrategyConfig,
    safe_name,
    selectable_columns,
)
from quantdesk.strategy.validation import PresetValidationError, validate_preset
from quantdesk.ui import builder_helpers as bh

_STATE_KEYS = ["indicators", "buy_rules", "sell_rules", "no_trade_rules", "range_zones"]
ZONE_ACTIONS = ["Normal Trading", "Block New Entries", "Tighten Trailing Stop", "Close Position & Block"]

_DEFAULT_RM_STATE: Dict[str, Any] = {
    "rm_mode": "None (Use Sell Rules Only)",
    "sl_pct": 2.0,
    "tp_pct": 5.0,
    "trail_pct": 2.0,
    "strat_dir": "Long Only",
    "risk_pct": 1.0,
    "max_dd_halt_pct": 0.0,
    "atr_period": 14,
    "atr_stop_mult": 2.0,
    "max_bars_in_trade": 0,
    "breakeven_trigger_pct": 0.0,
    "partial_exit_pct": 0.0,
    "partial_trigger_pct": 0.0,
    "htf_bias": False,
    "htf_minutes": 60,
    "htf_ema_period": 50,
    "intraday_square_off": False,
    "entry_time_filter": False,
    "entry_start_time": "09:30",
    "entry_end_time": "14:45",
    "pos_size_type": "Fixed Value per Trade (₹) (Default)",
    "fixed_pos_value": 50000.0,
    "lot_size": 1,
}


def _row_id(row: dict) -> str:
    """Stable per-row id for widget keys; stripped by ``StrategyConfig.to_dict``."""
    if not row.get("_id"):
        row["_id"] = uuid.uuid4().hex[:8]
    return row["_id"]


def _ensure_defaults() -> None:
    for k in _STATE_KEYS:
        st.session_state.setdefault(k, [])
    for k, v in _DEFAULT_RM_STATE.items():
        st.session_state.setdefault(k, v)


def _restore_from_preset(data: dict) -> None:
    data = validate_preset(data)
    st.session_state["indicators"] = data.get("indicators", [])
    st.session_state["buy_rules"] = data.get("buy_rules", [])
    st.session_state["sell_rules"] = data.get("sell_rules", [])
    st.session_state["no_trade_rules"] = data.get("no_trade_rules", [])
    st.session_state["range_zones"] = data.get("range_zones", [])
    rm = data.get("risk_management", {})
    st.session_state["rm_mode"] = rm.get("mode", "None (Use Sell Rules Only)")
    st.session_state["sl_pct"] = rm.get("sl_pct", 2.0)
    st.session_state["tp_pct"] = rm.get("tp_pct", 5.0)
    st.session_state["trail_pct"] = rm.get("trail_pct", 2.0)
    st.session_state["strat_dir"] = rm.get("strat_dir", "Long Only")
    st.session_state["risk_pct"] = rm.get("risk_pct", 1.0)
    st.session_state["max_dd_halt_pct"] = rm.get("max_dd_halt_pct", 0.0)
    st.session_state["atr_period"] = rm.get("atr_period", 14)
    st.session_state["atr_stop_mult"] = rm.get("atr_stop_mult", 2.0)
    st.session_state["max_bars_in_trade"] = rm.get("max_bars_in_trade", 0)
    st.session_state["breakeven_trigger_pct"] = rm.get("breakeven_trigger_pct", 0.0)
    st.session_state["partial_exit_pct"] = rm.get("partial_exit_pct", 0.0)
    st.session_state["partial_trigger_pct"] = rm.get("partial_trigger_pct", 0.0)
    st.session_state["htf_bias"] = rm.get("htf_bias", False)
    st.session_state["htf_minutes"] = rm.get("htf_minutes", 60)
    st.session_state["htf_ema_period"] = rm.get("htf_ema_period", 50)
    st.session_state["intraday_square_off"] = rm.get("intraday_square_off", False)
    st.session_state["entry_time_filter"] = rm.get("entry_time_filter", False)
    st.session_state["entry_start_time"] = rm.get("entry_start_time", "09:30")
    st.session_state["entry_end_time"] = rm.get("entry_end_time", "14:45")
    st.session_state["pos_size_type"] = data.get("pos_size_type", "Fixed Value per Trade (₹) (Default)")
    st.session_state["fixed_pos_value"] = data.get("fixed_pos_value", 50000.0)
    st.session_state["lot_size"] = data.get("lot_size", 1)
    # Reset auto-name tracker so newly added indicators get fresh names
    for k in ("_last_auto_ind_name", "_ind_name_input", "_last_auto_lego_name", "_lego_name_input", "_quick_preview_res"):
        st.session_state.pop(k, None)


def _current_export() -> dict:
    return {
        "indicators": st.session_state.get("indicators", []),
        "buy_rules": st.session_state.get("buy_rules", []),
        "sell_rules": st.session_state.get("sell_rules", []),
        "no_trade_rules": st.session_state.get("no_trade_rules", []),
        "range_zones": st.session_state.get("range_zones", []),
        "risk_management": {
            "mode": st.session_state.get("rm_mode", "None (Use Sell Rules Only)"),
            "sl_pct": st.session_state.get("sl_pct", 2.0),
            "tp_pct": st.session_state.get("tp_pct", 5.0),
            "trail_pct": st.session_state.get("trail_pct", 2.0),
            "strat_dir": st.session_state.get("strat_dir", "Long Only"),
            "risk_pct": st.session_state.get("risk_pct", 1.0),
            "max_dd_halt_pct": st.session_state.get("max_dd_halt_pct", 0.0),
            "atr_period": st.session_state.get("atr_period", 14),
            "atr_stop_mult": st.session_state.get("atr_stop_mult", 2.0),
            "max_bars_in_trade": st.session_state.get("max_bars_in_trade", 0),
            "breakeven_trigger_pct": st.session_state.get("breakeven_trigger_pct", 0.0),
            "partial_exit_pct": st.session_state.get("partial_exit_pct", 0.0),
            "partial_trigger_pct": st.session_state.get("partial_trigger_pct", 0.0),
            "htf_bias": st.session_state.get("htf_bias", False),
            "htf_minutes": st.session_state.get("htf_minutes", 60),
            "htf_ema_period": st.session_state.get("htf_ema_period", 50),
            "intraday_square_off": st.session_state.get("intraday_square_off", False),
            "entry_time_filter": st.session_state.get("entry_time_filter", False),
            "entry_start_time": st.session_state.get("entry_start_time", "09:30"),
            "entry_end_time": st.session_state.get("entry_end_time", "14:45"),
        },
        "pos_size_type": st.session_state.get("pos_size_type", "Fixed Value per Trade (₹) (Default)"),
        "fixed_pos_value": st.session_state.get("fixed_pos_value", 50000.0),
        "lot_size": st.session_state.get("lot_size", 1),
    }


def _parse_hhmm(val: str, fallback: dt.time) -> dt.time:
    try:
        parts = str(val).strip().split(":")
        return dt.time(int(parts[0]), int(parts[1]))
    except Exception:
        return fallback


def _has_pro_features_active() -> bool:
    """Detect whether the current strategy already uses Pro-only features."""
    for ind in st.session_state.get("indicators", []):
        if ind.get("type") in bh.CUSTOM_BLOCK_TYPES:
            return True
    for list_key in ("buy_rules", "sell_rules"):
        for r in st.session_state.get(list_key, []):
            if int(r.get("group", 0) or 0) > 0 or r.get("negate") or r.get("timing", "now") != "now":
                return True
    return False


def _render_starter_templates(is_pro: bool) -> None:
    """1-click Starter Templates bar."""
    with st.expander("🎯 Quick Start: 1-Click Starter Templates", expanded=not is_pro):
        st.caption("Pick a proven template to load indicators, entry/exit rules, and risk controls in 1 click — then tweak anything below.")
        names = list(templates.STARTER_TEMPLATES.keys())
        cols = st.columns(len(names))
        for col, tname in zip(cols, names):
            desc = templates.TEMPLATE_DESCRIPTIONS.get(tname, "")
            with col:
                if st.button(f"✨ {tname}", key=f"tpl_btn_{safe_name(tname)}", help=desc, width="stretch"):
                    try:
                        _restore_from_preset(templates.get_template(tname))
                        st.session_state["_tpl_loaded_msg"] = f"Loaded starter template **{tname}**! Review or tweak below."
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Could not load template: {e}")
                st.caption(desc)
        if st.session_state.get("_tpl_loaded_msg"):
            st.success(st.session_state.pop("_tpl_loaded_msg"))


def _render_presets_section(is_pro: bool) -> None:
    """Server strategy presets, JSON import, and download."""
    with st.expander("💾 Saved Server Presets & JSON Import / Export", expanded=is_pro):
        available_strats = presets.list_strategies()
        c_load, c_save = st.columns(2)
        with c_load:
            st.markdown("#### Load Existing Strategy")
            if available_strats:
                selected_strat = st.selectbox("Select Strategy Preset", available_strats)
                if st.button("Load Strategy", type="primary", width="stretch"):
                    try:
                        _restore_from_preset(presets.load_strategy_raw(selected_strat))
                        st.success(f"Loaded {selected_strat} successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to load: {e}")
            else:
                st.info("No saved strategies found on server.")

            st.markdown("---")
            st.markdown("#### Import Local File")
            uploaded_file = st.file_uploader("Upload .json", type=["json"], label_visibility="collapsed")
            if uploaded_file is not None and st.button("Import Uploaded Strategy", width="stretch"):
                try:
                    _restore_from_preset(json.load(uploaded_file))
                    st.success("Strategy loaded successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to load: {e}")

        with c_save:
            st.markdown("#### Save Current Strategy to Server")
            save_name = st.text_input("Strategy Name", value="MyStrategy")
            if st.button("Save Strategy Permanently", width="stretch"):
                try:
                    fn = presets.save_strategy(save_name, _current_export())
                    st.success(f"Saved as {fn} on server!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save: {e}")

            if available_strats:
                st.markdown("---")
                del_strat = st.selectbox("Delete a Preset", available_strats, key="del_strat")
                if st.button("🗑️ Delete Strategy", width="stretch"):
                    try:
                        presets.delete_strategy(del_strat)
                        st.success(f"Deleted {del_strat}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to delete: {e}")

            st.markdown("---")
            st.markdown("#### Download Local Copy")
            st.download_button(
                label="📥 Download .json file",
                data=json.dumps(_current_export(), indent=4),
                file_name="my_strategy.json",
                mime="application/json",
                width="stretch",
            )


def _render_indicator_list() -> None:
    """Display active indicators with readable parameter details and remove button."""
    inds = st.session_state["indicators"]
    if not inds:
        st.info("No indicators defined yet. Add one below (or load a Starter Template above).")
        return

    for i, ind in enumerate(inds):
        rid = _row_id(ind)
        col1, col2, col3, col4 = st.columns([2, 2, 3, 1])
        col1.write(f"**{ind['name']}**")
        col2.write(f"Type: `{ind['type']}`")
        itype = ind.get("type", "")
        if itype == "Custom Formula":
            detail = f"Formula: `{ind.get('formula', '')}`"
        elif itype == "Series Math":
            p = ind.get("params", {}) or {}
            rhs_txt = p.get("num_val", 0.0) if int(p.get("use_num", 0) or 0) else ind.get("rhs", "Close")
            detail = f"{ind.get('source', 'Close')} `{p.get('op', 'add')}` {rhs_txt}"
        else:
            detail = f"Source: {ind.get('source', 'Close')}, Params: {ind.get('params', {})}"
        col3.write(detail)
        if col4.button("❌ Remove", key=f"rm_ind_{rid}", width="stretch"):
            new_inds = st.session_state["indicators"].copy()
            new_inds.pop(i)
            st.session_state["indicators"] = new_inds
            st.rerun()


def _sync_auto_name(suggested: str, input_key: str, last_key: str, label: str, help_text: str) -> str:
    """Keep an indicator/block name input auto-synced until the user edits it manually."""
    prev_auto = st.session_state.get(last_key)
    cur_val = st.session_state.get(input_key)
    if cur_val is None or cur_val == "" or cur_val == prev_auto:
        st.session_state[input_key] = suggested
    st.session_state[last_key] = suggested
    return st.text_input(
        label,
        key=input_key,
        help=help_text,
    )


def _render_param_inputs(ind_type: str, available_vars: List[str], key_prefix: str = "std") -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Render parameter inputs for ``ind_type`` and return ``(params, extra_fields)``."""
    params: Dict[str, Any] = {}
    extra_fields: Dict[str, Any] = {}

    if ind_type == "Series Math":
        c1, c2 = st.columns(2)
        op_choice = c1.selectbox(
            "Math Operation",
            ["add", "sub", "mul", "div", "pct_diff"],
            format_func=lambda x: bh.SERIES_MATH_OP_LABELS.get(x, x),
            key=f"{key_prefix}_sm_op",
        )
        rhs_mode = c2.radio(
            "Second Operand (B)",
            ["Another Column (Series B)", "Fixed Number"],
            horizontal=True,
            key=f"{key_prefix}_sm_mode",
        )
        use_num = 1 if rhs_mode == "Fixed Number" else 0
        params["op"] = op_choice
        params["use_num"] = use_num
        if use_num:
            params["num_val"] = float(
                st.number_input("Numeric Value (B)", value=0.0, step=0.1, key=f"{key_prefix}_sm_num")
            )
            extra_fields["rhs"] = "Close"
        else:
            extra_fields["rhs"] = st.selectbox(
                "Series B Column",
                available_vars,
                key=f"{key_prefix}_sm_rhs",
                help="Select a second OHLCV or indicator column.",
            )
            params["num_val"] = 0.0
        extra_fields["placement"] = st.radio(
            "Chart Placement",
            ["panel", "overlay"],
            index=0,
            horizontal=True,
            key=f"{key_prefix}_sm_place",
            help="Use 'overlay' if the output stays in price units (e.g. High - ATR), or 'panel' for ratios/oscillators.",
        )
        return params, extra_fields

    if ind_type == "Custom Formula":
        valid_vars = sorted({c for c in available_vars if c.isidentifier()} | {safe_name(c) for c in available_vars if safe_name(c).isidentifier()})
        st.caption(f"**Available columns:** `{', '.join(valid_vars)}`")
        st.caption(f"**Safe functions:** `{bh.formula_functions_caption()}`")
        formula = st.text_input(
            "Custom Formula Expression",
            value="(Close - Open) / atr(14)",
            key=f"{key_prefix}_cf_expr",
            help="Single-line math expression (no imports, loops, or underscores).",
        )
        with st.expander("💡 Formula Examples", expanded=False):
            for ex, desc in bh.FORMULA_EXAMPLES:
                st.markdown(f"- `{ex}` — {desc}")
        try:
            assert_safe_formula(formula, allowed_vars=set(valid_vars))
            st.caption("✅ Formula syntax & security check passed.")
        except FormulaValidationError as e:
            st.error(f"Formula check: {e}")

        extra_fields["formula"] = formula
        extra_fields["placement"] = st.radio(
            "Chart Placement",
            ["panel", "overlay"],
            index=0,
            horizontal=True,
            key=f"{key_prefix}_cf_place",
        )
        return params, extra_fields

    for key, kind, default, extra in INDICATOR_PARAM_SPECS.get(ind_type, []):
        label = key.replace("_", " ").title()
        if kind == "select":
            choices = extra.get("choices", [])
            fmt = bh.ROLLING_STAT_LABELS if key == "stat" else {}
            params[key] = st.selectbox(
                label,
                choices,
                index=choices.index(default) if default in choices else 0,
                format_func=lambda x, f=fmt: f.get(x, x),
                help=extra.get("help"),
                key=f"{key_prefix}_p_{ind_type}_{key}",
            )
        elif kind == "int":
            kwargs: Dict[str, Any] = {
                "value": int(default),
                "min_value": int(extra.get("min", 1)),
                "step": 1,
                "help": extra.get("help"),
                "key": f"{key_prefix}_p_{ind_type}_{key}",
            }
            if "max" in extra:
                kwargs["max_value"] = int(extra["max"])
            params[key] = int(st.number_input(label, **kwargs))
        else:
            params[key] = float(
                st.number_input(
                    label,
                    value=float(default),
                    min_value=float(extra.get("min", 0.1)),
                    format=extra.get("format"),
                    help=extra.get("help"),
                    key=f"{key_prefix}_p_{ind_type}_{key}",
                )
            )
    return params, extra_fields


def _save_indicator(ind_name: str, ind_type: str, source: str, params: Dict[str, Any], extra_fields: Dict[str, Any], available_vars: List[str]) -> None:
    existing = st.session_state["indicators"]
    clean_name = (ind_name or "").strip()
    if not clean_name:
        st.error("Indicator Name cannot be empty.")
        return
    if any(x["name"] == clean_name or safe_name(x["name"]) == safe_name(clean_name) for x in existing):
        st.error("Name already exists (or collides after sanitizing)!")
        return

    new_ind: Dict[str, Any] = {
        "name": clean_name,
        "type": ind_type,
        "source": source,
        "params": params,
    }
    new_ind.update(extra_fields)

    if ind_type == "Custom Formula":
        valid_vars = {c for c in available_vars if c.isidentifier()} | {safe_name(c) for c in available_vars if safe_name(c).isidentifier()}
        try:
            assert_safe_formula(new_ind.get("formula", ""), allowed_vars=valid_vars)
        except FormulaValidationError as e:
            st.error(f"Invalid Custom Formula: {e}")
            return

    new_inds = existing.copy()
    new_inds.append(new_ind)
    st.session_state["indicators"] = new_inds
    for k in ("_last_auto_ind_name", "_ind_name_input", "_last_auto_lego_name", "_lego_name_input"):
        st.session_state.pop(k, None)
    st.rerun()


def _render_add_indicator(available_vars: List[str], is_pro: bool) -> None:
    """Smart Indicator Builder supporting beginner indicators + Lego Custom Blocks + Safe Formula."""
    with st.expander("➕ Add New Indicator", expanded=True):
        if is_pro:
            tab_std, tab_lego = st.tabs(["📊 Standard Indicators (27+)", "🧱 Lego Custom Blocks & Formula"])
        else:
            tab_std, tab_lego = st.tabs(["🌟 Beginner Indicators", "🧩 All Indicators & Custom Blocks"])

        with tab_std:
            if is_pro:
                std_choices = [t for t in INDICATOR_TYPES if t not in bh.CUSTOM_BLOCK_TYPES]
            else:
                show_all = st.checkbox("Show all 27+ indicators", value=False, key="simple_show_all_inds")
                std_choices = [t for t in INDICATOR_TYPES if t not in bh.CUSTOM_BLOCK_TYPES] if show_all else bh.BEGINNER_INDICATORS

            ind_type = st.selectbox("Indicator Type", std_choices, key="std_ind_type")
            if ind_type in bh.INDICATOR_HELP:
                st.caption(f"❓ {bh.INDICATOR_HELP[ind_type]}")
            source = st.selectbox(
                "Source Data",
                available_vars,
                key="std_ind_src",
                help="Select any OHLCV column OR a previously defined indicator (chaining).",
            )
            params, extra_fields = _render_param_inputs(ind_type, available_vars, key_prefix="std")
            existing_names = [x["name"] for x in st.session_state["indicators"]]
            suggested = bh.suggest_indicator_name(ind_type, params, existing_names)
            ind_name = _sync_auto_name(
                suggested,
                "_ind_name_input",
                "_last_auto_ind_name",
                "Indicator Name",
                "Auto-suggested readable name. You can rename it anytime.",
            )

            if ind_type == "Angle of Indicator":
                st.info(
                    f"Computes: arctan( ({source}[now] - {source}[{params.get('period', 5)} bars ago]) "
                    f"/ ATR({params.get('atr_period', 14)}) ) × (180/π)"
                )
            if st.button("Save Indicator", key="btn_save_std_ind", type="primary"):
                _save_indicator(ind_name, ind_type, source, params, extra_fields, available_vars)

        with tab_lego:
            lego_choices = bh.CUSTOM_BLOCK_TYPES if is_pro else (bh.CUSTOM_BLOCK_TYPES + [t for t in INDICATOR_TYPES if t not in bh.BEGINNER_INDICATORS and t not in bh.CUSTOM_BLOCK_TYPES])
            lego_type = st.selectbox("Custom Block / Indicator Type", lego_choices, key="lego_ind_type")
            if lego_type in bh.INDICATOR_HELP:
                st.caption(f"❓ {bh.INDICATOR_HELP[lego_type]}")

            if lego_type == "Custom Formula":
                lego_source = "Close"
            else:
                src_label = "Series A (Source)" if lego_type == "Series Math" else "Source Data"
                lego_source = st.selectbox(src_label, available_vars, key="lego_ind_src")

            lego_params, lego_extra = _render_param_inputs(lego_type, available_vars, key_prefix="lego")
            existing_names = [x["name"] for x in st.session_state["indicators"]]
            lego_suggested = bh.suggest_indicator_name(lego_type, lego_params, existing_names)
            lego_name = _sync_auto_name(
                lego_suggested,
                "_lego_name_input",
                "_last_auto_lego_name",
                "Block Name",
                "Readable name for this custom block output column.",
            )
            if st.button("Save Custom Block", key="btn_save_lego_ind", type="primary"):
                _save_indicator(lego_name, lego_type, lego_source, lego_params, lego_extra, available_vars)


def _render_indicator_library() -> None:
    with st.expander("📚 Indicator Library (Saved Presets)", expanded=False):
        lib_c1, lib_c2 = st.columns(2)
        ind_presets = presets.list_indicator_presets()
        with lib_c1:
            st.markdown("**Load a saved indicator preset:**")
            if ind_presets:
                sel_preset = st.selectbox("Select Preset", ind_presets, key="sel_ind_preset")
                if st.button("📥 Add Preset to Strategy", width="stretch"):
                    try:
                        preset_data = presets.load_indicator_preset(sel_preset)
                        existing_names = [x["name"] for x in st.session_state["indicators"]]
                        for pind in preset_data.get("indicators", []):
                            if pind["name"] in existing_names:
                                pind["name"] = f"{pind['name']}_{len(existing_names)+1}"
                            st.session_state["indicators"].append(pind)
                        st.success(f"Added {len(preset_data.get('indicators', []))} indicators from '{sel_preset}'")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to load preset: {e}")
            else:
                st.info("No indicator presets saved yet.")
        with lib_c2:
            st.markdown("**Save current indicators as preset:**")
            preset_name = st.text_input("Preset Name", value="MyIndicatorSet", key="preset_save_name")
            if st.button("💾 Save as Indicator Preset", width="stretch"):
                if st.session_state["indicators"]:
                    try:
                        presets.save_indicator_preset(preset_name, st.session_state["indicators"])
                        st.success(f"Saved '{preset_name}' with {len(st.session_state['indicators'])} indicators!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to save: {e}")
                else:
                    st.warning("No indicators to save. Add indicators first.")
            if ind_presets:
                del_preset = st.selectbox("Delete Preset", ind_presets, key="del_ind_preset")
                if st.button("🗑️ Delete Preset", key="del_ind_btn", width="stretch"):
                    presets.delete_indicator_preset(del_preset)
                    st.success(f"Deleted '{del_preset}'")
                    st.rerun()


def _render_rules(rule_list: List[dict], list_name: str, variables: List[str], show_advanced: bool) -> None:
    for i, rule in enumerate(rule_list):
        rid = _row_id(rule)
        logic_opts = ["IF", "AND", "OR"] if i > 0 else ["IF"]
        cur_logic = rule.get("logic", "IF" if i == 0 else "AND")
        if cur_logic not in logic_opts:
            cur_logic = logic_opts[0]

        if show_advanced:
            c1, c_grp, c_not, c2, c3, c4, c_tim, c_tb, c5 = st.columns([1.0, 0.9, 0.7, 1.8, 1.5, 2.0, 1.6, 0.8, 0.7])
        else:
            c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 0.8])

        logic = c1.selectbox("Logic", logic_opts, index=logic_opts.index(cur_logic), key=f"{list_name}_log_{rid}")

        group = int(rule.get("group", 0) or 0)
        negate = bool(rule.get("negate", False))
        timing = rule.get("timing", "now") or "now"
        if timing not in RULE_TIMINGS:
            timing = "now"
        timing_bars = max(1, int(rule.get("timing_bars", 1) or 1))

        if show_advanced:
            group = int(
                c_grp.number_input(
                    "Grp #",
                    value=group,
                    min_value=0,
                    step=1,
                    key=f"{list_name}_grp_{rid}",
                    help="Same Group # = wrapped in parentheses e.g. (A AND B) OR (C AND D).",
                )
            )
            negate = bool(c_not.checkbox("NOT", value=negate, key=f"{list_name}_not_{rid}", help="Invert condition"))

        var1 = c2.selectbox(
            "Var 1",
            variables,
            index=variables.index(rule["var1"]) if rule.get("var1") in variables else 0,
            key=f"{list_name}_v1_{rid}",
        )
        op = c3.selectbox(
            "Operator",
            OPERATORS,
            index=OPERATORS.index(rule["operator"]) if rule.get("operator") in OPERATORS else 0,
            key=f"{list_name}_op_{rid}",
        )
        is_num = c4.checkbox("Is Number?", value=bool(rule.get("is_num", False)), key=f"{list_name}_isnum_{rid}")
        if is_num:
            try:
                default_val = float(rule.get("var2", 0))
            except (ValueError, TypeError):
                default_val = 0.0
            var2: Any = c4.number_input("Value", value=default_val, key=f"{list_name}_v2num_{rid}")
        else:
            var2 = c4.selectbox(
                "Var 2",
                variables,
                index=variables.index(rule["var2"]) if rule.get("var2") in variables else 0,
                key=f"{list_name}_v2var_{rid}",
            )

        if show_advanced:
            timing = c_tim.selectbox(
                "Timing",
                RULE_TIMINGS,
                index=RULE_TIMINGS.index(timing),
                format_func=lambda t: bh.TIMING_LABELS.get(t, t),
                key=f"{list_name}_tim_{rid}",
            )
            if timing != "now":
                timing_bars = int(
                    c_tb.number_input("N", value=timing_bars, min_value=1, step=1, key=f"{list_name}_tb_{rid}")
                )
            else:
                c_tb.caption("—")

        if c5.button("❌", key=f"{list_name}_rm_{rid}"):
            new_list = st.session_state[list_name].copy()
            new_list.pop(i)
            st.session_state[list_name] = new_list
            st.rerun()

        rule.update(
            {
                "logic": logic,
                "var1": var1,
                "operator": op,
                "var2": var2,
                "is_num": is_num,
                "group": group,
                "negate": negate,
                "timing": timing,
                "timing_bars": timing_bars,
            }
        )

    btn_label = {
        "buy_rules": "➕ Add BUY Condition",
        "sell_rules": "➕ Add SELL Condition",
        "no_trade_rules": "➕ Add No-Trade Condition",
    }.get(list_name, f"➕ Add Condition to {list_name}")
    if st.button(btn_label, key=f"add_{list_name}"):
        new_list = st.session_state[list_name].copy()
        new_list.append(
            {
                "logic": "AND" if len(new_list) > 0 else "IF",
                "var1": "Close",
                "operator": ">",
                "var2": "Close",
                "is_num": False,
                "group": 0,
                "negate": False,
                "timing": "now",
                "timing_bars": 1,
            }
        )
        st.session_state[list_name] = new_list
        st.rerun()


def _render_range_zones(available_vars: List[str]) -> None:
    st.markdown("Define **indicator value zones** with specific trading behaviours (block entries, tighten stop, etc.).")
    for i, zone in enumerate(st.session_state["range_zones"]):
        rid = _row_id(zone)
        zc1, zc2, zc3, zc4, zc5, zc6, zc7 = st.columns([2, 1.5, 1.5, 2, 2, 2, 0.8])
        zone_indicator_opts = available_vars.copy()
        cur_idx = zone_indicator_opts.index(zone["indicator"]) if zone["indicator"] in zone_indicator_opts else 0
        zone["indicator"] = zc1.selectbox("Indicator", zone_indicator_opts, index=cur_idx, key=f"rz_ind_{rid}")
        zone["min_val"] = zc2.number_input("Min", value=float(zone["min_val"]), step=0.01, format="%.2f", key=f"rz_min_{rid}")
        zone["max_val"] = zc3.number_input("Max", value=float(zone["max_val"]), step=0.01, format="%.2f", key=f"rz_max_{rid}")
        zone["label"] = zc4.text_input("Label", value=zone.get("label", ""), key=f"rz_lbl_{rid}")
        act_idx = ZONE_ACTIONS.index(zone["action"]) if zone["action"] in ZONE_ACTIONS else 0
        zone["action"] = zc5.selectbox("Action", ZONE_ACTIONS, index=act_idx, key=f"rz_act_{rid}")
        if zone["action"] == "Tighten Trailing Stop":
            zone["tight_pct"] = zc6.number_input("Tight SL %", value=float(zone.get("tight_pct", 1.0)), min_value=0.1, step=0.1, key=f"rz_tpct_{rid}")
        else:
            zc6.write("")
            zone["tight_pct"] = 0
        if zc7.button("❌", key=f"rz_rm_{rid}"):
            new_rz = st.session_state["range_zones"].copy()
            new_rz.pop(i)
            st.session_state["range_zones"] = new_rz
            st.rerun()

    if st.button("➕ Add Range Zone", key="add_range_zone"):
        new_rz = st.session_state["range_zones"].copy()
        new_rz.append(
            {
                "indicator": available_vars[0] if available_vars else "Close",
                "min_val": -10.0,
                "max_val": 10.0,
                "label": "Zone",
                "action": "Normal Trading",
                "tight_pct": 1.0,
            }
        )
        st.session_state["range_zones"] = new_rz
        st.rerun()


def _render_risk_management(is_pro: bool) -> None:
    st.subheader("Step 3: Risk Management")
    strat_dir = st.radio(
        "Strategy Direction",
        STRAT_DIRECTIONS,
        index=STRAT_DIRECTIONS.index(st.session_state.get("strat_dir", "Long Only"))
        if st.session_state.get("strat_dir") in STRAT_DIRECTIONS
        else 0,
        horizontal=True,
    )
    st.session_state["strat_dir"] = strat_dir

    pos_opts = [
        "Fixed Value per Trade (₹) (Default)",
        "Compound 100% Equity",
        "Risk % of Equity per Trade",
        "Half-Kelly (Adaptive)",
        "Full Kelly (Adaptive)",
    ]
    cur_pos = st.session_state.get("pos_size_type", pos_opts[0])
    pos_size_type = st.radio(
        "Position Sizing",
        pos_opts,
        index=pos_opts.index(cur_pos) if cur_pos in pos_opts else 0,
        horizontal=True,
        help="Fixed Value caps capital per trade. 100% Equity compounds your whole capital each trade. "
             "Risk % sizes each trade so a stop-out loses a fixed % of equity (professional sizing — needs a Stop Loss). "
             "Kelly sizes from THIS strategy's own realized win-rate & payoff as the backtest runs (Half-Kelly is the "
             "safer, lower-swing default most desks use).",
    )
    fixed_pos_value = float(st.session_state.get("fixed_pos_value", 50000.0))
    risk_pct = float(st.session_state.get("risk_pct", 1.0))
    if pos_size_type == "Fixed Value per Trade (₹) (Default)":
        fixed_pos_value = st.number_input("Amount per Trade (₹)", value=fixed_pos_value, min_value=100.0, step=1000.0)
    elif pos_size_type == "Risk % of Equity per Trade":
        risk_pct = st.number_input(
            "Risk % of Equity per Trade",
            value=risk_pct,
            min_value=0.1,
            max_value=50.0,
            step=0.1,
            help="Each trade is sized so hitting the stop loses ~this % of equity. Requires a Stop Loss.",
        )
        if st.session_state.get("rm_mode") not in ("Static % Stop Loss / Take Profit", "ATR Stop Loss (Volatility)"):
            st.caption("⚠️ Risk-% sizing needs a stop distance — set **Exit / Risk Management Mode → Static % Stop Loss** or **ATR Stop Loss** below.")
    elif pos_size_type in ("Half-Kelly (Adaptive)", "Full Kelly (Adaptive)"):
        st.caption(
            f"📈 Adaptive Kelly: sizes from your strategy's realized edge, engaging after "
            f"{cfg_mod.KELLY_MIN_TRADES} trades have closed (a fixed ₹ size is used during that warm-up). "
            "Full Kelly is aggressive and can swing hard — Half-Kelly is the recommended default."
        )
    lot_size = st.number_input(
        "Instrument Lot Size (Use 1 for Equity)",
        value=int(st.session_state.get("lot_size", 1)),
        min_value=1,
        step=1,
        help="Options and Futures trade in fixed lot multiples.",
    )

    st.session_state["pos_size_type"] = pos_size_type
    st.session_state["fixed_pos_value"] = fixed_pos_value
    st.session_state["risk_pct"] = risk_pct
    st.session_state["lot_size"] = lot_size

    rm_opts = [
        "None (Use Sell Rules Only)",
        "Static % Stop Loss / Take Profit",
        "ATR Stop Loss (Volatility)",
        "Trailing Stop Loss (%)",
        "Indicator-Based Exit (Advanced)",
    ]
    cur_rm = st.session_state.get("rm_mode", rm_opts[0])
    rm_mode = st.radio(
        "Exit / Risk Management Mode",
        rm_opts,
        index=rm_opts.index(cur_rm) if cur_rm in rm_opts else 0,
    )
    sl_pct = tp_pct = trail_pct = 0.0
    atr_period = int(st.session_state.get("atr_period", 14))
    atr_stop_mult = float(st.session_state.get("atr_stop_mult", 2.0))
    if rm_mode == "Static % Stop Loss / Take Profit":
        c1, c2 = st.columns(2)
        sl_pct = c1.number_input("Stop Loss %", value=float(st.session_state.get("sl_pct", 2.0)), min_value=0.0, step=0.1)
        tp_pct = c2.number_input("Take Profit %", value=float(st.session_state.get("tp_pct", 5.0)), min_value=0.0, step=0.1)
        st.info("SL/TP are fixed at entry price. Sell Rules ALSO trigger exits independently.")
    elif rm_mode == "ATR Stop Loss (Volatility)":
        c1, c2, c3 = st.columns(3)
        atr_period = c1.number_input("ATR Period", value=atr_period, min_value=1, step=1)
        atr_stop_mult = c2.number_input("ATR Stop ×", value=atr_stop_mult, min_value=0.1, step=0.1, help="Stop distance = this × ATR.")
        tp_pct = c3.number_input("Take Profit % (0 = none)", value=float(st.session_state.get("tp_pct", 0.0)), min_value=0.0, step=0.1)
        st.info("Volatility-adaptive stop: the stop sits mult×ATR from entry (wider in volatile markets).")
    elif rm_mode == "Trailing Stop Loss (%)":
        trail_pct = st.number_input("Trailing Stop %", value=float(st.session_state.get("trail_pct", 2.0)), min_value=0.1, step=0.1)
        st.info("The stop follows the highest price since entry. Sell Rules ALSO trigger exits independently.")
    elif rm_mode == "Indicator-Based Exit (Advanced)":
        st.info("Define exit conditions in the **SELL Conditions** above. They are the only exit trigger.")

    st.session_state["rm_mode"] = rm_mode
    st.session_state["sl_pct"] = sl_pct
    st.session_state["tp_pct"] = tp_pct
    st.session_state["trail_pct"] = trail_pct
    st.session_state["atr_period"] = atr_period
    st.session_state["atr_stop_mult"] = atr_stop_mult

    has_adv_active = any([
        float(st.session_state.get("max_dd_halt_pct", 0.0)) > 0,
        int(st.session_state.get("max_bars_in_trade", 0)) > 0,
        float(st.session_state.get("breakeven_trigger_pct", 0.0)) > 0,
        float(st.session_state.get("partial_exit_pct", 0.0)) > 0,
        bool(st.session_state.get("htf_bias", False)),
        bool(st.session_state.get("intraday_square_off", False)),
        bool(st.session_state.get("entry_time_filter", False)),
    ])
    with st.expander("⚙️ Advanced Exits, Intraday Time Window & HTF Bias", expanded=(is_pro or has_adv_active)):
        cbk1, cbk2 = st.columns(2)
        max_dd_halt_pct = cbk1.number_input(
            "🛑 Max-Drawdown Circuit Breaker % (0 = off)",
            value=float(st.session_state.get("max_dd_halt_pct", 0.0)),
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            help="Flatten any open position and stop taking new trades once equity falls this far below its running peak.",
        )
        max_bars_in_trade = cbk2.number_input(
            "⏱️ Time Stop — max bars per trade (0 = off)",
            value=int(st.session_state.get("max_bars_in_trade", 0)),
            min_value=0,
            step=1,
            help="Close any trade that is still open after this many bars.",
        )
        st.session_state["max_dd_halt_pct"] = max_dd_halt_pct
        st.session_state["max_bars_in_trade"] = max_bars_in_trade

        st.markdown("**Advanced trade management** (optional — leave at 0 to disable)")
        be_col, pe_col1, pe_col2 = st.columns(3)
        breakeven_trigger_pct = be_col.number_input(
            "🔒 Breakeven trigger % (0 = off)",
            value=float(st.session_state.get("breakeven_trigger_pct", 0.0)),
            min_value=0.0,
            step=0.1,
            help="Once price moves this % in your favour, the stop moves to entry price.",
        )
        partial_exit_pct = pe_col1.number_input(
            "✂️ Scale-out: % of position to bank (0 = off)",
            value=float(st.session_state.get("partial_exit_pct", 0.0)),
            min_value=0.0,
            max_value=99.0,
            step=5.0,
            help="Close this fraction of the position at the first target and let the rest run.",
        )
        partial_trigger_pct = pe_col2.number_input(
            "✂️ Scale-out trigger % (favourable move)",
            value=float(st.session_state.get("partial_trigger_pct", 0.0)),
            min_value=0.0,
            step=0.1,
            help="The favourable move (%) at which the partial is banked.",
        )
        st.session_state["breakeven_trigger_pct"] = breakeven_trigger_pct
        st.session_state["partial_exit_pct"] = partial_exit_pct
        st.session_state["partial_trigger_pct"] = partial_trigger_pct

        st.markdown("**Higher-timeframe bias & Intraday controls** (optional)")
        htf_c1, htf_c2, htf_c3, sq_c = st.columns([1.4, 1, 1, 1.6])
        htf_bias = htf_c1.checkbox(
            "📊 Higher-TF trend bias",
            value=bool(st.session_state.get("htf_bias", False)),
            help="Only take LONGS above / SHORTS below a higher-timeframe EMA.",
        )
        htf_minutes = htf_c2.number_input(
            "HTF minutes", value=int(st.session_state.get("htf_minutes", 60)), min_value=1, step=5, disabled=not htf_bias
        )
        htf_ema_period = htf_c3.number_input(
            "HTF EMA", value=int(st.session_state.get("htf_ema_period", 50)), min_value=1, step=1, disabled=not htf_bias
        )
        intraday_square_off = sq_c.checkbox(
            "🕒 Intraday square-off (MIS)",
            value=bool(st.session_state.get("intraday_square_off", False)),
            help="Flatten any open position at each session's last bar — no overnight carry.",
        )
        st.session_state["htf_bias"] = htf_bias
        st.session_state["htf_minutes"] = htf_minutes
        st.session_state["htf_ema_period"] = htf_ema_period
        st.session_state["intraday_square_off"] = intraday_square_off

        et_c1, et_c2, et_c3 = st.columns([1.6, 1, 1])
        entry_time_filter = et_c1.checkbox(
            "⏰ Intraday Entry Window",
            value=bool(st.session_state.get("entry_time_filter", False)),
            help="Only allow new entries between Start Time and End Time (24h HH:MM).",
        )
        start_t = et_c2.time_input(
            "Entry Start",
            value=_parse_hhmm(st.session_state.get("entry_start_time", "09:30"), dt.time(9, 30)),
            disabled=not entry_time_filter,
        )
        end_t = et_c3.time_input(
            "Entry End",
            value=_parse_hhmm(st.session_state.get("entry_end_time", "14:45"), dt.time(14, 45)),
            disabled=not entry_time_filter,
        )
        st.session_state["entry_time_filter"] = entry_time_filter
        st.session_state["entry_start_time"] = start_t.strftime("%H:%M")
        st.session_state["entry_end_time"] = end_t.strftime("%H:%M")


def render(ctx) -> None:
    st.header("🛠️ No-Code Strategy Builder")
    st.markdown("Build trading strategies visually — start in **Simple** mode in 30 seconds, or switch to **Pro** for full Lego blocks, rule groups, timing & intraday filters.")

    _ensure_defaults()

    mode_options = ["🌱 Simple (Guided)", "⚡ Pro (All Controls)"]
    builder_mode = st.radio(
        "Builder Mode",
        mode_options,
        index=0,
        horizontal=True,
        key="builder_mode",
        help="Simple mode keeps the workspace clean with starter templates and beginner-friendly controls. Pro mode unlocks all 27+ indicators, Lego math/formula blocks, rule groups, timing, and regime zones.",
    )
    is_pro = builder_mode == "⚡ Pro (All Controls)"

    _render_starter_templates(is_pro)
    _render_presets_section(is_pro)

    st.markdown("---")

    # --- Step 1: Indicators ------------------------------------------------
    st.subheader("Step 1: Define Indicators & Custom Blocks")
    _render_indicator_list()
    _render_indicator_library()
    available_vars = selectable_columns(st.session_state["indicators"])
    _render_add_indicator(available_vars, is_pro)

    # --- Step 2: Entry & Exit Rules ----------------------------------------
    st.subheader("Step 2: Entry & Exit Rules")
    variables = available_vars.copy()
    show_adv_rules = is_pro or _has_pro_features_active()
    if not show_adv_rules:
        st.caption("Tip: Switch to **⚡ Pro (All Controls)** above to unlock Scratch-style `Group #` parentheses, `NOT`, and `Timing` (`N bars ago` / `Within last N bars` / `Consecutive`).")

    st.markdown("#### 🟢 BUY Conditions")
    _render_rules(st.session_state["buy_rules"], "buy_rules", variables, show_adv_rules)
    st.markdown("#### 🔴 SELL Conditions")
    _render_rules(st.session_state["sell_rules"], "sell_rules", variables, show_adv_rules)

    # --- Step 2.5 & 2.75: Regime Filter & Range Zones ----------------------
    if is_pro:
        st.subheader("Step 2.5: Regime Filter (Optional)")
        st.markdown("Define **No-Trade Zone** conditions. When true, **no new trades are entered**.")
        _render_rules(st.session_state["no_trade_rules"], "no_trade_rules", variables, True)

        st.subheader("Step 2.75: Advanced Range Management (Optional)")
        _render_range_zones(available_vars)
    else:
        with st.expander("🚫 Optional: No-Trade Zone Filter", expanded=bool(st.session_state["no_trade_rules"])):
            _render_rules(st.session_state["no_trade_rules"], "no_trade_rules", variables, show_adv_rules)
        with st.expander("🎚️ Optional: Advanced Range Zones", expanded=bool(st.session_state["range_zones"])):
            _render_range_zones(available_vars)

    # --- Step 3: Risk Management -------------------------------------------
    _render_risk_management(is_pro)

    # --- Step 4: Live Summary, Sanity Check, Quick Preview & Compile -------
    st.markdown("---")
    st.subheader("Step 4: Review, Quick Test & Compile")

    export_dict = _current_export()
    cfg = StrategyConfig.from_dict(export_dict)
    bh.render_plain_english_card(cfg)
    bh.render_sanity_alerts(cfg)

    act_c1, act_c2 = st.columns(2)
    with act_c1:
        if st.button("⚡ Quick Test Strategy (Preview)", width="stretch"):
            try:
                validate_preset(export_dict)
                with st.spinner("Running quick preview backtest..."):
                    res = bh.run_quick_preview(cfg)
                st.session_state["_quick_preview_res"] = res
            except (PresetValidationError, FormulaValidationError, ValueError) as e:
                st.error(f"Cannot preview strategy: {e}")
            except Exception as e:  # noqa: BLE001
                st.error(f"Preview failed: {e}")

    with act_c2:
        compile_clicked = st.button("💾 Compile & Save Strategy", type="primary", width="stretch")

    preview_res = st.session_state.get("_quick_preview_res")
    if preview_res:
        st.caption(f"⚡ **Quick Preview Dataset:** {preview_res['dataset_label']}")
        pm1, pm2, pm3, pm4 = st.columns(4)
        pm1.metric("# Trades", f"{preview_res['trades']}")
        pm2.metric("Return %", f"{preview_res['return_pct']:.2f}%")
        pm3.metric("Win Rate %", f"{preview_res['win_rate_pct']:.1f}%")
        pm4.metric("Max Drawdown %", f"{preview_res['max_dd_pct']:.2f}%")

    if compile_clicked:
        try:
            validate_preset(export_dict)
            code = build_strategy_code(cfg)
            st.session_state["custom_strategy_code"] = code
            ind_names = [x["name"] for x in st.session_state.get("indicators", [])]
            st.session_state["strategy_label"] = " + ".join(ind_names) if ind_names else "Custom"
            st.success("Strategy Compiled successfully! Go to Backtester to run it.")
            with st.expander("Show Generated Python Code (Read Only)"):
                st.code(code, language="python")
        except (PresetValidationError, FormulaValidationError, ValueError) as e:
            st.error(f"Compilation failed: {e}")
