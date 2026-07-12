# Changelog

## v8.1 — Backlog Clear: trade analytics, tax export, HTF bias & intraday square-off (2026-07-12)

Clears the QuantDesk deferred backlog. Full test suite green + AppTest (all 13 pages, a UI backtest,
and the builder/settings/optimizer flows). New `tests/test_backlog_v81.py`.

**Analytics & reporting**
- **Trade-level analytics** on the Backtester: per-trade **MAE / MFE / R-multiple** plus a
  **Kelly / payoff / expectancy** summary computed from the run's own trades.
- **India tax-format CSV** export — per-trade STT / stamp / exchange / SEBI / brokerage / GST with
  gross vs net P&L, for your tax records.
- **Batch runs** now persist `dataset_hash / segment / net_return / equity_curve`, so folder-batch
  results appear in the leaderboard and are reproducible like single runs.

**No-code strategy capabilities**
- **Higher-timeframe trend bias** — take longs only above (and shorts only below) a higher-timeframe
  EMA, computed by resampling the instrument's own data.
- **Intraday square-off (MIS)** — flatten any open position at each session's last bar (no overnight
  carry); for minute data.

**Reliability & housekeeping**
- **Consistent timestamps** — all loaded data is normalised to one tz convention (tz-naive IST), so
  mixed `+05:30` / naive CSVs no longer clash.
- **Per-run error reports** — a failed backtest/download drops a diagnosable JSON (traceback +
  context) under `.workflow/errors/`.
- **Strategy Builder** — deleting a rule/zone row no longer shifts the remaining rows' values
  (stable per-row widget keys).
- **Options Chain** — changing the underlying/expiry after a fetch clears the stale contracts so you
  can't save the wrong chain.
- **Results** — delete now defaults to the run you're viewing and asks for confirmation.
- **Portable tests** — set `QUANTDESK_TEST_CSV` (and `QUANTDESK_TEST_DIR`); data-backed tests skip
  cleanly when the file isn't present. No more hardcoded machine paths.

Architecture note: multi-asset portfolio, options legs/Greeks, benchmark (alpha/beta/IR), trade
replay, futures-continuous contracts and survivorship are intentionally **not** built into
backtesting.py here — they live in the companion MBS engine (VectorBT + NautilusTrader); a strategy
"graduates" to MBS for that validation. See `modular history/progress.json → deferred_roadmap`.

## v8.0 — Data Integrity: no more silent download gaps + calendar-aware quality gate (2026-07-11)

Closes a data-trust blind spot surfaced by an audit of the download → data path. **117 passing tests**
(was 112). This release is about one promise: **you can trust that a saved dataset contains what its name
says it contains.**

- **Silent chunk loss — FIXED (the headline).** Historical data is fetched in chunks (30 days for
  1-minute, a year otherwise). Previously, a chunk that failed every retry was swallowed as an empty
  frame — indistinguishable from a genuinely empty range — so a rate-limit or dropped connection left a
  **silent hole** in the saved file while the UI reported success and named the file for the *requested*
  range. Now `brokers/downloader.py` raises `ChunkDownloadError` (carrying the exact date range), logs a
  WARNING, and returns a `DownloadResult(df, failed_ranges, complete)`. All three downloader pages
  (Single / Bulk / Master) **warn loudly** on any failure and name the saved file + `downloads_log` row
  by the **actual retrieved range**, not the requested one; the Bulk page adds an "Incomplete" count.
- **Smarter retries.** Back-off is now exponential with full jitter (was a fixed 0.5/1.0/1.5 s) and
  honours a broker's HTTP 429 `Retry-After` signal, so a transient rate-limit is far more likely to
  recover within the retry budget instead of dropping the chunk.
- **Calendar-aware data-quality gate.** `data/quality.py` now checks your data against the **NSE trading
  calendar** (weekends + holidays excluded, via a bundled `nse_holidays.csv`) and flags **missing trading
  days**. A contiguous multi-week hole lowers the quality score and raises *"N missing trading day(s) —
  possible incomplete download"*; scattered single days barely move it. This catches holes left by *past*
  downloads, not just future ones.
