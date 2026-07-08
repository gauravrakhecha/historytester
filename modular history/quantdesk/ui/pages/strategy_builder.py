"""
No-Code Strategy Builder page (upstox_app.py:1437-2266).

Same UI/behaviour as the monolith, but: preset I/O comes from ``quantdesk.strategy.presets``,
the indicator add-form is driven by ``INDICATOR_PARAM_SPECS``, code is produced by
``quantdesk.strategy.codegen.build_strategy_code``, and the variable lists come from
``models.selectable_columns`` — so EVERY multi-output indicator (Supertrend/Stochastic/
Keltner/Donchian, not just MACD/Bollinger) is now selectable in rules (bug fix).
"""
from __future__ import annotations

import json

import streamlit as st

from quantdesk.strategy import presets
from quantdesk.strategy.codegen import build_strategy_code
from quantdesk.strategy.indicators import INDICATOR_PARAM_SPECS, INDICATOR_TYPES
from quantdesk.strategy.validation import validate_preset
from quantdesk.strategy.models import (
    OPERATORS,
    STRAT_DIRECTIONS,
    StrategyConfig,
    selectable_columns,
)

_STATE_KEYS = ["indicators", "buy_rules", "sell_rules", "no_trade_rules", "range_zones"]
ZONE_ACTIONS = ["Normal Trading", "Block New Entries", "Tighten Trailing Stop", "Close Position & Block"]


def _restore_from_preset(data: dict):
    # v7.1 security: validate a preset (uploaded JSON or a server file) before it reaches
    # session_state -> codegen -> exec(). Raises PresetValidationError, caught by the callers.
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
    # position sizing (preserved across save/load — was previously lost)
    st.session_state["pos_size_type"] = data.get("pos_size_type", "Fixed Value per Trade (₹) (Default)")
    st.session_state["fixed_pos_value"] = data.get("fixed_pos_value", 50000.0)
    st.session_state["lot_size"] = data.get("lot_size", 1)


def _current_export() -> dict:
    return {
        "indicators": st.session_state["indicators"],
        "buy_rules": st.session_state["buy_rules"],
        "sell_rules": st.session_state["sell_rules"],
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
        },
        "pos_size_type": st.session_state.get("pos_size_type", "Fixed Value per Trade (₹) (Default)"),
        "fixed_pos_value": st.session_state.get("fixed_pos_value", 50000.0),
        "lot_size": st.session_state.get("lot_size", 1),
    }


