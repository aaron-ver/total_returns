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
