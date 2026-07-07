"""
Track 2 (v2 spec) — B as the auction event model (refined pre-auction signal).

Not a model of B's everyday drift (the v1 placebo showed there isn't one) — a
deployable pre-auction expected-outperformance score.

Target: post-auction tenor-matched CM breakeven change (1d primary, 3d/5d secondary),
demeaned within tenor x reopening (as v1).
Features (max ~5 regressors on N~200, no ML):
  z_B_pre        t-10..t-5 mean (v1's established signal, linear)
  z_A_pre        same window — does fundamental cheapness add anything?
  size_surprise  offering vs mean of prior 4 same-tenor auction offerings, -1
  concession_bp  tenor CM BE change t-5..t-1 (control: effect must be NET of the
                 mechanical concession-buyback)
  move_pct1y     MOVE 1y percentile (dealer balance-sheet stress proxy — no primary
                 dealer TIPS position feed in repo; documented)
Specs: M0 = z_B only; M1 = all five; M2 = M1 + z_A x z_B (the single allowed
interaction test).

Inference: WEEK-cluster bootstrap everywhere (auctions cluster; NW is not enough).
OOS scores: expanding refit of M1 before each calendar year >= config.T2_OOS_START,
score that year's auctions -> per-auction expected outperformance + bootstrap CI.

Output: reports/track2_inference.csv, reports/track2_scores.csv
Usage:  python -m breakeven_rv.track2
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, auction_study
from breakeven_rv.validation import cluster_bootstrap_ols

OUT_INF = os.path.join(config.REPORTS, "track2_inference.csv")
OUT_SCORES = os.path.join(config.REPORTS, "track2_scores.csv")

M1_VARS = ["z_B_pre", "z_A_pre", "size_surprise", "concession_bp", "move_pct1y"]


def build() -> pd.DataFrame:
    """v1 auction panel + the Track-2 features."""
    ap = auction_study.build_panel()
    p = panel_mod.load()
    move_pct = p["move"].ffill(limit=5).rolling(252, min_periods=200).rank(pct=True)

    rows = []
    for _, a in ap.iterrows():
        d, ten = a["auctionDate"], a["tenor"]
        be = p[{"5y": "be5", "10y": "be10", "30y": "be30"}[ten]].dropna()
        past = be.loc[:d]
        conc = (past.iloc[-1] - past.iloc[-5]) * 100.0 if len(past) >= 5 else np.nan
        mp = move_pct.loc[:d]
        rows.append({"concession_bp": conc,
                     "move_pct1y": mp.iloc[-1] if len(mp) else np.nan})
    ap = pd.concat([ap.reset_index(drop=True), pd.DataFrame(rows)], axis=1)

    # size surprise vs trailing same-tenor offerings
    ap = ap.sort_values("auctionDate").reset_index(drop=True)
    trail = (ap.groupby("tenor")["size_bn"]
             .transform(lambda s: s.shift(1).rolling(config.T2_SIZE_TRAIL, min_periods=2).mean()))
    ap["size_surprise"] = ap["size_bn"] / trail - 1.0
    ap["week"] = ap["auctionDate"].dt.strftime("%G-%V")
    # NOTE: no two TIPS auctions ever share an ISO week in this sample, so week-cluster
    # == iid bootstrap. Month clusters (auctions ~1-2/month, and the z signal persists
    # across adjacent auctions) are the binding robustness check — reported alongside.
    ap["month"] = ap["auctionDate"].dt.strftime("%Y-%m")
    return ap


def inference(ap: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for h in config.POST_HORIZONS:
        yc = f"post_be_{h}d_dm"
        sub = ap.dropna(subset=[yc] + M1_VARS)
        y = sub[yc]
        specs = {
            "M0_zB": sub[["z_B_pre"]],
            "M1_full": sub[M1_VARS],
            "M2_interaction": sub[M1_VARS].assign(zA_x_zB=sub["z_A_pre"] * sub["z_B_pre"]),
        }
        for name, X in specs.items():
            for cl_name in ("week", "month"):
                t = cluster_bootstrap_ols(y, X, sub[cl_name], n_boot=config.T2_N_BOOT)
                t.insert(0, "cluster", cl_name)
                t.insert(0, "spec", name)
                t.insert(0, "h", h)
                frames.append(t)
    return pd.concat(frames, ignore_index=True)


def oos_scores(ap: pd.DataFrame) -> pd.DataFrame:
    """Expanding annual refit of M1; per-auction OOS score + week-cluster bootstrap CI."""
    yc = "post_be_1d_dm"
    sub = ap.dropna(subset=[yc] + M1_VARS).sort_values("auctionDate").reset_index(drop=True)
    years = sorted(sub["auctionDate"].dt.year.unique())
    start_y = pd.Timestamp(config.T2_OOS_START).year
    rng = np.random.default_rng(7)
    out = []
    for yr in [y for y in years if y >= start_y]:
        tr = sub[sub["auctionDate"].dt.year < yr]
        te = sub[sub["auctionDate"].dt.year == yr]
        if len(tr) < 40 or te.empty:
            continue
        Xtr = sm.add_constant(tr[M1_VARS])
        fit = sm.OLS(tr[yc], Xtr).fit()
        Xte = sm.add_constant(te[M1_VARS], has_constant="add")
        score = Xte @ fit.params
        # bootstrap CI on the score: refit on week-cluster resamples of the train set
        wk_ids = tr["week"].unique()
        groups = {w: tr.index[tr["week"] == w] for w in wk_ids}
        draws = []
        for _ in range(400):
            take = rng.choice(wk_ids, size=len(wk_ids), replace=True)
            b = sub.loc[np.concatenate([groups[w].values for w in take])]
            try:
                bp = sm.OLS(b[yc], sm.add_constant(b[M1_VARS], has_constant="add")).fit().params
                draws.append(Xte @ bp)
            except Exception:
                continue
        dd = pd.DataFrame(draws)
        for i, (ridx, r) in enumerate(te.iterrows()):
            out.append({"auctionDate": r["auctionDate"], "cusip": r["cusip"], "tenor": r["tenor"],
                        "score_bp": float(score.loc[ridx]),
                        "score_ci_lo": float(dd.iloc[:, i].quantile(0.025)) if len(dd) else np.nan,
                        "score_ci_hi": float(dd.iloc[:, i].quantile(0.975)) if len(dd) else np.nan,
                        "realized_bp": r[yc]})
    return pd.DataFrame(out)


def run():
    config.ensure_dirs()
    ap = build()
    inf = inference(ap)
    inf.to_csv(OUT_INF, index=False)
    with pd.option_context("display.width", 200, "display.max_rows", 100):
        print("Week-cluster bootstrap inference (const rows omitted):")
        print(inf[inf["var"] != "const"].round(3).to_string(index=False))

    sc = oos_scores(ap)
    sc.to_csv(OUT_SCORES, index=False)
    v = sc.dropna(subset=["score_bp", "realized_bp"])
    print(f"\nOOS per-auction scores ({config.T2_OOS_START}+): n={len(v)}, "
          f"corr(score, realized 1d)={v['score_bp'].corr(v['realized_bp']):.3f}")
    terc = pd.qcut(v["score_bp"], 3, labels=["low", "mid", "high"])
    print("realized 1d outperformance by OOS score tercile (bp):")
    print(v.groupby(terc, observed=True)["realized_bp"].agg(["mean", "count"]).round(2).to_string())
    ny = v["auctionDate"].dt.year.nunique()
    print(f"\ncapacity: {len(v) / ny:.0f} auctions/year with a usable score — this is an "
          f"episodic overlay, not a standalone book")
    print(f"\n  wrote {OUT_INF}, {OUT_SCORES}")
    return inf, sc


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
