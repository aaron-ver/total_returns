# TIPS / Breakeven Total-Return Build

Daily financed total-return series for on-the-run US TIPS (5y/10y/30y) and a parallel
breakeven series. Spec: [reference.MD](reference.MD).

The full pipeline is built: a **data layer** (pull/cache raw inputs from Bloomberg +
TreasuryDirect), an **auction calendar / OTR reconstruction**, the **financed total-return
engine** (a DV01-normalized breakeven bp series per tenor), and three ways to consume it — an
**interactive HTML dashboard**, a day-level **Excel/CSV export** for hand-replication, and a
static **PNG visualizer**. The engine follows reference.MD §5–§9 with the desk's as-built
choices — DV01-normalized **linear** bp (100k DV01/leg, rebalanced monthly), **GC-mid**
financing with a tunable repo half-spread, and a **maturity-matched** OTR pairing — documented
in reference.MD's "As-built" section and in `engine.py`.

## Setup (contained env)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
:: blpapi is not on PyPI -- install from Bloomberg's repo (needs the Terminal running):
pip install --index-url https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

Requires an active Bloomberg Terminal logged in on this machine (the Desktop API runs on
`localhost:8194`). No other account is needed for the core build.

## Data sources

| Input | Source | Notes |
|---|---|---|
| TIPS/UST clean+dirty prices, real/nominal yields | Bloomberg DAPI | by CUSIP; dirty is cash (inflation-adj) for TIPS |
| Index ratio / DRI / accrued | **computed** (reference.MD §2.1–2.4) | BBG's historical IDX_RATIO/INT_ACC are unreliable; §2.1 formula matches BBG live to 1e-6 |
| CPI-U NSA | `CPURNSA Index` | current print (not as-first-published) |
| GC repo (financing, BOTH legs) | `GCFRTSY Index` (DTCC GCF Treasury) | **desk decision: TIPS=UST at GC; specialness & repo bid/offer ignored** — discount slippage in analysis |
| Auction calendar / OTR schedule | TreasuryDirect public API | complete TIPS history to 1998; nominals paged to 2003 |

## Commands

