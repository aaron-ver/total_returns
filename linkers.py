"""
European & UK inflation-linked bond universe + conventions (reference_intl.MD).

This module plays the combined role that data_layer.SEED_UNIVERSE + auctions.py play for
the US build: it is the single source of truth for
  * WHICH non-US linkers exist (the universe, by ISIN), and
  * the per-market CONVENTIONS the engine must apply (index, lag, coupon freq, settlement
    calendar/lag, deflation floor, quote convention, financing curve).

Unlike the US (TreasuryDirect's free auction API) there is no single public European auction
feed, and the desk instruction is "pull with bbg". So the universe is a CURATED ISIN list
(SEED_UNIVERSE, the backbone) that is ENRICHED/validated from Bloomberg static fields, with the
per-tap auction/syndication calendar collected from Bloomberg + the DMO results files. See
reference_intl.MD §9-§10.

Usage:
  python linkers.py markets                 # print the per-market convention matrix
  python linkers.py universe                # build cache_intl/universe.csv from SEED_UNIVERSE
  python linkers.py enrich                   # pull Bloomberg static for every ISIN -> confirm/fill
  python linkers.py status                   # what's cached
"""
from __future__ import annotations
import os, sys, csv
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache_intl")            # kept separate from the US ./cache
UNIVERSE_CSV = os.path.join(CACHE, "universe.csv")

# Bloomberg security suffix for sovereigns (US build used "<cusip> Govt"; ISIN works the same).
BBG_SUFFIX = "Govt"


# ===========================================================================================
# Reference inflation indices (monthly NSA levels). Tickers are best-known Bloomberg tickers --
# VERIFY each on the terminal on the first pull (the US field map was likewise validated live).
# The index used for bond indexation is the UNREVISED, first-published number (reference_intl §4).
# ===========================================================================================
REF_INDEX = {
    "EUR_HICPXT": dict(ticker="CPTFEMU Index",  desc="Eurozone HICP ex-tobacco NSA (Eurostat)"),
    "FR_CPIXT":   dict(ticker="FRCPXTOB Index",  desc="France CPI ex-tobacco NSA (INSEE)"),
    "UK_RPI":     dict(ticker="UKRPI Index",     desc="UK Retail Prices Index NSA (ONS)"),
    "IT_FOIXT":   dict(ticker="ITCPIUNR Index",  desc="Italy FOI ex-tobacco NSA (ISTAT) — for BTP Italia (deferred); VERIFY"),
}

# Local GC financing (reference_intl §6; bonds finance in own-country GC, no specialness, local ccy).
# Core vs PERIPHERAL euro GC = CME RepoFunds Rate (RFR), country-specific sovereign repo off
# BrokerTec/MTS (Bloomberg page REPF <GO>). Indicative GC vs €STR: DE ≈ −40bp, FR ≈ −56bp,
# ES ≈ −9bp, IT ≈ +9bp -- i.e. core funds rich (special), periphery cheaper. RFR tickers are left
# `None` to fill from REPF (guessing risks silently pulling the WRONG security); until filled,
# financing_series() falls back to the cached €STR/SONIA single rate, so the build still runs.
# Alternative single secured euro GC: STOXX GC Pooling EUR ON = "GCPION Index" (set a market's
# repo to "gcpool" to use it). See reference_intl §6 for the menu.
FINANCING = {
    "estr_gc":  dict(ticker="ESTRON Index",  ccy="EUR", desc="€STR unsecured O/N — euro fallback (from Oct-2019)"),
    "eonia":    dict(ticker="EONIA Index",   ccy="EUR", desc="EONIA O/N — pre-Oct-2019 euro extension (≈€STR+8.5bp)"),
    "sonia_gc": dict(ticker="SONIO/N Index", ccy="GBP", desc="SONIA O/N — gilt GC proxy (back to ~1997)"),
    "gcpool":   dict(ticker="GCPION Index",  ccy="EUR", desc="STOXX GC Pooling EUR ON — secured euro GC (single rate)"),
    "rfr_de":   dict(ticker=None, ccy="EUR", desc="RepoFunds Rate Germany — core GC (fill ticker from REPF)"),
    "rfr_fr":   dict(ticker=None, ccy="EUR", desc="RepoFunds Rate France — core GC (fill ticker from REPF)"),
    "rfr_it":   dict(ticker=None, ccy="EUR", desc="RepoFunds Rate Italy — peripheral GC (fill ticker from REPF)"),
    "rfr_es":   dict(ticker=None, ccy="EUR", desc="RepoFunds Rate Spain — peripheral GC (fill ticker from REPF)"),
}
EUR_FALLBACK_REPO = "estr_gc"
GBP_FALLBACK_REPO = "sonia_gc"

