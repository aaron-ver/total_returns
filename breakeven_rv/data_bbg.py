"""
Bloomberg pull for the breakeven RV study (needs the terminal, like root data_layer.py).

Pulls daily history for the series in config.BBG_SERIES:
  - USSWIT zero-coupon CPI swap curve (Residual B + swap-space fair-value target)
  - USGGBE / USGGT constant-maturity TIPS breakevens & real yields (BBG generics)
  - MOVE, BBDXY

Output: breakeven_rv/cache/bbg.parquet — one wide daily frame, columns = config names.

Usage:  python -m breakeven_rv.data_bbg [pull|status]
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bbg
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "bbg.parquet")


def pull():
    config.ensure_dirs()
    today = pd.Timestamp.today().strftime("%Y%m%d")
    # one batched history request per field-set (all series share PX_LAST)
    tickers = {name: tic for name, (tic, _fld) in config.BBG_SERIES.items()}
    bbg.open_session()
    try:
        h = bbg.history(list(tickers.values()), ["PX_LAST"], config.START, today)
    finally:
        bbg.close_session()
    frames = []
    for name, tic in tickers.items():
        rows = h.get(tic, [])
        if not rows:
            print(f"  WARN no data for {name} ({tic})")
            continue
        df = pd.DataFrame(rows).rename(columns={"PX_LAST": name})[["date", name]]
        df["date"] = pd.to_datetime(df["date"])
        frames.append(df.set_index("date"))
        print(f"  {name:10s} {tic:18s} n={len(df):5d} "
              f"{str(df['date'].min())[:10]} -> {str(df['date'].max())[:10]}")
    out = pd.concat(frames, axis=1, sort=True).sort_index()
    out.to_parquet(OUT)
    print(f"  wrote {OUT}  ({out.shape[0]} rows x {out.shape[1]} cols)")
    return out


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.data_bbg pull")
    return pd.read_parquet(OUT)


def status():
    if os.path.exists(OUT):
        df = pd.read_parquet(OUT)
        print(f"bbg.parquet: {df.shape[0]} rows x {df.shape[1]} cols, "
              f"{str(df.index.min())[:10]} -> {str(df.index.max())[:10]}")
        print(df.notna().sum().to_string())
    else:
        print("bbg.parquet: missing")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    pull() if cmd == "pull" else status()
