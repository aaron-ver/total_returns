# Systematic Linker Curve/Fly Trades Around Supply and the Calendar — Research Plan

## Objective

Determine whether statistically robust, cost-viable curve, fly, or single-issue RV trades exist in inflation-linked markets (TIPS, EUR, GBP) around (a) auction and syndication events and (b) recurring calendar dates. The output is a verdict per market/sector/event bucket — tradeable, marginal, or dead — supported by event-study evidence gross and net of costs, plus a specification for a semi-systematic overlay where warranted.

## Data inventory and constraints

The study is built on the existing issue-level return series (EUR/GBP from ~2010, TIPS per current coverage) and the synthetic constant-maturity (CTM) bucket series already maintained for each linker market. Two constraints shape the design. First, no when-issued or pre-auction quotes were saved, so for genuinely new issues the bond itself is only observable from its issue date; the pre-auction concession must be measured on neighboring bonds and CTM points, while the bond's own path is only available for the post-auction richening leg. Reopens do not have this problem — the bond exists throughout the window — which makes reopens the higher-quality sample for the full concession/richening cycle and new issues primarily a neighbors-and-wings study. Second, EUR data begins ~2010, so the 2010–2012 sovereign crisis sits inside the sample; it will be handled as an explicit subsample split rather than pooled.

Supporting data to assemble: official auction and syndication calendars with both announcement and auction/pricing dates (US Treasury tentative schedule and results; AFT; Tesoro; UK DMO; Bundesfinanzagentur through end of German issuance; Tesoro Público for Spain), announced and realized sizes, CPI/HICP/RPI release dates per market, month-end index rebalancing dates and new-issue index inclusion dates, and seasonal vectors (BLS-implied for CPI, ECB SA series for HICPx, and the house RPI seasonal estimate for the UK).

## Phase 1 — Event calendar construction (the event matrix)

Build a master event table where each row is one supply event with fields: market, issuer, bond identifier, sector bucket (front/intermediate/long, mapped to 5y/10y/30y equivalents), new issue vs reopen, auction vs syndication, announcement date, auction or pricing date, settlement date, announced size, realized size, and DV01 of supply. For syndications the event anchor is the mandate announcement, with pricing day as a secondary anchor. Flag every event whose t−10 to t+10 window contains a CPI/HICP/RPI print, a month-end, or an overlapping supply event in the same market, so these can be excluded or dummied in robustness runs.

Anchor logic: where announcement and auction are separated by five or more business days, treat them as two distinct anchors and estimate separate paths around each (concession may build from announcement; richening keys off auction). Where spacing is under five days — common for EUR auctions — collapse to a single auction-anchored window and record the announcement offset as a covariate.

Expected counts should be tabulated up front per bucket. Any bucket with fewer than ~20 events gets pooled upward (e.g., merge issuers within EUR at the same tenor with issuer fixed effects) rather than analyzed standalone.

## Phase 2 — Metric construction

All analysis runs on adjusted series, built once and reused across studies.

Seasonality and carry adjustment: convert issue-level breakevens to seasonally adjusted breakevens using each market's seasonal vector (interpolated spot and maturity seasonals per the standard embedded-seasonality calculation), and work in changes of the adjusted measure. Real yields require no seasonal adjustment but event windows will still be checked against known carry extremes at the short end.

Fitted-curve residuals: fit a daily smooth curve (spline or Nelson–Siegel–Svensson, whichever is stabler per market given bond counts) to each issuer's real yields and, separately, to seasonally adjusted breakevens. The per-bond residual series is the primary RV object. Where issuer curves are too sparse (Germany, Spain), residuals are computed against the CTM bucket interpolation instead.

CTM series: use the existing synthetic constant-maturity buckets to define the tradeable curve and fly measures (e.g., 5s10s, 10s30s, 5-10-30 fly in both real yield and SA breakeven space), which sidesteps on-the-run identity drift at exactly the event dates.

Standardization: all event-window changes expressed both in raw bp and as z-scores against trailing 60-day realized vol of the same measure, so that 2013, 2020, and 2021–22 do not dominate the averages.

## Phase 3 — Study A: supply event study on curves and flies

For each event bucket, compute the average and median cumulative path of the relevant CTM curve/fly and of the auctioned sector's residual from t−10 to t+10 around each anchor. Hypotheses, stated ex ante: the sector cheapens on the curve into supply and retraces after settlement; the effect scales with supply DV01; reopens show a smaller but cleaner cycle than new issues; EUR new issues may show the opposite initial sign (launched at a discount, richening as size builds via reopens) consistent with the structural differences in EUR issuance.

