"""
P2 (v3) — the decisive experiment: transfer re-test + Track 2 rebuild on z_B_bond.

Signal: z_B_bond (bond-built basis, b_bond.py), lagged t-10..t-5 mean as always.
z_B_bond is 10y-sector-level (B_bond is built on the 10y OTR/off1); it is applied to
all tenors' auctions as the sector signal, with a 10y-only subsample reported
alongside (documented deviation from v2's tenor-matched index-z).

Pieces (all -> reports/):
  1. v3_transfer.csv    the DECISION TABLE: post-auction 1d effect of z_B_bond in the
                        same four measurement spaces as v2. Pre-registered verdicts:
                        (a) bond-level slopes real -> strategy real; (b) index-only ->
                        artifact closure; (c) nothing -> weaker closure.
  2. v3_track2_inference.csv  Track 2 rebuilt: outcomes = CM-index 1d (v2 comparability)
                        AND financed OTR return t0->t+1 (the tradeable object, decision-
                        relevant). Controls: z_A_pre, size surprise, bond-space
                        concession (cum financed OTR return t-5..t-1), dealer positions
                        (P0.2, vintage-safe, replaces MOVE). Week+month cluster
                        bootstrap. Dealer-absorption test (P5.1) = M1 with/without the
                        dealer control.
  3. v3_track2_scores.csv     expanding-annual OOS scores on the bond-space outcome.
  4. v3_autopsy.csv     index-artifact autopsy: event-relative profile of
                        (CM-index BE − bond-built BE) around auctions (t-5..t+5 vs a
                        t-20..t-11 baseline).
  5. v3_decay.csv       signal decay: transfer slope (bond + index outcome) with z
                        measured at single lags t-1/-3/-5/-10.

Usage:  python -m breakeven_rv.v3_experiment
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, residuals, data_auctions, b_bond, data_dealer
from breakeven_rv.validation import cluster_bootstrap_ols

R = config.REPORTS
M1_VARS = ["z_B_bond_pre", "z_A_pre", "size_surprise", "concession_bond_bp", "dealer_pos_z"]


def _rets(tenor):
    return pd.read_parquet(os.path.join(config.ROOT_CACHE, f"returns_{tenor}.parquet"))["r_BE_bp"].dropna()


def _lagmean(s: pd.Series, date, lo, hi):
    win = s.dropna().loc[:date - pd.Timedelta(days=1)].tail(lo)
    return float(win.iloc[: lo - hi + 1].mean()) if len(win) >= lo else np.nan


def build_panel() -> pd.DataFrame:
    """Auction panel with the v3 signal + controls. One row per auction >= z availability."""
    p = panel_mod.load()
    res = residuals.load()
    bb = b_bond.load()
    auc = data_auctions.load()
    lo, hi = config.AUCTION_LAG
    zb = bb["z_B_bond"].dropna()
    dealer = data_dealer.load_daily(p.index)
    rets = {t: _rets(t) for t in ("5y", "10y", "30y")}
    becol = {"5y": "be5", "10y": "be10", "30y": "be30"}

    rows = []
    for _, a in auc[auc["auctionDate"] >= zb.index.min()].iterrows():
        d, ten = a["auctionDate"], a["tenor"]
        r = rets[ten]
        be = p[becol[ten]].dropna()
        d0r = r.index.searchsorted(d, side="right") - 1
        d0b = be.index.searchsorted(d, side="right") - 1
        if d0r < 6 or d0r + 3 >= len(r) or d0b < 1 or d0b + 3 >= len(be):
            continue
        # outcomes
        post_bond = {f"post_ret_{h}d": float(r.iloc[d0r + 1: d0r + 1 + h].sum())
                     for h in (1, 3)}
        post_cm = {f"post_cm_{h}d": float((be.iloc[d0b + h] - be.iloc[d0b]) * 100)
                   for h in (1, 3)}
        # signal + controls, all known before t-5 close / vintage-safe
        rows.append({
            "auctionDate": d, "cusip": a["cusip"], "tenor": ten,
            "is_reopening": bool(a["is_reopening"]),
            "z_B_bond_pre": _lagmean(zb, d, lo, hi),
            "z_A_pre": _lagmean(res["z_A"], d, lo, hi),
            "size_surprise": np.nan,      # filled below (needs trailing group means)
            "size_bn": a["offeringAmount"] / 1e9 if pd.notna(a["offeringAmount"]) else np.nan,
            "concession_bond_bp": float(r.iloc[d0r - 4: d0r].sum()),   # close t-5 -> close t-1, bond space
            "dealer_pos_z": float(dealer["total_z"].reindex([r.index[d0r - 5]]).iloc[0])
                            if r.index[d0r - 5] in dealer.index else np.nan,
            **post_bond, **post_cm,
        })
    ap = pd.DataFrame(rows).sort_values("auctionDate").reset_index(drop=True)
    trail = ap.groupby("tenor")["size_bn"].transform(
        lambda s: s.shift(1).rolling(config.T2_SIZE_TRAIL, min_periods=2).mean())
    ap["size_surprise"] = ap["size_bn"] / trail - 1.0
    for c in ("post_ret_1d", "post_ret_3d", "post_cm_1d", "post_cm_3d"):
        ap[f"{c}_dm"] = ap[c] - ap.groupby(["tenor", "is_reopening"])[c].transform("mean")
    ap["week"] = ap["auctionDate"].dt.strftime("%G-%V")
    ap["month"] = ap["auctionDate"].dt.strftime("%Y-%m")
    return ap


def transfer_table() -> pd.DataFrame:
    """The decision table: z_B_bond's post-auction 1d slope in the four v2 spaces."""
    p = panel_mod.load()
    bb = b_bond.load()
    auc = data_auctions.load()
    zb = bb["z_B_bond"].dropna()
    becol = {"5y": "be5", "10y": "be10", "30y": "be30"}
    ycache = {}

    def yld(cusip):
        if cusip not in ycache:
            f = os.path.join(config.ROOT_CACHE, "daily", f"{cusip}.parquet")
            ycache[cusip] = pd.read_parquet(f)["YLD_YTM_MID"].dropna() if os.path.exists(f) else None
        return ycache[cusip]

    lo, hi = config.AUCTION_LAG
    r1, r2, r3 = [], [], []
    bebd = (bb["be_bond_otr"] * 100).dropna()
    for _, a in auc[auc["auctionDate"] >= zb.index.min()].iterrows():
        ten = a["tenor"]
        be = p[becol[ten]].dropna()
        r = _rets(ten)
        zp = _lagmean(zb, a["auctionDate"], lo, hi)
        if not np.isfinite(zp):
            continue
        d0 = be.index.searchsorted(a["auctionDate"], side="right") - 1
        i0 = r.index.searchsorted(a["auctionDate"], side="right") - 1
        if d0 >= 1 and d0 + 1 < len(be) and i0 + 1 < len(r):
            r1.append({"z": zp, "tenor": ten,
                       "dcm": (be.iloc[d0 + 1] - be.iloc[d0]) * 100, "dotr": r.iloc[i0 + 1]})
        y = yld(a["cusip"])
        if y is not None and np.isfinite(a["highYield"]):
            j0 = y.index.searchsorted(a["auctionDate"], side="right") - 1
            if 0 <= j0 < len(y) - 1:
                r2.append({"z": zp, "tenor": ten,
                           "dyld": (y.iloc[j0 + 1] - a["highYield"]) * 100})
        if ten == "10y":
            k0 = bebd.index.searchsorted(a["auctionDate"], side="right") - 1
            if k0 >= 1 and k0 + 1 < len(bebd):
                r3.append({"z": zp, "dbe_bond": bebd.iloc[k0 + 1] - bebd.iloc[k0]})

    rows = []
    for name, data, col, sub10 in (("cm_index", r1, "dcm", True),
                                   ("financed_otr_return", r1, "dotr", True),
                                   ("auctioned_bond_yld_vs_stop", r2, "dyld", True),
                                   ("bond_built_BE (10y)", r3, "dbe_bond", False)):
        df = pd.DataFrame(data).dropna(subset=["z", col])
        for label, d in ([(name, df)] +
                         ([(f"{name} [10y only]", df[df["tenor"] == "10y"])] if sub10 else [])):
            if len(d) < 20:
                continue
            X = sm.add_constant(d["z"])
            fit = sm.OLS(d[col], X).fit(cov_type="HC1")
            rows.append({"measure": label, "n": len(d), "slope_on_zB_bond": fit.params["z"],
                         "t": fit.tvalues["z"], "uncond_mean_bp": d[col].mean()})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v3_transfer.csv"), index=False)
    return out


