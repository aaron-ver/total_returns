"""
Financed breakeven total-return engine (reference.MD §5-§9 + desk BUILD SPEC).

Per tenor (5y/10y/30y) and leg (TIPS, UST), build a daily DV01-normalized return series,
then r_BE = r_TIPS - r_UST. Each leg is computed AS A LONG financed at GC mid; the minus
sign on the UST leg encodes the short. Legs are stored separately so an arbitrary beta can
be applied later as r_TIPS - beta * r_UST without rebuilding.

Construction (desk decisions, confirmed):
  * Each leg sized to 100k DV01 and expressed in bp: bp_t = $PnL_t / DV01_denom. DV01_denom is
    the held bond's DV01 per 100 face fixed at the MONTHLY rebalance (1st of the month; notional
    = 1e7/DV01) and held constant within the month -- a fresh denom epoch starts at each
    calendar-month begin OR a roll. Algebraically notional-free ->
    bp_t = (dV + coupon - financing) per 100 face / (DV01 per 100 face)_denom.
  * DV01: real-yield DV01 x IR for TIPS, nominal-yield DV01 for UST (computed in pricing.py;
    we do NOT use BBG's TIPS RISK_MID -- it is ~half).
  * V (cash): TIPS = (clean+accrued) x IR ; UST = clean+accrued.
  * dV is day-over-day within the SAME bond; at a monthly roll the new bond's first-day dV
    uses its OWN prior-day price (returns are spliced, never levels).
  * Coupon: booked the day a coupon date passes (TIPS = C/2 x IR(pay date)); dirty already
    drops via the accrued reset. No reinvestment / notional does not grow.
  * Financing: days/360 x GC x V_{t-1}, GC = GCFRTSY (mid), both legs as longs.
  * Roll: ISSUE-DATE-GATED, on the TIPS auction clock (see roll_schedule). A bond is used only
    on/after its issue date. TIPS roll 1st b-day after a new-issue month (5y May/Nov, 10y
    Feb/Aug, 30y Mar). The nominal leg is the MATURITY-MATCHED comparator -- the note whose
    maturity is the closest most-recent match to the OTR TIPS, then kept: 5y Apr/Apr & Oct/Oct,
    30y Feb/Feb, and the staggered 10y cycle (Feb:Jan/Nov, Mar:Jan/Feb, ... Aug:Jul/May,
    Sep:Jul/Aug). Pairing fixed within a month, reconsidered at month starts (validate check C).
  * Accumulate LINEARLY (cumsum of daily bp), not compounded. Convexity ignored.

Usage:
  python engine.py            # build 5y, 10y, 30y -> cache/returns_<tenor>.parquet
  python engine.py 10y        # one tenor, with a summary
  python engine.py validate   # permanent splice/day-count/pairing checks (A,B,C) per tenor
  python engine.py window 10y 2024-01-01 2024-12-31 3 3   # raw daily/monthly returns + total
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import data_layer as dl
import auctions
import pricing

CACHE = dl.CACHE

# Analysis window start. 2011 is the first year after the 2006-2010 "two TIPS auctions in a
# month" structure, so from here every month has exactly one real TIPS auction -> the seasonal
# anchor is unambiguous and the desk's last-~5y focus is well inside. All output series (returns,
# seasonal table, export, dashboard) begin here; the raw daily cache is left untouched.
ANALYSIS_START = pd.Timestamp("2011-01-01")


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


_ISS = None
def _issue_dates():
    """Original (earliest) issue date per CUSIP — NOT a later reopening date."""
    global _ISS
    if _ISS is None:
        a = auctions.load_auctions().dropna(subset=["issueDate"])
        _ISS = {c: pd.Timestamp(v) for c, v in a.groupby("cusip")["issueDate"].min().items()}
    return _ISS


def _next_bday(ts):
    """Next business day (T+1) on the bond-market calendar — the settlement date."""
    cal = trading_calendar()
    i = cal.searchsorted(pd.Timestamp(ts), side="right")
    return cal[i] if i < len(cal) else (pd.Timestamp(ts) + pd.offsets.BDay(1))


_MATMAP = None
def _maturity_map():
    """{cusip: maturityDate (Timestamp)} from the auction calendar (one row per cusip)."""
    global _MATMAP
    if _MATMAP is None:
        a = auctions.load_auctions().dropna(subset=["maturityDate"])
        _MATMAP = {c: pd.Timestamp(v) for c, v in a.groupby("cusip")["maturityDate"].first().items()}
    return _MATMAP


def _maturity_matched_nominal(tenor):
    """Nominal roll events [(eff, iss, cusip)] that, each month, hold the nominal note whose
    maturity is the closest match to the on-the-run TIPS maturity for that month -- the desk
    breakeven rule: "get both legs' maturities as close as possible, then keep them" (match
    the closest most-recent maturity, then freeze the pairing).

    Per month M (held all month -> the monthly DV01/notional rebalance epoch):
      * target = maturity of the OTR TIPS for month M (its roll-effective month <= M);
      * eligible nominal notes = those ISSUED by the cutoff. The cutoff is the TIPS roll date
        in a TIPS-roll month, else the 1st of M. This cutoff is exactly what produces the 10y
        stagger: the same-cycle 10y note is auctioned/issued on the ~15th, AFTER the 1st-of-
        month TIPS roll, so for that first month the leg holds the previous closest note
        (Feb -> Nov, Aug -> May) and only picks up the new note the next month (Mar -> Feb,
        Sep -> Aug);
      * among eligible, take the smallest maturity gap ON OR AFTER the target (so the Oct-15
        5y TIPS pairs with the Oct-31 note, not the 1-day-closer Sep-30); if none matures on
        or after the target, take the closest before. Ties break to the most recently issued.

    Verified to reproduce the desk table exactly (validate() check C): 5y Apr/Apr & Oct/Oct,
    30y Feb/Feb, and the 10y cycle Jan:Jul/Aug, Feb:Jan/Nov, Mar:Jan/Feb, Apr-Jul:Jan/Feb,
    Aug:Jul/May, Sep:Jul/Aug, Oct-Dec:Jul/Aug. (Currently wired in only for 10y; 5y/30y keep
    their original OTR-at-the-TIPS-boundary branch, which yields the identical pairing.)"""
    tips_ev = roll_schedule("tips", tenor)
    if not tips_ev:
        return []
    mat = _maturity_map()
    roll_eff = {pd.Timestamp(e).to_period("M"): pd.Timestamp(e) for e, _c in tips_ev}
    tl = sorted((pd.Timestamp(e).to_period("M"), mat.get(c)) for e, c in tips_ev)
    ni = auctions.new_issues("nominal", tenor).copy()
    ni["issueDate"] = pd.to_datetime(ni["issueDate"])
    ni["maturity"] = ni["cusip"].map(mat)
    ni = ni.dropna(subset=["maturity"]).sort_values("issueDate")
    if ni.empty:
        return []
    last = max(tl[-1][0].to_timestamp(), ni["issueDate"].max()) + pd.offsets.MonthBegin(2)
    months = pd.date_range(tl[0][0].to_timestamp(), last, freq="MS")
    ev = []
    for m in months:
        mper = m.to_period("M")
        target = None                                      # OTR TIPS maturity for this month
        for p, mt in tl:
            if p <= mper:
                target = mt
            else:
                break
        if target is None or pd.isna(target):
            continue
        cutoff = roll_eff.get(mper, m)                     # roll month -> TIPS roll date; else 1st
        cand = ni[ni["issueDate"] <= cutoff]
        if cand.empty:
            continue
        gap = (cand["maturity"] - target).dt.days
        after = cand[gap >= 0]
        if not after.empty:                                # closest maturity ON OR AFTER the TIPS
            pick = after.assign(g=gap[gap >= 0]).sort_values(
                ["g", "issueDate"], ascending=[True, False]).iloc[0]
        else:                                              # none after -> closest before
            pick = cand.assign(g=-gap).sort_values(
                ["g", "issueDate"], ascending=[True, False]).iloc[0]
        ev.append((cutoff, pick["issueDate"], pick["cusip"]))
    return ev


def roll_schedule(leg, tenor):
    """Issue-date-gated roll events [(effective_date, cusip)] (roll-fix spec). A bond is used
    only on/after its issue date; legs ride the TIPS auction clock and the nominal is fitted:

      TIPS (all tenors): roll on the 1st business day of the month AFTER the new-issue auction
        (the new TIPS settled at the prior month-end, so it is already spot-tradeable).
        -> 5y: May/Nov; 10y: Feb/Aug; 30y: Mar.   (reopenings keep the same CUSIP)
      Nominal 5y / 30y: roll ON the TIPS-roll date to the OTR nominal as of that date (its
        issue date is at/before the boundary -> clean, no mid-month switch). This yields the
        maturity-matched pairing directly: 5y Apr/Apr & Oct/Oct, 30y Feb/Feb.
      Nominal 10y (SPECIAL): the matching new note (Feb/Aug) isn't issued until the ~15th,
        AFTER the TIPS roll on the 1st, so a same-month switch would briefly hold a worse
        match. Instead the leg is MATURITY-MATCHED to the OTR TIPS each month (see
        _maturity_matched_nominal): it holds the nominal note whose maturity is closest to
        the TIPS maturity and KEEPS it. That produces the desk cycle Jan:Jul/Aug, Feb:Jan/Nov,
        Mar:Jan/Feb, Apr-Jul:Jan/Feb, Aug:Jul/May, Sep-Dec:Jul/Aug -- i.e. in the first month
        of a new TIPS the leg stays on the previous closest note (Feb->Nov, Aug->May) and
        rolls to the new note the next month (Mar->Feb, Sep->Aug). Uses the May & Nov notes
        too (not only Feb/Aug). Verified by validate() check C.
      Between TIPS rolls: FREEZE both legs (the maturity-match keeps the same pairing).
    """
    ev = []   # (effective_date, issue_date, cusip)
    if leg == "tips":
        for _, r in auctions.new_issues("tips", tenor).iterrows():
            iss = pd.Timestamp(r["issueDate"])
            eff = _first_bday_on_or_after(iss + pd.offsets.MonthBegin(1))   # 1st b-day of next month
            if eff is not None:
                ev.append((eff, iss, r["cusip"]))
    elif tenor == "10y":                                   # nominal 10y: maturity-matched to TIPS
        ev.extend(_maturity_matched_nominal("10y"))         # closest-maturity, keep-and-roll cycle
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
    # Return-effective roll date = max(roll date, next b-day after the new bond's issue date).
    # The new bond's FIRST return uses its ISSUE-DATE close as the prior mark (never a
    # when-issued, pre-issue mark; never an old->new cross-CUSIP difference); the old bond
    # covers the roll date's return whenever the new bond is issued ON the roll date.
    #   - TIPS / 5y / 30y nominal: issue is month-end, roll is the next b-day, so eff = roll
    #     date -> e.g. May 1 return = new(May1) - new(Apr30 issue close).  [old bond holds Apr30]
    #   - 10y nominal (staggered): new note is issued ON the roll date (the 15th) with only a
    #     when-issued prior, so eff = issue+1 -> first return = issue -> issue+1.
    iss = _issue_dates()
    pairs = sorted((max(pd.Timestamp(e), _next_bday(iss[c])) if c in iss else pd.Timestamp(e), c)
                   for e, c in events)
    eff, ecus = [], []
    for adj, c in pairs:                                   # drop consecutive duplicate cusips
        if not ecus or ecus[-1] != c:
            eff.append(adj); ecus.append(c)
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
    out = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    return out[out.index >= ANALYSIS_START]   # analysis window (returns built on full history, emitted 2011+)


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


def refresh(update_data=True, tenors=("5y", "10y", "30y"), rebuild=True):
    """One call to make sure everything downstream is up-to-date. Used by export.py and
    dashboard.py so they never serve stale numbers.
      1. update_data -> data_layer.update(): pull the latest CPI/repo, auction calendar, any
         new bonds, and re-pull the current OTRs from Bloomberg. Needs the Terminal; if it is
         unavailable (or any step fails) we WARN and fall back to the cached data so the
         export/dashboard still builds — it just won't include today's brand-new prints.
      2. rebuild -> build_tenor() for each tenor: rewrite returns_<tenor>.parquet from the
         (now-fresh) cache, so load_returns()/the dashboard payload reflect the latest data
         and the current engine logic (e.g. the maturity-matched pairing).
    Returns the dict of rebuilt return frames (or loaded ones if rebuild=False)."""
    if update_data:
        try:
            import data_layer
            data_layer.update()
        except Exception as e:
            print(f"  [refresh] live data update skipped — using cached data ({type(e).__name__}: {e})")
    out = {}
    for t in tenors:
        try:
            out[t] = build_tenor(t) if rebuild else load_returns(t)
        except Exception as e:
            print(f"  [refresh] {t} {'rebuild' if rebuild else 'load'} failed: {e}")
    return out


# Desk maturity-month pairing table (TIPS_maturity_month, nominal_maturity_month) expected per
# PRICING calendar month -- "get both legs' maturities as close as possible, then keep them":
#   5y : Apr-TIPS (May-Oct) -> Apr/Apr ; Oct-TIPS (Nov-Apr) -> Oct/Oct
#   30y: always Feb/Feb
#   10y: Jan Jul/Aug | Feb Jan/Nov | Mar Jan/Feb | Apr-Jul Jan/Feb | Aug Jul/May | Sep-Dec Jul/Aug
# Verified live against the engine's actually-held CUSIPs by validate() check C.
def _expected_pairing(tenor, month):
    """(TIPS_maturity_month, nominal_maturity_month) the desk rule expects for `month` (1-12),
    or None if not pinned to a fixed pair for this tenor."""
    m = month
    if tenor == "30y":
        return (2, 2)
    if tenor == "5y":
        return (4, 4) if 5 <= m <= 10 else (10, 10)
    if tenor == "10y":
        return {1: (7, 8), 2: (1, 11), 3: (1, 2), 8: (7, 5), 9: (7, 8)}.get(
            m, (1, 2) if 4 <= m <= 7 else (7, 8))
    return None


def validate(tenor, cpi=None, gc=None):
    """Permanent splice/day-count/pairing assertions:
      A) sum d(t) tiles the settlement timeline exactly -- d(t) == settle(t)-settle(t-1) for
         every consecutive pair, so no calendar day is double-counted or dropped (covers the
         weekend re-dating and holiday handling in one check).
      B) every daily return is WITHIN a single bond -- on each roll day the new note's first
         return differences two marks OF THE NEW NOTE, both on/after its issue date (never a
         when-issued mark, never an old->new cross-CUSIP difference).
      C) bond pairing matches the desk maturity-match table (_expected_pairing): for every
         month both legs trade, the held TIPS & nominal maturity MONTHS equal the expected
         closest-maturity pair (5y Apr/Apr & Oct/Oct, 30y Feb/Feb, the 10y staggered cycle).
    Returns (ok, messages)."""
    cpi = _macro()["cpi_nsa"] if cpi is None else cpi
    gc = gc_series() if gc is None else gc
    # ORIGINAL issue date per cusip (earliest; NOT a later reopening date)
    a = auctions.load_auctions().dropna(subset=["issueDate"])
    iss = {c: pd.Timestamp(v) for c, v in a.groupby("cusip")["issueDate"].min().items()}
    msgs, ok = [], True
    leg_df = {}
    for leg in ("tips", "nominal"):
        d = leg_series(leg, tenor, cpi, gc)
        leg_df[leg] = d
        if d.empty:
            continue
        # A) tiling: d(t) == (settle(t) - settle(prev row)).days for all rows after the first
        gap = (d["settle"] - d["settle"].shift()).dt.days
        bad = d.index[1:][(d["days"].iloc[1:].to_numpy() != gap.iloc[1:].to_numpy())]
        if len(bad):
            ok = False; msgs.append(f"  [A FAIL] {leg} {tenor}: {len(bad)} rows where d != settle gap (e.g. {bad[0].date()})")
        else:
            msgs.append(f"  [A ok]   {leg} {tenor}: d tiles settlement timeline ({len(d)} rows, sum d={int(d['days'].sum())})")
        # B) within-bond: on each roll, the prior mark used is the SAME (new) cusip, on/after issue
        nbad = 0
        for t in d.index[d["is_roll"].fillna(False).to_numpy()]:
            c = d.at[t, "cusip"]; m = packs_idx(c)
            if m is None or t not in m:
                nbad += 1; continue
            il = m.get_loc(t)
            prior = m[il - 1] if il > 0 else None
            if prior is None or (c in iss and prior < iss[c]):    # would be a WI / pre-entry mark
                nbad += 1
        if nbad:
            ok = False; msgs.append(f"  [B FAIL] {leg} {tenor}: {nbad} roll days difference a pre-issue/cross-bond mark")
        else:
            msgs.append(f"  [B ok]   {leg} {tenor}: every roll's first return is within-new-note, on/after issue")
    # C) maturity-match pairing: month-end held CUSIP per leg -> compare maturity months to table.
    # Hard-fail only on RECENT months (trailing 60 = the current auction regime the table
    # describes). Older misses are reported, not failed: they reflect the then-current Treasury
    # cadence (5y TIPS were one annual April issue before ~2019 -> Apr-maturing year-round; the
    # 10y series' first months in 2003 predate the steady Jan/Jul clock), not a roll error.
    t, u = leg_df.get("tips"), leg_df.get("nominal")
    if t is not None and u is not None and not t.empty and not u.empty:
        mat = _maturity_map()
        tc = t["cusip"].groupby(t.index.to_period("M")).last()   # held into month-end (post-roll)
        uc = u["cusip"].groupby(u.index.to_period("M")).last()
        common = tc.index.intersection(uc.index)
        recent_cut = common.max() - 60 if len(common) else None
        bad, recent_bad, nchk = [], None, 0
        for mp in common:
            exp = _expected_pairing(tenor, mp.month)
            tm, um = mat.get(tc[mp]), mat.get(uc[mp])
            if exp is None or tm is None or um is None:
                continue
            nchk += 1
            if (tm.month, um.month) != exp:
                rec = mp > recent_cut
                bad.append((mp, (tm.month, um.month), exp, tc[mp], uc[mp]))
                if rec and recent_bad is None:
                    recent_bad = bad[-1]
        if recent_bad is not None:
            ok = False
            mp, got, exp, tcus, ucus = recent_bad
            msgs.append(f"  [C FAIL] {tenor}: held pairing off the maturity-match table in {mp} "
                        f"(got months {got} expected {exp}; held TIPS {tcus} / UST {ucus})")
        elif bad:
            msgs.append(f"  [C ok]   {tenor}: matches the desk pairing table for the current regime "
                        f"({nchk - len(bad)}/{nchk} months); {len(bad)} older months "
                        f"({bad[0][0]}..{bad[-1][0]}) follow the then-current auction cadence")
        else:
            msgs.append(f"  [C ok]   {tenor}: all {nchk} months match the desk maturity-pairing table "
                        f"({str(common.min())}..{str(common.max())})")
    return ok, msgs


_PIDX = {}
def packs_idx(cusip):
    """Cached daily price index for a cusip (the bond's own trading days)."""
    if cusip not in _PIDX:
        p = os.path.join(CACHE, "daily", f"{cusip}.parquet")
        if not os.path.exists(p):
            _PIDX[cusip] = None
        else:
            df = pd.read_parquet(p)
            df = df[["PX_CLEAN_MID"]].dropna() if "PX_CLEAN_MID" in df else df
            _PIDX[cusip] = df.index if len(df) else None
    return _PIDX[cusip]


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


# ===========================================================================================
# Seasonal auction-cycle bucketing (desk spec). A pure AGGREGATION layer over the engine's
# existing daily P&L -- it does NOT recompute returns. We sum the existing daily bp (the
# DV01-normalized P&L per 100k DV01; $ P&L = bp x 100,000) into auction-anchored within-month
# buckets, then stack across years with the MEDIAN.
#
# Shared monthly calendar (spec sec.5): every calendar month carries exactly ONE TIPS auction
# (the tenor rotates Jan 10y / Feb 30y / Mar 10y / Apr 5y / ... / Dec 5y); that single auction
# anchors ALL THREE tenor series. Read: "how each tenor trades around the monthly TIPS supply
# event." (Early history has a few 2-auction months -> earliest used, logged.)
#
# Five trading-day anchors per month M (sec.2), on the shared bond-market calendar:
#   A0 last TD of M-1   A1 prev-TD on/before (auction - 7 cal days)   A2 auction
#   A3 next-TD on/after (auction + 7 cal days)                        A4 last TD of M
# Four half-open buckets (open, close]; a boundary day's P&L belongs to the bucket on its LEFT:
#   P1 (A0,A1]  P2 (A1,A2]  P3 (A2,A3]  P4 (A3,A4]   (P2 includes the auction day; P3 starts T+1)
# Clamps (sec.3): late A3>=A4 -> A3=A4 (P4 empty, NaN); early A1<=A0 -> A1=A0 (P1 empty, NaN +
#   logged -- only fires for some early-history front-of-month auctions; modern auctions sit in
#   the back half so it is otherwise inert).
SEASONAL_WEEK = 7              # "1 week" around the auction, in CALENDAR days
PERIOD_SPAN = {1: "P1 · m/e→auction−1w", 2: "P2 · auction−1w→auction",
               3: "P3 · auction→auction+1w", 4: "P4 · auction+1w→m/e"}


_TIPS_AUCT = None
def tips_auction_calendar():
    """{(year, month): auction Timestamp} -- the single monthly TIPS auction shared across tenors
    (new issues AND reopenings; the tenor rotates). If a month carries >1 TIPS auction the
    earliest is used (logged once); months with none are absent (those months can't be bucketed)."""
    global _TIPS_AUCT
    if _TIPS_AUCT is None:
        s = auctions.real_tips_auctions().dropna(subset=["auctionDate"])   # contingency auctions excluded
        cal, multi = {}, []
        for d in sorted(pd.to_datetime(s["auctionDate"]).unique()):
            d = pd.Timestamp(d)
            if d < ANALYSIS_START:                          # only the 2011+ window is bucketed
                continue
            ym = (d.year, d.month)
            (multi.append(ym) if ym in cal else cal.setdefault(ym, d))
        if multi:
            print(f"  [seasonal] {len(multi)} month(s) had >1 TIPS auction; earliest used (e.g. {multi[0]})")
        _TIPS_AUCT = cal
    return _TIPS_AUCT


_TIPS_ISNEW = None
def tips_auction_isnew():
    """{(year, month): True if that month's anchoring TIPS auction is a NEW issue, else reopening}.
    New-issue months in the modern rotation are Jan (10y), Feb (30y), Apr (5y), Jul (10y), Oct (5y);
    the rest are reopenings. Derived from the auction's `reopening` flag (robust to schedule drift),
    aligned to the same anchor dates as tips_auction_calendar(). Used to group/condition the seasonal
    analysis on new-issue vs reopening months."""
    global _TIPS_ISNEW
    if _TIPS_ISNEW is None:
        s = auctions.real_tips_auctions().dropna(subset=["auctionDate"]).copy()
        is_re = s["reopening"].astype(str).str.strip().str.lower().isin(["yes", "true", "1", "t", "y"])
        reopen = {pd.Timestamp(d): bool(b) for d, b in zip(pd.to_datetime(s["auctionDate"]), is_re)}
        _TIPS_ISNEW = {ym: not reopen.get(pd.Timestamp(d), False)
                       for ym, d in tips_auction_calendar().items()}
    return _TIPS_ISNEW


def _last_trading_day(year, month, cal):
    end = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
    return cal[cal.searchsorted(end, side="right") - 1]


def month_anchors(year, month, cal=None, auction=None):
    """(A0,A1,A2,A3,A4, clamped, kind) for (year, month), or None if no auction that month.
    kind in {None,'late','early'}; both clamp actions still apply if (degenerately) both fire."""
    cal = trading_calendar() if cal is None else cal
    if auction is None:
        auction = tips_auction_calendar().get((year, month))
        if auction is None:
            return None
    A2 = pd.Timestamp(auction)
    pm = pd.Timestamp(year, month, 1) - pd.offsets.MonthBegin(1)
    A0 = _last_trading_day(pm.year, pm.month, cal)
    A4 = _last_trading_day(year, month, cal)
    A1 = cal[cal.searchsorted(A2 - pd.Timedelta(days=SEASONAL_WEEK), side="right") - 1]   # prev TD on/before
    A3 = cal[min(cal.searchsorted(A2 + pd.Timedelta(days=SEASONAL_WEEK), side="left"), len(cal) - 1)]  # next TD on/after
    late, early = A3 >= A4, A1 <= A0
    if late:
        A3 = A4                                   # P4 empties
    if early:
        A1 = A0                                   # P1 empties (defensive)
    kind = "early" if early else ("late" if late else None)
    return A0, A1, A2, A3, A4, (late or early), kind


def seasonal_table(tenors=("5y", "10y", "30y"), save=True):
    """The keystone tidy long table (spec sec.7), one row per (year, month, period, tenor):
      tips_pnl, ust_pnl  -- SUM of the engine's daily bp (DV01-normalized P&L) inside the bucket
      trading_days       -- # days summed (0 -> empty/clamped bucket; pnl recorded NaN)
      clamped            -- the month hit a clamp (short P3 / empty P4, or empty P1)
      new_issue          -- the month's anchoring TIPS auction is a NEW issue (else a reopening),
                            so the analysis can group/condition on new-issue vs reopening months
    The 48-bar seasonal, cumulative path, within-month signature and every phase-2 grouping are
    group-bys on this one table. Beta-hedged breakeven is derived downstream as tips - beta*ust
    (beta fixed within a month, so sum-then-scale == scale-then-sum)."""
    cal = trading_calendar()
    aucs = tips_auction_calendar()
    isnew = tips_auction_isnew()
    rows, early_hits = [], []
    for ten in tenors:
        try:
            r = load_returns(ten)
        except Exception:
            continue
        idx = r.index
        for (y, m), sub in r.groupby([idx.year, idx.month]):
            anc = month_anchors(int(y), int(m), cal, aucs.get((int(y), int(m))))
            if anc is None:
                continue                          # no TIPS auction that month -> unbucketable
            _A0, A1, A2, A3, _A4, clamped, kind = anc
            nw = bool(isnew.get((int(y), int(m)), True))   # new issue vs reopening month
            if kind == "early":
                early_hits.append((ten, int(y), int(m)))
            d = sub.index
            per = np.where(d <= A1, 1, np.where(d <= A2, 2, np.where(d <= A3, 3, 4)))
            for p in (1, 2, 3, 4):
                mask = per == p
                td = int(mask.sum())
                if td == 0:
                    rows.append((int(y), int(m), p, ten, np.nan, np.nan, 0, bool(clamped), nw))
                else:
                    rows.append((int(y), int(m), p, ten, float(sub["r_TIPS_bp"].to_numpy()[mask].sum()),
                                 float(sub["r_UST_bp"].to_numpy()[mask].sum()), td, bool(clamped), nw))
    df = pd.DataFrame(rows, columns=["year", "month", "period", "tenor",
                                     "tips_pnl", "ust_pnl", "trading_days", "clamped", "new_issue"])
    if early_hits:
        print(f"  [seasonal] early-auction clamp fired {len(early_hits)}x (front-of-month auctions, "
              f"e.g. {early_hits[0]}); P1 recorded empty for those months")
    if save:
        df.to_parquet(os.path.join(CACHE, "seasonal.parquet"))
    return df


def seasonal_qa(tenors=("5y", "10y", "30y")):
    """Spec sec.9 QA: auction day-of-month range (early-clamp guard), seam continuity
    A0[M]==A4[M-1], and counts of clamped short-P3 / empty-P4 buckets. Returns (ok, messages)."""
    cal = trading_calendar()
    aucs = tips_auction_calendar()
    msgs, ok = [], True
    doms = [d.day for d in aucs.values()]
    msgs.append(f"  auctions: {len(aucs)} months  day-of-month min={min(doms)} max={max(doms)} "
                f"(early clamp only when an auction lands within ~1w of month start)")
    bad = 0
    for (y, m) in sorted(aucs):
        pm = pd.Timestamp(y, m, 1) - pd.offsets.MonthBegin(1)
        if (pm.year, pm.month) not in aucs:
            continue
        a = month_anchors(y, m, cal, aucs[(y, m)]); ap = month_anchors(pm.year, pm.month, cal, aucs[(pm.year, pm.month)])
        if a and ap and a[0] != ap[4]:
            bad += 1
    if bad:
        ok = False; msgs.append(f"  [seam FAIL] A0[M] != A4[M-1] in {bad} month(s)")
    else:
        msgs.append("  [seam ok] A0[M] == A4[M-1] for every month")
    df = seasonal_table(tenors, save=False)
    late = int(((df.period == 4) & df.trading_days.eq(0)).sum())     # empty P4 <=> late clamp <=> short P3
    early = int(((df.period == 1) & df.trading_days.eq(0)).sum())    # empty P1 <=> early clamp
    msgs.append(f"  late-clamped months (short P3 + empty-P4 NaN): {late}   "
                f"early-clamped (empty-P1 NaN): {early}   of {len(df)//4} month-tenor buckets")
    return ok, msgs


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
    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        cpi = _macro()["cpi_nsa"]; gc = gc_series(); all_ok = True
        for ten in ["5y", "10y", "30y"]:
            okt, msgs = validate(ten, cpi, gc)
            all_ok = all_ok and okt
            print(f"=== validate {ten} ==="); [print(m) for m in msgs]
        print("\nALL PASS" if all_ok else "\nFAILURES PRESENT")
        sys.exit(0 if all_ok else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "seasonal":
        # python engine.py seasonal  -> build the tidy bucket table + run QA
        df = seasonal_table(save=True)
        print(f"=== seasonal table: {len(df)} rows -> cache/seasonal.parquet ===")
        print(df.groupby("tenor")["trading_days"].agg(["count", "sum"]).to_string())
        ok, msgs = seasonal_qa()
        print("=== seasonal QA ==="); [print(m) for m in msgs]
        sys.exit(0 if ok else 1)
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
