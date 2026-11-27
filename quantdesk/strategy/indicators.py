"""
quantdesk.strategy.indicators — indicator registry (codegen) + param specs + fast helpers.

This houses the per-indicator code emission that the original kept as one giant if/elif chain
(upstox_app.py:1916-2073). ``emit_indicator`` reproduces the SAME generated source so compiled
strategies behave identically (backtest parity).

Also exposed:
- ``INDICATOR_TYPES`` / ``INDICATOR_PARAM_SPECS`` — drive the builder UI (from lines 1643-1704).
- vectorised helpers ``lr_slope_vec`` / ``r2_vec`` / ``alma_vec`` and ``rolling_apply_*``
  reference implementations, validated for equality in tests/test_indicators.py. They are the
  fast equivalents of the original ``rolling().apply()`` forms used by Linear-Reg Slope, R² and
  ALMA. Hurst and HMM keep the original algorithm (documented; numbers unchanged).
"""
from __future__ import annotations

import ast
import math
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import ta

from quantdesk.strategy.models import classify_columns, safe_name

# When True (default) ALMA / Linear-Reg-Slope / R-Squared emit fast vectorised forms.
# When False they emit the original per-window rolling.apply forms. The two are proven
# numerically equal (tests/test_indicators.py + tests/test_parity.py); the switch exists so
# parity can be A/B tested and as an escape hatch.
USE_VECTORIZED = True

# Order shown in the builder dropdown (upstox_app.py:1643-1650)
INDICATOR_TYPES: List[str] = [
    "SMA", "EMA", "WMA", "ALMA", "RSI", "MACD", "Bollinger Bands", "ATR",
    "Linear Regression Slope", "R-Squared",
    "ATR-Normalized Angle", "Angle of Indicator",
    "Supertrend", "Parabolic SAR", "ADX", "Stochastic Oscillator",
    "VWAP", "Keltner Channels", "Donchian Channels",
    "Hurst Exponent", "HMM Regime",
    # v7 normalized / cross-asset-comparable features
    "Ratio to SMA", "Distance from MA (ATR)", "MA Slope (ATR)",
    "Z-Score", "Percentile Rank", "Return (n-bar)",
    # v8.2 visual custom building blocks & safe formula
    "Series Math", "Rolling Stat", "Bar Shift", "Custom Formula",
]

# Parameter widgets per type (mirrors upstox_app.py:1666-1704).
# Each entry: list of (param_key, kind, default, extra) where kind in {int,float} and
# extra carries min/format/help hints for the UI.
INDICATOR_PARAM_SPECS: Dict[str, list] = {
    "SMA": [("period", "int", 14, {"min": 1})],
    "EMA": [("period", "int", 14, {"min": 1})],
    "WMA": [("period", "int", 14, {"min": 1})],
    "ALMA": [("period", "int", 14, {"min": 1})],
    "RSI": [("period", "int", 14, {"min": 1})],
    "ATR": [("period", "int", 14, {"min": 1})],
    "Linear Regression Slope": [("period", "int", 14, {"min": 1})],
    "R-Squared": [("period", "int", 14, {"min": 1})],
    "ATR-Normalized Angle": [("period", "int", 14, {"min": 1})],
    "VWAP": [("period", "int", 14, {"min": 1})],
    "Hurst Exponent": [("period", "int", 100, {"min": 100, "help": "Needs >=100 points."})],
    "HMM Regime": [
        ("period", "int", 100, {"min": 10, "help": "Rolling window to fit the HMM."}),
        ("components", "int", 2, {"min": 2, "max": 5, "help": "Hidden states (e.g. 2=Bull/Bear)."}),
    ],
    "Angle of Indicator": [
        ("period", "int", 5, {"min": 1, "help": "Bars to compute the slope over."}),
        ("atr_period", "int", 14, {"min": 1, "help": "ATR period for normalisation."}),
    ],
    "Bollinger Bands": [
        ("period", "int", 20, {"min": 1}),
        ("std_dev", "float", 2.0, {"min": 0.1}),
    ],
    "MACD": [
        ("fast", "int", 12, {}),
        ("slow", "int", 26, {}),
        ("sign", "int", 9, {}),
    ],
    "Supertrend": [
        ("period", "int", 7, {"min": 1}),
        ("multiplier", "float", 3.0, {"min": 0.1}),
    ],
    "Parabolic SAR": [
        ("af", "float", 0.02, {"min": 0.001, "format": "%.3f"}),
        ("max_af", "float", 0.2, {"min": 0.01, "format": "%.2f"}),
    ],
    "ADX": [("period", "int", 14, {"min": 1})],
    "Stochastic Oscillator": [
        ("k_period", "int", 14, {"min": 1}),
        ("d_period", "int", 3, {"min": 1}),
    ],
    "Keltner Channels": [
        ("period", "int", 20, {"min": 1}),
        ("multiplier", "float", 2.0, {"min": 0.1}),
    ],
    "Donchian Channels": [("period", "int", 20, {"min": 1})],
    # v7 normalized features (own-scale, comparable across instruments/timeframes)
    "Ratio to SMA": [("period", "int", 20, {"min": 1, "help": "x / SMA(x, period). Pick Volume as source for 'volume vs its average'."})],
    "Distance from MA (ATR)": [
        ("period", "int", 20, {"min": 1, "help": "EMA length"}),
        ("atr_period", "int", 14, {"min": 1, "help": "ATR length used to normalise"}),
    ],
    "MA Slope (ATR)": [
        ("period", "int", 20, {"min": 1, "help": "EMA length"}),
        ("lookback", "int", 5, {"min": 1, "help": "bars to measure slope over"}),
        ("atr_period", "int", 14, {"min": 1, "help": "ATR length used to normalise"}),
    ],
    "Z-Score": [("period", "int", 50, {"min": 2, "help": "rolling window for mean/std"})],
    "Percentile Rank": [("period", "int", 50, {"min": 2, "help": "rolling window; output 0..1"})],
    "Return (n-bar)": [("period", "int", 5, {"min": 1, "help": "x / x[t-period] - 1"})],
    # v8.2 visual custom building blocks & safe formula
    "Series Math": [
        ("op", "select", "add", {
            "choices": ["add", "sub", "mul", "div", "pct_diff"],
            "help": "add (A+B), sub (A-B), mul (A*B), div (A/B), pct_diff ((A-B)/B*100)",
        }),
        ("use_num", "int", 0, {
            "min": 0, "max": 1,
            "help": "0 = combine with Series B (rhs), 1 = combine with constant num_val",
        }),
        ("num_val", "float", 0.0, {
            "help": "Numeric constant B when use_num=1",
        }),
    ],
    "Rolling Stat": [
        ("stat", "select", "max", {
            "choices": ["max", "min", "mean", "sum", "std"],
            "help": "Rolling statistic over the last N bars",
        }),
        ("period", "int", 20, {"min": 1, "help": "Rolling window length (bars)"}),
    ],
    "Bar Shift": [
        ("shift", "int", 1, {"min": 0, "help": "Bars to shift backward (e.g. 1 = previous bar)"}),
    ],
    "Custom Formula": [],
}

