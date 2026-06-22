"""
Export the breakeven total-return data to Excel / CSV for hand-replication.

Two products:
  export_full(path)            -- EVERYTHING used, day-level (business days), one sheet per
                                  tenor: which TIPS/UST CUSIP, their clean price & yield,
                                  accrued, index ratio, dirty, cash value V, DV01, the monthly
                                  DV01 denominator, daily dV/coupon/financing/PnL, repo rate,
                                  per-leg bp return, breakeven, and cumulatives. Plus a macro
                                  sheet (CPI, repo) and a README sheet with the formulas.
  export_returns(path, xT, xU) -- compact: just the returns (+ long/short BE at the given
                                  repo half-spreads), one sheet per tenor. Fast (from cache).

Replication chain (every column is in the sheet):
  dirty_real = clean + accrued ;  V = dirty_real * IR  (UST: IR = 1)
  financing  = days/360 * gc_repo/100 * V_prev
  PnL        = dV + coupon - financing            (dV = V - V_prev, same bond)
  bp         = PnL / denom                        (denom = month's 100k-DV01 rebalance)
  r_BE_bp    = r_TIPS_bp - r_UST_bp ;  cum_* = running sum (linear, not compounded)

Usage:
  python export.py                 # -> exports/breakeven_full.xlsx + per-tenor CSVs
  python export.py returns 3 3     # -> exports/breakeven_returns.xlsx at x_TIPS=x_UST=3bp
"""
from __future__ import annotations
import os, sys
import pandas as pd

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = engine.CACHE
EXPORTS = os.path.join(HERE, "exports")
TENORS = ["5y", "10y", "30y"]

FULL_COLS = [
    "TIPS_cusip", "UST_cusip", "gc_repo",
    "TIPS_clean", "TIPS_yield", "TIPS_accrued", "TIPS_IR", "TIPS_dirty_real", "TIPS_V",
    "TIPS_DV01", "TIPS_denom", "TIPS_dV", "TIPS_coupon", "TIPS_days", "TIPS_financing",
    "TIPS_pnl", "r_TIPS_bp",
    "UST_clean", "UST_yield", "UST_accrued", "UST_IR", "UST_dirty", "UST_V",
    "UST_DV01", "UST_denom", "UST_dV", "UST_coupon", "UST_days", "UST_financing",
    "UST_pnl", "r_UST_bp",
    "r_BE_bp", "cum_TIPS_bp", "cum_UST_bp", "cum_BE_bp",
]

README_ROWS = [
    ("date", "business day (the return is for prior business day -> this day)"),
    ("TIPS_cusip / UST_cusip", "the on-the-run bond each leg tracks that day (rolls 1st b-day of month)"),
    ("gc_repo", "GC financing rate used that day (%, GCFRTSY; USRG1T/fed funds before 2009)"),
    ("*_clean", "quoted clean (real for TIPS) price, per 100 face (Bloomberg PX_CLEAN_MID)"),
    ("*_yield", "yield to maturity (%, real for TIPS, nominal for UST; Bloomberg YLD_YTM_MID)"),
    ("*_accrued", "accrued interest per 100 (computed from coupon schedule, act/act)"),
    ("TIPS_IR", "index ratio = DRI/base CPI (computed from CPI, matches Treasury to 1e-6); UST_IR=1"),
    ("*_dirty_real / UST_dirty", "clean + accrued (real for TIPS)"),
    ("*_V", "cash value = dirty_real * IR (TIPS) ; = dirty (UST)"),
    ("*_DV01", "DV01 per 100 face (real-yield DV01 * IR for TIPS; nominal for UST; our calc, not BBG)"),
    ("*_denom", "DV01 at the monthly rebalance, held constant within the month (the 100k-DV01 divisor)"),
    ("*_dV", "V - V_prev within the SAME bond (never across a roll)"),
    ("*_coupon", "coupon cash booked that day (TIPS = C/2 * IR; paid when a coupon date passes)"),
    ("*_days", "calendar days since prior business day (act/360 financing)"),
    ("*_financing", "days/360 * gc_repo/100 * V_prev"),
    ("*_pnl", "dV + coupon - financing  (per 100 face)"),
    ("r_TIPS_bp / r_UST_bp", "leg daily return in bp = pnl / denom"),
    ("r_BE_bp", "r_TIPS_bp - r_UST_bp  (long-TIPS / short-UST breakeven, mid financing)"),
    ("cum_*", "running linear sum of the daily bp (NOT compounded)"),
    ("NOTE", "repo bid/offer & specialness NOT included here (mid GC). Apply +/-x via the tool."),
]


