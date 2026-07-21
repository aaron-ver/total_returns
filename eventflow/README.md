# eventflow — weekend / headline de-risking study (boss spec 2026-07)

Hypotheses: (1) London PMs de-risk Thu-eve→Fri-London-close on headline weeks (front end
bid); (2) quiet weekends carry a premium; (3) sell the front end into the Friday closes to
harvest the fear flow; (4) Trump/geopolitical headlines cluster after 3pm ET.

## Data (all free, no Bloomberg — BBG limits bypassed as the boss suggested)
- `pull_bars.py` — Yahoo HOURLY futures bars, 2.4 years (Feb-2024→): TU=ZT, TY=ZN, US=ZB, WN=UB.
  Cache accretes each run. NO open interest here — later small BBG daily pull (FUT_AGGTE_OPEN_INT).
- `pull_news.py` — GDELT DOC 2.0 API: 15-min global headline volume for `iran` and `trump`
  queries since 2026-01. Rate-limited: waits 6s/request, backs off on 429; if a run fails,
  simply rerun later (bans clear within the hour).
- Corporate proxy note: TLS re-signed by the firm's MITM; `common.http_get` relaxes verification
  (public data only) — see comment there.

## Analysis
- `windows.py` — weekly clock-window returns per instrument + the CURVE = TU − β·US series
  (β from full-sample hourly OLS: "pull out the parallel curve risk"). Windows (ET):
  Thu17→FriLondon(11:30), FriLondon→Fri15:00, Fri15:00→reopen (weekend gap), Mon London.
  Prints mean/t/hit by window for the boss sample (Mar-1→) and the full 2.4y sample.
- TODO next: headline split (high/low-iran weeks) once GDELT cache fills; Q4 clock histogram
  of headline timestamps vs 15:00 ET; day-of-week×hour heatmap; eventflow.html for the portal.

## First results (2026-07-20, full 2.4y sample — see windows.py output)
- CURVE thu_into_ldn: +1.0 bp/wk, t=+1.7, hit 59% — front end SPECIFICALLY outperforms into
  London Friday close (the de-risking bid), visible only after stripping parallel risk.
- TU weekend_gap: −0.4 bp/wk, t=−2.9, hit-long 35% — being LONG over the weekend systematically
  LOST money → the Friday fear bid unwinds at the Sunday reopen. Mirror-read: the boss's "sell
  the front end into Friday close, cover at reopen" carried positive expectancy on this sample.
- CAVEATS: units are bp of futures PRICE (not yield); TU's tick ≈ 0.4 price-bp so the weekend
  edge is roughly ONE TICK/week before costs — real but thin; needs the headline conditioning
  (does it concentrate in headline weeks?) before anyone trades it.

Units: log price return × 1e4. Positive = price up = yields down = rally.