- **Real finding from verification:** the bundled RELIANCE 15-minute fixture has genuine, recurring
  ~1-month **Feb–March holes** every year (2023–2026; 92 missing trading days) that the old gate scored
  95+ and the new gate scores 66.6. Older multi-year datasets likely have similar holes — re-download to
  fill them. New tests: `tests/test_downloader.py` (failed-chunk surfacing, Retry-After) and
  `test_quality_flags_missing_chunk`; `test_quality_clean_data` was corrected (its old "≥95 on the real
  file" assertion had merely encoded the old gate's blindness to missing days).
- **Packaging fix (important):** the repo's `.gitignore` had a bare `data/` rule that also matched the
  **source** package `modular history/quantdesk/data/`, so every release up to v7.8 shipped **without the
  data layer** (loader / quality / resample / corporate-actions) — a fresh clone could not run. The rule
  is now anchored (`/data/` + `modular history/data/`), and the full `quantdesk/data/` package is restored
  to the repository.

## v7.8 — Risk management complete: breakeven stops, partial exits, adaptive Kelly sizing (2026-07-09)

Finishes the risk-management chapter of the post-audit "Trustworthy Core" milestone (Task 3 pt.3 — the
last deferred items). **112 passing tests**, AppTest (all 13 pages + a UI backtest), and real-data +
synthetic drives. All three controls are opt-in and default-OFF — existing strategies are byte-identical
(the parity test now locks this).

- **Breakeven stop** (`breakeven_trigger_pct`) — once an open trade moves the trigger % in your favour,
  its stop is ratcheted up to the entry price (never loosened; guarded so the new stop always sits on the
  safe side of the current close). A winning trade can no longer turn into a loss. Works alongside the
  Static-% and ATR stop modes.
- **Partial exit / scale-out** (`partial_exit_pct` + `partial_trigger_pct`) — banks a chosen fraction of
  the position at a first target (`Position.close(portion=...)`, once per trade) and lets the remainder
  run with its SL/TP intact. Composes with the breakeven stop (bank half, move the rest to breakeven).
  Real drive: split 55 → 60 trade records and cut max drawdown −7.82% → −7.60%.
- **Adaptive Kelly sizing** — two new Position-Sizing modes, **"Half-Kelly (Adaptive)"** and **"Full
  Kelly (Adaptive)"**, that estimate win-rate × payoff from the strategy's *own* closed trades as the
  backtest runs and deploy that Kelly fraction of equity. Engages only after `KELLY_MIN_TRADES` (20)
  trades have closed (fixed-₹ warm-up before that); a non-positive measured edge sizes at minimum — Kelly
  only sizes UP once a real edge is observed. Synthetic drive: Half-Kelly compounded a positive-edge
  strategy to **+42.7%** while Full-Kelly's variance left the *same* edge at **−0.5%** — exactly the
  aggression the UI caption warns about (Half-Kelly is the recommended default).
- Wired through `config.py`, `models.RiskManagement`, `strategy/codegen.py`, the compiler namespace
  (`breakeven_stop` / `kelly_fraction` / `half_kelly_fraction` — pure, unit-tested helpers), the Strategy
  Builder UI ("Advanced trade management" row + new sizing options), and `validation.py`, with full
  preset round-trip.

*(Task 3 is now COMPLETE — no risk items remain deferred. The "Trustworthy Core" milestone, audit
4.8 → ~6.0, is fully shipped. Next: v8, the Autonomous Data Agent — see
`modular history/ROADMAP_v8_data_agent.md`. Session handoff: `modular history/RESUME_CONTEXT.md`.)*

## v7.7 — Risk management: ATR (volatility) stops + time stops (2026-07-08)

Extends the v7.6 risk module with two more professional exit controls. **108 passing tests**, AppTest,
real-data drive. Existing strategies are byte-identical (the new modes only emit when selected).

- **ATR Stop Loss (Volatility)** — a new exit mode where the stop sits `mult × ATR` from entry (wider in
  volatile markets, tighter in calm ones). It also drives **ATR-based risk-% sizing** (`atr_risk_qty`) when
  paired with "Risk % of Equity" sizing, so the position size adapts to volatility.
- **Time stop** (`max_bars_in_trade`) — closes any trade still open after N bars.
- Wired through `models.RiskManagement`, `strategy/codegen.py`, the compiler namespace (the `atr_stop`
  helper), the Strategy Builder UI, and `validation.py`, with preset round-trip. Real drive: the time stop
  capped max holding from 150 → 20 bars; the ATR stop lifted an EMA-crossover's return 1.8% → 5.4%.

*(Completes the post-audit "Trustworthy Core" milestone, 4.8→~6.0. Still deferred to a later pt.3: breakeven
stops, partial exits/scale-out, Kelly-informed sizing. A full session handoff is in
`modular history/RESUME_CONTEXT.md`.)*

## v7.6 — Trustworthy Core, part 2: professional risk management (2026-07-08)

Completes the post-audit "Trustworthy Core" milestone by lifting the app's lowest-scored dimension —
risk management — out of fixed-₹/lot-only sizing. **105 passing tests**, AppTest, and a real-data drive.

- **`quantdesk/risk/{sizing,stops}.py`** (new) — pure, unit-tested primitives: `risk_pct_qty`
  (`qty = equity*risk% / stop_distance`), `atr_risk_qty`, `percent_equity_qty`, `fixed_value_qty`,
  `kelly`/`half_kelly`, and `static_sl_tp` / `atr_stop` / `breakeven_stop` / `trailing_stop`. They're
  injected into the compiler namespace so the no-code codegen calls the **tested** functions instead of
  inlining string math.
- **"Risk % of Equity per Trade" sizing** — a new Position-Sizing mode that sizes each trade so a
  stop-out loses a fixed % of equity (using the Static-% stop distance), capped to affordable size.
- **Max-drawdown circuit-breaker** — flattens any position and blocks new entries once equity falls a
  configurable % below its running peak. In a real drive it capped drawdown from −11.8% to exactly −10.0%.
- Wired through `models.RiskManagement`, `codegen.py`, the Strategy Builder UI, and `validation.py`
  (round-trips through presets). Existing strategies are byte-identical (parity preserved).

*(Deferred to a later release: wiring ATR/breakeven/time stops into codegen — the pure helpers exist —
plus partial exits/scale-out and Kelly-informed sizing.)*

## v7.5 — Trustworthy Core, part 1: security + charge-accurate backtests (2026-07-08)

A multi-agent quant-desk audit scored the app **4.8/10** and flagged two things as *wrong or
dangerous*. v7.5 fixes both (the "Trustworthy Core" milestone; the risk-management module lands next).
**95 passing tests**, AppTest, and real-data drives.

**Security — closes a real RCE.** The Strategy Builder's JSON preset upload fed an arbitrary file
straight into `session_state` → codegen → `compiler.exec()` with no validation, and codegen
interpolated user-controlled fields verbatim into the source string. Two independent layers now guard it:
- **`strategy/validation.py`** (new) — `assert_safe_ast` runs in the compiler right before `exec()`
  (rejects imports outside a tiny allowlist, dangerous builtins, and any dunder attribute/string,
  blocking `().__class__.__bases__`-style sandbox escapes). Because every run funnels through
  `compile_strategy`, this protects programmatic callers too, not just the upload path.
- **`validate_preset`** — schema/type-validation at the upload/load door: every rule/zone column
  reference must resolve to a real OHLCV/indicator column, thresholds must be numeric, and every
  type/operator/action/mode must be a known registry value.

**Charge-accurate backtests — the audit's #1 correctness finding.** The exact India charge model was a
post-trade *display* pass; it never entered the simulated equity curve, so Return/Sharpe/DSR/walk-forward/
Monte-Carlo and the optimizer objective all ran on an approximate flat commission. Now
`costs/india.make_fee_model(segment)` supplies a per-leg commission **callable** that backtesting.py
applies **inside** the simulation (`engine`, `wfo`, and the Backtester UI thread the segment + slippage
through; the Gross-vs-Net panel becomes a per-trade reconciliation). The impact is large: an EMA-crossover
that looked like **20.8% return / 0.79 Sharpe / 95% PSR** gross is revealed as **0.7% / 0.03 / 55%** after
~₹20k of real charges — the difference between "significant edge" and "destroyed by costs".

Also bundles the planning docs `modular history/ROADMAP_v7.1_trustworthy_core.md` and
`ROADMAP_v8_data_agent.md` (the upcoming autonomous data agent).

## v7.0.1 — Startup/navigation performance (2026-06-28)

Patch release. Opening the **Strategy & Indicator Plotter** or **Backtester** for the first time in a
session froze for ~6s because heavy libraries were imported at module top and therefore loaded during
the page switch: `backtesting` (+ `bokeh`, ~2.3s) and `pandas_ta` (~1.8s) via `strategy/compiler.py`,
and `quantstats` (+ matplotlib, ~4.9s) via `viz/charts.py`. All three are now **lazy-loaded** — they
import on first compile/run/tearsheet (under the existing spinners), not on navigation.

- `strategy/compiler.py`: `backtesting` + `pandas_ta` imported inside `_namespace()`.
- `viz/charts.py`: `quantstats` loaded on demand via `_qs_module()` / `quantstats_available()`.
- `backtest/engine.py` + `ui/pages/plotter.py`: `backtesting` imported at point of use.

Result: page-module import **6.08s → 0.91s**; no behavior change. 67 tests green; AppTest + real-data
drives verified.

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
