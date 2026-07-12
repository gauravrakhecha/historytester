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

## Reliable downloads & data integrity

Historical data is pulled in **chunks** (30 days at a time for 1-minute data, a year at a time
otherwise) and stitched together. Two safeguards make sure you never end up trusting data that
has silent holes in it:

**1. A failed chunk is never hidden.** If a piece of the download fails every retry (a broker
rate-limit, a dropped connection), the app now:

- **tells you loudly** which date range(s) failed instead of quietly skipping them,
- **names the saved file by the range it actually got** (e.g. `RELIANCE_1Minute_2024-01-01_2024-05-04.parquet`),
  not the range you asked for — so a file's name never over-promises its contents,
- and, in a bulk run, reports a separate **"Incomplete"** count alongside Complete / Failed.

If you see an "incomplete" warning, just re-fetch that symbol; the app retries with a proper
back-off (and respects a broker's "try again in N seconds" rate-limit signal).

**2. The data-quality check catches missing days.** Before a backtest, every dataset gets a
0–100 **quality score**. It now also compares your data against the **NSE trading calendar**
(weekends and holidays excluded) and flags any **missing trading days** — so a gap left behind
by an old failed download shows up as *"N missing trading day(s) — possible incomplete download"*
and lowers the score, instead of passing as clean just because the bars that *are* present look
fine. A short run of scattered missing days barely moves the score; a solid multi-week hole
drops it sharply.

> Tip: if the quality check flags missing days on data you downloaded a while ago, that data was
> likely affected by the old silent-gap behaviour — re-download it once to fill the holes.

## What changed (and what didn't)

**Same 13 features**, identical behaviour: Master / Single / Bulk downloaders, Options Chain,
Local Converter, Download Logs, Data Management (backup/restore), Settings, No-Code Strategy
Builder, Strategy & Indicator Plotter, Backtester + Optimizer (Optuna + Grid), Backtest Results,
and the ML Pattern Finder.

**v8.1 — backlog clear:** trade analytics (MAE/MFE, R-multiple, Kelly) on the Backtester; per-trade
**India tax-format CSV** export; no-code **higher-timeframe trend bias** + **intraday square-off (MIS)**
in the strategy builder; batch runs now appear in the leaderboard with reproducibility hashes;
consistent timestamp handling; per-run error reports; and portable tests (set `QUANTDESK_TEST_CSV`).

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
- **v6.1 chart redesign** — oscillators (RSI/ADX/Stochastic/MACD/…) render in their own stacked
  panels with guide lines instead of being crushed onto the price axis; WebGL + candle
  downsampling + intraday gap-collapse; optional engine-native Bokeh "Pro chart" toggle.

**v8.0 — data integrity (reliable downloads):**
- **Silent chunk loss fixed** — a download chunk that fails all retries is now logged, surfaced
  to you, and excluded from the file's advertised date range, instead of being swallowed as an
  empty result that left an invisible hole. Retries use exponential back-off with jitter and
  honour a broker's HTTP 429 `Retry-After`.
- **Calendar-aware quality gate** — the data-quality score now detects **missing NSE trading
  days** (weekends/holidays excluded), so a multi-week gap from an old failed download is caught
  and flagged rather than passing as clean. See *"Reliable downloads & data integrity"* above.

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
