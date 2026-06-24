"""
Treasury auction calendar + on-the-run (OTR) schedule reconstruction.

Source: TreasuryDirect public API (no account/key needed).
  - TIPS history is complete back to 1998 via /securities/auctioned?type=TIPS
  - Nominal Notes/Bonds are paged by date range via /securities/search

Two consumers, two views of "on-the-run":
  * otr_schedule() -- the simple monthly convention (reference.MD §9.2.1): OTR(tenor) for
    month M = the most recently *auctioned* security of that original tenor whose auction date
    falls strictly before the 1st of M ("old OTR through the auction month"). This feeds the
    levels VISUALIZER (visualize.py) and seeds the bond universe (otr_universe -> universe.csv).
  * new_issues() -- distinct original issues per CUSIP. This feeds the RETURN ENGINE, which
    builds its own issue-date-gated, maturity-matched roll (engine.roll_schedule); the engine
    does NOT use otr_schedule. See engine.py and reference.MD "As-built deltas".

Usage:
  python auctions.py pull        # fetch + cache the auction calendar
  python auctions.py schedule    # build + print the monthly OTR schedule
"""
from __future__ import annotations
import os, sys
import requests
import pandas as pd

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
AUCTIONS_PARQUET = os.path.join(CACHE, "auctions.parquet")
SCHEDULE_PARQUET = os.path.join(CACHE, "otr_schedule.parquet")

TA = "https://www.treasurydirect.gov/TA_WS/securities"
START_YEAR = 2003
KEEP = ["cusip", "securityType", "originalSecurityTerm", "securityTerm", "auctionDate",
        "issueDate", "datedDate", "maturityDate", "interestRate", "reopening",
        "originalIssueDate", "refCpiOnDatedDate", "offeringAmount", "totalAccepted"]
# offeringAmount = announced auction size ($); totalAccepted = amount actually issued ($, incl.
# SOMA add-ons). A tiny size flags a contingency/test auction (e.g. 2020-07-10 5y reopen = $25mn
# vs a real ~$14bn auction) — used in export.py to mark fake auctions.
# original tenors we track (TIPS legs and their nominal comparators)
TENORS = {"5-Year": "5y", "10-Year": "10y", "30-Year": "30y"}
# Below this offering size a TIPS "auction" is a contingency/test, not a real supply event
# (e.g. the 2020-07-10 5y reopening was $25mn vs a real ~$14bn; the next-smallest real auction
# is $5bn). Such auctions are excluded everywhere (flags + seasonal anchor) via real_tips_auctions.
MIN_AUCTION_SIZE = 1_000_000_000   # $1bn


