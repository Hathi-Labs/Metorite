#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Watchdog: keeps the Next.js frontend running on port 3001.
    Run this script ONCE in its own terminal window — it will restart
    the frontend automatically if it crashes.

.USAGE
    pwsh -File "C:\Users\VijayRaghavVarada\Documents\Github\Metorite\scripts\frontend_watchdog.ps1"
#>

$FrontendDir = "C:\Users\VijayRaghavVarada\Documents\Github\Metorite\workbench\control_plane"
$Port = 3001

Set-Location $FrontendDir

$attempt = 0
while ($true) {
    $attempt++
    
    # Kill any lingering process on the port before starting
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting frontend on port $Port (attempt $attempt)..." -ForegroundColor Cyan

    & npm run dev

    $exitCode = $LASTEXITCODE
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Frontend exited (code $exitCode). Restarting in 3s..." -ForegroundColor Yellow
    Start-Sleep -Seconds 3
}
