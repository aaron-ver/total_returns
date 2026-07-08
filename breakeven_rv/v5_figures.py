"""
v5 figures (reports/figures/):
  v5_flag_timeline.png   monitor flag count + episode PnL path, 2020 and 2022 (10y)
  v5_segmented_fv.png    segmented (monitor-detector) vs rolling FV, 10y, break dates

Usage:  python -m breakeven_rv.v5_figures
"""
from __future__ import annotations
import json, os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, v5_core

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


def fig_flag_timeline():
    p = panel_mod.load()
    st = v5_core.state_frame("10y")
    y = (p["swap_10y"] * 100).dropna()
    episodes = [("2020-02-19", "2020-04-01", "2020 (COVID spiral)"),
                ("2022-02-28", "2022-04-13", "2022 (tightening stress)")]
    fig, axes = plt.subplots(2, 2, figsize=(11, 6), sharex="col",
                             height_ratios=[2.2, 1])
    for c, (e, x, name) in enumerate(episodes):
        seg = y.loc[e:x]
        path = seg - seg.iloc[0]                       # long-BE episode PnL path, bp
        flags = st["flags"].reindex(seg.index).fillna(0)
        ax = axes[0, c]
        ax.axhline(0, color=INK2, lw=0.6)
        crisis = flags >= config.V5_MONITOR_MIN_FLAGS
        for t0, on in zip(seg.index, crisis):
            if on:
                ax.axvspan(t0, t0 + pd.Timedelta(days=1), color=RED, alpha=0.10, lw=0)
        ax.plot(seg.index, path.values, color=BLUE, lw=2)
        _style(ax, f"{name}: episode PnL path (long BE, bp); CRISIS days shaded")
        ax2 = axes[1, c]
        ax2.step(seg.index, flags.values, where="mid", color=AQUA, lw=1.8)
        ax2.axhline(config.V5_MONITOR_MIN_FLAGS, color=INK2, lw=0.8, ls=":")
        ax2.set_ylim(-0.2, 4.2)
        ax2.set_ylabel("flags (of 4)", color=INK2, fontsize=8)
        _style(ax2)
        ax2.tick_params(axis="x", rotation=30)
    axes[0, 0].set_ylabel("bp", color=INK2, fontsize=9)
    fig.suptitle("The monitor in-flight: flag count vs episode damage (10y)",
                 color=INK, fontsize=11, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(os.path.join(config.FIGURES, "v5_flag_timeline.png"), dpi=150)
    plt.close(fig)


def fig_segmented_fv():
    f = pd.read_parquet(os.path.join(config.CACHE, "v5_seg_fv_10y.parquet"))
    with open(os.path.join(config.CACHE, "v5_breaks_10y.json")) as fh:
        breaks = json.load(fh)["monitor"]
    l1 = v5_core.load_layer1("10y")
    fig, ax = plt.subplots(figsize=(10.5, 4.5))
    ax.plot(f.index, f["y_bp"], color=INK2, lw=0.8, alpha=0.7, label="10y CPI swap (bp)")
    ax.plot(l1.index, l1["fv_ols"] * 100, color=AQUA, lw=1.4, label="rolling FV (504bd)")
    ax.plot(f.index, f["fv_monitor"], color=BLUE, lw=1.4,
            label="segmented FV (monitor detector, frozen per segment)")
    for i, b in enumerate(breaks):
        ax.axvline(pd.Timestamp(b), color=RED, lw=1.0, ls=":",
                   label="regime break" if i == 0 else None)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="upper left")
    ax.set_ylabel("bp", color=INK2, fontsize=9)
    _style(ax, "Piecewise-stable vs rolling fair value, 10y — monitor-detector breaks "
               f"({', '.join(b[:7] for b in breaks)})")
    fig.tight_layout()
    fig.savefig(os.path.join(config.FIGURES, "v5_segmented_fv.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    config.ensure_dirs()
    fig_flag_timeline()
    fig_segmented_fv()
    print(f"wrote 2 v5 figures -> {config.FIGURES}")
