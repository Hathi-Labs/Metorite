#!/usr/bin/env bash
# CIV-28c: hand a PR to the merge bot, then wait for the bot's line. From any machine.
#
#   bash scripts/hand_over.sh <PR> <task> <LOW|STANDARD|HIGH> <note>    hand over
#   bash scripts/hand_over.sh --wait <PR>                                wait for DONE or BACK
#   bash scripts/hand_over.sh --status <PR>                              one look, no wait
#
# Hand over. On the home box, in the bot's checkout (~/lanes/merge-bot-src), it
# fetches main and runs origin/main's lane_handover.sh --pr from a temp file.
# So an old copy in that checkout never runs. HANDOVER_REPO points the script at
# the bot's checkout, so the early refusals read the bot's own HIGH list.
# From the PC it runs over `ssh metorite-home` (docs/home_box.md). On the box
# as user dev, the checkout is local, so it runs with no ssh.
# Before it hands over, it counts the hand-overs of the PR in the queue. This
# hand-over is number count + 1, and that number goes to a state file on this
# machine (HAND_OVER_STATE/pr-<PR>). --wait and --status read the result of
# that number, so an old BACK never reads as the result of a new hand-over.
# Exit 0: handed over. Exit 1: refused, and a fix can pass it (VERIFY, Visual
# review, a conflict, R1). Exit 4: refused for good (a HIGH tier, a HIGH path, a
# fork, a base that is not main, a protected head): merge it yourself with the
# full loop. Exit 2: usage. Exit 3: the box cannot take it (no checkout, the bot
# timer is off, main has no --pr mode), or the result is unknown (ssh failed,
# or the time ran out). After an unknown result, run --status.
#
# Wait. It reads the queue and the log on the box, one read each HAND_OVER_POLL
# seconds, and holds no lock. The bot gives the nth hand-over of a PR the nth
# DONE or BACK line. A DONE line ends the PR. It prints the line. Exit 0: DONE.
# Exit 1: BACK. Exit 3: no line before the deadline (HAND_OVER_WAIT minutes),
# the hand-over is not in the queue, or the box cannot be read. A RESOLVED line
# is printed once, as a note.
#
# Status. One read. Exit 0: DONE. Exit 1: BACK for this hand-over. Exit 5: in
# the queue, with no result yet (run --wait). Exit 6: not in the queue (hand
# over again, or merge it yourself). Exit 3: the box cannot be read.
#
# Settings:
#   HAND_OVER_HOST [metorite-home]  the ssh host. "local" runs it on this machine.
#   HAND_OVER_SRC [~/lanes/merge-bot-src]  the bot's checkout on that host
#   HAND_OVER_LANE [session@<host name>]  the lane field of the queue line
#   HAND_OVER_STATE [~/.cache/metorite-hand-over]  the number of the last hand-over
#   HAND_OVER_TIMEOUT [600]  seconds for the hand-over
#   HAND_OVER_WAIT [120]  minutes for --wait
#   HAND_OVER_POLL [60]  seconds between two reads of --wait
# Fence: tests/unit/test_hand_over.py.
set -uo pipefail

usage() {
  echo "usage: hand_over.sh <PR> <task> <LOW|STANDARD|HIGH> <note>" >&2
  echo "   or: hand_over.sh --wait <PR>" >&2
  echo "   or: hand_over.sh --status <PR>" >&2
  exit 2
}
REL="scripts/home_server/lane_handover.sh"
SRC_DEFAULT="$HOME/lanes/merge-bot-src"
STATE_DIR="${HAND_OVER_STATE:-$HOME/.cache/metorite-hand-over}"
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

need_pr() { case "$1" in ''|*[!0-9]*) echo "hand_over: the PR is a number: got '$1'" >&2; exit 2 ;; esac; }

# ── One read of the box ─────────────────────────────────────────────────────
read_script() {  # the count of valid hand-overs of the PR, then its result lines
  cat <<EOF
q=~/lanes/merge-bot-queue.tsv; l=~/lanes/merge-bot.log
[ -f "\$q" ] || { echo 0; exit 0; }
awk -F'\t' -v p=$1 '\$1 == p && NF == 7 && length(\$5) == 40 && \$5 ~ /^[0-9a-f]+\$/ && \$6 ~ /^(LOW|STANDARD|HIGH)\$/ {n++} END {print n + 0}' "\$q"
grep -E '^(DONE|BACK|RESOLVED) $1( |\$)' "\$l" 2>/dev/null
exit 0
EOF
}

# read_box <pr>: sets N (hand-overs in the queue), LINES (result lines) and RC.
read_box() {
  local out
  out="$(run_box 60 "$(read_script "$1")")"; RC=$?
  out="$(tr -d '\r' <<< "$out")"
  N="$(head -n 1 <<< "$out")"; LINES="$(sed -n '2,$p' <<< "$out")"
  [ "$RC" = 0 ] && [[ "$N" =~ ^[0-9]+$ ]]
}

