"""
Chart Backtester (Terminal) page — TradingView-style offline chart workspace.

Combines dataset & strategy selection (Active Builder strategy or any of the 5 Starter
Templates), in-memory parameter/risk tweaking, single-click backtest execution, linked KPIs,
an interactive offline OpenAlgo Pro Trading Terminal (Replay, Trade Focus, Equity/DD pane),
and a clickable Trade Blotter with CSV & India Tax Breakdown exports.
"""
from __future__ import annotations

import copy
import os
import traceback
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from quantdesk.backtest import engine, metrics
from quantdesk.config import QUALITY_BLOCK_THRESHOLD, QUALITY_WARN_THRESHOLD
from quantdesk.core import settings_repo
from quantdesk.core.db import get_download_history
from quantdesk.costs import india
from quantdesk.data.loader import load_indexed
from quantdesk.data.quality import validate as validate_quality
from quantdesk.strategy.codegen import build_strategy_code
from quantdesk.strategy.indicators import INDICATOR_PARAM_SPECS, FormulaValidationError
from quantdesk.strategy.models import (
    OHLCV,
    RM_MODES,
    STRAT_DIRECTIONS,
    RiskManagement,
    StrategyConfig,
    classify_columns,
    safe_name,
    selectable_columns,
)
from quantdesk.strategy.templates import STARTER_TEMPLATES, TEMPLATE_DESCRIPTIONS, get_template
from quantdesk.strategy.validation import PresetValidationError, validate_preset
from quantdesk.viz.charts import render_openalgo_terminal


def _terminal_chart_height(indicators: List[Dict[str, Any]] | None) -> int:
    """Compute iframe height accommodating overlays, stacked indicator panels, and Equity/DD pane."""
    if not indicators:
        return 780
    panels = {
        info["group"]
        for info in classify_columns(indicators).values()
        if info.get("placement") == "panel"
    }
    return min(1140, 780 + 120 * len(panels))


def _builder_config_from_session() -> StrategyConfig:
    """Snapshot the active Strategy Builder state into a StrategyConfig without mutating it."""
    rm = RiskManagement(
        mode=st.session_state.get("rm_mode", "None (Use Sell Rules Only)"),
        sl_pct=float(st.session_state.get("sl_pct", 2.0)),
        tp_pct=float(st.session_state.get("tp_pct", 5.0)),
        trail_pct=float(st.session_state.get("trail_pct", 2.0)),
        strat_dir=st.session_state.get("strat_dir", "Long Only"),
        risk_pct=float(st.session_state.get("risk_pct", 1.0)),
        max_dd_halt_pct=float(st.session_state.get("max_dd_halt_pct", 0.0)),
        atr_period=int(st.session_state.get("atr_period", 14)),
        atr_stop_mult=float(st.session_state.get("atr_stop_mult", 2.0)),
        max_bars_in_trade=int(st.session_state.get("max_bars_in_trade", 0)),
        breakeven_trigger_pct=float(st.session_state.get("breakeven_trigger_pct", 0.0)),
        partial_exit_pct=float(st.session_state.get("partial_exit_pct", 0.0)),
        partial_trigger_pct=float(st.session_state.get("partial_trigger_pct", 0.0)),
        htf_bias=bool(st.session_state.get("htf_bias", False)),
        htf_minutes=int(st.session_state.get("htf_minutes", 60)),
        htf_ema_period=int(st.session_state.get("htf_ema_period", 50)),
        intraday_square_off=bool(st.session_state.get("intraday_square_off", False)),
        entry_time_filter=bool(st.session_state.get("entry_time_filter", False)),
        entry_start_time=str(st.session_state.get("entry_start_time", "09:30")),
        entry_end_time=str(st.session_state.get("entry_end_time", "14:45")),
    )
    return StrategyConfig(
        indicators=copy.deepcopy(st.session_state.get("indicators", [])),
        buy_rules=copy.deepcopy(st.session_state.get("buy_rules", [])),
        sell_rules=copy.deepcopy(st.session_state.get("sell_rules", [])),
        no_trade_rules=copy.deepcopy(st.session_state.get("no_trade_rules", [])),
        range_zones=copy.deepcopy(st.session_state.get("range_zones", [])),
        risk_management=rm,
        pos_size_type=st.session_state.get("pos_size_type", "Fixed Value per Trade (₹) (Default)"),
        fixed_pos_value=float(st.session_state.get("fixed_pos_value", 50000.0)),
        lot_size=int(st.session_state.get("lot_size", 1)),
    )