# Cross-asset-comparable indicator types (v7 Phase B): their value carries the SAME meaning
# regardless of the instrument's absolute price/volume scale — the v7 normalized features plus
# inherently bounded / unit-free oscillators. The ML Pattern Finder DEFAULTS to these (a learned
# rule like "Z-Score < -1.5" generalizes across RELIANCE ~1,300 and NIFTY ~25,000, whereas
# "EMA20 > 1330" does not). Anything NOT listed here is treated as raw/absolute: the ML finder
# warns when it is selected and per-instrument z-scores it before bulk aggregation.
CROSS_ASSET_COMPARABLE_TYPES = {
    # v7 normalized features (Phase A)
    "Ratio to SMA", "Distance from MA (ATR)", "MA Slope (ATR)",
    "Z-Score", "Percentile Rank", "Return (n-bar)",
    # inherently bounded / unit-free oscillators (already comparable across instruments)
    "RSI", "ADX", "Stochastic Oscillator", "R-Squared", "Hurst Exponent",
    "ATR-Normalized Angle", "Angle of Indicator", "HMM Regime",
}


def partition_features(selected: List[str], indicators: List[dict]):
    """Split selected feature columns into ``(comparable, raw)`` by their indicator type.

    OHLCV columns and price-scale / absolute indicators (SMA/EMA/MACD/ATR/…) are *raw*; the v7
    normalized features and bounded oscillators are *comparable*. Used by the ML Pattern Finder to
    (a) pick a sensible default selection, (b) warn when raw columns are chosen, and (c) decide
    which columns to per-instrument-normalize before concatenating trades from many instruments.
    """
    col_type = {col: meta.get("type") for col, meta in classify_columns(indicators).items()}
    comparable, raw = [], []
    for f in selected:
        if col_type.get(f) in CROSS_ASSET_COMPARABLE_TYPES:
            comparable.append(f)
        else:  # OHLCV (not in classify_columns) or a price-scale/absolute indicator
            raw.append(f)
    return comparable, raw


