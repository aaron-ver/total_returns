"""
Phase 2 — daily fitted real-yield curve per issuer + per-bond residuals.

US v1: least-squares cubic B-spline of real yield on time-to-maturity, fit per day
across the full TIPS strip (cache/bond_quotes_full.parquet). The per-bond residual
(yld − fitted, in bp) is the primary RV object for Studies A/B.

Declared spec (logged in IMPLEMENTATION.md before any event analysis):
  - MIN_TAU = 1.0y: sub-1y linkers dropped (carry/index-accrual distortions dominate).
  - Interior knots from the candidate set [2, 3.5, 5, 7.5, 10, 20], keeping a candidate
    only if it lies strictly inside the day's tau range and every inter-knot segment
    holds >= 2 bonds (the 10-24y gap in the TIPS strip then drops the 20y knot
    automatically on days where the long cluster is thin).
  - MIN_BONDS_DAY = 6: thinner days are skipped (affects parts of 2004-2005).
  - Residuals are evaluated ONLY at bond points — the fitted values inside the
    10-24y maturity gap are never used as observations.
  - resid_chg_std_60d: trailing 60d std of the bond's daily residual changes
    (min 30 obs), for z-standardized event paths.

The TIPS-vs-swap/BE-space analogue reuses breakeven_rv/b_bond.py (already seasonally
adjusted, expanding) rather than refitting a BE curve — see PLAN.md Phase 2.

Output: cache/curve_residuals.parquet (date, cusip, tau, yld, fitted, resid_bp,
        resid_chg_std_60d) and cache/curve_pillars.parquet (fitted at fixed taus).
Usage:  python -m breakeven_structures.curves [build|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
from scipy.interpolate import LSQUnivariateSpline

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config, data_universe

OUT = os.path.join(config.CACHE, "curve_residuals.parquet")
OUT_PILLARS = os.path.join(config.CACHE, "curve_pillars.parquet")

MIN_TAU = 1.0
MIN_BONDS_DAY = 6
KNOT_CANDIDATES = [2.0, 3.5, 5.0, 7.5, 10.0, 20.0]
PILLARS = [2, 5, 7, 10, 20, 30]
STD_WINDOW = 60
STD_MIN_OBS = 30


def _day_knots(tau: np.ndarray) -> list[float]:
    """Candidate knots inside the day's range with >= 2 bonds in every segment."""
    lo, hi = tau.min(), tau.max()
    knots = [k for k in KNOT_CANDIDATES if lo + 0.25 < k < hi - 0.25]
    while knots:
        edges = [lo] + knots + [hi]
        counts = [((tau >= a) & (tau < b)).sum() for a, b in zip(edges[:-1], edges[1:])]
        counts[-1] += (tau == hi).sum()
        if min(counts) >= 2:
            return knots
        # drop the knot bounding the thinnest segment (prefer dropping the higher knot)
        i = int(np.argmin(counts))
        knots.pop(min(i, len(knots) - 1))
    return knots


def build():
    config.ensure_dirs()
    q = data_universe.load()
    q["tau"] = (q.maturity - q.date).dt.days / 365.25
    q = q[q.tau >= MIN_TAU].sort_values(["date", "tau"])

    res_frames, pillar_rows, skipped = [], [], 0
    for d, g in q.groupby("date", sort=True):
        g = g.dropna(subset=["yld"])
        if len(g) < MIN_BONDS_DAY:
            skipped += 1
            continue
        tau, yld = g.tau.values, g.yld.values
        knots = _day_knots(tau)
        try:
            sp = LSQUnivariateSpline(tau, yld, t=knots, k=3)
        except Exception:
            skipped += 1
            continue
        fitted = sp(tau)
        res_frames.append(pd.DataFrame({
            "date": d, "cusip": g.cusip.values, "tau": tau, "yld": yld,
            "fitted": fitted, "resid_bp": (yld - fitted) * 100.0}))
        row = {"date": d}
        for p in PILLARS:
            row[f"fit_{p}y"] = float(sp(p)) if tau.min() <= p <= tau.max() else np.nan
        pillar_rows.append(row)

    res = pd.concat(res_frames, ignore_index=True)
    # trailing std of daily residual CHANGES per bond (for z-standardized paths)
    res = res.sort_values(["cusip", "date"])
    chg = res.groupby("cusip")["resid_bp"].diff()
    res["resid_chg_std_60d"] = (chg.groupby(res["cusip"])
                                .transform(lambda s: s.rolling(STD_WINDOW, min_periods=STD_MIN_OBS).std()))
    res = res.sort_values(["date", "tau"]).reset_index(drop=True)

    res.to_parquet(OUT)
    pd.DataFrame(pillar_rows).set_index("date").to_parquet(OUT_PILLARS)
    print(f"  wrote {OUT}: {len(res):,} bond-days, {res.date.nunique():,} days "
          f"({res.date.min().date()} .. {res.date.max().date()}), {skipped} days skipped")
    print(f"  residual dispersion (bp): cross-sectional std by year:")
    disp = res.groupby(res.date.dt.year)["resid_bp"].std().round(1)
    print("  " + disp.to_string().replace("\n", "\n  "))
    return res


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_structures.curves build")
    return pd.read_parquet(OUT)


# ---------------------------------------------------------- leave-one-out residuals
# The panel residual is fit INCLUDING the bond, so the fit partially absorbs the
# bond's own dislocation — attenuating exactly the effect Study A measures on the
# auctioned bond. LOO refits the day's curve WITHOUT the bond and prices it off that.
_QCACHE: dict | None = None
_LOO_MEMO: dict = {}


def _quotes_by_day():
    global _QCACHE
    if _QCACHE is None:
        from breakeven_structures import data_universe
        q = data_universe.load()
        q["tau"] = (q.maturity - q.date).dt.days / 365.25
        q = q[q.tau >= MIN_TAU].dropna(subset=["yld"]).sort_values(["date", "tau"])
        _QCACHE = {d: g[["cusip", "tau", "yld"]] for d, g in q.groupby("date")}
    return _QCACHE


def loo_resid(cusip: str, date: pd.Timestamp) -> float:
    """Leave-one-out residual (bp) of `cusip` on `date`; NaN if not fittable."""
    key = (cusip, date)
    if key in _LOO_MEMO:
        return _LOO_MEMO[key]
    g = _quotes_by_day().get(date)
    out = np.nan
    if g is not None:
        own = g[g.cusip == cusip]
        rest = g[g.cusip != cusip]
        if len(own) == 1 and len(rest) >= MIN_BONDS_DAY:
            # collapse exact-duplicate maturities (e.g. an old 30y and a 5y sharing
            # an Apr-15 date) — fitpack needs increasing x
            agg = rest.groupby("tau", as_index=False)["yld"].mean()
            tau, yld = agg.tau.values, agg.yld.values
            o_tau, o_yld = float(own.tau.iloc[0]), float(own.yld.iloc[0])
            if tau.min() <= o_tau <= tau.max():         # never extrapolate
                try:
                    sp = LSQUnivariateSpline(tau, yld, t=_day_knots(tau), k=3)
                    out = (o_yld - float(sp(o_tau))) * 100.0
                except Exception:
                    pass
    _LOO_MEMO[key] = out
    return out


def load_pillars():
    return pd.read_parquet(OUT_PILLARS)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    else:
        r = load()
        print(f"{len(r):,} rows, {r.date.nunique()} days, resid std {r.resid_bp.std():.1f}bp")
