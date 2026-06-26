"""
Outlier / robustness diagnostics for the gasoline<->breakeven hedge regression (hedge.py).

Question from the desk: does the low daily R^2 (~0.07-0.20) come from a few outlier days,
and if so would removing/treating them substantially improve the fit? This module answers it
and lays out the treatment menu (winsorize, Huber, quantile/median, Theil-Sen, RANSAC) so the
hedge ratio can be re-estimated robustly if warranted.

For each tenor it runs, on the aligned daily pairs (gas $/contract vs breakeven $, from
hedge.aligned_pairs):
  1. baseline OLS  -- beta (contracts), R^2, t-stat (classical + HC3 heteroskedastic-robust);
  2. influence    -- Cook's distance, externally-studentized residuals, leverage (statsmodels
                     OLSInfluence): how many points exceed the usual thresholds, and WHICH days;
  3. trim curves  -- R^2 / beta after dropping the top k% by influence (Cook's D) vs by residual
                     (the honest test: influence-trimming that doesn't lift R^2 => no outlier
                     problem; residual-trimming always lifts R^2 mechanically, so it's a weak test);
  4. treatments   -- OLS, winsorized OLS (1%, 2.5%), Huber RLM, median/L1 (QuantReg q=0.5),
                     Theil-Sen, RANSAC: each method's beta (contracts) + R^2, so you can see how
                     much the hedge ratio moves under each;
  5. conditional  -- R^2 and beta restricted to the largest |gas move| days (top 50/25/10%): is
                     the hedge tight exactly when it matters, even if the all-days R^2 is low?

CLI:
  python hedge_outliers.py report           # one-screen verdict across 5y/10y/30y (full sample)
  python hedge_outliers.py report 5y        # trailing-5y window instead of full sample
  python hedge_outliers.py diagnose 10y     # the full detail dump for one tenor
  python hedge_outliers.py plot             # scatter + OLS/robust fit lines, outliers flagged
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.linear_model import TheilSenRegressor, RANSACRegressor

import hedge

HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")
TENORS = ("5y", "10y", "30y")


def _pairs(tenor, years=None):
    """Aligned daily (gas_usd, be_usd) pairs; optionally only the trailing `years` years."""
    p = hedge.aligned_pairs(tenor)
    if years is not None and not p.empty:
        p = p[p.index >= p.index.max() - pd.DateOffset(years=years)]
    return p


def _r2(x, y, slope, intercept):
    """R^2 of an arbitrary line on (x, y) -- 1 - SSE/SST (comparable across estimators)."""
    sse = np.sum((y - (intercept + slope * x)) ** 2)
    sst = np.sum((y - y.mean()) ** 2)
    return 1 - sse / sst if sst > 0 else np.nan


def fit_methods(x, y):
    """Fit every estimator; return {name: dict(beta, intercept, r2)}. beta = # contracts.
    R^2 is computed on the SAME data the model saw (winsor methods on the winsorized data)."""
    X = sm.add_constant(x)
    out = {}
    ols = sm.OLS(y, X).fit()
    out["OLS"] = dict(beta=ols.params[1], intercept=ols.params[0], r2=ols.rsquared)
    for p in (1.0, 2.5):                                   # winsorize X and Y independently
        xw = np.clip(x, *np.percentile(x, [p, 100 - p]))
        yw = np.clip(y, *np.percentile(y, [p, 100 - p]))
        f = sm.OLS(yw, sm.add_constant(xw)).fit()
        out[f"winsor{p:g}%"] = dict(beta=f.params[1], intercept=f.params[0], r2=f.rsquared)
    hub = sm.RLM(y, X, M=sm.robust.norms.HuberT()).fit()   # Huber M-estimator
    out["Huber"] = dict(beta=hub.params[1], intercept=hub.params[0],
                        r2=_r2(x, y, hub.params[1], hub.params[0]))
    med = sm.QuantReg(y, X).fit(q=0.5)                      # median / L1 regression
    out["median"] = dict(beta=med.params[1], intercept=med.params[0],
                         r2=_r2(x, y, med.params[1], med.params[0]))
    ts = TheilSenRegressor(random_state=0).fit(x.reshape(-1, 1), y)
    out["TheilSen"] = dict(beta=ts.coef_[0], intercept=ts.intercept_,
                           r2=_r2(x, y, ts.coef_[0], ts.intercept_))
    rs = RANSACRegressor(random_state=0).fit(x.reshape(-1, 1), y)
    sl, ic = rs.estimator_.coef_[0], rs.estimator_.intercept_
    out["RANSAC"] = dict(beta=sl, intercept=ic, r2=_r2(x, y, sl, ic),
                         inliers=int(rs.inlier_mask_.sum()))
    return out


def influence(x, y):
    """statsmodels OLS influence: Cook's D, externally-studentized residuals, leverage, plus
    threshold counts and the indices of the most-influential points (by Cook's D)."""
    res = sm.OLS(y, sm.add_constant(x)).fit()
    inf = res.get_influence()
    cooks = inf.cooks_distance[0]
    rstud = inf.resid_studentized_external
    lev = inf.hat_matrix_diag
    n = len(x)
    return {
        "res": res, "cooks": cooks, "rstud": rstud, "lev": lev,
        "n_cooks": int((cooks > 4 / n).sum()),               # Cook's D > 4/n
        "n_rstud": int((np.abs(rstud) > 3).sum()),           # |studentized resid| > 3
        "n_lev": int((lev > 3 * lev.mean()).sum()),          # leverage > 3x average
        "top_cooks": np.argsort(-cooks)[:8],
        "hc3_t": res.params[1] / res.HC3_se[1],              # heteroskedasticity-robust t-stat
    }


def trim_curve(x, y, by, pcts=(0.005, 0.01, 0.025, 0.05)):
    """beta/R^2 after dropping the top k% by `by` ('cooks' influence, or 'resid' |stud.resid|)."""
    inf = influence(x, y)
    score = inf["cooks"] if by == "cooks" else np.abs(inf["rstud"])
    order = np.argsort(-score)
    n = len(x); rows = []
    for pc in pcts:
        k = int(n * pc); keep = np.ones(n, bool); keep[order[:k]] = False
        f = sm.OLS(y[keep], sm.add_constant(x[keep])).fit()
        rows.append((pc, k, f.params[1], f.rsquared))
    return rows


def conditional_r2(x, y, qs=(50, 75, 90)):
    """beta/R^2 on the days with the largest |gas move| (top (100-q)% by |x|)."""
    ax = np.abs(x); rows = []
    for q in qs:
        m = ax >= np.percentile(ax, q)
        f = sm.OLS(y[m], sm.add_constant(x[m])).fit()
        rows.append((100 - q, int(m.sum()), f.params[1], f.rsquared))
    return rows


def diagnose(tenor, years=None, verbose=True):
    """Full diagnostic for one tenor. Returns a dict; prints a readable report if verbose."""
    p = _pairs(tenor, years)
    x = p["gas_usd"].to_numpy(); y = p["be_usd"].to_numpy()
    inf = influence(x, y); res = inf["res"]
    methods = fit_methods(x, y)
    tcook = trim_curve(x, y, "cooks"); tres = trim_curve(x, y, "resid")
    cond = conditional_r2(x, y)
    span = f"{str(p.index.min())[:7]}..{str(p.index.max())[:7]}"
    win = f"trailing {years}y" if years else "full sample"
    if verbose:
        n = len(x)
        print(f"\n=== {tenor}  ({win}, n={n}, {span}) ===")
        print(f"  OLS:  beta={res.params[1]:.1f} contracts   R2={res.rsquared:.3f}   "
              f"t={res.tvalues[1]:.1f}  (HC3 t={inf['hc3_t']:.1f})")
        print(f"  influence: Cook's D>4/n: {inf['n_cooks']} ({inf['n_cooks']/n*100:.1f}%) | "
              f"|stud.resid|>3: {inf['n_rstud']} | leverage>3x: {inf['n_lev']}")
        print(f"  trim by INFLUENCE (Cook's D) -- honest test: " +
              "  ".join(f"{int(pc*100*10)/10 if pc<0.01 else pc*100:g}%->R2 {r2:.3f}" for pc, k, b, r2 in tcook))
        print(f"  trim by RESIDUAL (mechanical):              " +
              "  ".join(f"{pc*100:g}%->R2 {r2:.3f}" for pc, k, b, r2 in tres))
        print(f"  treatments (beta | R2):  " +
              " | ".join(f"{m} {d['beta']:.1f}/{d['r2']:.3f}" for m, d in methods.items()))
        print(f"  conditional on big gas moves:  all R2={res.rsquared:.3f}  | " +
              "  ".join(f"top{tp:g}% beta={b:.1f} R2={r2:.3f}" for tp, nn, b, r2 in cond))
        worst = p.iloc[inf["top_cooks"]]
        print("  most-influential days (Cook's D):")
        for dt, row in worst.iterrows():
            print(f"     {str(dt)[:10]}  gas=${row['gas_usd']:>9,.0f}/contract  be=${row['be_usd']:>12,.0f}"
                  f"   local beta={row['be_usd']/row['gas_usd'] if row['gas_usd'] else float('nan'):.0f}")
    return dict(tenor=tenor, n=len(x), span=span, ols=res, influence=inf, methods=methods,
                trim_cooks=tcook, trim_resid=tres, conditional=cond, pairs=p)


def report(years=None):
    print(f"Gasoline<->breakeven hedge — outlier / robustness diagnostics "
          f"({'trailing %dy' % years if years else 'full sample'})")
    for t in TENORS:
        diagnose(t, years=years, verbose=True)
    print("\nReading: if INFLUENCE-trimming (Cook's D) doesn't lift R^2, the low R^2 is structural "
          "noise (many breakeven drivers), not a handful of outliers — robust methods then mainly "
          "stabilize beta, they don't 'fix' R^2. Rising R^2 on big-gas-move days with a stable beta "
          "means the hedge bites when it matters.")


def plot(years=None, path=None):
    """1x3 scatter (one per tenor): gas $/contract vs breakeven $, with OLS / Huber / median fit
    lines and the top-Cook's-D outliers flagged in red."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(PLOTS, exist_ok=True)
    path = path or os.path.join(PLOTS, f"hedge_outliers{('_%dy' % years) if years else ''}.png")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    for ax, t in zip(axes, TENORS):
        d = diagnose(t, years=years, verbose=False)
        p = d["pairs"]; x = p["gas_usd"].to_numpy(); y = p["be_usd"].to_numpy()
        m = d["methods"]
        ax.scatter(x, y, s=6, alpha=0.25, color="0.5", label=f"daily (n={len(x)})")
        ax.scatter(x[d["influence"]["top_cooks"]], y[d["influence"]["top_cooks"]], s=40,
                   color="red", zorder=5, label="top Cook's D")
        xs = np.array([x.min(), x.max()])
        for name, c, ls in (("OLS", "tab:blue", "-"), ("Huber", "tab:green", "--"), ("median", "tab:orange", ":")):
            ax.plot(xs, m[name]["intercept"] + m[name]["beta"] * xs, color=c, ls=ls, lw=1.6,
                    label=f"{name}: {m[name]['beta']:.0f} ({m[name]['r2']:.2f})")
        ax.axhline(0, color="k", lw=0.4); ax.axvline(0, color="k", lw=0.4)
        ax.set_title(f"{t}  (R²={m['OLS']['r2']:.2f})"); ax.set_xlabel("gas $/contract/day")
        ax.set_ylabel("breakeven $/day") if t == "5y" else None
        ax.legend(fontsize=7, loc="upper left"); ax.grid(alpha=0.3)
    fig.suptitle(f"Hedge regression: breakeven $ vs gasoline $/contract — fits & outliers "
                 f"({'trailing %dy' % years if years else 'full sample'})", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=110); plt.close(fig)
    print(f"  wrote {path}")
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    cmd = args[0] if args else "report"
    yrs = next((int(a[:-1]) for a in args if a.endswith("y") and a[:-1].isdigit()), None)
    ten = next((a for a in args if a in TENORS), None)
    if cmd == "report":
        report(years=yrs)
    elif cmd == "diagnose":
        diagnose(ten or "10y", years=yrs)
    elif cmd == "plot":
        plot(years=yrs)
    else:
        print(__doc__)