def emit_indicator(ind: dict, src_map: Dict[str, str]) -> str:
    """Return the generated ``init()`` code lines for one indicator and update ``src_map``.

    Byte-for-byte equivalent to the original chain so compiled strategies match exactly.
    """
    raw_name = ind["name"]
    name = safe_name(raw_name)
    prior_src_map = dict(src_map)
    src_col = ind.get("source", "Close") or "Close"
    src = prior_src_map.get(src_col, f"self.{src_col}")
    src_map[raw_name] = f"self.{name}"
    t = ind["type"]
    code = ""

    if t == "SMA":
        code += f"        self.{name} = self.I(ta.trend.sma_indicator, pd.Series({src}), self.{name}_period)\n"
    elif t == "EMA":
        code += f"        self.{name} = self.I(ta.trend.ema_indicator, pd.Series({src}), self.{name}_period)\n"
    elif t == "WMA":
        code += f"        self.{name} = self.I(ta.trend.wma_indicator, pd.Series({src}), self.{name}_period)\n"
    elif t == "ALMA":
        if USE_VECTORIZED:
            # SPEED: single-convolution ALMA (proven equal to the original rolling apply).
            code += f"        self.{name} = self.I(alma_vec, {src}, self.{name}_period)\n"
        else:
            code += f"""        def alma_{name}(series, window, offset=0.85, sigma=6):
            m = offset * (window - 1)
            s = window / sigma
            weights = np.exp(-((np.arange(window) - m) ** 2) / (2 * s ** 2))
            weights /= weights.sum()
            return np.convolve(series, weights[::-1], mode='valid')
        self.{name} = self.I(lambda s: pd.Series(s).rolling(self.{name}_period).apply(lambda x: alma_{name}(x, self.{name}_period)[-1] if len(x)==self.{name}_period else np.nan, raw=True), {src})\n"""
    elif t == "RSI":
        code += f"        self.{name} = self.I(ta.momentum.rsi, pd.Series({src}), self.{name}_period)\n"
    elif t == "MACD":
        code += f"        macd_{name} = ta.trend.MACD(close=pd.Series({src}), window_slow=self.{name}_slow, window_fast=self.{name}_fast, window_sign=self.{name}_sign)\n"
        code += f"        self.{name}_line = self.I(lambda: macd_{name}.macd())\n"
        code += f"        self.{name}_signal = self.I(lambda: macd_{name}.macd_signal())\n"
        src_map[f"{raw_name}_line"] = f"self.{name}_line"
        src_map[f"{raw_name}_signal"] = f"self.{name}_signal"
    elif t == "Bollinger Bands":
        code += f"        bb_{name} = ta.volatility.BollingerBands(close=pd.Series({src}), window=self.{name}_period, window_dev=self.{name}_std_dev)\n"
        code += f"        self.{name}_high = self.I(lambda: bb_{name}.bollinger_hband())\n"
        code += f"        self.{name}_low = self.I(lambda: bb_{name}.bollinger_lband())\n"
        src_map[f"{raw_name}_high"] = f"self.{name}_high"
        src_map[f"{raw_name}_low"] = f"self.{name}_low"
    elif t == "ATR":
        code += f"        self.{name} = self.I(ta.volatility.average_true_range, pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), self.{name}_period)\n"
    elif t == "Linear Regression Slope":
        if USE_VECTORIZED:
            # SPEED: closed-form rolling OLS slope (proven equal to scipy.linregress slope).
            code += f"        self.{name} = self.I(lr_slope_vec, {src}, self.{name}_period)\n"
        else:
            code += "        from scipy.stats import linregress\n"
            code += "        def get_slope(x):\n"
            code += "            mask = ~np.isnan(x)\n"
            code += "            if mask.sum() < 2: return np.nan\n"
            code += "            return linregress(np.arange(len(x))[mask], x[mask])[0]\n"
            code += f"        self.{name} = self.I(rolling_apply, get_slope, {src}, self.{name}_period)\n"
    elif t == "R-Squared":
        if USE_VECTORIZED:
            # SPEED: closed-form rolling R^2 (proven equal to scipy.linregress rvalue**2).
            code += f"        self.{name} = self.I(r2_vec, {src}, self.{name}_period)\n"
        else:
            code += "        from scipy.stats import linregress\n"
            code += "        def get_r2(x):\n"
            code += "            mask = ~np.isnan(x)\n"
            code += "            if mask.sum() < 2: return np.nan\n"
            code += "            return linregress(np.arange(len(x))[mask], x[mask])[2]**2\n"
            code += f"        self.{name} = self.I(rolling_apply, get_r2, {src}, self.{name}_period)\n"
    elif t == "ATR-Normalized Angle":
        code += f"        atr_{name} = ta.volatility.average_true_range(pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), self.{name}_period)\n"
        code += f"        def calc_angle_{name}(price, atr, n):\n"
        code += "            diff = pd.Series(price).diff(n)\n"
        code += "            norm = diff / atr.replace(0, np.nan)\n"
        code += "            return np.arctan(norm) * (180 / np.pi)\n"
        code += f"        self.{name} = self.I(calc_angle_{name}, {src}, atr_{name}, self.{name}_period)\n"
    elif t == "Angle of Indicator":
        code += f"        atr_{name} = ta.volatility.average_true_range(pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), self.{name}_atr_period)\n"
        code += f"        def calc_indangle_{name}(indicator_vals, atr_vals, n):\n"
        code += "            s = pd.Series(indicator_vals)\n"
        code += "            a = pd.Series(atr_vals)\n"
        code += "            diff = s.diff(n)\n"
        code += "            norm = diff / a.replace(0, np.nan)\n"
        code += "            return np.arctan(norm) * (180 / np.pi)\n"
        code += f"        self.{name} = self.I(calc_indangle_{name}, {src}, atr_{name}, self.{name}_period)\n"
    elif t == "Supertrend":
        code += f"        def get_supertrend_{name}(high, low, close, length, multiplier):\n"
        code += f"            df = pta.supertrend(pd.Series(high), pd.Series(low), pd.Series(close), length=int(length), multiplier=float(multiplier))\n"
        code += "            return df.iloc[:, 0].values, df.iloc[:, 1].values\n"
        code += f"        st_res_{name} = self.I(get_supertrend_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_period, self.{name}_multiplier)\n"
        code += f"        self.{name}_trend = st_res_{name}[0]\n"
        code += f"        self.{name}_dir = st_res_{name}[1]\n"
        src_map[f"{raw_name}_trend"] = f"self.{name}_trend"
        src_map[f"{raw_name}_dir"] = f"self.{name}_dir"
    elif t == "Parabolic SAR":
        code += f"        def get_psar_{name}(high, low, close, af, max_af):\n"
        code += f"            df = pta.psar(pd.Series(high), pd.Series(low), pd.Series(close), af=float(af), max_af=float(max_af))\n"
        code += "            return df.iloc[:,0].fillna(df.iloc[:,1]).values\n"
        code += f"        self.{name} = self.I(get_psar_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_af, self.{name}_max_af)\n"
    elif t == "ADX":
        code += f"        def get_adx_{name}(high, low, close, length):\n"
        code += f"            df = pta.adx(pd.Series(high), pd.Series(low), pd.Series(close), length=int(length))\n"
        code += "            return df.iloc[:,0].values\n"
        code += f"        self.{name} = self.I(get_adx_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_period)\n"
    elif t == "Stochastic Oscillator":
        code += f"        def get_stoch_{name}(high, low, close, k, d):\n"
        code += f"            df = pta.stoch(pd.Series(high), pd.Series(low), pd.Series(close), k=int(k), d=int(d))\n"
        code += "            return df.iloc[:,0].values, df.iloc[:,1].values\n"
        code += f"        stoch_res_{name} = self.I(get_stoch_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_k_period, self.{name}_d_period)\n"
        code += f"        self.{name}_k = stoch_res_{name}[0]\n"
        code += f"        self.{name}_d = stoch_res_{name}[1]\n"
        src_map[f"{raw_name}_k"] = f"self.{name}_k"
        src_map[f"{raw_name}_d"] = f"self.{name}_d"
    elif t == "VWAP":
        code += f"        def get_vwap_{name}(high, low, close, volume, index_arr):\n"
        code += "            idx = pd.to_datetime(index_arr)\n"
        code += f"            return pta.vwap(pd.Series(high, index=idx), pd.Series(low, index=idx), pd.Series(close, index=idx), pd.Series(volume, index=idx)).values\n"
        code += f"        self.{name} = self.I(get_vwap_{name}, self.data.High, self.data.Low, self.data.Close, self.data.Volume, self.data.index)\n"
    elif t == "Keltner Channels":
        code += f"        def get_kc_{name}(high, low, close, length, scalar):\n"
        code += f"            df = pta.kc(pd.Series(high), pd.Series(low), pd.Series(close), length=int(length), scalar=float(scalar))\n"
        code += "            return df.iloc[:,2].values, df.iloc[:,1].values, df.iloc[:,0].values\n"
        code += f"        kc_res_{name} = self.I(get_kc_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_period, self.{name}_multiplier)\n"
        code += f"        self.{name}_upper = kc_res_{name}[0]\n"
        code += f"        self.{name}_mid = kc_res_{name}[1]\n"
        code += f"        self.{name}_lower = kc_res_{name}[2]\n"
        src_map[f"{raw_name}_upper"] = f"self.{name}_upper"
        src_map[f"{raw_name}_mid"] = f"self.{name}_mid"
        src_map[f"{raw_name}_lower"] = f"self.{name}_lower"
    elif t == "Donchian Channels":
        code += f"        def get_donchian_{name}(high, low, length):\n"
        code += f"            df = pta.donchian(pd.Series(high), pd.Series(low), lower_length=int(length), upper_length=int(length))\n"
        code += "            return df.iloc[:,2].values, df.iloc[:,1].values, df.iloc[:,0].values\n"
        code += f"        don_res_{name} = self.I(get_donchian_{name}, self.data.High, self.data.Low, self.{name}_period)\n"
        code += f"        self.{name}_upper = don_res_{name}[0]\n"
        code += f"        self.{name}_mid = don_res_{name}[1]\n"
        code += f"        self.{name}_lower = don_res_{name}[2]\n"
        src_map[f"{raw_name}_upper"] = f"self.{name}_upper"
        src_map[f"{raw_name}_mid"] = f"self.{name}_mid"
        src_map[f"{raw_name}_lower"] = f"self.{name}_lower"
    elif t == "Hurst Exponent":
        code += f"        def get_hurst_{name}(series, window):\n"
        code += "            import numpy as np\n"
        code += "            import pandas as pd\n"
        code += "            from hurst import compute_Hc\n"
        code += "            def _calc(x):\n"
        code += "                try:\n"
        code += "                    H, c, data = compute_Hc(x, kind='price', simplified=True)\n"
        code += "                    return H\n"
        code += "                except:\n"
        code += "                    return np.nan\n"
        code += "            return pd.Series(series).rolling(int(window)).apply(_calc, raw=True).values\n"
        code += f"        self.{name} = self.I(get_hurst_{name}, {src}, self.{name}_period)\n"
    elif t == "HMM Regime":
        code += f"        def get_hmm_{name}(series, window, components):\n"
        code += "            import numpy as np\n"
        code += "            import pandas as pd\n"
        code += "            from hmmlearn.hmm import GaussianHMM\n"
        code += "            def _calc(x):\n"
        code += "                try:\n"
        code += "                    returns = np.diff(x) / x[:-1]\n"
        code += "                    if len(returns) < components * 2: return np.nan\n"
        code += "                    returns = np.where(np.isnan(returns), 0, returns).reshape(-1, 1)\n"
        code += "                    model = GaussianHMM(n_components=int(components), covariance_type='diag', n_iter=50, random_state=42)\n"
        code += "                    model.fit(returns)\n"
        code += "                    state = model.predict(returns[-1:])\n"
        code += "                    order = np.argsort(model.covars_.ravel())\n"
        code += "                    mapped_state = np.where(order == state[0])[0][0]\n"
        code += "                    return mapped_state\n"
        code += "                except:\n"
        code += "                    return np.nan\n"
        code += "            return pd.Series(series).rolling(int(window)).apply(_calc, raw=True).values\n"
        code += f"        self.{name} = self.I(get_hmm_{name}, {src}, self.{name}_period, self.{name}_components)\n"
    # --- v7 normalized features (vectorised helpers injected into the compile namespace) ---
    elif t == "Ratio to SMA":
        code += f"        self.{name} = self.I(ratio_to_sma_vec, {src}, self.{name}_period)\n"
    elif t == "Distance from MA (ATR)":
        code += f"        self.{name} = self.I(dist_from_ma_atr_vec, {src}, self.data.High, self.data.Low, self.data.Close, self.{name}_period, self.{name}_atr_period)\n"
    elif t == "MA Slope (ATR)":
        code += f"        self.{name} = self.I(ma_slope_atr_vec, {src}, self.data.High, self.data.Low, self.data.Close, self.{name}_period, self.{name}_lookback, self.{name}_atr_period)\n"
    elif t == "Z-Score":
        code += f"        self.{name} = self.I(zscore_vec, {src}, self.{name}_period)\n"
    elif t == "Percentile Rank":
        code += f"        self.{name} = self.I(pctile_rank_vec, {src}, self.{name}_period)\n"
    elif t == "Return (n-bar)":
        code += f"        self.{name} = self.I(ret_n_vec, {src}, self.{name}_period)\n"
    # --- v8.2 visual custom building blocks & safe formula ---
    elif t == "Series Math":
        params = ind.get("params", {}) or {}
        op_val = str(params.get("op", "add"))
        use_num = int(params.get("use_num", 0) or 0)
        if use_num:
            b_expr = f"self.{name}_num_val" if "num_val" in params else repr(float(params.get("num_val", 0.0)))
        else:
            rhs_col = ind.get("rhs", "Close") or "Close"
            b_expr = prior_src_map.get(rhs_col, f"self.{rhs_col}")
        code += f"        self.{name} = self.I(series_math_vec, {src}, {b_expr}, {op_val!r})\n"
    elif t == "Rolling Stat":
        params = ind.get("params", {}) or {}
        stat_val = str(params.get("stat", "max"))
        period_expr = f"self.{name}_period" if "period" in params else repr(int(params.get("period", 20)))
        code += f"        self.{name} = self.I(rolling_stat_vec, {src}, {period_expr}, {stat_val!r})\n"
    elif t == "Bar Shift":
        params = ind.get("params", {}) or {}
        shift_expr = f"self.{name}_shift" if "shift" in params else repr(int(params.get("shift", 1)))
        code += f"        self.{name} = self.I(shift_vec, {src}, {shift_expr})\n"
    elif t == "Custom Formula":
        formula = str(ind.get("formula", "Close") or "Close").strip()
        env_map: Dict[str, str] = {}
        for col_name, attr_expr in prior_src_map.items():
            if col_name.isidentifier():
                env_map[col_name] = attr_expr
            sname = safe_name(col_name)
            if sname and sname.isidentifier() and sname not in env_map:
                env_map[sname] = attr_expr
        assert_safe_formula(formula, allowed_vars=set(env_map))
        env_items = ", ".join(f"{k!r}: {v}" for k, v in env_map.items())
        code += f"        self.{name} = self.I(compute_custom_formula, {formula!r}, {{{env_items}}}, name={name!r})\n"
    return code


