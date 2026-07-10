# Daily refresh for the linkers project — the single command Task Scheduler runs on the Bloomberg
# TERMINAL BOX. Pulls live from the terminal, rebuilds every series, regenerates the dashboards,
# and pushes the consumable artifacts to S3.
#
#   PULL needs the Bloomberg terminal RUNNING and you LOGGED ON — that's why this is scheduled on
#   the terminal box, not a headless cloud job (DRD Redshift doesn't carry the linker prices; see
#   DATA_SOURCES.md). If the terminal is down, PULL fails but the rest still rebuilds from cache.
#
# Run by hand to test:   powershell -NoProfile -ExecutionPolicy Bypass -File run_daily.ps1
# Registered as a task by  register_task.ps1  (run that once).

$proj = "C:\Users\azhang\OneDrive - Verition Fund Management LLC\Desktop\total_returns"

# --- Create the log FIRST, before anything can fail, so every run leaves evidence -------------
$logdir = Join-Path $proj "logs"
try { New-Item -ItemType Directory -Force -Path $logdir | Out-Null } catch {}
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log   = Join-Path $logdir "pipeline_$stamp.log"
function Log($m) { $m | Tee-Object -FilePath $log -Append }

Log "== linkers daily refresh $stamp =="
Log "proj: $proj"
$code = 1
try {
    Set-Location $proj
    $py = Join-Path $proj ".venv\Scripts\python.exe"
    Log "python: $py  (exists: $(Test-Path $py))"
    if (-not (Test-Path $py)) { throw "venv python not found at $py" }

    # Non-secret S3 config; existing setx values win, this is the self-contained fallback.
    if (-not $env:LINKERS_S3_BUCKET) { $env:LINKERS_S3_BUCKET = "s3-verition-linkers-rates" }
    if (-not $env:AWS_REGION)        { $env:AWS_REGION        = "us-east-1" }
    Log "bucket=$($env:LINKERS_S3_BUCKET)  region=$($env:AWS_REGION)"

    & $py pipeline.py --push *>&1 | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
    Log "== done (python exit $code) =="

    # ---- HOBBES (Desktop\Hobbes): rates screener refresh + portal publish -------------------
    # Runs after the linkers pipeline, each step isolated — a Hobbes failure never affects the
    # linkers refresh. Needs the same logged-in Bloomberg terminal (refresh_store + icap snapshot).
    $hobbes = "C:\Users\azhang\OneDrive - Verition Fund Management LLC\Desktop\Hobbes"
    $hpy = Join-Path $hobbes ".venv\Scripts\python.exe"
    if (Test-Path $hpy) {
        Log "== HOBBES daily =="
        try { & $hpy (Join-Path $hobbes "screener\scripts\refresh_store.py") *>&1 |
              Tee-Object -FilePath $log -Append }
        catch { Log "HOBBES refresh_store FAILED: $_" }
        try { & $hpy (Join-Path $hobbes "screener\scripts\render_static.py") *>&1 |
              Tee-Object -FilePath $log -Append }
        catch { Log "HOBBES render_static FAILED: $_" }
        try { & $hpy (Join-Path $hobbes "Vol Pricer\icap_vol_feed.py") --out (
                Join-Path $hobbes "Vol Pricer\icap_surface.json") *>&1 |
              Tee-Object -FilePath $log -Append }
        catch { Log "HOBBES icap snapshot FAILED: $_" }
        try { & $hpy (Join-Path $hobbes "publish.py") *>&1 | Tee-Object -FilePath $log -Append }
        catch { Log "HOBBES publish FAILED: $_" }
        Log "== HOBBES done =="
    }

    # ---- INFLATION (Desktop\inflation): DTCC tape snapshots -> portal ------------------------
    # Stdlib + blpapi only, so it runs on THIS venv's python (the repo's own venv is empty).
    # DTCC data is public internet; DV01 uses live OIS when the terminal is up, else flat-rate.
    $infl = "C:\Users\azhang\OneDrive - Verition Fund Management LLC\Desktop\inflation"
    if (Test-Path (Join-Path $infl "snapshot.py")) {
        Log "== INFLATION tapes =="
        try { & $py (Join-Path $infl "snapshot.py") *>&1 | Tee-Object -FilePath $log -Append }
        catch { Log "INFLATION snapshot FAILED: $_" }
        try { & $py (Join-Path $infl "publish.py") *>&1 | Tee-Object -FilePath $log -Append }
        catch { Log "INFLATION publish FAILED: $_" }
        Log "== INFLATION done =="
    }
}
catch {
    Log "FATAL: $_"
    $code = 1
}
finally {
    # Retain only the 30 most recent logs.
    Get-ChildItem $logdir -Filter "pipeline_*.log" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue
}
exit $code
