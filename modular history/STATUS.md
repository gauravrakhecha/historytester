# CURRENT STATUS — QuantDesk (modular) — resume doc

**Last updated:** 2026-06-27 · **Current version:** **v6.1** · Machine-readable twin: [`progress.json`](progress.json) · Granular file index: [`modular.json`](modular.json)

> **Read this first after a context reset.** It captures everything needed to continue without re-deriving state.

---

## 1. What this is
A modular rebuild of an independent app: the **Upstox "Unified Market Data Downloader & Quant Engine"** — a 13-page Streamlit dashboard to download Indian market data (Upstox/Zerodha/Dhan) and build/backtest/optimise/ML-mine no-code strategies.

- **Original** single-file app: `D:\mbs\historydownloader\upstox_app.py` (3,207 lines) — **retained, never edited**.
- **This (modular) app:** `D:\mbs\historydownloader\modular history\` → package `quantdesk/` (56 modules) + thin `app_main.py`.
- **Build is complete and shipped through v6.1.** Not a work-in-progress refactor — it's a maintained product now.

## 2. Where it's published
| Repo | Visibility | State | Purpose |
|---|---|---|---|
| [gauravrakhecha/historytester](https://github.com/gauravrakhecha/historytester) | private | `main`, tag **v6.1** (`f369458`) | main repo (v3 monolith at root + `modular history/` subfolder) |
| [gauravrakhecha/sambhav](https://github.com/gauravrakhecha/sambhav) | private | synced to **v6.1** (`5ca228c`) | dedicated **collaborator** repo (separate repo = true isolation; GitHub has no per-branch read access) |

Tags: v3.0–v3.3 (monolith) → v4.0 (modular) → v5.0 (honest numbers) → v6.0 (audit/speed/multi-format) → **v6.1 (chart redesign)**. Only `main` exists on historytester (old sambhav/sabhav branches deleted).

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

## 7. TO CONTINUE — ⭐ v7 Feature Maturity (Phase A DONE) → see [`ROADMAP_v7.md`](ROADMAP_v7.md)
A multi-agent maturity audit confirmed the user's critique: **Backtester = usable, Optimizer = amateur,
ML Pattern Finder = amateur/toy** (ML trained on raw absolute features that don't generalize across assets).

- **✅ Phase A — DONE (local, unreleased; 45 tests green):** added 6 normalized indicator types —
  **Ratio to SMA** (use Volume source for `vol/SMA20(vol)`), **Distance from MA (ATR)**, **MA Slope (ATR)**,
  **Z-Score**, **Percentile Rank**, **Return (n-bar)**. Implemented as registry types (config placement/guides
  + `indicators.py` types/param-specs/helpers + `emit_indicator` + compiler namespace), so the **builder,
  ML finder, and plotter all get them** and they render in their own panels. Tests: `tests/test_features.py`.
- **▶ Phase B — NEXT (M):** `ml/patterns.py` + `ml_finder.py` — default to normalized features; time-ordered
  holdout/k-fold; per-rule Wilson lower-bound + Benjamini-Hochberg FDR + sample floor; per-instrument
  normalization before bulk aggregation.
- **Phase C (M-L):** optimizer validation — Deflated/Probabilistic Sharpe, parameter-stability heatmap
  (grid already computes & discards `return_heatmap`), walk-forward (`backtest/wfo.py`), Monte-Carlo bands.
- Release framing: **v7.0 = A+B**, **v7.1 = C**. Phase A is **not pushed yet** (ship with v7.0). Details in `ROADMAP_v7.md`.

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
