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
                                            ::   controls: tenor, repo x_TIPS/x_UST sliders, date range
                                            ::   (Full/5y/1y/YTD), chart|table, monthly|daily, Download-CSV
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
