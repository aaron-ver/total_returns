"""
v4 figures (reports/figures/):
  v4_phantom_vs_genuine.png  live-z vs frozen-z paths, a phantom and a genuine episode
  v4_wait_cost_2020.png      the 2020 spiral: entry-at-signal vs entry-at-stabilization
  v4_flow_terciles.png       decomposition price share by dealer-position tercile

Usage:  python -m breakeven_rv.v4_figures
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, layer1, residuals, v4_core

BLUE, AQUA, RED = "#2a78d6", "#1baf7a", "#e34948"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"


def _style(ax, title=None):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=INK2, labelsize=8)
    if title:
        ax.set_title(title, color=INK, fontsize=10, loc="left", pad=8)


def _z_paths(entry, exit_):
    """(live z, frozen z) daily paths for one episode."""
    p = panel_mod.load()
    res = residuals.load()
    l1 = layer1.load("swap10")
    idx = res.dropna(subset=["z_A"]).index
    z = res["z_A"].reindex(idx)
    resid = res["resid_A_bp"].reindex(idx)
    y = (p["swap_10y"] * 100.0).reindex(idx)
    X = pd.DataFrame({"const": 1.0, **{c: p[c].reindex(idx) for c in config.L1_FACTORS}}) * 100.0
    B = l1[["beta_const"] + [f"beta_{c}" for c in config.L1_FACTORS]].reindex(idx)
    e = idx.searchsorted(pd.Timestamp(entry))
    x = idx.searchsorted(pd.Timestamp(exit_))
    b_e = B.iloc[e].values
    vol_e = resid.iloc[e] / z.iloc[e]
    fz = (y.iloc[e:x + 1].values - X.iloc[e:x + 1].values @ b_e) / vol_e
    return z.iloc[e:x + 1], pd.Series(fz, index=idx[e:x + 1])


def fig_phantom():
    ep = v4_core.episodes(1.0)
    conv = ep[ep["exit_reason"] == "converged"]
    ph = conv[conv["phantom"]].nlargest(1, "fair_coef_bp").iloc[0]
    gen = conv[(~conv["phantom"]) & (conv["b_state"] == "confirm")
               & (conv["days"] >= 8)].nlargest(1, "pnl_price_bp").iloc[0]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, e, name in ((axes[0], ph, "PHANTOM"), (axes[1], gen, "GENUINE")):
        lz, fz = _z_paths(e["entry"], e["exit"])
        ax.axhline(0, color=INK2, lw=0.6)
        for b in (config.V4_EXIT_Z, -config.V4_EXIT_Z):
            ax.axhline(b, color=INK2, lw=0.6, ls=":")
        ax.plot(lz.index, lz.values, color=BLUE, lw=2, label="live z (rolling model)")
        ax.plot(fz.index, fz.values, color=AQUA, lw=2, label="frozen z (entry model)")
        _style(ax, f"{name}: {e['entry'].date()} → {e['exit'].date()} ({e['quadrant']})")
        ax.tick_params(axis="x", rotation=30)
    axes[0].set_ylabel("z", color=INK2, fontsize=9)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower left")
    fig.suptitle("Live z converges while frozen z never closes (left) vs a real resolution (right)",
                 color=INK, fontsize=10, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(os.path.join(config.FIGURES, "v4_phantom_vs_genuine.png"), dpi=150)
    plt.close(fig)


def fig_wait_cost():
    f = pd.read_parquet(os.path.join(config.CACHE, "v4_2020_paths.parquet"))
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.axhline(0, color=INK2, lw=0.6)
    ax.plot(f.index, f["cum_from_signal"], color=RED, lw=2, label="enter at signal (2020-02-19)")
    if "cum_from_stab" in f:
        ax.plot(f.index, f["cum_from_stab"], color=BLUE, lw=2,
                label="enter at stabilization (5d Δz stops worsening)")
    cat = pd.Timestamp(config.COVID_CATALYST)
    ax.axvline(cat, color=INK2, lw=0.8, ls=":")
    ax.text(cat, ax.get_ylim()[0] * 0.9, " Fed announcement (catalyst)", color=INK2, fontsize=8)
    ax.set_ylabel("cumulative episode PnL, bp (long BE)", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower left")
    _style(ax, "The 2020 spiral: catching the falling knife vs waiting for stabilization")
    fig.tight_layout()
    fig.savefig(os.path.join(config.FIGURES, "v4_wait_cost_2020.png"), dpi=150)
    plt.close(fig)


def fig_flow():
    t = pd.read_csv(os.path.join(config.REPORTS, "v4_priceshare_by_flow.csv"))
    t = t[t["cut"] == "dealer_tercile(marginal)"]
    fig, ax = plt.subplots(figsize=(6, 4))
    xs = np.arange(len(t))
    ax.axhline(0, color=INK2, lw=0.8)
    ax.bar(xs, t["price_share"], width=0.55, color=BLUE)
    for i, (v, n) in enumerate(zip(t["price_share"], t["n"])):
        ax.text(i, v + (0.03 if v >= 0 else -0.07), f"{v:+.2f}  (n={int(n)})",
                ha="center", color=INK2, fontsize=9)
    ax.set_xticks(xs, [str(x) for x in t["index"]])
    ax.set_xlabel("dealer TIPS net-position 1y-z tercile at entry", color=INK2, fontsize=9)
    ax.set_ylabel("decomposition price share", color=INK2, fontsize=9)
    _style(ax, "Reversion price share rises with dealer inventory pressure (2013+)")
    fig.tight_layout()
    fig.savefig(os.path.join(config.FIGURES, "v4_flow_terciles.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    config.ensure_dirs()
    fig_phantom()
    fig_wait_cost()
    fig_flow()
    print(f"wrote 3 v4 figures -> {config.FIGURES}")
