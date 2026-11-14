#!/usr/bin/env bash
# CIV-28c: hand a PR to the merge bot with one command, from any machine.
#
#   bash scripts/hand_over.sh <PR> <task> <LOW|STANDARD|HIGH> <note>
#
# It runs `lane_handover.sh --pr` in the bot's own checkout on the home box
# (~/lanes/merge-bot-src), over `ssh metorite-home` (docs/home_box.md). On the
# box as user dev, that checkout is local, so it runs with no ssh. The box
# checks the PR in a temp worktree and appends the hand-over to the bot's queue.
# The bot then waits for pr-check, runs the gate, and merges. Each BACK goes to
# the PR as a comment.
#
# It prints the result of the box. Exit 0: handed over. Exit 1: refused, and
# nothing is queued. Exit 2: usage. Exit 3: the box cannot be reached, the box
# copy is old, or the hand-over ran out of time.
#
# Settings:
#   HAND_OVER_HOST [metorite-home]  the ssh host. "local" runs it on this machine.
#   HAND_OVER_SRC [~/lanes/merge-bot-src]  the bot's checkout on that host
#   HAND_OVER_LANE [session@<host name>]  the lane field of the queue line
#   HAND_OVER_TIMEOUT [600]  seconds for the whole hand-over
# Fence: tests/unit/test_hand_over.py.
set -uo pipefail

usage() { echo "usage: hand_over.sh <PR> <task> <LOW|STANDARD|HIGH> <note>" >&2; exit 2; }
[ $# -ge 4 ] || usage
PR="$1"; TASK="$2"; TIER="$3"; shift 3; NOTE="$*"
REL="scripts/home_server/lane_handover.sh"
LIMIT="${HAND_OVER_TIMEOUT:-600}"
me="$(printf '%s' "${HOSTNAME:-unknown}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-')"
LANE="${HAND_OVER_LANE:-session@${me:-unknown}}"
args=(--pr "$PR" "$TASK" "$LANE" "$TIER" "$NOTE")
# A box copy from before CIV-28c reads "--pr" as a PR number. Stop with a clear reason.
OLD="the box copy of $REL has no --pr mode. Move the bot's checkout to a main that holds CIV-28c"

HOST="${HAND_OVER_HOST:-}"
if [ -z "$HOST" ]; then
  # On the box as user dev, the bot's checkout is here, so no ssh.
  if [ -f "${HAND_OVER_SRC:-$HOME/lanes/merge-bot-src}/$REL" ]; then HOST=local; else HOST=metorite-home; fi
fi

if [ "$HOST" = local ]; then
  src="${HAND_OVER_SRC:-$HOME/lanes/merge-bot-src}"
  [ -f "$src/$REL" ] || { echo "hand_over: no $REL in $src" >&2; exit 3; }
  grep -q -- '--pr <PR>' "$src/$REL" || { echo "hand_over: $OLD" >&2; exit 3; }
  timeout "$LIMIT" bash "$src/$REL" "${args[@]}"
  rc=$?
else
  # The remote login shell (bash) reads one string, so each argument is quoted.
  if [ -n "${HAND_OVER_SRC:-}" ]; then src="$(printf '%q' "$HAND_OVER_SRC")"; else src='~/lanes/merge-bot-src'; fi
  cmd="cd $src || exit 3; grep -q -- '--pr <PR>' $REL || { echo $(printf '%q' "hand_over: $OLD") >&2; exit 3; }"
  cmd="$cmd; bash $REL$(printf ' %q' "${args[@]}")"
  timeout "$LIMIT" ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "$cmd"
  rc=$?
  [ "$rc" = 255 ] && { echo "hand_over: ssh cannot reach $HOST. Nothing is queued." >&2; exit 3; }
fi
case "$rc" in
  0) ;;
  124) echo "hand_over: the hand-over took more than $LIMIT s and was stopped. Look in the queue before you try again." >&2; exit 3 ;;
  *) [ "$rc" = 1 ] && echo "hand_over: refused. Fix the reason above, push, and run it again." >&2 ;;
esac
exit "$rc"
