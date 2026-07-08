"""
v6 core — sequential segmentation engine + model-evaluation harness.

`segment_run(...)` walks the sample once, in real time:
  - within the current segment, the MODEL produces a residual each day:
      frozen : betas fit once on the segment's first `burnin` days, then constant
      ewls   : betas re-fit daily on within-segment data, exponentially weighted
               (long half-life) — the "slow-within" hybrid
      ridge  : betas re-fit daily on the trailing 504bd (may span breaks) but shrunk
               toward the current segment's frozen anchor
  - TRIGGERS watched daily (all real-time): 'stress' = v5 monitor CRISIS >= 10
    consecutive bd; 'drift' = Page CUSUM on the segment residual / burn-in sd (the
    honest-residual drift — the PRIME quiet candidate); 'ferr' = rolling 60bd resid
    sd vs first-60bd sd; 'macro' = Mahalanobis distance of the macro vector from the
    within-segment mean/cov. A fired trigger = break; new segment starts; model
    abstains through the new burn-in.

`evaluate(...)` scores any (resid, betas) pair identically (v4/v5 conventions):
  phantom rate (frozen-z audit, entry |z| >= 1), staleness (max |60bd median resid|;
  bd with |60bd mean| > 2x model resid sd), abstention days, episode decomposition
  price share.

Usage: imported by v6_partA/B/C.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config
from breakeven_rv.validation import rolling_half_life


# ---------------------------------------------------------------- triggers ---
class DriftCusum:
    def __init__(self):
        self.reset(1.0)

    def reset(self, sd):
        self.sd = max(sd, 1e-9)
        self.sp = self.sn = 0.0

    def step(self, resid) -> bool:
        z = resid / self.sd
        self.sp = max(0.0, self.sp + z - config.V6_QUIET_CUSUM_K)
        self.sn = max(0.0, self.sn - z - config.V6_QUIET_CUSUM_K)
        return max(self.sp, self.sn) > config.V6_QUIET_CUSUM_H


class FErr:
    def __init__(self):
        self.reset(1.0)

    def reset(self, sd):
        self.sd0 = max(sd, 1e-9)
        self.buf, self.run = [], 0

    def step(self, resid) -> bool:
        self.buf.append(resid)
        if len(self.buf) < 60:
            return False
        cur = np.std(self.buf[-60:])
        self.run = self.run + 1 if cur > config.V6_FERR_MULT * self.sd0 else 0
        return self.run >= config.V6_FERR_RUN


class MacroShift:
    """Mahalanobis distance of the macro vector from the segment's opening state."""
    def __init__(self, M: np.ndarray):
        self.M = M          # (n, m) macro matrix aligned to the run's index
        self.reset(0)

    def reset(self, t0):
        self.t0, self.mu, self.icov, self.run = t0, None, None, 0

    def step(self, t) -> bool:
        if t - self.t0 < config.V6_MACRO_MIN:
            return False
        if self.mu is None:
            # arm on the first V6_MACRO_MIN VALID rows after t0 (a NaN prefix in a
            # macro series must not leave the detector unarmed forever)
            W = self.M[self.t0: t + 1]
            W = W[~np.isnan(W).any(axis=1)]
            if len(W) < config.V6_MACRO_MIN:
                return False
            W = W[:config.V6_MACRO_MIN]
            self.mu = W.mean(axis=0)
            cov = np.cov(W.T) + 1e-6 * np.eye(W.shape[1])
            self.icov = np.linalg.inv(cov)
        v = self.M[t]
        if np.isnan(v).any():
            return False
        d = float(np.sqrt((v - self.mu) @ self.icov @ (v - self.mu)))
        self.run = self.run + 1 if d > config.V6_MACRO_D else 0
        return self.run >= config.V6_MACRO_RUN


