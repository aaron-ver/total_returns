"""
Layer 1 — the parsimonious fair-value anchor (plan §7) -> Residual A.

Baseline: the Barclays four-factor model — 3m10y nominal slope, log RBOB gasoline,
VIX, log USD — fit on a rolling ~2y window, plus an EWLS variant (half-life ~1y,
no hard cutoff). Fit target:
  - PRIMARY:  swap_10y (10y ZC CPI swap). Modelling in swap space avoids TIPS
    carry seasonality and the roll (plan §6); the TIPS translation happens via
    the iota (Residual B), so A and B stay two independent lenses:
        BE rich/cheap  =  A (fundamental, swap space)  +  B (TIPS-vs-swap basis)
  - ROBUSTNESS: be10 (CM TIPS breakeven) fit directly.

A LASSO diagnostic over a wider economically-grouped basket (config.L1_LASSO_FACTORS)
is refit monthly on the same rolling window — NOT to improve fit, but to show
whether the four factors are stable or the key driver rotated (plan §7).

Residual A = target − fitted, in bp. z = residual / same-window residual vol.
The current day IS inside its own fit window (weight 1/504, negligible absorption)
— this is the standard contemporaneous-FV convention; the alternative (fit through
t-1, predict t) was checked to make no material difference and is available via
`exclude_current=True`.

Output: cache/layer1_{target}.parquet  (fv, resid, z per method + rolling betas)
Usage:  python -m breakeven_rv.layer1 [build|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod

TARGETS = {"swap10": "swap_10y", "be10": "be10"}


def _out(target_key):
    return os.path.join(config.CACHE, f"layer1_{target_key}.parquet")


def rolling_fit(y: pd.Series, X: pd.DataFrame, window: int, halflife: float | None = None,
                exclude_current: bool = False) -> pd.DataFrame:
    """Rolling (optionally exponentially-weighted) OLS. Returns fv/resid/r2 + betas.
    Plain numpy loop: ~5000 days x lstsq(504 x 5) runs in ~1s."""
    df = pd.concat([y.rename("_y"), X], axis=1).dropna()
    yv = df["_y"].values
    Xv = np.column_stack([np.ones(len(df)), df[X.columns].values])
    k = Xv.shape[1]
    n = len(df)
    fv = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    betas = np.full((n, k), np.nan)
    if halflife:
        w_full = 0.5 ** (np.arange(window - 1, -1, -1) / halflife)
    for i in range(window - 1, n):
        lo = i - window + 1
        hi = i if exclude_current else i + 1
        Xi, yi = Xv[lo:hi], yv[lo:hi]
        if halflife:
            w = w_full[: hi - lo]
            sw = np.sqrt(w)
            b, *_ = np.linalg.lstsq(Xi * sw[:, None], yi * sw, rcond=None)
        else:
            b, *_ = np.linalg.lstsq(Xi, yi, rcond=None)
        betas[i] = b
        fv[i] = Xv[i] @ b
        e = yi - Xi @ b
        r2[i] = 1.0 - np.sum(e ** 2) / max(np.sum((yi - yi.mean()) ** 2), 1e-12)
    out = pd.DataFrame(index=df.index)
    out["fv"] = fv
    out["resid_bp"] = (df["_y"].values - fv) * 100.0
    out["r2"] = r2
    for j, c in enumerate(["const"] + list(X.columns)):
        out[f"beta_{c}"] = betas[:, j]
    return out


def lasso_diagnostic(y: pd.Series, X: pd.DataFrame, window: int, refit_every: int = 21) -> pd.DataFrame:
    """Monthly-refit LASSO over the wider basket (standardized within window).
    Records the selected coefficients — a factor-rotation monitor, not a better FV."""
    from sklearn.linear_model import LassoCV
    df = pd.concat([y.rename("_y"), X], axis=1).dropna()
    rows = []
    for i in range(window - 1, len(df), refit_every):
        sub = df.iloc[i - window + 1: i + 1]
        Xs = (sub[X.columns] - sub[X.columns].mean()) / sub[X.columns].std()
        ys = sub["_y"]
        m = LassoCV(cv=5, alphas=50, max_iter=5000).fit(Xs.values, ys.values)
        rows.append({"date": df.index[i], "alpha": m.alpha_,
                     **{f"coef_{c}": v for c, v in zip(X.columns, m.coef_)}})
    return pd.DataFrame(rows).set_index("date")


def build():
    config.ensure_dirs()
    p = panel_mod.load()
    X = p[config.L1_FACTORS]
    for key, col in TARGETS.items():
        y = p[col]
        ols = rolling_fit(y, X, config.L1_WINDOW)
        ewls = rolling_fit(y, X, config.L1_WINDOW, halflife=config.L1_HALFLIFE)
        out = pd.DataFrame(index=ols.index)
        for name, fit in (("ols", ols), ("ewls", ewls)):
            out[f"fv_{name}"] = fit["fv"]
            out[f"resid_{name}_bp"] = fit["resid_bp"]
            out[f"r2_{name}"] = fit["r2"]
            sd = fit["resid_bp"].rolling(config.Z_WINDOW, min_periods=config.Z_MIN_PERIODS).std()
            out[f"z_{name}"] = fit["resid_bp"] / sd    # same-window residual vol (plan §7)
        for c in ["const"] + config.L1_FACTORS:   # const kept: frozen-coefficient FV (track1_decomp)
            out[f"beta_{c}"] = ols[f"beta_{c}"]
        out.to_parquet(_out(key))
        v = out.dropna(subset=["z_ols"])
        print(f"  layer1_{key}: n={len(v)} {str(v.index.min())[:10]} -> {str(v.index.max())[:10]}  "
              f"median in-window R2 ols={out['r2_ols'].median():.3f} ewls={out['r2_ewls'].median():.3f}  "
              f"resid sd={out['resid_ols_bp'].std():.1f}bp")

    # LASSO factor-rotation diagnostic (on the primary target only)
    Xl = p[config.L1_LASSO_FACTORS].dropna(how="any")
    las = lasso_diagnostic(p["swap_10y"], p[config.L1_LASSO_FACTORS], config.L1_WINDOW)
    las.to_parquet(os.path.join(config.CACHE, "layer1_lasso.parquet"))
    nz = (las.filter(like="coef_") != 0).mean().sort_values(ascending=False)
    print("  LASSO selection frequency (share of windows with non-zero coef):")
    print(nz.round(2).to_string())


def load(target_key: str = "swap10"):
    path = _out(target_key)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} missing — run: python -m breakeven_rv.layer1 build")
    return pd.read_parquet(path)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    else:
        for k in TARGETS:
            try:
                df = load(k)
                print(k, df.shape, df[["r2_ols", "z_ols"]].tail(3).round(2).to_string())
            except FileNotFoundError as e:
                print(e)
