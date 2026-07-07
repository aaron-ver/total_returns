"""
Track 1 (v2 spec) — the fit-reversion decomposition: A's critical deliverable.

For every A-reversion episode (entry when |z_A| crosses config.EP_ENTRY_Z, tracked
until |z_A| < config.EP_EXIT_Z or a time stop at EP_HL_MULT x the rolling half-life),
freeze the Layer-1 coefficients b_e as of the entry date and decompose the closing
of the residual gap into three additive parts (s = trade direction = -sign(resid_e)):

  pnl_price    = s * (y_x - y_e)              market moved -> REAL PnL
  fair_factor  = -s * (X_x - X_e) @ b_e       fair moved because FACTORS moved
                                              (fundamental catch-up, frozen betas -> no PnL)
  fair_coef    = -s * X_x @ (b_x - b_e)       fair moved because the rolling fit
                                              ADAPTED (fit-reversion -> no PnL)

  gap_closed   = pnl_price + fair_factor + fair_coef
               = |resid_e| - sign-consistent residual at exit   (identity, verified)

The spec's two-way split is the rollup: "market-vs-frozen-fair" = pnl_price +
fair_factor, "frozen-fair-vs-live-fair" = fair_coef. Both are reported.

If the majority of A's "reversion" is fair_factor + fair_coef, the go/no-go betas
were measuring model adaptation, not tradeable convergence — Track 1 is dead
regardless of those t-stats, and the report must say so plainly.

Output: reports/fit_reversion_episodes.csv + aggregate shares (overall, by quadrant
at entry).  Usage:  python -m breakeven_rv.track1_decomp
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, layer1, residuals
from breakeven_rv.validation import rolling_half_life

OUT = os.path.join(config.REPORTS, "fit_reversion_episodes.csv")

BETA_COLS = ["beta_const"] + [f"beta_{c}" for c in config.L1_FACTORS]


def episodes(entry_z: float = None, exit_z: float = None,
             quad_override: pd.Series | None = None) -> pd.DataFrame:
    """Episode table. entry_z/exit_z default to config (v3 robustness grid passes others);
    quad_override replaces the index-B quadrant with e.g. the bond-B one (v3 P3)."""
    entry_z = config.EP_ENTRY_Z if entry_z is None else entry_z
    exit_z = config.EP_EXIT_Z if exit_z is None else exit_z
    p = panel_mod.load()
    res = residuals.load()
    l1 = layer1.load("swap10")

    idx = res.dropna(subset=["z_A"]).index
    z = res["z_A"].reindex(idx)
    resid = res["resid_A_bp"].reindex(idx)
    quad = (quad_override if quad_override is not None else res["quadrant"]).reindex(idx)
    y = (p["swap_10y"] * 100.0).reindex(idx)                 # market, bp
    X = pd.DataFrame({"const": 1.0, **{c: p[c].reindex(idx) for c in config.L1_FACTORS}})
    X = X * 100.0                                            # betas are in %-units; scale once
    B = l1[BETA_COLS].reindex(idx)
    B.columns = ["const"] + config.L1_FACTORS
    hl = rolling_half_life(resid, config.HL_WINDOW, config.HL_CLIP).reindex(idx)

    rows = []
    i, n = 0, len(idx)
    while i < n:
        if not (np.isfinite(z.iloc[i]) and abs(z.iloc[i]) >= entry_z and np.isfinite(hl.iloc[i])):
            i += 1
            continue
        e = i
        s = -np.sign(resid.iloc[e])                          # +1 long BE (cheap), -1 short (rich)
        stop = e + int(round(config.EP_HL_MULT * hl.iloc[e]))
        x = None
        for j in range(e + 1, min(stop, n - 1) + 1):
            if abs(z.iloc[j]) < exit_z:
                x, reason = j, "converged"
                break
        if x is None:
            x = min(stop, n - 1)
            reason = "time_stop" if x == stop else "sample_end"
        b_e, b_x = B.iloc[e].values, B.iloc[x].values
        X_e, X_x = X.iloc[e].values, X.iloc[x].values
        pnl_price = s * (y.iloc[x] - y.iloc[e])
        fair_factor = -s * float((X_x - X_e) @ b_e)
        fair_coef = -s * float(X_x @ (b_x - b_e))
        gap_closed = pnl_price + fair_factor + fair_coef      # == s*(resid_x − resid_e): >0 = gap shrank
        ident = gap_closed - s * (resid.iloc[x] - resid.iloc[e])   # ~0 by construction (appendix check)
        path = s * (y.iloc[e:x + 1] - y.iloc[e])              # intra-episode PnL path, bp
        q = quad.iloc[e]
        rows.append({
            "entry": idx[e], "exit": idx[x], "days": x - e, "exit_reason": reason,
            "side": "long_BE" if s > 0 else "short_BE",       # cheap-entry vs rich-entry (v3 P3.2)
            "z_entry": z.iloc[e], "resid_entry_bp": resid.iloc[e], "resid_exit_bp": resid.iloc[x],
            "quadrant_entry": q,
            "b_state": ("confirm" if q in ("both_cheap", "both_rich")
                        else "contradict" if q == "disagree" else "neutral"),
            "hl_entry": hl.iloc[e],
            "pnl_price_bp": pnl_price, "fair_factor_bp": fair_factor,
            "fair_coef_bp": fair_coef, "gap_closed_bp": gap_closed,
            "mae_bp": float(path.min()), "mfe_bp": float(path.max()),   # max adverse/favorable excursion
            "identity_resid": ident,
        })
        i = x + 1                                             # no overlapping episodes
    return pd.DataFrame(rows)


def summarize(ep: pd.DataFrame) -> pd.DataFrame:
    comp = ["pnl_price_bp", "fair_factor_bp", "fair_coef_bp", "gap_closed_bp"]

    def agg(sub):
        tot = sub["gap_closed_bp"].sum()
        return pd.Series({
            "n_episodes": len(sub), "median_days": sub["days"].median(),
            "share_converged": (sub["exit_reason"] == "converged").mean(),
            "gap_closed_bp_total": tot,
            "share_price_PnL": sub["pnl_price_bp"].sum() / tot if tot else np.nan,
            "share_fair_factor": sub["fair_factor_bp"].sum() / tot if tot else np.nan,
            "share_fair_coef": sub["fair_coef_bp"].sum() / tot if tot else np.nan,
            "share_mkt_vs_frozen": (sub["pnl_price_bp"].sum() + sub["fair_factor_bp"].sum()) / tot if tot else np.nan,
            "mean_pnl_price_bp": sub["pnl_price_bp"].mean(),
            "hit_rate_pnl": (sub["pnl_price_bp"] > 0).mean(),
        })

    parts = {"ALL": agg(ep)}
    for b, sub in ep.groupby("b_state"):
        parts[f"B={b}"] = agg(sub)
    for q, sub in ep.groupby("quadrant_entry"):
        if len(sub) >= 8:
            parts[f"quad={q}"] = agg(sub)
    return pd.DataFrame(parts).T


def run():
    config.ensure_dirs()
    ep = episodes()
    ep.to_csv(OUT, index=False)
    summ = summarize(ep)
    print(f"episodes: {len(ep)}  ({ep['entry'].min().date()} .. {ep['entry'].max().date()})")
    print(f"exit reasons: {ep['exit_reason'].value_counts().to_dict()}")
    with pd.option_context("display.width", 220):
        print("\nDecomposition of gap closing (shares of total gap closed):")
        print(summ.round(3).to_string())
    print(f"\n  wrote {OUT}")
    return ep, summ


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
