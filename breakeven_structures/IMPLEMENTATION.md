# Implementation log — auction-cycle structures study

Deviations, sourcing outcomes, and declared parameters, in build order. Everything in
"Declared before first build" was fixed BEFORE any event panel was constructed
(user directive, 2026-07-09). Parameters live in `config.py`; this file records the why.

## Declared before first build (2026-07-09)

**Resolved questions (user):**
- Q1: TIPS-first v1; FR/IT/UK frozen-spec v2 — nothing in v2 tuned. `V1_MARKETS`/`V2_MARKETS`.
- Q2: nominal 5/10/30 auctions in two roles — separate event type + same-tenor contamination
  flag inside TIPS windows (`INCLUDE_NOMINAL_EVENTS`, `FLAG_NOMINAL_SAME_TENOR`). Internals
  pulled for nominals (same TreasuryDirect fieldset).
- Q3: prep first, pull once. `data_universe.py dry-run` prints the exact session plan; the
  single terminal session covers the full TIPS strip. The other Phase 0 items
  (announcementDate, nominal internals) are public-API and were pulled without the terminal.
- Q4: no pre-2014 UK — 8m-lag gilts are a structurally different instrument (lag mechanics,
  carry, seasonal treatment). Parked as possible v3 only if UK v2 is marginal AND
  sample-starved. `UK_MIN_DATE = 2014-01-01`.
- Q5: syndications split out; pricing-day anchor is near-meaningless (concession builds from
  mandate announcement). `SYNDICATION_POLICY='pricing_only'`: excluded from pre-event path
  analysis, post-pricing-only otherwise, never pooled into auction buckets. Mandate dates to
  be sourced targeted (BBG headlines/press releases, small n).
- Q6: index entry approximated. US/GB = first month-end on/after issue. EUR = first month-end
  after cumulative outstanding (initial + taps from the auctions parquet) crosses
  `EUR_INDEX_MIN_OUT = 2.0e9` — because EUR new issues often launch below index-minimum size.
  The 2.0bn threshold is itself a declared approximation, to be verified against the index
  factsheet.
- Q7: desk starts saving 1pm WI snaps (desk-side); slow-accruing future leg, not a v1
  dependency. Inbox: `breakeven_rv/inbox/wi_snaps.csv`.

**Standing directives (user):**
1. CPI-in-window = DUMMY, don't drop (`CPI_WINDOW_POLICY`). The mid-month print lands inside
   ±10bd of most TIPS auctions; exclusion would gut the sample. Paths dummy out / exclude the
   print-day return; full exclusion = robustness run only.
2. Holdout regime caveat declared now: chronological `TRAIN_FRAC=0.60` on TIPS puts training
   ≈ pre-2020 and the holdout in the 2021-22 inflation regime + after. Holdout-era placebo
   distributions are reported SEPARATELY (the `in_holdout` label in the event matrix makes
   this mechanical).
3. anchor_quality: back-shift bbg_amt anchors by standard settle lag
   (`SETTLE_BACKSHIFT_BD`: EUR 2bd, GB 1bd), never mix silently, report approx-anchor share
   per bucket in `reports/data_quality.csv`.

**Other parameters declared before first build:** event window t−10..t+10
(`T_PRE`/`T_POST`), sector buckets by remaining maturity (<7.5y → 5y-equiv, 7.5–15 → 10y,
>15 → 30y), `MIN_BUCKET_EVENTS=20` (pool upward w/ issuer FE), `MIN_CELL_N=12` (anecdote
rule, = breakeven_rv), `N_BOOT=N_PLACEBO=2000`, `VOL_WINDOW_BD=60`,
`ROUND_TRIP_COSTS_BP=[0.5,1,2]`, `FIN_HALF_SPREAD_BP_GRID=[3,10]` (10 = specialness-proxy
stress; no special-repo data exists anywhere, per `financing.py`), `PULL_START=2004`.

## Sourcing outcomes (Phase 0, 2026-07-09)

- **announcementDate**: served by the TreasuryDirect API (verified live) — added to KEEP in
  `data_auctions_us.py`; full history backfills for free. `announce_gap_bd` derived.
