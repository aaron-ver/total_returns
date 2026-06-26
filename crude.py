"""
Crude oil (WTI = CL, Brent = CO) front-month return series — for the breakeven-hedge experiment
(boss ask: same R²/beta + outlier test as gasoline, but on Brent & WTI). Standalone; NOT wired
into the dashboard or export.

Mirrors energy.py (RBOB gasoline), with two crude-specific differences:
  * Contract = 1,000 barrels, quoted USD/bbl, so a $1/bbl move = $1,000 per contract
    (Bloomberg FUT_VAL_PT = 1000).  -> usd_per_contract = chg($/bbl) * 1000.
  * The generic front rolls MID-month for WTI (~the 20th) and month-start for Brent, so the roll
    day is taken from the front's FUT_CUR_GEN_TICKER actually CHANGING (robust to any schedule),
    not assumed at the month boundary. On a roll the return differences the SAME contract across
    it: r = front(t) - second(t-1)  (yesterday's 2nd generic is today's front), validated by
    second's gen-ticker(t-1) == front's gen-ticker(t).

One spliced front-price + one daily $/contract return series per product, on the intersection of
the commodity's trading days and the bond calendar (engine.trading_calendar()) — the same
gas/bond day-alignment used in energy.py.

Usage:
  python crude.py pull            # pull CL1/CL2/CO1/CO2 prices + gen-tickers -> cache/crude_raw_*.parquet
  python crude.py build           # build spliced $/contract series -> cache/crude_*.parquet
  python crude.py preview WTI     # eyeball recent rows + roll days
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import bbg
import engine

CACHE = engine.CACHE
START = "20030101"
TODAY = pd.Timestamp.today().strftime("%Y%m%d")

# product -> generic front/second tickers + $/contract multiplier (FUT_VAL_PT, validated 2026-06).
# Crude is USD/bbl, 1,000 bbl/contract -> $1/bbl = $1,000/contract.
PRODUCTS = {
    "WTI":   dict(front="CL1 Comdty", second="CL2 Comdty", mult=1000.0, name="WTI crude (CL)"),
    "Brent": dict(front="CO1 Comdty", second="CO2 Comdty", mult=1000.0, name="Brent crude (CO)"),
}


def _raw_path(product):
    return os.path.join(CACHE, f"crude_raw_{product}.parquet")


def _built_path(product):
    return os.path.join(CACHE, f"crude_{product}.parquet")


def pull(product):
    """Pull front/second PX_LAST + FUT_CUR_GEN_TICKER (the active contract, for roll detection)."""
    os.makedirs(CACHE, exist_ok=True)
    spec = PRODUCTS[product]
    bbg.open_session()
    try:
        h = bbg.history([spec["front"], spec["second"]], ["PX_LAST", "FUT_CUR_GEN_TICKER"], START, TODAY)
    finally:
        bbg.close_session()

    def frame(sec, pxname, gename):
        rows = h.get(sec, [])
        if not rows:
            return pd.DataFrame()
        d = pd.DataFrame(rows)
        d["date"] = pd.to_datetime(d["date"])
        d = d.set_index("date").sort_index()
        out = pd.DataFrame(index=d.index)
        out[pxname] = d.get("PX_LAST")
        out[gename] = d.get("FUT_CUR_GEN_TICKER")
        return out

    f = frame(spec["front"], "front", "front_gen")
    s = frame(spec["second"], "second", "second_gen")
    if f.empty:
        raise RuntimeError(f"no data for {spec['front']}")
    raw = f.join(s, how="outer").sort_index()
    raw.to_parquet(_raw_path(product))
    print(f"  {product}: n={len(raw)} {str(raw.index.min())[:10]}->{str(raw.index.max())[:10]}  -> {_raw_path(product)}")
    return raw


def pull_all():
    for p in PRODUCTS:
        pull(p)


def _load_raw(product):
    p = _raw_path(product)
    return pd.read_parquet(p).sort_index() if os.path.exists(p) else None


def build(product, save=True):
    """Spliced front price + daily $/contract return on the commodity∩bond calendar. Roll day =
    front gen-ticker changes; on a roll the prior mark is the 2nd contract's prior close (the
    contract that becomes the new front). Columns: front, second, prior_mark, days, is_roll, chg
    ($/bbl), usd_per_contract (=chg*mult), r_bp (=chg/prior*1e4)."""
    raw = _load_raw(product)
    if raw is None or raw.empty:
        raise FileNotFoundError(f"no crude_raw_{product} — run: python crude.py pull")
    mult = PRODUCTS[product]["mult"]
    f = raw["front"].dropna()
    common = f.index.intersection(engine.trading_calendar()).sort_values()
    fc = raw["front"].reindex(common); sc = raw["second"].reindex(common)
    fg = raw["front_gen"].reindex(common); sg = raw["second_gen"].reindex(common)
    rows, prev, skipped = {}, None, []
    for t in common:
        if prev is None:
            prev = t
            continue
        pt = fc.loc[t]
        gt, gp = fg.loc[t], fg.loc[prev]
        is_roll = pd.notna(gt) and pd.notna(gp) and gt != gp        # front contract changed since prev common day
        if is_roll:
            prior = sc.loc[prev]                                    # new front was the 2nd generic yesterday
            ok = pd.isna(sg.loc[prev]) or (sg.loc[prev] == gt)      # validate (trust if 2nd gen-ticker missing)
            if pd.isna(prior) or not ok:
                skipped.append(t); prev = t; continue
        else:
            prior = fc.loc[prev]
        if pd.isna(prior) or prior == 0 or pd.isna(pt):
            prev = t; continue
        chg = float(pt - prior)
        rows[t] = {"front": float(pt), "second": float(sc.loc[t]) if pd.notna(sc.loc[t]) else np.nan,
                   "prior_mark": float(prior), "days": int((t - prev).days), "is_roll": bool(is_roll),
                   "chg": chg, "usd_per_contract": chg * mult, "r_bp": chg / prior * 1e4}
        prev = t
    out = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    out.index.name = "date"
    out = out[out.index >= engine.ANALYSIS_START]
    out["cum_bp"] = out["r_bp"].cumsum()
    if skipped:
        print(f"  [{product}] {len(skipped)} roll day(s) skipped (couldn't validate the 1-contract splice, "
              f"e.g. {skipped[0].date()})")
    if save:
        out.to_parquet(_built_path(product))
        print(f"  wrote {_built_path(product)}  ({len(out)} rows, {str(out.index.min())[:10]}..{str(out.index.max())[:10]}, "
              f"{int(out['is_roll'].sum())} rolls)")
    return out


def build_all(save=True):
    return {p: build(p, save) for p in PRODUCTS}


def load(product):
    return pd.read_parquet(_built_path(product)).sort_index()


def preview(product, rows=12):
    df = build(product, save=False)
    with pd.option_context("display.max_rows", 80, "display.width", 180):
        print(f"=== {PRODUCTS[product]['name']} ({len(df)} rows, {str(df.index.min())[:10]}..{str(df.index.max())[:10]}) ===")
        print(df.tail(rows).round(4).to_string())
        r = df[df["is_roll"]]
        print(f"\n=== roll days ({len(r)}): mid-month for WTI, month-start for Brent ===")
        print(r[["front", "second", "prior_mark", "days", "chg", "usd_per_contract"]].tail(rows).round(4).to_string())


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "pull":
        pull_all()
    elif cmd == "build":
        build_all()
    elif cmd == "preview":
        preview(sys.argv[2] if len(sys.argv) > 2 else "WTI", int(sys.argv[3]) if len(sys.argv) > 3 else 12)
    else:
        print(__doc__)
