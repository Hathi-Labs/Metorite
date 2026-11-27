#!/usr/bin/env bash
# Install or enable the answer watcher on the home box (CIV-14f, task 1731).
#
#   answer_watcher.sh install   copy the script and the units. Enables nothing.
#   answer_watcher.sh enable    enable and start the timer. OWNER-GATE (`deploy`).
#   answer_watcher.sh disable   stop and disable the timer.
#
# ⚠️ `install` copies answer_watcher.py to ~/.local/lib/metorite. After a change
# to it, run `install` again, or the timer runs the old copy.
# ⚠️ It never prints a token. The token comes from ~/.config/metorite/watcher.env
# when that file exists, else from the box token helper (task 4062).
# Fence: tests/unit/test_answer_watcher.py.
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HOME}/.local/lib/metorite"
UNITS="${HOME}/.config/systemd/user"
TIMER="metorite-answer-watcher.timer"

install_files() {
  mkdir -p "$LIB" "$UNITS"
  install -m 0755 "$HERE/answer_watcher.py" "$LIB/answer_watcher.py"
  install -m 0644 "$HERE/metorite-answer-watcher.service" "$UNITS/"
  install -m 0644 "$HERE/metorite-answer-watcher.timer" "$UNITS/"
  systemctl --user daemon-reload
  echo "installed. The timer is NOT enabled. The token goes to ~/.config/metorite/watcher.env (mode 0600)."
}

case "${1:-}" in
  install) install_files ;;
  enable)
    if [ -f "${HOME}/.config/metorite/watcher.env" ]; then
      [ "$(stat -c %a "${HOME}/.config/metorite/watcher.env")" = "600" ] || { echo "watcher.env must be mode 0600" >&2; exit 2; }
    elif [ ! -x "${HOME}/.local/bin/metorite-box-headers" ] || [ ! -f "${HOME}/.config/metorite/box.env" ]; then
      # Task 4062: with no watcher.env, the watcher reads the box token (box_connect.sh).
      echo "no watcher.env and no box token: run scripts/box_connect.sh first" >&2; exit 2
    fi
    systemctl --user enable --now "$TIMER" ;;
  disable) systemctl --user disable --now "$TIMER" ;;
  *) echo "usage: answer_watcher.sh install|enable|disable" >&2; exit 64 ;;
esac
