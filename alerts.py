"""
Upcoming-auction reminder (cron-ready stub).

We only have HISTORICAL auction dates, so this ESTIMATES each stable-schedule market's next linker
auction from the calendar pattern we validated (weekday + week-of-month; see seasonal_intl): France
= 3rd Thursday, Spain = 1st Thursday, Italy = 4th-week Tuesday. UK gilts are excluded (no fixed
schedule — the DMO announces per-quarter; wire the real forward calendar there later).

Prints (and can later post to Slack/SES) the markets with an estimated auction within `days`. This is
the piece to drop into an EventBridge->Lambda once AWS lands: swap `_notify` for a webhook/email.

Usage:  python alerts.py [days]      # default 7
"""
from __future__ import annotations
import sys
import pandas as pd

# market -> (weekday 0=Mon..6=Sun, nth occurrence in month). Validated in seasonal_intl (Q-B).
SCHEDULE = {"FR_OATEI": (3, 3), "FR_OATI": (3, 3), "ES_EI": (3, 1), "IT_BTPEI": (1, 4)}
LABEL = {"FR_OATEI": "France OAT€i", "FR_OATI": "France OATi", "ES_EI": "Spain SPGB€i",
         "IT_BTPEI": "Italy BTP€i"}


def _nth_weekday(year, month, weekday, n):
    """Date of the n-th `weekday` in year-month (n=1..5); None if it doesn't exist."""
    first = pd.Timestamp(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (n - 1) * 7
    d = pd.Timestamp(year, month, 1) + pd.Timedelta(days=day - 1)
    return d if d.month == month else None


def next_auction(market, today):
    wd, n = SCHEDULE[market]
    for add in range(0, 3):                              # this month, then next couple
        mp = (today.to_period("M") + add)
        d = _nth_weekday(mp.year, mp.month, wd, n)
        if d is not None and d >= today.normalize():
            return d
    return None


def upcoming(days=7, today=None):
    today = pd.Timestamp.today() if today is None else pd.Timestamp(today)
    hits = []
    for m in SCHEDULE:
        d = next_auction(m, today)
        if d is not None:
            dd = (d - today.normalize()).days
            hits.append((dd, m, d))
    hits.sort()
    _notify(today, days, hits)
    return hits


def _notify(today, days, hits):
    due = [h for h in hits if h[0] <= days]
    print(f"Auction outlook as of {today.date()} (est.; UK excluded — no fixed schedule):")
    for dd, m, d in hits:
        flag = "  <<< within window" if dd <= days else ""
        print(f"  {LABEL[m]:16s} est. next auction {d.date()}  (in {dd}d){flag}")
    if due:
        print(f"\n  ** {len(due)} market(s) with an estimated auction in the next {days} days **")
    # later (AWS): post `due` to Slack/SES here instead of/along with printing.


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    upcoming(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