def tenor_full(tenor):
    """Assemble the full day-level detail frame for one tenor (both legs side by side)."""
    cpi = engine._macro()["cpi_nsa"]
    gc = engine.gc_series()
    t = engine.leg_series("tips", tenor, cpi, gc).add_prefix("TIPS_")
    u = engine.leg_series("nominal", tenor, cpi, gc).add_prefix("UST_")
    df = pd.concat([t, u], axis=1)
    df["gc_repo"] = df["TIPS_gc"].combine_first(df["UST_gc"])
    df["UST_dirty"] = df["UST_dirty_real"]
    df["r_TIPS_bp"] = df["TIPS_bp"]
    df["r_UST_bp"] = df["UST_bp"]
    df["r_BE_bp"] = df["r_TIPS_bp"] - df["r_UST_bp"]
    df["cum_TIPS_bp"] = df["r_TIPS_bp"].cumsum()
    df["cum_UST_bp"] = df["r_UST_bp"].cumsum()
    df["cum_BE_bp"] = df["r_BE_bp"].cumsum()
    out = df.reindex(columns=FULL_COLS)
    out.index.name = "date"
    return out


def _format_sheet(ws, date_col=True):
    """Fix the cosmetics: date column shows as YYYY-MM-DD (not a too-wide datetime that
    renders as #######), sensible column widths, bold + frozen header row."""
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font
    ws.freeze_panes = "B2"
    for cell in ws[1]:                                  # header row
        cell.font = Font(bold=True)
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        header = str(col[0].value or "")
        ws.column_dimensions[letter].width = max(12, len(header) + 2)
    if date_col:                                        # column A = the date index
        ws.column_dimensions["A"].width = 12
        for cell in ws["A"][1:]:
            cell.number_format = "yyyy-mm-dd"


def export_full(path=None):
    os.makedirs(EXPORTS, exist_ok=True)
    path = path or os.path.join(EXPORTS, "breakeven_full.xlsx")
    frames = {}
    for ten in TENORS:
        print(f"  building {ten} ...", flush=True)
        frames[ten] = tenor_full(ten)
    macro = engine._macro()
    readme = pd.DataFrame(README_ROWS, columns=["column", "description"])
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        readme.to_excel(xl, sheet_name="README", index=False)
        _format_sheet(xl.sheets["README"], date_col=False)
        for ten in TENORS:
            frames[ten].to_excel(xl, sheet_name=ten)
            _format_sheet(xl.sheets[ten])
            frames[ten].to_csv(os.path.join(EXPORTS, f"breakeven_{ten}.csv"))
        macro.to_excel(xl, sheet_name="macro")
        _format_sheet(xl.sheets["macro"])
    print(f"  wrote {path}  ({', '.join(TENORS)} sheets + macro + README)")
    print(f"  wrote per-tenor CSVs in {EXPORTS}")
    return path


def export_returns(path=None, xT=0.0, xU=0.0):
    """Compact returns export at given repo half-spreads (used by the interactive button)."""
    os.makedirs(EXPORTS, exist_ok=True)
    path = path or os.path.join(EXPORTS, "breakeven_returns.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for ten in TENORS:
            p = os.path.join(CACHE, f"returns_{ten}.parquet")
            if not os.path.exists(p):
                continue
            d = engine.apply_spread(engine.load_returns(ten), xT, xU)
            d["cum_longBE_bp"] = d["longBE_bp"].cumsum()
            d["cum_shortBE_bp"] = d["shortBE_bp"].cumsum()
            d["cum_BEmid_bp"] = d["BEmid_bp"].cumsum()
            d.index.name = "date"
            d.to_excel(xl, sheet_name=ten)
            _format_sheet(xl.sheets[ten])
    print(f"  wrote {path}  (x_TIPS={xT}bp, x_UST={xU}bp; sheets {', '.join(TENORS)})")
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "returns":
        xT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
        xU = float(sys.argv[3]) if len(sys.argv) > 3 else xT
        export_returns(xT=xT, xU=xU)
    else:
        export_full()
