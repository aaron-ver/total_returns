"""
Track 1 (v2 spec) — A as the everyday mean-reversion engine: conditioning model.

Target: h-day forward change in residual A, h in config.T1_HORIZONS (matched to the
~14bd half-life). Features (deliberately small, no kitchen sink):
  z_A                      the base predictor
  b_confirm / b_contradict is B confirming (same-sign |z_B|>1), contradicting
                           (opposite-sign |z_B|>1), or neutral — the validated
                           quadrant diagnostic, plus their interactions with z_A
                           (the hypothesis IS an interaction: A-reversion stronger
                           when B confirms)
  energy_share20           share of the last 20d swap-BE move explained by the
                           gasoline factor (proxy — no core-BE/fixings data in repo;
                           documented in IMPLEMENTATION.md). 0 when the 20d move
                           is < 5bp (nothing to attribute); clipped to [-1, 2].
  move_pct1y               MOVE 1y rolling percentile (stress regime)
  auction_prox             TIPS auction within +/-5bd (CONTROL — auctions are B's
                           arena; keep Track 1 from harvesting the auction effect)

Method: walk-forward ridge (expanding window, annual refits, h-day purge between
train and test so overlapping targets never leak). Baseline = z_A alone, same
protocol — the question is OOS LIFT over the baseline, not fit. A depth-2 GBM is
fitted ONLY if the ridge shows lift > config.T1_GBM_GATE, and must beat the ridge
in a 20d-block bootstrap to be kept (v2 spec discipline).

Output: reports/track1_oos.csv (metrics), reports/track1_stability.csv (per-refit
standardized coefs).  Usage:  python -m breakeven_rv.track1
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, layer1, residuals, data_auctions

OUT_OOS = os.path.join(config.REPORTS, "track1_oos.csv")
OUT_STAB = os.path.join(config.REPORTS, "track1_stability.csv")

FEATURES = ["z_A", "b_confirm", "b_contradict", "zA_x_confirm", "zA_x_contradict",
            "energy_share20", "move_pct1y", "auction_prox"]


def build_features() -> pd.DataFrame:
    p = panel_mod.load()
    res = residuals.load()
    l1 = layer1.load("swap10")
    auc = data_auctions.load()
    f = pd.DataFrame(index=res.index)
    zA, zB = res["z_A"], res["z_B"]
    f["z_A"] = zA
    conf = (zB.abs() > config.Z_THRESHOLD) & (np.sign(zB) == np.sign(zA))
    cont = (zB.abs() > config.Z_THRESHOLD) & (np.sign(zB) == -np.sign(zA))
    f["b_confirm"] = conf.astype(float)
    f["b_contradict"] = cont.astype(float)
    f["zA_x_confirm"] = zA * f["b_confirm"]
    f["zA_x_contradict"] = zA * f["b_contradict"]
    # energy attribution proxy: gasoline-factor contribution to the 20d swap-BE move
    dy20 = (p["swap_10y"] * 100.0).diff(20)
    gas20 = (l1["beta_log_gas"] * p["log_gas"].diff(20) * 100.0).reindex(f.index)
    share = (gas20 / dy20).clip(-1.0, 2.0)
    f["energy_share20"] = share.where(dy20.abs() >= 5.0, 0.0)
    f["move_pct1y"] = (p["move"].ffill(limit=5)
                       .rolling(252, min_periods=200).rank(pct=True).reindex(f.index))
    # auction proximity: any real TIPS auction within +/-5bd of t
    prox = pd.Series(0.0, index=f.index)
    pos = f.index.searchsorted(pd.DatetimeIndex(auc["auctionDate"]))
    for j in pos:
        prox.iloc[max(0, j - 5): j + 6] = 1.0
    f["auction_prox"] = prox
    f["_resid_A"] = res["resid_A_bp"]
    return f


def walk_forward(y: pd.Series, X: pd.DataFrame, h: int, model: str = "ridge"):
    """Expanding-window walk-forward with an h-day purge. Returns (oos_pred, coef_df)."""
    from sklearn.linear_model import RidgeCV
    from sklearn.ensemble import GradientBoostingRegressor
    df = pd.concat([y.rename("_y"), X], axis=1).dropna()
    yv, Xv = df["_y"], df[X.columns]
    preds = pd.Series(np.nan, index=df.index)
    coefs = []
    for start in range(config.T1_TRAIN_MIN, len(df), config.T1_REFIT):
        tr = slice(0, start - h)                      # purge: no target overlap into test
        te = slice(start, min(start + config.T1_REFIT, len(df)))
        mu, sd = Xv.iloc[tr].mean(), Xv.iloc[tr].std().replace(0, 1.0)
        Xtr, Xte = (Xv.iloc[tr] - mu) / sd, (Xv.iloc[te] - mu) / sd
        if model == "ridge":
            m = RidgeCV(alphas=np.logspace(-2, 3, 16)).fit(Xtr.values, yv.iloc[tr].values)
            coefs.append({"refit_date": df.index[start], **dict(zip(X.columns, m.coef_))})
        else:
            m = GradientBoostingRegressor(max_depth=2, n_estimators=200,
                                          learning_rate=0.05, subsample=0.7,
                                          random_state=7).fit(Xtr.values, yv.iloc[tr].values)
        preds.iloc[te] = m.predict(Xte.values)
    return preds, (pd.DataFrame(coefs).set_index("refit_date") if coefs else None)


def oos_metrics(y: pd.Series, pred: pd.Series) -> dict:
    df = pd.concat([y.rename("y"), pred.rename("p")], axis=1).dropna()
    if len(df) < 50:
        return {"n_oos": len(df)}
    r2_zero = 1.0 - ((df["y"] - df["p"]) ** 2).sum() / (df["y"] ** 2).sum()
    return {"n_oos": len(df), "oos_r2_vs_zero": r2_zero,
            "oos_corr": df["y"].corr(df["p"]),
            "sign_hit": float((np.sign(df["y"]) == np.sign(df["p"])).mean())}


def block_bootstrap_lift(y, pred_a, pred_b, block: int = 20, n_boot: int = 1000) -> float:
    """P(model A's OOS MSE < model B's) under a 20d moving-block bootstrap."""
    df = pd.concat([y.rename("y"), pred_a.rename("a"), pred_b.rename("b")], axis=1).dropna()
    d = ((df["y"] - df["b"]) ** 2 - (df["y"] - df["a"]) ** 2).values   # >0 where A better
    rng = np.random.default_rng(7)
    n = len(d)
    nblocks = int(np.ceil(n / block))
    wins = 0
    for _ in range(n_boot):
        starts = rng.integers(0, n - block, size=nblocks)
        samp = np.concatenate([d[s:s + block] for s in starts])[:n]
        wins += samp.mean() > 0
    return wins / n_boot


def run():
    config.ensure_dirs()
    f = build_features()
    rows, stab_frames = [], []
    common = f[FEATURES + ["_resid_A"]].dropna().index   # one sample for ALL models
    for h in config.T1_HORIZONS:
        y = (f["_resid_A"].shift(-h) - f["_resid_A"]).reindex(common)
        y.name = f"dA_{h}d"
        base_pred, _ = walk_forward(y, f.loc[common, ["z_A"]], h)
        full_pred, coefs = walk_forward(y, f.loc[common, FEATURES], h)
        mb, mf = oos_metrics(y, base_pred), oos_metrics(y, full_pred)
        lift = mf["oos_r2_vs_zero"] - mb["oos_r2_vs_zero"]
        rows.append({"h": h, "model": "baseline_zA", **mb})
        rows.append({"h": h, "model": "ridge_conditioned", **mf, "lift_vs_baseline": lift})
        coefs["h"] = h
        stab_frames.append(coefs)
        # GBM gate: only if the LINEAR conditioning model shows OOS lift first
        if lift > config.T1_GBM_GATE:
            gbm_pred, _ = walk_forward(y, f[FEATURES], h, model="gbm")
            mg = oos_metrics(y, gbm_pred)
            pwin = block_bootstrap_lift(y, gbm_pred, full_pred)
            keep = pwin > 0.95
            rows.append({"h": h, "model": "gbm_depth2", **mg,
                         "lift_vs_baseline": mg["oos_r2_vs_zero"] - mb["oos_r2_vs_zero"],
                         "p_beats_ridge_blockboot": pwin, "kept": keep})
        else:
            print(f"  h={h}: ridge lift {lift:+.4f} <= gate {config.T1_GBM_GATE} — GBM not attempted")

    out = pd.DataFrame(rows)
    out.to_csv(OUT_OOS, index=False)
    stab = pd.concat(stab_frames)
    stab.to_csv(OUT_STAB)
    print("\nOOS metrics (expanding walk-forward, h-day purge, refit annually):")
    print(out.round(4).to_string(index=False))
    # feature stability: sign consistency + mean/sd of standardized coef across refits
    print("\nFeature stability across refits (standardized coefs, all horizons):")
    g = stab.groupby("h")
    summ = pd.concat({h: pd.DataFrame({
        "mean": sub[FEATURES].mean(), "sd": sub[FEATURES].std(),
        "sign_consistency": (np.sign(sub[FEATURES]) == np.sign(sub[FEATURES].mean())).mean()})
        for h, sub in g}, axis=0)
    print(summ.round(3).to_string())
    print(f"\n  wrote {OUT_OOS}, {OUT_STAB}")
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
