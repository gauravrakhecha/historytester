"""
quantdesk.strategy.codegen — build a backtesting.py Strategy class source from a StrategyConfig.

Faithful port of the original "Compile Strategy" block (upstox_app.py:1876-2257). It reads
from a :class:`quantdesk.strategy.models.StrategyConfig` instead of ``st.session_state`` and
delegates per-indicator emission to :func:`quantdesk.strategy.indicators.emit_indicator`. The
generated source is functionally identical to the monolith's, so backtest results match.
"""
from __future__ import annotations

from typing import Dict

from quantdesk.config import KELLY_MIN_TRADES
from quantdesk.strategy.indicators import emit_indicator
from quantdesk.strategy.models import StrategyConfig, safe_name


def _single_rule_cond(r: dict, idx: int, prefix: str, src_map: Dict[str, str]) -> str:
    """Emit the Python boolean expression for a single rule (with optional timing & negate)."""
    v1 = src_map.get(r["var1"], f"self.{r['var1']}")
    op = r["operator"]
    is_num = bool(r.get("is_num"))
    v2_ref = f"self.{prefix}_rule_{idx}_val" if is_num else src_map.get(r["var2"], f"self.{r['var2']}")

    def _pair(off_expr: str) -> str:
        """Condition at negative bar offset ``off_expr`` (e.g. '-1' or '-1 - _k')."""
        v2_cur = v2_ref if is_num else f"{v2_ref}[{off_expr}]"
        if op == "Crosses Above":
            prev_off = f"{off_expr} - 1" if off_expr != "-1" else "-2"
            v2_prev = v2_ref if is_num else f"{v2_ref}[{prev_off}]"
            return f"({v1}[{prev_off}] <= {v2_prev} and {v1}[{off_expr}] > {v2_cur})"
        if op == "Crosses Below":
            prev_off = f"{off_expr} - 1" if off_expr != "-1" else "-2"
            v2_prev = v2_ref if is_num else f"{v2_ref}[{prev_off}]"
            return f"({v1}[{prev_off}] >= {v2_prev} and {v1}[{off_expr}] < {v2_cur})"
        return f"({v1}[{off_expr}] {op} {v2_cur})"

    timing = r.get("timing", "now") or "now"
    n_bars = max(1, int(r.get("timing_bars", 1) or 1))
    is_cross = op in ("Crosses Above", "Crosses Below")

    if timing == "now":
        cond = _pair("-1")
    elif timing == "bars_ago":
        min_len = n_bars + (2 if is_cross else 1)
        cur_idx = -1 - n_bars
        prev_idx = -2 - n_bars
        v2_cur = v2_ref if is_num else f"{v2_ref}[{cur_idx}]"
        if op == "Crosses Above":
            v2_prev = v2_ref if is_num else f"{v2_ref}[{prev_idx}]"
            inner = f"({v1}[{prev_idx}] <= {v2_prev} and {v1}[{cur_idx}] > {v2_cur})"
        elif op == "Crosses Below":
            v2_prev = v2_ref if is_num else f"{v2_ref}[{prev_idx}]"
            inner = f"({v1}[{prev_idx}] >= {v2_prev} and {v1}[{cur_idx}] < {v2_cur})"
        else:
            inner = f"({v1}[{cur_idx}] {op} {v2_cur})"
        cond = f"(len(self.data.Close) >= {min_len} and {inner})"
    elif timing == "within_last":
        bound = f"min({n_bars}, max(0, len(self.data.Close) - 1))" if is_cross else f"min({n_bars}, len(self.data.Close))"
        cond = f"any({_pair('-1 - _k')} for _k in range({bound}))"
    elif timing == "consecutive":
        min_len = n_bars + (1 if is_cross else 0)
        cond = f"(len(self.data.Close) >= {min_len} and all({_pair('-1 - _k')} for _k in range({n_bars})))"
    else:
        cond = _pair("-1")

    if r.get("negate"):
        cond = f"(not {cond})" if cond.startswith("(") else f"(not ({cond}))"
    return cond


