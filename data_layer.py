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
TODAY = pd.Timestamp.today().strftime("%Y%m%d")  # dynamic so re-runs fetch the latest day

# --- Macro / financing series (reference.MD §10, §7.3) --------------------
# FINANCING DECISION (per desk, 2026-06): we assume TIPS and UST finance at the SAME
# GC level -- ignoring specialness and repo bid/offer (no sell-side source exists; the
# big shops build it in-house). Net financing on a breakeven is therefore ~0, and any
# analysis should discount some slippage for the ignored specials/bid-offer.
# Primary GC rate = GCFRTSY (DTCC GCF Treasury repo) -- general Treasury collateral, so
# it serves both legs. SOFR / USRG1T kept as cross-checks and pre-2009 extension.
GC_REPO = "gcf_treasury"  # the financing series used for both legs
MACRO = {
    "cpi_nsa":      ("CPURNSA Index", "PX_LAST"),   # CPI-U NSA (caveat: current print, not as-first-published)
    "gcf_treasury": ("GCFRTSY Index", "PX_LAST"),   # DTCC GCF Treasury repo, 2009-11+  <- PRIMARY financing
    "sofr":         ("SOFRRATE Index", "PX_LAST"),  # GC Treasury repo, 2018-04+ (cross-check)
    "gc_repo_on":   ("USRG1T Curncy", "PX_LAST"),   # USD overnight GC repo, 2004+ (pre-2009 extension)
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
# Bulk pull = price + yield only (fast: ~1s/bond). DV01/duration are deliberately NOT
# bulk-pulled: BBG's computed-analytics fields (RISK_MID, DUR_ADJ_MID) ~3x the request
# time, TIPS DV01 isn't served historically at all, and we need DV01 for BOTH legs on the
# same basis -- so the §4 pricing engine computes DV01 for both legs later (consistent).
# (IDX_RATIO is deliberately excluded: it ~4x's the request time over full history and we
#  compute the index ratio from CPI via §2.1, validated to match BBG live to 1e-6.)
DAILY_FIELDS_TIPS    = ["PX_CLEAN_MID", "PX_DIRTY_MID", "YLD_YTM_MID", "PX_LAST"]
DAILY_FIELDS_NOMINAL = ["PX_CLEAN_MID", "PX_DIRTY_MID", "YLD_YTM_MID", "PX_LAST"]

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


def build_universe():
    """Build universe.csv from the auction-derived OTR schedule (every CUSIP that has
    ever held an OTR role across 2003->present). Falls back to the seed if auctions
    haven't been pulled yet."""
    _ensure_dirs()
    path = os.path.join(CACHE, "universe.csv")
    try:
        import auctions
        uni = auctions.otr_universe()  # columns: cusip, leg, tenor
        uni.to_csv(path, index=False)
        print(f"  universe.csv: {len(uni)} bonds from auction schedule")
    except Exception as e:
        print(f"  WARN auction universe unavailable ({e}); using seed")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["cusip", "role", "leg", "tenor", "note"])
            w.writerows(SEED_UNIVERSE)
    return path


def load_universe():
    path = os.path.join(CACHE, "universe.csv")
    if not os.path.exists(path):
        build_universe()
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


def pull_bond(cusip, leg="tips", static=None):
    sec = f"{cusip} Govt"
    # static (use prefetched batch if provided)
    st = static if static is not None else bbg.reference([sec], STATIC_FIELDS).get(sec, {})
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


def _save_daily(cusip, rows):
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    if "PX_DIRTY_MID" in df:
        first = df["PX_DIRTY_MID"].first_valid_index()
        if first is not None:
            df = df.loc[first:]
    df.to_parquet(os.path.join(CACHE, "daily", f"{cusip}.parquet"))
    return len(df)


def pull_bonds(skip_existing=True, batch=20):
    """Pull static + daily for the whole OTR universe, batching history requests by leg
    (~20 securities/request) since Bloomberg costs the same for one or many per call."""
    _ensure_dirs()
    uni = load_universe()
    todo = uni if not skip_existing else uni[~uni["cusip"].apply(
        lambda c: os.path.exists(os.path.join(CACHE, "daily", f"{c}.parquet")))]
    if todo.empty:
        print("  all bonds already cached")
        return
    bbg.open_session()
    try:
        # static: batch 50/request
        cusips = todo["cusip"].tolist()
        static_map = {}
        for k in range(0, len(cusips), 50):
            chunk = [f"{c} Govt" for c in cusips[k:k+50]]
            static_map.update(bbg.reference(chunk, STATIC_FIELDS))
        for c in cusips:
            st = static_map.get(f"{c} Govt", {})
            pd.DataFrame([{**{"cusip": c}, **st}]).to_parquet(os.path.join(CACHE, "static", f"{c}.parquet"))
        # daily: batch by leg (consistent field set), ~batch securities/request
        done = 0
        for leg, fields in (("tips", DAILY_FIELDS_TIPS), ("nominal", DAILY_FIELDS_NOMINAL)):
            cs = todo[todo["leg"] == leg]["cusip"].tolist()
            for k in range(0, len(cs), batch):
                grp = cs[k:k+batch]
                h = bbg.history([f"{c} Govt" for c in grp], fields, START, TODAY)
                for c in grp:
                    nrows = _save_daily(c, h.get(f"{c} Govt", []))
                done += len(grp)
                print(f"  [{done}/{len(cusips)}] {leg} batch -> {len(grp)} bonds", flush=True)
    finally:
        bbg.close_session()
    print(f"  done: {len(cusips)} bonds")


