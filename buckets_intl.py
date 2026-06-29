"""
Constant-maturity bucket assessment for the linkers (the desk's proposed buckets).

READ-ONLY analysis — does NOT feed pricing or rolls. Classifies every auction and every bond-month
by REMAINING tenor (time-to-maturity at that point), so a bond rolls DOWN through buckets as it ages.
Produces, per market:
  * a coverage summary (auctions per bucket + bond-presence continuity), and
  * a bucket x year OCCUPANCY GRID (which bond, by maturity year, sits in each bucket at mid-year).

Buckets (remaining tenor, desk proposal):
  2y=2-3, 5y=4-5, 7y=6-8, 10y=9-11, 12y=12-13, 15y=14-17, 20y=18-22, 25y=23-27, 30y=28+   (EUR std)
  France/UK long end instead of a single 30y:  30y=28-34, 40y=35-44, 50y=45+

Usage:  python buckets_intl.py    # -> exports/intl_bucket_grid.md + exports/intl_bucket_grid.csv
"""
from __future__ import annotations
import os, sys
import pandas as pd

import linkers
import issuance_intl as iss

EXPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
ORDER = ['2y', '5y', '7y', '10y', '12y', '15y', '20y', '25y', '30y', '40y', '50y']
TARGET = {'2y': 2.5, '5y': 4.5, '7y': 7, '10y': 10, '12y': 12.5, '15y': 15.5,
          '20y': 20, '25y': 25, '30y': 31, '40y': 40, '50y': 50}
MARKETS = {'IT_BTPEI': 'Italy BTP€i', 'FR_OATEI': 'France OAT€i', 'FR_OATI': 'France OATi',
           'ES_EI': 'Spain SPGB€i', 'DE_EI': 'Germany Bund€i', 'UK_3M': 'UK gilt'}
FR_STYLE = {'FR_OATEI', 'FR_OATI', 'UK_3M'}        # markets that split the long end into 30/40/50
YEARS = list(range(2010, 2027))


def bucket(y, fr=False):
    """Remaining-tenor (years) -> bucket label, or None if < ~1.5y. fr=True splits 30y into 30/40/50."""
    if y != y or y < 1.5:
        return None
    r = round(y)
    if r <= 3: return '2y'
    if r <= 5: return '5y'
    if r <= 8: return '7y'
    if r <= 11: return '10y'
    if r <= 13: return '12y'
    if r <= 17: return '15y'
    if r <= 22: return '20y'
    if r <= 27: return '25y'
    if not fr: return '30y'
    if r <= 34: return '30y'
    if r <= 44: return '40y'
    return '50y'


def _ctx():
    u = linkers.load_universe(); u['mat'] = pd.to_datetime(u['maturity'])
    ev = iss.events()
    iss_of = ev.groupby('isin')['event_date'].min()        # first event = issue (robust vs blank first_issue)
    matof = dict(zip(u['isin'], u['mat']))
    ev = ev.copy(); ev['mat'] = pd.to_datetime(ev['isin'].map(matof))
    ev['rem'] = (ev['mat'] - ev['event_date']).dt.days / 365.25
    return ev, iss_of, matof


def coverage_df(market, ev, iss_of, matof):
    fr = market in FR_STYLE
    e = ev[ev['market'] == market].copy()
    e['bkt'] = e['rem'].apply(lambda y: bucket(y, fr))
    isins = sorted(set(e['isin']))
    pres = {b: 0 for b in ORDER}
    mgrid = pd.period_range(f'{YEARS[0]}-01', f'{YEARS[-1]}-12', freq='M')
    for mo in mgrid:
        mid = mo.to_timestamp('M'); bks = set()
        for i in isins:
            isd, mat = iss_of.get(i), matof.get(i)
            if pd.notna(isd) and pd.notna(mat) and isd <= mid <= mat:
                b = bucket((mat - mid).days / 365.25, fr)
                if b: bks.add(b)
        for b in bks:
            pres[b] += 1
    rows = []
    for b in ORDER:
        sub = e[e['bkt'] == b]
        if sub.empty and pres[b] == 0:
            continue
        rows.append({'bucket': b, 'auctions_all': len(sub),
                     'auctions_21_26': int((sub['event_date'] >= '2021-01-01').sum()),
                     'bonds': sub['isin'].nunique(), 'presence_pct': round(100 * pres[b] / len(mgrid))})
    return pd.DataFrame(rows)


def grid_df(market, iss_of, matof):
    fr = market in FR_STYLE
    isins = sorted(set(iss.events().query("market == @market")['isin']))
    g = {b: {} for b in ORDER}
    for yr in YEARS:
        mid = pd.Timestamp(yr, 6, 30); inb = {b: [] for b in ORDER}
        for i in isins:
            isd, mat = iss_of.get(i), matof.get(i)
            if pd.isna(isd) or pd.isna(mat) or not (isd <= mid <= mat):
                continue
            rem = (mat - mid).days / 365.25; b = bucket(rem, fr)
            if b: inb[b].append((abs(rem - TARGET[b]), mat.year, i))
        for b in ORDER:
            if inb[b]:
                inb[b].sort(); g[b][yr] = str(inb[b][0][1])[2:] + ('*' if len(inb[b]) > 1 else '')
            else:
                g[b][yr] = '·'
    df = pd.DataFrame(g).T.reindex(ORDER)
    df = df.loc[[b for b in ORDER if not (df.loc[b] == '·').all()]]
    df.columns = [f"'{str(y)[2:]}" for y in YEARS]
    return df


def write_report():
    os.makedirs(EXPORTS, exist_ok=True)
    ev, iss_of, matof = _ctx()
    md = ["# Constant-maturity bucket occupancy & coverage (linkers)", "",
          "Classified by **remaining tenor** (time-to-maturity), so a bond rolls *down* through buckets as it ages.",
          "**Buckets (remaining yrs):** 2y=2-3, 5y=4-5, 7y=6-8, 10y=9-11, 12y=12-13, 15y=14-17, 20y=18-22, "
          "25y=23-27, 30y=28+ (EUR). France/UK split the long end: 30y=28-34, 40y=35-44, 50y=45+.", "",
          "**Occupancy grid** — each cell = the maturity year (YY) of the bond nearest the bucket's tenor at "
          "**mid-year (30 Jun)**; `·` = no bond in range (a constant-maturity *gap*); `*` = 2+ bonds available "
          "(you have a choice). Follow a row left→right to see the roll (the label steps up as new lines issue).", ""]
    csv_rows = []
    for m, label in MARKETS.items():
        cov = coverage_df(m, ev, iss_of, matof)
        grid = grid_df(m, iss_of, matof)
        md += [f"## {label}{'  — FR/UK long-end buckets' if m in FR_STYLE else ''}", "",
               "**Coverage** (auctions_all / since-2021 / #bonds / % months 2010-26 with a live bond):", "",
               "```", cov.to_string(index=False) if len(cov) else "(none)", "```", "",
               "**Occupancy grid (bucket × year):**", "",
               "```", grid.to_string() if len(grid) else "(none)", "```", ""]
        for _, r in cov.iterrows():
            csv_rows.append({'market': m, **r.to_dict()})
    path_md = os.path.join(EXPORTS, "intl_bucket_grid.md")
    with open(path_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    path_csv = os.path.join(EXPORTS, "intl_bucket_grid.csv")
    pd.DataFrame(csv_rows).to_csv(path_csv, index=False)
    print(f"  wrote {path_md}")
    print(f"  wrote {path_csv}  (per-market/bucket coverage)")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    write_report()
