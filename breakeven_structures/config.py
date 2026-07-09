"""
Central config for the auction-cycle structures study: paths + ALL DECLARED PARAMETERS.

Every parameter below was declared BEFORE any event panel was built (user directives
2026-07-09, logged in IMPLEMENTATION.md). Downstream modules import from here so a
parameter change happens in ONE place. Run convention: from the repo root, e.g.
    python -m breakeven_structures.run_all pull
"""
from __future__ import annotations
import os

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)                       # repo root (total_returns)
CACHE = os.path.join(DIR, "cache")                # gitignored
REPORTS = os.path.join(DIR, "reports")            # md tracked; csv/figures gitignored
FIGURES = os.path.join(REPORTS, "figures")
INBOX = os.path.join(DIR, "inbox")                # manual drops (cpi_release_dates.csv, ...)

ROOT_CACHE = os.path.join(ROOT, "cache")          # per-bond daily/static, macro, auctions
INTL_CACHE = os.path.join(ROOT, "cache_intl")     # intl daily/static, auctions, cmt

# --- Market scope (Q1/Q4: declared sequencing; NOTHING in v2 gets tuned) ----
V1_MARKETS = ["US"]                     # TIPS-first laboratory
V2_MARKETS = ["FR", "IT", "GB"]         # frozen-spec validation
POOL_ONLY = ["ES"]                      # pooled-EUR robustness only, never standalone
DROPPED = ["DE"]                        # 3 bonds, issuance terminated
UK_MIN_DATE = "2014-01-01"              # Q4: no pre-2014 UK (8m-lag gilts are a different instrument)

# --- Event windows -----------------------------------------------------------
T_PRE = 10                              # event window: t-10 .. t+10 business days
T_POST = 10
POST_HORIZONS = [1, 3, 5]               # post-anchor performance snapshots (matches breakeven_rv)

# --- Sector buckets: remaining maturity (years) at anchor -> tenor-equivalent
SECTOR_BUCKETS = [(0.0, 7.5, "5y"), (7.5, 15.0, "10y"), (15.0, 101.0, "30y")]

# --- Anchors (Q5 + directive 3) ----------------------------------------------
# bbg_amt-sourced intl reopenings are dated by the AMT_OUTSTANDING step (~settle).
# Back-shift by the market's standard settle lag; NEVER silently mix anchor types;
# report approx-anchor share per bucket in the data-quality deliverable.
SETTLE_BACKSHIFT_BD = {"FR": 2, "IT": 2, "ES": 2, "GB": 1, "DE": 2}
ANCHOR_QUALITY_RANK = {"exact": 0, "approx": 1, "pricing_only": 2}
# Syndications: pricing-day anchoring is near-meaningless (concession builds from the
# mandate announcement). Until mandate dates are sourced (targeted, small n), syndications
# run post-pricing-only and are EXCLUDED from pre-event path analysis. Never pooled into
# auction buckets.
SYNDICATION_POLICY = "pricing_only"
DEDUPE_WINDOW_CD = 7                    # same-isin events within 7cd = one event; keep best quality

# --- Nominal supply (Q2: two roles) -------------------------------------------
# (a) nominal 5/10/30 auctions are a separate event type (BE structures around nominal supply)
# (b) nominal same-tenor supply inside a TIPS event window is a contamination flag
INCLUDE_NOMINAL_EVENTS = True
FLAG_NOMINAL_SAME_TENOR = True