def _gen_logic(rules: list, prefix: str, src_map: Dict[str, str]) -> str:
    """Emit boolean rule expression supporting parenthesized ``group``s, ``negate``, and ``timing``.

    When all rules share a single group and have default ``negate=False`` and ``timing='now'``,
    the emitted expression is identical to the legacy ``gen_logic`` output (parity-safe).
    """
    if not rules:
        return "False"

    # Partition contiguous rules sharing the same ``group`` id into parenthesized groups.
    groups: list[tuple[str, list[str]]] = []
    cur_gid = None
    for idx, r in enumerate(rules):
        gid = int(r.get("group", 0) or 0)
        cond = _single_rule_cond(r, idx, prefix, src_map)
        if cur_gid is None or gid != cur_gid:
            joiner = "and" if r.get("logic", "IF") == "AND" else "or"
            groups.append((joiner, [cond]))
            cur_gid = gid
        else:
            logic = "and" if r.get("logic", "AND") == "AND" else "or"
            groups[-1][1].append(f" {logic} {cond}")

    if len(groups) == 1:
        return "".join(groups[0][1])

    out_parts = []
    for g_idx, (joiner, items) in enumerate(groups):
        grp_expr = f"({''.join(items)})"
        if g_idx == 0:
            out_parts.append(grp_expr)
        else:
            out_parts.append(f" {joiner} {grp_expr}")
    return "".join(out_parts)


