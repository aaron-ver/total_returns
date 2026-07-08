"""
Exp 4 (v4) — the rich/cheap asymmetry as a tested hypothesis.

H: rich-side dislocations are structurally purer component 1 (positioning/exuberance,
bounded); cheap-side ones are contaminated with component 3 (crisis information,
unbounded left tail). Tests:
  (a) confirmed-cell price share, rich vs cheap — episode bootstrap CI on the diff
  (b) MAE 5th percentile cheap vs rich + concentration in crisis-state episodes

Output: reports/v4_asymmetry.csv.  Usage:  python -m breakeven_rv.v4_exp4
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, v4_core

R = config.REPORTS


def _stats(sub):
    tot = sub["gap_closed_bp"].sum()
    return pd.Series({
        "n": len(sub),
        "price_share": sub["pnl_price_bp"].sum() / tot if tot else np.nan,
        "mean_pnl_bp": sub["pnl_price_bp"].mean(),
        "hit_rate": (sub["pnl_price_bp"] > 0).mean(),
        "mae_p5_bp": sub["mae_bp"].quantile(0.05),
        "mae_median_bp": sub["mae_bp"].median(),
        "mfe_median_bp": sub["mfe_bp"].median(),
        "median_days": sub["days"].median(),
        "phantom_rate": sub.loc[sub["exit_reason"] == "converged", "phantom"].mean(),
        "crisis_share": sub["crisis"].mean(),
    })


def _boot_share_diff(a: pd.DataFrame, b: pd.DataFrame, n_boot=4000, seed=7):
    """Bootstrap over episodes: CI on price_share(a) − price_share(b)."""
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        sa = a.sample(len(a), replace=True, random_state=rng.integers(1 << 31))
        sb = b.sample(len(b), replace=True, random_state=rng.integers(1 << 31))
        ta, tb = sa["gap_closed_bp"].sum(), sb["gap_closed_bp"].sum()
        if ta == 0 or tb == 0:
            continue
        diffs.append(sa["pnl_price_bp"].sum() / ta - sb["pnl_price_bp"].sum() / tb)
    d = pd.Series(diffs)
    return d.mean(), d.quantile(0.025), d.quantile(0.975)


def run():
    config.ensure_dirs()
    ep = v4_core.episodes(1.0)
    tabs = []
    for cut, g in (("ALL", ep.groupby("side")),
                   ("by_bstate", ep.groupby(["b_state", "side"]))):
        t = g.apply(_stats, include_groups=False)
        t["cut"] = cut
        tabs.append(t)
    out = pd.concat(tabs).reset_index()
    out.to_csv(os.path.join(R, "v4_asymmetry.csv"), index=False)
    print("Side splits (long_BE = cheap-entry, short_BE = rich-entry):")
    with pd.option_context("display.width", 220):
        print(out.round(2).to_string(index=False))

    # quadrant composition by side
    print("\nQuadrant composition by side:")
    print(ep.groupby(["side", "quadrant"]).size().unstack(fill_value=0).to_string())

    # (a) confirmed-cell price-share diff, rich vs cheap, episode bootstrap
    conf = ep[ep["b_state"] == "confirm"]
    rich, cheap = conf[conf["side"] == "short_BE"], conf[conf["side"] == "long_BE"]
    print(f"\n(a) confirmed cell: rich n={len(rich)}, cheap n={len(cheap)}")
    if min(len(rich), len(cheap)) < config.V4_MIN_CELL:
        print(f"    cheap-confirm n={len(cheap)} < {config.V4_MIN_CELL}: the rich-vs-cheap "
              f"confirmed-cell difference is NOT ESTABLISHABLE at this sample (anecdote rule).")
    m, lo, hi = _boot_share_diff(rich, cheap)
    print(f"    price-share diff (rich − cheap): {m:+.2f}  [95% CI {lo:+.2f}, {hi:+.2f}] "
          f"(reported regardless; see anecdote caveat)")
    # same test on ALL episodes (adequately powered)
    m2, lo2, hi2 = _boot_share_diff(ep[ep["side"] == "short_BE"], ep[ep["side"] == "long_BE"])
    print(f"    ALL episodes price-share diff (rich − cheap): {m2:+.2f}  [95% CI {lo2:+.2f}, {hi2:+.2f}]")

    # (b) MAE tail + crisis concentration
    print("\n(b) MAE 5th pct: cheap "
          f"{ep[ep['side']=='long_BE']['mae_bp'].quantile(0.05):.1f}bp vs rich "
          f"{ep[ep['side']=='short_BE']['mae_bp'].quantile(0.05):.1f}bp")
    worst = ep.nsmallest(max(5, int(len(ep) * 0.05)), "mae_bp")
    print(f"    worst-5% MAE episodes: {len(worst)}, crisis-state share {worst['crisis'].mean():.2f}, "
          f"cheap-side share {(worst['side']=='long_BE').mean():.2f}")
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
