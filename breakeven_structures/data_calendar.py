"""
Calendar layer: US CPI release dates + index-entry approximations.

CPI release dates (for the CPI-in-window dummy, directive 1: dummy, don't drop):
  Preference order, recorded in the output's `source` column:
    1. FRED release-dates API (release_id=10), needs a free key in env FRED_API_KEY —
       exact historical release dates back to the 1990s.
    2. Inbox CSV at breakeven_structures/inbox/cpi_release_dates.csv (column: release_date).
    3. RULE fallback: possible-print window = calendar days 10..15 of every month.
       Good enough for the window-contamination FLAG; NOT good enough for the
       print-day return dummy in Study A paths — that analysis stays blocked until
       1. or 2. lands (logged in IMPLEMENTATION.md).
  BLS pages are bot-blocked from this box (403, verified 2026-07-09) — not an option.

Index entry (Q6, declared approximation):
  US/GB: first month-end on/after the bond's first issue date.
  EUR (FR/IT/ES): new issues often launch below index-minimum size — entry = first
  month-end after cumulative outstanding (initial issue + taps, from the auctions
  parquets) crosses EUR_INDEX_MIN_OUT. Falls back to the simple rule (flagged
  entry_rule='issue_month_end_fallback') where amounts are missing.

Usage:  python -m breakeven_structures.data_calendar [pull|status]
"""
from __future__ import annotations
import os, sys, json
import urllib.request
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config

OUT_CPI = os.path.join(config.CACHE, "cpi_releases.parquet")
OUT_ENTRY = os.path.join(config.CACHE, "index_entry.parquet")


# --------------------------------------------------------------------------- CPI
def _fred_release_dates(api_key: str) -> pd.DataFrame:
    """All historical CPI release dates from the FRED API (paged, 1000/page)."""
    rows, offset = [], 0
    while True:
        url = (f"{config.FRED_RELEASE_URL}?release_id={config.CPI_RELEASE_ID}"
               f"&realtime_start=1990-01-01&realtime_end=9999-12-31"
               f"&include_release_dates_with_no_data=true"
               f"&api_key={api_key}&file_type=json&limit=1000&offset={offset}")
        with urllib.request.urlopen(url, timeout=60) as r:
            j = json.load(r)
        batch = j.get("release_dates", [])
        rows += [b["date"] for b in batch]
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame({"release_date": pd.to_datetime(sorted(set(rows)))})
    df["source"] = "fred_api"
    return df


def _fred_key() -> str:
    key = os.environ.get(config.FRED_API_KEY_ENV, "").strip()
    if not key and os.path.exists(config.FRED_API_KEY_FILE):
        with open(config.FRED_API_KEY_FILE) as f:
            key = f.read().strip()
    return key


def pull_cpi():
    config.ensure_dirs()
    key = _fred_key()
    if key:
        try:
            df = _fred_release_dates(key)
            df.to_parquet(OUT_CPI)
            print(f"  CPI releases from FRED API: {len(df)} dates "
                  f"({df.release_date.min().date()} .. {df.release_date.max().date()})")
            return df
        except Exception as e:
            print(f"  WARN FRED API failed ({e}); trying inbox")
    if os.path.exists(config.CPI_INBOX_CSV):
        df = pd.read_csv(config.CPI_INBOX_CSV)
        df["release_date"] = pd.to_datetime(df["release_date"])
        df = df[["release_date"]].drop_duplicates().sort_values("release_date")
        df["source"] = "inbox_csv"
        df.to_parquet(OUT_CPI)
        print(f"  CPI releases from inbox: {len(df)} dates")
        return df
    print("  NO exact CPI release dates (no FRED_API_KEY, no inbox CSV) — "
          "window flags will use the day-10..15 RULE; print-day dummies stay blocked.")
    if os.path.exists(OUT_CPI):
        os.remove(OUT_CPI)
    return None


