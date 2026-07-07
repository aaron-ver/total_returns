"""
v3 metrics — the surviving-book numbers (spec "Additional metrics" + P4-adjacent).

Transfer outcome was (c)/artifact, so the B auction strategy is NOT backtested
(P4 conditionality). The only surviving candidate is the CONFIRM-GATED A overlay
(bond-B quadrants, v2 rules otherwise). This module produces for it:
  - threshold x cost grid (all reported, nothing picked)
  - capacity & frequency honesty box (entries/yr, holding period, annual bp budget)
  - turnover-adjusted breakeven cost (round-trip bp where net Sharpe = 0)
  - entry-latency decay (confirm-episode price PnL entering 0/1/3/5 bd late)
  - rolling 2y Sharpe series (csv -> figure in v3_figures.py)

Outputs: reports/v3_surviving_book.csv, v3_capacity_box.csv, v3_latency.csv,
v3_rolling_sharpe.csv.  Usage:  python -m breakeven_rv.v3_metrics
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, panel as panel_mod, track3_backtest
from breakeven_rv.v3_revalidate import quadrant_bond_series
from breakeven_rv.validation import perf_stats

R = config.REPORTS


def surviving_grid(qb) -> tuple[pd.DataFrame, dict]:
    rows, keep = [], {}
    for thr in config.T3_ENTRY_GRID:
        for cost in config.T3_COSTS_BP:
            pnl, tr = track3_backtest.backtest_A(thr, cost, quad_override=qb, confirm_gate=True)
            st = perf_stats(pnl, tr["pnl_net_bp"] if len(tr) else None)
            ep_skew = float(tr["pnl_net_bp"].skew()) if len(tr) > 3 else np.nan
            rows.append({"strategy": "A_confirm_gated", "thr": thr, "cost_bp": cost,
                         **st, "per_trade_skew": ep_skew})
            keep[(thr, cost)] = (pnl, tr)
    return pd.DataFrame(rows), keep


def run():
    config.ensure_dirs()
    qb = quadrant_bond_series()
    grid, keep = surviving_grid(qb)
    grid.to_csv(os.path.join(R, "v3_surviving_book.csv"), index=False)
    print("Surviving book: CONFIRM-GATED A overlay (bond quadrants), thr x cost grid:")
    print(grid.round(2).to_string(index=False))

    # capacity box (base thr=1.0, cost=1.0)
    pnl, tr = keep[(1.0, 1.0)]
    years = (pnl.index.max() - pnl.index.min()).days / 365.25
    box = pd.DataFrame([
        {"rule": "A_confirm_gated (thr=1.0)", "entries_per_year": len(tr) / years,
         "mean_holding_bd": tr["days"].mean(), "ann_bp_net_1bp_cost": pnl.mean() * 252,
         "note": "episodic overlay"},
        {"rule": "B auction strategy", "entries_per_year": 0, "mean_holding_bd": np.nan,
         "ann_bp_net_1bp_cost": 0.0,
         "note": "CLOSED - index artifact (v3 transfer verdict)"},
    ])
    box.to_csv(os.path.join(R, "v3_capacity_box.csv"), index=False)
    print("\nCapacity & frequency honesty box:")
    print(box.round(2).to_string(index=False))

    # breakeven cost: net Sharpe ~ linear in cost -> interpolate zero crossing per thr
    print("\nTurnover-adjusted breakeven round-trip cost (net Sharpe = 0):")
    for thr in config.T3_ENTRY_GRID:
        sub = grid[grid["thr"] == thr].sort_values("cost_bp")
        be = np.interp(0.0, -sub["sharpe"], sub["cost_bp"])   # sharpe decreasing in cost
        lo, hi = sub["sharpe"].iloc[0], sub["sharpe"].iloc[-1]
        note = "" if (lo > 0 > hi) else ("  (>2bp: positive at all tested costs)" if hi > 0
                                         else "  (<0.5bp: negative at all tested costs)")
        print(f"  thr={thr}: breakeven ~{be:.2f}bp{note}")

    # entry-latency decay on confirm episodes (price PnL entering k bd late)
    ep = pd.read_csv(os.path.join(R, "v3_episodes.csv"), parse_dates=["entry", "exit"])
    conf = ep[ep["b_state"] == "confirm"]
    p = panel_mod.load()
    y = (p["swap_10y"] * 100).dropna()
    rows = []
    for k in (0, 1, 3, 5):
        pnls = []
        for _, e in conf.iterrows():
            i_e = y.index.searchsorted(e["entry"]) + k
            i_x = y.index.searchsorted(e["exit"])
            if i_e >= i_x or i_x >= len(y):
                continue
            s = 1.0 if e["side"] == "long_BE" else -1.0
            pnls.append(s * (y.iloc[i_x] - y.iloc[i_e]))
        rows.append({"entry_lag_bd": k, "mean_price_pnl_bp": np.mean(pnls),
                     "hit_rate": np.mean([x > 0 for x in pnls]), "n": len(pnls)})
    lat = pd.DataFrame(rows)
    lat.to_csv(os.path.join(R, "v3_latency.csv"), index=False)
    print("\nEntry-latency decay (confirm episodes, price PnL entering k bd late):")
    print(lat.round(2).to_string(index=False))

    # rolling 2y Sharpe series (for the figure)
    d = pnl.dropna()
    roll = (d.rolling(504).mean() / d.rolling(504).std() * np.sqrt(252)).dropna()
    hit = (d[d != 0] > 0).rolling(504, min_periods=100).mean()
    pd.DataFrame({"rolling_sharpe_2y": roll}).to_csv(os.path.join(R, "v3_rolling_sharpe.csv"))
    print(f"\nrolling 2y Sharpe (thr=1.0, cost=1.0): last={roll.iloc[-1]:.2f}, "
          f"min={roll.min():.2f}, max={roll.max():.2f} -> figure")
    return grid, box, lat


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
