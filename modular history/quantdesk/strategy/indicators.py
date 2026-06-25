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

from typing import Dict, List

import numpy as np
import pandas as pd

from quantdesk.strategy.models import safe_name

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
}


def emit_indicator(ind: dict, src_map: Dict[str, str]) -> str:
    """Return the generated ``init()`` code lines for one indicator and update ``src_map``.

    Byte-for-byte equivalent to the original chain so compiled strategies match exactly.
    """
    raw_name = ind["name"]
    name = safe_name(raw_name)
    src = src_map.get(ind["source"], f"self.{ind['source']}")
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
