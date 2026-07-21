"""
Hourly futures bars from Yahoo (CME front-month continuations) — the boss's suggested BBG
bypass. ~6 months of 1h history per request; the cache MERGES each pull so history accretes
forward the longer we run it. No Bloomberg, no auth. Open interest is NOT available here
(later: tiny BBG daily FUT_AGGTE_OPEN_INT pull when limits allow).

Tickers:  ZT=F 2y note (TU) · ZN=F 10y note (TY) · ZB=F classic bond (US) · UB=F ultra (WN)

Run:  python eventflow/pull_bars.py      -> eventflow/cache/bars_<sym>.parquet (UTC index)
"""
from __future__ import annotations
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eventflow.common import CACHE, http_json

TICKERS = {"TU": "ZT=F", "TY": "ZN=F", "US": "ZB=F", "WN": "UB=F"}


def pull_one(sym, ysym):
    j = http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
                  f"?interval=1h&range=730d&includePrePost=true")
    r = j["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                       "close": q["close"], "volume": q["volume"]},
                      index=pd.to_datetime(ts, unit="s", utc=True))
    df = df.dropna(subset=["close"])
    p = os.path.join(CACHE, f"bars_{sym}.parquet")
    if os.path.exists(p):
        old = pd.read_parquet(p)
        df = pd.concat([old[~old.index.isin(df.index)], df]).sort_index()
    df.to_parquet(p)
    print(f"  {sym} ({ysym}): {len(df)} bars  {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d %H:%M} UTC")
    return df


def pull_all():
    for sym, ysym in TICKERS.items():
        try:
            pull_one(sym, ysym)
        except Exception as e:
            print(f"  {sym}: FAILED {type(e).__name__}: {e}")


def load(sym):
    p = os.path.join(CACHE, f"bars_{sym}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    pull_all()
