#!/usr/bin/env bash
# CIV-28 (D218 point 2): the merge bot. ONE pass of it merges each queued PR
# that is green on GitHub and whose trial merge with today's main is clean.
# A timer calls it every 2 minutes (systemd/metorite-merge-bot.timer).
#
# It uses no model and no Metorite credential. It never pushes, never
# touches main locally, never passes --admin or --auto, and never dispatches or
# reruns deploy.yml. It does not test a PR again after main moves (D218, D176):
# the gate is the trial merge plus scripts/premerge.sh.
#
# Per PR of ~/lanes/merge-queue.tsv (PR, task, worktree, lane, note) that has
# no line in ~/lanes/merge-queue.log yet:
#   merged -> DONE <pr> <sha>     closed -> BACK <pr> closed
#   a required check red          -> BACK <pr> red: <names>
#   a required check missing or pending -> skip, the next pass looks again
#   a required check cancelled    -> gh run rerun --failed ONCE, then skip
#   the trial merge conflicts     -> BACK <pr> conflict: <files>
#   premerge.sh exits 1           -> BACK <pr> premerge: <tail>
#   else gh pr ready (if draft), gh pr merge --squash, then DONE <pr> <sha>.
# A lane session reads the log, closes each DONE task, and returns each BACK.
#
# Environment (defaults in brackets):
#   MERGE_QUEUE [~/lanes/merge-queue.tsv]   MERGE_LOG [~/lanes/merge-queue.log]
#   MERGE_BOT_STATE [~/.cache/metorite-merge-bot]  lock file and rerun marks
#   MERGE_BOT_WORKDIR [~/lanes/merge-bot-wt]  a checkout of the repo, bot-owned
#   PREMERGE_CMD [timeout 900 bash scripts/premerge.sh]
#   GH_CMD [gh]   MERGE_BOT_DRY_RUN=1: print actions, call no write command
# Fence: tests/unit/test_merge_bot.py.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUEUE="${MERGE_QUEUE:-$HOME/lanes/merge-queue.tsv}"
LOG="${MERGE_LOG:-$HOME/lanes/merge-queue.log}"
STATE="${MERGE_BOT_STATE:-$HOME/.cache/metorite-merge-bot}"
WORKDIR="${MERGE_BOT_WORKDIR:-$HOME/lanes/merge-bot-wt}"
PREMERGE_CMD="${PREMERGE_CMD:-timeout 900 bash scripts/premerge.sh}"
GH_CMD="${GH_CMD:-gh}"
DRY="${MERGE_BOT_DRY_RUN:-0}"

say() { echo "merge_bot: $*" >&2; }
gh_() { $GH_CMD "$@"; }
one_line() { tr '\t\r\n' '   ' | sed 's/  */ /g; s/^ //; s/ $//'; }

mkdir -p "$STATE" "$(dirname "$LOG")"
# One pass at a time. flock where it exists, else an atomic mkdir.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$STATE/lock"
  flock -n 9 || { say "another pass runs"; exit 0; }
else
  mkdir "$STATE/lock.d" 2>/dev/null || { say "another pass runs"; exit 0; }
  trap 'rmdir "$STATE/lock.d" 2>/dev/null' EXIT
fi
[ -f "$QUEUE" ] || { say "no queue"; exit 0; }

record() {  # record <line>: one result line in the log. A dry run only prints it.
  if [ "$DRY" = 1 ]; then say "DRY RUN would log: $*"; else printf '%s\n' "$*" >> "$LOG"; fi
}
# A PR is pending when the queue holds more hand-over lines of it than the log
# holds result lines (DONE or BACK). The nth hand-over gets the nth result, so a
# lane can hand a PR over again after a BACK. A DONE ends the PR for good: a stray
# later hand-over line of a merged PR is skipped, and it never gets a second merge.
pending() {
  local q r
  if [ -f "$LOG" ] && grep -qE "^DONE $1( |$)" "$LOG"; then return 1; fi
  q="$(awk -F'	' -v p="$1" '$1==p' "$QUEUE" | wc -l)"
  r=0
  [ -f "$LOG" ] && r="$(grep -cE "^(DONE|BACK) $1( |$)" "$LOG")"
  [ "$q" -gt "$r" ]
}

required=""
load_required() {
  [ -n "$required" ] && return 0
  required="$(gh_ api 'repos/{owner}/{repo}/branches/main/protection/required_status_checks' --jq '.contexts[]' 2>/dev/null | tr -d '\r')" || required=""
  [ -n "$required" ]
}

view() {  # view <pr>: prints "STATE<TAB>isDraft<TAB>merge sha"
  gh_ pr view "$1" --json state,isDraft,mergeCommit \
    --jq '[.state, (.isDraft|tostring), (.mergeCommit.oid // "")] | @tsv' 2>/dev/null | tr -d '\r'
}