```bat
:: --- data layer (caches to ./cache as parquet) ---
python data_layer.py macro                 :: CPI + GC repo series (2003->present)
python data_layer.py universe              :: build the OTR bond universe from the auction schedule
python data_layer.py bonds                 :: pull static + daily for every OTR bond (resumable)
python data_layer.py update                :: INCREMENTAL daily refresh (~45s): macro + new auctions
                                           ::   + brand-new bonds + re-pull current OTRs only.
                                           ::   Off-the-run/matured bonds are frozen (never re-pulled).
                                           ::   (export.py & dashboard.py call this for you — see below.)
python data_layer.py status                :: what's cached
python data_layer.py preview               :: CPI publications (monthly, m/m + y/y) + GC repo + universe
python data_layer.py preview 36            :: same, but last 36 CPI months
python data_layer.py preview 91282CPU9 40  :: one bond's static + daily (40 rows)

:: --- auction calendar / OTR schedule ---
python auctions.py pull                     :: fetch + cache the Treasury auction calendar
python auctions.py schedule                 :: build + print the monthly OTR schedule

:: --- return engine (financed breakeven TR) ---
python engine.py                            :: build 5y/10y/30y -> cache/returns_<tenor>.parquet
python engine.py 10y                        :: one tenor + summary
python engine.py validate                   :: permanent per-tenor checks: (A) day-count tiles the
                                            ::   settlement timeline, (B) every roll's first return is
                                            ::   within one bond, (C) held pairing matches the desk
                                            ::   maturity-match table (5y Apr/Apr,Oct/Oct; 30y Feb/Feb;
                                            ::   10y staggered cycle). Exits non-zero on failure.
python engine.py seasonal                   :: build the auction-cycle bucket table -> cache/seasonal.parquet,
                                            ::   then print QA (auction day-of-month, A0==A4[M-1] seam, clamp counts)
:: output columns: r_TIPS_bp, r_UST_bp, r_BE_bp(=r_TIPS-r_UST), s_TIPS, s_UST (repo-spread
::   sensitivity: bp drag per 1bp half-spread), cum_* (linear-sum bp)
:: each leg = financed LONG at GC mid; DV01 denominator set at the MONTHLY rebalance (100k DV01)
::   and held constant within the month; r_BE encodes long-TIPS/short-UST.
:: recombine with an arbitrary beta later as r_TIPS_bp - beta*r_UST_bp (no rebuild needed).

:: raw numbers for a timeframe/month (terminal):
python engine.py window 10y 2024-01-01 2024-12-31 3 3   :: monthly table + window total (bp)
python engine.py window 10y 2020-03-01 2020-03-31 3 3    :: <=45d window -> daily rows
::   args: window <tenor> [start] [end] [xT_bp] [xU_bp]   (use - to skip start/end)

:: --- full data dump for hand-replication (boss's ask) ---
:: export & dashboard auto-REFRESH first (engine.refresh): pull latest data via data_layer.update
::   then rebuild returns_<tenor>.parquet -> never stale. If the Terminal is down they warn and
::   fall back to the cache. Add --no-update to skip the Bloomberg pull and build from cache as-is.
python export.py                            :: exports/breakeven_full.xlsx (5y/10y/30y sheets, ALL inputs
                                            ::   + intermediates, day-level business days) + per-tenor CSVs + README sheet
python export.py returns 3 3                :: compact returns-only xlsx at x_TIPS=x_UST=3bp
python export.py --no-update                :: export from the cached data as-is (no Bloomberg pull)

:: --- interactive HTML dashboard (recommended; instant, offline, no server) ---
python dashboard.py                         :: refresh, then build dashboard.html and open it
                                            ::   views: Chart | Table | SEASONAL (Aggregate/History/Calendar/Predict).
                                            ::   tenor is MULTI-SELECT (overlay in Chart & Calendar); repo x_TIPS/x_UST
                                            ::   sliders, date range; seasonal adds TIPS/Nominal/Breakeven + beta,
                                            ::   sample window(s) Full/5Y/3Y, issue new/reopen, month/H1/H2.
                                            ::   all recompute is client-side JS -> no lag. Plotly embedded (offline).
python dashboard.py --no-update             :: build from cached data (skip the Bloomberg pull)
python dashboard.py --no-open               :: build but don't open the browser

:: --- baseline visualizer (PNG into ./plots) ---
python visualize.py                         :: yields/breakeven/repo per tenor + coverage + returns
python visualize.py returns                 :: cumulative financed-return charts
python visualize.py 10y                     :: just the 10y yield panel
python visualize.py coverage                :: data-coverage / health chart
```

Typical first run:
```bat
python auctions.py pull && python data_layer.py universe && python data_layer.py macro && python data_layer.py bonds
python visualize.py
```

## Files

- `bbg.py` — Bloomberg DAPI connector (`reference()`, `history()`) + validated field map
- `auctions.py` — TreasuryDirect auction calendar + §9.2.1 OTR-schedule reconstruction
- `data_layer.py` — pulls/caches macro + per-bond series; `index_ratio()` (§2.1, validated)
- `pricing.py` — DCF pricing + DV01 (vectorized per bond; validated vs BBG)
- `financing.py` — repo financing with a tunable bid/offer half-spread `x` (the slippage knob)
- `engine.py` — financed breakeven TR engine -> `cache/returns_<tenor>.parquet`; `validate`
  (splice/day-count/pairing checks), `window`, and `refresh()` (data update + rebuild) entry points
- `export.py` — full day-level data dump to Excel/CSV for hand-replication (auto-refreshes first)
- `dashboard.py` — self-contained interactive HTML dashboard (Plotly + JS; instant, offline; auto-refreshes first)
- `visualize.py` — OTR-spliced charts with auction markers + returns chart (static PNGs)
- `cache/` — parquet caches (git-ignored, regenerable)
- `plots/` — generated PNGs (git-ignored)

## Seasonal / auction-cycle analysis

A pure **aggregation layer** over the engine's existing daily P&L (it does *not* recompute
returns): sum the daily bp into auction-anchored within-month buckets, stack across years with
the **median**. Built by `engine.seasonal_table` → `cache/seasonal.parquet`; surfaced in the
dashboard's **Seasonal** view. `python engine.py seasonal` builds the table and prints QA.