# --- vectorised reference helpers (validated in tests/test_indicators.py) ----
def lr_slope_vec(series, window: int) -> np.ndarray:
    """Rolling OLS slope over a fixed window (closed-form; matches scipy.linregress slope)."""
    x = pd.Series(series, dtype="float64")
    t = np.arange(window)
    t_mean = t.mean()
    denom = ((t - t_mean) ** 2).sum()
    sxx = denom

    def _slope(win):
        if np.isnan(win).any():
            mask = ~np.isnan(win)
            if mask.sum() < 2:
                return np.nan
            tt = np.arange(len(win))[mask]
            return np.polyfit(tt, win[mask], 1)[0]
        return (((t - t_mean) * (win - win.mean())).sum()) / sxx

    return x.rolling(window).apply(_slope, raw=True).values


def r2_vec(series, window: int) -> np.ndarray:
    """Rolling R^2 of an OLS fit vs a time index (matches scipy.linregress rvalue**2)."""
    x = pd.Series(series, dtype="float64")

    def _r2(win):
        mask = ~np.isnan(win)
        if mask.sum() < 2:
            return np.nan
        tt = np.arange(len(win))[mask]
        yy = win[mask]
        if np.all(yy == yy[0]):
            return np.nan  # zero variance -> linregress returns nan rvalue
        r = np.corrcoef(tt, yy)[0, 1]
        return r * r

    return x.rolling(window).apply(_r2, raw=True).values


