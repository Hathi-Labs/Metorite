#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Watchdog: keeps the Metorite gateway running.
    Run this script ONCE in its own terminal window — it will restart
    the gateway automatically if it crashes.

.USAGE
    pwsh -File "C:\Users\VijayRaghavVarada\Documents\Github\Metorite\scripts\gateway_watchdog.ps1"

    Or right-click the file in Explorer → "Run with PowerShell"
#>

$Root = "C:\Users\VijayRaghavVarada\Documents\Github\Metorite"
$Port = 8001

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Find-FreePort {
    param([int[]]$Candidates = @(8001,8002,8003,8004,8005))
    foreach ($p in $Candidates) {
        if (-not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)) {
            return $p
        }
    }
    return 8001
}

$attempt = 0
while ($true) {
    $attempt++
    $Port = Find-FreePort
    
    # Update .env.local so Next.js always points at the current port
    $envLocal = "$Root\workbench\control_plane\.env.local"
    if (Test-Path $envLocal) {
        (Get-Content $envLocal) -replace "GATEWAY_BASE_URL=http://127\.0\.0\.1:\d+", "GATEWAY_BASE_URL=http://127.0.0.1:$Port" | Set-Content $envLocal
    }
    
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting gateway on port $Port (attempt $attempt)..." -ForegroundColor Cyan
    
    Set-Location $Root
    & uv run uvicorn gateway.main:app --host 0.0.0.0 --port $Port --app-dir apps/services/gateway
    
    $exitCode = $LASTEXITCODE
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Gateway exited (code $exitCode). Restarting in 3s..." -ForegroundColor Yellow
    Start-Sleep -Seconds 3
}
