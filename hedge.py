"""
Breakeven <-> gasoline hedge ratio, in NUMBER OF RBOB CONTRACTS (desk spec).

Goal (boss's framing): work out a monthly hedge ratio in number of gasoline contracts to
hold against a 100k-DV01 breakeven position, using $ changes (not %), so the answer is
directly "hold N contracts short/long for the month."

Unit bridge
-----------
  * 1 bp of breakeven  = $100,000          (each leg sized to 100k DV01 -> engine bp = $/100k)
        breakeven daily $ P&L  =  r_BE_bp * 1e5
  * 1.0 gasoline price-point (= 1 cent/gal) on ONE 42,000-gal contract = $420  (FUT_VAL_PT)
        gasoline daily $/contract = chg * 420   (energy.usd_per_contract)

Regress the breakeven $ P&L on the gasoline $/contract. Because Y is in $ and X is in
$-per-contract, the OLS slope is ALREADY the number of contracts:

        contracts = d($BE) / d($gas per 1 contract)

Worked example (boss): 10c gas <-> 2bp BE ($200k) => $20k/cent => /$420 = 47.6 -> ~48
contracts. Regressing $BE on $gas/contract reproduces this directly: slope = 200000/(10*420)
= 47.6. Sign: gas and breakeven co-move (gas up -> inflation up -> BE up), so the slope is
positive; to hedge a LONG breakeven you hold that many contracts SHORT (and vice-versa).

Estimation
----------
  * DAILY P&L regression (day-on-day, contemporaneous) -- ~250 pts/yr; smoother and less
    fiddly than block returns. (The earlier 5-trading-day block estimator was dropped: daily
    gives a much smoother hedge-ratio path.)
  * Walk-forward, rebalanced on the 1st trading day of each month -- IN SYNC with the engine's
    monthly DV01 rebalance. The ratio set at month M's rebalance uses only data BEFORE it, then
    is held all month.
  * TWO trailing windows reported side by side: 2-year and 5-year. The 5y window is much
    smoother and rides through confounding regimes (covid, the 2021-22 inflation spike) that a
    2y window can over-fit. Both are daily; pick per use.

Alignment
---------
Gas and breakeven are paired on the energy COMMON-day calendar (gas-trading-days INTERSECT
bond-trading-days, built in energy.py). The breakeven $ is SUMMED over each energy interval,
so when one market has an extra holiday the two sides cover the exact same span (a 2-3 day
step), matching the day-skip rule used to build the gas returns.

Usage
-----
  python hedge.py build      # all tenors -> cache/hedge_ratios.parquet (monthly, 2y & 5y daily)
  python hedge.py plot       # -> plots/hedge_ratios.png (contracts over time, per tenor)
  python hedge.py preview    # print the recent monthly hedge ratios per tenor
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import engine
import energy

CACHE = engine.CACHE
HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")
TENORS = ("5y", "10y", "30y")

BP_USD = 100_000.0          # 1 engine bp of breakeven = $100k (each leg sized to 100k DV01)
WINDOWS = {"2y": 2, "5y": 5}   # trailing rolling-regression windows (years), daily PnL
RATIOS = os.path.join(CACHE, "hedge_ratios.parquet")

# STATIC full-sample hedge ratios (desk decision: don't mechanically rebalance — run ONE regression
# over the entire horizon and hardcode the ratio per tenor, adjustable). Stored as the per-leg gas
# betas (bT, bU) so the β slider stays consistent: h(β) = bT − β·bU contracts. At β=100 the headline
# ratios are 5y≈75.9, 10y≈48.7, 30y≈33.7. These are HARDCODED PARAMETERS — edit to override; re-derive
# from the current data with full_sample_betas(tenor). (Full-sample = in-sample/structural, not a
# walk-forward backtest.)
FULL_HEDGE = {
    "5y":  (39.1140, -36.8186),
    "10y": (4.5349, -44.1762),
    "30y": (-12.8255, -46.5225),
}


def aligned_pairs(tenor, beta=1.0):
    """Daily (gas $/contract, per-leg & breakeven $) pairs on the energy common-day calendar.
    Each leg's daily $ P&L is SUMMED over the energy interval that closes it, so gas and bonds
    span the exact same days (handles the gas/bond holiday misalignment the same way energy.py
    does). Columns: gas_usd, tips_usd, ust_usd, be_usd (= tips_usd - beta*ust_usd; beta=1.0 is
    the pure equal-DV01 breakeven r_BE_bp), + is_roll, days. The per-leg columns let any beta's
    hedge ratio be formed as h(beta) = bT - beta*bU (see monthly_leg_betas)."""
    e = energy.load_energy()
    r = engine.load_returns(tenor)
    if e.empty or r.empty:
        return pd.DataFrame(columns=["gas_usd", "tips_usd", "ust_usd", "be_usd"])
    eidx = e.index

    def bucket(col):                                    # sum a bond-day $ series into energy intervals
        s = (r[col] * BP_USD).dropna()
        s = s[s.index <= eidx[-1]]                      # no bond days past the last common day
        pos = eidx.searchsorted(s.index, side="left").clip(max=len(eidx) - 1)
        return s.groupby(eidx[pos]).sum()

    out = pd.DataFrame({"gas_usd": e["usd_per_contract"], "tips_usd": bucket("r_TIPS_bp"),
                        "ust_usd": bucket("r_UST_bp"), "is_roll": e["is_roll"], "days": e["days"]})
    out["be_usd"] = out["tips_usd"] - beta * out["ust_usd"]
    return out.dropna(subset=["gas_usd", "be_usd"]).sort_index()


def _ols(x, y):
    """OLS slope/intercept of y on x with R^2, corr, t-stat(slope), n. NaNs dropped."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    if n < 3 or np.std(x) == 0:
        return {"slope": np.nan, "intercept": np.nan, "r2": np.nan, "corr": np.nan, "tstat": np.nan, "n": n}
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    sxy = ((x - xm) * (y - ym)).sum()
    slope = sxy / sxx
    intercept = ym - slope * xm
    resid = y - (intercept + slope * x)
    sigma2 = (resid ** 2).sum() / (n - 2)
    se = np.sqrt(sigma2 / sxx) if sxx > 0 else np.nan
    tstat = slope / se if se and np.isfinite(se) and se > 0 else np.nan
    corr = np.corrcoef(x, y)[0, 1]
    return {"slope": slope, "intercept": intercept, "r2": corr ** 2, "corr": corr, "tstat": tstat, "n": n}


