# Auction-Cycle Structure Trades in Real / Breakeven Space — Implementation Plan

**Provenance.** This plan adapts the original research draft (preserved verbatim as
`DRAFT_ORIGINAL.md`) to what this repo already has. The draft's scientific design — event studies
around supply anchors, fly RV around new issues, calendar regressions, placebo-gated inference,
chronological holdout — is kept. What changes is the *build*: most of the data assembly the draft
scopes as Phase 1–2 work already exists here, the actual data coverage differs materially from the
draft's assumptions, and the `breakeven_rv` study (v1–v6, closed 2026-07-08) already ran a
sector-level version of the auction hypothesis and produced binding lessons this study must obey.

All repo paths below verified 2026-07-08.

---

## 1. Objective (unchanged from draft)

Determine whether statistically robust, cost-viable curve, fly, or single-issue RV trades exist in
inflation-linked markets around (a) auction/syndication events and (b) recurring calendar dates.
Output: a verdict per market × sector × event-type bucket — **tradeable / marginal / dead** — with
event-study evidence gross and net of costs, plus a trade spec for every viable cell.

---

## 2. Binding lessons imported from `breakeven_rv` (non-negotiable)

The prior study tested "TIPS auctions perform when the sector is cheap" and **closed it as a
constant-maturity index construction artifact** (`breakeven_rv/REPORT_V3.md`): the (CM index −
bond-built) BE spread sits +0.4–0.6bp elevated t−4..t0 around auctions and collapses at t+1. The
v1 headline (+4.3bp/event, t≈4.4) was the index's own quote-refresh noise. Consequences here:

1. **Never use USGGBE-family CM indices as signal or outcome within ±5bd of a supply event.** All
   measurement is on individual bond quotes, bond-built curves, or the in-house CMT buckets
   (which are built from traded bonds and carry their construction inside the repo).
2. **Transfer test is mandatory before Phase 6.** Any effect found in curve/residual space must
   replicate in (i) financed DV01-normalized bp returns of the executable structure and (ii) the
   auctioned bond's own quotes, before costs are even discussed. This gate killed the v1/v2 result.
3. **Honesty conventions** (all enforced in `breakeven_rv/config.py`, reuse the same constants):
   report every grid node, never cherry-pick; cells with n < 12 events are "not establishable",
   not "suggestive"; expanding-window statistics only (and beware the expanding-percentile
   NaN-prefix bug documented in `breakeven_rv/IMPLEMENTATION.md`); vintage discipline (CPI usable
   from the 15th of m+1; NY Fed dealer data from `pub_date` = asof + 10cd); parameters declared
   before validation runs; placebo distribution must be cleared, not just a t-test.
4. **Encouragement, not just caution:** REPORT_V3's closing recommendation is exactly this study —
   *cross-sectional bond-vs-bond structures on the curve, where construction noise cancels by
   design*. The quadrant diagnostic also survived the bond rebuild; only the sector-level money
   didn't.

---

## 3. What already exists (reuse, do not rebuild)

