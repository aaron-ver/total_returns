"""
Auction & syndication calendar for European/UK linkers (reference_intl.MD §9 — the boss's
headline "gather auction/syndication dates and sizes" ask).

There is NO single public European auction API (the US used TreasuryDirect). The desk instruction
is "pull with bbg", so this collects in three layers and unions them (distinguishing NEW vs REOPENING):

  1. BLOOMBERG static, NEW issues (from_static): per ISIN the ORIGINAL issue date, first-settle date,
     amount issued, issue price. The definitive new-issue event per bond.

  2. BLOOMBERG AMT_OUTSTANDING history, REOPENINGS (pull_reopenings): every positive step-up in a
     bond's daily amount-outstanding series is a tap/reopening (date + size). Bloomberg-native, no
     DMO files needed. (Falls back to layer 3 if the field isn't served historically.)

  3. DMO RESULTS FILES, tap-level (load_dmo): each debt office publishes downloadable auction results
     (date, ISIN, nominal allotted, price/yield, reopening). Drop normalized CSVs in
     cache_intl/auctions_raw/<country>.csv. Sources in DMO_SOURCES below; manual cross-check.

Output: cache_intl/auctions.parquet — one row per event, columns:
  isin, market, country, event_date, settle_date, event_type(issue|reopening|auction|tap),
  amount, price, yield, reopening(bool), source(bbg|bbg_amt|dmo).

Usage:
  python auctions_intl.py reopenings  # pull AMT_OUTSTANDING history -> derive taps -> reopenings.parquet
  python auctions_intl.py build       # union new issues + reopenings (+DMO) -> cache_intl/auctions.parquet
  python auctions_intl.py dmo         # fold in any cache_intl/auctions_raw/<country>.csv files
  python auctions_intl.py sources     # print where to download each DMO's results files
"""
from __future__ import annotations
import os, sys, glob
import pandas as pd

import linkers

CACHE = linkers.CACHE
RAW = os.path.join(CACHE, "auctions_raw")
OUT = os.path.join(CACHE, "auctions.parquet")
REOPEN_PARQUET = os.path.join(CACHE, "reopenings.parquet")
OUTSTANDING_FIELD = "AMT_OUTSTANDING"
CANON = ["isin", "market", "country", "event_date", "settle_date", "event_type",
         "amount", "price", "yield", "reopening", "source"]


def _canon(df):
    df = df.copy()
    for c in CANON:
        if c not in df.columns:
            df[c] = pd.NA
    return df[CANON]

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


def pull_reopenings(eps_bn=0.05, include_deferred=False):
    """Derive REOPENING/tap events (date + size) from each linker's AMT_OUTSTANDING DAILY HISTORY:
    every positive step-up in the outstanding amount is a tap. Bloomberg-native — no DMO files
    needed. The original NEW issue is anchored separately (from_static); here we capture only the
    post-issue step-ups. Caches cache_intl/reopenings.parquet. Needs the Terminal.

    If AMT_OUTSTANDING isn't served historically (series comes back flat) the bond is skipped and a
    warning is printed — then fall back to the DMO auction-results files (auctions_intl.py sources)."""
    import bbg
    import data_layer_intl as dl
    u = linkers.load_universe(include_deferred=include_deferred)
    ev, flat, served = [], 0, 0
    bbg.open_session()
    try:
        for n, i in enumerate(u["isin"], 1):
            sec = f"{i} {linkers.BBG_SUFFIX}"
            try:
                rows = dl._history_one(sec, [OUTSTANDING_FIELD], dl.DAILY_START, dl.TODAY)
            except Exception as e:
                print(f"  [{n}/{len(u)}] {i} AMT_OUTSTANDING FAILED ({e})", flush=True); continue
            s = pd.DataFrame(rows)
            if s.empty or OUTSTANDING_FIELD not in s.columns:
                continue
            s["date"] = pd.to_datetime(s["date"])
            s = s.set_index("date")[OUTSTANDING_FIELD].dropna().sort_index()
            if s.nunique() <= 1:                          # flat -> history not served, can't detect taps
                flat += 1; continue
            served += 1
            steps = s.diff()
            taps = steps[steps > eps_bn * 1e9]            # positive step-ups = reopenings (ignore buybacks)
            for dt, amt in taps.items():
                ev.append({"isin": i, "event_date": pd.Timestamp(dt), "event_type": "reopening",
                           "amount": float(amt), "reopening": True, "source": "bbg_amt"})
            print(f"  [{n}/{len(u)}] {i} -> {len(taps)} taps", flush=True)
    finally:
        bbg.close_session()
    out = pd.DataFrame(ev)
    os.makedirs(CACHE, exist_ok=True)
    out.to_parquet(REOPEN_PARQUET)
    print(f"  wrote {REOPEN_PARQUET}: {len(out)} reopening events across {served} bonds "
          f"({flat} had flat/unserved AMT_OUTSTANDING history)")
    if served == 0:
        print("  WARN: AMT_OUTSTANDING not served historically -> use the DMO auction-results files "
              "instead (python auctions_intl.py sources).")
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
    """Union the issuance calendar: NEW issues (from_static, bbg) + REOPENINGS (reopenings.parquet
    from pull_reopenings, bbg_amt) + any DMO tap files -> cache_intl/auctions.parquet."""
    os.makedirs(CACHE, exist_ok=True)
    parts = []
    new_rows = from_static()
    if not new_rows.empty:
        parts.append(_canon(new_rows))
    if os.path.exists(REOPEN_PARQUET):
        rp = pd.read_parquet(REOPEN_PARQUET)
        if not rp.empty:
            u = linkers.load_universe(include_deferred=True).set_index("isin")
            rp["market"] = rp["isin"].map(u["market"]); rp["country"] = rp["isin"].map(u["country"])
            parts.append(_canon(rp))
    dmo_rows = load_dmo()
    if not dmo_rows.empty:
        parts.append(_canon(dmo_rows))
    if not parts:
        print("  no auction data — run linkers.enrich() (new issues) + auctions_intl.pull_reopenings() (taps)")
        return pd.DataFrame()
    allrows = pd.concat(parts, ignore_index=True).sort_values(["country", "isin", "event_date"])
    allrows.to_parquet(OUT)
    nnew = int((allrows["event_type"] == "issue").sum())
    nreopen = int((allrows["reopening"] == True).sum())
    print(f"  wrote {OUT}: {len(allrows)} events — {nnew} new issues, {nreopen} reopenings "
          f"(sources: {dict(allrows['source'].value_counts())})")
    if nreopen == 0:
        print("  (no reopenings yet — run `python auctions_intl.py reopenings` to pull them from AMT_OUTSTANDING)")
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
    elif cmd == "reopenings":
        pull_reopenings()
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
