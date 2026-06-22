"""
Interactive explorer for the financed breakeven total-return series.

Controls (native-fast; repo spread applied analytically so sliders are instant):
  * repo half-spread sliders  x_TIPS, x_UST (bp)  -- long leg pays GC+x, short leg earns GC-x
  * tenor   5y / 10y / 30y
  * view    chart | table
  * freq    auto | daily | monthly   (table granularity; auto = daily if window <= ~45 days)
  * start / end date boxes (YYYY-MM-DD; blank = FULL history)
  * Export xlsx button -> exports/breakeven_returns.xlsx at the current repo spreads

CHART: cumulative net P&L (bp, linear-sum) for long-BE, short-BE, mid (zero-spread), over the
window (rebased to the window start). TABLE: raw daily/monthly numbers + a TOTAL (window P&L).
Both BE directions carry the slippage (not mirror images). Specialness not modeled.

Full data dump for hand-replication (all inputs + intermediates):  python export.py

Run:  python interactive.py            (window; needs a desktop GUI)
      python interactive.py 5 4 10y     (headless chart snapshot)
"""
from __future__ import annotations
import os, sys
import pandas as pd

import engine
import export

# --- fix matplotlib 3.11 bug: TextBox._resize is wrongly wrapped with the mouse-event
#     reparenting decorator, so a ResizeEvent (no .inaxes) crashes on every window resize.
#     _resize only needs to stop typing -> replace with an un-decorated version. ---------
try:
    import matplotlib.widgets as _mw
    def _safe_textbox_resize(self, event):
        self.stop_typing()
    _mw.TextBox._resize = _safe_textbox_resize
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
PLOTS = os.path.join(HERE, "plots")
TENORS = ["5y", "10y", "30y"]


def _precompute():
    """Per tenor, the cumulative components so the slider is a fast linear combination:
       long_cum(x)  = cumB - xT*cumST - xU*cumSU
       short_cum(x) = -cumB - xT*cumST - xU*cumSU      (cum* are full cumsums, NaN-aligned)."""
    pc = {}
    for t in TENORS:
        p = os.path.join(CACHE, f"returns_{t}.parquet")
        if not os.path.exists(p):
            continue
        df = engine.load_returns(t)
        m = df["r_BE_bp"].notna()
        comp = pd.DataFrame({
            "cumB": df["r_BE_bp"].cumsum(),
            "cumST": df["s_TIPS"].where(m).cumsum(),
            "cumSU": df["s_UST"].where(m).cumsum(),
        }).dropna()
        pc[t] = comp
    if not pc:
        raise SystemExit("No returns_*.parquet — run:  python engine.py")
    return pc


def _cum(pc, tenor, xT, xU, start, end):
    """Windowed, rebased cumulative long/short/mid BE (bp)."""
    c = pc[tenor]
    longc = c["cumB"] - xT * c["cumST"] - xU * c["cumSU"]
    shortc = -c["cumB"] - xT * c["cumST"] - xU * c["cumSU"]
    midc = c["cumB"]
    out = pd.DataFrame({"long": longc, "short": shortc, "mid": midc})
    s = pd.Timestamp(start) if start else None
    e = pd.Timestamp(end) if end else None
    if s is not None:
        base = out[out.index < s]
        out = out[out.index >= s]
        if len(base):
            out = out - base.iloc[-1]          # rebase to window start
    if e is not None:
        out = out[out.index <= e]
    return out


