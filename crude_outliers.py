"""
Crude (Brent CO / WTI CL) vs breakeven: full-sample R²/beta + outlier diagnostics.

Boss ask: the SAME analysis as hedge_outliers.py (gasoline), but with Brent and WTI as the
explanatory return — regress the US breakeven $ P&L (the same Y as the gas work) on each crude's
daily $/contract change, full sample, and run the outlier/robustness battery. Crude contract =
1,000 bbl so $1/bbl = $1,000/contract (built in crude.py). Standalone — NOT wired into the
dashboard/export. Generates plots/crude_outliers.png (Brent & WTI × 5y/10y/30y), the crude analog
of hedge_outliers.png.

  beta (OLS slope of breakeven $ on crude $/contract) = # of crude contracts per 100k-DV01
  breakeven; R² = fraction of daily breakeven variance the crude move explains.

CLI:
  python crude_outliers.py report     # full-sample R²/beta + influence/trim/treatments per product×tenor
  python crude_outliers.py plot       # -> plots/crude_outliers.png
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import engine, crude
import hedge_outliers as ho      # reuse fit_methods / influence / trim_curve / conditional_r2

HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")
TENORS = ("5y", "10y", "30y")
PRODUCTS = ("Brent", "WTI")
BP_USD = 100_000.0


def aligned_pairs(tenor, product, beta=1.0):
    """Daily (crude $/contract, breakeven $) pairs on the crude∩bond common-day calendar — the
    breakeven $ summed over each crude interval, same alignment as hedge.aligned_pairs for gas."""
    c = crude.load(product)
    r = engine.load_returns(tenor)
    if c.empty or r.empty:
        return pd.DataFrame(columns=["x_usd", "be_usd"])
    eidx = c.index

    def bucket(col):
        s = (r[col] * BP_USD).dropna()
        s = s[s.index <= eidx[-1]]
        pos = eidx.searchsorted(s.index, side="left").clip(max=len(eidx) - 1)
        return s.groupby(eidx[pos]).sum()

    out = pd.DataFrame({"x_usd": c["usd_per_contract"], "tips_usd": bucket("r_TIPS_bp"),
                        "ust_usd": bucket("r_UST_bp")})
    out["be_usd"] = out["tips_usd"] - beta * out["ust_usd"]
    return out.dropna(subset=["x_usd", "be_usd"]).sort_index()


def diagnose(product, tenor, verbose=True):
    p = aligned_pairs(tenor, product)
    x = p["x_usd"].to_numpy(); y = p["be_usd"].to_numpy()
    inf = ho.influence(x, y); res = inf["res"]
    methods = ho.fit_methods(x, y)
    tcook = ho.trim_curve(x, y, "cooks"); tres = ho.trim_curve(x, y, "resid")
    cond = ho.conditional_r2(x, y)
    if verbose:
        n = len(x)
        print(f"\n--- {product} {tenor}  (n={n}, {str(p.index.min())[:7]}..{str(p.index.max())[:7]}) ---")
        print(f"  OLS:  beta={res.params[1]:.1f} contracts   R2={res.rsquared:.3f}   "
              f"t={res.tvalues[1]:.1f} (HC3 t={inf['hc3_t']:.1f})")
        print(f"  influence: Cook's D>4/n: {inf['n_cooks']} ({inf['n_cooks']/n*100:.1f}%) | "
              f"|stud.resid|>3: {inf['n_rstud']} | leverage>3x: {inf['n_lev']}")
        print(f"  trim by INFLUENCE (Cook's D): " +
              "  ".join(f"{pc*100:g}%->R2 {r2:.3f}" for pc, k, b, r2 in tcook))
        print(f"  trim by RESIDUAL (mechanical): " +
              "  ".join(f"{pc*100:g}%->R2 {r2:.3f}" for pc, k, b, r2 in tres))
        print(f"  treatments (beta | R2):  " +
              " | ".join(f"{m} {d['beta']:.1f}/{d['r2']:.3f}" for m, d in methods.items()))
        print(f"  conditional on big crude moves: all R2={res.rsquared:.3f} | " +
              "  ".join(f"top{tp:g}% beta={b:.1f} R2={r2:.3f}" for tp, nn, b, r2 in cond))
        worst = p.iloc[inf["top_cooks"]]
        print("  most-influential days (Cook's D):")
        for dt, row in worst.head(6).iterrows():
            print(f"     {str(dt)[:10]}  crude=${row['x_usd']:>9,.0f}/contract  be=${row['be_usd']:>12,.0f}")
    return dict(product=product, tenor=tenor, n=len(x), ols=res, influence=inf,
                methods=methods, trim_cooks=tcook, conditional=cond, pairs=p)


def report():
    print("Crude (Brent / WTI) vs US breakeven $ — full-sample R²/beta + outlier diagnostics")
    print("beta = # crude contracts (1,000 bbl) per 100k-DV01 breakeven; R² = daily variance explained.")
    for prod in PRODUCTS:
        print(f"\n================ {crude.PRODUCTS[prod]['name']} ================")
        for ten in TENORS:
            diagnose(prod, ten, verbose=True)
    print("\nSame reading as gasoline: influence-trimming that doesn't lift R² => low R² is structural "
          "noise, not a few outliers; R² rising on big-crude-move days with a stable beta => the link "
          "bites when crude moves most.")


def plot(path=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(PLOTS, exist_ok=True)
    path = path or os.path.join(PLOTS, "crude_outliers.png")
    fig, axes = plt.subplots(len(PRODUCTS), len(TENORS), figsize=(16, 9))
    for pi, prod in enumerate(PRODUCTS):
        for ti, ten in enumerate(TENORS):
            ax = axes[pi][ti]
            d = diagnose(prod, ten, verbose=False)
            p = d["pairs"]; x = p["x_usd"].to_numpy(); y = p["be_usd"].to_numpy(); m = d["methods"]
            ax.scatter(x, y, s=6, alpha=0.22, color="0.5", label=f"daily (n={len(x)})")
            ax.scatter(x[d["influence"]["top_cooks"]], y[d["influence"]["top_cooks"]], s=36,
                       color="red", zorder=5, label="top Cook's D")
            xs = np.array([x.min(), x.max()])
            for name, c, ls in (("OLS", "tab:blue", "-"), ("Huber", "tab:green", "--"), ("median", "tab:orange", ":")):
                ax.plot(xs, m[name]["intercept"] + m[name]["beta"] * xs, color=c, ls=ls, lw=1.6,
                        label=f"{name}: {m[name]['beta']:.0f} ({m[name]['r2']:.2f})")
            ax.axhline(0, color="k", lw=0.4); ax.axvline(0, color="k", lw=0.4)
            ax.set_title(f"{prod} {ten}  (R²={m['OLS']['r2']:.2f}, β={m['OLS']['beta']:.0f} contracts)", fontsize=10)
            ax.grid(alpha=0.3); ax.legend(fontsize=6, loc="upper left")
            if ti == 0:
                ax.set_ylabel(f"{prod}\nbreakeven $/day")
            if pi == len(PRODUCTS) - 1:
                ax.set_xlabel("crude $/contract/day")
    fig.suptitle("Breakeven $ vs crude $/contract — full sample: OLS/Huber/median fits & outliers "
                 "(Brent & WTI × 5y/10y/30y)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=110); plt.close(fig)
    print(f"  wrote {path}")
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        report()
    elif cmd == "plot":
        plot()
    else:
        print(__doc__)
