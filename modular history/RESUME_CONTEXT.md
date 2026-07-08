# RESUME_CONTEXT — QuantDesk handoff (read this first)

**As of:** 2026-07-08 · **Current version: v7.7** (published) · Companions: [`STATUS.md`](STATUS.md) ·
[`progress.json`](progress.json) · [`modular.json`](modular.json)

> One-file handoff so a fresh session continues without re-deriving state. Everything below is
> verified and on GitHub unless it says "local/unpushed" (nothing is unpushed right now — v7.7 is fully shipped).

---

## 1. What this project is
Modular rebuild of the Upstox "Unified Market Data Downloader & Quant Engine" — a 13-page **Streamlit**
no-code dashboard (download Indian market data → build/backtest/optimise/ML-mine strategies).
Package `quantdesk/` (~60 modules) + thin `app_main.py`. Engine = **backtesting.py** (single-asset).
App dir: `D:\mbs\historydownloader\modular history`. Original monolith `upstox_app.py` retained, never edited.

## 2. Published state (GitHub)
- **historytester** (private): `main`, tag **v7.7**, GitHub release Latest. Layout: root = v3 monolith,
  `modular history/` subfolder = this app.
- **sambhav** (private, collaborator repo): synced to **v7.7**.
- Publish flow + sambhav sync: `progress.json → how_to_publish_new_version` / `repos.sambhav.sync_procedure`.
  Commits authored as `gauravrakhecha <gauravrakhecha@gmail.com>` (set repo-local before committing); end
  commits with `Co-Authored-By: Claude Opus 4.8`. Always secret-scan (gho_/eyJ/-----BEGIN; no .env/.db/pycache).

## 3. How to run & verify (simpleton venv)
```
VENV = D:\simpleton\historydownloader\.venv\Scripts\python.exe
DATA = D:\simpleton\historydownloader\data   (real RELIANCE 15Min etc.)
set PYTHONIOENCODING=utf-8
<VENV> -m pytest tests\ -q -p no:cacheprovider      # 108 tests (~2.5 min)
<VENV> tests\verify_app.py                           # AppTest: all 13 pages render + a UI backtest
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

Net effect: audit **4.8 → ~6.0**. All wired through models/codegen/compiler-namespace/builder-UI/validation;
existing strategies are byte-identical (parity preserved). Test files: `tests/test_security.py` (21),
`tests/test_charges_in_equity.py` (7), `tests/test_risk.py` (13).

## 5. NEXT — pick one (both planned, nothing started)
- **(a) Task 3 pt.3** (finish risk): **breakeven stops**, **partial exits / scale-out**, **Kelly-informed
  sizing**. The pure helpers `stops.breakeven_stop` and `sizing.kelly_fraction/half_kelly_fraction` already
  exist and are tested — mostly codegen wiring (mirror the ATR-stop pattern in `strategy/codegen.py`).
- **(b) v8 — Autonomous Data Agent** ([`ROADMAP_v8_data_agent.md`](ROADMAP_v8_data_agent.md)): auto-download
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
