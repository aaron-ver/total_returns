"""
One daily-refresh entrypoint for the whole project (US + intl). Runs the stages in order —
PULL -> BUILD -> EXPORT -> RENDER -> ALERTS — with every step isolated (continue-on-failure, timed)
so one failure never sinks the run. This is the single script to schedule/containerize later
(EventBridge -> Fargate); today just run it locally.

Data constraint: Bloomberg *desktop* pulls need the terminal, so the PULL stage (and the US
engine/energy refresh) only fetch live data when the terminal is up. Use --no-pull to (re)build
everything from the existing caches — safe to run anywhere, anytime.

Usage:
  python pipeline.py --no-pull       # LOCAL rebuild from cache (no Bloomberg, no S3) — the dev default
  python pipeline.py                 # full run incl. Bloomberg pull (needs terminal); still local
  python pipeline.py --no-pull --push  # rebuild from cache AND publish to S3 (opt-in)
  python pipeline.py --stage build   # run a single stage (pull|build|export|render|push|alerts)
  python pipeline.py --no-pull --stage render   # e.g. just regenerate the dashboards
  python pipeline.py --verbose       # print full tracebacks on failures
S3 push is OPT-IN (--push or --stage push); a plain run never touches S3.
"""
from __future__ import annotations
import sys, time, traceback

NO_PULL = "--no-pull" in sys.argv
VERBOSE = "--verbose" in sys.argv
PUSH = "--push" in sys.argv          # S3 sync is OPT-IN: a plain run stays fully local
STAGE = "all"
if "--stage" in sys.argv:
    i = sys.argv.index("--stage")
    if i + 1 < len(sys.argv):
        STAGE = sys.argv[i + 1]

LOG = []


def run(label, fn, terminal=False):
    """Run one step, timed and isolated. `terminal=True` steps are skipped under --no-pull."""
    if terminal and NO_PULL:
        print(f"  [SKIP] {label}  (--no-pull)"); LOG.append((label, "SKIP", 0.0)); return
    t = time.time()
    try:
        fn()
        dt = time.time() - t; LOG.append((label, "OK", dt)); print(f"  [OK]   {label}  ({dt:.0f}s)")
    except Exception as e:
        dt = time.time() - t; LOG.append((label, "FAIL", dt))
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        if VERBOSE:
            traceback.print_exc()


def stage_pull():
    """Live Bloomberg desktop pulls (need the terminal). US TIPS/energy come via their refresh() in
    BUILD; here we pull the intl bonds, the nominal-hedge universe, and crude."""
    print("\n== PULL (Bloomberg terminal) ==")
    import data_layer_intl, nominals_intl, crude
    run("intl bonds (daily + static, incremental)", data_layer_intl.update, terminal=True)
    run("intl nominal-hedge universe", nominals_intl.pull, terminal=True)
    run("crude Brent/WTI (CO/CL)", crude.pull_all, terminal=True)


def stage_build():
    """Recompute all series from cache. US refresh() pulls too when the terminal is up (else cache)."""
    print("\n== BUILD ==")
    import engine, energy, crude, hedge
    import auctions_intl, engine_intl, breakeven_intl, cmt_intl, issuance_intl, buckets_intl
    run("US: TIPS/macro + returns", lambda: engine.refresh(update_data=not NO_PULL))
    run("US: energy (RBOB) series", lambda: energy.refresh(update_data=not NO_PULL))
    run("crude: front-month series", crude.build_all)
    run("US: gasoline hedge ratios", hedge.build_all)
    run("intl: auction calendar", auctions_intl.build)
    run("intl: per-bond returns", engine_intl.build_all)
    run("intl: breakevens (street pairs)", breakeven_intl.build_all)
    run("intl: constant-maturity buckets", cmt_intl.build_all)
    run("intl: issuance matrices", issuance_intl.build)
    run("intl: bucket coverage grid", buckets_intl.write_report)


def stage_export():
    """Curated marts (DB-ready) + reports/plots."""
    print("\n== EXPORT (marts + reports) ==")
    import export, seasonal_intl, energy_intl, marts
    run("US: breakeven export workbook", export.export_full)
    run("intl: seasonal tables (cycle+calendar)", seasonal_intl._export)
    run("intl: Brent energy-hedge report", energy_intl.report)
    run("intl: energy outlier plots", energy_intl.plot)
    run("intl: DB marts", marts.build_all)


def stage_render():
    """Regenerate the self-contained dashboards (no browser open in a pipeline run)."""
    print("\n== RENDER (dashboards) ==")
    import dashboard, dashboard_intl
    run("US dashboard.html", lambda: dashboard.build(open_browser=False))
    run("intl dashboard_intl.html", lambda: dashboard_intl.build(open_browser=False))


def stage_push():
    """Optional S3 sync of the consumable outputs (no-op unless LINKERS_S3_BUCKET + AWS creds set)."""
    print("\n== PUSH (S3 sync, if configured) ==")
    import storage
    run("S3 artifact sync", storage.push)


def stage_alerts():
    print("\n== ALERTS ==")
    import alerts
    run("upcoming-auction reminder", lambda: alerts.upcoming(days=7))


STAGES = {"pull": stage_pull, "build": stage_build, "export": stage_export,
          "render": stage_render, "push": stage_push, "alerts": stage_alerts}


def main():
    t0 = time.time()
    if STAGE == "all":
        order = ["pull", "build", "export", "render", "alerts"]   # local only by default
        if PUSH:
            order.insert(order.index("alerts"), "push")           # S3 sync only with --push
    else:
        order = [STAGE]
    for s in order:
        if s not in STAGES:
            print(f"unknown stage '{s}' (pick: {', '.join(STAGES)} or all)"); return
        STAGES[s]()
    ok = sum(1 for _, s, _ in LOG if s == "OK"); fail = [l for l, s, _ in LOG if s == "FAIL"]
    print(f"\n== DONE in {time.time()-t0:.0f}s — {ok} ok, {len(fail)} failed, "
          f"{sum(1 for _,s,_ in LOG if s=='SKIP')} skipped ==")
    if fail:
        print("  failed steps:", "; ".join(fail))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