def _save_png(pc, xT, xU, tenor, start=None, end=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cum = _cum(pc, tenor, xT, xU, start, end)
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(cum.index, cum["mid"], color="0.6", lw=1, label=f"mid: {cum['mid'].iloc[-1]:+.0f}bp")
    ax.plot(cum.index, cum["long"], color="tab:green", lw=1.2, label=f"long BE: {cum['long'].iloc[-1]:+.0f}bp")
    ax.plot(cum.index, cum["short"], color="tab:red", lw=1.2, label=f"short BE: {cum['short'].iloc[-1]:+.0f}bp")
    ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=0.3)
    ax.set_title(f"{tenor} financed breakeven net P&L  (x_TIPS={xT}bp, x_UST={xU}bp)")
    ax.set_ylabel("cumulative net P&L (bp, linear-sum)"); ax.legend(loc="upper left")
    fig.tight_layout()
    os.makedirs(PLOTS, exist_ok=True)
    out = os.path.join(PLOTS, "interactive_snapshot.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"  wrote {out}")


def run_interactive(pc):
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Slider, RadioButtons, TextBox, Button
    st = {"tenor": "10y", "view": "chart", "freq": "auto", "start": None, "end": None}
    FREQ = {"auto": "auto", "daily": "D", "monthly": "M"}

    fig = plt.figure(figsize=(13, 8))
    ax = fig.add_axes([0.28, 0.22, 0.68, 0.70])
    ax_xt = fig.add_axes([0.30, 0.115, 0.55, 0.03]); ax_xu = fig.add_axes([0.30, 0.065, 0.55, 0.03])
    s_xt = Slider(ax_xt, "repo x_TIPS (bp)", 0.0, 25.0, valinit=3.0, valstep=0.5)
    s_xu = Slider(ax_xu, "repo x_UST (bp)", 0.0, 25.0, valinit=3.0, valstep=0.5)
    ax_t = fig.add_axes([0.02, 0.74, 0.15, 0.18]); ax_t.set_title("tenor", fontsize=9)
    r_t = RadioButtons(ax_t, TENORS, active=TENORS.index(st["tenor"]))
    ax_v = fig.add_axes([0.02, 0.62, 0.15, 0.10]); ax_v.set_title("view", fontsize=9)
    r_v = RadioButtons(ax_v, ["chart", "table"], active=0)
    ax_f = fig.add_axes([0.02, 0.46, 0.15, 0.14]); ax_f.set_title("table freq", fontsize=9)
    r_f = RadioButtons(ax_f, ["auto", "daily", "monthly"], active=0)
    tb_s = TextBox(fig.add_axes([0.085, 0.38, 0.085, 0.035]), "start ", initial="")
    tb_e = TextBox(fig.add_axes([0.085, 0.33, 0.085, 0.035]), "end ", initial="")
    btn = Button(fig.add_axes([0.03, 0.25, 0.14, 0.045]), "Export xlsx")
    status = fig.text(0.03, 0.21, "", fontsize=8, color="tab:blue")

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
        win = f"{start or 'start'}..{end or 'end'}"
        ax.clear()
        if view == "chart":
            ax.axis("on")
            cum = _cum(pc, ten, xT, xU, start, end)
            if len(cum):
                ax.plot(cum.index, cum["mid"], color="0.6", lw=1, label=f"mid: {cum['mid'].iloc[-1]:+.0f}bp")
                ax.plot(cum.index, cum["long"], color="tab:green", lw=1.4, label=f"long BE: {cum['long'].iloc[-1]:+.0f}bp")
                ax.plot(cum.index, cum["short"], color="tab:red", lw=1.4, label=f"short BE: {cum['short'].iloc[-1]:+.0f}bp")
                ax.legend(loc="upper left")
            ax.axhline(0, color="k", lw=0.5); ax.grid(alpha=0.3)
            ax.set_ylabel("cumulative net P&L (bp)")
            ax.set_title(f"{ten} breakeven net P&L  {win}  (x_TIPS={xT:.1f}, x_UST={xU:.1f}bp)")
        else:
            ax.axis("off")
            tbl, tot = engine.window_table(ten, start, end, xT, xU, freq=FREQ[st["freq"]])
            show = tbl.tail(32)
            cells = [[i] + [f"{v:+.1f}" for v in row] for i, row in zip(show.index, show.values)]
            cells.append(["TOTAL"] + [f"{v:+.1f}" for v in tot.values])
            tab = ax.table(cellText=cells, colLabels=["period"] + list(tbl.columns), loc="center", cellLoc="right")
            tab.auto_set_font_size(False); tab.set_fontsize(8); tab.scale(1, 1.2)
            for j in range(len(tbl.columns) + 1):
                tab[(len(cells), j)].set_text_props(weight="bold")
            extra = f"  (last 32 of {len(tbl)} rows)" if len(tbl) > 32 else ""
            ax.set_title(f"{ten} returns (bp)  {win}  x_TIPS={xT:.1f} x_UST={xU:.1f}{extra}", fontsize=10)
        fig.canvas.draw_idle()

    def on_export(_):
        try:
            status.set_text("exporting ...")
            fig.canvas.draw_idle()
            p = export.export_returns(xT=s_xt.val, xU=s_xu.val)
            status.set_text(f"saved {os.path.basename(p)}")
        except Exception as e:
            status.set_text(f"export failed: {e}")
        fig.canvas.draw_idle()

    s_xt.on_changed(redraw); s_xu.on_changed(redraw)
    r_t.on_clicked(lambda l: (st.update(tenor=l), redraw()))
    r_v.on_clicked(lambda l: (st.update(view=l), redraw()))
    r_f.on_clicked(lambda l: (st.update(freq=l), redraw()))
    tb_s.on_submit(lambda v: (st.update(start=_parse(v)), redraw()))
    tb_e.on_submit(lambda v: (st.update(end=_parse(v)), redraw()))
    btn.on_clicked(on_export)
    redraw()
    plt.show()


if __name__ == "__main__":
    pc = _precompute()
    if len(sys.argv) > 1:
        xT = float(sys.argv[1]); xU = float(sys.argv[2]) if len(sys.argv) > 2 else xT
        tenor = sys.argv[3] if len(sys.argv) > 3 else "10y"
        _save_png(pc, xT, xU, tenor)
    else:
        try:
            run_interactive(pc)
        except Exception as e:
            print(f"Interactive backend unavailable ({e}). Saving a static snapshot instead.")
            _save_png(pc, 3.0, 3.0, "10y")