| Draft scoped as "assemble" | Already in repo | Coverage (verified) |
|---|---|---|
| US TIPS auction results incl. internals | `breakeven_rv/data_auctions.py` → `breakeven_rv/cache/auctions_tips.parquet` | 250 events ≥$1bn, 1997→now; bid-to-cover, high/low/median yield, `tail_median_bp`, dealer/direct/indirect takedown %, SOMA, reopening flag |
| US auction calendar + OTR logic | `auctions.py` (TreasuryDirect API): `otr_schedule()`, `new_issues()`, `real_tips_auctions()` | TIPS 1998+, nominals 2003+ (calendar only, no internals) |
| EUR/GBP auction & tap history | `cache_intl/auctions.parquet` via `auctions_intl.py` (BBG static + AMT_OUTSTANDING steps + DMO D5D PDFs in `gilt_issuance/`) | 970 events: FR 374 (1999+), GB 334 (2005+ dense), IT 181 (2004+), ES 78 (2015+), DE 3; syndications flagged |
| Per-bond price history, US | `breakeven_rv/cache/tips_bond_quotes.parquet` (+ raw `cache/daily/{cusip}.parquet`) | 2010-02→now, 177 CUSIPs — **engine-held bonds only** (e.g. 32 10y TIPS), not the full strip |
| Per-bond price history, intl | `cache_intl/daily/{isin}.parquet`, universe in `cache_intl/universe.csv` | FR OATei 12 bonds 2002+, FR OATi 5 1999+, IT 15 2004+, UK (3m-lag) 32 2014+, DE 3 2014+, ES 5 2015+ |
| CTM bucket series | `cmt_intl.py` → `cache_intl/cmt/*.parquet` | Intl only, ~2016+; per-bucket financed linker/nominal/BE bp returns — **already carry `is_auction_date`, `auction_isin`, `auction_amount`, `auction_is_held` columns** |
| US bond-built basis + OTR/off-run IDs | `breakeven_rv/b_bond.py` → `b_bond*.parquet` | `otr_cusip`/`off1_cusip` per date per tenor; maturity-matched BE-vs-swap basis, seasonally adjusted (expanding) |
| US financed sector returns w/ auction flags | `engine.py` → `cache/returns_{5,10,30}y.parquet`, `exports/breakeven_*.csv` | DV01-normalized financed bp; `Is_{5,10,30}y_auction_date`, `auction_size_bn`, roll/coupon flags already columns |
| Pricing / DV01 | `pricing.py` (`bond_metrics()`, bump-and-reprice DV01) | BBG RISK_MID is mis-scaled for TIPS — always use in-house DV01 |
| Financing | `financing.py`: GC mid ± half-spread (default 3bp), act/360 | No specialness model (see gaps) |
| Dealer positioning | `breakeven_rv/data_dealer.py` → NY Fed FR2004 weekly TIPS by maturity bucket | 2013-04+, vintage-safe (`load_daily()`), series-break-aware z-scores |
| Seasonals | intl: `seasonal_intl.py` + `exports/cmt/_seasonal_*.csv`; US: expanding month-of-year adj in `b_bond.py` | reuse; never full-sample seasonals |
| BBG access | `bbg.py`: shared-session `history()`/`reference()` with `SETTLE_DT` overrides; batch ≤15 CUSIPs on heavy fields | identify bonds as `"<CUSIP> Govt"` |
| Prior art on concession | `breakeven_rv/auction_study.py` (event panel builder, placebo machinery), `track2.py` (t−5→t0 concession control) | lift the panel/placebo code patterns |

**Net effect on the draft:** its Phase 1 ("event calendar construction") and half of Phase 2
collapse from build-work into assembly-work. The real net-new build is: fitted-curve residuals,
the structure-return layer (switches/flies as financed packages), and the three studies themselves.

---

## 4. Where reality differs from the draft (design deviations)

1. **Coverage is not "EUR/GBP from ~2010".** Bond-level: FR 2002+, IT 2004+ (both *include*
   pre-GFC), UK only 2014+ (the 3m-lag universe; 8m-lag gilts aren't cached — so no LDI-free
   pre-2014 UK sample without a new pull), ES 2015+/5 bonds and DE 3 bonds (**too thin standalone
   — ES pooled into EUR robustness only, DE dropped**; German issuance is terminated anyway).
   Subsample splits change accordingly: FR/IT get a pre/post-2015 split and a 2010–12 crisis
   dummy; UK gets LDI (Sep–Oct 22) and RPI-reform (Nov 20) exclusion runs as in the draft.
2. **Intl reopening anchors are settlement-dated, not auction-dated.** BBG-sourced reopenings in
   `cache_intl/auctions.parquet` are dated by the AMT_OUTSTANDING step (≈ settle, T+2..T+5 after
   auction). GB events from DMO PDFs have true auction dates; FR/IT/ES mostly don't. Handle by
   (a) preferring DMO/manual-CSV rows where present, (b) back-shifting bbg_amt anchors by the
   market's standard settle lag and flagging them `anchor_quality='approx'`, (c) robustness run
   excluding approx anchors. Do **not** silently mix anchor types.