def cpi_print_days(start, end) -> tuple[pd.DatetimeIndex, str]:
    """(days that count as CPI print days, source). Exact dates if cached, else the
    declared rule window (10th..15th of every month)."""
    if os.path.exists(OUT_CPI):
        df = pd.read_parquet(OUT_CPI)
        d = df["release_date"]
        return pd.DatetimeIndex(d[(d >= start) & (d <= end)]), df["source"].iloc[0]
    lo, hi = config.CPI_RULE_WINDOW_DOM
    days = pd.date_range(start, end, freq="D")
    return days[(days.day >= lo) & (days.day <= hi)], "rule_window"


# --------------------------------------------------------------------- index entry
def _month_end_on_after(d: pd.Timestamp) -> pd.Timestamp:
    me = d + pd.offsets.MonthEnd(0)
    return me if me >= d else d + pd.offsets.MonthEnd(1)


def build_index_entry():
    """One row per bond: (market, bond_id, entry_date, entry_rule)."""
    config.ensure_dirs()
    rows = []

    # US: simple rule off the auctions_us pull
    from breakeven_structures import data_auctions_us
    us = data_auctions_us.load()
    first = (us[us.leg == "tips"].dropna(subset=["issueDate"])
             .sort_values("issueDate").drop_duplicates("cusip", keep="first"))
    for _, r in first.iterrows():
        rows.append({"market": "US", "bond_id": r.cusip,
                     "entry_date": _month_end_on_after(r.issueDate),
                     "entry_rule": "issue_month_end"})

    # intl: per-isin from the intl auctions parquet
    intl = pd.read_parquet(os.path.join(config.INTL_CACHE, "auctions.parquet"))
    intl["event_date"] = pd.to_datetime(intl["event_date"])
    intl["amount"] = pd.to_numeric(intl["amount"], errors="coerce")
    uni = pd.read_csv(os.path.join(config.INTL_CACHE, "universe.csv"))
    country_of = uni.set_index("isin")["country"].to_dict()

    # amount scale heuristic per country (DMO amounts are in mn; BBG steps in units) —
    # normalize to currency units and PRINT the inferred scale for the build log.
    for ctry, g in intl.groupby("country"):
        if ctry == "DE" or ctry not in ("FR", "IT", "ES", "GB"):
            continue
        med = g["amount"].dropna().median()
        scale = 1e6 if (pd.notna(med) and med < 1e6) else 1.0
        if scale != 1.0:
            print(f"  {ctry}: amounts read as MILLIONS (median {med:,.0f}) — scaled x1e6")
        rule = config.INDEX_ENTRY_RULE.get(ctry, "issue_month_end")
        for isin, b in g.sort_values("event_date").groupby("isin"):
            first_dt = b["event_date"].min()
            if rule == "issue_month_end" or b["amount"].isna().all():
                rows.append({"market": ctry, "bond_id": isin,
                             "entry_date": _month_end_on_after(first_dt),
                             "entry_rule": "issue_month_end" if rule == "issue_month_end"
                                           else "issue_month_end_fallback"})
                continue
            cum = (b["amount"].fillna(0) * scale).cumsum()
            crossed = b.loc[cum >= config.EUR_INDEX_MIN_OUT, "event_date"]
            if crossed.empty:      # never crossed (yet) — not in index
                rows.append({"market": ctry, "bond_id": isin,
                             "entry_date": pd.NaT, "entry_rule": "size_threshold_not_crossed"})
            else:
                rows.append({"market": ctry, "bond_id": isin,
                             "entry_date": _month_end_on_after(crossed.iloc[0]),
                             "entry_rule": "size_threshold"})

    out = pd.DataFrame(rows)
    out.to_parquet(OUT_ENTRY)
    print(f"  wrote {OUT_ENTRY}: {len(out)} bonds")
    print(out.groupby(["market", "entry_rule"]).size().to_string())
    return out


def load_index_entry():
    if not os.path.exists(OUT_ENTRY):
        raise FileNotFoundError(f"{OUT_ENTRY} missing — run: python -m breakeven_structures.data_calendar pull")
    return pd.read_parquet(OUT_ENTRY)


def pull():
    pull_cpi()
    build_index_entry()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        pull()
    else:
        d, src = cpi_print_days("2024-01-01", "2026-07-01")
        print(f"CPI print days 2024+: {len(d)} (source={src})")
        if os.path.exists(OUT_ENTRY):
            print(load_index_entry().groupby(["market", "entry_rule"]).size().to_string())