# Settlement calendars (for business-day rolling, settlement lag, month-end anchor). The engine
# builds a holiday-aware calendar per id (reference_intl §1). 'TARGET2' = euro; 'UK' = gilt.
CALENDARS = {"TARGET2": "Eurozone TARGET2 settlement calendar",
             "UK": "UK gilt market calendar"}


# ===========================================================================================
# Per-market conventions. A "market" = (country, program) because France runs two curves and
# Italy/UK run two instrument types. `deferred=True` markets are catalogued but NOT run in
# Phase 1 (BTP Italia pay-go/par-reset; UK old-style 8m-lag cash bonds) -- reference_intl §5,§7.
# ===========================================================================================
MARKETS = {
    "FR_OATEI": dict(country="FR", program="OAT€i",        index="EUR_HICPXT", freq=1, settle_lag=2,
                     calendar="TARGET2", floor=True,  quote="real",      ccy="EUR", repo="estr_gc",
                     lag_months=3, interp=True),
    "FR_OATI":  dict(country="FR", program="OATi",         index="FR_CPIXT",   freq=1, settle_lag=2,
                     calendar="TARGET2", floor=True,  quote="real",      ccy="EUR", repo="estr_gc",
                     lag_months=3, interp=True),
    "IT_BTPEI": dict(country="IT", program="BTP€i",        index="EUR_HICPXT", freq=2, settle_lag=2,
                     calendar="TARGET2", floor=True,  quote="real",      ccy="EUR", repo="estr_gc",
                     lag_months=3, interp=True),
    "ES_EI":    dict(country="ES", program="Bonos/Oblig€i",index="EUR_HICPXT", freq=1, settle_lag=2,
                     calendar="TARGET2", floor=True,  quote="real",      ccy="EUR", repo="estr_gc",
                     lag_months=3, interp=True),
    "DE_EI":    dict(country="DE", program="Bund/Bobl€i",  index="EUR_HICPXT", freq=1, settle_lag=2,
                     calendar="TARGET2", floor=True,  quote="real",      ccy="EUR", repo="estr_gc",
                     lag_months=3, interp=True, status="runoff_2024"),   # no new issuance from 2024
    "UK_3M":    dict(country="GB", program="UKTi (3m-lag)",index="UK_RPI",     freq=2, settle_lag=1,
                     calendar="UK",      floor=False, quote="real",      ccy="GBP", repo="sonia_gc",
                     lag_months=3, interp=True),
    # --- deferred (structurally different; catalogued, not run in Phase 1) ---
    "UK_8M":    dict(country="GB", program="UKTi (8m-lag)",index="UK_RPI",     freq=2, settle_lag=1,
                     calendar="UK",      floor=False, quote="nominal",   ccy="GBP", repo="sonia_gc",
                     lag_months=8, interp=False, deferred=True),
    "IT_ITALIA":dict(country="IT", program="BTP Italia",   index="IT_FOIXT",   freq=2, settle_lag=2,
                     calendar="TARGET2", floor=True,  quote="par_reset", ccy="EUR", repo="estr_gc",
                     lag_months=3, interp=True, deferred=True),
}


def conv(market):
    """Conventions dict for a market code (e.g. 'IT_BTPEI'). Raises on unknown code."""
    if market not in MARKETS:
        raise KeyError(f"unknown market {market!r}; known: {sorted(MARKETS)}")
    return MARKETS[market]


def is_deferred(market):
    return bool(MARKETS.get(market, {}).get("deferred"))


def active_markets():
    """Market codes that Phase 1 actually runs (excludes the deferred special structures)."""
    return [m for m in MARKETS if not is_deferred(m)]


# Own-country GC repo per country (reference_intl §6): core (DE/FR) vs peripheral (IT/ES) euro GC via
# CME RepoFunds Rate; gilts via SONIA. Applied to the active markets (deferred keep their default).
REPO_BY_COUNTRY = {"FR": "rfr_fr", "DE": "rfr_de", "IT": "rfr_it", "ES": "rfr_es", "GB": "sonia_gc"}
for _m, _c in MARKETS.items():
    if not _c.get("deferred"):
        _c["repo"] = REPO_BY_COUNTRY.get(_c["country"], _c.get("repo"))


