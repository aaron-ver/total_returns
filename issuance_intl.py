"""
Issuance landscape per market — year-on-year, by original tenor, NEW issues vs REOPENINGS.

Built so the desk can SEE each market's issuance cadence and tenor variety before designing the
(pseudo-)constant-maturity buckets — kept separate per market (currency/country × programme) since
quantities and tenor variations differ. Distinguishes NEW issues from REOPENINGS/taps, which will
feed the auction/seasonal analysis later.

Original tenor = round((maturity − first issue) / 365.25) in years — the term AT LAUNCH (Europe/UK
issue odd tenors, not neat 5/10/30). For a reopening, `remaining_tenor` (years left at the tap date)
is also recorded — useful for "where on the curve did the supply land" seasonal work.

Sources (via auctions_intl, source column on each event):
  * NEW issues = Bloomberg static ISSUE_DT (definitive).         source 'bbg'
  * REOPENINGS = step-ups in Bloomberg AMT_OUTSTANDING history.   source 'bbg_amt'   (run
    `python auctions_intl.py reopenings` then `build`); or DMO files (source 'dmo').
  If no reopenings have been pulled yet, the table is NEW-only (flagged in the output).

Usage:
  python issuance_intl.py            # build -> exports/intl_issuance.xlsx + .md, print per-market matrices
"""
from __future__ import annotations
import os, sys
import pandas as pd

import linkers

CACHE = linkers.CACHE
EXPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")

MARKET_LABEL = {"FR_OATEI": "France OAT€i", "FR_OATI": "France OATi", "IT_BTPEI": "Italy BTP€i",
                "ES_EI": "Spain SPGB€i", "DE_EI": "Germany Bund€i", "UK_3M": "UK gilt (UKTi)"}
MARKET_ORDER = ["FR_OATEI", "FR_OATI", "IT_BTPEI", "ES_EI", "DE_EI", "UK_3M"]
EVENT_COLS = ["market", "program", "isin", "desc", "event_date", "event_year", "type",
              "amt_bn", "orig_tenor", "remaining_tenor", "maturity", "source"]


def _num(x):
    v = pd.to_numeric(x, errors="coerce")
    return float(v) if pd.notna(v) else float("nan")


def bond_issuance():
    """One row per linker: market, original issue date/year, maturity, original tenor, sizes (NEW issue)."""
    u = linkers.load_universe()                          # active linkers (excludes deferred/non-traded)
    rows = []
    for _, r in u.iterrows():
        sp = os.path.join(CACHE, "static", f"{r['isin']}.parquet")
        if not os.path.exists(sp):
            continue
        s = pd.read_parquet(sp).iloc[0]
        iss = pd.to_datetime(s.get("ISSUE_DT"), errors="coerce")
        mat = pd.to_datetime(s.get("MATURITY"), errors="coerce")
        if pd.isna(iss) or pd.isna(mat):
            continue
        ten = int(round((mat - iss).days / 365.25))
        ai, ao = _num(s.get("AMT_ISSUED")), _num(s.get("AMT_OUTSTANDING"))
        rows.append({"market": r["market"], "country": r["country"], "program": r["program"],
                     "isin": r["isin"], "desc": r["desc"],
                     "issue_date": iss, "issue_year": int(iss.year),
                     "maturity": mat, "maturity_year": int(mat.year), "orig_tenor": ten,
                     "amt_issued_bn": round(ai / 1e9, 2) if ai == ai else None,
                     "amt_out_bn": round(ao / 1e9, 2) if ao == ao else None})
    return pd.DataFrame(rows).sort_values(["market", "issue_year", "orig_tenor", "maturity"]).reset_index(drop=True)


def reopening_events(info):
    """Reopening/tap events from the auction calendar (auctions_intl), joined to each bond's line
    (original tenor) + remaining tenor at the tap. Empty if no reopenings pulled yet. `info` =
    bond_issuance() indexed by isin."""
    p = os.path.join(CACHE, "auctions.parquet")
    if not os.path.exists(p):
        return pd.DataFrame(columns=EVENT_COLS)
    a = pd.read_parquet(p)
    if "reopening" not in a.columns:
        return pd.DataFrame(columns=EVENT_COLS)
    a = a[(a["reopening"] == True) & (a["isin"].isin(info.index))].copy()
    if a.empty:
        return pd.DataFrame(columns=EVENT_COLS)
    a["event_date"] = pd.to_datetime(a["event_date"])
    a["event_year"] = a["event_date"].dt.year
    a["market"] = a["isin"].map(info["market"]); a["program"] = a["isin"].map(info["program"])
    a["desc"] = a["isin"].map(info["desc"]); a["orig_tenor"] = a["isin"].map(info["orig_tenor"])
    mat = pd.to_datetime(a["isin"].map(info["maturity"]))
    a["maturity"] = mat
    a["remaining_tenor"] = ((mat - a["event_date"]).dt.days / 365.25).round().astype("Int64")
    a["amt_bn"] = (pd.to_numeric(a["amount"], errors="coerce") / 1e9).round(2)
    a["type"] = "reopening"
    return a.reindex(columns=EVENT_COLS)


