# Daily refresh for the linkers project — the single command Task Scheduler runs on the
# Bloomberg TERMINAL BOX. Pulls live from the terminal, rebuilds every series, regenerates the
# dashboards, and pushes the consumable artifacts to S3.
#
#   PULL needs the Bloomberg terminal RUNNING and the user LOGGED ON — that's why this is scheduled
#   on the terminal box, not a headless cloud job (DRD Redshift doesn't carry the linker prices;
#   see DATA_SOURCES.md). If the terminal is down, the pull stage fails but the rest still rebuilds
#   from cache — the run is isolated stage-by-stage and never hard-crashes.
#
# Run it by hand any time to test:   powershell -ExecutionPolicy Bypass -File run_daily.ps1
# It is registered as a scheduled task by  register_task.ps1  (run that once).

$ErrorActionPreference = "Continue"
$proj = "C:\Users\azhang\OneDrive - Verition Fund Management LLC\Desktop\total_returns"
Set-Location $proj
$py = Join-Path $proj ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Error "venv python not found at $py — create the venv or fix the path."
    exit 1
}

# Non-secret S3 config. These are normally set once via setx; the guards mean an existing value
# (from setx / your profile) always wins — this is only a self-contained fallback for the task.
if (-not $env:LINKERS_S3_BUCKET) { $env:LINKERS_S3_BUCKET = "s3-verition-linkers-rates" }  # Terraform-provisioned name
if (-not $env:AWS_REGION)        { $env:AWS_REGION        = "us-east-1" }                   # ...and its region
# AWS credentials come from the standard chain (~/.aws/credentials [default]). NO secrets in here.

# Log to a dated file under logs\ (git-ignored) and to the console.
$logdir = Join-Path $proj "logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log   = Join-Path $logdir "pipeline_$stamp.log"

"== linkers daily refresh  $stamp ==" | Tee-Object -FilePath $log
& $py pipeline.py --push *>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
"== done (python exit $code) ==" | Tee-Object -FilePath $log -Append

# Retain only the 30 most recent logs.
Get-ChildItem $logdir -Filter "pipeline_*.log" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
