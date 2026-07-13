"""
Per-bond financed returns for the US universe (every cached TIPS + nominal CUSIP) — the US
counterpart of engine_intl's per-bond sheets, so the dashboard can show individual bonds and
legs, not just the CMT buckets.

Math is identical to engine.leg_series' per-day walk (same packs, same settle-span financing,
same monthly 100k-DV01 denominator epochs, same coupon capture) but for ONE bond over its whole
cached history, vectorized. No roll logic — a bond is just itself.

Outputs:
  cache/returns_bonds/<cusip>.parquet   daily sheet: bp / gross_bp / fin_bp / fin_sens / cum_bp ...
  cache/returns_bonds/_index.csv        one row per bond: cusip, leg, tenor, desc, maturity, span

Run:  python us_bonds.py            # build all (needs the daily/static caches, no Bloomberg)
      python us_bonds.py status
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

import engine
import data_layer as dl

CACHE = engine.CACHE
OUT = os.path.join(CACHE, "returns_bonds")


def bond_series(cusip, leg, cpi, gc):
    """Full-history financed DV01-normalized daily returns for one bond (engine conventions)."""
    pack = engine._bond_pack(cusip, leg, cpi)
    if pack is None:
        return None
    m = pack["m"]
    if len(m) < 2:
        return None
    V = m["V"].to_numpy(float)
    dv01 = m["dv01_per100"].to_numpy(float)
    settle = pd.DatetimeIndex(m["settle"])

    # monthly denominator epochs: at each month's first obs, denom = dv01 of the PREVIOUS obs
    # (fallback: that day's own dv01), held constant within the month  [mirrors leg_series]
    month = m.index.to_period("M")
    first = np.r_[True, month[1:] != month[:-1]]
    prev_dv01 = np.r_[np.nan, dv01[:-1]]
    denom = np.where(first, np.where(np.isfinite(prev_dv01) & (prev_dv01 != 0),
                                     prev_dv01, dv01), np.nan)
    denom = pd.Series(denom, index=m.index).ffill().to_numpy(float)

    d_days = np.r_[np.nan, (settle[1:] - settle[:-1]).days]
    V_prev = np.r_[np.nan, V[:-1]]
    dV = np.r_[np.nan, np.diff(V)]
    g = gc.reindex(gc.index.union(m.index)).ffill().reindex(m.index).to_numpy(float)
    fin = np.where(np.isfinite(g), d_days / 360.0 * (g / 100.0) * V_prev, 0.0)

    # coupons paid within each settle span (settle_prev, settle]: coupon/2 (× IR for TIPS)
    cpn = np.zeros(len(m))
    ir = m["IR"].to_numpy(float) if leg == "tips" else np.ones(len(m))
    sv = settle.values
    for pdte in pack["pay"]:
        k = int(np.searchsorted(sv, np.datetime64(pdte)))          # first settle >= pay date
        if 1 <= k < len(m) and sv[k - 1] < np.datetime64(pdte) <= sv[k]:
            irc = ir[k] if np.isfinite(ir[k]) else 1.0
            cpn[k] += (pack["coupon"] / 2.0) * irc

    ok = (np.isfinite(dV) & np.isfinite(V_prev) & np.isfinite(denom) & (denom != 0)
          & np.isfinite(d_days))
    pnl = dV + cpn - fin
    df = pd.DataFrame({
        "settle": settle, "clean": m["clean"], "yield": m["ytm"], "IR": m["IR"],
        "V": V, "V_prev": V_prev, "DV01": dv01, "denom": denom,
        "dV": dV, "coupon": cpn, "days": d_days, "gc": g, "financing": fin,
        "gross_bp": np.where(ok, (dV + cpn) / denom, np.nan),
        "fin_bp": np.where(ok, fin / denom, np.nan),
        "bp": np.where(ok, pnl / denom, np.nan),
        "fin_sens": np.where(ok, (d_days / 360.0 * V_prev / 1e4) / denom, np.nan),
    }, index=m.index)
    df = df[np.isfinite(df["bp"])]
    if df.empty:
        return None
    df["cum_bp"] = df["bp"].cumsum()
    df["is_coupon"] = df["coupon"] != 0.0
    return df


def build_all(save=True):
    os.makedirs(OUT, exist_ok=True)
    cpi = engine._macro()["cpi_nsa"]
    gc = engine.gc_series()
    u = dl.load_universe()
    rows, n_ok = [], 0
    for _, r in u.iterrows():
        cusip, leg, tenor = r["cusip"], r["leg"], r["tenor"]
        try:
            df = bond_series(cusip, leg, cpi, gc)
        except Exception as e:
            print(f"  {cusip} FAILED: {type(e).__name__}: {e}", flush=True)
            df = None
        if df is None:
            continue
        if save:
            df.to_parquet(os.path.join(OUT, f"{cusip}.parquet"))
        coupon, maturity, _ = engine._static(cusip)
        try:
            st = pd.read_parquet(os.path.join(CACHE, "static", f"{cusip}.parquet"))
            desc = str(st["SECURITY_DES"].iloc[0])
        except Exception:
            desc = cusip
        rows.append({"cusip": cusip, "leg": leg, "tenor": tenor, "desc": desc,
                     "coupon": coupon, "maturity": pd.Timestamp(maturity),
                     "first": df.index.min(), "last": df.index.max(), "n": len(df),
                     "cum_bp": round(float(df["cum_bp"].iloc[-1]), 1)})
        n_ok += 1
        if n_ok % 50 == 0:
            print(f"  ... {n_ok} bonds built", flush=True)
    idx = pd.DataFrame(rows)
    if save and len(idx):
        idx.to_csv(os.path.join(OUT, "_index.csv"), index=False)
    print(f"  us_bonds: {n_ok}/{len(u)} bonds -> {OUT}")
    return idx


def load_index():
    p = os.path.join(OUT, "_index.csv")
    if not os.path.exists(p):
        return pd.DataFrame()
    idx = pd.read_csv(p, parse_dates=["maturity", "first", "last"])
    return idx


def load_bond(cusip):
    p = os.path.join(OUT, f"{cusip}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "status":
        idx = load_index()
        print(idx.groupby(["leg", "tenor"]).size() if len(idx) else "not built")
    else:
        build_all()
