"""
Go/no-go reversion test (plan §8 step 1) — the project's pivot point.

For each residual (A, B) and horizon h in config.HORIZONS:
    resid[t+h] − resid[t]  =  a + b * resid[t] + e
b < 0 (significant, NW lags >= h) means large residuals are followed by convergence.
If residuals are random walks (b ~ 0), THERE IS NO STRATEGY and nothing downstream
fixes it. The same regression is run inside each quadrant subsample (plan §2.3):
the novel claim is that agree-quadrants revert faster/more reliably than
model-error quadrants.

Also reported: AR(1) half-lives, |z|>threshold hit rates, and a regime-robustness
split (pre-COVID / COVID / 2021-22 spike / post) — a one-regime result is not a result.

Output: reports/reversion.csv + printed tables.
Usage:  python -m breakeven_rv.reversion
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, residuals
from breakeven_rv.validation import nw_ols, half_life, hit_rate

OUT = os.path.join(config.REPORTS, "reversion.csv")

REGIMES = {
    "2008_to_2014":    ("2006-01-01", "2014-12-31"),   # sample starts 2008-10 (see IMPLEMENTATION.md)
    "2015_to_covid":   ("2015-01-01", "2020-02-28"),
    "covid":           ("2020-03-01", "2020-12-31"),
    "2021_22_spike":   ("2021-01-01", "2022-12-31"),
    "2023_present":    ("2023-01-01", "2099-01-01"),
}


def _test(resid: pd.Series, z: pd.Series, h: int) -> dict:
    fwd = resid.shift(-h) - resid
    r = nw_ols(fwd, resid, lags=h + 2)
    hr = hit_rate(z, fwd, config.Z_THRESHOLD)
    key = resid.name
    return {"h": h, "n": r.get("n"), "beta": r.get(f"beta_{key}"),
            "t_NW": r.get(f"t_{key}"), "r2": r.get("r2"),
            "hit_rate_|z|>1": hr["hit"], "n_signal": hr["n"]}


def run() -> pd.DataFrame:
    config.ensure_dirs()
    res = residuals.load().dropna(subset=["z_A", "z_B"])
    rows = []

    for name, rc, zc in (("A_fundamental", "resid_A_bp", "z_A"),
                         ("B_liquidity", "resid_B_bp", "z_B")):
        resid, z = res[rc], res[zc]
        # B's tradeable dislocation is the deviation from its trailing norm, not the raw
        # iota level (which has a persistent negative mean) — demean with the z-window.
        if name.startswith("B"):
            resid = resid - resid.rolling(config.Z_WINDOW, min_periods=config.Z_MIN_PERIODS).mean()
            resid.name = rc
        for h in config.HORIZONS:
            rows.append({"signal": name, "sample": "full", **_test(resid, z, h)})
        for reg, (lo, hi) in REGIMES.items():
            sub = resid.loc[lo:hi]
            if len(sub) > 100:
                rows.append({"signal": name, "sample": reg, **_test(sub, z.loc[lo:hi], 10)})
        rows.append({"signal": name, "sample": "half_life_bd",
                     "h": None, "n": None, "beta": half_life(resid.dropna()),
                     "t_NW": None, "r2": None, "hit_rate_|z|>1": None, "n_signal": None})

    # quadrant-conditional reversion (h=10): does agreement predict better convergence?
    h = 10
    for name, rc in (("A_fundamental", "resid_A_bp"), ("B_liquidity", "resid_B_bp")):
        resid = res[rc]
        if name.startswith("B"):
            resid = resid - resid.rolling(config.Z_WINDOW, min_periods=config.Z_MIN_PERIODS).mean()
            resid.name = rc
        fwd = resid.shift(-h) - resid
        for q in ("both_cheap", "both_rich", "A_only_cheap", "A_only_rich",
                  "B_only_cheap", "B_only_rich", "disagree"):
            mask = res["quadrant"] == q
            if mask.sum() < 40:
                continue
            r = nw_ols(fwd[mask], resid[mask], lags=h + 2)
            rows.append({"signal": name, "sample": f"quad:{q}", "h": h, "n": r.get("n"),
                         "beta": r.get(f"beta_{rc}"), "t_NW": r.get(f"t_{rc}"), "r2": r.get("r2"),
                         "hit_rate_|z|>1": None, "n_signal": int(mask.sum())})

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(out.round(3).to_string(index=False))
    print(f"\n  wrote {OUT}")
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