# Nominal-government-bond conventions, by country, for the breakeven hedge leg (reference_intl §8.1).
# Same machinery as a linker but index=None (no inflation uplift, IR=1) and no floor; coupon
# frequency is read from each bond's Bloomberg static (freq_default is the country norm if missing):
# nominal OAT/Bund/Bono pay ANNUAL, BTP/gilt pay SEMIANNUAL. Financed in the same own-country GC.
NOMINAL_MARKETS = {
    "FR": dict(calendar="TARGET2", settle_lag=2, repo="rfr_fr",   freq_default=1),
    "DE": dict(calendar="TARGET2", settle_lag=2, repo="rfr_de",   freq_default=1),
    "IT": dict(calendar="TARGET2", settle_lag=2, repo="rfr_it",   freq_default=2),
    "ES": dict(calendar="TARGET2", settle_lag=2, repo="rfr_es",   freq_default=1),
    "GB": dict(calendar="UK",      settle_lag=1, repo="sonia_gc", freq_default=2),
}


def nominal_conv(country):
    """Conventions dict for a NOMINAL government bond used as a breakeven hedge leg (index=None)."""
    n = NOMINAL_MARKETS.get(country)
    if n is None:
        raise KeyError(f"no nominal conventions for country {country!r}; known: {sorted(NOMINAL_MARKETS)}")
    return dict(country=country, index=None, freq=None, floor=False, lag_months=3, interp=True, **n)


def country_of_isin(isin):
    """Best-effort issuer country from an ISIN prefix (BBG static COUNTRY is the authoritative check)."""
    return {"FR": "FR", "IT": "IT", "ES": "ES", "DE": "DE", "GB": "GB"}.get(str(isin)[:2])