def preview(cusip=None, rows=24):
    """Quick textual look at the cache.
    No cusip: CPI publications (monthly) + GC repo tail + universe summary.
    With cusip: that bond's static + daily head/tail.  `rows` controls how many."""
    _ensure_dirs()
    m = os.path.join(CACHE, "macro.parquet")
    with pd.option_context("display.max_rows", max(rows, 60), "display.width", 160):
        if cusip is None:
            if os.path.exists(m):
                mdf = pd.read_parquet(m)
                # CPI prints monthly -> drop the NaN daily rows so you see actual publications
                cpi = mdf["cpi_nsa"].dropna()
                cpi_tbl = cpi.tail(rows).to_frame("CPI_U_NSA")
                cpi_tbl["m/m %"] = (cpi.pct_change() * 100).round(3).tail(rows)
                cpi_tbl["y/y %"] = (cpi.pct_change(12) * 100).round(3).tail(rows)
                print(f"=== CPI-U NSA publications (last {rows} months) — CPURNSA Index ===")
                print(cpi_tbl.to_string())
                print(f"\n=== GC repo / financing (last {min(rows,10)} rows) ===")
                print(mdf[["gcf_treasury", "sofr", "gc_repo_on", "fed_funds"]].dropna(how="all").tail(min(rows, 10)).to_string())
            uni = load_universe()
            print(f"\n=== UNIVERSE ({len(uni)} bonds) ===")
            print(uni.groupby(["leg", "tenor"]).size().to_string())
            print("\nPreview a bond:  python data_layer.py preview 91282CPU9 [rows]")
            return
        dpath = os.path.join(CACHE, "daily", f"{cusip}.parquet")
        spath = os.path.join(CACHE, "static", f"{cusip}.parquet")
        if os.path.exists(spath):
            print(f"=== STATIC {cusip} ===")
            print(pd.read_parquet(spath).T.to_string())
        if os.path.exists(dpath):
            d = pd.read_parquet(dpath)
            print(f"\n=== DAILY {cusip}  ({len(d)} rows, {str(d.index.min())[:10]}..{str(d.index.max())[:10]}) ===")
            h = max(rows // 2, 3)
            print(f"head:\n{d.head(h).to_string()}")
            print(f"tail:\n{d.tail(h).to_string()}")
        else:
            print(f"no daily cache for {cusip}")


def current_otr_cusips():
    """The CUSIPs currently holding an OTR role (latest month of the schedule).
    These are the only bonds whose cache needs refreshing day-to-day; off-the-run and
    matured bonds are frozen."""
    import auctions
    s = auctions.otr_schedule()
    last = s["month"].max()
    return s[s["month"] == last][["cusip", "leg"]].drop_duplicates()


def update():
    """Incremental refresh -- cheap, run daily. Does NOT rewrite all 529 bonds:
      1. macro (CPI/repo) — full but tiny;
      2. auction calendar + universe — picks up any new auction;
      3. pull_bonds(skip_existing=True) — fetches only brand-new bonds;
      4. re-pull just the CURRENT OTRs (their history is short & still growing).
    Off-the-run / matured bonds are left untouched (their data never changes)."""
    print("1) macro"); pull_macro()
    print("2) auctions + universe")
    try:
        import auctions
        auctions.pull()
    except Exception as e:
        print(f"   WARN auctions refresh failed: {e}")
    build_universe()
    print("3) new bonds (skip existing)"); pull_bonds(skip_existing=True)
    print("4) refresh current OTRs")
    otr = current_otr_cusips()
    bbg.open_session()
    try:
        for _, r in otr.iterrows():
            fields = DAILY_FIELDS_TIPS if r["leg"] == "tips" else DAILY_FIELDS_NOMINAL
            st = bbg.reference([f"{r['cusip']} Govt"], STATIC_FIELDS).get(f"{r['cusip']} Govt", {})
            pd.DataFrame([{**{"cusip": r["cusip"]}, **st}]).to_parquet(
                os.path.join(CACHE, "static", f"{r['cusip']}.parquet"))
            h = bbg.history(f"{r['cusip']} Govt", fields, START, TODAY).get(f"{r['cusip']} Govt", [])
            n = _save_daily(r["cusip"], h)
            print(f"   {r['cusip']} ({r['leg']}) -> {n} rows")
    finally:
        bbg.close_session()
    print("update done")


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
    elif cmd == "universe":
        build_universe()
    elif cmd == "bonds":
        pull_bonds()
    elif cmd == "update":
        update()
    elif cmd == "preview":
        # accept: preview | preview <rows> | preview <cusip> [rows]
        a2 = sys.argv[2] if len(sys.argv) > 2 else None
        if a2 is not None and a2.isdigit():
            preview(None, int(a2))
        else:
            preview(a2, int(sys.argv[3]) if len(sys.argv) > 3 else 24)
    elif cmd == "status":
        status()
    else:
        print(__doc__)
