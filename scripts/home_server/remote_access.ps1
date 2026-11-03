# Give the PC session remote access to the home server while it runs Windows
# (task 1534). The PC session then does the rest over SSH: WSL, Ubuntu 24.04
# and scripts/home_server/setup.sh inside it.
#
# Open PowerShell with "Run as administrator" on the home computer, and run:
#
#   irm https://raw.githubusercontent.com/Hathi-Labs/Metorite/mt-38b0acb6-home-server/scripts/home_server/remote_access.ps1 | iex
#
# It installs OpenSSH Server and Tailscale, trusts the SSH keys that
# github.com/vjvarada.keys publishes, and stops the computer from sleeping on
# mains power. It asks for one sign-in: Tailscale. Safe to run again.
#
# Windows PowerShell 5.1. ASCII only, because 5.1 misreads UTF-8 with no BOM.

& {
  $ErrorActionPreference = 'Stop'
  $GhUser = 'vjvarada'
  $BoxName = 'metorite-home'
  $Ts = "$env:ProgramFiles\Tailscale\tailscale.exe"

  function Say([string]$t) { Write-Host "`n== $t" -ForegroundColor Cyan }
  function Note([string]$t) { Write-Host "   $t" }

  $me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Open PowerShell with 'Run as administrator', then run the line again."
  }

  Say 'Install and start OpenSSH Server'
  $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' | Select-Object -First 1
  if ($cap.State -ne 'Installed') {
    Note 'This can take a few minutes.'
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
  }
  Set-Service -Name sshd -StartupType Automatic
  Start-Service -Name sshd
  if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
      -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
  }
  # Each SSH session gets PowerShell, not cmd.
  New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -PropertyType String -Force `
    -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" | Out-Null

  Say "Trust the SSH keys that github.com/$GhUser publishes"
  $keys = ([string](Invoke-RestMethod -UseBasicParsing "https://github.com/$GhUser.keys")).Trim()
  if (-not $keys) { throw "https://github.com/$GhUser.keys lists no key." }
  # An admin's key goes in this one file. sshd ignores it unless only
  # Administrators and SYSTEM can read it. SIDs, so a non-English Windows works.
  $file = "$env:ProgramData\ssh\administrators_authorized_keys"
  Set-Content -Path $file -Value $keys -Encoding Ascii
  icacls.exe $file /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls could not set the rights on $file" }
  Note "$(($keys -split "`n").Count) key(s) trusted."

  Say 'Keep the computer awake on mains power'
  powercfg.exe /change standby-timeout-ac 0
  powercfg.exe /change hibernate-timeout-ac 0

  Say 'Install Tailscale'
  if (-not (Test-Path $Ts)) {
    $msi = Join-Path $env:TEMP 'tailscale-setup.msi'
    Invoke-WebRequest -UseBasicParsing 'https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi' -OutFile $msi
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /quiet /norestart" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "the Tailscale installer stopped with code $($p.ExitCode)" }
    Start-Sleep -Seconds 5
  }

  Say 'Join your Tailscale network (the one sign-in)'
  $state = ''
  try { $state = (& $Ts status --json | ConvertFrom-Json).BackendState } catch { }
  if ($state -eq 'Running') {
    Note 'Already joined.'
    & $Ts set --hostname=$BoxName --unattended=true
  } else {
    Note 'Open the link it shows, and sign in with the SAME account as on your PC.'
    # Unattended: Tailscale stays connected after a restart, before anyone signs in.
    & $Ts up --hostname=$BoxName --unattended=true
  }
  if ($LASTEXITCODE -ne 0) { throw 'Tailscale did not join. Run the line again.' }

  $ip = (& $Ts ip -4 | Select-Object -First 1)
  Say 'Finished'
  Note "Windows user: $env:USERNAME  (sign-in name: $(whoami))"
  Note "Tailscale:    $BoxName  $ip"
  Note ''
  Note "Tell Claude on your PC: remote access is ready, the sign-in name is $(whoami)."
}
