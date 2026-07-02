"""
DRD Redshift (rome-prod) access — explore the BBG datasets, and (later) the source adapter that lets
the pipeline pull market data from here instead of the Bloomberg terminal.

Read-only. Credentials: the DRD-issued user is hardcoded (not secret); the PASSWORD comes from the
environment (never hardcode it):
    .venv/Scripts/python.exe -m pip install redshift_connector      # already done
    $Env:REDSHIFT_PW="<password from Slack>"                        # per session (like the AWS creds)

Usage:
  python redshift_intl.py schemas                 # list schemas + table counts (needs grant)
  python redshift_intl.py tables <schema>         # tables in a schema (needs grant)
  python redshift_intl.py cols <schema.table>     # columns of a table (needs grant)
  python redshift_intl.py head <schema.table>     # first rows (needs grant)
  python redshift_intl.py sql "select ..."        # ad-hoc query (first 1000 rows)
  # --- catalog probes: work on ANY schema WITHOUT a SELECT grant (pg_catalog is world-readable) ---
  python redshift_intl.py xtables <schema>        # tables in any schema (structure only)
  python redshift_intl.py xcols <schema.table>    # columns of any table (structure only)
  # --- run the MOMENT DRD grants read: proves which feeds are actually populated for us ---
  python redshift_intl.py coverage                # bond/inflation/repo/futures reality check vs our universe

If it hangs/times out: you're probably not on the corp VPN (the cluster is network-restricted).
If it errors about SSL: set  $Env:REDSHIFT_SSL="true"  and retry.
"""
from __future__ import annotations
import os, sys

HOST = "rome-prod.cfzhfitii0ov.us-east-1.redshift.amazonaws.com"
PORT = 5439
DATABASE = "drd"
USER = "aarzhro"
SSL = os.environ.get("REDSHIFT_SSL", "false").lower() == "true"   # DRD's doc uses ssl=False


def connect():
    import redshift_connector
    pw = os.environ.get("REDSHIFT_PW")
    if not pw:
        import getpass
        pw = getpass.getpass("Redshift password: ")
    return redshift_connector.connect(host=HOST, port=PORT, database=DATABASE, user=USER,
                                      password=pw, ssl=SSL)


def query(sql, limit=None):
    """Run SQL, return (columns, rows). Reuses one connection per call (fine for exploring)."""
    c = connect()
    try:
        cur = c.cursor(); cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    finally:
        c.close()
    return cols, (rows[:limit] if limit else rows)


def schemas():
    _, rows = query("select table_schema, count(*) n from information_schema.tables "
                    "group by 1 order by 1")
    print(f"{'schema':45s} tables")
    for s, n in rows:
        print(f"  {s:43s} {n}")


def tables(schema):
    _, rows = query("select table_name from information_schema.tables "
                    f"where table_schema='{schema}' order by 1")
    for (t,) in rows:
        print(" ", t)
    print(f"  ({len(rows)} tables in {schema})")


def cols(schema_table):
    s, t = schema_table.split(".", 1)
    _, rows = query("select column_name, data_type from information_schema.columns "
                    f"where table_schema='{s}' and table_name='{t}' order by ordinal_position")
    for cn, dt in rows:
        print(f"  {cn:32s} {dt}")


def head(schema_table, n=8):
    cols_, rows = query(f"select * from {schema_table} limit {n}")
    print(" | ".join(cols_))
    for r in rows:
        print(" | ".join(str(x) for x in r))


def xtables(schema):
    """Tables/views in ANY schema via the system catalog — works even without a SELECT grant."""
    _, rows = query("select c.relname, c.relkind from pg_class c "
                    "join pg_namespace n on c.relnamespace = n.oid "
                    f"where n.nspname = '{schema}' and c.relkind in ('r', 'v') order by 1")
    for name, kind in rows:
        print(f"  {name:45s} {'view' if kind == 'v' else 'table'}")
    print(f"  ({len(rows)} in {schema})")


def xcols(schema_table):
    """Columns of a table in ANY schema via the system catalog — works even without a SELECT grant."""
    s, t = schema_table.split(".", 1)
    _, rows = query("select a.attname, ty.typname "
                    "from pg_attribute a "
                    "join pg_class c on a.attrelid = c.oid "
                    "join pg_namespace n on c.relnamespace = n.oid "
                    "join pg_type ty on a.atttypid = ty.oid "
                    f"where n.nspname = '{s}' and c.relname = '{t}' "
                    "and a.attnum > 0 and not a.attisdropped order by a.attnum")
    for cn, dt in rows:
        print(f"  {cn:32s} {dt}")
    if not rows:
        print(f"  (no such table {schema_table}, or name misspelled)")


