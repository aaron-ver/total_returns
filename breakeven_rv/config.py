"""
Central config for the breakeven RV study: paths, tickers, model parameters.

Everything downstream imports from here so a parameter change happens in ONE place.
Run convention: all modules run from the repo root (so root modules bbg.py /
data_layer.py are importable), e.g.  python -m breakeven_rv.run_all build
"""
from __future__ import annotations
import os

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)                      # repo root (total_returns)
CACHE = os.path.join(DIR, "cache")               # gitignored
REPORTS = os.path.join(DIR, "reports")           # md tracked; csv/figures gitignored
FIGURES = os.path.join(REPORTS, "figures")

ROOT_CACHE = os.path.join(ROOT, "cache")         # the existing repo cache (energy, macro, per-bond daily)

START = "20040101"                               # BBG pull start (swap data begins ~2004)

# --- Bloomberg series (terminal / DAPI, via root bbg.py) -------------------
# Zero-coupon USD CPI swaps (the derivative inflation curve; Residual B + swap-space FV target)
SWAP_TENORS = [1, 2, 3, 5, 7, 10, 15, 20, 30]
BBG_SERIES = {
    **{f"swap_{t}y": (f"USSWIT{t} Curncy", "PX_LAST") for t in SWAP_TENORS},
    "be5":    ("USGGBE05 Index", "PX_LAST"),   # constant-maturity TIPS breakevens (BBG generic)
    "be10":   ("USGGBE10 Index", "PX_LAST"),
    "be30":   ("USGGBE30 Index", "PX_LAST"),
    "real5":  ("USGGT05Y Index", "PX_LAST"),   # constant-maturity TIPS real yields
    "real10": ("USGGT10Y Index", "PX_LAST"),
    "real30": ("USGGT30Y Index", "PX_LAST"),
    "move":   ("MOVE Index", "PX_LAST"),       # rates implied vol (stress conditioning)
    "bbdxy":  ("BBDXY Index", "PX_LAST"),      # Bloomberg USD spot index (traded, unrevised; from Dec-2004)
}

# --- FRED series (no key needed; fredgraph csv endpoint) --------------------
FRED_SERIES = {
    "dgs3m":      "DGS3MO",    # nominal curve points (H.15, effectively unrevised)
    "dgs2":       "DGS2",
    "dgs5":       "DGS5",
    "dgs7":       "DGS7",      # v3: pillars for maturity-matched interpolation (b_bond.py)
    "dgs10":      "DGS10",
    "dgs20":      "DGS20",     # v3 (gap 1987-93 — outside our sample)
    "dgs30":      "DGS30",
    "vix":        "VIXCLS",
    "usd_broad":  "DTWEXBGS",  # Fed broad trade-weighted USD (daily from 2006; annual weight revisions)
    "be10_fred":  "T10YIE",    # cross-checks of the BBG CM breakevens
    "be5_fred":   "T5YIE",
    "real10_fred": "DFII10",
}

# --- TreasuryDirect auction pull --------------------------------------------
TD_API = "https://www.treasurydirect.gov/TA_WS/securities/search"
TD_START_YEAR = 2003
MIN_AUCTION_SIZE = 1_000_000_000   # below this, contingency/test auction (mirrors root auctions.py)
TENOR_MAP = {"5-Year": "5y", "10-Year": "10y", "30-Year": "30y"}

# --- Model parameters --------------------------------------------------------
L1_WINDOW = 504          # rolling OLS window (~2y of business days; Barclays OOS sweet spot)
L1_HALFLIFE = 252        # EWLS variant half-life (~1y)
Z_WINDOW = 504           # z-score window (same-window residual vol, per plan §7)
Z_MIN_PERIODS = 252
L1_FACTORS = ["slope_3m10y", "log_gas", "vix", "log_usd"]     # Barclays four-factor baseline
L1_LASSO_FACTORS = L1_FACTORS + ["slope_2s10s", "move", "gcf_repo", "cpi_yoy_lagged", "dgs10"]

