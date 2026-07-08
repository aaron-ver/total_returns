"""
Exp 2 (v4) — decompose B_bond's variance: purify the component-1 detector.

B_bond mixes the liquidity premium with financing noise. Split:
  financing-explained = fitted part of B on [GCF repo spread, MOVE]
  B_clean             = residual (candidate pure liquidity-premium read)
No OTR-specialness series exists in the repo (no repo-specials feed) — documented
stub; GCF spread is the financing state available.

Real-time discipline: B_clean for CLASSIFICATION is built from a trailing-504bd
rolling regression (causal); the full-sample/by-year variance-explained table is
descriptive only and says so.

Outputs: reports/v4_b_decomposition.csv (variance explained), v4_quadrants_bclean.csv
(v3 B_bond vs v4 B_clean confirmed-cell decomposition comparison).
Usage:  python -m breakeven_rv.v4_exp2
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, b_bond, residuals, track1_decomp, v4_core
from breakeven_rv.residuals import classify
from breakeven_rv.validation import rolling_z

R = config.REPORTS


def _financing_frame():
    st = v4_core.state_frame()
    bb = b_bond.load()
    df = pd.DataFrame({"B": bb["B_comb_sa"], "gcf_spread": st["gcf_spread"],
                       "move": st["move_lvl"]}).dropna()
    return df


def variance_table(df) -> pd.DataFrame:
    rows = []
    for name, (yv, Xv) in {
        "level_full": (df["B"], df[["gcf_spread", "move"]]),
        "changes_full": (df["B"].diff(), df[["gcf_spread", "move"]].diff()),
    }.items():
        d = pd.concat([yv.rename("y"), Xv], axis=1).dropna()
        fit = sm.OLS(d["y"], sm.add_constant(d[Xv.columns])).fit()
        rows.append({"sample": name, "r2": fit.rsquared, "n": int(fit.nobs),
                     **{f"beta_{c}": fit.params[c] for c in Xv.columns}})
    for y_, sub in df.groupby(df.index.year):
        if len(sub) < 150:
            continue
        fit = sm.OLS(sub["B"], sm.add_constant(sub[["gcf_spread", "move"]])).fit()
        rows.append({"sample": f"level_{y_}", "r2": fit.rsquared, "n": int(fit.nobs)})
    return pd.DataFrame(rows)


def b_clean_z() -> pd.Series:
    """Causal B_clean: trailing-504bd rolling OLS of B on financing state, residual
    z-scored on the same trailing window convention as z_B_bond."""
    df = _financing_frame()
    yv = df["B"].values
    Xv = np.column_stack([np.ones(len(df)), df[["gcf_spread", "move"]].values])
    resid = np.full(len(df), np.nan)
    w = config.Z_WINDOW
    for i in range(w - 1, len(df)):
        b, *_ = np.linalg.lstsq(Xv[i - w + 1: i + 1], yv[i - w + 1: i + 1], rcond=None)
        resid[i] = yv[i] - Xv[i] @ b
    bc = pd.Series(resid, index=df.index, name="B_clean")
    return rolling_z(bc, config.Z_WINDOW, config.Z_MIN_PERIODS)


def run():
    config.ensure_dirs()
    df = _financing_frame()
    vt = variance_table(df)
    vt.to_csv(os.path.join(R, "v4_b_decomposition.csv"), index=False)
    print("B_bond variance explained by financing state (gcf_spread + MOVE):")
    print(vt.round(3).to_string(index=False))
    print("  (no OTR-specialness series exists in the repo — documented stub; "
          "by-year rows are descriptive full-sample fits)")

    z_clean = b_clean_z()
    res = residuals.load()
    za = res["z_A"]
    quad_clean = pd.Series(index=res.index, dtype=object)
    ok = za.notna() & z_clean.reindex(res.index).notna()
    quad_clean[ok] = [classify(a, b, config.Z_THRESHOLD)
                      for a, b in zip(za[ok], z_clean.reindex(res.index)[ok])]

    # decomposition split: v3 (B_bond quadrants) vs v4 (B_clean quadrants)
    from breakeven_rv.v3_revalidate import quadrant_bond_series, _state_table
    ep_v3 = track1_decomp.episodes(quad_override=quadrant_bond_series())
    ep_v4 = track1_decomp.episodes(quad_override=quad_clean)
    tab = pd.concat([_state_table(ep_v3, "v3_B_bond"), _state_table(ep_v4, "v4_B_clean")])
    tab.to_csv(os.path.join(R, "v4_quadrants_bclean.csv"), index=False)
    print("\nConfirmed-cell comparison, B_bond vs B_clean quadrants "
          "(decomposition, entry 1.0 / exit 0.25):")
    print(tab.round(3).to_string(index=False))
    zb = b_bond.load()["z_B_bond"]
    both = pd.DataFrame({"clean": z_clean, "raw": zb}).dropna()
    print(f"\ncorr(z_B_clean, z_B_bond) = {both['clean'].corr(both['raw']):.3f}  (n={len(both)})")
    return vt, tab


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