def coverage():
    """Post-grant REALITY CHECK: are the rows we actually need populated in each target table?
    Run this the moment DRD grants read — it verifies every feed with real counts against our
    live universe, so we know feed-by-feed what can leave the terminal vs. what stays. Any block
    that errors = no grant yet on that schema; any block that returns 0 rows = data not there
    (stays on the terminal). Read-only, safe to re-run."""
    import os
    import pandas as pd
    here = os.path.dirname(os.path.abspath(__file__))
    ci = os.path.join(here, "cache_intl")

    def _q(sql):
        try:
            return query(sql)[1]
        except Exception as e:
            print(f"    [no access / error] {type(e).__name__}: {str(e)[:110]}")
            return None

    # 1) BOND PRICES — how many of our linkers+nominals are actually in bloomberg_prices.prices
    print("\n[1] bond prices  ->  bloomberg_prices.prices")
    isins = []
    for f in ("universe.csv", "nominal_universe.csv"):
        p = os.path.join(ci, f)
        if os.path.exists(p):
            isins += pd.read_csv(p)["isin"].dropna().astype(str).tolist()
    isins = sorted({i for i in isins if i.isalnum()})
    print(f"    our universe: {len(isins)} ISINs (linkers + nominals)")
    if isins:
        inlist = ",".join(f"'{i}'" for i in isins)
        r = _q(f"select count(distinct id_isin), min(record_date), max(record_date) "
               f"from bloomberg_prices.prices where id_isin in ({inlist})")
        if r:
            n, mn, mx = r[0]
            pct = round(100 * (n or 0) / len(isins))
            print(f"    PRESENT: {n}/{len(isins)} ISINs ({pct}%)   dates {mn}..{mx}")
        r = _q(f"select id_isin, record_date, px_last, crncy, security_typ "
               f"from bloomberg_prices.prices where id_isin in ({inlist}) and px_last is not null "
               f"order by record_date desc limit 4")
        for row in (r or []):
            print("    sample:", " | ".join(str(x) for x in row))

    # 2) INFLATION INDICES — the three reference series the linkers accrete on
    print("\n[2] inflation indices  ->  bloomberg_per_security.index_prices")
    for tk in ("CPTFEMU Index", "FRCPXTOB Index", "UKRPI Index"):
        r = _q(f"select count(*), min(update_date), max(update_date), max(px_last) "
               f"from bloomberg_per_security.index_prices where ticker = '{tk}'")
        if r:
            n, mn, mx, last = r[0]
            print(f"    {tk:16s} rows={n}  {mn}..{mx}  lastpx~{last}")

    # 3) FINANCING FIXINGS — names are unknown, so discover anything O/N-repo-ish
    print("\n[3] financing fixings  ->  bloomberg_fixings.fixings  (fuzzy: €STR/SONIA/EONIA/GC/repo)")
    where = " or ".join(f"{c} ilike '%{k}%'"
                        for c in ("name", "closename")
                        for k in ("estr", "sonia", "eonia", "repo", "gc pool", " gc"))
    r = _q(f"select distinct closename, name from bloomberg_fixings.fixings where {where} order by 1 limit 40")
    for row in (r or []):
        print("    ", " | ".join(str(x) for x in row))

    # 4) CRUDE / RBOB FUTURES — discover the real ticker format + counts
    print("\n[4] crude / RBOB futures  ->  bloomberg_futures_prices")
    for tbl in ("bloomberg_futures_prices.futures_prices_nonshare",
                "bloomberg_futures_prices.futures_prices_share"):
        short = tbl.split(".")[-1]
        r = _q(f"select ticker, count(*) n, min(settle_dt), max(settle_dt) from {tbl} "
               f"where ticker ilike 'CO_%' or ticker ilike 'CL_%' or ticker ilike 'XB_%' "
               f"or upper(name) like '%BRENT%' or upper(name) like '%WTI%' or upper(name) like '%GASOLINE%' "
               f"group by ticker order by n desc limit 15")
        print(f"    {short}:")
        for row in (r or []):
            print("      ", " | ".join(str(x) for x in row))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "schemas"
    try:
        if cmd == "schemas":
            schemas()
        elif cmd == "tables":
            tables(sys.argv[2])
        elif cmd == "cols":
            cols(sys.argv[2])
        elif cmd == "head":
            head(sys.argv[2])
        elif cmd == "xtables":
            xtables(sys.argv[2])
        elif cmd == "xcols":
            xcols(sys.argv[2])
        elif cmd == "coverage":
            coverage()
        elif cmd == "sql":
            c, r = query(sys.argv[2], limit=1000)
            print(" | ".join(c))
            for row in r:
                print(" | ".join(str(x) for x in row))
        else:
            print(__doc__)
    except Exception as e:
        print(f"[redshift] {type(e).__name__}: {e}")
        print("  -> check VPN (network-restricted), REDSHIFT_PW set, or try $Env:REDSHIFT_SSL=\"true\"")
