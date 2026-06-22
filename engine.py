"""
Financed breakeven total-return engine (reference.MD §5-§9 + desk BUILD SPEC).

Per tenor (5y/10y/30y) and leg (TIPS, UST), build a daily DV01-normalized return series,
then r_BE = r_TIPS - r_UST. Each leg is computed AS A LONG financed at GC mid; the minus
sign on the UST leg encodes the short. Legs are stored separately so an arbitrary beta can
be applied later as r_TIPS - beta * r_UST without rebuilding.

Construction (desk decisions, confirmed):
  * Each leg renormalized DAILY to 100k DV01: bp_t = $PnL_t / DV01_{t-1}.  Algebraically
    notional-free -> bp_t = (dV + coupon - financing) per 100 face / (DV01 per 100 face)_{t-1}.
  * DV01: real-yield DV01 x IR for TIPS, nominal-yield DV01 for UST (computed in pricing.py;
    we do NOT use BBG's TIPS RISK_MID -- it is ~half).
  * V (cash): TIPS = (clean+accrued) x IR ; UST = clean+accrued.
  * dV is day-over-day within the SAME bond; at a monthly roll the new bond's first-day dV
    uses its OWN prior-day price (returns are spliced, never levels).
  * Coupon: booked the day a coupon date passes (TIPS = C/2 x IR(pay date)); dirty already
    drops via the accrued reset. No reinvestment / notional does not grow.
  * Financing: days/360 x GC x V_{t-1}, GC = GCFRTSY (mid), both legs as longs.
  * Roll: 1st business day of the month per the OTR schedule (auctions.py, §9.2.1).
  * Accumulate LINEARLY (cumsum of daily bp), not compounded. Convexity ignored.

Usage:
  python engine.py            # build 5y, 10y, 30y -> cache/returns_<tenor>.parquet
  python engine.py 10y        # one tenor, with a summary
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import data_layer as dl
import auctions
import pricing

CACHE = dl.CACHE


def _macro():
    return pd.read_parquet(os.path.join(CACHE, "macro.parquet"))


def gc_series():
    """GC financing rate (percent), daily, ffilled. GCFRTSY primary; USRG1T then fed funds
    extend it back before 2009-11 (same GC concept) so early tenors aren't dropped."""
    m = _macro()
    gc = m["gcf_treasury"]
    if "gc_repo_on" in m:
        gc = gc.combine_first(m["gc_repo_on"])
    if "fed_funds" in m:
        gc = gc.combine_first(m["fed_funds"])
    return gc.sort_index().ffill()


def _static(cusip):
    s = pd.read_parquet(os.path.join(CACHE, "static", f"{cusip}.parquet")).iloc[0]
    return float(s["CPN"]), pd.Timestamp(s["MATURITY"]), float(s["BASE_CPI"]) if pd.notna(s.get("BASE_CPI")) else None


_AUCT = None
def _dated_date(cusip):
    """Dated date (accrual start) from the cached auction calendar; fallback to issue date."""
    global _AUCT
    if _AUCT is None:
        _AUCT = auctions.load_auctions()
    rows = _AUCT[_AUCT["cusip"] == cusip]
    if not rows.empty and pd.notna(rows["datedDate"].iloc[0]):
        return pd.Timestamp(rows["datedDate"].iloc[0])
    s = pd.read_parquet(os.path.join(CACHE, "static", f"{cusip}.parquet")).iloc[0]
    return pd.Timestamp(s["ISSUE_DT"])


def _bond_pack(cusip, leg, cpi):
    """Precompute one bond's daily metrics + coupon schedule for the return walk."""
    path = os.path.join(CACHE, "daily", f"{cusip}.parquet")
    if not os.path.exists(path):
        return None
    d = pd.read_parquet(path)
    if not {"PX_CLEAN_MID", "YLD_YTM_MID"}.issubset(d.columns):
        return None
    df = d[["PX_CLEAN_MID", "YLD_YTM_MID"]].dropna()
    if len(df) < 2:
        return None
    coupon, maturity, base_cpi = _static(cusip)
    dated = _dated_date(cusip)
    ir = None
    if leg == "tips":
        if base_cpi is None:
            return None
        ir = dl.index_ratio_series(cpi, base_cpi, df.index)
    m = pricing.bond_metrics(df["PX_CLEAN_MID"], df["YLD_YTM_MID"], coupon, maturity, dated, ir=ir)
    pds = pricing.pay_dates(maturity, dated)
    return {"m": m, "pay": pds, "coupon": coupon, "base_cpi": base_cpi, "leg": leg}


