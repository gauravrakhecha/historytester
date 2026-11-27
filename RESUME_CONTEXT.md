# RESUME_CONTEXT — QuantDesk v9.0 Handoff

**As of:** 2026-11-27 · **Current version: v9.0.0** · Companions: [`README.md`](README.md) · [`STATUS.md`](STATUS.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`progress.json`](progress.json)

---

## 1. Current State

QuantDesk is a standalone, modular Python 3.12 Streamlit application (`quantdesk/` package + thin `app_main.py`, **16 UI pages**) covering market-data acquisition, quality-checked Parquet/CSV storage, no-code strategy design, offline interactive charting & replay, backtesting/WFO/Optuna/ML research, and loose-coupled OpenAlgo live automation.

Historical planning documents from `v5`–`v8` (`ROADMAP_*.md`, `modular.json`) are archived in [`docs/archive/`](docs/archive/).

---

## 2. Key Implemented Systems (v9.0)

- **Strategy Core (`quantdesk/strategy/`):**
  - 27 baseline/normalized indicators + 4 visual custom blocks (`Series Math`, `Rolling Stat`, `Bar Shift`, AST-safe `Custom Formula` with `assert_safe_formula`).
  - Grouped rule logic (`group`, `negate`, `timing`, `timing_bars`), intraday `entry_time_filter`, `htf_bias` (causal `.shift(1)`), `intraday_square_off`, 5 `STARTER_TEMPLATES`, `describe_strategy_plain_english`, and `check_strategy_sanity` (`templates.py`).
  - Two-layer safety: `validate_preset()` at load/save boundaries and `assert_safe_ast()` inside `compiler.py`.
- **Charting & Replay Terminal (`quantdesk/viz/`, `quantdesk/ui/pages/chart_terminal.py`):**
  - Vendored offline `openalgo-charts` v2.7.5 (`quantdesk/viz/assets/openalgo-charts/`).
  - Passive viewer (`openalgo_chart.py`) and interactive Pro Chart Terminal (`openalgo_terminal.py`: client-side Bar Replay HUD, synced Equity/Drawdown panes via `createLinkGroup`, Entry/Exit/SL/TP `PriceLine`s, Trade Focus).
- **Data Acquisition & Hub (`quantdesk/brokers/`, `quantdesk/data/`, `quantdesk/integrations/`):**
  - Native **Upstox** OAuth (`Save to .env`, new-tab redirect handling, `UDAPI1013` guards) + **OpenAlgo** REST v1 (`POST /api/v1/history`, `/search`, `/ping`, `/analyzer` with strict Marshmallow payloads).
  - Historify-inspired `data/catalog.py`: coverage catalog, NSE calendar gap checks, provisional-bar-safe 2-day rewind incremental top-up (`keep="last"`), and in-memory ZIP export.
  - Zero `import openalgo` coupling (`integrations/openalgo_manager.py` monitors external OpenAlgo Web/Desktop at `127.0.0.1:5000`).
- **Live Bridge & Risk Guard (`quantdesk/live/`, `quantdesk/strategy/openalgo_export.py`, `quantdesk/ui/pages/live_deploy.py`):**
  - `OpenAlgoClient` (`/funds`, `/positionbook`, `/orderbook`, `/tradebook`, `/placeorder`, `/closeposition`, `/cancelallorder`).
  - `risk_guard` kill switches (`daily_loss_limit`, `max_trades_per_day`, `max_open_positions`, duplicate-bar guard, cooldown, explicit `live_confirmed`).
  - Isolated `signal_runner` (CLI `quantdesk-live` / subprocess runner, never blocking Streamlit loops), standalone bot exporter, and headless `openalgo_blueprint.py`.

---

## 3. Environment & Verification Notes

- **Local venv:** `.venv\Scripts\python.exe` (Python 3.12).
- **Tests:** Run pytest with `--ignore=tests/test_downloader.py -p no:cacheprovider` on sandboxed Windows (avoids Windows `asyncio` self-pipe restriction in `test_downloader.py`), with `NUMBA_CACHE_DIR` pointing to a writable scratch folder if needed, and `MODULAR_DB_FILE` pointing to a temporary SQLite DB when testing AppTest UI flows.
- **Publish procedure:** See `progress.json -> how_to_publish_new_version`. Keep `.env`, `*.db`, `.venv/`, `__pycache__/`, `.master_cache/`, `data/`, `plots/`, and `.workflow/` logs out of git.
