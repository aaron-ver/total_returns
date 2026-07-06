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
