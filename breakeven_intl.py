"""
Breakeven total return for European/UK linkers (reference_intl.MD §8.1).

Breakeven = long the inflation-linked bond, short a NOMINAL government bond as the rates hedge,
both DV01-normalized (100k DV01/leg) so r_BE = r_real - beta*r_nominal isolates the inflation bet
(beta=1.0 = equal-DV01 plain breakeven). Each leg is the financed total return from engine_intl,
financed in its OWN-country GC (local ccy), so for a same-country pair net financing ≈ 0 at GC mid,
while a cross-country hedge (e.g. an Italian linker vs a Bund) carries the core-vs-peripheral GC
differential as a real P&L line -- the sovereign-basis point the desk flagged (reference_intl §8.1).

The nominal comparator per linker is NOT guessed: it comes from a lookup CSV (`breakeven_map.csv`)
-- "the street has a list of bonds used for breakeven" -- so the desk's file plugs straight in.
Until it's populated the mechanism is complete and idle.

Usage:
  python breakeven_intl.py pull       # pull static+daily for the nominal hedge bonds in the map
  python breakeven_intl.py build      # build r_BE per linker -> cache_intl/breakeven/<real_isin>.parquet
  python breakeven_intl.py export     # one sheet per breakeven pair -> exports/linkers_breakeven.xlsx
  python breakeven_intl.py map        # print the loaded map
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import linkers
import data_layer_intl as dl
import engine_intl as eng

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
MAP_CSV = os.path.join(HERE, "breakeven_map.csv")
EXPORTS = os.path.join(HERE, "exports")

SHEET_COLS = ["real_isin", "nominal_isin", "beta", "country",
              "r_real_bp", "r_nom_bp", "r_BE_bp", "cum_real_bp", "cum_nom_bp", "cum_BE_bp",
              "real_yield", "nom_yield", "net_fin_bp", "days"]


# Desk "street" linker reports (monthly). Each 'Reports' sheet has, per linker: ISIN (real bond),
# Comparator + Comparator ISIN (the nominal hedge), and Yield Beta 1M/3M. We map ISIN -> Comparator
# ISIN. Germany is dropped (boss: issuance stopped, ignore); 8m-lag gilts and non-traded bonds drop
# out automatically since they aren't in the active universe.
STREET_FILES = [os.path.join(HERE, "europe_isins.xlsx"), os.path.join(HERE, "uk_isins.xlsx")]
EXCLUDE_COUNTRIES = {"DE"}


def _read_street(path):
    """Parse one desk report's 'Reports' sheet -> rows of (Issue, ISIN, Comparator, Comparator ISIN,
    Yield Beta 1M/3M). Finds the header row (the file has a title row above it)."""
    raw = pd.read_excel(path, sheet_name="Reports", header=None)
    hdr = None
    for i in range(min(8, len(raw))):
        vals = [str(x).strip() for x in raw.iloc[i].tolist()]
        if "ISIN" in vals and "Comparator ISIN" in vals:
            hdr = i; break
    if hdr is None:
        raise ValueError(f"{os.path.basename(path)}: no header row with 'ISIN' & 'Comparator ISIN'")
    df = pd.read_excel(path, sheet_name="Reports", header=hdr)
    df.columns = [str(c).strip() for c in df.columns]
    cols = ["Issue", "ISIN", "Comparator", "Comparator ISIN", "Yield Beta 1M", "Yield Beta 3M"]
    df = df[[c for c in cols if c in df.columns]].copy()
    return df.dropna(subset=["ISIN", "Comparator ISIN"])


def import_street_files(beta=1.0, files=None):
    """Build breakeven_map.csv from the desk's monthly reports: real ISIN -> Comparator (nominal)
    ISIN. beta defaults to 1.0 (equal-DV01 plain breakeven, the stable/comparable default); the
    file's Yield Beta 1M/3M are carried as extra columns for an optional beta-adjusted variant.
    Keeps only linkers in the active universe; drops Germany. Reconciles and prints coverage."""
    files = files or STREET_FILES
    s = pd.concat([_read_street(p) for p in files], ignore_index=True)
    s["ISIN"] = s["ISIN"].astype(str).str.strip()
    s["Comparator ISIN"] = s["Comparator ISIN"].astype(str).str.strip()
    u = linkers.load_universe()                              # active (excludes 8m-lag, non-traded)
    active = set(u["isin"]); country = dict(zip(u["isin"], u["country"]))
    rows, unmatched, excl = [], [], []
    for _, r in s.iterrows():
        ri = r["ISIN"]
        if ri not in active:
            unmatched.append((ri, r.get("Issue"))); continue
        if country.get(ri) in EXCLUDE_COUNTRIES:
            excl.append(ri); continue
        rows.append({"real_isin": ri, "nominal_isin": r["Comparator ISIN"], "beta": beta,
                     "note": f"{r.get('Issue')} vs {r.get('Comparator')}",
                     "beta_1m": r.get("Yield Beta 1M"), "beta_3m": r.get("Yield Beta 3M")})
    out = pd.DataFrame(rows).drop_duplicates("real_isin").reset_index(drop=True)
    out.to_csv(MAP_CSV, index=False)
    covered = set(out["real_isin"])
    missing = [(i, n) for i, n in zip(u["isin"], u["program"] + " " + u["desc"].astype(str))
               if i not in covered and country.get(i) not in EXCLUDE_COUNTRIES]
    print(f"  wrote {MAP_CSV}: {len(out)} breakeven pairs")
    print(f"    matched {len(covered)} active linkers; dropped {len(excl)} German; "
          f"{len(unmatched)} file rows not in our active universe (8m-lag/other)")
    if unmatched:
        print("    file ISINs not mapped:", ", ".join(f"{i}({n})" for i, n in unmatched[:12]))
    if missing:
        print(f"    active linkers with NO street comparator ({len(missing)}):",
              ", ".join(i for i, _ in missing[:12]) + ("..." if len(missing) > 12 else ""))
    return out


def load_map():
    """The linker -> nominal-hedge lookup. CSV columns: real_isin, nominal_isin, beta(opt), note(opt).
    Lines starting with '#' are ignored. Returns an empty frame (with columns) if the file is absent
    or has no rows yet."""
    cols = ["real_isin", "nominal_isin", "beta", "note"]
    if not os.path.exists(MAP_CSV):
        return pd.DataFrame(columns=cols)
    m = pd.read_csv(MAP_CSV, comment="#", dtype={"real_isin": str, "nominal_isin": str})
    for c in cols:
        if c not in m:
            m[c] = pd.NA
    m = m.dropna(subset=["real_isin", "nominal_isin"])
    m["beta"] = pd.to_numeric(m["beta"], errors="coerce").fillna(1.0)
    return m[cols].reset_index(drop=True)


def _nominal_country(nominal_isin):
    """Issuer country of the nominal hedge (Bloomberg static COUNTRY, else ISIN prefix)."""
    sp = os.path.join(CACHE, "static", f"{nominal_isin}.parquet")
    if os.path.exists(sp):
        c = pd.read_parquet(sp).iloc[0].get("COUNTRY")
        if isinstance(c, str) and c.strip().upper()[:2] in linkers.NOMINAL_MARKETS:
            return c.strip().upper()[:2]
    return linkers.country_of_isin(nominal_isin)


def pull_nominals(skip_existing=True):
    """Pull static + daily for every distinct nominal hedge bond in the map (they aren't in the
    linker universe). Resumable. Needs the Terminal."""
    m = load_map()
    if m.empty:
        print(f"  breakeven_map.csv is empty — add real_isin,nominal_isin rows (see {MAP_CSV})"); return
    noms = sorted(set(m["nominal_isin"]))
    print(f"  pulling {len(noms)} nominal hedge bonds")
    dl.pull_isins(noms, skip_existing=skip_existing)


def _real_leg(real_isin):
    """The linker's financed-return frame: use the cached engine build, else compute from its market."""
    try:
        return eng.load_returns(real_isin)
    except Exception:
        u = linkers.load_universe(include_deferred=True)
        row = u[u["isin"] == real_isin]
        return eng.bond_series(real_isin, row.iloc[0]["market"]) if not row.empty else pd.DataFrame()


