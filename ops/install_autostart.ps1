# Start the supervisor (backend + built dashboard) automatically when you log on to Windows.
#
#   powershell -ExecutionPolicy Bypass -File ops\install_autostart.ps1              # install
#   powershell -ExecutionPolicy Bypass -File ops\install_autostart.ps1 -Uninstall   # remove
#
# Creates a shortcut in your Startup folder (no administrator rights needed). It runs at logon,
# not at boot: the backend's "Log in to Facebook" window needs your desktop session, which a
# Windows service doesn't have. MySQL80 itself is a Windows service and starts at boot; the
# supervisor waits for it.
param(
    [switch]$Uninstall,
    [int]$Port = 8001,
    [int]$DashboardPort = 5173
)
$root = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "Facebook Filter supervisor.lnk"

if ($Uninstall) {
    if (Test-Path $link) { Remove-Item $link; "Removed $link" } else { "Not installed ($link not found)" }
    return
}

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = (Get-Command powershell.exe).Source
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\ops\start_supervisor.ps1`" -Port $Port -DashboardPort $DashboardPort"
$s.WorkingDirectory = $root
$s.WindowStyle = 7  # minimized
$s.Description = "Facebook Filter: backend + dashboard supervisor"
$s.Save()
"Installed $link"
"It starts at your next logon. To start it now:  powershell -ExecutionPolicy Bypass -File `"$root\ops\start_supervisor.ps1`""
