"""
quantdesk.viz.charts — Plotly chart helpers (Streamlit-free; return Figures).

``render_plotly_backtest`` is a verbatim port of upstox_app.py:70-121 (candles + dynamic
indicator overlays + buy/sell markers + volume). ``feature_importance_bar`` reproduces the
XGBoost importance bar chart (upstox_app.py:3121-3127) for reuse by the ML page.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def render_plotly_backtest(out_df: pd.DataFrame, trades) -> go.Figure:
    """Interactive candlestick + indicators + signals + volume figure."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.8, 0.2]
    )

    fig.add_trace(
        go.Candlestick(
            x=out_df.index,
            open=out_df["Open"], high=out_df["High"],
            low=out_df["Low"], close=out_df["Close"],
            name="Price",
        ),
        row=1, col=1,
    )

    for col in out_df.columns:
        if col not in ["Open", "High", "Low", "Close", "Volume", "Buy_Signal", "Sell_Signal"]:
            fig.add_trace(
                go.Scatter(x=out_df.index, y=out_df[col], mode="lines", name=col), row=1, col=1
            )

    buys = out_df[out_df["Buy_Signal"] == 1] if "Buy_Signal" in out_df.columns else pd.DataFrame()
    sells = out_df[out_df["Sell_Signal"] == 1] if "Sell_Signal" in out_df.columns else pd.DataFrame()

    if not buys.empty:
        fig.add_trace(
            go.Scatter(
                x=buys.index, y=buys["Low"] * 0.99,
                mode="markers",
                marker=dict(symbol="triangle-up", size=12, color="#00ff00", line=dict(width=1, color="black")),
                name="Buy Signal",
            ),
            row=1, col=1,
        )

    if not sells.empty:
        fig.add_trace(
            go.Scatter(
                x=sells.index, y=sells["High"] * 1.01,
                mode="markers",
                marker=dict(symbol="triangle-down", size=12, color="#ff0000", line=dict(width=1, color="black")),
                name="Sell Signal",
            ),
            row=1, col=1,
        )

    colors = np.where(out_df["Close"] >= out_df["Open"], "#26a69a", "#ef5350")
    fig.add_trace(
        go.Bar(x=out_df.index, y=out_df["Volume"], marker_color=colors, name="Volume"),
        row=2, col=1,
    )

    fig.update_layout(
        title="Strategy Backtest Visualization",
        xaxis_rangeslider_visible=False,
        height=750,
        margin=dict(l=20, r=20, t=50, b=20),
        template="plotly_dark",
        hovermode="x unified",
    )
    return fig


def feature_importance_bar(feat_imp_df: pd.DataFrame) -> go.Figure:
    """Horizontal XGBoost feature-importance bar chart."""
    fig = go.Figure(
        go.Bar(
            x=feat_imp_df["Importance"],
            y=feat_imp_df["Feature"].str.replace("Entry_", ""),
            orientation="h",
            marker=dict(color="#26a69a"),
        )
    )
    fig.update_layout(
        title="XGBoost Feature Importance",
        template="plotly_dark",
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig
