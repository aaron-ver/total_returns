"""
Data layer for the European/UK inflation-linked bond build (reference_intl.MD §9-§10).

Mirror of data_layer.py for the non-US linkers. Pulls raw inputs from Bloomberg and caches to
./cache_intl as parquet, keeping the US ./cache untouched. Return math stays in engine_intl.py.

Caches
------
  cache_intl/macro.parquet         : reference indices (euro HICPxt, French CPIxt, UK RPI) +
                                     local GC financing (€STR, SONIA), monthly/daily
  cache_intl/static/<isin>.parquet : per-bond static reference fields (via linkers.enrich)
  cache_intl/daily/<isin>.parquet  : per-bond daily series (clean, dirty, real yield,
                                     Bloomberg index ratio + reference CPI as cross-check)
  cache_intl/universe.csv          : the curated bond universe (from linkers.SEED_UNIVERSE)

Index ratio (reference_intl §3-§4): computed from the reference-index series with the market's
lag, the SAME interpolation as US TIPS (data layer §2.1). The base DRI is RECOMPUTED from the
current index series at the bond's dated date, so a constant base-year REBASING cancels in the
ratio (numerator and denominator scale together). Result is cross-checked against Bloomberg's
per-bond INDEX_RATIO and both are stored.

Usage:
  python data_layer_intl.py macro          # pull reference indices + financing
  python data_layer_intl.py bonds          # pull static + daily (lean fields) for every ISIN (resumable)
  python data_layer_intl.py ircheck [yrs]  # merge Bloomberg INDEX_RATIO over the last `yrs` (default 3) as IR_bbg cross-check
  python data_layer_intl.py update         # incremental refresh
  python data_layer_intl.py status
"""
from __future__ import annotations
import os, sys, calendar, math, time
import pandas as pd

import bbg
import linkers

CACHE = linkers.CACHE
START = "19900101"                                   # macro: long enough for the oldest base months
DAILY_START = "19980101"                             # per-bond daily floor (oldest active issue ~1999)
TODAY = pd.Timestamp.today().strftime("%Y%m%d")

# Per-bond daily fields -- the MINIMAL set engine_intl actually consumes: real clean price + real
# yield (it computes dirty/accrued/IR itself, §3). PX_DIRTY_MID and the computed-analytics indexation
# fields (INDEX_RATIO/REFERENCE_CPI) are deliberately NOT bulk-pulled: the US build learned IDX_RATIO
# "~4x's request time over full history", and the 2026-06-26 freeze showed that request SIZE
# (range × bonds × fields) trips Bloomberg's backend timeout. We COMPUTE the index ratio and validate
# it against BBG's INDEX_RATIO via the cheap recent-window pull_ir_check().
DAILY_FIELDS = ["PX_CLEAN_MID", "YLD_YTM_MID"]
IR_XCHECK_FIELDS = ["INDEX_RATIO"]                   # pulled only over a recent window (pull_ir_check)
CHUNK_YEARS = 8                                       # split each bond's daily history into ~8yr windows


def _date_windows(start, end, years):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    cur = s
    while cur <= e:
        nxt = min(cur + pd.DateOffset(years=years) - pd.Timedelta(days=1), e)
        yield cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")
        cur = nxt + pd.Timedelta(days=1)


def _reconnect():
    try:
        bbg.close_session()
    except Exception:
        pass
    time.sleep(4)
    bbg.open_session()


def _history_one(sec, fields, start, end, chunk_years=CHUNK_YEARS, tries=3):
    """Daily history for ONE security, split into small date windows so no single request is large
    enough to trip the backend timeout (the root cause of the freezes). Reconnect+retry per window;
    raises if a window still fails after `tries` so the caller can skip just this bond and continue."""
    rows = []
    windows = list(_date_windows(start, end, chunk_years)) if chunk_years else [(start, end)]
    for a, b in windows:
        for k in range(tries):
            try:
                rows += bbg.history([sec], fields, a, b).get(sec, [])
                break
            except Exception as e:
                if k == tries - 1:
                    raise RuntimeError(f"{sec} {a[:4]}-{b[:4]} {type(e).__name__}")
                _reconnect()
    return rows