# ===========================================================================================
# SEED UNIVERSE -- the curated backbone (one row per BOND/ISIN). Compiled from DMO sources
# (AFT, MEF, Tesoro, Finanzagentur, UK DMO); see reference_intl.MD §10 and the research note.
# Treat coupon/maturity/issue as indicative-as-of-mid-2026 and CONFIRM via enrich() on the
# terminal. The engine reads the Bloomberg-confirmed static, not these literals -- these exist
# so the pull knows WHICH bonds to fetch (and as a cross-check on the confirmed values).
#
# Columns: isin, market, cpn, maturity (YYYY-MM-DD), first_issue (YYYY-MM-DD or ""), desc
# ===========================================================================================
SEED_UNIVERSE = [
    # isin,            market,     cpn,    maturity,     first_issue,   desc
    # ---- France OAT€i (euro HICPxt, annual) ----
    ("FR0011008705", "FR_OATEI", 1.85, "2027-07-25", "2010-07-25", "OATEI 1.85 07/25/27"),
    ("FR0013410552", "FR_OATEI", 0.10, "2029-03-01", "2019-03-01", "OATEI 0.1 03/01/29"),
    ("FR0011982776", "FR_OATEI", 0.70, "2030-07-25", "2014-06-11", "OATEI 0.7 07/25/30"),
    ("FR0014001N38", "FR_OATEI", 0.10, "2031-07-25", "2020-07-25", "OATEI 0.1 07/25/31"),
    ("FR0000188799", "FR_OATEI", 3.15, "2032-07-25", "2002-10-25", "OATEI 3.15 07/25/32"),
    ("FR001400JI88", "FR_OATEI", 0.60, "2034-07-25", "2022-07-25", "OATEI 0.6 07/25/34"),
    ("FR0013327491", "FR_OATEI", 0.10, "2036-07-25", "2018-03-28", "OATEI 0.1 07/25/36"),
    ("FR001400AQH0", "FR_OATEI", 0.10, "2038-07-25", "2021-07-25", "GREEN OATEI 0.1 07/25/38"),
    ("FR0010447367", "FR_OATEI", 1.80, "2040-07-25", "2007-03-07", "OATEI 1.8 07/25/40"),
    ("FR001400QCA1", "FR_OATEI", 0.95, "2043-07-25", "2023-07-25", "OATEI 0.95 07/25/43"),
    ("FR0013209871", "FR_OATEI", 0.10, "2047-07-25", "2016-09-28", "OATEI 0.1 07/25/47"),
    ("FR0014008181", "FR_OATEI", 0.10, "2053-07-25", "2021-07-25", "OATEI 0.1 07/25/53"),
    # ---- France OATi (French CPIxt, annual) ----
    ("FR0013238268", "FR_OATI",  0.10, "2028-03-01", "2016-03-01", "OATI 0.1 03/01/28"),
    ("FR0000186413", "FR_OATI",  3.40, "2029-07-25", "1999-07-25", "OATI 3.4 07/25/29"),
    ("FR0014003N51", "FR_OATI",  0.10, "2032-03-01", "2021-03-01", "OATI 0.1 03/01/32"),
    ("FR0013524014", "FR_OATI",  0.10, "2036-03-01", "2020-03-01", "OATI 0.1 03/01/36"),
    ("FR001400IKW5", "FR_OATI",  0.55, "2039-03-01", "2023-03-01", "OATI 0.55 03/01/39"),
    # ---- Italy BTP€i (euro HICPxt, SEMIANNUAL) ----
    ("IT0005415416", "IT_BTPEI", 0.65, "2026-05-15", "2020-06-29", "BTPEI 0.65 05/15/26"),
    ("IT0004735152", "IT_BTPEI", 3.10, "2026-09-15", "2011-03-15", "BTPEI 3.1 09/15/26"),
    ("IT0005246134", "IT_BTPEI", 1.30, "2028-05-15", "2016-11-15", "BTPEI 1.3 05/15/28"),
    ("IT0005543803", "IT_BTPEI", 1.50, "2029-05-15", "2023-04-26", "BTPEI 1.5 05/15/29"),
    ("IT0005387052", "IT_BTPEI", 0.40, "2030-05-15", "2019-05-15", "BTPEI 0.4 05/15/30"),
    ("IT0005657348", "IT_BTPEI", 1.10, "2031-08-15", "2025-06-27", "BTPEI 1.1 08/15/31"),
    ("IT0005138828", "IT_BTPEI", 1.25, "2032-09-15", "2015-09-15", "BTPEI 1.25 09/15/32"),
    ("IT0005482994", "IT_BTPEI", 0.10, "2033-05-15", "2021-11-15", "BTPEI 0.1 05/15/33"),
    ("IT0003745541", "IT_BTPEI", 2.35, "2035-09-15", "2004-09-15", "BTPEI 2.35 09/15/35"),
    ("IT0005588881", "IT_BTPEI", 1.80, "2036-05-15", "2023-11-15", "BTPEI 1.8 05/15/36"),
    ("IT0005547812", "IT_BTPEI", 2.40, "2039-05-15", "2023-05-15", "BTPEI 2.4 05/15/39"),
    ("IT0004545890", "IT_BTPEI", 2.55, "2041-09-15", "2009-09-15", "BTPEI 2.55 09/15/41"),
    ("IT0005706293", "IT_BTPEI", 2.25, "2046-02-15", "",           "BTPEI 2.25 02/15/46"),
    ("IT0005436701", "IT_BTPEI", 0.15, "2051-05-15", "2020-11-15", "BTPEI 0.15 05/15/51"),
    ("IT0005647273", "IT_BTPEI", 2.55, "2056-05-15", "2024-11-15", "BTPEI 2.55 05/15/56"),
    # ---- Spain Bonos/Obligaciones€i (euro HICPxt, annual); *48/*49/*50 are non-traded private placements ----
    ("ES00000128S2", "ES_EI",    0.65, "2027-11-30", "2017-05-01", "SPGBEI 0.65 11/30/27"),
    ("ES00000127C8", "ES_EI",    1.00, "2030-11-30", "2015-03-01", "SPGBEI 1.0 11/30/30"),
    ("ES0000012C12", "ES_EI",    0.70, "2033-11-30", "2018-09-01", "SPGBEI 0.7 11/30/33"),
    ("ES0000012O18", "ES_EI",    1.15, "2036-11-30", "2024-02-01", "SPGBEI 1.15 11/30/36"),
    ("ES0000012M69", "ES_EI",    2.05, "2039-11-30", "2023-07-01", "SPGBEI 2.05 11/30/39"),
    ("ES0000012F19", "ES_EI",    1.00, "2048-11-30", "2019-06-01", "SPGBEI 1.0 11/30/48 (priv. placement)"),
    ("ES0000012F27", "ES_EI",    1.05, "2049-11-30", "2019-06-01", "SPGBEI 1.05 11/30/49 (priv. placement)"),
    ("ES0000012F35", "ES_EI",    1.10, "2050-11-30", "2019-06-01", "SPGBEI 1.1 11/30/50 (priv. placement)"),
    # ---- Germany Bund€i (euro HICPxt, annual); issuance ENDED 2024 ----
    ("DE0001030559", "DE_EI",    0.50, "2030-04-15", "2014-04-08", "DBRI 0.5 04/15/30"),
    ("DE0001030583", "DE_EI",    0.10, "2033-04-15", "2021-02-09", "DBRI 0.1 04/15/33"),
    ("DE0001030575", "DE_EI",    0.10, "2046-04-15", "2015-06-09", "DBRI 0.1 04/15/46"),
    # ---- UK new-style index-linked gilts (RPI, SEMIANNUAL, 3m lag, NO floor); issue dates from BBG ----
    ("GB00B128DH60", "UK_3M",    1.250, "2027-11-22", "", "UKTI 1.25 11/22/27"),
    ("GB00BZ1NTB69", "UK_3M",    0.125, "2028-08-10", "", "UKTI 0.125 08/10/28"),
    ("GB00B3Y1JG82", "UK_3M",    0.125, "2029-03-22", "", "UKTI 0.125 03/22/29"),
    ("GB00BNNGP551", "UK_3M",    0.125, "2031-08-10", "", "UKTI 0.125 08/10/31"),
    ("GB00B3D4VD98", "UK_3M",    1.250, "2032-11-22", "", "UKTI 1.25 11/22/32"),
    ("GB00BMF9LJ15", "UK_3M",    0.750, "2033-11-22", "", "UKTI 0.75 11/22/33"),
    ("GB00B46CGH68", "UK_3M",    0.750, "2034-03-22", "", "UKTI 0.75 03/22/34"),
    ("GB00BT7HZZ68", "UK_3M",    1.125, "2035-09-22", "", "UKTI 1.125 09/22/35"),
    ("GB00BYZW3J87", "UK_3M",    0.125, "2036-11-22", "", "UKTI 0.125 11/22/36"),
    ("GB00B1L6W962", "UK_3M",    1.125, "2037-11-22", "", "UKTI 1.125 11/22/37"),
    ("GB00BMY62Z61", "UK_3M",    1.750, "2038-09-22", "", "UKTI 1.75 09/22/38"),
    ("GB00BLH38265", "UK_3M",    0.125, "2039-03-22", "", "UKTI 0.125 03/22/39"),
    ("GB00B3LZBF68", "UK_3M",    0.625, "2040-03-22", "", "UKTI 0.625 03/22/40"),
    ("GB00BGDYHF49", "UK_3M",    0.125, "2041-08-10", "", "UKTI 0.125 08/10/41"),
    ("GB00B3MYD345", "UK_3M",    0.625, "2042-11-22", "", "UKTI 0.625 11/22/42"),
    ("GB00B7RN0G65", "UK_3M",    0.125, "2044-03-22", "", "UKTI 0.125 03/22/44"),
    ("GB00BMF9LH90", "UK_3M",    0.625, "2045-03-22", "", "UKTI 0.625 03/22/45"),
    ("GB00BYMWG366", "UK_3M",    0.125, "2046-03-22", "", "UKTI 0.125 03/22/46"),
    ("GB00B24FFM16", "UK_3M",    0.750, "2047-11-22", "", "UKTI 0.75 11/22/47"),
    ("GB00BZ13DV40", "UK_3M",    0.125, "2048-08-10", "", "UKTI 0.125 08/10/48"),
    ("GB00BT7J0134", "UK_3M",    1.875, "2049-09-22", "", "UKTI 1.875 09/22/49"),
    ("GB00B421JZ66", "UK_3M",    0.500, "2050-03-22", "", "UKTI 0.5 03/22/50"),
    ("GB00BNNGP882", "UK_3M",    0.125, "2051-03-22", "", "UKTI 0.125 03/22/51"),
    ("GB00B73ZYW09", "UK_3M",    0.250, "2052-03-22", "", "UKTI 0.25 03/22/52"),
    ("GB00BPSNBG80", "UK_3M",    1.250, "2054-11-22", "", "UKTI 1.25 11/22/54"),
    ("GB00B0CNHZ09", "UK_3M",    1.250, "2055-11-22", "", "UKTI 1.25 11/22/55"),
    ("GB00BYVP4K94", "UK_3M",    0.125, "2056-11-22", "", "UKTI 0.125 11/22/56"),
    ("GB00BP9DLZ64", "UK_3M",    0.125, "2058-03-22", "", "UKTI 0.125 03/22/58"),
    ("GB00B4PTCY75", "UK_3M",    0.375, "2062-03-22", "", "UKTI 0.375 03/22/62"),
    ("GB00BD9MZZ71", "UK_3M",    0.125, "2065-11-22", "", "UKTI 0.125 11/22/65"),
    ("GB00BDX8CX86", "UK_3M",    0.125, "2068-03-22", "", "UKTI 0.125 03/22/68"),
    ("GB00BM8Z2W66", "UK_3M",    0.125, "2073-03-22", "", "UKTI 0.125 03/22/73"),
    # ====== DEFERRED (structurally different; catalogued, NOT run in Phase 1) ======
    # ---- UK old-style 8m-lag gilts (only TWO remain; the 2.5% IL 2024 matured Jul-2024) ----
    ("GB0008932666", "UK_8M",    4.125, "2030-07-22", "1992-06-12", "4.125% IL Treasury Stock 2030 (8m-lag)"),
    ("GB0031790826", "UK_8M",    2.000, "2035-01-26", "2002-07-11", "2% IL Treasury Stock 2035 (8m-lag)"),
    # ---- Italy BTP Italia (domestic FOIxt, retail, pay-go + par reset) ----
    ("IT0005388175", "IT_ITALIA", 0.65, "2027-10-28", "2019-10-28", "BTP Italia 0.65 10/28/27"),
    ("IT0005532723", "IT_ITALIA", 2.00, "2028-03-14", "2023-03-14", "BTP Italia 2.0 03/14/28"),
    ("IT0005517195", "IT_ITALIA", 1.60, "2028-11-22", "2022-11-22", "BTP Italia 1.6 11/22/28"),
    ("IT0005497000", "IT_ITALIA", 1.60, "2030-06-28", "2022-06-28", "BTP Italia 1.6 06/28/30"),
    ("IT0005713547", "IT_ITALIA", 1.60, "2031-06-23", "2026-06-23", "BTP Italia 1.6 06/23/31"),
    ("IT0005648248", "IT_ITALIA", 1.85, "2032-06-04", "2025-06-04", "BTP Italia 1.85 06/04/32"),
]


