"""
The two residuals + the quadrant diagnostic (plan §2) — the core signal frame.

  Residual A (fundamental): swap_10y − Layer-1 fair value, in bp (layer1.py, primary
    swap-space fit). A < 0 = priced inflation CHEAP to the macro read.
  Residual B (liquidity/basis): iota10 = be10 − swap_10y, in bp (panel.py). No model —
    a subtraction of two traded prices. B below its trailing norm = TIPS CHEAP vs swap.
    (B is z-scored against a rolling mean/vol because the iota has a persistent
    negative level — the signal is its deviation from norm, not its sign.)

Quadrant classification at |z| > config.Z_THRESHOLD:
  both_cheap / both_rich   — the two independent lenses agree      (highest conviction)
  B_only_cheap / B_only_rich — pure liquidity/supply dislocation   (best clean revert)
  A_only_cheap / A_only_rich — fundamental read alone              (likely model error;
                                                                    do NOT trade as snap-back)
  disagree                  — opposite signs beyond threshold      (early warning)
  neutral                   — neither beyond threshold

Output: cache/residuals.parquet  [resid_A_bp, z_A, resid_B_bp, z_B, quadrant, ...]
Usage:  python -m breakeven_rv.residuals [build|status]
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, layer1
from breakeven_rv.validation import rolling_z, half_life

OUT = os.path.join(config.CACHE, "residuals.parquet")


def classify(z_a: float, z_b: float, thr: float) -> str:
    a = "cheap" if z_a < -thr else ("rich" if z_a > thr else "fair")
    b = "cheap" if z_b < -thr else ("rich" if z_b > thr else "fair")
    if a == b == "fair":
        return "neutral"
    if a == b:
        return f"both_{a}"
    if a == "fair":
        return f"B_only_{b}"
    if b == "fair":
        return f"A_only_{a}"
    return "disagree"


def build():
    config.ensure_dirs()
    p = panel_mod.load()
    l1 = layer1.load("swap10")

    out = pd.DataFrame(index=p.index)
    out["resid_A_bp"] = l1["resid_ols_bp"]
    out["z_A"] = l1["z_ols"]
    out["z_A_ewls"] = l1["z_ewls"]
    out["resid_B_bp"] = p["iota10"]
    out["z_B"] = rolling_z(p["iota10"], config.Z_WINDOW, config.Z_MIN_PERIODS)
    # tenor-matched basis z for the auction study (5y/30y auctions)
    for t in (5, 30):
        out[f"z_B_{t}y"] = rolling_z(p[f"iota{t}"], config.Z_WINDOW, config.Z_MIN_PERIODS)
    out["quadrant"] = [
        classify(a, b, config.Z_THRESHOLD) if np.isfinite(a) and np.isfinite(b) else None
        for a, b in zip(out["z_A"], out["z_B"])]

    out.to_parquet(OUT)
    v = out.dropna(subset=["z_A", "z_B"])
    print(f"  wrote {OUT}: n={len(v)} {str(v.index.min())[:10]} -> {str(v.index.max())[:10]}")
    print(f"  half-life: A={half_life(v['resid_A_bp']):.0f}bd  "
          f"B={half_life(v['resid_B_bp']):.0f}bd  (plan §3 expects B << A)")
    print("  quadrant occupancy:")
    print((v["quadrant"].value_counts(normalize=True) * 100).round(1).to_string())
    print(f"  corr(z_A, z_B) = {v['z_A'].corr(v['z_B']):.2f}  (low = genuinely two lenses)")
    return out


def load():
    if not os.path.exists(OUT):
        raise FileNotFoundError(f"{OUT} missing — run: python -m breakeven_rv.residuals build")
    return pd.read_parquet(OUT)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    else:
        df = load()
        print(df.dropna(subset=["z_A"]).tail(5).round(2).to_string())
