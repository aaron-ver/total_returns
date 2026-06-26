# European & UK Inflation-Linked Bonds — Build Reference (extension to TIPS engine)

**Project:** extend the financed total-return engine from US TIPS to **Italian, French, Spanish, German and UK** inflation-linked government bonds (linkers).
**Phase 1 scope (this build):** *outright* per-bond financed total return — one daily return series per linker, each on its own export sheet. **No breakeven leg yet, no gas hedge, no visualizer** (deferred per desk). The breakeven nominal-leg choice (own-country nominal vs Bund/swap) and the analysis-tool organization are explicitly **TBD** — see §8.
**Source grounding:** each DMO's official methodology (AFT, MEF/Tesoro Italia, Tesoro Público, Deutsche Finanzagentur, UK DMO) + Eurostat HICP rules; the US mechanics reference is [reference.MD](reference.MD). Where this file and `reference.MD` agree, the US doc is the deeper mechanics backstop; where they differ, **this file governs the non-US bonds.**

> **Read first — what carries over and what does not.** The US TIPS engine's *spine* ports directly: daily financed P&L = `ΔV + coupon_cash − repo`, `V = real_dirty × index_ratio`, DV01-normalized bp, linear accumulation, return-splicing across rolls, settlement-date marking. The pricing DCF (`pricing.py`) is already convention-parameterized (coupon `freq`, act/act ICMA) and is **reused as-is**. What changes per market is the *periphery*: which inflation index, the indexation lag, coupon **frequency**, the settlement **calendar** and lag, the deflation **floor**, the **quote** convention, and — the big one — that these markets have **no neat 5y/10y/30y auction buckets and a far messier, partly-syndicated issuance calendar**. So we drop the US "tenor role + monthly OTR roll" abstraction and instead track **each bond as its own fixed instrument for its whole life** (no roll/splice within a bond — splicing only ever mattered for the US OTR *role*).

---

## 0. The one-bond object (same shape as US, per reference.MD §0)

For every linker, on each date `t`:

```
Financed daily P&L(t) = ΔV(t) + coupon_cash(t) − repo_interest(t)
V = real_dirty_price × index_ratio          (UK old-style 8m-lag is the exception — §5)
bp(t) = (ΔV + coupon_cash − repo_interest) / DV01_denom      (per 100 face)
```

`DV01_denom` = the bond's own DV01 per 100 face, fixed at each calendar-month start (the 100k-DV01 rebalance epoch), exactly as in the US engine. Cumulative = linear `cumsum` of daily bp. **No reinvestment, notional does not grow, GC-mid financing** — all the US "as-built deltas" (reference.MD top) carry over unchanged. The only structural simplification: **there is no on-the-run roll inside a single-bond series**, so the engine walks one CUSIP/ISIN end-to-end. (The *universe* of which bonds exist and when they were issued/syndicated is still gathered — it is the input to any future bucketing/curve analysis the desk designs.)

---

## 1. Per-market convention matrix

