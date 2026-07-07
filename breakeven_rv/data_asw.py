"""
P0.5 (v3) — market-quoted asset-swap spreads for the 10y TIPS + nominal universe.

ASSET_SWAP_SPD_MID serves historically per CUSIP on this terminal (validated).
The quoted TIPS-vs-nominal ASW differential is the street's own "iota" measure, so
it is the construction cross-check for b_bond.py: corr(B_bond, asw_nominal − asw_tips)
should be materially positive (signs: TIPS trading cheap -> BE below swap -> B_bond
low -> TIPS ASW wide vs nominal).

Output: breakeven_rv/cache/tips_asw.parquet (long: date, cusip, leg, asw)

Usage:  python -m breakeven_rv.data_asw [pull|status]
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bbg
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "tips_asw.parquet")


def pull():
    config.ensure_dirs()
    ex = pd.read_csv(os.path.join(config.ROOT, "exports", "breakeven_10y.csv"),
                     usecols=["TIPS_cusip", "UST_cusip"])
    legs = {"tips": ex["TIPS_cusip"].dropna().unique(),
            "nominal": ex["UST_cusip"].dropna().unique()}
    today = pd.Timestamp.today().strftime("%Y%m%d")
    frames = []
    bbg.open_session()
    try:
        for leg, cusips in legs.items():
            secs = [f"{c} Govt" for c in cusips]
            for k in range(0, len(secs), 15):     # small batches: computed field is slow
                h = bbg.history(secs[k:k + 15], ["ASSET_SWAP_SPD_MID"], config.START, today)
                for sec, rows in h.items():
                    if not rows:
                        continue
                    df = pd.DataFrame(rows).rename(columns={"ASSET_SWAP_SPD_MID": "asw"})
                    df["date"] = pd.to_datetime(df["date"])
                    df["cusip"] = sec.split()[0]
                    df["leg"] = leg
                    frames.append(df)
                print(f"  {leg}: {min(k + 15, len(secs))}/{len(secs)} pulled", flush=True)
    finally:
        bbg.close_session()
    out = pd.concat(frames, ignore_index=True).dropna(subset=["asw"])
    out.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(out)} rows, {out['cusip'].nunique()} cusips "
          f"({out['date'].min().date()} .. {out['date'].max().date()})")
    return out


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.data_asw pull")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    pull()
