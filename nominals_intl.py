"""
Comprehensive NOMINAL government-bond universe — for contemporaneous breakeven hedging.

Problem (boss, 2026-06): the street file gives ONE nominal comparator per linker, so the CMT
contemporaneous matcher could only choose from ~68 (recent) bonds. Going back in time it then fell
back to far-off maturities — e.g. the OAT€i 2040 hedged with a 2032 nominal in 2007 (an 8y gap),
when an OAT 2038 (issued 2006) actually existed. The pool, not the rule, was the problem.

Fix: ingest a FULL nominal-bullet universe per country (exported from Bloomberg Security Finder) so
the matcher (cmt_intl._pick_nominal) picks the genuinely closest-maturity nominal that existed then.

WORKFLOW (the Bloomberg part is yours; I can't run the Terminal):
  1. Bloomberg Security Finder (the SRCH / SECF screen), per sovereign — French Republic, Republic of
     Italy, Kingdom of Spain, United Kingdom:
        Govt  ->  Coupon Type = FIXED,  Mty Type = BULLET,  EXCLUDE Inflation-Linked
        UNCHECK "Exclude Matured/Called"  (so since-matured benchmarks are included -> better deep
                                           history; optional but recommended for the short end)
        Column Settings -> make sure ISIN, Coupon, Maturity, Issue Date, Amt Issued are shown
        Export -> Excel.   Save into  nominal_universe/  (one file per country, any filename).
  2. python nominals_intl.py import     # parse exports -> cache_intl/nominal_universe.csv
  3. python nominals_intl.py pull        # daily+static for all of them (resumable; needs Terminal)
  4. python cmt_intl.py                  # rebuild — the matcher now draws from this universe
     python cmt_intl.py diag             # the refreshed worst-case mismatch report

Until step 3 is done the CMT matcher automatically falls back to the old street-comparator pool, so
nothing breaks in the meantime.
"""
from __future__ import annotations
import os, sys, glob, re
from html.parser import HTMLParser
import pandas as pd

import linkers
import data_layer_intl as dl

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = linkers.CACHE
NOMINAL_DIR = os.path.join(HERE, "nominal_universe")
UNIVERSE_CSV = os.path.join(CACHE, "nominal_universe.csv")
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
COUNTRIES = {"FR", "IT", "ES", "GB", "DE"}


def _find_col(cols, *keys, exclude=()):
    for c in cols:
        cl = str(c).strip().lower()
        if any(k in cl for k in keys) and not any(x in cl for x in exclude):
            return c
    return None