def _history_retry(secs, fields, start, end, tries=3):
    """Batched bbg.history with reconnect+backoff (used by the short-window ircheck). Raises after
    `tries` rather than hanging."""
    for k in range(tries):
        try:
            return bbg.history(secs, fields, start, end)
        except Exception as e:
            print(f"   batch failed ({type(e).__name__}: {e}); reconnect+retry {k + 1}/{tries}", flush=True)
            _reconnect()
    raise RuntimeError(f"history failed after {tries} tries for {len(secs)} securities")


def _ensure_dirs():
    linkers._ensure_dirs()


# --------------------------------------------------------------------------- macro / indices
def pull_macro():
    """Pull every reference index (monthly NSA level) + every financing curve (daily) into one
    macro frame. Reference indices are joined on month-end; financing on its native daily index."""
    _ensure_dirs()
    frames = []
    for key, meta in linkers.REF_INDEX.items():
        h = bbg.history(meta["ticker"], ["PX_LAST"], START, TODAY, periodicity="MONTHLY").get(meta["ticker"], [])
        if not h:
            print(f"  WARN no data for {key} ({meta['ticker']})"); continue
        df = pd.DataFrame(h); df["date"] = pd.to_datetime(df["date"])
        # normalize to month-end so the §3 interpolation can look up (year, month)
        s = df.set_index("date")["PX_LAST"]
        s.index = s.index + pd.offsets.MonthEnd(0)
        frames.append(s.rename(key).to_frame())
        print(f"  {key:12s} {meta['ticker']:16s} n={len(s)} {str(s.index.min())[:7]}->{str(s.index.max())[:7]}")
    for key, meta in linkers.FINANCING.items():
        if not meta.get("ticker"):                       # RFR tickers left None until filled from REPF
            print(f"  {key:12s} (no ticker yet — fill from REPF; falls back to €STR/SONIA)"); continue
        h = bbg.history(meta["ticker"], ["PX_LAST"], START, TODAY).get(meta["ticker"], [])
        if not h:
            print(f"  WARN no data for {key} ({meta['ticker']})"); continue
        df = pd.DataFrame(h); df["date"] = pd.to_datetime(df["date"])
        frames.append(df.set_index("date")["PX_LAST"].rename(key).to_frame())
        print(f"  {key:12s} {meta['ticker']:16s} n={len(df)} (daily financing)")
    macro = pd.concat(frames, axis=1).sort_index()
    macro.to_parquet(os.path.join(CACHE, "macro.parquet"))
    print(f"  wrote {os.path.join(CACHE, 'macro.parquet')}  ({macro.shape[0]}x{macro.shape[1]})")
    return macro


def _macro():
    return pd.read_parquet(os.path.join(CACHE, "macro.parquet"))


def ref_index_series(index_key):
    """Monthly NSA level series for a reference index, indexed by month-end (raw, current base)."""
    m = _macro()
    if index_key not in m:
        raise KeyError(f"{index_key} not in macro cache; run `python data_layer_intl.py macro`")
    return m[index_key].dropna()


_FALLBACK_LOGGED = set()
def financing_series(repo_key):
    """Daily GC financing rate (percent), ffilled. If the requested curve isn't in the macro cache
    (e.g. an RFR ticker not yet filled), fall back to the cached €STR/SONIA single rate by currency
    so the engine still runs -- logged once."""
    m = _macro()
    s = m[repo_key].dropna() if repo_key in m.columns else pd.Series(dtype=float)
    if s.empty:
        meta = linkers.FINANCING.get(repo_key, {})
        fb = linkers.GBP_FALLBACK_REPO if meta.get("ccy") == "GBP" else linkers.EUR_FALLBACK_REPO
        if repo_key not in _FALLBACK_LOGGED:
            print(f"  [financing] {repo_key} unavailable -> falling back to {fb}")
            _FALLBACK_LOGGED.add(repo_key)
        s = m[fb].dropna() if fb in m.columns else pd.Series(dtype=float)
    return s.sort_index().ffill()


