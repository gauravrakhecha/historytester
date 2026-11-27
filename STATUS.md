# CURRENT STATUS — QuantDesk v9.0

**Last updated:** 2026-11-27 · **Current version:** **v9.0.0** · Handoff: [`RESUME_CONTEXT.md`](RESUME_CONTEXT.md) · Machine index: [`progress.json`](progress.json) · Guide: [`README.md`](README.md)

---

## 1. What Shipped in v9.0

- **Offline OpenAlgo Charts v2.7.5 + Integrated Chart Backtester Terminal (`📊 Chart Backtester (Terminal)`):**
  Vendored `openalgo-charts` standalone JS offline inside `quantdesk/viz/assets/openalgo-charts/`. Added [quantdesk/viz/openalgo_terminal.py](quantdesk/viz/openalgo_terminal.py) and [quantdesk/ui/pages/chart_terminal.py](quantdesk/ui/pages/chart_terminal.py) with client-side Bar-by-Bar Replay + HUD, linked Equity/Drawdown panes (`createLinkGroup`), Trade Focus `PriceLine`s, clickable Trade Blotter, and non-destructive parameter tweaks.
- **No-Code Strategy Builder v2 (`🛠️ Strategy Builder`):**
  Added Simple (Guided) vs Pro (All Controls) modes, 5 1-click `STARTER_TEMPLATES`, live Plain-English strategy translator, sanity trap checker, Quick Test Preview, readable auto-naming, 4 custom indicator blocks (`Series Math`, `Rolling Stat`, `Bar Shift`, and AST-safe `Custom Formula`), parenthesized condition groups, `NOT`, lookback timing (`bars_ago`, `within_last`, `consecutive`), and intraday entry time windows (`HH:MM`–`HH:MM`).
- **OpenAlgo Ecosystem Broker & Unified Data Hub (`📡 Data Hub & OpenAlgo`):**
  Added strict Marshmallow OpenAlgo REST v1 data adapter ([quantdesk/brokers/openalgo.py](quantdesk/brokers/openalgo.py)), simplified sidebar authentication to **`Upstox` | `OpenAlgo`** (covering 38+ brokers via OpenAlgo Web/Desktop), added [quantdesk/data/catalog.py](quantdesk/data/catalog.py) (Data Coverage Catalog, provisional-bar-safe incremental top-up, ZIP export), and [quantdesk/integrations/openalgo_manager.py](quantdesk/integrations/openalgo_manager.py).
- **Live Operations Monitor, Alerts & Risk-Guarded Execution (`🚦 Live Monitor & Deploy`):**
  Added [quantdesk/live/](quantdesk/live/__init__.py) (`OpenAlgoClient`, `risk_guard` kill switches, isolated `signal_runner` with research-to-live parity and `alert_only` -> `sandbox` -> `live` workflow), standalone script generator ([quantdesk/strategy/openalgo_export.py](quantdesk/strategy/openalgo_export.py)), optional headless Flask blueprint ([quantdesk/integrations/openalgo_blueprint.py](quantdesk/integrations/openalgo_blueprint.py)), and PEP 621 packaging ([pyproject.toml](pyproject.toml)).
- **Production Hardening & Upstox Auth Fix:**
  Fixed HTF EMA causality (`shift(1)`), Upstox OAuth `UDAPI1013` / redirect credential persistence (`Save to .env`, new-tab links, redirect URL code extraction), path-traversal guards, portable data paths, and Streamlit `width="stretch"` compatibility.

---

## 2. Repositories

| Repo | Visibility | Tag / Branch | Purpose |
|---|---|---|---|
| [gauravrakhecha/historytester](https://github.com/gauravrakhecha/historytester) | private | `main`, `v9.0` | Primary modular QuantDesk repository (flat root layout) |
| [gauravrakhecha/sambhav](https://github.com/gauravrakhecha/sambhav) | private | `main`, `v9.0` | Dedicated collaborator mirror synced cleanly from `historytester` |

---

## 3. Quick Run & Verification

```powershell
# Launch dashboard (localhost:8501)
run.bat

# Automated verification (run from repo root)
.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/test_downloader.py -p no:cacheprovider -q
.venv\Scripts\python.exe tests/verify_app.py
.venv\Scripts\python.exe tests/verify_features.py
```

Legacy planning docs from `v5`–`v8` are archived under [`docs/archive/`](docs/archive/).
