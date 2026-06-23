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
  * Roll: ISSUE-DATE-GATED, on the TIPS auction clock (see roll_schedule). A bond is used
    only on/after its issue date. TIPS roll 1st b-day after a new-issue month (5y May/Nov,
    10y Feb/Aug, 30y Mar); nominal 5y/30y roll on that boundary; nominal 10y is staggered
    (stays on the prior note until the new Feb/Aug note's 15th issue date). Freeze between.
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
    # value each obs day to its T+1 SETTLEMENT date: accrued/IR/DV01 to settle(obs)
    settle = _next_bday_array(df.index)
    ir = None
    if leg == "tips":
        if base_cpi is None:
            return None
        ir_at_settle = dl.index_ratio_series(cpi, base_cpi, settle)        # indexed by settle date
        ir = pd.Series(ir_at_settle.reindex(settle).to_numpy(), index=df.index)  # IR(settle) per obs
    m = pricing.bond_metrics(df["PX_CLEAN_MID"], df["YLD_YTM_MID"], coupon, maturity, dated,
                             ir=ir, settle=settle)
    pds = pricing.pay_dates(maturity, dated)
    return {"m": m, "pay": pds, "coupon": coupon, "base_cpi": base_cpi, "leg": leg}


_CAL = None
def trading_calendar():
    """Holiday-aware bond-market business-day calendar (fed funds publishes only on US business
    days), extended ~1y forward so next_bday() resolves for the most recent observations."""
    global _CAL
    if _CAL is None:
        base = pd.DatetimeIndex(sorted(_macro()["fed_funds"].dropna().index))
        fwd = pd.bdate_range(base.max() + pd.Timedelta(days=1), base.max() + pd.Timedelta(days=400))
        _CAL = base.union(fwd)
    return _CAL


def _next_bday_array(idx):
    """Vectorized T+1 settlement date for each date in idx (next bond-market business day)."""
    cal = trading_calendar()
    pos = cal.searchsorted(pd.DatetimeIndex(idx), side="right").clip(max=len(cal) - 1)
    return cal[pos]


def _first_bday_on_or_after(ts):
    cal = trading_calendar()
    i = cal.searchsorted(pd.Timestamp(ts))
    return cal[i] if i < len(cal) else None


def _next_bday(ts):
    """Next business day (T+1) on the bond-market calendar — the settlement date."""
    cal = trading_calendar()
    i = cal.searchsorted(pd.Timestamp(ts), side="right")
    return cal[i] if i < len(cal) else (pd.Timestamp(ts) + pd.offsets.BDay(1))


def roll_schedule(leg, tenor):
    """Issue-date-gated roll events [(effective_date, cusip)] (roll-fix spec). A bond is used
    only on/after its issue date; legs ride the TIPS auction clock and the nominal is fitted:

      TIPS (all tenors): roll on the 1st business day of the month AFTER the new-issue auction
        (the new TIPS settled at the prior month-end, so it is already spot-tradeable).
        -> 5y: May/Nov; 10y: Feb/Aug; 30y: Mar.   (reopenings keep the same CUSIP)
      Nominal 5y / 30y: roll ON the TIPS-roll date to the OTR nominal as of that date (its
        issue date is at/before the boundary -> clean, no mid-month switch).
      Nominal 10y (SPECIAL): the matching new note (Feb/Aug) isn't issued until the 15th,
        AFTER the TIPS roll on the 1st. So the nominal stays on the previous note for days
        1-14 and switches to the new note ON ITS ISSUE DATE (the 15th). Only Feb & Aug new
        notes are used; intervening new issues (May/Nov) are frozen out.
      Between TIPS rolls: FREEZE both legs (no rolling to intervening new issues).
    """
    ev = []   # (effective_date, issue_date, cusip)
    if leg == "tips":
        for _, r in auctions.new_issues("tips", tenor).iterrows():
            iss = pd.Timestamp(r["issueDate"])
            eff = _first_bday_on_or_after(iss + pd.offsets.MonthBegin(1))   # 1st b-day of next month
            if eff is not None:
                ev.append((eff, iss, r["cusip"]))
    elif tenor == "10y":                                   # nominal 10y: staggered, 15th of Feb/Aug
        ni = auctions.new_issues("nominal", "10y")
        ni = ni[pd.to_datetime(ni["issueDate"]).dt.month.isin([2, 8])]
        for _, r in ni.iterrows():
            iss = pd.Timestamp(r["issueDate"]); eff = _first_bday_on_or_after(iss)
            if eff is not None:
                ev.append((eff, iss, r["cusip"]))
    else:                                                  # nominal 5y / 30y: roll at TIPS boundary
        ni = auctions.new_issues("nominal", tenor).sort_values("issueDate")
        iss = pd.to_datetime(ni["issueDate"]).to_numpy(); cus = ni["cusip"].tolist()
        for rd, _c in roll_schedule("tips", tenor):
            j = np.searchsorted(iss, np.datetime64(pd.Timestamp(rd)), side="right") - 1
            if j >= 0:
                ev.append((pd.Timestamp(rd), pd.Timestamp(iss[j]), cus[j]))  # OTR nominal at boundary
    # collapse: at a given effective date keep the LATEST-issued cusip (handles pre-data issues
    # all clamped to the calendar start -> the one actually on-the-run at the start wins), then
    # drop consecutive duplicate cusips (reopenings / unchanged OTR).
    bydate = {}
    for eff, iss, c in sorted(ev, key=lambda x: (x[0], x[1])):
        bydate[eff] = c
    out = []
    for eff in sorted(bydate):
        if not out or out[-1][1] != bydate[eff]:
            out.append((eff, bydate[eff]))
    return out


def leg_series(leg, tenor, cpi, gc):
    """DV01-normalized bp return for one leg/tenor, spliced across the issue-date-gated roll
    schedule (roll_schedule). The DV01 denominator is reset to the held bond's DV01 at the
    start of each epoch -- an epoch boundary is either a calendar-month start (the monthly
    100k-DV01 rebalance) or a roll (CUSIP change) -- and held constant within the epoch.
    Returns a DataFrame indexed by date with the full per-day detail (bp, fin_sens, etc.)."""
    import bisect
    events = roll_schedule(leg, tenor)
    if not events:
        return pd.DataFrame()
    eff = [e[0] for e in events]; ecus = [e[1] for e in events]
    packs = {}
    for c in set(ecus):
        p = _bond_pack(c, leg, cpi)
        if p is not None:
            packs[c] = p
    if not packs:
        return pd.DataFrame()
    all_days = sorted(set().union(*[set(p["m"].index) for p in packs.values()]))
    start = eff[0]
    rows = {}
    cur_c = cur_mo = denom = None
    prev_emit_c = None                                     # last cusip actually emitted (roll flag)
    for t in all_days:
        if t < start:
            continue
        k = bisect.bisect_right(eff, t) - 1                # active cusip = latest roll <= t
        if k < 0:
            continue
        c = ecus[k]
        if c not in packs:
            continue
        m = packs[c]["m"]
        if t not in m.index:
            continue
        iloc = m.index.get_loc(t)
        mo = t.to_period("M")
        if c != cur_c or mo != cur_mo:                     # new denom epoch: roll or month rebalance
            before = m.index[m.index < t]
            d0 = m["dv01_per100"].loc[before[-1]] if len(before) else m["dv01_per100"].iat[iloc]
            denom = d0 if (np.isfinite(d0) and d0 != 0) else m["dv01_per100"].iat[iloc]
            cur_c, cur_mo = c, mo
        if iloc == 0 or not np.isfinite(denom) or denom == 0:
            continue
        rt, rp = m.iloc[iloc], m.iloc[iloc - 1]
        Vt, Vp = rt["V"], rp["V"]
        if not np.isfinite(Vt) or not np.isfinite(Vp):
            continue
        # settlement-date-driven span: d(t) = settle(t) - settle(t-1). ΔV already spans this
        # (V is marked to settle), so the weekend's 3-day accrual lands on FRIDAY (whose settle
        # jumps to Monday); repo must use the SAME d on the SAME day.
        st, sp = pd.Timestamp(rt["settle"]), pd.Timestamp(rp["settle"])
        d = (st - sp).days
        dV = Vt - Vp
        g = gc.asof(t)
        # repo accrues on the cash borrowed at the START of the span (the prior settle value
        # V_prev), over d days. (The spec's literal V(t) over-charges a growing position by
        # ~ d*rate*dV/360 -> a systematic drift; V_prev is the standard financed-return base
        # and keeps the weekend re-dating weekly-invariant.)
        fin = d / 360.0 * (g / 100.0) * Vp if np.isfinite(g) else 0.0
        cpn = 0.0
        for pdte in packs[c]["pay"]:
            if sp < pdte <= st:                            # coupon paid within the settle span
                irc = float(rt["IR"]) if leg == "tips" else 1.0
                cpn += (packs[c]["coupon"] / 2.0) * irc
        pnl = dV + cpn - fin
        bp = pnl / denom
        fin_sens = (d / 360.0 * Vp / 10000.0) / denom
        is_roll = prev_emit_c is not None and c != prev_emit_c
        prev_emit_c = c
        rows[t] = {"cusip": c, "settle": st, "clean": rt["clean"], "yield": rt["ytm"],
                   "accrued": rt["accrued"], "IR": rt["IR"], "dirty_real": rt["dirty_real"],
                   "V": Vt, "V_prev": Vp, "DV01": rt["dv01_per100"], "denom": denom,
                   "notional": 1.0e7 / denom,             # face giving 100k DV01 (held all month)
                   "dV": dV, "coupon": cpn, "days": d, "gc": g, "financing": fin, "pnl": pnl,
                   "gross_bp": (dV + cpn) / denom,         # price+coupon return, before financing
                   "fin_bp": fin / denom,                  # financing drag (bp), GC mid
                   "bp": bp, "fin_sens": fin_sens, "is_roll": is_roll, "is_coupon": cpn != 0.0}
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