# --------------------------------------------------------------------------- index ratio (§3-§4)
def daily_reference_index(index_key, settle_dates, lag_months=3, interp=True):
    """Daily reference index (DRI) for each settlement date, by the lagged interpolation of the
    monthly reference index (reference_intl §3). interp=False -> step (8m-lag old-style, deferred).
    Returns a Series aligned to settle_dates (NaN where the index months aren't published)."""
    idx = ref_index_series(index_key)
    by_month = {(ts.year, ts.month): float(v) for ts, v in idx.items()}
    out = {}
    for s in pd.DatetimeIndex(settle_dates):
        m_lo = s - pd.DateOffset(months=lag_months)          # month M-lag (the anchor)
        a = by_month.get((m_lo.year, m_lo.month))
        if a is None:
            continue
        if not interp:                                        # monthly step (8m-lag)
            out[s] = a; continue
        m_hi = s - pd.DateOffset(months=lag_months - 1)       # month M-(lag-1)
        b = by_month.get((m_hi.year, m_hi.month))
        if b is None:
            continue
        D = calendar.monthrange(s.year, s.month)[1]
        out[s] = a + ((s.day - 1) / D) * (b - a)
    return pd.Series(out)


def index_ratio_series(index_key, dated_date, settle_dates, lag_months=3, interp=True,
                       rounding="round5"):
    """Index ratio IR(settle) = DRI(settle) / DRI(dated_date), with the base DRI RECOMPUTED from
    the SAME current-base index series (so constant rebasings cancel; reference_intl §4). Rounding
    'round5' (euro/UK convention) or 'trunc6round5' (US convention). Returns a Series on settle_dates."""
    base_dri = daily_reference_index(index_key, [pd.Timestamp(dated_date)], lag_months, interp)
    if base_dri.empty or pd.isna(base_dri.iloc[0]) or base_dri.iloc[0] == 0:
        return pd.Series(dtype=float)
    base = float(base_dri.iloc[0])
    dri = daily_reference_index(index_key, settle_dates, lag_months, interp)
    raw = dri / base
    if rounding == "trunc6round5":
        return raw.apply(lambda x: round(math.floor(x * 1e6) / 1e6, 5))
    return raw.round(5)


# --------------------------------------------------------------------------- per-bond pulls
def _save_daily(isin, rows):
    if not rows:
        return 0
    df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    if "PX_CLEAN_MID" in df:                                  # trim NaN-padded pre-issuance rows
        first = df["PX_CLEAN_MID"].first_valid_index()
        if first is not None:
            df = df.loc[first:]
    df.to_parquet(os.path.join(CACHE, "daily", f"{isin}.parquet"))
    return len(df)


def _pull_daily(isins, pause=0.2):
    """Per-bond chunked daily pull (assumes a bbg session is open). One bond at a time, each in small
    date windows; a bond that fails is logged and SKIPPED. Returns the list of failed ISINs."""
    failed = []
    for n, i in enumerate(isins, 1):
        sec = f"{i} {linkers.BBG_SUFFIX}"
        try:
            nr = _save_daily(i, _history_one(sec, DAILY_FIELDS, DAILY_START, TODAY))
            print(f"  [{n}/{len(isins)}] {i} -> {nr} rows", flush=True)
        except Exception as e:
            failed.append(i)
            print(f"  [{n}/{len(isins)}] {i} FAILED ({e}) — skipped, retry next run", flush=True)
        time.sleep(pause)
    return failed


def pull_static(isins):
    """Pull Bloomberg static (linkers.STATIC_FIELDS) for an arbitrary ISIN list -> static/<isin>.parquet
    (assumes a bbg session is open). Used for nominal hedge bonds that aren't in the linker universe."""
    secs = [f"{i} {linkers.BBG_SUFFIX}" for i in isins]
    st = {}
    for k in range(0, len(secs), 50):
        st.update(bbg.reference(secs[k:k + 50], linkers.STATIC_FIELDS))
    for i in isins:
        rec = st.get(f"{i} {linkers.BBG_SUFFIX}", {})
        if rec:
            pd.DataFrame([{**{"isin": i}, **rec}]).to_parquet(os.path.join(CACHE, "static", f"{i}.parquet"))
    return st


