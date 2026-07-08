"""
Exp 3 (v4) — price share vs dealer positions: direct test of the compensation theory.

Dealer TIPS net positions (vintage-safe, 2013+) as the independent flow proxy.
Prediction if compensation is right: price share / hit rate rise with inventory
pressure ALIGNED with the dislocation (stuffed dealers + cheap basis = the
paid-to-warehouse state). Small-N discipline: terciles collapse to halves when any
cell < config.V4_MIN_CELL; episode counts always shown.

Output: reports/v4_priceshare_by_flow.csv.
Usage:  python -m breakeven_rv.v4_exp3
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, v4_core

R = config.REPORTS


def _share(sub):
    tot = sub["gap_closed_bp"].sum()
    return pd.Series({"n": len(sub),
                      "price_share": sub["pnl_price_bp"].sum() / tot if tot else np.nan,
                      "mean_pnl_bp": sub["pnl_price_bp"].mean(),
                      "hit_rate": (sub["pnl_price_bp"] > 0).mean()})


def run():
    config.ensure_dirs()
    ep = v4_core.episodes(1.0)
    ep = ep.dropna(subset=["dealer_z1y"]).copy()          # dealer data binds 2013+
    print(f"episodes with dealer state: {len(ep)} "
          f"({ep['entry'].min().date()} .. {ep['entry'].max().date()})")

    # tercile vs halves decision (small-N discipline)
    ep["flow_terc"] = pd.qcut(ep["dealer_z1y"], 3, labels=["low", "mid", "high"])
    min_cell = ep.groupby(["flow_terc", "b_state"], observed=True).size().min()
    if min_cell < config.V4_MIN_CELL:
        ep["flow_grp"] = pd.qcut(ep["dealer_z1y"], 2, labels=["low", "high"])
        print(f"  tercile x b_state min cell = {min_cell} < {config.V4_MIN_CELL} "
              f"-> COLLAPSED TO HALVES for the interaction table (terciles kept for the marginal)")
    else:
        ep["flow_grp"] = ep["flow_terc"]

    # aligned pressure: stuffed dealers (high z) + cheap (long) entry, or light dealers + rich
    ep["aligned"] = np.where(
        ((ep["side"] == "long_BE") & (ep["dealer_z1y"] > 0)) |
        ((ep["side"] == "short_BE") & (ep["dealer_z1y"] < 0)), "aligned", "opposed")

    tabs = []
    t1 = ep.groupby("flow_terc", observed=True).apply(_share, include_groups=False)
    t1["cut"] = "dealer_tercile(marginal)"
    t2 = ep.groupby(["flow_grp", "b_state"], observed=True).apply(_share, include_groups=False)
    t2["cut"] = "halves_x_bstate"
    t3 = ep.groupby("aligned", observed=True).apply(_share, include_groups=False)
    t3["cut"] = "aligned_pressure"
    t4 = ep.groupby(["aligned", "b_state"], observed=True).apply(_share, include_groups=False)
    t4["cut"] = "aligned_x_bstate"
    out = pd.concat([t1, t2, t3, t4]).reset_index()
    out.to_csv(os.path.join(R, "v4_priceshare_by_flow.csv"), index=False)

    print("\nPrice share by dealer-position tercile (marginal):")
    print(t1.round(2).to_string())
    print("\nInteraction: dealer halves x B-state (does flow add BEYOND B-confirmation?):")
    print(t2.round(2).to_string())
    print("\nAligned-pressure cut (stuffed+cheap / light+rich = 'paid-to-warehouse'):")
    print(t3.round(2).to_string())
    print("\nAligned x B-state:")
    print(t4.round(2).to_string())
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
