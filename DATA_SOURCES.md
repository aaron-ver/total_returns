# Data sources & pipeline map

Every data input, how it's sourced, and whether it can move to Redshift (server-side BBG) or must
stay a scraper / manual upload. This is the map for automating the daily refresh (see `pipeline.py`).

**Ingestion classes**
- **BBG‑DAPI** — Bloomberg *desktop* API; needs a terminal. Runs on a terminal box today; **moves to Redshift** once BBG data lands there (then a headless cron can pull). Swap point: the data‑layer modules.
- **SCRAPER** — public API / file fetch + parse; cloud‑runnable now (Lambda/Fargate cron). Never in Redshift.
- **MANUAL** — a person exports/sources it (Bloomberg screen export, desk report, DMO PDF). Lands in a repo folder / S3 "inbox". Never in Redshift.

## Inputs

| # | Data | Class | Script | Output | Redshift? |
|---|------|-------|--------|--------|-----------|
| 1 | US TIPS + UST px/yield, static | BBG‑DAPI | `data_layer.py` | `cache/` | ✅ eligible |
| 2 | US CPI (macro) | BBG‑DAPI | `data_layer.py` | `cache/macro*` | ✅ eligible |
| 3 | US GC repo (GCFRTSY / fed funds) | BBG‑DAPI | `data_layer.py` | `cache/` | ✅ eligible |
| 4 | US TIPS auction calendar | SCRAPER | `auctions.py` (TreasuryDirect API) | `cache/` | ❌ (public API) |
| 5 | RBOB gasoline XB1/XB2 | BBG‑DAPI | `energy.py` | `cache/energy_raw.parquet` | ✅ eligible |
| 6 | Crude Brent/WTI CO1/CO2/CL1/CL2 | BBG‑DAPI | `crude.py` | `cache/crude_raw_*.parquet` | ✅ eligible |
| 7 | Intl linker px/yield + static | BBG‑DAPI | `data_layer_intl.py` | `cache_intl/{daily,static}` | ✅ eligible |
| 8 | Intl reference indices (euro HICPxt `CPTFEMU`, FR CPIxt `FRCPXTOB`, UK RPI `UKRPI`) | BBG‑DAPI | `data_layer_intl.py` | `cache_intl/macro*` | ⚠️ **verify** — niche index series; may not be in Redshift |
| 9 | Intl GC repo (RepoFunds per country, €STR/EONIA, SONIA) | BBG‑DAPI | `data_layer_intl.py` | `cache_intl/` | ⚠️ **verify** — niche |
| 10 | **Nominal‑hedge universe (ISIN list)** | **MANUAL** | Bloomberg Security Finder export → `nominal_universe/` → `nominals_intl.py import` | `cache_intl/nominal_universe.csv` | ❌ (screen export; save as CSV/xlsx) |
| 11 | Nominal‑hedge px/static (from #10 ISINs) | BBG‑DAPI | `nominals_intl.py pull` | `cache_intl/{daily,static}` | ✅ eligible (once ISINs known) |
| 12 | **Street breakeven map** (real→nominal comparator + betas) | **MANUAL** | desk reports `europe_isins.xlsx`,`uk_isins.xlsx` → `breakeven_intl.py import` | `breakeven_map.csv` | ❌ (desk file) |
| 13 | FR auction/tap history | MANUAL | AFT (BOE‑sourced) | `cache_intl/auctions_raw/FR.csv` | ❌ |
| 14 | ES auction/tap history | MANUAL | Banco de España / BOE | `cache_intl/auctions_raw/ES.csv` | ❌ |
| 15 | UK gilt auction history | MANUAL+SCRAPER | DMO **D5D PDFs** → `gilt_issuance/` → `auctions_intl.py uk_d5d` | `cache_intl/auctions_raw/GB.csv` | ❌ (PDF parse) |
| 16 | IT auction/tap history | MANUAL | Banca d'Italia *storico aste* | `cache_intl/auctions_raw/IT.csv` | ❌ |

## Build → artifacts (all cloud‑runnable from the caches; no terminal)

`auctions_intl.build` → `cache_intl/auctions.parquet` · `engine_intl.build_all` → `cache_intl/returns/` ·
`breakeven_intl.build_all` → `cache_intl/breakeven/` · `cmt_intl.build_all` → `cache_intl/cmt/` ·
`seasonal_intl`, `energy_intl`, `hedge`, `export`, `issuance_intl`, `buckets_intl`.

**Curated marts** (`marts.py` → `marts/*.{parquet,csv}`, DB‑ready): `mart_cmt`, `mart_hedge`,
`mart_auctions`, `mart_bonds`. **Dashboards**: `dashboard.html`, `dashboard_intl.html`.

## Orchestration
`pipeline.py` runs PULL → BUILD → EXPORT → RENDER → ALERTS (isolated, timed). `--no-pull` rebuilds
from cache (no terminal). Later: EventBridge cron → Fargate for BUILD/EXPORT/RENDER; the terminal box
(or Redshift) feeds PULL; a Lambda runs `alerts.py`.

## Migration notes (when AWS/Redshift land)
- Point the **BBG‑DAPI** modules (rows 1‑3,5‑9,11) at a Redshift adapter — one seam per data‑layer module. **Verify rows 8‑9** are actually in the Redshift BBG feed; if not they stay DAPI/manual.
- Rows 4,15 are already parse‑from‑source (cloud‑runnable). Rows 10,12‑16 stay **manual → S3 inbox**.
- Swap local artifact paths for `s3://` in one `write_artifact()` helper; date‑partition drops for reproducibility/audit.
