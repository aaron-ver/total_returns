"""
Auction & syndication calendar for European/UK linkers (reference_intl.MD §9 — the boss's
headline "gather auction/syndication dates and sizes" ask).

There is NO single public European auction API (the US used TreasuryDirect). The desk instruction
is "pull with bbg", so this collects in two layers and unions them:

  1. BLOOMBERG, bond-level (reliable via refdata): per ISIN the ORIGINAL issue date, first-settle
     date, total AMOUNT ISSUED, issue price, and the distribution method (auction vs syndication)
     where Bloomberg carries it. This is what Bloomberg's ReferenceDataRequest exposes cleanly —
     it does NOT expose a clean per-TAP (reopening-by-reopening) history through refdata.

  2. DMO RESULTS FILES, tap-level (the granular calendar): each debt office publishes downloadable
     auction/syndication results (date, ISIN, nominal allotted, price/yield, reopening). Drop the
     normalized CSVs in cache_intl/auctions_raw/<country>.csv and this folds them in. Sources in
     DMO_SOURCES below. Bloomberg's {AUCN}/{NIM} screens are the manual cross-check.

Output: cache_intl/auctions.parquet — one row per (isin, event_date), columns:
  isin, market, country, event_date, settle_date, event_type(auction|syndication|tap|issue),
  amount, price, yield, reopening, source(bbg|dmo).

Usage:
  python auctions_intl.py bbg         # build the bond-level table from cached Bloomberg static
  python auctions_intl.py dmo         # fold in any cache_intl/auctions_raw/<country>.csv files
  python auctions_intl.py build       # union both -> cache_intl/auctions.parquet
  python auctions_intl.py sources     # print where to download each DMO's results files
"""
from __future__ import annotations
import os, sys, glob
import pandas as pd

import linkers

CACHE = linkers.CACHE
RAW = os.path.join(CACHE, "auctions_raw")
OUT = os.path.join(CACHE, "auctions.parquet")

# Where to download each debt office's auction/syndication results (the tap-level calendar).
DMO_SOURCES = {
    "FR": "AFT — auction results: https://www.aft.gouv.fr/en/auctions-results  (OATi/OAT€i ‘indexed OAT’ lines)",
    "IT": "MEF/Banca d'Italia — auction results: https://www.dt.mef.gov.it/en/debito_pubblico/risultati_aste/  (BTP€i)",
    "ES": "Tesoro Público — resultados de subastas: https://www.tesoro.es/en/deuda-publica/subastas/resultado-de-subastas",
    "DE": "Deutsche Finanzagentur — auction results: https://www.deutsche-finanzagentur.de/en/federal-securities/auctions  (linkers ended 2024)",
    "GB": "UK DMO — gilt auction & syndication results: https://www.dmo.gov.uk/data/  (Gilt Operations / Results)",
}

# Common normalized schema expected for a DMO CSV in cache_intl/auctions_raw/<country>.csv:
DMO_SCHEMA = ["isin", "event_date", "settle_date", "event_type", "amount", "price", "yield", "reopening"]

# Bloomberg static fields that describe issuance (pulled into static cache by linkers.enrich;
# this extra one captures the distribution method where available).
METHOD_FIELDS = ["ISSUE_DT", "FIRST_SETTLE_DT", "AMT_ISSUED", "ISSUE_PX", "DISTRIBUTION_METHODOLOGY"]


def _market_country(isin):
    u = linkers.load_universe(include_deferred=True)
    row = u[u["isin"] == isin]
    if row.empty:
        return None, None
    return row.iloc[0]["market"], row.iloc[0]["country"]


def from_static():
    """Bond-level issuance rows from the cached Bloomberg static (one ORIGINAL issue per ISIN).
    Run linkers.enrich() first so the static parquets exist."""
    rows = []
    u = linkers.load_universe(include_deferred=True)
    for _, r in u.iterrows():
        sp = os.path.join(CACHE, "static", f"{r['isin']}.parquet")
        if not os.path.exists(sp):
            continue
        s = pd.read_parquet(sp).iloc[0]
        rows.append({"isin": r["isin"], "market": r["market"], "country": r["country"],
                     "event_date": pd.to_datetime(s.get("ISSUE_DT"), errors="coerce"),
                     "settle_date": pd.to_datetime(s.get("FIRST_SETTLE_DT"), errors="coerce"),
                     "event_type": "issue",
                     "amount": pd.to_numeric(s.get("AMT_ISSUED"), errors="coerce"),
                     "price": pd.to_numeric(s.get("ISSUE_PX"), errors="coerce"),
                     "yield": pd.NA, "reopening": False, "source": "bbg"})
    return pd.DataFrame(rows)


def pull_method(write_back=True):
    """Optional: pull the distribution method (auction vs syndication) + issuance fields from
    Bloomberg for every ISIN, to tag the bond-level rows. Needs the Terminal."""
    import bbg
    u = linkers.load_universe(include_deferred=True)
    secs = [f"{i} {linkers.BBG_SUFFIX}" for i in u["isin"]]
    bbg.open_session()
    try:
        out = {}
        for k in range(0, len(secs), 50):
            out.update(bbg.reference(secs[k:k + 50], METHOD_FIELDS))
    finally:
        bbg.close_session()
    return out


def load_dmo():
    """Fold in any normalized DMO results CSVs in cache_intl/auctions_raw/<country>.csv (tap-level)."""
    os.makedirs(RAW, exist_ok=True)
    frames = []
    for fp in glob.glob(os.path.join(RAW, "*.csv")):
        country = os.path.splitext(os.path.basename(fp))[0].upper()
        df = pd.read_csv(fp, dtype={"isin": str})
        for c in DMO_SCHEMA:
            if c not in df:
                df[c] = pd.NA
        df["country"] = country
        df["market"] = df["isin"].map(lambda i: (_market_country(i)[0]))
        df["source"] = "dmo"
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
        df["settle_date"] = pd.to_datetime(df["settle_date"], errors="coerce")
        frames.append(df[["isin", "market", "country"] + DMO_SCHEMA[1:] + ["source"]])
        print(f"  {country}: {len(df)} tap rows from {os.path.basename(fp)}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build():
    os.makedirs(CACHE, exist_ok=True)
    bbg_rows = from_static()
    dmo_rows = load_dmo()
    parts = [d for d in (bbg_rows, dmo_rows) if not d.empty]
    if not parts:
        print("  no auction data — run linkers.enrich() (for bbg rows) and/or drop DMO CSVs in cache_intl/auctions_raw/")
        return pd.DataFrame()
    allrows = pd.concat(parts, ignore_index=True).sort_values(["country", "isin", "event_date"])
    allrows.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(allrows)} events "
          f"({(allrows.source == 'bbg').sum()} bbg bond-level, {(allrows.source == 'dmo').sum()} dmo tap-level)")
    print(allrows.groupby(["country", "event_type"]).size().to_string())
    return allrows


def load():
    if not os.path.exists(OUT):
        return build()
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "bbg":
        print(from_static().to_string())
    elif cmd == "dmo":
        d = load_dmo()
        print(d.to_string() if not d.empty else "  no DMO CSVs in cache_intl/auctions_raw/")
    elif cmd == "build":
        build()
    elif cmd == "sources":
        print("Drop normalized CSVs (schema: " + ",".join(DMO_SCHEMA) + ") in cache_intl/auctions_raw/<country>.csv\n")
        for c, s in DMO_SOURCES.items():
            print(f"  {c}: {s}")
    else:
        print(__doc__)
