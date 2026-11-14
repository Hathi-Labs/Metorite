#!/usr/bin/env bash
# CIV-28c: hand a PR to the merge bot, then wait for the bot's line. From any machine.
#
#   bash scripts/hand_over.sh <PR> <task> <LOW|STANDARD|HIGH> <note>    hand over
#   bash scripts/hand_over.sh --wait <PR>                                wait for DONE or BACK
#
# Hand over. On the home box, in the bot's checkout (~/lanes/merge-bot-src), it
# fetches main and runs origin/main's lane_handover.sh --pr from a temp file.
# So an old copy in that checkout never runs. HANDOVER_REPO points the script at
# the bot's checkout, so the early refusals read the bot's own HIGH list.
# From the PC it runs over `ssh metorite-home` (docs/home_box.md). On the box
# as user dev, the checkout is local, so it runs with no ssh.
# Exit 0: handed over. Exit 1: refused, and nothing is queued. Exit 2: usage.
# Exit 3: the box cannot take it (no checkout, the bot timer is off, main has
# no --pr mode), or the result is unknown (ssh failed, or the time ran out).
#
# Wait. It reads the queue and the log on the box, one read each HAND_OVER_POLL
# seconds, and holds no lock. The bot gives the nth hand-over of a PR the nth
# DONE or BACK line, so a line of an older hand-over is skipped. A DONE line
# ends the PR. It prints the line. Exit 0: DONE. Exit 1: BACK. Exit 3: no line
# before the deadline (HAND_OVER_WAIT minutes), no hand-over of the PR in the
# queue, or the box cannot be read. A RESOLVED line is printed once, as a note.
#
# Settings:
#   HAND_OVER_HOST [metorite-home]  the ssh host. "local" runs it on this machine.
#   HAND_OVER_SRC [~/lanes/merge-bot-src]  the bot's checkout on that host
#   HAND_OVER_LANE [session@<host name>]  the lane field of the queue line
#   HAND_OVER_TIMEOUT [600]  seconds for the hand-over
#   HAND_OVER_WAIT [120]  minutes for --wait
#   HAND_OVER_POLL [60]  seconds between two reads of --wait
# Fence: tests/unit/test_hand_over.py.
set -uo pipefail

usage() {
  echo "usage: hand_over.sh <PR> <task> <LOW|STANDARD|HIGH> <note>" >&2
  echo "   or: hand_over.sh --wait <PR>" >&2
  exit 2
}
REL="scripts/home_server/lane_handover.sh"
SRC_DEFAULT="$HOME/lanes/merge-bot-src"
HOST="${HAND_OVER_HOST:-}"
if [ -z "$HOST" ]; then
  # On the box as user dev, the bot's checkout is here, so no ssh.
  if [ -e "${HAND_OVER_SRC:-$SRC_DEFAULT}/.git" ]; then HOST=local; else HOST=metorite-home; fi
fi
# The checkout, as the box shell reads it. The default keeps "~" for the box to expand.
if [ -n "${HAND_OVER_SRC:-}" ]; then SRC_Q="$(printf '%q' "$HAND_OVER_SRC")"; else SRC_Q='~/lanes/merge-bot-src'; fi

# run_box <seconds> <script>: run a bash script on the box, or here when HOST is local.
run_box() {
  if [ "$HOST" = local ]; then
    timeout "$1" bash -c "$2"
  else
    timeout "$1" ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "$2"
  fi
}

# ── --wait <PR> ─────────────────────────────────────────────────────────────
read_script() {  # one cheap read: the count of hand-overs, then the result lines
  cat <<EOF
q=~/lanes/merge-bot-queue.tsv; l=~/lanes/merge-bot.log
[ -f "\$q" ] || { echo 0; exit 0; }
awk -F'\t' -v p=$1 '\$1 == p && NF == 7 && length(\$5) == 40 && \$5 ~ /^[0-9a-f]+\$/ && \$6 ~ /^(LOW|STANDARD|HIGH)\$/ {n++} END {print n + 0}' "\$q"
grep -E '^(DONE|BACK|RESOLVED) $1( |\$)' "\$l" 2>/dev/null
exit 0
EOF
}

