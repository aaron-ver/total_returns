"""
Statistical utilities for the breakeven RV study (plan §10).

Overlapping h-day-ahead targets make naive t-stats fiction — every regression on
daily data with a forward horizon goes through nw_ols (Newey-West / HAC errors,
lag >= horizon). Auction-level regressions (sparse, near-independent events)
can use plain OLS but nw_ols with small lags is still the default.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import statsmodels.api as sm


def nw_ols(y: pd.Series, X: pd.DataFrame | pd.Series, lags: int) -> dict:
    """OLS with Newey-West (HAC) standard errors. Returns a flat dict of results.
    y, X are aligned on their common non-NaN index; an intercept is added."""
    if isinstance(X, pd.Series):
        X = X.to_frame()
    df = pd.concat([y.rename("_y"), X], axis=1).dropna()
    if len(df) < 30:
        return {"n": len(df), "ok": False}
    Xc = sm.add_constant(df[X.columns])
    res = sm.OLS(df["_y"], Xc).fit(cov_type="HAC", cov_kwds={"maxlags": max(int(lags), 1)})
    out = {"n": int(res.nobs), "r2": float(res.rsquared), "ok": True}
    for c in X.columns:
        out[f"beta_{c}"] = float(res.params[c])
        out[f"t_{c}"] = float(res.tvalues[c])
        out[f"p_{c}"] = float(res.pvalues[c])
    return out


def half_life(resid: pd.Series) -> float:
    """Mean-reversion half-life (business days) from an AR(1) fit on the residual:
    d_resid[t] = a + b*resid[t-1] + e  ->  HL = -ln(2)/ln(1+b). inf if b >= 0."""
    r = resid.dropna()
    d = r.diff().dropna()
    lag = r.shift(1).reindex(d.index)
    b = np.polyfit(lag.values, d.values, 1)[0]
    if b >= 0:
        return float("inf")
    return float(-np.log(2) / np.log(1 + b))


def hit_rate(z: pd.Series, fwd_change: pd.Series, threshold: float) -> dict:
    """P(residual moves toward zero over the horizon | |z| > threshold)."""
    df = pd.concat([z.rename("z"), fwd_change.rename("d")], axis=1).dropna()
    sig = df[df["z"].abs() > threshold]
    if len(sig) == 0:
        return {"n": 0, "hit": np.nan}
    hits = (np.sign(sig["d"]) == -np.sign(sig["z"])).mean()
    return {"n": int(len(sig)), "hit": float(hits)}


def rolling_z(series: pd.Series, window: int, min_periods: int, demean: bool = True) -> pd.Series:
    """z-score vs the trailing window (never centered — no lookahead)."""
    mu = series.rolling(window, min_periods=min_periods).mean() if demean else 0.0
    sd = series.rolling(window, min_periods=min_periods).std()
    return (series - mu) / sd


def rolling_half_life(resid: pd.Series, window: int, clip: tuple[float, float]) -> pd.Series:
    """Rolling AR(1) half-life (bd) of a residual, estimated on the trailing window only
    (feeds the 3x-half-life time stop). Clipped to sane bounds; NaN until window fills."""
    r = resid.dropna()
    lag, d = r.shift(1), r.diff()
    # rolling OLS slope of d on lag: b = cov(lag, d)/var(lag), trailing window
    cov = lag.rolling(window).cov(d)
    var = lag.rolling(window).var()
    b = (cov / var).where(var > 0)
    hl = -np.log(2) / np.log1p(b.where(b < 0))
    return hl.clip(*clip).reindex(resid.index)


def cluster_bootstrap_ols(y: pd.Series, X: pd.DataFrame, clusters: pd.Series,
                          n_boot: int = 2000, seed: int = 7) -> pd.DataFrame:
    """OLS with cluster-bootstrap inference: resample whole clusters (e.g. auction weeks)
    with replacement, refit, take percentile CIs. Returns one row per coefficient
    (incl. const): coef, boot se, t, 2.5%/97.5% CI, n, n_clusters.
    Auctions cluster in time — NW lags are not enough there (v2 spec, Track 2)."""
    df = pd.concat([y.rename("_y"), X, clusters.rename("_cl")], axis=1).dropna()
    Xc = sm.add_constant(df[X.columns])
    base = sm.OLS(df["_y"], Xc).fit()
    ids = df["_cl"].unique()
    groups = {c: df.index[df["_cl"] == c] for c in ids}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        take = rng.choice(ids, size=len(ids), replace=True)
        idx = np.concatenate([groups[c].values for c in take])
        sub = df.loc[idx]
        try:
            b = sm.OLS(sub["_y"], sm.add_constant(sub[X.columns], has_constant="add")).fit().params
            draws.append(b)
        except Exception:
            continue
    bs = pd.DataFrame(draws)
    rows = []
    for c in Xc.columns:
        se = bs[c].std()
        rows.append({"var": c, "coef": base.params[c], "se_boot": se,
                     "t_boot": base.params[c] / se if se > 0 else np.nan,
                     "ci_lo": bs[c].quantile(0.025), "ci_hi": bs[c].quantile(0.975)})
    out = pd.DataFrame(rows)
    out["n"] = int(base.nobs)
    out["n_clusters"] = len(ids)
    return out


def perf_stats(daily_bp: pd.Series, trade_pnls: pd.Series | None = None) -> dict:
    """Annualized performance metrics for a daily PnL stream in bp (of BE yield on a
    DV01-normalized position). Sharpe uses 252bd; hit rate is trade-level if given."""
    d = daily_bp.dropna()
    if len(d) == 0 or d.std() == 0:
        return {"ann_bp": 0.0, "vol_bp": 0.0, "sharpe": np.nan, "max_dd_bp": 0.0,
                "skew": np.nan, "hit_rate": np.nan, "n_trades": 0}
    cum = d.cumsum()
    dd = (cum - cum.cummax()).min()
    out = {"ann_bp": d.mean() * 252, "vol_bp": d.std() * np.sqrt(252),
           "sharpe": d.mean() / d.std() * np.sqrt(252), "max_dd_bp": dd,
           "skew": float(d.skew())}
    if trade_pnls is not None and len(trade_pnls):
        out["hit_rate"] = float((trade_pnls > 0).mean())
        out["n_trades"] = int(len(trade_pnls))
    else:
        out["hit_rate"], out["n_trades"] = np.nan, 0
    return out
