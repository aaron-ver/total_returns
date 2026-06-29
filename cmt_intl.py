"""
(Pseudo-)constant-maturity bucket series for the linkers (desk spec, boss 2026-06).

A SELECTION + SPLICE layer over the existing per-bond returns -- it does not recompute returns.

Rules:
  * Buckets = remaining-tenor bands (buckets_intl ranges; FR/UK split the long end).
  * Each bucket holds ONE bond at a time = the most-recently issued/tapped bond whose remaining
    tenor is currently in the band, using taps known as of the PRIOR month-end (1-month roll lag).
  * If the held bond ages out of the band, switch to the most-recently-tapped bond still in the band
    (so a 25y that has rolled to ~20y can become the 20y bond if nothing fresher exists).
  * Gaps: gap="empty" (default) -> no holding, daily return 0 (cum flat) while the band is empty.
          gap="hold"            -> carry the last held bond (even once aged out) until a new in-band
                                   bond appears.
  * The bucket's daily return = the held bond's own daily return (outright financed bp from
    engine_intl, and breakeven r_BE_bp from breakeven_intl where available). Splicing returns (not
    levels) means a name switch never injects a jump. Held bond is constant within a calendar month.

Usage:
  python cmt_intl.py                 # build all market/bucket series -> cache_intl/cmt/ + summary
  python cmt_intl.py IT_BTPEI 10y    # one bucket: held-bond sequence + return summary
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import linkers
import issuance_intl as iss
import buckets_intl as bk
import engine_intl as eng

CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")


def held_monthly(market, gap="empty"):
    """{bucket: Series(index=month Period, value=held ISIN or NA)} per the roll rules."""
    fr = market in bk.FR_STYLE
    u = linkers.load_universe()
    matof = {i: pd.Timestamp(m) for i, m in zip(u["isin"], pd.to_datetime(u["maturity"]))}
    ev = iss.events(); ev = ev[ev["market"] == market]
    if ev.empty:
        return {}
    taps = {i: sorted(pd.to_datetime(s)) for i, s in ev.groupby("isin")["event_date"]}
    isins = list(taps)
    months = pd.period_range(ev["event_date"].min().to_period("M"),
                             pd.Timestamp.today().to_period("M"), freq="M")
    out = {b: {} for b in bk.ORDER}
    for mp in months:
        mstart = mp.to_timestamp()                              # first day of the month
        prev_end = mstart - pd.Timedelta(days=1)               # last day of prior month (roll lag)
        for b in bk.ORDER:
            cands = []
            for i in isins:
                mat = matof.get(i)
                if pd.isna(mat) or mat < mstart:
                    continue
                if bk.bucket((mat - mstart).days / 365.25, fr) != b:
                    continue
                prior = [t for t in taps[i] if t <= prev_end]   # known as of prior month-end
                if prior:
                    cands.append((prior[-1], mat, i))           # (last tap, maturity, isin)
            if cands:
                cands.sort()                                    # by last-tap, then maturity
                out[b][mp] = cands[-1][2]                       # most recently tapped (tiebreak: longer)
    held = {}
    for b in bk.ORDER:
        s = pd.Series(out[b], dtype=object).reindex(months)
        if gap == "hold":
            s = s.ffill()                                       # carry last bond across empty months
        if s.notna().any():
            held[b] = s
    return held


_RET = {}
def _bond_returns(isin):
    """(outright bp series, breakeven r_BE_bp series) for a bond, indexed by date (cached)."""
    if isin not in _RET:
        try:
            o = eng.load_returns(isin)["bp"]
        except Exception:
            o = pd.Series(dtype=float)
        bep = os.path.join(CACHE, "breakeven", f"{isin}.parquet")
        be = pd.read_parquet(bep)["r_BE_bp"] if os.path.exists(bep) else pd.Series(dtype=float)
        _RET[isin] = (o, be)
    return _RET[isin]


def build_market(market, gap="empty", save=True):
    """Daily CMT series for every populated bucket of a market. Returns {bucket: DataFrame}."""
    held = held_monthly(market, gap)
    if not held:
        return {}
    # daily trading-day grid = union of all held bonds' return dates
    used = set().union(*[set(s.dropna().unique()) for s in held.values()])
    days = sorted(set().union(*[set(_bond_returns(i)[0].index) for i in used if not _bond_returns(i)[0].empty]))
    days = pd.DatetimeIndex(days)
    res = {}
    for b, mser in held.items():
        held_isin = pd.Series(mser.reindex(days.to_period("M")).to_numpy(), index=days)  # held name per day
        r_bp = pd.Series(np.nan, index=days); r_be = pd.Series(np.nan, index=days)
        for isin in pd.unique(held_isin.dropna()):
            mask = held_isin == isin
            o, be = _bond_returns(isin)
            r_bp.loc[mask] = o.reindex(days[mask]).to_numpy()
            if not be.empty:
                r_be.loc[mask] = be.reindex(days[mask]).to_numpy()
        df = pd.DataFrame({"held_isin": held_isin, "r_bp": r_bp, "r_BE_bp": r_be}, index=days)
        df.index.name = "date"
        # cum: flat on empty/no-data days (return treated as 0 for accumulation)
        fv = df["held_isin"].first_valid_index()               # trim leading pre-holding days
        if fv is not None:
            df = df.loc[fv:]
        df["cum_bp"] = df["r_bp"].fillna(0).cumsum()
        df["cum_BE_bp"] = df["r_BE_bp"].fillna(0).cumsum()
        res[b] = df
        if save:
            os.makedirs(CMT_DIR, exist_ok=True)
            df.to_parquet(os.path.join(CMT_DIR, f"{market}__{b}.parquet"))
    return res


def build_all(gap="empty"):
    summ = []
    for m in bk.MARKETS:
        r = build_market(m, gap)
        for b, df in r.items():
            d = df[df["held_isin"].notna()]
            summ.append({"market": m, "bucket": b, "names": df["held_isin"].nunique(),
                         "days_held": int(df["held_isin"].notna().sum()),
                         "first": str(d.index.min().date()) if len(d) else "",
                         "last": str(d.index.max().date()) if len(d) else "",
                         "cum_bp": round(df["cum_bp"].iloc[-1], 0) if len(df) else 0,
                         "cum_BE_bp": round(df["cum_BE_bp"].iloc[-1], 0) if len(df) else 0})
    s = pd.DataFrame(summ)
    print(s.to_string(index=False))
    print(f"\n  wrote per-bucket series to {CMT_DIR}/<market>__<bucket>.parquet")
    return s


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 2:
        m, b = sys.argv[1], sys.argv[2]
        held = held_monthly(m)
        print(f"=== {m} {b}: held bond by month (roll-lagged, most-recently-tapped in band) ===")
        if b in held:
            seq = held[b].dropna()
            # collapse consecutive same-name to show the roll points
            chg = seq[seq != seq.shift()]
            u = linkers.load_universe(include_deferred=True).set_index("isin")
            for mp, isin in chg.items():
                print(f"  {mp}  -> {isin}  {u.loc[isin,'desc'] if isin in u.index else ''}")
            df = build_market(m).get(b)
            if df is not None:
                print(f"\n  days held: {df['held_isin'].notna().sum()}  cum outright={df['cum_bp'].iloc[-1]:+.0f}bp  "
                      f"cum breakeven={df['cum_BE_bp'].iloc[-1]:+.0f}bp")
        else:
            print("  (bucket never populated)")
    else:
        build_all()
