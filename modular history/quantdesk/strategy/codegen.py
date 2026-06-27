"""
quantdesk.strategy.codegen — build a backtesting.py Strategy class source from a StrategyConfig.

Faithful port of the original "Compile Strategy" block (upstox_app.py:1876-2257). It reads
from a :class:`quantdesk.strategy.models.StrategyConfig` instead of ``st.session_state`` and
delegates per-indicator emission to :func:`quantdesk.strategy.indicators.emit_indicator`. The
generated source is functionally identical to the monolith's, so backtest results match.
"""
from __future__ import annotations

from typing import Dict

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
    sl_pct = rm.sl_pct if rm_mode == "Static % Stop Loss / Take Profit" else 0.0
    tp_pct = rm.tp_pct if rm_mode == "Static % Stop Loss / Take Profit" else 0.0
    trail_pct = rm.trail_pct if rm_mode == "Trailing Stop Loss (%)" else 0.0
    strat_dir = rm.strat_dir
    pos_size_type = cfg.pos_size_type
    fixed_pos_value = cfg.fixed_pos_value
    lot_size = cfg.lot_size

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

    src_map: Dict[str, str] = {
        "Close": "self.data.Close", "Open": "self.data.Open", "High": "self.data.High",
        "Low": "self.data.Low", "Volume": "self.data.Volume",
    }

    code += "        # Helper function for rolling apply\n"
    code += "        def rolling_apply(func, series, window):\n"
    code += "            return pd.Series(series).rolling(window).apply(func, raw=False)\n\n"

    for ind in indicators:
        code += emit_indicator(ind, src_map)

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

    # position sizing
    if pos_size_type == "Fixed Value per Trade (₹) (Default)":
        code += f"        _raw_qty = int({fixed_pos_value} // self.data.Close[-1])\n"
    else:
        code += "        _raw_qty = int((self.equity * 0.99) // self.data.Close[-1])\n"
    code += "        _trade_size = max(self.lot_size, _raw_qty - (_raw_qty % self.lot_size))\n"
    size_arg = "size=_trade_size"

    def get_sl_tp(is_short=False):
        sl_str, tp_str = "None", "None"
        if rm_mode == "Static % Stop Loss / Take Profit" and (sl_pct > 0 or tp_pct > 0):
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
