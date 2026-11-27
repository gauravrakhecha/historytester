# Architecture — QuantDesk (Modular Edition, v9.0)

This document explains how **QuantDesk v9.0** is structured, how data and execution flow across layers, and how to extend modules cleanly without introducing tight UI or third-party server coupling.

## Design Principles

1. **One concern per package.** Data ingestion/cataloging, broker HTTP adapters, no-code strategy compilation, risk/cost modeling, backtesting/optimization, ML rule mining, OpenAlgo canvas visualization, live execution, and Streamlit UI pages live in separate subpackages.
2. **Backend never imports Streamlit.** Everything under `quantdesk/` outside `quantdesk/ui/` (except optional Streamlit cache shims in `core/cache.py` guarded by script-context detection) runs cleanly inside headless scripts, multiprocessing workers, CI tests, and optional Flask endpoints.
3. **Zero core dependency on OpenAlgo.** QuantDesk never imports `openalgo` in core strategy, backtest, data, risk, or live modules. It bundles `openalgo-charts` v2.7.5 (Apache-2.0) locally for offline canvas rendering and communicates with OpenAlgo Web / OpenAlgo Desktop strictly over its HTTP JSON v1 contract (`127.0.0.1:5000`).
4. **AST-sandboxed strategy pipeline & parity.** Every strategy passes `validate_preset()` and `assert_safe_ast()`, and `Custom Formula` blocks pass `assert_safe_formula()` before compilation. Vectorized helpers preserve mathematical parity with baseline implementations (`tests/test_parity.py`).
5. **Nothing user-owned is hard-coded.** Paths, broker credentials, capital, brokerage/slippage, and OpenAlgo endpoints come from `quantdesk/config.py`, SQLite `settings`, or gitignored `.env`.

## Package Map

```
app_main.py                          Thin Streamlit entry shell (auth sidebar, 16-page NAV, dispatch)
pyproject.toml                       PEP 621 package metadata (v9.0.0), optional [openalgo], quantdesk-live CLI
install.bat / run.bat                Windows uv virtual-environment setup & launcher
quantdesk/
  config.py                          Paths, defaults, indicator placement & panel guide maps
  core/
    db.py                            Centralized SQLite WAL layer (downloads_log, bulk_templates, backtests, settings, ml_patterns)
    settings_repo.py                 Cached settings repository + backtest_params()
    logging.py                       Rotating logger + per-run JSON error reports (.workflow/errors/)
    cache.py                         Streamlit/headless-safe cache decorator
    reproducibility.py               SHA reproducibility hashes for dataset, config, strategy, code
  data/
    loader.py                        tz-naive IST normalization & mtime-cached CSV/Parquet reader
    resample.py                      Vectorized per-session OHLCV resampling
    corporate_actions.py             Split/bonus/dividend/rights adjustment
    master_list.py                   Unified exchange master loader (Upstox & OpenAlgo mappings + offline fallback)
    quality.py                       NSE trading-calendar missing-day detector & data quality scoring
    recovery.py                      Scan folders to rebuild downloads_log
    saver.py                         Dual Parquet/CSV writer with customizable output dirs
    catalog.py                       Coverage catalog, provisional-bar-safe incremental top-up, bulk ZIP export
  costs/
    india.py                         Decimal India statutory fees (STT, stamp, exchange, SEBI, brokerage, GST)
  brokers/
    base.py                          Rate-limit/auth exception hierarchy (RetryableError, NonRetryableError)
    upstox.py                        Upstox async historical candles + expired options chain endpoints
    openalgo.py                      Strict Marshmallow OpenAlgo REST v1 adapter (/history, /search, /ping, /analyzer)
    zerodha.py / dhan.py             Legacy direct adapters retained for backward test coverage
    downloader.py                    Async chunked downloader with semaphore, exponential backoff + jitter
  risk/
    sizing.py                        Fixed value, % equity, Risk %, ATR risk, Half/Full Adaptive Kelly
    stops.py                         ATR stop, breakeven stop ratchet
  strategy/
    models.py                        StrategyConfig, RiskManagement, RULE_TIMINGS, safe_name, classify_columns
    indicators.py                    31 indicator specs + vectorized helpers + AST-safe Custom Formula evaluator
    codegen.py                       Python Strategy class generator (grouped logic, NOT, lookback timing, entry time window)
    compiler.py                      AST-gated bytecode compiler (fresh Strategy subclass per run)
    templates.py                     5 Starter Templates, plain-English translator, sanity warning checker
    validation.py                    Preset validator + AST sandbox + formula safety checks
    presets.py                       Safe JSON save/load/delete under strategies/ & indicator_presets/
    openalgo_export.py               Standalone secret-free OpenAlgo Python bot exporter
  backtest/
    engine.py                        Single backtest & OOS optimization orchestration + quality gate
    optimizer.py                     Optuna Bayesian & Grid Search parameter optimizer
    wfo.py                           Walk-Forward Optimization (anchored/rolling IS -> OOS stitch)
    batch.py                         Parallel folder batch backtester + feature extraction + DB logging
    worker.py                        Spawn-safe ProcessPoolExecutor worker
    metrics.py                       PSR/DSR, Monte Carlo bootstrap, MAE/MFE/R-multiple, India tax breakdown CSV
  ml/
    patterns.py                      XGBoost importance + OOS holdout/k-fold Wilson & BH-FDR rule mining
  viz/
    assets/openalgo-charts/          Vendored offline openalgo-charts v2.7.5 standalone bundle
    openalgo_chart.py                Offline OpenAlgo HTML/JS chart adapter (overlays, panels, markers)
    openalgo_terminal.py             Pro Chart Terminal (Bar Replay HUD, linked Equity/DD, Trade Focus PriceLines)
    charts.py                        Plotly multi-panel charts & re-exports
  live/
    openalgo_client.py               Strict Marshmallow REST client (/funds, /orderbook, /positionbook, /tradebook, /placeorder)
    risk_guard.py                    Pre-trade kill switches (daily max loss, max trades, max open positions, cooldown, live confirm)
    signal_runner.py                 Parity signal evaluator, isolated step/daemon runner, webhook alerts, JSONL event log
  integrations/
    openalgo_manager.py              OpenAlgo Web/Desktop reachability/latency detector & isolated sidecar helpers
    openalgo_blueprint.py            Headless backtest runner & optional lazy-imported Flask Blueprint
  ui/
    components.py                    Auth sidebar (Upstox OAuth + OpenAlgo), instrument selector, 16-page NAV
    builder_helpers.py               Beginner presets, auto-naming, plain-English cards, quick preview runner
    pages/*.py                       16 modular Streamlit render(ctx) pages
tests/                               218 automated checks + headless 16-page AppTest & deep feature verifiers
docs/archive/                        Historical v5-v8 roadmaps & refactor index kept for reference
```