def alma_vec(series, window: int, offset: float = 0.85, sigma: float = 6) -> np.ndarray:
    """ALMA via single convolution (fast equivalent of the per-window rolling apply)."""
    s = np.asarray(series, dtype="float64")
    m = offset * (window - 1)
    sd = window / sigma
    w = np.exp(-((np.arange(window) - m) ** 2) / (2 * sd ** 2))
    w /= w.sum()
    out = np.full_like(s, np.nan)
    if len(s) >= window:
        conv = np.convolve(s, w[::-1], mode="valid")
        out[window - 1:] = conv
    return out


# --- v7 normalized / cross-asset-comparable feature helpers ----------------------------------
# All return numpy arrays aligned to the input. They are injected into the compile namespace
# (see compiler._namespace) and called via self.I in generated strategy code. Designed so a
# learned rule (or a builder comparison) carries the SAME meaning on RELIANCE, NIFTY, crypto, etc.
def _ema(series, n):
    return ta.trend.ema_indicator(pd.Series(series, dtype="float64"), int(n))


def _atr(high, low, close, m):
    return ta.volatility.average_true_range(
        pd.Series(high, dtype="float64"), pd.Series(low, dtype="float64"),
        pd.Series(close, dtype="float64"), int(m),
    )


def ratio_to_sma_vec(series, n):
    """x / SMA(x, n). Volume source -> 'volume vs its 20-bar average'; >1 means above average."""
    s = pd.Series(series, dtype="float64")
    sma = ta.trend.sma_indicator(s, int(n))
    return (s / sma.replace(0, np.nan)).values