def build_strategy_code(cfg: StrategyConfig) -> str:
    """Generate the ``VisualStrategy(Strategy)`` source string for ``cfg``."""
    indicators = cfg.indicators or []
    buy_rules = cfg.buy_rules or []
    sell_rules = cfg.sell_rules or []
    no_trade_rules = cfg.no_trade_rules or []
    range_zones = cfg.range_zones or []
    rm = cfg.risk_management
    rm_mode = rm.mode
    use_atr_stop = rm_mode == "ATR Stop Loss (Volatility)"
    sl_pct = rm.sl_pct if rm_mode == "Static % Stop Loss / Take Profit" else 0.0
    # Take-profit may accompany a static-% OR an ATR stop
    tp_pct = rm.tp_pct if rm_mode in ("Static % Stop Loss / Take Profit", "ATR Stop Loss (Volatility)") else 0.0
    trail_pct = rm.trail_pct if rm_mode == "Trailing Stop Loss (%)" else 0.0
    strat_dir = rm.strat_dir
    pos_size_type = cfg.pos_size_type
    fixed_pos_value = cfg.fixed_pos_value
    lot_size = cfg.lot_size
    risk_pct = getattr(rm, "risk_pct", 1.0)               # v7.1 Task 3: risk-% sizing
    max_dd_halt_pct = getattr(rm, "max_dd_halt_pct", 0.0)  # v7.1 Task 3: max-drawdown circuit breaker
    atr_period = getattr(rm, "atr_period", 14)             # v7.1 Task 3 pt.2: ATR (volatility) stop
    atr_stop_mult = getattr(rm, "atr_stop_mult", 2.0)
    max_bars_in_trade = getattr(rm, "max_bars_in_trade", 0)  # v7.1 Task 3 pt.2: time stop
    # v7.8 Task 3 pt.3 — breakeven stop + partial exit / scale-out
    breakeven_trigger_pct = getattr(rm, "breakeven_trigger_pct", 0.0)
    partial_exit_pct = getattr(rm, "partial_exit_pct", 0.0)
    partial_trigger_pct = getattr(rm, "partial_trigger_pct", 0.0)
    has_breakeven = breakeven_trigger_pct > 0
    has_partial = partial_exit_pct > 0 and partial_trigger_pct > 0
    is_kelly = pos_size_type in ("Half-Kelly (Adaptive)", "Full Kelly (Adaptive)")
    # v8.1 — multi-timeframe bias + intraday square-off
    htf_bias = bool(getattr(rm, "htf_bias", False))
    htf_minutes = int(getattr(rm, "htf_minutes", 60) or 60)
    htf_ema_period = int(getattr(rm, "htf_ema_period", 50) or 50)
    intraday_square_off = bool(getattr(rm, "intraday_square_off", False))
    # v8.2 — intraday entry time window filter
    entry_time_filter = bool(getattr(rm, "entry_time_filter", False))
    entry_start_time = str(getattr(rm, "entry_start_time", "09:30") or "09:30")
    entry_end_time = str(getattr(rm, "entry_end_time", "14:45") or "14:45")

    has_zones = len(range_zones) > 0

    code = "import pandas_ta as pta\n"
    code += "class VisualStrategy(Strategy):\n"
    code += f"    lot_size = {lot_size}\n"

    # class-level indicator params
    for ind in indicators:
        name = safe_name(ind["name"])
        for k, v in ind.get("params", {}).items():
            val_repr = repr(v) if isinstance(v, str) else f"{v}"
            code += f"    {name}_{k} = {val_repr}\n"

    # class-level numeric rule thresholds
    for i, r in enumerate(buy_rules):
        if r.get("is_num"):
            code += f"    buy_rule_{i}_val = {r['var2']}\n"
    for i, r in enumerate(sell_rules):
        if r.get("is_num"):
            code += f"    sell_rule_{i}_val = {r['var2']}\n"
    for i, r in enumerate(no_trade_rules):
        if r.get("is_num"):
            code += f"    no_trade_rule_{i}_val = {r['var2']}\n"
    for i, z in enumerate(range_zones):
        code += f"    zone_{i}_min = {z['min_val']}\n"
        code += f"    zone_{i}_max = {z['max_val']}\n"
        if z["action"] == "Tighten Trailing Stop":
            code += f"    zone_{i}_tight_pct = {z.get('tight_pct', 1.0)}\n"

    # init()
    code += "\n    def init(self):\n"
    code += "        self.dividends_earned = 0.0\n"
    code += "        self.rights_premium_earned = 0.0\n"
    code += "        self._prev_position_size = 0\n"
    if has_partial:  # v7.8: one scale-out per trade — reset when flat (see next())
        code += "        self._scaled_out = False\n"

    src_map: Dict[str, str] = {
        "Close": "self.data.Close", "Open": "self.data.Open", "High": "self.data.High",
        "Low": "self.data.Low", "Volume": "self.data.Volume",
    }

    code += "        # Helper function for rolling apply\n"
    code += "        def rolling_apply(func, series, window):\n"
    code += "            return pd.Series(series).rolling(window).apply(func, raw=False)\n\n"

    for ind in indicators:
        code += emit_indicator(ind, src_map)

    if use_atr_stop:  # ATR series for the volatility stop / ATR-risk sizing (v7.1 Task 3 pt.2)
        code += f"        self._risk_atr = self.I(ta.volatility.average_true_range, pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), {atr_period})\n"

    if rm_mode == "Trailing Stop Loss (%)" or has_zones:
        code += "        self._highest_since_entry = 0\n"
        code += "        self._lowest_since_entry = float('inf')\n"

    if htf_bias:
        # Higher-timeframe EMA trend bias: resample the base close to the HTF, take an EMA,
        # forward-fill it back onto the base bars. Longs only above it, shorts only below.
        code += "        _htf_idx = pd.DatetimeIndex(self.data.index)\n"
        code += "        _htf_close = pd.Series(np.asarray(self.data.Close), index=_htf_idx)\n"
        code += f"        _htf_ema_res = _htf_close.resample('{htf_minutes}min').last().dropna().ewm(span={htf_ema_period}, adjust=False).mean()\n"
        # Shift by 1 HTF bar so only completed higher-timeframe bars are visible (no lookahead)
        code += "        _htf_ema_res = _htf_ema_res.shift(1)\n"
        code += "        _htf_ema_full = _htf_ema_res.reindex(_htf_idx, method='ffill').to_numpy()\n"
        code += "        self._htf_ema = self.I(lambda: _htf_ema_full, name='HTF_EMA', overlay=True)\n"

    if intraday_square_off:
        # Precompute (from the full index) which bars are the LAST bar of their session/day.
        code += "        _sq_days = pd.Series(pd.DatetimeIndex(self.data.index)).dt.normalize().to_numpy()\n"
        code += "        self._session_last = (np.append(_sq_days[:-1] != _sq_days[1:], True)\n"
        code += "                              if len(_sq_days) else np.array([], dtype=bool))\n"

    # next()
    code += "\n    def next(self):\n"
    code += "        if len(self.data.Close) < 2: return\n"
    code += "        if hasattr(self.data, 'Dividend') and getattr(self, '_prev_position_size', 0) > 0:\n"
    code += "            div = self.data.Dividend[-1]\n"
    code += "            if div > 0:\n"
    code += "                earned = div * self._prev_position_size\n"
    code += "                self.dividends_earned += earned\n"
    code += "                self._broker._cash += earned\n"
    code += "        if hasattr(self.data, 'Rights_Premium') and getattr(self, '_prev_position_size', 0) > 0:\n"
    code += "            rp = self.data.Rights_Premium[-1]\n"
    code += "            if rp > 0:\n"
    code += "                earned = rp * self._prev_position_size\n"
    code += "                self.rights_premium_earned += earned\n"
    code += "                self._broker._cash += earned\n"

    if intraday_square_off:
        code += "        # Intraday square-off (MIS): flatten at each session's last bar (no overnight carry)\n"
        code += "        _sq_i = len(self.data.Close) - 1\n"
        code += "        if _sq_i < len(self._session_last) and self._session_last[_sq_i]:\n"
        code += "            if self.position: self.position.close()\n"
        code += "            self._prev_position_size = self.position.size\n"
        code += "            return\n"

    if has_partial:  # reset the one-scale-out-per-trade flag whenever we are flat
        code += "        if not self.position: self._scaled_out = False\n"

    if max_dd_halt_pct > 0:
        # Max-drawdown circuit breaker (v7.1 Task 3): once equity falls this far below its running
        # peak, flatten any position and block new entries for the rest of the run.
        code += "        # Max-drawdown circuit breaker\n"
        code += "        self._peak_equity = max(getattr(self, '_peak_equity', self.equity), self.equity)\n"
        code += f"        if self.equity <= self._peak_equity * {1 - max_dd_halt_pct / 100.0}:\n"
        code += "            if self.position: self.position.close()\n"
        code += "            self._prev_position_size = self.position.size\n"
        code += "            return\n"

    if max_bars_in_trade > 0:
        # Time stop (v7.1 Task 3 pt.2): close a trade after a maximum number of bars held.
        code += "        # Time stop\n"
        code += f"        if self.position and self.trades and (len(self.data.Close) - self.trades[-1].entry_bar) >= {int(max_bars_in_trade)}:\n"
        code += "            self.position.close()\n"
        code += "            self._prev_position_size = self.position.size\n"
        code += "            return\n"

    if has_partial:
        # Partial exit / scale-out (v7.8 Task 3 pt.3): once the OPEN trade has moved the trigger%
        # in favour, bank a fraction of the position and let the rest run. Once per trade (the
        # _scaled_out flag, reset when flat). Any native SL/TP carries to the remainder.
        frac = min(max(partial_exit_pct / 100.0, 0.01), 0.99)  # keep it a genuine partial (0<frac<1)
        code += "        # Partial exit / scale-out\n"
        code += "        if self.position and self.trades and not self._scaled_out:\n"
        code += "            _pe_entry = self.trades[-1].entry_price\n"
        code += f"            if self.position.is_long and self.data.High[-1] >= _pe_entry * {1 + partial_trigger_pct / 100.0}:\n"
        code += f"                self.position.close(portion={frac})\n"
        code += "                self._scaled_out = True\n"
        code += f"            elif self.position.is_short and self.data.Low[-1] <= _pe_entry * {1 - partial_trigger_pct / 100.0}:\n"
        code += f"                self.position.close(portion={frac})\n"
        code += "                self._scaled_out = True\n"

    if has_breakeven:
        # Breakeven stop (v7.8 Task 3 pt.3): once the OPEN trade has moved the trigger% in favour,
        # ratchet its stop up to the entry price (never loosen). Guarded so the new stop always sits
        # on the safe side of the current close (a long's stop below it), so it can't fill instantly.
        code += "        # Breakeven stop\n"
        code += "        if self.position and self.trades:\n"
        code += "            _be_t = self.trades[-1]\n"
        code += "            _be_entry = _be_t.entry_price\n"
        code += f"            if self.position.is_long and self.data.High[-1] >= _be_entry * {1 + breakeven_trigger_pct / 100.0}:\n"
        code += "                _be = breakeven_stop(_be_entry, False)\n"
        code += "                if _be is not None and _be < self.data.Close[-1] and (_be_t.sl is None or _be_t.sl < _be):\n"
        code += "                    _be_t.sl = _be\n"
        code += f"            elif self.position.is_short and self.data.Low[-1] <= _be_entry * {1 - breakeven_trigger_pct / 100.0}:\n"
        code += "                _be = breakeven_stop(_be_entry, True)\n"
        code += "                if _be is not None and _be > self.data.Close[-1] and (_be_t.sl is None or _be_t.sl > _be):\n"
        code += "                    _be_t.sl = _be\n"

    buy_cond = _gen_logic(buy_rules, "buy", src_map)
    sell_cond = _gen_logic(sell_rules, "sell", src_map)
    no_trade_cond = _gen_logic(no_trade_rules, "no_trade", src_map)

    has_regime = bool(no_trade_rules) and no_trade_cond != "False"

    if has_regime:
        code += "        # Regime filter: No-Trade Zone check\n"
        code += f"        _no_trade_zone = {no_trade_cond}\n\n"

    if has_zones:
        code += "        # Advanced Range Management: classify indicator zones\n"
        code += "        _zone_block_entry = False\n"
        code += "        _zone_close_position = False\n"
        # self._zone_tight_sl so the trailing block (which reads self._zone_tight_sl) sees it.
        code += "        self._zone_tight_sl = 0.0\n"
        for zi, z in enumerate(range_zones):
            ind_ref = src_map.get(z["indicator"], f"self.{z['indicator']}")
            code += f"        # Zone: {z.get('label', f'Zone_{zi}')}\n"
            code += f"        if {ind_ref}[-1] >= self.zone_{zi}_min and {ind_ref}[-1] <= self.zone_{zi}_max:\n"
            if z["action"] == "Block New Entries":
                code += "            _zone_block_entry = True\n"
            elif z["action"] == "Close Position & Block":
                code += "            _zone_block_entry = True\n"
                code += "            _zone_close_position = True\n"
            elif z["action"] == "Tighten Trailing Stop":
                code += f"            self._zone_tight_sl = self.zone_{zi}_tight_pct\n"
            else:
                code += "            pass  # Normal Trading\n"
        code += "\n"

    # position sizing (v7.1 Task 3: + risk-% of equity, sized from the stop distance)
    if pos_size_type == "Risk % of Equity per Trade":
        if use_atr_stop:  # size from the ATR-derived stop distance (mult × ATR)
            code += f"        _rq = atr_risk_qty(self.equity, {risk_pct}, self._risk_atr[-1], {atr_stop_mult}, self.lot_size)\n"
            code += "        _cap = max(self.lot_size, int((self.equity * 0.99) // self.data.Close[-1]))\n"
            code += "        _trade_size = max(self.lot_size, min(_rq, _cap))\n"
        elif sl_pct > 0:  # the static % stop gives the per-unit risk (stop distance)
            code += f"        _stop_dist = self.data.Close[-1] * {sl_pct / 100.0}\n"
            code += f"        _rq = risk_pct_qty(self.equity, {risk_pct}, _stop_dist, self.lot_size)\n"
            # cap to what equity can actually afford (tight stops otherwise size beyond capital)
            code += "        _cap = max(self.lot_size, int((self.equity * 0.99) // self.data.Close[-1]))\n"
            code += "        _trade_size = max(self.lot_size, min(_rq, _cap))\n"
        else:  # no stop distance available -> fall back to a fixed notional
            code += f"        _trade_size = fixed_value_qty({fixed_pos_value or 50000.0}, self.data.Close[-1], self.lot_size)\n"
    elif is_kelly:
        # Adaptive Kelly sizing (v7.8 Task 3 pt.3): estimate win-rate & payoff from THIS strategy's
        # own realized trades so far, then deploy that Kelly fraction of equity. Before KELLY_MIN_TRADES
        # have closed (or when the measured edge is <= 0) it sizes at the fixed-value fallback / minimum
        # lot — Kelly only sizes UP once a real, positive edge has been observed.
        kelly_fn = "kelly_fraction" if pos_size_type == "Full Kelly (Adaptive)" else "half_kelly_fraction"
        code += "        _ct = self.closed_trades\n"
        code += f"        if len(_ct) >= {int(KELLY_MIN_TRADES)}:\n"
        code += "            _wins = [t.pl for t in _ct if t.pl > 0]\n"
        code += "            _losses = [-t.pl for t in _ct if t.pl < 0]\n"
        code += "            if _wins and _losses:\n"
        code += "                _wr = len(_wins) / (len(_wins) + len(_losses))\n"
        code += "                _payoff = (sum(_wins) / len(_wins)) / (sum(_losses) / len(_losses))\n"
        code += f"                _f = {kelly_fn}(_wr, _payoff)\n"
        code += "            else:\n"
        code += "                _f = 0.0\n"
        code += "            _raw_qty = int((self.equity * _f * 0.99) // self.data.Close[-1])\n"
        code += "            _trade_size = max(self.lot_size, _raw_qty - (_raw_qty % self.lot_size))\n"
        code += "        else:\n"
        code += f"            _trade_size = fixed_value_qty({fixed_pos_value or 50000.0}, self.data.Close[-1], self.lot_size)\n"
    elif pos_size_type == "Fixed Value per Trade (₹) (Default)":
        code += f"        _raw_qty = int({fixed_pos_value} // self.data.Close[-1])\n"
        code += "        _trade_size = max(self.lot_size, _raw_qty - (_raw_qty % self.lot_size))\n"
    else:  # Compound 100% Equity / Percent of Equity
        code += "        _raw_qty = int((self.equity * 0.99) // self.data.Close[-1])\n"
        code += "        _trade_size = max(self.lot_size, _raw_qty - (_raw_qty % self.lot_size))\n"
    size_arg = "size=_trade_size"

    def get_sl_tp(is_short=False):
        sl_str, tp_str = "None", "None"
        if use_atr_stop:  # volatility stop: entry -/+ mult*ATR (returns None until ATR warms up)
            sl_str = f"atr_stop(self.data.Close[-1], self._risk_atr[-1], {atr_stop_mult}, {is_short})"
            if tp_pct > 0:
                tp_str = (
                    f"self.data.Close[-1] * (1 - {tp_pct / 100})"
                    if is_short
                    else f"self.data.Close[-1] * (1 + {tp_pct / 100})"
                )
        elif rm_mode == "Static % Stop Loss / Take Profit" and (sl_pct > 0 or tp_pct > 0):
            if sl_pct > 0:
                sl_str = (
                    f"self.data.Close[-1] * (1 + {sl_pct / 100})"
                    if is_short
                    else f"self.data.Close[-1] * (1 - {sl_pct / 100})"
                )
            if tp_pct > 0:
                tp_str = (
                    f"self.data.Close[-1] * (1 - {tp_pct / 100})"
                    if is_short
                    else f"self.data.Close[-1] * (1 + {tp_pct / 100})"
                )
        return sl_str, tp_str

    buy_cond_str = buy_cond if buy_rules else "False"
    sell_cond_str = sell_cond if sell_rules else "False"

    code += f"        _buy_signal = {buy_cond_str}\n"
    code += f"        _sell_signal = {sell_cond_str}\n"

    if htf_bias:  # higher-timeframe trend bias gate (NaN during warm-up -> blocks entries)
        code += "        _htf_long_ok = bool(self.data.Close[-1] > self._htf_ema[-1])\n"
        code += "        _htf_short_ok = bool(self.data.Close[-1] < self._htf_ema[-1])\n"
    else:
        code += "        _htf_long_ok = True\n"
        code += "        _htf_short_ok = True\n"

    code += "        _block_new_entries = False\n"
    if has_regime:
        code += "        if _no_trade_zone:\n"
        code += "            _block_new_entries = True\n"

    if has_zones:
        code += "        if _zone_block_entry:\n"
        code += "            _block_new_entries = True\n"
        code += "        if _zone_close_position:\n"
        code += "            if self.position: self.position.close()\n"
        code += "            _block_new_entries = True\n"

    if entry_time_filter:
        code += "        _bar_hm = self.data.index[-1].strftime('%H:%M')\n"
        code += f"        if not ({entry_start_time!r} <= _bar_hm <= {entry_end_time!r}):\n"
        code += "            _block_new_entries = True\n"

    code += "        if self.equity <= 0: return  # Bankrupt\n"

    # Buy
    code += "        if _buy_signal:\n"
    if strat_dir in ["Long & Short", "Short Only"]:
        code += "            if self.position.is_short: self.position.close()\n"
    if strat_dir in ["Long & Short", "Long Only"]:
        sl_str, tp_str = get_sl_tp(is_short=False)
        code += "            if not self.position.is_long and not _block_new_entries and _htf_long_ok:\n"
        if sl_str != "None" or tp_str != "None":
            code += f"                self.buy({size_arg}, sl={sl_str}, tp={tp_str})\n"
        else:
            code += f"                self.buy({size_arg})\n"

    # Sell
    code += "        if _sell_signal:\n"
    if strat_dir in ["Long & Short", "Long Only"]:
        code += "            if self.position.is_long: self.position.close()\n"
    if strat_dir in ["Long & Short", "Short Only"]:
        sl_str, tp_str = get_sl_tp(is_short=True)
        code += "            if not self.position.is_short and not _block_new_entries and _htf_short_ok:\n"
        if sl_str != "None" or tp_str != "None":
            code += f"                self.sell({size_arg}, sl={sl_str}, tp={tp_str})\n"
        else:
            code += f"                self.sell({size_arg})\n"

    # Trailing stops
    if rm_mode == "Trailing Stop Loss (%)" or has_zones:
        code += "        # Trailing Stops\n"
        if strat_dir in ["Long & Short", "Long Only"]:
            code += "        if self.position.is_long:\n"
            code += "            if getattr(self, '_prev_position_size', 0) <= 0:\n"
            code += "                self._highest_since_entry = self.trades[-1].entry_price if self.trades else self.data.Close[-1]\n"
            code += "            else:\n"
            code += "                self._highest_since_entry = max(getattr(self, '_highest_since_entry', self.data.Close[-1]), self.data.High[-1])\n"
            if has_zones:
                code += f"            _eff_trail = {trail_pct / 100} if '{rm_mode}' == 'Trailing Stop Loss (%)' else 0\n"
                code += "            if getattr(self, '_zone_tight_sl', 0) > 0: _eff_trail = max(_eff_trail, self._zone_tight_sl / 100)\n"
                code += "            if _eff_trail > 0:\n"
                code += "                trail_stop = self._highest_since_entry * (1 - _eff_trail)\n"
                code += "                if self.data.Low[-1] <= trail_stop: self.position.close()\n"
            elif rm_mode == "Trailing Stop Loss (%)":
                code += f"            trail_stop = self._highest_since_entry * (1 - {trail_pct / 100})\n"
                code += "            if self.data.Low[-1] <= trail_stop: self.position.close()\n"

        if strat_dir in ["Long & Short", "Short Only"]:
            code += "        if self.position.is_short:\n"
            # _prev_position_size is SIGNED (negative while short). A fresh short means we were
            # flat/long last bar => prev >= 0; while continuing short prev < 0 and we ratchet the
            # low DOWN. (Using <= 0 here was the bug: it stayed true every bar, pinning the trail.)
            code += "            if getattr(self, '_prev_position_size', 0) >= 0:\n"
            code += "                self._lowest_since_entry = self.trades[-1].entry_price if self.trades else self.data.Close[-1]\n"
            code += "            else:\n"
            code += "                self._lowest_since_entry = min(getattr(self, '_lowest_since_entry', self.data.Close[-1]), self.data.Low[-1])\n"
            if has_zones:
                code += f"            _eff_trail = {trail_pct / 100} if '{rm_mode}' == 'Trailing Stop Loss (%)' else 0\n"
                code += "            if getattr(self, '_zone_tight_sl', 0) > 0: _eff_trail = max(_eff_trail, self._zone_tight_sl / 100)\n"
                code += "            if _eff_trail > 0:\n"
                code += "                trail_stop = self._lowest_since_entry * (1 + _eff_trail)\n"
                code += "                if self.data.High[-1] >= trail_stop: self.position.close()\n"
            elif rm_mode == "Trailing Stop Loss (%)":
                code += f"            trail_stop = self._lowest_since_entry * (1 + {trail_pct / 100})\n"
                code += "            if self.data.High[-1] >= trail_stop: self.position.close()\n"

    code += "        self._prev_position_size = self.position.size\n"
    return code
