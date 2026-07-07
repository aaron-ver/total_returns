"""
P0.2 (v3) — primary dealer TIPS net positions (NY Fed FR 2004, public API).

Weekly (Wednesday as-of) net outright positions in TIPS, by remaining-maturity
bucket, across the four series breaks that carry TIPS buckets (2013-04 onward;
earlier breaks had no TIPS position line — documented limitation, so the dealer
control only binds on the 2013+ auction subsample).

Vintage rule (same pattern as CPI): a week's figure is usable only from
`pub_date = asofdate + config.DEALER_PUB_LAG_D calendar days` (FR2004 publishes the
following Thursday, ~8d; 10d is conservative).

Series-break handling: levels are NOT comparable across breaks (reporting-panel and
definition changes — e.g. the 2021-12 -> 2022-01 jump). Downstream users must use
`total_z` (z-scored within each break) or within-break changes, never the raw level
across a break. Both are provided here.

Output: breakeven_rv/cache/dealer_tips_positions.parquet
  [asofdate, pub_date, seriesbreak, <bucket cols $mn>, total, total_z, total_chg_4w]

Usage:  python -m breakeven_rv.data_dealer [pull|status]
"""
from __future__ import annotations
import json, os, sys
import urllib.request
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config

OUT = os.path.join(config.CACHE, "dealer_tips_positions.parquet")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "python-breakeven-rv"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def pull():
    config.ensure_dirs()
    frames = []
    for sb in config.DEALER_SERIES_BREAKS:
        cols = {}
        for key in config.DEALER_TIPS_SERIES:
            j = _get(f"{config.NYFED_PD_API}/get/{sb}/timeseries/{key}.json")
            rows = j.get("pd", {}).get("timeseries", [])
            if rows:
                s = pd.Series({pd.Timestamp(r["asofdate"]): pd.to_numeric(r["value"], errors="coerce")
                               for r in rows}, name=key)
                cols[key] = s
        if cols:
            df = pd.DataFrame(cols)
            df["seriesbreak"] = sb
            frames.append(df)
            print(f"  {sb}: {len(df)} weeks  {df.index.min().date()} -> {df.index.max().date()}")
    out = pd.concat(frames).sort_index()
    out.index.name = "asofdate"
    bucket_cols = [c for c in config.DEALER_TIPS_SERIES if c in out.columns]
    out["total"] = out[bucket_cols].sum(axis=1)
    # z within each series break (levels not comparable across breaks)
    out["total_z"] = out.groupby("seriesbreak")["total"].transform(
        lambda s: (s - s.expanding(min_periods=26).mean()) / s.expanding(min_periods=26).std())
    chg = out.groupby("seriesbreak")["total"].diff(4)
    out["total_chg_4w"] = chg
    out["pub_date"] = out.index + pd.Timedelta(days=config.DEALER_PUB_LAG_D)
    out = out.reset_index()
    out.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(out)} weeks total")
    return out


def load_daily(index: pd.DatetimeIndex) -> pd.DataFrame:
    """As-known-on-date daily view (vintage-safe): each week's row becomes visible on
    pub_date; values forward-fill until the next publication."""
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.data_dealer pull")
    w = pd.read_parquet(OUT).set_index("pub_date").sort_index()
    cols = ["total", "total_z", "total_chg_4w"]
    return w[cols].reindex(index, method="ffill")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    pull() if (len(sys.argv) < 2 or sys.argv[1] == "pull") else None
