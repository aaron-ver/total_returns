"""
Orchestration for the breakeven RV study. Mirrors the plan's sequencing (§12).

  python -m breakeven_rv.run_all pull      # BBG + FRED + TreasuryDirect (BBG needs the terminal)
  python -m breakeven_rv.run_all build     # panel -> layer1 -> residuals
  python -m breakeven_rv.run_all analyze   # v1: reversion go/no-go + auction study
  python -m breakeven_rv.run_all layer2    # v2: track1 (+decomp), track2, track3 backtest
  python -m breakeven_rv.run_all v3        # v3: pulls (bonds/dealer/fixings/asw) + b_bond
                                           #     + experiment + revalidate + metrics + figures
  python -m breakeven_rv.run_all v4        # v4: component separation (signal-side only)
  python -m breakeven_rv.run_all v5        # v5: multi-tenor confirmation + regime detection
  python -m breakeven_rv.run_all all       # everything

Everything except `pull` and the BBG parts of `v3` runs from cache — no terminal needed.
"""
from __future__ import annotations
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _step(label, fn):
    t0 = time.time()
    print(f"\n=== {label} ===")
    fn()
    print(f"    ({time.time() - t0:.1f}s)")


def pull():
    from breakeven_rv import data_bbg, data_fred, data_auctions
    _step("PULL bbg", data_bbg.pull)
    _step("PULL fred", data_fred.pull)
    _step("PULL auctions", data_auctions.pull)


def build():
    from breakeven_rv import panel, layer1, residuals
    _step("BUILD panel", panel.build)
    _step("BUILD layer1", layer1.build)
    _step("BUILD residuals", residuals.build)


def analyze():
    from breakeven_rv import reversion, auction_study
    _step("ANALYZE reversion go/no-go", reversion.run)
    _step("ANALYZE auction study", auction_study.run)


def layer2():
    from breakeven_rv import track1, track1_decomp, track2, track3_backtest
    _step("TRACK1 conditioning model", track1.run)
    _step("TRACK1 fit-reversion decomposition", track1_decomp.run)
    _step("TRACK2 auction event model", track2.run)
    _step("TRACK3 backtest", track3_backtest.run)


def v4():
    from breakeven_rv import v4_core, v4_exp1, v4_exp2, v4_exp3, v4_exp4, v4_exp5, v4_figures
    for ez in [0.75, 1.0, 1.5]:
        _step(f"V4 episodes entry_z={ez}", lambda ez=ez: v4_core.episodes(ez, use_cache=False))
    _step("V4 exp1 frozen-z", v4_exp1.run)
    _step("V4 exp2 B_clean", v4_exp2.run)
    _step("V4 exp3 flow", v4_exp3.run)
    _step("V4 exp4 asymmetry", v4_exp4.run)
    _step("V4 exp5 state/wait-cost", v4_exp5.run)
    _step("V4 figures", lambda: (v4_figures.fig_phantom(), v4_figures.fig_wait_cost(),
                                 v4_figures.fig_flow()))


def v5():
    from breakeven_rv import b_bond, v5_core, v5_partA, v5_partB, v5_partC, v5_figures, config as cfg
    for t in ("5y", "30y"):
        _step(f"V5 b_bond {t}", lambda t=t: b_bond.build(t))
    def _eps():
        for t in cfg.V5_TENORS:
            v5_core.build_layer1(t)
            for ez in cfg.V3_ENTRY_GRID:
                v5_core.episodes(t, ez, use_cache=False)
    _step("V5 core episodes (all tenors)", _eps)
    _step("V5 Part A scorecard", v5_partA.run)
    _step("V5 Part B monitor", v5_partB.run)
    _step("V5 Part C segmented FV", v5_partC.run)
    _step("V5 figures", lambda: (v5_figures.fig_flag_timeline(), v5_figures.fig_segmented_fv()))


def v3():
    from breakeven_rv import (data_bonds, data_dealer, data_fixings, data_asw,
                              b_bond, v3_experiment, v3_revalidate, v3_metrics, v3_figures)
    _step("V3 pull bonds", data_bonds.build)
    _step("V3 pull dealer positions", data_dealer.pull)
    _step("V3 pull fixings", data_fixings.pull)
    _step("V3 pull asw", data_asw.pull)
    _step("V3 build B_bond", b_bond.build)
    _step("V3 decisive experiment", v3_experiment.run)
    _step("V3 revalidate", v3_revalidate.run)
    _step("V3 metrics", v3_metrics.run)
    _step("V3 figures", lambda: (v3_figures.fig_seasonal(), v3_figures.fig_autopsy(),
                                 v3_figures.fig_rolling_sharpe()))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("pull", "all"):
        pull()
    if cmd in ("build", "all"):
        build()
    if cmd in ("analyze", "all"):
        analyze()
    if cmd in ("layer2", "all"):
        layer2()
    if cmd in ("v3", "all"):
        v3()
    if cmd in ("v4", "all"):
        v4()
    if cmd in ("v5", "all"):
        v5()
