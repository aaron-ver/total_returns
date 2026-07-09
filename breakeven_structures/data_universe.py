"""
Phase 0 — full TIPS strip price history (the ONE terminal pull, Q3: prep first, pull once).

The engine's quote cache (breakeven_rv/cache/tips_bond_quotes.parquet) covers only
bonds that ever held an OTR/off-run role. Study B (fly RV) needs the NEIGHBORS and
WINGS of every new issue — the full strip. This module:

  dry-run : from cache, list the full-strip CUSIPs, what's already in cache/daily/,
            what's missing, and the exact batch plan for the terminal session.
  pull    : the terminal session itself (Bloomberg DAPI via root bbg.py). Pulls
            static + daily (PX_CLEAN_MID, PX_DIRTY_MID, YLD_YTM_MID, PX_LAST) for
            missing CUSIPs into the ROOT cache/daily|static layout (same files the
            engine uses; existing files are never overwritten). ~1s/bond.
  build   : consolidate the full strip into cache/bond_quotes_full.parquet with the
            tips_bond_quotes schema (date, cusip, leg, tenor, yld, px_clean,
            maturity, coupon). Runs from cache; reports coverage so a partial cache
            is visible, never silent.

The public-API halves of Phase 0 (announcementDate backfill, nominal internals) live
in data_auctions_us.py and need NO terminal.

Usage:  python -m breakeven_structures.data_universe [dry-run|pull|build|status]
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config

OUT = os.path.join(config.CACHE, "bond_quotes_full.parquet")
DAILY_DIR = os.path.join(config.ROOT_CACHE, "daily")
STATIC_DIR = os.path.join(config.ROOT_CACHE, "static")
DAILY_FIELDS = ["PX_CLEAN_MID", "PX_DIRTY_MID", "YLD_YTM_MID", "PX_LAST"]


def cusip_list() -> pd.DataFrame:
    """Every TIPS CUSIP alive on/after PULL_START, from the root auction calendar.
    One row per CUSIP: cusip, tenor, issueDate, maturityDate, coupon."""
    import auctions
    a = auctions.load_auctions()
    t = a[a.leg == "tips"].dropna(subset=["issueDate"]).sort_values("issueDate")
    t = t.drop_duplicates("cusip", keep="first")
    alive = t[t.maturityDate >= pd.Timestamp(config.PULL_START)]
    return (alive[["cusip", "tenor", "issueDate", "maturityDate", "interestRate"]]
            .rename(columns={"interestRate": "coupon"}).reset_index(drop=True))


def _cached(cusip: str) -> bool:
    return os.path.exists(os.path.join(DAILY_DIR, f"{cusip}.parquet"))


def _stale_live(u: pd.DataFrame, max_age_bd: int = 5) -> pd.DataFrame:
    """Cached bonds still alive whose last cached quote is older than max_age_bd.
    The daily engine update refreshes only the current OTRs, so live OFF-RUN files go
    stale between full pulls — the terminal session must re-pull them too."""
    today = pd.Timestamp.today().normalize()
    cutoff = today - pd.offsets.BDay(max_age_bd)
    rows = []
    for r in u.itertuples():
        p = os.path.join(DAILY_DIR, f"{r.cusip}.parquet")
        if not os.path.exists(p) or r.maturityDate <= today:
            continue
        last = pd.read_parquet(p, columns=[]).index.max()
        if last < cutoff:
            rows.append({"cusip": r.cusip, "tenor": r.tenor, "last_quote": last})
    return pd.DataFrame(rows, columns=["cusip", "tenor", "last_quote"])


def dry_run():
    u = cusip_list()
    u["cached"] = u.cusip.map(_cached)
    missing = u[~u.cached]
    stale = _stale_live(u)
    n_pull = len(missing) + len(stale)
    n_hist_batches = -(-n_pull // config.PULL_BATCH)
    n_stat_batches = -(-n_pull // config.PULL_STATIC_BATCH)
    print(f"full TIPS strip alive since {config.PULL_START[:4]}: {len(u)} CUSIPs")
    print(u.groupby("tenor").agg(n=("cusip", "size"), cached=("cached", "sum")).to_string())
    print(f"\nmissing from cache/daily: {len(missing)}")
    if len(missing):
        print(missing[["cusip", "tenor", "issueDate", "maturityDate"]].to_string(index=False))
    print(f"stale LIVE off-run bonds (engine update only refreshes OTRs): {len(stale)}")
    if len(stale):
        print(f"  oldest quote {stale.last_quote.min().date()}")
    print(f"\nterminal session plan: {n_stat_batches} static request(s) "
          f"(batch {config.PULL_STATIC_BATCH}) + {n_hist_batches} history request(s) "
          f"(batch {config.PULL_BATCH}, fields {DAILY_FIELDS}, start {config.PULL_START})")
    print(f"estimated wall time ~{max(1, n_pull)}s of request time (~1s/bond)")
    print("\nrun on the terminal box:  python -m breakeven_structures.run_all pull-terminal")
    return missing


def pull(skip_existing: bool = True):
    """TERMINAL ONLY. Pulls missing CUSIPs + re-pulls stale LIVE bonds (full history —
    cheap, and it keeps one code path). Matured bonds' files are never touched."""
    import bbg, data_layer
    u = cusip_list()
    missing = u if not skip_existing else u[~u.cusip.map(_cached)]
    stale = _stale_live(u)
    todo = pd.concat([missing[["cusip", "tenor"]], stale[["cusip", "tenor"]]]).drop_duplicates("cusip")
    if todo.empty:
        print("  full strip already cached and fresh — nothing to pull")
        return
    today = pd.Timestamp.today().strftime("%Y%m%d")
    bbg.open_session()
    try:
        cusips = todo.cusip.tolist()
        for k in range(0, len(cusips), config.PULL_STATIC_BATCH):
            chunk = [f"{c} Govt" for c in cusips[k:k + config.PULL_STATIC_BATCH]]
            smap = bbg.reference(chunk, data_layer.STATIC_FIELDS)
            for sec, st in smap.items():
                c = sec.split()[0]
                pd.DataFrame([{**{"cusip": c}, **st}]).to_parquet(
                    os.path.join(STATIC_DIR, f"{c}.parquet"))
        done = 0
        for k in range(0, len(cusips), config.PULL_BATCH):
            grp = cusips[k:k + config.PULL_BATCH]
            h = bbg.history([f"{c} Govt" for c in grp], DAILY_FIELDS,
                            config.PULL_START, today)
            for c in grp:
                n = data_layer._save_daily(c, h.get(f"{c} Govt", []))
                if not n:
                    print(f"  WARN {c}: no daily data")
            done += len(grp)
            print(f"  [{done}/{len(cusips)}] pulled", flush=True)
    finally:
        bbg.close_session()
    print(f"  done: {len(todo)} bonds -> {DAILY_DIR}")