# --- CPI-in-window (directive 1: dummy, don't drop) ---------------------------
# The mid-month print lands inside +/-10bd of most TIPS auctions; exclusion would gut
# the sample. Paths dummy out (or exclude the return of) the print day itself; full
# event exclusion is a robustness run ONLY.
CPI_WINDOW_POLICY = "dummy"
# Exact release dates: FRED release-dates API (free key) or inbox CSV; else rule window.
# Key lookup order: env FRED_API_KEY, then breakeven_structures/inbox/fred_api_key.txt.
FRED_API_KEY_ENV = "FRED_API_KEY"
FRED_API_KEY_FILE = os.path.join(INBOX, "fred_api_key.txt")
FRED_RELEASE_URL = "https://api.stlouisfed.org/fred/release/dates"
CPI_RELEASE_ID = 10                     # FRED release id for "Consumer Price Index"
CPI_INBOX_CSV = os.path.join(INBOX, "cpi_release_dates.csv")   # one column: release_date
CPI_RULE_WINDOW_DOM = (10, 15)          # fallback: possible print days = 10th..15th of each month

# --- Index entry approximation (Q6) --------------------------------------------
# US/UK: first month-end on/after issue. EUR: new issues often launch below index-minimum
# size and enter only once reopenings build the outstanding -> first month-end after
# cumulative outstanding (initial + taps, from the auctions parquet) crosses the minimum.
EUR_INDEX_MIN_OUT = 2.0e9               # EUR; declared approximation — verify vs index factsheet
INDEX_ENTRY_RULE = {"US": "issue_month_end", "GB": "issue_month_end",
                    "FR": "size_threshold", "IT": "size_threshold", "ES": "size_threshold"}

# --- TreasuryDirect (US pull; public API, no terminal) -------------------------
TD_API = "https://www.treasurydirect.gov/TA_WS/securities/search"
TIPS_START_YEAR = 1997
NOMINAL_START_YEAR = 2003
MIN_AUCTION_SIZE = 1_000_000_000        # below = contingency/test auction (house rule)
TENOR_MAP = {"5-Year": "5y", "10-Year": "10y", "30-Year": "30y"}

# --- Statistics (grids/gates declared up front; report ALL nodes, tune nothing) -
TRAIN_FRAC = 0.60                       # chronological split. DECLARED CAVEAT (directive 2):
                                        # on TIPS this puts training ~pre-2020 and the holdout in
                                        # the 2021-22 inflation regime + after. Holdout-era placebo
                                        # distributions are therefore reported SEPARATELY.
N_BOOT = 2000                           # event-resampled bootstrap draws for path bands
N_PLACEBO = 2000                        # placebo windows matched on month x market
VOL_WINDOW_BD = 60                      # z-standardization: trailing 60d vol of the same measure
MIN_BUCKET_EVENTS = 20                  # below this a bucket pools upward (issuer FE), never standalone
MIN_CELL_N = 12                         # anecdote rule (= breakeven_rv CONFIRM_MIN_N)
SEASONAL_MIN_MONTHS = 36                # expanding seasonal adj needs >= 3y (house rule)

# --- Costs & financing (Phase 6) ------------------------------------------------
ROUND_TRIP_COSTS_BP = [0.5, 1.0, 2.0]   # house grid (optimistic/base/pessimistic), report all
PER_LEG_COST_BP = {                     # draft schedule, per leg, by market/age
    "US_otr": 0.25, "US_offrun": 0.5,
    "FR": 1.0, "IT": 1.0, "ES": 1.5,
    "GB": 1.0, "GB_long": 1.5,
}
FIN_HALF_SPREAD_BP_GRID = [3.0, 10.0]   # 3 = house default; 10 = specialness-proxy stress for
                                        # structures long an OTR leg (no special-repo data exists)

# --- Terminal pull (Phase 0; data_universe.py) -----------------------------------
PULL_START = "20040101"                 # full available depth (house convention: swap/engine
                                        # data begins ~2004; extra history is nearly free)
PULL_BATCH = 20                         # securities per history request (data_layer convention)
PULL_STATIC_BATCH = 50


def ensure_dirs():
    for d in (CACHE, REPORTS, FIGURES, INBOX):
        os.makedirs(d, exist_ok=True)


def bucket_of(remaining_years: float):
    """Map remaining maturity at the anchor to the 5y/10y/30y-equivalent sector bucket."""
    for lo, hi, name in SECTOR_BUCKETS:
        if lo <= remaining_years < hi:
            return name
    return None
