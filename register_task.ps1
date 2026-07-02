# Register the daily linkers refresh as a Windows Scheduled Task. Run this ONCE, in PowerShell,
# from the project directory. Registering a task that runs as YOURSELF does not need admin rights.
# Re-run any time to change the time / update the definition (-Force overwrites).
#
#   .\register_task.ps1                       # default: weekdays at 16:00 local
#   .\register_task.ps1 -At "15:30"           # different time
#   .\register_task.ps1 -TaskName "MyRefresh" # different name
#
# Time note: pick a time YOU ARE STILL LOGGED IN WITH BLOOMBERG OPEN — the pull needs your
# interactive terminal session (that's the LogonType below). 16:00 works: US TIPS closed at 3pm,
# EU/UK long closed. If you leave earlier, set -At to a time you're reliably at your desk.

param(
    [string]$At       = "16:00",   # work hours: US TIPS closed at 3pm, EU long closed, you're still logged in
    [string]$TaskName = "LinkersDailyRefresh"
)

$proj   = "C:\Users\azhang\OneDrive - Verition Fund Management LLC\Desktop\total_returns"
$script = Join-Path $proj "run_daily.ps1"
if (-not (Test-Path $script)) { Write-Error "run_daily.ps1 not found at $script"; exit 1 }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory $proj

# Weekdays only — linker/nominal markets are closed on weekends (a weekend run just finds no new
# data, so this is only to avoid noise). Switch to -Daily if you prefer.
$trigger = New-ScheduledTaskTrigger -Weekly -At $At `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday

# Run as the logged-on user (required: Bloomberg desktop needs the interactive session).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Daily linkers refresh: BBG terminal pull -> build -> render -> S3 push (run_daily.ps1)." | Out-Null

Write-Host "Registered '$TaskName' — weekdays at $At, only when $env:USERNAME is logged on."
Write-Host ""
Write-Host "Test it now:      Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "See last result:  Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Latest log:       Get-Content (Get-ChildItem '$proj\logs\pipeline_*.log' | Sort LastWriteTime | Select -Last 1) -Tail 40"
Write-Host "Remove it:        Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
