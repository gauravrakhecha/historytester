# Architecture — Quant Engine (Modular Edition, v4)

This document explains how the modular app is structured, how data flows through it, and how
to extend it. It is the v4 refactor of the original single-file `upstox_app.py` (kept at the
repo root as the v3 reference). **Behaviour is identical** to v3 — verified by a numeric parity
harness — but the code is split into small, testable modules and runs faster.

## Design principles

1. **One concern per module.** UI, data access, broker I/O, strategy codegen, the backtest
   engine, optimisation, ML, and charts are separate packages.
2. **Backend never imports Streamlit.** Everything under `quantdesk/` except `ui/` is import-safe
   from a multiprocessing worker or a pytest run. The cache layer (`core/cache.py`) only touches
   Streamlit when a live script run context exists.
3. **Parity first, speed second.** Generated strategy code is a faithful port of v3. The only
   numerical change — vectorised ALMA / Linear-Regression-Slope / R-Squared — is proven
   byte-identical to the originals (`tests/test_parity.py`).
4. **Nothing hard-coded that the user owns.** Paths, broker keys, costs and instrument lists live
   in `config.py` / the settings table / `.env`, never inline.

## Package map

```
app_main.py                Streamlit entry: page config → auth sidebar → nav → dispatch
quantdesk/
  config.py                env, paths, constants (index lists, indicator output suffixes)
  core/
    db.py                  single SQLite layer (schema, connect(), repos) — same tables as v3
    settings_repo.py       cached settings accessor + backtest_params() bundle
    logging.py             rotating file + console logger
    cache.py               Streamlit-or-not cache shim (worker/test safe)
    reproducibility.py     dataset/config/strategy/code content hashes
  data/
    loader.py              read csv/parquet (mtime-cached); load_ohlcv / load_indexed
    resample.py            vectorised per-session timeframe resampling
    corporate_actions.py   split/bonus back-adjust + dividend/rights accrual (dtype-safe)
    master_list.py         unified instrument master + asset classifiers (cache_resource + disk cache)
    quality.py             data sanity report (enforced as a gate)
    recovery.py            rebuild the download log by scanning a folder
    saver.py               write downloads as Parquet / CSV / Both (+ separate CSV dir)
  costs/india.py           Decimal India statutory charges (STT/stamp/GST/exchange/SEBI)
  brokers/
    base.py                adapter seam + RetryableError
    upstox.py / zerodha.py / dhan.py   one fetch_single() per broker (+ Upstox options)
    downloader.py          async orchestrator: chunking, semaphore, RAM-safe spooling
  strategy/
    models.py              StrategyConfig + safe_name + selectable_columns
    indicators.py          indicator registry (codegen) + param specs + vectorised helpers
    codegen.py             build a backtesting.py Strategy class source from a StrategyConfig
    compiler.py            exec source → Strategy class (bytecode-cached, fresh class per call)
    presets.py             save/load/import/export strategy & indicator presets
  backtest/
    worker.py              top-level ProcessPoolExecutor worker (spawn-safe)
    engine.py              single run (+ optional optimisation)
    optimizer.py           Optuna (Bayesian) + Grid search
    batch.py               folder batch + feature extraction + DB persistence
    metrics.py             safe stats, annualised return, year-wise table, trade enrichment
  ml/patterns.py           pure XGBoost importance + decision-tree rule mining
  viz/charts.py            Plotly: backtest chart + feature-importance bar
  ui/
    components.py          auth sidebar, instrument selector, nav, PageContext
    pages/*.py             one render(ctx) module per dashboard page (13 pages)
```

## Data flow (a backtest)

```
download (brokers/downloader) ──► parquet/csv on disk ──► data/loader.load_indexed
                                                              │
strategy builder (ui) ─► StrategyConfig ─► strategy/codegen ─► strategy/compiler ─► Strategy class
                                                              │
                                          backtest/engine.run_single (backtesting.py)
                                                              │
                            backtest/metrics (stats, yearly, enriched trades) ─► ui + core/db
```

For folder batch & ML, `backtest/batch.py` fans the same worker across files with a
`ProcessPoolExecutor`; `ml/patterns.py` then mines the aggregated, indicator-enriched trades.

## Speed changes vs v3

| Area | v3 | v4 |
|------|----|----|
| ALMA / LR-Slope / R² | per-window `rolling().apply()` (Python) | vectorised numpy (proven equal) |
| Strategy compile | `exec()` re-parsed every run/trial | bytecode cached; fresh class per call |
| Settings | fresh `sqlite3.connect` in ~6 places | one cached accessor |
| Data load | re-read from disk every run | (path, mtime) cached |
| Trade enrichment | per-row Python list comprehension | vectorised `numpy` indexing |

## Extending it

- **New indicator:** add a branch in `strategy/indicators.py:emit_indicator` and an entry in
  `INDICATOR_TYPES` / `INDICATOR_PARAM_SPECS`. Multi-output indicators register their columns in
  `config.INDICATOR_OUTPUT_SUFFIXES` so every page picks them up automatically.
- **New broker:** add `brokers/<name>.py` with `async def fetch_single(...)` and register it in
  `brokers/downloader._FETCHERS`.
- **New page:** add `ui/pages/<name>.py` exposing `render(ctx)` and an entry in
  `ui/components.NAV`.

## Verification

- `tests/test_units.py` — loader/resample/quality/indicator-equality/codegen/presets/ML.
- `tests/test_parity.py` — vectorised vs original indicator forms produce identical metrics.
- `tests/verify_app.py` — Streamlit AppTest renders every page + runs a backtest through the UI.
- `tests/verify_features.py` — drives plotter / builder / settings / optimizer flows.
- `modular.json` — the file-by-file build & verification index (status + loop log).
