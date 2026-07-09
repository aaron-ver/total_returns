"""
US auction results — TIPS AND nominal 5/10/30 — with full internals + announcementDate.

Extends breakeven_rv/data_auctions.py (TIPS-only, no announcement date) per the
2026-07-09 directives:
  - nominal Note/Bond 5/10/30 auctions pulled with the same internals fieldset
    (Q2: separate event type + same-tenor contamination flag for TIPS windows);
  - announcementDate added to KEEP (verified served by the TreasuryDirect API,
    2026-07-08) — this is the second anchor for the dual-anchor US design.

Public API, no terminal needed. Same conventions as the breakeven_rv module:
contingency/test auctions < $1bn dropped; tail proxy = (high − median) since no
WI snap exists historically (see breakeven_rv/data_wi.py).

Output: breakeven_structures/cache/auctions_us.parquet — one row per auction
(TIPS + nominal, new issues AND reopenings).

Usage:  python -m breakeven_structures.data_auctions_us [pull|status]
"""
from __future__ import annotations
import os, sys
import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config

OUT = os.path.join(config.CACHE, "auctions_us.parquet")

KEEP = [
    "cusip", "securityType", "originalSecurityTerm", "securityTerm",
    "announcementDate", "auctionDate", "issueDate", "maturityDate", "datedDate",
    "reopening", "series",
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
DATES = ["announcementDate", "auctionDate", "issueDate", "maturityDate", "datedDate"]


def _pull_type(typ: str, y_start: int) -> list[pd.DataFrame]:
    """Page one security type in 2-year auctionDate windows (the API caps at ~250 rows)."""
    frames = []
    this_year = pd.Timestamp.today().year
    for y0 in range(y_start, this_year + 1, 2):
        j = requests.get(config.TD_API, params={
            "format": "json", "type": typ, "dateFieldName": "auctionDate",
            "startDate": f"{y0}-01-01", "endDate": f"{y0 + 1}-12-31"}, timeout=60).json()
        if j:
            frames.append(pd.DataFrame(j))
    return frames


def pull():
    config.ensure_dirs()
    frames = (_pull_type("TIPS", config.TIPS_START_YEAR)
              + _pull_type("Note", config.NOMINAL_START_YEAR)
              + _pull_type("Bond", config.NOMINAL_START_YEAR))
    df = pd.concat(frames, ignore_index=True)
    # TIPS come back from all three type queries (securityType Note/Bond + tips=Yes flag);
    # classify by the flag, then dedupe below.
    leg = df.get("tips", pd.Series("No", index=df.index)).apply(
        lambda s: "tips" if str(s).strip().lower() == "yes" else "nominal")
    for c in KEEP:
        if c not in df:
            df[c] = None
    df = df[KEEP].copy()
    df["leg"] = leg.values
    for c in DATES:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["originalSecurityTerm"].isin(config.TENOR_MAP)].copy()
    df["tenor"] = df["originalSecurityTerm"].map(config.TENOR_MAP)
    df["is_reopening"] = df["reopening"].astype(str).str.strip().str.lower().eq("yes")
    sz = df["offeringAmount"]
    df = df[sz.isna() | (sz >= config.MIN_AUCTION_SIZE)]
    df = (df.sort_values("auctionDate")
            .drop_duplicates(["cusip", "auctionDate"])
            .reset_index(drop=True))

    # derived measures (identical definitions to breakeven_rv/data_auctions.py)
    df["tail_median_bp"] = (df["highYield"] - df["averageMedianYield"]) * 100.0
    comp = df["competitiveAccepted"]
    df["dealer_pct"] = df["primaryDealerAccepted"] / comp * 100.0
    df["indirect_pct"] = df["indirectBidderAccepted"] / comp * 100.0
    df["direct_pct"] = df["directBidderAccepted"] / comp * 100.0
    df["announce_gap_bd"] = [
        len(pd.bdate_range(a, b)) - 1 if pd.notna(a) and pd.notna(b) else None
        for a, b in zip(df["announcementDate"], df["auctionDate"])]

    df.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(df)} auctions "
          f"({df['auctionDate'].min().date()} .. {df['auctionDate'].max().date()})")
    print(df.groupby(["leg", "tenor", "is_reopening"]).size().to_string())
    cov = df.groupby("leg")[["announcementDate", "bidToCoverRatio", "highYield",
                             "averageMedianYield", "dealer_pct"]].apply(
        lambda g: g.notna().mean() * 100).round(1)
    print("field coverage % by leg:\n" + cov.to_string())
    return df


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_structures.data_auctions_us pull")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        pull()
    else:
        df = load()
        print(f"{len(df)} auctions cached ({df['leg'].value_counts().to_dict()})")
