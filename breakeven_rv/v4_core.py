"""
v4 core — shared machinery for the component-separation experiments.

Two products, both cached per entry threshold:
  state_frame()   daily state variables, ALL real-time computable / vintage-safe:
                    velocity (5/10bd changes of z_A and z_B_bond), MOVE & VIX levels
                    + 1y percentiles, GCF repo spread, CPFF (3m CP − fed funds; the
                    public FRA-OIS stand-in — no free FRA-OIS series exists,
                    documented), 20d corr of bond-BE changes vs SPX returns
                    ("everything sold at once"), expanding percentile of |5d dz_B|,
                    the PRE-DECLARED crisis flag count, and dealer-position state
                    (publication-lagged: level z within series break + 1y rolling z).
  episodes(entry_z) enriched episode table: v3 decomposition components + bond-B
                    quadrant/b_state + FROZEN-Z tracking (entry-date coefficients AND
                    entry-date residual vol; z_frozen(e) == z_live(e) identically) +
                    phantom-resolution flags + entry-state variables + crisis flag.

Frozen-z definitions (Exp 1):
  z_frozen(t) = (y(t) − X(t)·b_e) / vol_e,   vol_e = resid_e / z_live_e
  at live exit x:  closed  if |z_frozen(x)| < exit_z
                   worse   if |z_frozen(x)| > |z_frozen(e)|
                   open    otherwise
  phantom = live exit reason 'converged' AND frozen not closed.
  frozen_close_lag_bd = bd from live exit until |z_frozen| < exit_z (tracked up to
  V4_FROZEN_EXTRA_BD past the live exit; NaN if it never closes in that window).

Usage:  python -m breakeven_rv.v4_core   (builds + caches all entry thresholds)
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, layer1, residuals, b_bond, data_dealer
from breakeven_rv.residuals import classify
from breakeven_rv.validation import rolling_half_life

BETA_COLS = ["beta_const"] + [f"beta_{c}" for c in config.L1_FACTORS]


def state_frame() -> pd.DataFrame:
    p = panel_mod.load()
    res = residuals.load()
    bb = b_bond.load()
    from breakeven_rv import data_fred
    fred = data_fred.load()
    macro = pd.read_parquet(os.path.join(config.ROOT_CACHE, "macro.parquet"))

    s = pd.DataFrame(index=p.index)
    zA, zB = res["z_A"], bb["z_B_bond"].reindex(p.index)
    s["z_A"], s["z_B_bond"] = zA, zB
    for k in (5, 10):
        s[f"dzA_{k}d"] = zA.diff(k)
        s[f"dzB_{k}d"] = zB.diff(k)
    s["move_lvl"] = p["move"].ffill(limit=5)
    s["move_pct1y"] = s["move_lvl"].rolling(252, min_periods=200).rank(pct=True)
    s["vix_lvl"] = p["vix"].ffill(limit=5)
    s["vix_pct1y"] = s["vix_lvl"].rolling(252, min_periods=200).rank(pct=True)
    s["gcf_spread"] = p["gcf_repo"] - macro["fed_funds"].reindex(p.index).ffill(limit=5)
    s["cpff"] = fred["cpff"].reindex(p.index).ffill(limit=5)
    spx_ret = p["spx"].pct_change()
    dbe = bb["be_bond_otr"].reindex(p.index).diff()
    s["eq_corr20"] = dbe.rolling(20).corr(spx_ret)
    # expanding (real-time) percentile of |5d dz_B|
    a = s["dzB_5d"].abs()
    s["absdzB5_pctl"] = a.expanding(min_periods=252).apply(
        lambda w: (w <= w[-1]).mean() if np.isfinite(w[-1]) else np.nan, raw=True)
    # pre-declared crisis flags
    f1 = s["absdzB5_pctl"] >= config.V4_CRISIS_PCTL
    f2 = s["move_pct1y"] >= config.V4_CRISIS_PCTL
    f3 = s["vix_pct1y"] >= config.V4_CRISIS_PCTL
    s["crisis_flags"] = f1.astype(int) + f2.astype(int) + f3.astype(int)
    s["crisis"] = s["crisis_flags"] >= config.V4_CRISIS_MIN_FLAGS
    # per-flag columns for the sensitivity grid
    s["flag_dzB"], s["flag_move"], s["flag_vix"] = f1, f2, f3
    # dealer state (vintage-safe daily view)
    s = s.join(data_dealer.load_daily(p.index).rename(
        columns={"total": "dealer_total", "total_z": "dealer_z", "total_chg_4w": "dealer_chg4w"}))
    # 1y rolling z of dealer total, within series break, then daily as-known
    w = pd.read_parquet(os.path.join(config.CACHE, "dealer_tips_positions.parquet"))
    z1 = w.groupby("seriesbreak")["total"].transform(
        lambda x: (x - x.rolling(52, min_periods=26).mean()) / x.rolling(52, min_periods=26).std())
    s["dealer_z1y"] = pd.Series(z1.values, index=pd.DatetimeIndex(w["pub_date"])) \
        .sort_index().reindex(p.index, method="ffill")
    return s


def _quad_bond(res, bb) -> pd.Series:
    zb = bb["z_B_bond"]
    za = res["z_A"]
    out = pd.Series(index=res.index, dtype=object)
    ok = za.notna() & zb.reindex(res.index).notna()
    out[ok] = [classify(a, b, config.Z_THRESHOLD)
               for a, b in zip(za[ok], zb.reindex(res.index)[ok])]
    return out


def episodes(entry_z: float, exit_z: float = None, use_cache: bool = True) -> pd.DataFrame:
    exit_z = config.V4_EXIT_Z if exit_z is None else exit_z
    cache_f = os.path.join(config.CACHE, f"v4_episodes_e{entry_z}_x{exit_z}.parquet")
    if use_cache and os.path.exists(cache_f):
        return pd.read_parquet(cache_f)

    p = panel_mod.load()
    res = residuals.load()
    l1 = layer1.load("swap10")
    bb = b_bond.load()
    st = state_frame()
    quad = _quad_bond(res, bb)

    idx = res.dropna(subset=["z_A"]).index
    z = res["z_A"].reindex(idx)
    resid = res["resid_A_bp"].reindex(idx)
    y = (p["swap_10y"] * 100.0).reindex(idx)
    X = pd.DataFrame({"const": 1.0, **{c: p[c].reindex(idx) for c in config.L1_FACTORS}}) * 100.0
    B = l1[BETA_COLS].reindex(idx)
    B.columns = ["const"] + config.L1_FACTORS
    hl = rolling_half_life(resid, config.HL_WINDOW, config.HL_CLIP).reindex(idx)
    Xv, Bv, yv = X.values, B.values, y.values

    STATE_COLS = ["dzA_5d", "dzA_10d", "dzB_5d", "dzB_10d", "move_lvl", "move_pct1y",
                  "vix_lvl", "vix_pct1y", "gcf_spread", "cpff", "eq_corr20",
                  "absdzB5_pctl", "crisis_flags", "crisis",
                  "flag_dzB", "flag_move", "flag_vix",
                  "dealer_total", "dealer_z", "dealer_z1y", "dealer_chg4w"]
    st_idx = st.reindex(idx)

    rows = []
    i, n = 0, len(idx)
    while i < n:
        if not (np.isfinite(z.iloc[i]) and abs(z.iloc[i]) >= entry_z
                and np.isfinite(hl.iloc[i]) and z.iloc[i] != 0):
            i += 1
            continue
        e = i
        s = -np.sign(resid.iloc[e])
        vol_e = resid.iloc[e] / z.iloc[e]                      # entry-date residual vol
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
        # frozen z path through live exit + extra window
        end = min(x + config.V4_FROZEN_EXTRA_BD, n - 1)
        fz = (yv[e:end + 1] - Xv[e:end + 1] @ b_e) / vol_e
        fz_x = fz[x - e]
        frozen_status = ("closed" if abs(fz_x) < exit_z
                         else "worse" if abs(fz_x) > abs(fz[0]) else "open")
        phantom = (reason == "converged") and frozen_status != "closed"
        post = np.where(np.abs(fz[x - e:]) < exit_z)[0]
        frozen_lag = int(post[0]) if len(post) else np.nan
        # decomposition components at live exit
        pnl_price = s * (yv[x] - yv[e])
        fair_factor = -s * float((Xv[x] - Xv[e]) @ b_e)
        fair_coef = -s * float(Xv[x] @ (Bv[x] - b_e))
        path = s * (yv[e:x + 1] - yv[e])
        q = quad.reindex(idx).iloc[e]
        rows.append({
            "entry": idx[e], "exit": idx[x], "days": x - e, "exit_reason": reason,
            "side": "long_BE" if s > 0 else "short_BE",
            "z_entry": z.iloc[e], "quadrant": q,
            "b_state": ("confirm" if q in ("both_cheap", "both_rich")
                        else "contradict" if q == "disagree" else "neutral"),
            "pnl_price_bp": pnl_price, "fair_factor_bp": fair_factor,
            "fair_coef_bp": fair_coef, "gap_closed_bp": pnl_price + fair_factor + fair_coef,
            "mae_bp": float(path.min()), "mfe_bp": float(path.max()),
            "z_frozen_exit": float(fz_x), "frozen_status": frozen_status,
            "phantom": bool(phantom), "frozen_close_lag_bd": frozen_lag,
            **{c: st_idx[c].iloc[e] for c in STATE_COLS},
        })
        i = x + 1
    ep = pd.DataFrame(rows)
    ep.to_parquet(cache_f)
    return ep


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for ez in config.V3_ENTRY_GRID:
        ep = episodes(ez, use_cache=False)
        print(f"entry_z={ez}: {len(ep)} episodes, phantom rate "
              f"{ep['phantom'].mean():.2f}, crisis share {ep['crisis'].mean():.2f}")
