"""
Boss-ready PDF reports for the experiments — one PDF per experiment, methodology + visuals +
results tables. Reads the experiments/out CSVs (run the experiments first) and rebuilds the
handful of series the charts need. Output: experiments/out/*.pdf.

Run:  .venv/Scripts/python.exe experiments/report_pdf.py
"""
from __future__ import annotations
import os, re, textwrap
import exp_common as C
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

# ---------------------------------------------------------------- style (validated ref palette)
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"
BLUE, AQUA, RED = "#2a78d6", "#1baf7a", "#e34948"          # categorical slots 1,2 / diverging pole
SEQ = ["#86b6ef", "#2a78d6", "#104281"]                     # ordinal blue steps 250/450/650
DIV_MID = "#f0efec"
PAGE = (8.5, 11)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "text.color": INK,
    "axes.edgecolor": BASE, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
})


_PNG_DIR = os.environ.get("EXP_PNG_DIR")            # set to also mirror every page as PNG (QA)
_PNG_N = [0]


def _save(pdf, fig):
    pdf.savefig(fig)
    if _PNG_DIR:
        _PNG_N[0] += 1
        fig.savefig(os.path.join(_PNG_DIR, f"page_{_PNG_N[0]:02d}.png"), dpi=110)
    plt.close(fig)


def _wrap(s, width=104):
    s = s.replace("$", r"\$")            # stop matplotlib mathtext from eating $...$ spans
    out = []
    for para in s.strip().split("\n"):
        out.append(textwrap.fill(para.strip(), width) if para.strip() else "")
    return "\n".join(out)


def text_page(pdf, title, subtitle, sections, callout=None):
    """A clean typographic page: title, subtitle, (heading, body) sections, optional stat callout."""
    fig = plt.figure(figsize=PAGE)
    y = 0.94
    fig.text(0.08, y, title, fontsize=19, fontweight="bold", color=INK); y -= 0.022
    fig.text(0.08, y, subtitle, fontsize=10.5, color=INK2); y -= 0.02
    fig.add_artist(plt.Line2D([0.08, 0.92], [y, y], color=BASE, lw=1)); y -= 0.028
    if callout:
        head, stats = callout
        box = FancyBboxPatch((0.08, y - 0.115), 0.84, 0.115, boxstyle="round,pad=0.012",
                             facecolor="#f9f9f7", edgecolor=BASE, lw=1, transform=fig.transFigure)
        fig.add_artist(box)
        fig.text(0.10, y - 0.026, head, fontsize=9, color=MUTED, fontweight="bold")
        n = len(stats)
        for k, (big, small) in enumerate(stats):
            x = 0.10 + k * (0.80 / n)
            fig.text(x, y - 0.072, big, fontsize=14, fontweight="bold", color=INK)
            fig.text(x, y - 0.098, small, fontsize=7.6, color=INK2)
        y -= 0.15
    for head, body in sections:
        fig.text(0.08, y, head, fontsize=11, fontweight="bold", color=INK); y -= 0.019
        body_w = _wrap(body)
        fig.text(0.08, y, body_w, fontsize=9, color=INK2, va="top", linespacing=1.55)
        y -= 0.0178 * (body_w.count("\n") + 1) + 0.028
    _save(pdf, fig)