# Bonds that exist but DON'T trade on the secondary market -> Bloomberg has no daily price series
# (returns 0 rows). Excluded from the active universe like the deferred markets; still catalogued.
# The Spanish 2048/2049/2050 are €430m private placements to a single investor, held to maturity.
NON_TRADED = {"ES0000012F19", "ES0000012F27", "ES0000012F35"}


def _ensure_dirs():
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(os.path.join(CACHE, "static"), exist_ok=True)
    os.makedirs(os.path.join(CACHE, "daily"), exist_ok=True)


def build_universe():
    """Write cache_intl/universe.csv from SEED_UNIVERSE (the curated backbone)."""
    _ensure_dirs()
    cols = ["isin", "market", "cpn", "maturity", "first_issue", "desc"]
    with open(UNIVERSE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols + ["country", "program", "index", "freq", "deferred"])
        for isin, market, cpn, mat, iss, desc in SEED_UNIVERSE:
            c = conv(market)
            excluded = bool(c.get("deferred")) or isin in NON_TRADED   # deferred structure OR non-traded
            w.writerow([isin, market, cpn, mat, iss, desc, c["country"], c["program"],
                        c["index"], c["freq"], excluded])
    print(f"  wrote {UNIVERSE_CSV}: {len(SEED_UNIVERSE)} bonds "
          f"({len(active_markets())} active markets, {len(MARKETS)} total)")
    return UNIVERSE_CSV


