# CURRENT STATUS — QuantDesk (modular) — resume doc

**Last updated:** 2026-06-28 · **Current version:** **v7.5** · Machine-readable twin: [`progress.json`](progress.json) · Granular file index: [`modular.json`](modular.json)

> **Read this first after a context reset.** It captures everything needed to continue without re-deriving state.

---

## 1. What this is
A modular rebuild of an independent app: the **Upstox "Unified Market Data Downloader & Quant Engine"** — a 13-page Streamlit dashboard to download Indian market data (Upstox/Zerodha/Dhan) and build/backtest/optimise/ML-mine no-code strategies.

- **Original** single-file app: `D:\mbs\historydownloader\upstox_app.py` (3,207 lines) — **retained, never edited**.
- **This (modular) app:** `D:\mbs\historydownloader\modular history\` → package `quantdesk/` (57 modules) + thin `app_main.py`.
- **Build is complete and shipped through v7.5.** Not a work-in-progress refactor — it's a maintained product now.

## 2. Where it's published
| Repo | Visibility | State | Purpose |
|---|---|---|---|
| [gauravrakhecha/historytester](https://github.com/gauravrakhecha/historytester) | private | `main`, tag **v7.5** | main repo (v3 monolith at root + `modular history/` subfolder) |
| [gauravrakhecha/sambhav](https://github.com/gauravrakhecha/sambhav) | private | synced to **v7.5** | dedicated **collaborator** repo (separate repo = true isolation; GitHub has no per-branch read access) |

Tags: … → v7.0 (Feature Maturity: A+B+C) → v7.0.1 (perf: page-switch lag fix) → **v7.5 (Trustworthy Core pt.1: security AST-whitelist + charges-in-equity)**. Only `main` exists on historytester (old sambhav/sabhav branches deleted).

## 3. How to run & verify
```bat
:: run  (from "modular history")
run.bat            ::  or  <venv>\python -m streamlit run app_main.py   -> localhost:8501

