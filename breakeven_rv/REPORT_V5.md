# breakeven_rv — v5: regime detection & multi-tenor confirmation (run 2026-07-08)

> **Reframe.** Desk guidance: sector-level TIPS RV is too small to be the product;
> regime-change detection is the invited research problem; piecewise-stable models are
> endorsed over continuously-adapting ones. v5's product is METHODOLOGY — TIPS 5y/10y/30y
> as the laboratory. **No backtesting, no PnL, no strategy construction.**
> Part A: the v4 machinery on 5y/30y against six PRE-REGISTERED hypotheses.
> Part B: the in-flight monitor formalized (4 expanding-pctl flags, CRISIS ≥ 2).
> Part C: regime-segmented fair value vs the rolling window (the phantom-cure test).
> Tables: `reports/v5_*.csv`; figures `v5_flag_timeline.png`, `v5_segmented_fv.png`.
> Build notes and one material bug fix: IMPLEMENTATION.md (v5 section).

## TL;DR — the five synthesis answers

**1. H1–H6 scorecard.** (Full detail: `v5_hypotheses.csv`; 10y = generating sample,
5y/30y = confirmation.)

| H | claim | 5y | 30y | pooled | status |
|---|---|---|---|---|---|
| H1 | price share increasing in dealer-flow tercile; top>0, bottom≤0 | **PASS** (−0.98/−0.07/+0.91) | **PASS** (−0.07/+0.20/+0.62) | **PASS**, top−bottom +1.32 [+0.40, +2.90] | **multi-tenor fact** |
| H2 | flow primary vs B-confirm (top-tercile neutral ≈ confirm) | NE | NE | NE (confirm n=5) | still unestablishable |
| H3 | phantom rate rises with entry threshold | **PASS** (.42→.69) | **PASS** (.51→.81) | — | **multi-tenor fact** |
| H4 | in-flight spiral → lower share + fatter tail | FAIL (0.24 vs 0.12) | FAIL (0.36 vs 0.29) | mild at ≥2 flags; strong only at ≥3 | **died out-of-tenor as stated** |
| H5 | stabilization wait keeps ≥ 2/3 of reversion | **PASS** (1.13) | **PASS** (0.97) | — | **multi-tenor fact** |
| H6 | no rich/cheap asymmetry (null replicates) | **PASS** | **PASS** | **PASS** (+0.01 [−0.62, +0.61]) | **multi-tenor fact** |

The interesting failure: **H4**. The v4 "spiral episodes have degraded price share"
was 10y/COVID-specific. What survives multi-tenor is (i) the monitor's *timing* (see
2), (ii) the extreme-tail concentration at **≥3 flags** (pooled: share −0.50 vs +0.25,
MAE p5 −109bp vs −25bp, n=18), and (iii) the wait being cheap (H5). The B-flip cell is
worst on 5y/10y but not 30y — mixed, n≤9 per tenor. Amusing footnote: 10y itself fails
H1's strict monotonicity (−0.29/−0.35/+0.68) while both confirmation tenors pass it.

**2. The monitor LEADS the damage — usable, with an honest asterisk.** Across 31
pooled spiral episodes: median **+8bd before the MAE trough** (77% fired first) and
**+2bd before the 50%-of-drawdown point** (65% first). On the three biggest episodes
(5y −155bp, 10y −101bp, 30y −82bp in 2019-20) the monitor led the trough by 12-14bd
and half-damage by 6-7bd. The asterisk: vs half-damage the lead is tight (p25 = −2bd);
this is an early-warning for the *bulk* of the move, not its first leg.
(`v5_monitor_leadlag.csv`, figure `v5_flag_timeline.png`.)

**3. Segmentation cuts the phantom rate ~4x — and the winning detector is the monitor
itself — but the segmented FV is NOT usable as-is.** (`v5_segmented_fv.csv`.)

| detector | segments (10y) | phantom @ entry 1.0 (baseline .53) | verdict |
|---|---|---|---|
| Page-CUSUM | 21 — fires annually, like clockwork | .21–.35 | REJECTED: a drift accumulator; it rebuilt the rolling window (19/20 breaks in calm periods) |
| coef-distance | 1–5, erratic across tenors, calm-period fires | .00–.18 (tiny n) | REJECTED: unstable identification |
| **monitor ≥10bd CRISIS** | **5-6: Aug-07, Sep-08, Mar-20, Oct-22, Apr-25** | **.13 (10y), .12 (5y), .08 (30y)** | narrative-clean, zero calm false breaks — **wins Part C** |

