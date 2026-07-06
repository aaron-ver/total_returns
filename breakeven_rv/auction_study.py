"""
Auction study (plan §9) — do TIPS auctions perform better when the sector is cheap?

Signal: z_A (10y swap-space fundamental residual) and z_B (tenor-MATCHED basis z),
each measured as the mean over business days t-10..t-5 BEFORE the auction
(config.AUCTION_LAG) — before the concession builds, so we don't just measure the
concession itself.

Outcomes (LHS), per auction:
  tail_median_bp   (highYield − averageMedianYield)*100 — dispersion proxy for the
                   tail (true WI-deadline tail not publicly available; documented)
  bid_to_cover     bidToCoverRatio
  dealer_pct       primary dealer share of competitive accepted (high = weak demand)
  post_be_{1,3,5}d tenor-matched CM breakeven change after the auction, bp
                   (positive = sector richens = auction "performed")

Method (small N — plan §9/§10):
  - outcomes demeaned within tenor x reopening cell (fixed effects by demeaning);
  - bucket auctions into cheap/neutral/rich z terciles -> mean outcome per bucket
    (the monotonic table IS the result);
  - the non-linearity check: |z| > threshold interaction via a cheap-tail dummy
    regression (outcome ~ z + 1[z<-thr]);
  - A and B tested separately (prior: B, the supply/positioning lens, should matter
    more for a supply event).

Output: reports/auction_study.csv (per-auction merged data: reports/auction_panel.csv)
Usage:  python -m breakeven_rv.auction_study
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, residuals, data_auctions
from breakeven_rv.validation import nw_ols

OUT = os.path.join(config.REPORTS, "auction_study.csv")
OUT_PANEL = os.path.join(config.REPORTS, "auction_panel.csv")

OUTCOMES = ["tail_median_bp", "bid_to_cover", "dealer_pct", "post_be_1d", "post_be_3d", "post_be_5d"]


def _lagged_mean(s: pd.Series, date: pd.Timestamp, lo: int, hi: int) -> float:
    """Mean of s over business days date-lo .. date-hi (lo > hi)."""
    win = s.loc[:date - pd.Timedelta(days=1)].tail(lo)
    if len(win) < lo:
        return np.nan
    return float(win.iloc[: lo - hi + 1].mean())     # oldest (lo..hi) part of the tail window


def build_panel() -> pd.DataFrame:
    p = panel_mod.load()
    res = residuals.load()
    auc = data_auctions.load()
    auc = auc[auc["auctionDate"] >= res.dropna(subset=["z_A"]).index.min()].copy()
    lo, hi = config.AUCTION_LAG

    rows = []
    for _, a in auc.iterrows():
        d = a["auctionDate"]
        ten = a["tenor"]
        be_col = {"5y": "be5", "10y": "be10", "30y": "be30"}[ten]
        zb_col = {"5y": "z_B_5y", "10y": "z_B", "30y": "z_B_30y"}[ten]
        be = p[be_col].dropna()
        # auction-day close of the tenor CM breakeven, then +1/3/5 bd moves
        past = be.loc[:d]
        if past.empty or (d - past.index[-1]).days > 5:
            continue
        d0, v0 = past.index[-1], past.iloc[-1]
        fut = be.loc[be.index > d0]
        post = {f"post_be_{h}d": (fut.iloc[h - 1] - v0) * 100.0 if len(fut) >= h else np.nan
                for h in config.POST_HORIZONS}
        rows.append({
            "auctionDate": d, "cusip": a["cusip"], "tenor": ten,
            "is_reopening": bool(a["is_reopening"]),
            "size_bn": a["offeringAmount"] / 1e9 if pd.notna(a["offeringAmount"]) else np.nan,
            "z_A_pre": _lagged_mean(res["z_A"].dropna(), d, lo, hi),
            "z_B_pre": _lagged_mean(res[zb_col].dropna(), d, lo, hi),
            "tail_median_bp": a["tail_median_bp"], "bid_to_cover": a["bidToCoverRatio"],
            "dealer_pct": a["dealer_pct"], **post,
        })
    ap = pd.DataFrame(rows).dropna(subset=["z_A_pre", "z_B_pre"], how="all")
    # tenor x reopening fixed effects by demeaning each outcome within its cell
    cell = ap.groupby(["tenor", "is_reopening"])
    for c in OUTCOMES:
        ap[f"{c}_dm"] = ap[c] - cell[c].transform("mean")
    ap.to_csv(OUT_PANEL, index=False)
    return ap


def placebo() -> pd.DataFrame:
    """Is the post-AUCTION reversion stronger than B's everyday reversion?
    Same regression — forward 10y CM BE change (bp) on z_B — run on (a) every
    business day, (b) auction days only. If the auction beta is materially larger
    than the everyday beta, supply events genuinely concentrate the reversion
    (not just 'B reverts anyway')."""
    p = panel_mod.load()
    res = residuals.load()
    auc = data_auctions.load()
    z = res["z_B"]
    be = p["be10"]
    rows = []
    auction_days = pd.DatetimeIndex(auc["auctionDate"]).intersection(be.index)
    for h in config.POST_HORIZONS:
        fwd = (be.shift(-h) - be) * 100.0
        r_all = nw_ols(fwd, z.rename("z"), lags=h + 2)
        r_auc = nw_ols(fwd.loc[auction_days], z.loc[auction_days].rename("z"), lags=2)
        rows.append({"h": h, "beta_all_days": r_all.get("beta_z"), "t_all": r_all.get("t_z"),
                     "n_all": r_all.get("n"), "beta_auction_days": r_auc.get("beta_z"),
                     "t_auction": r_auc.get("t_z"), "n_auction": r_auc.get("n")})
    out = pd.DataFrame(rows)
    print("\nplacebo — fwd 10y BE change on z_B, all days vs auction days:")
    print(out.round(3).to_string(index=False))
    out.to_csv(os.path.join(config.REPORTS, "auction_placebo.csv"), index=False)
    return out


def run() -> pd.DataFrame:
    config.ensure_dirs()
    ap = build_panel()
    print(f"auction panel: {len(ap)} auctions "
          f"({ap['auctionDate'].min().date()} .. {ap['auctionDate'].max().date()})\n")
    rows = []
    for sig in ("z_A_pre", "z_B_pre"):
        z = ap[sig]
        buckets = pd.qcut(z, 3, labels=["cheap", "neutral", "rich"])
        for c in OUTCOMES:
            y = ap[f"{c}_dm"]
            means = y.groupby(buckets, observed=True).mean()
            counts = y.groupby(buckets, observed=True).count()
            # top-vs-bottom bucket t-test (auctions ~ independent events; small NW lag)
            cheap, rich = y[buckets == "cheap"].dropna(), y[buckets == "rich"].dropna()
            diff = cheap.mean() - rich.mean()
            se = np.sqrt(cheap.var() / len(cheap) + rich.var() / len(rich)) if len(cheap) > 5 and len(rich) > 5 else np.nan
            # linear + cheap-tail dummy (the non-linearity check)
            X = pd.DataFrame({"z": z, "cheap_tail": (z < -config.Z_THRESHOLD).astype(float)})
            r = nw_ols(y, X, lags=2)
            rows.append({
                "signal": sig, "outcome": c,
                "mean_cheap": means.get("cheap"), "mean_neutral": means.get("neutral"),
                "mean_rich": means.get("rich"),
                "n_cheap": counts.get("cheap"), "n_rich": counts.get("rich"),
                "cheap_minus_rich": diff, "t_diff": diff / se if se and se > 0 else np.nan,
                "beta_z": r.get("beta_z"), "t_z": r.get("t_z"),
                "beta_cheap_tail": r.get("beta_cheap_tail"), "t_cheap_tail": r.get("t_cheap_tail"),
                "n": r.get("n"),
            })
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(out.round(3).to_string(index=False))
    print(f"\n  wrote {OUT}")
    placebo()
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
