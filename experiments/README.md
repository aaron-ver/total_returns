# experiments/ — research sandbox (isolated from production)

Test bed for the Barclays inflation-guide patterns. **Fully isolated**: reads the production
caches, writes only to `experiments/out/` (results) and `experiments/cache/` (experiment-only
pulls). Production never imports this folder; deleting it cannot break the pipeline. Conventions
match the engines exactly (DV01-normalized bp per 100k DV01, BE = linker − β·nominal at β=1,
linear cumsum, energy-interval bucketing per hedge.py / energy_intl.py).

Run everything from the project root with the venv python.

## Exp 1 — extension-conditioned month-end returns  `exp_extension.py`
Month-end index flows scale with that month's **index duration extension** (new issuance entering
at the rebalance + short bonds dropping below 1y), which is predictable in advance from the
issuance calendar. Tests whether the last-5-trading-day return is proportional to the month's
extension, and whether it reverses in the first 5 days of the next month.
```
.venv/Scripts/python.exe experiments/exp_extension.py
```
**First results (2026-07): the strongest finding so far.** All six US series (5y/10y/30y ×
TIPS/BE) show positive ME-window betas with t ≈ 3.2–3.5 (12–21 bp of ME return per year of
extension; hi-vs-lo extension terciles ~5 bp apart in the week), with negative (not individually
significant) reversal betas — the buy-pressure-then-fade signature. FR_OATI echoes it (t ≈ 2.7–3.2).
11 of 68 series |t|≥2, concentrated in US + FR_OATI rather than scattered. Extension proxy:
TTM-weighted, real auction amounts for US, constant current-outstanding weights for intl.
Caveats: TTM duration proxy; US profile puts the big extension in **April** (new 5y) with Jul
secondary, Oct/Dec negative — slightly different from the guide's Jan/Jul story but data-driven.

## Exp 2 — factor controls on the energy hedge  `exp_factors.py`
The guide's 10y BE model is slope + RBOB + VIX + dollar (adj-R² ~0.83). Two checks on OUR hedge:
(1) is the crude β stable once 3m10y slope / broad dollar / VIX are controlled for (omitted-variable
bias in the 2020–22 β hump?); (2) does dollar-deflating crude tighten the fit? Plus TTF gas and
UK Bank Rate as intl-specific factors.
```
.venv/Scripts/python.exe experiments/exp_factors.py pull    # ONE-TIME, Bloomberg terminal open
.venv/Scripts/python.exe experiments/exp_factors.py         # analysis, no terminal needed
```
Outputs per series: β_uni vs β_ctl (+% shift), R² raw vs controlled vs dollar-adjusted, control
t-stats, rolling 2y β with/without controls (the stability check).

**First results (2026-07).** (1) The single-factor crude β IS overstated: controls shrink it
−18/−29/−40% for US 5y/10y/30y and ~−5…−15% intl (UK 2y −32%). Slope (t≈+18…+25) and VIX
(t≈−13…−15) are the big omitted variables in the US; **TTF gas is the dominant extra factor for
euro/UK breakevens** (t up to +13), confirming the guide's LASSO. R² roughly doubles with controls
(US 10y 0.12→0.30). (2) Dollar-deflating crude does NOT improve the fit at daily frequency
(r2_usdadj ≈ r2_uni everywhere) — honest null for that guide claim in our setup. Practical read:
front-end Brent-only hedge ratios are ~10–30% too big; part of that exposure is really gas/slope.

## Exp 3 — maturity-month seasonal RV  `exp_maturity_rv.py`
Seasonal accretion should make maturity-month cohorts earn different calendar-month returns
(July-maturity capturing H1 accrual, etc.). Cross-sectionally demeaned monthly per-bond bp by
(market, cohort, calendar month).
```
.venv/Scripts/python.exe experiments/exp_maturity_rv.py
```
**First results (2026-07): weak.** 6/96 cells |t|≥2 ≈ the chance rate; July-cohort H1-vs-H2 shows
nothing. Only a mild UK cluster (Aug-maturity cohort +1.8–2.6 bp in Jan/Jul, t≈2.3–2.8 — RPI
seasonality is the strongest of the three markets). Realized *returns* may be the wrong lens —
the guide's claim is about *pricing* (rich/cheap vs a fitted seasonal-adjusted curve); the sharper
test is the fitted-curve residual study below.

## Next candidates (not yet built)
- **CPI-print-day event study** — needs a release-date pull (ECO calendar) into experiments/cache.
- **Three-anchor auction study** (announcement / auction / settlement) — announcement dates needed;
  auction+settle already in cache_intl/auctions.parquet.
- **New-issue vs reopening split** of the auction cycle (flag exists in both auction caches).
- **Fitted seasonal+floor-adjusted real curve → rich/cheap residuals** (the guide's micro-RV
  framework) — bigger build, would supersede Exp 3.
- **Deflation-floor state variable** — flag low-BE regimes where close-maturity BE comparisons break.
- **SPF inflation-risk-premium → year-ahead BE returns** (quarterly, slow signal).
