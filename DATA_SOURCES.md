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

## Redshift verification — DRD `rome-prod` / `drd` DB (checked 2026‑07 via `redshift_intl.py coverage`/`hunt`)
**Verdict: DRD Redshift is an *equities* warehouse. The core linker feeds are NOT in it, so the Bloomberg terminal box stays the pull node.**

| Our feed | In Redshift? | Table | Notes |
|---|---|---|---|
| Linker + nominal px/yield (rows 1,7,11) | ❌ **No** | — | `bloomberg_prices.prices` = 3.08B rows, **100% `market_sector_des='Equity'`**; 0/1028 of our ISINs present. Catalog‑wide scan of every `id_isin`/`yld_ytm` table shows **no sovereign cash‑bond price table** anywhere (only equities/ETF/risk‑model/CDS/options/preferreds). → **stays BBG‑DAPI (terminal).** |
| Inflation indices CPTFEMU/FRCPXTOB/UKRPI (rows 2,8) | ❌ **No** | — | `bloomberg_per_security.index_prices` is a curated commodity/index subscription list (HO1, SM1, HRC2, …) — **no CPI/HICP/RPI**. `index_prices_historical` + `bloomberg_fi_indices` are permission‑denied; even if granted, these are 3 monthly series → keep on terminal. |
| Bond static (coupon/maturity/dated) | ❌ **No** | — | Only equity secmasters + `golden_copy` (identifiers only, no coupon/maturity). → stays BBG‑DAPI (already cached, changes only on new issues). |
| GC financing €STR/SONIA/EONIA (rows 3,9) | ✅ **Yes** | `bloomberg_fixings.fixings` | Names `Interest:Fixing:EUR:ESTR:1D`, `…:GBP:SONIA:1D`, `…:EUR:EONIA:1D`, `closename='Official'`. No secured GC‑pool/RepoFunds (we already fall back to €STR/SONIA). |
| Crude/RBOB futures (rows 5,6) | ✅ **Yes (dated only)** | `bloomberg_futures_prices.futures_prices_{nonshare,share}` | No BBG generics (CL1/CO1/XB1). WTI `CL*` + RBOB `XB*` clean in `nonshare`; Brent `CO*` only in `share` in Refinitiv‑RIC form (`COM7=Z0`). Would need front‑month roll reconstructed from dated contracts via `bloomberg_futures.futures` metadata. |

**Consequence:** a fully headless cloud cron pulling everything from Redshift is **not achievable** — the backbone (sovereign linker/nominal prices) isn't there. The terminal box must run the daily pull regardless. Redshift's realistic role: optional **secondary/backfill source** for fixings + energy only.

## Migration notes (when AWS/Redshift land)
- ~~Point the BBG‑DAPI modules at a Redshift adapter~~ — **superseded by the verification above.** Only rows 3/9 (fixings) and 5/6 (crude/RBOB, with roll reconstruction) are sourceable from DRD; rows 1‑2,7,8,11 + static stay BBG‑DAPI on the terminal box.
- **Automate the pull on the terminal box** (Windows Task Scheduler → `pipeline.py --push`), not a headless Lambda. Everything downstream of PULL (build/export/render/push) is already cloud‑runnable from cache.
- Rows 4,15 are already parse‑from‑source (cloud‑runnable). Rows 10,12‑16 stay **manual → S3 inbox**.
- Swap local artifact paths for `s3://` in one `write_artifact()` helper; date‑partition drops for reproducibility/audit.
