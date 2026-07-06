"""
EXPERIMENT 2 — multi-factor controls on the energy hedge (Barclays guide: their 10y BE model is
slope + RBOB + VIX + dollar, adj-R^2 ~0.83 vs our single-factor 0.12-0.20).

Two questions, both about OUR existing product:
  1. Is the gasoline/Brent hedge beta STABLE once curve slope and the dollar are controlled for?
     (Omitted-variable bias could be part of the 2020-22 beta hump.)
  2. Does deflating crude by the broad dollar tighten the hedge regression? (Guide: dollar-adjusted
     crude fits fundamentals R^2 0.81 vs 0.68 raw.)

Data: needs a one-time experimental pull (Bloomberg terminal open):
    .venv/Scripts/python.exe experiments/exp_factors.py pull
Then analyze any time (no terminal):
    .venv/Scripts/python.exe experiments/exp_factors.py
Out: experiments/out/factor_hedge_us.csv / factor_hedge_intl.csv / rolling beta CSVs + summary.

Conventions: identical to hedge.py / energy_intl.py — per-leg $ P&L summed into the energy-day
intervals, be = linker - nominal at beta=1, factor moves differenced at the same interval closes.
"""
from __future__ import annotations
import os, sys
import exp_common as C
import numpy as np
import pandas as pd

FACTORS_PARQUET = os.path.join(C.ECACHE, "factors.parquet")
TICKERS = {                                # ticker -> short name (each tolerated to fail)
    "USGG10YR Index": "us10y",
    "USGG3M Index":   "us3m",
    "BBDXY Index":    "dollar",            # broad dollar; DXY fallback below
    "DXY Curncy":     "dxy",
    "VIX Index":      "vix",
    "TZT1 Comdty":    "ttf",               # Dutch TTF front — the euro-BE gas factor
    "UKBRBASE Index": "ukbase",            # UK Bank Rate (RPI mortgage-interest channel)
}
START = "20030101"


def pull():
    """One-time experimental pull -> experiments/cache/factors.parquet (terminal required)."""
    import bbg
    today = pd.Timestamp.today().strftime("%Y%m%d")
    bbg.open_session()
    try:
        h = bbg.history(list(TICKERS), ["PX_LAST"], START, today)
    finally:
        bbg.close_session()
    cols = {}
    for sec, name in TICKERS.items():
        rows = h.get(sec, [])
        if rows:
            d = pd.DataFrame(rows)
            d["date"] = pd.to_datetime(d["date"])
            cols[name] = d.set_index("date")["PX_LAST"].astype(float)
            print(f"  {sec:16s} -> {name:7s} n={len(cols[name])}")
        else:
            print(f"  {sec:16s} -> NO DATA (skipped)")
    df = pd.DataFrame(cols).sort_index()
    df.to_parquet(FACTORS_PARQUET)
    print(f"  wrote {FACTORS_PARQUET}  {df.shape}")


def _factors():
    if not os.path.exists(FACTORS_PARQUET):
        print("  [exp_factors] no factors cache — run  experiments/exp_factors.py pull  "
              "on the terminal first")
        return None
    f = pd.read_parquet(FACTORS_PARQUET)
    if "dollar" not in f and "dxy" in f:
        f["dollar"] = f["dxy"]
    f["slope"] = f.get("us10y", np.nan) - f.get("us3m", np.nan)     # 3m10y, pct pts
    f["logusd"] = np.log(f["dollar"])
    return f


def _interval_moves(f, eidx, cols):
    """Factor changes over the same energy intervals the hedge uses: level at each energy close
    (ffilled) differenced -> exactly one move per interval."""
    lev = f[cols].reindex(f.index.union(eidx)).ffill().reindex(eidx)
    return lev.diff()


