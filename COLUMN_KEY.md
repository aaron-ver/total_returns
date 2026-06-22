# Breakeven total-return export — column key

One sheet per tenor (5y / 10y / 30y), one row per **business day**. Each leg is a financed
**long** (the TIPS leg and the UST/nominal leg); the breakeven is their difference.
All prices are per 100 face; returns are in **bp**.

### Identifiers / shared
| Column | Meaning |
|---|---|
| `date` | Business day. The row's return is for *prior business day → this day*. |
| `TIPS_cusip`, `UST_cusip` | The on-the-run bond each leg tracks that day (rolls on the 1st business day of each month). |
| `gc_repo` | GC repo financing rate that day, % (DTCC GCF Treasury `GCFRTSY`; before 2009 uses overnight GC / fed funds). |

### Per leg — prefix `TIPS_` (real) and `UST_` (nominal)
| Column | Meaning |
|---|---|
| `*_clean` | Quoted clean price (real for TIPS), per 100 — Bloomberg. |
| `*_yield` | Yield to maturity, % (real for TIPS, nominal for UST) — Bloomberg. |
| `*_accrued` | Accrued interest per 100 (computed, actual/actual). |
| `TIPS_IR` | Index ratio = reference CPI / base CPI (inflation uplift). `UST_IR` = 1. |
| `*_dirty_real` / `UST_dirty` | `clean + accrued`. |
| `*_V` | **Cash value** = `dirty × IR` (the actual $ exposure per 100 face; for UST, = dirty). |
| `*_DV01` | $ change in `V` per 1 bp yield move, per 100 face (our calc; real-yield DV01 × IR for TIPS). |
| `*_denom` | The DV01 used as the return denominator — fixed at the **monthly 100k-DV01 rebalance** (constant within the month). |
| `*_dV` | `V − V_prev`, same bond (never across a roll). |
| `*_coupon` | Coupon cash booked that day (TIPS = ½·coupon × IR; paid when a coupon date passes). |
| `*_days` | Calendar days since prior business day (for act/360 financing). |
| `*_financing` | `days/360 × gc_repo/100 × V_prev` (cost of financing the position). |
| `*_pnl` | `dV + coupon − financing` — daily $ P&L per 100 face. |
| `r_TIPS_bp`, `r_UST_bp` | Leg daily return in bp = `pnl / denom` (DV01-normalized to 100k per leg). |

### Output
| Column | Meaning |
|---|---|
| `r_BE_bp` | Breakeven daily return = `r_TIPS_bp − r_UST_bp` (long TIPS / short UST). |
| `cum_TIPS_bp`, `cum_UST_bp`, `cum_BE_bp` | Running **cumulative** (linear sum of daily bp — **not** compounded). |

### Replication chain (every input is in the row)
```
dirty   = clean + accrued
V       = dirty × IR                         (UST: IR = 1)
dV      = V − V_prev                          (same bond)
financing = days/360 × gc_repo/100 × V_prev
pnl     = dV + coupon − financing
bp      = pnl / denom                         (denom = monthly 100k-DV01)
r_BE_bp = r_TIPS_bp − r_UST_bp
cum_*   = Σ daily bp
```

### Conventions (read once)
- Returns are **DV01-normalized to 100k per leg**, rebalanced monthly — so a leg's daily bp ≈ **−(change in yield) + carry**.
- Financing is **mid GC only**. Repo **bid/offer and specialness are NOT included** here (model them with the ±x knob in the dashboard).
- Cumulative columns are a **linear sum** of daily bp (not compounded). Convexity ignored (desk-approved).
- Blank TIPS cells in the earliest rows = that tenor's TIPS didn't exist yet (UST-only); breakeven starts once both legs trade.
