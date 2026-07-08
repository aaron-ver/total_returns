"""
Exp 1 (v4) — frozen-z episode tracking: honest resolution measurement.

Live z can normalize via coefficient drift or residual-vol growth with zero price
movement; the frozen z (entry coefficients, entry vol) cannot. Phantom resolution =
live exit says 'converged' while frozen z is still open/worse.

Outputs: reports/v4_frozen_z_episodes.csv (per episode, all entry thresholds),
printed aggregate: phantom rate overall / by b_state / by quadrant + resolution lag.
Usage:  python -m breakeven_rv.v4_exp1
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, v4_core

R = config.REPORTS


def run():
    config.ensure_dirs()
    frames = []
    for ez in config.V3_ENTRY_GRID:
        ep = v4_core.episodes(ez)
        ep["entry_z_grid"] = ez
        frames.append(ep)
    allep = pd.concat(frames, ignore_index=True)
    allep.to_csv(os.path.join(R, "v4_frozen_z_episodes.csv"), index=False)

    for ez, ep in allep.groupby("entry_z_grid"):
        conv = ep[ep["exit_reason"] == "converged"]
        print(f"\nentry_z={ez}: {len(ep)} episodes, {len(conv)} live-converged")
        print(f"  phantom rate among live-converged: {conv['phantom'].mean():.2f}")
        print(f"  frozen status at live exit (all episodes): "
              f"{ep['frozen_status'].value_counts(normalize=True).round(2).to_dict()}")
        lag = ep["frozen_close_lag_bd"].dropna()
        print(f"  frozen-close lag after live exit: median {lag.median():.0f}bd, "
              f"never-closed-within-{config.V4_FROZEN_EXTRA_BD}bd: "
              f"{ep['frozen_close_lag_bd'].isna().mean():.2f}")

    ep1 = allep[allep["entry_z_grid"] == 1.0]
    conv1 = ep1[ep1["exit_reason"] == "converged"]
    print("\nPhantom rate by B-state (entry_z=1.0, live-converged episodes):")
    tab = conv1.groupby("b_state").agg(
        n=("phantom", "size"), phantom_rate=("phantom", "mean"),
        mean_price_pnl_bp=("pnl_price_bp", "mean")).round(2)
    print(tab.to_string())
    print("\nPhantom rate by quadrant (n >= 8 only):")
    q = conv1.groupby("quadrant").agg(n=("phantom", "size"), phantom_rate=("phantom", "mean"))
    print(q[q["n"] >= 8].round(2).to_string())

    # representative episodes for the figure: worst phantom + a clean genuine one
    ph = conv1[conv1["phantom"]].nlargest(1, "fair_coef_bp")
    gen = conv1[(~conv1["phantom"]) & (conv1["b_state"] == "confirm")].nlargest(1, "pnl_price_bp")
    picks = pd.concat([ph.assign(kind="phantom"), gen.assign(kind="genuine")])
    picks[["kind", "entry", "exit", "quadrant", "pnl_price_bp", "fair_coef_bp"]].to_csv(
        os.path.join(R, "v4_exp1_figure_picks.csv"), index=False)
    print("\nfigure picks:")
    print(picks[["kind", "entry", "exit", "quadrant", "pnl_price_bp", "fair_coef_bp"]]
          .round(1).to_string(index=False))
    return allep


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
