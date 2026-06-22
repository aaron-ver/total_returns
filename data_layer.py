"""
Data layer for the TIPS / breakeven total-return build (reference.MD §10).

Pulls raw daily series from Bloomberg and caches them to ./cache as parquet.
Return math is deliberately NOT done here (per desk instruction: data layer first).

What it caches
--------------
  cache/macro.parquet          : CPI, GC repo (SOFR + USRG1T), fed funds  (2003->present)
  cache/static/<cusip>.parquet : per-bond static reference fields
  cache/daily/<cusip>.parquet  : per-bond daily series (clean, dirty, accrued,
                                  index ratio, reference CPI, yield, risk, duration)
  cache/universe.csv           : the bond universe + role/tenor tags (OTR schedule input)

Usage
-----
  python data_layer.py macro            # pull/refresh macro series
  python data_layer.py bonds            # pull static+daily for every cusip in universe.csv
  python data_layer.py status           # show what's cached

Identify every bond by CUSIP (description tickers are unreliable). The universe.csv
is the OTR-schedule backbone: it lists each bond that has held an on-the-run role
(5y/10y/30y TIPS and its nominal comparator) with issue date, so the splice schedule
(reference.MD §9.2.1) can be reconstructed on the "old-OTR-through-auction-month,
roll 1st business day" calendar. Seeded here with the current on-the-runs; extend it
from the Treasury auction calendar to go back to 2003.
"""
from __future__ import annotations
import os, sys, csv
import pandas as pd
import bbg

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
START = "20030101"
TODAY = "20260620"

# --- Macro / financing series (reference.MD §10, §7.3) --------------------
# GC financing chain: SOFR is GC from 2018-04; USRG1T (USD O/N GC repo) covers
# 2004->2018; fed funds as a pre-2004 / sanity fallback.
MACRO = {
    "cpi_nsa":      ("CPURNSA Index", "PX_LAST"),   # CPI-U NSA (see caveat: current print, not as-first-published)
    "sofr":         ("SOFRRATE Index", "PX_LAST"),  # GC Treasury repo, 2018-04+
    "gc_repo_on":   ("USRG1T Curncy", "PX_LAST"),   # USD overnight GC repo, 2004+
    "gcf_treasury": ("GCFRTSY Index", "PX_LAST"),   # DTCC GCF Treasury repo, 2009-11+
    "fed_funds":    ("FEDL01 Index", "PX_LAST"),    # fallback / sanity, 2003+
}

# --- Per-bond fields -------------------------------------------------------
STATIC_FIELDS = ["SECURITY_DES", "ID_CUSIP", "CPN", "MATURITY", "ISSUE_DT", "FIRST_CPN_DT",
                 "BASE_CPI", "REFERENCE_INDEX", "ISSUE_PX", "AMT_ISSUED", "INFLATION_LINKED_INDICATOR"]

# IMPORTANT (validated 2026-06-22): Bloomberg's HistoricalDataRequest does NOT serve
# the TIPS analytics fields as daily series. For a TIPS, only these come back reliably:
#   PX_CLEAN_MID  -> real clean price
#   PX_DIRTY_MID  -> CASH (inflation-adjusted) dirty price == V/100  (already x index ratio)
#   YLD_YTM_MID   -> real yield
#   IDX_RATIO     -> index ratio, but ONLY within the bond's trading life; pre-issuance
#                    rows backfill to a garbage ~0.998 and MUST be masked.
# These return NOTHING as TIPS daily history and must be COMPUTED (see reference.MD):
#   INT_ACC (accrued, §2.4), REFERENCE_CPI / DRI (§2.1), RISK_MID / DV01 (§4 engine).
# For NOMINAL bonds, RISK_MID and DUR_ADJ_MID *do* serve historically.
DAILY_FIELDS_TIPS    = ["PX_CLEAN_MID", "PX_DIRTY_MID", "YLD_YTM_MID", "IDX_RATIO", "PX_LAST"]
DAILY_FIELDS_NOMINAL = ["PX_CLEAN_MID", "PX_DIRTY_MID", "YLD_YTM_MID", "RISK_MID", "DUR_ADJ_MID", "PX_LAST"]

# Current on-the-runs (validated 2026-06-22). role: tenor + leg.
SEED_UNIVERSE = [
    # cusip,        role,        leg,      tenor, note
    ("91282CQP9", "OTR_5y",   "tips",    "5y",  "TII 1 1/4 04/15/31"),
    ("91282CPU9", "OTR_10y",  "tips",    "10y", "TII 1 7/8 01/15/36"),
    ("912810US5", "OTR_30y",  "tips",    "30y", "TII 2 3/8 02/15/56"),
    ("91282CQU8", "OTR_5y",   "nominal", "5y",  "T 4 1/8 05/31/31"),
    ("91282CQQ7", "OTR_10y",  "nominal", "10y", "T 4 3/8 05/15/36"),
    ("912810UU0", "OTR_30y",  "nominal", "30y", "T 5 05/15/56"),
]


