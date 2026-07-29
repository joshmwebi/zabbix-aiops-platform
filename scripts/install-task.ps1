<#
Register the alert-enrichment poller as a Windows scheduled task.

Run from an ELEVATED PowerShell (right-click > Run as administrator):

    powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1

The task runs as SYSTEM: no stored password, no dependency on a human
account staying enabled, and it keeps running when nobody is logged in. It
starts at boot and restarts if the process dies. See ADR-005.

Manage it afterwards:
    Get-ScheduledTask aiops-alert-enrichment          # status
    Start-ScheduledTask aiops-alert-enrichment        # start now
    Stop-ScheduledTask aiops-alert-enrichment         # stop
    Unregister-ScheduledTask aiops-alert-enrichment   # remove
Output: logs\poller.out.log in the repo.

Works on Windows PowerShell 5.1 (what ships with Windows Server) and PS 7.
#>

$ErrorActionPreference = "Stop"

$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "alert-enrichment\poll.py"
$name   = "aiops-alert-enrichment"

# --- preflight ------------------------------------------------------------

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Run this from an elevated PowerShell (Run as administrator) - registering a SYSTEM task requires it."
}
if (-not (Test-Path $python)) {
    Write-Error "No venv at $python. Create it first: python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
}
if (-not (Test-Path $script)) {
    Write-Error "Cannot find $script - run this from inside the repo."
}
if (-not (Test-Path (Join-Path $repo ".env"))) {
    Write-Error "No .env in $repo. The task would start and exit immediately. Copy .env.example to .env and fill it in."
}

$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir "poller.out.log"

# --- register -------------------------------------------------------------

# cmd /c wraps the call so stdout and stderr can be redirected to a file;
# Task Scheduler itself discards console output.
$cmdArgs = '/c ""{0}" "{1}" >> "{2}" 2>&1"' -f $python, $script, $out

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgs -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -MultipleInstances IgnoreNew

# SYSTEM: no password to store or rotate, survives account changes.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "Polls Zabbix, correlates problems into incidents, enriches with an LLM, delivers triage. Repo: $repo" | Out-Null

Write-Host "Registered '$name' (runs as SYSTEM)." -ForegroundColor Green
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 5

Get-ScheduledTask -TaskName $name | Select-Object TaskName, State | Format-Table -AutoSize

Write-Host "Log: $out"
Write-Host "Tail it with:  Get-Content '$out' -Wait -Tail 20"
