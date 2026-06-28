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


# Columns that are never plotted as indicator lines (price/signal/non-indicator series).
_SKIP_COLS = {
    "Open", "High", "Low", "Close", "Volume", "Buy_Signal", "Sell_Signal",
    "OI", "Dividend", "Rights_Premium",
}
_CANDLE_MAX = 8000  # downsample candles (SVG) beyond this many bars for responsiveness


def render_plotly_backtest(out_df: pd.DataFrame, trades, indicators=None) -> go.Figure:
    """Interactive multi-panel backtest chart.

    Redesign (chart-messiness fix): price-scale indicators (SMA/EMA/Bollinger/Supertrend/…) are
    overlaid on the candlestick; own-scale indicators (RSI/ADX/Stochastic/MACD/ATR/…) get their
    OWN stacked sub-panels with guide lines, instead of being crushed onto the price axis. Signal
    markers use a range-scaled offset, indicator/marker traces use WebGL (Scattergl), candles are
    downsampled on very long series, and intraday gaps (overnight/weekend) are collapsed.

    ``indicators`` is the strategy's indicator config list; when omitted, every extra column is
    treated as a price overlay (legacy behaviour).
    """
    from quantdesk.config import INDICATOR_PANEL_GUIDES
    from quantdesk.strategy.models import classify_columns

    cls = classify_columns(indicators) if indicators else {}

    overlay_cols, panels = [], {}  # panels: group_name -> {"type": str, "cols": [str]}
    for col in out_df.columns:
        if col in _SKIP_COLS:
            continue
        info = cls.get(col)
        if info and info["placement"] == "panel":
            grp = panels.setdefault(info["group"], {"type": info["type"], "cols": []})
            grp["cols"].append(col)
        else:
            overlay_cols.append(col)  # overlay or unclassified -> price axis

    panel_names = list(panels.keys())
    n_panels = len(panel_names)
    has_vol = "Volume" in out_df.columns

    rows = 1 + n_panels + (1 if has_vol else 0)
    weights = [3.2] + [1.25] * n_panels + ([0.9] if has_vol else [])
    total = sum(weights)
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=min(0.03, 0.6 / max(rows, 1)),
        row_heights=[w / total for w in weights],
    )
    price_row = 1
    panel_row = {name: 2 + i for i, name in enumerate(panel_names)}
    vol_row = rows if has_vol else None

    # Downsample only the (SVG) candles for very long series; markers/indicators stay full-res
    # on the same time axis so they still align.
    n = len(out_df)
    cdf = out_df.iloc[:: max(1, n // _CANDLE_MAX)] if n > _CANDLE_MAX else out_df
    fig.add_trace(
        go.Candlestick(
            x=cdf.index, open=cdf["Open"], high=cdf["High"], low=cdf["Low"], close=cdf["Close"],
            name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ),
        row=price_row, col=1,
    )

    for col in overlay_cols:
        fig.add_trace(
            go.Scattergl(x=out_df.index, y=out_df[col], mode="lines", name=col, line=dict(width=1)),
            row=price_row, col=1,
        )

    if "Buy_Signal" in out_df.columns:
        rng = float((out_df["High"] - out_df["Low"]).median() or 0.0)
        off = rng * 0.8
        buys = out_df[out_df["Buy_Signal"] == 1]
        sells = out_df[out_df["Sell_Signal"] == 1]
        if not buys.empty:
            fig.add_trace(
                go.Scattergl(
                    x=buys.index, y=buys["Low"] - off, mode="markers",
                    marker=dict(symbol="triangle-up", size=10, color="#00e676", line=dict(width=1, color="black")),
                    name="Buy",
                ), row=price_row, col=1,
            )
        if not sells.empty:
            fig.add_trace(
                go.Scattergl(
                    x=sells.index, y=sells["High"] + off, mode="markers",
                    marker=dict(symbol="triangle-down", size=10, color="#ff5252", line=dict(width=1, color="black")),
                    name="Sell",
                ), row=price_row, col=1,
            )

    for name in panel_names:
        grp = panels[name]
        r = panel_row[name]
        for col in grp["cols"]:
            fig.add_trace(
                go.Scattergl(x=out_df.index, y=out_df[col], mode="lines", name=col, line=dict(width=1)),
                row=r, col=1,
            )
        for gy in INDICATOR_PANEL_GUIDES.get(grp["type"], []):
            fig.add_hline(y=gy, row=r, col=1, line_dash="dot", line_color="gray", opacity=0.4)
        fig.update_yaxes(title_text=name, row=r, col=1, title_font=dict(size=10))

    if has_vol:
        colors = np.where(out_df["Close"] >= out_df["Open"], "#26a69a", "#ef5350")
        fig.add_trace(
            go.Bar(x=out_df.index, y=out_df["Volume"], marker_color=colors, name="Volume", showlegend=False),
            row=vol_row, col=1,
        )
        fig.update_yaxes(title_text="Vol", row=vol_row, col=1, title_font=dict(size=10))

    # Collapse non-trading gaps for intraday data (keeps candles dense, not strung across nights).
    try:
        deltas = pd.Series(out_df.index).diff().dropna()
        median_min = (deltas.dt.total_seconds().median() / 60) if len(deltas) else 1440
        if median_min < 1440:
            fig.update_xaxes(rangebreaks=[
                dict(bounds=["sat", "mon"]),
                dict(bounds=[15.5, 9.25], pattern="hour"),
            ])
    except Exception:
        pass

    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(title_text="Price", row=price_row, col=1)
    fig.update_layout(
        title="Strategy Backtest Visualization",
        height=520 + 170 * (n_panels + (1 if has_vol else 0)),
        margin=dict(l=20, r=20, t=50, b=20),
        template="plotly_dark",
        hovermode="x",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )
    return fig


def equity_overlay(curves: dict) -> go.Figure:
    """Overlay multiple daily-equity curves (label -> pd.Series) for the Results leaderboard."""
    fig = go.Figure()
    for label, series in curves.items():
        if series is None or len(series) == 0:
            continue
        fig.add_trace(go.Scatter(x=list(series.index), y=list(series.values), mode="lines", name=label))
    fig.update_layout(
        title="Equity curves (compared runs)",
        template="plotly_dark", height=420, margin=dict(l=20, r=20, t=40, b=20),
        hovermode="x unified", yaxis_title="Equity (₹)",
    )
    return fig


# --- v7 Phase C: optimizer-validation charts -------------------------------------------------
def parameter_stability_heatmap(heatmap: pd.Series, metric: str = "Objective") -> go.Figure:
    """Parameter-stability surface from a grid ``return_heatmap`` (MultiIndex Series of param->metric).

    1 param -> a line (is the optimum a plateau or a lone spike?); 2 params -> a 2-D heatmap;
    >2 params -> the top combinations as a bar. A robust optimum is a broad high region, not a peak.
    """
    names = list(heatmap.index.names)
    if len(names) == 1:
        s = heatmap.sort_index()
        fig = go.Figure(go.Scatter(x=list(s.index), y=list(s.values), mode="lines+markers",
                                   line=dict(color="#26a69a")))
        fig.update_layout(xaxis_title=names[0], yaxis_title=metric)
    elif len(names) == 2:
        grid = heatmap.unstack(level=-1)
        fig = go.Figure(go.Heatmap(
            z=grid.values, x=[str(c) for c in grid.columns], y=[str(r) for r in grid.index],
            colorscale="Viridis", colorbar=dict(title=metric),
        ))
        fig.update_layout(xaxis_title=names[1], yaxis_title=names[0])
    else:
        top = heatmap.sort_values(ascending=False).head(20)
        fig = go.Figure(go.Bar(x=[str(i) for i in top.index], y=list(top.values),
                               marker=dict(color="#26a69a")))
        fig.update_layout(xaxis_title="Parameter combination", yaxis_title=metric)
    fig.update_layout(title=f"Parameter stability ({metric})", template="plotly_dark",
                      height=420, margin=dict(l=20, r=20, t=50, b=20))
    return fig


def monte_carlo_fan(bands: pd.DataFrame) -> go.Figure:
    """Fan chart from a Monte-Carlo ``bands`` DataFrame (index=trade #, cols p5/p50/p95/realized)."""
    x = list(bands.index)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=list(bands["p95"]), mode="lines", line=dict(width=0),
                             name="P95", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=list(bands["p5"]), mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(38,166,154,0.20)", name="P5–P95"))
    fig.add_trace(go.Scatter(x=x, y=list(bands["p50"]), mode="lines",
                             line=dict(color="#26a69a", dash="dash"), name="Median (P50)"))
    if "realized" in bands.columns:
        fig.add_trace(go.Scatter(x=x, y=list(bands["realized"]), mode="lines",
                                 line=dict(color="#ffb74d", width=2), name="Realized"))
    fig.update_layout(title="Monte-Carlo equity bands (trade-order bootstrap)",
                      template="plotly_dark", height=420, margin=dict(l=20, r=20, t=50, b=20),
                      xaxis_title="Trade #", yaxis_title="Equity (₹)", hovermode="x unified")
    return fig


def walk_forward_equity(stitched: pd.Series) -> go.Figure:
    """Stitched out-of-sample equity curve from walk-forward analysis."""
    fig = go.Figure(go.Scatter(x=list(stitched.index), y=list(stitched.values), mode="lines",
                               line=dict(color="#26a69a"), name="Stitched OOS equity"))
    fig.update_layout(title="Walk-forward — stitched out-of-sample equity",
                      template="plotly_dark", height=400, margin=dict(l=20, r=20, t=50, b=20),
                      yaxis_title="Equity (₹)", hovermode="x")
    return fig


# --- QuantStats (optional dependency, guarded per CLAUDE.md Section 11) -------
try:
    import quantstats as _qs  # noqa: N813
    QUANTSTATS_AVAILABLE = True
except Exception:  # noqa: BLE001
    QUANTSTATS_AVAILABLE = False


def quantstats_tearsheet_html(returns: pd.Series, benchmark: pd.Series | None = None) -> str | None:
    """Full QuantStats HTML tearsheet for a (daily) returns series, or None if unavailable/empty."""
    if not QUANTSTATS_AVAILABLE or returns is None or len(returns) < 2:
        return None
    import io

    buf = io.StringIO()
    try:
        _qs.reports.html(returns, benchmark=benchmark, output=buf, title="Strategy Tearsheet")
        return buf.getvalue()
    except Exception:
        try:
            _qs.reports.html(returns, output=buf, title="Strategy Tearsheet")
            return buf.getvalue()
        except Exception:
            return None


def quantstats_metrics(returns: pd.Series) -> dict:
    """A curated risk block QuantStats can produce from a daily returns series."""
    if not QUANTSTATS_AVAILABLE or returns is None or len(returns) < 2:
        return {}
    s = _qs.stats
    out = {}
    try:
        out["VaR (95%)"] = round(float(s.value_at_risk(returns)) * 100, 2)
        out["CVaR (95%)"] = round(float(s.cvar(returns)) * 100, 2)
        out["Volatility (ann.)"] = round(float(s.volatility(returns)) * 100, 2)
        out["Kelly"] = round(float(s.kelly_criterion(returns)), 3)
        out["Tail ratio"] = round(float(s.tail_ratio(returns)), 2)
    except Exception:
        pass
    return out


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
