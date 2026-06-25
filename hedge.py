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

Estimation (boss spec)
----------------------
  * Walk-forward, rebalanced on the 1st trading day of each month -- IN SYNC with the
    engine's monthly DV01 rebalance. The ratio set at month M's rebalance uses only data
    BEFORE it (a trailing window), then is held all month.
  * Trailing window = 2 years.
  * Primary estimator = DISJOINT (non-overlapping) 5-trading-day block $ changes (~100
    points / 2yr) to damp daily noise. Cross-check = day-on-day (~500 points / 2yr) -- "should
    not be too different."
  * Regression is CONTEMPORANEOUS (gas vs breakeven over the SAME block/day): the hedge is
    held concurrently, and contemporaneous daily ~ contemporaneous 5-day is exactly why the
    two estimators agree. (A lead-lag "predict next block" variant is a one-line flip of the
    `lead` arg if the desk wants it.)

Alignment
---------
Gas and breakeven are paired on the energy COMMON-day calendar (gas-trading-days INTERSECT
bond-trading-days, built in energy.py). The breakeven $ is SUMMED over each energy interval,
so when one market has an extra holiday the two sides cover the exact same span (a 2-3 day
step), matching the day-skip rule used to build the gas returns.

Usage
-----
  python hedge.py build      # all tenors -> cache/hedge_ratios.parquet (monthly, contracts + stats)
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
WINDOW_YEARS = 2
BLOCK = 5                   # disjoint 5-trading-day blocks (the primary estimator)
MIN_DAILY = 250             # require ~1yr of history before emitting a ratio
MIN_BLOCKS = 50
RATIOS = os.path.join(CACHE, "hedge_ratios.parquet")


def aligned_pairs(tenor):
    """Daily (gas $/contract, breakeven $) pairs on the energy common-day calendar.
    The breakeven daily $ P&L is SUMMED over each energy interval so both sides span the
    exact same days (handles the gas/bond holiday misalignment the same way energy.py does).
    Returns a DataFrame indexed by common day with columns gas_usd, be_usd (+ is_roll, days)."""
    e = energy.load_energy()
    r = engine.load_returns(tenor)
    if e.empty or r.empty:
        return pd.DataFrame(columns=["gas_usd", "be_usd"])
    eidx = e.index
    be = (r["r_BE_bp"] * BP_USD).dropna()
    be = be[be.index <= eidx[-1]]                       # no bond days past the last common day
    # map each bond-return day to the energy interval that CLOSES it (first common day >= it),
    # then sum the breakeven $ within each interval -> aligned to the gas spans.
    pos = eidx.searchsorted(be.index, side="left").clip(max=len(eidx) - 1)
    be_by_t = be.groupby(eidx[pos]).sum()
    out = pd.DataFrame({"gas_usd": e["usd_per_contract"], "be_usd": be_by_t,
                        "is_roll": e["is_roll"], "days": e["days"]}).dropna(subset=["gas_usd", "be_usd"])
    return out.sort_index()


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


def _blocks(df, block=BLOCK, lead=0):
    """Sum (gas_usd, be_usd) into DISJOINT consecutive `block`-day chunks, anchored at the
    most-recent end so the latest block is always full; partial oldest block dropped. `lead`>0
    pairs gas block i with be block i+lead (predictive); lead=0 is contemporaneous."""
    n = len(df)
    if n < block:
        return pd.DataFrame(columns=["gas_usd", "be_usd"])
    bid = (n - 1 - np.arange(n)) // block                # 0 = most-recent block, counting back
    g = df.assign(_b=bid).groupby("_b")
    sums = g[["gas_usd", "be_usd"]].sum()
    sums = sums[g.size() == block].sort_index(ascending=False)   # chronological, full blocks only
    if lead:
        sums = pd.DataFrame({"gas_usd": sums["gas_usd"].to_numpy()[:-lead],
                             "be_usd": sums["be_usd"].to_numpy()[lead:]})
    return sums


