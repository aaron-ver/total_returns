"""
v3 figures (static matplotlib, reports/figures/):
  b_bond_seasonal.png   B_comb raw vs seasonally-adjusted (appendix, spec P1)
  v3_autopsy.png        (CM index − bond-built) BE spread around 10y auctions
  v3_rolling_sharpe.png rolling 2y Sharpe of the confirm-gated A overlay

Usage:  python -m breakeven_rv.v3_figures
"""
from __future__ import annotations
import os, sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, b_bond

# palette (validated defaults): categorical slots 1-2, ink tokens, light surface
BLUE, AQUA = "#2a78d6", "#1baf7a"
INK, INK2 = "#0b0b0b", "#52514e"
SURFACE = "#fcfcfb"
GRID = "#e6e5e1"


def _style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)


def fig_seasonal():
    bb = b_bond.load()
    raw, sa = bb["B_comb"].dropna(), bb["B_comb_sa"].dropna()
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(raw.index, raw.values, color=BLUE, lw=1.2, label="raw")
    ax.plot(sa.index, sa.values, color=AQUA, lw=1.2, label="seasonally adjusted")
    ax.text(raw.index[-1], raw.iloc[-1], "  raw", color=BLUE, fontsize=9, va="center")
    ax.text(sa.index[-1], sa.iloc[-1] - 2, "  seas. adj.", color=AQUA, fontsize=9, va="center")
    ax.legend(frameon=False, labelcolor=INK2, fontsize=9, loc="lower left")
    ax.set_ylabel("B_bond (bp)", color=INK2, fontsize=9)
    _style(ax, "B_bond: OTR/off-the-run 10y TIPS basis, raw vs seasonal-adjusted")
    fig.tight_layout()
    fig.savefig(os.path.join(config.FIGURES, "b_bond_seasonal.png"), dpi=150)
    plt.close(fig)


def fig_autopsy():
    au = pd.read_csv(os.path.join(config.REPORTS, "v3_autopsy.csv"))
    fig, ax = plt.subplots(figsize=(7, 4))
    x, m, se = au["event_day"], au["mean_spread_vs_base_bp"], au["se"]
    ax.axhline(0, color=INK2, lw=0.8)
    ax.axvline(0, color=INK2, lw=0.8, ls=":")
    ax.fill_between(x, m - 1.96 * se, m + 1.96 * se, color=BLUE, alpha=0.15, linewidth=0)
    ax.plot(x, m, color=BLUE, lw=2, marker="o", ms=4)
    ax.text(0, ax.get_ylim()[1] * 0.95, " auction day", color=INK2, fontsize=8, va="top")
    ax.set_xlabel("business days around 10y TIPS auction", color=INK2, fontsize=9)
    ax.set_ylabel("(CM index − bond-built) BE, bp vs t−20..−11 base", color=INK2, fontsize=9)
    _style(ax, "Index-artifact autopsy: index–bond BE spread\n"
               "builds into the auction, corrects at t+1 (±95% CI)")
    fig.tight_layout()
    fig.savefig(os.path.join(config.FIGURES, "v3_autopsy.png"), dpi=150)
    plt.close(fig)


def fig_rolling_sharpe():
    rs = pd.read_csv(os.path.join(config.REPORTS, "v3_rolling_sharpe.csv"),
                     index_col=0, parse_dates=True)["rolling_sharpe_2y"].dropna()
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.axhline(0, color=INK2, lw=0.8)
    ax.plot(rs.index, rs.values, color=BLUE, lw=1.6)
    ax.set_ylabel("Sharpe (2y rolling)", color=INK2, fontsize=9)
    _style(ax, "Confirm-gated A overlay: rolling 2y Sharpe (thr=1.0, cost=1.0bp) — "
               "where the edge lived")
    fig.tight_layout()
    fig.savefig(os.path.join(config.FIGURES, "v3_rolling_sharpe.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    config.ensure_dirs()
    fig_seasonal()
    fig_autopsy()
    fig_rolling_sharpe()
    print(f"wrote 3 figures -> {config.FIGURES}")
