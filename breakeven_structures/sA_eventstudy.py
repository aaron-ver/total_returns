"""
Study A — supply event study on curve residuals (US TIPS v1).

For every TIPS auction event: the cumulative path of the relevant bond's curve
residual (bp, cheap = +) over t-T_PRE .. t+T_POST business days around the auction.

Event bond:
  reopenings : the auctioned CUSIP itself (full pre+post path observable).
  new issues : mean path of the TWO nearest-maturity existing bonds (the bond has no
               pre-history; per PLAN.md the new bond's own post-issue path is Study B
               territory). Cells reported separately — never pooled with reopens.

Paths (per directive 1 — CPI dummy, don't drop):
  path_raw   cumulative residual change from t-T_PRE
  path_xcpi  same with CPI-print-day changes zeroed (PRIMARY — exact FRED dates)
  path_z     daily changes / the bond's trailing 60d resid-change std at window start

Declared scalar stats (hypotheses ex ante, PLAN.md Phase 3):
  concession   = path_xcpi(t0)        (>0 = cheapens into the auction)
  retrace_h    = path_xcpi(t+h) - path_xcpi(t0), h in POST_HORIZONS (<0 = richens after)
  supply-DV01 scaling: Spearman corr(stat, supply_dv01_mm_bp) per cell.

Inference:
  - event-resampled bootstrap (N_BOOT) 95% CI on the mean path;
  - placebo: per event, up to PLACEBO_PER_EVENT anchors on the SAME bond, same
    month-of-year, >= PLACEBO_MIN_GAP_BD from any same-bucket TIPS auction, full window
    coverage; placebo distribution = N_PLACEBO draws of one-per-event; one-sided
    p-values in the hypothesized direction. Effects carried forward ONLY past placebo.
  - eras: full / train / holdout (in_holdout label); holdout-era placebo reported
    separately (directive 2).

Outputs: reports/sA_paths.csv, reports/sA_stats.csv
Usage:   python -m breakeven_structures.sA_eventstudy [run|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config, curves, data_events

OUT_PATHS = os.path.join(config.REPORTS, "sA_paths.csv")
OUT_STATS = os.path.join(config.REPORTS, "sA_stats.csv")

PLACEBO_PER_EVENT = 8
PLACEBO_MIN_GAP_BD = 15
ANCHOR_TOL_BD = 2          # anchor must be a curve-fit day within this tolerance
RNG_SEED = 43


# ---------------------------------------------------------------- panel plumbing
def _panels():
    r = curves.load()
    resid = r.pivot(index="date", columns="cusip", values="resid_bp")
    std60 = r.pivot(index="date", columns="cusip", values="resid_chg_std_60d")
    tau = r.pivot(index="date", columns="cusip", values="tau")
    return resid, std60, tau


def _window(resid: pd.DataFrame, cusip: str, pos: int, loo: bool = False):
    """Residual level series at positions pos-T_PRE .. pos+T_POST (None if incomplete).
    loo=True prices the bond off a curve fit WITHOUT it (curves.loo_resid) — used for
    the auctioned bond itself, where the in-fit residual absorbs its own dislocation."""
    lo, hi = pos - config.T_PRE, pos + config.T_POST
    if lo < 0 or hi >= len(resid.index):
        return None
    dates = resid.index[lo:hi + 1]
    if loo:
        w = pd.Series([curves.loo_resid(cusip, d) for d in dates], index=dates)
    else:
        if cusip not in resid.columns:
            return None
        w = resid[cusip].iloc[lo:hi + 1]
    if w.isna().any():
        return None
    return w


def _paths_from_window(w: pd.Series, cpi_days: set, std0: float):
    """(path_raw, path_xcpi, path_z) as np arrays indexed t-T_PRE..t+T_POST."""
    chg = w.diff().fillna(0.0)
    is_print = np.array([d in cpi_days for d in w.index])
    chg_x = chg.where(~is_print, 0.0)
    raw = chg.cumsum().values
    xcpi = chg_x.cumsum().values
    z = (chg_x / std0).cumsum().values if std0 and np.isfinite(std0) and std0 > 0 \
        else np.full(len(w), np.nan)
    return raw - raw[0], xcpi - xcpi[0], z - (z[0] if np.isfinite(z[0]) else 0)


def _nearest_pos(dates: pd.DatetimeIndex, anchor: pd.Timestamp):
    i = dates.searchsorted(anchor)
    for j in (i, i - 1, i + 1):
        if 0 <= j < len(dates) and abs(len(pd.bdate_range(min(dates[j], anchor),
                                                          max(dates[j], anchor))) - 1) <= ANCHOR_TOL_BD:
            return j
    return None


# ---------------------------------------------------------------- event assembly
def _event_bonds(ev_row, resid, tau, pos):
    """CUSIP(s) whose residual path represents this event at panel position pos."""
    if ev_row.is_reopening:
        return [ev_row.bond_id] if ev_row.bond_id in resid.columns else []
    # new issue: two nearest-maturity existing bonds at the anchor
    day_tau = tau.iloc[pos].dropna()
    day_tau = day_tau[day_tau.index != ev_row.bond_id]
    if day_tau.empty:
        return []
    target = float(ev_row.remaining_y)
    return day_tau.sub(target).abs().nsmallest(2).index.tolist()


def _collect(events, resid, std60, tau, cpi_days):
    """One record per usable event: paths + metadata."""
    recs = []
    dates = resid.index
    for e in events.itertuples():
        pos = _nearest_pos(dates, e.anchor_date)
        if pos is None:
            continue
        bonds = _event_bonds(e, resid, tau, pos)
        paths = []
        for b in bonds:
            w = _window(resid, b, pos, loo=bool(e.is_reopening))
            if w is None:
                continue
            std0 = std60[b].iloc[pos - config.T_PRE] if b in std60.columns else np.nan
            paths.append(_paths_from_window(w, cpi_days, std0))
        if not paths:
            continue
        raw = np.nanmean([p[0] for p in paths], axis=0)
        xcpi = np.nanmean([p[1] for p in paths], axis=0)
        z = np.nanmean([p[2] for p in paths], axis=0)
        recs.append({"bond_id": e.bond_id, "anchor_date": e.anchor_date, "pos": pos,
                     "bucket": e.bucket, "kind": "reopen" if e.is_reopening else "new_issue",
                     "in_holdout": e.in_holdout, "supply_dv01": e.supply_dv01_mm_bp,
                     "bonds_used": bonds, "raw": raw, "xcpi": xcpi, "z": z})
    return recs


# ---------------------------------------------------------------- placebo machinery
def _placebo_pool(rec, events, resid, std60, tau, cpi_days, rng):
    """Placebo paths for one event: same bond(s) construction, same month-of-year,
    away from same-bucket auctions, full window coverage."""
    dates = resid.index
    same_bucket = events[(events.bucket == rec["bucket"])].anchor_date.values
    # month +/-1 (with wrap), NOT exact month: TIPS auctions recur in the same months
    # every year, so exact-month matching + auction exclusion is empty by construction
    # (documented deviation from the draft's calendar-month matching).
    m = rec["anchor_date"].month
    months = {(m + 10) % 12 + 1, m, m % 12 + 1}
    cand = np.where(dates.month.isin(list(months)))[0]
    cand = cand[(cand >= config.T_PRE) & (cand < len(dates) - config.T_POST)]
    if len(cand) == 0:
        return []
    # exclude anchors near any same-bucket auction
    cd = dates[cand].values
    near = np.zeros(len(cand), dtype=bool)
    for a in same_bucket:
        near |= np.abs((cd - a).astype("m8[D]").astype(int)) <= PLACEBO_MIN_GAP_BD * 1.6
    cand = cand[~near]
    rng.shuffle(cand)
    out = []
    loo = rec["kind"] == "reopen"          # placebo mirrors the event construction
    for pos in cand:
        paths = []
        for b in rec["bonds_used"]:
            w = _window(resid, b, pos, loo=loo)
            if w is None:
                continue
            std0 = std60[b].iloc[pos - config.T_PRE] if b in std60.columns else np.nan
            paths.append(_paths_from_window(w, cpi_days, std0))
        if len(paths) == len(rec["bonds_used"]):
            out.append(np.nanmean([p[1] for p in paths], axis=0))   # xcpi only
        if len(out) >= PLACEBO_PER_EVENT:
            break
    return out


def _placebo_pvals(recs, pools, stat_fns, rng):
    """One-per-event placebo draws (N_PLACEBO); one-sided p per declared stat."""
    usable = [(r, p) for r, p in zip(recs, pools) if len(p) >= 3]
    if len(usable) < config.MIN_CELL_N:
        return {k: np.nan for k in stat_fns}, len(usable)
    obs = {k: np.mean([fn(r["xcpi"]) for r, _ in usable]) for k, fn in stat_fns.items()}
    draws = {k: np.empty(config.N_PLACEBO) for k in stat_fns}
    for i in range(config.N_PLACEBO):
        sample = [p[rng.integers(len(p))] for _, p in usable]
        for k, fn in stat_fns.items():
            draws[k][i] = np.mean([fn(s) for s in sample])
    pv = {}
    for k in stat_fns:
        d = draws[k]
        # one-sided in the hypothesized direction: concession +, retrace -
        pv[k] = float((d >= obs[k]).mean()) if k == "concession" else float((d <= obs[k]).mean())
    return pv, len(usable)


# ---------------------------------------------------------------- stats & output
def _stat_fns():
    t0 = config.T_PRE
    fns = {"concession": lambda p: p[t0]}
    for h in config.POST_HORIZONS:
        fns[f"retrace_{h}"] = lambda p, h=h: p[t0 + h] - p[t0]
    return fns


def _cell_stats(recs, pools, era, rng):
    fns = _stat_fns()
    X = np.vstack([r["xcpi"] for r in recs])
    R = np.vstack([r["raw"] for r in recs])
    Z = np.vstack([r["z"] for r in recs])
    n = len(recs)
    boot_idx = rng.integers(0, n, size=(config.N_BOOT, n))
    boot_means = X[boot_idx].mean(axis=1)
    row = {"era": era, "n": n,
           "concession_bp": X[:, config.T_PRE].mean(),
           "concession_raw_bp": R[:, config.T_PRE].mean(),
           "concession_z": np.nanmean(Z[:, config.T_PRE]),
           "hit_concession": (X[:, config.T_PRE] > 0).mean()}
    for h in config.POST_HORIZONS:
        ret = X[:, config.T_PRE + h] - X[:, config.T_PRE]
        row[f"retrace_{h}_bp"] = ret.mean()
        row[f"hit_retrace_{h}"] = (ret < 0).mean()
    pv, n_placebo = _placebo_pvals(recs, pools, fns, rng)
    for k, v in pv.items():
        row[f"p_{k}"] = v
    row["n_placebo_events"] = n_placebo
    dv = np.array([r["supply_dv01"] for r in recs], dtype=float)
    conc = X[:, config.T_PRE]
    ok = np.isfinite(dv)
    row["dv01_spearman_concession"] = (pd.Series(dv[ok]).corr(pd.Series(conc[ok]), method="spearman")
                                       if ok.sum() >= config.MIN_CELL_N else np.nan)
    boot_paths = X[boot_idx].mean(axis=1)              # (N_BOOT, n_days) mean paths
    paths = {"mean_xcpi": X.mean(axis=0), "median_xcpi": np.median(X, axis=0),
             "mean_raw": R.mean(axis=0), "mean_z": np.nanmean(Z, axis=0),
             "ci_lo": np.percentile(boot_paths, 2.5, axis=0),
             "ci_hi": np.percentile(boot_paths, 97.5, axis=0)}
    return row, paths


def run():
    config.ensure_dirs()
    rng = np.random.default_rng(RNG_SEED)
    resid, std60, tau = _panels()
    from breakeven_structures import data_calendar
    prints, cpi_src = data_calendar.cpi_print_days(resid.index.min(), resid.index.max())
    cpi_days = set(prints)
    print(f"  CPI print days: {len(cpi_days)} (source={cpi_src})")

    ev = data_events.load()
    ev = ev[(ev.market == "US") & (ev.leg == "tips")].copy()
    recs = _collect(ev, resid, std60, tau, cpi_days)
    print(f"  usable events: {len(recs)} / {len(ev)}")

    pools = [_placebo_pool(r, ev, resid, std60, tau, cpi_days, rng) for r in recs]

    stats_rows, path_rows = [], []
    cells = sorted({(r["bucket"], r["kind"]) for r in recs})
    for bucket, kind in cells:
        sel = [i for i, r in enumerate(recs) if r["bucket"] == bucket and r["kind"] == kind]
        for era, mask in (("full", [True] * len(sel)),
                          ("train", [not recs[i]["in_holdout"] for i in sel]),
                          ("holdout", [recs[i]["in_holdout"] for i in sel])):
            idx = [i for i, m in zip(sel, mask) if m]
            if len(idx) < config.MIN_CELL_N:
                stats_rows.append({"bucket": bucket, "kind": kind, "era": era,
                                   "n": len(idx), "not_establishable": True})
                continue
            row, paths = _cell_stats([recs[i] for i in idx], [pools[i] for i in idx], era, rng)
            stats_rows.append({"bucket": bucket, "kind": kind,
                               "not_establishable": False, **row})
            for k, arr in paths.items():
                for d, v in zip(range(-config.T_PRE, config.T_POST + 1), arr):
                    path_rows.append({"bucket": bucket, "kind": kind, "era": era,
                                      "series": k, "rel_day": d, "value": v})

    stats = pd.DataFrame(stats_rows)
    paths = pd.DataFrame(path_rows)
    stats.to_csv(OUT_STATS, index=False)
    paths.to_csv(OUT_PATHS, index=False)
    print(f"  wrote {OUT_STATS} / {OUT_PATHS}")
    show = stats[~stats.not_establishable.astype(bool)]
    cols = ["bucket", "kind", "era", "n", "concession_bp", "p_concession",
            "retrace_5_bp", "p_retrace_5", "hit_concession", "hit_retrace_5"]
    with pd.option_context("display.width", 200):
        print(show[cols].round(3).to_string(index=False))
    return stats, paths


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
