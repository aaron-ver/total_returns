"""
Chart pack for the structures study. v1: Study A event-path small multiples.

Design per the dataviz method: categorical hues in fixed order (blue=full sample,
aqua=train, yellow=holdout — yellow carries a direct label per the relief rule),
text in ink tokens, recessive grid, one shared y-scale per row, zero/auction-day
reference lines. Paths are the ex-CPI-print cumulative LOO-residual means with the
event-resampled 95% bootstrap band on the full-sample line.

Usage:  python -m breakeven_structures.figures [sA]
"""
from __future__ import annotations
import os, sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config

C_FULL, C_TRAIN, C_HOLD = "#2a78d6", "#1baf7a", "#eda100"
INK, INK2 = "#0b0b0b", "#52514e"
GRID = "#e5e4de"


def sA():
    paths = pd.read_csv(os.path.join(config.REPORTS, "sA_paths.csv"))
    stats = pd.read_csv(os.path.join(config.REPORTS, "sA_stats.csv"))
    kinds = ["new_issue", "reopen"]
    buckets = ["5y", "10y", "30y"]

    fig, axes = plt.subplots(len(kinds), len(buckets), figsize=(12, 6.5),
                             sharex=True, sharey="row")
    fig.patch.set_facecolor("white")

    for i, kind in enumerate(kinds):
        for j, b in enumerate(buckets):
            ax = axes[i, j]
            cell = paths[(paths.bucket == b) & (paths.kind == kind)]
            ax.axhline(0, color=GRID, lw=1, zorder=1)
            ax.axvline(0, color=INK2, lw=0.8, ls=(0, (2, 3)), zorder=1)
            ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            for spine in ("left", "bottom"):
                ax.spines[spine].set_color(GRID)
            ax.tick_params(colors=INK2, labelsize=8)

            if cell.empty:
                ax.text(0.5, 0.5, "not establishable\n(n < 12 under LOO)",
                        transform=ax.transAxes, ha="center", va="center",
                        fontsize=8, color=INK2)
            else:
                piv = cell.pivot_table(index="rel_day", columns=["era", "series"],
                                       values="value")
                if ("full", "mean_xcpi") in piv.columns:
                    x = piv.index
                    ax.fill_between(x, piv[("full", "ci_lo")], piv[("full", "ci_hi")],
                                    color=C_FULL, alpha=0.14, lw=0, zorder=2)
                    ax.plot(x, piv[("full", "mean_xcpi")], color=C_FULL, lw=2, zorder=4)
                for era, c, lbl, dy in (("train", C_TRAIN, "train", -5),
                                        ("holdout", C_HOLD, "holdout", 5)):
                    col = (era, "mean_xcpi")
                    if col in piv.columns:
                        ax.plot(piv.index, piv[col], color=c, lw=1.4, zorder=3)
                        ax.annotate(lbl, (piv.index[-1], piv[col].iloc[-1]),
                                    xytext=(3, dy), textcoords="offset points",
                                    fontsize=7, color=c, va="center")
                srow = stats[(stats.bucket == b) & (stats.kind == kind) & (stats.era == "full")]
                if len(srow) and not bool(srow.not_establishable.iloc[0]):
                    s = srow.iloc[0]
                    ax.text(0.02, 0.96,
                            f"n={int(s['n'])}  conc {s.concession_bp:+.2f}bp (p={s.p_concession:.2f})\n"
                            f"retrace5 {s.retrace_5_bp:+.2f}bp (p={s.p_retrace_5:.2f})",
                            transform=ax.transAxes, va="top", fontsize=7, color=INK2)
            if i == 0:
                ax.set_title(b, fontsize=10, color=INK)
            if j == 0:
                ax.set_ylabel(f"{kind.replace('_', ' ')}\ncum. residual (bp, cheap +)",
                              fontsize=8, color=INK)
            if i == len(kinds) - 1:
                ax.set_xlabel("business days vs auction", fontsize=8, color=INK2)

    handles = [plt.Line2D([], [], color=c, lw=2) for c in (C_FULL, C_TRAIN, C_HOLD)]
    fig.legend(handles, ["full sample (95% bootstrap CI)", "train era", "holdout era"],
               loc="upper right", frameon=False, fontsize=8, ncol=3,
               bbox_to_anchor=(0.99, 0.995))
    fig.suptitle("Study A — TIPS auction event paths: leave-one-out curve residual, "
                 "ex-CPI-print-day", fontsize=11, color=INK, x=0.01, ha="left", y=0.985)
    fig.text(0.01, 0.935, "reopenings: auctioned bond's own path · new issues: two "
             "nearest-maturity neighbors · placebo-gated stats in reports/sA_stats.csv",
             fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.915))
    out = os.path.join(config.FIGURES, "sA_paths.png")
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    sA()
