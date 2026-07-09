"""
Phase 1 — master event matrix. One row per supply event, US + intl.

Sources (all cached already; no terminal):
  US   : breakeven_structures/cache/auctions_us.parquet (TIPS + nominal 5/10/30, internals,
         announcementDate) — anchors are true auction dates, anchor_quality='exact'.
  intl : cache_intl/auctions.parquet — anchor handling per PLAN.md §4.2 / config:
         dmo-sourced rows are true auction dates ('exact'); bbg/bbg_amt rows are
         settlement-dated (AMT_OUTSTANDING step) and are back-shifted by the market's
         standard settle lag ('approx'); syndications are anchored at pricing with
         anchor_quality='pricing_only' and are EXCLUDED from pre-event paths (Q5).

Contamination flags (t-T_PRE .. t+T_POST around the anchor):
  flag_cpi                CPI print day in window (US: exact dates if cached, else the
                          declared day-10..15 rule; intl: NaN until v2 calendars exist)
  flag_overlap_supply     another same-market (US: same-leg) event in window
  flag_nominal_same_tenor US TIPS only: nominal same-tenor auction in window (Q2b)
  flag_index_entry        an index-entry date (any same-market bond) in window
  flag_month_end_settle   month-end within +/-2bd of SETTLE (documented deviation: the
                          draft's "month-end in window" flags ~every event since a +/-10bd
                          window nearly always spans a month-end; the risk being flagged is
                          index extension coinciding with settlement)

Split label: in_holdout = anchor past the chronological TRAIN_FRAC quantile per market
(directive 2: holdout-era placebos reported separately — the label makes that mechanical).

Output: cache/events.parquet, reports/event_counts.csv, reports/data_quality.csv
Usage:  python -m breakeven_structures.data_events [build|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config, data_auctions_us, data_calendar

OUT = os.path.join(config.CACHE, "events.parquet")
BD = pd.offsets.BDay

COLS = ["market", "leg", "bond_id", "event_type", "is_reopening", "anchor_date",
        "anchor_quality", "announce_date", "announce_gap_bd", "settle_date",
        "maturity", "remaining_y", "bucket", "size_announced", "size_accepted",
        "supply_dv01_mm_bp", "bid_to_cover", "tail_median_bp", "dealer_pct",
        "indirect_pct", "direct_pct", "stop_yield", "source"]


def _us_supply_dv01(r) -> float:
    """$mm per bp of the accepted amount, in-house bump-and-reprice (pricing.py).
    ir=1.0 declared approximation (index ratio at issue ~1; reopenings slightly above)."""
    if pd.isna(r.highYield) or pd.isna(r.interestRate) or pd.isna(r.maturityDate) \
            or pd.isna(r.issueDate) or pd.isna(r.totalAccepted):
        return np.nan
    try:
        import pricing
        d = pricing.risk_dv01(r.issueDate, r.maturityDate, r.interestRate,
                              r.highYield, ir=1.0)   # pricing.py takes ytm in PERCENT
        return float(r.totalAccepted) / 100.0 * d["dv01_per_1bp"] / 1e6
    except Exception as e:
        print(f"  WARN supply_dv01 failed for {r.cusip} @ {r.auctionDate}: {e}")
        return np.nan


def build_us() -> pd.DataFrame:
    a = data_auctions_us.load()
    ev = pd.DataFrame({
        "market": "US", "leg": a.leg, "bond_id": a.cusip, "event_type": "auction",
        "is_reopening": a.is_reopening, "anchor_date": a.auctionDate,
        "anchor_quality": "exact", "announce_date": a.announcementDate,
        "announce_gap_bd": a.announce_gap_bd, "settle_date": a.issueDate,
        "maturity": a.maturityDate,
        "remaining_y": (a.maturityDate - a.auctionDate).dt.days / 365.25,
        "size_announced": a.offeringAmount, "size_accepted": a.totalAccepted,
        "bid_to_cover": a.bidToCoverRatio, "tail_median_bp": a.tail_median_bp,
        "dealer_pct": a.dealer_pct, "indirect_pct": a.indirect_pct,
        "direct_pct": a.direct_pct, "stop_yield": a.highYield, "source": "treasurydirect",
    })
    ev["bucket"] = ev.remaining_y.map(config.bucket_of)
    ev["supply_dv01_mm_bp"] = [_us_supply_dv01(r) for r in a.itertuples()]
    return ev


def build_intl() -> pd.DataFrame:
    raw = pd.read_parquet(os.path.join(config.INTL_CACHE, "auctions.parquet"))
    for c in ("event_date", "settle_date"):
        raw[c] = pd.to_datetime(raw[c], errors="coerce")
    raw["amount"] = pd.to_numeric(raw["amount"], errors="coerce")
    raw = raw[raw.country.isin(config.V2_MARKETS + config.POOL_ONLY)].copy()

    uni = pd.read_csv(os.path.join(config.INTL_CACHE, "universe.csv"),
                      parse_dates=["maturity"])
    mat_of = uni.set_index("isin")["maturity"].to_dict()

    rows = []
    for r in raw.itertuples():
        et = str(r.event_type).lower()
        if et == "syndication":
            anchor, quality = r.event_date, "pricing_only"
        elif str(r.source).lower() == "dmo" or et == "auction":
            anchor, quality = r.event_date, "exact"
        else:  # bbg static issue / bbg_amt step: settlement-dated -> back-shift
            anchor = r.event_date - BD(config.SETTLE_BACKSHIFT_BD.get(r.country, 2))
            quality = "approx"
        mat = mat_of.get(r.isin, pd.NaT)
        rem = (mat - anchor).days / 365.25 if pd.notna(mat) else np.nan
        rows.append({
            "market": r.country, "leg": "linker", "bond_id": r.isin,
            "event_type": "syndication" if et == "syndication"
                          else ("new_issue" if et == "issue" else "reopening"),
            "is_reopening": et in ("reopening", "tap", "auction"),
            "anchor_date": anchor, "anchor_quality": quality,
            "announce_date": pd.NaT, "announce_gap_bd": np.nan,
            "settle_date": r.settle_date if pd.notna(r.settle_date) else r.event_date,
            "maturity": mat, "remaining_y": rem,
            "size_announced": np.nan, "size_accepted": r.amount,
            "supply_dv01_mm_bp": np.nan,       # joined from quotes in Phase 2
            "bid_to_cover": np.nan, "tail_median_bp": np.nan, "dealer_pct": np.nan,
            "indirect_pct": np.nan, "direct_pct": np.nan,
            "stop_yield": getattr(r, "yield", np.nan), "source": r.source,
        })
    ev = pd.DataFrame(rows)
    ev["bucket"] = ev.remaining_y.map(lambda y: config.bucket_of(y) if pd.notna(y) else None)

    # dedupe: same isin observed twice within DEDUPE_WINDOW_CD (e.g. DMO auction row +
    # bbg_amt settle row, or bbg issue row + syndication row) -> keep best anchor quality
    ev["_q"] = ev.anchor_quality.map(config.ANCHOR_QUALITY_RANK)
    # syndication outranks a same-event plain 'new_issue' row despite worse anchor quality
    ev.loc[ev.event_type == "syndication", "_q"] -= 3
    ev = ev.sort_values(["bond_id", "_q", "anchor_date"])
    keep = []
    for isin, g in ev.groupby("bond_id", sort=False):
        kept_dates: list[pd.Timestamp] = []
        for r in g.itertuples():
            if all(abs((r.anchor_date - d).days) > config.DEDUPE_WINDOW_CD for d in kept_dates):
                keep.append(r.Index)
                kept_dates.append(r.anchor_date)
    ev = ev.loc[sorted(keep)].drop(columns="_q")
    return ev


def _window_flags(ev: pd.DataFrame) -> pd.DataFrame:
    lo = ev.anchor_date - BD(config.T_PRE)
    hi = ev.anchor_date + BD(config.T_POST)

    # CPI prints (US only until intl calendars exist in v2)
    start, end = ev.anchor_date.min() - pd.Timedelta(days=40), ev.anchor_date.max() + pd.Timedelta(days=40)
    prints, cpi_src = data_calendar.cpi_print_days(start, end)
    pv = prints.values
    is_us = (ev.market == "US").values
    ev["flag_cpi"] = [
        bool(((pv >= l.to_datetime64()) & (pv <= h.to_datetime64())).any()) if u else None
        for l, h, u in zip(lo, hi, is_us)]
    ev["cpi_flag_source"] = np.where(is_us, cpi_src, None)

    # overlapping supply in the same market (US: same leg), excluding self
    anchors = {}
    for key, g in ev.groupby(["market", "leg"]):
        anchors[key] = g.anchor_date.values
    flag_ov = []
    for r, l, h in zip(ev.itertuples(), lo, hi):
        arr = anchors[(r.market, r.leg)]
        inside = (arr >= l.to_datetime64()) & (arr <= h.to_datetime64())
        flag_ov.append(bool(inside.sum() > 1))          # >1 because self is inside
    ev["flag_overlap_supply"] = flag_ov

    # US TIPS: nominal same-tenor supply in window (Q2b)
    nom = ev[(ev.market == "US") & (ev.leg == "nominal")]
    nom_by_bucket = {b: g.anchor_date.values for b, g in nom.groupby("bucket")}
    flag_nom = []
    for r, l, h in zip(ev.itertuples(), lo, hi):
        if r.market != "US" or r.leg != "tips" or r.bucket not in nom_by_bucket:
            flag_nom.append(None if r.market != "US" or r.leg != "tips" else False)
            continue
        arr = nom_by_bucket[r.bucket]
        flag_nom.append(bool(((arr >= l.to_datetime64()) & (arr <= h.to_datetime64())).any()))
    ev["flag_nominal_same_tenor"] = flag_nom

    # index-entry date (any same-market bond) in window
    try:
        entry = data_calendar.load_index_entry()
        entry_by_mkt = {m: g.entry_date.dropna().values for m, g in entry.groupby("market")}
    except FileNotFoundError:
        entry_by_mkt = {}
    ev["flag_index_entry"] = [
        bool(((entry_by_mkt.get(r.market, np.array([], dtype="M8[ns]")) >= l.to_datetime64())
              & (entry_by_mkt.get(r.market, np.array([], dtype="M8[ns]")) <= h.to_datetime64())).any())
        if r.market in entry_by_mkt else None
        for r, l, h in zip(ev.itertuples(), lo, hi)]

    # month-end within +/-2bd of SETTLE (documented deviation from the draft's
    # month-end-in-window, which flags ~every event)
    def _me_near(settle):
        if pd.isna(settle):
            return None
        me = settle + pd.offsets.MonthEnd(0)
        prev_me = me if me <= settle + BD(2) else settle - pd.offsets.MonthEnd(1)
        return bool(abs(len(pd.bdate_range(min(settle, prev_me), max(settle, prev_me))) - 1) <= 2
                    or abs(len(pd.bdate_range(min(settle, me), max(settle, me))) - 1) <= 2)
    ev["flag_month_end_settle"] = ev.settle_date.map(_me_near)
    return ev


def build():
    config.ensure_dirs()
    ev = pd.concat([build_us(), build_intl()], ignore_index=True)
    ev = ev.dropna(subset=["anchor_date"]).sort_values(["market", "anchor_date"]).reset_index(drop=True)
    ev = _window_flags(ev)

    # chronological split label per market x leg (directive 2) — per-leg so the dense
    # nominal calendar can't drag the TIPS cut early
    ev["in_holdout"] = False
    for (m, l), g in ev.groupby(["market", "leg"]):
        cut = g.anchor_date.quantile(config.TRAIN_FRAC)
        ev.loc[g.index[g.anchor_date > cut], "in_holdout"] = True

    ev.to_parquet(OUT)
    print(f"  wrote {OUT}: {len(ev)} events")

    counts = (ev.groupby(["market", "leg", "bucket", "event_type", "is_reopening"])
                .agg(n=("anchor_date", "size"),
                     first=("anchor_date", "min"), last=("anchor_date", "max"),
                     n_holdout=("in_holdout", "sum"))
                .reset_index())
    counts.to_csv(os.path.join(config.REPORTS, "event_counts.csv"), index=False)

    dq = (ev.assign(approx=ev.anchor_quality.eq("approx"),
                    pricing_only=ev.anchor_quality.eq("pricing_only"),
                    no_amount=ev.size_accepted.isna())
            .groupby(["market", "bucket"])
            .agg(n=("anchor_date", "size"), approx_share=("approx", "mean"),
                 pricing_only_share=("pricing_only", "mean"),
                 no_amount_share=("no_amount", "mean"))
            .round(3).reset_index())
    dq.to_csv(os.path.join(config.REPORTS, "data_quality.csv"), index=False)

    print(counts.groupby(["market", "leg"])["n"].sum().to_string())
    print("\nanchor quality shares:")
    print(ev.groupby(["market", "anchor_quality"]).size().to_string())
    return ev


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_structures.data_events build")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    else:
        ev = load()
        print(f"{len(ev)} events; buckets under MIN_BUCKET_EVENTS:")
        c = ev.groupby(["market", "leg", "bucket", "event_type"]).size()
        print(c[c < config.MIN_BUCKET_EVENTS].to_string())