def load_universe(include_deferred=False):
    """The bond universe as a DataFrame. By default excludes the deferred special structures."""
    if not os.path.exists(UNIVERSE_CSV):
        build_universe()
    u = pd.read_csv(UNIVERSE_CSV, dtype={"isin": str, "market": str})
    if not include_deferred and "deferred" in u:
        u = u[~u["deferred"].astype(str).str.lower().isin(["true", "1"])]
    return u.reset_index(drop=True)


# --- Bloomberg static fields to confirm/enrich each bond (validated set; see data_layer_intl) ---
STATIC_FIELDS = ["SECURITY_DES", "ID_ISIN", "CRNCY", "COUNTRY", "CPN", "CPN_FREQ", "MATURITY",
                 "ISSUE_DT", "FIRST_SETTLE_DT", "INT_ACC_DT", "FIRST_CPN_DT", "BASE_CPI",
                 "REFERENCE_INDEX", "INFLATION_LINKED_INDICATOR", "DAY_CNT_DES", "CALC_TYP_DES",
                 "AMT_OUTSTANDING", "AMT_ISSUED", "ISSUE_PX", "FLT_DAYS_PRIOR"]


def enrich(write=True):
    """Pull Bloomberg static for every universe ISIN to CONFIRM coupon/maturity/base-CPI and fill
    amount outstanding etc. Writes one static parquet per ISIN (cache_intl/static/<isin>.parquet)
    and returns a summary frame comparing seed vs Bloomberg. Needs the Terminal."""
    import bbg
    _ensure_dirs()
    u = load_universe(include_deferred=True)
    secs = [f"{i} {BBG_SUFFIX}" for i in u["isin"]]
    bbg.open_session()
    try:
        st = {}
        for k in range(0, len(secs), 50):
            st.update(bbg.reference(secs[k:k + 50], STATIC_FIELDS))
    finally:
        bbg.close_session()
    rows = []
    for _, r in u.iterrows():
        sec = f"{r['isin']} {BBG_SUFFIX}"
        rec = st.get(sec, {})
        if write and rec:
            pd.DataFrame([{**{"isin": r["isin"], "market": r["market"]}, **rec}]).to_parquet(
                os.path.join(CACHE, "static", f"{r['isin']}.parquet"))
        rows.append({"isin": r["isin"], "market": r["market"],
                     "seed_cpn": r["cpn"], "bbg_cpn": rec.get("CPN"),
                     "seed_mat": r["maturity"], "bbg_mat": rec.get("MATURITY"),
                     "base_cpi": rec.get("BASE_CPI"), "amt_out": rec.get("AMT_OUTSTANDING"),
                     "des": rec.get("SECURITY_DES")})
    out = pd.DataFrame(rows)
    miss = out["bbg_cpn"].isna().sum()
    print(f"  enriched {len(out)} bonds; {miss} returned no static (check ISIN / '{BBG_SUFFIX}' suffix)")
    return out