def _apply_to_builder_session(cfg: StrategyConfig, code: str, label: str) -> None:
    """Explicitly copy tweaked configuration to Strategy Builder session state when requested."""
    data = cfg.to_dict()
    rm = data.get("risk_management", {})
    st.session_state["indicators"] = data.get("indicators", [])
    st.session_state["buy_rules"] = data.get("buy_rules", [])
    st.session_state["sell_rules"] = data.get("sell_rules", [])
    st.session_state["no_trade_rules"] = data.get("no_trade_rules", [])
    st.session_state["range_zones"] = data.get("range_zones", [])
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
    st.session_state["custom_strategy_code"] = code
    st.session_state["strategy_label"] = label


def _param_spec_map(ind_type: str) -> Dict[str, tuple[str, Dict[str, Any]]]:
    """Map parameter name -> (kind, extra_meta) from INDICATOR_PARAM_SPECS."""
    out: Dict[str, tuple[str, Dict[str, Any]]] = {}
    for item in INDICATOR_PARAM_SPECS.get(ind_type, []):
        if len(item) >= 4:
            key, kind, _default, extra = item[0], item[1], item[2], item[3] or {}
            out[key] = (kind, extra)
    return out


def _render_quick_tweaks(
    base_cfg: StrategyConfig,
    source_key: str,
    strategy_source: str,
    base_label: str,
) -> tuple[StrategyConfig, str, str | None]:
    """Render the Quick Parameter Tweak & Risk Overrides expander.

    Returns ``(tweaked_cfg, charges_segment, compiled_code_or_none)`` without mutating Builder state.
    """
    tweaked = StrategyConfig.from_dict(copy.deepcopy(base_cfg.to_dict()))
    segment = st.session_state.get("ct_segment", india.SEGMENTS[0])

    with st.expander("⚙️ Quick Parameter Tweak & Risk Overrides", expanded=False):
        # 1. Numeric indicator parameters
        numeric_fields: list[tuple[int, str, Any, str, Dict[str, Any]]] = []
        for idx, ind in enumerate(tweaked.indicators):
            spec_map = _param_spec_map(ind.get("type", ""))
            for p_key, p_val in (ind.get("params") or {}).items():
                if isinstance(p_val, bool) or not isinstance(p_val, (int, float)):
                    continue
                if p_key == "use_num":
                    continue
                kind, extra = spec_map.get(
                    p_key, ("int" if isinstance(p_val, int) else "float", {})
                )
                if kind not in ("int", "float"):
                    kind = "int" if isinstance(p_val, int) else "float"
                numeric_fields.append((idx, p_key, p_val, kind, extra))

        if numeric_fields:
            st.markdown("**Indicator Parameters**")
            cols = st.columns(min(4, len(numeric_fields)))
            for i, (ind_idx, p_key, p_val, kind, extra) in enumerate(numeric_fields[:8]):
                ind = tweaked.indicators[ind_idx]
                col = cols[i % len(cols)]
                w_key = f"ct_ind_{source_key}_{ind_idx}_{safe_name(p_key)}"
                label = f"{ind['name']} · {p_key}"
                if kind == "int":
                    min_v = int(extra.get("min", 1 if "period" in p_key else 0))
                    max_v = int(extra["max"]) if "max" in extra else None
                    new_v = col.number_input(
                        label,
                        value=int(p_val),
                        min_value=min_v,
                        max_value=max_v,
                        step=1,
                        key=w_key,
                    )
                    tweaked.indicators[ind_idx]["params"][p_key] = int(new_v)
                else:
                    min_v = float(extra.get("min", 0.0))
                    new_v = col.number_input(
                        label,
                        value=float(p_val),
                        min_value=min_v,
                        step=0.1,
                        key=w_key,
                    )
                    tweaked.indicators[ind_idx]["params"][p_key] = float(new_v)

        # 2. Numeric rule thresholds
        rule_fields: list[tuple[str, int, dict]] = []
        for idx, r in enumerate(tweaked.buy_rules):
            if r.get("is_num") and isinstance(r.get("var2"), (int, float)):
                rule_fields.append(("buy", idx, r))
        for idx, r in enumerate(tweaked.sell_rules):
            if r.get("is_num") and isinstance(r.get("var2"), (int, float)):
                rule_fields.append(("sell", idx, r))

        if rule_fields:
            st.markdown("**Rule Thresholds**")
            rcols = st.columns(min(4, len(rule_fields)))
            for i, (side, r_idx, r) in enumerate(rule_fields[:8]):
                col = rcols[i % len(rcols)]
                tag = "Buy" if side == "buy" else "Sell"
                lbl = f"{tag} #{r_idx + 1}: {r.get('var1', '')} {r.get('operator', '')}"
                w_key = f"ct_rule_{source_key}_{side}_{r_idx}"
                new_t = col.number_input(lbl, value=float(r["var2"]), step=1.0, key=w_key)
                target_list = tweaked.buy_rules if side == "buy" else tweaked.sell_rules
                target_list[r_idx]["var2"] = float(new_t)

        # 3. Risk & Segment overrides
        st.markdown("**Risk & Charges Overrides**")
        rm = tweaked.risk_management
        rc1, rc2, rc3 = st.columns(3)
        dir_idx = STRAT_DIRECTIONS.index(rm.strat_dir) if rm.strat_dir in STRAT_DIRECTIONS else 0
        rm.strat_dir = rc1.selectbox(
            "Direction",
            STRAT_DIRECTIONS,
            index=dir_idx,
            key=f"ct_dir_{source_key}",
        )
        mode_idx = RM_MODES.index(rm.mode) if rm.mode in RM_MODES else 0
        rm.mode = rc2.selectbox(
            "Mode",
            RM_MODES,
            index=mode_idx,
            key=f"ct_rm_mode_{source_key}",
        )
        segment = rc3.selectbox(
            "Charges Segment",
            india.SEGMENTS,
            index=0,
            key="ct_segment",
            help="Real India statutory charges applied inside the backtest simulation.",
        )

        rp1, rp2, rp3 = st.columns(3)
        sl_enabled = rm.mode == "Static % Stop Loss / Take Profit"
        tp_enabled = rm.mode in ("Static % Stop Loss / Take Profit", "ATR Stop Loss (Volatility)")
        trail_enabled = rm.mode == "Trailing Stop Loss (%)"

        rm.sl_pct = float(
            rp1.number_input(
                "SL %",
                value=float(rm.sl_pct),
                min_value=0.0,
                max_value=100.0,
                step=0.25,
                disabled=not sl_enabled,
                key=f"ct_sl_{source_key}",
            )
        )
        rm.tp_pct = float(
            rp2.number_input(
                "TP %",
                value=float(rm.tp_pct),
                min_value=0.0,
                max_value=500.0,
                step=0.5,
                disabled=not tp_enabled,
                key=f"ct_tp_{source_key}",
            )
        )
        rm.trail_pct = float(
            rp3.number_input(
                "Trailing %",
                value=float(rm.trail_pct),
                min_value=0.0,
                max_value=100.0,
                step=0.25,
                disabled=not trail_enabled,
                key=f"ct_trail_{source_key}",
            )
        )

        # Compile in-memory
        compiled_code: str | None = None
        compile_err: str | None = None
        if (
            strategy_source == "Active Strategy (from Builder)"
            and not tweaked.indicators
            and not tweaked.buy_rules
            and st.session_state.get("custom_strategy_code")
        ):
            compiled_code = st.session_state["custom_strategy_code"]
        else:
            try:
                validate_preset(tweaked.to_dict())
                compiled_code = build_strategy_code(tweaked)
            except (PresetValidationError, FormulaValidationError, ValueError) as exc:
                compile_err = str(exc)

        if compile_err:
            st.error(f"Strategy configuration error: {compile_err}")
        elif compiled_code:
            if st.button(
                "💾 Copy Tweaked Strategy to Builder",
                width="stretch",
                key=f"ct_sync_builder_{source_key}",
                help="Optional: push these tweaked parameters into Strategy Builder's saved state.",
            ):
                _apply_to_builder_session(tweaked, compiled_code, base_label)
                st.success("Copied tweaked strategy to Strategy Builder.")

    return tweaked, segment, compiled_code


