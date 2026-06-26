# QuantDesk Roadmap v5

> A synthesized, prioritized roadmap for the QuantDesk no-code backtesting dashboard — built from six independent expert reviews (engine realism, quant methodology, reporting/risk, data quality, strategy/portfolio, architecture, and UX).

---

## Executive Summary

QuantDesk is a clean, well-layered no-code backtesting dashboard built on `backtesting.py`: a 13-page Streamlit UI over a Streamlit-free backend (data / strategy / codegen / backtest / ml / viz), with a genuine no-code strategy builder, Optuna/grid optimization, folder batch, corporate-action adjustment, and a working data-quality module. The architecture is sound and extensible.

But the app **systematically overstates expected performance** through three confirmed, mutually reinforcing gaps:

1. **In-sample optimizer.** The optimizer re-runs `best_params` on the SAME full dataset and reports in-sample numbers as results (`optimizer.py:45`) with no out-of-sample / walk-forward split.
2. **Flat cost model.** Costs collapse to a single flat symmetric commission = `brokerage + slippage` (`settings_repo.py:68-71`), so India's asymmetric STT/stamp/GST/exchange charges — the app's whole ₹-first value prop — are unmodeled.
3. **Dead quality gate.** The data-quality gate exists but is dead code (`quality.validate()` has zero call sites), so backtests can silently run on bad/unadjusted data.

**The single biggest opportunity is to make every reported number HONEST**: enforce IS/OOS validation, itemize real India charges as a post-trade pass, and gate on data quality. Layered on top, **QuantStats** unlocks an institutional reporting tier almost for free, because `stats['_equity_curve']` is already a datetime-indexed equity series.

---

## QuantStats Verdict — **ADOPT**

QuantStats is the correct, low-effort, high-value move, and three independent reviewers converged on it. The exact input it needs already exists and is already consumed elsewhere: `stats['_equity_curve']` is a datetime-indexed DataFrame with an `'Equity'` column (`metrics.yearly_performance` / `backtester.py` already groups it by `.index.year`). Wiring it unlocks a full institutional tearsheet (rolling Sharpe, drawdown periods, monthly-returns heatmap, returns distribution, VaR/CVaR), plus alpha/beta/IR vs a Nifty benchmark, for a fraction of the effort of hand-rolling those analytics. It aligns with the project's `CLAUDE.md` Phase 8 mandate.

Trade-level stats QuantStats **cannot** produce (MAE/MFE, R-multiple) remain custom work — so QuantStats *complements* rather than replaces the trade-analytics roadmap.

### How to wire it

1. **Add `quantstats`** to a requirements/pyproject (currently absent) with a guarded optional import per the `CLAUDE.md` Section 11 pattern:
   ```python
   try:
       import quantstats as qs
       _QS = True
   except ImportError:
       _QS = False
   ```
2. **Add `metrics.equity_returns(stats)`:** `eq = stats['_equity_curve']['Equity']; rets = eq.pct_change().dropna()`.
   **CRITICAL:** for intraday bars, first resample to daily — `rets = eq.resample('1D').last().pct_change().dropna()` — because QuantStats annualizes assuming daily periodicity; feeding 5-minute returns silently inflates Sharpe/CAGR.
3. **Add `viz.charts.quantstats_tearsheet_html(rets, benchmark=None)`** calling `qs.reports.html(rets, output=buf)` and render via Streamlit `components.html` inside an "Advanced" expander on the backtester results page (after `backtester.py:180`), with a `download_button` mirroring the existing trade-log CSV export.
4. **Surface individual `qs.stats.*`** (`value_at_risk`, `cvar`, `rolling_sharpe`, `kelly_criterion`, `drawdown_details`) inline in a curated risk block next to the existing metric tiles.
5. **Pass a Nifty buy-and-hold series** (loaded from the same `data_root` via the existing `load_indexed` path) as `benchmark=` to add alpha/beta/IR panels.
6. **Prerequisite:** persist the equity curve (compressed parquet/JSON sidecar keyed by run id) so historical runs can regenerate the tearsheet — currently only scalar stats are saved.

---

## Themes

