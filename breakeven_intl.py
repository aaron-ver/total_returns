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


def build_all():
    m = load_map()
    if m.empty:
        print(f"  breakeven_map.csv is empty — nothing to build (add rows: real_isin,nominal_isin,beta)"); return []
    built, skipped = [], []
    for _, r in m.iterrows():
        be = build_be(r["real_isin"], r["nominal_isin"], float(r["beta"]))
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


def export_be(path=None):
    m = load_map()
    if m.empty:
        print("  no map -> no breakeven export"); return None
    os.makedirs(EXPORTS, exist_ok=True)
    path = path or os.path.join(EXPORTS, "linkers_breakeven.xlsx")
    from export_intl import _format
    readme = pd.DataFrame([
        ("r_real_bp", "linker financed daily return, bp per 100k DV01 (engine_intl)"),
        ("r_nom_bp", "nominal hedge financed daily return, bp per 100k DV01 (own-country GC)"),
        ("r_BE_bp", "breakeven daily return = r_real - beta*r_nom (DV01-matched; beta=1 => equal DV01)"),
        ("net_fin_bp", "real fin drag - beta*nom fin drag; ≈0 for a same-country pair at GC mid, "
                       "= core-vs-peripheral GC differential for a cross-country (e.g. vs Bund) hedge"),
        ("cum_*", "running LINEAR sum (bp), not compounded"),
        ("beta", "DV01 weight on the nominal leg from breakeven_map.csv (1.0 = plain breakeven)"),
    ], columns=["column", "description"])
    pairs = {}
    for _, r in m.iterrows():
        be = build_be(r["real_isin"], r["nominal_isin"], float(r["beta"]), save=False)
        if not be.empty:
            pairs[r["real_isin"]] = be.reindex(columns=SHEET_COLS)
    if not pairs:
        print("  map present but no pair had data (pull nominals + build engine first)"); return None
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        readme.to_excel(xl, sheet_name="README", index=False); _format(xl.sheets["README"], date_col=False)
        m.to_excel(xl, sheet_name="MAP", index=False); _format(xl.sheets["MAP"], date_col=False)
        for isin, be in pairs.items():
            be.to_excel(xl, sheet_name=isin[:31]); _format(xl.sheets[isin[:31]])
    print(f"  wrote {path}  ({len(pairs)} breakeven sheets + MAP + README)")
    return path


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
