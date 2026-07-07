"""
P0.1 (v3) — per-CUSIP TIPS + nominal bond quotes, consolidated.

Source (a) succeeded: the repo's existing per-CUSIP Bloomberg pulls
(root cache/daily/{cusip}.parquet, YLD_YTM_MID + PX_CLEAN_MID, from each bond's
issue date) already cover EVERY CUSIP the engine has ever held for the 5y/10y/30y
breakeven series — verified zero gaps. This module consolidates them into one
long frame with static reference (maturity, coupon) attached.
Convention: BBG mid closes, T+1 settle yields (same convention as the engine).
Fallbacks (b) FINRA TRACE (TIPS from 2019) / (c) CRSP-WRDS were not needed.

Output: breakeven_rv/cache/tips_bond_quotes.parquet
  [date, cusip, leg, tenor, yld, px_clean, maturity, coupon]

Usage:  python -m breakeven_rv.data_bonds [build|status]
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "tips_bond_quotes.parquet")


def build():
    config.ensure_dirs()
    frames = []
    seen = set()
    for ten in ("5y", "10y", "30y"):
        ex = pd.read_csv(os.path.join(config.ROOT, "exports", f"breakeven_{ten}.csv"),
                         usecols=["TIPS_cusip", "UST_cusip"])
        for leg, col in (("tips", "TIPS_cusip"), ("nominal", "UST_cusip")):
            for cusip in ex[col].dropna().unique():
                if cusip in seen:
                    continue
                seen.add(cusip)
                dpath = os.path.join(config.ROOT_CACHE, "daily", f"{cusip}.parquet")
                spath = os.path.join(config.ROOT_CACHE, "static", f"{cusip}.parquet")
                if not os.path.exists(dpath):
                    print(f"  WARN missing daily file for {cusip}")
                    continue
                d = pd.read_parquet(dpath)
                st = pd.read_parquet(spath).iloc[0] if os.path.exists(spath) else {}
                f = pd.DataFrame({
                    "date": d.index, "cusip": cusip, "leg": leg, "tenor": ten,
                    "yld": d.get("YLD_YTM_MID"), "px_clean": d.get("PX_CLEAN_MID")})
                f["maturity"] = pd.Timestamp(st.get("MATURITY")) if st.get("MATURITY") is not None else pd.NaT
                f["coupon"] = st.get("CPN")
                frames.append(f)
    out = pd.concat(frames, ignore_index=True).dropna(subset=["yld"])
    out.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(out)} rows, {out['cusip'].nunique()} cusips "
          f"({out['date'].min().date()} .. {out['date'].max().date()})")
    print(out.groupby(["leg", "tenor"])["cusip"].nunique().to_string())
    return out


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.data_bonds build")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build() if (len(sys.argv) < 2 or sys.argv[1] == "build") else print(load().head())