def directory(write=True):
    """Human-readable ISIN dictionary for EVERY bond in the system (linkers + their nominal hedge
    bonds): ISIN -> description, coupon, maturity, country, role, and (for linkers) the paired
    nominal comparator. Sources: universe.csv (linkers) + breakeven_map.csv (nominals & pairing),
    upgraded with the Bloomberg static cache (authoritative coupon/maturity) where it's been pulled.
    Writes cache_intl/isin_directory.csv + ISIN_DIRECTORY.md. Re-run after pulling the nominals to
    fill their exact coupon/maturity."""
    rows, pair = {}, {}
    u = load_universe(include_deferred=True)
    for _, r in u.iterrows():
        rows[r["isin"]] = {"isin": r["isin"], "role": "linker", "country": r["country"],
                           "program": r["program"], "desc": r["desc"],
                           "coupon": r["cpn"], "maturity": r["maturity"], "comparator_isin": pd.NA}
    bm = os.path.join(HERE, "breakeven_map.csv")
    if os.path.exists(bm):
        m = pd.read_csv(bm, comment="#")
        for _, r in m.iterrows():
            note = str(r.get("note", "")); parts = note.split(" vs ", 1)
            if r["real_isin"] in rows:                       # tag the linker with its hedge
                rows[r["real_isin"]]["comparator_isin"] = r["nominal_isin"]
            ni = str(r["nominal_isin"])
            pair[ni] = parts[0] if parts else ""             # which linker this nominal hedges
            if ni not in rows:
                rows[ni] = {"isin": ni, "role": "nominal", "country": country_of_isin(ni),
                            "program": "nominal govt", "desc": (parts[1] if len(parts) > 1 else ni),
                            "coupon": pd.NA, "maturity": pd.NA, "comparator_isin": pd.NA}
    for isin, rec in rows.items():                            # upgrade from BBG static where present
        sp = os.path.join(CACHE, "static", f"{isin}.parquet")
        if os.path.exists(sp):
            s = pd.read_parquet(sp).iloc[0]
            if pd.notna(s.get("CPN")):
                rec["coupon"] = float(s["CPN"])
            if pd.notna(s.get("MATURITY")):
                rec["maturity"] = str(pd.Timestamp(s["MATURITY"]).date())
    df = pd.DataFrame(rows.values())
    df["maturity"] = df["maturity"].astype(str)
    df = df.sort_values(["country", "role", "maturity"]).reset_index(drop=True)
    if write:
        _ensure_dirs()
        df.to_csv(os.path.join(CACHE, "isin_directory.csv"), index=False)
        _write_directory_md(df, pair)
        print(f"  wrote cache_intl/isin_directory.csv + ISIN_DIRECTORY.md "
              f"({len(df)} ISINs: {(df.role=='linker').sum()} linkers, {(df.role=='nominal').sum()} nominals)")
    return df


