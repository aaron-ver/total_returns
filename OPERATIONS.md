# Operations runbook — linkers system

Everything you need to keep the system running: what runs automatically, what you do and when, and
how to fix the common issues. Deeper detail on the scheduler is in [AUTOMATION.md](AUTOMATION.md);
the data-source map is in [DATA_SOURCES.md](DATA_SOURCES.md).

All commands are run from the project folder in PowerShell, using the venv python:
`.\.venv\Scripts\python.exe …`

---

## Quick reference — what to run and when

| When | Task | Command |
|---|---|---|
| **Every weekday, 4pm** | Full refresh (pull → build → render → push) | *automatic* (scheduled task) — just have Bloomberg open |
| **Daily, ~30s** | Health check — did it run? | `Get-ScheduledTaskInfo -TaskName LinkersDailyRefresh` + log tail |
| **Weekly** | Fresh ONE-link portal for your boss (dashboards + research PDFs, 7-day expiry) | `.\.venv\Scripts\python.exe storage.py portal` |
| **When a new linker is issued** | Add its ISIN, pull, rebuild | edit `linkers.py` → `pipeline.py --stage pull` → `pipeline.py --no-pull --push` |
| **Occasionally (quarterly-ish)** | Refresh the nominal-hedge universe | Security Finder export → `nominals_intl.py import` → `nominals_intl.py pull` |
| **Anytime** | Run a refresh now (missed a day) | `.\.venv\Scripts\python.exe pipeline.py --push` |
| **Anytime** | Rebuild + push without Bloomberg | `.\.venv\Scripts\python.exe pipeline.py --no-pull --push` |
| **If S3 push fails** | Check AWS credentials | `.\.venv\Scripts\python.exe storage.py identity` |

---