def events(new=None):
    """Granular event log: every NEW issue + REOPENING, one row each (the input for seasonal work)."""
    new = bond_issuance() if new is None else new
    n = new.assign(event_date=new["issue_date"], event_year=new["issue_year"], type="new",
                   amt_bn=new["amt_issued_bn"].fillna(new["amt_out_bn"]),
                   remaining_tenor=new["orig_tenor"], source="bbg").reindex(columns=EVENT_COLS)
    reop = reopening_events(new.set_index("isin"))
    out = pd.concat([n, reop], ignore_index=True) if not reop.empty else n
    return out.sort_values(["market", "event_date"]).reset_index(drop=True)


def pivot(ev, market, etype, values="count"):
    """year × original-tenor matrix for one market and event type (count or summed amount bn)."""
    d = ev[(ev["market"] == market) & (ev["type"] == etype)]
    if d.empty:
        return pd.DataFrame()
    if values == "count":
        p = d.pivot_table(index="event_year", columns="orig_tenor", values="isin", aggfunc="count", fill_value=0)
    else:
        p = d.pivot_table(index="event_year", columns="orig_tenor", values="amt_bn", aggfunc="sum", fill_value=0).round(1)
    p.columns = [f"{int(c)}y" for c in p.columns]
    return p


def build():
    os.makedirs(EXPORTS, exist_ok=True)
    new = bond_issuance()
    ev = events(new)
    has_reop = (ev["type"] == "reopening").any()

    md = ["# Linker issuance by year & original tenor (per market)", "",
          "Original tenor = round(maturity − first issue) in years. Cells = **count** of events that year.",
          ("Shows NEW issues and REOPENINGS." if has_reop else
           "**NEW issues only** — run `python auctions_intl.py reopenings && python auctions_intl.py build` "
           "to add reopenings (from Bloomberg AMT_OUTSTANDING history)."), ""]
    print("\n=== Linker issuance by year × original tenor (per market) ===")
    if not has_reop:
        print("(NEW issues only — run auctions_intl.py reopenings + build to add reopenings)")
    for m in MARKET_ORDER:
        pn = pivot(ev, m, "new", "count")
        if pn.empty:
            continue
        tens = sorted(new[new["market"] == m]["orig_tenor"].unique())
        n_re = int(((ev["market"] == m) & (ev["type"] == "reopening")).sum())
        hdr = (f"{MARKET_LABEL[m]}  —  {len(new[new['market']==m])} bonds, "
               f"{n_re} reopenings, tenors: {', '.join(str(t)+'y' for t in tens)}")
        print(f"\n{hdr}\n[NEW issues]"); print(pn.to_string())
        md += [f"## {hdr}", "", "**New issues**", "", "```", pn.to_string(), "```", ""]
        pr = pivot(ev, m, "reopening", "count")
        if not pr.empty:
            print("[REOPENINGS]"); print(pr.to_string())
            md += ["**Reopenings**", "", "```", pr.to_string(), "```", ""]
    md_path = os.path.join(EXPORTS, "intl_issuance.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    # tidy long (market, year, tenor, type -> count, amount)
    long = ev.groupby(["market", "program", "event_year", "orig_tenor", "type"]).agg(
        n=("isin", "count"), amt_bn=("amt_bn", "sum")).reset_index()
    long["amt_bn"] = long["amt_bn"].round(1)
    # summary per market
    summ = []
    for m in MARKET_ORDER:
        nd = new[new["market"] == m]
        if nd.empty:
            continue
        summ.append({"market": m, "program": MARKET_LABEL[m], "bonds": len(nd),
                     "new_issues": len(nd), "reopenings": int(((ev.market == m) & (ev.type == "reopening")).sum()),
                     "first_year": int(nd.issue_year.min()), "last_year": int(nd.issue_year.max()),
                     "min_tenor": int(nd.orig_tenor.min()), "max_tenor": int(nd.orig_tenor.max()),
                     "amt_out_bn": round(nd.amt_out_bn.sum(), 0)})
    summ = pd.DataFrame(summ)

    xls = os.path.join(EXPORTS, "intl_issuance.xlsx")
    try:
        from export_intl import _format
    except Exception:
        _format = None
    with pd.ExcelWriter(xls, engine="openpyxl") as xl:
        summ.to_excel(xl, sheet_name="SUMMARY", index=False)
        long.to_excel(xl, sheet_name="by_year_tenor_type", index=False)
        ev.to_excel(xl, sheet_name="events", index=False)              # granular log for seasonal work
        for m in MARKET_ORDER:
            pn = pivot(ev, m, "new", "count")
            if pn.empty:
                continue
            sh = MARKET_LABEL[m].replace("€", "e")[:25]
            pn.to_excel(xl, sheet_name=f"{sh} new#")
            pr = pivot(ev, m, "reopening", "count")
            if not pr.empty:
                pr.to_excel(xl, sheet_name=f"{sh} reopen#")
        new.to_excel(xl, sheet_name="bonds", index=False)
        if _format:
            for ws in xl.sheets.values():
                _format(ws, date_col=False)
    print(f"\n  wrote {xls}  (SUMMARY + by_year_tenor_type + events + per-market new#/reopen# + bonds)")
    print(f"  wrote {md_path}")
    if not has_reop:
        print("  NOTE: NEW issues only — run `python auctions_intl.py reopenings && python auctions_intl.py build`, "
              "then re-run this, to populate reopenings.")
    return ev


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build()