def _write_directory_md(df, pair):
    """Readable markdown: breakeven pairs per country + a flat ISIN lookup."""
    cty_name = {"FR": "France", "IT": "Italy", "ES": "Spain", "DE": "Germany", "GB": "United Kingdom"}
    lines = ["# ISIN directory — European/UK linkers & breakeven hedge bonds", "",
             "Generated from `universe.csv` + `breakeven_map.csv` (+ Bloomberg static where pulled). "
             "Re-run `python linkers.py directory` to refresh.", ""]
    bm = os.path.join(HERE, "breakeven_map.csv")
    if os.path.exists(bm):
        m = pd.read_csv(bm, comment="#")
        d = df.set_index("isin")
        lines += ["## Breakeven pairs (linker → nominal hedge)", ""]
        for cty in ["FR", "IT", "ES", "GB"]:
            sub = m[m["real_isin"].astype(str).str[:2] == cty]
            if sub.empty:
                continue
            lines += [f"### {cty_name.get(cty, cty)} ({len(sub)})", "",
                      "| Linker ISIN | Linker | → | Hedge ISIN | Nominal hedge |", "|---|---|---|---|---|"]
            for _, r in sub.iterrows():
                note = str(r.get("note", "")); p = note.split(" vs ", 1)
                lk = p[0] if p else r["real_isin"]; nm = p[1] if len(p) > 1 else r["nominal_isin"]
                lines.append(f"| `{r['real_isin']}` | {lk} | → | `{r['nominal_isin']}` | {nm} |")
            lines.append("")
    lines += ["## Flat lookup (all ISINs)", "", "| ISIN | Bond | Cpn | Maturity | Country | Role |",
              "|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        cpn = "" if pd.isna(r["coupon"]) else f"{float(r['coupon']):g}%"
        mat = "" if r["maturity"] in ("nan", "NaT", "") else r["maturity"]
        lines.append(f"| `{r['isin']}` | {r['desc']} | {cpn} | {mat} | {r['country']} | {r['role']} |")
    with open(os.path.join(HERE, "ISIN_DIRECTORY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def market_matrix():
    """The per-market convention matrix as a DataFrame (for inspection / the export README)."""
    rows = []
    for m, c in MARKETS.items():
        rows.append({"market": m, **{k: c.get(k) for k in
                     ("country", "program", "index", "freq", "settle_lag", "calendar",
                      "floor", "quote", "ccy", "repo", "lag_months", "interp", "deferred")}})
    return pd.DataFrame(rows).set_index("market")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "markets"
    if cmd == "markets":
        with pd.option_context("display.max_columns", 30, "display.width", 200):
            print("=== per-market conventions ===")
            print(market_matrix().to_string())
            print("\n=== reference indices ===")
            for k, v in REF_INDEX.items():
                print(f"  {k:12s} {v['ticker']:16s} {v['desc']}")
            print("\n=== financing curves ===")
            for k, v in FINANCING.items():
                print(f"  {k:10s} {v['ticker']:16s} {v['desc']}")
    elif cmd == "universe":
        build_universe()
        u = load_universe(include_deferred=True)
        print(u.groupby(["country", "program"]).size().to_string())
    elif cmd == "enrich":
        print(enrich().to_string())
    elif cmd == "directory":
        directory()
    elif cmd == "status":
        _ensure_dirs()
        n_static = len(os.listdir(os.path.join(CACHE, "static")))
        n_daily = len(os.listdir(os.path.join(CACHE, "daily")))
        print(f"cache_intl: universe={'yes' if os.path.exists(UNIVERSE_CSV) else 'no'}  "
              f"static={n_static}  daily={n_daily}")
    else:
        print(__doc__)
