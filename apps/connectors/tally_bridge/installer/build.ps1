# Build the Windows installer of the Tally bridge (TLY-5 PR B).
#
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Version 0.1.0
#
# Needs: uv, and Inno Setup 6 (ISCC.exe). Output: dist\TallyBridgeSetup-<version>.exe.
# The build is unsigned. Signing waits for the owner's certificate.
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Only the locked versions (uv.lock, with hashes): the program, pywin32 and
# the build group with PyInstaller. Never the newest versions on PyPI.
uv sync --frozen --group build --python 3.12
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
uv run --frozen --group build pyinstaller installer\tally_bridge.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$env:TALLY_BRIDGE_VERSION = $Version
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 is not installed: $iscc" }
& $iscc installer\tally_bridge.iss
Write-Host "Built dist\TallyBridgeSetup-$Version.exe"
