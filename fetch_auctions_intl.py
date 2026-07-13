"""
AUTOMATED download + parse of the JP/AU/NZ linker auction histories — the public debt-office
files the desk used to fetch by hand. All three are direct-download spreadsheets, so this runs
ANYWHERE (no Bloomberg, no auth) and is wired into the daily pipeline pull.

Sources (verified 2026-07):
  JP  MOF "Historical Data of Auction Results" — Auction Results for JGBs workbook, sheet
      10年物価連動 (10y inflation-indexed). Page:
      https://www.mof.go.jp/english/policy/jgbs/auction/past_auction_results/index.html
  AU  AOFM Data Hub -> Transactional data -> "Treasury Indexed Bonds - Issuance" (tender /
      syndication / tap / panel / conversion).  https://www.aofm.gov.au/data-hub
  NZ  NZ Debt Management -> Inflation-Indexed Bonds -> related files ->
      "Government bonds - tender issuance history", sheet IIBs.
      https://debtmanagement.treasury.govt.nz/government-securities/inflation-indexed-bonds

Each parses into the standard DMO schema -> cache_intl/auctions_raw/{JP,AU,NZ}.csv, which
auctions_intl.build() unions into the canonical auction calendar (with synd/auction method).

Usage:
  python fetch_auctions_intl.py            # download fresh files + parse + write raw CSVs
  python fetch_auctions_intl.py parse      # parse only (use previously downloaded files)
Downloads land in cache_intl/auction_sources/.
"""
from __future__ import annotations
import os, re, sys, urllib.request

import pandas as pd

import linkers

CACHE = linkers.CACHE
SRC = os.path.join(CACHE, "auction_sources")
RAW = os.path.join(CACHE, "auctions_raw")
SCHEMA = ["isin", "event_date", "settle_date", "event_type", "amount", "price", "yield", "reopening"]

