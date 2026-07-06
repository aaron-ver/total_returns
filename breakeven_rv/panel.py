"""
Master daily panel for the breakeven RV study.

Joins the three pulls (bbg / fred / root-repo caches) into one business-day frame
and derives the model columns. Vintage discipline (plan §6):
  - every Layer-1 factor is a market close known same-day (slope, gasoline, VIX, USD);
  - CPI enters ONLY via `cpi_yoy_lagged`, shifted to its publication date
    (15th of month m+1, config.CPI_PUB_DAY) — never the reference month;
  - all series are actual daily closes, no revised macro data anywhere else.

Columns (key):
  targets    : swap_10y (primary FV space), be10 (TIPS space), iota5/10/30 (= BE − swap)
  L1 factors : slope_3m10y, log_gas, vix, log_usd
  L2 extras  : slope_2s10s, move, gcf_repo, cpi_yoy_lagged, be10_mom20, dgs10

Output: breakeven_rv/cache/panel.parquet

Usage:  python -m breakeven_rv.panel [build|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, data_bbg, data_fred

OUT = os.path.join(config.CACHE, "panel.parquet")


def _cpi_yoy_lagged(cpi_nsa: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """CPI y/y as KNOWN on each date: the print for month m is usable from the 15th
    of m+1 (conservative vs the actual BLS release day, ~10th-13th)."""
    cpi = cpi_nsa.dropna()
    yoy = cpi.pct_change(12) * 100.0
    pub = yoy.index + pd.DateOffset(months=1)
    pub = pub.map(lambda d: d.replace(day=config.CPI_PUB_DAY))
    known = pd.Series(yoy.values, index=pub).sort_index()
    return known.reindex(index, method="ffill")


def build():
    config.ensure_dirs()
    bbg_df = data_bbg.load()
    fred = data_fred.load()
    energy = pd.read_parquet(os.path.join(config.ROOT_CACHE, "energy_raw.parquet"))
    macro = pd.read_parquet(os.path.join(config.ROOT_CACHE, "macro.parquet"))

    # business-day spine = BBG dates (the target's calendar)
    idx = bbg_df.index[bbg_df.index.dayofweek < 5]
    p = pd.DataFrame(index=idx)

    # targets / swap curve / vol
    for c in bbg_df.columns:
        p[c] = bbg_df[c]
    # FRED factors: ffill(limit=3) bridges the FRED-vs-BBG holiday mismatch only
    for c in ("dgs3m", "dgs2", "dgs5", "dgs10", "dgs30", "vix", "usd_broad",
              "be10_fred", "be5_fred", "real10_fred"):
        p[c] = fred[c].reindex(idx).ffill(limit=3)
    p["xb1"] = energy["XB1"].reindex(idx).ffill(limit=3)
    p["gcf_repo"] = macro["gcf_treasury"].reindex(idx).ffill(limit=3)

    # derived — Layer 1 (Barclays four-factor)
    p["slope_3m10y"] = p["dgs10"] - p["dgs3m"]
    p["log_gas"] = np.log(p["xb1"])
    p["log_usd"] = np.log(p["bbdxy"])           # traded index, unrevised (vs DTWEXBGS re-weighting)
    # derived — Layer 2 / conditioning
    p["slope_2s10s"] = p["dgs10"] - p["dgs2"]
    p["cpi_yoy_lagged"] = _cpi_yoy_lagged(macro["cpi_nsa"], idx)
    p["be10_mom20"] = p["be10"] - p["be10"].shift(20)
    # Residual B raw material: iota = TIPS CM breakeven − matched-tenor ZC swap (in bp)
    for t in (5, 10, 30):
        p[f"iota{t}"] = (p[f"be{t}"] - p[f"swap_{t}y"]) * 100.0

    # cross-check the BBG CM breakeven against FRED's (should be ~identical)
    both = p[["be10", "be10_fred"]].dropna()
    corr = both["be10"].corr(both["be10_fred"])
    mad = (both["be10"] - both["be10_fred"]).abs().mean() * 100
    print(f"  cross-check be10 (BBG) vs T10YIE (FRED): corr={corr:.4f}, mean|diff|={mad:.1f}bp, n={len(both)}")

    p.to_parquet(OUT)
    core = p[["swap_10y", "be10"] + config.L1_FACTORS].dropna()
    print(f"  wrote {OUT}  ({p.shape[0]} rows x {p.shape[1]} cols)")
    print(f"  complete L1 rows: {len(core)}  {str(core.index.min())[:10]} -> {str(core.index.max())[:10]}")
    return p


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.panel build")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    else:
        df = load()
        print(df.notna().sum().to_string())