def _window_ols(pairs, R, years):
    """Daily OLS over the trailing `years`-year window ending just before rebalance R, or None
    if the window isn't (near-)full yet (data must reach back to within ~2 months of `years`)."""
    w = pairs[(pairs.index >= R - pd.DateOffset(years=years)) & (pairs.index < R)]
    if len(w) < years * 150 or w.index.min() > R - pd.DateOffset(months=years * 12 - 2):
        return None
    return _ols(w["gas_usd"], w["be_usd"])


def monthly_hedge_ratios(tenor, windows=WINDOWS, beta=1.0):
    """Walk-forward monthly hedge ratio (contracts) for one tenor, from a DAILY regression of
    breakeven $ on gas $/contract over each trailing window in `windows`. One row per rebalance
    month (1st common trading day); a window's columns are NaN until it has ~that many years of
    history (so 2y fills from ~2013, 5y from ~2016). `beta` weights the nominal leg in the
    breakeven (1.0 = pure breakeven; 0.75 = UST under-weighted)."""
    pairs = aligned_pairs(tenor, beta=beta)
    if pairs.empty:
        return pd.DataFrame()
    per = pairs.index.to_period("M")
    rebal = pairs.index[~per.duplicated()]               # 1st common trading day of each month
    rows = []
    for R in rebal:
        rec = {"rebalance_date": R, "month": R.to_period("M").to_timestamp(), "tenor": tenor}
        have = False
        for tag, yrs in windows.items():
            o = _window_ols(pairs, R, yrs)
            if o is None:
                rec.update({f"hedge_contracts_{tag}": np.nan, f"r2_{tag}": np.nan,
                            f"corr_{tag}": np.nan, f"tstat_{tag}": np.nan, f"n_{tag}": 0})
            else:
                rec.update({f"hedge_contracts_{tag}": o["slope"], f"r2_{tag}": o["r2"],
                            f"corr_{tag}": o["corr"], f"tstat_{tag}": o["tstat"], f"n_{tag}": o["n"]})
                have = True
        if have:
            rows.append(rec)
    return pd.DataFrame(rows)


def _window(pairs, R, years):
    """Trailing `years`-year window of pairs ending just before R, or None if not (near-)full."""
    w = pairs[(pairs.index >= R - pd.DateOffset(years=years)) & (pairs.index < R)]
    if len(w) < years * 150 or w.index.min() > R - pd.DateOffset(months=years * 12 - 2):
        return None
    return w