def table_page(pdf, df, title, note=None, rename=None, max_rows=26):
    """Minimal table: bold header, hairline separators, right-aligned numbers, NaN as em-dash."""
    df = df.head(max_rows)
    if rename:
        df = df.rename(columns=rename)
    fig = plt.figure(figsize=PAGE)
    fig.text(0.08, 0.94, title, fontsize=14, fontweight="bold", color=INK)
    top = 0.905
    if note:
        note_w = _wrap(note, 118)
        fig.text(0.08, 0.918, note_w, fontsize=8, color=INK2, va="top", linespacing=1.5)
        top = 0.918 - 0.0125 * (note_w.count("\n") + 1) - 0.018
    cols = list(df.columns)
    x0, x1 = 0.08, 0.92
    widths = np.array([max(len(str(c)), df[c].astype(str).str.len().max()) for c in cols],
                      float) + 2.5                                 # +padding between columns
    xs = x0 + np.concatenate([[0], np.cumsum(widths / widths.sum())]) * (x1 - x0)
    rh = min(0.0205, (top - 0.055) / (len(df) + 1))    # shrink rows so long tables fit the page
    for j, c in enumerate(cols):
        num = pd.api.types.is_numeric_dtype(df[c])
        fig.text(xs[j + 1] - 0.006 if num else xs[j] + 0.002, top, str(c), fontsize=7.6,
                 fontweight="bold", color=INK, ha="right" if num else "left")
    fig.add_artist(plt.Line2D([x0, x1], [top - 0.006, top - 0.006], color=BASE, lw=1))
    for i, (_, row) in enumerate(df.iterrows()):
        yy = top - 0.012 - (i + 1) * rh
        if i % 2 == 0:
            fig.add_artist(plt.Rectangle((x0, yy - 0.004), x1 - x0, rh - 0.002,
                                         facecolor="#f4f4f1", edgecolor="none",
                                         transform=fig.transFigure, zorder=0))
        for j, c in enumerate(cols):
            v = row[c]
            num = pd.api.types.is_numeric_dtype(df[c])
            if isinstance(v, float) and not np.isfinite(v):
                txt = "—"
            else:
                txt = f"{v:,.2f}" if isinstance(v, float) else str(v)
            fig.text(xs[j + 1] - 0.006 if num else xs[j] + 0.002, yy, txt, fontsize=7.4,
                     color=INK2, ha="right" if num else "left")
    _save(pdf, fig)


def _chart_fig(nrows, height_ratios=None, suptitle=None):
    fig, axes = plt.subplots(nrows, 1, figsize=PAGE,
                             gridspec_kw=dict(top=0.90, bottom=0.07, left=0.10, right=0.94,
                                              hspace=0.42, height_ratios=height_ratios))
    if suptitle:
        fig.suptitle(suptitle, x=0.10, y=0.955, ha="left", fontsize=14, fontweight="bold")
    return fig, (axes if nrows > 1 else [axes])