| Theme | Summary |
|---|---|
| **Honest results (overfitting & validation)** | The optimizer reports in-sample best params as expected performance with no OOS split, no walk-forward, no deflated Sharpe; the ML rule miner trains/evaluates on the same trades. The single most dangerous class of gap: every optimized strategy looks far better than it will trade. Fixing requires IS/OOS split first, then WFO, deflated Sharpe, Monte Carlo bands, parameter-stability surfaces, and explicit UI warnings on optimized runs. |
| **Cost & execution realism (India-specific)** | The flat symmetric commission cannot express India's asymmetric segment-specific charges (STT sell-side, stamp buy-side, 18% GST, exchange/SEBI). Compounded by: no intraday session anchoring (resample bridges the overnight gap), no forced square-off, a trailing stop that fires on Close instead of intrabar High/Low, and a single global margin. Core realism gaps for a ₹-first intraday tool. |
| **Reporting & risk analytics depth** | Reporting is a raw stats dump plus a coarse yearly table; no tearsheet, VaR/CVaR, rolling metrics, monthly heatmap, drawdown-period table, MAE/MFE, R-multiple, Kelly, or benchmark comparison. Several metrics (Profit Factor, Expectancy, SQN) are already computed by `backtesting.py` but buried. QuantStats plus a small custom trade-analytics layer closes most of this cheaply. |
| **Data integrity & coverage** | The quality gate is dead code, corporate-action adjustment status is neither persisted nor surfaced, resampling silently drops Dividend/Rights_Premium cashflows, and there is no holiday calendar, survivorship caveat, or PIT constituents. The batch worker also bypasses the central loader, creating a second divergent read path with no caching or validation. |
| **Strategy & portfolio capabilities** | Single-instrument, single-timeframe, one-position model: no partial exits, no pyramiding, only static %SL/TP and %-trailing stops, no ATR/time/session exits, no risk-% sizing, no multi-timeframe bias, and no true portfolio (batch only averages isolated full-capital runs). Plus no cross-run comparison/leaderboard despite the data already being persisted. |
| **Architecture, reproducibility & safety** | No reproducibility hashes (dataset/config/strategy/code) so old results can't be trusted or re-derived; no checkpoint/resume for long Optuna/batch runs; `exec()` of generated source has no AST whitelist and imported preset JSON is unvalidated (latent RCE the moment any free-text field is added); run logs aren't run_id-tagged; tests are non-portable (hardcoded absolute paths) with the highest-risk codegen branches untested. |
| **Usability for a non-coder solo trader** | No onboarding/Home page, the Strategy Builder dumps all advanced features (regime filters, range zones) on first-timers at once, results are a raw `st.json` dump with no plain-language interpretation or pass/fail coloring, no trade replay, no A/B run comparison, no PDF/Excel/India-tax export, and stale-compiled-strategy and destructive-delete footguns. |

---

## Roadmap

Effort key: **S** small · **M** medium · **L** large · **XL** extra-large.

### NOW — make every number honest

| Item | Why | Impact | Effort |
|---|---|---|---|
| **IS/OOS split in the optimizer:** optimize on the first ~70% of `df`, evaluate frozen `best_params` once on the last ~30%, report both side by side with an OOS-degradation %. | `optimizer.py:45` re-runs best_params on the SAME full df and reports in-sample numbers — the single worst trap. A non-coder reads an Optuna-tuned "Best return" as forward performance. A simple split kills it with no new dependency. | High | M |
| **Real India charge engine** as a post-trade pass over `stats['_trades']`: run with `fees=0`, then compute STT (sell-side), stamp (buy-side), exchange txn, SEBI, and 18% GST per leg by segment in `Decimal`; subtract from gross; surface a Gross-vs-Net + per-charge breakdown panel. | `settings_repo.py:68-71` collapses everything into one symmetric % that misstates intraday/futures net P&L and makes "why is my profit smaller?" unanswerable — undermining the ₹-first differentiator. | High | L |
| **Wire `quality.validate()`** into BOTH backtest entry points (`backtester._render_single` after `load_indexed`, and `worker.run_worker`); surface score/issues in the UI; store on the backtest row; add warn/block thresholds. | `quality.validate()` is fully implemented but has zero call sites — users can silently backtest on duplicate timestamps, high<low bars, or huge gaps. Highest impact-to-effort fix since the function already exists (Standing Rule 0.2a). | High | S |
| **Add QuantStats** (guarded import) + `metrics.equity_returns(stats)` (resampling intraday→daily) + an "Advanced" tearsheet expander with HTML download, plus a curated risk block (VaR/CVaR, rolling Sharpe, drawdown-period table, monthly heatmap). | `stats['_equity_curve']['Equity']` already exists and is datetime-indexed; one wiring unlocks an institutional reporting tier (largest depth gain for least code). | High | M |
| **Surface buried metrics:** promote Profit Factor, Expectancy [%], SQN, and `backtesting.py`'s Buy & Hold Return into curated tiles, the worker result dict, the batch table, and new DB columns. | `backtesting.py` 0.6.5 already computes these into the stats Series but they're hidden — zero new computation, immediate depth, a free benchmark baseline. | Medium | S |
| **Cross-run comparison/leaderboard** in `results.py`: multiselect over backtests rows, sortable side-by-side metric table, overlaid equity curves; replace the raw `st.json` inspect view with a formatted summary + plain-language interpretation and green/amber/red thresholds. | The backtests table already stores return/DD/win-rate/Sharpe per run, so ranking is near-free and is exactly how a designer decides what to keep; the raw json dump leaves a non-coder unable to judge if Sharpe 0.7 is good. | High | S |
| **Fix the custom trailing stop** to trigger intrabar against Low (long) / High (short) or via `trade.sl`, instead of on Close only (`codegen.py:244-264`). | Close-only checks let price wick through the stop and exit better-than-real, overstating P&L and contradicting the intrabar-correct static SL/TP. The high-water mark already uses High/Low; only the trigger comparison is wrong. | Medium | M |
| **Carry Dividend/Rights_Premium through resampling** (sum per bar) in `resample_dataframe` so dividend cashflows aren't dropped on every higher-timeframe file. | Codegen credits those columns as cash, so a daily/15m backtest of a dividend stock under-reports vs the 1-min run of the same period — a silent internal inconsistency. Small isolated change. | High | S |
| **Reproducibility hashes** (dataset/config/strategy/code) as four columns on the backtests table, stamped in `run_single`'s callers and `save_batch_to_db`; reuse `compiler.py`'s existing `hash(code)` as strategy_hash and fix `batch.py:118` writing empty start/end dates. | Nothing today identifies WHICH data/costs/code produced a stored result, so an old number can't be trusted or re-derived. Cheap now, near-impossible to retrofit (Standing Rule 0.2b). | High | M |
| **Relabel `batch.py` output** as "independent single-asset runs (not a combined portfolio)" and add survivorship + corporate-action caveats; show a "⚠ not adjusted for corporate actions" banner when a file's adjustment status is false/unknown. | The AVERAGE row reads like portfolio performance though each run used 100% capital; adjustment is Upstox-only/opt-in so many cached files may be unadjusted with no warning. Near-zero effort, prevents real misinterpretation. | Medium | S |
| **Anchor resample bins to the 09:15 session open** (origin/offset) and drop bars that bridge the 15:30→09:15 overnight gap. | `data/resample.py` bins to `'{minutes}min'` with no origin, so an N-minute bar can span the overnight gap, producing fictitious bars that corrupt every intraday backtest. | High | M |

