"""
Energy hedge series (RBOB gasoline futures) for the breakeven build.

Pulls the two front generic RBOB gasoline futures from Bloomberg -- XB1 (generic 1st)
and XB2 (generic 2nd) -- and builds ONE continuous front-month PRICE series and ONE
daily RETURN series in bp, handling the month-end generic roll the same way the bond
engine splices a roll.

The roll (desk note, verified on the tape)
------------------------------------------
RBOB futures expire on the last business day of the month BEFORE the delivery month, so
the generic front (XB1) rolls into the next contract on the FIRST trading day of each
month. The contract that was the 2nd generic (XB2) yesterday becomes the new front (XB1)
today. To avoid booking the roll spread as a return, the roll-day return differences the
SAME underlying contract across the roll:

    roll day (1st trading day of a month):  r = XB1(t) - XB2(t-1)
    every other day:                        r = XB1(t) - XB1(t-1)

This mirrors engine.leg_series's splice ("the new bond's first return uses its OWN
prior-day price, never an old->new cross-contract difference"). XB2(t-1) IS the new
front's own close on the prior day (it was the 2nd generic then), so XB1(t) - XB2(t-1)
is a clean single-contract day-over-day return. (Note: the desk's first phrasing was
"XB2(t) - XB1(t-1)"; that differences two different contracts -- this is the corrected,
bond-consistent direction.)

Calendar alignment (desk ask)
-----------------------------
Returns are built on the INTERSECTION of gas trading days and the bond business-day
calendar (engine.trading_calendar() -- "the bond reference"). Where one side is missing a
day, that day is skipped and the return spans the gap: if gas trades 1,2,3 but bonds only
1,3, the gas return runs 1->3 (a 2-day return), and vice-versa. This is the same
multi-day-step convention the bond engine uses for weekends/holidays, so the gas series
lines up day-for-day with returns_<tenor>.parquet for hedging.

Units / return convention
--------------------------
RBOB is quoted in US cents per gallon (Bloomberg QUOTE_UNITS = 'USd/gal.'), so a price
move of 1.0 = 1 cent/gallon, worth FUT_VAL_PT = $420 on one 42,000-gallon contract. The
PRIMARY hedge input is the daily $ change per contract (the desk wants $, not %, so the
hedge ratio comes out in number of contracts):

    chg              = XB1(t) - prior_mark                 (price points = cents/gallon)
    usd_per_contract = chg * 420                           (daily $ P&L on ONE contract)
    r_bp             = chg / prior_mark * 1e4              (percentage return in bp; kept for ref)
    cum_bp           = running LINEAR sum of r_bp           (not compounded -- matches the engine)

`usd_per_contract` is what hedge.py regresses the breakeven $ P&L on -> the regression
slope is directly the number of gasoline contracts (see hedge.py). No financing/carry
term: a same-contract futures return already excludes the roll yield (the curve carry
shows up as the XB1-XB2 spread, which the splice deliberately steps over).

Output series begins at engine.ANALYSIS_START (2011-01-01) to align with the bond exports;
the raw cache keeps the full history Bloomberg returns.

Usage
-----
  python energy.py pull       # pull XB1/XB2 daily closes -> cache/energy_raw.parquet
  python energy.py build      # build the spliced price+return series -> cache/energy.parquet
  python energy.py refresh    # pull then build (add --no-update to build from cache as-is)
  python energy.py preview    # eyeball recent rows + every roll day (spliced vs naive)
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import bbg
import engine

CACHE = engine.CACHE
START = "20030101"                                  # XB history actually begins ~2005-2006
TODAY = pd.Timestamp.today().strftime("%Y%m%d")     # dynamic so re-runs fetch the latest day

# Generic RBOB gasoline futures. XB1 = front, XB2 = second. PX_LAST = daily settlement.
ENERGY = {
    "XB1": ("XB1 Comdty", "PX_LAST"),   # generic 1st RBOB future (front month held)
    "XB2": ("XB2 Comdty", "PX_LAST"),   # generic 2nd RBOB future (becomes the front next month)
}
RAW = os.path.join(CACHE, "energy_raw.parquet")     # raw XB1/XB2 closes (full history)
BUILT = os.path.join(CACHE, "energy.parquet")       # spliced price + return series (2011+)

# RBOB contract economics (Bloomberg, validated 2026-06-25):
#   QUOTE_UNITS = 'USd/gal.'  -> price is in US CENTS per gallon (a 1.0 move = 1 cent/gal)
#   FUT_CONT_SIZE = 42,000 gallons ;  FUT_VAL_PT = $420  (the $ value of a 1.0 price-point move)
# So $ change per contract = (price-point change) * USD_PER_PT.
CONTRACT_GALLONS = 42000
USD_PER_PT = 420.0          # = FUT_VAL_PT = 42,000 gal * $0.01/cent ; $/contract per 1.0 (=1 cent/gal)


def pull_energy():
    """Pull XB1/XB2 daily closes from Bloomberg and cache the raw frame (outer-joined on date)."""
    os.makedirs(CACHE, exist_ok=True)
    tickers = [tic for tic, _ in ENERGY.values()]
    bbg.open_session()
    try:
        h = bbg.history(tickers, ["PX_LAST"], START, TODAY)
    finally:
        bbg.close_session()
    frames = []
    for name, (tic, fld) in ENERGY.items():
        rows = h.get(tic, [])
        if not rows:
            print(f"  WARN no data for {name} ({tic})")
            continue
        df = pd.DataFrame(rows).rename(columns={fld: name})[["date", name]]
        df["date"] = pd.to_datetime(df["date"])
        frames.append(df.set_index("date"))
        print(f"  {name:4s} {tic:12s} n={len(df)} {str(df['date'].min())[:10]}->{str(df['date'].max())[:10]}")
    if not frames:
        raise RuntimeError("no energy data returned from Bloomberg")
    raw = pd.concat(frames, axis=1).sort_index()
    raw.to_parquet(RAW)
    print(f"  wrote {RAW}  ({raw.shape[0]} rows x {raw.shape[1]} cols)")
    return raw


def _load_raw():
    if not os.path.exists(RAW):
        return None
    return pd.read_parquet(RAW).sort_index()


def build_energy(save=True):
    """Build the spliced front-month price + bp return series from the raw XB1/XB2 cache.

    One row per day in the INTERSECTION of gas trading days and the bond business-day
    calendar. Columns:
      XB1, XB2     -- raw generic closes that day (XB1 = the held front-month price)
      prior_mark   -- the contract-consistent prior close used in the return:
                      XB2(prev common day) on a roll day, else XB1(prev common day)
      days         -- calendar-day gap to the prior common day (2/3 across weekends/holidays)
      is_roll      -- first common trading day of a new month (generic front rolled)
      chg          -- XB1(t) - prior_mark   (price points; cents/gallon for RBOB)
      usd_per_contract -- chg * USD_PER_PT ($420) = daily $ P&L on ONE contract <- hedge input
      r_bp         -- chg / prior_mark * 1e4 (percentage return in bp; kept for reference)
      cum_bp       -- running linear sum of r_bp (from ANALYSIS_START)
    """
    raw = _load_raw()
    if raw is None or raw.empty or "XB1" not in raw or "XB2" not in raw:
        raise FileNotFoundError("no/again-incomplete energy_raw cache — run: python energy.py pull")
    x1 = raw["XB1"].dropna()                         # gas trading days = days with a front close
    x2 = raw["XB2"]
    bond_cal = engine.trading_calendar()             # "the bond reference" business-day calendar
    common = x1.index.intersection(bond_cal).sort_values()
    rows, prev, missing_x2 = {}, None, []
    for t in common:
        if prev is None:
            prev = t
            continue
        is_roll = t.to_period("M") != prev.to_period("M")   # 1st common day of a new month
        price_t = float(x1.loc[t])
        if is_roll:                                  # new front = yesterday's 2nd generic (XB2)
            pm = x2.get(prev, np.nan)
            if pd.isna(pm):                          # XB2 unexpectedly missing -> can't splice cleanly
                missing_x2.append(prev)
                prev = t
                continue
            prior = float(pm)
        else:
            prior = float(x1.loc[prev])              # same front contract, day-over-day
        if not np.isfinite(prior) or prior == 0 or not np.isfinite(price_t):
            prev = t
            continue
        chg = price_t - prior
        rows[t] = {
            "XB1": price_t,
            "XB2": float(x2.get(t)) if pd.notna(x2.get(t)) else np.nan,
            "prior_mark": prior,
            "days": int((t - prev).days),
            "is_roll": bool(is_roll),
            "chg": chg,
            "usd_per_contract": chg * USD_PER_PT,
            "r_bp": chg / prior * 1e4,
        }
        prev = t
    out = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    out.index.name = "date"
    out = out[out.index >= engine.ANALYSIS_START]    # align with the bond exports
    out["cum_bp"] = out["r_bp"].cumsum()
    if missing_x2:
        print(f"  [energy] {len(missing_x2)} roll day(s) skipped — XB2 missing on the prior "
              f"day (e.g. {missing_x2[0].date()})")
    if save:
        out.to_parquet(BUILT)
        print(f"  wrote {BUILT}  ({len(out)} rows, {str(out.index.min())[:10]}..{str(out.index.max())[:10]}, "
              f"{int(out['is_roll'].sum())} rolls)")
    return out


def load_energy():
    return pd.read_parquet(BUILT).sort_index()


def refresh(update_data=True):
    """Pull (if update_data) then rebuild the energy series. Mirrors engine.refresh's
    fall-back-to-cache behavior so export.py/dashboard.py never go stale or crash when the
    Terminal is down. Returns the built frame (or None on total failure)."""
    if update_data:
        try:
            pull_energy()
        except Exception as e:
            print(f"  [energy.refresh] live pull skipped — using cached data ({type(e).__name__}: {e})")
    try:
        return build_energy()
    except Exception as e:
        print(f"  [energy.refresh] build failed: {e}")
        return None


def preview(rows=20):
    """Eyeball the tail + every roll day, with the naive (cross-contract) return alongside
    the spliced one so the roll handling is visible."""
    raw = _load_raw()
    if raw is None:
        print("no energy_raw cache — run: python energy.py pull")
        return
    df = build_energy(save=False)
    with pd.option_context("display.max_rows", max(rows, 60), "display.width", 180):
        print(f"=== energy series ({len(df)} rows, {str(df.index.min())[:10]}..{str(df.index.max())[:10]}) ===")
        print(df.tail(rows).round(4).to_string())
        rolls = df[df["is_roll"]].copy()
        # naive front-only return (XB1 today vs XB1 yesterday) -> shows the roll-spread contamination
        x1 = raw["XB1"].dropna()
        naive = []
        for t in rolls.index:
            before = x1.index[x1.index < t]
            naive.append((x1.loc[t] - x1.loc[before[-1]]) / x1.loc[before[-1]] * 1e4 if len(before) else np.nan)
        rolls["naive_r_bp"] = naive
        print(f"\n=== roll days ({len(rolls)}): spliced r_bp vs naive XB1-only r_bp ===")
        print(rolls[["XB1", "XB2", "prior_mark", "days", "chg", "usd_per_contract", "r_bp", "naive_r_bp"]].tail(rows).round(4).to_string())


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if cmd == "pull":
        pull_energy()
    elif cmd == "build":
        build_energy()
    elif cmd == "refresh":
        refresh(update_data="--no-update" not in sys.argv)
    elif cmd == "preview":
        preview(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    else:
        print(__doc__)
