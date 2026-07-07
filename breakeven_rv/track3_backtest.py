"""
Track 3 (v2 spec) — the backtest: the decider between fit-reversion and PnL.

Trades the FINANCED breakeven total-return streams already built by the repo's
engine (cache/returns_{5y,10y,30y}.parquet, r_BE_bp = daily DV01-normalized financed
BE return in bp) — so carry, financing and the roll are all in the PnL.

Rules implemented EXACTLY as spec'd (no discretionary additions):

A-strategy (10y):
  enter  |z_A| >= thr, thr in config.T3_ENTRY_GRID (ALL three reported, none picked)
  size   pos = -z_A / T3_SIZE_CAP_Z, clipped to [-1, +1]  (saturates at 2 sigma)
  exit   |z_A| < EP_EXIT_Z, OR time stop at EP_HL_MULT x rolling half-life at entry,
         OR quadrant flips to "disagree" (B moving against — the orthogonal cut).
         No price stops. Position fixed from entry (no daily rescaling).
  Entries are not taken while the state is ALREADY "disagree" (the cut rule would
  fire instantly; "flips to" presumes it wasn't). Documented.

B-strategy (all tenors): auction windows ONLY.
  enter close t-5 before each auction, pos = -z_B_pre / T3_SIZE_CAP_Z clipped [-1,1]
  exit close t+1 (primary) / t+3 (variant). Nothing outside auction windows.

Costs: round-trip haircut in bp of BE yield (grid config.T3_COSTS_BP — report all
levels, never one optimistic number). Half applied on entry, half on exit.

Output: reports/track3_summary.csv, track3_subperiods.csv, track3_trades_A.csv,
track3_trades_B.csv + printed worst-5 narration and the fit-reversion reconciliation.
Usage:  python -m breakeven_rv.track3_backtest
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, residuals, data_auctions
from breakeven_rv.validation import rolling_half_life, perf_stats

OUT_SUMMARY = os.path.join(config.REPORTS, "track3_summary.csv")
OUT_SUB = os.path.join(config.REPORTS, "track3_subperiods.csv")

REGIMES = {
    "2011_2014": ("2011-01-01", "2014-12-31"),
    "2015_covid": ("2015-01-01", "2020-02-28"),
    "covid": ("2020-03-01", "2020-12-31"),
    "2021_22_spike": ("2021-01-01", "2022-12-31"),
    "2023_present": ("2023-01-01", "2099-01-01"),
}


def _returns(tenor: str) -> pd.Series:
    r = pd.read_parquet(os.path.join(config.ROOT_CACHE, f"returns_{tenor}.parquet"))
    return r["r_BE_bp"].dropna()


def backtest_A(thr: float, cost_bp: float, quad_override: pd.Series | None = None,
               confirm_gate: bool = False):
    """Returns (daily_pnl, trades). PnL accrues from the close after entry through the
    exit close; position size is fixed at entry.
    quad_override: alternative quadrant state series (v3: bond-B quadrants).
    confirm_gate: only enter when the state is both_cheap/both_rich (v3 live rule —
    v2 showed neutral entries are dead)."""
    res = residuals.load()
    r = _returns("10y")
    idx = r.index
    z = res["z_A"].reindex(idx).ffill(limit=2)
    quad = (quad_override if quad_override is not None else res["quadrant"]) \
        .reindex(idx).ffill(limit=2)
    hl = rolling_half_life(res["resid_A_bp"], config.HL_WINDOW, config.HL_CLIP) \
        .reindex(idx).ffill(limit=2)

    pnl = pd.Series(0.0, index=idx)
    trades = []
    pos, e, stop = 0.0, None, None
    for i, t in enumerate(idx):
        if pos != 0.0:
            pnl.loc[t] += pos * r.loc[t]
            exit_reason = None
            if abs(z.loc[t]) < config.EP_EXIT_Z:
                exit_reason = "converged"
            elif i - e >= stop:
                exit_reason = "time_stop"
            elif quad.loc[t] == "disagree":
                exit_reason = "quadrant_cut"
            if exit_reason or i == len(idx) - 1:
                pnl.loc[t] -= cost_bp / 2 * abs(pos)
                gross = pos * r.iloc[e + 1: i + 1].sum()
                trades.append({"entry": idx[e], "exit": t, "days": i - e,
                               "pos": pos, "z_entry": z.iloc[e],
                               "quadrant_entry": quad.iloc[e],
                               "exit_reason": exit_reason or "sample_end",
                               "pnl_gross_bp": gross,
                               "pnl_net_bp": gross - cost_bp * abs(pos)})
                pos = 0.0
        elif np.isfinite(z.loc[t]) and abs(z.loc[t]) >= thr and np.isfinite(hl.loc[t]) \
                and quad.loc[t] != "disagree" \
                and (not confirm_gate or quad.loc[t] in ("both_cheap", "both_rich")):
            pos = float(np.clip(-z.loc[t] / config.T3_SIZE_CAP_Z, -1.0, 1.0))
            e, stop = i, int(round(config.EP_HL_MULT * hl.loc[t]))
            pnl.loc[t] -= cost_bp / 2 * abs(pos)
    return pnl, pd.DataFrame(trades)


def backtest_B(exit_h: int, cost_bp: float, entry_off: int | None = None):
    """Auction-window-only strategy, all tenors, positions per auction (they can stack).
    entry_off = business days BEFORE the auction to enter (default config.T3_B_ENTRY = 5,
    the spec'd rule). entry_off=0 (enter at the auction-day close) is a DIAGNOSTIC
    deviation: the spec'd t-5 entry holds through the concession build, which the
    auction study's outcome window (t0 -> t+h) deliberately excluded — the leg
    attribution printed by run() shows how much the concession leg costs."""
    res = residuals.load()
    auc = data_auctions.load()
    zcol = {"5y": "z_B_5y", "10y": "z_B", "30y": "z_B_30y"}
    rets = {t: _returns(t) for t in ("5y", "10y", "30y")}
    pnl = pd.Series(0.0, index=rets["10y"].index.union(rets["5y"].index).union(rets["30y"].index))
    trades = []
    lo, hi = config.AUCTION_LAG
    for _, a in auc.iterrows():
        ten = a["tenor"]
        r = rets[ten]
        z = res[zcol[ten]].dropna()
        off = config.T3_B_ENTRY if entry_off is None else entry_off
        d0pos = r.index.searchsorted(a["auctionDate"], side="right") - 1
        e = d0pos - off
        x = d0pos + exit_h
        if e < 0 or x >= len(r) or d0pos < 0:
            continue
        # signal window is ALWAYS t-10..t-5 (known before either entry convention)
        sig_date = r.index[d0pos - config.T3_B_ENTRY]
        zwin = z.loc[:sig_date].tail(lo - hi + 1)
        if len(zwin) < lo - hi + 1:
            continue
        zpre = float(zwin.mean())
        pos = float(np.clip(-zpre / config.T3_SIZE_CAP_Z, -1.0, 1.0))
        if pos == 0.0:
            continue
        entry_date = r.index[e]
        leg = pos * r.iloc[e + 1: x + 1]
        pnl = pnl.add(leg, fill_value=0.0)
        pnl.loc[entry_date] -= cost_bp / 2 * abs(pos)
        pnl.loc[r.index[x]] -= cost_bp / 2 * abs(pos)
        trades.append({"auctionDate": a["auctionDate"], "tenor": ten, "z_B_pre": zpre,
                       "pos": pos, "pnl_gross_bp": float(leg.sum()),
                       "pnl_pre_auction_bp": float((pos * r.iloc[e + 1: d0pos + 1]).sum()),
                       "pnl_post_auction_bp": float((pos * r.iloc[d0pos + 1: x + 1]).sum()),
                       "pnl_net_bp": float(leg.sum()) - cost_bp * abs(pos)})
    return pnl[pnl.ne(0.0).cummax()], pd.DataFrame(trades)


def transfer_diagnostics():
    """Does the auction effect measured on the CM/generic BE index transfer to traded
    prices? Three tests, same z_B_pre conditioning (results -> reports/track3_transfer.csv):
      1. post-auction 1d move: CM index vs financed OTR BE return (slope on z each)
      2. auctioned bond's own real yield vs its stop-out (buy-the-auction channel)
      3. OTR BE yield change from the HELD bonds' yields (bypasses index AND engine)
    v2 finding: the slope lives ONLY in the index (see REPORT_V2.md)."""
    from breakeven_rv import panel as panel_mod
    p = panel_mod.load()
    res = residuals.load()
    auc = data_auctions.load()
    zcol = {"5y": "z_B_5y", "10y": "z_B", "30y": "z_B_30y"}
    becol = {"5y": "be5", "10y": "be10", "30y": "be30"}
    lo, hi = config.AUCTION_LAG
    ycache: dict = {}

    def yld(cusip):
        if cusip not in ycache:
            f = os.path.join(config.ROOT_CACHE, "daily", f"{cusip}.parquet")
            ycache[cusip] = (pd.read_parquet(f)["YLD_YTM_MID"].dropna()
                             if os.path.exists(f) else None)
        return ycache[cusip]

    def zpre_at(ten, sig_date):
        zw = res[zcol[ten]].dropna().loc[:sig_date].tail(lo - hi + 1)
        return float(zw.mean()) if len(zw) == lo - hi + 1 else np.nan

    r1, r2, r3 = [], [], []
    ex = pd.read_csv(os.path.join(config.ROOT, "exports", "breakeven_10y.csv"),
                     usecols=["date", "TIPS_cusip", "UST_cusip"], parse_dates=["date"]
                     ).set_index("date")
    for _, a in auc.iterrows():
        ten = a["tenor"]
        be = p[becol[ten]].dropna()
        r = _returns(ten)
        d0 = be.index.searchsorted(a["auctionDate"], side="right") - 1
        if d0 >= 10 and d0 + 1 < len(be):
            zp = zpre_at(ten, be.index[d0 - 5])
            i0 = r.index.searchsorted(be.index[d0], side="right") - 1
            if np.isfinite(zp) and i0 + 1 < len(r):
                r1.append({"z": zp, "dcm": (be.iloc[d0 + 1] - be.iloc[d0]) * 100,
                           "dotr": r.iloc[i0 + 1]})
        y = yld(a["cusip"])
        if y is not None and np.isfinite(a["highYield"]):
            j0 = y.index.searchsorted(a["auctionDate"], side="right") - 1
            if j0 >= 5 and j0 + 1 < len(y):
                zp = zpre_at(ten, y.index[j0 - 5])
                if np.isfinite(zp):
                    r2.append({"z": zp, "dyld_1d_vs_stop": (y.iloc[j0 + 1] - a["highYield"]) * 100})
        if ten == "10y":
            k0 = ex.index.searchsorted(a["auctionDate"], side="right") - 1
            if k0 >= 5 and k0 + 1 < len(ex):
                yt, yn = yld(ex.iloc[k0]["TIPS_cusip"]), yld(ex.iloc[k0]["UST_cusip"])
                zp = zpre_at(ten, ex.index[k0 - 5])
                if yt is not None and yn is not None and np.isfinite(zp):
                    try:
                        d_be = ((yn.loc[ex.index[k0 + 1]] - yt.loc[ex.index[k0 + 1]])
                                - (yn.loc[ex.index[k0]] - yt.loc[ex.index[k0]])) * 100
                        r3.append({"z": zp, "dbe_otr_yld": d_be})
                    except KeyError:
                        pass

    rows = []
    for name, data, col in (("cm_index", r1, "dcm"), ("financed_otr_return", r1, "dotr"),
                            ("auctioned_bond_yld_vs_stop", r2, "dyld_1d_vs_stop"),
                            ("otr_be_yield_from_bonds", r3, "dbe_otr_yld")):
        df = pd.DataFrame(data).dropna()
        slope = np.polyfit(df["z"], df[col], 1)[0] if len(df) > 20 else np.nan
        rows.append({"measure": name, "n": len(df), "slope_on_zB": slope,
                     "uncond_mean_bp": df[col].mean() if len(df) else np.nan})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(config.REPORTS, "track3_transfer.csv"), index=False)
    print("\nTransfer diagnostics — post-auction 1d effect per measurement space:")
    print(out.round(2).to_string(index=False))
    return out


def run():
    config.ensure_dirs()
    rows, sub_rows = [], []
    base_A = None
    for thr in config.T3_ENTRY_GRID:
        for cost in config.T3_COSTS_BP:
            pnl, tr = backtest_A(thr, cost)
            st = perf_stats(pnl, tr["pnl_net_bp"] if len(tr) else None)
            rows.append({"strategy": "A_everyday", "thr": thr, "cost_bp": cost, **st})
            if thr == 1.0 and cost == 1.0:
                base_A = (pnl, tr)
    for exit_h in config.T3_B_EXITS:
        for cost in config.T3_COSTS_BP:
            pnl, tr = backtest_B(exit_h, cost)
            st = perf_stats(pnl, tr["pnl_net_bp"] if len(tr) else None)
            rows.append({"strategy": f"B_auction_t+{exit_h}", "thr": np.nan, "cost_bp": cost, **st})
            if exit_h == 1 and cost == 1.0:
                base_B = (pnl, tr)
            # diagnostic deviation: enter at the auction-day close (skip the concession leg)
            pnl0, tr0 = backtest_B(exit_h, cost, entry_off=0)
            st0 = perf_stats(pnl0, tr0["pnl_net_bp"] if len(tr0) else None)
            rows.append({"strategy": f"B_t0entry_t+{exit_h}", "thr": np.nan, "cost_bp": cost, **st0})

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY, index=False)
    print("Backtest summary (financed BE total returns, bp on a DV01-normalized unit):")
    print(summary.round(2).to_string(index=False))

    # combined + sub-periods at base parameters (thr=1.0, cost=1.0bp)
    pnl_A, tr_A = base_A
    pnl_B, tr_B = base_B
    comb = pnl_A.add(pnl_B, fill_value=0.0)
    print("\nBase parameters (thr=1.0, cost=1.0bp), sub-period performance:")
    for name, s in (("A_everyday", pnl_A), ("B_auction_t+1", pnl_B), ("combined", comb)):
        for reg, (a, b) in REGIMES.items():
            st = perf_stats(s.loc[a:b])
            sub_rows.append({"strategy": name, "regime": reg, **st})
    sub = pd.DataFrame(sub_rows)
    sub.to_csv(OUT_SUB, index=False)
    print(sub.round(2).to_string(index=False))
    tr_A.to_csv(os.path.join(config.REPORTS, "track3_trades_A.csv"), index=False)
    tr_B.to_csv(os.path.join(config.REPORTS, "track3_trades_B.csv"), index=False)

    # entry-timing attribution for the spec'd B rule (why t-5 entry underperforms)
    print("\nB-strategy leg attribution (spec'd t-5 entry, exit t+1, cost=1.0bp):")
    print(f"  concession leg (t-5 -> t0): mean {tr_B['pnl_pre_auction_bp'].mean():+.2f}bp/trade, "
          f"total {tr_B['pnl_pre_auction_bp'].sum():+.0f}bp")
    print(f"  post-auction leg (t0 -> t+1): mean {tr_B['pnl_post_auction_bp'].mean():+.2f}bp/trade, "
          f"total {tr_B['pnl_post_auction_bp'].sum():+.0f}bp")

    # A PnL by B-state at entry (reporting split, not a rule)
    tr_A["b_state"] = tr_A["quadrant_entry"].map(
        lambda q: "confirm" if q in ("both_cheap", "both_rich")
        else ("contradict" if q == "disagree" else "neutral"))
    print("\nA-strategy net PnL by B-state at entry (thr=1.0, cost=1.0bp):")
    print(tr_A.groupby("b_state")["pnl_net_bp"].agg(["sum", "mean", "count"]).round(2).to_string())

    print("\nWorst 5 A-episodes (thr=1.0, cost=1.0bp):")
    worst = tr_A.nsmallest(5, "pnl_net_bp")
    for _, w in worst.iterrows():
        print(f"  {w['entry'].date()} -> {w['exit'].date()} ({w['days']}bd) "
              f"pos={w['pos']:+.2f} z_in={w['z_entry']:+.2f} quad_in={w['quadrant_entry']:<13s} "
              f"exit={w['exit_reason']:<12s} pnl={w['pnl_net_bp']:+.1f}bp")

    # reconciliation with the fit-reversion decomposition
    print("\nReconciliation vs fit-reversion decomposition:")
    ep_path = os.path.join(config.REPORTS, "fit_reversion_episodes.csv")
    if os.path.exists(ep_path):
        ep = pd.read_csv(ep_path)
        print(f"  decomposition: price-PnL share of gap closed = "
              f"{ep['pnl_price_bp'].sum() / ep['gap_closed_bp'].sum():.1%} "
              f"(mean {ep['pnl_price_bp'].mean():+.2f}bp/episode over {len(ep)} episodes)")
    gross = tr_A["pnl_gross_bp"]
    print(f"  backtest (thr=1.0): mean gross {gross.mean():+.2f}bp/trade over {len(tr_A)} trades, "
          f"total gross {gross.sum():+.0f}bp, total net {tr_A['pnl_net_bp'].sum():+.0f}bp")
    transfer_diagnostics()
    print(f"\n  wrote {OUT_SUMMARY}, {OUT_SUB}, track3_trades_A/B.csv, track3_transfer.csv")
    return summary, sub


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
