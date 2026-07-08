"""
v5 Part B — the regime monitor, formalized (fixed rule from config: 4 expanding-pctl
flags, CRISIS at >= 2, B-FLIP = contradict while CRISIS).

Key deliverable is the LEAD/LAG measurement: does the monitor fire BEFORE the damage
(usable) or after (narrative)? Per spiral episode: bd between first CRISIS day and
(a) the MAE trough, (b) the day 50% of eventual max drawdown was first reached.
Positive lead = flag fired first.

Outputs: reports/v5_monitor_transitions.csv, v5_monitor_leadlag.csv, appendix
sensitivity rerun of the v4 grid, figure v5_flag_timeline.png (2020 + 2022 episodes).
Usage:  python -m breakeven_rv.v5_partB
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, v5_core

R = config.REPORTS


def transitions() -> pd.DataFrame:
    rows = []
    for t in config.V5_TENORS:
        ep = v5_core.episodes(t, 1.0)
        entered_calm = ep[~ep["crisis"].astype(bool)]
        flipped = entered_calm[entered_calm["max_flags_in_ep"] >= config.V5_MONITOR_MIN_FLAGS]
        rows.append({
            "tenor": t, "n_episodes": len(ep),
            "entered_in_crisis": int(ep["crisis"].sum()),
            "entered_calm": len(entered_calm),
            "calm_to_crisis_in_ep": len(flipped),
            "calm_to_crisis_rate": len(flipped) / len(entered_calm) if len(entered_calm) else np.nan,
            "bflip_events": int(ep["bflip_bd"].notna().sum()),
        })
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v5_monitor_transitions.csv"), index=False)
    return out


def leadlag() -> pd.DataFrame:
    rows = []
    for t in config.V5_TENORS:
        ep = v5_core.episodes(t, 1.0)
        spir = ep[(ep["max_flags_in_ep"] >= config.V5_MONITOR_MIN_FLAGS)
                  & (ep["mae_bp"] < -10) & ep["first_crisis_bd"].notna()]
        for _, e in spir.iterrows():
            rows.append({
                "tenor": t, "entry": e["entry"], "mae_bp": e["mae_bp"],
                "first_crisis_bd": e["first_crisis_bd"],
                "mae_trough_bd": e["mae_trough_bd"], "dd50_bd": e["dd50_bd"],
                "lead_vs_trough_bd": e["mae_trough_bd"] - e["first_crisis_bd"],
                "lead_vs_dd50_bd": (e["dd50_bd"] - e["first_crisis_bd"])
                                   if np.isfinite(e["dd50_bd"]) else np.nan,
            })
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v5_monitor_leadlag.csv"), index=False)
    return out


def sensitivity() -> pd.DataFrame:
    """Appendix: rerun the flag grid on pooled episodes (report whole, not tuned)."""
    allep = v5_core.all_episodes(1.0)
    rows = []
    for mf in (1, 2, 3):
        spir = allep[allep["max_flags_in_ep"] >= mf]
        calm = allep[allep["max_flags_in_ep"] < mf]
        st = spir["gap_closed_bp"].sum()
        ct = calm["gap_closed_bp"].sum()
        rows.append({"min_flags": mf, "n_spiral": len(spir),
                     "share_spiral": spir["pnl_price_bp"].sum() / st if st else np.nan,
                     "share_calm": calm["pnl_price_bp"].sum() / ct if ct else np.nan,
                     "mae_p5_spiral": spir["mae_bp"].quantile(0.05) if len(spir) > 3 else np.nan,
                     "mae_p5_calm": calm["mae_bp"].quantile(0.05)})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v5_monitor_sensitivity.csv"), index=False)
    return out


def run():
    config.ensure_dirs()
    tr = transitions()
    print("Monitor state transitions (entry 1.0 episodes):")
    print(tr.round(2).to_string(index=False))
    ll = leadlag()
    print(f"\nLead/lag on spiral episodes (n={len(ll)}; positive = monitor fired BEFORE):")
    if len(ll):
        for c in ("lead_vs_trough_bd", "lead_vs_dd50_bd"):
            v = ll[c].dropna()
            print(f"  {c}: median {v.median():+.0f}bd, p25 {v.quantile(.25):+.0f}, "
                  f"p75 {v.quantile(.75):+.0f}, fired-before share {(v > 0).mean():.2f} (n={len(v)})")
        print(ll.sort_values("mae_bp").head(8).round(1).to_string(index=False))
    sens = sensitivity()
    print("\nSensitivity (pooled, min_flags grid):")
    print(sens.round(2).to_string(index=False))
    return tr, ll, sens


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