# judge <pr>: after read_box. Sets VERDICT (done, back, wait or none), LINE, TARGET.
# TARGET is the number of this machine's last hand-over of the PR, or the newest
# hand-over when this machine made none. The bot gives hand-over k the kth line.
judge() {
  local pr="$1" slot="" results r=0
  [ -f "$STATE_DIR/pr-$pr" ] && slot="$(head -n 1 "$STATE_DIR/pr-$pr" | tr -cd '0-9')"
  TARGET="${slot:-$N}"
  # A DONE line ends the PR for good, as in the bot.
  LINE="$(grep -m 1 -E "^DONE $pr( |$)" <<< "$LINES")"
  if [ -n "$LINE" ]; then VERDICT=done; return; fi
  if [ "$TARGET" -lt 1 ] || [ "$N" -lt "$TARGET" ]; then VERDICT=none; return; fi
  results="$(grep -E "^(DONE|BACK) $pr( |$)" <<< "$LINES")"
  [ -n "$results" ] && r="$(wc -l <<< "$results")"
  if [ "$r" -ge "$TARGET" ]; then LINE="$(sed -n "${TARGET}p" <<< "$results")"; VERDICT=back; return; fi
  VERDICT=wait
}

# ── --status <PR> ───────────────────────────────────────────────────────────
status_of() {
  local pr="$1"
  need_pr "$pr"
  read_box "$pr" || { echo "hand_over: cannot read the queue and the log on $HOST (exit $RC)." >&2; exit 3; }
  judge "$pr"
  case "$VERDICT" in
    done) echo "$LINE"; exit 0 ;;
    back) echo "$LINE"; exit 1 ;;
    wait) echo "WAITING: hand-over $TARGET of PR $pr is in the queue, with no DONE or BACK line yet."; exit 5 ;;
    none) echo "NOT QUEUED: the queue holds $N hand-over(s) of PR $pr, so hand-over ${TARGET/#0/1} is not in it."; exit 6 ;;
  esac
}

# ── --wait <PR> ─────────────────────────────────────────────────────────────
wait_for() {
  local pr="$1" wait_min="${HAND_OVER_WAIT:-120}" poll="${HAND_OVER_POLL:-60}"
  local deadline fails=0 first=1 told=0 resolved
  need_pr "$pr"
  case "$wait_min$poll" in *[!0-9]*) echo "hand_over: HAND_OVER_WAIT and HAND_OVER_POLL are whole numbers" >&2; exit 2 ;; esac
  deadline=$(( $(date +%s) + wait_min * 60 ))
  while :; do
    if ! read_box "$pr"; then
      fails=$((fails + 1))
      if [ "$first" = 1 ] || [ "$fails" -ge 5 ]; then
        echo "hand_over: cannot read the queue and the log on $HOST (exit $RC). The result of PR $pr is unknown." >&2
        exit 3
      fi
    else
      fails=0
      judge "$pr"
      # A RESOLVED line: the bot merged main into the branch and pushed it.
      resolved="$(grep -E "^RESOLVED $pr( |$)" <<< "$LINES")"
      if [ -n "$resolved" ] && [ "$(wc -l <<< "$resolved")" -gt "$told" ]; then
        tail -n +"$((told + 1))" <<< "$resolved" | sed 's/$/ (note: run git pull --ff-only before your next commit on the branch)/' >&2
        told="$(wc -l <<< "$resolved")"
      fi
      case "$VERDICT" in
        done) echo "$LINE"; exit 0 ;;
        back)
          echo "$LINE"
          echo "hand_over: the bot sent PR $pr back. The reason is on the PR as a comment too." >&2
          exit 1 ;;
        none) echo "hand_over: the queue holds no hand-over ${TARGET/#0/1} of PR $pr. Hand it over first." >&2; exit 3 ;;
      esac
    fi
    first=0
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "hand_over: no DONE or BACK line for PR $pr in $wait_min min (hand-over $TARGET of it waits)." >&2
      exit 3
    fi
    sleep "$poll"
  done
}

case "${1:-}" in
  --wait) [ $# -eq 2 ] || usage; wait_for "$2" ;;
  --status) [ $# -eq 2 ] || usage; status_of "$2" ;;
esac

# ── hand over ───────────────────────────────────────────────────────────────
[ $# -ge 4 ] || usage
PR="$1"; TASK="$2"; TIER="$3"; shift 3; NOTE="$*"
need_pr "$PR"
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

# The number of this hand-over: one more than the count in the queue now.
read_box "$PR" || { echo "hand_over: cannot read the queue on $HOST (exit $RC). Nothing was handed over." >&2; exit 3; }
SLOT=$((N + 1))
mkdir -p "$STATE_DIR" && printf '%s\n' "$SLOT" > "$STATE_DIR/pr-$PR" \
  || { echo "hand_over: cannot write $STATE_DIR/pr-$PR. Nothing was handed over." >&2; exit 3; }

run_box "$LIMIT" "$(handover_script)"
rc=$?
case "$rc" in
  0) echo "hand_over: this is hand-over $SLOT of PR $PR. Wait for the bot with: bash scripts/hand_over.sh --wait $PR" ;;
  1) echo "hand_over: refused. Fix the reason above, push, and run it again." >&2 ;;
  4) echo "hand_over: the bot never takes this PR. Merge it yourself with the full loop (next-ticket.md step 7). Do not change the diff to pass a check." >&2 ;;
  124|255)
    [ "$rc" = 124 ] && why="the hand-over took more than $LIMIT s" || why="ssh lost $HOST"
    echo "hand_over: $why. The result is unknown: the box may have queued it." >&2
    echo "hand_over: check it with: bash scripts/hand_over.sh --status $PR" >&2
    exit 3 ;;
esac
exit "$rc"
