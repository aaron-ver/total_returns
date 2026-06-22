# TIPS / Breakeven Total-Return Build

Daily financed total-return series for on-the-run US TIPS (5y/10y/30y) and a parallel
breakeven series. Spec: [reference.MD](reference.MD).

This repo currently contains the **data layer** (pulling/caching raw inputs) plus an
**auction calendar** and a **baseline visualizer**. The return math (the financed-P&L
engine in reference.MD §5–§9) is the next build and is **not** done yet.

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
python data_layer.py status                :: what's cached
python data_layer.py preview               :: CPI publications (monthly, m/m + y/y) + GC repo + universe
python data_layer.py preview 36            :: same, but last 36 CPI months
python data_layer.py preview 91282CPU9 40  :: one bond's static + daily (40 rows)

:: --- auction calendar / OTR schedule ---
python auctions.py pull                     :: fetch + cache the Treasury auction calendar
python auctions.py schedule                 :: build + print the monthly OTR schedule

:: --- baseline visualizer (PNG into ./plots) ---
python visualize.py                         :: real/nominal yields, breakeven, GC repo per tenor + coverage
python visualize.py 10y                     :: just the 10y panel
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
- `visualize.py` — OTR-spliced charts with auction markers
- `cache/` — parquet caches (git-ignored, regenerable)
- `plots/` — generated PNGs (git-ignored)

## Known limitations (by design, per desk)

- **No specialness / no repo bid-offer.** Both legs finance at GC (`GCFRTSY`). Net
  financing on a breakeven ≈ 0. Analyses should discount some slippage.
- **CPI is the revised print**, not as-first-published; immaterial for the index ratio
  (we use Treasury's published base CPI + the lagged formula) but flagged for rigor.
- **OTR pairing** uses same-tenor nominal vs TIPS; a 1–3 month maturity gap is accepted
  (reference.MD §9.2.1) and absorbed by DV01 weighting.