| | **US TIPS** (baseline) | **FR OAT€i** | **FR OATi** | **IT BTP€i** | **ES Bonos/Oblig€i** | **DE Bund€i / Bobl€i** | **UK gilt (new-style)** | **UK gilt (old-style)** |
|---|---|---|---|---|---|---|---|---|
| Inflation index | US CPI-U NSA | **Euro HICP ex-tobacco** | **French CPI ex-tobacco** | **Euro HICP ex-tobacco** | **Euro HICP ex-tobacco** | **Euro HICP ex-tobacco** | **UK RPI** | **UK RPI** |
| Index source | BLS | Eurostat (unrevised) | INSEE (unrevised) | Eurostat (unrevised) | Eurostat (unrevised) | Eurostat (unrevised) | ONS | ONS |
| Indexation lag | 3-month, daily interp | 3-month, daily interp | 3-month, daily interp | 3-month, daily interp | 3-month, daily interp | 3-month, daily interp | **3-month**, daily interp | **8-month**, step (monthly) |
| Coupon frequency | **semiannual** | **annual** | **annual** | **semiannual** | **annual** | **annual** | **semiannual** | **semiannual** |
| Day count (accrual) | act/act ICMA | act/act ICMA | act/act ICMA | act/act ICMA | act/act ICMA | act/act ICMA | act/act ICMA | act/act (legacy) |
| Settlement | T+1 | **T+2** | **T+2** | **T+2** | **T+2** | **T+2** | **T+1** | **T+1** |
| Calendar | US bond mkt | **TARGET2** | **TARGET2** | **TARGET2** | **TARGET2** | **TARGET2** | **UK (gilt)** | **UK (gilt)** |
| Deflation floor (principal) | **yes** (par) | **yes** | **yes** | **yes** | **yes** | **yes** | **no** (can redeem <par) | **no** |
| Screen quote | real | real | real | real | real | real | real (per 100 real) | **nominal/cash** (≈8m-lag) |
| Index-ratio rounding | trunc 6 → round 5 | round 5 (verify) | round 5 (verify) | round 5 (verify) | round 5 (verify) | round 5 (verify) | round 5 | n/a (uses RPI ratio) |
| Maturity-month anchor | Jan/Apr/Jul/Oct + Feb 30y | ~**25 Jul** (also Mar) | ~**25 Jul** | ~**15 Sep** (also 15 May) | ~**30 Nov** | ~**15 Apr** | **22 Nov / 22 Mar / 10 Aug** etc. | mixed |
| Status | active | active | active | active | active | **issuance ended 2024** | active | **~3 left, runoff** |

Notes that bite:

- **Coupon frequency drives daily accrual** and the number of remaining cashflows `N` in the DCF — `freq=1` for FR/DE/ES, `freq=2` for IT BTP€i, UK, US. `pricing.py` already takes `freq`; we just pass the right one per bond.
- **Settlement T+2 (euro)** changes the marking date vs the US T+1: `V`, accrued and the index ratio are valued to the **T+2** business day on **TARGET2**, and the financing day-count `d = settle(t) − settle(t−1)` is computed on that calendar. UK new-style is T+1 on the **gilt** calendar. (This is the analogue of the US `_next_bday`/`trading_calendar`, generalized per market.)
- **Index ratio / IC formula is the SAME interpolation as US** for every 3-month-lag bond:
  `DRI_d = Index_{m−3} + (d−1)/D_m · (Index_{m−2} − Index_{m−3})`, `IR = DRI_settle / DRI_base`. So `data_layer.index_ratio()` is reused; only the *index series* and the *base* differ. **Base = the daily reference index on the bond's (first) dated date** — reopenings inherit the original base, same as US.
- **Rebasing of the reference index is a real data trap (§4).** Euro HICP-xt and French CPI-xt both got new reference years with the **Jan-2026 print published 25-Feb-2026**. The DMO rule: use *"the first index published, irrespective of later revisions"* and splice base changes *"so as not to alter the normal trajectory of the indexation coefficient."* A raw Bloomberg level series is in the **current** base, so reconstructing the IR from it across a rebase is wrong unless chained. Handling in §4.

---

## 2. Multiple instruments inside one country — keep them separate

- **France: two curves.** `OATi` (domestic **French** CPI ex-tobacco) and `OAT€i` (**euro** HICP ex-tobacco). Different inflation bases → never pool. Tag each bond with its index.
- **Italy: two instruments.** `BTP€i` (euro HICPx, institutional, standard linker — **in scope**) and `BTP Italia` (domestic **FOI ex-tobacco**, retail). **BTP Italia is structurally different and is DEFERRED** (§7): it pays inflation accretion semiannually "pay-go," **resets principal to par every 6 months**, and carries a path-dependent floor — its P&L engine is not the `V = real_dirty × IR` template.
- **UK: two designs.** **New-style 3-month-lag** gilts (post-2005) are standard linkers — **in scope**. **Old-style 8-month-lag** gilts (~3 left) trade in cash/nominal space with no daily index ratio — **DEFERRED** (§5/§7), catalogued but not run.

---

## 3. Index-ratio computation (3-month-lag bonds) — the reusable core

Identical to reference.MD §2.1–2.2, with the country's reference index and base:

```
DRI_{d,m} = RefIndex_{m−3} + ((d − 1)/D_m) · (RefIndex_{m−2} − RefIndex_{m−3})
IR_{d,m}  = DRI_{d,m} / DRI_base            (round to 5 dp; US truncates-6-then-rounds-5)
```

