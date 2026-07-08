"""
v5 Part A — multi-tenor confirmation of the v4 gate findings (5y/30y = the
out-of-sample tenors; hypotheses H1-H6 pre-registered in the spec BEFORE any
5y/30y outcome was computed). Grades each PASS / FAIL / NOT ESTABLISHABLE.

Pooling rules: episodes overlapping in time share macro state -> pooled inference
resamples CALENDAR QUARTERS (cluster bootstrap); tenor fixed effects are implicit
in share-by-group comparisons (shares computed within tenor, then pooled tests on
the episode level with quarter clusters).

Output: reports/v5_hypotheses.csv (the scorecard), v5_tenor_tables.csv (per-tenor
decomposition/flow/state tables).  Usage:  python -m breakeven_rv.v5_partA
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, v5_core

R = config.REPORTS
NE = "NOT ESTABLISHABLE"


def _share(sub):
    tot = sub["gap_closed_bp"].sum()
    return sub["pnl_price_bp"].sum() / tot if tot else np.nan


def _qboot_diff(a: pd.DataFrame, b: pd.DataFrame, n_boot=3000, seed=7):
    """Quarter-cluster bootstrap CI on share(a) − share(b). Shares are ratios of
    sums, so resampling quarters of per-quarter (pnl, gap) sums is exact and fast."""
    rng = np.random.default_rng(seed)
    quarters = pd.Index(a["entry"].dt.to_period("Q").unique()).union(
        pd.Index(b["entry"].dt.to_period("Q").unique()))

    def qsums(df):
        g = df.groupby(df["entry"].dt.to_period("Q"))[["pnl_price_bp", "gap_closed_bp"]].sum()
        return g.reindex(quarters).fillna(0.0).values

    sa, sb = qsums(a), qsums(b)
    pick = rng.integers(0, len(quarters), size=(n_boot, len(quarters)))
    ta, tb = sa[pick].sum(axis=1), sb[pick].sum(axis=1)     # (n_boot, 2)
    ok = (ta[:, 1] != 0) & (tb[:, 1] != 0)
    d = pd.Series(ta[ok, 0] / ta[ok, 1] - tb[ok, 0] / tb[ok, 1])
    return d.mean(), d.quantile(0.025), d.quantile(0.975)


def _wait_cost(tenor: str, ep: pd.DataFrame) -> pd.Series:
    """Fraction of reversion surviving entry-at-stabilization, per spiral episode."""
    p = panel_mod.load()
    l1 = v5_core.load_layer1(tenor)
    y = (p[f"swap_{tenor.rstrip('y')}y"] * 100).dropna()
    z = l1["z_ols"].dropna()
    dz5 = z.diff(5)
    fracs = []
    spir = ep[(ep["max_flags_in_ep"] >= config.V5_MONITOR_MIN_FLAGS) & (ep["mae_bp"] < -10)]
    for _, e in spir.iterrows():
        s = 1.0 if e["side"] == "long_BE" else -1.0
        seg_y = y.loc[e["entry"]:e["exit"]]
        if len(seg_y) < 3:
            continue
        pnl_sig = s * (seg_y.iloc[-1] - seg_y.iloc[0])
        seg_dz = (-s) * dz5.loc[e["entry"]:e["exit"]]
        stab = seg_dz[seg_dz <= 0].index
        t_stab = stab[1] if len(stab) > 1 else (stab[0] if len(stab) else None)
        if t_stab is None or pnl_sig == 0:
            continue
        fracs.append((s * (seg_y.iloc[-1] - seg_y.loc[t_stab])) / pnl_sig)
    return pd.Series(fracs)


def grade_tenor(tenor: str) -> tuple[list, list]:
    grades, tables = [], []
    ep = v5_core.episodes(tenor, 1.0)
    epf = ep.dropna(subset=["dealer_z1y"]).copy()

    # H1 — flow gate
    if len(epf) >= 30:
        epf["terc"] = pd.qcut(epf["dealer_z1y"], 3, labels=["low", "mid", "high"])
        sh = epf.groupby("terc", observed=True).apply(
            lambda s: pd.Series({"n": len(s), "share": _share(s)}), include_groups=False)
        tables.append(sh.assign(tenor=tenor, table="H1_flow_terciles").reset_index())
        est = (sh["n"] >= config.V4_MIN_CELL).all()
        monotone = sh["share"].is_monotonic_increasing
        ok = monotone and sh.loc["high", "share"] > 0 and sh.loc["low", "share"] <= 0
        grades.append({"tenor": tenor, "H": "H1", "verdict": ("PASS" if ok else "FAIL") if est else NE,
                       "detail": f"shares {sh['share'].round(2).tolist()} n {sh['n'].astype(int).tolist()}"})
        # H2 — within top tercile, neutral vs confirm
        top = epf[epf["terc"] == "high"]
        cn, cc = top[top["b_state"] == "neutral"], top[top["b_state"] == "confirm"]
        if min(len(cn), len(cc)) >= config.V4_MIN_CELL:
            sn, sc = _share(cn), _share(cc)
            ok = np.isfinite(sn) and sn > 0 and (sn >= 0.5 * sc if np.isfinite(sc) else True)
            grades.append({"tenor": tenor, "H": "H2", "verdict": "PASS" if ok else "FAIL",
                           "detail": f"neutral {sn:.2f}(n{len(cn)}) vs confirm {sc:.2f}(n{len(cc)})"})
        else:
            grades.append({"tenor": tenor, "H": "H2", "verdict": NE,
                           "detail": f"neutral n{len(cn)}, confirm n{len(cc)} < {config.V4_MIN_CELL}"})
    else:
        grades += [{"tenor": tenor, "H": h, "verdict": NE, "detail": f"flow n={len(epf)}"}
                   for h in ("H1", "H2")]

    # H3 — phantom gradient over the entry grid
    rates = []
    for ez in config.V3_ENTRY_GRID:
        e2 = v5_core.episodes(tenor, ez)
        conv = e2[e2["exit_reason"] == "converged"]
        rates.append(conv["phantom"].mean())
    ok = all(rates[i] < rates[i + 1] for i in range(len(rates) - 1))
    grades.append({"tenor": tenor, "H": "H3", "verdict": "PASS" if ok else "FAIL",
                   "detail": f"phantom {['%.2f' % r for r in rates]} at {config.V3_ENTRY_GRID}"})

    # H4 — in-flight separation
    spir = ep[ep["max_flags_in_ep"] >= config.V5_MONITOR_MIN_FLAGS]
    calm = ep[ep["max_flags_in_ep"] < config.V5_MONITOR_MIN_FLAGS]
    bflip = ep[ep["bflip_bd"].notna()]
    tables.append(pd.DataFrame({
        "grp": ["spiral", "calm", "bflip_cell"],
        "n": [len(spir), len(calm), len(bflip)],
        "share": [_share(spir), _share(calm), _share(bflip)],
        "mae_p5": [g["mae_bp"].quantile(0.05) if len(g) > 3 else np.nan
                   for g in (spir, calm, bflip)]}).assign(tenor=tenor, table="H4_inflight"))
    if min(len(spir), len(calm)) >= config.V4_MIN_CELL:
        ok = (_share(spir) < _share(calm)
              and spir["mae_bp"].quantile(0.05) < calm["mae_bp"].quantile(0.05)
              and (len(bflip) < 3 or _share(bflip) <= _share(spir)))
        grades.append({"tenor": tenor, "H": "H4", "verdict": "PASS" if ok else "FAIL",
                       "detail": f"spiral {_share(spir):.2f}(n{len(spir)}) vs calm "
                                 f"{_share(calm):.2f}(n{len(calm)}); bflip {_share(bflip):.2f}(n{len(bflip)})"})
    else:
        grades.append({"tenor": tenor, "H": "H4", "verdict": NE,
                       "detail": f"spiral n{len(spir)}"})

    # H5 — wait cost
    fr = _wait_cost(tenor, ep)
    if len(fr) >= 5:
        ok = fr.median() >= 2 / 3
        grades.append({"tenor": tenor, "H": "H5", "verdict": "PASS" if ok else "FAIL",
                       "detail": f"median frac surviving {fr.median():.2f} (n={len(fr)})"})
    else:
        grades.append({"tenor": tenor, "H": "H5", "verdict": NE, "detail": f"spiral n={len(fr)}"})

    # H6 — no side asymmetry (replicated null expected)
    rich, cheap = ep[ep["side"] == "short_BE"], ep[ep["side"] == "long_BE"]
    m, lo, hi = _qboot_diff(rich, cheap)
    ok = lo <= 0 <= hi
    grades.append({"tenor": tenor, "H": "H6", "verdict": "PASS" if ok else "FAIL",
                   "detail": f"rich−cheap {m:+.2f} [{lo:+.2f},{hi:+.2f}]"})

    # per-tenor record table
    tables.append(ep.groupby("b_state").apply(
        lambda s: pd.Series({"n": len(s), "share": _share(s),
                             "mean_pnl": s["pnl_price_bp"].mean()}), include_groups=False)
        .assign(tenor=tenor, table="bstate").reset_index())
    return grades, tables


def pooled_grades(allep: pd.DataFrame) -> list:
    grades = []
    epf = allep.dropna(subset=["dealer_z1y"]).copy()
    epf["terc"] = epf.groupby("tenor")["dealer_z1y"].transform(
        lambda s: pd.qcut(s, 3, labels=["low", "mid", "high"]))
    sh = epf.groupby("terc", observed=True).apply(
        lambda s: pd.Series({"n": len(s), "share": _share(s)}), include_groups=False)
    m, lo, hi = _qboot_diff(epf[epf["terc"] == "high"], epf[epf["terc"] == "low"])
    grades.append({"tenor": "POOLED", "H": "H1",
                   "verdict": "PASS" if (sh["share"].is_monotonic_increasing
                                         and sh.loc["high", "share"] > 0
                                         and sh.loc["low", "share"] <= 0 and lo > 0) else "FAIL",
                   "detail": f"shares {sh['share'].round(2).tolist()} n {sh['n'].astype(int).tolist()}; "
                             f"top−bottom {m:+.2f} [{lo:+.2f},{hi:+.2f}] (quarter clusters)"})
    top = epf[epf["terc"] == "high"]
    cn, cc = top[top["b_state"] == "neutral"], top[top["b_state"] == "confirm"]
    if min(len(cn), len(cc)) >= config.V4_MIN_CELL:
        m, lo, hi = _qboot_diff(cn, cc)
        sn, sc = _share(cn), _share(cc)
        grades.append({"tenor": "POOLED", "H": "H2",
                       "verdict": "PASS" if (sn > 0 and lo <= 0 <= hi) or sn >= 0.5 * sc else "FAIL",
                       "detail": f"neutral {sn:.2f}(n{len(cn)}) vs confirm {sc:.2f}(n{len(cc)}); "
                                 f"diff {m:+.2f} [{lo:+.2f},{hi:+.2f}]"})
    else:
        grades.append({"tenor": "POOLED", "H": "H2", "verdict": NE,
                       "detail": f"neutral n{len(cn)}, confirm n{len(cc)}"})
    rich, cheap = allep[allep["side"] == "short_BE"], allep[allep["side"] == "long_BE"]
    m, lo, hi = _qboot_diff(rich, cheap)
    grades.append({"tenor": "POOLED", "H": "H6",
                   "verdict": "PASS" if lo <= 0 <= hi else "FAIL",
                   "detail": f"rich−cheap {m:+.2f} [{lo:+.2f},{hi:+.2f}]"})
    return grades


def run():
    config.ensure_dirs()
    grades, tables = [], []
    for t in config.V5_TENORS:
        g, tb = grade_tenor(t)
        grades += g
        tables += tb
    allep = v5_core.all_episodes(1.0)
    grades += pooled_grades(allep)
    gr = pd.DataFrame(grades)
    gr.to_csv(os.path.join(R, "v5_hypotheses.csv"), index=False)
    pd.concat([t if isinstance(t, pd.DataFrame) else pd.DataFrame(t) for t in tables]) \
        .to_csv(os.path.join(R, "v5_tenor_tables.csv"), index=False)
    with pd.option_context("display.width", 220, "display.max_colwidth", 90):
        print("H1-H6 scorecard (10y = generating sample; 5y/30y = confirmation):")
        print(gr.to_string(index=False))
    return gr


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