### NEXT — depth, robustness, and the refinement loop

| Item | Why | Impact | Effort |
|---|---|---|---|
| **Optional forced intraday square-off** (close near 15:20–15:30) tied to a product flag (MIS vs CNC) in `codegen.py` `next()`. | Without it an "intraday" strategy silently holds overnight, inflating returns and ignoring carry/gap risk — primary realism gap for an India intraday tool. | High | M |
| **Walk-forward (`backtest/wfo.py`):** rolling/anchored WFO (reuse `optimizer.optimize` per IS window + `engine.run_single` per OOS window), stitch OOS segments into one honest equity curve, report WFO efficiency and per-window param drift; checkpoint per window. | A single IS/OOS split is fragile to the cut date; WFO is the industry standard and the Phase 7 mandate. Building blocks already exist. | High | L |
| **Deflated/Probabilistic Sharpe** in `metrics.py`, feed `n_trials` from the optimizer, have Optuna maximize DSR instead of raw Sharpe; add a multiple-testing correction (binomial/Fisher + Benjamini-Hochberg) and raise min-sample thresholds in the ML rule miner. | The optimizer reports the max over up to 1000 trials (upward-biased even on random strategies) and the rule miner keeps any leaf beating baseline×1.1 with ≥5 samples. DSR is the canonical correction; pure-Python, no new dependency. | High | M |
| **Parameter-stability surface:** in grid mode use `Backtest.optimize(return_heatmap=True)` (already computed and discarded in `_grid_optimize`) to render a 1D/2D heatmap; for Optuna use contour/slice plots. | Shows whether an optimum is a robust plateau or a fragile spike — the cheapest, most intuitive overfitting check, and the grid data is already computed and thrown away. | High | S |
| **Monte Carlo / bootstrap robustness:** resample/shuffle `stats['_trades']` returns (1000 sims), report 5th/50th/95th-percentile final equity and worst-case drawdown, plot a fan chart. | One lucky path can carry a whole backtest; confidence bands turn a single equity curve into a risk-aware distribution. Trade log is already available. | High | M |
| **Trade-level analytics QuantStats can't do:** MAE/MFE per trade (from OHLC between EntryBar/ExitBar, extending the vectorized `enrich_trades` pattern), R-multiple (PnL / initial risk from `sl_pct`), and Kelly/half-Kelly from the PnL column. | Canonical diagnostics for refining stops/targets; feed the Trade-Replay direction; half-Kelly is the project's documented default sizing. | High | L |
| **Trade Replay / step-through** on the Results or Backtester page: a slider over `trades[EntryBar..ExitBar]` zooming `render_plotly_backtest` to that window and showing the captured `Entry_<indicator>` snapshot + matched rule. | The #1 debugging need for a discretionary-systematic trader; the enriched-trade snapshots already exist so no re-run is needed. | High | L |
| **Persist equity curve + enriched trade log** to the DB (sidecar parquet/JSON keyed by run id) so the Results page can regenerate tearsheets, drawdown tables, monthly heatmaps and trade-level stats for ANY historical run. | Without this, every reporting improvement works only on the just-run backtest and the Results browser stays a dead `st.json` dump — the multiplier that makes the QuantStats investment pay off app-wide. | High | M |
| **Guided Home/Get-Started page** (NAV[0], 4 cards + dismissible checklist) and split the Strategy Builder into simple-vs-advanced (collapse regime filter, range zones, advanced exits) with a one-click starter template (EMA crossover) using the existing presets machinery. | Biggest first-run usability win for the stated non-coder audience; touches only `ui/pages`. | High | M |
| **Benchmark engine (`backtest/benchmark.py`):** align strategy daily returns from `_equity_curve` with a Nifty series from `data_root`; compute CAGR-vs-benchmark, alpha, beta, Information Ratio, tracking error; persist and show side by side. | A 15% return in a year Nifty did 25% is underperformance but reads as a win today (Phase 8). Reuses the existing data path; can also feed QuantStats `benchmark=`. | High | M |
| **Enrich exits and sizing:** partial exits/scale-out (`trade.close(portion)`), ATR-based and time/N-bar stops, and a "Risk % per trade" sizing mode (qty from `equity*risk%/stop_distance`). | Every exit today is a full close and sizing is binary (fixed-₹ or 100% equity); these unlock the most common realistic strategy patterns and professional risk-based sizing. ATR is already a shipped indicator. | High | M |
| **Multi-timeframe support:** inject a forward-filled higher-timeframe indicator via `self.I` and an "on timeframe" selector per indicator in the builder (resample util already exists). | HTF bias filters ("trade the 15m only when the daily trend is up") are a core India equity/futures technique that is currently impossible. | High | L |
| **Make long runs survivable:** persistent SQLite `RDBStorage` Optuna study (`storage=` + `study_name`); `batch.py` persists each per-file result as it completes so a restarted batch skips done files; cooperative cancellation flag via `progress_cb`. | A 200-trial Optuna or 50-file batch over years of 1-min data loses all progress on restart and can't be cancelled cleanly (Phase 5/7). | High | M |
| **Close the `exec()` trust boundary:** AST parse + node/import whitelist gate in `compiler.py` before compile, and schema-validate imported preset JSON in `presets.load_strategy_raw` against the `StrategyConfig` shape. | `exec()` runs generated source with full builtins; the pipeline is safe only because every input is widget-constrained today — one custom-formula field or a hand-edited preset turns it into RCE. | High | M |
| **Portable tests:** replace path-dependent tests with synthetic fixtures (remove the hardcoded `D:\simpleton` absolute path) and add golden-output coverage for high-risk codegen branches (SL/TP, trailing, range zones, short side) plus an optimizer/batch smoke test. | The suite is non-portable/un-runnable in CI and the most bug-prone codegen logic is untested. | High | M |
| **NSE trading calendar (`data/calendar.py`** via a maintained CSV or `exchange_calendars`) and extend `quality.validate` to compute expected-vs-actual bars per session (gap/missing-bar/holiday checks). | Without a calendar the app can't distinguish a holiday gap from corrupt/missing data, so it can neither warn on real gaps nor avoid false alarms. | Medium | M |
| **Route `worker.run_worker` through `loader.load_indexed`** (or a process-safe equivalent), add the quality gate there, and preserve full tracebacks instead of `str(e)[:100]`. | The batch/ML path reimplements file reading (a second divergent read path), gets no validation, and truncates errors so failed files are undiagnosable. | Medium | S |
| **Persist & surface corporate-action adjustment status:** add `adjusted` (and `quality_score`, ISIN) columns to `downloads_log`, set them in the download pages. | A split looks like a 50% crash; without a persisted flag a backtest can't tell whether a cached file is adjusted (Standing Rule 0.2d). | High | M |
| **Run-id-tagged structured logging** + per-run metrics (bars_processed, run_duration, peak_memory) + `error_report_{run_id}.json` on unhandled exception. | Interleaved unstructured logs across ProcessPool workers can't be tied to a run; a failed overnight batch is currently undiagnosable (Standing Rule 0.2c). | Medium | M |
| **PDF tearsheet + Excel workbook export** (Summary/Trades/Yearly sheets); an India tax/P&L statement format once the real charge breakdown exists. | The ₹/India-first app offers only CSV + ZIP today; a non-coder has nothing presentable to review or file outside the app. | Medium | L |
| **Small-sample gating:** confidence note / grey-out on headline metrics when #Trades is below a threshold (~30), with a binomial CI on Win Rate. | A 6-trade backtest currently shows Sharpe and Win Rate with full confidence; small-sample metrics are noise and the user gets no signal. | Medium | S |

