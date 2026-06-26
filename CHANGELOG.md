# Changelog

## v5.0 — Honest Numbers (2026-06-26)

Built on the v4 modular package (`modular history/`). This release makes every reported number
**trustworthy** — validated out-of-sample, charged with real India statutory costs, and gated on
data quality — plus institutional reporting via QuantStats. Driven by a multi-agent specialist +
quant review (`modular history/ROADMAP_v5.md`).

### Honest results
- **Out-of-sample validation (#1):** the optimizer now tunes on the in-sample first 70% and
  reports the **honest out-of-sample** result on the held-out tail, with an In-Sample vs
  Out-of-Sample comparison + degradation % and an explicit overfitting warning.
- **Data-quality gate (#3):** `quality.validate()` is now enforced before every backtest (single
  + batch worker) — blocks broken data (high<low, dup timestamps), warns on marginal, stored per run.

### ₹-accurate costs
- **Real India charge engine (#2):** a Decimal post-trade pass itemising STT / stamp / exchange /
  SEBI / brokerage / 18% GST per leg by segment (Equity Intraday / Delivery / Futures), shown as a
  Gross-vs-Net P&L panel with a per-charge breakdown.

### Reporting depth
- **QuantStats (#4):** institutional HTML tearsheet + VaR/CVaR/Kelly/volatility risk block behind
  an Advanced expander (intraday equity resampled to daily to avoid inflated Sharpe/CAGR).
- **Surfaced buried metrics (#5):** Profit Factor, Expectancy, SQN, Buy & Hold — already computed
  by backtesting.py but previously hidden — now in tiles, the batch table and the DB.
- **Results leaderboard (#6):** compare runs side-by-side with overlaid equity curves and
  plain-language 🟢🟡🔴 health verdicts (replaces the raw JSON dump). Equity curves are persisted.

### Correctness fixes
- **Trailing stop (#7):** now triggers **intrabar** (Low/High) instead of on Close — and fixes a
  latent v3 crash (`position.entry_price` does not exist in backtesting.py, so trailing stops were
  broken in the original).
- **Resampling:** carries Dividend/Rights cashflows into higher timeframes (#8) and anchors bins
  per trading session so no bar bridges the overnight gap (#10).
- **Reproducibility hashes (#9):** dataset / config / strategy / code hashes stored on every run.
- **Batch** relabeled as *independent single-asset runs* (not a combined portfolio).

### Verified
- pytest 18/18 (parity still byte-exact); Streamlit AppTest renders all 13 pages and runs a
  backtest through the UI (21 metrics); deep flows (plotter/builder/settings/optimizer-IS-OOS) green.

---

## v4.0 — Modular Edition (2026-06-25)

A full architectural refactor of the single-file `upstox_app.py` into a modular, faster, tested
package under `modular history/`. **No feature removed; behaviour preserved** (numeric parity
verified). The original v3 app is retained at the repo root for reference.

### Architecture
- Split the 3,207-line monolith into 52 focused modules: `config`, `core/` (db, settings,
  logging, cache), `data/`, `brokers/`, `strategy/`, `backtest/`, `ml/`, `viz/`, `ui/` (+ one
  `render(ctx)` module per page). Thin `app_main.py` entry. See `modular history/ARCHITECTURE.md`.
- Backend modules never import Streamlit, so they run inside multiprocessing workers and tests.
- Single database layer and a single cached settings accessor (replacing ~20 inline connections
  and ~6 ad-hoc settings reads).

### Performance ("superfast")
- Vectorised the slow per-bar indicators — ALMA, Linear-Regression Slope, R-Squared — replacing
  per-window `rolling().apply()`. Proven **numerically identical** to the originals
  (`tests/test_parity.py`).
- Bytecode-cached strategy compilation (no re-parsing each run / optimiser trial).
- Cached settings, instrument master list, and data-file loads.
- Vectorised trade-log enrichment (removed per-row Python loops).

### Fixes
- Multi-output indicators (Supertrend, Stochastic, Keltner, Donchian) are now selectable on the
  Plotter and ML pages (the monolith only exposed MACD & Bollinger there).
- Recovery scan now parses both `{symbol}_{tf}_{from}_{to}` and `..._from_to_to` filename formats.
- Optimizer "Equity Final" maximise metric mapped to the correct backtesting.py key.
- Stats table no longer emits an Arrow/Timedelta serialization warning.
- Cache layer no longer pulls Streamlit into worker/test processes.

### Enhancements (nothing removed)
- Local tools (Backtester, Strategy Builder, Settings, Converter, Logs, Results, ML) now work
  **without** a broker login; only live downloaders / options chain require authentication.
- Structured rotating logs; a non-blocking data-quality report.
- File-by-file build & verification index (`modular history/modular.json`).

### Verified
- `pytest` unit suite + parity harness green.
- Streamlit AppTest renders all 13 pages and runs a backtest through the UI.
- Live broker paths verified end-to-end (master list, daily/1-min downloads with corporate
  actions, options expiries → contracts → historical candles).

---

## v3.3 — Metrics & Plotter Update (2026-06-21)
- Fixed 90% drawdown bug by switching default position sizing to Fixed Value.
- Added year-wise CAGR & drawdown details.
- Added explicit Start/End date and Initial Capital metrics to the single backtest.
- Renamed Signal Inspector to "Strategy & Indicator Plotter".

## v3.2 / v3.1 / v3.0 — The Realism Update
- True-realism execution: corporate-action accrual (T-1 holdings, no phantom cash), slippage /
  spread penalty, fixed position sizing, bankruptcy guard.
- Deep analytics: trade inspector, year-wise breakdown, annualised CAGR & starting capital.
- Persistent batch backtesting to SQLite.
- Async download engine, Upstox corporate-actions sanitisation, 130+ pandas-ta indicators,
  no-code optimizer, advanced range zones, regime filters, isolated `uv` Python 3.12 env.

## v1.0 (2026-05-07)
- Initial release: multi-broker historical data downloader.
