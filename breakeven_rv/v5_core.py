"""
v5 core — the v4 machinery generalized across tenors (5y/10y/30y), plus the
FORMALIZED Part-B monitor.

Per tenor:
  layer1     rolling-504bd OLS of the tenor ZC swap on the four factors
             (10y reuses the existing layer1_swap10 cache; 5y/30y built here)
  b_bond     per-tenor bond basis (built by b_bond.build(tenor))
  state      monitor flags, ALL EXPANDING percentiles (v5 fixed rule):
               f1: |5d dz_B_bond|      expanding-pctl >= .90
               f2: MOVE                expanding-pctl >= .90
               f3: VIX                 expanding-pctl >= .90
               f4: 20d corr(dBE_bond, SPX ret)  expanding-pctl >= .90
             CRISIS = >= 2 flags. (v4 used 1y-rolling pctls and 3 flags; the v5
             rule is the fixed headline; sensitivity rerun in Part B appendix.)
  episodes   v4-style enriched episodes + monitor lead/lag fields:
             first_crisis_bd (bd offset from entry of first in-episode CRISIS day),
             mae_trough_bd, dd50_bd (first day reaching 50% of eventual max
             drawdown), bflip_bd (quadrant contradict while CRISIS).

Usage:  python -m breakeven_rv.v5_core     (builds all tenors, prints summary)
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, layer1 as layer1_mod, b_bond, data_dealer
from breakeven_rv.residuals import classify
from breakeven_rv.validation import rolling_half_life

BETA_COLS = ["beta_const"] + [f"beta_{c}" for c in config.L1_FACTORS]


def _layer1_path(tenor):
    return os.path.join(config.CACHE, f"layer1_swap{tenor.rstrip('y')}.parquet")


def build_layer1(tenor: str):
    """Rolling OLS FV for the tenor swap (identical spec to the 10y build)."""
    if tenor == "10y":
        return layer1_mod.load("swap10")
    p = panel_mod.load()
    fit = layer1_mod.rolling_fit(p[f"swap_{tenor.rstrip('y')}y"], p[config.L1_FACTORS],
                                 config.L1_WINDOW)
    out = pd.DataFrame(index=fit.index)
    out["fv_ols"] = fit["fv"]
    out["resid_ols_bp"] = fit["resid_bp"]
    out["r2_ols"] = fit["r2"]
    sd = fit["resid_bp"].rolling(config.Z_WINDOW, min_periods=config.Z_MIN_PERIODS).std()
    out["z_ols"] = fit["resid_bp"] / sd
    for c in ["const"] + config.L1_FACTORS:
        out[f"beta_{c}"] = fit[f"beta_{c}"]
    out.to_parquet(_layer1_path(tenor))
    return out


def load_layer1(tenor: str):
    if tenor == "10y":
        return layer1_mod.load("swap10")
    path = _layer1_path(tenor)
    if not os.path.exists(path):
        return build_layer1(tenor)
    return pd.read_parquet(path)


def _exp_pctl(s: pd.Series, min_periods: int = 252) -> pd.Series:
    """Expanding percentile of the NON-NaN history (a NaN prefix must not dilute the
    denominator — with e.g. a 2013 data start on a 2004 index, the raw version could
    never reach 0.9)."""
    v = s.dropna()
    p = v.expanding(min_periods=min_periods).apply(
        lambda w: (w <= w[-1]).mean(), raw=True)
    return p.reindex(s.index)


def state_frame(tenor: str) -> pd.DataFrame:
    p = panel_mod.load()
    l1 = load_layer1(tenor)
    bb = b_bond.load(tenor)
    s = pd.DataFrame(index=p.index)
    zA = l1["z_ols"].reindex(p.index)
    zB = bb["z_B_bond"].reindex(p.index)
    s["z_A"], s["z_B_bond"] = zA, zB
    for k in (5, 10):
        s[f"dzA_{k}d"] = zA.diff(k)
        s[f"dzB_{k}d"] = zB.diff(k)
    s["move_pct_exp"] = _exp_pctl(p["move"].ffill(limit=5))
    s["vix_pct_exp"] = _exp_pctl(p["vix"].ffill(limit=5))
    s["absdzB5_pctl"] = _exp_pctl(s["dzB_5d"].abs())
    dbe = bb["be_bond_otr"].reindex(p.index).diff()
    s["eq_corr20"] = dbe.rolling(20).corr(p["spx"].pct_change())
    s["eqcorr_pctl"] = _exp_pctl(s["eq_corr20"])
    for name, col in (("flag_dzB", "absdzB5_pctl"), ("flag_move", "move_pct_exp"),
                      ("flag_vix", "vix_pct_exp"), ("flag_eq", "eqcorr_pctl")):
        s[name] = s[col] >= config.V5_MONITOR_PCTL
    s["flags"] = s[["flag_dzB", "flag_move", "flag_vix", "flag_eq"]].sum(axis=1)
    s["crisis"] = s["flags"] >= config.V5_MONITOR_MIN_FLAGS
    # dealer state (sector-agnostic; vintage-safe), incl 1y rolling z within break
    s = s.join(data_dealer.load_daily(p.index).rename(
        columns={"total": "dealer_total", "total_z": "dealer_z", "total_chg_4w": "dealer_chg4w"}))
    w = pd.read_parquet(os.path.join(config.CACHE, "dealer_tips_positions.parquet"))
    z1 = w.groupby("seriesbreak")["total"].transform(
        lambda x: (x - x.rolling(52, min_periods=26).mean()) / x.rolling(52, min_periods=26).std())
    s["dealer_z1y"] = pd.Series(z1.values, index=pd.DatetimeIndex(w["pub_date"])) \
        .sort_index().reindex(p.index, method="ffill")
    # quadrant state (tenor A x tenor bond-B)
    quad = pd.Series(index=p.index, dtype=object)
    ok = zA.notna() & zB.notna()
    quad[ok] = [classify(a, b, config.Z_THRESHOLD) for a, b in zip(zA[ok], zB[ok])]
    s["quadrant"] = quad
    return s


def episodes(tenor: str, entry_z: float, exit_z: float = None,
             use_cache: bool = True) -> pd.DataFrame:
    exit_z = config.V4_EXIT_Z if exit_z is None else exit_z
    cache_f = os.path.join(config.CACHE, f"v5_ep_{tenor}_e{entry_z}_x{exit_z}.parquet")
    if use_cache and os.path.exists(cache_f):
        return pd.read_parquet(cache_f)

    p = panel_mod.load()
    l1 = load_layer1(tenor)
    st = state_frame(tenor)
    scol = f"swap_{tenor.rstrip('y')}y"

    idx = l1.dropna(subset=["z_ols"]).index
    z = l1["z_ols"].reindex(idx)
    resid = l1["resid_ols_bp"].reindex(idx)
    y = (p[scol] * 100.0).reindex(idx)
    X = pd.DataFrame({"const": 1.0, **{c: p[c].reindex(idx) for c in config.L1_FACTORS}}) * 100.0
    B = l1[BETA_COLS].reindex(idx)
    hl = rolling_half_life(resid, config.HL_WINDOW, config.V5_HL_CLIP[tenor]).reindex(idx)
    Xv, Bv, yv = X.values, B.values, y.values
    sti = st.reindex(idx)
    crisis = sti["crisis"].fillna(False).values
    quad_arr = sti["quadrant"].values
    STATE_COLS = ["dzA_5d", "dzB_5d", "move_pct_exp", "vix_pct_exp", "eqcorr_pctl",
                  "flags", "crisis", "dealer_z1y", "dealer_z"]

    rows = []
    i, n = 0, len(idx)
    while i < n:
        if not (np.isfinite(z.iloc[i]) and abs(z.iloc[i]) >= entry_z
                and np.isfinite(hl.iloc[i]) and z.iloc[i] != 0):
            i += 1
            continue
        e = i
        s_dir = -np.sign(resid.iloc[e])
        vol_e = resid.iloc[e] / z.iloc[e]
        b_e = Bv[e]
        stop = e + int(round(config.EP_HL_MULT * hl.iloc[e]))
        x, reason = None, None
        for j in range(e + 1, min(stop, n - 1) + 1):
            if abs(z.iloc[j]) < exit_z:
                x, reason = j, "converged"
                break
        if x is None:
            x = min(stop, n - 1)
            reason = "time_stop" if x == stop else "sample_end"
        end = min(x + config.V4_FROZEN_EXTRA_BD, n - 1)
        fz = (yv[e:end + 1] - Xv[e:end + 1] @ b_e) / vol_e
        fz_x = fz[x - e]
        frozen_status = ("closed" if abs(fz_x) < exit_z
                         else "worse" if abs(fz_x) > abs(fz[0]) else "open")
        post = np.where(np.abs(fz[x - e:]) < exit_z)[0]
        pnl_price = s_dir * (yv[x] - yv[e])
        fair_factor = -s_dir * float((Xv[x] - Xv[e]) @ b_e)
        fair_coef = -s_dir * float(Xv[x] @ (Bv[x] - b_e))
        path = s_dir * (yv[e:x + 1] - yv[e])
        # monitor lead/lag fields (all bd offsets from entry)
        ep_crisis = crisis[e:x + 1]
        first_crisis = int(np.argmax(ep_crisis)) if ep_crisis.any() else np.nan
        mae_trough = int(np.argmin(path))
        dd50_hits = np.where(path <= 0.5 * path.min())[0] if path.min() < 0 else np.array([])
        dd50 = int(dd50_hits[0]) if len(dd50_hits) else np.nan
        ep_quad = quad_arr[e:x + 1]
        bflip_hits = [k for k in range(len(ep_quad))
                      if ep_quad[k] == "disagree" and ep_crisis[k]]
        q = quad_arr[e]
        rows.append({
            "tenor": tenor, "entry": idx[e], "exit": idx[x], "days": x - e,
            "exit_reason": reason, "side": "long_BE" if s_dir > 0 else "short_BE",
            "z_entry": z.iloc[e], "quadrant": q,
            "b_state": ("confirm" if q in ("both_cheap", "both_rich")
                        else "contradict" if q == "disagree" else "neutral"),
            "pnl_price_bp": pnl_price, "fair_factor_bp": fair_factor,
            "fair_coef_bp": fair_coef, "gap_closed_bp": pnl_price + fair_factor + fair_coef,
            "mae_bp": float(path.min()), "mfe_bp": float(path.max()),
            "z_frozen_exit": float(fz_x), "frozen_status": frozen_status,
            "phantom": (reason == "converged") and frozen_status != "closed",
            "frozen_close_lag_bd": int(post[0]) if len(post) else np.nan,
            "first_crisis_bd": first_crisis, "mae_trough_bd": mae_trough,
            "dd50_bd": dd50, "bflip_bd": bflip_hits[0] if bflip_hits else np.nan,
            "max_flags_in_ep": int(sti["flags"].iloc[e:x + 1].max()),
            **{c: sti[c].iloc[e] for c in STATE_COLS},
        })
        i = x + 1
    ep = pd.DataFrame(rows)
    ep.to_parquet(cache_f)
    return ep


def all_episodes(entry_z: float = 1.0, use_cache: bool = True) -> pd.DataFrame:
    return pd.concat([episodes(t, entry_z, use_cache=use_cache)
                      for t in config.V5_TENORS], ignore_index=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for t in config.V5_TENORS:
        l1 = build_layer1(t) if t != "10y" else load_layer1(t)
        print(f"{t}: layer1 median R2 {l1['r2_ols'].median():.3f}, "
              f"resid sd {l1['resid_ols_bp'].std():.1f}bp")
        for ez in config.V3_ENTRY_GRID:
            ep = episodes(t, ez, use_cache=False)
            print(f"  entry {ez}: {len(ep)} episodes, phantom "
                  f"{ep.loc[ep.exit_reason == 'converged', 'phantom'].mean():.2f}, "
                  f"crisis-at-entry {ep['crisis'].mean():.2f}")
