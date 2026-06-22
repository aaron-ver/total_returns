# Financed (Levered) Total Return for TIPS & Breakevens — Reference

**Project:** construct daily financed total-return series for on-the-run US TIPS at 5y / 10y / 30y, plus a parallel breakeven total-return series.
**Source grounding:** Barclays, *Global Inflation-Linked Products: A User's Guide* (Pond, Hill, Shelepko; 12 June 2024). Page citations refer to that document.
**Scope note:** This is a build spec + mechanics reference. It is deliberately written in dollar/cashflow terms first, with yield framings as secondary repackagings.

---

## 0. The whole pipeline in one picture

For each reference bond, on each date `t`, the object you are building is:

```
Financed daily P&L(t) = ΔV(t) + coupon_cash(t) − repo_interest(t)
```

where `V = (dirty real price) × (index ratio)` is the cash value, and the three terms are, respectively, the marked-to-market value change, any coupon received, and the cost of the borrowed cash. Everything below is either (a) how to compute each term, (b) what data each term needs, or (c) how to chain the daily numbers into a continuous series across coupons and on-the-run rolls.

The deterministic part of that daily P&L (the part knowable if real yields don't move) is **carry-and-roll**; the random part is the real-yield surprise.

---

## 1. Notation & conventions

| Symbol | Meaning |
|---|---|
| `CPI_m` | CPI-U NSA index level for reference month `m` (as first published; **not** revised) |
| `DRI_{d,m}` | Daily reference index for settlement day `d` in month `m` |
| `DRI_base` | Reference index on the bond's dated/issue date |
| `IR_{d,m}` | Index ratio = `DRI / DRI_base` |
| `C` | Annual **real** coupon rate (e.g., 2.375%); semiannual payment is `C/2` per 100 face |
| `P_clean` | Quoted ("screen") clean **real** price, per 100, inflation-unadjusted |
| `AC` | Accrued **real** coupon, per 100 |
| `P_dirty` | Dirty real price = `P_clean + AC` |
| `V` | Cash value (full settlement value) = `IR × P_dirty` |
| `RY` | Real yield to maturity |
| `NY` | Nominal yield to maturity (comparator) |
| `BE` | Breakeven inflation |
| `w` | Fraction of current coupon period remaining = days(settle→next coupon)/days(period) |
| `N` | Number of remaining coupons |

**Conventions to lock down (these are silent error sources):**
- Index ratio: **truncate to 6 decimals, then round to 5** (p. 13).
- Repo accrues **act/360** on the **full cash invoice `V`** you actually borrowed — not the clean price, not par.
- Coupon accrual day-count for US TIPS follows the nominal Treasury convention (actual/actual within period).
- CPI used for indexation is the **first print**, never the revised figure.

**Units of the output (state this once, loudly):** `V`, `ΔV`, coupon cash, and repo interest are all **nominal dollars** — the IR multiplier converts the real price into nominal cash, so the entire financed-return series is a **nominal total return**. The real/nominal split matters only *inside* the pricing engine (the DCF discounts real cashflows at the real yield, §4.1) and when *attributing* what drove the nominal return (accretion vs real-yield move). Real engine, nominal P&L — don't let engine units leak into reporting.

**Data continuity (no extrapolation):** each individual CUSIP trades **every business day** once issued, so within a bond's life there are no price gaps. "On-the-run 10y" is a *role* that relays from one CUSIP to the next at each auction; you never extrapolate a price — you **splice returns** across the relay (§9.2).

---

## 2. TIPS cash-flow mechanics (Topic 1) — *primer pp. 11–17*

### 2.1 Daily reference index (the indexation lag)
```
DRI_{d,m} = CPI_{m−3} + ((d − 1) / D_m) × (CPI_{m−2} − CPI_{m−3})
```
- `D_m` = number of days in settlement month `m`.
- On day 1 of month `m`, `DRI = CPI_{m−3}` (the "3-month lag" anchor); by month-end it interpolates to `CPI_{m−2}`.
- **Why lagged & deterministic:** CPI isn't available in real time, and cash amounts must be knowable in advance. Consequence you exploit: near-term accretion is *known*, not forecast.

### 2.2 Index ratio
```
IR_{d,m} = DRI_{d,m} / DRI_base        (truncate 6 → round 5)
```
- New CUSIP: `IR = 1.00000` at issue. **Reopenings inherit the original dated date**, so an on-the-run can already have `IR > 1`.

### 2.3 Coupon (paid on inflation-adjusted principal)
```
Dollar coupon (per 100 face) = (C / 2) × IR_{coupon date}
```
Real value of that coupon, deflated by the same IR, is constant `C/2` — the invariance that defines the instrument.

### 2.4 Full (cash) settlement price
```
V = IR × (P_clean + AC) = IR × P_dirty
AC = (C / 2) × (days since last coupon / days in coupon period) = (C/2)(1 − w)
```
The screen price is **real**; the IR multiplier converts to the dollars that actually wire.

### 2.5 Deflation floor (an embedded long option)
```
Principal repaid at maturity = max( 100 , 100 × IR_maturity )
       = 100 × IR_maturity + max(0, 100 − 100 × IR_maturity)
```
- Floors **principal at maturity only**, *not* coupons (except Australia) (p. 16).
- Measured **from the base date** (cumulative path), not from today.
- ⇒ near-par / newly issued bonds carry an at-the-money floor (worth more); deeply seasoned bonds' floor is far out-of-the-money. Relevant when comparing breakevens across bonds with very different IRs.

---

## 3. Real vs nominal yields & breakevens (Topic 2) — *pp. 15–16, 88–93*

### 3.1 Breakeven identity
```
1 + NY = (1 + RY) × (1 + BE)
BE = (1 + NY)/(1 + RY) − 1          ← use this (exact)
BE ≈ NY − RY                        ← additive approximation, low-rate only
```
Use the multiplicative form everywhere; the additive one drifts a few bp at current yield levels.

### 3.2 Fisher decomposition / risk premium
```
1 + NY = (1 + RY) × (1 + E[π]) × (1 + ρ)
⇒  BE ≈ E[π] + ρ
```
`ρ` = liquidity-adjusted inflation risk premium = tug-of-war between:
- **Inflation risk premium** (nominal holders demand compensation) → pushes BE **above** E[π];
- **Liquidity premium** (TIPS cheap/illiquid → higher RY) → pushes BE **below** E[π].

Net can be either sign; in stress (e.g., 2008) the liquidity effect dominates and BE understates expected inflation. **You do not need to decompose `ρ` to build a TR series** — you mark observed BE; the split is a fair-value/research question (pp. 42, 88, 94).

### 3.3 Isolating the inflation bet (long TIPS / short nominal)
- Equal notionals do **not** isolate inflation; you must be **real-rate / DV01-neutral**.
- Hedge ratio `h = DV01_TIPS / DV01_nominal` (notional of nominal per unit TIPS).
- TIPS DV01 is sensitivity to **real** yield; nominal DV01 to **nominal** yield — different variables, so matching assumes a real↔nominal co-move.
- TIPS usually has the **longer** duration (lower real coupon + lower yield), reinforced by the IR multiplier ⇒ **`h < 1`** typically (hold *less* nominal notional than TIPS... see §9.3 for the exact direction in your build).
- **Beta-adjust:** real yields are less volatile than nominal; regress `ΔRY` on `ΔNY`, scale `h` by `β` (often `< 1`) (p. 82). Your chosen hedge ratio *defines* the breakeven series.

---

## 4. Pricing engine: real DCF ↔ price ↔ yield (Topic 3 support)

### 4.1 Dirty real price from real yield (the fundamental object)
```
P_dirty(RY) = Σ_{k=0}^{N−1} (C/2) / (1 + RY/2)^(k + w)  +  100 / (1 + RY/2)^((N−1) + w)
w = days(settle → next coupon) / days(coupon period)
```
- Cashflows are **real**; discount rate is the **real** yield; **inflation appears nowhere** in this sum.
- `w` and `N` re-index every day → this is the passage of time inside the price, and the source of roll-down.

### 4.2 Clean ↔ dirty (a quoting convention, not valuation)
```
P_clean (screen) = P_dirty − AC
```
- Clean strips the predictable within-period coupon sawtooth so a price *change* reflects a *yield* change (comparability across coupon-cycle position).
- **You always settle dirty × IR; clean never changes hands.**

### 4.3 Yield from price
Solve §4.1 for `RY` by iteration. To go from a screen quote to cash: `clean → +AC → dirty → ×IR → cash invoice`.

---

## 5. Total-return decomposition (Topic 3) — *pp. 19–21*

### 5.1 The organizing identity
```
V = P_dirty × IR
ΔV = IR_t·ΔP_dirty  +  P_dirty_t·ΔIR  +  ΔIR·ΔP_dirty
       └─ price return ┘   └─ accretion ┘   └─ cross term ┘
```
- **`IR·ΔP_dirty`** — real-yield-driven price return; contains coupon accrual (because `P_dirty` is dirty) **and** roll-down.
- **`P_dirty·ΔIR`** — inflation accretion (near-term: *known*).
- **`ΔIR·ΔP_dirty`** — small cross term; don't silently drop in a precise series.

### 5.2 Daily financed P&L
```
Daily financed P&L = ΔV + coupon_cash − repo_interest
repo_interest = (days/360) × repo_rate × V_invoice
```

### 5.3 Price-return sub-decomposition
```
ΔP_dirty ≈ roll_down(static curve)  −  ModDur_real × ΔRY  +  ½·Conv·(ΔRY)²
```
- Roll-down = price change from aging one step along the **unchanged real curve** (compute via a point ~1y shorter on the same curve, scaled to horizon; p. 7171+ shows the ASW analog).
- Roll **must** be computed on the **real** curve for TIPS; on **both** curves for breakeven.

### 5.4 Why unrealized MTM is P&L today
Economic wealth = liquidation value now, not realized cash. A flat-price day is **not** a zero-P&L day (accretion accrues, repo is charged). The series must be **daily MTM + carry**, never realized-cash-only (which would be flat between coupons then jump). Risk and financing both run off the daily mark.

---

## 6. Carry & roll (Topic 4) — *pp. 19–22*

### 6.1 Definitions
```
Carry (dollars)  = inflation_accretion + coupon_accrual − repo_cost      ← primary, use this
Carry (yield)    = forward_real_yield − spot_real_yield                  ← repackaging
Carry & roll     = (accretion + coupon + roll) − financing = return if the real curve is static
```
The **forward real yield is a breakeven threshold, not a forecast**. Positive carry ⇒ forward yield sits *above* spot by the size of the cushion ⇒ the real yield can rise that many bp before you merely cover financing.

### 6.2 No-arbitrage forward (carry) price
> **Not needed for the core series** under the "old OTR through the auction month" roll convention (§9.2): every observation is **spot-settled**, so you never price to a future settle date. Keep this only if you later add forward-carry/relative-value analytics on top of the series.
```
Forward cash price = V_spot × (1 + repo × days/360)  −  reinvested coupons
Forward real yield : divide forward cash price by IR_forward, strip forward AC, solve DCF.
```
*Worked (TIIOct28, pp. 19–21):* `V=104.00`, repo 5.32% × 26/360 ⇒ cost `0.40`, forward cash `104.40`; forward RY `1.78%` vs spot `1.73%` ⇒ **carry = +5bp** (driven by Jan CPI +0.54% m/m accreting in March).

### 6.3 Certainty horizon (Topic 1 ⇒ carry)
- Carry is **exact only as far as published CPI allows** (p. 21). On a date whose latest print is month `m−2`, exact carry runs to the **end of month `m`** (then the next print extends it a month).
- Beyond the horizon: project accretion with a **seasonal** CPI path (economist forecast) or lift the **inflation-swap fixings curve**. Method is unchanged; only certainty drops. Flag these dates as model-dependent.

### 6.4 TIPS carry vs nominal carry
- Nominal carry ≈ coupon − repo (smooth).
- TIPS carry adds **inflation accretion**, which is **large and lumpy** because CPI is **seasonal** and enters with the **~2-month lag** (p. 19). US: firmer H1, softer H2. A soft accruing print can make real-yield carry **negative** (real yields must *fall* just to cover repo).
- **Never annualize a single month's TIPS carry**; never compare months without seasonal adjustment.

### 6.5 Coupon inside the horizon (reinvestment, Exhibit 1, p. 22)
When a coupon pays mid-horizon: reinvest it into the bond at that day's forward price ⇒ **notional grows**. Apply consistently or the series shows a fake drop on every coupon date.

---

## 7. Repo & financing (Topic 5) — *plumbing = general; TIPS specifics pp. 148*

### 7.1 Repo = collateralized borrowing (one trade, two names)
- **Repo (your financed long):** sell bond now / buy back later → you receive cash, pay repo interest, **keep all bond economics** (coupon, accretion, price).
- **Reverse repo (counterparty's view / your short):** lend cash, take bond as collateral, earn repo interest.

### 7.2 Shorting (the breakeven nominal leg)
To short, **borrow the bond via reverse repo** (lend cash, receive bond), then sell it. While short you **owe a manufactured (substitute) coupon to the lender you borrowed from** (the third-party buyer collects the issuer's coupon). Repo interest and the coupon are **separate buckets**; the coupon does not change the repo rate or amount.

### 7.3 GC vs special
- **General collateral (GC):** lender indifferent to which bond ⇒ rate ≈ money-market rate. **SOFR literally is the GC Treasury repo rate.**
- **Special:** a specific bond in heavy demand to borrow ⇒ its repo rate trades **below** GC (lenders accept less interest to get it).
- **Self-correcting:** crowding a short *creates* specialness, which collapses the repo you earn and erases the edge.

### 7.4 Why specialness moves realized return (signs)
| Position | If the bond goes special | Effect on financed return |
|---|---|---|
| **Long** the bond | finance it cheaply (pay below-GC repo) | **UP** (tailwind) |
| **Short** the bond | must accept below-GC repo on your cash to borrow it | **DOWN** (headwind) |

For breakevens: you are **short the nominal comparator**, so a **special nominal** is a drag — the repo you *earn* on the short leg collapses below the repo you *pay* on the TIPS leg. The TIPS↔nominal **repo differential** (p. 148): TIPS can finance as GC, so the differential is largely **nominal-leg scarcity**; it blew out in **2008**; post-2020 (Fed standing repo facility) modest but "still not zero." Also present: **haircuts** and **balance-sheet charges** (second-order for a clean series, but real).

---

## 8. Financed / excess return & the TRS check (Topic 6) — *pp. 163–164*

### 8.1 The object
```
Financed (excess) return = total return − financing cost
```
This is the right object because it's the money you keep, it's leverage-neutral/comparable, and it isolates the **view** from the **funding**.

### 8.2 TRS as a sanity check (NOT a build target)
A total-return swap receiver gets the asset's total return and pays `FFOIS ± spread` (the financing leg). So a TRS = repo-financed long in swap form; its payoff mirrors your financed return **minus the dealer spread**, and it **resets notional discretely** (your series marks daily). Use the worked example to validate, do not implement.
*Example (p. 164):* TR index 100→102 = +2.0% over Q; financing 5.58% × 3/12 = 1.395% ⇒ **financed ≈ +0.605%**. Notional then **resets 100→102** (compounding).

---

## 9. Building the series — step by step

### 9.1 Per-bond financed TR (each on-the-run 5y / 10y / 30y)
For each date `t`:
1. Build `DRI(t)` from published CPI (or seasonal projection past the certainty horizon); compute `IR(t)` (truncate6→round5).
2. Get quoted clean real price (or solve from real yield); compute `AC(t)`, `P_dirty(t)`, `V(t) = IR(t)·P_dirty(t)`.
3. `ΔV = IR·ΔP_dirty + P_dirty·ΔIR + ΔIR·ΔP_dirty`.
4. Add `coupon_cash(t)` on payment dates (apply reinvestment → notional grows).
5. Subtract `repo_interest(t) = (Δdays/360)·repo_rate(t)·V`. Use the **bond-specific** repo (GC or special), not a flat rate.
6. `daily financed return = (ΔV + coupon_cash − repo_interest) / V(t−1)`.
7. Chain (compound) into the index.

### 9.2 On-the-run rolling & splicing (flag — top failure mode)

**Vocabulary (plain):**
- **Auction** = day Treasury *sells* a new bond. **Issue settlement** = a few business days later, when the bond actually exists / cash moves.
- **On-the-run (OTR)** = the *newest* bond at a tenor; it's a **job title**, not a fixed bond — the title passes to the new issue each auction. **Off-the-run** = a bond that has been replaced.
- **Roll** = the moment your series **switches which bond it tracks** (old OTR → new OTR).
- **Spot settlement** = priced for normal immediate settlement using today's data (no projection). **Forward settlement** = priced to a *future* settle date, which requires projecting carry/financing forward.

**Roll convention adopted for this project (per desk instruction): "old OTR through the auction month."**
- A new bond auctioned on, say, Jan 23 **does not** become the reference immediately.
- The series **keeps tracking the OLD on-the-run for the rest of January** (the just-replaced bond — technically off-the-run for ~a week, used deliberately).
- **Roll on the post-auction month boundary** (default: 1st business day of the following month, e.g., Feb 1), by which point the new bond has settled and trades **spot**.

**Why this convention (it removes real complexity):**
- Every price used is a normal **spot** price → **no forward-settlement machinery** needed for the core series (§6.2 becomes optional, analytics-only).
- The roll date is a **fixed calendar boundary**, identical in logic across 5y/10y/30y, instead of an irregular per-tenor auction/settle date.
- The auction sits **inside a continuous stretch** of the series, so downstream **moving-window analysis** (e.g., "Jan auction date → end of Jan", "Feb 1 → 30y auction") never straddles a splice seam.

**The splice mechanic (unchanged and non-negotiable):** at the roll, **stitch RETURNS, not price levels.** Run the old bond's daily financed return up to the roll date; from the roll date, switch the reference to the new bond and continue with its returns; chain the cumulative index across the seam. Levels may jump between bonds (different coupon/IR/price) — that's fine; the *return* is continuous because you only ever multiply in daily returns. Splicing **levels** injects a fake jump on every roll. Apply the identical rule across all three tenors.

**Roll trigger (decided): the 1st business day of the month *following* the auction month.** Both legs roll on this single monthly clock (see §9.2.1). This keeps every observation spot-settled and matches the calendar-window framing for downstream analysis.

#### 9.2.1 Both legs on one monthly clock (TIPS and nominal auction on *different* calendars)

The subtlety: **TIPS and nominal Treasuries are auctioned on unrelated schedules**, so their on-the-run identities change on different dates. If each leg rolled on its *own* auction, the two legs would hop to new bonds on mismatched dates all month, scattering roll seams everywhere and occasionally forcing you onto a just-auctioned bond that hasn't spot-settled.

**Rule:** force *both* legs onto the **same monthly clock** — fix the TIPS↔nominal pairing at each month start and **only ever roll, both legs together, on the 1st business day of the month.** Ignore any intra-month auction (TIPS *or* nominal) until the next month boundary. Consequences: both legs always roll on the same date (no desync seam); you never touch an unsettled new issue (no forward settlement, ever); the pairing is constant within a month and reconsidered only at month starts.

**Trade-off (accepted):** the paired TIPS and nominal maturities won't be perfectly matched (often a 1–3 month gap), and for part of a month you may sit on a nominal that's about to be replaced. Both are immaterial against a multi-year tenor and are absorbed by the DV01 weighting — the simplicity (always spot, always synced) is worth it.

**Worked walkthrough (desk example):**
- **Jan 2026:** new TIPS Jan-2036 is auctioned. Through the **last business day of January**, the breakeven series uses the *previous* on-the-runs: **TIPS Jul-2035 vs UST Aug-2035**.
- **Feb 1 (1st b-day):** roll the **TIPS leg** to **Jan-2036**. The nominal also rolls on this monthly clock — the Feb pairing is **TIPS Jan-2036 vs UST Nov-2035** (UST Feb-2036 has not been issued yet, so the current on-the-run nominal is Nov-2035).
- **Mid-February:** UST Feb-2036 is auctioned. **Do NOT switch to it now.** Keep **UST Nov-2035** as the comparator for the rest of February.
- **Mar 1 (1st b-day):** reconsider both pairings at the month boundary and roll as needed.

### 9.3 Breakeven financed TR
1. Long TIPS leg: financed return per §9.1 (**repo** — pay; keep coupon + accretion + price).
2. Short nominal leg: borrow the nominal via **reverse repo** (lend cash, sell the bond). As a short, every sign flips vs a long, and there is **no IR / no accretion** (`V_nominal = P_nominal_dirty`):
   ```
   nominal leg P&L (as short) = −ΔP_nominal_dirty − coupon_owed + repo_earned
   ```
3. Weight to **DV01/real-rate neutral**; optionally **beta-adjust** (§3.3). The hedge ratio defines the series — document it.
4. `r_BE = r_TIPS_financed − h·r_nominal_financed` (compute each leg *as if long*; the minus sign encodes the short).
5. **Net financing = repo_paid(TIPS) − repo_earned(nominal)**; ≈ 0 if both at GC, becomes a drag when the **nominal goes special**.
6. **Roll both legs together on the single monthly clock (§9.2.1)** — same 1st-business-day boundary, intra-month auctions ignored, pairing fixed within the month.

### 9.4 Where each ingredient lands (self-check map)
- **Inflation accretion:** (a) daily `P_dirty·ΔIR`; (b) inside carry. (Same quantity, two horizons.)
- **Financing differential:** one place — net of (repo paid on long TIPS) − (repo earned on short nominal), §9.3 step 5.

---

## 10. Data inputs you must source

| Input | Used in | Notes |
|---|---|---|
| CPI-U NSA history (as-first-published) | DRI / IR / accretion | never revised values |
| Seasonal CPI projection **or** inflation-swap fixings | carry past certainty horizon | §6.3 |
| Bond static: CUSIP, dated date, `DRI_base`, real coupon, maturity, coupon dates | everything | reopenings inherit original dated date |
| OTR schedule (which CUSIP is on-the-run when) per 5y/10y/30y | splicing | §9.2 |
| Daily quoted clean real prices / real yields (each OTR) | `P_dirty`, ΔP | |
| Nominal comparator daily prices / yields | breakeven leg | choose comparator deliberately (p. 16) |
| Repo rates per bond, both legs (GC + specials) | financing | bond-specific, time-varying |
| Real & nominal DV01s; real↔nominal beta | hedge ratio | §3.3 |

---

## 11. Common-mistake checklist (the traps that actually break series)

- [ ] **Indexation/lag:** wrong reference months, or treating "today's CPI" as today's accretion (it's CPI from ~2–3m ago).
- [ ] **IR rounding:** not truncating-6-then-rounding-5 (small but systematic, compounds).
- [ ] **Real vs nominal mixing:** marking on `P_clean` instead of `V = IR·P_dirty`; bumping price *and* IR for the same inflation (double-count) — the #1 homemade-series bug.
- [ ] **Coupon-date double-count / reinvestment:** dirty price drops by the coupon; book the cash *or* the price, not both; grow notional on reinvest.
- [ ] **Financing base/daycount:** repo on `V` (the cash borrowed), act/360 — not clean, not par.
- [ ] **Flat GC when special:** biases financed return; worst at on-the-run points.
- [ ] **Roll/splice errors:** stitching levels not returns; rolling *at* the auction (forces forward settlement) instead of at the post-auction **month boundary**; rolling the two breakeven legs on different dates.
- [ ] **Carry seasonality:** annualizing one month; comparing months without seasonal adjustment; treating projected carry as known.
- [ ] **Roll on wrong curve:** TIPS roll on real curve; breakeven roll on both.
- [ ] **Breakeven hedge:** equal notionals (residual real-rate risk); ignoring beta; getting the repo signs backwards (pay on long, earn on short).
- [ ] **Deflation floor:** assuming it protects coupons, or measuring from current IR instead of base.
- [ ] **Compounding/notional convention:** index (compound) vs position P&L (fixed) — pick one, be consistent.

---

## 12. Load-bearing page map (≈45 pages of ~429)

| Topic | Primary section(s) | Pages |
|---|---|---|
| 1. TIPS mechanics (IR, ref CPI, lag, coupons, floor) | Linker cash flows and yields | **11–17** |
| 2. Real/nominal & breakevens; risk premium | Linker cash flows & yields; Real yield & breakeven carry; The elusive inflation risk premium | 9–18, **19–25**, 88–93 |
| 3. Total-return decomposition | Real yield & breakeven carry; Total return swaps | **19–25**, 163–166 |
| 4. Carry & roll | Real yield & breakeven carry; How to construct forwards | **19–25**, 77–81 |
| 5. Repo & financing | Real yield & breakeven carry; Linker asset swaps; Total return swaps | 19–25, 135–148, 163–166 |
| 6. Financed/levered total return | Real yield & breakeven carry; Total return swaps | 19–25, **163–166** |
| (Secondary) Seasonality of accretion/carry | Seasonality: Estimation and adjustment | 26–36 |
| (Secondary) Hedge ratios / beta | Beta calculations | 82 |

---

## Appendix A — End-to-end worked example (TIIOct28, primer pp. 19–21)

| Step | Value |
|---|---|
| Bond | TII 2.375% 15-Oct-2028 (on-the-run 5y, Mar 2024) |
| Trade / settle | 5 Mar 2024 / 6 Mar 2024 |
| Quoted clean (real) | 102.82813 |
| AC (143/183 × 1.1875) | 0.93 |
| `DRI_base` / `DRI(6-Mar)` | 306.29390 / 307.01552 |
| `IR(6-Mar)` | 1.00236 |
| Cash invoice `V` = IR×(clean+AC) | **104.00** |
| Spot real yield | 1.73% |
| Repo (SOFR-based) | 5.32% |
| Horizon | 26 days → fwd settle 1 Apr 2024 |
| Repo cost = 26/360 × 5.32% × 104.00 | 0.40 |
| Forward cash price = 104.00 + 0.40 | **104.40** |
| `IR(1-Apr)` = 308.41700/306.29390 | 1.006934 |
| Fwd inflation-unadjusted dirty = 104.40 / IR | 103.68 |
| Fwd clean (screen) | 102.59 |
| Forward real yield (solved) | 1.78% |
| **Carry = fwd RY − spot RY** | **+5 bp** |

Reading: known accretion (Jan CPI +0.54% m/m) + coupon accrual exceed the 0.40 repo by enough that real yields could rise 5bp before merely covering financing. If RY on 1-Apr prints below 1.78% you profit vs financing; above, you lose.

---

## Appendix B — One-line glossary of the arc

Index ratio scales a real bond into cash → real and nominal yields differ by a breakeven (= expected inflation + premium) → daily total return = price + coupon + accretion + roll − financing, marked daily → the static-curve part is carry-and-roll, lumpy for TIPS because accretion is seasonal and lagged → financing comes from repo, bond-specific via specialness → total return − financing = the financed return you are building.