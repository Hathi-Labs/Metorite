#!/usr/bin/env bash
# Install or enable the orchestrator watch on the home box (CIV-ORCH, task 4058).
#
#   orch_watch.sh install   copy the scripts and the units. Enables nothing.
#   orch_watch.sh enable    enable and start the timer. OWNER-GATE (`deploy`).
#   orch_watch.sh disable   stop and disable the timer.
#
# ⚠️ `install` copies orch_watch.py AND lane_keeper.py to ~/.local/lib/metorite.
# orch_watch.py imports the readers of lane_keeper.py, and the keeper wakes
# lane-orch, so the two copies must match. After a change to either, run
# `install` again, or the timers run the old copies.
# It never prints a token. It reads no credential.
# Fence: tests/unit/test_orch_watch.py.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HOME}/.local/lib/metorite"
UNITS="${HOME}/.config/systemd/user"
TIMER="metorite-orch-watch.timer"

install_files() {
  mkdir -p "$LIB" "$UNITS"
  install -m 0755 "$HERE/orch_watch.py" "$LIB/orch_watch.py"
  install -m 0755 "$HERE/lane_keeper.py" "$LIB/lane_keeper.py"
  install -m 0644 "$HERE/metorite-orch-watch.service" "$UNITS/"
  install -m 0644 "$HERE/metorite-orch-watch.timer" "$UNITS/"
  systemctl --user daemon-reload
  echo "installed. The timer is NOT enabled. Add lane-orch to ~/lanes/keeper-lanes.txt to let the keeper wake it."
}

case "${1:-}" in
  install) install_files ;;
  enable) systemctl --user enable --now "$TIMER" ;;
  disable) systemctl --user disable --now "$TIMER" ;;
  *) echo "usage: orch_watch.sh install|enable|disable" >&2; exit 64 ;;
esac
