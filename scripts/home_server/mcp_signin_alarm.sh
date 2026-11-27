#!/usr/bin/env bash
# Install or enable the sign-in alarm on a dev machine (MCP-G17g, task 4033).
#
#   mcp_signin_alarm.sh install   copy the script and the units. Enables nothing.
#   mcp_signin_alarm.sh enable    enable and start the timer. OWNER-GATE (`deploy`).
#   mcp_signin_alarm.sh disable   stop and disable the timer.
#
# ⚠️ `install` copies mcp_signin_alarm.py to ~/.local/lib/metorite. After a change
# to it, run `install` again, or the timer runs the old copy.
# Channel settings go to ~/.config/metorite/alarm.env (docs/home_box.md §5.3).
# Fence: tests/unit/test_mcp_signin_alarm.py.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HOME}/.local/lib/metorite"
UNITS="${HOME}/.config/systemd/user"
TIMER="metorite-signin-alarm.timer"

install_files() {
  mkdir -p "$LIB" "$UNITS"
  install -m 0755 "$HERE/mcp_signin_alarm.py" "$LIB/mcp_signin_alarm.py"
  install -m 0644 "$HERE/metorite-signin-alarm.service" "$UNITS/"
  install -m 0644 "$HERE/metorite-signin-alarm.timer" "$UNITS/"
  systemctl --user daemon-reload
  echo "installed. The timer is NOT enabled. Channel settings: ~/.config/metorite/alarm.env."
}

case "${1:-}" in
  install) install_files ;;
  enable) systemctl --user enable --now "$TIMER" ;;
  disable) systemctl --user disable --now "$TIMER" ;;
  *) echo "usage: mcp_signin_alarm.sh install|enable|disable" >&2; exit 64 ;;
esac