wait_for() {
  local pr="$1" wait_min="${HAND_OVER_WAIT:-120}" poll="${HAND_OVER_POLL:-60}"
  local deadline out rc n=0 lines done_line results r fails=0 first=1 told=0 resolved
  case "$pr" in ''|*[!0-9]*) echo "hand_over: the PR is a number: got '$pr'" >&2; exit 2 ;; esac
  case "$wait_min$poll" in *[!0-9]*) echo "hand_over: HAND_OVER_WAIT and HAND_OVER_POLL are whole numbers" >&2; exit 2 ;; esac
  deadline=$(( $(date +%s) + wait_min * 60 ))
  while :; do
    out="$(run_box 60 "$(read_script "$pr")")"; rc=$?
    out="$(tr -d '\r' <<< "$out")"
    n="$(head -n 1 <<< "$out")"
    if [ "$rc" != 0 ] || ! [[ "$n" =~ ^[0-9]+$ ]]; then
      fails=$((fails + 1))
      if [ "$first" = 1 ] || [ "$fails" -ge 5 ]; then
        echo "hand_over: cannot read the queue and the log on $HOST (exit $rc). The result of PR $pr is unknown." >&2
        exit 3
      fi
    else
      fails=0
      [ "$n" -gt 0 ] || { echo "hand_over: the queue holds no hand-over of PR $pr. Hand it over first." >&2; exit 3; }
      lines="$(sed -n '2,$p' <<< "$out")"
      # A RESOLVED line: the bot merged main into the branch and pushed it.
      resolved="$(grep -E "^RESOLVED $pr( |$)" <<< "$lines")"
      if [ -n "$resolved" ] && [ "$(wc -l <<< "$resolved")" -gt "$told" ]; then
        tail -n +"$((told + 1))" <<< "$resolved" | sed 's/$/ (note: run git pull --ff-only before your next commit on the branch)/' >&2
        told="$(wc -l <<< "$resolved")"
      fi
      # A DONE line ends the PR for good, as in the bot.
      done_line="$(grep -m 1 -E "^DONE $pr( |$)" <<< "$lines")"
      if [ -n "$done_line" ]; then echo "$done_line"; exit 0; fi
      results="$(grep -E "^(DONE|BACK) $pr( |$)" <<< "$lines")"
      r=0; [ -n "$results" ] && r="$(wc -l <<< "$results")"
      if [ "$r" -ge "$n" ]; then
        sed -n "${n}p" <<< "$results"
        echo "hand_over: the bot sent PR $pr back. The reason is on the PR as a comment too." >&2
        exit 1
      fi
    fi
    first=0
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "hand_over: no DONE or BACK line for PR $pr in $wait_min min (hand-over $n of it waits)." >&2
      exit 3
    fi
    sleep "$poll"
  done
}

if [ "${1:-}" = --wait ]; then
  [ $# -eq 2 ] || usage
  wait_for "$2"
fi

# ── hand over ───────────────────────────────────────────────────────────────
[ $# -ge 4 ] || usage
PR="$1"; TASK="$2"; TIER="$3"; shift 3; NOTE="$*"
LIMIT="${HAND_OVER_TIMEOUT:-600}"
me="$(printf '%s' "${HOSTNAME:-unknown}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-')"
LANE="${HAND_OVER_LANE:-session@${me:-unknown}}"
ARGS_Q="$(printf ' %q' --pr "$PR" "$TASK" "$LANE" "$TIER" "$NOTE")"
OLD_Q="$(printf '%q' "hand_over: origin/main's $REL has no --pr mode, so CIV-28c is not on main yet. Merge the PR yourself.")"

# The box runs origin/main's script, never the copy in the bot's checkout.
handover_script() {
  cat <<EOF
src=$SRC_Q
cd "\$src" 2>/dev/null || { echo "hand_over: no bot checkout at \$src" >&2; exit 3; }
git fetch -q --no-write-fetch-head origin main 2>/dev/null || echo "hand_over: git fetch of main failed, so the script may be old" >&2
tmp="\$(mktemp "\${TMPDIR:-/tmp}/lane_handover.XXXXXX")" || exit 3
trap 'rm -f "\$tmp"' EXIT
git show origin/main:$REL > "\$tmp" 2>/dev/null || { echo "hand_over: cannot read origin/main:$REL" >&2; exit 3; }
grep -q -- '--pr <PR>' "\$tmp" || { echo $OLD_Q >&2; exit 3; }
HANDOVER_REPO="\$src" bash "\$tmp"$ARGS_Q
EOF
}

run_box "$LIMIT" "$(handover_script)"
rc=$?
case "$rc" in
  0) ;;
  1) echo "hand_over: refused. Fix the reason above, push, and run it again." >&2 ;;
  124|255)
    [ "$rc" = 124 ] && why="the hand-over took more than $LIMIT s" || why="ssh lost $HOST"
    echo "hand_over: $why. The result is unknown: the box may have queued it." >&2
    echo "hand_over: check the queue with: HAND_OVER_WAIT=0 bash scripts/hand_over.sh --wait $PR" >&2
    exit 3 ;;
esac
exit "$rc"
