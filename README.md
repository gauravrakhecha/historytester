# Unified Market Data Downloader & No-Code Backtester

An all-in-one financial workstation: download historical market data across multiple Indian
brokers (Upstox, Zerodha, Dhan) and build + backtest + optimise + ML-mine algorithmic trading
strategies — **without writing a line of code**.

> **Two editions in this repo:**
> - **Modular Edition** (recommended, **latest = v7**): a fast, modular, tested package under
>   [`modular history/`](modular%20history/). v4 refactored the monolith 1:1; **v5** added honest
>   out-of-sample validation, real India charges, a data-quality gate and QuantStats; **v6** is an
>   audit-hardening + speed + CSV/Parquet-saving release; **v7 "Feature Maturity"** makes the analytics
>   trustworthy — normalized cross-asset features, an ML pattern finder with out-of-sample + significance
>   gating, and optimizer validation (Probabilistic/Deflated Sharpe, parameter-stability heatmap,
>   walk-forward, Monte-Carlo bands). Start here for new work.
> - **v3 — Single-file** (`upstox_app.py`): the original 3,207-line app, retained as reference.
>
> The v4 refactor was feature-for-feature identical to v3 (numeric parity harness); v5/v6/v7 then make
> every reported number trustworthy and the app fast. See the [Changelog](CHANGELOG.md) and
> [`modular history/ROADMAP_v7.md`](modular%20history/ROADMAP_v7.md).

---

## 🚀 Quick start

### v4 (Modular Edition)
```bat
cd "modular history"
install.bat     :: one-time: creates an isolated .venv (uv) + installs requirements
run.bat         :: launches the dashboard in your browser
```

### v3 (Single-file)
```bat
install.bat
run.bat
```

Both open a Streamlit dashboard at `http://localhost:8501`.

---

## ✨ Features (both editions)

**Data**
- Multi-broker async downloader (Upstox / Zerodha / Dhan) — fast chunked, RAM-safe.
- Master / Single / Bulk downloaders, NIFTY 50 & BANK presets, saved symbol templates.
- Options-chain historical data (Upstox), with strike-window selection + timeframe extraction.
- Corporate-actions sanitisation: splits/bonus back-adjustment, dividends, rights (TERP).
- Local timeframe converter (1-min → any TF), download logs, backup/restore.

**Strategy & backtest**
- No-code Strategy Builder: 20+ indicators, AND/OR buy/sell rules, regime (no-trade) filter,
  advanced range zones, full risk management (SL/TP, trailing, long/short, position sizing).
- Backtester on `backtesting.py` with India-style commission + slippage, year-wise performance,
  enriched trade log, interactive Plotly charts.
- Optimizer: **Optuna** (Bayesian) and **Grid** search.
- Backtest results database + ML Pattern Finder (XGBoost importance + decision-tree rules).

---

## 🆕 What's new in v6 — *Audit Hardening, Speed & Multi-Format Saving*

- **Faster UI** — tab switches and search no longer rescan the 141k-row instrument list every
  keystroke; the master list is a shared singleton + 24h disk cache (startup ~10s → ~0.8s).
- **Audit fixes** — a multi-agent audit caught and fixed real bugs: ML Pattern Finder crash,
  short trailing-stop never ratcheting (+ a latent crash), double-counted charges, Optuna integer
  params, and presets dropping position sizing.
- **Corporate-actions download crash** fixed (int64 Volume on pandas ≥ 3).
- **CSV + Parquet saving** — choose Parquet / CSV / **Both** in Settings, with an optional
  separate CSV folder; each download page now has both a Parquet and a CSV folder input.
- **Chart redesign (v6.1)** — oscillators (RSI/ADX/Stochastic/MACD/…) now render in their **own
  stacked panels** with guide lines instead of being crushed onto the price axis; price-scale
  indicators stay overlaid. Faster (WebGL, candle downsampling, intraday gap-collapse) and an
  optional engine-native **Bokeh "Pro chart"** toggle.
- **41 passing tests** (parity still byte-exact).

## 🆕 What's new in v5 — *Honest Numbers*

- **Out-of-sample validation** — the optimizer tunes on 70% and reports the honest out-of-sample
  result on unseen data, with an IS-vs-OOS comparison + overfitting warning.
- **Real India charges** — Decimal STT/stamp/exchange/SEBI/brokerage/18% GST per segment, shown as
  a Gross-vs-Net P&L panel.
- **Data-quality gate** enforced before every backtest (warn/block).
- **QuantStats** institutional tearsheet + VaR/CVaR/Kelly risk block (HTML export).
- **Surfaced metrics** (Profit Factor, Expectancy, SQN, Buy & Hold) + a **Results leaderboard**
  with overlaid equity curves and plain-language 🟢🟡🔴 verdicts.
- **Fixes** — intrabar trailing stop (and a latent crash), dividends carried through resampling,
  per-session resample anchoring, reproducibility hashes on every run.

Full feature-gap analysis & roadmap: [`modular history/ROADMAP_v5.md`](modular%20history/ROADMAP_v5.md).

## 🆕 What's new in v4

- **Modular architecture** — 52 small modules instead of one 3,207-line file. See
  [`modular history/ARCHITECTURE.md`](modular%20history/ARCHITECTURE.md).
- **Superfast** — vectorised slow indicators (ALMA, Linear-Regression Slope, R-Squared; proven
  numerically identical), bytecode-cached strategy compilation, cached settings/master-list/data,
  vectorised trade-log enrichment.
- **Fixes** — multi-output indicators (Supertrend/Stochastic/Keltner/Donchian) are now selectable
  on the Plotter & ML pages; recovery scan handles both filename formats; optimizer "Equity Final"
  metric fixed; cleaner stats rendering.
- **More accessible** — local tools (backtester, builder, settings, converter, results, ML) work
  **without** a broker login; only the live downloaders/options need authentication.
- **Tested** — unit tests, a numeric parity harness, and Streamlit AppTest UI drivers
  (`modular history/tests/`). Build/verification is tracked file-by-file in
  [`modular history/modular.json`](modular%20history/modular.json).

---

## ⚙️ Configuration

- Broker API keys go in a local `.env` (never committed). Example:
  ```env
  UPSTOX_API_KEY=your_key
  UPSTOX_API_SECRET=your_secret
  UPSTOX_REDIRECT_URI=http://localhost:8501
  ```
- Commission, slippage, capital and data paths are editable in the **Settings** page.

---

## ✅ Verify (v4)

```bat
cd "modular history"
python -m pytest tests/test_units.py tests/test_parity.py -q
python tests/verify_app.py        :: renders every page + runs a backtest via Streamlit AppTest
```

---

## 📚 Docs
- [Changelog](CHANGELOG.md)
- [v4 app README](modular%20history/README.md)
- [v4 Architecture](modular%20history/ARCHITECTURE.md)
- `Upstox Data Downloader - User Guide.pdf`

---

*Disclaimer: for backtesting and research only. Always paper-trade before deploying real capital.*
