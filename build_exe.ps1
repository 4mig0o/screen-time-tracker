$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonPath = $null
$candidateCommands = @(
    (Get-Command python -ErrorAction SilentlyContinue),
    (Get-Command py -ErrorAction SilentlyContinue)
)

foreach ($candidate in $candidateCommands) {
    if (-not $candidate) {
        continue
    }
    try {
        & $candidate.Source -c "import sys; print(sys.executable)" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $pythonPath = $candidate.Source
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonPath) {
    Write-Error "Python was not found. Install Python 3.10 or newer and enable Add Python to PATH."
}

Write-Host "Installing Pillow and PyInstaller..."
& $pythonPath -m pip install --upgrade pillow pyinstaller

Write-Host "Building the executable..."
& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name ScreenTimeTracker `
    screen_time_tracker.py

Write-Host ""
Write-Host "Build complete: $projectRoot\dist\ScreenTimeTracker.exe"
Write-Host "To create a desktop shortcut, run .\create_shortcut.ps1"
