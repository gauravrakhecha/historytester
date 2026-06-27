"""Chart redesign tests: oscillators get their own panels; overlays stay on price."""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdesk.strategy.models import classify_columns  # noqa: E402
from quantdesk.viz.charts import render_plotly_backtest  # noqa: E402

INDS = [
    {"name": "EMA20", "type": "EMA", "source": "Close", "params": {"period": 20}},
    {"name": "BB", "type": "Bollinger Bands", "source": "Close", "params": {"period": 20, "std_dev": 2.0}},
    {"name": "RSI1", "type": "RSI", "source": "Close", "params": {"period": 14}},
    {"name": "MACD1", "type": "MACD", "source": "Close", "params": {"fast": 12, "slow": 26, "sign": 9}},
]


def test_classify_columns_overlay_vs_panel():
    c = classify_columns(INDS)
    assert c["EMA20"]["placement"] == "overlay"
    assert c["BB_high"]["placement"] == "overlay" and c["BB_low"]["placement"] == "overlay"
    assert c["RSI1"]["placement"] == "panel"
    assert c["MACD1_line"]["placement"] == "panel" and c["MACD1_signal"]["group"] == "MACD1"


def _out_df(n=300):
    idx = pd.date_range("2023-01-02 09:15", periods=n, freq="15min")
    base = 2900 + np.cumsum(np.random.default_rng(1).normal(0, 5, n))
    df = pd.DataFrame({
        "Open": base, "High": base + 8, "Low": base - 8, "Close": base + 1,
        "Volume": np.random.default_rng(2).integers(1000, 9000, n),
        "EMA20": base, "BB_high": base + 20, "BB_low": base - 20,   # overlays (price scale)
        "RSI1": np.random.default_rng(3).uniform(20, 80, n),          # panel (0-100)
        "MACD1_line": np.random.default_rng(4).normal(0, 2, n),       # panel (~0)
        "MACD1_signal": np.random.default_rng(5).normal(0, 2, n),
        "Buy_Signal": 0, "Sell_Signal": 0,
    }, index=idx)
    df.iloc[10, df.columns.get_loc("Buy_Signal")] = 1
    df.iloc[40, df.columns.get_loc("Sell_Signal")] = 1
    return df


def test_render_separates_oscillators_into_panels():
    fig = render_plotly_backtest(_out_df(), trades=None, indicators=INDS)
    # rows = price(1) + RSI panel + MACD panel + volume = 4 -> yaxis..yaxis4 exist, yaxis5 doesn't
    layout = fig.layout
    assert "yaxis4" in layout and "yaxis5" not in layout, "expected exactly 4 stacked panels"

    def axis_of(name):
        for t in fig.data:
            if t.name == name:
                return t.yaxis or "y"
        return None

    price_axis = next((t.yaxis or "y") for t in fig.data if t.type == "candlestick")
    assert price_axis == "y"
    # overlays share the price axis; oscillators are on different (panel) axes
    assert axis_of("EMA20") == "y"
    assert axis_of("BB_high") == "y"
    assert axis_of("RSI1") not in ("y", None)
    assert axis_of("MACD1_line") not in ("y", None)
    # RSI and MACD are in DIFFERENT panels
    assert axis_of("RSI1") != axis_of("MACD1_line")


def test_render_without_indicators_is_legacy_safe():
    # No indicators passed -> extra columns treated as overlays, single price + volume layout.
    df = _out_df()
    fig = render_plotly_backtest(df, trades=None)
    assert "yaxis2" in fig.layout  # price + volume
    assert any(t.type == "candlestick" for t in fig.data)


def test_native_bokeh_html_renders():
    """Step 6: engine-native Bokeh 'Pro chart' produces standalone HTML."""
    from quantdesk.backtest import engine
    from quantdesk.data.loader import load_indexed
    from quantdesk.strategy.codegen import build_strategy_code
    from quantdesk.strategy.models import StrategyConfig

    cfg = StrategyConfig(
        indicators=[{"name": "EMA_f", "type": "EMA", "source": "Close", "params": {"period": 10}}],
        buy_rules=[{"logic": "IF", "var1": "Close", "operator": ">", "var2": "EMA_f", "is_num": False}],
        sell_rules=[{"logic": "IF", "var1": "Close", "operator": "<", "var2": "EMA_f", "is_num": False}],
    )
    df = load_indexed(r"D:\simpleton\historydownloader\data\RELIANCE\RELIANCE_15Min_2022-06-01_to_2026-06-18.csv").iloc[-1500:]
    html = engine.native_bokeh_html(df, build_strategy_code(cfg), 100000, 0.0005, 1.0)
    assert len(html) > 1000 and "bokeh" in html.lower()