def pull_bonds(skip_existing=True, include_deferred=False, do_enrich=True, pause=0.2):
    """Pull static (via linkers.enrich) + daily for the whole linker universe, ONE bond at a time in
    small date windows (resilient to the backend timeout; resumable via skip_existing)."""
    _ensure_dirs()
    u = linkers.load_universe(include_deferred=include_deferred)
    if skip_existing:
        u = u[~u["isin"].apply(lambda i: os.path.exists(os.path.join(CACHE, "daily", f"{i}.parquet")))]
    if u.empty:
        print("  all bonds already cached"); return
    if do_enrich:
        linkers.enrich(write=True)                            # static for all (cheap, also confirms)
    isins = u["isin"].tolist()
    bbg.open_session()
    try:
        failed = _pull_daily(isins, pause)
    finally:
        bbg.close_session()
    print(f"  done: {len(isins) - len(failed)} pulled, {len(failed)} failed"
          + (f": {', '.join(failed)}" if failed else ""))


def pull_isins(isins, skip_existing=True, pause=0.2):
    """Pull static + daily for an arbitrary ISIN list (the breakeven NOMINAL hedge bonds, which
    aren't in the linker universe). Resumable; returns failed ISINs."""
    _ensure_dirs()
    isins = list(dict.fromkeys(str(i) for i in isins if i))   # dedup, keep order
    if skip_existing:
        isins = [i for i in isins if not os.path.exists(os.path.join(CACHE, "daily", f"{i}.parquet"))]
    if not isins:
        print("  all requested ISINs already cached"); return []
    bbg.open_session()
    try:
        pull_static(isins)
        failed = _pull_daily(isins, pause)
    finally:
        bbg.close_session()
    print(f"  done: {len(isins) - len(failed)} pulled, {len(failed)} failed"
          + (f": {', '.join(failed)}" if failed else ""))
    return failed


def pull_ir_check(years=3, batch=8, include_deferred=False):
    """Cheap cross-check: pull Bloomberg's own INDEX_RATIO over only the last `years` and merge it
    into the daily caches as the IR_bbg column (the engine compares our computed IR against it).
    Short window keeps the heavy analytics field light -- run AFTER pull_bonds."""
    _ensure_dirs()
    start = (pd.Timestamp.today() - pd.DateOffset(years=years)).strftime("%Y%m%d")
    u = linkers.load_universe(include_deferred=include_deferred)
    isins = u["isin"].tolist()
    bbg.open_session()
    try:
        for k in range(0, len(isins), batch):
            grp = isins[k:k + batch]
            h = _history_retry([f"{i} {linkers.BBG_SUFFIX}" for i in grp], IR_XCHECK_FIELDS, start, TODAY)
            for i in grp:
                rows = h.get(f"{i} {linkers.BBG_SUFFIX}", [])
                dp = os.path.join(CACHE, "daily", f"{i}.parquet")
                if not rows or not os.path.exists(dp):
                    continue
                ir = pd.DataFrame(rows); ir["date"] = pd.to_datetime(ir["date"])
                ir = ir.set_index("date")["INDEX_RATIO"]
                d = pd.read_parquet(dp)
                d["INDEX_RATIO"] = ir.reindex(d.index)
                d.to_parquet(dp)
            print(f"  [{min(k + batch, len(isins))}/{len(isins)}] ir-check merged", flush=True)
    finally:
        bbg.close_session()
    print("  ir-check done")


def _last_cached_date(isin):
    p = os.path.join(CACHE, "daily", f"{isin}.parquet")
    if not os.path.exists(p):
        return None
    d = pd.read_parquet(p)
    return d.index.max() if len(d) else None