**Analysis window:** all output series (returns, seasonal, export, dashboard) start **2011-01-01**
(`engine.ANALYSIS_START`) — the first year after the 2006–2010 twin-auction structure, so every
month has exactly one real TIPS auction. Contingency/test auctions (offering < $1bn, e.g. the
2020-07-10 $25mn 5y) are excluded everywhere via `auctions.real_tips_auctions`
(`MIN_AUCTION_SIZE`), so the monthly anchor is never ambiguous — no two-auction tie-break needed.

**Shared monthly auction calendar** — every calendar month carries exactly one TIPS auction (the
tenor rotates Jan 10y, Feb 30y, Mar 10y, Apr 5y, … Dec 5y); that single auction anchors **all
three** tenor series ("how each tenor trades around the monthly TIPS supply event"). Five
trading-day anchors per month M: `A0` last TD of M−1, `A1` prev-TD on/before (auction−7d), `A2`
auction, `A3` next-TD on/after (auction+7d), `A4` last TD of M. Four half-open buckets, boundary
day to the left:

| Period | Span (open, close] |
|---|---|
| P1 | A0 → A1  (month-start → auction−1w; long, low-signal by design) |
| P2 | A1 → A2  (auction−1w → auction; includes the auction day) |
| P3 | A2 → A3  (auction → auction+1w; strictly post-auction, starts T+1) |
| P4 | A3 → A4  (auction+1w → month-end) |

- **Clamps:** late auction (`A3 ≥ A4`) → `A3 = A4`, short P3 + **empty P4 (NaN)** (common —
  auctions sit ~day 15–24, so `auction+1w` often passes month-end). Early auction (`A1 ≤ A0`) →
  empty P1 (NaN); never fires in the 2011+ window (auctions are all back-half), kept as a guard.
- **Keystone table** (`engine.seasonal_table`): one tidy row per `(year, month, period, tenor)`
  with `tips_pnl, ust_pnl` (bucket-**summed** leg P&L), `tips_slip, ust_slip` (bucket-summed repo
  half-spread sensitivity, so any `(x_TIPS, x_UST)` nets client-side as `slip = x·Σs`),
  `trading_days`, `clamped`, `new_issue`. Every view — 48-bar seasonal, cumulative path, within-month
  signature, and all phase-2 groupings — is a group-by on this one table.
- **Metrics:** TIPS leg, Nominal (UST) leg, or **Breakeven** = `TIPS − β·UST` (β slider, default
  **100% = equal-DV01 / plain breakeven** baseline; lower β under-weights the UST leg). β is applied client-side per year-bucket
  before the median, so the slider is instant. Units are the engine's **bp** (= $/100k-DV01 P&L;
  ×$100k = dollars) — consistent with the Chart/Table views; the spec's "$/100k DV01" is the same
  series ×100,000.
- **Aggregation:** across years per `(month, period)` — shown as a **box plot** (box = 25–75th
  IQR, whiskers = 1.5×IQR, solid = median, dashed = **mean**, faint points = each year), plus the
  cumulative Σ-median path. Empty/clamped buckets drop out. The "within-month signature" pools
  each period across all months to isolate the pure auction cycle.

### Dashboard Seasonal sub-modes & filters