# ----------------------------------------------------------------- engine ----
def segment_run(idx, y, X, crisis=None, macro=None, quiet: str = "none",
                use_stress: bool = True, model: str = "frozen",
                ewls_hl: float = 504, ridge_alpha: float = 1.0,
                burnin: int = None):
    """One real-time pass. Returns (resid Series, betas (n,k), breaks list[int],
    break_trigger list[str], abstain_days int)."""
    burnin = config.V5_BURNIN if burnin is None else burnin
    n, k = X.shape
    resid = np.full(n, np.nan)
    betas = np.full((n, k), np.nan)
    breaks, trig_names = [], []
    drift, ferr = DriftCusum(), FErr()
    mac = MacroShift(macro) if (macro is not None and quiet in ("macro", "combined")) else None
    t0 = 0
    b_seg, sd_seg = None, None
    crisis_run = 0
    abstain = 0
    no_trig_until = 0            # refractory after each break (config.V5_REFRACTORY, as
    t = 0                        # declared for the v5 detectors) — prevents re-firing
    while t < n:                 # on the same ongoing event
        seg_age = t - t0
        if seg_age < burnin:
            abstain += 1
            t += 1
            continue
        if seg_age == burnin:
            b_seg, *_ = np.linalg.lstsq(X[t0:t], y[t0:t], rcond=None)
            sd_seg = np.std(y[t0:t] - X[t0:t] @ b_seg) or 1.0
            drift.reset(sd_seg)
            ferr.reset(sd_seg)
            if mac is not None:
                mac.reset(t0)
        # model residual for day t
        if model == "frozen":
            b_t = b_seg
        elif model == "ewls":
            w = 0.5 ** (np.arange(seg_age - 1, -1, -1) / ewls_hl)
            sw = np.sqrt(w)
            b_t, *_ = np.linalg.lstsq(X[t0:t] * sw[:, None], y[t0:t] * sw, rcond=None)
        elif model == "ridge":
            lo = max(0, t - 504)
            Xw, yw = X[lo:t], y[lo:t]
            G = Xw.T @ Xw
            lam = ridge_alpha * np.trace(G) / k
            b_t = np.linalg.solve(G + lam * np.eye(k), Xw.T @ yw + lam * b_seg)
        betas[t] = b_t
        r = y[t] - X[t] @ b_t
        resid[t] = r
        # triggers (watched on the FROZEN-anchor residual for drift/ferr so that an
        # adapting model can't hide the drift from its own detector)
        r_frozen = y[t] - X[t] @ b_seg
        fired = None
        if t >= no_trig_until:
            if use_stress and crisis is not None:
                crisis_run = crisis_run + 1 if crisis[t] else 0
                if crisis_run >= config.V5_MONITOR_RUN:
                    fired = "stress"
            if fired is None and quiet in ("drift", "combined") and drift.step(r_frozen):
                fired = "drift"
            if fired is None and quiet == "ferr" and ferr.step(r_frozen):
                fired = "ferr"
            if fired is None and quiet in ("macro", "combined") and mac is not None \
                    and mac.step(t):
                fired = "macro"
        if fired:
            breaks.append(t)
            trig_names.append(fired)
            t0, crisis_run = t, 0
            no_trig_until = t + config.V5_REFRACTORY
        t += 1
    return pd.Series(resid, index=idx), betas, breaks, trig_names, abstain