def _get(url, params):
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _norm(df):
    # TIPS carry securityType "Note"/"Bond" with a separate tips="Yes" flag.
    leg = df.get("tips", pd.Series("No", index=df.index)).apply(
        lambda s: "tips" if str(s).strip().lower() == "yes" else "nominal")
    for c in KEEP:
        if c not in df:
            df[c] = None
    df = df[KEEP].copy()
    df["leg"] = leg.values
    for c in ("auctionDate", "issueDate", "datedDate", "maturityDate", "originalIssueDate"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("interestRate", "refCpiOnDatedDate", "offeringAmount", "totalAccepted"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def pull():
    os.makedirs(CACHE, exist_ok=True)
    frames = []
    this_year = 2026
    # The /auctioned endpoint silently caps at ~250 recent records (misses old TIPS &
    # nominals), so page EVERY type via /search on 2-year auctionDate windows. TIPS go
    # back to 1998 (30y) / ~2004 (5y,10y); nominals from START_YEAR.
    for typ, y_start in (("TIPS", 1998), ("Note", START_YEAR), ("Bond", START_YEAR)):
        for y0 in range(y_start, this_year + 1, 2):
            j = _get(f"{TA}/search", {"format": "json", "type": typ,
                                      "dateFieldName": "auctionDate",
                                      "startDate": f"{y0}-01-01", "endDate": f"{y0+1}-12-31"})
            if j:
                frames.append(pd.DataFrame(j))
    allrec = pd.concat(frames, ignore_index=True)
    df = _norm(allrec)
    # keep only the tenors we track; drop dupes (same cusip can appear via reopenings)
    df = df[df["originalSecurityTerm"].isin(TENORS)].copy()
    df["tenor"] = df["originalSecurityTerm"].map(TENORS)
    df = df.sort_values("auctionDate").drop_duplicates(["cusip", "auctionDate"])
    df.to_parquet(AUCTIONS_PARQUET)
    print(f"  wrote {AUCTIONS_PARQUET}: {len(df)} auctions "
          f"({df['auctionDate'].min().date()} .. {df['auctionDate'].max().date()})")
    for leg in ("tips", "nominal"):
        for ten in ("5y", "10y", "30y"):
            n = len(df[(df.leg == leg) & (df.tenor == ten)])
            print(f"    {leg:8s} {ten:4s}: {n} auctions")
    return df


def load_auctions():
    if not os.path.exists(AUCTIONS_PARQUET):
        return pull()
    return pd.read_parquet(AUCTIONS_PARQUET)


def real_tips_auctions(min_size=MIN_AUCTION_SIZE):
    """TIPS auctions with the contingency/test auctions removed (offeringAmount < min_size, e.g.
    the 2020-07-10 $25mn 5y reopening). NaN offering amounts are KEPT (treated as real). This is
    the single source of truth for 'a real TIPS auction' used by the export flags and the seasonal
    anchor, so a fake auction never lights up a flag or anchors a bucket."""
    a = load_auctions()
    a = a[a["leg"] == "tips"].copy()
    sz = a["offeringAmount"]
    return a[sz.isna() | (sz >= min_size)]


def otr_schedule():
    """Monthly OTR schedule per leg/tenor under the §9.2.1 convention.
    Returns long DataFrame: [month, leg, tenor, cusip, maturityDate, interestRate, auctionDate]."""
    a = load_auctions()
    months = pd.date_range("2003-01-01", "2026-07-01", freq="MS")
    rows = []
    for leg in ("tips", "nominal"):
        for ten in ("5y", "10y", "30y"):
            sub = a[(a.leg == leg) & (a.tenor == ten)].sort_values("auctionDate")
            if sub.empty:
                continue
            for m in months:
                # most recent auction strictly before this month start (old-OTR-through-auction-month)
                prior = sub[sub["auctionDate"] < m]
                if prior.empty:
                    continue
                last = prior.iloc[-1]
                rows.append({"month": m, "leg": leg, "tenor": ten, "cusip": last["cusip"],
                             "maturityDate": last["maturityDate"], "interestRate": last["interestRate"],
                             "auctionDate": last["auctionDate"]})
    sched = pd.DataFrame(rows)
    sched.to_parquet(SCHEDULE_PARQUET)
    return sched


def new_issues(leg, tenor):
    """Distinct NEW issues (a CUSIP's first/original issue) for a leg/tenor, sorted by
    issue date. One row per CUSIP: [cusip, issueDate, auctionDate]. Reopenings (same CUSIP,
    later issueDate) are collapsed to the original. This is the basis for issue-date-gated
    rolls (a bond may be used only on/after its issue date — reference: roll-fix spec)."""
    a = load_auctions()
    sub = a[(a.leg == leg) & (a.tenor == tenor)].copy()
    sub = sub.dropna(subset=["issueDate"]).sort_values("issueDate")
    sub = sub.drop_duplicates("cusip", keep="first")     # earliest issue = original new issue
    return sub[["cusip", "issueDate", "auctionDate"]].reset_index(drop=True)


def otr_universe():
    """Distinct CUSIPs that have ever held an OTR role (the bonds to pull prices for)."""
    s = otr_schedule()
    return s[["cusip", "leg", "tenor"]].drop_duplicates().reset_index(drop=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "schedule"
    if cmd == "pull":
        pull()
    elif cmd == "schedule":
        s = otr_schedule()
        print(f"OTR schedule: {len(s)} month/leg/tenor rows")
        # show the most recent few months across legs/tenors
        recent = s[s.month >= "2026-01-01"].sort_values(["month", "leg", "tenor"])
        with pd.option_context("display.max_rows", 60, "display.width", 160):
            print(recent.to_string(index=False))
    else:
        print(__doc__)
