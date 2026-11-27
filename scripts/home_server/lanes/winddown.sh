#!/usr/bin/env bash
# winddown.sh <session-id> [<session-id> ...]
# Wake each lane session and tell it to wind down (LBB-4, task 4042,
# engineering_practice.md section 7b.4). The session ids come as arguments.
# This script holds no Metorite credential. The Stop hook does the PR check.
set -u

if [ "$#" -lt 1 ]; then
  echo "usage: winddown.sh <session-id> [<session-id> ...]" >&2
  exit 2
fi

M_BASE="Wind down now. Finish the step you are on, push your work, and give each task you hold a disposition (review, done, a question, or release_task with a handoff). For each open PR that you pushed, queue it with a live merger (hand_over.sh or a REQUEUE line), or hand it to a named landing lane in a NOTE and in the release_task handoff."

wake() { local s="wd$1"; tmux kill-session -t "$s" 2>/dev/null; tmux new-session -d -s "$s" -x 220 -y 50 "claude attach $1" || return 0; sleep 8; tmux send-keys -t "$s" -l "$2"; sleep 1; tmux send-keys -t "$s" Enter; sleep 4; tmux send-keys -t "$s" C-z; sleep 1; tmux kill-session -t "$s" 2>/dev/null; echo "$(date -u +%H:%M) sent $1"; }

for id in "$@"; do
  wake "$id" "$M_BASE"
done
echo all-sent