def build():
    """Consolidate the strip from cache/daily into one long panel (partial cache OK,
    coverage reported loudly)."""
    config.ensure_dirs()
    u = cusip_list()
    frames, missing = [], []
    for r in u.itertuples():
        p = os.path.join(DAILY_DIR, f"{r.cusip}.parquet")
        if not os.path.exists(p):
            missing.append(r.cusip)
            continue
        d = pd.read_parquet(p)
        if "YLD_YTM_MID" not in d or d["YLD_YTM_MID"].dropna().empty:
            missing.append(r.cusip)
            continue
        frames.append(pd.DataFrame({
            "date": d.index, "cusip": r.cusip, "leg": "tips", "tenor": r.tenor,
            "yld": d.get("YLD_YTM_MID"), "px_clean": d.get("PX_CLEAN_MID"),
            "maturity": r.maturityDate, "coupon": r.coupon}))
    out = pd.concat(frames, ignore_index=True).dropna(subset=["yld"])
    out = out[out.date >= pd.Timestamp(config.PULL_START)]
    out.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(out):,} rows, {out.cusip.nunique()}/{len(u)} CUSIPs, "
          f"{out.date.min().date()} .. {out.date.max().date()}")
    if missing:
        print(f"  MISSING {len(missing)} CUSIPs (run the terminal pull): {missing}")
    return out


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_structures.data_universe build")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry-run"
    if cmd in ("dry-run", "dryrun"):
        dry_run()
    elif cmd == "pull":
        pull()
    elif cmd == "build":
        build()
    else:
        print(f"cached: {os.path.exists(OUT)}")
