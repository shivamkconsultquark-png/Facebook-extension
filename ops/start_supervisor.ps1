# Starts ops/supervisor.py in a hidden console window (the hidden console is what lets the supervisor
# stop the backend gracefully with Ctrl+Break). Used by the Startup-folder shortcut that
# install_autostart.ps1 creates; can also be run by hand.
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8001,
    [int]$DashboardPort = 5173
)
$python = Join-Path $Root ".venv\Scripts\python.exe"
$supervisor = Join-Path $Root "ops\supervisor.py"
if (-not (Test-Path $python)) { throw "No virtualenv Python at $python" }
$arguments = @("`"$supervisor`"", "--root", "`"$Root`"", "--port", $Port)
if ($DashboardPort -gt 0) { $arguments += @("--dashboard-port", $DashboardPort) }
Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden
