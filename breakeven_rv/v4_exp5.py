"""
Exp 5 (v4) — state/regime conditioning: the March-2020 experiment.

Pre-declared rule (config, declared before any outcome split): crisis at ENTRY =
>= 2 of {|5d dz_B| expanding-pctl >= .90, MOVE 1y-pctl >= .90, VIX 1y-pctl >= .90}.

Three parts:
 (a) outcome splits by entry-state x B-state (+ the appendix sensitivity grid over
     pctl x min_flags — reported whole, not tuned);
 (b) the ENTRY-STATE BLINDNESS check: the same splits using the max flag count
     reached DURING the episode (ex-post, clearly labeled — usable for a monitoring/
     cut rule, never for entry classification). Motivated by Exp 4's finding that the
     worst-MAE episodes were flagged NORMAL at entry (the spiral develops mid-episode);
 (c) wait-cost: for spiral episodes, reversion captured from entry-at-signal vs
     entry-at-stabilization (first day the sign-adjusted 5d dz_A stops worsening) vs
     entry-at-catalyst (2020-03-23 Fed announcement, where applicable).

Outputs: reports/v4_state_conditioning.csv, v4_state_sensitivity.csv, v4_wait_cost.csv,
cache/v4_2020_paths.parquet (figure input).
Usage:  python -m breakeven_rv.v4_exp5
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, residuals, v4_core

R = config.REPORTS


def _stats(sub):
    tot = sub["gap_closed_bp"].sum()
    return pd.Series({"n": len(sub),
                      "price_share": sub["pnl_price_bp"].sum() / tot if tot else np.nan,
                      "mean_pnl_bp": sub["pnl_price_bp"].mean(),
                      "mae_p5_bp": sub["mae_bp"].quantile(0.05),
                      "median_days": sub["days"].median()})


def in_episode_flags(ep: pd.DataFrame, st: pd.DataFrame) -> pd.Series:
    """Max crisis flag count reached between entry and exit (EX-POST descriptor)."""
    out = []
    cf = st["crisis_flags"]
    for _, e in ep.iterrows():
        out.append(cf.loc[e["entry"]:e["exit"]].max())
    return pd.Series(out, index=ep.index, name="max_flags_in_episode")


def wait_cost(ep: pd.DataFrame) -> pd.DataFrame:
    """For spiral episodes (>=2 flags reached in-episode AND MAE < -10bp): PnL from
    signal vs from stabilization vs from the datable catalyst."""
    p = panel_mod.load()
    res = residuals.load()
    y = (p["swap_10y"] * 100).dropna()
    z = res["z_A"].dropna()
    dz5 = z.diff(5)
    cat = pd.Timestamp(config.COVID_CATALYST)
    rows = []
    spir = ep[(ep["max_flags_in_episode"] >= config.V4_CRISIS_MIN_FLAGS) & (ep["mae_bp"] < -10)]
    for _, e in spir.iterrows():
        s = 1.0 if e["side"] == "long_BE" else -1.0
        seg_y = y.loc[e["entry"]:e["exit"]]
        if len(seg_y) < 3:
            continue
        pnl_signal = s * (seg_y.iloc[-1] - seg_y.iloc[0])
        # stabilization: first day after entry when sign-adjusted 5d dz stops worsening
        seg_dz = (s * -1) * dz5.loc[e["entry"]:e["exit"]]   # >0 = still worsening
        stab_days = seg_dz[seg_dz <= 0].index
        t_stab = stab_days[1] if len(stab_days) > 1 else (stab_days[0] if len(stab_days) else None)
        pnl_stab = s * (seg_y.iloc[-1] - seg_y.loc[t_stab]) if t_stab is not None else np.nan
        pnl_cat = (s * (seg_y.iloc[-1] - seg_y.asof(cat))
                   if e["entry"] <= cat <= e["exit"] else np.nan)
        rows.append({"entry": e["entry"], "exit": e["exit"], "side": e["side"],
                     "quadrant": e["quadrant"], "mae_bp": e["mae_bp"],
                     "t_stabilization": t_stab,
                     "pnl_from_signal_bp": pnl_signal, "pnl_from_stab_bp": pnl_stab,
                     "pnl_from_catalyst_bp": pnl_cat,
                     "frac_surviving_wait": pnl_stab / pnl_signal
                     if pnl_signal and np.isfinite(pnl_stab) else np.nan})
    return pd.DataFrame(rows)


def run():
    config.ensure_dirs()
    ep = v4_core.episodes(1.0).copy()
    st = v4_core.state_frame()
    ep["max_flags_in_episode"] = in_episode_flags(ep, st)

    # (a) entry-state splits
    t1 = ep.groupby(["crisis", "b_state"]).apply(_stats, include_groups=False)
    t1["cut"] = "entry_state"
    t1m = ep.groupby("crisis").apply(_stats, include_groups=False)
    t1m["cut"] = "entry_state_marginal"
    # (b) in-episode state splits (ex-post descriptor)
    ep["spiral_in_ep"] = ep["max_flags_in_episode"] >= config.V4_CRISIS_MIN_FLAGS
    t2 = ep.groupby("spiral_in_ep").apply(_stats, include_groups=False)
    t2["cut"] = "in_episode_state(EX-POST)"
    t2b = ep.groupby(["spiral_in_ep", "b_state"]).apply(_stats, include_groups=False)
    t2b["cut"] = "in_episode_x_bstate(EX-POST)"
    out = pd.concat([t1m, t1, t2, t2b]).reset_index()
    out.to_csv(os.path.join(R, "v4_state_conditioning.csv"), index=False)
    print("Entry-state crisis rule (pre-declared) — marginal + x B-state:")
    print(pd.concat([t1m, t1]).round(2).to_string())
    print(f"\nENTRY-STATE BLINDNESS check — worst-MAE episodes flagged at entry vs in-episode:")
    worst = ep.nsmallest(5, "mae_bp")
    print(worst[["entry", "side", "quadrant", "mae_bp", "crisis", "max_flags_in_episode"]]
          .to_string(index=False))
    print("\nIn-episode (EX-POST) state splits — monitoring-rule evidence, NOT entry info:")
    print(pd.concat([t2, t2b]).round(2).to_string())

    # sensitivity grid (appendix; report whole)
    rows = []
    for pctl in config.V4_SENS_PCTLS:
        f = ((ep["absdzB5_pctl"] >= pctl).astype(int) + (ep["move_pct1y"] >= pctl).astype(int)
             + (ep["vix_pct1y"] >= pctl).astype(int))
        for mf in config.V4_SENS_FLAGS:
            c = f >= mf
            tot_c = ep.loc[c, "gap_closed_bp"].sum()
            tot_n = ep.loc[~c, "gap_closed_bp"].sum()
            rows.append({"pctl": pctl, "min_flags": mf, "n_crisis": int(c.sum()),
                         "share_price_crisis": ep.loc[c, "pnl_price_bp"].sum() / tot_c if tot_c else np.nan,
                         "share_price_normal": ep.loc[~c, "pnl_price_bp"].sum() / tot_n if tot_n else np.nan,
                         "mae_p5_crisis": ep.loc[c, "mae_bp"].quantile(0.05) if c.sum() > 3 else np.nan})
    sens = pd.DataFrame(rows)
    sens.to_csv(os.path.join(R, "v4_state_sensitivity.csv"), index=False)
    print("\nSensitivity grid (appendix):")
    print(sens.round(2).to_string(index=False))

    # (c) wait cost
    wc = wait_cost(ep)
    wc.to_csv(os.path.join(R, "v4_wait_cost.csv"), index=False)
    print("\nWait-cost on spiral episodes (>=2 flags reached in-episode, MAE < -10bp):")
    print(wc.round(1).to_string(index=False))
    if len(wc):
        med = wc["frac_surviving_wait"].median()
        print(f"  median fraction of reversion surviving the stabilization wait: {med:.2f}")

    # figure input: the 2020 spiral episode paths
    p = panel_mod.load()
    res = residuals.load()
    y = (p["swap_10y"] * 100).dropna()
    ep2020 = ep[(ep["entry"] >= "2020-01-01") & (ep["entry"] <= "2020-03-31")]
    if len(ep2020):
        e = ep2020.iloc[0]
        seg = y.loc[e["entry"]: e["exit"]]
        s = 1.0 if e["side"] == "long_BE" else -1.0
        fig_df = pd.DataFrame({"y_bp": seg, "cum_from_signal": s * (seg - seg.iloc[0])})
        wcrow = wc[wc["entry"] == e["entry"]]
        if len(wcrow) and pd.notna(wcrow.iloc[0]["t_stabilization"]):
            t_stab = wcrow.iloc[0]["t_stabilization"]
            fig_df["cum_from_stab"] = np.where(seg.index >= t_stab,
                                               s * (seg - seg.loc[t_stab]), np.nan)
        fig_df.to_parquet(os.path.join(config.CACHE, "v4_2020_paths.parquet"))
    return out, sens, wc


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