def monthly_hedge_ratios(tenor, window_years=WINDOW_YEARS, block=BLOCK, lead=0):
    """Walk-forward monthly hedge ratio (contracts) for one tenor. For each month's rebalance
    (1st common trading day of the month), regress trailing-`window_years` breakeven $ on gas
    $/contract -- both the 5-day-block estimator (primary) and day-on-day (cross-check).
    Returns one row per rebalance month (only once the window has enough history)."""
    pairs = aligned_pairs(tenor)
    if pairs.empty:
        return pd.DataFrame()
    per = pairs.index.to_period("M")
    rebal = pairs.index[~per.duplicated()]               # 1st common trading day of each month
    span_floor = pd.DateOffset(months=window_years * 12 - 2)   # require a (near-)full 2yr window
    rows = []
    for R in rebal:
        w = pairs[(pairs.index >= R - pd.DateOffset(years=window_years)) & (pairs.index < R)]
        if len(w) < MIN_DAILY or w.index.min() > R - span_floor:   # not ~2 full years of history yet
            continue
        od = _ols(w["gas_usd"], w["be_usd"])
        b = _blocks(w[["gas_usd", "be_usd"]], block, lead)
        ob = _ols(b["gas_usd"], b["be_usd"]) if len(b) >= MIN_BLOCKS else _ols([], [])
        rows.append({
            "rebalance_date": R, "month": R.to_period("M").to_timestamp(), "tenor": tenor,
            "hedge_contracts": ob["slope"], "r2_block": ob["r2"], "corr_block": ob["corr"],
            "tstat_block": ob["tstat"], "n_block": ob["n"],
            "hedge_contracts_daily": od["slope"], "r2_daily": od["r2"], "corr_daily": od["corr"],
            "tstat_daily": od["tstat"], "n_daily": od["n"],
        })
    return pd.DataFrame(rows)


def build_all(tenors=TENORS, save=True, lead=0):
    frames = [monthly_hedge_ratios(t, lead=lead) for t in tenors]
    frames = [f for f in frames if not f.empty]
    if not frames:
        print("  no hedge ratios — is energy.parquet / returns_*.parquet built?")
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).sort_values(["tenor", "rebalance_date"])
    if save:
        df.to_parquet(RATIOS)
        for t in tenors:
            s = df[df["tenor"] == t]
            if not s.empty:
                last = s.iloc[-1]
                print(f"  {t}: {len(s)} months {str(s['month'].min())[:7]}..{str(s['month'].max())[:7]} | "
                      f"latest {str(last['month'])[:7]} block={last['hedge_contracts']:.1f} contracts "
                      f"(R2={last['r2_block']:.2f}), daily={last['hedge_contracts_daily']:.1f}")
        print(f"  wrote {RATIOS}  ({len(df)} rows)")
    return df


def load_ratios():
    return pd.read_parquet(RATIOS)


def daily_hedge(tenor, index, which="hedge_contracts"):
    """Map a daily DatetimeIndex to the hedge ratio effective that month (the ratio set at the
    month's rebalance, held all month). Returns a Series aligned to `index` (NaN before the
    first available rebalance). Used to fold the monthly ratio into the daily export sheets."""
    try:
        df = load_ratios()
    except Exception:
        return pd.Series(np.nan, index=index)
    s = df[df["tenor"] == tenor]
    if s.empty:
        return pd.Series(np.nan, index=index)
    by_month = s.set_index(s["month"].dt.to_period("M"))[which]
    return pd.Series(pd.DatetimeIndex(index).to_period("M").map(by_month).to_numpy(), index=index)


def plot_hedge_ratios(path=None):
    """Plot the monthly hedge ratio (contracts) over time, per tenor -- the boss's 'how do they
    evolve' chart. Top: contracts (5-day-block solid + daily faint). Bottom: block R^2."""
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
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    fig.suptitle("Gasoline hedge ratio vs breakeven — contracts per 100k-DV01 BE "
                 f"({WINDOW_YEARS}y rolling, monthly rebalance, disjoint {BLOCK}-day blocks)", fontsize=12)
    for t in TENORS:
        s = df[df["tenor"] == t].sort_values("month")
        if s.empty:
            continue
        c = colors[t]
        axes[0].plot(s["month"], s["hedge_contracts"], color=c, lw=1.4, label=f"{t} (5-day block)")
        axes[0].plot(s["month"], s["hedge_contracts_daily"], color=c, lw=0.8, ls=":", alpha=0.7,
                     label=f"{t} (daily)")
        axes[1].plot(s["month"], s["r2_block"], color=c, lw=1.2, label=f"{t}")
    axes[0].set_ylabel("hedge ratio (# contracts)"); axes[0].axhline(0, color="k", lw=0.5)
    axes[0].legend(loc="upper left", fontsize=8, ncol=3); axes[0].grid(alpha=0.3)
    axes[1].set_ylabel("block regression R²"); axes[1].set_ylim(0, 1)
    axes[1].legend(loc="upper left", fontsize=8, ncol=3); axes[1].grid(alpha=0.3)
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
    with pd.option_context("display.max_rows", 200, "display.width", 180):
        for t in TENORS:
            s = df[df["tenor"] == t].sort_values("month")
            if s.empty:
                continue
            print(f"\n=== {t} monthly hedge ratio (contracts) — last {rows} months ===")
            show = s.set_index(s["month"].dt.strftime("%Y-%m"))[
                ["hedge_contracts", "r2_block", "tstat_block", "n_block",
                 "hedge_contracts_daily", "r2_daily", "n_daily"]]
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
