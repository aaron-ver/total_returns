"""
P0.3 (v3) — CPI fixings (market-implied monthly CPI prints).

What exists on this terminal: the USSWIF{1..12} family ("USD INFL CPI FIX <MON> 1Y")
— market-implied CPI-U NSA index levels for each upcoming calendar month. VERDICT
(documented in IMPLEMENTATION.md): the tickers resolve and quote live, but BBG
history begins 2025-01 (~18 months) — far too short for any backtest in this study.
No market-implied CORE breakeven series is entitled/resolvable (USGGBEC10 empty).
Consequence: Track 1's energy attribution keeps the gasoline-factor proxy; this
module still pulls and caches what exists so the series accumulates history going
forward, with a per-date quality flag.

Output: breakeven_rv/cache/cpi_fixings.parquet (long: date, ticker, value, months
of history available at pull time as the quality field)

Usage:  python -m breakeven_rv.data_fixings [pull|status]
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bbg
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "cpi_fixings.parquet")


def pull():
    config.ensure_dirs()
    today = pd.Timestamp.today().strftime("%Y%m%d")
    bbg.open_session()
    try:
        h = bbg.history(config.FIXINGS_TICKERS, ["PX_LAST"], "20100101", today)
    finally:
        bbg.close_session()
    frames = []
    for tic in config.FIXINGS_TICKERS:
        rows = h.get(tic, [])
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["ticker"] = tic
        frames.append(df)
    if not frames:
        print("  no fixings history served — nothing cached")
        return None
    out = pd.concat(frames, ignore_index=True).rename(columns={"PX_LAST": "value"})
    out["date"] = pd.to_datetime(out["date"])
    hist_months = (out["date"].max() - out["date"].min()).days / 30.4
    out["history_months_at_pull"] = round(hist_months, 1)
    out.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(out)} rows, {out['ticker'].nunique()} tickers, "
          f"history {out['date'].min().date()} -> {out['date'].max().date()} "
          f"(~{hist_months:.0f} months — {'USABLE' if hist_months >= 60 else 'TOO SHORT for research; accumulating'})")
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    pull()