### LATER — strategic expansion (multi-step initiatives)

| Item | Why | Impact | Effort |
|---|---|---|---|
| **True multi-asset portfolio engine:** shared capital ledger, per-symbol allocation methods (equal/mean-variance/risk-parity/half-Kelly/etc.), a >0.80 correlation guard, and a book-level equity curve & drawdown — evolving `batch.py` beyond independent runs. | `backtesting.py` is single-asset by design, so this needs a custom multi-asset loop or a second engine; the largest strategic gap for portfolio capabilities (Phase 11). | High | XL |
| **Futures continuous-contract construction** (panama/ratio back-adjust off an expiry calendar) plus roll-cost modeling, and move margin/lot_size into per-instrument config sized off margin-blocked capital. | Equities are adjusted but futures can't run multi-month without manual stitching, and a single global margin can't represent NIFTY vs a stock future. Naive stitching also fakes signals at the roll. | Medium | L |
| **Options backtesting path** (separate simulator: vollib pricing + contract selection/roll/settlement + ITM-STT trap) over the per-contract files already downloaded; interim: make the "no options backtest" limitation explicit in-app. | `backtesting.py` is single-series and cannot model legs/Greeks/payoff; options are correctly the project's last priority (Phase 13), but users must not assume the equity engine covers them. | Medium | XL |
| **Survivorship / point-in-time index constituents** (maintained historical-membership dataset) for universe/batch studies. | Universe runs over today's symbols overstate returns by excluding delisted names; full PIT is XL, so the now-roadmap caveat banner is the interim mitigation. | Medium | XL |
| **Indicator plugin registry** (`@register_indicator` decorator + `custom/` folder scan with AST validation) so users can plug proprietary indicators without editing the core if/elif chain. | Adding one indicator today means four edits to core files and users can't add their own (e.g. the proprietary RSI Hybrid); the design rule is "everything user-extensible" (Phase 6). Depends on the AST gate. | Medium | L |
| **Pyramiding / adds-to-winner support** (max adds + add sizing) reconciled with the single-entry trailing-stop state. | The `is_long`/`is_short` guard blocks scale-in and DCA-style trend entries; a deliberate one-position limitation worth lifting after partial exits land. | Medium | L |
| **Volume-participation / spread slippage model** (impact ~ size/bar_volume) in the same post-trade charge pass as statutory charges. | Flat-% slippage makes thin small-caps look as cheap to trade as NIFTY; an acceptable baseline today but understates cost for large/illiquid orders. | Low | M |
| **Opt-in forward-test / paper-trading path:** first "forward test on the latest N bars" reusing the compiled strategy, then a paper loop, then live via the existing broker adapters. | Brokers are download-only today; forward/paper testing is the eventual product direction toward a daily driver, behind explicit opt-in given solo-desk risk. | Medium | XL |
| **Optional VectorBT coarse pre-screen** for simple MA/RSI-style sweeps, handing finalists to `backtesting.py` for exact charge-accurate P&L. | Every trial runs a full event loop; a vectorized pre-screen could prune the search 10–100×, but the no-code set (Supertrend/HMM/Hurst) doesn't map cleanly to VBT so it's opt-in for a strategy subset. | Medium | XL |
| **Single serialized DB-writer** (queue consumed by one writer) so incremental per-worker result persistence doesn't hit "database is locked". | Latent: today's batch is single-writer/safe, but the moment workers persist incrementally (needed for checkpoint/resume) multi-writer WAL lock errors appear (Phase 10). | Low | M |
| **Regime-conditional performance reporting** (group trade returns by HMM state or ADX/vol terciles) reusing the existing HMM indicator and the `yearly_performance` groupby idiom. | Shows whether edge is concentrated in one regime (fragile) or broad (robust); the HMM indicator already exists but performance is never reported by regime. | Medium | M |
| **Exposure/turnover/cost-drag reporting** (turnover vs equity combined with per-trade charges) and time-in-market. | Ties directly to the app's cost-accuracy value prop — shows how much of gross return the charge-heavy churn ate; depends on the real charge engine landing first. | Medium | M |
| **Comprehension & footgun fixes:** plain-language `help=` tooltips on high-traffic widgets, an in-app Glossary page, a stale-compiled-strategy marker (hash current builder state vs compile-time), and plain-language confirms on destructive deletes. | Stale compiled code silently backtests old logic, and single-click deletes can wipe saved strategies. Low effort, steady payoff. | Low | S |
| **R-multiple distribution and alerts** (last-bar signal badge; notification on batch/optimize completion via existing `progress_cb`). | R-multiples evaluate edge independent of size; a "last bar fired BUY" badge and run-complete notification are genuinely useful for a solo trader running daily scans or overnight sweeps. | Low | M |

