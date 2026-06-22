"""
Interactive explorer for the financed breakeven total-return series.

Sliders for the repo bid/offer HALF-SPREAD on each leg (x_TIPS, x_UST, in bp) and a tenor
selector. Shows the cumulative net P&L (bp, linear-sum) for the long-breakeven and short-
breakeven packages, with the mid (zero-spread) curve for reference, and prints net P&L.

Repo-spread effect is applied analytically from the stored financing-sensitivity columns
(s_TIPS, s_UST = bp drag per 1bp of half-spread), so it updates instantly without rebuilding:
    long_BE(x)  = cumsum(  r_BE_bp - x_T*s_TIPS - x_U*s_UST )
    short_BE(x) = cumsum( -r_BE_bp - x_T*s_TIPS - x_U*s_UST )
Both carry the slippage (a long leg pays GC+x, a short leg earns GC-x), so they are not
mirror images. reference.MD §7, §9.3; specialness is NOT modeled (symmetric bid/offer only).

Run:  python interactive.py        (opens a window; needs a desktop GUI backend)
      python interactive.py 5 4 10y (headless: save PNG at x_TIPS=5, x_UST=4, tenor 10y)
"""
from __future__ import annotations
import os, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
PLOTS = os.path.join(HERE, "plots")
TENORS = ["5y", "10y", "30y"]


def _load():
    out = {}
    for t in TENORS:
        p = os.path.join(CACHE, f"returns_{t}.parquet")
        if os.path.exists(p):
            out[t] = pd.read_parquet(p)
    if not out:
        raise SystemExit("No returns_*.parquet — run:  python engine.py")
    return out


def curves(df, xT, xU):
    """Return (long_BE_cum, short_BE_cum, mid_cum) Series for given half-spreads (bp)."""
    slip = xT * df["s_TIPS"] + xU * df["s_UST"]
    long_be = (df["r_BE_bp"] - slip).cumsum()
    short_be = (-df["r_BE_bp"] - slip).cumsum()
    mid = df["r_BE_bp"].cumsum()
    return long_be, short_be, mid


def _save_png(rets, xT, xU, tenor):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    df = rets[tenor]
    lb, sb, mid = curves(df, xT, xU)
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(mid.index, mid, color="0.6", lw=1, label=f"mid (x=0): {mid.dropna().iloc[-1]:+.0f}bp")
    ax.plot(lb.index, lb, color="tab:green", lw=1.2, label=f"long BE: {lb.dropna().iloc[-1]:+.0f}bp")
    ax.plot(sb.index, sb, color="tab:red", lw=1.2, label=f"short BE: {sb.dropna().iloc[-1]:+.0f}bp")
    ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=0.3)
    ax.set_title(f"{tenor} financed breakeven net P&L  (x_TIPS={xT}bp, x_UST={xU}bp)")
    ax.set_ylabel("cumulative net P&L (bp, linear-sum)"); ax.legend(loc="upper left")
    fig.tight_layout()
    os.makedirs(PLOTS, exist_ok=True)
    out = os.path.join(PLOTS, "interactive_snapshot.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"  wrote {out}  (long BE {lb.dropna().iloc[-1]:+.0f}bp, short BE {sb.dropna().iloc[-1]:+.0f}bp)")


def run_interactive(rets):
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Slider, RadioButtons
    state = {"tenor": "10y"}

    fig, ax = plt.subplots(figsize=(12, 7))
    plt.subplots_adjust(left=0.30, bottom=0.22)
    l_mid, = ax.plot([], [], color="0.6", lw=1, label="mid (x=0)")
    l_long, = ax.plot([], [], color="tab:green", lw=1.4, label="long BE")
    l_short, = ax.plot([], [], color="tab:red", lw=1.4, label="short BE")
    ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=0.3)
    ax.set_ylabel("cumulative net P&L (bp, linear-sum)")
    txt = ax.text(0.02, 0.02, "", transform=ax.transAxes, fontsize=9, va="bottom",
                  bbox=dict(boxstyle="round", fc="white", alpha=0.8))

    ax_xt = plt.axes([0.30, 0.10, 0.55, 0.03])
    ax_xu = plt.axes([0.30, 0.05, 0.55, 0.03])
    s_xt = Slider(ax_xt, "repo x_TIPS (bp)", 0.0, 25.0, valinit=3.0, valstep=0.5)
    s_xu = Slider(ax_xu, "repo x_UST (bp)", 0.0, 25.0, valinit=3.0, valstep=0.5)
    ax_radio = plt.axes([0.03, 0.45, 0.18, 0.22]); ax_radio.set_title("tenor", fontsize=9)
    radio = RadioButtons(ax_radio, TENORS, active=TENORS.index(state["tenor"]))

    def update(_=None):
        df = rets[state["tenor"]]
        xT, xU = s_xt.val, s_xu.val
        lb, sb, mid = curves(df, xT, xU)
        for line, ser in ((l_mid, mid), (l_long, lb), (l_short, sb)):
            line.set_data(ser.index, ser.values)
        ax.relim(); ax.autoscale_view()
        lbf, sbf, midf = lb.dropna().iloc[-1], sb.dropna().iloc[-1], mid.dropna().iloc[-1]
        ax.set_title(f"{state['tenor']} financed breakeven net P&L  (x_TIPS={xT:.1f}bp, x_UST={xU:.1f}bp)")
        txt.set_text(f"net P&L (cum bp)\n  long  BE: {lbf:+.0f}\n  short BE: {sbf:+.0f}\n  mid (x=0): {midf:+.0f}")
        ax.legend(loc="upper left")
        fig.canvas.draw_idle()

    def set_tenor(label):
        state["tenor"] = label; update()

    s_xt.on_changed(update); s_xu.on_changed(update); radio.on_clicked(set_tenor)
    update()
    plt.show()


if __name__ == "__main__":
    rets = _load()
    if len(sys.argv) > 1:                       # headless snapshot: x_TIPS x_UST [tenor]
        xT = float(sys.argv[1]); xU = float(sys.argv[2]) if len(sys.argv) > 2 else xT
        tenor = sys.argv[3] if len(sys.argv) > 3 else "10y"
        _save_png(rets, xT, xU, tenor)
    else:
        try:
            run_interactive(rets)
        except Exception as e:
            print(f"Interactive backend unavailable ({e}). Saving a static snapshot instead.")
            _save_png(rets, 3.0, 3.0, "10y")
