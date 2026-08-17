#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Start all Metorite dev services: gateway + Next.js frontend.
    Auto-selects a free port for the gateway and updates .env.local.

.USAGE
    cd C:\Users\VijayRaghavVarada\Documents\Github\Metorite
    .\scripts\start_dev.ps1
#>

$Root = "C:\Users\VijayRaghavVarada\Documents\Github\Metorite"
$FrontendDir = "$Root\workbench\control_plane"
$EnvLocal = "$FrontendDir\.env.local"

# ── Find a free port for the gateway ────────────────────────────────────────
function Find-FreePort {
    param([int[]]$Candidates = @(8000,8001,8002,8003,8004,8005,8080))
    foreach ($p in $Candidates) {
        $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        if (-not $busy) { return $p }
    }
    throw "No free port found in candidates: $Candidates"
}

$GatewayPort = Find-FreePort
Write-Host "Gateway port: $GatewayPort" -ForegroundColor Cyan

# ── Update .env.local with the selected gateway port ─────────────────────────
if (Test-Path $EnvLocal) {
    $content = Get-Content $EnvLocal -Raw
    if ($content -match "GATEWAY_BASE_URL=") {
        $content = $content -replace "GATEWAY_BASE_URL=http://127\.0\.0\.1:\d+", "GATEWAY_BASE_URL=http://127.0.0.1:$GatewayPort"
    } else {
        $content += "`nGATEWAY_BASE_URL=http://127.0.0.1:$GatewayPort`n"
    }
    Set-Content $EnvLocal $content -NoNewline
} else {
    "GATEWAY_BASE_URL=http://127.0.0.1:$GatewayPort" | Set-Content $EnvLocal
}
Write-Host "Updated .env.local -> port $GatewayPort" -ForegroundColor Green

# ── Start gateway in a new window ────────────────────────────────────────────
# NOTE: --limit-max-requests 100 --timeout-keep-alive 2 prevents Windows TCP
# CLOSE_WAIT socket exhaustion that causes the gateway to stop accepting connections.
# This is a Windows-specific workaround — Linux (production) does not need it.
Write-Host "Starting gateway on port $GatewayPort ..." -ForegroundColor Yellow
$gatewayCmd = "cd '$Root'; `$env:PYTHONUTF8=1; uv run uvicorn gateway.main:app --host 0.0.0.0 --port $GatewayPort --app-dir apps/services/gateway --limit-max-requests 100 --timeout-keep-alive 2"
Start-Process pwsh -ArgumentList "-NoExit", "-Command", $gatewayCmd -WindowStyle Normal

# Give the gateway ~3s to bind the port before starting the frontend
Start-Sleep -Seconds 3

# ── Find a free port for Next.js ─────────────────────────────────────────────
$FrontendPort = Find-FreePort -Candidates @(3001,3002,3003,3004)
Write-Host "Frontend port: $FrontendPort" -ForegroundColor Cyan

# ── Start Next.js in a new window ────────────────────────────────────────────
Write-Host "Starting Next.js on port $FrontendPort ..." -ForegroundColor Yellow
$nextCmd = "cd '$FrontendDir'; npm run dev -- -p $FrontendPort"
Start-Process pwsh -ArgumentList "-NoExit", "-Command", $nextCmd -WindowStyle Normal

Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Gateway:  http://localhost:$GatewayPort" -ForegroundColor Green
Write-Host "  Frontend: http://localhost:$FrontendPort" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "Both services started in separate windows." -ForegroundColor White
