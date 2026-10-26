$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$exePath = Join-Path $projectRoot "ScreenTimeTracker.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    $exePath = Join-Path $projectRoot "dist\ScreenTimeTracker.exe"
}

if (-not (Test-Path -LiteralPath $exePath)) {
    Write-Error "没有找到 ScreenTimeTracker.exe，请先执行 .\build_exe.ps1"
}

$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutName = ([string][char]0x5C4F) + ([char]0x5E55) + ([char]0x4F7F) + ([char]0x7528) + ([char]0x65F6) + ([char]0x957F) + ".lnk"
$shortcutPath = Join-Path $desktopPath $shortcutName
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = Split-Path -Parent $exePath
$shortcut.IconLocation = "$exePath,0"
$shortcut.Description = "Screen time tracker"
$shortcut.Save()

$startupPath = [Environment]::GetFolderPath("Startup")
$collectorShortcutPath = Join-Path $startupPath "ScreenTimeTracker Collector.lnk"
$collectorShortcut = $shell.CreateShortcut($collectorShortcutPath)
$collectorShortcut.TargetPath = $exePath
$collectorShortcut.Arguments = "--collector"
$collectorShortcut.WorkingDirectory = Split-Path -Parent $exePath
$collectorShortcut.IconLocation = "$exePath,0"
$collectorShortcut.Description = "Background screen time collector"
$collectorShortcut.Save()

Write-Host "Desktop shortcut created: $shortcutPath"
Write-Host "Startup collector shortcut created: $collectorShortcutPath"