def dist_from_ma_atr_vec(series, high, low, close, n, m):
    """(x - EMA(x, n)) / ATR(m): how stretched from the mean, in volatility units (unit-free)."""
    ema = _ema(series, n)
    atr = _atr(high, low, close, m)
    return ((pd.Series(series, dtype="float64") - ema) / atr.replace(0, np.nan)).values


def ma_slope_atr_vec(series, high, low, close, n, k, m):
    """(EMA(x,n) - EMA(x,n)[t-k]) / (k * ATR(m)): trend slope per bar in ATR units."""
    ema = _ema(series, n)
    atr = _atr(high, low, close, m)
    return ((ema - ema.shift(int(k))) / (int(k) * atr.replace(0, np.nan))).values


def zscore_vec(series, N):
    """(x - rolling_mean_N) / rolling_std_N: standardized deviation, comparable across series."""
    s = pd.Series(series, dtype="float64")
    r = s.rolling(int(N))
    return ((s - r.mean()) / r.std(ddof=0).replace(0, np.nan)).values


def pctile_rank_vec(series, N):
    """Rolling percentile rank of the current bar within the last N bars, in [0, 1]."""
    return pd.Series(series, dtype="float64").rolling(int(N)).rank(pct=True).values


def ret_n_vec(series, n):
    """n-bar return: x / x[t-n] - 1 (scale-free)."""
    s = pd.Series(series, dtype="float64")
    return (s / s.shift(int(n)) - 1.0).values


# --- v8.2 visual custom building blocks & safe formula evaluator ----------------------------
_SERIES_MATH_OPS = frozenset({"add", "sub", "mul", "div", "pct_diff"})
_ROLLING_STATS = frozenset({"max", "min", "mean", "sum", "std"})

SAFE_FORMULA_FUNCS = frozenset({
    "sma", "ema", "wma", "rsi", "atr",
    "rolling_max", "rolling_min", "shift",
    "abs", "min", "max", "sqrt", "log",
})

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)
_ALLOWED_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_MAX_FORMULA_LEN = 2000
_MAX_FORMULA_DEPTH = 50


class FormulaValidationError(ValueError):
    """Raised when a Custom Formula expression fails AST whitelist validation."""


