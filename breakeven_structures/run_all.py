"""
Orchestration for the auction-cycle structures study.

Stages (everything except pull-terminal runs WITHOUT a Bloomberg terminal):
  pull           public-API pulls: US auctions (TIPS + nominal, announcementDate) + calendar
  events         Phase 1: master event matrix + counts + data-quality report
  dry-run        Phase 0 terminal-session plan (what the one BBG session will pull)
  pull-terminal  the one terminal session (Bloomberg DAPI; run on the terminal box)
  build-universe consolidate the full TIPS strip panel from cache
  all            pull + events + dry-run
  status         what's cached

Usage:  python -m breakeven_structures.run_all [stage]
"""
from __future__ import annotations
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_structures import config


def _stage(name, fn):
    print(f"\n=== {name} " + "=" * max(1, 60 - len(name)))
    t0 = time.time()
    fn()
    print(f"=== {name} done in {time.time() - t0:.1f}s")


def main(cmd: str):
    from breakeven_structures import data_auctions_us, data_calendar, data_events, data_universe
    if cmd in ("pull", "all"):
        _stage("pull: US auctions (TreasuryDirect)", data_auctions_us.pull)
        _stage("pull: calendar (CPI releases + index entry)", data_calendar.pull)
    if cmd in ("events", "all"):
        _stage("events: master event matrix", data_events.build)
    if cmd in ("curves", "all"):
        from breakeven_structures import curves
        _stage("curves: fitted TIPS curve + residuals", curves.build)
    if cmd in ("studyA", "all"):
        from breakeven_structures import sA_eventstudy, figures
        _stage("Study A: auction event paths", sA_eventstudy.run)
        _stage("figures: Study A chart pack", figures.sA)
    if cmd in ("dry-run", "dryrun", "all"):
        _stage("dry-run: terminal session plan", data_universe.dry_run)
    if cmd in ("pull-terminal",):
        _stage("TERMINAL pull: full TIPS strip", data_universe.pull)
        _stage("build: full strip panel", data_universe.build)
    if cmd in ("build-universe",):
        _stage("build: full strip panel", data_universe.build)
    if cmd == "status":
        for f in ("auctions_us.parquet", "cpi_releases.parquet", "index_entry.parquet",
                  "events.parquet", "bond_quotes_full.parquet",
                  "curve_residuals.parquet"):
            p = os.path.join(config.CACHE, f)
            print(f"  {f:28s} {'yes' if os.path.exists(p) else 'no'}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1] if len(sys.argv) > 1 else "status")
