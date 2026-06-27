# Unified Market Data Downloader & Quant Engine — Modular Edition

A **modular, faster** refactor of the original single-file `upstox_app.py` (3,207 lines).
**Every original feature is preserved** — this rebuild only reorganises the code into small,
testable modules and speeds it up. The original app is kept untouched one folder up
(`../upstox_app.py`) for reference.

> Note: the original README claimed a "Bokeh charting engine"; the app actually uses
> **Plotly**, which is what this edition uses too. Corrected here for accuracy.

## Run it

```bat
install.bat   :: one-time: creates an isolated .venv via uv and installs requirements
run.bat       :: launches the dashboard (Streamlit) in your browser
```

`run.bat` uses this folder's `.venv` if present, otherwise the parent app's `.venv`.

## Saving downloaded data (CSV / Parquet)

Every downloader (Master / Single / Bulk / Options) can save **Parquet**, **CSV**, or **Both**:

- Set the default in **⚙️ Settings → Paths & Data Format → Download Save Format** (default **Both**),
  with an optional **CSV Data Directory** for keeping CSVs in a separate folder.
- Each download page also shows **two folder inputs** — a **Parquet Save Folder** and a **CSV Save
  Folder** (blank = save CSVs alongside the Parquet) — so you can set both destinations per download.
- A 💾 line on each page states exactly which formats are written and where.

## What changed (and what didn't)

**Same 13 features**, identical behaviour: Master / Single / Bulk downloaders, Options Chain,
Local Converter, Download Logs, Data Management (backup/restore), Settings, No-Code Strategy
Builder, Strategy & Indicator Plotter, Backtester + Optimizer (Optuna + Grid), Backtest Results,
and the ML Pattern Finder.

**Faster:**
- Vectorised the slow per-bar indicators (ALMA, Linear-Regression Slope, R-Squared) — proven
  numerically identical to the originals (`tests/test_parity.py`).
- Compiled-strategy bytecode caching (no re-parsing each run / optimiser trial).
- Centralised, cached settings, master list and data loads.
- Vectorised trade-log enrichment (no per-row Python loops).

**More robust / accessible (nothing removed):**
- Local tools (backtester, strategy builder, settings, converter, results, ML…) now work
  **without** a broker login — only the live downloaders/options need authentication.
- One database layer, one settings accessor, structured logging.
- Recovery scan now handles both `_to_` and plain filename formats.
- Multi-output indicators (Supertrend, Stochastic, Keltner, Donchian) are now selectable
  everywhere (the monolith omitted them on the Plotter & ML pages).

**v6 — honest results, audit fixes & multi-format saving (see ../CHANGELOG.md):**
- Out-of-sample optimiser validation, real India statutory charges, data-quality gate, QuantStats
  tearsheet, Results leaderboard, reproducibility hashes.
- A multi-agent audit then fixed real bugs: ML Pattern Finder crash, short trailing-stop never
  ratcheting (+ a latent crash), double-counted charges, Optuna integer params, presets dropping
  position sizing, and the tab-switch / search lag (instrument selector re-scan).
- Corporate-actions split download crash fixed (int64 Volume on pandas ≥ 3).
- CSV + Parquet dual-format saving with separate folders.

## Architecture

```
app_main.py            thin Streamlit entry: auth sidebar, nav, page dispatch
quantdesk/
  config.py            env, paths, constants
  core/                db · settings_repo · logging · cache
  data/                loader · resample · corporate_actions · master_list · quality · recovery
  brokers/             base · upstox · zerodha · dhan · downloader (async)
  strategy/            models · indicators · codegen · compiler · presets
  backtest/            worker · engine · optimizer · batch · metrics
  ml/patterns.py       XGBoost importance + decision-tree rules
  viz/charts.py        Plotly helpers
  ui/components.py     shared widgets + page context
  ui/pages/*.py        one render(ctx) module per page
tests/                 unit tests + numeric parity harness + AppTest UI drivers
modular.json           live file-by-file build / verification index
```

Backend modules never import Streamlit, so they run inside multiprocessing workers and tests.

## Verify

```bat
python -m pytest tests/test_units.py tests/test_parity.py -q
python tests/verify_app.py        :: drives every page via Streamlit AppTest
python tests/verify_features.py   :: clicks plotter / builder / settings / optimizer flows
```
