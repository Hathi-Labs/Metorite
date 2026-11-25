# Build the Windows installer of the Tally bridge (TLY-5 PR B).
#
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Version 0.1.0
#
# Needs: uv, and Inno Setup 6 (ISCC.exe). Output: dist\TallyBridgeSetup-<version>.exe.
# The build is unsigned. Signing waits for the owner's certificate.
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

uv venv .build-venv --python 3.12
& .\.build-venv\Scripts\python.exe -m ensurepip
& .\.build-venv\Scripts\python.exe -m pip install --quiet . "pyinstaller>=6,<7" "pywin32>=306"
& .\.build-venv\Scripts\pyinstaller.exe installer\tally_bridge.spec --noconfirm --distpath dist --workpath build

$env:TALLY_BRIDGE_VERSION = $Version
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 is not installed: $iscc" }
& $iscc installer\tally_bridge.iss
Write-Host "Built dist\TallyBridgeSetup-$Version.exe"
