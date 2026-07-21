"""
Window-return engine for the weekend/headline de-risking study.

Clock windows (US/Eastern; futures tape):
  thu_into_ldn   Thu 17:00 -> Fri 11:30   "London PMs de-risk into their close" (hypothesis 1)
  ldn_to_us      Fri 11:30 -> Fri 15:00   London close -> US rates close
  weekend_gap    Fri 15:00 -> next reopen (Sun 18:00+) "quiet-weekend premium" (hypothesis 2)
  mon_ldn        Mon 02:00 -> Mon 11:30   Monday London session (the unwind?)

Returns are log price changes x 1e4 (~bp of price). CURVE = TU - beta*US where beta is the
full-sample hourly OLS slope — the boss's "pull out the parallel curve risk": what's left is
front-end-specific richening/cheapening, immune to whole-curve rallies.

Run:  python eventflow/windows.py [start]      (default sample split at 2026-03-01)
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eventflow.common import TZ, LONDON_CLOSE_ET, US_RATES_CLOSE_ET
from eventflow import pull_bars

BOSS_START = "2026-03-01"


def closes_et(sym):
    b = pull_bars.load(sym)
    if b is None:
        raise SystemExit(f"no bars for {sym} — run eventflow/pull_bars.py")
    s = b["close"].copy()
    s.index = s.index.tz_convert(TZ)
    return s


def _at_or_before(s, ts):
    """Last price at/before ts (None if the gap to it exceeds 12h — market closed too long)."""
    pos = s.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    t = s.index[pos]
    if (ts - t) > pd.Timedelta(hours=12):
        return None
    return float(s.iloc[pos])


def _first_after(s, ts, max_h=30):
    pos = s.index.searchsorted(ts, side="left")
    if pos >= len(s):
        return None
    t = s.index[pos]
    if (t - ts) > pd.Timedelta(hours=max_h):
        return None
    return float(s.iloc[pos])


def week_windows(s):
    """One row per week: log-return (x1e4) of each clock window."""
    days = pd.date_range(s.index[0].normalize(), s.index[-1].normalize(), freq="W-THU", tz=TZ)
    rows = []
    for thu in days:
        fri = thu + pd.Timedelta(days=1)
        mon = thu + pd.Timedelta(days=4)
        pts = {
            "thu17": _at_or_before(s, thu + pd.Timedelta(hours=17)),
            "fri_ldn": _at_or_before(s, fri + pd.Timedelta(hours=LONDON_CLOSE_ET)),
            "fri_us": _at_or_before(s, fri + pd.Timedelta(hours=US_RATES_CLOSE_ET)),
            "reopen": _first_after(s, fri + pd.Timedelta(hours=US_RATES_CLOSE_ET + 1), max_h=76),
            "mon_ldn": _at_or_before(s, mon + pd.Timedelta(hours=LONDON_CLOSE_ET)),
        }
        def lr(a, b):
            return None if (pts[a] is None or pts[b] is None or pts[a] <= 0) \
                else round(np.log(pts[b] / pts[a]) * 1e4, 2)
        rows.append({"week": fri.date().isoformat(),
                     "thu_into_ldn": lr("thu17", "fri_ldn"),
                     "ldn_to_us": lr("fri_ldn", "fri_us"),
                     "weekend_gap": lr("fri_us", "reopen"),
                     "mon_ldn": lr("reopen", "mon_ldn")})
    return pd.DataFrame(rows).set_index("week")


def hourly_rets(s):
    r = np.log(s / s.shift(1)) * 1e4
    return r.dropna()


def curve_beta(tu, us):
    """Full-sample hourly OLS beta of TU returns on US returns (the parallel-risk hedge)."""
    a, b = hourly_rets(tu).align(hourly_rets(us), join="inner")
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.polyfit(b[m], a[m], 1)[0])


def _stats(x):
    x = pd.to_numeric(x, errors="coerce").dropna()
    n = len(x)
    if n < 4:
        return dict(n=n, mean=np.nan, t=np.nan, hit=np.nan)
    t = x.mean() / (x.std(ddof=1) / np.sqrt(n)) if x.std(ddof=1) > 0 else np.nan
    return dict(n=n, mean=round(x.mean(), 2), t=round(t, 2),
                hit=round(100 * (x > 0).mean()))


def dow_hour_grid(s, start=None):
    """Mean hourly return by (day-of-week, 2h ET bucket) — where in the week does TU move?"""
    r = hourly_rets(s)
    if start:
        r = r[r.index >= pd.Timestamp(start, tz=TZ)]
    g = r.groupby([r.index.dayofweek, (r.index.hour // 2) * 2]).agg(["mean", "count"])
    return g["mean"].unstack().round(2), g["count"].unstack()


def report(start=BOSS_START):
    tu, ty, us, wn = closes_et("TU"), closes_et("TY"), closes_et("US"), closes_et("WN")
    beta = curve_beta(tu, us)
    print(f"hourly TU~US beta (parallel hedge): {beta:.3f}\n")
    frames = {"TU (2y)": tu, "TY (10y)": ty, "US (30y bond)": us, "WN (ultra)": wn}
    W = {k: week_windows(s) for k, s in frames.items()}
    W["CURVE (TU - b*US)"] = W["TU (2y)"].astype(float) - beta * W["US (30y bond)"].astype(float)

    for label, since in (("BOSS SAMPLE (since " + start + ")", start), ("FULL SAMPLE (2024→)", None)):
        print("=" * 30, label, "=" * 30)
        hdr = f"{'instrument':22s}" + "".join(f"{c:>26s}" for c in
              ["thu_into_ldn", "ldn_to_us", "weekend_gap", "mon_ldn"])
        print(hdr)
        for name, w in W.items():
            ww = w[w.index >= since] if since else w
            cells = []
            for c in ["thu_into_ldn", "ldn_to_us", "weekend_gap", "mon_ldn"]:
                st = _stats(ww[c])
                cells.append(f"{st['mean']:+7.1f} (t{st['t']:+.1f} n{st['n']:d} h{st['hit']:.0f}%)"
                             if np.isfinite(st["mean"]) else f"{'—':>24s}")
            print(f"{name:22s}" + "".join(f"{c:>26s}" for c in cells))
        print()
    print("units: ~bp of futures PRICE (log-ret x 1e4); positive = price up = yields DOWN = rally")
    print("read hypothesis 1: positive thu_into_ldn on TU = front end rallies into London close")
    print("read hypothesis 2: positive weekend_gap = paid to hold long over the weekend")
    return W


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    report(sys.argv[1] if len(sys.argv) > 1 else BOSS_START)