# check_status <pr>: prints "ready", "wait", "red: <names>", or "rerun <run id>".
# A required name with no row is not ready. "skipping" counts as a pass.
check_status() {
  local pr="$1" rows name hit bad="" pending=0 rerun="" st
  rows="$(gh_ pr checks "$pr" --json name,bucket,link \
    --jq '.[] | [.name, .bucket, .link] | @tsv' 2>/dev/null | tr -d '\r' || true)"
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    hit="$(awk -F'\t' -v n="$name" '$1==n {print $2 "\t" $3}' <<< "$rows")"
    if [ -z "$hit" ]; then pending=1; continue; fi
    st="$(cut -f1 <<< "$hit")"
    if grep -qx 'fail' <<< "$st"; then bad="$bad${bad:+, }$name"
    elif grep -qx 'cancel' <<< "$st"; then
      [ -n "$rerun" ] || rerun="$(awk -F'\t' '$1=="cancel" {print $2; exit}' <<< "$hit" | sed -E 's#.*/runs/([0-9]+).*#\1#')"
      pending=1
    elif grep -qx 'pending' <<< "$st"; then pending=1
    fi
  done <<< "$required"
  if [ -n "$bad" ]; then echo "red: $bad"
  elif [ -n "$rerun" ]; then echo "rerun $rerun"
  elif [ "$pending" = 1 ]; then echo wait
  else echo ready; fi
}

# gate <pr>: trial merge, then premerge. Prints nothing and returns 0 to merge.
# On a failure it prints the BACK reason and returns 1.
gate() {
  local pr="$1" ref="refs/merge-bot/pr-$1" out files try main_before main_after tail_
  if [ ! -e "$WORKDIR/.git" ]; then
    git -C "$ROOT" worktree add -q --detach "$WORKDIR" >/dev/null 2>&1 || { echo "premerge: no workdir at $WORKDIR"; return 1; }
  fi
  cd "$WORKDIR" || { echo "premerge: cannot enter $WORKDIR"; return 1; }
  git fetch -q origin "+refs/heads/main:refs/remotes/origin/main" "+refs/pull/$pr/head:$ref" 2>/dev/null \
    || { echo "premerge: git fetch failed"; return 1; }
  if ! out="$(git merge-tree --write-tree --name-only origin/main "$ref" 2>&1)"; then
    files="$(sed -n '2,$p' <<< "$out" | sed '/^$/,$d' | tr '\n' ' ')"
    echo "conflict: ${files:-see git merge-tree}"; return 1
  fi
  for try in 1 2 3; do
    git checkout -q --detach "$ref" 2>/dev/null || { echo "premerge: checkout failed"; return 1; }
    main_before="$(git rev-parse origin/main)"
    if ! out="$(bash -c "$PREMERGE_CMD" 2>&1)"; then
      tail_="$(tail -n 3 <<< "$out" | one_line)"
      echo "premerge: ${tail_:-exit 1}"; return 1
    fi
    git fetch -q origin "+refs/heads/main:refs/remotes/origin/main" 2>/dev/null || true
    main_after="$(git rev-parse origin/main)"
    [ "$main_before" = "$main_after" ] && return 0
    say "PR $pr: main moved during premerge (try $try)"
  done
  echo "main moved during premerge"; return 1
}

handle() {
  local pr="$1" v state draft sha st reason out run mark
  v="$(view "$pr")" || { say "PR $pr: gh cannot read it, next pass"; return; }
  [ -n "$v" ] || return
  state="$(cut -f1 <<< "$v")"; draft="$(cut -f2 <<< "$v")"; sha="$(cut -f3 <<< "$v")"
  case "$state" in
    MERGED) record "DONE $pr $sha"; return ;;
    CLOSED) record "BACK $pr closed"; return ;;
  esac
  load_required || { say "PR $pr: cannot read the required checks, next pass"; return; }
  st="$(check_status "$pr")"
  case "$st" in
    wait) say "PR $pr: required checks not ready"; return ;;
    red:*) record "BACK $pr $st"; return ;;
    rerun*)
      run="${st#rerun }"; mark="$STATE/rerun-$pr-$run"
      if [ ! -e "$mark" ]; then
        if [ "$DRY" = 1 ]; then say "DRY RUN would: gh run rerun $run --failed"
        else : > "$mark"; gh_ run rerun "$run" --failed >/dev/null 2>&1 || say "PR $pr: rerun of $run failed"; fi
      fi
      return ;;
  esac
  reason="$(gate "$pr")" || { record "BACK $pr $(one_line <<< "$reason")"; return; }
  if [ "$DRY" = 1 ]; then
    say "DRY RUN would: gh pr ready $pr (if draft), gh pr merge $pr --squash"; return
  fi
  if [ "$draft" = true ]; then gh_ pr ready "$pr" >/dev/null 2>&1; fi
  out="$(gh_ pr merge "$pr" --squash 2>&1)"
  # A gh error can hide a merge that landed, so read the state again.
  v="$(view "$pr")"
  if [ "$(cut -f1 <<< "$v")" = MERGED ]; then record "DONE $pr $(cut -f3 <<< "$v")"
  else record "BACK $pr merge failed: $(one_line <<< "$out")"; fi
}

seen=""
while IFS=$'\t' read -r pr task wt lane note <&3; do
  case "$pr" in ''|'#'*) continue ;; esac
  case "$pr" in *[!0-9]*) say "bad queue line: $pr"; continue ;; esac
  case " $seen " in *" $pr "*) continue ;; esac
  seen="$seen $pr"
  pending "$pr" || continue
  say "PR $pr (task $task, $lane)"
  handle "$pr"
done 3< "$QUEUE"
exit 0