---

## Detailed Findings by Dimension (Appendix)

Raw findings from each reviewer, grouped by dimension. Categories: `missing` (absent), `should_add` (present but inadequate), `can_add` (nice-to-have).

### 1. Backtest engine realism & execution — *Quant execution engineer*

**Current state:** A thin wrapper over `backtesting.py`. `engine.make_backtest()` builds `Backtest(df, cls, cash, commission=total_commission, margin, finalize_trades=True)`. Cost model is a single flat fraction (`total_commission = brokerage + slippage`, default 0.0005) applied symmetrically to both legs — no STT/stamp/GST/exchange/SEBI breakdown, no buy/sell asymmetry, no per-segment rates. Orders fill at next bar's open (no look-ahead); static SL/TP fill intrabar correctly, but the custom trailing stop triggers on Close only. No session handling — resample bins to `'{minutes}min'` with no origin anchor and can bridge the overnight gap; no intraday square-off. Sizing is fixed-₹//price or 99% equity, lot-rounded; one global margin. No multi-asset portfolio (batch averages isolated full-capital runs). Optimizer is pure in-sample. Corporate actions adjust equities only; no futures roll; options is a live-chain viewer with no backtest path.

| Gap | Category | Impact | Effort |
|---|---|---|---|
| India statutory charges (STT/stamp/GST/exchange/SEBI) not modeled — only a flat symmetric %. Best done as a post-trade charge pass over `stats['_trades']` with `Decimal`. | missing | high | L |
| No intraday session handling or square-off; resample bridges the overnight gap. Add `origin`/`offset`, drop cross-session bins, optional forced square-off tied to MIS/CNC. | should_add | high | M |
| Optimizer has no walk-forward / OOS split (in-sample overfitting). Even a 70/30 split prevents the worst false confidence. | should_add | high | L |
| Custom trailing stop fires only on bar Close, not intrabar High/Low — overstates fills. High-water mark already uses High/Low; only the trigger comparison is wrong. | should_add | medium | M |
| No futures roll / continuous-contract construction (panama/ratio back-adjust off an expiry calendar) + roll-cost. | missing | medium | L |
| Single global margin/lot for all instruments — no real futures leverage/SPAN modeling. Move margin+lot to per-instrument config. | should_add | medium | M |
| No options backtesting (engine is single-series; no legs/Greeks/payoff). Needs a separate options simulator; fair to defer but flag the limitation in-app. | missing | medium | XL |
| No portfolio-level backtest — batch only averages isolated single-asset runs. Minimum viable: relabel the table; full engine is larger. | can_add | medium | XL |
| Slippage is a flat fraction, not a spread/volume-impact model. Optional volume-participation slippage in the same post-trade pass. | can_add | low | M |
| Survivorship / point-in-time universe not addressed for batch/index runs. Cheap mitigation: a caveat banner. | can_add | low | S |

### 2. Statistical rigor & overfitting controls — *Quant researcher (validation)*

