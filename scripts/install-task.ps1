<#
Register the alert-enrichment poller as a Windows scheduled task.

Run from the repo root, in the venv or not (the task uses the venv's python
directly, so activation doesn't matter):

    powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1

What it creates: a task named "aiops-alert-enrichment" that starts
poll.py in continuous mode at system startup and restarts it if it dies.
Task Scheduler was chosen over NSSM deliberately: it ships with Windows,
needs no download, and anyone administering the box can find it in
taskschd.msc without knowing this project exists. See ADR-005.

Manage it afterwards:
    Get-ScheduledTask aiops-alert-enrichment          # status
    Start-ScheduledTask aiops-alert-enrichment        # start now
    Stop-ScheduledTask aiops-alert-enrichment         # stop
    Unregister-ScheduledTask aiops-alert-enrichment   # remove
Output lands in logs\poller.out.log next to the repo.
#>

$ErrorActionPreference = "Stop"

$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "alert-enrichment\poll.py"
$name   = "aiops-alert-enrichment"

if (-not (Test-Path $python)) {
    Write-Error "No venv found at $python — create it first: python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt"
}
if (-not (Test-Path (Join-Path $repo ".env"))) {
    Write-Error "No .env in $repo — the task would start but exit immediately. Copy .env.example and fill it in."
}

# cmd /c redirection captures stdout/stderr to a log file; Task Scheduler
# itself discards console output.
$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir "poller.out.log"

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"`"$python`" `"$script`" >> `"$out`" 2>&1`"" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

# Runs as the current user with stored credentials so it works when nobody
# is logged in. You'll be prompted for the account password once.
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Limited -User $env:USERNAME `
    -Password (Read-Host -AsSecureString "Password for $env:USERNAME (stored by Task Scheduler)" | ConvertFrom-SecureString -AsPlainText) `
    -Force | Out-Null

Write-Host "Registered '$name'. Starting it now..."
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State
Write-Host "Output: $out"
