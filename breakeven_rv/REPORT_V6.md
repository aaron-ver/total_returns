# breakeven_rv — v6: the hybrid model & quiet-regime detection (run 2026-07-08)

> **Hindsight-discipline statement (header, per spec).** The 10y 2021-22 staleness
> episode is the DEVELOPMENT case: all three quiet detectors were designed knowing
> it. Validation is elsewhere: the 5y/30y (never inspected during design), pre-2013
> breaks, and the UK (UK_3M) and France (FR_OATEI) linker markets from cache_intl,
> run with identical config parameters and zero per-market tuning. All detector and
> hybrid parameters were declared in config before any validation run; one
> sensitivity appendix is reported whole (`v6_drift_sensitivity.csv`), nothing was
> selected from it. **No backtesting, no PnL, no strategy work.**
> Tables `reports/v6_*.csv`; lead figure `figures/v6_frontier.png`.

## TL;DR — the five synthesis answers

**1. The frontier: a genuine tradeoff — no architecture dominates.** (Lead figure;
`v6_frontier.csv`.) Honesty and freshness are bought with the same currency
(adaptation), and every architecture buys a different mix:

| architecture (10y) | phantom | days structurally wrong | abstention |
|---|---|---|---|
| rolling 504 (baseline) | 0.54 | 53 | 504 |
| EWLS 252 unsegmented | 0.63 | 47 | 504 |
| frozen @ stress breaks (v5) | **0.20** | 286 | 414 |
| h2: EWLS-in-segment (2y/4y hl) | 0.65-0.66 | **118-123** | 414 |
| h3: ridge-to-anchor (α=5) | 0.17 | 367 | 414 |
| ref: frozen @ failed-budget combined | 0.18 | 23 | **2,237 (half the sample)** |

The sharpest lesson is **h2**: slow within-segment adaptation re-admits nearly ALL
the phantom (0.65 vs rolling's 0.54 — slow adaptation is still adaptation; the
frozen-z audit sees through the half-life). h3 keeps honesty but inherits the stale
anchor. The failed-budget combined reference is fresh AND honest — by abstaining
half of history, i.e. the rolling window rebuilt in break space. Same ordering on
5y and 30y. **There is no free lunch in this design space; the binding constraint
is Part B's.**

**2. Quiet detection: FAIL under the declared rules — on the development case AND
in validation.** (`v6_detector_grades.csv`, full FP budget in `v6_break_budget.csv`.)

| detector | breaks/18y (US tenors) | budget (≤8) | timely narrative hits |
|---|---|---|---|
| stress monitor (v5, reference) | 5.2 | PASS | 3-4 of 6 (GFC +7bd, COVID +11-15bd, pivot +12-13bd, Apr-25 +9bd; misses taper & 2021 — by design) |
| drift-CUSUM (prime candidate) | 31-32 | **FAIL** | 4-6/6, but only because it fires every ~6 months |
| forecast-error degradation | 7-10 | mixed | 1-2/6 — passes budget where it detects nothing |
| macro-state shift | 21 | **FAIL** | 2/6 |

Same verdict on UK and France (drift 28-32/18y, macro 20-26/18y). The sensitivity
appendix (h × burn-in grid, reported whole) shows the failure is structural, not
parametric: **no cell passes the budget on the 10y**, and the UK's only
budget-passing cells (burn-in 252) detect nothing. Diagnosis: a frozen model
anchored on a 60-120bd burn-in is *always* biased somewhere, so residual-drift
detectors degenerate into clockwork; and macro states trend perpetually, so
within-segment Mahalanobis distance always eventually exceeds any threshold. Honest
exceptions worth recording: the macro detector found the **UK LDI event at +2bd**
and the UK inflation surge at +51bd — real signal buried in an unusable
false-positive stream.