3. **US CTM buckets don't exist and won't be built.** The draft leans on CTM everywhere; for TIPS
   the artifact finding says bond-level is safer anyway. US curve/fly measures come from the
   fitted-curve layer (below) + `b_bond` + engine tenor returns. Intl keeps CMT buckets (2016+)
   *plus* bond-level residuals (2002+) — the pre-2016 intl sample is bond-residual-only.
4. **The US quote universe must be extended for the fly study.** `tips_bond_quotes` covers only
   engine-held CUSIPs. Study B needs the neighbors/wings of every new issue — i.e. the full TIPS
   strip (~50 live + matured since 2010). One terminal pull session via existing `data_layer.py`
   machinery; `auctions.py` already provides the CUSIP list.
5. **Announcement dates: free for the US, manual for intl.** TreasuryDirect serves
   `announcementDate` (verified against the live API 2026-07-08) — one-line addition to the
   `data_auctions.py` KEEP list, full history backfillable. AFT/Tesoro announcement dates would be
   manual assembly; **intl v1 runs auction-anchored only**, with the dual-anchor design a US-only
   feature until/unless intl announcement data is sourced.
6. **We have conditioning data the draft doesn't use.** Auction internals (tail proxy,
   bid-to-cover, dealer takedown %) and NY Fed dealer TIPS positions (vintage-safe) are already
   cached. These join Phase 7's declared conditioning set. Prior: v4 found the dealer-inventory
   *level* gate additive at sector level (price share monotone in 1y-z tercile) — worth one
   declared test cross-sectionally.
