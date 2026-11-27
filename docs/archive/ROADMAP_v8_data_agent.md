# ROADMAP v8 — Autonomous Data Agent (auto-download + multi-timeframe build)

**Status:** PLANNED (not started) · **Priority:** HIGH · **Created:** 2026-06-28 · after v7.0.1
**Goal:** a dedicated agent that, after a one-time setup + a (daily) broker login, **keeps a chosen
universe of instruments downloaded and resampled into the user's selected timeframes — on its own,
on a schedule, while respecting each broker's API rate limits.**

> Implementation-ready: grounded in the existing modules so a fresh context can build without re-deriving.

---

## 0. What exists today (reuse, don't rebuild)
- **Download orchestrator:** `brokers/downloader.download_long_range_async` — chunked (30-day for
  1-Minute, 365-day for Day), `asyncio.Semaphore(5)`, 3-attempt linear-backoff retry, RAM-safe Parquet
  spooling, optional Upstox corporate-actions adjustment. Sync wrapper `run_async_download`.
- **Broker fetchers:** `brokers/{upstox,zerodha,dhan}.fetch_single(...)`. Upstox v2 historical-candle
  supports base intervals **`1minute`** and **`day`** only → higher timeframes come from resampling.
- **Resampler:** `data/resample.resample_dataframe(df, minutes)` — per-session anchored (no overnight
  bridging), carries OI/Dividend/Rights. **Minute-based only** (no Day→Week/Month path yet).
- **Saver:** `data/saver` — Parquet / CSV / Both (+ optional separate CSV dir). Convention
  `{root}/{Symbol}/{Interval}/{Symbol}_{Interval}_{from}_{to}.*`.
