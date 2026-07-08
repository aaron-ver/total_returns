"""
v6 figures: the phantom-vs-staleness frontier (lead figure of REPORT_V6).

Usage:  python -m breakeven_rv.v6_figures
"""
from __future__ import annotations
import os, sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config

BLUE, AQUA, YELLOW, RED = "#2a78d6", "#1baf7a", "#eda100", "#e34948"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"

FAMILY = {  # architecture family -> (color, marker)
    "bench_rolling504": (INK2, "o"), "bench_ewls252": (INK2, "s"),
    "bench_v5_frozen_stress": (BLUE, "o"),
    "h2_ewls504_stress": (AQUA, "o"), "h2_ewls1008_stress": (AQUA, "s"),
    "h3_ridge1_stress": (YELLOW, "o"), "h3_ridge5_stress": (YELLOW, "s"),
    "ref_frozen_quietcombined_FAILEDBUDGET": (RED, "x"),
}
SHORT = {"bench_rolling504": "rolling", "bench_ewls252": "ewls252",
         "bench_v5_frozen_stress": "frozen", "h2_ewls504_stress": "ewls-seg 2y",
         "h2_ewls1008_stress": "ewls-seg 4y", "h3_ridge1_stress": "ridge 1",
         "h3_ridge5_stress": "ridge 5",
         "ref_frozen_quietcombined_FAILEDBUDGET": "combined (FAILED budget)"}


def fig_frontier():
    fr = pd.read_csv(os.path.join(config.REPORTS, "v6_frontier.csv"))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), sharey=True)
    for ax, tenor in zip(axes, config.V5_TENORS):
        sub = fr[fr["tenor"] == tenor]
        for _, r in sub.iterrows():
            col, mk = FAMILY.get(r["model"], (INK2, "o"))
            ax.scatter(r["stale_wrong_days"], r["phantom_rate"], color=col,
                       marker=mk, s=55, zorder=3)
            ax.annotate(SHORT.get(r["model"], r["model"]),
                        (r["stale_wrong_days"], r["phantom_rate"]),
                        textcoords="offset points", xytext=(6, 4),
                        fontsize=7, color=INK2)
        ax.set_xlabel("days structurally wrong (|60bd mean| > 2sd)", color=INK2, fontsize=8)
        ax.set_facecolor(SURFACE)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(GRID)
        ax.grid(color=GRID, lw=0.8)
        ax.tick_params(colors=INK2, labelsize=8)
        ax.set_title(tenor, color=INK, fontsize=10, loc="left")
    axes[0].set_ylabel("phantom-resolution rate", color=INK2, fontsize=9)
    fig.set_facecolor(SURFACE)
    fig.suptitle("The honesty–freshness frontier: phantom rate vs staleness, per architecture "
                 "(down-left = better; no point dominates)", color=INK, fontsize=11,
                 x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(os.path.join(config.FIGURES, "v6_frontier.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    config.ensure_dirs()
    fig_frontier()
    print(f"wrote v6_frontier.png -> {config.FIGURES}")
