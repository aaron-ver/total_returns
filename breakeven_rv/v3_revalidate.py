"""
P3 (v3) — re-validate everything that was built on index-B.

1. Quadrant re-check: quadrant states recomputed with z_B_bond; the fit-reversion
   decomposition's by-state split and the A-backtest's B-confirmed subset re-run and
   shown SIDE-BY-SIDE against the v2 (index-B) numbers, n per cell prominent. If the
   confirm cell falls below config.CONFIRM_MIN_N, the result is declared not
   establishable at this sample.
2. Long/short symmetry split (P3.2): decomposition episodes and backtest trades by
   entry side (cheap-entry = long BE vs rich-entry = short BE).
3. Episode robustness grid (P3.3): decomposition shares at entry {0.75,1.0,1.5} x
   exit {0.25,0.5}. Reported whole, nothing picked.
4. MAE/MFE distribution per episode + max |identity residual| (build sanity).

Outputs: reports/v3_quadrant_recheck.csv, v3_side_split.csv, v3_episode_grid.csv,
v3_mae_mfe.csv.  Usage:  python -m breakeven_rv.v3_revalidate
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, residuals, b_bond, track1_decomp, track3_backtest

R = config.REPORTS


def quadrant_bond_series() -> pd.Series:
    res = residuals.load()
    bb = b_bond.load()
    zb = bb["z_B_bond"]
    out = pd.Series(index=res.index, dtype=object)
    za = res["z_A"]
    for t in res.index:
        a, b = za.get(t, np.nan), zb.get(t, np.nan)
        if np.isfinite(a) and np.isfinite(b):
            out.loc[t] = residuals.classify(a, b, config.Z_THRESHOLD)
    return out


def _state_table(ep: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for b, sub in list(ep.groupby("b_state")) + [("ALL", ep)]:
        tot = sub["gap_closed_bp"].sum()
        rows.append({"version": label, "b_state": b, "n": len(sub),
                     "share_price_PnL": sub["pnl_price_bp"].sum() / tot if tot else np.nan,
                     "share_fair_factor": sub["fair_factor_bp"].sum() / tot if tot else np.nan,
                     "share_fair_coef": sub["fair_coef_bp"].sum() / tot if tot else np.nan,
                     "mean_pnl_price_bp": sub["pnl_price_bp"].mean(),
                     "hit_rate_pnl": (sub["pnl_price_bp"] > 0).mean()})
    return pd.DataFrame(rows)


def run():
    config.ensure_dirs()
    qb = quadrant_bond_series()

    # ---- 1. quadrant re-check: decomposition split, v2 (index) vs v3 (bond) ----
    ep_v2 = track1_decomp.episodes()
    ep_v3 = track1_decomp.episodes(quad_override=qb)
    tab = pd.concat([_state_table(ep_v2, "v2_index_B"), _state_table(ep_v3, "v3_bond_B")])
    tab.to_csv(os.path.join(R, "v3_quadrant_recheck.csv"), index=False)
    print("Quadrant re-check — decomposition shares by B-state at entry:")
    print(tab.round(3).to_string(index=False))
    n_confirm = int(tab[(tab["version"] == "v3_bond_B") & (tab["b_state"] == "confirm")]["n"].sum())
    if n_confirm < config.CONFIRM_MIN_N:
        print(f"  *** confirm-cell n={n_confirm} < {config.CONFIRM_MIN_N}: "
              f"NOT ESTABLISHABLE at this sample ***")

    # A-backtest B-confirmed subset, index vs bond quadrants (thr=1.0, cost=1.0)
    print("\nA-backtest net PnL by B-state at entry (thr=1.0, cost=1.0bp):")
    rows = []
    for label, qo in (("v2_index_B", None), ("v3_bond_B", qb)):
        _, tr = track3_backtest.backtest_A(1.0, 1.0, quad_override=qo)
        tr["b_state"] = tr["quadrant_entry"].map(
            lambda q: "confirm" if q in ("both_cheap", "both_rich")
            else ("contradict" if q == "disagree" else "neutral"))
        g = tr.groupby("b_state")["pnl_net_bp"].agg(["sum", "mean", "count"])
        g["version"] = label
        rows.append(g.reset_index())
        # side split of trades (P3.2)
        tr["side"] = np.where(tr["pos"] > 0, "long_BE", "short_BE")
        s = tr.groupby(["b_state", "side"])["pnl_net_bp"].agg(["sum", "mean", "count"])
        s["version"] = label
        rows.append(s.reset_index())
    bt = pd.concat(rows, ignore_index=True)
    print(bt.round(2).to_string(index=False))

    # ---- 2. long/short split of the decomposition (v3 quadrants) ----
    side = ep_v3.groupby(["b_state", "side"]).apply(
        lambda s: pd.Series({"n": len(s), "mean_pnl_price_bp": s["pnl_price_bp"].mean(),
                             "sum_pnl_price_bp": s["pnl_price_bp"].sum(),
                             "hit_rate": (s["pnl_price_bp"] > 0).mean()}),
        include_groups=False).reset_index()
    all_side = ep_v3.groupby("side").apply(
        lambda s: pd.Series({"n": len(s), "mean_pnl_price_bp": s["pnl_price_bp"].mean(),
                             "sum_pnl_price_bp": s["pnl_price_bp"].sum(),
                             "hit_rate": (s["pnl_price_bp"] > 0).mean()}),
        include_groups=False).reset_index()
    all_side["b_state"] = "ALL"
    side = pd.concat([side, all_side], ignore_index=True)
    side.to_csv(os.path.join(R, "v3_side_split.csv"), index=False)
    print("\nLong/short symmetry (decomposition episodes, v3 bond quadrants):")
    print(side.round(2).to_string(index=False))
    bt.to_csv(os.path.join(R, "v3_backtest_states.csv"), index=False)

    # ---- 3. episode robustness grid ----
    grid = []
    for ez in config.V3_ENTRY_GRID:
        for xz in config.V3_EXIT_GRID:
            ep = track1_decomp.episodes(entry_z=ez, exit_z=xz, quad_override=qb)
            tot = ep["gap_closed_bp"].sum()
            conf = ep[ep["b_state"] == "confirm"]
            ctot = conf["gap_closed_bp"].sum()
            grid.append({"entry_z": ez, "exit_z": xz, "n": len(ep),
                         "share_price": ep["pnl_price_bp"].sum() / tot,
                         "share_factor": ep["fair_factor_bp"].sum() / tot,
                         "share_coef": ep["fair_coef_bp"].sum() / tot,
                         "n_confirm": len(conf),
                         "confirm_share_price": conf["pnl_price_bp"].sum() / ctot if ctot else np.nan,
                         "confirm_mean_pnl_bp": conf["pnl_price_bp"].mean() if len(conf) else np.nan})
    gr = pd.DataFrame(grid)
    gr.to_csv(os.path.join(R, "v3_episode_grid.csv"), index=False)
    print("\nEpisode robustness grid (v3 bond quadrants; report whole, tune nothing):")
    print(gr.round(3).to_string(index=False))

    # ---- 4. MAE/MFE + identity residual ----
    mm = ep_v3.groupby("b_state")[["mae_bp", "mfe_bp"]].describe(percentiles=[0.05, 0.25, 0.5])
    mm.to_csv(os.path.join(R, "v3_mae_mfe.csv"))
    print("\nMAE (max adverse excursion) distribution by B-state, bp "
          "(the ex-ante tail-sizing input):")
    print(ep_v3.groupby("b_state")["mae_bp"]
          .quantile([0.05, 0.25, 0.5]).unstack().round(1).to_string())
    print(f"\nmax |decomposition identity residual| = {ep_v3['identity_resid'].abs().max():.2e} "
          f"(appendix sanity: ~0 by construction)")
    ep_v3.to_csv(os.path.join(R, "v3_episodes.csv"), index=False)
    return tab, side, gr, ep_v3


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
