"""
v6 Part C — per-regime variable reselection on the segmentation skeleton.

At each STRESS break (the only detector that survived Part B's budget), after the
60bd burn-in: LASSO selection over the economically-grouped basket
(config.V6_RESELECT_BASKET), then OLS on the selected variables (selection by LASSO,
fit by OLS — no shrinkage bias in the FV level), FROZEN until the next break.
Reselection happens ONLY at breaks; selection uses only within-segment burn-in data
(60 obs — the small-n caveat is reported, with a 120bd burn-in variant).

Hypothesis to grade: the 2021+ segment selects realized-CPI/energy momentum over
VIX, and reselection cuts the 2021-22 staleness vs frozen-four-factors.
NOTE the structural catch found here: the stress detector's segment containing
2021-22 STARTS at the March-2020 break, so "the 2021+ segment" does not exist under
a stress-only trigger — reselection can only choose variables with COVID burn-in
data. Graded accordingly.

Output: reports/v6_reselect.csv (per-segment selections), v6_reselect_scores.csv.
Usage:  python -m breakeven_rv.v6_partC
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, v6_core

R = config.REPORTS


def basket_data(tenor: str):
    p = panel_mod.load()
    scol = f"swap_{tenor.rstrip('y')}y"
    cols = config.V6_RESELECT_BASKET
    df = pd.concat([p[scol].rename("_y") * 100.0, (p[cols] * 100.0).ffill(limit=5)],
                   axis=1).dropna()
    y = df["_y"].values
    X = np.column_stack([np.ones(len(df)), df[cols].values])
    return df.index, y, X, cols


def reselect_run(idx, y, X, cols, breaks, burnin):
    """Frozen-per-segment with LASSO->OLS variable selection at each break."""
    from sklearn.linear_model import LassoCV
    n, k = X.shape
    resid = np.full(n, np.nan)
    betas = np.full((n, k), np.nan)
    abstain = 0
    bounds = [0] + list(breaks) + [n]
    selections = []
    for s0, s1 in zip(bounds[:-1], bounds[1:]):
        if s1 - s0 <= burnin + 5:
            abstain += s1 - s0
            continue
        Xw, yw = X[s0:s0 + burnin, 1:], y[s0:s0 + burnin]
        mu, sd = Xw.mean(axis=0), Xw.std(axis=0)
        sd[sd == 0] = 1.0
        las = LassoCV(cv=3, alphas=25, max_iter=5000).fit((Xw - mu) / sd, yw)
        sel = [j for j, c in enumerate(las.coef_) if c != 0]
        if not sel:
            sel = list(range(k - 1))                       # degenerate: keep all
        Xs = X[:, [0] + [j + 1 for j in sel]]
        b_sel, *_ = np.linalg.lstsq(Xs[s0:s0 + burnin], yw, rcond=None)
        b_full = np.zeros(k)
        b_full[0] = b_sel[0]
        for m, j in enumerate(sel):
            b_full[j + 1] = b_sel[m + 1]
        resid[s0 + burnin:s1] = y[s0 + burnin:s1] - X[s0 + burnin:s1] @ b_full
        betas[s0 + burnin:s1] = b_full
        abstain += burnin
        selections.append({"seg_start": str(idx[s0].date()),
                           "selected": ", ".join(cols[j] for j in sel)})
    return pd.Series(resid, index=idx), betas, abstain, selections


def run():
    config.ensure_dirs()
    rows, sel_rows = [], []
    for tenor in config.V5_TENORS:
        idx, y, X, cols = basket_data(tenor)
        # stress breaks (trigger independent of the model)
        idx4, y4, X4, crisis = v6_core.tenor_data(tenor)
        _, _, brk4, _, _ = v6_core.segment_run(idx4, y4, X4, crisis=crisis,
                                               quiet="none", model="frozen")
        # map break positions onto the basket index
        brk = [idx.searchsorted(idx4[b]) for b in brk4]
        # frozen four-factor reference on the SAME index/basket sample
        Xf = X[:, [0] + [1 + cols.index(c) for c in config.L1_FACTORS]]
        rf, Bf_, brkf, _, abf = v6_core.segment_run(
            idx, y, Xf, crisis=crisis if len(idx4) == len(idx) else None,
            quiet="none", model="frozen") if len(idx4) == len(idx) else (None,) * 5
        for burnin in (60, 120):
            rr, Br, ab, sels = reselect_run(idx, y, X, cols, brk, burnin)
            ev = v6_core.evaluate(idx, y, X, rr, Br, tenor, abstain=ab)
            rows.append({"tenor": tenor, "model": f"frozen_reselect_b{burnin}", **ev})
            if burnin == 60:
                for s in sels:
                    sel_rows.append({"tenor": tenor, **s})
        if rf is not None:
            rows.append({"tenor": tenor, "model": "frozen_4F_same_sample",
                         **v6_core.evaluate(idx, y, Xf, rf, Bf_, tenor, abstain=abf)})
        print(f"  {tenor} done", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(R, "v6_reselect_scores.csv"), index=False)
    sel = pd.DataFrame(sel_rows)
    sel.to_csv(os.path.join(R, "v6_reselect.csv"), index=False)
    with pd.option_context("display.width", 220, "display.max_colwidth", 80):
        print("\nPer-segment variable selection (burn-in 60bd):")
        print(sel.to_string(index=False))
        print("\nReselection vs frozen four-factor (same stress-break skeleton):")
        print(out.round(3).to_string(index=False))
    return out, sel


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
