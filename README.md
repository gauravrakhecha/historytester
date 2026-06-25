# Unified Market Data Downloader & No-Code Backtester

An all-in-one financial workstation: download historical market data across multiple Indian
brokers (Upstox, Zerodha, Dhan) and build + backtest + optimise + ML-mine algorithmic trading
strategies — **without writing a line of code**.

> **Two editions in this repo:**
> - **v4 — Modular Edition** (recommended): the same app refactored into a fast, modular,
>   testable package under [`modular history/`](modular%20history/). Start here for new work.
> - **v3 — Single-file** (`upstox_app.py`): the original 3,207-line app, retained as reference.
>
> v4 is **feature-for-feature identical** to v3 (verified by a numeric parity harness) — just
> faster, modular, and with several bug fixes. See the [Changelog](CHANGELOG.md).

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