# JP: stable direct URL — fully automated (verified 2026-07).
# AU/NZ: their sites bot-block plain HTTP (AOFM times out, NZDM 403s even with browser headers),
# so those two stay a MANUAL monthly download into cache_intl/auction_sources/ — the parse is
# automated. Manual pages:
#   AU  https://www.aofm.gov.au/data-hub  -> Transactional data ->
#       "Treasury Indexed Bonds - Issuance"          -> save as au_tib_issuance.xlsx
#   NZ  https://debtmanagement.treasury.govt.nz/government-securities/inflation-indexed-bonds
#       -> "Government bonds - tender issuance history" -> save as nz_tender_history.xlsx
JP_URL = "https://www.mof.go.jp/english/policy/jgbs/auction/past_auction_results/Auction_Results_for_JGBs.xls"
MANUAL = {"au_tib_issuance.xlsx": "AOFM data-hub (see module docstring)",
          "nz_tender_history.xlsx": "NZ Debt Management IIB page (see module docstring)",
          "nz_syndications.xlsx": "NZDM syndication results (updates only after a new syndication): "
                                  "debtmanagement.treasury.govt.nz/resource/government-bonds-syndication"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def fetch():
    """Refresh what CAN be fetched automatically (JP), report staleness of the manual files."""
    os.makedirs(SRC, exist_ok=True)
    try:
        req = urllib.request.Request(JP_URL, headers=UA)
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
        with open(os.path.join(SRC, "jp_jgb_auctions.xls"), "wb") as f:
            f.write(data)
        print(f"  JP: MOF auction results refreshed ({len(data) // 1024} KB)")
    except Exception as e:
        print(f"  JP: fetch failed ({type(e).__name__}) — using existing file")
    for fname, where in MANUAL.items():
        p = os.path.join(SRC, fname)
        if not os.path.exists(p):
            print(f"  {fname}: MISSING — download from {where}")
        else:
            age = (pd.Timestamp.today() - pd.Timestamp(os.path.getmtime(p), unit="s")).days
            note = f"  ({age}d old — consider refreshing)" if age > 45 else ""
            print(f"  {fname}: present{note}")


def _uni(market):
    u = linkers.load_universe(include_deferred=True)
    u = u[u["market"] == market].copy()
    u["mat"] = pd.to_datetime(u["maturity"])
    return u


def parse_jp():
    p = os.path.join(SRC, "jp_jgb_auctions.xls")
    if not os.path.exists(p):
        print("  JP: no source file"); return
    d = pd.read_excel(p, sheet_name="10年物価連動", header=None, skiprows=5)
    d = d[[0, 1, 2, 3, 4, 7, 8, 11]]
    d.columns = ["series", "auction_date", "issue_date", "maturity", "coupon", "accepted_oku",
                 "avg_price", "high_yield"]
    d = d.dropna(subset=["auction_date", "maturity"])
    u = _uni("JP_JGBI")
    key = {(m, round(float(c), 3)): i for m, c, i in zip(u["mat"], u["cpn"], u["isin"])}
    d["isin"] = [key.get((pd.Timestamp(m), round(float(c), 3))) for m, c in
                 zip(d["maturity"], d["coupon"])]
    d = d.dropna(subset=["isin"])                          # old-style series 1-16 fall out here
    d = d.sort_values("auction_date")
    first = d.groupby("isin")["auction_date"].transform("min")
    out = pd.DataFrame({
        "isin": d["isin"], "event_date": pd.to_datetime(d["auction_date"]).dt.strftime("%Y-%m-%d"),
        "settle_date": pd.to_datetime(d["issue_date"]).dt.strftime("%Y-%m-%d"),
        "event_type": ["reopening" if r else "auction" for r in d["auction_date"] != first],
        "amount": pd.to_numeric(d["accepted_oku"], errors="coerce") * 1e8,   # 億円 -> yen
        "price": pd.to_numeric(d["avg_price"], errors="coerce"),
        "yield": pd.to_numeric(d["high_yield"], errors="coerce"),
        "reopening": (d["auction_date"] != first).values})
    out.to_csv(os.path.join(RAW, "JP.csv"), index=False)
    print(f"  JP.csv: {len(out)} events, {out['isin'].nunique()} bonds "
          f"({int((~out['reopening']).sum())} new — all MOF auctions)")


def parse_au():
    p = os.path.join(SRC, "au_tib_issuance.xlsx")
    if not os.path.exists(p):
        print("  AU: no source file"); return
    d = pd.read_excel(p, sheet_name="Transactions", header=None, skiprows=3)
    d = d[[0, 1, 2, 3, 4, 6, 9, 19]]
    d.columns = ["date", "tender_no", "maturity", "coupon", "isin", "allotted", "wavg_yield", "settle"]
    d = d.dropna(subset=["date"])
    u = _uni("AU_TIB")
    ours = set(u["isin"])
    key = {(m, round(float(c), 3)): i for m, c, i in zip(u["mat"], u["cpn"], u["isin"])}
    d["isin"] = [i if isinstance(i, str) and i in ours else
                 key.get((pd.Timestamp(m) if pd.notna(m) else None,
                          round(float(c), 3) if pd.notna(c) else None))
                 for i, m, c in zip(d["isin"], d["maturity"], d["coupon"])]
    d = d.dropna(subset=["isin"])
    tno = d["tender_no"].astype(str).str.upper()
    d["kind"] = "auction"                                   # TIB###, INDEXTENDER, INDEXTAP, TIBPANEL
    d.loc[tno.str.contains("SYN"), "kind"] = "syndication"  # TIBSYN##
    d = d[~tno.str.contains("CON|SWITCH")]                  # TIBCON## conversions aren't supply
    d = d.sort_values("date")
    first = d.groupby("isin")["date"].transform("min")
    reo = d["date"] != first
    out = pd.DataFrame({
        "isin": d["isin"], "event_date": pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d"),
        "settle_date": pd.to_datetime(d["settle"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "event_type": [k if k == "syndication" else ("reopening" if r else k)
                       for k, r in zip(d["kind"], reo)],   # syndicated TAPS keep their synd tag
        "amount": pd.to_numeric(d["allotted"], errors="coerce"),
        "price": pd.NA, "yield": pd.to_numeric(d["wavg_yield"], errors="coerce"),
        "reopening": reo.values})
    out.to_csv(os.path.join(RAW, "AU.csv"), index=False)
    kinds = dict(pd.Series([e for e in out["event_type"]]).value_counts())
    print(f"  AU.csv: {len(out)} events, {out['isin'].nunique()} bonds  {kinds}")


def parse_nz():
    p = os.path.join(SRC, "nz_tender_history.xlsx")
    if not os.path.exists(p):
        print("  NZ: no source file"); return
    d = pd.read_excel(p, sheet_name="IIBs", header=None, skiprows=5)
    d = d[[0, 1, 2, 3, 6, 10]]
    d.columns = ["date", "atype", "maturity", "coupon", "offered_m", "accepted_m"]
    d = d.dropna(subset=["date", "maturity"])
    u = _uni("NZ_IIB")
    key = {(m, round(float(c), 3)): i for m, c, i in zip(u["mat"], u["cpn"], u["isin"])}
    d["maturity"] = pd.to_datetime(d["maturity"], errors="coerce")     # stray header rows -> NaT
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date", "maturity"])
    d["cpn_pct"] = pd.to_numeric(d["coupon"], errors="coerce") * 100   # file has decimals (0.03)
    bymat = u.groupby("mat")["isin"].agg(lambda s: s.iloc[0] if len(s) == 1 else None)
    d["isin"] = [key.get((pd.Timestamp(m), round(c, 3))) or bymat.get(pd.Timestamp(m))
                 for m, c in zip(d["maturity"], d["cpn_pct"])]   # maturity-only fallback: NZDM has
    # occasional coupon typos (e.g. two 2023 tenders of the 2.5% 2035 recorded as 3%)
    unmapped = d[d["isin"].isna()][["maturity", "cpn_pct"]].drop_duplicates()
    d = d.dropna(subset=["isin"]).sort_values("date")
    first = d.groupby("isin")["date"].transform("min")
    reo = d["date"] != first
    out = pd.DataFrame({
        "isin": d["isin"], "event_date": pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d"),
        "settle_date": "", "event_type": ["reopening" if r else "auction" for r in reo],
        "amount": pd.to_numeric(d["accepted_m"], errors="coerce") * 1e6,
        "price": pd.NA, "yield": pd.NA, "reopening": reo.values})
    # syndications (separate NZDM workbook: launches + syndicated taps, WITH issue yield =
    # the clearing level). https://debtmanagement.treasury.govt.nz/resource/government-bonds-syndication
    sp = os.path.join(SRC, "nz_syndications.xlsx")
    if os.path.exists(sp):
        srows = []
        for sheet, reo in (("Syndication", False), ("Syndicated Tap", True)):
            try:
                sd = pd.read_excel(sp, sheet_name=sheet, header=None, skiprows=6)
            except Exception:
                continue
            sd = sd[[0, 1, 2, 3, 4, 5, 7]]
            sd.columns = ["date", "settle", "maturity", "coupon", "type", "amt_m", "iss_yield"]
            sd["date"] = pd.to_datetime(sd["date"], errors="coerce")
            sd["maturity"] = pd.to_datetime(sd["maturity"], errors="coerce")
            sd = sd.dropna(subset=["date", "maturity"])
            sd = sd[sd["type"].astype(str).str.contains("Inflation", case=False, na=False)]
            sd["isin"] = [bymat.get(pd.Timestamp(m)) for m in sd["maturity"]]
            sd = sd.dropna(subset=["isin"])
            for _, r in sd.iterrows():
                srows.append({"isin": r["isin"],
                              "event_date": r["date"].strftime("%Y-%m-%d"),
                              "settle_date": pd.Timestamp(r["settle"]).strftime("%Y-%m-%d")
                              if pd.notna(r["settle"]) else "",
                              "event_type": "syndication",
                              "amount": float(r["amt_m"]) * 1e6 if pd.notna(r["amt_m"]) else pd.NA,
                              "price": pd.NA,
                              "yield": float(r["iss_yield"]) * 100 if pd.notna(r["iss_yield"]) else pd.NA,
                              "reopening": reo})
        if srows:
            out = pd.concat([out, pd.DataFrame(srows)], ignore_index=True)
            out = out.sort_values("event_date")
    out.to_csv(os.path.join(RAW, "NZ.csv"), index=False)
    nsyn = int((out["event_type"] == "syndication").sum())
    print(f"  NZ.csv: {len(out)} events ({nsyn} syndications incl taps w/ clearing yields, "
          f"{len(out) - nsyn} tenders), {out['isin'].nunique()} bonds")
    if len(unmapped):
        print(f"  NZ UNMAPPED lines (add their ISINs to linkers.SEED_UNIVERSE):")
        for _, r in unmapped.iterrows():
            print(f"    {r['cpn_pct']:.2f}% {pd.Timestamp(r['maturity']).date()}")


def parse():
    os.makedirs(RAW, exist_ok=True)
    parse_jp(); parse_au(); parse_nz()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("all", "fetch"):
        fetch()
    if cmd in ("all", "parse"):
        parse()