def track2_inference(ap: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for yc in ("post_ret_1d_dm", "post_cm_1d_dm", "post_ret_3d_dm", "post_cm_3d_dm"):
        sub = ap.dropna(subset=[yc, "z_B_bond_pre", "z_A_pre", "size_surprise", "concession_bond_bp"])
        specs = {"M0_zB_bond": ["z_B_bond_pre"],
                 "M1_no_dealer": ["z_B_bond_pre", "z_A_pre", "size_surprise", "concession_bond_bp"]}
        if sub["dealer_pos_z"].notna().sum() > 60:
            specs["M1_full"] = M1_VARS
        for name, cols in specs.items():
            s2 = sub.dropna(subset=cols)
            for cl in ("week", "month"):
                t = cluster_bootstrap_ols(s2[yc], s2[cols], s2[cl], n_boot=config.T2_N_BOOT)
                t.insert(0, "cluster", cl)
                t.insert(0, "spec", name)
                t.insert(0, "outcome", yc)
                frames.append(t)
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(os.path.join(R, "v3_track2_inference.csv"), index=False)
    return out


def oos_scores(ap: pd.DataFrame) -> pd.DataFrame:
    """Expanding annual refits on the BOND-space outcome (the tradeable object)."""
    yc = "post_ret_1d_dm"
    cols = ["z_B_bond_pre", "z_A_pre", "size_surprise", "concession_bond_bp"]
    sub = ap.dropna(subset=[yc] + cols).reset_index(drop=True)
    out = []
    for yr in sorted(sub["auctionDate"].dt.year.unique()):
        tr = sub[sub["auctionDate"].dt.year < yr]
        te = sub[sub["auctionDate"].dt.year == yr]
        if len(tr) < 40 or te.empty:
            continue
        fit = sm.OLS(tr[yc], sm.add_constant(tr[cols])).fit()
        score = sm.add_constant(te[cols], has_constant="add") @ fit.params
        for ridx, r in te.iterrows():
            out.append({"auctionDate": r["auctionDate"], "cusip": r["cusip"], "tenor": r["tenor"],
                        "score_bp": float(score.loc[ridx]), "realized_bp": r[yc]})
    sc = pd.DataFrame(out)
    sc.to_csv(os.path.join(R, "v3_track2_scores.csv"), index=False)
    return sc


def autopsy() -> pd.DataFrame:
    """Event-relative profile of (CM-index BE − bond-built BE), 10y auctions."""
    p = panel_mod.load()
    bb = b_bond.load()
    auc = data_auctions.load()
    spread = (p["be10"] * 100 - bb["be_bond_otr"] * 100).dropna()
    prof = {}
    for _, a in auc[auc["tenor"] == "10y"].iterrows():
        d0 = spread.index.searchsorted(a["auctionDate"], side="right") - 1
        if d0 < 20 or d0 + 6 >= len(spread):
            continue
        base = spread.iloc[d0 - 20: d0 - 10].mean()
        for k in range(-5, 6):
            prof.setdefault(k, []).append(spread.iloc[d0 + k] - base)
    rows = [{"event_day": k, "mean_spread_vs_base_bp": np.mean(v),
             "se": np.std(v) / np.sqrt(len(v)), "n": len(v)} for k, v in sorted(prof.items())]
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v3_autopsy.csv"), index=False)
    return out


def decay(ap: pd.DataFrame) -> pd.DataFrame:
    """Transfer slope with z_B_bond measured at single lags before the auction."""
    bb = b_bond.load()
    zb = bb["z_B_bond"].dropna()
    rows = []
    for lag in config.V3_DECAY_LAGS:
        zl = []
        for d in ap["auctionDate"]:
            win = zb.loc[:d - pd.Timedelta(days=1)].tail(lag)
            zl.append(win.iloc[0] if len(win) == lag else np.nan)
        ap[f"z_lag{lag}"] = zl
        for yc in ("post_ret_1d_dm", "post_cm_1d_dm"):
            d = ap.dropna(subset=[yc, f"z_lag{lag}"])
            fit = sm.OLS(d[yc], sm.add_constant(d[f"z_lag{lag}"])).fit(cov_type="HC1")
            rows.append({"lag_bd": lag, "outcome": yc, "beta": fit.params.iloc[1],
                         "t": fit.tvalues.iloc[1], "n": len(d)})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v3_decay.csv"), index=False)
    return out


def run():
    config.ensure_dirs()
    print("=== transfer decision table (signal = z_B_bond, t-10..t-5) ===")
    tt = transfer_table()
    print(tt.round(2).to_string(index=False))

    ap = build_panel()
    print(f"\n=== Track 2 rebuild: {len(ap)} auctions "
          f"({ap['auctionDate'].min().date()} .. {ap['auctionDate'].max().date()}) ===")
    inf = track2_inference(ap)
    show = inf[(inf["var"] != "const") & (inf["cluster"] == "week")
               & (inf["outcome"].str.contains("1d"))]
    print(show.round(3).to_string(index=False))

    sc = oos_scores(ap)
    if len(sc):
        print(f"\nOOS scores (bond-space outcome): n={len(sc)}, "
              f"corr={sc['score_bp'].corr(sc['realized_bp']):.3f}")
        terc = pd.qcut(sc["score_bp"], 3, labels=["low", "mid", "high"])
        print(sc.groupby(terc, observed=True)["realized_bp"].agg(["mean", "count"]).round(2).to_string())

    print("\n=== index-artifact autopsy: (CM index − bond-built) BE spread around 10y auctions ===")
    au = autopsy()
    print(au.round(2).to_string(index=False))

    print("\n=== signal decay profile ===")
    dc = decay(ap)
    print(dc.round(3).to_string(index=False))

    # verdict
    bond_rows = tt[tt["measure"].isin(["financed_otr_return", "auctioned_bond_yld_vs_stop",
                                       "bond_built_BE (10y)"])]
    idx_row = tt[tt["measure"] == "cm_index"]
    bond_sig = (bond_rows["t"].abs() > 2).any()
    idx_sig = (idx_row["t"].abs() > 2).any()
    verdict = "(a) BOND-LEVEL EFFECT REAL" if bond_sig else \
              ("(b) INDEX-ONLY -> ARTIFACT CLOSURE" if idx_sig else "(c) NOTHING PREDICTS -> CLOSURE (weaker wording)")
    print(f"\n*** PRE-REGISTERED VERDICT: {verdict} ***")
    return tt, inf, sc, au, dc, verdict


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
