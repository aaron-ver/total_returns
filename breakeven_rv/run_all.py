"""
Orchestration for the breakeven RV study. Mirrors the plan's sequencing (§12).

  python -m breakeven_rv.run_all pull      # BBG + FRED + TreasuryDirect (BBG needs the terminal)
  python -m breakeven_rv.run_all build     # panel -> layer1 -> residuals
  python -m breakeven_rv.run_all analyze   # reversion go/no-go + auction study
  python -m breakeven_rv.run_all all       # everything

`build` and `analyze` run from cache — no terminal needed.
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