- **DB:** `core/db` — `downloads_log` (broker, symbol, instrument_key, interval, start/end, file_path,
  downloaded_at), `bulk_templates` (named symbol baskets — reuse as the agent's "universe"), `settings`.
- **Recovery:** `data/recovery.rebuild_db_from_folder` — reconstruct the download log from disk.
- **Master list + selector:** `data/master_list` + `ui/components.render_instrument_selector`.
- **Auth:** `ui/components.render_auth_sidebar` — Upstox OAuth (access token in `session_state`/`.env`).
- **Quality gate:** `data/quality.validate`.

**What's missing (this is the v8 build):** a watermark/incremental layer, a real rate limiter, a
token store decoupled from Streamlit, a calendar resampler (D/W/M), a scheduler/daemon + CLI, and a
monitoring/setup page.

---

## 1. The central constraint — broker token lifecycle (design around this first)
- **Upstox & Zerodha access tokens expire daily** (Upstox ~03:30 IST; Zerodha needs daily TOTP login).
  There is **no unattended multi-day token** in the standard public flow. **Dhan** tokens are
  longer-lived (~24h+), so Dhan is the most "set-and-forget" broker.
- ⇒ "Login once and it runs on its own" is true **within a token's validity window (one trading day)**.
  For continuous operation the agent must: **store the token + its expiry**, run autonomously until it
  expires, **detect expiry (HTTP 401) and HALT cleanly into a `NEEDS_LOGIN` state**, and let the user
  re-authenticate in **one click** (the UI writes the fresh token to the shared store; the next
  scheduled run picks it up). Optional notification when re-login is required.
- **Non-goal (default):** headless/TOTP auto-login (fragile, broker-ToS-sensitive). Documented as an
  advanced opt-in only; never the default path.

---

## Phase 0 — Foundations: token store, rate limiter, watermarks  [effort: M]
The de-risking layer everything else builds on. No user-visible feature yet.

**New DB tables** (`core/db` schema + idempotent migration):
- `agent_jobs(id, name, broker, universe_ref, base_intervals, target_timeframes, save_format,
  parquet_dir, csv_dir, schedule_cron, incremental, enabled, created_at, updated_at)`.
- `agent_runs(id, job_id, started_at, finished_at, status, symbols_total, succeeded, failed,
  bars_added, summary_json)`.
- `data_watermarks(broker, instrument_key, interval, last_timestamp, last_run_at, status, PRIMARY
  KEY(broker, instrument_key, interval))` — the incremental cursor.
- `broker_tokens(broker, access_token, obtained_at, expires_at, status)` — the shared token store
  (DB-backed; the **same DB the UI already uses**, gitignored). Never logged.

**`agent/store.py`** — read/write token + watermarks. The auth sidebar writes the token here on login;
the agent process reads it. (Token validity = `expires_at` or a cheap probe request.)

**`agent/ratelimiter.py`** — async **token-bucket** per broker: configurable `requests_per_second`,
`requests_per_minute`, and max concurrency; honors HTTP **429 + `Retry-After`** with exponential
backoff. Replaces the blind "retry on any Exception" in `fetch_chunk_async` with **typed handling**:
429 → throttle/backoff; **401 → raise `AuthExpired` (halt + flag)**; network/5xx → retry; other 4xx →
skip-and-record. Defaults conservative; per-broker overrides in `settings`.

**DoD:** unit tests for the token-bucket (never exceeds configured rate; backs off on injected 429),
watermark round-trip, and the error-classification of fetch responses.

---

## Phase 1 — Incremental download + full timeframe ladder  [effort: M]
Turn the one-shot range downloader into an **incremental, idempotent** per-symbol updater that also
builds every selected timeframe.

- **`agent/incremental.py`** — for `(broker, instrument_key, interval)`: read the watermark, compute the
  missing range `[last_timestamp+1 … today]`, call `downloader.download_long_range_async` (now via the
  rate limiter) for just that range, **merge** into the existing file (de-dup on Timestamp + sort —
  already the downloader's behaviour), update the watermark. First run = full backfill (chunked,
  RAM-safe), subsequent runs = tiny deltas.
- **`data/resample.resample_calendar(df, rule)`** (new) — Day→**Week/Month** via a calendar resample
  (`W`/`M`/`Q`) reusing the same OHLC/Volume/OI/cashflow aggregation. (`resample_dataframe` keeps
  handling intraday minutes.)
- **TF ladder build:** download base = `1 Minute` (intraday) and/or `Day`; generate each selected target
  TF — intraday minutes via `resample_dataframe`, D/W/M via `resample_calendar` — and save each to its
  own `{Interval}` folder via `data/saver`. Run `quality.validate` per built frame; annotate/skip on
  block-threshold failures.
- **Idempotency & coverage:** re-running a fresh job is a no-op; `downloads_log` + watermarks give a
  per-symbol-per-interval "up to date as of" coverage view.

**DoD:** tests — incremental run downloads only the delta and merges without dupes; the ladder produces
byte-consistent resamples vs `tf_generator`/`resample_dataframe`; D/W/M calendar resample correctness;
quality gate enforced. (Optionally extend Upstox to v3 multi-interval later — resampling from 1-Minute
stays the robust, broker-agnostic default.)

---

## Phase 2 — Agent runner, CLI & scheduling (the "runs on its own" core)  [effort: M-L]
- **`agent/runner.py`** — `run_once(job)`: resolve the universe (a `bulk_templates` basket or
  NIFTY50/BankNifty/custom list) → check token (else `NEEDS_LOGIN`, halt) → for each symbol, run the
  Phase-1 incremental ladder under the rate limiter, with **per-symbol retry/backoff and a dead-letter**
  (failed symbols recorded and retried next run) → write an `agent_runs` row + per-failure error reports
  (`reports/errors/…`) + run-id-tagged structured logs.
- **`agent/cli.py`** — `python -m quantdesk.agent {init|run-once|daemon|status|login-url}`:
  - `init` — interactive/one-shot job setup (or read an existing `agent_jobs` row);
  - `run-once` — a single pass (what the OS scheduler invokes);
  - `daemon` — long-running **APScheduler** loop honoring `schedule_cron` + market-hours awareness;
  - `status` — last run, next run, per-symbol coverage, `NEEDS_LOGIN` flag.
- **OS-level autonomy (the real "on its own"):** generate a **`run_agent.bat`** + **Windows Task
  Scheduler** registration (and a cron line for non-Windows) so a single `run-once` fires daily after
  market close (e.g. 16:00 IST), fully independent of whether the Streamlit UI is open. The in-app
  `daemon` (APScheduler in a background thread) is offered as the simpler, only-runs-while-app-open
  alternative.
- **Market calendar:** skip weekends/holidays (reuse/seed an exchange holiday list); for intraday jobs,
  optional intraday refresh cadence.

**DoD:** an end-to-end `run-once` on a 2–3 symbol universe downloads + builds all selected TFs, writes
an `agent_runs` summary, and a second immediate run is a no-op (incremental). 401 simulation → clean
`NEEDS_LOGIN` halt, no partial corruption. Rate limiter holds under the full universe.

---

## Phase 3 — Dashboard: Agent setup wizard + monitor  [effort: M]
A new Streamlit page (`ui/pages/data_agent.py`, added to `components.NAV`) — the only surface a
non-coder touches.

- **Setup wizard (define once):** broker · universe (reuse `render_instrument_selector` + saved
  `bulk_templates`, or NIFTY50/BankNifty presets) · base interval(s) · **target timeframes (multiselect:
  1/3/5/15/75/Day/Week/Month)** · save format + folders (`render_save_folders`) · schedule (time-of-day
  or interval) · incremental on/off. Writes one `agent_jobs` row. Every field gets a plain-language "?"
  tooltip (project UX standard).
- **One-click setup of autonomy:** a button that generates `run_agent.bat` and registers/*shows the
  exact command* for Windows Task Scheduler — plus copy-paste instructions. Plain-language: "your
  computer will run this every day at 4 PM; you only need to log in each morning."
- **Daily login & status:** a prominent **"Login for today"** action (writes the fresh token to
  `broker_tokens`); a status panel — last run, next run, `NEEDS_LOGIN` banner, per-symbol/interval
  coverage table ("RELIANCE 1Min ✓ up to 2026-06-27, 15Min ✓, Day ✓"), failures, bars added last run;
  and a **"Run now"** manual trigger.

**DoD:** the page renders (AppTest), a job can be created/edited, a manual "Run now" executes a real
run-once and the status table reflects it; setup instructions verified end-to-end on a real machine
(build-verify-loop).

---

## Phase 4 — Hardening & niceties  [effort: S–M]
- **Notifications** (opt-in): Telegram/email on run complete / failure / `NEEDS_LOGIN` (Freqtrade-style).
- **Per-broker rate-limit config** surfaced in Settings (defaults conservative; user tunes to their plan;
  agent always honors `Retry-After`).
- **Coverage/health report:** "which symbols are stale / failing", exportable.
- **Docs:** `docs/` "Set up the auto-download agent" walkthrough; tooltips link to it.

---

## Cross-cutting / risks
- ⚠ **Token expiry is the #1 UX reality** — design the daily one-click re-login to be frictionless; be
  honest in the UI that brokers force a daily login (Dhan is the exception). Never auto-TOTP by default.
- 🔒 **Secrets:** tokens live in the gitignored app DB; never logged, never committed. (Secret scan
  already part of the publish flow.)
- 🧱 **Decoupling:** the agent must run **without Streamlit** (a plain process). Keep `agent/*`
  Streamlit-free (like the existing backend modules); the UI only reads/writes the DB + token store.
- ♻ **Idempotent + resumable:** every run is safe to re-run; watermarks + de-dup guarantee no dupes,
  partial runs resume next pass.
- 📏 **Rate limits:** publish-rate numbers vary by broker/plan — make them config, default conservative,
  and always respect `429`/`Retry-After`. This replaces today's blind semaphore-only approach.

## Suggested release framing
- **v8.0** = Phases 0–2 (the autonomous engine + CLI + scheduling — usable from the terminal/Task
  Scheduler). **v8.1** = Phase 3 (dashboard) + Phase 4 (notifications/docs).

## New/changed files (summary)
```
quantdesk/agent/{__init__,store,ratelimiter,incremental,runner,scheduler,cli}.py
quantdesk/data/resample.py            # + resample_calendar (D/W/M)
quantdesk/core/db.py                  # + agent_jobs/agent_runs/data_watermarks/broker_tokens (+migration)
quantdesk/brokers/downloader.py       # route through ratelimiter; typed error handling (401/429/5xx)
quantdesk/ui/components.py            # NAV entry
quantdesk/ui/pages/data_agent.py      # setup wizard + monitor
run_agent.bat (generated)             # OS-scheduler entrypoint
tests/test_agent_*.py
```