- **BLS CPI release schedule: bot-blocked (403) from this box**, even with browser headers —
  scraping is not an option. Exact historical release dates come from either
  (a) the FRED release-dates API (release_id=10; free key in env `FRED_API_KEY` or in
      `breakeven_structures/inbox/fred_api_key.txt`), or
  (b) desk CSV at `breakeven_structures/inbox/cpi_release_dates.csv` (column `release_date`).
  Until one lands, window-contamination flags use the declared day-10..15-of-month rule
  (`cpi_flag_source='rule_window'` in the event matrix); the print-day return dummy in Study A
  is BLOCKED on exact dates. FRED key is the low-effort fix.
- **Intl CPI/HICP/RPI release calendars**: not built in v1 (TIPS-first); intl `flag_cpi` is
  NaN. v2 prerequisite.
- **Intl amounts**: units differ by source (DMO in £mn, BBG AMT_OUTSTANDING steps in raw
  units); `data_calendar.build_index_entry()` infers scale per country from the median and
  logs it. Verify in build output.

## Build deviations (as built)

- **flag_month_end**: the draft's "month-end inside t−10..t+10" flags nearly every event
  (a ±10bd window spans ~28cd). Replaced with `flag_month_end_settle` (month-end within ±2bd
  of SETTLE — the actual risk is index extension coinciding with settlement) plus
  `flag_index_entry` (an index-entry date inside the window). Deviation documented here.
- **Intl event dedupe**: the same physical event can appear twice (DMO auction row +
  bbg_amt settle-step row; bbg static issue row + syndication row). Rows for the same ISIN
  within `DEDUPE_WINDOW_CD=7` calendar days collapse to the best anchor quality;
  a syndication row outranks a plain new-issue row for the same event.
- **US supply DV01**: in-house bump-and-reprice (`pricing.risk_dv01`) at the auction stop
  (`highYield`), coupon = `interestRate`, settle = issue date, `ir=1.0` (declared
  approximation; reopening index ratios slightly exceed 1). NaN where the stop/coupon is
  missing. Intl supply DV01 deferred to Phase 2 (needs a yield joined from quotes).
- **Intl new-issue anchors**: bbg-static `issue` rows are dated by ISSUE_DT (settlement of
  the new line) — back-shifted like bbg_amt rows, `anchor_quality='approx'`.

## Phase 0/1 build outcomes (2026-07-09)

- `auctions_us.parquet`: 1,005 auctions ≥$1bn (TIPS 250 from 1997, nominal 5/10/30 755 from
  2003). **announcementDate coverage 100% both legs**; announce_gap median 5bd (dual-anchor
  design viable across the whole US history). Internals coverage: BTC/high/median ≥96%,
  takedown ~82-86%.
- **Intl anchors resolved BETTER than planned**: the cached `cache_intl/auctions.parquet`
  reopenings all come from the manual results files (AFT/Tesoro/BdE/DMO PDFs → `source='dmo'`)
  with TRUE auction dates. Final anchor-quality mix: 834 exact / 12 approx (bbg-static new
  issues, back-shifted) / 53 pricing_only (syndications). PLAN §4.2 updated; the back-shift
  machinery remains for any future bbg_amt rows.
