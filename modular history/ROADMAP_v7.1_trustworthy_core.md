# ROADMAP v7.1 — Trustworthy Core

**Status:** IN PROGRESS · **Priority:** HIGH · **Created:** 2026-06-28 · after v7.0.1
**Origin:** the multi-agent quant-desk audit (verified **4.8/10**). This milestone fixes the three
highest-impact things the panel found *wrong or dangerous* — before building v8 (the data agent).
Target: **4.8 → ~6.0**. Then Roadmap 8.

Order is dependency-driven (security → charges → risk), not impact order.

---

## Task 1 — AST-whitelist + preset validation (security)  ✅ DONE 2026-06-28
Closes the audit's **preset-upload RCE**: `strategy_builder.py` file_uploader → `_restore_from_preset`
→ codegen → `compiler.exec()` fed attacker-controlled JSON with no validation.
- **`quantdesk/strategy/validation.py`** (new): `assert_safe_ast(source)` — AST scan run by the
  compiler right before `exec()`; rejects imports outside `{numpy,pandas,pandas_ta,ta,hurst,hmmlearn,
  scipy}`, denied builtins (`eval/exec/__import__/open/os/sys/…`), and any dunder attribute or string
  constant (blocks `().__class__.__bases__` escapes). Because every run funnels through
  `compile_strategy`, this protects programmatic callers too, not just the upload path.
  `validate_preset(data)` — schema/type-validation: every rule/zone column ref must resolve to a real
  OHLCV/indicator column, thresholds/params must be numeric, types/operators/actions/modes must be
  known registry values.
- **`compiler.py`**: `assert_safe_ast(code)` on cache-miss (cached with bytecode).
- **`strategy_builder.py`**: `_restore_from_preset` routes upload + server-load through `validate_preset`.
- **Verified:** `tests/test_security.py` (21) — every classic escape rejected, real multi-indicator
  source (incl. Hurst/HMM imports) passes, malicious presets rejected, app-export round-trips. Full
  suite **88 green** (zero AST false positives) + AppTest all pages + UI backtest.

## Task 2 — Charges into the equity curve (correctness)  ✅ DONE 2026-06-28
Fixed the audit's standout finding: the India charge model was a **post-trade display pass** that
never entered the simulated equity curve.
- **`costs/india.py`**: `make_fee_model(segment)` → a backtesting.py commission **callable**
  `(order_size, price) -> cash`. Since backtesting.py invokes it per-leg with the *position* sign
  (can't tell buy from sell), the one-sided STT/stamp are split evenly across legs — EXACT for the
  symmetric charges and within ~0.001% of notional on the asymmetric ones (golden-number verified).
- **`engine.make_backtest`/`run_single`/`native_bokeh_html` + `wfo.walk_forward`**: thread `segment`
  (+ `slippage` → backtesting.py `spread`); when `segment` is set the fee model is used, so Return/
  Sharpe/DSR/WFO/MC and the optimizer objective are all charge-accurate. No segment ⇒ legacy flat
  commission (back-compat for tests/callers).
- **`backtester.py`**: passes `segment`/`slippage` into run_single/native_bokeh_html/walk_forward; the
  Gross-vs-Net panel is now a per-trade **reconciliation** (charges applied in-sim, PnL already net).
- **Verified:** `tests/test_charges_in_equity.py` (7 — golden-number reconciliation per segment,
  hand-computed delivery leg, charges reduce Return, run_single↔make_backtest parity, back-compat).
  Full suite **95 green** + AppTest + real-data drive: an EMA strategy's gross 20.8% ret / 0.79 Sharpe
  / 95% PSR collapses to 0.7% / 0.03 / 55% net of ₹20k charges — the exact "not desk-deployable" gap closed.

## Task 3 — `quantdesk/risk/` module (capability)  ✅ DONE (core) 2026-06-28
Lifts the audit's lowest dimension (Risk 3.5) out of fixed-₹/lot-only sizing.
- **`quantdesk/risk/{sizing,stops}.py`** (new): pure, unit-tested helpers — `risk_pct_qty`
  (`qty = equity*risk% / stop_distance`), `atr_risk_qty`, `percent_equity_qty`, `fixed_value_qty`,
  `kelly`/`half_kelly`; `static_sl_tp`, `atr_stop`, `breakeven_stop`, `trailing_stop`. Defensive
  (bad inputs → one lot / None). Injected into the compiler namespace so codegen calls the *tested*
  functions instead of inlining string math.
- **`models.RiskManagement`** + **`codegen.py`** + **builder UI** + **`validation.py`**: a new
  **"Risk % of Equity per Trade"** sizing mode (sized from the Static-% stop distance, capped to
  affordable size) and a **max-drawdown circuit-breaker** (`max_dd_halt_pct`) in `next()` — flatten +
  block new entries once equity falls that far below its running peak. Both round-trip through presets.
- **Verified:** `tests/test_risk.py` (10 — sizing/stop math, codegen→compile→backtest through the AST
  gate, default-unchanged parity). Full suite **105 green** + AppTest. Real drive: the breaker capped
  drawdown from −11.8% to exactly −10.0%; risk-% sizing runs.
- **Deferred (Task 3 pt.2 / next):** wiring ATR/breakeven/time stops into codegen (helpers exist),
  partial exits/scale-out, and Kelly-informed sizing.

## Release
Ship Tasks 1–3 together as **v7.1** (charges-in-equity is the CHANGELOG headline); publish + sambhav
sync at the end. `ROADMAP_v8_data_agent.md` (the data agent) follows as v8.
