# ROADMAP v7 — Feature Maturity (normalized features · ML rigor · optimizer validation)

**Status:** PLANNED (not started) · **Priority:** HIGH · **Created:** 2026-06-27 · after v6.1
**Source:** multi-agent feature-maturity audit (run `wf_ebc554f3-006`; the run aborted on a schema cap
but 4 of 5 reviewers completed — assessments recovered from transcripts, unanimous & code-grounded).

> This file is implementation-ready so a fresh context can continue without re-deriving anything.

---

## 0. Verdict (why this phase exists)
The user's critique is **correct and confirmed in code**. Maturity scorecard:

| Feature | Maturity | Useful as-is |
|---|---|---|
| Backtester | usable / borderline-pro | yes |
| Optimizer | **amateur** | marginal |
| **ML Pattern Finder** | **amateur (toy-ish)** | marginal |

**Root problem (ML):** features are RAW ABSOLUTE snapshots — `metrics.enrich_trades` (lines ~73-89)
samples each indicator/OHLCV array at the entry bar verbatim (`vals = arr[entry_bars]`); `ml/patterns.py`
trains XGBoost + a depth-3 DecisionTree on those. A learned rule like `Volume > 637000` or
`EMA20 > 1330` cannot generalize across instruments (RELIANCE ~1,300 vs NIFTY ~25,000) or timeframes.
Also: no train/test holdout, no significance test, no multiple-comparison control, and **bulk
cross-instrument aggregation concatenates different-scale trades into one matrix** (`ml_finder.py`).

**Fix thesis (validated, unanimous):** features must be RELATIVE / NORMALIZED and comparable across
assets — exactly the user's examples (`volume / SMA20(volume)`, EMA slope). Normalized features ALSO
fix bulk aggregation (ratios/z-scores are already comparable, so concatenation becomes valid).

---

## Phase A — Normalized feature library  (highest impact; do first)  [effort: M]

Implement the comparable features as **NEW INDICATOR TYPES in the existing registry**, so the no-code
builder, the ML finder, and the plotter ALL get them for free via the single-source pipeline
(`selectable_columns` → `enrich_trades` → codegen). No separate code path.

### Features to add (name · formula · placement)
1. **Volume Ratio** — `Volume / SMA(Volume, n)`  (n=20). panel, guide=1.0. (also exposes a >1 meaning)
2. **Distance from MA (ATR)** — `(src - EMA(src, n)) / ATR(m)`  (n=20, m=14). panel, guide=0.
3. **MA Slope (ATR)** — `(EMA(src,n) - EMA(src,n).shift(k)) / (k * ATR(m))`  (n=20, k=5, m=14). panel, guide=0.
4. **Z-Score** — `(src - rolling_mean(src,N)) / rolling_std(src,N)`  (N=50). panel, guides ±2, 0.
5. **Percentile Rank** — rolling-N percentile of `src` in [0,1]  (N=50). panel, guides 0.2/0.8.
6. **Return (n-bar)** — `src / src.shift(n) - 1`  (n=5). panel, guide=0. (optional ATR-normalized variant)

`src` uses the existing source-chaining (Close default, or any prior indicator). Volume Ratio is
volume-based (source fixed to Volume).

### Where to edit (mirror how ALMA/LR-Slope/R² were done)
- `quantdesk/config.py`: add each type to `INDICATOR_PLACEMENT` ("panel") and `INDICATOR_PANEL_GUIDES`.
- `quantdesk/strategy/indicators.py`:
  - add to `INDICATOR_TYPES` and `INDICATOR_PARAM_SPECS` (period/atr_period/lookback/N as needed),
  - add vectorised helpers: `vol_ratio_vec(vol,n)`, `dist_from_ma_atr_vec`, `ma_slope_atr_vec`,
    `zscore_vec`, `pctile_rank_vec`, `ret_n_vec` (numpy/pandas; see lr_slope_vec/alma_vec pattern),
  - add `emit_indicator` branches emitting `self.X = self.I(<helper>, <src/data.High/Low/Close>, <params>)`.
- `quantdesk/strategy/compiler.py`: inject the new helpers into `_namespace()` (like alma_vec/lr_slope_vec/r2_vec).
- (No change needed in models/selectable_columns/enrich_trades/charts — they read the registry generically.)

### Verify
- New `tests/test_features.py`: each helper matches its formula on RELIANCE; a codegen+compile+backtest
  runs using each new type; cross-asset sanity (same feature on RELIANCE vs a high-priced/▲index series
  yields comparable ranges).
- Parity unaffected (these are NEW types; existing strategies unchanged). Run full suite + verify_app.

---

