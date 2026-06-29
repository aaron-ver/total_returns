"""
(Pseudo-)constant-maturity bucket return series for the linkers — the desk's breakeven product,
rolled to a fixed curve point (boss spec, 2026-06). Mirrors the US per-tenor breakeven sheet
(export.tenor_full): each bucket is one constant-maturity point whose held bond rolls over time.

A SELECTION + SPLICE layer over the per-bond/per-pair returns — it does NOT recompute returns.

Roll rule (boss-approved "forward-only by vintage"):
  * Buckets = remaining-tenor bands (buckets_intl ranges; FR/UK split the long end 30/40/50).
  * Each bucket holds, each month, the bond in its band that was ISSUED most recently (latest
    first-auction date) — i.e. the band's on-the-run — using issues known as of the PRIOR month-end
    (1-month roll lag). So the bucket rolls FORWARD to a newer line the month after it appears, and
    NEVER reverts to an older-vintage line on a stray re-tap (kills the "wobble").
  * Age-out: when the held bond drops below the band it leaves the candidate set, so the bucket
    switches to the newest-vintage bond REMAINING in the band (a 25y rolled to ~20y becomes the 20y
    reference iff nothing fresher exists). Buckets are judged independently — a bond rolling from 12y
    into 10y becomes the 10y reference only if it is the newest in the 10y band.
  * Gap = empty/flat: when NO bond is in the band, the bucket holds nothing and the daily return is
    0 (cum runs flat) for that span (boss: "leave the bucket empty ... returns flat when there
    aren't any").

The nominal hedge is CONTEMPORANEOUS (boss 2026-06): re-picked each month as the nominal closest in
maturity to the held linker among those THEN existing (pool = all street comparators, per country),
NOT the single fixed street comparator — so breakeven history isn't truncated by a comparator issued
after the holding. The per-pair files in exports/linker_breakevens/ keep the desk's fixed comparator.

Each bucket file follows the US breakeven format (linker_ = inflation-linked leg, nominal_ = the
nominal hedge leg, both DV01-normalized; r_BE = r_linker - beta*r_nominal, beta=1), spliced across rolls, plus:
  * linker_isin / nominal_isin   — the held pair each day (the ticker; changes on a roll)
  * is_roll_day / held_roll_from — the bucket switched its held linker that day (the roll), and from what
  * is_auction_date + auction_isin / auction_amount / auction_is_held — an in-BAND auction (new issue
    OR tap) occurred that day. Per the desk, an auction date is flagged for the bucket EVEN IF the
    held bond did not switch (multiple linkers can be auctioned in one month, so a bucket can show
    several auction days/month — handle each as an event in the auction-cycle analysis).

Output: exports/cmt/<MARKET>/<bucket>.csv (one folder per market) + _README.md + per-market summary;
caches to cache_intl/cmt/<MARKET>__<bucket>.parquet.

Usage:
  python cmt_intl.py                 # build all market/bucket series -> exports/cmt/ + caches
  python cmt_intl.py IT_BTPEI 10y    # one bucket: roll sequence + return/auction summary
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

import linkers
import issuance_intl as iss
import buckets_intl as bk
import engine_intl as eng
import breakeven_intl as be
import nominals_intl as nom

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
CMT_DIR = os.path.join(CACHE, "cmt")
EXPORTS = os.path.join(HERE, "exports")

# Per-day columns copied straight from the held pair's breakeven frame (US-style leg detail).
SRC_COLS = be.FULL_COLS                       # settlement_date..cum_BE_bp, is_coupon_day, is_weekend_step
CMT_COLS = (["bucket"] + SRC_COLS +
            ["nominal_mat_gap_y", "is_roll_day", "held_roll_from",
             "is_auction_date", "auction_isin", "auction_amount", "auction_is_held"])


# ---------------------------------------------------------------- selection (the roll) -----------
def held_monthly(market):
    """{bucket: Series(index=month Period -> held linker ISIN or NA)} under forward-only-by-vintage."""
    fr = market in bk.FR_STYLE
    u = linkers.load_universe(include_deferred=True)
    matof = {i: pd.Timestamp(m) for i, m in zip(u["isin"], pd.to_datetime(u["maturity"]))}
    ev = iss.events(); ev = ev[ev["market"] == market]
    if ev.empty:
        return {}
    first_issue = {i: pd.Timestamp(d) for i, d in ev.groupby("isin")["event_date"].min().items()
                   if i not in linkers.NON_TRADED}
    isins = list(first_issue)
    months = pd.period_range(ev["event_date"].min().to_period("M"),
                             pd.Timestamp.today().to_period("M"), freq="M")
    out = {b: {} for b in bk.ORDER}
    for mp in months:
        mstart = mp.to_timestamp()                              # first day of the month
        prev_end = mstart - pd.Timedelta(days=1)               # prior month-end (1-month roll lag)
        for b in bk.ORDER:
            best = None                                        # (first_issue, maturity, isin) -> pick LATEST
            for i in isins:
                mat, fi = matof.get(i), first_issue[i]
                if pd.isna(mat) or mat < mstart or fi > prev_end:
                    continue                                   # matured, or not yet issued as of prior m-e
                if bk.bucket((mat - mstart).days / 365.25, fr) != b:
                    continue                                   # not in this remaining-tenor band now
                cand = (fi, mat, i)
                if best is None or cand > best:                # newest vintage (tiebreak: longer maturity)
                    best = cand
            if best is not None:
                out[b][mp] = best[2]
    held = {}
    for b in bk.ORDER:
        s = pd.Series(out[b], dtype=object).reindex(months)
        if s.notna().any():
            held[b] = s
    return held


# ---------------------------------------------------------------- per-bond source frames ---------
# engine nominal-leg column -> output nominal_ column
NOM_MAP = {"clean": "nominal_clean", "yield": "nominal_yield", "accrued": "nominal_accrued",
           "dirty_real": "nominal_dirty", "V": "V_nominal", "V_prev": "V_nominal_prev",
           "notional": "nominal_notional", "denom": "nominal_DV01", "dV": "nominal_dV",
           "coupon": "nominal_coupon", "financing": "nominal_financing", "gross_bp": "nominal_gross_bp",
           "fin_bp": "nominal_fin_bp", "bp": "r_nominal_bp"}

_STAT = {}
def _stat(isin, field):
    if isin not in _STAT:
        p = os.path.join(CACHE, "static", f"{isin}.parquet")
        _STAT[isin] = pd.read_parquet(p).iloc[0].to_dict() if os.path.exists(p) else {}
    return _STAT[isin].get(field)


_OUT = {}
def _outright_full(linker_isin):
    """Linker leg over its FULL engine history, mapped into the linker_ columns (nominal/BE blank)."""
    if linker_isin in _OUT:
        return _OUT[linker_isin]
    u = linkers.load_universe(include_deferred=True); row = u[u["isin"] == linker_isin]
    if row.empty:
        _OUT[linker_isin] = pd.DataFrame(); return _OUT[linker_isin]
    r = eng.bond_series(linker_isin, row.iloc[0]["market"])
    if r.empty:
        _OUT[linker_isin] = pd.DataFrame(); return _OUT[linker_isin]
    o = pd.DataFrame(index=r.index); o.index.name = "date"
    o["settlement_date"] = r["settle"]; o["d"] = r["days"]; o["gc_repo"] = r["gc"]
    o["linker_isin"] = linker_isin; o["linker_clean"] = r["clean"]; o["linker_yield"] = r["yield"]
    o["linker_accrued"] = r["accrued"]; o["linker_IR"] = r["IR"]; o["linker_IR_bbg"] = r["IR_bbg"]
    o["linker_dirty"] = r["dirty_real"]; o["V_linker"] = r["V"]; o["V_linker_prev"] = r["V_prev"]
    o["linker_notional"] = r["notional"]; o["linker_DV01"] = r["denom"]; o["linker_dV"] = r["dV"]
    o["linker_coupon"] = r["coupon"]; o["linker_financing"] = r["financing"]
    o["linker_gross_bp"] = r["gross_bp"]; o["linker_fin_bp"] = r["fin_bp"]; o["r_linker_bp"] = r["bp"]
    o["is_coupon_day"] = r["coupon"] != 0
    _OUT[linker_isin] = o.reindex(columns=SRC_COLS)
    return _OUT[linker_isin]


_NOMF = {}
def _nominal_frame(isin, country):
    """DV01-normalized nominal-leg return frame (engine), cached."""
    if isin not in _NOMF:
        try:
            _NOMF[isin] = eng.nominal_series(isin, country)
        except Exception:
            _NOMF[isin] = pd.DataFrame()
    return _NOMF[isin]


def _has_daily(isin):
    return os.path.exists(os.path.join(CACHE, "daily", f"{isin}.parquet"))


_POOL = {}
def _country_nominal_pool(country):
    """Candidate nominal hedges for a country with maturity/issue. Prefers the COMPREHENSIVE nominal
    universe (nominals_intl: full Security-Finder bullet list, so the matcher can find the genuinely
    closest-maturity nominal at any date); falls back to the street comparators if that isn't pulled
    yet. Only bonds with daily data cached are eligible (cheap parquet check; frames built lazily)."""
    if country in _POOL:
        return _POOL[country]
    out = []
    u = nom.load()
    if not u.empty:
        for _, r in u[u["country"] == country].iterrows():
            if not _has_daily(r["isin"]):
                continue
            ind = _stat(r["isin"], "INFLATION_LINKED_INDICATOR")     # belt-and-suspenders: never a linker
            if isinstance(ind, str) and ind.strip().upper().startswith("Y"):
                continue
            mat, iss = r["maturity"], r["issue_date"]
            if pd.isna(iss):
                iss = pd.to_datetime(_stat(r["isin"], "ISSUE_DT"), errors="coerce")
            if pd.notna(mat) and pd.notna(iss):
                out.append({"isin": r["isin"], "mat": pd.Timestamp(mat), "iss": pd.Timestamp(iss)})
    if not out:                                                # fall back to the street comparator pool
        for ni in pd.unique(be.load_map()["nominal_isin"]):
            if be._nominal_country(ni) != country:
                continue
            mat = pd.to_datetime(_stat(ni, "MATURITY"), errors="coerce")
            iss = pd.to_datetime(_stat(ni, "ISSUE_DT"), errors="coerce")
            if pd.notna(mat) and pd.notna(iss) and _has_daily(ni):
                out.append({"isin": ni, "mat": mat, "iss": iss})
    _POOL[country] = out
    return out


def _pick_nominal(country, lmat, mstart, prev_end):
    """The CONTEMPORANEOUS hedge: the nominal closest in maturity to the linker, among those already
    issued (as of prior month-end) and not yet matured (at month start). None if the pool is empty."""
    if pd.isna(lmat):
        return None
    cands = [n for n in _country_nominal_pool(country) if n["iss"] <= prev_end and n["mat"] > mstart]
    return min(cands, key=lambda n: abs((n["mat"] - lmat).days))["isin"] if cands else None


# ---------------------------------------------------------------- auction calendar ---------------
def _auctions_by_bucket(market, fr):
    """Every auction/tap of the market, tagged with the bucket of its remaining tenor at the event."""
    u = linkers.load_universe(include_deferred=True)
    matof = {i: pd.Timestamp(m) for i, m in zip(u["isin"], pd.to_datetime(u["maturity"]))}
    e = iss.events(); e = e[e["market"] == market].copy()
    e["mat"] = e["isin"].map(matof)
    e["rem"] = (e["mat"] - pd.to_datetime(e["event_date"])).dt.days / 365.25
    e["bkt"] = e["rem"].apply(lambda y: bk.bucket(y, fr))
    e["nd"] = pd.to_datetime(e["event_date"]).dt.normalize()
    return e[["nd", "isin", "amt_bn", "bkt"]].dropna(subset=["bkt"])


# ---------------------------------------------------------------- build one bucket ---------------
def build_bucket(market, b, held_s, grid, auc, country, matof):
    """Splice the held linker leg (full history) + the CONTEMPORANEOUS nominal hedge (re-picked each
    month = closest-maturity nominal then existing) into the US breakeven format over a daily grid."""
    per = pd.Series(pd.Series(held_s).reindex(grid.to_period("M")).to_numpy(), index=grid)  # held linker/day
    fv = per.first_valid_index()
    if fv is None:
        return pd.DataFrame()
    grid = grid[grid >= fv]; per = per.loc[grid]
    # LINKER leg (full per-bond history, spliced across rolls)
    parts = []
    for isin in pd.unique(per.dropna()):
        src = _outright_full(isin)
        if src.empty:
            continue
        parts.append(src.reindex(grid[per.to_numpy() == isin]))
    out = (pd.concat(parts).reindex(grid) if parts else pd.DataFrame(index=grid)).reindex(columns=SRC_COLS)
    out.index.name = "date"
    out.insert(0, "bucket", b)
    out["linker_isin"] = per.to_numpy()                        # authoritative held ticker (NA on gap days)
    # NOMINAL leg: pick the time-appropriate hedge per month (closest maturity to the held linker)
    sel = {}
    for mp, lisin in pd.Series(held_s).dropna().items():
        ms = mp.to_timestamp()
        ni = _pick_nominal(country, matof.get(lisin), ms, ms - pd.Timedelta(days=1))
        if ni:
            sel[mp] = ni
    nom_per = pd.Series(pd.Series(sel, dtype=object).reindex(grid.to_period("M")).to_numpy(), index=grid)
    out["nominal_isin"] = nom_per.to_numpy()
    nmat = {}
    for ni in pd.unique(nom_per.dropna()):
        fr = _nominal_frame(ni, country)
        if fr.empty:
            continue
        common = grid[nom_per.to_numpy() == ni].intersection(fr.index)
        for src_c, dst_c in NOM_MAP.items():
            if src_c in fr.columns:
                out.loc[common, dst_c] = fr.loc[common, src_c].to_numpy()
        nmat[ni] = pd.to_datetime(_stat(ni, "MATURITY"), errors="coerce")
    # breakeven (beta = 1.0 equal-DV01); NaN where no nominal that day -> BE runs flat there
    lfin = pd.to_numeric(out["linker_fin_bp"], errors="coerce"); nfin = pd.to_numeric(out["nominal_fin_bp"], errors="coerce")
    out["beta"] = 1.0
    out["net_fin_bp"] = lfin - nfin
    out["r_BE_bp"] = pd.to_numeric(out["r_linker_bp"], errors="coerce") - pd.to_numeric(out["r_nominal_bp"], errors="coerce")
    lmat_d = pd.to_datetime(pd.Series(per.to_numpy(), index=grid).map(matof))
    nmat_d = pd.to_datetime(pd.Series(nom_per.to_numpy(), index=grid).map(nmat))
    out["nominal_mat_gap_y"] = ((nmat_d.to_numpy() - lmat_d.to_numpy()) / np.timedelta64(1, "D")) / 365.25
    # cumulatives recomputed over the SPLICED daily returns; gap/no-data days = 0 (flat)
    for r_col, c_col in [("r_linker_bp", "cum_linker_bp"), ("r_nominal_bp", "cum_nominal_bp"),
                         ("r_BE_bp", "cum_BE_bp")]:
        out[c_col] = pd.to_numeric(out[r_col], errors="coerce").fillna(0).cumsum()
    # roll flags
    roll = (per != per.shift()) & per.notna()
    out["is_roll_day"] = roll.to_numpy()
    out["held_roll_from"] = per.shift().where(roll).to_numpy()
    lc = pd.to_numeric(out["linker_coupon"], errors="coerce").fillna(0) != 0
    nc = pd.to_numeric(out["nominal_coupon"], errors="coerce").fillna(0) != 0
    out["is_coupon_day"] = (lc | nc).to_numpy()                # coupon on either leg
    out["is_weekend_or_holiday_step"] = pd.to_numeric(out["d"], errors="coerce").fillna(0) > 1
    # auction flags for THIS bucket (in-band auctions; flagged even with no roll)
    ab = auc[auc["bkt"] == b]
    iso = ab.groupby("nd")["isin"].apply(lambda s: ";".join(sorted(set(s))))
    amt = ab.groupby("nd")["amt_bn"].sum()
    aset = set(iso.index)
    nd = out.index.normalize()
    out["is_auction_date"] = nd.isin(aset)
    out["auction_isin"] = [iso.get(d, "") for d in nd]
    out["auction_amount"] = [round(amt.get(d), 3) if d in amt.index and pd.notna(amt.get(d)) else np.nan for d in nd]
    out["auction_is_held"] = [bool(pd.notna(h) and d in aset and h in iso.get(d, "").split(";"))
                              for d, h in zip(nd, out["linker_isin"].to_numpy())]
    return out.reindex(columns=CMT_COLS)


# ---------------------------------------------------------------- build a market -----------------
def build_market(market, save=True):
    held = held_monthly(market)
    if not held:
        return {}
    fr = market in bk.FR_STYLE
    auc = _auctions_by_bucket(market, fr)
    u = linkers.load_universe(include_deferred=True)
    matof = {i: pd.Timestamp(m) for i, m in zip(u["isin"], pd.to_datetime(u["maturity"]))}
    cser = u[u["market"] == market]["country"]
    country = cser.iloc[0] if len(cser) else None
    isins = set().union(*[set(s.dropna().unique()) for s in held.values()])
    idxs = [src.index for src in (_outright_full(i) for i in isins) if not src.empty]
    if not idxs:
        return {}
    grid = pd.DatetimeIndex(sorted(set().union(*[set(ix) for ix in idxs])))
    folder = os.path.join(EXPORTS, "cmt", market)
    if save:
        os.makedirs(folder, exist_ok=True); os.makedirs(CMT_DIR, exist_ok=True)
    res, locked = {}, []
    for b in bk.ORDER:
        if b not in held:
            continue
        df = build_bucket(market, b, held[b], grid, auc, country, matof)
        if df.empty:
            continue
        res[b] = df
        if save:
            df.to_parquet(os.path.join(CMT_DIR, f"{market}__{b}.parquet"))
            try:
                df.to_csv(os.path.join(folder, f"{b}.csv"))
            except PermissionError:
                locked.append(b)
    if save and locked:
        print(f"  !! {market}: {len(locked)} CSV(s) locked (open in Excel?), not rewritten: {', '.join(locked)}")
    return res


def _summary_row(market, b, df):
    held = df[df["linker_isin"].notna()]
    be = df[df["r_BE_bp"].notna()]
    return {"market": market, "bucket": b, "names": df["linker_isin"].nunique(),
            "rolls": int(df["is_roll_day"].sum()), "auctions": int(df["is_auction_date"].sum()),
            "days_held": int(df["linker_isin"].notna().sum()),
            "first": str(held.index.min().date()) if len(held) else "",
            "last": str(held.index.max().date()) if len(held) else "",
            "cum_linker_bp": round(df["cum_linker_bp"].iloc[-1]) if len(df) else 0,
            "be_first": str(be.index.min().date()) if len(be) else "",   # where breakeven data begins
            "be_cov_pct": round(100 * len(be) / len(held)) if len(held) else 0,  # comparator depth
            "cum_BE_bp": round(df["cum_BE_bp"].iloc[-1]) if len(df) else 0}


def _write_readme():
    rows = [("--- CONSTANT-MATURITY ROLL (per file = one market, one bucket) ---", ""),
            ("bucket", "the constant-maturity point this file tracks (remaining-tenor band)"),
            ("linker_isin", "the inflation-linked bond HELD that day — the ticker; changes on a roll. "
                            "NA on gap days (no bond in band -> returns flat)"),
            ("is_roll_day", "the bucket switched its held linker that day (rolled to a newer-vintage in-band "
                            "line, or to the newest remaining line after the prior one aged out)"),
            ("held_roll_from", "the linker held immediately before the roll (blank off roll days)"),
            ("is_auction_date", "a linker whose REMAINING tenor sits in THIS bucket was auctioned that day "
                                "(new issue OR tap). Flagged EVEN IF the held bond did not switch — an "
                                "auction date is an event for the bucket regardless of rolls (desk rule). "
                                "Multiple linker auctions can fall in one month, so >1/month is expected."),
            ("auction_isin", "the in-bucket bond(s) auctioned that day (';'-joined if several)"),
            ("auction_amount", "summed offering amount that day (bn, local ccy), in-bucket auctions only"),
            ("auction_is_held", "True iff the bond auctioned that day is the one the bucket currently holds"),
            ("nominal_isin", "the CONTEMPORANEOUS nominal hedge held that day — re-picked each month as "
                             "the nominal CLOSEST IN MATURITY to the held linker among nominals THEN existing "
                             "(issued, not matured). NOT the fixed street comparator: this is the time-"
                             "appropriate hedge so breakeven history isn't truncated by a comparator that "
                             "post-dates the holding. Pool = all street comparators, pooled per country."),
            ("nominal_mat_gap_y", "maturity gap (yrs) between the chosen nominal hedge and the held linker "
                                  "(hedge match quality; ~0 = tight, large = sparse nominal pool that period)"),
            ("beta", "1.0 (equal-DV01 plain breakeven) for the contemporaneous hedge"),
            ("cum_linker_bp / cum_nominal_bp / cum_BE_bp", "running LINEAR sums recomputed over the SPLICED "
                                                           "series (flat across gap days); not the per-pair cum"),
            ("NOTE outright vs breakeven coverage", "the OUTRIGHT linker leg spans each held bond's full "
                "history. The BREAKEVEN leg uses the contemporaneous nominal hedge (above), so it now covers "
                "as far back as a similar-maturity nominal existed in the pool (see be_first / be_cov_pct in "
                "_summary.csv). Germany has no nominals in the pool -> outright only. NB the per-pair files in "
                "exports/linker_breakevens/ keep the desk's FIXED street comparator; the contemporaneous hedge "
                "is used only here, for the constant-maturity history."),
            ("--- LEG DETAIL (column mechanics shared with the linker breakeven per-pair files) ---", "")]
    rows += be.README_FULL_ROWS
    folder = os.path.join(EXPORTS, "cmt"); os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "_README.md"), "w", encoding="utf-8") as f:
        f.write("# Constant-maturity linker breakeven buckets\n\nOne folder per market; one CSV per "
                "remaining-tenor bucket. Each bucket is a fixed curve point whose held bond rolls "
                "forward to the band's newest-issued line (never reverting on a stray tap), leaving the "
                "bucket flat when the band is empty. Format mirrors the US per-tenor breakeven sheet.\n\n"
                "| column | description |\n|---|---|\n" + "\n".join(f"| {c} | {d} |" for c, d in rows) + "\n")


# ---------------------------------------------------------------- diagnostics (desk review) ------
def _d(isin, field):
    v = pd.to_datetime(_stat(isin, field), errors="coerce")
    return v.date() if pd.notna(v) else None


def hedge_mismatch_report(save=True, top=20):
    """Worst maturity mismatches in the contemporaneous hedge: each (linker, chosen nominal) segment
    used in the CMT, with both bonds' issue/maturity and the gap — the boss's 'when they were issued
    and the closest nominal at the time'. Sorted by |gap|. -> exports/cmt/_hedge_mismatch.csv."""
    rows = []
    for f in glob.glob(os.path.join(CMT_DIR, "*.parquet")):
        market, bkt = os.path.basename(f)[:-8].split("__")
        d = pd.read_parquet(f)
        d = d[d["nominal_isin"].notna() & d["linker_isin"].notna()]
        if d.empty:
            continue
        key = d["linker_isin"].astype(str) + "|" + d["nominal_isin"].astype(str)
        for _, g in d.groupby((key != key.shift()).cumsum()):           # each held linker/nominal pairing
            li, ni = g["linker_isin"].iloc[0], g["nominal_isin"].iloc[0]
            rows.append({"market": market, "bucket": bkt, "linker": li, "linker_issue": _d(li, "ISSUE_DT"),
                         "linker_mat": _d(li, "MATURITY"), "nominal": ni, "nominal_issue": _d(ni, "ISSUE_DT"),
                         "nominal_mat": _d(ni, "MATURITY"), "gap_y": round(float(g["nominal_mat_gap_y"].iloc[0]), 2),
                         "used_from": g.index.min().date(), "used_to": g.index.max().date(), "days": len(g)})
    df = pd.DataFrame(rows)
    if df.empty:
        print("  (no hedge segments)"); return df
    df = df.reindex(df["gap_y"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    if save:
        os.makedirs(os.path.join(EXPORTS, "cmt"), exist_ok=True)
        df.to_csv(os.path.join(EXPORTS, "cmt", "_hedge_mismatch.csv"), index=False)
    over3 = (df["gap_y"].abs() > 3).sum()
    print(f"=== HEDGE MATURITY MISMATCH — {len(df)} pairings, {over3} with |gap|>3y. Worst {top}: ===")
    print(df.head(top).to_string(index=False))
    return df


def multi_tap_report(save=True, top=20):
    """Confirm + rank multiple taps/issuance in one calendar month, two ways:
      (A) same bond tapped/issued 2+ times in a month  -> exports/cmt/_multi_tap_months.csv
      (B) a market's busiest months (2+ DIFFERENT linkers auctioned) -> exports/cmt/_busy_auction_months.csv
    Plus per-market density. (B) is the UK pattern you spotted (several lines auctioned per month)."""
    os.makedirs(os.path.join(EXPORTS, "cmt"), exist_ok=True)
    ev = iss.events().copy()
    ev["event_date"] = pd.to_datetime(ev["event_date"])
    ev["ym"] = ev["event_date"].dt.to_period("M")
    u = linkers.load_universe(include_deferred=True).set_index("isin")
    desc = lambda i: u.loc[i, "desc"] if i in u.index else ""
    # (A) same bond, >=2 events in one month
    sb = (ev.groupby(["market", "isin", "ym"])
            .agg(n=("event_date", "count"), types=("type", lambda s: ";".join(sorted(set(s)))),
                 amt_bn=("amt_bn", "sum"),
                 dates=("event_date", lambda s: ";".join(sorted(s.dt.strftime("%Y-%m-%d"))))).reset_index())
    sb = sb[sb["n"] >= 2].copy(); sb["desc"] = sb["isin"].map(desc); sb["ym"] = sb["ym"].astype(str)
    sb = sb.sort_values("n", ascending=False).reset_index(drop=True)
    # (B) market-month totals: events + distinct linkers auctioned
    mm = (ev.groupby(["market", "ym"])
            .agg(events=("event_date", "count"), bonds=("isin", "nunique"),
                 amt_bn=("amt_bn", "sum"),
                 isins=("isin", lambda s: ";".join(sorted(set(s))))).reset_index())
    busy = mm[mm["bonds"] >= 2].copy(); busy["ym"] = busy["ym"].astype(str)
    busy = busy.sort_values(["bonds", "events"], ascending=False).reset_index(drop=True)
    # (C) per-market density
    dens = (mm.groupby("market")
              .agg(auction_months=("ym", "size"), max_bonds_mo=("bonds", "max"),
                   months_2plus_bonds=("bonds", lambda s: int((s >= 2).sum()))).reset_index())
    dens["pct_months_2plus"] = (100 * dens["months_2plus_bonds"] / dens["auction_months"]).round().astype(int)
    dens = dens.sort_values("pct_months_2plus", ascending=False)
    if save:
        sb.to_csv(os.path.join(EXPORTS, "cmt", "_multi_tap_months.csv"), index=False)
        busy.to_csv(os.path.join(EXPORTS, "cmt", "_busy_auction_months.csv"), index=False)
    print("\n=== MULTIPLE TAPS / ISSUANCE IN ONE MONTH ===")
    print("per-market density (auction-months with >=2 DIFFERENT linkers auctioned):")
    print(dens.to_string(index=False))
    print(f"\n(A) SAME bond tapped >=2x in a month: {len(sb)} case(s)")
    if len(sb):
        print(sb.head(top)[["market", "isin", "desc", "ym", "n", "types", "dates"]].to_string(index=False))
    print(f"\n(B) busiest months — most DIFFERENT linkers auctioned in one month, worst {top}:")
    print(busy.head(top)[["market", "ym", "bonds", "events", "amt_bn"]].to_string(index=False))
    return sb, busy


def diagnostics():
    hedge_mismatch_report()
    multi_tap_report()
    print("\n  wrote exports/cmt/_hedge_mismatch.csv  and  exports/cmt/_multi_tap_months.csv")


def build_all():
    os.makedirs(os.path.join(EXPORTS, "cmt"), exist_ok=True)
    summ = []
    for m in bk.MARKETS:
        res = build_market(m)
        for b in bk.ORDER:
            if b in res:
                summ.append(_summary_row(m, b, res[b]))
    _write_readme()
    s = pd.DataFrame(summ)
    if len(s):
        s.to_csv(os.path.join(EXPORTS, "cmt", "_summary.csv"), index=False)
        print(s.to_string(index=False))
    print(f"\n  wrote per-bucket CSVs -> {os.path.join(EXPORTS, 'cmt')}/<market>/  (+ _README.md, _summary.csv)")
    print(f"  cached series -> {CMT_DIR}/<market>__<bucket>.parquet")
    return s


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "diag":
        diagnostics()
    elif len(sys.argv) > 2:
        m, b = sys.argv[1], sys.argv[2]
        held = held_monthly(m)
        u = linkers.load_universe(include_deferred=True).set_index("isin")
        print(f"=== {m} {b}: forward-only-by-vintage roll ===")
        if b in held:
            seq = held[b].dropna(); chg = seq[seq != seq.shift()]
            for mp, isin in chg.items():
                print(f"  {mp}  -> {isin}  {u.loc[isin, 'desc'] if isin in u.index else ''}")
            df = build_market(m, save=False).get(b)
            if df is not None:
                print("\n  " + str(_summary_row(m, b, df)))
        else:
            print("  (bucket never populated)")
    else:
        build_all()