def series_math_vec(a, b_or_num, op_code: str = "add") -> np.ndarray:
    """Combine series ``a`` with series or scalar ``b_or_num`` via ``op_code``."""
    if op_code not in _SERIES_MATH_OPS:
        raise ValueError(f"Unsupported Series Math op {op_code!r}; expected one of {sorted(_SERIES_MATH_OPS)}")
    a_arr = np.asarray(a, dtype="float64")
    b_arr = np.asarray(b_or_num, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        if op_code == "add":
            return a_arr + b_arr
        if op_code == "sub":
            return a_arr - b_arr
        if op_code == "mul":
            return a_arr * b_arr
        if op_code == "div":
            return np.where(b_arr == 0, np.nan, a_arr / b_arr)
        return np.where(b_arr == 0, np.nan, (a_arr - b_arr) / b_arr * 100.0)


def rolling_stat_vec(series, period: int, stat: str = "max") -> np.ndarray:
    """Compute a rolling statistic (max/min/mean/sum/std) over ``period`` bars."""
    n = int(period)
    if n < 1:
        raise ValueError(f"Rolling Stat period must be >= 1, got {period!r}")
    if stat not in _ROLLING_STATS:
        raise ValueError(f"Unsupported Rolling Stat {stat!r}; expected one of {sorted(_ROLLING_STATS)}")
    r = pd.Series(series, dtype="float64").rolling(n)
    if stat == "std":
        return r.std(ddof=0).values
    return getattr(r, stat)().values


def shift_vec(series, shift: int = 1) -> np.ndarray:
    """Shift ``series`` backward by ``shift`` bars (no negative lookahead shifts)."""
    k = int(shift)
    if k < 0:
        raise ValueError(f"Bar Shift must be >= 0 (no lookahead), got {shift!r}")
    return pd.Series(series, dtype="float64").shift(k).values


def assert_safe_formula(
    expr: str,
    allowed_vars: Optional[Iterable[str]] = None,
) -> ast.Expression:
    """Validate ``expr`` against an expression-only AST whitelist; return parsed AST."""
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaValidationError("Custom Formula must be a non-empty expression string.")
    if len(expr) > _MAX_FORMULA_LEN:
        raise FormulaValidationError(f"Custom Formula exceeds max length of {_MAX_FORMULA_LEN} characters.")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise FormulaValidationError(f"Invalid Custom Formula syntax: {e}") from e

    allowed_set = set(allowed_vars) if allowed_vars is not None else None
    call_func_nodes: set[int] = set()

    def _walk(node: ast.AST, depth: int) -> None:
        if depth > _MAX_FORMULA_DEPTH:
            raise FormulaValidationError("Custom Formula nesting is too deep.")
        if isinstance(node, ast.Expression):
            _walk(node.body, depth + 1)
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise FormulaValidationError(f"Disallowed binary operator: {type(node.op).__name__}")
            _walk(node.left, depth + 1)
            _walk(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise FormulaValidationError(f"Disallowed unary operator: {type(node.op).__name__}")
            _walk(node.operand, depth + 1)
        elif isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, _ALLOWED_CMPOPS):
                    raise FormulaValidationError(f"Disallowed comparison operator: {type(op).__name__}")
            _walk(node.left, depth + 1)
            for comp in node.comparators:
                _walk(comp, depth + 1)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in SAFE_FORMULA_FUNCS:
                fn = node.func.id if isinstance(node.func, ast.Name) else type(node.func).__name__
                raise FormulaValidationError(f"Disallowed function call: {fn}")
            if node.keywords:
                raise FormulaValidationError("Keyword arguments are not allowed in Custom Formula calls.")
            call_func_nodes.add(id(node.func))
            for arg in node.args:
                if isinstance(arg, ast.Starred):
                    raise FormulaValidationError("Starred arguments are not allowed in Custom Formula calls.")
                _walk(arg, depth + 1)
        elif isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                raise FormulaValidationError("Only variable reads are allowed in Custom Formula.")
            if node.id.startswith("_"):
                raise FormulaValidationError(f"Disallowed identifier: {node.id}")
            if id(node) not in call_func_nodes:
                if allowed_set is not None and node.id not in allowed_set:
                    raise FormulaValidationError(f"Unknown variable {node.id!r} in Custom Formula.")
                if allowed_set is None and node.id in SAFE_FORMULA_FUNCS:
                    raise FormulaValidationError(f"Function {node.id!r} must be called with parentheses.")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)) or not math.isfinite(node.value):
                raise FormulaValidationError(f"Only finite numeric constants are allowed, got {node.value!r}")
        else:
            raise FormulaValidationError(f"Disallowed expression construct: {type(node).__name__}")

    _walk(tree, 0)
    return tree


def _as_int_period(val: Any, min_val: int = 1, name: str = "period") -> int:
    if isinstance(val, np.ndarray):
        if val.ndim > 0:
            raise FormulaValidationError(f"{name} must be a scalar integer, not a series.")
        val = val.item()
    n = int(val)
    if n < min_val:
        raise ValueError(f"{name} must be >= {min_val}, got {val!r}")
    return n