- `RefIndex` = unrevised **euro HICP ex-tobacco** (FR OAT€i / IT BTP€i / ES / DE), **French CPI ex-tobacco** (FR OATi), or **UK RPI** (UK new-style).
- `DRI_base` = `DRI` on the bond's **dated date at first issue** (Bloomberg `BASE_CPI` / `REFERENCE_INDEX`; or compute from the index). Reopenings inherit it.
- `V = IR × (clean_real + accrued_real)`; coupon cash on a pay date `= (C/freq) × IR(pay date)`.
- **Deflation floor** (all except UK): redemption principal `= max(100, 100 × IR_maturity)`. Only bites near/below par (low-coupon recent issues); record but it affects only the final redemption mark.

**Rounding caveat:** the euro DMOs publish the *coefficient d'indexation / coefficiente di indicizzazione* rounded to **5 decimals**; some docs imply a 6→5 step like the US. The difference is ≤1e-5 on the ratio — encode round-5, leave a knob, and validate the computed IR against Bloomberg's per-bond index ratio on a few dates per bond (the US build validated to 1e-6).

---

## 4. The reference-index data problem (this is where the "more cleaning" lives)

The US build *computes* the index ratio from `CPURNSA` and only pulls price+yield per bond (BBG's historical `IDX_RATIO` was unreliable). For the new markets we need the **reference index series** AND must survive **rebasings**. Plan:

1. **Pull the reference index series** for each index (euro HICPx, French CPIx, UK RPI) as a monthly level — but a single ticker is in the *latest* base, so:
2. **Also pull each bond's own daily index ratio / reference CPI from Bloomberg** (`INDEX_RATIO`, `REFERENCE_CPI`/`BASE_CPI`) as the **authoritative indexation** — these are computed by Bloomberg with the correct chained base and lag, so they sidestep the rebasing splice. (For euro/UK linkers BBG's index-ratio is generally reliable within the bond's life, unlike the TIPS historical quirk — **to be confirmed on the first pull**, same way the US fields were validated 2026-06-22.)
3. **Engine uses the rules-based IR (§3) where the reconstructed index is clean, and falls back to / cross-checks against BBG's per-bond IR.** Store both columns in the per-bond cache so any divergence is visible. Document which one fed each bond's returns.
4. **Rebasing handling for the rules path:** build a **chained** reference-index series — when Eurostat/INSEE rebases, rescale the pre-rebasing levels by the published linking factor so the ratio is continuous (the DMO "do not alter the trajectory" rule). Known events to handle: **euro HICP & French CPI rebasing effective with the Jan-2026 print (25-Feb-2026)**; older base changes (e.g. euro HICP 2005=100→2015=100 in 2016) for history.

**CPI/HICP publication timing** (matters later for any release-aligned analysis, not for the IR itself): French CPI prints mid-month *ahead* of the euro aggregate; euro HICP has a **flash then final** pattern. The IR uses the **final, first-published** number for month `m−3`/`m−2`, which is available well before it is needed (3-month lag), so near-term accretion is still *known*.

---

## 5. UK old-style (8-month lag) — why it is deferred

The remaining old-style gilts do **not** fit `V = real_dirty × IR`:
- 8-month lag, **monthly step** (no daily interpolation): the RPI for indexation is fixed 8 months before each cashflow, so each coupon is **known a full period ahead** and the bond effectively trades like a nominal bond between RPI fixings.
- Quoted/settled differently from new-style; there is **no continuous daily index ratio** to multiply a real price by.
They are **catalogued** (ISIN, coupon, maturity, the relevant RPI base months) so the universe is complete, but the return engine **skips** them in Phase 1. If any must be included, they get a dedicated `old_style` code path.

---

## 6. Financing & carry (Phase-1 default; the cross-country normalization is a desk decision)

- **Phase 1 finances each bond in its own local GC repo:** euro linkers at **€STR-based GC** (€STR / EUREPO / GC pooling proxy), gilts at **SONIA-based GC**. Local currency, **no FX leg**. This is the faithful single-bond financed return and avoids inventing cross-currency basis.
- **GC mid only, specialness out of scope** (same as US). Repo bid/offer stays a tunable half-spread `x` applied after the build. Auction/syndication-cycle **repo specialness** (larger and lumpier in the smaller euro markets) is *flagged* for the future supply study but **not modeled** now.
- **Carry decomposition** (nominal carry = coupon − repo; inflation carry = accretion) is country-specific because each inflation seasonal (US CPI / euro HICP / UK RPI) differs — relevant to any future seasonal/bucketing layer, not to the raw per-bond return.
- **DECISION DEFERRED (boss):** whether carry/return is reported in each bond's local funding or **normalized to one base currency** (which injects cross-currency basis as a P&L line). Phase 1 = local. Cross-currency normalization is a switch we add when the desk decides. See §8.

---

## 7. Deferred / special-handling instruments (catalogued, not run in Phase 1)

| Instrument | Why deferred | What we still gather |
|---|---|---|
| **Italy BTP Italia** | pay-go semiannual accretion, principal resets to par each 6m, path-dependent floor — different P&L engine | full static + issuance (retail placements), tagged `special=btp_italia` |
| **UK old-style (8m-lag) gilts** | cash/nominal space, monthly-step indexation, no daily IR | static + the 8m RPI base months, tagged `special=uk_8m` |
| **(any non-standard linker)** | structure ≠ `V = real_dirty × IR` | flagged for review |

---

## 8. Decisions the desk still owns (do not hard-code without sign-off)

1. **Breakeven nominal leg** — own-country nominal (isolates inflation; both legs carry that sovereign's credit/liquidity; for IT/ES embeds the BTP–Bund / Bono–Bund spread) **vs** Bund/swap (cleaner rates, injects sovereign basis). No clean answer like TIPS-vs-UST. *Phase 1 builds outright legs only; breakeven construction waits on this.*
2. **Funding normalization** — local funding per bond vs one base currency (§6).
3. **Analysis-tool organization** — the boss has not decided how to bucket/curve these (no neat 5y/10y/30y, partly-syndicated). Phase 1 just produces clean per-bond returns + the issuance calendar as raw material.
4. **Universe depth** — currently-outstanding only vs include matured bonds for history (Phase-1 default below).
5. **Structural regime breaks** to encode for any cross-year stacking: **Germany no new issuance from 2024** (auction-anchored framework N/A); **UK RPI→CPIH reform from 2030** (full alignment after the Jan-2031 print) — treat pre/post-2030 as a regime change, not continuous history.

---

## 9. Data inputs to source (the boss's "gather everything" list)

Per **bond** (static, from Bloomberg by ISIN):
`SECURITY_DES, ID_ISIN, CPN, CPN_FREQ, MATURITY, ISSUE_DT/FIRST_SETTLE_DT, DATED_DATE, FIRST_CPN_DT, BASE_CPI (DRI base), REFERENCE_INDEX (which index), INFLATION_LINKED_INDICATOR, FLOORED (deflation floor flag), AMT_OUTSTANDING, COUNTRY, CRNCY, DAY_CNT_DES, CALC_TYP`.

Per **bond** (daily, from Bloomberg by ISIN): `PX_CLEAN_MID, PX_DIRTY_MID, YLD_YTM_MID` (+ `INDEX_RATIO`, `REFERENCE_CPI` as the indexation cross-check; `RISK_MID`/`DUR_ADJ_MID` not relied on — DV01 computed in `pricing.py` for both legs on one basis).

**Reference index** series (monthly, chained across rebasings — §4): euro HICP ex-tobacco, French CPI ex-tobacco, UK RPI.

**Financing** series (daily): €STR-based GC (euro), SONIA-based GC (gilts). [US uses GCFRTSY.]

**Auction & syndication calendar** (the headline ask) — per bond / per tap: `auction/syndication date, settlement date, ISIN, coupon, maturity, method (auction vs syndication), amount allotted/issued, reopening flag`. Source = Bloomberg (per desk instruction "pull with bbg"); the national DMO sites are the authoritative cross-check (AFT, MEF, Tesoro, Finanzagentur, UK DMO). Unlike US TreasuryDirect there is no single free API — see `linkers.py` for the Bloomberg-driven collection and the DMO fallbacks.

**Calendars:** TARGET2 (euro settlement), UK gilt calendar (UK settlement) for business-day rolling and the month-end anchor.

---

## 10. The bond universe

The universe lives in `linkers.SEED_UNIVERSE` (the curated backbone), built to `cache_intl/universe.csv`
and CONFIRMED on the terminal via `linkers.enrich()` (Bloomberg static). Compiled from DMO sources
(AFT, MEF, Tesoro, Finanzagentur, UK DMO), cross-checked vs cbonds/Borsa Italiana/giltsyield, **as of
June 2026** — re-confirm coupon/maturity/base-CPI on the first pull.

**Count — 82 bonds (74 active + 8 deferred):**

| Country | Program | # active | Notes |
|---|---|---|---|
| France | OAT€i | 12 | maturities 2027–2053; anchor ~25 Jul but 2029 line is 1 Mar; one Green OAT€i (2038) |
| France | OATi | 5 | maturities 2028–2039; mostly 1 Mar; OATi 3.40 07/25/29 is the legacy line |
| Italy | BTP€i | 14 | 2026–2056; anchor 15 Sep (old) / 15 May (post-2016), one 15 Aug (2031) |
| Spain | Bonos/Oblig€i | 8 | **5 liquid benchmarks** (27/30/33/36/39) + **3 non-traded private placements** (2048/49/50, €430m each, held to maturity — flag/exclude from liquidity-sensitive work) |
| Germany | Bund€i | 3 | **0.5% 2030, 0.1% 2033, 0.1% 2046** — issuance ended 2024; the 0.1% 2026 matured 15-Apr-2026 |
| UK | UKTi new-style (3m) | 32 | RPI, semiannual, **no floor**; 2027–2073 |

**Deferred (catalogued, not run — §5/§7):** UK old-style 8m-lag = **2 bonds** (4.125% Stock 2030, 2% Stock 2035) — **correction to the brief: only TWO remain, not three** (the 2½% IL 2024 matured 17-Jul-2024). Italy **BTP Italia** = 6 bonds (retail, pay-go/par-reset).

**Watch items from the research pass (encode where relevant):**
- **Feb-2026 rebasing** of BOTH euro HICPxt (Eurostat) and French CPIxt (INSEE) to base-2025; coefficients through 1-Mar-2026 unchanged, new base after. Handled by the "recompute base from the same series" rule (§4) + the BBG IR cross-check.
- **UK RPI→CPIH reform from 2030** (full YoY alignment after the Jan-2031 print) — a deliberate index regime break; any pre/post-2030 stacking must treat it as such (§8.5).
- **Near/at-maturity 2026 bonds** still in the seed: BTP€i 0.65% 15-May-2026 and 3.10% 15-Sep-2026 (drop once redeemed).
- Euro **amounts outstanding are nominal** point-in-time; the inflation-uplifted value is higher and drifts with reopenings.

Sources (per market): AFT OAT€i/OATi outstanding & characteristics; MEF BTP€i revalued-capital file + factsheet; Tesoro "valores en circulación" + inflation-linked PDF; Finanzagentur inflation-linked page + outstanding-securities PDF (24-Jun-2026); UK DMO Quarterly Review (Jul–Sep 2025) + 3-month-lag deck + prospectus.

---

## Appendix — file map for the extension (mirrors the US build)

- `linkers.py` — per-country **conventions config** + **seed universe** + **Bloomberg universe discovery** + **auction/syndication calendar** collection. (Plays the combined role that `data_layer.SEED_UNIVERSE` + `auctions.py` play for the US.)
- `data_layer_intl.py` — pull/cache static + daily + reference-index series per ISIN; compute the index ratio per market (reuses `data_layer.index_ratio`). Writes `cache_intl/…` to keep the US cache untouched.
- `pricing.py` — **reused unchanged** (DCF + DV01 already take `freq`; act/act ICMA matches).
- `engine_intl.py` — per-bond financed total-return walk (one ISIN end-to-end; no OTR roll), country-aware settlement calendar/lag, floor at redemption.
- `export_intl.py` — one workbook, **one sheet per bond** + a README/universe sheet.
