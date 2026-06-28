# Changelog

## v7.0 — Feature Maturity (2026-06-28)

The v7 "Feature Maturity" milestone (see `modular history/ROADMAP_v7.md`), driven by a multi-agent
maturity audit: the Optimizer was *amateur* and the ML Pattern Finder *toy-ish* (it trained on **raw
absolute features** that don't generalize across instruments, with no out-of-sample test). v7 fixes
all three layers. **Phases A + B + C** complete; **67 passing tests**, parity unaffected.

**Phase A — normalized cross-asset feature library** (new no-code indicator types, so the builder, ML
finder and plotter all gain signals that mean the same thing on RELIANCE, NIFTY or any instrument):
- **Ratio to SMA** (`x / SMA(x,n)` — pick Volume for "volume vs its average"), **Distance from MA (ATR)**,
  **MA Slope (ATR)**, **Z-Score**, **Percentile Rank**, **Return (n-bar)** — each rendered in its own panel.
  Implemented via the registry (placement/guides + types/specs/vectorised helpers + codegen + compiler).
  Covered by `tests/test_features.py`.

**Phase B — ML rigor** (`ml/patterns.py` + `ui/pages/ml_finder.py`): the pattern finder now
- **defaults to the normalized features** and **z-scores any raw/absolute feature per-instrument**
  before bulk aggregation (each trade frame is tagged with its `Source`);
- mines rules on a **time-ordered holdout (70/30)** or **walk-forward k-fold** and scores them
  **out-of-sample**;
- promotes a rule only if it clears a **sample-size floor**, survives a one-sided binomial test vs the
  OOS baseline under **Benjamini–Hochberg FDR**, and keeps a **Wilson lower-bound** edge over baseline —
  everything weaker is shown as an "exploratory candidate" with the reason it failed. `tests/test_ml_rigor.py`.

**Phase C — optimizer validation** (`backtest/metrics.py`, `optimizer.py`, new `backtest/wfo.py`,
`viz/charts.py`, `ui/pages/backtester.py`):
- **Probabilistic & Deflated Sharpe** (Bailey/López de Prado) — DSR deflates the winner's Sharpe for the
  number of configurations tried; Optuna gains a "Probabilistic Sharpe (PSR)" objective;
- **Parameter-stability surface** — the grid `return_heatmap` (previously discarded) is surfaced as a
  1-D/2-D heatmap so you can see whether an optimum is a robust plateau or a fragile spike;
- **Walk-forward analysis** — anchored/rolling: re-optimise per in-sample window, trade frozen params on
  the next unseen window, stitch the OOS equity, and report **Walk-Forward Efficiency**;
- **Monte-Carlo bootstrap** — resample the trade sequence for P5/P50/P95 final-equity & worst-drawdown
  bands with a fan chart. `tests/test_optimizer_validation.py`.

(Supersedes the `v7.0-alpha` pre-release, which shipped Phase A only.)

## v6.1 — Chart Redesign (2026-06-27)

The Plotly backtest chart was "messy" because a type-blind loop dumped **every** indicator —
including oscillators (RSI/ADX/Stochastic/MACD on 0–100 / ~0 scales) — onto the single price
axis. A multi-agent debate concluded: **keep Plotly, fix the design** (Streamlit's `st.bokeh_chart`
is a no-op here, and a pivot would carry the same flaw at far higher cost). Fixes:

- **Overlay vs panel routing** — price-scale indicators (SMA/EMA/WMA/ALMA/VWAP/Bollinger/
  Supertrend/Keltner/Donchian/PSAR) stay overlaid on the candles; own-scale oscillators each get
  their **own stacked sub-panel** with guide lines (RSI 30/70, Stoch 20/80, ADX 25, MACD zero).
  Driven by a single `config.INDICATOR_PLACEMENT` + `models.classify_columns`.
- **Readable & fast** — range-scaled signal markers (not fixed-%), WebGL (`Scattergl`) traces,
  candle downsampling beyond 8,000 bars, intraday range-breaks (collapse overnight/weekend gaps),
  `hovermode='x'` + horizontal legend.
- **Optional "Pro chart"** — a checkbox embeds backtesting.py's **native Bokeh** plot (price +
  indicators + equity + drawdown + trades, auto-downsampled) via the proven `file_html` embed —
  the one genuine Bokeh win, captured without pivoting the owned Plotly charts.

41 passing tests (3 new chart-layout tests + the native-Bokeh render); verified live in-browser.

## v6.0 — Audit Hardening, Speed & Multi-Format Saving (2026-06-27)

Built on v5. A user-reported slowness prompted a **multi-agent audit** that found (and this
release fixes) several real bugs, plus performance work and CSV/Parquet saving. **37 passing
tests** (parity still byte-exact).

### Performance — tab switching / search lag
- Instrument **master list** is now a shared `cache_resource` singleton + cached in session_state
  (no 53 MB deep-copy or "Loading…" flash per rerun), the asset-class classifier is **vectorised**
  (3.0s → 0.3s), and the list is cached **on disk for 24h** (startup ~10s → ~0.8s).
- The instrument **selector** no longer re-scans all ~141k rows on every keystroke (per-class
  slices precomputed once) — search keystroke ~90ms → ~33ms.
- `PRAGMA journal_mode=WAL` set once (not per DB connection); resample vectorised (~40× faster).

### Bug fixes (from the audit)
- **ML Pattern Finder** crashed on every run (read `Return [%]` instead of the per-trade
  `ReturnPct`) — fixed; outputs now labelled in-sample/exploratory.
- **Short trailing stops never ratcheted** (signed position size) and the **"Tighten Trailing
  Stop" zone did nothing** — both fixed; also fixed a latent crash (`position.entry_price`).
- **Charges panel double-counted** the engine commission and mislabeled gross — now reconstructs
  true commission-free gross so statutory charges are the only cost layer.
- **Optuna** fed floats into integer params (silent failed trials, fake "best") — now uses
  `suggest_int` for integer ranges and errors if no valid configuration is found.
- **Presets** silently dropped position sizing (size type / per-trade ₹ / lot) — now round-tripped.
- Year-wise **drawdown** reported ~0% (min vs max), volume-less charts, and a mtime-based dataset
  hash — all fixed; data-quality gate now enforced inside the engine.

### Corporate-actions download crash
- Downloading an equity with a split/bonus crashed on pandas ≥ 3 (`int64` Volume × float
  multiplier). Fixed by widening OHLCV to float before adjustment. Added the previously-missing
  corporate-actions test suite.

### CSV + Parquet saving
- New **Download Save Format** setting (Parquet / CSV / **Both**, default Both) and an optional
  **separate CSV folder**. Every download page now exposes **both** a Parquet and a CSV folder
  input, and states exactly what is saved where.

---

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