## Phase B — ML rigor  (make discovered rules trustworthy)  [effort: M]  ✅ DONE 2026-06-28
**Implemented** (56 tests green; AppTest + real-data drive): `ml/patterns.py` rewritten + `ml_finder.py` +
`indicators.partition_features`/`CROSS_ASSET_COMPARABLE_TYPES` + `batch.extract_trades` `Source` tagging +
`config.ML_SAMPLE_FLOOR/ML_HOLDOUT_FRAC/ML_FDR_ALPHA`. The four points below are all done:
ML finder defaults to normalized features (warns + per-instrument z-scores raw ones before bulk concat);
time-ordered holdout (70/30) **and** walk-forward k-fold; per-leaf sample-size floor → one-sided binomial
vs OOS baseline → Benjamini-Hochberg FDR → Wilson lower-bound gate; only survivors promoted to `rules`,
the rest surfaced as `candidate_rules` with a reject reason. XGBoost importance kept descriptive (not gated).
Tests: `tests/test_ml_rigor.py`. Original implementation (for reference):

Edit `quantdesk/ml/patterns.py` + `quantdesk/ui/pages/ml_finder.py`:
1. **Default to normalized features** — the ML page should steer users to the Phase-A features (and/or
   auto-derive them from selected base indicators); warn when raw absolute columns are selected.
2. **Per-instrument normalization before bulk aggregation** — if any raw feature remains, z-score it
   per source file before concatenation (Phase-A features make this largely moot).
3. **Holdout / k-fold** — time-ordered 70/30 split (or k-fold); fit on train, score rules on test;
   report OOS win-rate, not in-sample.
4. **Significance gate** — per decision-tree leaf: Wilson lower-bound on win-rate + one-sided binomial
   test vs baseline; **Benjamini-Hochberg FDR** across leaves; **sample-size floor** (config, default
   ~30-50). Only surface rules that pass. Keep "exploratory" labelling until they do.

### Verify
- `tests/test_ml_rigor.py`: synthetic dataset with a known edge → rule recovered AND passes significance;
  pure-noise dataset → NO rule surfaced (FDR/holdout rejects it).

---

## Phase C — Optimizer validation  (stop compounding overfitting)  [effort: M-L]  ✅ DONE 2026-06-28
**Implemented** (67 tests green; AppTest + real-data drive): all four items below shipped.
`metrics.py` → `probabilistic_sharpe_ratio`/`expected_max_sharpe`/`deflated_sharpe_ratio`/`sharpe_moments`/
`validation_stats` + `monte_carlo_bootstrap`. `optimizer.optimize` → `(stats, best, meta)` exposing the grid
`return_heatmap` + Optuna trials/per-trial Sharpes + a `PSR_OBJECTIVE`. NEW `backtest/wfo.py` walk-forward
(anchored/rolling, stitched OOS equity, Walk-Forward Efficiency). `charts.py` → `parameter_stability_heatmap`/
`monte_carlo_fan`/`walk_forward_equity`. Backtester page surfaces all four. Tests: `tests/test_optimizer_validation.py`.
Original plan (for reference):

- `quantdesk/backtest/metrics.py`: add **Deflated / Probabilistic Sharpe** (correct best-of-N trials;
  feed `n_trials`); have Optuna optionally maximise DSR.
- `quantdesk/backtest/optimizer.py`: in grid mode use `Backtest.optimize(..., return_heatmap=True)`
  (currently discarded) → return the heatmap for a **parameter-stability surface** (UI renders 1D/2D).
- `quantdesk/backtest/wfo.py` (new): rolling/anchored **walk-forward** — optimise per IS window, evaluate
  frozen params per OOS window, stitch OOS equity, report **Walk-Forward Efficiency**.
- **Monte-Carlo / bootstrap**: resample per-trade `ReturnPct` with replacement (R=1000) → P5/P50/P95
  final-equity & worst-drawdown bands; fan chart in the Backtester.

### Verify
- `tests/test_optimizer_validation.py`: DSR formula sanity; WFO stitches N OOS segments; MC bands bracket
  the realized curve.

---

## Suggested release framing
- **v7.0** = Phase A + Phase B (the user's core complaint — comparable features + trustworthy ML).
- **v7.1** = Phase C (optimizer validation: DSR, stability heatmap, WFO, MC).

## Cross-cutting notes / gotchas
- Reuse the vectorised-helper + compiler-namespace-injection pattern (proven for ALMA/LR-Slope/R²).
- New types are "panel" placement → the v6.1 chart redesign shows them in their own panels automatically.
- ⚠ Security gate still applies before any user-typed formula feature: AST-whitelist `exec()` first.
- Verification env: simpleton venv; full pytest ~3 min; AppTest for pages; no headless screenshots.

## Reference: unanimous proposed features (from the audit, deduped)
`volume/SMA_n(volume)` (+>1 flag) · `(Close-EMA_n)/ATR_m` · `(EMA_n - EMA_n.shift(k))/(k·ATR_m)` ·
`Close/Close.shift(n)-1` · `(x-mean_N)/std_N` · rolling-N percentile rank · (validation) Deflated Sharpe,
Walk-Forward Efficiency, bootstrap MDD/CAGR bands, per-rule Wilson LB + BH-FDR, parameter-stability score.