def _study(pairs, crude_col, be_col, f, controls, label):
    """Univariate vs controlled beta + the dollar-deflation R^2 test, one series."""
    if pairs is None or pairs.empty or len(pairs) < 200:
        return None
    mv = _interval_moves(f, pairs.index, [c for c in ["slope", "logusd", "vix", "ttf"]
                                          if c in f and c in controls or c in controls])
    mv = mv[[c for c in controls if c in mv]]
    uni = C.ols(pairs[crude_col], pairs[be_col])
    X = pd.concat([pairs[crude_col].rename("crude"), mv], axis=1)
    mult = C.mols(X, pairs[be_col])
    # dollar-deflation: rescale each interval's crude $ move by the dollar level (base = start)
    defl = None
    if "logusd" in f:
        usd = f["logusd"].reindex(f.index.union(pairs.index)).ffill().reindex(pairs.index)
        adj = pairs[crude_col] * np.exp(-(usd - usd.dropna().iloc[0]))
        defl = C.ols(adj, pairs[be_col])
    if mult is None:
        return None
    return {"series": label, "n": mult["n"],
            "beta_uni": round(uni["slope"], 3), "r2_uni": round(uni["r2"], 3),
            "beta_ctl": round(mult["coef"]["crude"], 3), "t_ctl": round(mult["t"]["crude"], 1),
            "r2_ctl": round(mult["r2"], 3),
            "beta_shift_pct": round(100 * (mult["coef"]["crude"] / uni["slope"] - 1), 1)
            if uni["slope"] else np.nan,
            "r2_usdadj": round(defl["r2"], 3) if defl else np.nan,
            "ctl_t": " ".join(f"{k}:{mult['t'][k]:+.1f}" for k in mv.columns)}


def _rolling(pairs, crude_col, be_col, f, controls, label, years=2):
    """Rolling beta with vs without controls -> CSV (stability / 2020-22 hump check)."""
    mv = _interval_moves(f, pairs.index, controls)[
        [c for c in controls if c in f.columns]]
    rows = []
    idx = pairs.index
    for k in range(len(idx)):
        end = idx[k]
        start = end - pd.DateOffset(years=years)
        w = pairs.loc[start:end]
        if len(w) < 250 or (k % 21):                       # monthly steps
            continue
        uni = C.ols(w[crude_col], w[be_col])
        m = C.mols(pd.concat([w[crude_col].rename("crude"), mv.loc[start:end]], axis=1), w[be_col])
        rows.append({"date": end, "beta_uni": uni["slope"],
                     "beta_ctl": m["coef"]["crude"] if m else np.nan})
    df = pd.DataFrame(rows).set_index("date")
    df.to_csv(f"{C.OUT}/factor_rolling_{label}.csv")
    return df


def run():
    f = _factors()
    if f is None:
        return
    have = [c for c in ["slope", "logusd", "vix", "ttf", "ukbase"] if c in f and f[c].notna().sum() > 500]
    print(f"  factors available: {have}")

    print("\n== US: gasoline hedge with slope/dollar/VIX controls (hedge.py conventions) ==")
    import hedge
    us = []
    for tenor in C.US_TENORS:
        pairs = hedge.aligned_pairs(tenor)
        r = _study(pairs, "gas_usd", "be_usd", f, ["slope", "logusd", "vix"], f"US_{tenor}")
        if r:
            us.append(r)
            _rolling(pairs, "gas_usd", "be_usd", f, ["slope", "logusd", "vix"], f"US_{tenor}")
    dfu = pd.DataFrame(us)
    if len(dfu):
        dfu.to_csv(f"{C.OUT}/factor_hedge_us.csv", index=False)
        print(dfu.to_string(index=False))

    print("\n== intl: Brent hedge with dollar/VIX(/TTF) controls (energy_intl conventions) ==")
    import energy_intl
    ctl_intl = [c for c in ["logusd", "vix", "ttf"] if c in have]
    rows = []
    for mkt in C.intl_markets():
        for b in C.cmt_buckets(mkt):
            pairs = energy_intl.aligned_pairs(mkt, b)
            r = _study(pairs, "crude", "be", f, ctl_intl, f"{mkt}_{b}")
            if r:
                rows.append(r)
    dfi = pd.DataFrame(rows)
    if len(dfi):
        dfi.to_csv(f"{C.OUT}/factor_hedge_intl.csv", index=False)
        big = dfi[np.abs(dfi["beta_shift_pct"]) >= 25].sort_values("beta_shift_pct", key=np.abs,
                                                                   ascending=False)
        print(f"  {len(dfi)} series; {len(big)} where controls move the crude beta >=25%:")
        print((big if len(big) else dfi.head(10)).to_string(index=False))

    print(f"\n  read: beta_uni vs beta_ctl = crude beta before/after controls (a big shift = the"
          f"\n        single-factor hedge was absorbing slope/dollar moves); r2_usdadj vs r2_uni ="
          f"\n        does dollar-deflated crude explain BE better. Rolling betas in out/.")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "pull":
        pull()
    else:
        run()
