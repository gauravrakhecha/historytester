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


def _gen_logic(rules: list, prefix: str, src_map: Dict[str, str]) -> str:
    """Reproduce the original ``gen_logic`` (upstox_app.py:2096-2117)."""
    if not rules:
        return "False"
    conds = []
    for idx, r in enumerate(rules):
        v1 = src_map.get(r["var1"], f"self.{r['var1']}")
        op = r["operator"]
        if r.get("is_num"):
            v2 = f"self.{prefix}_rule_{idx}_val"
            if op == "Crosses Above":
                cond = f"({v1}[-2] <= {v2} and {v1}[-1] > {v2})"
            elif op == "Crosses Below":
                cond = f"({v1}[-2] >= {v2} and {v1}[-1] < {v2})"
            else:
                cond = f"({v1}[-1] {op} {v2})"
        else:
            v2 = src_map.get(r["var2"], f"self.{r['var2']}")
            if op == "Crosses Above":
                cond = f"({v1}[-2] <= {v2}[-2] and {v1}[-1] > {v2}[-1])"
            elif op == "Crosses Below":
                cond = f"({v1}[-2] >= {v2}[-2] and {v1}[-1] < {v2}[-1])"
            else:
                cond = f"({v1}[-1] {op} {v2}[-1])"
        if idx == 0:
            conds.append(cond)
        else:
            logic = "and" if r["logic"] == "AND" else "or"
            conds.append(f" {logic} {cond}")
    return "".join(conds)


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

    has_zones = len(range_zones) > 0

    code = "import pandas_ta as pta\n"
    code += "class VisualStrategy(Strategy):\n"
    code += f"    lot_size = {lot_size}\n"

    # class-level indicator params
    for ind in indicators:
        name = safe_name(ind["name"])
        for k, v in ind.get("params", {}).items():
            code += f"    {name}_{k} = {v}\n"

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

    code += "        if self.equity <= 0: return  # Bankrupt\n"

    # Buy
    code += "        if _buy_signal:\n"
    if strat_dir in ["Long & Short", "Short Only"]:
        code += "            if self.position.is_short: self.position.close()\n"
    if strat_dir in ["Long & Short", "Long Only"]:
        sl_str, tp_str = get_sl_tp(is_short=False)
        code += "            if not self.position.is_long and not _block_new_entries:\n"
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
        code += "            if not self.position.is_short and not _block_new_entries:\n"
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