def leg_series(leg, tenor, cpi, gc):
    """DV01-normalized bp return for one leg/tenor, spliced across the OTR schedule.

    DV01 denominator is set at the MONTHLY rebalance (the DV01 of the month's OTR bond at
    the start of the month) and held CONSTANT within the month -- consistent with rebalancing
    the position to 100k DV01 each month rather than every day. Returns a DataFrame indexed by
    date with: bp (the mid-financed return) and fin_sens (bp drag per 1bp of repo half-spread,
    so the interactive tool can re-apply repo spreads without rebuilding)."""
    sched = auctions.otr_schedule()
    sub = sched[(sched.leg == leg) & (sched.tenor == tenor)].sort_values("month")
    if sub.empty:
        return pd.DataFrame(columns=["bp", "fin_sens"])
    month_cusip = {pd.Timestamp(r.month): r.cusip for r in sub.itertuples()}
    packs = {}
    for c in sub["cusip"].unique():
        p = _bond_pack(c, leg, cpi)
        if p is not None:
            packs[c] = p
    rows = {}
    for mo in sorted(month_cusip):
        c = month_cusip[mo]
        if c not in packs:
            continue
        pk = packs[c]; m = pk["m"]
        nextmo = mo + pd.DateOffset(months=1)
        before = m.index[m.index < mo]
        in_month = m.index[(m.index >= mo) & (m.index < nextmo)]
        # monthly rebalance DV01: the month's OTR bond DV01 at the start of the holding period
        # (last obs before the month = the rebalance point), held constant all month.
        denom = m["dv01_per100"].loc[before[-1]] if len(before) else float("nan")
        if (not np.isfinite(denom) or denom == 0) and len(in_month):
            denom = m["dv01_per100"].loc[in_month[0]]
        if not np.isfinite(denom) or denom == 0:
            continue
        for t in in_month:
            iloc = m.index.get_loc(t)
            if iloc == 0:
                continue                       # no prior obs in this bond
            tprev = m.index[iloc - 1]
            rt, rp = m.iloc[iloc], m.iloc[iloc - 1]
            Vt, Vp = rt["V"], rp["V"]
            if not np.isfinite(Vt) or not np.isfinite(Vp):
                continue
            dV = Vt - Vp
            days = (t - tprev).days
            g = gc.asof(tprev)
            fin = days / 360.0 * (g / 100.0) * Vp if np.isfinite(g) else 0.0
            cpn = 0.0
            for pdte in pk["pay"]:
                if tprev < pdte <= t:
                    irc = float(rt["IR"]) if leg == "tips" else 1.0
                    cpn += (pk["coupon"] / 2.0) * irc
            pnl = dV + cpn - fin
            bp = pnl / denom
            # bp drag per 1bp of repo half-spread x: extra financing days/360 * (x/10000) * Vp.
            fin_sens = (days / 360.0 * Vp / 10000.0) / denom
            rows[t] = {"cusip": c, "clean": rt["clean"], "yield": rt["ytm"],
                       "accrued": rt["accrued"], "IR": rt["IR"], "dirty_real": rt["dirty_real"],
                       "V": Vt, "DV01": rt["dv01_per100"], "denom": denom, "dV": dV,
                       "coupon": cpn, "days": days, "gc": g, "financing": fin, "pnl": pnl,
                       "bp": bp, "fin_sens": fin_sens}
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def build_tenor(tenor, save=True):
    cpi = _macro()["cpi_nsa"]
    gc = gc_series()
    t = leg_series("tips", tenor, cpi, gc)
    u = leg_series("nominal", tenor, cpi, gc)
    df = pd.DataFrame({
        "r_TIPS_bp": t["bp"], "r_UST_bp": u["bp"],
        "s_TIPS": t["fin_sens"], "s_UST": u["fin_sens"],   # bp drag per 1bp repo half-spread
    }).sort_index()
    df["r_BE_bp"] = df["r_TIPS_bp"] - df["r_UST_bp"]
    df["cum_TIPS_bp"] = df["r_TIPS_bp"].cumsum()
    df["cum_UST_bp"] = df["r_UST_bp"].cumsum()
    df["cum_BE_bp"] = df["r_BE_bp"].cumsum()
    if save:
        df.to_parquet(os.path.join(CACHE, f"returns_{tenor}.parquet"))
    return df