**3. Reselection: sensible rotation, wrong bottleneck.** (`v6_reselect.csv`,
`v6_reselect_scores.csv`.) LASSO→OLS at stress breaks picks economically coherent
regime variables — the 2020-03 segments select {VIX, USD} (crisis), the 2022+
segments select {CPI momentum, nominal level} over gasoline (inflation regime),
exactly the hypothesized rotation. With 120bd burn-in it also cuts phantom to
0.03-0.13. But it barely dents the 2021-22 staleness (187bp vs 196bp max on the
10y), because **the hypothesized "2021+ segment" does not exist under a stress-only
skeleton** — the segment runs Mar-2020 → Oct-2022, and no variable chosen on COVID
burn-in data can encode the surge. Reselection is downstream of break timing; the
missing quiet break is the bottleneck. (60bd burn-in selection is additionally
unstable — near-full baskets in calm segments; small-n caveat as spec'd.)

**4. Does the honest model carry more genuine reversion? Not demonstrably.** Price
shares under the low-phantom architectures are erratic (few converged episodes;
ratio-of-small-sums instability: −1.0 to +1.8 across tenors) and do not show a
systematic improvement over rolling (~0.10-0.31). Cutting phantoms shrinks the
episode count rather than enriching it. On current evidence the honest model is a
better *measurement* instrument, not (yet) a better *trading* one — stated plainly.

**5. The recommended production FV architecture** (for any fair-value desk, from
what survived): **rolling or slow-EWLS FV for day-to-day levels, PLUS a frozen
stress-segmented model run in parallel as the honesty audit, PLUS the v5 stress
monitor for break-triggered re-estimation and in-flight risk.** Concretely: (i)
levels and factor exposures from the adaptive model; (ii) any *signal* extracted
from its residual must pass the frozen-z audit before being believed (the phantom
rate of the adaptive residual is ~50-65%); (iii) hard re-estimation + variable
reselection only at stress-monitor breaks (5-6/18y, zero calm false fires, +7-15bd
detection lags), with ≥120bd burn-in. **Measured failure modes:** blind to quiet
regime changes (2013, 2021 — no admissible detector found; the one candidate that
saw them, macro-shift, fails the false-positive budget); abstention ~7-15% of
sample around breaks; per-segment selection unstable below 120 observations.
**What would falsify it:** a quiet-break detector that passes the ≤8/18y budget
with ≥half of narrative events caught inside 120bd on ≥2 unseen markets — the
sensitivity appendix shows no such detector exists in the space searched here; a
fundamentally different design (e.g. multivariate structural-break tests on the
factor COVARIANCE, or exogenous policy/text-based break candidates) is the v7
research object.

## v5 → v6 reconciliation

| number | v5 | v6 | why |
|---|---|---|---|
| frozen-monitor phantom (10y) | 0.13 | 0.20 | v6 engine adds the 126bd refractory INSIDE the sequential run and resets triggers at breaks; 6 breaks vs 5 (one 2007-08 / 2008-03 split) |
| frozen-monitor breaks | 5 | 6 per tenor | same; all narratively defensible except 2022-05-09 (5y) / 2008-03-06 (early-GFC tremor — borderline story) |
| staleness (frozen, 10y) | ">100bp for a year" (figure) | quantified: max |60bd median| 196bp, 286 days structurally wrong | v6 metric definitions |
| 2021 inflation surge detection | "missed by stress; open problem" | still missed by every ADMISSIBLE detector; caught only by budget-failing ones | Part B verdict |

## Engineering notes (documented, all pre-validation)

- Sequential engine corrections vs the first draft: 126bd refractory after breaks
  (v5's declared detector parameter, missing from the first engine pass — without it
  every trigger re-fires on the same event and the FP budget fails trivially); the
  macro detector arms on the first 120 VALID observations (a NaN prefix left it
  permanently unarmed — genuine bug, fixed before any grading was read as final).
- Intl Layer-1 analogue: BE level from the engine's UK_3M / FR_OATEI 10y CMT bucket
  quotes (nominal − linker yield, roll jumps included); slope from the 7y bucket
  (the 2y bucket only exists from 2024 — CMT buckets start N years pre-maturity);
  factors [local slope, log Brent, VIX], no FX (no cached series; new pulls out of
  scope). Usable history 2019+ (~1,700-1,900 days), which covers COVID, the
  inflation surge, and (UK) the LDI event; GFC/Brexit/euro-crisis anchors are
  pre-sample and excluded from grading denominators.
- Drift/ferr triggers watch the FROZEN-anchor residual even under adaptive
  within-segment models, so a model cannot hide drift from its own detector.