Given the data constraint, new-issue events measure: (i) the CTM curve/fly around the tenor, (ii) neighbor-bond residuals pre-event, and (iii) the new bond's own residual from issue date forward (post-auction leg only). Reopen events measure the full path of the bond itself plus the same CTM and neighbor measures.

Inference: bootstrap confidence bands on the mean path (resampling events), sign tests on hit rates, and a placebo distribution built from randomly drawn non-event windows matched on calendar month and market. An effect is only carried forward if it clears the placebo distribution, not just a t-test.

## Phase 4 — Study B: RV fly around new issues (the 2036-2038-2040 case)

For every new issue with two adjacent existing bonds, construct the DV01-weighted 50/50 fly of the neighbors versus the interpolated new point pre-auction (via CTM/fitted curve, since the bond doesn't trade yet) and versus the actual bond post-issuance. Test whether the interpolated point cheapens relative to the wings into the event and richens after, and symmetrically whether the wings richen/cheapen as substitution flows hit them. Pool across events within each market with event fixed effects; report the average residual path of new bond, left wing, and right wing separately — the wing asymmetry (does the shorter or longer neighbor absorb more of the switch flow?) is itself potentially tradeable. Reopens get the same treatment and serve as the benchmark since the full path is observable.

## Phase 5 — Study C: calendar effects

Regress daily (or weekly) changes in the CTM curve/fly measures and in a cross-sectional cheap/rich factor (mean absolute residual) on month dummies, month-end dummies (split by whether a new linker enters the index that month-end), CPI/HICP/RPI release-day dummies, auction-proximity dummies from the Phase 1 calendar, and UK fiscal year-end. The auction controls are essential: linker supply calendars are themselves seasonal (no BTPei supply in August/December, TIPS new-issue months fixed), so an unconditional monthly effect is likely supply in disguise. A second, linker-specific test: whether seasonally adjusted breakevens still exhibit residual monthly patterns, i.e., whether the market's implied seasonal vector is systematically damped relative to statistical estimates — if so, this is directly expressible in forward BEs or fixings and belongs in the viable list.

Structural-break handling: UK results reported ex- and including the RPI reform announcement window (Nov 2020) and the LDI crisis (Sep–Oct 2022); EUR reported for full sample, ex-2010–12, and post-2015; TIPS split around 2013 and 2020–22.

## Phase 6 — From effect to trade: costs and viability

Translate each surviving effect into a concrete rule fixed ex ante from the shape of the average path on a training sample (first ~60% of events chronologically), validated on the holdout: entry anchor and offset, exit offset, instrument (real fly vs BE fly — if the concession is predominantly a real-yield phenomenon, trade the 3-leg real fly and avoid the 6-leg BE fly), and weighting (DV01-neutral 50/50 baseline; PCA weights as robustness). Apply a per-leg transaction cost schedule by market and issue age (indicatively 0.2–0.5bp TIPS on-the-runs, 0.5–1.5bp EUR issues, wider for off-the-runs and long GBP) and report net Sharpe, hit rate, worst event, and capacity considerations. Grid searches over entry/exit are reported in full with a multiple-testing haircut, never cherry-picked.

## Phase 7 — Semi-systematic overlay

For buckets that are marginal unconditionally, test state-dependent versions: trade only when the pre-event residual z-score indicates the concession has (or has not) already built by announcement; scale by supply DV01 relative to trailing absorption; skip events with a CPI print inside the window; skip when trailing vol exceeds a threshold. Each conditioning variable is specified before looking at conditional results, and the number of variants is kept small (≤5) to preserve statistical meaning.

## Deliverables

1. Event matrix and data-quality report (counts per bucket, exclusions, calendar sources).
2. Event-path chart pack: average paths with bootstrap bands per bucket, both anchors, raw and z-scored.
3. Results tables: mean/median/hit-rate/placebo-adjusted significance, gross and net of costs.
4. Calendar regression tables with and without supply controls.
5. Verdict grid (market × sector × event type × trade construct → viable / marginal / dead) and, for viable cells, a one-page trade spec each.

## Known pitfalls checklist

Seasonal contamination of unadjusted BEs; CPI prints inside event windows; month-end index extension coinciding with settlement; on-the-run identity drift (mitigated by CTM and bond-level residuals); small samples per bucket (mitigated by pooling, bootstrap, placebo tests); in-sample overfitting of entry/exit (mitigated by chronological train/holdout and reporting full grids); regime dominance by high-vol episodes (mitigated by z-scoring and subsample splits); EUR syndication timing unpredictability (mandate announcement as anchor); German issuance termination (closed sample, no live trade); UK RPI reform and LDI crisis breaks.