"""
Curated DB-ready 'marts' — flat, typed tables distilled from the built caches, ready to load into
Postgres/Redshift (or be read by future trade tooling) once AWS lands. One clean table per concept,
one row per observation, stable column names. Written as BOTH parquet (typed) and csv (portable) to
marts/, so the DB swap later is a `COPY`/`read_parquet` away — no reshaping.

Tables:
  mart_cmt        date x market x bucket -> held bond, nominal hedge, daily & cum returns, flags
  mart_hedge      market x bucket        -> full-sample Brent hedge ratio + R^2 (per leg + breakeven)
  mart_auctions   one row per tap        -> market, isin, date, type, amount, remaining tenor, bucket
  mart_bonds      one row per linker      -> isin, market, country, program, desc, maturity, first issue

Usage:  python marts.py        # (re)build all marts from the current caches
"""
from __future__ import annotations
import os, sys, glob
import pandas as pd

import linkers
import buckets_intl as bk
import issuance_intl as iss

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
MARTS = os.path.join(HERE, "marts")

CMT_COLS = ["market", "bucket", "date", "held_isin", "nominal_isin", "nominal_mat_gap_y",
            "r_linker_bp", "r_BE_bp", "cum_linker_bp", "cum_BE_bp",
            "is_roll_day", "is_auction_date", "auction_isin", "auction_amount", "auction_is_held"]


def mart_cmt():
    """Every constant-maturity bucket series, stacked long by (market, bucket, date)."""
    rows = []
    for f in sorted(glob.glob(os.path.join(CMT_DIR, "*.parquet"))):
        market, bucket = os.path.basename(f)[:-8].split("__")
        d = pd.read_parquet(f).reset_index().rename(columns={"index": "date", "linker_isin": "held_isin"})
        d["market"] = market; d["bucket"] = bucket
        rows.append(d.reindex(columns=CMT_COLS))
    if not rows:
        return pd.DataFrame(columns=CMT_COLS)
    return pd.concat(rows, ignore_index=True)


def mart_hedge():
    """Full-sample Brent energy-hedge ratios per bucket (contracts) + R^2. Empty if crude unavailable."""
    try:
        import energy_intl as en
    except Exception as e:
        print(f"  (mart_hedge skipped: {e})"); return pd.DataFrame()
    rows = []
    for m in en.MKT_ORDER:
        for b in bk.ORDER:
            fb = en.full_betas(m, b)
            if fb:
                rows.append(fb)
    return pd.DataFrame(rows)


def mart_auctions():
    """One row per new issue / tap, tagged with remaining tenor and its constant-maturity bucket."""
    u = linkers.load_universe(include_deferred=True)
    matof = {i: pd.Timestamp(m) for i, m in zip(u["isin"], pd.to_datetime(u["maturity"]))}
    fr_by = {m: (m in bk.FR_STYLE) for m in bk.MARKETS}
    ev = iss.events().copy()
    ev["event_date"] = pd.to_datetime(ev["event_date"])
    ev["mat"] = ev["isin"].map(matof)
    ev["remaining_tenor_y"] = ((ev["mat"] - ev["event_date"]).dt.days / 365.25).round(2)
    ev["bucket"] = [bk.bucket(y, fr_by.get(m, False)) for y, m in zip(ev["remaining_tenor_y"], ev["market"])]
    return ev[["market", "isin", "desc", "event_date", "type", "amt_bn",
               "remaining_tenor_y", "bucket", "source"]].sort_values(["market", "event_date"]).reset_index(drop=True)


def mart_bonds():
    """One row per linker in the universe."""
    u = linkers.load_universe(include_deferred=True).copy()
    keep = [c for c in ["isin", "market", "country", "program", "desc", "maturity", "first_issue", "coupon"] if c in u.columns]
    return u[keep].sort_values(["market", "maturity"]).reset_index(drop=True)


def _write(df, name):
    if df is None or df.empty:
        print(f"  (skip {name}: empty)"); return
    df.to_parquet(os.path.join(MARTS, f"{name}.parquet"))
    df.to_csv(os.path.join(MARTS, f"{name}.csv"), index=False)
    print(f"  {name:14s} {df.shape[0]:>7} rows x {df.shape[1]:>2} cols")


def build_all():
    os.makedirs(MARTS, exist_ok=True)
    print("building marts ->", MARTS)
    _write(mart_cmt(), "mart_cmt")
    _write(mart_hedge(), "mart_hedge")
    _write(mart_auctions(), "mart_auctions")
    _write(mart_bonds(), "mart_bonds")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build_all()