HORIZONS = [5, 10, 20]   # forward residual-change horizons (business days)
Z_THRESHOLD = 1.0        # |z| beyond this = "cheap"/"rich" for quadrant + auction buckets

AUCTION_LAG = (10, 5)    # residual measured as mean over t-10..t-5 bd before auction (pre-concession)
POST_HORIZONS = [1, 3, 5]  # post-auction performance windows (business days)

# --- Layer 2 (v2 spec): Track 1 — A conditioning + fit-reversion decomposition
T1_HORIZONS = [5, 10, 20]     # matched to A's ~14bd half-life (no h > 40)
T1_TRAIN_MIN = 1008           # ~4y initial walk-forward training window
T1_REFIT = 252                # annual refits, strictly OOS evaluation
T1_GBM_GATE = 0.005           # min ridge-over-baseline OOS R2 lift before a GBM is even tried
EP_ENTRY_Z = 1.0              # episode entry |z_A|
EP_EXIT_Z = 0.25              # episode exit |z_A|
EP_HL_MULT = 3                # time stop at 3x currently-estimated half-life
HL_WINDOW = 504               # rolling half-life estimation window
HL_CLIP = (5, 60)             # sane bounds on the rolling half-life (bd)

# --- Layer 2 (v2 spec): Track 2 — B auction event model
T2_N_BOOT = 2000              # week-cluster bootstrap draws
T2_OOS_START = "2016-01-01"   # first OOS scoring year (expanding refit annually before each year)
T2_SIZE_TRAIL = 4             # size surprise vs mean of prior N same-tenor auctions

# --- Layer 2 (v2 spec): Track 3 — backtest
T3_ENTRY_GRID = [0.75, 1.0, 1.5]   # A-strategy entry thresholds (report ALL, never pick silently)
T3_SIZE_CAP_Z = 2.0                # size = -z/cap, saturating at |z| = cap (2 sigma)
T3_COSTS_BP = [0.5, 1.0, 2.0]      # round-trip cost grid, bp of BE yield (optimistic/base/pessimistic)
T3_B_ENTRY = 5                     # B-strategy: enter at close t-5 before auction
T3_B_EXITS = [1, 3]                # exit t+1 (primary) / t+3 (variant)

# --- v3 spec: bond-level Residual B + data upgrades --------------------------
NOMINAL_PILLARS = {"dgs2": 2, "dgs5": 5, "dgs7": 7, "dgs10": 10, "dgs20": 20, "dgs30": 30}
NYFED_PD_API = "https://markets.newyorkfed.org/api/pd"
DEALER_TIPS_SERIES = ["PDPOSTIPS-L2", "PDPOSTIPS-G2", "PDPOSTIPS-G6L11", "PDPOSTIPS-G11"]
DEALER_SERIES_BREAKS = ["SBN2013", "SBN2015", "SBN2022", "SBN2024"]   # TIPS buckets exist 2013-04+
DEALER_PUB_LAG_D = 10          # FR2004: Wednesday as-of, published following Thursday (~8d);
                               # vintage rule = usable 10 calendar days after as-of (like CPI)
FIXINGS_TICKERS = [f"USSWIF{m} Curncy" for m in range(1, 13)]   # monthly CPI fixings (1y)
SEASONAL_MIN_MONTHS = 36       # expanding month-of-year seasonal adj needs >= 3y of history
V3_ENTRY_GRID = [0.75, 1.0, 1.5]     # episode robustness grid (report all, tune nothing)
V3_EXIT_GRID = [0.25, 0.5]
V3_DECAY_LAGS = [1, 3, 5, 10]        # signal decay profile: z measured at t-lag
CONFIRM_MIN_N = 12             # below this n, the confirm-cell result is "not establishable"

# CPI publication lag: the CPI print for month m is released ~day 10-13 of m+1.
# Conservative rule used for vintage discipline: value for month m becomes known
# on the 15th calendar day of m+1 (first business day on/after).
CPI_PUB_DAY = 15


def ensure_dirs():
    for d in (CACHE, REPORTS, FIGURES):
        os.makedirs(d, exist_ok=True)