**Current state:** Essentially zero overfitting controls. `run_single` reports in-sample stats directly. `_optuna_optimize` maximizes a metric over `n_trials` then re-runs `bt.run(**study.best_params)` on the identical DataFrame; `_grid_optimize` returns `backtesting.py`'s best in-sample. No train/test, walk-forward, or holdout anywhere (confirmed by grep — "deflated/probabilistic/bootstrap/monte carlo/benchmark/information ratio/out-of-sample/walk-forward/bonferroni" return nothing). `metrics.py` exposes only passthroughs plus `annualized_return` and `yearly_performance`. The UI shows "✨ Optimizer found best parameters" with optimized in-sample metrics as if honest. `ml/patterns.py` mines decision-tree rules on the same trades used to fit (threshold baseline×1.1, samples≥5).

| Gap | Category | Impact | Effort |
|---|---|---|---|
| Optimizer reports in-sample results with no OOS validation (overfitting by construction). Split inside `run_single`/`optimize`; show IS vs OOS with degradation %. | missing | high | M |
| Walk-forward optimization (rolling/anchored WFO). Building blocks exist; mostly orchestration over date-sliced frames. | should_add | high | L |
| Monte Carlo / bootstrap robustness of the result (1000 sims on trade returns; fan chart). | should_add | high | M |
| Deflated / Probabilistic Sharpe Ratio (correct for number of trials). Pure-Python; have Optuna maximize DSR. | should_add | high | M |
| Multiple-testing correction for the optimizer and the ML rule miner (binomial/Fisher + Bonferroni/BH; raise min samples). | should_add | high | M |
| Benchmark comparison: alpha/beta/IR vs Nifty (and buy & hold). At minimum surface the already-computed "Buy & Hold Return [%]". | should_add | high | M |
| Parameter-stability heatmap / sensitivity surface. Grid `return_heatmap=True` is already computed and discarded. | should_add | high | S |
| Regime-conditional performance analysis (HMM state or ADX/vol terciles). | can_add | medium | M |
| ML pattern miner trains and evaluates on the same trades (no holdout). Add stratified split / k-fold. | missing | medium | M |
| Per-trade significance / minimum-sample gating on headline metrics (binomial CI on Win Rate). | can_add | medium | S |
| Optimization "best params" presented without overfitting warning in the UI. | should_add | high | S |

### 3. Reporting depth & risk analytics — *Risk & performance-reporting specialist*

**Current state:** Shallow and dependent on what `backtesting.py` 0.6.5 returns plus a few helpers: `clean_stats_series` dumps the raw stats Series verbatim; `yearly_performance` gives per-calendar-year Return%/MaxDD%; `annualized_return`; `enrich_trades` adds `Entry_<indicator>` snapshots. The Results UI shows 8 tiles, the raw stats table, the yearly table, the enriched trade log (CSV-exportable), and a Plotly chart. `viz/charts.py` has only `render_plotly_backtest` and `feature_importance_bar` — no equity-curve, drawdown, distribution, heatmap, or rolling charts. `results.py` just `st.json()`-dumps the stored `full_stats`. The `backtests` table persists 5 scalars + a `full_stats` blob — the equity curve and trade log are NOT persisted. No `quantstats`/`empyrical` in requirements. Note: `backtesting.py` already computes Profit Factor, SQN, Sortino, Calmar, Expectancy [%] — present in the dump but never curated.

| Gap | Category | Impact | Effort |
|---|---|---|---|
| No QuantStats tearsheet / HTML export — the single highest-leverage addition. `stats['_equity_curve']['Equity']` is the exact input QuantStats needs (resample intraday→daily first). | should_add | high | M |
| No VaR / CVaR (expected shortfall) anywhere. Free once QuantStats is in. | should_add | high | S |
| No rolling Sharpe / volatility / drawdown charts. | should_add | high | S |
| No monthly-returns heatmap. | should_add | medium | S |
| No drawdown-period analytics (depth, start, valley, recovery, duration). `_equity_curve` already carries DrawdownPct/DrawdownDuration. | should_add | high | M |
| No MAE / MFE per trade (custom; not in `backtesting.py` `_trades`, not in `enrich_trades`). Compute from OHLC between EntryBar/ExitBar. | missing | high | L |
| No R-multiple distribution (requires stop distance per trade; SL% already persisted). | can_add | medium | M |
| Trade-level stats present in raw dump but never curated: Profit Factor, Expectancy, SQN. Pure surfacing. | should_add | medium | S |
| No Kelly / half-Kelly position-sizing output (half-Kelly is the documented default). | can_add | medium | S |
| No exposure / turnover / time-in-market reporting (ties to the cost-accuracy value prop). | can_add | medium | M |
| Results DB page can't reproduce deep analytics — equity curve & trade log are not persisted. Prerequisite multiplier. | should_add | high | M |
| No benchmark comparison (alpha/beta vs Nifty buy-and-hold) in reports. Pass `benchmark=` to QuantStats. | can_add | medium | M |

### 4. Data integrity & coverage — *Market-data engineer*

**Current state:** A clean port of the original Upstox monolith. Loading is centralised in `loader.py` with an (path, mtime)-keyed in-process cache returning `.copy()` — caching correctness is sound. `quality.py::validate` returns a 0–100 score over duplicate timestamps, bad OHLC, and zero-volume bars. `corporate_actions.py::apply_corporate_actions` back-adjusts splits/bonuses and injects Dividend/Rights_Premium. Multi-source masters supported (24h cache). Options chain download saves per-contract OHLCV+OI. **Critical gaps:** (1) `quality.validate()` is never called — backtester and batch worker feed raw data straight in; (2) corp-action adjustment is download-time-only, opt-in, Upstox-only, and its status is not persisted (`downloads_log` has no adjusted/isin/quality column); (3) `resample_dataframe` drops Dividend/Rights_Premium.

