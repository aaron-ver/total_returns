"""
One-off experiment: gasoline hedge ratio for the PURE breakeven (beta=100%, equal-DV01
TIPS vs UST) versus a 75%-beta breakeven (TIPS - 0.75*UST, i.e. the nominal leg
under-weighted to 75k DV01).

Same machinery as hedge.py (daily walk-forward regression of breakeven $ on gas $/contract,
2y & 5y rolling windows, monthly rebalance) -- only the breakeven definition changes via the
new `beta` arg on hedge.monthly_hedge_ratios. Reports the latest contracts/R^2 per tenor &
window for each beta and plots the two hedge-ratio paths.

CLI:
  python hedge_beta.py            # table + plot (plots/hedge_beta_compare.png)
  python hedge_beta.py table      # just the table
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import hedge

HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")
TENORS = ("5y", "10y", "30y")
BETAS = {"BE100": 1.0, "BE75": 0.75}     # pure breakeven vs 75%-beta breakeven


def _ratios():
    """{(label, tenor): monthly_hedge_ratios frame} for each beta/tenor."""
    return {(lab, t): hedge.monthly_hedge_ratios(t, beta=b) for lab, b in BETAS.items() for t in TENORS}


def table(R=None):
    R = R or _ratios()
    print("Gasoline hedge ratio (# contracts) — pure breakeven (BE100) vs 75%-beta (BE75)\n")
    for win in ("2y", "5y"):
        col = f"hedge_contracts_{win}"; r2c = f"r2_{win}"
        print(f"--- {win} window (latest month) ---")
        print(f"  {'tenor':6s} {'BE100':>14s} {'BE75':>14s}   BE75/BE100   corr(paths)")
        for t in TENORS:
            a = R[("BE100", t)].dropna(subset=[col]); b = R[("BE75", t)].dropna(subset=[col])
            if a.empty or b.empty:
                continue
            la, lb = a.iloc[-1], b.iloc[-1]
            # correlation of the two monthly hedge-ratio paths over their common months
            m = a.set_index("month")[col].align(b.set_index("month")[col], join="inner")
            corr = m[0].corr(m[1]) if len(m[0]) > 2 else np.nan
            print(f"  {t:6s} {la[col]:7.1f} (R²{la[r2c]:.2f}) {lb[col]:7.1f} (R²{lb[r2c]:.2f})   "
                  f"{lb[col]/la[col]:6.2f}      {corr:.3f}")
        print()


def plot(R=None, path=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    os.makedirs(PLOTS, exist_ok=True)
    R = R or _ratios()
    path = path or os.path.join(PLOTS, "hedge_beta_compare.png")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharex=True)
    for ax, t in zip(axes, TENORS):
        for lab, c in (("BE100", "tab:blue"), ("BE75", "tab:red")):
            for win, style in (("5y", dict(lw=1.6, ls="-")), ("2y", dict(lw=0.9, ls=":", alpha=0.7))):
                s = R[(lab, t)].dropna(subset=[f"hedge_contracts_{win}"]).sort_values("month")
                if not s.empty:
                    ax.plot(s["month"], s[f"hedge_contracts_{win}"], color=c,
                            label=f"{lab} ({win})", **style)
        ax.set_title(t); ax.axhline(0, color="k", lw=0.4); ax.grid(alpha=0.3)
        ax.set_xlabel("rebalance month"); ax.legend(fontsize=7, loc="upper left")
        if t == "5y":
            ax.set_ylabel("hedge ratio (# contracts)")
        ax.xaxis.set_major_locator(mdates.YearLocator(2)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle("Gasoline hedge ratio: pure breakeven (BE100) vs 75%-beta breakeven (BE75) "
                 "— solid = 5y window, dotted = 2y", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=110); plt.close(fig)
    print(f"  wrote {path}")
    return path


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    R = _ratios()
    table(R)
    if not (len(sys.argv) > 1 and sys.argv[1] == "table"):
        plot(R)
