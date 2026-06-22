"""
Interactive explorer for the financed breakeven total-return series.

Controls:
  * repo half-spread sliders  x_TIPS, x_UST (bp)   -- a long leg pays GC+x, a short leg earns GC-x
  * tenor selector            5y / 10y / 30y
  * view selector             chart  |  table
  * start / end date boxes    (YYYY-MM-DD; blank = full history) -- windows BOTH views

CHART view : cumulative net P&L (bp, linear-sum) over the window for long-BE, short-BE, and
             the mid (zero-spread) reference, rebased to the window start.
TABLE view : RAW NUMBERS. One row per day if the window <= 45 days, else one row per month
             (sum of daily bp). Columns TIPS / UST / BEmid / longBE / shortBE, plus a TOTAL
             row = the window net P&L. (Long table is tail-trimmed; narrow with start/end.)

Both BE directions carry the slippage drag (they are not mirror images). Specialness is not
modeled (symmetric bid/offer only). reference.MD §7, §9.3.

Run:  python interactive.py                  (window; needs a desktop GUI backend)
      python interactive.py 5 4 10y           (headless chart snapshot)
For raw numbers in the terminal:  python engine.py window <tenor> <start> <end> <xT> <xU>
"""
from __future__ import annotations
import os, sys
import pandas as pd

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
PLOTS = os.path.join(HERE, "plots")
TENORS = ["5y", "10y", "30y"]


def _load():
    out = {}
    for t in TENORS:
        p = os.path.join(CACHE, f"returns_{t}.parquet")
        if os.path.exists(p):
            out[t] = engine.load_returns(t)
    if not out:
        raise SystemExit("No returns_*.parquet — run:  python engine.py")
    return out


def _window_cum(rets, tenor, xT, xU, start, end):
    """Cumulative (rebased) long/short/mid BE over the window, for the chart."""
    d = engine.apply_spread(rets[tenor], xT, xU)
    if start:
        d = d[d.index >= pd.Timestamp(start)]
    if end:
        d = d[d.index <= pd.Timestamp(end)]
    d = d.dropna(how="all")
    return d[["longBE_bp", "shortBE_bp", "BEmid_bp"]].cumsum()


