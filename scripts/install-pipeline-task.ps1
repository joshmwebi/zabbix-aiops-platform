<#
Register the warehouse refresh as a Windows scheduled task.

Run from an ELEVATED PowerShell (Run as administrator), from the repo root:

    powershell -ExecutionPolicy Bypass -File scripts\install-pipeline-task.ps1

Creates "aiops-warehouse-refresh": runs scripts\refresh.py daily, which
extracts new Zabbix telemetry and then rebuilds the dbt models. Output goes
to logs\refresh.log.

Runs as SYSTEM — no stored password, survives the installing account being
disabled, and works when nobody is logged in. SYSTEM must be able to read
the Snowflake private key; it can, by default, for files under C:\aiops.

Daily rather than hourly because the marts are built at a daily grain, so a
more frequent refresh would cost warehouse credits without changing any
answer. Change -At below if a different time suits.

Manage it:
    Get-ScheduledTask aiops-warehouse-refresh
    Start-ScheduledTask aiops-warehouse-refresh      # run now, to test
    Unregister-ScheduledTask aiops-warehouse-refresh
#>

$ErrorActionPreference = "Stop"

$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "scripts\refresh.py"
$name   = "aiops-warehouse-refresh"
$runAt  = "06:00"

# --- preflight ------------------------------------------------------------

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Run this from an elevated PowerShell - registering a SYSTEM task requires it."
}
foreach ($p in @($python, $script, (Join-Path $repo ".env"))) {
    if (-not (Test-Path $p)) { Write-Error "Missing: $p" }
}

$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir "refresh.log"

# --- register -------------------------------------------------------------

# cmd /c wraps the call so stdout and stderr can be appended to a log file;
# Task Scheduler discards console output on its own.
$cmdArgs = '/c ""{0}" "{1}" >> "{2}" 2>&1"' -f $python, $script, $out

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgs -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "Extracts Zabbix trends into Snowflake bronze and rebuilds dbt silver/gold models. Repo: $repo" | Out-Null

Write-Host "Registered '$name' - daily at $runAt, running as SYSTEM." -ForegroundColor Green
Write-Host "Log: $out"
Write-Host ""
Write-Host "Test it now with:  Start-ScheduledTask $name"
Write-Host "Then watch:        Get-Content '$out' -Wait -Tail 30"
