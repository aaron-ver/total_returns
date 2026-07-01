"""
Brent-crude energy hedge for the intl linker buckets (boss: "add an energy hedge using CO1/CO2
instead of XB; full-sample hedge ratios across buckets, like the US").

Reuses the already-built Brent front-month $/contract series (crude.load("Brent") = CO1/CO2 with the
gen-ticker roll, $1/bbl = $1,000/contract) and regresses each bucket's daily leg $ P&L on the Brent
$/contract, FULL SAMPLE (no rolling — the boss expects the ratio to be less stable, so one number per
bucket). Per leg so the dashboard beta slider still works:  hedge h(beta) = bL - beta*bN contracts,
where bL/bN = crude betas of the linker / nominal legs. At beta=1, h = crude beta of the breakeven.

Alignment mirrors hedge.aligned_pairs: each bucket's daily leg $ (r_*_bp * 100k) is SUMMED into the
Brent trading-day intervals, so crude and bond span the same days across holiday mismatches.

NB currency: Brent is USD, the breakeven $ is LOCAL ccy per 100k-DV01, so the ratio is "USD Brent
contracts per 100k-local-DV01 BE" (a cross-ccy hedge). R^2 (how much of the BE variance Brent
explains) is scale/ccy-invariant -- that's the "is the calendar effect just energy?" number.

CLI:
  python energy_intl.py report      # per market/bucket: R^2, beta(contracts), t, outlier battery -> CSV
  python energy_intl.py plot        # per-market scatter grids (BE $ vs Brent $ + fits + outliers)
  python energy_intl.py hedge       # print the full-sample per-leg crude betas per bucket
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import linkers
import buckets_intl as bk
import crude
import hedge_outliers as ho

CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")
EXPORTS = os.path.join(HERE, "exports", "cmt")
BP_USD = 100_000.0                                  # 1 engine bp of BE = 100k (each leg 100k DV01)
MKT_ORDER = ["IT_BTPEI", "FR_OATEI", "FR_OATI", "ES_EI", "UK_3M"]   # DE has no nominal -> no BE hedge


def _ols(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y); x, y = x[m], y[m]; n = len(x)
    if n < 3 or np.std(x) == 0:
        return {"slope": np.nan, "r2": np.nan, "tstat": np.nan, "n": n}
    xm, ym = x.mean(), y.mean(); sxx = ((x - xm) ** 2).sum(); sxy = ((x - xm) * (y - ym)).sum()
    slope = sxy / sxx; resid = y - (ym - slope * xm + slope * x)
    se = np.sqrt((resid ** 2).sum() / (n - 2) / sxx) if sxx > 0 else np.nan
    corr = np.corrcoef(x, y)[0, 1]
    return {"slope": slope, "r2": corr ** 2, "tstat": slope / se if se else np.nan, "n": n}


def _cmt(market, bucket):
    p = os.path.join(CMT_DIR, f"{market}__{bucket}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


_BR = None
def _brent():
    global _BR
    if _BR is None:
        _BR = crude.load("Brent")
    return _BR


def aligned_pairs(market, bucket):
    """Daily (Brent $/contract, linker $, nominal $, be $) on the Brent-day calendar; bucket leg $
    summed into each Brent interval. be = linker - nominal (beta=1)."""
    br = _brent()
    d = _cmt(market, bucket)
    if br is None or br.empty or d is None or d.empty:
        return pd.DataFrame()
    eidx = br.index

    def summ(col):
        if col not in d:
            return pd.Series(dtype=float)
        s = (pd.to_numeric(d[col], errors="coerce") * BP_USD).dropna()
        s = s[s.index <= eidx[-1]]
        if s.empty:
            return pd.Series(dtype=float)
        pos = eidx.searchsorted(s.index, side="left").clip(max=len(eidx) - 1)
        return s.groupby(eidx[pos]).sum()

    out = pd.DataFrame({"crude": br["usd_per_contract"], "lin": summ("r_linker_bp"), "nom": summ("r_nominal_bp")})
    out["be"] = out["lin"] - out["nom"]
    return out.dropna(subset=["crude"]).sort_index()


def full_betas(market, bucket):
    """Full-sample per-leg crude betas (contracts) + breakeven R^2. h(beta)=bL-beta*bN."""
    p = aligned_pairs(market, bucket)
    if p.empty or p["be"].notna().sum() < 30:
        return None
    bL = _ols(p["crude"], p["lin"]); bN = _ols(p["crude"], p["nom"]); bE = _ols(p["crude"], p["be"])
    return {"market": market, "bucket": bucket, "n": bE["n"],
            "bL": bL["slope"], "bN": bN["slope"], "be_beta": bE["slope"], "be_r2": bE["r2"],
            "be_t": bE["tstat"], "out_beta": bL["slope"], "out_r2": bL["r2"]}


def hedge_map():
    """{market: {bucket: [bL, bN]}} full-sample per-leg crude betas — for the dashboard energy hedge."""
    out = {}
    for m in MKT_ORDER:
        for b in bk.ORDER:
            fb = full_betas(m, b)
            if fb and pd.notna(fb["bL"]) and pd.notna(fb["bN"]):
                out.setdefault(m, {})[b] = [round(float(fb["bL"]), 4), round(float(fb["bN"]), 4)]
    return out


def crude_monthly():
    """{'YYYY-MM': Brent $/contract summed over the month} — the monthly hedge input for the dashboard."""
    br = crude.load("Brent")
    ms = br["usd_per_contract"].groupby(br.index.to_period("M")).sum()
    return {str(p): round(float(v), 1) for p, v in ms.items()}


# ------------------------------------------------------------------ stats + outliers -------------
def report(save=True):
    rows = []
    for m in MKT_ORDER:
        for b in bk.ORDER:
            p = aligned_pairs(m, b)
            if p.empty or p["be"].notna().sum() < 30:
                continue
            q = p.dropna(subset=["be"]); x = q["crude"].to_numpy(); y = q["be"].to_numpy()
            inf = ho.influence(x, y); meth = ho.fit_methods(x, y)
            tr = ho.trim_curve(x, y, "cooks"); cond = ho.conditional_r2(x, y)
            rows.append({"market": m, "bucket": b, "n": len(x),
                         "brent_contracts_BE": round(meth["OLS"]["beta"], 1), "R2_BE": round(meth["OLS"]["r2"], 3),
                         "HC3_t": round(inf["hc3_t"], 1), "Huber_beta": round(meth["Huber"]["beta"], 1),
                         "cooks_pct": round(inf["n_cooks"] / len(x) * 100, 1),
                         "R2_trim1pct": round([r2 for pc, k, bb, r2 in tr if abs(pc - 0.01) < 1e-9][0], 3) if tr else None,
                         "R2_top10pct_moves": round([r2 for tp, nn, bb, r2 in cond if tp == 10][0], 3) if cond else None})
    df = pd.DataFrame(rows)
    if save and len(df):
        os.makedirs(EXPORTS, exist_ok=True)
        df.to_csv(os.path.join(EXPORTS, "_energy_hedge.csv"), index=False)
    print("Brent energy hedge vs intl breakeven — full-sample R^2 / beta (contracts) + outlier battery")
    print("beta = USD Brent contracts (1,000 bbl) per 100k-DV01 breakeven; R^2 = daily BE variance Brent explains.\n")
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(df.to_string(index=False))
    if save and len(df):
        print(f"\n  wrote {os.path.join(EXPORTS, '_energy_hedge.csv')}")
    return df


def plot(market=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(PLOTS, exist_ok=True)
    markets = [market] if market else MKT_ORDER
    for m in markets:
        buckets = [b for b in bk.ORDER if not aligned_pairs(m, b).empty and aligned_pairs(m, b)["be"].notna().sum() >= 30]
        if not buckets:
            continue
        ncol = 3; nrow = int(np.ceil(len(buckets) / ncol))
        fig, axes = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 3.6 * nrow), squeeze=False)
        for ax in axes.flat:
            ax.axis("off")
        for j, b in enumerate(buckets):
            ax = axes[j // ncol][j % ncol]; ax.axis("on")
            p = aligned_pairs(m, b).dropna(subset=["be"]); x = p["crude"].to_numpy(); y = p["be"].to_numpy()
            inf = ho.influence(x, y); meth = ho.fit_methods(x, y)
            ax.scatter(x, y, s=6, alpha=0.2, color="0.5")
            ax.scatter(x[inf["top_cooks"]], y[inf["top_cooks"]], s=30, color="red", zorder=5, label="top Cook's D")
            xs = np.array([x.min(), x.max()])
            for name, c, ls in (("OLS", "tab:blue", "-"), ("Huber", "tab:green", "--")):
                ax.plot(xs, meth[name]["intercept"] + meth[name]["beta"] * xs, color=c, ls=ls, lw=1.5,
                        label=f"{name} {meth[name]['beta']:.0f} (R²{meth[name]['r2']:.2f})")
            ax.axhline(0, color="k", lw=0.3); ax.axvline(0, color="k", lw=0.3)
            ax.set_title(f"{b}  β={meth['OLS']['beta']:.0f}  R²={meth['OLS']['r2']:.2f}  n={len(x)}", fontsize=9)
            ax.legend(fontsize=6, loc="upper left"); ax.grid(alpha=0.3)
        fig.suptitle(f"{bk.MARKETS.get(m, m)} — breakeven $/day vs Brent $/contract (full sample, OLS/Huber + Cook's D)", fontsize=12)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        path = os.path.join(PLOTS, f"energy_intl_{m}.png")
        fig.savefig(path, dpi=110); plt.close(fig)
        print(f"  wrote {path}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        report()
    elif cmd == "plot":
        plot(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "hedge":
        import json; print(json.dumps(hedge_map(), indent=1))
    else:
        print(__doc__)
