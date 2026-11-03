# Install Ubuntu 24.04 in WSL on the Windows home server, and run setup.sh in
# it (task 1534). Run it as the Windows admin, after remote_access.ps1, from
# the PC:
#
#   ssh metorite-home-win "irm https://raw.githubusercontent.com/Hathi-Labs/Metorite/main/scripts/home_server/wsl_setup.ps1 | iex"
#
# Set $env:METORITE_REF to a full commit id to fetch setup.sh from that
# commit. The raw branch URL can serve a copy up to 5 minutes old.
#
# The steps:
#   1. .wslconfig: 24 GB, and no idle shutdown of the distro or the VM.
#   2. the distro "metorite" in C:\wsl\metorite, with systemd.
#   3. setup.sh inside it, as root, with no sign-in. It runs in NAT mode,
#      because in mirrored mode the new sshd would fight Windows for port 22.
#   4. mirrored networking, so the tailnet reaches Linux sshd on port 2222.
#   5. a task that starts the distro at boot, and every 5 minutes if it
#      stopped, with or without a signed-in user.
# Safe to run again. Windows PowerShell 5.1, ASCII only.

& {
  $ErrorActionPreference = 'Stop'
  $env:WSL_UTF8 = '1'
  $Distro = 'metorite'
  $Location = 'C:\wsl\metorite'
  $Ref = if ($env:METORITE_REF) { $env:METORITE_REF } else { 'main' }
  $Raw = "https://raw.githubusercontent.com/Hathi-Labs/Metorite/$Ref/scripts/home_server"
  $Cfg = Join-Path $env:USERPROFILE '.wslconfig'
  $WslVm = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'  # the Hyper-V VM creator id of WSL
  $TaskName = 'Metorite WSL keep-alive'

  function Say([string]$t) { Write-Host "`n== $t" }
  function Note([string]$t) { Write-Host "   $t" }
  # wsl.exe writes progress to stderr. Under 'Stop', PowerShell 5.1 can turn
  # that into an error, so each wsl.exe call runs under 'Continue', and the
  # exit code decides.
  function Wsl {
    $ErrorActionPreference = 'Continue'
    & wsl.exe @args
  }
  function InDistro([string]$cmd) {
    Wsl -d $Distro -u root -e bash -c $cmd
    if ($LASTEXITCODE -ne 0) { throw "this command failed in the distro: $cmd" }
  }
  function Write-WslConfig([bool]$mirrored) {
    $lines = @('[wsl2]', 'memory=24GB', 'swap=16GB', 'vmIdleTimeout=-1')
    if ($mirrored) { $lines += 'networkingMode=mirrored' }
    $lines += @('', '[general]', 'instanceIdleTimeout=-1', '', '[experimental]', 'autoMemoryReclaim=gradual')
    if ($mirrored) { $lines += 'hostAddressLoopback=true' }
    $text = ($lines -join "`r`n") + "`r`n"
    $old = if (Test-Path $Cfg) { [IO.File]::ReadAllText($Cfg) } else { '' }
    if ($old -eq $text) { return $false }
    if ($old) { Copy-Item $Cfg "$Cfg.bak-$(Get-Date -Format yyyyMMdd-HHmmss)" }
    [IO.File]::WriteAllText($Cfg, $text)
    return $true
  }
  # No Windows drives and no Windows programs in the distro, as in the PC's
  # metorite-ci distro (scripts/ci_pc/provision.sh). CI runs branch code, and
  # the agents have sudo: with interop on, either could act as this Windows
  # admin. wsl.conf goes in on stdin, because /mnt/c is not there.
  function Set-WslConf([bool]$withUser) {
    $text = "[boot]`nsystemd=true`n`n[network]`nhostname=metorite-home`n`n[automount]`nenabled=false`n`n[interop]`nenabled=false`nappendWindowsPath=false`n"
    if ($withUser) { $text += "`n[user]`ndefault=dev`n" }
    $now = (Wsl -d $Distro -u root -e sh -c 'cat /etc/wsl.conf 2>/dev/null; true') -join "`n"
    if (($now + "`n") -eq $text) { return $false }
    $ErrorActionPreference = 'Continue'
    $text | & wsl.exe -d $Distro -u root -e sh -c "tr -d '\r' > /etc/wsl.conf"
    if ($LASTEXITCODE -ne 0) { throw 'could not write /etc/wsl.conf' }
    return $true
  }
  function Restart-Distro {
    Wsl --terminate $Distro | Out-Null
    Start-Sleep -Seconds 3
    InDistro 'test -d /run/systemd/system'
  }

  Say 'WSL settings, NAT mode while setup.sh runs'
  if (-not (@(Wsl -l -q) | ForEach-Object { "$_".Trim() } | Where-Object { $_ -eq $Distro })) {
    [void](Write-WslConfig $false)
    Say "Install Ubuntu 24.04 as '$Distro' in $Location"
    New-Item -ItemType Directory -Force -Path $Location | Out-Null
    Wsl --install Ubuntu-24.04 --name $Distro --location $Location --no-launch --web-download
    if ($LASTEXITCODE -ne 0) { throw "wsl --install stopped with code $LASTEXITCODE" }
  } else {
    Note "The distro '$Distro' is already there."
  }
  if (Write-WslConfig $false) {
    Note 'Settings changed: restart WSL.'
    Wsl --shutdown
    Start-Sleep -Seconds 5
  }

  Say 'systemd on, hostname metorite-home, no Windows PATH in Linux'
  if (Set-WslConf $false) { Restart-Distro }
  InDistro 'test -d /run/systemd/system'
  Note 'systemd runs.'

  Say "setup.sh inside the distro (about 10 minutes), from $Ref"
  InDistro "curl -fsSL '$Raw/setup.sh' -o /root/setup.sh && SETUP_NO_SIGNIN=1 bash /root/setup.sh"

  Say 'Default user dev, mirrored networking, port 2222 open'
  [void](Set-WslConf $true)
  if (-not (Get-NetFirewallHyperVRule -Name 'Metorite-WSL-sshd' -ErrorAction SilentlyContinue)) {
    New-NetFirewallHyperVRule -Name 'Metorite-WSL-sshd' -DisplayName 'Metorite WSL sshd 2222' `
      -Direction Inbound -VMCreatorId $WslVm -Protocol TCP -LocalPorts 2222 | Out-Null
  }
  if (-not (Get-NetFirewallRule -Name 'Metorite-WSL-sshd' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'Metorite-WSL-sshd' -DisplayName 'Metorite WSL sshd 2222' `
      -Direction Inbound -Protocol TCP -LocalPort 2222 -Action Allow | Out-Null
  }
  [void](Write-WslConfig $true)
  Wsl --shutdown
  Start-Sleep -Seconds 5
  InDistro 'systemctl is-active ssh >/dev/null'
  $ok = Test-NetConnection -ComputerName 127.0.0.1 -Port 2222 -InformationLevel Quiet -WarningAction SilentlyContinue
  if (-not $ok) { throw 'Linux sshd does not answer on port 2222.' }
  Note 'Linux sshd answers on port 2222.'

  Say "Task '$TaskName': start the distro at boot, and every 5 minutes"
  $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wsl.exe" -Argument "-d $Distro -u root -e /bin/true"
  $boot = New-ScheduledTaskTrigger -AtStartup
  $every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
  # S4U: runs with nobody signed in, and stores no password. The name comes
  # from the token: in an SSH session $env:USERDOMAIN names no real account.
  $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  $who = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest
  $how = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($boot, $every) -Principal $who `
    -Settings $how -Force | Out-Null
  Note 'Registered.'

  Say 'Finished'
  Wsl -l -v
  Note "From the PC: ssh metorite-home (user dev) and ssh metorite-home-admin (user acb), port 2222."
}