## The 3 requirements for the automatic 4pm run
It runs itself **only if**, at 4pm on a weekday:
1. The machine is **on** and you're **logged into Windows** (a locked screen is fine).
2. **Bloomberg is running** (the pull needs it).
3. Your **AWS credentials are valid** (they're long-lived now, so this is a set-and-forget ✓).

If #1 or #2 isn't met, it catches up next time you're logged on; if it catches up when Bloomberg
is closed, the pull is skipped and it rebuilds from the last cache (never crashes).

---

## Routine tasks in detail

### 1. Daily health check (~30 seconds)
```powershell
Get-ScheduledTaskInfo -TaskName LinkersDailyRefresh
Get-Content (Get-ChildItem .\logs\pipeline_*.log | Sort LastWriteTime | Select -Last 1) -Tail 40
```
**Good** looks like: `LastTaskResult 0`, and the log ends with `== DONE … — 19 ok, 0 failed …`,
the PULL stages `[OK]`, and PUSH `uploaded N changed`. If PULL shows `[FAIL]`, Bloomberg wasn't up
— just run it again manually (below) with the terminal open.

### 2. Weekly — refresh the ONE-link portal for your boss
One link that connects to everything: a small index page with both dashboards (intl linkers + US
TIPS) and every research PDF. Links expire after 7 days, so regenerate weekly and resend:
```powershell
.\.venv\Scripts\python.exe storage.py portal
```
Everything renders in the browser (no downloads), and the dashboards behind the links update
automatically with each daily refresh — the boss keeps clicking the same link all week and always
sees the latest build. New research PDFs appear on the portal automatically after a push (the
daily push uploads `experiments/out/*.pdf` to `research/`).

Individual links, if ever needed: `storage.py url` (intl) / `storage.py url us` (US TIPS).
*(Once the permanent CloudFront URL is set up with the cloud team, this weekly step goes away.)*

**Hobbes (Desktop\Hobbes) publishes onto the same portal — and it's part of the daily run.**
The scheduled task now also refreshes Hobbes after the linkers pipeline: screener store re-pull
(Bloomberg) → static screener HTML render → ICAP surface snapshot → publish to `monitors/`.
Each step is isolated — a Hobbes failure never affects the linkers refresh. One-time setup in the
Hobbes venv: `.venv\Scripts\python.exe -m pip install boto3`.

Manual Hobbes publish any time (from the Hobbes repo):
```powershell
.\.venv\Scripts\python.exe screener\scripts\render_static.py   # re-render screener.html from the store
.\.venv\Scripts\python.exe publish.py                          # push to monitors/ (only changed files)
```
Everything under `monitors/` shows up in the portal's MONITOR section next time you run
`storage.py portal`. The screener page shows its own as-of date; the vol pricer's ICAP surface is
a snapshot stamped `asof` (daily by default — re-publish while the feed runs for fresher marks).

### 3. When a NEW linker is issued (a brand-new ISIN, a few times/year)
A **tap/reopening of an existing bond needs no action** — the daily run picks it up automatically.
Only a **brand-new bond line** needs to be added:

1. Open `linkers.py`, find `SEED_UNIVERSE`, and add a row in the right country block:
   ```python
   #  isin,            market,     cpn,   maturity,     first_issue,   desc
   ("FR001400XXXX", "FR_OATEI", 0.10, "2035-07-25", "2026-05-25", "OATEI 0.1 07/25/35"),
   ```
   (`market` is one of `FR_OATEI, FR_OATI, IT_BTPEI, ES_EI, DE_EI, UK_3M, UK_8M`.)
2. Pull just the new bond (skip-existing fetches only it) and rebuild + publish:
   ```powershell
   .\.venv\Scripts\python.exe pipeline.py --stage pull      # fetches the new ISIN (needs Bloomberg)
   .\.venv\Scripts\python.exe pipeline.py --no-pull --push  # rebuild everything + push to S3
   ```
The exact static (coupon/maturity) is confirmed from Bloomberg on pull — the row above just tells
the pull *which* bond to fetch.

### 4. Occasionally — refresh the nominal-hedge universe
The hedge picks the closest nominal bond that existed at each point. When new nominals are issued
(or a hedge looks stale/missing), refresh the pool:
1. In Bloomberg, re-run the **Security Finder** export for each country's nominal govies and save
   the files (CSV) into the `nominal_universe\` folder (same format as before).
2. Import + pull:
   ```powershell
   .\.venv\Scripts\python.exe nominals_intl.py import   # rebuild cache_intl/nominal_universe.csv
   .\.venv\Scripts\python.exe nominals_intl.py pull     # pull prices for any new ISINs (skip existing)
   .\.venv\Scripts\python.exe pipeline.py --no-pull --push   # rebuild hedges + dashboard, publish
   ```

### 5. Auctions & reminders
Reminders print automatically at the end of every daily run (the ALERTS stage). To look ahead
manually:
```powershell
.\.venv\Scripts\python.exe alerts.py 21     # estimated auctions in the next 21 days
```
The auction *calendar* itself rebuilds every daily run (`auctions_intl.build`). For UK gilt history,
drop new DMO **D5D** PDFs into `gilt_issuance\` and run:
```powershell
.\.venv\Scripts\python.exe auctions_intl.py uk_d5d
```

---

## On-demand commands

| Goal | Command |
|---|---|
| Full refresh now (with pull) | `.\.venv\Scripts\python.exe pipeline.py --push` |
| Rebuild from cache + push (no Bloomberg) | `.\.venv\Scripts\python.exe pipeline.py --no-pull --push` |
| Just the pull, with full errors | `.\.venv\Scripts\python.exe pipeline.py --stage pull --verbose` |
| Just regenerate dashboards | `.\.venv\Scripts\python.exe pipeline.py --no-pull --stage render` |
| Just push to S3 | `.\.venv\Scripts\python.exe pipeline.py --stage push` |
| ONE-link portal (dashboards + research PDFs) | `.\.venv\Scripts\python.exe storage.py portal` |
| Single dashboard link | `.\.venv\Scripts\python.exe storage.py url` (intl) / `storage.py url us` (TIPS) |
| Check AWS creds / bucket reach | `.\.venv\Scripts\python.exe storage.py identity` |
| Run the scheduled task now | `Start-ScheduledTask -TaskName LinkersDailyRefresh` |
| Change the schedule time | `.\register_task.ps1 -At "15:30"` |

---

## Making changes (dev workflow)

The pipeline is staged so you **only re-run what your change touches** — you rarely need a full run.
The one rule: **PULL is only needed when the raw Bloomberg data changes.** Code changes (math, UI)
read the existing cache, so skip it with `--no-pull`.

| You changed… | Run | Bloomberg? |
|---|---|---|
| **Dashboard UI / client-side JS** (`dashboard_intl.py`, `dashboard.py`) — layout, controls, colors, the β/energy/box/regression math done in-browser | `.\.venv\Scripts\python.exe pipeline.py --no-pull --stage render` | No |
| **Python calculations** (`engine.py`, `engine_intl.py`, `cmt_intl.py`, `breakeven_intl.py`, `seasonal_intl.py`, `energy_intl.py`, `hedge.py`, `pricing.py`) | `.\.venv\Scripts\python.exe pipeline.py --no-pull --push` | No |
| **New raw data** — a new Bloomberg field, ticker, or bond that must be fetched | `.\.venv\Scripts\python.exe pipeline.py --push` (full) | **Yes** |

Why: `BUILD` recomputes series from the cache, `EXPORT` rebuilds marts/reports, `RENDER` regenerates
the HTML. A math change needs BUILD→EXPORT→RENDER (that's what `--no-pull --push` runs); a pure UI
change only needs RENDER.

### Iterate first, push when happy
While tweaking, **leave off `--push`** so nothing goes to S3 until you've eyeballed it:
```powershell
.\.venv\Scripts\python.exe pipeline.py --no-pull --stage render   # rebuild the HTML locally
start .\dashboard_intl.html                                       # open it in your browser to check
#  …tweak dashboard_intl.py, repeat…
.\.venv\Scripts\python.exe pipeline.py --stage push               # publish only once it looks right
```
For a calculation change, the loop is the same with `--no-pull` (no `--push`) to rebuild + preview,
then `--stage push` when satisfied.

### Adding a genuinely new data field (the one gotcha)
If you add a **new field to an existing pull** (e.g. a new column in `DAILY_FIELDS`), the bonds
already cached won't have it — `skip_existing` would leave them stale. Force a re-pull of the
affected cache:
- delete the relevant files under `cache_intl\daily\` (or the whole folder for a full re-pull), then
- `.\.venv\Scripts\python.exe pipeline.py --push`

A new *bond* or *ticker* doesn't need this — only a new *field* on already-cached securities.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| PULL stages `[FAIL]` in the log | Bloomberg wasn't running / session dropped | Open Bloomberg, re-run `pipeline.py --push` |
| `storage.py identity` → `TEMPORARY` | SSO creds crept back into `~/.aws/credentials` | Put the long-lived `svc_mucida` key back under `[default]` (no session token) |
| `s3 reach: FAILED` (400) | Region/bucket env doesn't match | `setx AWS_REGION "us-east-1"`, `setx LINKERS_S3_BUCKET "s3-verition-linkers-rates"`, restart VS Code |
| Task `LastTaskResult` ≠ 0 but no log | Script died before logging | Read the newest `logs\pipeline_*.log` — it now records a `FATAL:` line |
| Env var changes not taking effect | VS Code cached old environment | Fully **quit and reopen** VS Code (a new terminal tab isn't enough) |
| A dead bond you want to re-check | It's in the skip list | Delete `cache_intl\daily_empty.txt`, then pull |
| Boss sees stale dashboard | Browser cache | Hard-refresh (Ctrl+F5); the `no-cache` header should prevent this going forward |

---

## How the pieces fit (one paragraph)
The **terminal box** pulls from Bloomberg (the only thing that *must* run here — DRD Redshift
doesn't carry linker prices), rebuilds all series and the dashboards, and pushes the results to
`s3://s3-verition-linkers-rates`. The pull is incremental (new dates only, dead bonds skipped); the
push is incremental (changed files only). Your boss opens the dashboard via a link that always
serves the latest daily build. The only recurring human tasks are: keep Bloomberg open at 4pm, send
a fresh link weekly, and add new linker ISINs when they're issued.
