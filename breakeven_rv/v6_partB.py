"""
v6 Part B — the quiet-break detector, with hindsight discipline.

DEVELOPMENT CASE: 10y 2021-22 (stated plainly — detectors were designed knowing it).
VALIDATION: 5y/30y (never inspected during design) + UK (UK_3M) and France (FR_OATEI)
linkers from cache_intl, run with ZERO per-market tuning (identical config params).

Detectors run ONE AT A TIME (stress off for the quiet grading; stress reported
separately) on a frozen-segment model; grading vs config.V6_NARRATIVE with a
timeliness ceiling of config.V6_LAG_CEILING bd; the FALSE-POSITIVE BUDGET table
lists EVERY break on every market with a narrative defense or an honest "no story".
> config.V6_FP_BUDGET breaks per 18y = the detector rebuilt the rolling window: FAIL.

Intl Layer-1 analogue (documented deviations): BE level = nominal_yield − linker_yield
from the engine's 10y CMT bucket (held-bond quotes; roll jumps included); factors =
[local 2s10s from the 2y/10y buckets, log Brent (root cache), VIX] — no FX factor
(no cached series; new pulls out of scope). Stress monitor is US-specific and NOT
applied to intl; intl grades the quiet detectors alone.

Outputs: reports/v6_break_budget.csv (the full FP budget), v6_detector_grades.csv.
Usage:  python -m breakeven_rv.v6_partB
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from breakeven_rv import config, v6_core, data_fred

R = config.REPORTS
INTL = os.path.join(config.ROOT, "cache_intl")


def intl_data(market: str):
    """(idx, y_bp, X[const, slope_2s10s, log_brent, vix]x100-basis, macro matrix)."""
    b10 = pd.read_parquet(os.path.join(INTL, "cmt", f"{market}__10y.parquet"))
    # slope uses the 7y bucket: CMT buckets start N years before maturity, so 7y is the
    # shortest bucket with usable history (2019+; the 2y bucket only exists from 2024)
    b7 = pd.read_parquet(os.path.join(INTL, "cmt", f"{market}__7y.parquet"))
    be = (b10["nominal_yield"] - b10["linker_yield"]) * 100.0          # bp
    slope = (b10["nominal_yield"] - b7["nominal_yield"].reindex(b10.index)) * 100.0
    brent = pd.read_parquet(os.path.join(config.ROOT_CACHE, "crude_raw_Brent.parquet"))["front"]
    fred = data_fred.load()
    df = pd.DataFrame({
        "_y": be, "slope": slope,
        "log_brent": np.log(brent).reindex(be.index).ffill(limit=3) * 100.0,
        "vix": fred["vix"].reindex(be.index).ffill(limit=3),
    }).dropna()
    y = df["_y"].values
    X = np.column_stack([np.ones(len(df)), df[["slope", "log_brent", "vix"]].values])
    # macro vector: local CPI momentum (from cache_intl macro), front-end level, brent 1y chg
    mac_raw = pd.read_parquet(os.path.join(INTL, "macro.parquet"))
    cpi_col = {"UK_3M": "UK_RPI", "FR_OATEI": "EUR_HICPXT"}.get(market)   # OATei = euro HICPxt
    cpi = mac_raw[cpi_col].dropna() if cpi_col in mac_raw else pd.Series(dtype=float)
    yoy = cpi.pct_change(12) * 100.0
    pub = yoy.index + pd.DateOffset(months=1)
    pub = pub.map(lambda d: d.replace(day=config.CPI_PUB_DAY))
    yoy_known = pd.Series(yoy.values, index=pub).sort_index().reindex(df.index, method="ffill")
    macro = pd.DataFrame({
        "cpi": yoy_known,
        "front": b7["nominal_yield"].reindex(df.index).ffill(limit=3),
        "brent1y": np.log(brent).diff(252).reindex(df.index).ffill(limit=3),
    }).values
    return df.index, y, X, macro


def grade(idx, breaks, trig, market_key: str) -> tuple[list, list]:
    """FP-budget rows + narrative hits (timely if 0..V6_LAG_CEILING bd after anchor)."""
    # only events the sample can actually see (anchor after warm-up) are gradable
    gradable_from = idx[0] + pd.Timedelta(days=400)
    narr = {ev: a for ev, a in config.V6_NARRATIVE[market_key].items()
            if pd.Timestamp(a) >= gradable_from}
    budget, hits = [], {}
    for b, tg in zip(breaks, trig):
        d = idx[b]
        story = "no story — possible false fire"
        for ev, anchor in narr.items():
            lag = (d - pd.Timestamp(anchor)).days
            if 0 <= lag <= config.V6_LAG_CEILING * 1.6:      # calendar ~ 1.6x bd
                bd_lag = int(lag / 1.45)
                story = f"{ev} (+{bd_lag}bd)"
                if ev not in hits or bd_lag < hits[ev]:
                    hits[ev] = bd_lag
                break
        budget.append({"date": str(d.date()), "trigger": tg, "defense": story})
    misses = [ev for ev in narr if ev not in hits]
    return budget, [{"event": ev, "lag_bd": lg, "timely": lg <= config.V6_LAG_CEILING}
                    for ev, lg in hits.items()] + [{"event": ev, "lag_bd": np.nan,
                                                    "timely": False} for ev in misses]


def run():
    config.ensure_dirs()
    budget_rows, grade_rows = [], []
    runs = []
    for tenor in config.V5_TENORS:
        idx, y, X, crisis = v6_core.tenor_data(tenor)
        macro = v6_core.macro_matrix(idx)
        runs.append((tenor, "us", idx, y, X, crisis, macro))
    for market in ("UK_3M", "FR_OATEI"):
        try:
            idx, y, X, macro = intl_data(market)
            runs.append((market, market, idx, y, X, None, macro))
            print(f"  {market}: {len(idx)} days {idx.min().date()} -> {idx.max().date()}")
        except Exception as e:
            print(f"  {market}: SKIPPED — {e}")

    for name, narr_key, idx, y, X, crisis, macro in runs:
        detectors = {"stress": dict(quiet="none", use_stress=True),
                     "drift": dict(quiet="drift", use_stress=False),
                     "ferr": dict(quiet="ferr", use_stress=False),
                     "macro": dict(quiet="macro", use_stress=False)}
        if crisis is None:
            detectors.pop("stress")
        for det, kw in detectors.items():
            _, _, brk, trig, _ = v6_core.segment_run(idx, y, X, crisis=crisis,
                                                     macro=macro, model="frozen", **kw)
            bud, hits = grade(idx, brk, [det] * len(brk), narr_key)
            years = (idx.max() - idx.min()).days / 365.25
            per18 = len(brk) / years * 18
            for r in bud:
                budget_rows.append({"market": name, "detector": det, **r})
            n_timely = sum(1 for h in hits if h["timely"])
            n_gradable = len(hits)
            grade_rows.append({
                "market": name, "detector": det, "n_breaks": len(brk),
                "breaks_per_18y": per18, "fp_budget_FAIL": per18 > config.V6_FP_BUDGET,
                "narrative_events": n_gradable,
                "timely_hits": n_timely,
                "hits_detail": "; ".join(f"{h['event']}:{h['lag_bd'] if np.isfinite(h['lag_bd']) else 'MISS'}"
                                         for h in hits)})
            print(f"  {name:9s} {det:7s}: {len(brk)} breaks ({per18:.1f}/18y) "
                  f"timely {n_timely}/{n_gradable}")

    bud = pd.DataFrame(budget_rows)
    gr = pd.DataFrame(grade_rows)
    bud.to_csv(os.path.join(R, "v6_break_budget.csv"), index=False)
    gr.to_csv(os.path.join(R, "v6_detector_grades.csv"), index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 100):
        print("\nDetector grades (FP budget = 8/18y; timely = within 120bd):")
        print(gr.to_string(index=False))
        print("\nFull false-positive budget:")
        print(bud.to_string(index=False))
    return gr, bud


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    run()
