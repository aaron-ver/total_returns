# Daily automation (terminal-box scheduler)

The daily refresh runs as a **Windows Scheduled Task on the Bloomberg terminal box**. That's the
right home for it — DRD Redshift is an equities warehouse and does **not** carry the linker/nominal
prices, inflation indices, or bond static (see [DATA_SOURCES.md](DATA_SOURCES.md)), so the Bloomberg
*desktop* terminal is the only place the core data can be pulled. Everything downstream of the pull
(build → export → render → S3 push) is cloud-ready and shareable.

Two ways to run it — pick based on whether you have an off-terminal compute box yet.

### A) All-in-one (today): everything on the terminal box
```
 [terminal box, logged on, BBG running]          [anywhere]
   Task Scheduler 16:00 weekdays
     └─ run_daily.ps1
          └─ pipeline.py --push
               PULL  (BBG terminal) ─┐
               BUILD                 │  isolated, timed, continue-on-failure
               EXPORT (marts)        │
               RENDER (dashboards)   │
               PUSH  → s3://verition-linkers-rates ──► team downloads dashboard_intl.html
               ALERTS (auction reminder)
```

### B) Split (target): terminal only pulls; S3 holds the data; compile runs anywhere
This is the better shape — the data lives in S3 (not one laptop), and the heavy compile runs
headless off the terminal (an AWS box, or any teammate).
```
 [terminal box, work hours, BBG up]              [AWS cron OR any teammate, headless]
   pipeline.py --pull-only                          pipeline.py --from-s3 --push
     PULL  (BBG terminal)                             PULL RAW  (S3 -> cache)   <- no terminal
     PUSH RAW  cache/ + cache_intl/ ──► s3://…/raw ──► BUILD / EXPORT / RENDER
                                                       PUSH  artifacts ──► s3://…  ──► team
```
The only terminal-bound step is the ~2-minute pull; you become the data *courier*, not the
compute host. The compute box in the right column needs AWS infra access (EC2/Fargate) — not set
up yet, but the plumbing (`--pull-only` / `--from-s3`, `storage.py push-raw`/`pull-raw`) is ready.

## Files
- **`run_daily.ps1`** — the wrapper the task runs. Sets non-secret S3 env (bucket/region), runs
  `pipeline.py --push`, tees output to `logs\pipeline_<timestamp>.log`, keeps the last 30 logs.
- **`register_task.ps1`** — registers the scheduled task. Run once.

## One-time setup
From the project directory, in PowerShell:
```powershell
.\register_task.ps1                 # weekdays 18:30 local; or  .\register_task.ps1 -At "19:00"
Start-ScheduledTask -TaskName LinkersDailyRefresh    # test it immediately
Get-Content (Get-ChildItem .\logs\pipeline_*.log | Sort LastWriteTime | Select -Last 1) -Tail 40
```
Registering a task that runs as **you** does not need admin rights.

## Requirements at run time (the trade-offs, stated plainly)
1. **Machine on + you logged on.** The task uses `LogonType Interactive` because the Bloomberg
   desktop API needs your live session. If you're logged off at 16:00, it won't run — it will run
   at the next weekday you're logged on (`-StartWhenAvailable`), or run it by hand. Set `-At` to a
   time you're reliably at your desk with Bloomberg open.
2. **Bloomberg terminal running.** If it's down, only the PULL stage fails; BUILD/EXPORT/RENDER
   still rebuild from the existing cache, so you still get fresh artifacts from the last good pull.
3. **AWS credentials valid — for the S3 push only.** See the dedicated section below.

## AWS credentials for unattended runs
You do **not** need the AWS CLI (the thing you couldn't install without admin). `boto3` reads a plain
text file — `%USERPROFILE%\.aws\credentials` — directly. So the install block is a non-issue. The
only real problem is **credential expiry**:

- **Temporary SSO/portal keys** (the copy-paste block with three values incl. a `aws_session_token`)
  expire in a few hours. Fine for a manual push at your desk; useless for a scheduled 4pm push if you
  pasted them in the morning. When they expire the **push** fails auth — but the local build still
  completes, so nothing is lost; re-paste and re-push.
- **Fix — long-lived IAM access key** (just two values, `aws_access_key_id` + `aws_secret_access_key`,
  **no** session token) in `[default]`. It never expires, needs no CLI, and the scheduled push just
  works. **Ask the AWS/DRD person for a long-lived IAM user access key** for this (or scoped to the
  one bucket).
- **Best — no keys at all:** when the compile moves to an AWS box (split mode B), attach an **IAM
  role** to the instance. boto3 picks it up automatically; there are no keys to rotate or expire. Ask
  for this when you request the EC2/Fargate box.

The push is always an isolated stage — a failed/expired push never blocks the rebuild.

## Operating it
```powershell
Get-ScheduledTaskInfo -TaskName LinkersDailyRefresh     # LastRunTime / LastTaskResult (0 = ok)
Start-ScheduledTask   -TaskName LinkersDailyRefresh     # run now
Unregister-ScheduledTask -TaskName LinkersDailyRefresh -Confirm:$false   # remove
```
Logs live in `logs\` (git-ignored). Each run appends the full stage-by-stage pipeline output with
timings and any `[FAIL]` lines.

## Manual fallback (box was off, or ad-hoc)
```powershell
.\.venv\Scripts\python.exe pipeline.py --push          # full run incl. terminal pull
.\.venv\Scripts\python.exe pipeline.py --no-pull --push # rebuild from cache + push (no terminal)
.\.venv\Scripts\python.exe pipeline.py --no-pull --stage render   # just regenerate dashboards
```
