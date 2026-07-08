"""
P1 (v3) — B_bond: the bond-level Residual B, built from traded prices only.

For the OTR and 1st-off-the-run 10y TIPS (the engine's actual holding schedule):
  BE_bond   = nominal yield INTERPOLATED TO THE BOND'S MATURITY DATE (PCHIP over
              H.15 pillars 2/5/7/10/20/30y) − the bond's own quoted real yield
  B         = (BE_bond − ZC swap interpolated to the same maturity date) * 100  [bp]
  B_sa      = B minus an expanding month-of-year seasonal component (min 3 prior
              years of history; zero adjustment before that) — the CPI-NSA seasonal
              carry gap pollutes the raw basis level. With/without shown in the
              appendix figure (reports/figures/b_bond_seasonal.png).
  B_comb    = mean of OTR and 1st-off bonds (averaging two independent quote sets
              halves idiosyncratic quote noise; per-bond columns kept)
  z_B_bond  = B_comb_sa z-scored on trailing 2y mean/vol (same convention as index-B)

Cross-checks written to reports/b_bond_sanity.csv (spec P1 sanity table):
  corr(z_B_bond, z_B_index) full-sample + by year; level mean/vol both versions;
  corr of DAILY CHANGES of the two B levels (flag if > 0.95 — would mean the index
  was never the noise source); corr vs the market-quoted ASW iota (P0.5).

Output: cache/b_bond.parquet
Usage:  python -m breakeven_rv.b_bond [build|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, data_bonds, data_fred, data_bbg, data_asw, residuals
from breakeven_rv.validation import rolling_z

OUT = os.path.join(config.CACHE, "b_bond.parquet")          # 10y (v3 default path)
OUT_SANITY = os.path.join(config.REPORTS, "b_bond_sanity.csv")

SWAP_T = np.array(config.SWAP_TENORS, dtype=float)


def _out_path(tenor: str) -> str:
    return OUT if tenor == "10y" else os.path.join(config.CACHE, f"b_bond_{tenor}.parquet")


def _holding_schedule(tenor: str = "10y") -> pd.DataFrame:
    """Per date: OTR TIPS cusip (engine schedule) + 1st-off-the-run (the previous
    OTR). Also the engine's matched nominal (for the ASW cross-check)."""
    ex = pd.read_csv(os.path.join(config.ROOT, "exports", f"breakeven_{tenor}.csv"),
                     usecols=["date", "TIPS_cusip", "UST_cusip"], parse_dates=["date"]
                     ).set_index("date")
    seq = ex["TIPS_cusip"][ex["TIPS_cusip"] != ex["TIPS_cusip"].shift(1)]
    prev_map = dict(zip(seq.values[1:], seq.values[:-1]))     # cusip -> its predecessor
    ex["off1_cusip"] = ex["TIPS_cusip"].map(prev_map)
    return ex.rename(columns={"TIPS_cusip": "otr_cusip", "UST_cusip": "nom_cusip"})