def _save_png(rets, xT, xU, tenor, start=None, end=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cum = _window_cum(rets, tenor, xT, xU, start, end)
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(cum.index, cum["BEmid_bp"], color="0.6", lw=1, label=f"mid: {cum['BEmid_bp'].iloc[-1]:+.0f}bp")
    ax.plot(cum.index, cum["longBE_bp"], color="tab:green", lw=1.2, label=f"long BE: {cum['longBE_bp'].iloc[-1]:+.0f}bp")
    ax.plot(cum.index, cum["shortBE_bp"], color="tab:red", lw=1.2, label=f"short BE: {cum['shortBE_bp'].iloc[-1]:+.0f}bp")
    ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=0.3)
    ax.set_title(f"{tenor} financed breakeven net P&L  (x_TIPS={xT}bp, x_UST={xU}bp)")
    ax.set_ylabel("cumulative net P&L (bp, linear-sum)"); ax.legend(loc="upper left")
    fig.tight_layout()
    os.makedirs(PLOTS, exist_ok=True)
    out = os.path.join(PLOTS, "interactive_snapshot.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"  wrote {out}")


def run_interactive(rets):
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Slider, RadioButtons, TextBox
    st = {"tenor": "10y", "view": "chart", "start": None, "end": None}

    fig = plt.figure(figsize=(13, 8))
    ax = fig.add_axes([0.30, 0.27, 0.66, 0.63])
    ax_xt = fig.add_axes([0.30, 0.13, 0.55, 0.03])
    ax_xu = fig.add_axes([0.30, 0.08, 0.55, 0.03])
    s_xt = Slider(ax_xt, "repo x_TIPS (bp)", 0.0, 25.0, valinit=3.0, valstep=0.5)
    s_xu = Slider(ax_xu, "repo x_UST (bp)", 0.0, 25.0, valinit=3.0, valstep=0.5)
    ax_ten = fig.add_axes([0.03, 0.68, 0.18, 0.22]); ax_ten.set_title("tenor", fontsize=9)
    r_ten = RadioButtons(ax_ten, TENORS, active=TENORS.index(st["tenor"]))
    ax_vw = fig.add_axes([0.03, 0.50, 0.18, 0.14]); ax_vw.set_title("view", fontsize=9)
    r_vw = RadioButtons(ax_vw, ["chart", "table"], active=0)
    ax_s = fig.add_axes([0.10, 0.41, 0.13, 0.04])
    ax_e = fig.add_axes([0.10, 0.35, 0.13, 0.04])
    tb_s = TextBox(ax_s, "start ", initial=""); tb_e = TextBox(ax_e, "end ", initial="")

    def _parse(v):
        v = (v or "").strip()
        if not v:
            return None
        try:
            return str(pd.Timestamp(v).date())
        except Exception:
            return None

    def redraw(_=None):
        xT, xU = s_xt.val, s_xu.val
        ten, view, start, end = st["tenor"], st["view"], st["start"], st["end"]
        ax.clear()
        win = f"{start or 'start'}..{end or 'end'}"
        if view == "chart":
            ax.axis("on")
            cum = _window_cum(rets, ten, xT, xU, start, end)
            if len(cum):
                ax.plot(cum.index, cum["BEmid_bp"], color="0.6", lw=1,
                        label=f"mid: {cum['BEmid_bp'].iloc[-1]:+.0f}bp")
                ax.plot(cum.index, cum["longBE_bp"], color="tab:green", lw=1.4,
                        label=f"long BE: {cum['longBE_bp'].iloc[-1]:+.0f}bp")
                ax.plot(cum.index, cum["shortBE_bp"], color="tab:red", lw=1.4,
                        label=f"short BE: {cum['shortBE_bp'].iloc[-1]:+.0f}bp")
                ax.legend(loc="upper left")
            ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=0.3)
            ax.set_ylabel("cumulative net P&L (bp)")
            ax.set_title(f"{ten} breakeven net P&L  {win}  (x_TIPS={xT:.1f}, x_UST={xU:.1f}bp)")
        else:
            ax.axis("off")
            tbl, tot = engine.window_table(ten, start, end, xT, xU)
            show = tbl.tail(30)
            cells = [[i] + [f"{v:+.1f}" for v in row] for i, row in zip(show.index, show.values)]
            cells.append(["TOTAL"] + [f"{v:+.1f}" for v in tot.values])
            tab = ax.table(cellText=cells, colLabels=["period"] + list(tbl.columns),
                           loc="center", cellLoc="right")
            tab.auto_set_font_size(False); tab.set_fontsize(8); tab.scale(1, 1.25)
            for j in range(len(tbl.columns) + 1):                 # bold TOTAL row
                tab[(len(cells), j)].set_text_props(weight="bold")
            extra = f"  (last 30 of {len(tbl)} rows)" if len(tbl) > 30 else ""
            ax.set_title(f"{ten} returns (bp)  {win}  x_TIPS={xT:.1f} x_UST={xU:.1f}{extra}", fontsize=10)
        fig.canvas.draw_idle()

    def set_ten(label): st["tenor"] = label; redraw()
    def set_vw(label): st["view"] = label; redraw()
    def set_s(v): st["start"] = _parse(v); redraw()
    def set_e(v): st["end"] = _parse(v); redraw()

    s_xt.on_changed(redraw); s_xu.on_changed(redraw)
    r_ten.on_clicked(set_ten); r_vw.on_clicked(set_vw)
    tb_s.on_submit(set_s); tb_e.on_submit(set_e)
    redraw()
    plt.show()


if __name__ == "__main__":
    rets = _load()
    if len(sys.argv) > 1:                       # headless chart snapshot: xT [xU] [tenor]
        xT = float(sys.argv[1]); xU = float(sys.argv[2]) if len(sys.argv) > 2 else xT
        tenor = sys.argv[3] if len(sys.argv) > 3 else "10y"
        _save_png(rets, xT, xU, tenor)
    else:
        try:
            run_interactive(rets)
        except Exception as e:
            print(f"Interactive backend unavailable ({e}). Saving a static snapshot instead.")
            _save_png(rets, 3.0, 3.0, "10y")