- `events.parquet`: 1,904 events (US tips 250, US nominal 755, FR 358, GB 302, IT 166, ES 73).
  Flag shares: overlap-supply 64%, nominal-same-tenor (TIPS windows) 74%, index-entry 22%,
  month-end-settle 35%, CPI 100% (rule window — every ±10bd window contains a print, exactly
  why the directive is dummy-don't-drop; exact print DAYS still needed for the path dummy).
- **Actual chronological cut dates** (TRAIN_FRAC=0.60 by event count, per market×leg):
  US tips 2018-03, US nominal 2018-02, FR 2022-07, GB 2019-04, IT 2022-02, ES 2024-01.
  Note: the US cut lands 2018, not the user's rough "pre-2020" estimate — count-weighted
  because reopening frequency rose. Declared as-is; the 2021-22 regime is entirely in holdout
  either way.
- **BUG fixed during build (before any analysis)**: `pricing.risk_dv01` takes ytm in PERCENT
  (docstring) and returns a dict. First pass divided by 100 → near-zero yield → 30y supply
  DV01 ~3x overstated (only visible at long duration; a 5y spot-check couldn't catch it).
  Post-fix medians: TIPS 10y $12.3mm/bp, nominal 30y $31.1mm/bp — sane.
- `bond_quotes_full.parquet`: 93/99 full-strip CUSIPs already in the engine cache →
  169,396 rows, 2004→2026 built WITHOUT the terminal. Terminal session shrinks to 6 matured
  1998-2002 CUSIPs (pre-2010 fly wings only): `python -m breakeven_structures.run_all pull-terminal`.
- **Stale live off-runs discovered**: the daily engine update refreshes only current OTRs, so
  45 live off-run TIPS had no quotes since 2026-06-24. The terminal session now also re-pulls
  stale live bonds (`data_universe._stale_live`, >5bd old); matured files never touched.

## Phase 2 + Study A specs (declared 2026-07-09, before results were seen)

- **Curve** (`curves.py`): per-day least-squares cubic B-spline of real yield on tau, knots
  from [2, 3.5, 5, 7.5, 10, 20] pruned to >=2 bonds/segment; MIN_TAU=1.0y; MIN_BONDS_DAY=6;
  residuals evaluated at bond points only, never in the 10-24y strip gap. 5,588 fit days
  2005-2026, 281 skipped. KNOWN LIMITATION: 2005-06 residuals are mechanically compressed
  (7-10 bonds vs ~10 spline dof) — early-year event magnitudes are attenuated; z-paths
  partially compensate.
- **LOO residuals for the auctioned bond**: the in-fit residual absorbs the bond's own
  dislocation (fit chases the bond). Event/placebo paths for REOPENINGS price the bond off a
  curve fit WITHOUT it (`curves.loo_resid`; exact-duplicate maturities averaged — fitpack
  needs increasing x; no extrapolation). Effect: 10y reopen concession 0.17bp (in-fit) →
  0.44bp (LOO). New-issue neighbor paths stay in-fit (the new bond isn't in the panel
  pre-auction; neighbors' own influence on the fit is generic, not event-specific).
- **LOO blind spot**: the reopened 30y is usually the LONGEST bond on the curve — LOO would
  extrapolate, which is banned → 30y reopen cell is NOT ESTABLISHABLE under the declared
  spec (n<12 usable). Reported as such, not patched.
- **Placebo amendment (documented deviation)**: draft's same-calendar-month matching is
  EMPTY by construction for TIPS (auctions recur in the same months every year, and
  same-bucket auction windows are excluded) → matching widened to month±1, same bond(s),
  same construction (LOO for reopens), >=15bd from any same-bucket auction, up to 8 anchors
  per event, one-per-event cluster draws (N=2000), one-sided p in the declared direction.
- **Study A first results** (`reports/sA_stats.csv`, figure `reports/figures/sA_paths.png`;
  195/250 events usable — losses are pre-2005 anchors + window coverage):
  - **5y post-auction RICHENING is the standout**: reopens retrace_5 −1.12bp (p=0.008;
    train −1.82 p=0.036, holdout −0.33 p=0.035), new-issue neighbors retrace_5 −0.44bp
    (p<0.001; train p=0.014, holdout p=0.006). Era-stable, passes placebo in BOTH eras —
    but NO pre-auction concession (5y conc ≈ 0 / negative): richening without measured
    cheapening, so the trade would be post-auction entry, not concession-capture.
  - **10y concession exists but doesn't pay**: reopen conc +0.44bp (p=0.026) with NO
    retrace (p=0.73); train conc +0.75 (p=0.024) vs holdout +0.08 (p=0.35) — faded
    post-2018. New-issue neighbors cheapen (+0.16, p=0.012, holdout stronger) and KEEP
    cheapening after — consistent with permanent-supply repricing, not a cycle.
  - **30y**: new issues nothing (n=17); reopens not establishable under LOO.
  - Multiple-testing caveat: 4 stats x 6 cells x 3 eras reported in full; the 5y retrace
    is the only pattern significant across independent eras and both event kinds.