def _bond_basis(quotes: pd.DataFrame, fred: pd.DataFrame, swaps: pd.DataFrame,
                sched: pd.Series) -> pd.DataFrame:
    """B (bp) for one holding column (cusip per date). Nominal & swap legs both
    interpolated to the bond's actual maturity date."""
    q = quotes.set_index(["date", "cusip"])[["yld", "maturity"]]
    pillars = np.array(list(config.NOMINAL_PILLARS.values()), dtype=float)
    pcols = list(config.NOMINAL_PILLARS.keys())
    scols = [f"swap_{t}y" for t in config.SWAP_TENORS]
    rows = {}
    for d, cusip in sched.dropna().items():
        try:
            yld, mat = q.loc[(d, cusip)]
        except KeyError:
            continue
        if d not in fred.index or d not in swaps.index or pd.isna(mat):
            continue
        nom = fred.loc[d, pcols].astype(float)
        swp = swaps.loc[d, scols].astype(float)
        if nom.isna().any() or swp.isna().any() or pd.isna(yld):
            continue
        tau = (mat - d).days / 365.25
        nom_i = float(PchipInterpolator(pillars, nom.values)(tau))
        swp_i = float(np.interp(tau, SWAP_T, swp.values))
        be = nom_i - float(yld)
        rows[d] = {"be_bond": be, "swap_mat": swp_i, "B": (be - swp_i) * 100.0, "tau": tau}
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def _seasonal_adjust(B: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Expanding month-of-year seasonal component, lookahead-free: for date t, the
    mean of B in the same calendar month of PRIOR years minus the all-prior mean.
    Zero until config.SEASONAL_MIN_MONTHS of history exists."""
    monthly = B.resample("ME").mean()
    seas = pd.Series(0.0, index=B.index)
    for t_idx, t in enumerate(B.index):
        hist = monthly[monthly.index < t.replace(day=1)]
        if len(hist) < config.SEASONAL_MIN_MONTHS:
            continue
        same = hist[hist.index.month == t.month]
        if len(same) >= 3:
            seas.iloc[t_idx] = same.mean() - hist.mean()
    return B - seas, seas


def build(tenor: str = "10y"):
    config.ensure_dirs()
    quotes = data_bonds.load()
    quotes = quotes[quotes["leg"] == "tips"]
    fred = data_fred.load()
    swaps = data_bbg.load()
    sched = _holding_schedule(tenor)

    parts = {}
    for name, col in (("otr", "otr_cusip"), ("off1", "off1_cusip")):
        parts[name] = _bond_basis(quotes, fred, swaps, sched[col])
        print(f"  {tenor} {name}: {len(parts[name])} days  "
              f"{parts[name].index.min().date()} -> {parts[name].index.max().date()}")

    out = pd.DataFrame(index=parts["otr"].index.union(parts["off1"].index))
    out["B_otr"] = parts["otr"]["B"]
    out["B_off1"] = parts["off1"]["B"]
    out["be_bond_otr"] = parts["otr"]["be_bond"]
    out["B_comb"] = out[["B_otr", "B_off1"]].mean(axis=1)
    out["B_comb_sa"], out["seasonal"] = _seasonal_adjust(out["B_comb"].dropna())
    out["z_B_bond"] = rolling_z(out["B_comb_sa"], config.Z_WINDOW, config.Z_MIN_PERIODS)
    out["z_B_bond_otr"] = rolling_z(_seasonal_adjust(out["B_otr"].dropna())[0],
                                    config.Z_WINDOW, config.Z_MIN_PERIODS)
    out["otr_cusip"] = sched["otr_cusip"].reindex(out.index)
    out["off1_cusip"] = sched["off1_cusip"].reindex(out.index)
    # v5: flag seasonal amplitude (5y seasonality expected LARGER — construction check)
    seas_amp = out["seasonal"].max() - out["seasonal"].min()
    print(f"  {tenor} seasonal amplitude (max-min of month effects): {seas_amp:.1f}bp")
    out.to_parquet(_out_path(tenor))
    if tenor != "10y":
        print(f"  wrote {_out_path(tenor)}")
        return out

    # ---- sanity table (spec P1) ----
    res = residuals.load()
    from breakeven_rv import panel as panel_mod
    p = panel_mod.load()
    B_idx = p["iota10"]                     # index-version basis level (bp)
    zb_idx = res["z_B"]
    df = pd.DataFrame({"z_bond": out["z_B_bond"], "z_index": zb_idx,
                       "B_bond": out["B_comb_sa"], "B_index": B_idx}).dropna()
    rows = [{"metric": "corr_z_levels_full", "value": df["z_bond"].corr(df["z_index"]), "n": len(df)}]
    for y, sub in df.groupby(df.index.year):
        if len(sub) > 100:
            rows.append({"metric": f"corr_z_levels_{y}", "value": sub["z_bond"].corr(sub["z_index"]), "n": len(sub)})
    dchg = df[["B_bond", "B_index"]].diff().dropna()
    chg_corr = dchg["B_bond"].corr(dchg["B_index"])
    rows += [
        {"metric": "corr_B_daily_changes", "value": chg_corr, "n": len(dchg)},
        {"metric": "B_bond_mean_bp", "value": df["B_bond"].mean(), "n": len(df)},
        {"metric": "B_bond_vol_bp", "value": df["B_bond"].std(), "n": len(df)},
        {"metric": "B_index_mean_bp", "value": df["B_index"].mean(), "n": len(df)},
        {"metric": "B_index_vol_bp", "value": df["B_index"].std(), "n": len(df)},
    ]
    # ASW cross-check: iota_asw = asw_tips − asw_nominal (TIPS cheap -> wide -> B low)
    asw = data_asw.load()
    aw = asw.set_index(["date", "cusip"])["asw"]
    iota_asw = {}
    for d, r in sched.iterrows():
        try:
            iota_asw[d] = aw.loc[(d, r["otr_cusip"])] - aw.loc[(d, r["nom_cusip"])]
        except KeyError:
            continue
    ia = pd.Series(iota_asw).sort_index()
    both = pd.DataFrame({"B": out["B_comb_sa"], "ia": ia}).dropna()
    dboth = both.diff().dropna()
    rows += [
        {"metric": "corr_B_vs_aswIota_level", "value": both["B"].corr(both["ia"]), "n": len(both)},
        {"metric": "corr_B_vs_aswIota_changes", "value": dboth["B"].corr(dboth["ia"]), "n": len(dboth)},
    ]
    sanity = pd.DataFrame(rows)
    sanity.to_csv(OUT_SANITY, index=False)
    print(sanity.round(3).to_string(index=False))
    if chg_corr > 0.95:
        print("  *** FLAG (spec P1): daily-change corr > 0.95 — index-B was NOT the noise "
              "source; the artifact story needs rethinking before proceeding ***")
    print(f"  wrote {OUT}, {OUT_SANITY}")
    return out


def load(tenor: str = "10y"):
    path = _out_path(tenor)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} missing — run: python -m breakeven_rv.b_bond build {tenor}")
    return pd.read_parquet(path)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build(sys.argv[2] if len(sys.argv) > 2 else "10y")