def _extract_out_df(df: pd.DataFrame, trades: pd.DataFrame, strat_instance: Any, indicators: List[dict]) -> pd.DataFrame:
    """Build OHLCV + indicator columns + Buy/Sell signal flags for chart rendering."""
    out_df = df.copy()
    out_df["Buy_Signal"] = 0
    out_df["Sell_Signal"] = 0
    if trades is not None and not trades.empty:
        buy_col = out_df.columns.get_loc("Buy_Signal")
        sell_col = out_df.columns.get_loc("Sell_Signal")
        n_rows = len(out_df)
        for _, t in trades.iterrows():
            eb = int(t["EntryBar"])
            xb = int(t["ExitBar"])
            if 0 <= eb < n_rows:
                out_df.iloc[eb, buy_col] = 1
            if 0 <= xb < n_rows:
                out_df.iloc[xb, sell_col] = 1

    for var_name in [c for c in selectable_columns(indicators or []) if c not in OHLCV]:
        attr = safe_name(var_name)
        if hasattr(strat_instance, attr):
            arr = getattr(strat_instance, attr)
            if hasattr(arr, "__len__") and len(arr) == len(out_df):
                out_df[var_name] = list(arr)
    return out_df


def _prepare_sorted_trades(trades: pd.DataFrame, strat_instance: Any, segment: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Enrich trades with MAE/MFE/R and India charges, sorted cleanly to match terminal 1..N IDs."""
    if trades is None or trades.empty:
        return pd.DataFrame(), pd.DataFrame()

    sorted_trades = trades.copy()
    sort_cols = [c for c in ("EntryBar", "ExitBar") if c in sorted_trades.columns]
    if sort_cols:
        sorted_trades = sorted_trades.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)

    sorted_trades = metrics.enrich_trade_analytics(sorted_trades, strat_instance)
    tax_df = metrics.tax_breakdown(sorted_trades, segment)
    if not tax_df.empty and len(tax_df) == len(sorted_trades):
        sorted_trades["TotalCharges"] = tax_df["TotalCharges"].to_numpy()
    return sorted_trades, tax_df


def _build_blotter_frame(trades: pd.DataFrame) -> pd.DataFrame:
    """Format an uncluttered Trade Blotter DataFrame with 1-based Trade # matching terminal IDs."""
    if trades is None or trades.empty:
        return pd.DataFrame()

    blotter = pd.DataFrame()
    blotter["Trade #"] = list(range(1, len(trades) + 1))
    if "Size" in trades.columns:
        blotter["Side"] = ["Short" if float(s) < 0 else "Long" for s in trades["Size"]]

    preferred = [
        "EntryTime",
        "ExitTime",
        "Size",
        "EntryPrice",
        "ExitPrice",
        "PnL",
        "ReturnPct",
        "MAE_pct",
        "MFE_pct",
        "R_multiple",
        "TotalCharges",
    ]
    for col in preferred:
        if col in trades.columns:
            if col == "ReturnPct":
                blotter["Return %"] = (trades[col].astype(float) * 100.0).round(2)
            else:
                blotter[col] = trades[col]

    for col in trades.columns:
        if col.startswith("Entry_") and col not in blotter.columns:
            blotter[col] = trades[col]

    for col in blotter.select_dtypes(include=["float"]).columns:
        if col != "Return %":
            blotter[col] = blotter[col].round(3)
    return blotter


def _get_blotter_selected_rows() -> list[int]:
    """Safely read current selected row indices from st.session_state['ct_trade_blotter']."""
    state = st.session_state.get("ct_trade_blotter")
    if not state:
        return []
    try:
        sel = state.get("selection", {}) if isinstance(state, dict) else getattr(state, "selection", {})
        rows = sel.get("rows", []) if isinstance(sel, dict) else getattr(sel, "rows", [])
        return [int(r) for r in rows] if rows else []
    except Exception:
        return []


def render(ctx) -> None:
    st.header("📊 Chart Backtester (Terminal)")
    st.caption(
        "Run any Starter Template or your active Strategy Builder setup directly on an interactive "
        "offline trading terminal with bar-by-bar replay, trade focus, and India charge accounting."
    )

    history = get_download_history()
    if history.empty:
        st.info(
            "No downloaded datasets found yet. Download or import market data from "
            "**Single Instrument Data**, **Bulk Data Downloader**, or **Local Data Converter** first, "
            "then return here to backtest directly on the chart."
        )
        return

    # --- 1. Top Control Strip ---
    options = history["file_path"].tolist()
    labels = [
        f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}"
        for _, row in history.iterrows()
    ]

    has_builder_strat = bool(st.session_state.get("custom_strategy_code"))
    source_options = (
        ["Active Strategy (from Builder)", "Starter Template"]
        if has_builder_strat
        else ["Starter Template"]
    )

    ctrl1, ctrl2 = st.columns([1.35, 1.0])
    selected_idx = ctrl1.selectbox(
        "Dataset",
        range(len(options)),
        format_func=lambda x: labels[x],
        key="ct_dataset_idx",
    )
    selected_file = options[selected_idx]
    selected_row = history.iloc[selected_idx]

    strategy_source = ctrl2.radio(
        "Strategy Source",
        source_options,
        index=0,
        horizontal=True,
        key="ct_strategy_source",
    )

    if strategy_source == "Starter Template":
        tpl_names = list(STARTER_TEMPLATES.keys())
        tpl_name = st.selectbox("Starter Template", tpl_names, key="ct_template_name")
        desc = TEMPLATE_DESCRIPTIONS.get(tpl_name, "")
        if desc:
            st.caption(desc)
        base_cfg = StrategyConfig.from_dict(get_template(tpl_name))
        base_label = tpl_name
        source_key = f"tpl_{safe_name(tpl_name)}"
    else:
        base_cfg = _builder_config_from_session()
        base_label = st.session_state.get("strategy_label", "Active Builder Strategy")
        source_key = "builder"

    tweaked_cfg, segment, compiled_code = _render_quick_tweaks(
        base_cfg, source_key, strategy_source, base_label
    )

    run_clicked = st.button("🚀 Run on Chart", type="primary", width="stretch", key="ct_run_btn")

    if run_clicked:
        if not os.path.exists(selected_file):
            st.error(f"Data file not found on disk: `{selected_file}`")
            return
        if not compiled_code:
            st.error("Strategy code could not be compiled. Check parameter overrides above.")
            return

        with st.spinner("Running strategy on Chart Terminal..."):
            try:
                df = load_indexed(selected_file)
                qreport = validate_quality(
                    df.reset_index() if df.index.name == "Timestamp" else df
                )
                qscore = qreport.get("quality_score", 100)
                if qscore < QUALITY_BLOCK_THRESHOLD:
                    engine._enforce_quality(df)  # raises ValueError with clear reason

                params = settings_repo.backtest_params()
                init_cash = float(params["initial_capital"])

                stats, _ = engine.run_single(
                    df,
                    compiled_code,
                    init_cash,
                    params["total_commission"],
                    params["margin"],
                    segment=segment,
                    slippage=params["slippage"],
                )

                strat_instance = stats["_strategy"]
                raw_trades = stats["_trades"].copy()
                indicators = copy.deepcopy(tweaked_cfg.indicators)
                if not raw_trades.empty:
                    variables = selectable_columns(indicators)
                    raw_trades = metrics.enrich_trades(raw_trades, strat_instance, variables)
                trades, tax_df = _prepare_sorted_trades(raw_trades, strat_instance, segment)

                out_df = _extract_out_df(df, trades, strat_instance, indicators)
                sm = metrics.extract_scalar_metrics(stats)
                ch = india.charges_for_trades(trades, segment)
                net_pnl = float(trades["PnL"].sum()) if (not trades.empty and "PnL" in trades.columns) else 0.0
                net_return_pct = (net_pnl / init_cash * 100.0) if init_cash > 0 else 0.0

                st.session_state["chart_terminal_result"] = {
                    "symbol": str(selected_row.symbol),
                    "interval": str(selected_row.interval),
                    "strategy_label": base_label,
                    "segment": segment,
                    "init_cash": init_cash,
                    "quality_score": qscore,
                    "quality_issues": qreport.get("issues", []),
                    "stats": stats,
                    "sm": sm,
                    "trades": trades,
                    "tax_df": tax_df,
                    "out_df": out_df,
                    "indicators": indicators,
                    "charges": ch,
                    "total_charges": float(ch.get("total", 0.0)),
                    "net_return_pct": round(net_return_pct, 2),
                }
                # Reset Trade Focus on fresh run
                st.session_state["ct_focus_select"] = None
                st.session_state["_ct_last_blotter_rows"] = []
            except ValueError as ve:
                st.error(f"⛔ Cannot run backtest: {ve}")
                return
            except Exception as exc:  # noqa: BLE001
                st.error(f"Error running Chart Terminal backtest: {exc}")
                st.code(traceback.format_exc(), language="text")
                return

    # --- 2. Chart Studio + Linked KPIs + Clickable Trade Blotter ---
    res = st.session_state.get("chart_terminal_result")
    if not res:
        st.info("Select a dataset and strategy above, then click **🚀 Run on Chart**.")
        return

    qscore = res.get("quality_score", 100)
    if qscore < QUALITY_WARN_THRESHOLD:
        issues = ", ".join(res.get("quality_issues") or []) or "n/a"
        st.warning(f"⚠️ Data quality {qscore}/100 (below {QUALITY_WARN_THRESHOLD}). Issues: {issues}.")

    st.caption(
        f"**Active Run:** {res['symbol']} ({res['interval']}) · "
        f"Strategy: **{res['strategy_label']}** · Segment: **{res['segment']}**"
    )

    sm = res["sm"]
    stats = res["stats"]
    sharpe_raw = stats.get("Sharpe Ratio")
    sharpe_disp = f"{float(sharpe_raw):.2f}" if sharpe_raw is not None and not pd.isna(sharpe_raw) else "N/A"

    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
    k1.metric("Return %", f"{sm['return_pct']:.2f}%")
    k2.metric("Max Drawdown %", f"{sm['max_drawdown_pct']:.2f}%")
    k3.metric("Sharpe Ratio", sharpe_disp)
    k4.metric("Win Rate %", f"{sm['win_rate_pct']:.1f}%")
    k5.metric("# Trades", f"{int(sm['num_trades'])}")
    k6.metric("Net Return %", f"{res['net_return_pct']:.2f}%")
    k7.metric("Total Charges ₹", f"₹{res['total_charges']:,.2f}")

    trades: pd.DataFrame = res["trades"]
    blotter_df = _build_blotter_frame(trades)

    # Sync Trade Blotter row click -> Trade Focus selectbox BEFORE instantiating selectbox
    current_blotter_rows = _get_blotter_selected_rows()
    last_blotter_rows = st.session_state.get("_ct_last_blotter_rows", [])
    if current_blotter_rows != last_blotter_rows:
        st.session_state["_ct_last_blotter_rows"] = current_blotter_rows
        if current_blotter_rows and not blotter_df.empty:
            row_idx = current_blotter_rows[0]
            if 0 <= row_idx < len(blotter_df):
                st.session_state["ct_focus_select"] = int(row_idx + 1)
        else:
            st.session_state["ct_focus_select"] = None

    trade_ids = list(range(1, len(blotter_df) + 1)) if not blotter_df.empty else []
    focus_options: list[int | None] = [None] + trade_ids

    def _fmt_focus(tid: int | None) -> str:
        if tid is None:
            return "All Bars"
        if not blotter_df.empty and 1 <= tid <= len(blotter_df):
            r = blotter_df.iloc[tid - 1]
            side = r.get("Side", "")
            ret = r.get("Return %", 0.0)
            pnl = r.get("PnL", 0.0)
            return f"Trade #{tid} ({side} · {ret:+.2f}% · ₹{pnl:,.0f})"
        return f"Trade #{tid}"

    if st.session_state.get("ct_focus_select") not in focus_options:
        st.session_state["ct_focus_select"] = None

    focus_trade_id = st.selectbox(
        "Trade Focus",
        options=focus_options,
        format_func=_fmt_focus,
        key="ct_focus_select",
        help="Jump the terminal chart directly to a trade (or select a row in the Trade Blotter below).",
    )

    chart_height = _terminal_chart_height(res["indicators"])
    try:
        terminal_html = render_openalgo_terminal(
            res["out_df"],
            stats=stats,
            trades=trades,
            indicators=res["indicators"],
            initial_capital=res["init_cash"],
            height=chart_height,
            initial_focus_trade=focus_trade_id,
        )
        components.html(terminal_html, height=chart_height, scrolling=False)
    except Exception as chart_exc:  # noqa: BLE001
        st.warning(f"Could not render Chart Terminal: {chart_exc}")

    st.subheader("Trade Blotter")
    if blotter_df.empty:
        st.info("No trades were executed during this backtest.")
        return

    st.caption("Click any row in the Trade Blotter to focus and zoom the chart onto that trade.")
    st.dataframe(
        blotter_df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="ct_trade_blotter",
    )

    dl1, dl2 = st.columns(2)
    dl1.download_button(
        label="📥 Trade Blotter (CSV)",
        data=trades.to_csv(index=False).encode("utf-8"),
        file_name="chart_terminal_trades.csv",
        mime="text/csv",
        width="stretch",
    )
    tax_df: pd.DataFrame = res.get("tax_df", pd.DataFrame())
    if tax_df is not None and not tax_df.empty:
        dl2.download_button(
            label="🧾 India Tax Breakdown (CSV)",
            data=tax_df.to_csv(index=False).encode("utf-8"),
            file_name="chart_terminal_tax_breakdown.csv",
            mime="text/csv",
            width="stretch",
            help="Per-trade STT / stamp / exchange / SEBI / brokerage / GST breakdown.",
        )
    else:
        dl2.caption("Tax breakdown CSV unavailable.")