# ================================================================= Exp 1 — extension
def build_extension_pdf():
    import exp_extension as E
    res = pd.read_csv(f"{C.OUT}/extension_results.csv")
    ext = pd.read_csv(f"{C.OUT}/extension_series_US.csv", index_col=0)
    ext.index = pd.PeriodIndex(ext.index, freq="M")

    path = f"{C.OUT}/report_exp1_extension_monthend.pdf"
    with PdfPages(path) as pdf:
        text_page(
            pdf, "Index extension and the month-end bid",
            "Experiment 1 — can the issuance calendar tell us which month-ends index flows will move?",
            [
                ("The idea",
                 "Bond indices rebalance on the last business day of each month: bonds issued during the "
                 "month enter, and bonds with under one year to maturity drop out. Both usually push the "
                 "index's duration UP ('the extension'), and index-tracking money must buy that duration "
                 "at the rebalance. The size of the extension is knowable IN ADVANCE from the auction "
                 "calendar — so the test is not 'is there a month-end effect' but 'is the month-end move "
                 "proportional to that month's extension'."),
                ("Setup",
                 "One observation per calendar month. X = extension(m): the jump in the index's "
                 "amount-weighted average duration (years) at the month-end rebalance — membership = "
                 "issued and >=1y to maturity; duration proxied by time-to-maturity; US weights = "
                 "cumulative auction sizes (reopenings enter as weight increases), intl = constant "
                 "current outstanding. Both old and new books are measured on the same day, so pure "
                 "aging cancels.  Y = the return series' summed bp over the LAST 5 trading days of the "
                 "month (and, separately, the FIRST 5 of the next month for reversal). OLS per series: "
                 "US 5y/10y/30y x TIPS/BE, and every intl CMT bucket x linker/BE. Returns are the "
                 "engines' DV01-normalized financed bp (BE = linker - nominal at beta 1)."),
                ("What we found",
                 "All six US series show the flow signature: bigger extension, stronger month-end week "
                 "(12-21 bp per year of extension, t = 3.2-3.5), with a partial, statistically weak "
                 "give-back the following week. France OATi echoes it (t = 2.7-3.2). 11 of 68 series "
                 "clear |t|>=2, concentrated in the US and FR OATi rather than scattered at random. "
                 "Caveats: TTM duration proxy; market-wide (not bucket-matched) extension; the US "
                 "extension calendar peaks in April (new 5y) with July secondary — see the profile "
                 "chart before trading a specific month."),
            ],
            callout=("KEY RESULT", [("t = 3.2-3.5", "all six US series, ME week"),
                                    ("+12-21 bp/yr", "return per year of extension"),
                                    ("186", "months in US sample"),
                                    ("~40% fade", "next week (weak t)")]))

        # -- page 2: the extension series + its seasonal profile
        fig, axes = _chart_fig(2, suptitle="The conditioning variable: US TIPS index extension")
        ax = axes[0]
        ext_s = ext[ext.index >= "2010-01"]                    # the regression sample (returns era);
        colors = [BLUE if v >= 0 else RED for v in ext_s["ext_y"]]   # pre-2004 mini-index distorts scale
        ax.bar(ext_s.index.to_timestamp(), ext_s["ext_y"], width=22, color=colors, edgecolor=SURF,
               lw=0.3)
        ax.axhline(0, color=BASE, lw=1)
        ax.set_title("Monthly index duration extension, regression sample 2010→ — blue = extension, "
                     "red = contraction", fontsize=9.5, loc="left", color=INK2)
        ax.set_ylabel("years")
        ax = axes[1]
        prof = ext.groupby(ext.index.month)["ext_y"].mean()
        ax.bar(prof.index, prof.values, color=BLUE, width=0.62, edgecolor=SURF, lw=0.5)
        ax.axhline(0, color=BASE, lw=1)
        ax.set_xticks(range(1, 13),
                      ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        ax.set_title("Average extension by calendar month — the April new-5y peak; Oct/Dec contract",
                     fontsize=9.5, loc="left", color=INK2)
        ax.set_ylabel("years")
        _save(pdf, fig)

        # -- page 3: the evidence — scatter + terciles for US_10y BE
        w = C.month_windows(C.us_returns("10y")["r_BE_bp"]).join(ext["ext_y"], how="inner").dropna(
            subset=["ext_y"])
        o = C.ols(w["ext_y"], w["me"])
        fig, axes = _chart_fig(2, suptitle="The evidence — US 10y breakeven bucket")
        ax = axes[0]
        ax.scatter(w["ext_y"], w["me"], s=26, color=BLUE, alpha=0.55, edgecolor=SURF, lw=0.5)
        xs = np.linspace(w["ext_y"].min(), w["ext_y"].max(), 50)
        ax.plot(xs, o["intercept"] + o["slope"] * xs, color=INK, lw=2)
        ax.axhline(0, color=BASE, lw=0.8); ax.axvline(0, color=BASE, lw=0.8)
        ax.set_xlabel("index extension (years)"); ax.set_ylabel("last-5-day BE return (bp)")
        ax.set_title(f"Month-end week vs that month's extension — slope {o['slope']:.1f} bp/yr,"
                     f"  t = {o['tstat']:.1f},  R² = {o['r2']:.2f},  n = {o['n']}",
                     fontsize=9.5, loc="left", color=INK2)
        ax = axes[1]
        ter = pd.qcut(w["ext_y"], 3, labels=False, duplicates="drop")
        m = w.groupby(ter)["me"].agg(["mean", "sem", "size"])
        labels = ["low extension", "mid", "high extension"][:len(m)]
        ax.bar(range(len(m)), m["mean"], yerr=1.96 * m["sem"], capsize=4,
               color=SEQ[:len(m)], width=0.5, edgecolor=SURF, lw=0.5,
               error_kw=dict(ecolor=MUTED, lw=1))
        ax.axhline(0, color=BASE, lw=1)
        ax.set_xticks(range(len(m)), [f"{l}\n(n={int(s)})" for l, s in zip(labels, m["size"])])
        for k, v in enumerate(m["mean"]):
            ax.annotate(f"{v:+.1f} bp", (k, v), textcoords="offset points",
                        xytext=(28, 2), fontsize=9, color=INK)
        ax.set_ylabel("mean ME-week BE return (bp)")
        ax.set_title("Same data, no regression: month-end week by extension tercile (95% CI)",
                     fontsize=9.5, loc="left", color=INK2)
        _save(pdf, fig)

        # -- page 4: results table
        sig = res[np.abs(res["t_me"]) >= 2].sort_values("t_me", key=np.abs, ascending=False)
        us = res[res["series"].str.startswith("US")]
        show = pd.concat([sig, us[~us.index.isin(sig.index)]]).drop_duplicates()
        table_page(pdf, show, "Results — all |t|>=2 series plus every US series",
                   note="β ME = bp of month-end-week return per year of index extension; β rev = same "
                        "for the first week of the next month. lo/hi = mean ME-week bp in the lowest/"
                        "highest extension tercile (— where the extension series is too coarse to cut). "
                        "68 series tested in total; 11 clear |t|>=2, concentrated in US and FR OATi.",
                   rename={"n_months": "n mo", "beta_me_bp_per_y": "β ME bp/y", "t_me": "t ME",
                           "r2_me": "R² ME", "beta_rev_bp_per_y": "β rev bp/y", "t_rev": "t rev",
                           "me_loExt_bp": "ME lo terc", "me_hiExt_bp": "ME hi terc"})
    print("wrote", path)


# ================================================================= Exp 2 — factor controls
def build_factors_pdf():
    us = pd.read_csv(f"{C.OUT}/factor_hedge_us.csv")
    intl = pd.read_csv(f"{C.OUT}/factor_hedge_intl.csv")
    intl = intl[intl["n"] >= 450].copy()                       # drop thin junk cells (keeps all 2y)
    def _t(s, k):
        m = re.search(rf"{k}:([+-][\d.]+)", s)
        return float(m.group(1)) if m else np.nan
    intl["ttf_t"] = intl["ctl_t"].apply(lambda s: _t(s, "ttf"))
    intl["mkt"] = intl["series"].str.rsplit("_", n=1).str[0]
    intl["bucket"] = intl["series"].str.rsplit("_", n=1).str[1]

    path = f"{C.OUT}/report_exp2_energy_factor_controls.pdf"
    with PdfPages(path) as pdf:
        text_page(
            pdf, "Is the crude hedge stealing credit?",
            "Experiment 2 — the energy hedge beta re-estimated with curve, dollar, VIX and gas controls",
            [
                ("The idea",
                 "Our dashboard hedges breakevens with a single factor: crude (gasoline in the US, Brent "
                 "intl). But crude co-moves with the curve slope, the dollar, VIX and natural gas — and "
                 "those ALSO move breakevens. A single-factor regression hands crude the credit for all "
                 "of it (classic omitted-variable bias), so the hedge ratio can be too big. The Barclays "
                 "guide's own 10y BE model is slope + RBOB + VIX + dollar."),
                ("Setup",
                 "Identical plumbing to production (hedge.py / energy_intl.py): each leg's daily $ P&L "
                 "summed into the intervals between energy closes; be$ = linker$ - nominal$ at beta 1. "
                 "Univariate: be$ ~ crude$ (the production hedge). Controlled: be$ ~ crude$ + d(3m10y "
                 "slope) + d(log broad dollar) + d(VIX) [+ d(TTF gas) intl], all differenced over the "
                 "same intervals. Also tested: dollar-DEFLATED crude as the single factor. Factors "
                 "pulled once into experiments/cache (USGG10YR, USGG3M, BBDXY, VIX, TZT1, UKBRBASE)."),
                ("What we found",
                 "(1) The crude-only beta IS overstated: controls shrink it 18/29/40% for US 5y/10y/30y "
                 "and 5-15% across intl (UK 2y -32%). (2) What was stealing the credit: in the US, curve "
                 "slope (t +18 to +25) and VIX (t -13 to -15); in euro/UK markets TTF GAS dominates "
                 "(t up to +13) — 2022 euro inflation was a gas shock, not an oil shock. (3) Model R² "
                 "roughly doubles with controls (US 10y 0.12 -> 0.30). (4) Dollar-deflating crude does "
                 "NOT help at daily frequency — tested, rejected. Practical read: the beta_ctl column is "
                 "the pure-crude hedge; front-end books arguably want a gas leg, not more Brent."),
            ],
            callout=("KEY RESULT", [("-18/-29/-40%", "US 5y/10y/30y β shift"),
                                    ("TTF t ≈ +13", "gas drives euro/UK BEs"),
                                    ("0.12 → 0.30", "US 10y R² with controls"),
                                    ("no gain", "dollar-deflated crude")]))

        # -- page 2: US uni vs controlled
        fig, axes = _chart_fig(2, suptitle="US: gasoline hedge beta and fit, before vs after controls")
        ax = axes[0]
        x = np.arange(len(us))
        ax.bar(x - 0.17, us["beta_uni"], 0.3, label="single-factor (production)", color=BLUE,
               edgecolor=SURF, lw=0.5)
        ax.bar(x + 0.17, us["beta_ctl"], 0.3, label="with slope/dollar/VIX controls", color=AQUA,
               edgecolor=SURF, lw=0.5)
        for k in x:
            ax.annotate(f"{us['beta_shift_pct'][k]:+.0f}%", (k + 0.17, us["beta_ctl"][k]),
                        textcoords="offset points", xytext=(0, 4), ha="center", fontsize=9, color=INK)
        ax.set_xticks(x, us["series"]); ax.legend(frameon=False, fontsize=8.5)
        ax.set_ylabel("crude beta (contracts per 100k DV01)")
        ax.set_title("Hedge ratio: the controlled beta is the pure-crude exposure", fontsize=9.5,
                     loc="left", color=INK2)
        ax = axes[1]
        ax.bar(x - 0.17, us["r2_uni"], 0.3, label="crude only", color=BLUE, edgecolor=SURF, lw=0.5)
        ax.bar(x + 0.17, us["r2_ctl"], 0.3, label="+ slope, dollar, VIX", color=AQUA,
               edgecolor=SURF, lw=0.5)
        ax.set_xticks(x, us["series"]); ax.legend(frameon=False, fontsize=8.5)
        ax.set_ylabel("R²")
        ax.set_title("Fit: share of BE variance explained roughly doubles", fontsize=9.5,
                     loc="left", color=INK2)
        _save(pdf, fig)

        # -- page 3: rolling stability, US 10y
        roll = pd.read_csv(f"{C.OUT}/factor_rolling_US_10y.csv", index_col=0, parse_dates=True)
        fig, axes = _chart_fig(1, suptitle="Stability: rolling 2y crude beta, US 10y")
        ax = axes[0]
        ax.plot(roll.index, roll["beta_uni"], color=BLUE, lw=2, label="single-factor")
        ax.plot(roll.index, roll["beta_ctl"], color=AQUA, lw=2, label="with controls")
        ax.legend(frameon=False, fontsize=9)
        ax.set_ylabel("crude beta (contracts)")
        ax.set_title("If the gap widens when slope/dollar/VIX are volatile (2020-22), the single-factor "
                     "beta was absorbing them", fontsize=9.5, loc="left", color=INK2)
        _save(pdf, fig)

        # -- page 4: intl — where the overstatement is, and the gas factor
        fig, axes = _chart_fig(2, suptitle="Intl: Brent hedge with dollar/VIX/TTF controls")
        ax = axes[0]
        front = intl[intl["bucket"] == "2y"].set_index("mkt")["beta_shift_pct"]
        order = front.sort_values().index
        ax.bar(range(len(front)), front[order], color=BLUE, width=0.55, edgecolor=SURF, lw=0.5)
        ax.axhline(0, color=BASE, lw=1)
        ax.set_xticks(range(len(front)), order)
        ax.set_ylabel("crude beta shift with controls (%)")
        ax.set_title("Front end (2y buckets): how much of the 'crude' hedge wasn't crude", fontsize=9.5,
                     loc="left", color=INK2)
        ax = axes[1]
        g = intl.groupby("mkt")["ttf_t"].median().sort_values(ascending=False)
        ax.bar(range(len(g)), g.values, color=BLUE, width=0.55, edgecolor=SURF, lw=0.5)
        ax.axhline(2, color=MUTED, lw=1, ls="--")
        ax.annotate("|t| = 2", (len(g) - 0.6, 2.1), fontsize=8, color=MUTED)
        ax.set_xticks(range(len(g)), g.index)
        ax.set_ylabel("median TTF gas t-stat across buckets")
        ax.set_title("The missing factor: TTF gas is significant everywhere in Europe/UK", fontsize=9.5,
                     loc="left", color=INK2)
        _save(pdf, fig)

        # -- page 5-6: tables
        REN = {"beta_uni": "β uni", "r2_uni": "R² uni", "beta_ctl": "β ctl", "t_ctl": "t ctl",
               "r2_ctl": "R² ctl", "beta_shift_pct": "Δβ %", "r2_usdadj": "R² usd-adj",
               "ctl_t": "control t-stats", "ttf_t": "TTF t"}
        table_page(pdf, us, "US results",
                   note="β uni / R² uni = production single-factor regression; β ctl / t ctl / R² ctl = "
                        "crude beta with slope+dollar+VIX controls; Δβ % = how far the crude beta "
                        "moved; R² usd-adj = fit with dollar-deflated crude (≈ R² uni everywhere = no "
                        "improvement); control t-stats are per-factor.", rename=REN)
        show = intl.sort_values("beta_shift_pct")[
            ["series", "n", "beta_uni", "r2_uni", "beta_ctl", "t_ctl", "r2_ctl",
             "beta_shift_pct", "ttf_t"]]
        table_page(pdf, show, "Intl results (cells with n >= 450), sorted by beta shift",
                   note="Same columns; TTF t = t-stat of the TTF gas control. FR_OATI_20y excluded "
                        "upstream (n=269, t ctl=1.4 — too thin to read).", rename=REN, max_rows=42)
    print("wrote", path)


# ================================================================= Exp 3 — maturity RV
def build_rv_pdf():
    cells = pd.read_csv(f"{C.OUT}/maturity_rv_cells.csv")

    path = f"{C.OUT}/report_exp3_maturity_rv.pdf"
    with PdfPages(path) as pdf:
        text_page(
            pdf, "Maturity-month seasonality: a null result",
            "Experiment 3 — do bonds maturing in different calendar months earn different seasonal returns?",
            [
                ("The idea",
                 "Inflation accretion is seasonal (strong spring, weak autumn), so a linker maturing in "
                 "July spends its final year capturing the strong half's accrual while a January bond "
                 "doesn't. The guide argues maturity-month cohorts should therefore trade systematically "
                 "rich/cheap. If the market misprices that, cohorts should show repeatable "
                 "calendar-month return differences vs their market."),
                ("Setup",
                 "Per bond: monthly sum of the engine's financed bp. Cross-sectionally demeaned within "
                 "each market-month (bond minus market average — kills all market-wide moves). Cohort = "
                 "the bond's maturity MONTH. For each (market, cohort, calendar month) cell with >=2 "
                 "bonds and >=5 years of history: mean relative bp and a t-stat across years. "
                 "96 cells tested across FR/IT/ES/UK."),
                ("What we found — honestly, nothing",
                 "6 of 96 cells clear |t|>=2 — almost exactly the 5% you'd expect from pure chance, and "
                 "the t-stat histogram sits on the standard normal. The specific July-cohort H1-accrual "
                 "prediction shows nothing. Only a faint UK cluster (Aug-maturity gilts in Jan/Jul) — "
                 "plausible, RPI has the strongest seasonality — but thin. Realized monthly returns are "
                 "probably the wrong lens for what is a PRICING claim: the sharper test is a fitted "
                 "seasonality-and-floor-adjusted real curve with rich/cheap residual mean-reversion "
                 "(queued as a follow-up). Knowing the naive version doesn't work is itself useful."),
            ],
            callout=("KEY RESULT", [("6 / 96", "cells with |t|>=2"),
                                    ("≈ 5%", "exactly the chance rate"),
                                    ("H1 ≈ H2", "July-cohort claim: nothing"),
                                    ("UK only", "faint Aug-cohort cluster")]))

        # -- page 2: t histogram vs N(0,1) + UK heatmap
        fig, axes = _chart_fig(2, suptitle="Why we call it a null")
        ax = axes[0]
        ax.hist(cells["t"], bins=24, density=True, color=BLUE, edgecolor=SURF, lw=0.5)
        xs = np.linspace(-4, 4, 200)
        ax.plot(xs, np.exp(-xs**2 / 2) / np.sqrt(2 * np.pi), color=INK, lw=2)
        ax.axvline(-2, color=MUTED, lw=1, ls="--"); ax.axvline(2, color=MUTED, lw=1, ls="--")
        ax.set_xlabel("cell t-stat"); ax.set_ylabel("density")
        ax.set_title("All 96 cell t-stats vs the standard normal (black) — the no-effect shape",
                     fontsize=9.5, loc="left", color=INK2)
        ax = axes[1]
        uk = cells[cells["market"] == "UK_3M"].pivot(index="cohort_matmonth", columns="cal_month",
                                                     values="t")
        uk = uk.reindex(index=sorted(uk.index), columns=range(1, 13))
        im = ax.imshow(uk.values, cmap=matplotlib.colors.LinearSegmentedColormap.from_list(
            "div", [BLUE, DIV_MID, RED]), vmin=-3, vmax=3, aspect="auto")
        ax.set_xticks(range(12), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                  "Oct", "Nov", "Dec"])
        ax.set_yticks(range(len(uk.index)), [f"mat {int(m):02d}" for m in uk.index])
        ax.grid(False)
        for i in range(uk.shape[0]):
            for j in range(12):
                v = uk.values[i, j]
                if np.isfinite(v) and abs(v) >= 2:
                    ax.text(j, i, f"{v:+.1f}", ha="center", va="center", fontsize=7.5, color=INK)
        cb = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
        cb.set_label("t-stat (red = cohort beats market)", fontsize=8, color=INK2)
        cb.outline.set_edgecolor(BASE)
        ax.set_title("UK (the only cluster): cohort maturity-month x calendar month, t-stats — "
                     "|t|>=2 cells annotated", fontsize=9.5, loc="left", color=INK2)
        _save(pdf, fig)

        # -- page 3: the significant cells
        top = cells[np.abs(cells["t"]) >= 2].sort_values("t", key=np.abs, ascending=False)
        table_page(pdf, top, "The six cells that cleared |t|>=2",
                   note="rel bp = cohort's mean monthly bp vs its market in that calendar month; "
                        "t across years. At 96 cells tested, ~5 would clear by chance — treat these as "
                        "candidates at best, the UK Aug-cohort pair being the only coherent story.",
                   rename={"cohort_matmonth": "maturity mo", "cal_month": "cal mo",
                           "mean_rel_bp": "rel bp", "n_years": "n yrs"})
    print("wrote", path)


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    build_extension_pdf()
    build_factors_pdf()
    build_rv_pdf()