def build_be(real_isin, nominal_isin, beta=1.0, save=True):
    """Breakeven daily/cumulative return for one (linker, nominal) pair. r_BE = r_real - beta*r_nom,
    both DV01-normalized bp. Returns a frame indexed by the common trading days, or empty."""
    real = _real_leg(real_isin)
    country = _nominal_country(nominal_isin)
    if real.empty or country is None:
        return pd.DataFrame()
    nom = eng.nominal_series(nominal_isin, country)
    if nom.empty:
        return pd.DataFrame()
    idx = real.index.intersection(nom.index)               # common trading days
    if len(idx) < 2:
        return pd.DataFrame()
    rr, nn = real.loc[idx], nom.loc[idx]
    out = pd.DataFrame(index=idx)
    out.index.name = "date"
    out["real_isin"] = real_isin
    out["nominal_isin"] = nominal_isin
    out["beta"] = beta
    out["country"] = country
    out["r_real_bp"] = rr["bp"]
    out["r_nom_bp"] = nn["bp"]
    out["r_BE_bp"] = rr["bp"] - beta * nn["bp"]
    out["cum_real_bp"] = out["r_real_bp"].cumsum()
    out["cum_nom_bp"] = out["r_nom_bp"].cumsum()
    out["cum_BE_bp"] = out["r_BE_bp"].cumsum()
    out["real_yield"] = rr["yield"]
    out["nom_yield"] = nn["yield"]
    out["net_fin_bp"] = rr["fin_bp"] - beta * nn["fin_bp"] # ≈0 same-ccy GC; = GC differential cross-country
    out["days"] = rr["days"]
    if save and not out.empty:
        os.makedirs(os.path.join(CACHE, "breakeven"), exist_ok=True)
        out.to_parquet(os.path.join(CACHE, "breakeven", f"{real_isin}.parquet"))
    return out