def _append_daily(isin, rows):
    """Merge freshly-pulled RECENT rows into a bond's existing daily cache: new values win on the
    overlap, older rows and extra columns (INDEX_RATIO from ircheck) are preserved."""
    if not rows:
        return 0
    nd = pd.DataFrame(rows); nd["date"] = pd.to_datetime(nd["date"]); nd = nd.set_index("date").sort_index()
    p = os.path.join(CACHE, "daily", f"{isin}.parquet")
    if os.path.exists(p):
        nd = nd.combine_first(pd.read_parquet(p)).sort_index()
    nd.to_parquet(p)
    return len(nd)


def update():
    """Incremental daily refresh -- cheap, run daily. macro (tiny) + any brand-new bonds, then
    TOP UP each live bond from its last cached date forward (a few rows), NOT a full re-pull.
    Matured & excluded (deferred / non-traded) bonds are left frozen."""
    print("1) macro (indices + financing)"); pull_macro()
    print("2) universe"); linkers.build_universe()
    print("3) new bonds (skip existing)"); pull_bonds(skip_existing=True)
    u = linkers.load_universe()                              # active only (excludes deferred/non-traded)
    live = u[pd.to_datetime(u["maturity"], errors="coerce") > pd.Timestamp.today()]
    print(f"4) top up {len(live)} live bonds (incremental, from last cached date)")
    bbg.open_session()
    try:
        for i in live["isin"]:
            last = _last_cached_date(i)
            if last is None:
                continue                                    # brand-new bond already pulled in step 3
            start = (last - pd.Timedelta(days=7)).strftime("%Y%m%d")   # small overlap, no chunking
            try:
                _append_daily(i, _history_one(f"{i} {linkers.BBG_SUFFIX}", DAILY_FIELDS, start, TODAY,
                                              chunk_years=0))
            except Exception as e:
                print(f"   {i} top-up failed ({e}) — keeping cached", flush=True)
    finally:
        bbg.close_session()
    print("update done")


def preview(isin=None, rows=20):
    _ensure_dirs()
    with pd.option_context("display.max_rows", max(rows, 40), "display.width", 200):
        if isin is None:
            mp = os.path.join(CACHE, "macro.parquet")
            if os.path.exists(mp):
                m = _macro()
                print("=== reference indices (last 12 monthly) ===")
                idx_cols = [k for k in linkers.REF_INDEX if k in m]
                print(m[idx_cols].dropna(how="all").tail(12).to_string())
                fin_cols = [k for k in linkers.FINANCING if k in m]
                print("\n=== financing (last 8 daily) ===")
                print(m[fin_cols].dropna(how="all").tail(8).to_string())
            u = linkers.load_universe(include_deferred=True)
            print(f"\n=== universe ({len(u)} bonds) ===")
            print(u.groupby(["country", "program"]).size().to_string())
            return
        sp = os.path.join(CACHE, "static", f"{isin}.parquet")
        dp = os.path.join(CACHE, "daily", f"{isin}.parquet")
        if os.path.exists(sp):
            print(f"=== STATIC {isin} ===\n{pd.read_parquet(sp).T.to_string()}")
        if os.path.exists(dp):
            d = pd.read_parquet(dp)
            print(f"\n=== DAILY {isin} ({len(d)} rows, {str(d.index.min())[:10]}..{str(d.index.max())[:10]}) ===")
            print(d.tail(rows).to_string())


def status():
    _ensure_dirs()
    mp = os.path.join(CACHE, "macro.parquet")
    print("macro.parquet:", "yes" if os.path.exists(mp) else "no")
    if os.path.exists(mp):
        print("  cols:", list(_macro().columns))
    print("static files:", len(os.listdir(os.path.join(CACHE, "static"))))
    print("daily files:", len(os.listdir(os.path.join(CACHE, "daily"))))
    print("universe:", len(linkers.load_universe(include_deferred=True)), "bonds")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "macro":
        pull_macro()
    elif cmd == "bonds":
        pull_bonds()
    elif cmd == "ircheck":
        pull_ir_check(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif cmd == "update":
        update()
    elif cmd == "preview":
        a2 = sys.argv[2] if len(sys.argv) > 2 else None
        preview(a2, int(sys.argv[3]) if len(sys.argv) > 3 else 20)
    elif cmd == "status":
        status()
    else:
        print(__doc__)
