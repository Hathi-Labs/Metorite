#!/usr/bin/env bash
# The daily release, started from the home box (CIV-13, D178).
#
# GitHub started the `30 21 * * *` cron of deploy.yml 3.5 to 5.5 h late,
# night after night (run 41949347780 started at 01:33 UTC). The release
# window of deploy.yml is 21:00 to 02:00 UTC, so a late run often found it
# closed and production did not move. The owner decided on 2026-11-05: the
# box starts the release at 21:30 UTC.
#
# A user timer on the box (metorite-nightly-release.timer) runs this script
# at 21:30 UTC. It dispatches deploy.yml on main with nightly=true. A
# nightly dispatch is a nightly run: RELEASE_HOLD and the window hold it, it
# cannot skip the tests, and it does not publish when another nightly run
# shipped that night. The cron stays in deploy.yml as the backup.
#
#   nightly_release.sh run       dispatch, then log the URL of the new run
#   nightly_release.sh install   install the timer for this user (dev)
#
# `run` refuses outside 21:00 to 01:00 UTC. The tests take about 40 min,
# and the window closes at 02:00. A late start of the timer (Persistent=true
# after the box was down) must not ship a release in working hours.
#
# ⚠️ `install` copies this file to ~/.local/lib/metorite. After a change to
# this file, run `install` again, or the timer runs the old copy.
#
# ⚠️ It never prints a token. `gh auth status` writes to /dev/null only.
# Fence: tests/unit/test_nightly_release_box.py.
set -Eeuo pipefail

REPO="Hathi-Labs/Metorite"
UNIT="metorite-nightly-release"
# The poll for the new run. Tests make it short.
POLL_SECONDS="${NIGHTLY_POLL_SECONDS:-60}"
POLL_INTERVAL="${NIGHTLY_POLL_INTERVAL:-5}"
# GitHub's clock and the box's clock can differ. A run created up to this
# many seconds before the dispatch still counts as this dispatch's run.
CLOCK_SLACK=120

log() { printf '%s nightly-release: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $1" >&2; exit "${2:-1}"; }

clock_guard() {
  local hm h
  hm="$(date -u +%H%M)"
  h=$(( 10#${hm:0:2} ))
  if [ "$h" -ge 21 ] || [ "$h" -lt 1 ]; then
    return 0
  fi
  die "it is ${hm} UTC. The nightly release starts only from 21:00 to 01:00 UTC, so it did not dispatch. To release now, dispatch deploy.yml without nightly." 3
}

auth_guard() {
  command -v gh >/dev/null 2>&1 || die "gh is not installed. scripts/home_server/setup.sh installs it." 4
  # Never show this output: it can hold a part of the token.
  if ! gh auth status >/dev/null 2>&1; then
    die "gh is not signed in for $(id -un). Run: gh auth login --scopes workflow. Then run this script again." 4
  fi
}

# Print the URL of the newest nightly dispatch created at or after $1.
find_run() {
  gh run list -R "$REPO" --workflow deploy.yml --event workflow_dispatch --limit 20 \
    --json url,createdAt,displayTitle \
  | python3 -c '
import json, sys
since = sys.argv[1]
try:
    runs = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
mine = [r for r in runs if isinstance(r, dict)
        and str(r.get("createdAt", "")) >= since
        and "nightly" in str(r.get("displayTitle", ""))]
mine.sort(key=lambda r: r["createdAt"])
if mine:
    print(mine[-1].get("url", ""))
' "$1"
}

run_release() {
  clock_guard
  auth_guard
  local since url deadline
  # Record the time BEFORE the dispatch, so the poll finds this run and no
  # older one. The slack covers a box clock that runs fast.
  since="$(date -u -d "@$(( $(date -u +%s) - CLOCK_SLACK ))" +%Y-%m-%dT%H:%M:%SZ)"
  log "dispatch deploy.yml on main with nightly=true"
  gh workflow run deploy.yml -R "$REPO" --ref main -f nightly=true -f reason="nightly release (home box)" \
    || die "gh workflow run failed. Production does not move tonight unless GitHub's cron runs in the window." 5
  deadline=$(( SECONDS + POLL_SECONDS ))
  while :; do
    url="$(find_run "$since" || true)"
    if [ -n "$url" ]; then
      log "started: ${url}"
      return 0
    fi
    [ "$SECONDS" -lt "$deadline" ] || break
    sleep "$POLL_INTERVAL"
  done
  die "GitHub took the dispatch, but no nightly run showed in ${POLL_SECONDS} s. Check: gh run list -R ${REPO} --workflow deploy.yml --event workflow_dispatch" 6
}

install_timer() {
  local here dest lib user
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  # A copy, not the checkout: a branch switch in ~/Metorite must not change
  # or remove the script that the timer runs.
  lib="$HOME/.local/lib/metorite"
  user="$(id -un)"
  mkdir -p "$dest" "$lib"
  install -m 0755 "$here/nightly_release.sh" "$lib/nightly_release.sh"
  install -m 0644 "$here/${UNIT}.service" "$here/${UNIT}.timer" "$dest/"
  # Linger keeps the user manager up with nobody signed in. In WSL the
  # Windows task "Metorite WSL keep-alive" (wsl_setup.ps1) keeps the distro up.
  if ! loginctl show-user "$user" -p Linger 2>/dev/null | grep -q '^Linger=yes$'; then
    sudo loginctl enable-linger "$user"
  fi
  systemctl --user daemon-reload
  systemctl --user enable --now "${UNIT}.timer"
  systemctl --user list-timers "${UNIT}.timer" --no-pager
  log "installed. Logs: journalctl --user -u ${UNIT}.service"
}

main() {
  case "${1:-}" in
    run) run_release ;;
    install) install_timer ;;
    *) echo "usage: $0 run|install" >&2; exit 2 ;;
  esac
}

# Sourcing the file defines the functions and runs none.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then main "$@"; fi