def monthly_leg_betas(tenor, windows=WINDOWS):
    """Per (window, rebalance month): the per-LEG gas-regression slopes bT (TIPS leg) and bU (UST
    leg) -- slope of that leg's daily $ on gas $/contract over the trailing window. The hedge
    ratio for ANY beta is then h(beta) = bT - beta*bU contracts, so a continuous beta slider needs
    only these two numbers per month (the dashboard forms h client-side). One row per rebalance
    month with bT_<w>, bU_<w>, n_<w> per window (NaN until the window is ~full)."""
    pairs = aligned_pairs(tenor)                        # beta irrelevant for the per-leg slopes
    if pairs.empty:
        return pd.DataFrame()
    per = pairs.index.to_period("M")
    rebal = pairs.index[~per.duplicated()]
    rows = []
    for R in rebal:
        rec = {"rebalance_date": R, "month": R.to_period("M").to_timestamp(), "tenor": tenor}
        have = False
        for tag, yrs in windows.items():
            w = _window(pairs, R, yrs)
            if w is None:
                rec.update({f"bT_{tag}": np.nan, f"bU_{tag}": np.nan, f"n_{tag}": 0})
            else:
                rec.update({f"bT_{tag}": _ols(w["gas_usd"], w["tips_usd"])["slope"],
                            f"bU_{tag}": _ols(w["gas_usd"], w["ust_usd"])["slope"], f"n_{tag}": len(w)})
                have = True
        if have:
            rows.append(rec)
    return pd.DataFrame(rows)


def daily_gas_usd(index=None):
    """energy.usd_per_contract (daily gas $/contract), reindexed onto `index` with 0 where the day
    isn't a gas common day (gas-gap/bond holiday). The gas move on a skipped day lands on the next
    common day by construction, so the monthly/cumulative hedge P&L stays correct."""
    g = energy.load_energy()["usd_per_contract"]
    return g if index is None else g.reindex(index).fillna(0.0)


def hedge_coef_map(tenor, windows=WINDOWS):
    """For the dashboard payload: {window: {'YYYY-MM': [bT, bU]}} for the rolling windows (monthly
    rebalance) PLUS {'full': [bT, bU]} -- the STATIC full-sample coefficients (no month key, used
    for every date). Client forms h(beta)=bT-beta*bU and the hedged P&L for any beta/window."""
    lb = monthly_leg_betas(tenor, windows)
    out = {tag: {} for tag in windows}
    for _, r in lb.iterrows():
        ym = pd.Timestamp(r["month"]).strftime("%Y-%m")
        for tag in windows:
            bT, bU = r[f"bT_{tag}"], r[f"bU_{tag}"]
            if pd.notna(bT) and pd.notna(bU):
                out[tag][ym] = [round(float(bT), 4), round(float(bU), 4)]
    bT, bU = FULL_HEDGE.get(tenor, (None, None))           # static full-sample (no rebalance)
    if bT is not None:
        out["full"] = [round(float(bT), 4), round(float(bU), 4)]
    return out


def build_all(tenors=TENORS, save=True):
    frames = [monthly_hedge_ratios(t) for t in tenors]
    frames = [f for f in frames if not f.empty]
    if not frames:
        print("  no hedge ratios — is energy.parquet / returns_*.parquet built?")
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).sort_values(["tenor", "rebalance_date"])
    if save:
        df.to_parquet(RATIOS)
        for t in tenors:
            s = df[df["tenor"] == t]
            if s.empty:
                continue
            last = s.iloc[-1]
            parts = []
            for tag in WINDOWS:
                c, r2 = last.get(f"hedge_contracts_{tag}"), last.get(f"r2_{tag}")
                parts.append(f"{tag}={c:.1f} (R2={r2:.2f})" if pd.notna(c) else f"{tag}=n/a")
            print(f"  {t}: {len(s)} months {str(s['month'].min())[:7]}..{str(s['month'].max())[:7]} | "
                  f"latest {str(last['month'])[:7]} " + " ".join(parts) + " contracts")
        print(f"  wrote {RATIOS}  ({len(df)} rows)")
    return df


def load_ratios():
    return pd.read_parquet(RATIOS)


def full_sample_betas(tenor):
    """(bT, bU) from a single regression over the ENTIRE aligned horizon (no rolling) — the static
    full-sample gas betas. Used to (re)derive the hardcoded FULL_HEDGE constants."""
    p = aligned_pairs(tenor)
    if p.empty:
        return (float("nan"), float("nan"))
    return (_ols(p["gas_usd"], p["tips_usd"])["slope"], _ols(p["gas_usd"], p["ust_usd"])["slope"])