def render(ctx):
    st.header("🛠️ No-Code Strategy Builder")
    st.markdown("Build your strategy visually using custom indicators and dynamic market state rules.")

    for k in _STATE_KEYS:
        st.session_state.setdefault(k, [])

    # --- presets -----------------------------------------------------------
    st.subheader("💾 Server Strategy Presets")
    available_strats = presets.list_strategies()
    c_load, c_save = st.columns(2)
    with c_load:
        st.markdown("### Load Existing Strategy")
        if available_strats:
            selected_strat = st.selectbox("Select Strategy Preset", available_strats)
            if st.button("Load Strategy", type="primary", use_container_width=True):
                try:
                    _restore_from_preset(presets.load_strategy_raw(selected_strat))
                    st.success(f"Loaded {selected_strat} successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to load: {e}")
        else:
            st.info("No saved strategies found on server.")

        st.markdown("---")
        st.markdown("### Import Local File")
        uploaded_file = st.file_uploader("Upload .json", type=["json"], label_visibility="collapsed")
        if uploaded_file is not None and st.button("Import Uploaded Strategy", use_container_width=True):
            try:
                _restore_from_preset(json.load(uploaded_file))
                st.success("Strategy loaded successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to load: {e}")

    with c_save:
        st.markdown("### Save Current Strategy to Server")
        save_name = st.text_input("Strategy Name", value="MyStrategy")
        if st.button("Save Strategy Permanently", use_container_width=True):
            try:
                fn = presets.save_strategy(save_name, _current_export())
                st.success(f"Saved as {fn} on server!")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save: {e}")

        if available_strats:
            st.markdown("---")
            del_strat = st.selectbox("Delete a Preset", available_strats, key="del_strat")
            if st.button("🗑️ Delete Strategy", use_container_width=True):
                try:
                    presets.delete_strategy(del_strat)
                    st.success(f"Deleted {del_strat}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to delete: {e}")

        st.markdown("---")
        st.markdown("### Download Local Copy")
        st.download_button(
            label="📥 Download .json file",
            data=json.dumps(_current_export(), indent=4),
            file_name="my_strategy.json",
            mime="application/json",
            use_container_width=True,
        )

    st.markdown("---")

    # --- Step 1: indicators ------------------------------------------------
    st.subheader("Step 1: Define Custom Indicators")
    for i, ind in enumerate(st.session_state["indicators"]):
        col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
        col1.write(f"**{ind['name']}**")
        col2.write(f"Type: {ind['type']}")
        col3.write(f"Source: {ind.get('source', 'Close')}, Params: {ind.get('params', {})}")
        if col4.button("❌ Remove", key=f"rm_ind_{i}"):
            new_inds = st.session_state["indicators"].copy()
            new_inds.pop(i)
            st.session_state["indicators"] = new_inds
            st.rerun()

    # indicator library presets
    with st.expander("📚 Indicator Library (Saved Presets)", expanded=False):
        lib_c1, lib_c2 = st.columns(2)
        ind_presets = presets.list_indicator_presets()
        with lib_c1:
            st.markdown("**Load a saved indicator preset:**")
            if ind_presets:
                sel_preset = st.selectbox("Select Preset", ind_presets, key="sel_ind_preset")
                if st.button("📥 Add Preset to Strategy", use_container_width=True):
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
            if st.button("💾 Save as Indicator Preset", use_container_width=True):
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
                if st.button("🗑️ Delete Preset", key="del_ind_btn", use_container_width=True):
                    presets.delete_indicator_preset(del_preset)
                    st.success(f"Deleted '{del_preset}'")
                    st.rerun()

    available_vars = selectable_columns(st.session_state["indicators"])

    with st.expander("➕ Add New Indicator", expanded=True):
        ind_type = st.selectbox("Indicator Type", INDICATOR_TYPES)
        ind_name = st.text_input("Indicator Name", value=f"Ind_{len(st.session_state['indicators'])+1}")
        source = st.selectbox(
            "Source Data", available_vars,
            help="Select any OHLCV column OR a previously defined indicator (chaining).",
        )
        params = {}
        for key, kind, default, extra in INDICATOR_PARAM_SPECS.get(ind_type, []):
            label = key.replace("_", " ").title()
            if kind == "int":
                params[key] = int(
                    st.number_input(label, value=int(default), min_value=int(extra.get("min", 1)), step=1, help=extra.get("help"))
                )
            else:
                params[key] = float(
                    st.number_input(
                        label, value=float(default), min_value=float(extra.get("min", 0.1)),
                        format=extra.get("format"), help=extra.get("help"),
                    )
                )
        if ind_type == "Angle of Indicator":
            st.info(
                f"Computes: arctan( ({source}[now] - {source}[{params.get('period', 5)} bars ago]) "
                f"/ ATR({params.get('atr_period', 14)}) ) × (180/π)"
            )

        if st.button("Save Indicator"):
            if any(x["name"] == ind_name for x in st.session_state["indicators"]):
                st.error("Name already exists!")
            else:
                new_inds = st.session_state["indicators"].copy()
                new_inds.append({"name": ind_name, "type": ind_type, "source": source, "params": params})
                st.session_state["indicators"] = new_inds
                st.rerun()

    # --- Step 2: entry/exit logic -----------------------------------------
    st.subheader("Step 2: Market States (Entry & Exit Logic)")
    variables = available_vars.copy()

    def render_rules(rule_list, list_name):
        for i, rule in enumerate(rule_list):
            c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 1])
            logic = c1.selectbox("Logic", ["IF", "AND", "OR"] if i > 0 else ["IF"], key=f"{list_name}_log_{i}")
            var1 = c2.selectbox("Var 1", variables, index=variables.index(rule["var1"]) if rule["var1"] in variables else 0, key=f"{list_name}_v1_{i}")
            op = c3.selectbox("Operator", OPERATORS, index=OPERATORS.index(rule["operator"]) if rule["operator"] in OPERATORS else 0, key=f"{list_name}_op_{i}")
            is_num = c4.checkbox("Is Number?", value=rule.get("is_num", False), key=f"{list_name}_isnum_{i}")
            if is_num:
                try:
                    default_val = float(rule.get("var2", 0))
                except (ValueError, TypeError):
                    default_val = 0.0
                var2 = c4.number_input("Value", value=default_val, key=f"{list_name}_v2num_{i}")
            else:
                var2 = c4.selectbox("Var 2", variables, index=variables.index(rule["var2"]) if rule["var2"] in variables else 0, key=f"{list_name}_v2var_{i}")
            if c5.button("❌", key=f"{list_name}_rm_{i}"):
                new_list = st.session_state[list_name].copy()
                new_list.pop(i)
                st.session_state[list_name] = new_list
                st.rerun()
            rule.update({"logic": logic, "var1": var1, "operator": op, "var2": var2, "is_num": is_num})

        if st.button(f"➕ Add Condition to {list_name}", key=f"add_{list_name}"):
            new_list = st.session_state[list_name].copy()
            new_list.append({"logic": "AND" if len(new_list) > 0 else "IF", "var1": "Close", "operator": ">", "var2": "Close", "is_num": False})
            st.session_state[list_name] = new_list
            st.rerun()

    st.markdown("#### BUY Conditions")
    render_rules(st.session_state["buy_rules"], "buy_rules")
    st.markdown("#### SELL Conditions")
    render_rules(st.session_state["sell_rules"], "sell_rules")

    # --- Step 2.5: regime filter ------------------------------------------
    st.subheader("Step 2.5: Regime Filter (Optional)")
    st.markdown(
        "Define **No-Trade Zone** conditions. When ALL are true, **no new trades are entered**. "
        "Existing positions exit only via trailing stop or sell rules."
    )
    st.markdown("#### 🚫 No-Trade Zone Conditions")
    render_rules(st.session_state["no_trade_rules"], "no_trade_rules")

    # --- Step 2.75: range zones -------------------------------------------
    st.subheader("Step 2.75: Advanced Range Management (Optional)")
    st.markdown("Define **indicator value zones** with specific trading behaviours (block entries, tighten stop, etc.).")
    for i, zone in enumerate(st.session_state["range_zones"]):
        zc1, zc2, zc3, zc4, zc5, zc6, zc7 = st.columns([2, 1.5, 1.5, 2, 2, 2, 0.8])
        zone_indicator_opts = available_vars.copy()
        cur_idx = zone_indicator_opts.index(zone["indicator"]) if zone["indicator"] in zone_indicator_opts else 0
        zone["indicator"] = zc1.selectbox("Indicator", zone_indicator_opts, index=cur_idx, key=f"rz_ind_{i}")
        zone["min_val"] = zc2.number_input("Min", value=float(zone["min_val"]), step=0.01, format="%.2f", key=f"rz_min_{i}")
        zone["max_val"] = zc3.number_input("Max", value=float(zone["max_val"]), step=0.01, format="%.2f", key=f"rz_max_{i}")
        zone["label"] = zc4.text_input("Label", value=zone.get("label", ""), key=f"rz_lbl_{i}")
        act_idx = ZONE_ACTIONS.index(zone["action"]) if zone["action"] in ZONE_ACTIONS else 0
        zone["action"] = zc5.selectbox("Action", ZONE_ACTIONS, index=act_idx, key=f"rz_act_{i}")
        if zone["action"] == "Tighten Trailing Stop":
            zone["tight_pct"] = zc6.number_input("Tight SL %", value=float(zone.get("tight_pct", 1.0)), min_value=0.1, step=0.1, key=f"rz_tpct_{i}")
        else:
            zc6.write("")
            zone["tight_pct"] = 0
        if zc7.button("❌", key=f"rz_rm_{i}"):
            new_rz = st.session_state["range_zones"].copy()
            new_rz.pop(i)
            st.session_state["range_zones"] = new_rz
            st.rerun()

    if st.button("➕ Add Range Zone", key="add_range_zone"):
        new_rz = st.session_state["range_zones"].copy()
        new_rz.append({"indicator": available_vars[0] if available_vars else "Close", "min_val": -10.0, "max_val": 10.0, "label": "Zone", "action": "Normal Trading", "tight_pct": 1.0})
        st.session_state["range_zones"] = new_rz
        st.rerun()

    # --- Step 3: risk management ------------------------------------------
    st.subheader("Step 3: Risk Management (Optional)")
    strat_dir = st.radio("Strategy Direction", STRAT_DIRECTIONS, horizontal=True)
    st.session_state["strat_dir"] = strat_dir

    pos_size_type = st.radio(
        "Position Sizing",
        ["Fixed Value per Trade (₹) (Default)", "Compound 100% Equity", "Risk % of Equity per Trade"],
        horizontal=True,
        help="Fixed Value caps capital per trade. 100% Equity compounds your whole capital each trade. "
             "Risk % sizes each trade so a stop-out loses a fixed % of equity (professional sizing — needs a Stop Loss).",
    )
    fixed_pos_value = 0.0
    risk_pct = st.session_state.get("risk_pct", 1.0)
    if pos_size_type == "Fixed Value per Trade (₹) (Default)":
        fixed_pos_value = st.number_input("Amount per Trade (₹)", value=st.session_state.get("fixed_pos_value", 50000.0), min_value=100.0, step=1000.0)
    elif pos_size_type == "Risk % of Equity per Trade":
        risk_pct = st.number_input(
            "Risk % of Equity per Trade", value=float(risk_pct), min_value=0.1, max_value=50.0, step=0.1,
            help="Each trade is sized so hitting the stop loses ~this % of equity. Requires a Static % Stop Loss (set the Exit mode below).",
        )
        if st.session_state.get("rm_mode") != "Static % Stop Loss / Take Profit":
            st.caption("⚠️ Risk-% sizing needs a stop distance — set **Exit / Risk Management Mode → Static % Stop Loss / Take Profit** below (otherwise it falls back to a fixed ₹ size).")
    lot_size = st.number_input("Instrument Lot Size (Use 1 for Equity)", value=st.session_state.get("lot_size", 1), min_value=1, step=1, help="Options and Futures trade in fixed lot multiples.")

    st.session_state["pos_size_type"] = pos_size_type
    st.session_state["fixed_pos_value"] = fixed_pos_value
    st.session_state["risk_pct"] = risk_pct
    st.session_state["lot_size"] = lot_size

    rm_mode = st.radio(
        "Exit / Risk Management Mode",
        ["None (Use Sell Rules Only)", "Static % Stop Loss / Take Profit", "ATR Stop Loss (Volatility)",
         "Trailing Stop Loss (%)", "Indicator-Based Exit (Advanced)"],
    )
    sl_pct = tp_pct = trail_pct = 0.0
    atr_period = int(st.session_state.get("atr_period", 14))
    atr_stop_mult = float(st.session_state.get("atr_stop_mult", 2.0))
    if rm_mode == "Static % Stop Loss / Take Profit":
        c1, c2 = st.columns(2)
        sl_pct = c1.number_input("Stop Loss %", value=st.session_state.get("sl_pct", 2.0), min_value=0.0, step=0.1)
        tp_pct = c2.number_input("Take Profit %", value=st.session_state.get("tp_pct", 5.0), min_value=0.0, step=0.1)
        st.info("SL/TP are fixed at entry price. Sell Rules ALSO trigger exits independently.")
    elif rm_mode == "ATR Stop Loss (Volatility)":
        c1, c2, c3 = st.columns(3)
        atr_period = c1.number_input("ATR Period", value=atr_period, min_value=1, step=1)
        atr_stop_mult = c2.number_input("ATR Stop ×", value=atr_stop_mult, min_value=0.1, step=0.1, help="Stop distance = this × ATR.")
        tp_pct = c3.number_input("Take Profit % (0 = none)", value=st.session_state.get("tp_pct", 0.0), min_value=0.0, step=0.1)
        st.info("Volatility-adaptive stop: the stop sits mult×ATR from entry (wider in volatile markets). "
                "Pairs naturally with **Risk % of Equity** sizing — size then adapts to volatility.")
    elif rm_mode == "Trailing Stop Loss (%)":
        trail_pct = st.number_input("Trailing Stop %", value=st.session_state.get("trail_pct", 2.0), min_value=0.1, step=0.1)
        st.info("The stop follows the highest price since entry. Sell Rules ALSO trigger exits independently.")
    elif rm_mode == "Indicator-Based Exit (Advanced)":
        st.info("Define exit conditions in the **SELL Conditions** above. They are the only exit trigger.")

    st.session_state["rm_mode"] = rm_mode
    st.session_state["sl_pct"] = sl_pct
    st.session_state["tp_pct"] = tp_pct
    st.session_state["trail_pct"] = trail_pct
    st.session_state["atr_period"] = atr_period
    st.session_state["atr_stop_mult"] = atr_stop_mult

    cbk1, cbk2 = st.columns(2)
    max_dd_halt_pct = cbk1.number_input(
        "🛑 Max-Drawdown Circuit Breaker % (0 = off)",
        value=float(st.session_state.get("max_dd_halt_pct", 0.0)), min_value=0.0, max_value=100.0, step=1.0,
        help="Flatten any open position and stop taking new trades once equity falls this far below its running peak.",
    )
    max_bars_in_trade = cbk2.number_input(
        "⏱️ Time Stop — max bars per trade (0 = off)",
        value=int(st.session_state.get("max_bars_in_trade", 0)), min_value=0, step=1,
        help="Close any trade that is still open after this many bars.",
    )
    st.session_state["max_dd_halt_pct"] = max_dd_halt_pct
    st.session_state["max_bars_in_trade"] = max_bars_in_trade

    # --- Compile -----------------------------------------------------------
    st.subheader("Compile Strategy")
    if st.button("💾 Compile & Save Strategy"):
        cfg = StrategyConfig.from_dict(_current_export())
        cfg.pos_size_type = pos_size_type
        cfg.fixed_pos_value = fixed_pos_value
        cfg.lot_size = lot_size
        code = build_strategy_code(cfg)
        st.session_state["custom_strategy_code"] = code
        ind_names = [x["name"] for x in st.session_state.get("indicators", [])]
        st.session_state["strategy_label"] = " + ".join(ind_names) if ind_names else "Custom"
        st.success("Strategy Compiled successfully! Go to Backtester to run it.")
        with st.expander("Show Generated Python Code (Read Only)"):
            st.code(code, language="python")
