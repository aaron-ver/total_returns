"""
TIPS auction internals from the TreasuryDirect public API (no key needed).

The root auctions.py pulls the calendar only (for the OTR schedule); this module
re-pulls TIPS auctions with the FULL result fields needed by the auction study:
bid-to-cover, high/low/median yield, dealer/direct/indirect takedown, SOMA, sizes.

Tail definition note (documented deviation): the classic auction tail is
(stop-out yield − 1pm WI quote), but the when-issued snap at the bid deadline is
not in any public source. Two proxies are built here:
  - tail_median_bp = (highYield − averageMedianYield) * 100
      auction-internal dispersion; available for every auction; standard proxy
      in the literature when WI data is missing.
  - the study also measures post-auction 1/3/5d moves (auction_study.py), which
    capture "performance" directly without the WI quote.

Output: breakeven_rv/cache/auctions_tips.parquet — one row per TIPS auction
(new issues AND reopenings; contingency/test auctions < $1bn dropped).

Usage:  python -m breakeven_rv.data_auctions [pull|status]
"""
from __future__ import annotations
import os, sys
import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "auctions_tips.parquet")

KEEP = [
    "cusip", "securityType", "originalSecurityTerm", "securityTerm",
    "auctionDate", "issueDate", "maturityDate", "reopening", "series",
    "offeringAmount", "totalAccepted", "totalTendered",
    "bidToCoverRatio", "highYield", "lowYield", "averageMedianYield",
    "competitiveAccepted", "competitiveTendered",
    "primaryDealerAccepted", "primaryDealerTendered",
    "directBidderAccepted", "indirectBidderAccepted",
    "somaAccepted", "somaIncluded", "interestRate",
]
NUM = ["offeringAmount", "totalAccepted", "totalTendered", "bidToCoverRatio",
       "highYield", "lowYield", "averageMedianYield", "competitiveAccepted",
       "competitiveTendered", "primaryDealerAccepted", "primaryDealerTendered",
       "directBidderAccepted", "indirectBidderAccepted", "somaAccepted", "interestRate"]
DATES = ["auctionDate", "issueDate", "maturityDate"]


def pull():
    config.ensure_dirs()
    frames = []
    this_year = pd.Timestamp.today().year
    # TIPS go back to 1997; page 2-year windows (the /auctioned endpoint caps at ~250 rows)
    for y0 in range(1997, this_year + 1, 2):
        j = requests.get(config.TD_API, params={
            "format": "json", "type": "TIPS", "dateFieldName": "auctionDate",
            "startDate": f"{y0}-01-01", "endDate": f"{y0 + 1}-12-31"}, timeout=60).json()
        if j:
            frames.append(pd.DataFrame(j))
    df = pd.concat(frames, ignore_index=True)
    for c in KEEP:
        if c not in df:
            df[c] = None
    df = df[KEEP].copy()
    for c in DATES:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["originalSecurityTerm"].isin(config.TENOR_MAP)].copy()
    df["tenor"] = df["originalSecurityTerm"].map(config.TENOR_MAP)
    df["is_reopening"] = df["reopening"].astype(str).str.strip().str.lower().eq("yes")
    # drop contingency/test auctions (e.g. the $25mn 2020-07-10 5y reopening)
    sz = df["offeringAmount"]
    df = df[sz.isna() | (sz >= config.MIN_AUCTION_SIZE)]
    df = df.sort_values("auctionDate").drop_duplicates(["cusip", "auctionDate"]).reset_index(drop=True)

    # derived outcome measures
    df["tail_median_bp"] = (df["highYield"] - df["averageMedianYield"]) * 100.0
    comp = df["competitiveAccepted"]
    df["dealer_pct"] = df["primaryDealerAccepted"] / comp * 100.0
    df["indirect_pct"] = df["indirectBidderAccepted"] / comp * 100.0
    df["direct_pct"] = df["directBidderAccepted"] / comp * 100.0

    df.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(df)} TIPS auctions "
          f"({df['auctionDate'].min().date()} .. {df['auctionDate'].max().date()})")
    print(df.groupby(["tenor", "is_reopening"]).size().to_string())
    cov = df[["bidToCoverRatio", "highYield", "averageMedianYield", "dealer_pct"]].notna().mean() * 100
    print("field coverage %:\n" + cov.round(1).to_string())
    return df


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.data_auctions pull")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        pull()
    else:
        df = load()
        print(f"{len(df)} auctions cached")