# --- comprehensive per-pair dump (US-TIPS-style: both legs' full chain side by side) ----------
FULL_COLS = [
    "settlement_date", "d", "gc_repo",
    # --- REAL (linker) leg ---
    "real_isin", "real_clean", "real_yield", "real_accrued", "real_IR", "real_IR_bbg",
    "real_dirty_real", "V_real", "V_real_prev", "real_DV01", "real_notional",
    "real_dV", "real_coupon", "real_financing", "real_gross_bp", "real_fin_bp", "r_real_bp",
    # --- NOMINAL hedge leg ---
    "nom_isin", "nom_clean", "nom_yield", "nom_accrued", "nom_dirty", "V_nom", "V_nom_prev",
    "nom_DV01", "nom_notional", "nom_dV", "nom_coupon", "nom_financing", "nom_gross_bp",
    "nom_fin_bp", "r_nom_bp",
    # --- breakeven ---
    "beta", "net_fin_bp", "r_BE_bp", "cum_real_bp", "cum_nom_bp", "cum_BE_bp",
    # --- flags ---
    "is_coupon_day", "is_weekend_or_holiday_step",
]

README_FULL_ROWS = [
    ("date", "observation day; marked to its T+settle business day (settlement_date)"),
    ("settlement_date", "T+1 (gilt) / T+2 (euro) settle on the local calendar; V/accrued/IR valued here"),
    ("d", "settlement span = settle(t)-settle(t-1) in calendar days; drives accrual (via dV) AND repo"),
    ("gc_repo", "local GC financing rate that day (%, €STR euro / SONIA gilt; RFR per-country once filled). Same for both legs (own-country pair)."),
    ("--- REAL (linker) leg ---", ""),
    ("real_isin", "the inflation-linked bond"),
    ("real_clean", "quoted clean REAL price per 100 (Bloomberg PX_CLEAN_MID)"),
    ("real_yield", "real yield to maturity"),
    ("real_accrued", "accrued REAL coupon per 100 = (C/freq)*(1-w), act/act ICMA"),
    ("real_IR", "index ratio = DRI(settle)/DRI(dated), rules-based from the reference index (reference_intl §3)"),
    ("real_IR_bbg", "Bloomberg's own INDEX_RATIO (cross-check; engine drives off real_IR). Blank before ircheck window."),
    ("real_dirty_real", "real_clean + real_accrued"),
    ("V_real", "cash value per 100 = real_dirty_real * real_IR (the inflation-uplifted settlement amount)"),
    ("V_real_prev", "prior-settle V_real (the financing base)"),
    ("real_DV01", "sizing DV01 per 100 face, fixed at month start = the bp denominator (our pricing.py calc, NOT BBG)"),
    ("real_notional", "face giving 100k DV01 = 1e7/real_DV01 (held all month)"),
    ("real_dV", "V_real - V_real_prev (same bond; weekend accretion lands on the pre-weekend day)"),
    ("real_coupon", "coupon cash booked when a pay date falls in the settle span = (C/freq)*IR"),
    ("real_financing", "d/360 * gc_repo/100 * V_real_prev (repo on cash carried in)"),
    ("real_gross_bp", "(real_dV + real_coupon)/real_DV01 — price+coupon return before financing"),
    ("real_fin_bp", "real_financing/real_DV01 — financing drag (bp)"),
    ("r_real_bp", "linker net financed return (bp) = real_gross_bp - real_fin_bp"),
    ("--- NOMINAL hedge leg (short) ---", ""),
    ("nom_isin", "nominal govt bond shorted as the rates hedge (the street comparator)"),
    ("nom_clean / nom_yield / nom_accrued", "clean price, yield, accrued (no index ratio; IR=1)"),
    ("nom_dirty", "nom_clean + nom_accrued"),
    ("V_nom / V_nom_prev", "cash value = nom_dirty (IR=1) and its prior-settle base"),
    ("nom_DV01 / nom_notional", "sizing DV01 per 100 (bp denominator) and the 100k-DV01 face"),
    ("nom_dV / nom_coupon / nom_financing", "day ΔV, coupon cash, repo financing (own-country GC)"),
    ("nom_gross_bp / nom_fin_bp / r_nom_bp", "gross, financing drag, net financed return (bp)"),
    ("--- BREAKEVEN ---", ""),
    ("beta", "DV01 weight on the nominal leg (1.0 = equal-DV01 plain breakeven; from breakeven_map.csv)"),
    ("net_fin_bp", "real_fin_bp - beta*nom_fin_bp (long pays / short earns); ≈0 for an own-country pair at GC mid"),
    ("r_BE_bp", "breakeven daily return = r_real_bp - beta*r_nom_bp"),
    ("cum_real_bp / cum_nom_bp / cum_BE_bp", "running LINEAR sums of the daily bp (not compounded)"),
    ("is_coupon_day", "a coupon paid on either leg that day"),
    ("is_weekend_or_holiday_step", "d > 1 (the step spans a weekend/holiday)"),
    ("NOTE financing", "GC mid, no specialness, no repo bid/offer (same convention as the US build)."),
    ("NOTE breakeven", "both legs DV01-normalized to 100k DV01; r_BE isolates inflation (street uses own-country nominal)."),
]

