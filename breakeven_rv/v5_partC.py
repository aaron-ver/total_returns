"""
v5 Part C — regime-segmented fair value: the phantom-cure test.

Three real-time break detectors (parameters pre-declared in config):
  cusum    sequential Page-CUSUM (k=0.5sd, h=15) on recursive one-step-ahead
           residuals of the Layer-1 regression, expanding within segment,
           252bd warm-up per segment;
  coefdist distance between the two adjacent non-overlapping 504bd windows'
           coefficient vectors, impact-weighted (RMS of X·(b1−b2) over the recent
           window, bp), fired at its own expanding 95th pctl, 126bd refractory;
  monitor  Part-B monitor in CRISIS >= 10 consecutive bd.

Segmented model: per segment, coefficients fit ONCE on the first `burnin` days
(abstains during burn-in — no residual emitted), FROZEN until the next break.
Primary metric: frozen-z phantom rate at entry 1.0 vs the rolling baseline
(10y: 53%). Secondary: price share, segment count/dates vs the regime narrative,
abstention days, stale-window residual RMS (60bd before each break vs segment
average), false breaks in calm (monitor flags < 2 at break date).

Outputs: reports/v5_segmented_fv.csv, v5_break_dates.csv; figure input
cache/v5_seg_fv_10y.parquet.  Usage:  python -m breakeven_rv.v5_partC
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, v5_core
from breakeven_rv.validation import rolling_half_life

R = config.REPORTS


def _data(tenor):
    p = panel_mod.load()
    scol = f"swap_{tenor.rstrip('y')}y"
    df = pd.concat([p[scol].rename("_y") * 100.0, p[config.L1_FACTORS] * 100.0], axis=1).dropna()
    y = df["_y"].values
    X = np.column_stack([np.ones(len(df)), df[config.L1_FACTORS].values])
    return df.index, y, X


def det_cusum(idx, y, X) -> list:
    breaks = []
    t0, n = 0, len(y)
    sp = sn = 0.0
    resids = []
    t = t0 + config.V5_CUSUM_WARMUP
    while t < n:
        b, *_ = np.linalg.lstsq(X[t0:t], y[t0:t], rcond=None)
        w = y[t] - X[t] @ b
        resids.append(w)
        sig = np.std(resids[-config.V5_CUSUM_WARMUP:]) or 1.0
        z = w / sig
        sp = max(0.0, sp + z - config.V5_CUSUM_K)
        sn = max(0.0, sn - z - config.V5_CUSUM_K)
        if max(sp, sn) > config.V5_CUSUM_H:
            breaks.append(t)
            t0, sp, sn, resids = t, 0.0, 0.0, []
            t = t0 + config.V5_CUSUM_WARMUP
            continue
        t += 1
    return breaks


def det_coefdist(idx, y, X) -> list:
    W = config.V5_COEF_HALF
    n = len(y)
    d = np.full(n, np.nan)
    for t in range(2 * W, n):
        b1, *_ = np.linalg.lstsq(X[t - 2 * W: t - W], y[t - 2 * W: t - W], rcond=None)
        b2, *_ = np.linalg.lstsq(X[t - W: t], y[t - W: t], rcond=None)
        d[t] = np.sqrt(np.mean((X[t - W: t] @ (b1 - b2)) ** 2))
    ds = pd.Series(d).dropna()
    pct_v = ds.expanding(min_periods=252).apply(lambda w: (w <= w[-1]).mean(), raw=True)
    pct = pct_v.reindex(range(n))
    breaks, last = [], -10**9
    for t in range(n):
        if np.isfinite(pct.iloc[t]) and pct.iloc[t] >= config.V5_COEF_PCTL \
                and t - last >= config.V5_REFRACTORY:
            breaks.append(t)
            last = t
    return breaks


def det_monitor(idx, tenor) -> list:
    st = v5_core.state_frame(tenor)
    crisis = st["crisis"].reindex(idx).fillna(False).values
    breaks, run, last = [], 0, -10**9
    for t in range(len(idx)):
        run = run + 1 if crisis[t] else 0
        if run == config.V5_MONITOR_RUN and t - last >= config.V5_REFRACTORY:
            breaks.append(t)
            last = t
    return breaks


def segmented_resid(idx, y, X, breaks, burnin):
    """Per-segment frozen-coefficient residual; NaN during burn-ins. Also returns
    the per-day segment beta matrix (for frozen-z decomposition in episodes)."""
    n = len(y)
    bounds = [0] + list(breaks) + [n]
    resid = np.full(n, np.nan)
    betas = np.full((n, X.shape[1]), np.nan)
    abstain = 0
    for s0, s1 in zip(bounds[:-1], bounds[1:]):
        if s1 - s0 <= burnin + 5:
            abstain += s1 - s0
            continue
        b, *_ = np.linalg.lstsq(X[s0:s0 + burnin], y[s0:s0 + burnin], rcond=None)
        resid[s0 + burnin:s1] = y[s0 + burnin:s1] - X[s0 + burnin:s1] @ b
        betas[s0 + burnin:s1] = b
        abstain += burnin
    return pd.Series(resid, index=idx), betas, abstain


def episodes_segmented(idx, y, X, resid, betas, tenor, entry_z=1.0):
    """Episode loop on the segmented residual (same rules; frozen-z uses the entry
    day's segment betas — identical to live within a segment, diverges after a
    mid-episode break)."""
    exit_z = config.V4_EXIT_Z
    r = resid
    sd = r.rolling(config.Z_WINDOW, min_periods=config.V5_SEG_Z_MIN).std()
    z = r / sd
    hl = rolling_half_life(r, config.HL_WINDOW, config.V5_HL_CLIP[tenor])
    rows = []
    i, n = 0, len(idx)
    zv, rv, hlv = z.values, r.values, hl.values
    while i < n:
        if not (np.isfinite(zv[i]) and abs(zv[i]) >= entry_z and np.isfinite(hlv[i]) and zv[i] != 0):
            i += 1
            continue
        e = i
        s_dir = -np.sign(rv[e])
        vol_e = rv[e] / zv[e]
        b_e = betas[e]
        stop = e + int(round(config.EP_HL_MULT * hlv[e]))
        x, reason = None, None
        for j in range(e + 1, min(stop, n - 1) + 1):
            if not np.isfinite(zv[j]):
                continue                      # abstention days inside an episode: keep waiting
            if abs(zv[j]) < exit_z:
                x, reason = j, "converged"
                break
        if x is None:
            x = min(stop, n - 1)
            reason = "time_stop" if x == stop else "sample_end"
        fz_x = (y[x] - X[x] @ b_e) / vol_e
        frozen_status = ("closed" if abs(fz_x) < exit_z
                         else "worse" if abs(fz_x) > abs(zv[e]) else "open")
        pnl_price = s_dir * (y[x] - y[e])
        fair_factor = -s_dir * float((X[x] - X[e]) @ b_e)
        b_x = betas[x] if np.isfinite(betas[x]).all() else b_e
        fair_coef = -s_dir * float(X[x] @ (b_x - b_e))
        rows.append({"entry": idx[e], "exit": idx[x], "exit_reason": reason,
                     "pnl_price_bp": pnl_price, "fair_factor_bp": fair_factor,
                     "fair_coef_bp": fair_coef,
                     "gap_closed_bp": pnl_price + fair_factor + fair_coef,
                     "phantom": (reason == "converged") and frozen_status != "closed"})
        i = x + 1
    return pd.DataFrame(rows)


def run():
    config.ensure_dirs()
    baselines = {}
    for t in config.V5_TENORS:
        ep = v5_core.episodes(t, 1.0)
        conv = ep[ep["exit_reason"] == "converged"]
        baselines[t] = conv["phantom"].mean()

    results, break_rows = [], []
    for tenor in config.V5_TENORS:
        idx, y, X = _data(tenor)
        st = v5_core.state_frame(tenor)
        flags_at = st["flags"].reindex(idx).fillna(0)
        dets = {"cusum": det_cusum(idx, y, X),
                "coefdist": det_coefdist(idx, y, X),
                "monitor": det_monitor(idx, tenor)}
        for name, brk in dets.items():
            for b in brk:
                break_rows.append({"tenor": tenor, "detector": name, "date": idx[b],
                                   "flags_at_break": int(flags_at.iloc[b]),
                                   "calm_false_break": flags_at.iloc[b] < config.V5_MONITOR_MIN_FLAGS})
            for burnin in config.V5_BURNIN_GRID:
                resid, betas, abstain = segmented_resid(idx, y, X, brk, burnin)
                ep = episodes_segmented(idx, y, X, resid, betas, tenor)
                conv = ep[ep["exit_reason"] == "converged"]
                tot = ep["gap_closed_bp"].sum()
                # stale-window RMS: 60bd before each break vs whole-resid RMS
                rms_all = np.sqrt(np.nanmean(resid.values ** 2))
                stale = [np.sqrt(np.nanmean(resid.values[max(0, b - 60):b] ** 2)) for b in brk]
                results.append({
                    "tenor": tenor, "detector": name, "burnin": burnin,
                    "n_segments": len(brk) + 1, "abstain_days": abstain,
                    "n_episodes": len(ep), "n_converged": len(conv),
                    "phantom_rate": conv["phantom"].mean() if len(conv) else np.nan,
                    "baseline_phantom": baselines[tenor],
                    "price_share": ep["pnl_price_bp"].sum() / tot if tot else np.nan,
                    "stale_rms_ratio": (np.mean(stale) / rms_all) if stale else np.nan,
                })
        # figure input (10y, burnin 60, all detectors' fv overlay)
        if tenor == "10y":
            fig = pd.DataFrame({"y_bp": pd.Series(y, index=idx)})
            for name, brk in dets.items():
                r_, b_, _ = segmented_resid(idx, y, X, brk, config.V5_BURNIN)
                fig[f"fv_{name}"] = pd.Series(y, index=idx) - r_
            fig.to_parquet(os.path.join(config.CACHE, "v5_seg_fv_10y.parquet"))
            pd.Series({n: [str(idx[b].date()) for b in brk] for n, brk in dets.items()}) \
                .to_json(os.path.join(config.CACHE, "v5_breaks_10y.json"))

    res = pd.DataFrame(results)
    res.to_csv(os.path.join(R, "v5_segmented_fv.csv"), index=False)
    brks = pd.DataFrame(break_rows)
    brks.to_csv(os.path.join(R, "v5_break_dates.csv"), index=False)
    with pd.option_context("display.width", 220, "display.max_rows", 100):
        print("Segmented FV vs rolling baseline (phantom rate = primary metric):")
        print(res.round(2).to_string(index=False))
        print("\nBreak dates (vs narrative: 2013 taper, 2015-16, COVID, 2021-22, 2023 normalization):")
        print(brks.to_string(index=False))
    return res, brks


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