7. **Structure P&L is computed in the house framework, not yield-space proxies.** Detection runs
   in yield/residual space (draft's design), but viability (Phase 6) prices every structure as a
   financed DV01-weighted package using `pricing.py` + `financing.py` — same convention as
   `engine.py`, so results are comparable with the TIPS deliverable. Cost grid reuses
   `T3_COSTS_BP = [0.5, 1.0, 2.0]` bp round-trip (per leg, scaled by the draft's per-market/age
   schedule), reported at all nodes.
8. **No WI data, confirmed.** `breakeven_rv/data_wi.py` is a stub (no historical BBG field; inbox
   at `breakeven_rv/inbox/wi_snaps.csv` if the desk ever supplies snaps). The draft's design
   already handles this correctly: new issues = neighbors-and-wings study pre-auction + own-path
   post-issue; **reopens are the primary sample** (US ~2/3 of the 250; FR 340, GB 268, IT 145).
9. **No special-repo data anywhere** — financing is GC ± half-spread by design (`financing.py`
   docstring documents why). OTR/WI richness that is really repo specialness will look like
   unexplained richening; interpret Study A/B results with this stated, and stress the financing
   half-spread (3→10bp) as a sensitivity in Phase 6 rather than pretending to model specials.

---

## 5. Market sequencing

**v1 = TIPS** (best data: auction internals 1997+, quotes 2010+, dealer conditioning, engine
integration, prior art). **v2 = FR + IT + UK** validation on the declared v1 spec — this also
matches the `breakeven_rv` closing note that the next laboratory is cross-sectional linkers.
ES = pooled-EUR robustness only. DE = dropped. Nothing in v2 is tuned; it validates frozen v1 rules.

---

## 6. Phase plan

### Phase 0 — Data extension (the only new pulls)
- `data_universe.py`: extend the per-CUSIP US pull to the full TIPS strip since 2010 (CUSIP list
  from `auctions.py::load_auctions()`; pull via `bbg.py` history, batched, into the existing
  `cache/daily/` layout; consolidate to `cache/bond_quotes_full.parquet` mirroring the
  `tips_bond_quotes` schema: date, cusip, leg, tenor, yld, px_clean, maturity, coupon).
- `data_events.py` (US half): re-pull TIPS auctions adding `announcementDate` (+ optionally the
  same fieldset for nominal 5/10/30 auctions — see open question Q2).
- `data_calendar.py`: CPI release dates (BLS schedule, scrapeable; FRED release metadata via the
  existing `data_fred.py` pattern), month-ends, deterministic index-entry approximation (linker
  enters index at first month-end post-issue; flag as approximation until real dates sourced).
- Requires one Bloomberg terminal session; everything else runs from cache.

### Phase 1 — Event matrix (`data_events.py`)
One row per supply event: market, bond id, sector bucket (5y/10y/30y-equivalent), new/reopen,
auction/syndication, announcement date (US), anchor date + `anchor_quality`, settle date,
announced/realized size, **DV01 of supply** (size × in-house DV01 from `pricing.py`), auction
internals where available (US), and contamination flags (CPI/HICP/RPI print, month-end, or
overlapping same-market supply inside t−10..t+10). US assembles from `auctions_tips.parquet` +
`auctions.py`; intl from `cache_intl/auctions.parquet` with the anchor-quality handling of §4.2.
Deliverable: counts per bucket table; buckets under 20 events pooled upward with issuer FE
(draft rule, kept).

### Phase 2 — Metric layer (`curves.py`, `structures.py`)
- **Fitted-curve residuals** (net-new): daily smooth curve per issuer on real yields and on
  SA breakevens; spline vs NSS chosen per market by bond count/stability (US 10y sector has
  ~10–15 live TIPS — spline fine; ES/DE too sparse → CMT interpolation instead, per draft).
  Per-bond residual = the primary RV object. SA via the existing seasonal machinery (expanding,
  never full-sample).
- **Curve/fly measures**: intl from CMT buckets (existing); US from fitted-curve points at
  5/10/30 + `b_bond` OTR/off1 series.
- **Structure returns** (net-new): `structures.py` prices any k-leg package (switch, 50/50 fly,
  DV01-neutral) as financed bp/day via `pricing.py`/`financing.py`, so any candidate rule
  backtests in executable terms. Reuses `engine.py` conventions (T+1 settle, act/360 financing,
  roll/coupon handling).
- **Standardization**: event-window changes in raw bp and z vs trailing 60d vol of the same
  measure (draft, kept).

### Phase 3 — Study A: supply event study (`sA_eventstudy.py`)
As drafted: mean/median cumulative paths t−10..t+10 of sector residual + curve/fly measures per
bucket; hypotheses ex ante (cheapen into supply, retrace post-settle; scales with supply DV01;
reopens cleaner than new issues; EUR new-issue sign may flip). Inference: event-resampled
bootstrap bands + hit-rate sign tests + **placebo from non-event windows matched on month ×
market** — effect carried forward only if it clears placebo (draft rule, and the exact gate that
killed v1). Add the transfer test here, not at Phase 6: paths must reproduce on bond quotes and
financed structure returns.

### Phase 4 — Study B: fly RV around new issues (`sB_fly.py`)
As drafted (the 2036-2038-2040 case): neighbor-wing 50/50 DV01 fly vs interpolated new point
pre-auction, vs actual bond post-issue; wing-asymmetry (who absorbs the switch flow) reported
separately; reopens as the observable-path benchmark. Requires the Phase 0 full-strip pull.
Pool with event FE per market.

### Phase 5 — Study C: calendar effects (`sC_calendar.py`)
As drafted: month / month-end (split by index-entry) / CPI-release / auction-proximity / UK FY-end
dummies on CTM curve-fly changes and a cross-sectional cheap/rich factor (mean |residual|), with
auction controls always in (supply calendars are themselves seasonal). Second test kept: is the
market's implied seasonal damped vs statistical seasonals (expressible in fixings — note the
USSWIF fixings cache exists but history only 2025+, so this test is spot-BE-based). Structural
break handling per §4.1 splits.