:: verify  (use the simpleton venv: D:\simpleton\historydownloader\.venv\Scripts\python.exe)
set PYTHONIOENCODING=utf-8
<venv>\python -m pytest tests\ -q -p no:cacheprovider        ::  41 tests (~3 min; parity ~85s)
<venv>\python tests\verify_app.py                            ::  AppTest: all 13 pages + a UI backtest
<venv>\python tests\verify_features.py                       ::  plotter / builder / settings / optimizer flows
```
- **Verification data:** `D:\simpleton\historydownloader\data` (real RELIANCE/RECLTD/…). Seed the app DB's download log via `data.recovery.rebuild_db_from_folder(DATA_DIR)` if dropdowns are empty.
- **Headless screenshots don't work here** (kaleido can't launch Chrome; Plotly/canvas render blank in the Preview sandbox). Trust AppTest + structural tests; use a real browser for visuals.

## 4. Key versions / env
Python 3.12 (uv venv) · streamlit 1.58 · plotly 6.8 · bokeh 3.9.1 (transitive) · backtesting 0.6.5 · **pandas ≥ 3** · quantstats 0.0.81. Secrets in `D:\mbs\historydownloader\.env` (Upstox keys) — never commit.

## 5. What's DONE (v6 + v6.1 highlights)
- **Speed:** master-list `cache_resource` + session cache + vectorised classifier + 24h disk cache; selector no longer rescans 141k rows per keystroke; WAL once; vectorised resample.
- **Audit fixes (from a multi-agent audit):** ML Finder crash (`ReturnPct`), short trailing-stop ratchet (+ latent crash), tighten-zone dead code, charges double-count, Optuna int params, presets dropping sizing, yearly drawdown sign, quality gate enforced in engine, content-based dataset hash.
- **Corporate-actions** split download crash (int64 Volume on pandas 3) fixed + tests.
- **Saving:** Parquet / CSV / **Both** + optional separate CSV folder; per-page Parquet & CSV folder inputs.
- **Charts (v6.1):** oscillators in their own stacked panels (guide lines), overlays on price, range-scaled markers, WebGL, candle downsampling, intraday gap-collapse; optional engine-native Bokeh "Pro chart" toggle.

## 6. Decisions that are settled (don't re-litigate)
- **Keep backtesting.py** (no Nautilus/VectorBT). **Keep Plotly** (no Bokeh / no TradingView Lightweight Charts — debated and dropped; the messiness was a design bug, now fixed).
- Indicator overlay-vs-panel is single-sourced in `config.INDICATOR_PLACEMENT`.
- Default save format = Both. Local tools work without broker login.

## 7. TO CONTINUE — ⭐ v7.1 "Trustworthy Core" IN PROGRESS → see [`ROADMAP_v7.1_trustworthy_core.md`](ROADMAP_v7.1_trustworthy_core.md)

> After a multi-agent quant-desk audit scored the app **4.8/10** (verified), we chose to fix the top
> 3 *wrong/dangerous* items (v7.1) BEFORE building the data agent (v8, [`ROADMAP_v8_data_agent.md`](ROADMAP_v8_data_agent.md)).
> - **✅ Task 1 — AST-whitelist + preset validation (security), DONE 2026-06-28 (local, unpushed):**
>   closes the preset-upload RCE. New `quantdesk/strategy/validation.py` (`assert_safe_ast` in the
>   compiler + `validate_preset` at the upload/load door). `tests/test_security.py` (21); full suite
>   **88 green** (no AST false positives) + AppTest.
> - **✅ Task 2 — Charges-in-equity, DONE 2026-06-28 (local, unpushed):** `costs.india.make_fee_model(segment)`
>   → a backtesting.py commission callable; threaded `segment`/`slippage` through
>   engine.make_backtest/run_single/native_bokeh_html + wfo.walk_forward + the backtester UI, so
>   Return/Sharpe/DSR/WFO/MC are all net of real charges (Gross-vs-Net panel is now a reconciliation).
>   `tests/test_charges_in_equity.py` (7); full suite **95 green** + AppTest. Real drive: a strategy's
>   gross 20.8%/0.79 Sharpe/95% PSR → 0.7%/0.03/55% net of ₹20k charges.
> - **▶ Task 3 — NEXT (in progress):** `quantdesk/risk/` module (risk-% & ATR sizing, ATR/time stops, max-DD circuit-breaker).
> **Tasks 1 & 2 RELEASED as v7.5** (2026-06-28); Task 3 follows in the next tag. (Target 4.8→~6.0.)

### Earlier: v7 Feature Maturity RELEASED (A+B+C, v7.0; perf fix v7.0.1) → see [`ROADMAP_v7.md`](ROADMAP_v7.md)
A multi-agent maturity audit confirmed the user's critique: **Backtester = usable, Optimizer = amateur,
ML Pattern Finder = amateur/toy** (ML trained on raw absolute features that don't generalize across assets).

- **✅ Phase A — DONE (local, unreleased; 45 tests green):** added 6 normalized indicator types —
  **Ratio to SMA** (use Volume source for `vol/SMA20(vol)`), **Distance from MA (ATR)**, **MA Slope (ATR)**,
  **Z-Score**, **Percentile Rank**, **Return (n-bar)**. Implemented as registry types (config placement/guides
  + `indicators.py` types/param-specs/helpers + `emit_indicator` + compiler namespace), so the **builder,
  ML finder, and plotter all get them** and they render in their own panels. Tests: `tests/test_features.py`.
- **✅ Phase B — DONE (2026-06-28, local, unreleased; 56 tests green):** ML rigor in `ml/patterns.py` +
  `ml_finder.py`. (1) ML finder **defaults to the normalized cross-asset features** (`partition_features`
  in `indicators.py` + `CROSS_ASSET_COMPARABLE_TYPES`); warns + **per-instrument z-scores raw/absolute
  features** before bulk concat (`batch.extract_trades` now tags each frame with `Source`). (2)
  **Time-ordered holdout (70/30 default) or walk-forward k-fold** — rules mined on train, scored OOS.
  (3) **Significance gate**: per-leaf sample-size floor (config `ML_SAMPLE_FLOOR=30`) → one-sided binomial
  vs OOS baseline → **Benjamini-Hochberg FDR** (`ML_FDR_ALPHA=0.10`) → **Wilson lower-bound** must still
  beat baseline. Only survivors → `rules`; the rest → `candidate_rules` (exploratory, with a reject reason).
  XGBoost importance stays descriptive (full matrix, not gated). Config defaults in `config.py`. Tests:
  `tests/test_ml_rigor.py` (11: Wilson/binomial/BH primitives, edge-recovered-and-passes, noise→none,
  k-fold, per-instrument normalization, partition_features, API back-compat). Parity unaffected (legacy
  `mine_patterns` positional call + old ML tests still green). Verified end-to-end on real RELIANCE trades
  (defaulting, Source tagging, holdout, gate all correct) + AppTest renders ml_finder.
- **✅ Phase C — DONE (2026-06-28, local, unreleased; 67 tests green):** optimizer validation.
  (1) **Probabilistic & Deflated Sharpe** in `backtest/metrics.py` (`probabilistic_sharpe_ratio`,
  `expected_max_sharpe`, `deflated_sharpe_ratio`, `sharpe_moments`, `validation_stats`) — DSR deflates
  the winner's Sharpe for the number of configs tried (Bailey/LdP). (2) **Parameter-stability surface**:
  `optimizer.optimize` now returns `(stats, best, meta)` with the grid `return_heatmap` (was discarded)
  + Optuna trials/per-trial Sharpes; `engine.run_single` carries it as `info['opt_meta']`; rendered via
  `charts.parameter_stability_heatmap` (1-D line / 2-D heatmap). (3) **Walk-forward** `backtest/wfo.py`
  (anchored/rolling: re-optimize per IS window, trade frozen params OOS, stitch OOS equity, report WFE).
  (4) **Monte-Carlo bootstrap** (`metrics.monte_carlo_bootstrap` + `charts.monte_carlo_fan`): resample
  the trade sequence → P5/P50/P95 final-equity & worst-drawdown bands + fan chart. Optuna gains a
  "Probabilistic Sharpe (PSR)" objective. Backtester page surfaces all four (validation panel + stability
  expander + Monte-Carlo expander + Walk-Forward section). Tests: `tests/test_optimizer_validation.py`
  (11). Verified end-to-end on real RELIANCE data (PSR/grid-heatmap/Optuna-DSR/MC-bands/WFE all correct —
  the EMA-crossover's strong IS / weak OOS is correctly flagged by DSR 27% & WFE −1.2).
- **Released as v7.0 (2026-06-28):** Phases A+B+C shipped together as the "Feature Maturity" milestone
  (the earlier v7.0-alpha pre-release was Phase A only). Pushed to historytester `main` + tag `v7.0` +
  GitHub release; sambhav synced to v7.0. Details in `ROADMAP_v7.md`.
- **⏩ Perf fix — page-switch lag (SHIPPED as v7.0.1, 2026-06-28):** opening the Plotter/Backtester
  was freezing ~6s on first visit because `compiler.py` (→ `backtesting`+`bokeh` ~2.3s, `pandas_ta`
  ~1.8s) and `viz/charts.py` (→ `quantstats` ~4.9s) imported those heavy libs at MODULE TOP, so the
  page-module import paid for them during the nav switch. Made all three **lazy** (compiler imports
  inside `_namespace()`; charts loads quantstats on demand via `_qs_module()`/`quantstats_available()`;
  `engine.make_backtest` + plotter button-handler import `backtesting` on use). Page import 6.08s→**0.91s**;
  heavy libs now load on first compile/run/tearsheet, under existing spinners. 67 tests green; AppTest OK.
  Pushed to historytester `main` + tag `v7.0.1` + GitHub release; sambhav synced.

**Other backlog (full list in `progress.json` → `deferred_roadmap`):** trade analytics (MAE/MFE, R-multiple,
Kelly) · Trade Replay · benchmark engine (alpha/beta vs Nifty) · partial exits / ATR stops / risk-% sizing /
multi-timeframe / intraday square-off · Strategy-Builder delete-row index drift · quality-gate missing-bar
detection · batch rows persisting hashes/equity · loader tz · portable tests (drop hardcoded `D:\simpleton`) ·
PDF/Excel export · results delete confirm.

**⚠ Security gate:** `compiler.py` `exec()`s generated code — safe only while inputs are widget-constrained. Add an AST whitelist + preset-schema validation **before** any user-extensibility / custom-formula feature.

## 8. Routine ops
- **Update the file index:** `MODLOG_AGENT=orchestrator <venv>\python .workflow/modlog.py log <agent> "<msg>"` (also `file/feature/speed/phase/agent`). Writes `modular.json`.
- **Publish a new version & sync sambhav:** see `progress.json` → `how_to_publish_new_version` and `repos.sambhav.sync_procedure` (clone → sync working tree → secret-scan → commit/tag/release → push; then mirror into sambhav).
- **Invite collaborator:** `gh api -X PUT repos/gauravrakhecha/sambhav/collaborators/<username> -f permission=pull` (or `push`).