class _HTMLTables(HTMLParser):
    """Stdlib HTML table extractor (Bloomberg 'Excel' exports are often HTML, not real .xls)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []; self._rows = None; self._row = None; self._cell = None; self._td = False
    def handle_starttag(self, tag, attrs):
        if tag == "table": self._rows = []
        elif tag == "tr" and self._rows is not None: self._row = []
        elif tag in ("td", "th") and self._row is not None: self._td = True; self._cell = []
    def handle_endtag(self, tag):
        if tag == "table" and self._rows is not None:
            if self._rows: self.tables.append(self._rows)
            self._rows = None
        elif tag == "tr" and self._row is not None:
            self._rows.append(self._row); self._row = None
        elif tag in ("td", "th") and self._td:
            self._row.append("".join(self._cell).strip()); self._td = False; self._cell = []
    def handle_data(self, d):
        if self._td: self._cell.append(d)


def _html_rows(text):
    p = _HTMLTables()
    try:
        p.feed(text)
    except Exception:
        pass
    if not p.tables:
        return pd.DataFrame()
    rows = max(p.tables, key=len)
    mx = max((len(r) for r in rows), default=0)
    return pd.DataFrame([r + [""] * (mx - len(r)) for r in rows])


def _load_raw(path):
    """Header-less DataFrame from a Bloomberg export, sniffing the true format (real xls/xlsx, HTML,
    HTML 'frameset' shell -> follow to its companion sheet, or csv/tab)."""
    with open(path, "rb") as fh:
        sig = fh.read(2048)
    low = sig.lower()
    if sig[:2] in (b"<h", b"<!", b"<t") or b"<html" in low or b"<table" in low:
        txt = open(path, encoding="utf-8", errors="replace").read()
        raw = _html_rows(txt)
        if raw.empty:                                          # frameset shell -> follow to data sheet
            m = (re.search(r'href="([^"]*sheet0*1\.html?)"', txt, re.I)
                 or re.search(r'href="([^"]*\.html?)"', txt, re.I))
            cand = os.path.join(os.path.dirname(path), m.group(1).replace("/", os.sep)) if m else None
            if cand and os.path.exists(cand):
                raw = _html_rows(open(cand, encoding="utf-8", errors="replace").read())
            if raw.empty:
                raise ValueError("Bloomberg 'Web Page' frameset shell with no embedded data "
                                 "(its *_files/sheet001.htm folder wasn't copied). Re-save as CSV or .xlsx.")
        return raw
    if sig[:2] == b"PK" or sig[:4] == b"\xd0\xcf\x11\xe0":
        return pd.read_excel(path, header=None)
    try:
        return pd.read_csv(path, header=None, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
    except Exception:
        return pd.read_excel(path, header=None)              # last resort


def _dates(col):
    """Parse a date column: handles Excel serial numbers (Save-As-CSV turns dates into 43971) and
    normal date strings."""
    s = col.astype(str).str.strip()
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.7 and num.dropna().between(8000, 95000).mean() > 0.8:
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(num.round(), unit="D")   # Excel serial
    return pd.to_datetime(s, errors="coerce")


def _read_one(path):
    """Parse one Security Finder export (header row auto-detected; columns fuzzy-matched)."""
    try:
        raw = _load_raw(path)
    except Exception as e:
        print(f"    !! {os.path.basename(path)}: {e}"); return pd.DataFrame()
    hdr = None
    for i in range(min(15, len(raw))):
        vals = [str(x).strip().lower() for x in raw.iloc[i].tolist()]
        if any("matur" in v for v in vals) and any(("coupon" in v or "cpn" in v or "isin" in v) for v in vals):
            hdr = i; break
    if hdr is None:
        print(f"    !! {os.path.basename(path)}: no header row (need Maturity + ISIN/Coupon)"); return pd.DataFrame()
    df = raw.iloc[hdr + 1:].copy(); df.columns = [str(x).strip() for x in raw.iloc[hdr].tolist()]
    ci = _find_col(df.columns, "isin")
    cm = _find_col(df.columns, "matur")
    if ci is None or cm is None:
        print(f"    !! {os.path.basename(path)}: missing ISIN or Maturity column "
              f"(add ISIN via Column Settings)"); return pd.DataFrame()
    cc = _find_col(df.columns, "coupon", "cpn", exclude=("type", "freq", "frq", "dt", "date"))
    cd = (_find_col(df.columns, "issue date", "issue dt")
          or _find_col(df.columns, "issue", exclude=("amount", "amt", "size", "px", "price")))
    ct = _find_col(df.columns, "ticker")
    ca = _find_col(df.columns, "amt", "amount")
    out = pd.DataFrame()
    out["isin"] = df[ci].astype(str).str.strip().str.upper()
    out["coupon"] = pd.to_numeric(df[cc], errors="coerce") if cc else pd.NA
    out["maturity"] = _dates(df[cm])
    out["issue_date"] = _dates(df[cd]) if cd else pd.NaT
    out["ticker"] = df[ct].astype(str).str.strip() if ct else ""
    out["amt_issued"] = (pd.to_numeric(df[ca].astype(str).str.replace(",", "", regex=False), errors="coerce")
                         if ca else pd.NA)
    out = out[out["isin"].astype(str).str.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")]
    return out


def import_universe():
    os.makedirs(NOMINAL_DIR, exist_ok=True)
    files = [f for f in glob.glob(os.path.join(NOMINAL_DIR, "*"))
             if f.lower().endswith((".xlsx", ".xls", ".csv")) and not os.path.basename(f).startswith("~")]
    if not files:
        print(f"  no export files in {NOMINAL_DIR}/  — drop the Security Finder exports there (see module docstring)")
        return pd.DataFrame()
    frames = [_read_one(f) for f in files]
    frames = [f for f in frames if not f.empty]
    if not frames:
        print("  parsed 0 rows"); return pd.DataFrame()
    u = pd.concat(frames, ignore_index=True)
    u["country"] = u["isin"].str[:2]
    u = u[u["country"].isin(COUNTRIES)]
    # strip any inflation-linkers that slip through (if the export wasn't filtered): drop ISINs that
    # are in our linker universe, plus obvious linker ticker tags (€i, "i ", I/L, "ils", "tii").
    n0 = len(u)
    try:
        linker_isins = set(linkers.load_universe(include_deferred=True)["isin"])
    except Exception:
        linker_isins = set()
    u = u[~u["isin"].isin(linker_isins)]
    tick = u["ticker"].astype(str).str.lower()
    u = u[~(tick.str.contains("i ", na=False) | tick.str.contains("€i", na=False)
            | tick.str.contains("i/l", na=False) | tick.str.contains("ils", na=False))]
    # final net: drop anything Bloomberg flags inflation-linked (catches same-ticker CPI linkers like
    # BTP Italia, ref ITCPIUNR) using cached static where available (bonds without static are never
    # used -- no daily -- and the runtime pool guard filters them regardless).
    def _is_linker(i):
        p = os.path.join(CACHE, "static", f"{i}.parquet")
        if not os.path.exists(p):
            return False
        v = pd.read_parquet(p).iloc[0].get("INFLATION_LINKED_INDICATOR")
        return isinstance(v, str) and v.strip().upper().startswith("Y")
    u = u[~u["isin"].map(_is_linker)]
    dropped = n0 - len(u)
    u = u.dropna(subset=["maturity"]).drop_duplicates("isin").sort_values(["country", "maturity"]).reset_index(drop=True)
    if dropped:
        print(f"  (dropped {dropped} inflation-linked / linker rows that were in the export)")
    os.makedirs(CACHE, exist_ok=True)
    u.to_csv(UNIVERSE_CSV, index=False)
    print(f"  wrote {UNIVERSE_CSV}: {len(u)} nominal bonds from {len(files)} file(s)")
    for c, g in u.groupby("country"):
        iss = pd.to_datetime(g["issue_date"], errors="coerce").dropna()
        print(f"    {c}: {len(g):4d} bonds | maturities {g['maturity'].min().year}-{g['maturity'].max().year}"
              f" | issued {iss.min().year if len(iss) else '?'}-{iss.max().year if len(iss) else '?'}")
    return u


def load():
    if not os.path.exists(UNIVERSE_CSV):
        return pd.DataFrame()
    u = pd.read_csv(UNIVERSE_CSV, dtype={"isin": str})
    u["maturity"] = pd.to_datetime(u["maturity"], errors="coerce")
    u["issue_date"] = pd.to_datetime(u["issue_date"], errors="coerce")
    return u


def needed_isins():
    """The nominals the contemporaneous matcher ACTUALLY selects (via cmt_intl.held_monthly) — pull
    just these (~99) instead of the whole 946-bond universe; picks are identical since the matcher
    only ever holds the closest-maturity nominal per held linker per month."""
    import cmt_intl as cmt                                     # lazy: avoid circular import
    u = load()
    if u.empty:
        return []
    lu = linkers.load_universe(include_deferred=True)
    lmat = {i: pd.Timestamp(m) for i, m in zip(lu["isin"], pd.to_datetime(lu["maturity"]))}
    pools = {c: [(r["isin"], pd.Timestamp(r["maturity"]), pd.Timestamp(r["issue_date"]))
                 for _, r in g.iterrows() if pd.notna(r["maturity"]) and pd.notna(r["issue_date"])]
             for c, g in u.groupby("country")}
    MK = {"IT_BTPEI": "IT", "FR_OATEI": "FR", "FR_OATI": "FR", "ES_EI": "ES", "UK_3M": "GB", "DE_EI": "DE"}
    sel = set()
    for m, c in MK.items():
        pl = pools.get(c, [])
        if not pl:
            continue
        for b, ser in cmt.held_monthly(m).items():
            for mp, li in ser.dropna().items():
                lm = lmat.get(li); ms = mp.to_timestamp(); pe = ms - pd.Timedelta(days=1)
                cands = [n for n in pl if n[2] <= pe and n[1] > ms]
                if cands:
                    sel.add(min(cands, key=lambda n: abs((n[1] - lm).days))[0])
    return sorted(sel)


def pull(skip_existing=True, needed_only=False):
    u = load()
    if u.empty:
        print("  nominal_universe.csv is empty — run `import` first"); return
    isins = needed_isins() if needed_only else sorted(u["isin"])
    print(f"  pulling {len(isins)} nominal bonds "
          f"({'matcher-selected only' if needed_only else 'full universe'}, "
          f"daily+static, skip_existing={skip_existing}) ...")
    dl.pull_isins(isins, skip_existing=skip_existing)


def status():
    u = load()
    print(f"nominal_universe.csv: {len(u)} bonds" + ("" if len(u) else f" (empty — import into {NOMINAL_DIR}/)"))
    if len(u):
        have = sum(os.path.exists(os.path.join(CACHE, "daily", f"{i}.parquet")) for i in u["isin"])
        print(u.groupby("country").size().to_string())
        print(f"daily data pulled for {have}/{len(u)}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "import":
        import_universe()
    elif cmd == "pull":
        pull(skip_existing="--fresh" not in sys.argv, needed_only="--needed" in sys.argv)
    elif cmd == "needed":
        ids = needed_isins(); print("\n".join(ids)); print(f"# {len(ids)} matcher-selected nominals")
    elif cmd == "status":
        status()
    else:
        print(__doc__)
