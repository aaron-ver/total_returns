"""
Baseline visualizer for the TIPS / breakeven data layer.

Assembles OTR-continuous series by splicing each month's on-the-run CUSIP (reference.MD
§9.2.1) from the per-bond caches, then plots real/nominal yields, breakeven, and the GC
repo -- with Treasury AUCTION dates marked as vertical lines so you can see exactly when
the reference bond rolls.

NOTE on splicing: for VISUALIZATION we splice yield/price *levels* at the month boundary
(via auctions.otr_schedule, the simple monthly convention), so a small step at each roll is
expected and visible (different CUSIP). The total-return engine (engine.py) splices RETURNS,
not levels, on its own issue-date-gated / maturity-matched roll -- a separate build; this
module is just the levels baseline.

Usage:
  python visualize.py                 # build all baseline charts into ./plots
  python visualize.py 10y             # just the 10y panel
  python visualize.py coverage        # data-coverage / health chart
Charts are saved as PNG in ./plots (headless-safe).
"""
from __future__ import annotations
import os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

import auctions

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
PLOTS = os.path.join(HERE, "plots")


def _daily(cusip):
    p = os.path.join(CACHE, "daily", f"{cusip}.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


def assemble_otr(leg, tenor, field):
    """Splice the OTR series for one field across month boundaries (§9.2.1)."""
    sched = auctions.otr_schedule()
    sub = sched[(sched.leg == leg) & (sched.tenor == tenor)].sort_values("month").reset_index(drop=True)
    pieces = []
    for i, row in sub.iterrows():
        m0 = row["month"]
        m1 = sub.loc[i + 1, "month"] if i + 1 < len(sub) else m0 + pd.DateOffset(months=1)
        d = _daily(row["cusip"])
        if d is None or field not in d:
            continue
        seg = d.loc[(d.index >= m0) & (d.index < m1), field].dropna()
        if not seg.empty:
            pieces.append(seg)
    if not pieces:
        return pd.Series(dtype=float)
    return pd.concat(pieces).sort_index()


def auction_dates(leg, tenor):
    a = auctions.load_auctions()
    s = a[(a.leg == leg) & (a.tenor == tenor)]
    return pd.to_datetime(s["auctionDate"]).dropna().sort_values()


def _mark_auctions(ax, dates, color, label):
    for i, d in enumerate(dates):
        ax.axvline(d, color=color, alpha=0.25, lw=0.8, label=label if i == 0 else None)


def gc_repo():
    m = pd.read_parquet(os.path.join(CACHE, "macro.parquet"))
    return m["gcf_treasury"].dropna()


def panel(tenor, start=None):
    """3-row panel for one tenor: yields, breakeven, GC repo, with auction markers.
    start=None shows the FULL available history."""
    os.makedirs(PLOTS, exist_ok=True)
    start = start or "1990-01-01"   # effectively full history
    ry = assemble_otr("tips", tenor, "YLD_YTM_MID")
    ny = assemble_otr("nominal", tenor, "YLD_YTM_MID")
    be = (ny - ry).dropna()
    repo = gc_repo()
    ta = auction_dates("tips", tenor)
    na = auction_dates("nominal", tenor)

    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
    fig.suptitle(f"On-the-run {tenor}: real vs nominal yield, breakeven, GC repo "
                 f"(auctions marked)", fontsize=12)

    ax = axes[0]
    if not ry.empty: ax.plot(ry.index, ry.values, color="tab:blue", lw=1, label=f"TIPS real yield ({tenor})")
    if not ny.empty: ax.plot(ny.index, ny.values, color="tab:red", lw=1, label=f"UST nominal yield ({tenor})")
    _mark_auctions(ax, ta[ta >= start], "tab:blue", "TIPS auction")
    _mark_auctions(ax, na[na >= start], "tab:red", "UST auction")
    ax.set_ylabel("yield (%)"); ax.legend(loc="upper left", fontsize=8); ax.grid(alpha=0.3)

    ax = axes[1]
    if not be.empty: ax.plot(be.index, be.values, color="tab:green", lw=1, label=f"breakeven (NY-RY, {tenor})")
    _mark_auctions(ax, ta[ta >= start], "tab:blue", None)
    ax.set_ylabel("breakeven (%)"); ax.legend(loc="upper left", fontsize=8); ax.grid(alpha=0.3)

    ax = axes[2]
    if not repo.empty: ax.plot(repo.index, repo.values, color="tab:purple", lw=1, label="GC repo (GCFRTSY)")
    ax.set_ylabel("repo (%)"); ax.legend(loc="upper left", fontsize=8); ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(PLOTS, f"panel_{tenor}.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"  wrote {out}  (real n={len(ry)}, nom n={len(ny)}, be n={len(be)})")
    return out


def coverage():
    """Data-coverage chart: count of bonds with data over time, per leg/tenor."""
    os.makedirs(PLOTS, exist_ok=True)
    sched = auctions.otr_schedule()
    fig, ax = plt.subplots(figsize=(13, 5))
    for leg in ("tips", "nominal"):
        for tenor in ("5y", "10y", "30y"):
            s = assemble_otr(leg, tenor, "YLD_YTM_MID")
            if not s.empty:
                ax.plot(s.index, [f"{leg} {tenor}"] * len(s), "|", ms=4,
                        label=f"{leg} {tenor} (n={len(s)})")
    ax.set_title("OTR series data coverage (each tick = a day with data)")
    ax.legend(loc="center left", bbox_to_anchor=(1, 0.5), fontsize=8)
    fig.tight_layout()
    out = os.path.join(PLOTS, "coverage.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"  wrote {out}")
    return out


def returns_panel():
    """Cumulative financed returns (bp, linear-sum) from engine.py: breakeven by tenor +
    per-leg breakdown."""
    os.makedirs(PLOTS, exist_ok=True)
    rets = {}
    for ten in ("5y", "10y", "30y"):
        p = os.path.join(CACHE, f"returns_{ten}.parquet")
        if os.path.exists(p):
            rets[ten] = pd.read_parquet(p)
    if not rets:
        print("  no returns_*.parquet — run: python engine.py")
        return
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    fig.suptitle("Financed breakeven total return (DV01-normalized, 100k/leg, linear-sum bp)",
                 fontsize=12)
    for ten, df in rets.items():
        axes[0].plot(df.index, df["cum_BE_bp"], lw=1.1, label=f"{ten} breakeven")
    axes[0].set_ylabel("cum breakeven return (bp)"); axes[0].legend(loc="upper left", fontsize=8)
    axes[0].grid(alpha=0.3); axes[0].axhline(0, color="k", lw=0.5)
    d10 = rets.get("10y")
    if d10 is not None:
        axes[1].plot(d10.index, d10["cum_TIPS_bp"], color="tab:blue", lw=1.1, label="10y TIPS leg")
        axes[1].plot(d10.index, d10["cum_UST_bp"], color="tab:red", lw=1.1, label="10y UST leg")
        axes[1].plot(d10.index, d10["cum_BE_bp"], color="tab:green", lw=1.1, label="10y breakeven (diff)")
    axes[1].set_ylabel("cum leg return (bp)"); axes[1].legend(loc="upper left", fontsize=8)
    axes[1].grid(alpha=0.3); axes[1].axhline(0, color="k", lw=0.5)
    axes[1].xaxis.set_major_locator(mdates.YearLocator()); axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(PLOTS, "returns.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"  wrote {out}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg == "coverage":
        coverage()
    elif arg == "returns":
        returns_panel()
    elif arg in ("5y", "10y", "30y"):
        panel(arg)
    else:
        for t in ("5y", "10y", "30y"):
            panel(t)
        coverage()
        returns_panel()
    print("Done. Open the PNGs in ./plots")