Costs, honestly: abstention 360bd (~7% of sample at 60bd burn-in; phantom reduction
robust across burn-in {40,60,120}); and the figure shows the real problem — the
post-COVID segment goes **>100bp stale through the 2021-22 inflation regime**, because
a stress-triggered detector cannot see a non-stress regime change (it also misses the
2013 taper). The phantom cure is real (frozen coefficients cannot fake resolutions)
but a 60bd-burn-in frozen model is not a usable FV between stress breaks. The natural
v6 extension (noted, not built): monitor breaks + slow within-segment re-estimation,
and/or drift detection gated by the monitor — and per desk guidance, per-regime
variable reselection belongs exactly here once the segmentation skeleton exists.

**4. The pooled gate (methodology statement, not a strategy pitch).** Best current
definition of a tradeable dislocation: **|z_A| ≥ 1 with dealer-inventory 1y-z in its
top tercile** (the flow gate — now the multi-tenor fact, top−bottom +1.32 quarter-
clustered), with B-confirmation as secondary evidence (its incremental value over flow
remains unestablishable, H2), an **in-flight monitor** (≥2 flags = caution, ≥3 = the
tail state; stand aside and re-enter at stabilization, which costs little — H5), and a
**~40-50% phantom haircut** on any resolution measured against an adapting model.
Honest event frequency: ~72 top-tercile episodes over 13 years across three tenors ≈
**5-6 events/year curve-wide** (before B or state refinements cut further). That is
the capacity fact that motivated the reframe.

**5. What transfers (the reusable product of the program).**
(a) *To cross-sectional linker RV:* the episode/decomposition engine (price vs factor
vs coefficient attribution), the frozen-z phantom test, and the flow-tercile
conditioning port directly — cross-sectional spreads even remove the Layer-1 model,
killing the phantom channel at the source; the repo's intl linker data
(cache_intl) is the obvious next laboratory. (b) *To any rates market:* four
methodology assets are market-agnostic — (i) the phantom-resolution test (any
rolling-model residual anywhere should be audited this way; half of "mean reversion"
in ours was model adaptation), (ii) the transfer test (index → traded price) before
trusting any index-derived signal, (iii) the 4-flag expanding-percentile regime
monitor with the lead/lag audit, (iv) piecewise-stable FV with stress-triggered
breaks as the phantom-robust alternative to rolling windows — plus its measured
failure mode (stress detectors miss non-stress regime changes), which tells you what
to build next. The TIPS-specific findings (flow gate levels, ~2-6 events/yr) stay in
TIPS; the machinery is the product.

## v4 → v5 reconciliation (every 10y number that changed)

| number | v4 | v5 | why |
|---|---|---|---|
| crisis flags | 3 flags, MOVE/VIX on 1y-rolling pctl | 4 flags (adds BE-equity corr), ALL expanding pctl | v5 fixed rule per spec; plus a bug fix — the expanding percentile previously counted a NaN prefix in its denominator, so the dz_B flag could effectively never fire (IMPLEMENTATION.md) |
| crisis-at-entry share (10y, entry 1.0) | 0.07 | 0.09 | above |
| in-flight spiral separation (10y) | −0.37 vs +0.28 (3-flag rule) | −0.26 vs +0.24 at ≥2 of 4; strong separation moves to ≥3 flags | rule change + fix; pooled multi-tenor version lives at ≥3 |
| wait-cost surviving fraction (10y) | 0.80 (n=11) | 0.85 (n=13) | flag fix adds spiral episodes |
| phantom rate 53% | rolling model | 13% under monitor-segmented FV | Part C — the phantom-cure result |
| dealer-flow terciles (10y) | −0.29/−0.35/+0.68 | unchanged (same machinery) | now PASSES pooled + both new tenors (H1) |

## Discipline notes

- H1-H6 were pre-registered in the spec before any 5y/30y number was computed; the
  scorecard grades them mechanically (rules in `v5_partA.py`). New 5y/30y patterns
  (e.g. the 5y's −155bp Dec-2019 episode) are exploratory and NOT in the gate.
- Pooled inference clusters by calendar quarter (episodes across tenors share macro
  state). n shown in every cell; anecdote rule applied (H2 everywhere; B-flip cells).
- 5y seasonal amplitude 17.4bp vs 30y 10.5bp — the expected 5y-seasonality-larger
  check passed; no construction issues flagged.
- The monitor and all detectors use expanding percentiles only; the detector
  parameters (CUSUM k/h, distance pctl, 10bd run) were declared in config before the
  segmentation results were computed.
