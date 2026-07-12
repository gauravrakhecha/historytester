# RESUME_CONTEXT — QuantDesk handoff (read this first)

**As of:** 2026-07-12 · **Current version: v8.2** (published) · Companions:
[`STATUS.md`](STATUS.md) · [`progress.json`](progress.json) · [`modular.json`](modular.json)

> One-file handoff so a fresh session continues without re-deriving state. Everything below is
> verified and on GitHub. Nothing is unpushed — v8.2 is fully shipped (historytester + sambhav).

---

## 0. Latest since v7.8 (read before the older sections)
- **v8.0 "Data Integrity"** — fixed silent download-chunk loss (`brokers/downloader.py` raises
  `ChunkDownloadError`, files named by ACTUAL retrieved range), exponential+jitter back-off honouring
  HTTP 429, and a **calendar-aware quality gate** (missing NSE trading days via bundled `nse_holidays.csv`).
  Also fixed a `.gitignore` bug that had excluded the whole `quantdesk/data/` source package from every
  prior release.
- **v8.1 "Backlog Clear"** — cleared the deferred backlog: trade analytics (MAE/MFE/R-multiple/Kelly on
  the Backtester), India tax-format CSV export, batch-row leaderboard/repro persistence, loader tz
  normalization, per-run error reports, Strategy-Builder stable-uuid row keys, options_chain stale-save
  guard, results delete-confirm, portable tests (`QUANTDESK_TEST_CSV`), and no-code **higher-timeframe EMA
  bias + intraday square-off (MIS)** in codegen. MBS-domain items (multi-asset portfolio, options legs,
  benchmark, trade replay, futures-continuous, survivorship) **closed as federate-to-MBS** (see
  `progress.json → deferred_roadmap`). New `tests/test_backlog_v81.py`.
