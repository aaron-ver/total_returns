"""
FRED pull for the breakeven RV study (no key needed — public fredgraph csv endpoint).

Pulls the nominal curve (H.15), VIX, Fed broad USD, and the FRED constant-maturity
breakeven/real-yield series (used as cross-checks of the BBG generics).
These series are effectively unrevised (H.15 / CBOE closes); the one caveat is
DTWEXBGS (broad USD), whose weights re-benchmark annually — BBDXY (from BBG) is the
primary USD factor for that reason, DTWEXBGS the fallback/cross-check.

Output: breakeven_rv/cache/fred.parquet — one wide daily frame.

Usage:  python -m breakeven_rv.data_fred [pull|status]
"""
from __future__ import annotations
import io, os, sys
import urllib.request
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "fred.parquet")
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"


def _fetch(sid: str) -> pd.Series:
    with urllib.request.urlopen(URL.format(sid=sid), timeout=60) as r:
        raw = r.read().decode()
    df = pd.read_csv(io.StringIO(raw), na_values=["."])
    df.columns = ["date", sid]                      # header name varies (DATE/observation_date)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[sid]


def pull():
    config.ensure_dirs()
    frames = {}
    for name, sid in config.FRED_SERIES.items():
        s = _fetch(sid)
        frames[name] = s
        print(f"  {name:12s} {sid:10s} n={s.notna().sum():5d} "
              f"{str(s.index.min())[:10]} -> {str(s.index.max())[:10]}")
    out = pd.DataFrame(frames).sort_index()
    out.to_parquet(OUT)
    print(f"  wrote {OUT}  ({out.shape[0]} rows x {out.shape[1]} cols)")
    return out


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.data_fred pull")
    return pd.read_parquet(OUT)


def status():
    if os.path.exists(OUT):
        df = pd.read_parquet(OUT)
        print(f"fred.parquet: {df.shape[0]} rows x {df.shape[1]} cols, "
              f"{str(df.index.min())[:10]} -> {str(df.index.max())[:10]}")
        print(df.notna().sum().to_string())
    else:
        print("fred.parquet: missing")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    pull() if cmd == "pull" else status()
