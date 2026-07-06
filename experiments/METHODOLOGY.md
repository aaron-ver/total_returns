# Experiments — methodology reference

Precise regression specs, variable construction, and output-column glossaries for each experiment.
Plain-English motivation lives in [README.md](README.md); this doc is the "what exactly did we
run" reference. Shared conventions (all experiments):

- Returns are the **cached engine series** — DV01-normalized bp per 100k DV01 per leg, financing
  netted, BE = linker − β·nominal at **β = 1**. Never recomputed here.
- OLS: `ols(x, y)` = slope/intercept, R², t-stat(slope), pairwise NaN-dropped (hedge.py's
  convention). Multivariate `mols` adds per-coefficient t from plain (X'X)⁻¹σ² standard errors.
- |t| ≥ 2 ≈ "unlikely to be luck"; when a table tests many cells, expect ~5% of them to clear
  |t|=2 by chance — judge the *count and concentration*, not single cells.

---

## Exp 1 — extension-conditioned month-end (`exp_extension.py`)

**Unit of observation: one calendar month** (per return series).

### Regressions (per series)
```
ME(m)  = α + β_me  · ext(m) + ε      "month-end week vs extension"
REV(m) = α + β_rev · ext(m) + ε      "next week's give-back vs extension"
```
- `ME(m)`  = Σ of the series' daily bp over the **last 5 trading days of month m**.
- `REV(m)` = Σ over the **first 5 trading days of month m+1**.
- These calendar windows are unrelated to the dashboard's P1–P4 (which slice the *auction* cycle).

### X: index duration extension, ext(m) — in YEARS, market-wide (not per bucket)
Index membership rule: first-issue ≤ rebal date AND time-to-maturity ≥ 1y (standard index
drop-out rule). Duration proxy: **TTM in years** (low-coupon linkers; ranks months correctly).
Weights: **US** = cumulative auction `totalAccepted` per CUSIP (time-varying — reopenings enter
here as weight increases); **intl** = current `AMT_OUTSTANDING` from static, held constant
(entry/exit still drives the series; tap amounts not modeled in v1).

At each month-end m, with BOTH portfolios' TTMs evaluated **at m** (so pure aging cancels):
```
ext(m) = D[ members(m),    amounts(m)    ]     "new: entrants added, <1y dropped, amounts updated"
       − D[ members(m−1),  amounts(m−1)  ]     "old: last rebal's book, aged to m"
D[·] = Σ wᵢ·TTMᵢ / Σ wᵢ
```
Entrants (new issues) usually push D up; dropouts (a 0.9y bond leaving an 8y-average book) push D
up; a month with no entrants has ext ≈ 0. ext(m) is **knowable in advance** from the auction
calendar — that's what makes it a signal rather than a diagnosis.

### Hypothesis
Index trackers must buy the duration their benchmark adds at the rebalance ⇒ β_me > 0
(bigger extension ⇒ stronger month-end week), and if it's flow pressure rather than information,
partial reversal ⇒ β_rev < 0.

### Output columns — `extension_results.csv`
| column | meaning |
|---|---|
| `series` | return series: `US_{tenor}` or `{market}_{bucket}` |
| `leg` | `TIPS`/`linker` = outright leg; `BE` = financed breakeven (β=1) |
| `n_months` | monthly observations in the regression |
| `beta_me_bp_per_y` | bp of ME-window return per **year** of extension |
| `t_me`, `r2_me` | t-stat and R² of the ME regression |
| `beta_rev_bp_per_y`, `t_rev` | same for the next-week reversal regression |
| `me_loExt_bp` / `me_hiExt_bp` | mean ME-window bp in the lowest / highest extension **tercile** of months (no-regression sanity check; NaN if the extension series has too few distinct values to cut) |

`extension_series_{US,market}.csv`: `month`, `ext_y` (the X), `n_index` (members after rebal),
`entrants`, `dropouts`.

### 2026-07 result + caveats
All six US series β_me > 0 with t ≈ 3.2–3.5 (12–21 bp/y; hi-vs-lo tercile ≈ 3–6 bp on the week);
β_rev < 0 but individually insignificant. FR_OATI echoes (t 2.7–3.2). Caveats: TTM duration proxy;
intl static weights; market-wide extension (a bucket-matched extension is the natural v2); the US
extension seasonal peaks in **April** (new 5y) with Jul secondary — check `extension_series_US.csv`
before trading a specific month.

---

## Exp 2 — factor controls on the energy hedge (`exp_factors.py`)

**Unit of observation: one energy-day interval** — identical to production: each bond leg's daily
$ P&L (bp × $10 per 100k-DV01… i.e. the engines' BP_USD) is summed into the interval between
consecutive energy closes, exactly as `hedge.aligned_pairs` / `energy_intl.aligned_pairs` do.

### Regressions (per series)
```
univariate:  be$(t) = α + β_uni · crude$(t) + ε                  (the production hedge)
controlled:  be$(t) = α + β_ctl · crude$(t) + Σ γⱼ·Fⱼ(t) + ε      (same + factor moves)
usd-adjust:  be$(t) = α + β · [crude$(t)·e^(−Δlog$)] + ε          (dollar-deflated crude)
```
Factors Fⱼ = changes over the SAME intervals (level ffilled to each energy close, then diff):
US: `slope` = USGG10YR − USGG3M (pct pts), `logusd` = Δlog(BBDXY), `vix` = ΔVIX.
Intl: `logusd`, `vix`, `ttf` = ΔTTF front (TZT1). (UK Bank Rate pulled, not yet in the spec.)

### Hypothesis
If crude co-moves with slope/dollar/VIX/gas and those also move BE, β_uni absorbs their effect
(omitted-variable bias) ⇒ β_ctl ≠ β_uni. β_ctl is the *pure-crude* hedge ratio.

### Output columns — `factor_hedge_{us,intl}.csv`
| column | meaning |
|---|---|
| `n` | energy intervals in sample |
| `beta_uni`, `r2_uni` | production-style single-factor crude β (contracts) and R² |
| `beta_ctl`, `t_ctl`, `r2_ctl` | crude β with controls, its t, model R² |
| `beta_shift_pct` | (β_ctl/β_uni − 1)·100 — how overstated the crude-only hedge was |
| `r2_usdadj` | R² of the dollar-deflated univariate (compare to `r2_uni`) |
| `ctl_t` | per-control t-stats, e.g. `slope:+23.5 logusd:-5.4 vix:-14.8` |

`factor_rolling_{series}.csv`: monthly-stepped rolling 2y `beta_uni` vs `beta_ctl` (stability /
2020-22 hump check).

### 2026-07 result
β_ctl < β_uni everywhere that matters: US −18/−29/−40% (5y/10y/30y), intl ~−5…−15%, UK 2y −32%.
Slope (t +18…+25) and VIX (t −13…−15) dominate in the US; **TTF gas dominates euro/UK BEs**
(t up to +13). R² roughly doubles with controls (US 10y 0.12→0.30). Dollar-deflation: no
improvement at this frequency (r2_usdadj ≈ r2_uni) — tested, rejected. Ignore FR_OATI_20y
(n=269, t_ctl=1.4).

---

## Exp 3 — maturity-month seasonal RV (`exp_maturity_rv.py`)

**Unit of observation: one (market, maturity-month cohort, calendar month) cell**, one value per
year.

### Construction
1. Per bond: monthly Σ of daily `bp` (per-bond financed return sheets).
2. Cross-sectionally demean within market-month: `rel(i, m) = bp(i, m) − mean_over_bonds(bp(·, m))`
   — kills market-wide moves, isolates bond-vs-market.
3. Cohort = the bond's **maturity month** (1–12). For each (market, cohort, calendar month):
   average `rel` across the cohort's bonds (needs ≥2 bonds), giving one observation per year;
   mean and t across years (needs ≥5 years).

### Hypothesis (guide)
Seasonal accretion makes cohorts earn different calendar-month carry (July-maturity capturing H1
accrual into redemption, etc.) — if the market misprices the seasonal vector, `rel` should be
systematically ≠ 0 in specific cells.

### Output columns — `maturity_rv_cells.csv`
`market` · `cohort_matmonth` (bond maturity month) · `cal_month` (calendar month of the return) ·
`mean_rel_bp` (cohort's mean monthly bp vs its market) · `t` (across years) · `n_years`.

### 2026-07 result
Null: 6/96 cells |t|≥2 ≈ the 5% chance rate; July-cohort H1-vs-H2 ≈ 0. Faint UK cluster only
(Aug-maturity in Jan/Jul). Realized monthly returns are probably the wrong lens for a *pricing*
claim — the sharper test is a fitted seasonal+floor-adjusted real curve with rich/cheap residual
mean-reversion (queued in README "next candidates").