| Gap | Category | Impact | Effort |
|---|---|---|---|
| Data-quality gate is never enforced before a backtest. Call `validate()` in both entry points; surface + store the score. | should_add | high | S |
| Corporate-action adjustment status not persisted or surfaced at backtest time. Add `adjusted`/`quality_score` columns + banner. | should_add | high | M |
| Resampling drops Dividend/Rights_Premium → dividend cashflows lost on any non-1min file. Sum within each bar. | should_add | high | S |
| No survivorship / point-in-time index constituents. Full PIT is XL; minimum viable is a caveat banner. | missing | high | XL |
| No trading-calendar / holiday awareness for gap detection. Add `data/calendar.py`; extend `validate` with expected-vs-actual bars. | should_add | medium | M |
| Reproducibility hashes (dataset/config/strategy/code) absent. Add columns to the backtests insert. | should_add | medium | M |
| Options data is per-contract OHLCV only; no chain-level / continuous / Greeks depth. | can_add | medium | L |
| Only OHLCV(+OI) bars; no tick / L1 / quote data (partly an engine constraint). | can_add | low | XL |
| Batch worker bypasses the central loader (no cache, no quality, divergent read path). Route through `load_indexed`. | should_add | medium | S |
| Quality penalty model is crude and can't HARD-BLOCK. Add warn/block thresholds. | can_add | low | S |

### 5. Strategy & portfolio capabilities — *Systematic strategy designer*