# --------------------------------------------------------------- evaluate ----
def evaluate(idx, y, X, resid: pd.Series, betas: np.ndarray, tenor: str,
             abstain: int, entry_z: float = 1.0) -> dict:
    """Uniform scorecard for any (resid, betas) model output (v4/v5 conventions)."""
    exit_z = config.V4_EXIT_Z
    sd = resid.rolling(config.Z_WINDOW, min_periods=config.V5_SEG_Z_MIN).std()
    z = resid / sd
    hl = rolling_half_life(resid, config.HL_WINDOW,
                           config.V5_HL_CLIP.get(tenor, (5, 60)))
    zv, rv, hlv = z.values, resid.values, hl.values
    n = len(idx)
    eps, i = [], 0
    while i < n:
        if not (np.isfinite(zv[i]) and abs(zv[i]) >= entry_z and np.isfinite(hlv[i]) and zv[i] != 0):
            i += 1
            continue
        e = i
        s_dir = -np.sign(rv[e])
        vol_e = rv[e] / zv[e]
        b_e = betas[e]
        stop = e + int(round(config.EP_HL_MULT * hlv[e]))
        x, reason = None, None
        for j in range(e + 1, min(stop, n - 1) + 1):
            if np.isfinite(zv[j]) and abs(zv[j]) < exit_z:
                x, reason = j, "converged"
                break
        if x is None:
            x = min(stop, n - 1)
            reason = "time_stop" if x == stop else "sample_end"
        fz_x = (y[x] - X[x] @ b_e) / vol_e
        pnl_price = s_dir * (y[x] - y[e])
        fair_factor = -s_dir * float((X[x] - X[e]) @ b_e)
        b_x = betas[x] if np.isfinite(betas[x]).all() else b_e
        fair_coef = -s_dir * float(X[x] @ (b_x - b_e))
        eps.append({"reason": reason, "phantom": reason == "converged" and abs(fz_x) >= exit_z,
                    "pnl_price": pnl_price,
                    "gap": pnl_price + fair_factor + fair_coef})
        i = x + 1
    ep = pd.DataFrame(eps)
    conv = ep[ep["reason"] == "converged"] if len(ep) else ep
    # staleness metrics
    med60 = resid.rolling(config.V6_STALE_WIN).median()
    mean60 = resid.rolling(config.V6_STALE_WIN).mean()
    model_sd = resid.std()
    wrong_days = int((mean60.abs() > config.V6_STALE_MULT * model_sd).sum())
    tot_gap = ep["gap"].sum() if len(ep) else np.nan
    return {"phantom_rate": conv["phantom"].mean() if len(conv) else np.nan,
            "n_episodes": len(ep), "n_converged": len(conv),
            "price_share": ep["pnl_price"].sum() / tot_gap if len(ep) and tot_gap else np.nan,
            "stale_max_med60_bp": float(med60.abs().max()),
            "stale_wrong_days": wrong_days,
            "abstain_days": abstain}


def tenor_data(tenor: str):
    """(idx, y_bp, X[const, factors x100], crisis bool array) for a US tenor."""
    from breakeven_rv import panel as panel_mod, v5_core
    p = panel_mod.load()
    scol = f"swap_{tenor.rstrip('y')}y"
    df = pd.concat([p[scol].rename("_y") * 100.0, p[config.L1_FACTORS] * 100.0], axis=1).dropna()
    y = df["_y"].values
    X = np.column_stack([np.ones(len(df)), df[config.L1_FACTORS].values])
    crisis = v5_core.state_frame(tenor)["crisis"].reindex(df.index).fillna(False).values
    return df.index, y, X, crisis


def layer1_betas_for(idx, tenor: str) -> tuple[pd.Series, np.ndarray]:
    """Rolling-504 baseline (resid, betas) transformed to the tenor_data basis
    (const column = 1, factor columns x100 -> beta_const x100, factor betas as-is)."""
    from breakeven_rv import v5_core
    l1 = v5_core.load_layer1(tenor).reindex(idx)
    resid = l1["resid_ols_bp"]
    B = np.column_stack([l1["beta_const"].values * 100.0]
                        + [l1[f"beta_{c}"].values for c in config.L1_FACTORS])
    return resid, B


def macro_matrix(idx) -> np.ndarray:
    """[CPI y/y (publication-lagged), front-end nominal level, gasoline 1y change]
    aligned to idx — the macro-state vector for the 'macro' trigger (US tenors)."""
    from breakeven_rv import panel as panel_mod
    p = panel_mod.load()
    m = pd.DataFrame({
        "cpi": p["cpi_yoy_lagged"], "front": p["dgs2"],
        "gas1y": p["log_gas"].diff(252)}).reindex(idx).ffill(limit=5)
    return m.values