def daily_hedge_beta(tenor, index, window="5y", beta=1.0):
    """Daily-forward-filled hedge ratio (contracts) for `beta` and `window`: each date maps to its
    month's h(beta) = bT - beta*bU. NaN before the first available rebalance (no hedge yet).
    window='full' uses the STATIC hardcoded full-sample ratio (FULL_HEDGE) for every date — no
    rebalance, applied across the entire horizon."""
    if window == "full":
        bT, bU = FULL_HEDGE.get(tenor, (float("nan"), float("nan")))
        return pd.Series(bT - beta * bU, index=index)
    lb = monthly_leg_betas(tenor)
    if lb.empty or f"bT_{window}" not in lb:
        return pd.Series(np.nan, index=index)
    h = lb[f"bT_{window}"] - beta * lb[f"bU_{window}"]
    by_month = pd.Series(h.to_numpy(), index=pd.DatetimeIndex(lb["month"]).to_period("M"))
    return pd.Series(pd.DatetimeIndex(index).to_period("M").map(by_month).to_numpy(), index=index)


def daily_hedge(tenor, index, which="hedge_contracts_2y"):
    """Map a daily DatetimeIndex to the hedge ratio effective that month (the ratio set at the
    month's rebalance, held all month). Returns a Series aligned to `index` (NaN before the
    first available rebalance). Used to fold the monthly ratio into the daily export sheets."""
    try:
        df = load_ratios()
    except Exception:
        return pd.Series(np.nan, index=index)
    s = df[df["tenor"] == tenor]
    if s.empty or which not in s:
        return pd.Series(np.nan, index=index)
    by_month = s.set_index(s["month"].dt.to_period("M"))[which]
    return pd.Series(pd.DatetimeIndex(index).to_period("M").map(by_month).to_numpy(), index=index)


def plot_hedge_ratios(path=None):
    """Plot the monthly hedge ratio (contracts) over time, per tenor -- the boss's 'how do they
    evolve' chart. Top: contracts (5y window solid, 2y window faint). Bottom: R^2. All daily."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    os.makedirs(PLOTS, exist_ok=True)
    try:
        df = load_ratios()
    except Exception:
        df = build_all()
    if df.empty:
        print("  nothing to plot")
        return
    path = path or os.path.join(PLOTS, "hedge_ratios.png")
    colors = {"5y": "tab:blue", "10y": "tab:green", "30y": "tab:red"}
    # window -> line style (solid 5y window = the smooth one; faint dotted 2y window)
    styles = {"5y": dict(lw=1.6, ls="-"), "2y": dict(lw=0.9, ls=":", alpha=0.7)}
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    fig.suptitle("Gasoline hedge ratio vs breakeven — contracts per 100k-DV01 BE "
                 "(daily P&L, monthly rebalance, 2y & 5y rolling windows)", fontsize=12)
    for t in TENORS:
        s = df[df["tenor"] == t].sort_values("month")
        if s.empty:
            continue
        c = colors[t]
        for tag in ("5y", "2y"):
            col = f"hedge_contracts_{tag}"
            if col in s:
                axes[0].plot(s["month"], s[col], color=c, label=f"{t} ({tag} window)", **styles[tag])
                axes[1].plot(s["month"], s[f"r2_{tag}"], color=c, label=f"{t} ({tag})", **styles[tag])
    axes[0].set_ylabel("hedge ratio (# contracts)"); axes[0].axhline(0, color="k", lw=0.5)
    axes[0].legend(loc="upper left", fontsize=7, ncol=3); axes[0].grid(alpha=0.3)
    axes[1].set_ylabel("regression R²"); axes[1].set_ylim(0, 1)
    axes[1].legend(loc="upper left", fontsize=7, ncol=3); axes[1].grid(alpha=0.3)
    axes[1].xaxis.set_major_locator(mdates.YearLocator()); axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(path, dpi=110); plt.close(fig)
    print(f"  wrote {path}")
    return path


def preview(rows=18):
    try:
        df = load_ratios()
    except Exception:
        df = build_all()
    cols = []
    for tag in WINDOWS:
        cols += [f"hedge_contracts_{tag}", f"r2_{tag}", f"tstat_{tag}", f"n_{tag}"]
    with pd.option_context("display.max_rows", 200, "display.width", 180):
        for t in TENORS:
            s = df[df["tenor"] == t].sort_values("month")
            if s.empty:
                continue
            print(f"\n=== {t} monthly hedge ratio (contracts), daily 2y vs 5y — last {rows} months ===")
            show = s.set_index(s["month"].dt.strftime("%Y-%m"))[cols]
            print(show.tail(rows).round(2).to_string())


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build_all()
    elif cmd == "plot":
        plot_hedge_ratios()
    elif cmd == "preview":
        preview(int(sys.argv[2]) if len(sys.argv) > 2 else 18)
    else:
        print(__doc__)