def _ensure_dirs():
    for sub in ("", "static", "daily"):
        os.makedirs(os.path.join(CACHE, sub), exist_ok=True)


def seed_universe():
    """Write the initial universe.csv from the current on-the-runs if absent."""
    _ensure_dirs()
    path = os.path.join(CACHE, "universe.csv")
    if os.path.exists(path):
        return path
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cusip", "role", "leg", "tenor", "note"])
        w.writerows(SEED_UNIVERSE)
    return path


def load_universe():
    path = seed_universe()
    return pd.read_csv(path, dtype=str)


def pull_macro():
    _ensure_dirs()
    frames = []
    for name, (tic, fld) in MACRO.items():
        h = bbg.history(tic, [fld], START, TODAY)
        rows = h.get(tic, [])
        if not rows:
            print(f"  WARN no data for {name} ({tic})")
            continue
        df = pd.DataFrame(rows).rename(columns={fld: name})[["date", name]]
        df["date"] = pd.to_datetime(df["date"])
        frames.append(df.set_index("date"))
        print(f"  {name:14s} {tic:18s} n={len(df)} {str(df['date'].min())[:10]}->{str(df['date'].max())[:10]}")
    macro = pd.concat(frames, axis=1).sort_index()
    out = os.path.join(CACHE, "macro.parquet")
    macro.to_parquet(out)
    print(f"  wrote {out}  ({macro.shape[0]} rows x {macro.shape[1]} cols)")
    return macro


def pull_bond(cusip, leg="tips"):
    sec = f"{cusip} Govt"
    # static
    st = bbg.reference([sec], STATIC_FIELDS).get(sec, {})
    st_df = pd.DataFrame([{**{"cusip": cusip}, **st}])
    st_df.to_parquet(os.path.join(CACHE, "static", f"{cusip}.parquet"))
    # daily
    fields = DAILY_FIELDS_TIPS if leg == "tips" else DAILY_FIELDS_NOMINAL
    h = bbg.history(sec, fields, START, TODAY).get(sec, [])
    if not h:
        print(f"  {cusip}: NO daily data")
        return st, None
    df = pd.DataFrame(h)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # mask to the bond's actual trading life (drop NaN-padded pre-issuance rows;
    # this also removes the garbage pre-issuance IDX_RATIO backfill for TIPS).
    if "PX_DIRTY_MID" in df:
        first = df["PX_DIRTY_MID"].first_valid_index()
        if first is not None:
            df = df.loc[first:]
    df.to_parquet(os.path.join(CACHE, "daily", f"{cusip}.parquet"))
    lo, hi = df.index.min(), df.index.max()
    print(f"  {cusip} {str(st.get('SECURITY_DES','')):24s} daily n={len(df)} "
          f"{str(lo)[:10]}->{str(hi)[:10]}")
    return st, df


def index_ratio(cpi_nsa, base_cpi, settle_date):
    """Compute the index ratio for a settlement date from CPI-U NSA (reference.MD §2.1-2.2).
    Validated to match Bloomberg's live IDX_RATIO to 1e-6.  cpi_nsa is a month-end-indexed
    Series of CPI-U NSA levels; base_cpi is the bond's DRI_base (BASE_CPI static field)."""
    import calendar, math
    s = pd.Timestamp(settle_date)
    d, D = s.day, calendar.monthrange(s.year, s.month)[1]
    m3 = (s - pd.DateOffset(months=3)); m2 = (s - pd.DateOffset(months=2))
    cpi_m3 = cpi_nsa[(cpi_nsa.index.year == m3.year) & (cpi_nsa.index.month == m3.month)].iloc[0]
    cpi_m2 = cpi_nsa[(cpi_nsa.index.year == m2.year) & (cpi_nsa.index.month == m2.month)].iloc[0]
    dri = cpi_m3 + ((d - 1) / D) * (cpi_m2 - cpi_m3)
    return round(math.floor(dri / base_cpi * 1e6) / 1e6, 5), dri


def pull_bonds():
    _ensure_dirs()
    uni = load_universe()
    for _, row in uni.iterrows():
        pull_bond(row["cusip"], leg=row.get("leg", "tips"))


def status():
    _ensure_dirs()
    m = os.path.join(CACHE, "macro.parquet")
    print("macro.parquet:", "yes" if os.path.exists(m) else "no")
    if os.path.exists(m):
        mdf = pd.read_parquet(m)
        print("  cols:", list(mdf.columns), "rows:", len(mdf))
    nd = len(os.listdir(os.path.join(CACHE, "daily"))) if os.path.isdir(os.path.join(CACHE, "daily")) else 0
    print(f"daily bond files: {nd}")
    print("universe:", len(load_universe()), "bonds")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "macro":
        pull_macro()
    elif cmd == "bonds":
        pull_bonds()
    elif cmd == "status":
        status()
    else:
        print(__doc__)