The Seasonal view has four sub-modes (toggle) and the shared filters below, all computed
client-side off the shipped `seas` table (so every control is instant). **Tenor is multi-select
only where overlay is supported** — the **Chart** (one cumulative long/short-BE line per tenor,
per-tenor totals across the top) and the seasonal **Calendar** (one median line per tenor). Every
other view is **single-tenor**: clicking a tenor switches to it, and switching into such a view
collapses the selection to the primary tenor (the label note reflects which mode you're in).

**Repo half-spread sliders apply everywhere.** `x_TIPS` and `x_UST` now net the financing cost off
**every** view — chart, table, and all six seasonal sub-modes — not just the chart. Each leg's cost
is `x · Σ(sensitivity)` over the bucket/day (TIPS by `x_TIPS`, UST by `x_UST`; the breakeven scales
the UST cost by β), subtracted as a cost in **both** long and short directions. Set both to 0 for
the mid (zero-spread) series. The seasonal-table-based views (Aggregate / History / Predict / Cumul)
use the bucket-summed `tips_slip / ust_slip`; the daily views (Calendar / Event) use daily `s_TIPS /
s_UST`. The chart's date-range presets are now **Full / 5Y / 3Y**, matching the seasonal windows.

**Position (Long / Short)** — a toggle shown on every cumulative view (all seasonal sub-modes +
the multi-tenor Chart). **Long** is the default; **Short** mirrors the metric so you can read the
short leg directly. For the seasonal metrics it is an exact sign flip (×−1). For the multi-tenor
Chart it switches each line from long-BE (`rBE − slip`) to true short-BE (`−rBE − slip`) — not an
exact mirror, because the repo half-spread is a cost both ways (same convention as the single-tenor
Chart's long/short lines). At `x_TIPS = x_UST = 0` the two coincide exactly. Titles append `— SHORT`.

- **Aggregate** — box-and-whisker per (month, period): box = IQR, whiskers = 1.5×IQR, solid =
  median, **bright-yellow tick = mean**, outlier years shown as points (hover → year + value);
  y-axis **rescaled to p1–p99** so the boxes fill the panel. + cumulative path + the within-month
  signature (per-period boxes; grouped median bars when comparing windows).
- **History** — each `(year, month, period)` bucket as a point over time (high-contrast lines,
  x = year), with period **checkboxes** + Month filter (e.g. P1 + all months = every P1 over time;
  P3 + Jan = each January's P3). Dashed line = median of the selection.
- **Calendar** — the **calendar effect**: median daily P&L by **business-day-of-month** (BDOM),
  *independent of the auction cycle* (the desk's point that the calendar effect now dominates).
  BDOM = a day's ordinal among its month's **trading** days, so the axis runs ~1–23 (not 1–31) and
  high BDOMs have lower `n` (months run 19–23 trading days). A **From start / From end** toggle
  flips the count: *from end* keys the last trading day as −1, −2, … so **month-ends align** across
  months — the clean way to read the turn-of-month effect (which otherwise smears at BDOM 20–23).
  Single tenor+window → **box plot per day** (box/median/mean tick, like Aggregate) + cumulative
  path; multiple **tenors** → one line per tenor; multiple **windows** → one line per window. Month
  filter to isolate one month.
- **Predict** — OLS regressions across months: **P1→P2, P2→P3, P3→P4, P2+P3→P4, (P1+P2)→P3,
  (P1+P2)→(P3+P4)**, the **cross-month P4→next-month P1** (filters anchor on the P4 month), and
  **month→next month** (each month's *total* P&L vs the next; adjacency follows the **filtered**
  set, so Issue=New gives Jan→Feb→Apr→Jul→Oct→…) — does early performance predict later? Table of
  slope / R² / corr / t-stat / n (|t|>2 flagged) + a scatter with the OLS line (pick the
  relationship at left). One observation per month; respects metric/β and all filters; multiple
  windows → a table block per window (degradation comparison). Linear OLS first; richer models later.
- **Cumul** — cumulative P&L holding only the **selected slice** (period checkboxes, combinable —
  e.g. P1+P2) each filtered month. The x-axis lists **only the kept months in sequence** (jumps
  over excluded ones — e.g. Issue=New + P1 = each new-issue month's P1: 2011-01, 2011-02, 2011-04,
  …; one tick per year), so it "rescales" to the relevant slices. Shows **metrics on the slice
  returns**: n, total, mean/slice, vol/slice, **Sharpe** (annualized by slice frequency), and
  **max drawdown**. The "what would this slice strategy have done" view; respects metric/β/window/issue/month.
  (Note: new-issue months are Jan/Feb/Apr/Jul 2011–18 and add Oct from ~2019, when the Oct 5y
  became a new issue — so the new-issue slice count steps 4→5/yr there; this is the real schedule.)
- **Event** — every auction **aligned at day 0** (the auction date), ±N business days. Each line =
  that auction's cumulative metric **rebased to 0 on the auction day** (so pre-auction is negative
  time, post is forward); thick **gold = median**, dotted **white = mean** across all filtered
  auctions. Pick a **Year** (its auctions drawn in colour vs the all-years median/mean) or "All"
  (every auction faint) to spot which cycles out/under-perform; **Window** slider sets ±N. Uses the
  shipped per-tenor daily series + auction dates; respects metric/β/issue/month.

Shared filters (apply to all sub-modes):
- **Metric** — TIPS leg, Nominal (UST) leg, or **Breakeven** = `TIPS − β·UST` (β slider, default
  **100% = equal-DV01 / plain DV01-matched** baseline). β applied per year-bucket *before* the median, so the
  slider is instant. Units = engine **bp** (= $/100k-DV01 P&L; ×$100k = dollars).
- **Sample window(s)** — **Full / 5Y / 3Y** as **checkboxes** (multi-select). Check several to
  **overlay/compare** them in the views where that's readable: the within-month **signature**
  (grouped bars per window), **Calendar** (a line per window), and **Predict** (a table block per
  window). The dense views (48-bar, History, Predict scatter) use the **longest** selected window.
  3Y is the degraded-signal check — the auction-cycle signal weakens markedly in recent windows
  while the calendar effect persists.
- **Issue type** — **All / New / Reopen** (an *auction-month* filter, **not** a bond filter). Each
  month's single anchoring TIPS auction is tagged `new_issue` vs reopening — new-issue months are
  **Jan (10y), Feb (30y), Apr (5y), Jul (10y), Oct (5y)**; the other 7 are reopenings. Because the
  anchor is the *shared* monthly auction, these are the **same calendar months for every tenor**
  (viewing 30y with Issue=New shows 30y returns in those 5 months). Derived from the `reopening`
  flag per year, so it tracks regime changes (the 5y Oct auction was a reopening pre-2019, a new
  issue after). All = New + Reopen = all 12 months. Composes with month/window/period — so
  "P2→P3 for new-issue months, last 5y" is just three toggles.
- **Months** — **multi-select checkboxes** (any subset of Jan–Dec) with quick-set buttons
  **All / H1 / H2 / None**; applies to History, Calendar, Predict, Cumul and Event. Pick e.g.
  Jan+Apr+Jul to condition on just those. Richer conditioning (rate/inflation regime, pre/post
  structural breaks, CPI-release alignment) still needs desk-defined cut points — TBD.

- **QA** (`python engine.py seasonal`): auction day-of-month range (early-clamp guard), seam
  continuity `A0[M] == A4[M−1]`, and clamp counts. Knobs: `engine.SEASONAL_WEEK` (±1-week span),
  `engine.ANALYSIS_START` (2011 cutoff), `auctions.MIN_AUCTION_SIZE` (contingency threshold), and
  `engine.tips_auction_calendar` (the anchor). Export integration is **phase 2**.

## Known limitations (by design, per desk)

- **Repo bid/offer = a tunable knob, not data.** No reliable historical repo bid/offer
  exists to pull (GCFRTSY is a traded mid with no bid/ask; USRG1T's bid/ask is a ~±50bp
  indicative band; bond-level RRA repo is entitlement-gated). So `financing.py` models it
  as a half-spread `x` (default 3bp): the long leg pays GC+x, the short leg earns GC−x.
  The engine builds BOTH a long-breakeven and a short-breakeven daily-return series per
  tenor, each carrying the slippage (they are not mirror images).
- **No specialness.** A one-sided drag on a short nominal; deliberately out of scope and
  not to be conflated with the symmetric bid/offer `x`.
- **CPI overrides.** Oct-2025 CPI-U NSA is the only month missing from Bloomberg
  (shutdown-delayed); hardcoded to Treasury's accrual value 325.604 in `data_layer.CPI_OVERRIDES`.
- **CPI is the revised print**, not as-first-published; immaterial for the index ratio
  (we use Treasury's published base CPI + the lagged formula) but flagged for rigor.
- **OTR pairing is maturity-matched.** Each month the nominal comparator is the note whose
  maturity is the closest most-recent match to the on-the-run TIPS, then held (5y Apr/Apr &
  Oct/Oct, 30y Feb/Feb, the staggered 10y cycle in reference.MD §9.2.1). Any residual 1–3
  month gap is absorbed by DV01 weighting. `python engine.py validate` (check C) asserts the
  held pairing matches this table for the current auction regime; pre-regime history (5y was
  annual-April before ~2019; 10y's 2003 startup) is reported, not failed.
