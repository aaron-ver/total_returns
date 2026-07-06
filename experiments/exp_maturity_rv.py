"""
EXPERIMENT 3 — maturity-month seasonal RV (Barclays guide): because inflation accretion is
seasonal, bonds maturing in different calendar months carry structurally different seasonal
accrual — e.g. TIPS July maturities capture the strong H1 accrual into maturity and trade rich
vs Jan/Apr; UK March issues cheap vs November. If the market misprices the seasonal vector, the
COHORT (maturity-month) of a bond predicts parts of its calendar-month return profile.

Test on our intl per-bond financed return sheets (bp = DV01-normalized, financing netted):
per market, per calendar month: mean monthly bp of each maturity-month cohort MINUS the market
mean that month (cross-sectional demeaning kills market-wide moves), t-stat across years.

Run:  .venv/Scripts/python.exe experiments/exp_maturity_rv.py
Out:  experiments/out/maturity_rv_cells.csv (all market x cohort x cal-month cells),
      maturity_rv_summary.csv (top |t| cells) + printed summary.
"""
from __future__ import annotations
import exp_common as C
import numpy as np
import pandas as pd

MIN_YEARS = 5          # a cohort/cal-month cell needs >= this many yearly observations
MIN_BONDS = 2          # cohort must have >= this many bonds in the month to count


def monthly_panel(market, u):
    """DataFrame [month x isin] of monthly summed bp for one market's bonds."""
    cols = {}
    for isin in u.loc[u["market"] == market, "isin"]:
        r = C.bond_returns(isin)
        if r is None or "bp" not in r:
            continue
        s = pd.to_numeric(r["bp"], errors="coerce").dropna()
        if len(s) < 200:
            continue
        cols[isin] = s.groupby(s.index.to_period("M")).sum()
    return pd.DataFrame(cols)


def run():
    u = C.intl_universe()
    cohort_of = dict(zip(u["isin"], u["maturity"].dt.month))
    cells = []
    for mkt in sorted(u["market"].unique()):
        pan = monthly_panel(mkt, u)
        if pan.shape[1] < 4:
            continue
        rel = pan.sub(pan.mean(axis=1), axis=0)               # vs market mean that month
        coh = pd.Series({i: cohort_of.get(i) for i in pan.columns})
        for cm in range(1, 13):                                # calendar month
            sub = rel[rel.index.month == cm]
            if sub.empty:
                continue
            for cval in sorted(coh.dropna().unique()):
                ids = coh[coh == cval].index
                block = sub[ids].dropna(how="all")
                per_year = block.mean(axis=1).dropna()         # cohort-mean rel bp, one obs per year
                nb = block.notna().sum(axis=1)
                per_year = per_year[nb >= MIN_BONDS]
                if len(per_year) < MIN_YEARS:
                    continue
                t = per_year.mean() / (per_year.std(ddof=1) / np.sqrt(len(per_year))) \
                    if per_year.std(ddof=1) > 0 else np.nan
                cells.append({"market": mkt, "cohort_matmonth": int(cval), "cal_month": cm,
                              "mean_rel_bp": round(per_year.mean(), 2), "t": round(t, 2),
                              "n_years": len(per_year)})
    df = pd.DataFrame(cells)
    df.to_csv(f"{C.OUT}/maturity_rv_cells.csv", index=False)
    top = df[np.abs(df["t"]) >= 2].sort_values("t", key=np.abs, ascending=False)
    top.to_csv(f"{C.OUT}/maturity_rv_summary.csv", index=False)
    print(f"== maturity-month seasonal RV: {len(df)} cells, {len(top)} with |t|>=2 ==")
    print(top.head(25).to_string(index=False) if len(top) else df.head(15).to_string(index=False))

    # the guide's specific claim, in our data: do July-maturity cohorts earn their seasonal accrual
    # in H1 vs H2 relative to the market?
    if len(df):
        jul = df[df["cohort_matmonth"] == 7]
        if len(jul):
            h1 = jul[jul["cal_month"] <= 6]["mean_rel_bp"].mean()
            h2 = jul[jul["cal_month"] > 6]["mean_rel_bp"].mean()
            print(f"\n  July-maturity cohorts, mean rel bp: H1 {h1:+.2f} vs H2 {h2:+.2f} "
                  f"(guide: H1 accrual capture -> H1 > H2)")
    print(f"\n  cells -> {C.OUT}/maturity_rv_cells.csv | read: mean_rel_bp = cohort's mean monthly"
          f"\n  bp vs its market in that calendar month (cross-sectionally demeaned), t across years.")
    return df


if __name__ == "__main__":
    if C.sys.platform == "win32":
        C.sys.stdout.reconfigure(encoding="utf-8")
    run()
