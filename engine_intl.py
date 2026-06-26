"""
Per-bond financed total-return engine for European/UK linkers (reference_intl.MD §0-§3,§6).

One daily DV01-normalized bp return series PER BOND (one ISIN walked end-to-end). The US engine's
spine is reused verbatim -- daily P&L = ΔV + coupon − financing, V = real_dirty × index_ratio,
bp = P&L / DV01_denom (monthly 100k-DV01 rebalance), linear cumsum, settlement-date marking --
but there is NO on-the-run roll/splice (that only existed for the US tenor *role*). What differs
per bond is read from linkers.MARKETS: inflation index, coupon frequency, settlement calendar +
lag, deflation floor, local financing curve. The DCF/DV01 (pricing.py) is reused unchanged.

Outputs cache_intl/returns/<isin>.parquet with the full per-day detail (clean, yield, accrued,
IR, V, DV01, dV, coupon, financing, bp, ...), so export_intl.py can lay one sheet per bond.

Usage:
  python engine_intl.py                      # build every active bond -> cache_intl/returns/
  python engine_intl.py FR0010135525         # one bond, with a summary
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import linkers
import data_layer_intl as dl
import pricing

CACHE = linkers.CACHE
CAL_FIN = {"TARGET2": "estr_gc", "UK": "sonia_gc"}    # calendar id -> financing series whose
#                                                       publication days define the business days


_CAL = {}
def trading_calendar(cal_id):
    """Holiday-aware business-day calendar for a settlement calendar id. Derived from the matching
    GC series' publication days (€STR publishes on TARGET2 days, SONIA on UK days) -- the same
    trick the US engine uses with fed funds -- extended ~1y forward so settle() resolves for the
    most recent observations."""
    if cal_id not in _CAL:
        fin = dl.financing_series(CAL_FIN[cal_id])
        base = pd.DatetimeIndex(sorted(fin.index))
        fwd = pd.bdate_range(base.max() + pd.Timedelta(days=1), base.max() + pd.Timedelta(days=400))
        _CAL[cal_id] = base.union(fwd)
    return _CAL[cal_id]


def _settle_array(obs_index, lag, cal):
    """Vectorized T+lag settlement date for each observation date (lag business days forward)."""
    pos = cal.searchsorted(pd.DatetimeIndex(obs_index), side="right")    # first cal day strictly after
    pos = (pos + (lag - 1)).clip(max=len(cal) - 1)
    return cal[pos]


def _static(isin):
    """(coupon, maturity, dated_date) from the cached Bloomberg static; dated = accrual start."""
    s = pd.read_parquet(os.path.join(CACHE, "static", f"{isin}.parquet")).iloc[0]
    cpn = float(s["CPN"])
    maturity = pd.Timestamp(s["MATURITY"])
    dated = s.get("INT_ACC_DT") or s.get("ISSUE_DT") or s.get("FIRST_SETTLE_DT")
    return cpn, maturity, pd.Timestamp(dated)


def bond_series(isin, market):
    """Daily financed bp return for one linker, walked end-to-end. Returns a DataFrame indexed by
    observation date with the full per-day detail, or empty if data is missing."""
    c = linkers.conv(market)
    path = os.path.join(CACHE, "daily", f"{isin}.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    d = pd.read_parquet(path)
    if not {"PX_CLEAN_MID", "YLD_YTM_MID"}.issubset(d.columns):
        return pd.DataFrame()
    df = d[["PX_CLEAN_MID", "YLD_YTM_MID"]].dropna()
    if len(df) < 2:
        return pd.DataFrame()
    coupon, maturity, dated = _static(isin)
    freq = int(c["freq"])
    cal = trading_calendar(c["calendar"])
    settle = _settle_array(df.index, int(c["settle_lag"]), cal)          # T+lag per obs (DatetimeIndex)

    # index ratio at each settlement date (rules-based, §3-§4), aligned back to the obs index
    rounding = "trunc6round5" if c["country"] == "US" else "round5"
    ir_at_settle = dl.index_ratio_series(c["index"], dated, settle, int(c["lag_months"]),
                                         bool(c["interp"]), rounding=rounding)
    ir = pd.Series(ir_at_settle.reindex(settle).to_numpy(), index=df.index)

    # BBG's own index ratio for cross-check (stored, not used to drive returns)
    bbg_ir = d["INDEX_RATIO"].reindex(df.index) if "INDEX_RATIO" in d else pd.Series(index=df.index, dtype=float)

    m = pricing.bond_metrics(df["PX_CLEAN_MID"], df["YLD_YTM_MID"], coupon, maturity, dated,
                             ir=ir, freq=freq, settle=settle)
    pays = pricing.pay_dates(maturity, dated, freq)
    gc = dl.financing_series(c["repo"])

    rows, cur_mo, denom = {}, None, None
    for iloc in range(len(m.index)):
        t = m.index[iloc]
        rt = m.iloc[iloc]
        mo = t.to_period("M")
        if mo != cur_mo:                                    # monthly 100k-DV01 rebalance epoch
            d0 = m["dv01_per100"].iloc[iloc - 1] if iloc > 0 else rt["dv01_per100"]
            denom = d0 if (np.isfinite(d0) and d0 != 0) else rt["dv01_per100"]
            cur_mo = mo
        if iloc == 0 or not np.isfinite(denom) or denom == 0:
            continue
        rp = m.iloc[iloc - 1]
        Vt, Vp = rt["V"], rp["V"]
        if not (np.isfinite(Vt) and np.isfinite(Vp)):
            continue
        st, sp = pd.Timestamp(rt["settle"]), pd.Timestamp(rp["settle"])
        dd = (st - sp).days                                 # settlement span (calendar days)
        dV = Vt - Vp
        g = gc.asof(t)
        fin = dd / 360.0 * (g / 100.0) * Vp if np.isfinite(g) else 0.0    # repo on cash carried in
        cpn = 0.0
        for pdte in pays:
            if sp < pdte <= st:                             # coupon paid within the settle span
                cpn += (coupon / freq) * float(rt["IR"])
        pnl = dV + cpn - fin
        rows[t] = {"isin": isin, "market": market, "settle": st,
                   "clean": rt["clean"], "yield": rt["ytm"], "accrued": rt["accrued"],
                   "IR": rt["IR"], "IR_bbg": float(bbg_ir.get(t)) if pd.notna(bbg_ir.get(t)) else np.nan,
                   "dirty_real": rt["dirty_real"], "V": Vt, "V_prev": Vp,
                   "DV01": rt["dv01_per100"], "denom": denom, "notional": 1.0e7 / denom,
                   "dV": dV, "coupon": cpn, "days": dd, "gc": g, "financing": fin, "pnl": pnl,
                   "gross_bp": (dV + cpn) / denom, "fin_bp": fin / denom, "bp": pnl / denom,
                   "fin_sens": (dd / 360.0 * Vp / 10000.0) / denom,
                   "is_coupon": cpn != 0.0}
    out = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    if not out.empty:
        out["cum_bp"] = out["bp"].cumsum()
    return out


def build_bond(isin, market, save=True):
    df = bond_series(isin, market)
    if save and not df.empty:
        os.makedirs(os.path.join(CACHE, "returns"), exist_ok=True)
        df.to_parquet(os.path.join(CACHE, "returns", f"{isin}.parquet"))
    return df


def build_all(include_deferred=False):
    u = linkers.load_universe(include_deferred=include_deferred)
    built, skipped = [], []
    for _, r in u.iterrows():
        try:
            df = build_bond(r["isin"], r["market"])
            (built if not df.empty else skipped).append(r["isin"])
            if not df.empty:
                print(f"  {r['isin']} {r['market']:10s} {len(df)} days  cum_bp={df['cum_bp'].iloc[-1]:+.0f}")
            else:
                print(f"  {r['isin']} {r['market']:10s} SKIP (no/short data)")
        except Exception as e:
            skipped.append(r["isin"]); print(f"  {r['isin']} {r['market']:10s} ERROR {type(e).__name__}: {e}")
    print(f"\nbuilt {len(built)} bonds, skipped {len(skipped)}")
    return built, skipped


def load_returns(isin):
    return pd.read_parquet(os.path.join(CACHE, "returns", f"{isin}.parquet"))


def refresh(update_data=True):
    """Pull latest data then rebuild every bond, so export_intl never serves stale numbers."""
    if update_data:
        try:
            dl.update()
        except Exception as e:
            print(f"  [refresh] live update skipped — using cache ({type(e).__name__}: {e})")
    return build_all()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        isin = sys.argv[1]
        u = linkers.load_universe(include_deferred=True)
        row = u[u["isin"] == isin]
        if row.empty:
            print(f"{isin} not in universe"); sys.exit(1)
        df = build_bond(isin, row.iloc[0]["market"])
        if df.empty:
            print("no data"); sys.exit(1)
        with pd.option_context("display.max_columns", 40, "display.width", 220):
            print(df.tail(15).to_string())
        print(f"\n{isin}: {len(df)} days  cum_bp={df['cum_bp'].iloc[-1]:+.1f}  "
              f"mean/day={df['bp'].mean():+.3f}  std={df['bp'].std():.3f}")
    else:
        build_all()