### Phase 6 — Costs & viability (`sD_viability.py`)
Rules fixed ex ante on chronological first ~60% of events, validated on holdout (draft, kept).
Instrument choice rule kept (if concession is a real-yield phenomenon, trade the 3-leg real fly,
not the 6-leg BE fly). Costs: per-leg schedule (draft's 0.2–0.5bp TIPS OTR, 0.5–1.5bp EUR, wider
off-run/long GBP) crossed with the house `T3_COSTS_BP` grid; financing half-spread stressed
3→10bp for structures long an OTR leg (specialness proxy, §4.9). Report net Sharpe, hit rate,
worst event, per-event skew, capacity; full grids with multiple-testing haircut.

### Phase 7 — Conditional overlay
Draft's ≤5 declared conditioners, now drawn from data we actually hold: pre-event residual z
(has the concession already built), supply DV01 vs trailing absorption, CPI-print-in-window skip,
trailing-vol skip, **dealer takedown % / NY Fed inventory z** (vintage-safe). Declared before
looking; anecdote rule (n<12) applies per cell.

---

## 7. Module layout & conventions (mirrors `breakeven_rv`)

```
breakeven_structures/
  PLAN.md  DRAFT_ORIGINAL.md  IMPLEMENTATION.md   # living deviations log, start at Phase 0
  config.py          # paths, windows, cost grids, MIN_CELL_N=12, declared params
  data_events.py  data_universe.py  data_calendar.py
  curves.py  structures.py
  sA_eventstudy.py  sB_fly.py  sC_calendar.py  sD_viability.py
  figures.py  run_all.py     # stages: pull | build | eventA | flyB | calC | viability | all
  cache/  reports/           # cache gitignored; reports/*.md tracked
  REPORT.md
```

Everything except `pull` runs from cache (no terminal). Each `data_*.py` owns one `OUT` parquet
with `pull()/build()/load()`. Findings and deviations logged in `IMPLEMENTATION.md` as built.

---

## 8. Deliverables (unchanged from draft)

1. Event matrix + data-quality report (counts, exclusions, anchor-quality shares).
2. Event-path chart pack (bootstrap bands, per bucket, both anchors where available, raw + z).
3. Results tables gross/net, placebo-adjusted, all grid nodes.
4. Calendar regressions with/without supply controls.
5. Verdict grid + one-page trade spec per viable cell.

---

## 9. Open questions / data needed (answers change scope, not design)

- **Q1 — Sequencing:** plan assumes TIPS-first v1, FR/IT/UK v2. Confirm or reorder.
- **Q2 — Nominal auctions:** BE structures also react to *nominal* supply. Extending
  `data_auctions.py` to pull nominal 5/10/30 internals is trivial (same API). Include nominal
  auction events as a separate event type in v1? (Plan says yes unless told otherwise.)
- **Q3 — Terminal session:** Phase 0 needs one DAPI session (full TIPS strip ~50 CUSIPs × 16y
  daily history, batched). Any preferred time window on the terminal box?
- **Q4 — UK pre-2014:** the 8m-lag gilt universe isn't cached. Pulling it would extend UK to
  ~2005 (pre-GFC + pre-LDI sample) at the cost of a manual ISIN list + pull. Worth it, or is
  2014+ UK acceptable for v2?
- **Q5 — Intl announcement dates:** AFT/Tesoro/DMO announcement calendars are manual assembly.
  v1 proceeds auction-anchored for intl; flag if the desk has these on file.
- **Q6 — Index-entry dates:** deterministic approximation (first month-end post-issue) vs real
  index-extension dates (desk/BBG export). Approximation used until real dates supplied.
- **Q7 — WI snaps:** if the desk saves 1pm WI levels going forward (`breakeven_rv/inbox/
  wi_snaps.csv` format: cusip, auctionDate, wi_yield_1pm), the true-tail leg of the US study
  switches on automatically. No historical source exists.
