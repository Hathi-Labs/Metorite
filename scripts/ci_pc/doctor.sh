#!/usr/bin/env bash
# Check, and with --fix repair, what local CI and local Docker need. From
# Windows (Git Bash).
#
#   bash scripts/ci_pc/doctor.sh          # report only, changes nothing
#   bash scripts/ci_pc/doctor.sh --fix    # repair what it safely can
#
# WHY. Local CI, Docker Desktop (the dev stack, the R8 scratch databases) and
# every WSL2 distro share ONE virtual machine. On 2026-10-20 one CI run took
# its memory, and WSL, git and Docker hung for every session on the PC.
# Recovery took four steps that nobody should rediscover under pressure:
#   1. `wsl --shutdown`. It did NOT stop the stuck VM that day;
#   2. restart the WSL service, which needs administrator rights (UAC);
#   3. restart Docker Desktop, which lost its VM;
#   4. start again the containers that have no restart policy.
# --fix does them in that order, and stops at the first one that works.
#
# ⚠️ --fix restarts SHARED things. Every session on this PC loses WSL and
# Docker for a minute or two. Run it when they are already broken, which is
# when the report says so.
#
# Every probe has a timeout. A doctor that hangs on the thing it checks is
# no use on the day it matters.
set -u
cd "$(dirname "$0")/../.."
FIX=false; [ "${1:-}" = "--fix" ] && FIX=true
DISTRO=metorite-ci
STATE="$HOME/.metorite"
SNAPSHOT="$STATE/docker-running.txt"
mkdir -p "$STATE"
DOCKER_EXE="/c/Program Files/Docker/Docker/Docker Desktop.exe"
problems=0

ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; problems=$((problems + 1)); }
note() { printf '  ..    %s\n' "$*"; }

wsl_answers()    { timeout 25 wsl.exe -l -q >/dev/null 2>&1; }
ci_answers()     { timeout 60 wsl.exe -d "$DISTRO" -e true >/dev/null 2>&1; }
docker_answers() { timeout 15 docker info >/dev/null 2>&1; }

restart_wsl() {
  note "wsl --shutdown"
  timeout 120 wsl.exe --shutdown >/dev/null 2>&1
  sleep 5
  wsl_answers && ci_answers && return 0
  note "WSL still stuck. Restarting the WSL service: accept the UAC prompt."
  powershell -NoProfile -Command 'Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile","-Command","Stop-Process -Name wslservice -Force -ErrorAction SilentlyContinue; Start-Sleep 5; Start-Service WSLService"' \
    >/dev/null 2>&1 || { note "elevation was refused"; return 1; }
  sleep 5
  wsl_answers && ci_answers
}

# Stop Docker Desktop, terminate ONLY its own distro, start it again.
# Measured 2026-10-20: after the WSL service restart, Docker Desktop waited
# 11 minutes for an engine whose distro never came up. Terminating just
# `docker-desktop` brought it back in 10 seconds, and other distros (local
# CI included) kept running.
restart_docker_desktop() {
  note "restarting Docker Desktop and its docker-desktop distro (up to 5 minutes)"
  powershell -NoProfile -Command 'Get-Process "Docker Desktop","com.docker.backend","com.docker.build" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue' >/dev/null 2>&1
  sleep 3
  timeout 60 wsl.exe --terminate docker-desktop >/dev/null 2>&1
  sleep 3
  ("$DOCKER_EXE" >/dev/null 2>&1 &)
  for _ in $(seq 1 60); do docker_answers && return 0; sleep 5; done
  return 1
}

restore_containers() {
  [ -s "$SNAPSHOT" ] || { note "no snapshot of running containers to restore"; return 0; }
  local missing
  missing="$(comm -23 <(sort "$SNAPSHOT") <(timeout 20 docker ps --format '{{.Names}}' | sort))"
  for c in $missing; do
    timeout 30 docker start "$c" >/dev/null 2>&1 && note "started $c" || note "could not start $c"
  done
}

echo "== WSL"
if wsl_answers; then ok "the WSL service answers"
else
  bad "the WSL service does not answer (HCS_E_CONNECTION_TIMEOUT or a hang)"
  $FIX && { restart_wsl && ok "WSL recovered" || bad "WSL did not recover. Reboot Windows."; }
fi

echo "== the $DISTRO distro"
if ci_answers; then
  ok "it boots"
  timeout 30 wsl.exe -d "$DISTRO" -e bash -c '
    a=$(awk "/^MemAvailable:/ {print int(\$2/1048576)}" /proc/meminfo)
    echo "mem $a"; echo "cores $(nproc)"
    systemctl is-active --quiet docker && echo "docker active" || echo "docker DOWN"
    echo "disk $(df -BG --output=avail / | tail -1 | tr -d " G")"' 2>/dev/null | tr -d '\0\r' > "$STATE/ci-probe.txt"
  mem=$(awk '/^mem/ {print $2}' "$STATE/ci-probe.txt"); cores=$(awk '/^cores/ {print $2}' "$STATE/ci-probe.txt")
  grep -q "docker active" "$STATE/ci-probe.txt" && ok "its Docker engine runs" || bad "its Docker engine is down"
  if [ "${mem:-0}" -ge 12 ]; then ok "the VM has ${mem} GB free, ${cores} cores (local CI picks shards from this)"
  else bad "the VM has only ${mem:-?} GB free. Local CI needs 12 or more. Something else holds it"; fi
elif timeout 25 wsl.exe -l -q 2>/dev/null | tr -d '\0\r' | grep -qx "$DISTRO"; then
  bad "it does not boot"
  $FIX && { restart_wsl && ok "it boots now" || bad "still down. Reboot Windows."; }
else
  bad "it does not exist. Run: bash scripts/ci_pc/setup.sh"
fi

echo "== Docker Desktop (the dev stack, the R8 scratch databases)"
if docker_answers; then
  ok "it answers"
  timeout 20 docker ps --format '{{.Names}}' > "$SNAPSHOT.tmp" 2>/dev/null && mv "$SNAPSHOT.tmp" "$SNAPSHOT" \
    && note "remembered $(wc -l < "$SNAPSHOT") running containers, so --fix can start them again"
else
  bad "it does not answer"
  if $FIX; then
    if restart_docker_desktop; then ok "Docker Desktop recovered"; restore_containers
    else bad "Docker Desktop did not recover. Quit it from the tray, then start it."; fi
  fi
fi

echo "== disk"
free_c=$(df -BG /c 2>/dev/null | awk 'NR==2 {gsub("G","",$4); print $4}')
[ "${free_c:-0}" -ge 20 ] && ok "C: has ${free_c} GB free" || bad "C: has only ${free_c:-?} GB free. The WSL disks grow and never shrink by themselves"

echo
if [ $problems -eq 0 ]; then echo "doctor: all good. Run: bash scripts/ci_local.sh"
elif $FIX; then echo "doctor: $problems problem(s) seen. Run it again without --fix to check the result."
else echo "doctor: $problems problem(s). To repair: bash scripts/ci_pc/doctor.sh --fix"; fi
[ $problems -eq 0 ]