**Current state:** A no-code builder compiling a single `backtesting.py` Strategy on ONE instrument / ONE timeframe per run. `models.py` (StrategyConfig) → `codegen.build_strategy_code` → `compiler.compile_strategy`. 21 indicator types, chainable via `source`; no custom-Python-indicator path. Sizing is one-shot (fixed-₹//price or compound-100%-equity, lot-rounded); entries guarded by `is_long`/`is_short` (no pyramiding); every exit is a full `position.close()` (no partial exits). Stops: static %SL/TP + close-based %-trailing; range_zones can tighten/block/close. Direction: Long/Short/Both. No multi-symbol (batch runs independently, one AVERAGE row). No multi-timeframe in a compiled strategy. `results.py` is a per-run browser with no leaderboard. No alerts.

| Gap | Category | Impact | Effort |
|---|---|---|---|
| No partial exits / scale-out (every exit is a full `position.close()`). `backtesting.py` supports `trade.close(portion)`. | should_add | high | L |
| No pyramiding / adding to a winning position (the `is_long` guard blocks scale-in). | can_add | medium | L |
| Stop/target richness is thin: only static %SL/TP and %-trailing. Add ATR-stop, breakeven, time/session-based exits. | should_add | high | M |
| No portfolio backtest: multi-symbol with shared capital, allocation & correlation guard. Largest strategic gap. | should_add | high | XL |
| No multi-timeframe strategies (HTF confirmation in entry logic). Inject a resampled forward-filled HTF series via `self.I`. | should_add | high | L |
| No strategy comparison / leaderboard across runs (data already persisted — mostly a UI query). | should_add | high | S |
| No custom-indicator plug-in path (limited to 21 built-ins). A registry via `compiler._namespace()`. | should_add | medium | L |
| No alerts (signal/threshold/run-complete notifications). Ride the existing `progress_cb`. | can_add | low | M |
| Optimizer has no overfitting guard (no walk-forward / OOS split). | should_add | high | M |
| Position sizing is binary: fixed-₹ or 100%-equity (no risk-based sizing). Add "Risk % per trade". | should_add | medium | M |
| No benchmark comparison (alpha/beta vs Nifty buy-and-hold). | can_add | medium | S |

### 6. Architecture, extensibility & scale — *Software architect*

**Current state:** A clean, layered refactor of the monolith. Backend is strictly Streamlit-free and ProcessPool-importable. No-code → codegen → `exec()` pipeline: `build_strategy_code()` emits a Strategy source string; `compile_strategy()` compiles+execs it into a fresh namespace with a bytecode cache keyed on `hash(code)`. `engine.run_single` wraps `Backtest`; `optimizer.py` supports grid + Optuna; `batch.py` fans `worker.run_worker` across files via ProcessPoolExecutor. `db.py` is the single SQLite layer (WAL + FK + busy timeout). Indicators are a registry (if/elif chain + spec dicts). Observability is a stdlib RotatingFileHandler. Tests: `test_units.py` + `test_parity.py`. **Absent:** reproducibility hashing, checkpoint/resume, VectorBT fast path, AST validation, run_id-tagged records. The backtests table stores return/dd/winrate/sharpe + a config blob but no data identity, cost params, or code version.

| Gap | Category | Impact | Effort |
|---|---|---|---|
| No reproducibility hashes on stored backtest runs. Reuse `hash(code)`; `pd.util.hash_pandas_object` for data; fix empty start/end in `batch.py:118`. | should_add | high | M |
| No checkpoint/resume for long optimizations or folder batches. Persistent Optuna study + incremental batch persistence. | should_add | high | M |
| `exec()` of generated source has no AST validation or output safety scan; imported presets are unvalidated trusted JSON. | should_add | high | M |
| No optional VectorBT fast path for large parameter sweeps (only a strategy subset maps cleanly). | can_add | medium | XL |
| Indicator extension requires editing a core if/elif chain (no plugin registry). | should_add | medium | L |
| Logger is not run_id-tagged; no per-run metrics or error reports. `worker.py:73` truncates tracebacks to 100 chars. | should_add | medium | M |
| Thin/absent automated test coverage; both files hard-depend on an absolute `D:\simpleton` path. | should_add | high | M |
| No packaging metadata (pyproject/requirements) in the modular package; `pandas_ta` is brittle and unpinned. | should_add | medium | S |
| SQLite concurrent-write contention under multi-worker batch persistence (latent until incremental writes land). | can_add | low | M |
| Optimizer cannot be cancelled and lacks WFO / OOS guard. | can_add | medium | L |

### 7. Usability & product direction for a non-developer solo trader — *Product / UX specialist*

**Current state:** A 13-page Streamlit dashboard dispatched by `app_main.py`, covering data download, a no-code Strategy Builder, a Plotter, a Backtester+Optimizer, a Results DB browser, and an ML Pattern Finder. Genuinely no-code and Optuna/Grid-capable. CSV export for trade logs and ML sheets; a ZIP backup of DB+data. **Gaps:** no onboarding (opens straight to a broker-auth sidebar + 13-item radio nav); no simple/advanced progressive disclosure (Strategy Builder is one long scroll exposing range zones/regime filters/4 risk modes at once); weak explainability (`results.py` dumps raw metrics via `st.json`; no plain-language interpretation or pass/fail coloring); no trade replay; no A/B comparison; CSV/ZIP-only export; flat commission hides the India charge breakdown; no live/paper-trading path.

| Gap | Category | Impact | Effort |
|---|---|---|---|
| No onboarding / guided first-run flow. Add a Home/Get-Started page as NAV[0] with cards + a dismissible checklist. | should_add | high | M |
| No simple-vs-advanced progressive disclosure in Strategy Builder. Collapse Steps 2.5/2.75 + advanced exits; ship a starter template. | should_add | high | M |
| Results page is a raw JSON/table dump, not explainable. Inline plain-language guidance + green/amber/red thresholds. | should_add | high | M |
| No trade replay / bar-by-bar step-through (enriched snapshots already exist). | should_add | high | L |
| No saved comparison / A-B of backtest runs (needs equity curve persisted). | should_add | high | M |
| No PDF / Excel / India-tax export — only CSV trade log + ZIP backup. | should_add | medium | L |
| Flat commission hides India charge breakdown (STT/stamp/GST). Itemize as a post-processing pass + Gross-vs-Net panel. | should_add | high | L |
| No live / paper-trading path (brokers are download-only). Sequence: forward-test → paper → live. | can_add | medium | XL |
| Sparse tooltips and no in-app glossary. Add `help=` + a Help/Glossary page. | can_add | medium | S |
| Two-step coupling: build → compile → switch page to backtest; stale compiled code silently backtests old logic. | can_add | medium | S |
| Destructive actions lack plain-language confirmation. Gate deletes behind a confirm. | can_add | low | S |

---

## North Star

> Become the trustworthy, ₹-accurate, no-code backtesting desk for a solo Indian equity/futures trader: **every number the app shows is honest** — validated out-of-sample, charged with real India statutory costs, run on quality-gated reproducible data — and **explained in plain language**. Power features are layered behind progressive disclosure, never removed.

---

## Risks & Tradeoffs

- **`backtesting.py` is single-series and bar-based by design.** It caps true multi-asset portfolio sim, options/multi-leg payoff, and intrabar/microstructure fidelity. Several "later" items (portfolio, options, tick data) cannot be built on it and need a second engine or custom loop — scope accordingly rather than forcing them onto `backtesting.py`.
- **Overfitting is currently structural, not incidental.** The optimizer, the ML rule miner, and the lack of benchmarking all push users toward deploying overfit strategies. Until IS/OOS + deflated Sharpe + WFO land, the optimizer's "best params" should carry an explicit in-sample warning — fixing the UI label is as important as fixing the math.
- **`exec()` of generated source is safe ONLY while every input stays widget-constrained.** Any future free-text field (custom formula, label) or a hand-edited preset JSON is direct RCE. The AST whitelist + preset schema validation must land **before** any user-extensibility feature (custom indicators, custom formulas), not after.
- **The India charge engine must use `Decimal` and correct buy/sell asymmetry and segment rates.** A subtly wrong cost model is worse than a flat one because it looks authoritative. Validate against worked examples (and the eventual ITM-STT mandatory test) before surfacing net P&L as truth.
- **QuantStats annualizes assuming daily periodicity.** Feeding intraday returns silently inflates Sharpe/CAGR. The `equity_returns` helper MUST resample to daily for intraday data; getting this wrong produces confident, wrong tearsheets.
- **Reproducibility hashes and equity-curve persistence are cheap now and near-impossible to retrofit.** Deferring them means every result accumulated until then stays untrustworthy and un-regenerable — they belong in the "now" bucket despite feeling like infrastructure.
- **Data-quality and corporate-action gaps are silent.** Unadjusted splits look like crashes and dropped dividend cashflows make timeframes disagree. These erode trust invisibly, so surfacing status (banners, flags, persisted columns) matters as much as the underlying fix.
- **XL items are partly gated on external data** (historical constituents, expiry calendars). Treat portfolio/options/PIT-constituents/forward-trading as multi-step initiatives with interim caveat banners, not single deliverables.
