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


def pivot_combined(ev, market):
    """year × original-tenor matrix with BOTH new issues and reopenings in one cell:
       'N'      = N new issues (no reopenings)
       'N+Rx'   = N new issues + R reopenings that year/tenor
       'Rx'     = R reopenings only
       ''       = nothing.  The 'x' flags reopenings (taps of an existing line)."""
    d = ev[ev["market"] == market]
    if d.empty:
        return pd.DataFrame()
    cnt = d.pivot_table(index="event_year", columns=["orig_tenor", "type"], values="isin",
                        aggfunc="count", fill_value=0)
    years = sorted(d["event_year"].unique())
    tens = sorted(d["orig_tenor"].dropna().unique())
    out = pd.DataFrame("", index=years, columns=[f"{int(t)}y" for t in tens])
    for y in years:
        for t in tens:
            nn = int(cnt.get((t, "new"), pd.Series(0, index=cnt.index)).get(y, 0))
            rr = int(cnt.get((t, "reopening"), pd.Series(0, index=cnt.index)).get(y, 0))
            out.at[y, f"{int(t)}y"] = (f"{nn}+{rr}x" if nn and rr else f"{rr}x" if rr else str(nn) if nn else "")
    out.index.name = "year"
    return out


def build():
    os.makedirs(EXPORTS, exist_ok=True)
    new = bond_issuance()
    ev = events(new)
    has_reop = (ev["type"] == "reopening").any()

    md = ["# Linker issuance by year & original tenor (per market)", "",
          "**What is a reopening / tap?** A government doesn't sell a whole bond at once. The FIRST sale "
          "(auction or syndication) creates the bond — the *new issue* (ISIN fixed: coupon + maturity). It "
          "then sells MORE of the **same** bond (same ISIN/coupon/maturity) at later auctions; each is a "
          "*reopening* / *tap*, adding to amount outstanding. So one line is tapped repeatedly — roughly "
          "monthly while it's the on-the-run — over its first ~1–3 years, which is why a single bond shows "
          "many auction events.",
          "",
          "Original tenor = round(maturity − first issue) in years. Cell = **count of events that year**: "
          "`N` = N new issues; `N+Rx` = N new + R reopenings; `Rx` = R reopenings only; blank = none. `x` flags "
          "reopenings. Counts are **per year** — `10x` = 10 taps spread across that year's months (not one "
          "month; a line is rarely tapped twice in a month).",
          ("Markets shown with reopenings: FR, ES. " if has_reop else "") +
          ("(IT/UK reopenings pending; DE issuance ended — those show NEW issues only.)"), ""]
    print("\n=== Linker issuance by year × original tenor (per market) ===")
    print("cell:  N = new issues | N+Rx = N new + R reopenings | Rx = reopenings only | · = none")
    if not has_reop:
        print("(NEW issues only so far — reopenings not loaded; see note below)")
    for m in MARKET_ORDER:
        pc = pivot_combined(ev, m)
        if pc.empty:
            continue
        tens = sorted(new[new["market"] == m]["orig_tenor"].unique())
        n_re = int(((ev["market"] == m) & (ev["type"] == "reopening")).sum())
        hdr = (f"{MARKET_LABEL[m]}  —  {len(new[new['market']==m])} bonds, "
               f"{n_re} reopenings, tenors: {', '.join(str(t)+'y' for t in tens)}")
        print(f"\n{hdr}"); print(pc.to_string())
        md += [f"## {hdr}", "", "```", pc.to_string(), "```", ""]
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
        for m in MARKET_ORDER:                                          # combined matrix per market (N / N+Rx)
            pc = pivot_combined(ev, m)
            if pc.empty:
                continue
            pc.to_excel(xl, sheet_name=MARKET_LABEL[m].replace("€", "e")[:28])
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