_NOM_CACHE = {}
def _nominal_cached(isin, country):
    k = (isin, country)
    if k not in _NOM_CACHE:
        _NOM_CACHE[k] = eng.nominal_series(isin, country)
    return _NOM_CACHE[k]


def build_be_full(real_isin, nominal_isin, beta=1.0, save=True):
    """Comprehensive per-pair frame: BOTH legs' full daily chain side by side + breakeven, every
    column needed to hand-check a day (the US-TIPS reproducibility format). Returns empty if data
    is missing."""
    real = _real_leg(real_isin)
    country = _nominal_country(nominal_isin)
    if real.empty or country is None:
        return pd.DataFrame()
    nom = _nominal_cached(nominal_isin, country)
    if nom.empty:
        return pd.DataFrame()
    idx = real.index.intersection(nom.index)
    if len(idx) < 2:
        return pd.DataFrame()
    r, n = real.loc[idx], nom.loc[idx]
    o = pd.DataFrame(index=idx); o.index.name = "date"
    o["settlement_date"] = r["settle"]; o["d"] = r["days"]; o["gc_repo"] = r["gc"]
    # real leg
    o["real_isin"] = real_isin; o["real_clean"] = r["clean"]; o["real_yield"] = r["yield"]
    o["real_accrued"] = r["accrued"]; o["real_IR"] = r["IR"]; o["real_IR_bbg"] = r["IR_bbg"]
    o["real_dirty_real"] = r["dirty_real"]; o["V_real"] = r["V"]; o["V_real_prev"] = r["V_prev"]
    o["real_DV01"] = r["denom"]; o["real_notional"] = r["notional"]; o["real_dV"] = r["dV"]
    o["real_coupon"] = r["coupon"]; o["real_financing"] = r["financing"]
    o["real_gross_bp"] = r["gross_bp"]; o["real_fin_bp"] = r["fin_bp"]; o["r_real_bp"] = r["bp"]
    # nominal leg
    o["nom_isin"] = nominal_isin; o["nom_clean"] = n["clean"]; o["nom_yield"] = n["yield"]
    o["nom_accrued"] = n["accrued"]; o["nom_dirty"] = n["dirty_real"]; o["V_nom"] = n["V"]
    o["V_nom_prev"] = n["V_prev"]; o["nom_DV01"] = n["denom"]; o["nom_notional"] = n["notional"]
    o["nom_dV"] = n["dV"]; o["nom_coupon"] = n["coupon"]; o["nom_financing"] = n["financing"]
    o["nom_gross_bp"] = n["gross_bp"]; o["nom_fin_bp"] = n["fin_bp"]; o["r_nom_bp"] = n["bp"]
    # breakeven
    o["beta"] = beta
    o["net_fin_bp"] = r["fin_bp"] - beta * n["fin_bp"]
    o["r_BE_bp"] = r["bp"] - beta * n["bp"]
    o["cum_real_bp"] = o["r_real_bp"].cumsum()
    o["cum_nom_bp"] = o["r_nom_bp"].cumsum()
    o["cum_BE_bp"] = o["r_BE_bp"].cumsum()
    o["is_coupon_day"] = (r["coupon"] != 0) | (n["coupon"] != 0)
    o["is_weekend_or_holiday_step"] = o["d"] > 1
    o = o.reindex(columns=FULL_COLS)
    if save and not o.empty:
        os.makedirs(os.path.join(CACHE, "breakeven"), exist_ok=True)
        o.to_parquet(os.path.join(CACHE, "breakeven", f"{real_isin}.parquet"))
    return o


