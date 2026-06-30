"""
Auction-cycle and calendar-seasonality tables for the intl linker buckets (baseline, no gilts on the
cycle). Reads the cmt_intl per-bucket caches (daily r_BE_bp / r_linker_bp + is_auction_date).

  * Auction cycle  -- event study around each bucket's OWN auctions (is_auction_date). For offsets
                      k = -W..+W trading days, the mean daily return is averaged across all the
                      bucket's auctions, then cumulated and rebased to 0 at k=0 (auction day) -> the
                      average within-cycle path (concession into / snap-back after the tap). Only the
                      non-gilt markets (IT/FR/ES) -- their auctions sit on a stable monthly session
                      across buckets; gilts auction on scattered days, so a fixed cycle is meaningless.
  * Calendar       -- mean monthly return by calendar month (Jan..Dec), across years -> the index-
                      seasonality signature. Computed for ALL markets (needs no auction schedule).

Usage:
  python seasonal_intl.py            # write exports/cmt/_seasonal_cycle.csv + _seasonal_calendar.csv
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import linkers
import buckets_intl as bk

CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
EXPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", "cmt")
# Cycle is an EVENT STUDY (anchored on each individual tap, then pooled) -> schedule-agnostic, so it
# works for gilts too despite their scattered calendar. Only Germany is excluded (no real tap history).
CYCLE_MKTS = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M"]
ALL = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M", "DE_EI"]
MET = {"be": "r_BE_bp", "out": "r_linker_bp"}
W = 10                                                       # event window: +/- 10 trading days


def _load(market, bucket):
    p = os.path.join(CMT_DIR, f"{market}__{bucket}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


def auction_cycle(market, bucket, w=W):
    """Average within-cycle cumulative path per metric (rebased to 0 on the auction day)."""
    d = _load(market, bucket)
    if d is None:
        return None
    apos = np.where(d["is_auction_date"].fillna(False).to_numpy())[0]
    if len(apos) < 3:
        return None
    n = len(d); offs = list(range(-w, w + 1))
    res = {"offsets": offs, "n": int(len(apos))}
    for mk, col in MET.items():
        if col not in d or not d[col].notna().any():
            res[mk] = None; continue
        r = pd.to_numeric(d[col], errors="coerce").to_numpy()
        dm = []
        for k in offs:
            v = [r[p + k] for p in apos if 0 <= p + k < n and not np.isnan(r[p + k])]
            dm.append(np.mean(v) if v else np.nan)
        cum = np.nancumsum(np.nan_to_num(dm))
        cum = cum - cum[offs.index(0)]                       # rebase to auction day
        res[mk] = [round(float(c), 2) for c in cum]
    return res


def calendar(market, bucket):
    """Mean monthly return by calendar month (Jan..Dec) per metric, across years."""
    d = _load(market, bucket)
    if d is None:
        return None
    ym = d.index.to_period("M")
    res = {"months": list(range(1, 13))}
    nyr = 0
    for mk, col in MET.items():
        if col not in d or not d[col].notna().any():
            res[mk] = None; continue
        s = pd.to_numeric(d[col], errors="coerce")
        monthly = s.groupby(ym).sum(min_count=10)            # need >=10 obs to count a month
        bym = monthly.groupby(monthly.index.month).mean()
        nyr = max(nyr, monthly.index.year.nunique())
        res[mk] = [None if m not in bym.index or pd.isna(bym[m]) else round(float(bym[m]), 2)
                   for m in range(1, 13)]
    res["years"] = int(nyr)
    return res


def build(w=W):
    cyc, cal = {}, {}
    for m in ALL:
        for b in bk.ORDER:
            if _load(m, b) is None:
                continue
            c = calendar(m, b)
            if c:
                cal.setdefault(m, {})[b] = c
            if m in CYCLE_MKTS:
                ac = auction_cycle(m, b, w)
                if ac:
                    cyc.setdefault(m, {})[b] = ac
    return {"cycle": cyc, "calendar": cal, "W": w}


def _export():
    os.makedirs(EXPORTS, exist_ok=True)
    data = build()
    crows = []
    for m, bs in data["cycle"].items():
        for b, c in bs.items():
            for i, k in enumerate(c["offsets"]):
                crows.append({"market": m, "bucket": b, "n_auctions": c["n"], "bd_from_auction": k,
                              "cum_be_bp": c["be"][i] if c["be"] else None,
                              "cum_out_bp": c["out"][i] if c["out"] else None})
    pd.DataFrame(crows).to_csv(os.path.join(EXPORTS, "_seasonal_cycle.csv"), index=False)
    lrows = []
    for m, bs in data["calendar"].items():
        for b, c in bs.items():
            for i, mo in enumerate(c["months"]):
                lrows.append({"market": m, "bucket": b, "years": c.get("years"), "month": mo,
                              "mean_be_bp": c["be"][i] if c["be"] else None,
                              "mean_out_bp": c["out"][i] if c["out"] else None})
    pd.DataFrame(lrows).to_csv(os.path.join(EXPORTS, "_seasonal_calendar.csv"), index=False)
    print(f"  wrote {EXPORTS}/_seasonal_cycle.csv ({len(crows)} rows) + _seasonal_calendar.csv ({len(lrows)} rows)")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    _export()
