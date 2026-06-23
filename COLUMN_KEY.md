# Breakeven total-return export — column key

One sheet per tenor (5y / 10y / 30y), one row per **business day** (the value date). Each leg is
a financed **long**; the breakeven is their difference. Prices per 100 face; returns in **bp**.

### Date / day-count
| Column | Meaning |
|---|---|
| `date` | Observation day. Marked to its **T+1 settlement** (`settlement_date`). |
| `settlement_date` | T+1 settle (bond-market calendar). **V / accrued / IR are valued to this date.** |
| `d` | Settlement span = `settle(t) − settle(t−1)`, calendar days. Lands weekend/holiday carry on the day **before** the weekend: **Friday d=3** (4 before a holiday), Monday d=1; a holiday's stale obs gets d=0. The same `d` drives both accrual (via ΔV) and repo, on the same day. |
| `gc_repo` | GC financing rate that day, % (GCFRTSY; USRG1T/fed funds before 2009). |

### Per leg — `TIPS_` (real) and `UST_` (nominal)
| Column | Meaning |
|---|---|
| `*_cusip` | On-the-run bond the leg tracks (issue-date-gated, TIPS-auction-clock roll). |
| `*_notional` | Face giving 100k DV01 = `1e7 / DV01` (set at the monthly reset, held all month). |
| `*_DV01` | Sizing DV01 per 100 face — **our** calc (real-yield×IR for TIPS; not BBG's TIPS risk, which is ~½). Set at the monthly reset, held constant → it is the bp denominator. |
| `V_tips` / `V_nom` | Cash value per 100 face **at settlement_date**: TIPS = `dirty_real × IR`; UST = `dirty`. |
| `V_tips_prev` / `V_nom_prev` | Prior settle value = the financing base: `fin = d/360 × gc/100 × V_prev`. |
| `TIPS_dirty_real` / `UST_dirty` | `clean + accrued`. |
| `TIPS_IR` | Index ratio = DRI / base CPI (from CPI, matches Treasury to 1e-6). UST IR = 1. |
| `*_clean` | Quoted clean price per 100 (Bloomberg PX_CLEAN_MID). |
| `*_gross_bp` | Leg price+coupon return *before* financing = `(ΔV + coupon) / DV01`. |
| `*_fin_bp` | Leg financing drag (bp) at GC mid = `(d/360 × gc/100 × V_prev) / DV01`. |
| `r_TIPS_bp` / `r_UST_bp` | Leg net daily return = `gross_bp − fin_bp`. |

### Breakeven
| Column | Meaning |
|---|---|
| `net_financing_bp` | `TIPS_fin_bp − UST_fin_bp` (long TIPS pays, short UST earns; GC mid). |
| `r_BE_bp` | Net breakeven daily return = `r_TIPS_bp − r_UST_bp`. |
| `cum_TIPS_bp` / `cum_UST_bp` / `cum_BE_bp` | Running **linear** sum of daily bp (not compounded). |

### Flags
| Column | Meaning |
|---|---|
| `is_roll_day` | Either leg switched CUSIP that day. |
| `is_coupon_day` | A coupon paid on either leg that day. |
| `is_weekend_or_holiday_step` | `d > 1` (step spans a weekend/holiday). |

### Replication chain (hand-check any day)
```
dirty_real = clean + accrued                         (accrued to SETTLEMENT date)
V          = dirty_real × IR    (UST: IR = 1; IR at settlement date)
ΔV         = V − V_prev          (same bond; spans settle(t-1)->settle(t) = d days)
gross_bp   = (ΔV + coupon) / DV01
fin_bp     = (d/360 × gc_repo/100 × V_prev) / DV01
r_leg_bp   = gross_bp − fin_bp
r_BE_bp    = r_TIPS_bp − r_UST_bp ;  cum_* = Σ daily bp
```
`notional = 1e7 / DV01`, so `notional/100 × DV01 = 100,000` (the 100k-DV01 sizing).

### Conventions / caveats
- DV01-normalized to **100k per leg**, reset at the monthly rebalance (held constant within the month).
- **Repo bid/offer `x` & specialness are NOT in these numbers** (GC mid). For long-BE apply
  `(GC + x_tips)` on TIPS and `(GC − x_nom)` on UST; short-BE flips. Use the dashboard to set `x`.
- `d` (settlement span) drives **both** accrual and repo, on the **same day**: a Fri→Mon step
  carries 3 days of each, **booked on Friday** (whose settle jumps to Monday). Holiday Mondays
  get d=0 (stale obs, no carry); the long weekend's 4 days sit on the preceding Friday.
- **BBG tie-out:** V is the dirty price at `settlement_date`, so set Bloomberg's settlement to
  `settlement_date` (Friday's row → Monday settle). Weekly total is invariant to Friday-vs-Monday
  booking — this convention re-dates the carry, it doesn't re-size it.
- Blank TIPS cells in the earliest rows = that tenor's TIPS didn't exist yet (UST-only); BE starts once both legs trade.
