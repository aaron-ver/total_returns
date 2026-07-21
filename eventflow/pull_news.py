"""
Headline-intensity series from GDELT's free DOC 2.0 API — 15-minute-resolution global news
volume for configurable queries (the "map the news stream" ask). No auth, no BBG.

Queries: iran (the geopolitical de-risking driver) + trump tariff/trade (the 'attacks' clock).
Fetched month-by-month since SAMPLE_START and merged into one parquet per query.

Run:  python eventflow/pull_news.py     -> eventflow/cache/news_<name>.parquet
      (index = UTC 15-min stamps; col 'vol' = % of global news volume matching the query)
"""
from __future__ import annotations
import os
import sys
import urllib.parse

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eventflow.common import CACHE, http_json

SAMPLE_START = "2026-01-01"          # a bit before the boss's Mar-1 start, for context
QUERIES = {
    "iran": '(iran OR israel iran OR strait of hormuz)',
    "trump": '(trump tariff OR trump trade OR trump fed OR trump powell)',
}


def _chunk(q, start, end, tries=4):
    import time
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote(q)
           + "&mode=timelinevol&format=json"
           + f"&STARTDATETIME={start:%Y%m%d%H%M%S}&ENDDATETIME={end:%Y%m%d%H%M%S}")
    time.sleep(10)                                 # GDELT rate limit (shared corporate IP): be extra polite
    for k in range(tries):
        try:
            j = http_json(url, timeout=90)
            break
        except Exception as e:
            if "429" in str(e) and k < tries - 1:
                time.sleep(20 * (k + 1)); continue
            raise
    tl = (j.get("timeline") or [{}])[0].get("data", [])
    if not tl:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(d["date"]): float(d["value"]) for d in tl})
    s.index = pd.DatetimeIndex(s.index)
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s


def pull_one(name, q):
    """Two layers, both merged into one cache: (a) DAILY-resolution coverage of the whole sample
    (the API coarsens long spans to daily — fine for the weekly hi/lo regime split); (b) the last
    week at FINE (15-min/hourly) resolution — the intraday clock accretes day by day as the cache
    merges, so the hour-of-day histogram keeps getting deeper. Failed chunks are SKIPPED (a 429
    mid-run keeps the successes; the daily pipeline rerun fills gaps)."""
    start = pd.Timestamp(SAMPLE_START, tz="UTC")
    end = pd.Timestamp.now(tz="UTC")
    parts, failed = [], 0
    cur = start
    while cur < end:
        nxt = min(cur + pd.DateOffset(months=2), end)   # bigger chunks = fewer requests
        try:
            parts.append(_chunk(q, cur, nxt))
        except Exception as e:
            failed += 1
            print(f"    {name} chunk {cur:%Y-%m} FAILED ({type(e).__name__}) — kept the rest")
        cur = nxt
    try:                                                # fine-resolution layer: last 7 days
        parts.append(_chunk(q, end - pd.Timedelta(days=7), end))
    except Exception:
        failed += 1
    if not any(len(p) for p in parts):
        raise RuntimeError(f"{name}: all chunks failed")
    s = pd.concat([p for p in parts if len(p)]).sort_index()
    s = s[~s.index.duplicated()]
    df = s.rename("vol").to_frame()
    p = os.path.join(CACHE, f"news_{name}.parquet")
    if os.path.exists(p):
        old = pd.read_parquet(p)
        df = pd.concat([old[~old.index.isin(df.index)], df]).sort_index()
    df.to_parquet(p)
    print(f"  {name}: {len(df)} x 15-min points  {df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d %H:%M} UTC"
          f"  (mean {df['vol'].mean():.3f}, max {df['vol'].max():.2f})")
    return df


def pull_all():
    for name, q in QUERIES.items():
        try:
            pull_one(name, q)
        except Exception as e:
            print(f"  {name}: FAILED {type(e).__name__}: {e}")


def load(name):
    p = os.path.join(CACHE, f"news_{name}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    pull_all()
