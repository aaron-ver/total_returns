"""
v6 Part A — the hybrid fair-value frontier.

Candidates (all use the COMBINED trigger: stress monitor OR drift-CUSUM OR macro
shift; Part B grades the detectors themselves):
  h1_frozen          frozen per segment (v5 design, better trigger set)
  h2_ewls{504,1008}  slow EWLS within segments, re-init at breaks
  h3_ridge{1,5}      daily ridge on trailing 504bd shrunk to the segment anchor
Benchmarks: rolling504, ewls252 (unsegmented), v5 frozen-monitor (stress only).

Every model scored identically by v6_core.evaluate: phantom rate (frozen-z audit),
staleness (max |60bd median resid|; days structurally wrong), abstention, episode
decomposition price share.

Output: reports/v6_frontier.csv (the central exhibit; chart in v6_figures).
Usage:  python -m breakeven_rv.v6_partA
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, v6_core, layer1 as layer1_mod, panel as panel_mod

R = config.REPORTS


def run():
    config.ensure_dirs()
    rows = []
    for tenor in config.V5_TENORS:
        idx, y, X, crisis = v6_core.tenor_data(tenor)
        macro = v6_core.macro_matrix(idx)

        # benchmarks
        resid, B = v6_core.layer1_betas_for(idx, tenor)
        rows.append({"tenor": tenor, "model": "bench_rolling504",
                     **v6_core.evaluate(idx, y, X, resid, B, tenor, abstain=config.L1_WINDOW)})
        p = panel_mod.load()
        scol = f"swap_{tenor.rstrip('y')}y"
        fit = layer1_mod.rolling_fit(p[scol], p[config.L1_FACTORS], config.L1_WINDOW,
                                     halflife=252)
        fit = fit.reindex(idx)
        Be = np.column_stack([fit["beta_const"].values * 100.0]
                             + [fit[f"beta_{c}"].values for c in config.L1_FACTORS])
        rows.append({"tenor": tenor, "model": "bench_ewls252",
                     **v6_core.evaluate(idx, y, X, fit["resid_bp"], Be, tenor,
                                        abstain=config.L1_WINDOW)})
        r5, B5, brk5, _, ab5 = v6_core.segment_run(idx, y, X, crisis=crisis,
                                                   quiet="none", model="frozen")
        rows.append({"tenor": tenor, "model": "bench_v5_frozen_stress",
                     "n_breaks": len(brk5),
                     **v6_core.evaluate(idx, y, X, r5, B5, tenor, abstain=ab5)})

        # hybrids on the SURVIVING trigger: Part B failed every quiet detector on the
        # false-positive budget, so the production trigger is stress-only; the model
        # inside the segment carries the staleness cure. One reference row keeps the
        # budget-failed combined trigger visible.
        # (h1 = frozen @ stress+quiet degenerates to bench_v5_frozen_stress when no
        #  quiet detector passes — bench row covers it)
        variants = [(f"h2_ewls{hl}_stress", dict(model="ewls", ewls_hl=hl, quiet="none"))
                    for hl in config.V6_EWLS_HL_GRID]
        variants += [(f"h3_ridge{a:g}_stress", dict(model="ridge", ridge_alpha=a, quiet="none"))
                     for a in config.V6_RIDGE_ALPHA_GRID]
        variants += [("ref_frozen_quietcombined_FAILEDBUDGET",
                      dict(model="frozen", quiet="combined"))]
        for name, kw in variants:
            r_, B_, brk, trig, ab = v6_core.segment_run(idx, y, X, crisis=crisis,
                                                        macro=macro, **kw)
            rows.append({"tenor": tenor, "model": name, "n_breaks": len(brk),
                         **v6_core.evaluate(idx, y, X, r_, B_, tenor, abstain=ab)})
        print(f"  {tenor} done ({len(rows)} rows so far)", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v6_frontier.csv"), index=False)
    with pd.option_context("display.width", 220):
        print("\nThe frontier (phantom vs staleness vs abstention vs price share):")
        print(out.round(3).to_string(index=False))
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