def load_returns(tenor):
    return pd.read_parquet(os.path.join(CACHE, f"returns_{tenor}.parquet"))


def apply_spread(df, xT=0.0, xU=0.0):
    """Add long/short breakeven daily bp at the given repo half-spreads (bp).
    Both directions carry the slippage drag (long pays GC+x, short earns GC-x)."""
    slip = xT * df["s_TIPS"] + xU * df["s_UST"]
    out = pd.DataFrame({
        "TIPS_bp": df["r_TIPS_bp"], "UST_bp": df["r_UST_bp"],
        "BEmid_bp": df["r_BE_bp"],
        "longBE_bp": df["r_BE_bp"] - slip,
        "shortBE_bp": -df["r_BE_bp"] - slip,
    }, index=df.index)
    return out


def window_table(tenor, start=None, end=None, xT=0.0, xU=0.0, freq="auto"):
    """Raw daily/monthly returns over a window. Returns (table, totals):
      - span <= 45 days (or freq='D') -> one row per day;
      - otherwise -> one row per calendar month (sum of daily bp).
    totals = sum of each column over the whole window (the window net P&L in bp)."""
    d = apply_spread(load_returns(tenor), xT, xU)
    if start:
        d = d[d.index >= pd.Timestamp(start)]
    if end:
        d = d[d.index <= pd.Timestamp(end)]
    d = d.dropna(how="all")
    totals = d.sum()
    if len(d) == 0:
        return d, totals
    span = (d.index.max() - d.index.min()).days
    if freq == "auto":
        freq = "D" if span <= 45 else "M"
    if freq == "M":
        tbl = d.resample("ME").sum()
        tbl.index = tbl.index.strftime("%Y-%m")
    else:
        tbl = d.copy()
        tbl.index = tbl.index.strftime("%Y-%m-%d")
    return tbl, totals


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "window":
        # python engine.py window <tenor> [start] [end] [xT] [xU]
        a = sys.argv
        tenor = a[2] if len(a) > 2 else "10y"
        start = a[3] if len(a) > 3 and a[3] != "-" else None
        end = a[4] if len(a) > 4 and a[4] != "-" else None
        xT = float(a[5]) if len(a) > 5 else 0.0
        xU = float(a[6]) if len(a) > 6 else xT
        tbl, tot = window_table(tenor, start, end, xT, xU)
        with pd.option_context("display.max_rows", 400, "display.width", 160):
            print(f"=== {tenor} returns (bp)  window={start or 'start'}..{end or 'end'}  "
                  f"repo x_TIPS={xT} x_UST={xU} ===")
            print(tbl.round(2).to_string())
            print("-" * 60)
            print("TOTAL (window net P&L, bp):")
            print(tot.round(1).to_string())
        sys.exit(0)
    tenors = [sys.argv[1]] if len(sys.argv) > 1 else ["5y", "10y", "30y"]
    for ten in tenors:
        df = build_tenor(ten)
        be = df["r_BE_bp"].dropna()
        print(f"\n=== {ten}: {len(df)} days, {str(df.index.min())[:10]}..{str(df.index.max())[:10]} ===")
        print(f"  r_BE_bp/day: mean={be.mean():+.3f}  std={be.std():.3f}  "
              f"cum_BE={df['cum_BE_bp'].dropna().iloc[-1]:+.0f}bp  "
              f"cum_TIPS={df['cum_TIPS_bp'].dropna().iloc[-1]:+.0f}  cum_UST={df['cum_UST_bp'].dropna().iloc[-1]:+.0f}")
        print(f"  saved cache/returns_{ten}.parquet")
