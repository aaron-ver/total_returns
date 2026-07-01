"""
Auction-cycle and calendar-seasonality tables for the intl linker buckets (baseline, no gilts on the
cycle). Reads the cmt_intl per-bucket caches (daily r_BE_bp / r_linker_bp + is_auction_date).

  * Auction cycle  -- event study around each bucket's OWN auctions (is_auction_date). For offsets
                      k = -W..+W trading days, the mean daily return is averaged across all the
                      bucket's auctions, then cumulated and rebased to 0 at k=0 (auction day) -> the
                      average within-cycle path (concession into / snap-back after the tap). Only the
                      non-gilt markets (IT/FR/ES) -- their auctions sit on a stable monthly session
                      across buckets; gilts auction on scattered days, so a fixed cycle is meaningless.
  * Calendar       -- mean monthly return by calendar month (Jan..Dec), across years -> the index-
                      seasonality signature. Computed for ALL markets (needs no auction schedule).

Usage:
  python seasonal_intl.py            # write exports/cmt/_seasonal_cycle.csv + _seasonal_calendar.csv
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import linkers
import buckets_intl as bk

CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
EXPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", "cmt")
# Cycle is an EVENT STUDY (anchored on each individual tap, then pooled) -> schedule-agnostic, so it
# works for gilts too despite their scattered calendar. Only Germany is excluded (no real tap history).
CYCLE_MKTS = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M"]
ALL = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M", "DE_EI"]
MET = {"be": "r_BE_bp", "out": "r_linker_bp"}
W = 21                                                       # compute +/- 21 td (~1 month each side);
# the dashboard narrows the shown range (10/15/21). NB euro taps are ~21 td apart, so beyond ~+/-10 the
# window overlaps the neighbouring auction -- the rebased path is width-independent, only the view changes.


def _load(market, bucket):
    p = os.path.join(CMT_DIR, f"{market}__{bucket}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


_CRUDE = None
def _crude_cum():
    """Cumulative Brent $/contract series (cumsum of the built front-month usd_per_contract), indexed
    by crude days — for the auction-cycle energy hedge. Empty Series if the crude cache is absent."""
    global _CRUDE
    if _CRUDE is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "crude_Brent.parquet")
        if os.path.exists(p):
            _CRUDE = pd.read_parquet(p)["usd_per_contract"].sort_index().cumsum()
        else:
            _CRUDE = pd.Series(dtype=float)
    return _CRUDE


def _crude_path(idx, apos, offs, n):
    """Per-event cumulative Brent $/contract path over the window, rebased to 0 on the auction day
    (asof-aligned to the bond offset dates). None if no crude cache."""
    cc = _crude_cum()
    if cc.empty:
        return None
    rows = []
    for p in apos:
        base = cc.asof(idx[p]); row = []
        for k in offs:
            j = p + k
            lvl = cc.asof(idx[j]) if 0 <= j < n else np.nan
            row.append(None if (pd.isna(lvl) or pd.isna(base)) else round(float(lvl - base), 1))
        rows.append(row)
    return rows


WINDOWS = ["full", "5y", "3y"]                               # sample windows (like the US dashboard)


def _since(win):
    return None if win == "full" else pd.Timestamp.today() - pd.DateOffset(years=int(win[:-1]))


def _rnd(a):
    return [None if (v != v) else round(float(v), 2) for v in a]


def _stats(P):
    """Per-offset cross-event distribution (mean/median/quartiles/10-90 whiskers) of the cumulative
    paths P [events x offsets]; None if no events."""
    if P.shape[0] == 0:
        return None
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        return {"mean": _rnd(np.nanmean(P, 0)), "med": _rnd(np.nanmedian(P, 0)),
                "q1": _rnd(np.nanpercentile(P, 25, axis=0)), "q3": _rnd(np.nanpercentile(P, 75, axis=0)),
                "lo": _rnd(np.nanpercentile(P, 10, axis=0)), "hi": _rnd(np.nanpercentile(P, 90, axis=0))}


def _leg_paths(d, col, apos, offs, z, n):
    """Per-event cumulative path (rebased to 0 at the auction day) for one leg; None if no data."""
    if col not in d or not d[col].notna().any():
        return None
    r = pd.to_numeric(d[col], errors="coerce").to_numpy()
    rows = []
    for p in apos:
        seg = np.zeros(len(offs)); oob = np.zeros(len(offs), bool)
        for i, k in enumerate(offs):
            j = p + k
            if 0 <= j < n:
                seg[i] = r[j] if not np.isnan(r[j]) else 0.0
            else:
                oob[i] = True
        c = np.cumsum(seg); c = c - c[z]; c[oob] = np.nan
        rows.append([None if (v != v) else round(float(v), 1) for v in c])
    return rows


def auction_cycle(market, bucket, w=W):
    """Per-EVENT cumulative leg paths around each of this bucket's taps (rebased to 0 on the auction
    day): out = linker leg, be = beta-1 breakeven leg, + the tap dates. Sample windows, beta and the
    cross-event distribution are all computed client-side from these (so beta/box respond live)."""
    d = _load(market, bucket)
    if d is None:
        return None
    apos = np.where(d["is_auction_date"].fillna(False).to_numpy())[0]
    if len(apos) < 3:
        return None
    n = len(d); offs = list(range(-w, w + 1)); z = offs.index(0)
    out = _leg_paths(d, "r_linker_bp", apos, offs, z, n)
    if out is None:
        return None
    return {"offsets": offs, "dates": [t.strftime("%Y-%m-%d") for t in d.index[apos]],
            "out": out, "be": _leg_paths(d, "r_BE_bp", apos, offs, z, n),
            "crude": _crude_path(d.index, apos, offs, n)}


def calendar(market, bucket):
    """Per-(year,month) monthly total returns per leg (out=linker, be=beta-1 breakeven). Sample
    windows, beta and the per-calendar-month distribution (mean/box) are computed client-side."""
    d = _load(market, bucket)
    if d is None:
        return None
    ym = d.index.to_period("M")
    allym = pd.period_range(ym.min(), ym.max(), freq="M")

    def monthly(col):
        if col not in d or not d[col].notna().any():
            return None
        s = pd.to_numeric(d[col], errors="coerce").groupby(ym).sum(min_count=10).reindex(allym)
        return [None if pd.isna(v) else round(float(v), 1) for v in s.values]

    out = monthly("r_linker_bp")
    if out is None:
        return None
    return {"ym": [str(p) for p in allym], "out": out, "be": monthly("r_BE_bp")}


def build(w=W):
    cyc, cal = {}, {}
    for m in ALL:
        for b in bk.ORDER:
            if _load(m, b) is None:
                continue
            c = calendar(m, b)
            if c:
                cal.setdefault(m, {})[b] = c
            if m in CYCLE_MKTS:
                ac = auction_cycle(m, b, w)
                if ac:
                    cyc.setdefault(m, {})[b] = ac
    today = pd.Timestamp.today()
    sampcut = {"full": None,
               "5y": (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d"),
               "3y": (today - pd.DateOffset(years=3)).strftime("%Y-%m-%d")}
    return {"cycle": cyc, "calendar": cal, "W": w, "windows": WINDOWS, "sampcut": sampcut}


def _arr(rows):
    return np.array([[np.nan if v is None else v for v in ev] for ev in rows], float) if rows else None


def _export():
    """CSV dumps of the FULL-sample window (breakeven+outright): cycle = per-offset distribution
    (recomputed from the per-event paths), calendar = per-month mean."""
    os.makedirs(EXPORTS, exist_ok=True)
    data = build()
    crows = []
    for m, bs in data["cycle"].items():
        for b, c in bs.items():
            so = _stats(_arr(c["out"])); sb = _stats(_arr(c["be"])) if c["be"] else None
            for i, k in enumerate(c["offsets"]):
                row = {"market": m, "bucket": b, "n_auctions": len(c["out"]), "bd_from_auction": k}
                for mk, st in (("be", sb), ("out", so)):
                    for s in ("mean", "med", "q1", "q3", "lo", "hi"):
                        row[f"{mk}_{s}"] = st[s][i] if st else None
                crows.append(row)
    pd.DataFrame(crows).to_csv(os.path.join(EXPORTS, "_seasonal_cycle.csv"), index=False)
    def _meanby(ym, arr):
        agg = {}
        if arr:
            for i, y in enumerate(ym):
                if arr[i] is not None:
                    agg.setdefault(int(y[5:7]), []).append(arr[i])
        return {mo: round(sum(v) / len(v), 2) for mo, v in agg.items()}
    lrows = []
    for m, bs in data["calendar"].items():
        for b, c in bs.items():
            mo_be, mo_out = _meanby(c["ym"], c["be"]), _meanby(c["ym"], c["out"])
            for mo in range(1, 13):
                lrows.append({"market": m, "bucket": b, "month": mo,
                              "mean_be_bp": mo_be.get(mo), "mean_out_bp": mo_out.get(mo)})
    pd.DataFrame(lrows).to_csv(os.path.join(EXPORTS, "_seasonal_calendar.csv"), index=False)
    print(f"  wrote {EXPORTS}/_seasonal_cycle.csv ({len(crows)} rows) + _seasonal_calendar.csv ({len(lrows)} rows)")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    _export()
