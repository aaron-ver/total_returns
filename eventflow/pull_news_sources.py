"""
Article-level headline timestamps from official news APIs (no scraping) — NYT + Guardian.
Superior to GDELT for the hour-of-day clock: these are actual publish timestamps of individual
articles from reputable outlets, not a volume index. Complements (doesn't replace) GDELT.

*** LICENSE GATE — DISABLED BY DEFAULT (checked 2026-07-21) ***
Both providers' FREE API tiers are licensed for NON-COMMERCIAL use only. Internal research at
a fund is commercial use, so running these against free keys is a terms-of-service violation.
Guardian's free tier additionally forbids retaining content for more than 24 hours.
GDELT (eventflow/pull_news.py) explicitly permits unrestricted commercial use and remains the
backbone. To enable THIS module, the desk needs a commercial arrangement:
  Guardian: commercial tier via https://open-platform.theguardian.com (request commercial key)
  NYT:      https://nytlicensing.com / developer relations
Once compliance signs off, set:  setx NEWS_API_COMMERCIAL_OK 1
(plus GUARDIAN_API_KEY / NYT_API_KEY with the licensed keys).

Sources & access:
  GUARDIAN  Content API — GUARDIAN_API_KEY. ~1 req/sec, paginated search with publish timestamps.
  NYT       Archive API — NYT_API_KEY. One request per MONTH returns every NYT article that
            month (very light: ~7 calls for our whole sample); we filter locally by keyword.

Output: eventflow/cache/articles_<source>_<query>.parquet  (one row per article: ts, headline)
The dashboard turns these into hour-of-day clocks alongside GDELT.

Run:  python eventflow/pull_news_sources.py            # both sources, all queries
      python eventflow/pull_news_sources.py guardian   # one source
"""
from __future__ import annotations
import os
import sys
import time
import urllib.parse

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eventflow.common import CACHE, http_json

SAMPLE_START = pd.Timestamp("2026-01-01")
QUERIES = {                                               # query -> (ANY of, AND all of)
    "iran": (["iran"], []),
    "trump": (["tariff", "tariffs", "trade war", "fed", "powell"], ["trump"]),
}


def _match(text, terms):
    any_of, all_of = terms
    return all(t in text for t in all_of) and any(t in text for t in any_of)


def _guardian_q(terms):
    any_of, all_of = terms
    q = "(" + " OR ".join(f'"{t}"' for t in any_of) + ")"
    for t in all_of:
        q = f'"{t}" AND ' + q
    return urllib.parse.quote(q)
GUARDIAN_KEY = os.environ.get("GUARDIAN_API_KEY")
NYT_KEY = os.environ.get("NYT_API_KEY")
LICENSED = os.environ.get("NEWS_API_COMMERCIAL_OK") == "1"

LICENSE_MSG = ("  DISABLED: NYT/Guardian free API tiers are non-commercial only (institutional "
               "use needs a license — see module docstring). GDELT remains the news source. "
               "After compliance sign-off: setx NEWS_API_COMMERCIAL_OK 1")


def _save(source, query, rows):
    if not rows:
        print(f"  {source}/{query}: no articles"); return
    df = pd.DataFrame(rows).drop_duplicates(subset=["ts"]).sort_values("ts")
    p = os.path.join(CACHE, f"articles_{source}_{query}.parquet")
    if os.path.exists(p):
        old = pd.read_parquet(p)
        df = pd.concat([old, df]).drop_duplicates(subset=["ts"]).sort_values("ts")
    df.to_parquet(p, index=False)
    print(f"  {source}/{query}: {len(df)} articles  {df['ts'].iloc[0]:%Y-%m-%d} -> {df['ts'].iloc[-1]:%Y-%m-%d}")


def _last_ts(source, query):
    d = load(source, query)
    return None if d is None or d.empty else pd.Timestamp(d["ts"].iloc[-1])


def pull_guardian(incremental=False):
    """Guardian search API, paginated per query. ~1 req/sec."""
    if not LICENSED or not GUARDIAN_KEY:
        print(LICENSE_MSG)
        return
    for query, terms in QUERIES.items():
        start = SAMPLE_START
        if incremental:
            last = _last_ts("guardian", query)
            if last is not None:
                start = last.tz_localize(None) - pd.Timedelta(days=1)
        rows = []
        q = _guardian_q(terms)
        page, pages = 1, 1
        while page <= min(pages, 40):                     # hard cap: 40 pages/query per run
            # order-by=oldest: if the cap hits, the next (incremental) run continues forward
            u = (f"https://content.guardianapis.com/search?q={q}&from-date={start:%Y-%m-%d}"
                 f"&order-by=oldest&page-size=50&page={page}&api-key={GUARDIAN_KEY}")
            try:
                r = http_json(u, timeout=45)["response"]
            except Exception as e:
                print(f"  guardian/{query} page {page}: {type(e).__name__} — stopping (partial kept)")
                break
            pages = r["pages"]
            for a in r["results"]:
                rows.append({"ts": pd.Timestamp(a["webPublicationDate"]),
                             "headline": a.get("webTitle", "")[:160]})
            page += 1
            time.sleep(1.1)
        if pages > 40 and page > 40:
            print(f"  guardian/{query}: page cap hit ({pages} pages total) — run again to continue")
        _save("guardian", query, rows)


def pull_nyt(incremental=False):
    """NYT Archive API: one request per month -> filter locally. Needs NYT_API_KEY."""
    if not LICENSED or not NYT_KEY:
        print(LICENSE_MSG)
        return
    start = SAMPLE_START
    if incremental:
        last = _last_ts("nyt", "iran")
        if last is not None:
            start = last.tz_localize(None)                # re-scan only the current month(s)
    months = pd.period_range(start, pd.Timestamp.today(), freq="M")
    hits = {q: [] for q in QUERIES}
    for m in months:
        u = f"https://api.nytimes.com/svc/archive/v1/{m.year}/{m.month}.json?api-key={NYT_KEY}"
        try:
            docs = http_json(u, timeout=120)["response"]["docs"]
        except Exception as e:
            print(f"  nyt {m}: {type(e).__name__} — skipped"); time.sleep(12); continue
        for d in docs:
            text = " ".join([d.get("headline", {}).get("main") or "",
                             d.get("abstract") or ""]).lower()
            for query, terms in QUERIES.items():
                if _match(text, terms):
                    hits[query].append({"ts": pd.Timestamp(d["pub_date"]),
                                        "headline": (d.get("headline", {}).get("main") or "")[:160]})
        print(f"  nyt {m}: scanned {len(docs)} articles")
        time.sleep(12)                                    # NYT free tier: 5 req/min
    for query, rows in hits.items():
        _save("nyt", query, rows)


def load(source, query):
    p = os.path.join(CACHE, f"articles_{source}_{query}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


def pull_all():
    pull_guardian()
    pull_nyt()


def pull_daily():
    """Cheap incremental top-up for the daily pipeline: Guardian since last cached article,
    NYT current month only. No-op until licensed AND the full backfill has been run."""
    if not LICENSED:
        return
    if GUARDIAN_KEY and _last_ts("guardian", "iran") is not None:
        pull_guardian(incremental=True)
    if NYT_KEY and _last_ts("nyt", "iran") is not None:
        pull_nyt(incremental=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "guardian"):
        pull_guardian(incremental=True)   # resumes from last cached article; first run = full backfill
    if which in ("all", "nyt"):
        pull_nyt(incremental=True)