def _formula_call(fn_name: str, args: List[Any], env_arrs: Dict[str, np.ndarray]) -> Any:
    if fn_name in ("sma", "ema", "wma"):
        if len(args) != 2:
            raise FormulaValidationError(f"{fn_name}(series, period) expects 2 arguments, got {len(args)}")
        n = _as_int_period(args[1], 1, "period")
        s = pd.Series(args[0], dtype="float64")
        fn = {"sma": ta.trend.sma_indicator, "ema": ta.trend.ema_indicator, "wma": ta.trend.wma_indicator}[fn_name]
        return fn(s, n).values
    if fn_name == "rsi":
        if len(args) not in (1, 2):
            raise FormulaValidationError(f"rsi(series, period=14) expects 1 or 2 arguments, got {len(args)}")
        n = _as_int_period(args[1], 1, "period") if len(args) == 2 else 14
        return ta.momentum.rsi(pd.Series(args[0], dtype="float64"), n).values
    if fn_name == "atr":
        if len(args) == 0:
            n = 14
            h, l_, c = env_arrs.get("High"), env_arrs.get("Low"), env_arrs.get("Close")
        elif len(args) == 1:
            n = _as_int_period(args[0], 1, "period")
            h, l_, c = env_arrs.get("High"), env_arrs.get("Low"), env_arrs.get("Close")
        elif len(args) == 4:
            h, l_, c = args[0], args[1], args[2]
            n = _as_int_period(args[3], 1, "period")
        else:
            raise FormulaValidationError("atr expects atr(period) or atr(High, Low, Close, period)")
        if h is None or l_ is None or c is None:
            raise FormulaValidationError("atr() requires High, Low, Close series in environment")
        return _atr(h, l_, c, n).values
    if fn_name in ("rolling_max", "rolling_min"):
        if len(args) != 2:
            raise FormulaValidationError(f"{fn_name}(series, period) expects 2 arguments, got {len(args)}")
        stat = "max" if fn_name == "rolling_max" else "min"
        return rolling_stat_vec(args[0], _as_int_period(args[1], 1, "period"), stat)
    if fn_name == "shift":
        if len(args) not in (1, 2):
            raise FormulaValidationError(f"shift(series, bars=1) expects 1 or 2 arguments, got {len(args)}")
        k = _as_int_period(args[1], 0, "shift") if len(args) == 2 else 1
        return shift_vec(args[0], k)
    if fn_name == "abs":
        if len(args) != 1:
            raise FormulaValidationError(f"abs(x) expects 1 argument, got {len(args)}")
        return np.abs(np.asarray(args[0], dtype="float64"))
    if fn_name in ("min", "max"):
        if len(args) < 1:
            raise FormulaValidationError(f"{fn_name} expects at least 1 argument")
        if len(args) == 1:
            arr = np.asarray(args[0], dtype="float64")
            return float(np.nanmin(arr)) if fn_name == "min" else float(np.nanmax(arr))
        acc = np.asarray(args[0], dtype="float64")
        ufunc = np.minimum if fn_name == "min" else np.maximum
        for extra in args[1:]:
            acc = ufunc(acc, np.asarray(extra, dtype="float64"))
        return acc
    if fn_name == "sqrt":
        if len(args) != 1:
            raise FormulaValidationError(f"sqrt(x) expects 1 argument, got {len(args)}")
        arr = np.asarray(args[0], dtype="float64")
        with np.errstate(invalid="ignore"):
            return np.where(arr < 0, np.nan, np.sqrt(arr))
    if fn_name == "log":
        if len(args) != 1:
            raise FormulaValidationError(f"log(x) expects 1 argument, got {len(args)}")
        arr = np.asarray(args[0], dtype="float64")
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(arr <= 0, np.nan, np.log(arr))
    raise FormulaValidationError(f"Unsupported function: {fn_name}")


def compute_custom_formula(expr: str, env_dict: Dict[str, Any]) -> np.ndarray:
    """Safely evaluate a validated single-line AST formula over ``env_dict`` series."""
    tree = assert_safe_formula(expr, allowed_vars=set(env_dict))
    env_arrs: Dict[str, np.ndarray] = {
        k: np.asarray(v, dtype="float64") for k, v in env_dict.items()
    }
    ref_len = next((arr.shape[-1] for arr in env_arrs.values() if arr.ndim >= 1), 0)

    def _eval(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return env_arrs[node.id]
        if isinstance(node, ast.UnaryOp):
            val = _eval(node.operand)
            return val if isinstance(node.op, ast.UAdd) else -val
        if isinstance(node, ast.BinOp):
            lhs = np.asarray(_eval(node.left), dtype="float64")
            rhs = np.asarray(_eval(node.right), dtype="float64")
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                if isinstance(node.op, ast.Add):
                    return lhs + rhs
                if isinstance(node.op, ast.Sub):
                    return lhs - rhs
                if isinstance(node.op, ast.Mult):
                    return lhs * rhs
                if isinstance(node.op, ast.Div):
                    return np.where(rhs == 0, np.nan, lhs / rhs)
                if isinstance(node.op, ast.FloorDiv):
                    return np.where(rhs == 0, np.nan, np.floor_divide(lhs, rhs))
                if isinstance(node.op, ast.Mod):
                    return np.where(rhs == 0, np.nan, np.remainder(lhs, rhs))
                if isinstance(node.op, ast.Pow):
                    return np.power(lhs, rhs)
        if isinstance(node, ast.Compare):
            cur = _eval(node.left)
            acc = None
            for op, comp in zip(node.ops, node.comparators):
                nxt = _eval(comp)
                if isinstance(op, ast.Eq):
                    res = cur == nxt
                elif isinstance(op, ast.NotEq):
                    res = cur != nxt
                elif isinstance(op, ast.Lt):
                    res = cur < nxt
                elif isinstance(op, ast.LtE):
                    res = cur <= nxt
                elif isinstance(op, ast.Gt):
                    res = cur > nxt
                else:
                    res = cur >= nxt
                acc = res if acc is None else (acc & res)
                cur = nxt
            return np.asarray(acc, dtype="float64")
        if isinstance(node, ast.Call):
            args = [_eval(a) for a in node.args]
            return _formula_call(node.func.id, args, env_arrs)
        raise FormulaValidationError(f"Disallowed AST node: {type(node).__name__}")

    out = np.asarray(_eval(tree.body), dtype="float64")
    if out.ndim == 0:
        return np.full(ref_len, float(out), dtype="float64")
    return out
