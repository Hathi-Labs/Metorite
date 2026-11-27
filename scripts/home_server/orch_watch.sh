#!/usr/bin/env bash
# Install or enable the orchestrator watch on the home box (CIV-ORCH, task 4058).
#
#   orch_watch.sh install   copy the scripts and the units. Enables nothing.
#   orch_watch.sh enable    enable and start the timer. OWNER-GATE (`deploy`).
#   orch_watch.sh disable   stop and disable the timer.
#
# ⚠️ `install` takes every file from `origin/main`, never from the working tree:
# it runs `git fetch origin`, then `git show origin/main:<path>` for each file.
# So an install from a branch or a dirty checkout still installs main. It copies
# orch_watch.py, lane_keeper.py and secret_shapes.py (the ONE credential list,
# task 1272) to ~/.local/lib/metorite. orch_watch.py imports the readers of
# lane_keeper.py and scrubs each detail with secret_shapes.py. After a merge
# that changes one of them, run `install` again, or the timers run old copies.
# It never prints a token. It reads no credential.
# Fence: tests/unit/test_orch_watch.py.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HOME}/.local/lib/metorite"
UNITS="${HOME}/.config/systemd/user"
TIMER="metorite-orch-watch.timer"

# Each installed file and its path in the repo.
FILES=(
  "orch_watch.py:scripts/home_server/orch_watch.py:0755"
  "lane_keeper.py:scripts/home_server/lane_keeper.py:0755"
  "secret_shapes.py:packages/acb_common/acb_common/secret_shapes.py:0644"
)
UNIT_FILES=(metorite-orch-watch.service metorite-orch-watch.timer)

install_files() {
  git -C "$HERE" fetch -q origin main
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  mkdir -p "$LIB" "$UNITS"
  for row in "${FILES[@]}"; do
    IFS=: read -r name path mode <<<"$row"
    git -C "$HERE" show "origin/main:$path" > "$TMP/$name"
    install -m "$mode" "$TMP/$name" "$LIB/$name"
  done
  for unit in "${UNIT_FILES[@]}"; do
    git -C "$HERE" show "origin/main:scripts/home_server/$unit" > "$TMP/$unit"
    install -m 0644 "$TMP/$unit" "$UNITS/$unit"
  done
  systemctl --user daemon-reload
  echo "installed from origin/main ($(git -C "$HERE" rev-parse --short origin/main)). The timer is NOT enabled."
  echo "Add lane-orch to ~/lanes/keeper-lanes.txt to let the keeper wake it."
}

case "${1:-}" in
  install) install_files ;;
  enable) systemctl --user enable --now "$TIMER" ;;
  disable) systemctl --user disable --now "$TIMER" ;;
  *) echo "usage: orch_watch.sh install|enable|disable" >&2; exit 64 ;;
esac