- **v8.2 "Repo restructure"** — the maintained app now lives at the **repo ROOT** (the old `modular
  history/` subfolder + the legacy `upstox_app.py` monolith are gone from the remote; monolith kept in git
  history at v8.1-). ⚠️ **Local dev still lives in `D:\mbs\historydownloader\modular history\`** — only the
  REMOTE went flat; the publish step maps the local folder's contents onto the remote root (see the updated
  `progress.json → how_to_publish_new_version`).
- Companion system: **MBS** (`D:\mbs`, repo `gauravrakhecha/mbs`) is the VectorBT+NautilusTrader
  "system of record"; QuantDesk is the data-acquisition + no-code prototyping + ML front end. A strategy
  "graduates" from QuantDesk to MBS for portfolio/options/benchmark validation.

## 1. What this project is
Modular rebuild of the Upstox "Unified Market Data Downloader & Quant Engine" — a 13-page **Streamlit**
no-code dashboard (download Indian market data → build/backtest/optimise/ML-mine strategies).
Package `quantdesk/` (~60 modules) + thin `app_main.py`. Engine = **backtesting.py** (single-asset).
Local dev dir: `D:\mbs\historydownloader\modular history` (the GitHub remote is flat at the root as of v8.2).

## 2. Published state (GitHub)
- **historytester** (private): `main`, tag **v8.2**, GitHub release Latest. Layout: **FLAT** — the app is at
  the repo root; `CHANGELOG.md`/`LICENSE`/`.gitignore` also at root.
- **sambhav** (private, collaborator repo): synced to **v8.2**.
- Publish flow + sambhav sync: `progress.json → how_to_publish_new_version` / `repos.sambhav.sync_procedure`
  (updated for the flat layout: clone → replace ROOT app files from the local `modular history/` → scan →
  tag → push → mirror sambhav). Commits authored as `gauravrakhecha <gauravrakhecha@gmail.com>` (set
  repo-local first); end commits with `Co-Authored-By: Claude Opus 4.8`. Always secret-scan
  (gho_/eyJ/-----BEGIN; no .env/.db/pycache; confirm `quantdesk/data/` present).

## 3. How to run & verify (simpleton venv)
```
VENV = D:\simpleton\historydownloader\.venv\Scripts\python.exe
DATA = D:\simpleton\historydownloader\data   (real RELIANCE 15Min etc.)
set PYTHONIOENCODING=utf-8
<VENV> -m pytest tests\ -q -p no:cacheprovider      # ~124 tests (~3 min)
<VENV> tests\verify_app.py                           # AppTest: all 13 pages render + a UI backtest
<VENV> tests\verify_features.py                      # deep flows: plotter/builder/settings/optimizer
run.bat                                              # launch the dashboard (localhost:8501)
```
Modular.json index updater: `MODLOG_AGENT=orchestrator <VENV> .workflow/modlog.py file <path> verified "<note>"`.

## 4. This session's work (the "v7.x Trustworthy Core" arc)
A **multi-agent quant-desk audit** (24 agents, adversarially verified) scored the app **4.8/10** ("not
desk-deployable") — see the panel results in the chat / `ROADMAP_v7.1_trustworthy_core.md`. We then fixed
the top wrong/dangerous items:

- **v7.0 / v7.0.1** (pre-session): Feature Maturity (normalized features, ML rigor, optimizer validation)
  + a page-switch-lag perf fix (lazy imports).
- **v7.5 — Trustworthy Core pt.1:**
  - *Task 1 (security):* `quantdesk/strategy/validation.py` — `assert_safe_ast` (compiler AST sandbox:
    import allowlist + denied builtins + dunder block) + `validate_preset`. Closed a **real preset-upload RCE**.
  - *Task 2 (charges-in-equity):* `costs/india.make_fee_model(segment)` (per-leg commission callable) threaded
    through `engine`/`wfo`/backtester UI so **every metric/optimizer/WFO/MC is net of real India charges**
    (drive: gross 20.8%/0.79 Sharpe/95% PSR → 0.7%/0.03/55% net).
- **v7.6 — Trustworthy Core pt.2 (task 3 core):** new `quantdesk/risk/{sizing,stops}.py` (pure, unit-tested,
  injected into the compiler namespace) + **"Risk % of Equity per Trade"** sizing + **max-drawdown circuit-breaker**.
- **v7.7 — Trustworthy Core pt.2 (task 3 pt.2):** **ATR (volatility) stop** mode + **time stop**
  (`max_bars_in_trade`) + ATR-risk sizing. Drive: time stop capped hold 150→20 bars; ATR stop lifted return 1.8%→5.4%.
- **v7.8 — Trustworthy Core pt.3 (task 3 FINISHED):** the last three risk controls, all
  opt-in / default-OFF (parity locked):
  - **Breakeven stop** (`breakeven_trigger_pct`) — ratchets the open `Trade.sl` up to entry once price
    moves the trigger% in favour (guarded so it can't fill instantly). Drive: altered exits vs SL/TP alone.
  - **Partial exit / scale-out** (`partial_exit_pct` + `partial_trigger_pct`) — banks a fraction via
    `Position.close(portion=)`, once per trade; remainder keeps its SL/TP; composes with breakeven. Drive:
    split 55→60 trade records, cut MaxDD −7.82→−7.60.
  - **Adaptive Kelly sizing** — two `pos_size_type` modes "Half-Kelly (Adaptive)" / "Full Kelly (Adaptive)"
    sizing from the strategy's own `closed_trades` (win-rate × payoff), engaging after `config.KELLY_MIN_TRADES`
    (20) closes. Drive: Half-Kelly compounded a +edge synthetic to +42.7% while Full-Kelly's variance left
    the same edge at −0.5%; correctly refuses to size a −edge strategy to minimum lot.
  - Wired through config/models/codegen/compiler-namespace/validation/builder-UI + preset round-trip;
    drove the actual builder widgets (AppTest) → all markers reach generated code. `tests/test_risk.py` now 17.

Net effect: audit **4.8 → ~6.0**. All wired through models/codegen/compiler-namespace/builder-UI/validation;
existing strategies are byte-identical (parity preserved). Test files: `tests/test_security.py` (21),
`tests/test_charges_in_equity.py` (7), `tests/test_risk.py` (17). Full suite **112 green** + AppTest.

## 5. NEXT (Task 3 / Trustworthy Core fully DONE & shipped as v7.8)
- **v8 — Autonomous Data Agent** ([`ROADMAP_v8_data_agent.md`](ROADMAP_v8_data_agent.md)): auto-download
  + multi-timeframe build on a schedule, respecting API rate limits. Phases 0–2 (token store + rate limiter +
  watermarks → incremental download + TF ladder → runner/CLI/Task-Scheduler) = v8.0. Central constraint:
  broker tokens expire daily (Dhan excepted) — design around a one-click daily re-login.

Remaining audit backlog beyond the above (from the panel, ordered): benchmark engine (α/β/IR vs Nifty) +
trade-level analytics (MAE/MFE, R-multiple, Kelly); Deflated-Sharpe-for-Grid + enforceable overfit banner;
data-integrity (gap/session/holiday detection, survivorship caveat, tz normalization); portable tests + CI +
run-id error reports; then the strategic L pieces (portfolio engine, live/paper execution, options/ITM-STT).

## 6. Gotchas discovered this session (don't relearn the hard way)
- **backtesting.py `commission` callable** = `(order_size, price) -> cash`, called **per leg** with the
  *position* sign (NOT the transaction side) — so one-sided STT/stamp are split evenly across legs
  (exact for symmetric charges; ~0.001% error otherwise). `spread=` is used for slippage.
- **AST sandbox** must ALLOW imports from `{numpy,pandas,pandas_ta,ta,hurst,hmmlearn,scipy}` (Hurst/HMM
  emit imports inside helpers) and allow `getattr`/`hasattr` (used in generated code) — but block dunder
  attrs AND dunder string constants. New codegen helper names must be injected into `compiler._namespace()`.
- **Session usage limits**: the multi-agent Workflow panel died twice on hard session limits; recover
  completed agent results from `<transcriptDir>/journal.jsonl` and synthesize inline rather than re-running.
- Charge-accurate mode changed every backtest number (intended) — a few tests assert `segment=None` for the
  legacy flat-commission path (back-compat). The transient `pyarrow ArrowTypeError` in `verify_app.py` is a
  non-deterministic seeding artifact, unrelated to code.
- Settled decisions (don't re-litigate): keep backtesting.py (no Nautilus/VectorBT), keep Plotly.
  `key_decisions`/`gotchas` in `progress.json` have the rest.
