"""
P0.4 (v3) — when-issued yield snaps for true auction tails: STUB.

Not obtainable in this run: BBG auction-page WI deadline snaps have no historical
DAPI field (AUCTION_STOP_YIELD / WHEN_ISSUED_FLAG return nothing), and no desk
records were available. The high−median proxy (data_auctions.tail_median_bp)
remains in force everywhere.

If the desk sources WI snaps, drop a CSV at  breakeven_rv/inbox/wi_snaps.csv  with:
    cusip,auctionDate,wi_yield_1pm
    912810UH9,2025-02-20,2.395
and every consumer of true tails picks it up via load() (true_tail_bp = (stop − WI)*100
joined onto the auction panel by cusip+auctionDate).
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config

INBOX = os.path.join(config.DIR, "inbox", "wi_snaps.csv")


def load() -> pd.DataFrame | None:
    """Returns the WI snap table if the desk has provided one, else None."""
    if not os.path.exists(INBOX):
        return None
    df = pd.read_csv(INBOX, parse_dates=["auctionDate"])
    assert {"cusip", "auctionDate", "wi_yield_1pm"} <= set(df.columns)
    return df


if __name__ == "__main__":
    d = load()
    print("no WI snaps provided (stub active)" if d is None else d.head())