def _pick_example(pairs):
    """Default example bond for the boss to eyeball: the longest-history pair (most rows -> spans
    many coupons, weekends and the index rebasings)."""
    return max(pairs, key=lambda k: len(pairs[k][1])) if pairs else None


def build_all():
    m = load_map()
    if m.empty:
        print(f"  breakeven_map.csv is empty — nothing to build (add rows: real_isin,nominal_isin,beta)"); return []
    built, skipped = [], []
    for _, r in m.iterrows():
        be = build_be_full(r["real_isin"], r["nominal_isin"], float(r["beta"]))
        if be.empty:
            skipped.append(r["real_isin"]); print(f"  {r['real_isin']} vs {r['nominal_isin']}  SKIP (missing data)")
        else:
            built.append(r["real_isin"])
            print(f"  {r['real_isin']} vs {r['nominal_isin']} (beta {r['beta']:.2f})  "
                  f"{len(be)} days  cum_BE={be['cum_BE_bp'].iloc[-1]:+.0f}bp")
    print(f"\nbuilt {len(built)} breakevens, skipped {len(skipped)}")
    return built


def load_be(real_isin):
    return pd.read_parquet(os.path.join(CACHE, "breakeven", f"{real_isin}.parquet"))


def export_be(example=None):
    """COMPREHENSIVE breakeven export (US-TIPS reproducibility format): one CSV PER PAIR with both
    legs' full chain, into exports/linker_breakevens/, plus a column README and a single nicely
    formatted Excel workbook for one example bond (default = longest history) so the desk can
    eyeball every column."""
    m = load_map()
    if m.empty:
        print("  no map -> no breakeven export"); return None
    folder = os.path.join(EXPORTS, "linker_breakevens")
    os.makedirs(folder, exist_ok=True)
    pairs, empty = {}, []
    for _, r in m.iterrows():
        o = build_be_full(r["real_isin"], r["nominal_isin"], float(r["beta"]), save=True)
        if o.empty:
            empty.append(r["real_isin"]); continue
        o.to_csv(os.path.join(folder, f"{r['real_isin']}.csv"))
        pairs[r["real_isin"]] = (r, o)
    if not pairs:
        print("  map present but no pair had data (run: breakeven_intl.py pull, engine_intl.py)"); return None
    with open(os.path.join(folder, "_README.md"), "w", encoding="utf-8") as f:
        f.write("# Linker breakeven — comprehensive per-pair columns\n\nOne CSV per breakeven pair "
                "(long linker / short nominal hedge); every field to hand-check the chain "
                "(reference_intl §8.1).\n\n| column | description |\n|---|---|\n"
                + "\n".join(f"| {c} | {d} |" for c, d in README_FULL_ROWS) + "\n")
    ex = example or _pick_example(pairs)
    expath = None
    if ex in pairs:
        from export_intl import _format
        readme = pd.DataFrame(README_FULL_ROWS, columns=["column", "description"])
        expath = os.path.join(EXPORTS, f"linker_breakeven_example_{ex}.xlsx")
        with pd.ExcelWriter(expath, engine="openpyxl") as xl:
            readme.to_excel(xl, sheet_name="README", index=False); _format(xl.sheets["README"], date_col=False)
            pairs[ex][1].to_excel(xl, sheet_name=ex[:31]); _format(xl.sheets[ex[:31]])
    print(f"  wrote {len(pairs)} per-pair CSVs -> {folder}  (+ _README.md)")
    if empty:
        print(f"  skipped {len(empty)} (missing data): {', '.join(empty[:6])}")
    if expath:
        print(f"  example workbook (easy to eyeball): {os.path.basename(expath)}  [{ex}]")
    return folder


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    if cmd == "import":
        import_street_files()
    elif cmd == "pull":
        pull_nominals()
    elif cmd == "build":
        build_all()
    elif cmd == "export":
        export_be()
    elif cmd == "map":
        m = load_map()
        print(f"breakeven_map.csv: {len(m)} pairs" + ("" if len(m) else f" (empty — populate {MAP_CSV})"))
        if len(m):
            print(m.to_string(index=False))
    else:
        print(__doc__)
