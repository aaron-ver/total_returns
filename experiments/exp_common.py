"""
Shared plumbing for the experiments/ package — READ-ONLY bridges to the production caches.

Isolation rules (why this folder exists):
  * experiments NEVER write into cache/, cache_intl/, exports/, marts/ or the dashboards —
    all outputs go to experiments/out/ (results) and experiments/cache/ (experiment-only pulls).
  * production code never imports experiments/ — deleting this folder cannot break the pipeline.
  * conventions match the engines exactly: returns are the cached DV01-normalized bp series
    (bp per 100k DV01), BE = linker − β·nominal at β=1, linear cumsum, no recomputation.

Run any experiment from the project root:
    .venv/Scripts/python.exe experiments/exp_extension.py
"""
from __future__ import annotations
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
import pandas as pd

OUT = os.path.join(HERE, "out")
ECACHE = os.path.join(HERE, "cache")
os.makedirs(OUT, exist_ok=True)
os.makedirs(ECACHE, exist_ok=True)

CACHE_US = os.path.join(ROOT, "cache")
CACHE_INTL = os.path.join(ROOT, "cache_intl")
CMT_DIR = os.path.join(CACHE_INTL, "cmt")


# ------------------------------------------------------------------ stats (hedge.py conventions)
def ols(x, y):
    """OLS slope of y on x with R^2 and t-stat(slope) — same convention as hedge.py/_ols."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 3 or np.ptp(x) == 0:
        return dict(slope=np.nan, intercept=np.nan, r2=np.nan, tstat=np.nan, n=n)
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    ssr = float(resid @ resid); sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ssr / sst if sst > 0 else np.nan
    se = np.sqrt(ssr / (n - 2) / ((x - x.mean()) ** 2).sum()) if n > 2 else np.nan
    return dict(slope=b, intercept=a, r2=r2, tstat=b / se if se and se > 0 else np.nan, n=n)


def mols(X, y):
    """Multivariate OLS y ~ const + X. Returns dict(coef=Series, t=Series, r2, n).
    X: DataFrame of regressors; NaN rows dropped."""
    df = pd.concat([pd.Series(np.asarray(y, float), index=X.index, name="_y"), X], axis=1).dropna()
    if len(df) < len(X.columns) + 3:
        return None
    yv = df["_y"].to_numpy()
    Xm = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in X.columns])
    beta, *_ = np.linalg.lstsq(Xm, yv, rcond=None)
    resid = yv - Xm @ beta
    dof = len(df) - Xm.shape[1]
    sigma2 = float(resid @ resid) / dof
    try:
        cov = sigma2 * np.linalg.inv(Xm.T @ Xm)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        se = np.full(Xm.shape[1], np.nan)
    sst = float(((yv - yv.mean()) ** 2).sum())
    names = ["const"] + list(X.columns)
    return dict(coef=pd.Series(beta, index=names), t=pd.Series(beta / se, index=names),
                r2=1 - float(resid @ resid) / sst if sst > 0 else np.nan, n=len(df))


# ------------------------------------------------------------------ month-end windows
def month_windows(bp: pd.Series, n=5):
    """Per calendar month: ME = sum of the LAST n trading days' bp of the month, REV = sum of the
    FIRST n trading days of the NEXT month (the reversal window). Index: month period (M)."""
    s = pd.to_numeric(bp, errors="coerce").dropna()
    if s.empty:
        return pd.DataFrame(columns=["me", "rev"])
    per = s.index.to_period("M")
    me = s.groupby(per).apply(lambda g: g.tail(n).sum())
    first = s.groupby(per).apply(lambda g: g.head(n).sum())
    rev = first.shift(-1)                                 # next month's first-n, aligned to month m
    return pd.DataFrame({"me": me, "rev": rev})


# ------------------------------------------------------------------ US loaders
def us_returns(tenor):
    """US bucketed daily returns (returns_{tenor}.parquet): r_TIPS_bp / r_UST_bp / r_BE_bp."""
    df = pd.read_parquet(os.path.join(CACHE_US, f"returns_{tenor}.parquet"))
    df.index = pd.to_datetime(df.index)
    return df


US_TENORS = ["5y", "10y", "30y"]


def us_tips_auctions():
    """TreasuryDirect TIPS auction history: cusip, auctionDate, issueDate, maturityDate,
    totalAccepted, reopening, tenor."""
    a = pd.read_parquet(os.path.join(CACHE_US, "auctions.parquet"))
    a = a[a["leg"] == "tips"].copy()
    for c in ("auctionDate", "issueDate", "maturityDate"):
        a[c] = pd.to_datetime(a[c])
    a["totalAccepted"] = pd.to_numeric(a["totalAccepted"], errors="coerce")
    return a


# ------------------------------------------------------------------ intl loaders
def intl_universe():
    u = pd.read_csv(os.path.join(CACHE_INTL, "universe.csv"))
    u["maturity"] = pd.to_datetime(u["maturity"])
    u["first_issue"] = pd.to_datetime(u["first_issue"], errors="coerce")
    return u


def intl_amt_outstanding():
    """{isin: AMT_OUTSTANDING} from the static cache (current outstanding — constant-weight proxy)."""
    out = {}
    sdir = os.path.join(CACHE_INTL, "static")
    for f in os.listdir(sdir):
        if f.endswith(".parquet"):
            try:
                st = pd.read_parquet(os.path.join(sdir, f))
                out[f[:-8]] = float(st["AMT_OUTSTANDING"].iloc[0])
            except Exception:
                pass
    return out


def cmt(market, bucket):
    p = os.path.join(CMT_DIR, f"{market}__{bucket}.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    return df


def cmt_buckets(market):
    pre = f"{market}__"
    return sorted((f[len(pre):-8] for f in os.listdir(CMT_DIR)
                   if f.startswith(pre) and f.endswith(".parquet")),
                  key=lambda b: float(b[:-1]))


def intl_markets():
    return sorted({f.split("__")[0] for f in os.listdir(CMT_DIR) if f.endswith(".parquet")})


def bond_returns(isin):
    """Per-bond financed return sheet (engine_intl): daily 'bp' = net DV01-normalized return."""
    p = os.path.join(CACHE_INTL, "returns", f"{isin}.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index)
    return df
