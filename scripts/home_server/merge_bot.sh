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
# The queue is ~/lanes/merge-bot-queue.tsv, and only lane_handover.sh writes it.
# One line per hand-over, tabs between the fields, under a "#" header line:
#   PR  task  worktree  lane  head_sha  tier  note
# The bot NEVER reads ~/lanes/merge-queue.tsv. That file holds the history of
# the lane-merge session, with HOLD, RED and NOTE lines that people wrote.
# The bot writes one result line per hand-over to ~/lanes/merge-bot.log, and
# nothing else writes that file.
#
# Per pending hand-over (the nth hand-over of a PR gets the nth result):
#   merged -> DONE <pr> <sha>     closed -> BACK <pr> closed
#   tier HIGH                     -> BACK, a person or lane-merge merges it (D218)
#   the PR head is not the queued SHA -> BACK, the lane hands it over again
#   no line of the PR body starts with "VERIFY: PASS" -> BACK
#   a required check red (its newest row) -> BACK <pr> red: <names>
#   a required check missing or pending   -> skip, the next pass looks again
#   a required check cancelled    -> gh run rerun --failed ONCE, then skip.
#                                    A new attempt that is cancelled too -> BACK <pr> cancelled twice
#   the diff touches a HIGH path (HIGH_PATHS_RE below) and the tier is lower -> BACK
#   the trial merge conflicts     -> BACK <pr> conflict: <files>
#   premerge.sh exits 1           -> BACK <pr> premerge: <tail>
#   gh pr merge says "Base branch was modified" -> retry twice, 20 s apart, then skip
#   else gh pr ready (if draft), gh pr merge --squash --match-head-commit <sha>,
#   then DONE <pr> <sha>.
# A transient error is a skip, never a BACK: a failed git fetch, a gh network
# error, or a view that fails after the merge call.
# A lane session reads the log, closes each DONE task, and returns each BACK.
#
# Environment (defaults in brackets):
#   MERGE_QUEUE [~/lanes/merge-bot-queue.tsv]   MERGE_LOG [~/lanes/merge-bot.log]
#   MERGE_BOT_STATE [~/.cache/metorite-merge-bot]  lock file and rerun marks
#   MERGE_BOT_WORKDIR [~/lanes/merge-bot-wt]  a checkout of the repo, bot-owned.
#     The bot resets and cleans it before each gate. It refuses its own checkout.
#   MERGE_BOT_LOCK [auto]  "mkdir" forces the mkdir lock (a test, or no flock)
#   PREMERGE_CMD [timeout 900 bash scripts/premerge.sh]
#   GH_CMD [gh]   MERGE_BOT_DRY_RUN=1: print actions, call no write command
#   MERGE_BASE_RETRY_WAIT [20]  seconds between the in-pass merge retries
# It reads gh JSON with python3, not with gh --jq.
# Fence: tests/unit/test_merge_bot.py.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
QUEUE="${MERGE_QUEUE:-$HOME/lanes/merge-bot-queue.tsv}"
LOG="${MERGE_LOG:-$HOME/lanes/merge-bot.log}"
STATE="${MERGE_BOT_STATE:-$HOME/.cache/metorite-merge-bot}"
WORKDIR="${MERGE_BOT_WORKDIR:-$HOME/lanes/merge-bot-wt}"
PREMERGE_CMD="${PREMERGE_CMD:-timeout 900 bash scripts/premerge.sh}"
GH_CMD="${GH_CMD:-gh}"
DRY="${MERGE_BOT_DRY_RUN:-0}"
LOCK_KIND="${MERGE_BOT_LOCK:-auto}"
MERGE_RETRY_WAIT="${MERGE_BASE_RETRY_WAIT:-20}"
# CIV-28b: the tier floor. The tier in the queue is the claim of a lane, not
# proof. A diff that touches one of these paths is HIGH, whatever the lane says.
# This is the ONE place for the list. Add a path here and nowhere else.
#   infra/postgres/ and infra/customer_console/  the schema and every migration
#   deploy/ and .github/workflows/deploy.yml     the deploy path
#   packages/acb_common/acb_common/db.py         the tenancy and RLS seam
#   tenant_redis.py                              the tenant key wrapper
#   auth*, billing*, credits*, rls*, entitle*    code files in any folder
HIGH_PATHS_RE='^(infra/(postgres|customer_console)/|deploy/|\.github/workflows/deploy\.yml$|packages/acb_common/acb_common/(db|tenant_redis)\.py$)|(^|/)(auth|billing|credits|rls|entitle)[^/]*\.(py|ts|tsx|sql)$'
STALE_LOCK_MIN=180  # The service stops a pass at 2 hours, so a 3-hour lock is dead.

say() { echo "merge_bot: $*" >&2; }
gh_() { $GH_CMD "$@"; }
one_line() { tr '\t\r\n' '   ' | sed 's/  */ /g; s/^ //; s/ $//'; }
json_() { python3 -c "$1"; }  # json_ <program>: the program reads JSON on stdin

mkdir -p "$STATE" "$(dirname "$LOG")"
# One pass at a time. flock where it exists, else an atomic mkdir.
if [ "$LOCK_KIND" != mkdir ] && command -v flock >/dev/null 2>&1; then
  exec 9>"$STATE/lock"
  flock -n 9 || { say "another pass runs"; exit 0; }
else
  if [ -d "$STATE/lock.d" ] && [ -n "$(find "$STATE/lock.d" -maxdepth 0 -mmin +"$STALE_LOCK_MIN" 2>/dev/null)" ]; then
    say "cleared a stale lock: $STATE/lock.d is older than 3 hours"
    rmdir "$STATE/lock.d" 2>/dev/null || rm -rf "$STATE/lock.d"
  fi
  mkdir "$STATE/lock.d" 2>/dev/null || { say "another pass runs"; exit 0; }
  trap 'rmdir "$STATE/lock.d" 2>/dev/null' EXIT
fi
[ -f "$QUEUE" ] || { say "no queue at $QUEUE"; exit 0; }

record() {  # record <line>: one result line in the log. A dry run only prints it.
  if [ "$DRY" = 1 ]; then say "DRY RUN would log: $*"; else printf '%s\n' "$*" >> "$LOG"; fi
}
results() {  # results <pr>: how many DONE or BACK lines the log holds for it
  if [ -f "$LOG" ]; then grep -cE "^(DONE|BACK) $1( |$)" "$LOG"; else echo 0; fi
}
is_done() { [ -f "$LOG" ] && grep -qE "^DONE $1( |$)" "$LOG"; }

# gh JSON readers. Each prints TSV. The PR body never leaves Python.
PY_REQUIRED='
import json, sys
d = json.loads(sys.stdin.buffer.read().decode("utf-8"))
names = list(d.get("contexts") or []) + [c.get("context", "") for c in d.get("checks") or []]
seen = []
for n in names:
    if n and n not in seen:
        seen.append(n)
sys.stdout.buffer.write("".join(n + "\n" for n in seen).encode("utf-8"))
'
PY_VIEW='
import json, sys
d = json.loads(sys.stdin.buffer.read().decode("utf-8"))
body = d.get("body") or ""
verified = any(line.startswith("VERIFY: PASS") for line in body.splitlines())
merged = (d.get("mergeCommit") or {}).get("oid") or ""
row = [d.get("state") or "", "true" if d.get("isDraft") else "false", merged,
       d.get("headRefOid") or "", "1" if verified else "0"]
sys.stdout.buffer.write(("\t".join(row) + "\n").encode("utf-8"))
'
# A re-run adds a second row with the same name. Only the newest row counts.
# A queued row has no start time yet, so it is the newest.
PY_CHECKS='
import json, sys
raw = sys.stdin.buffer.read().decode("utf-8").strip()
rows = json.loads(raw) if raw else []
def when(r):
    start = r.get("startedAt") or ""
    start = "" if start.startswith("0001-") else start
    if not start and r.get("bucket") == "pending":
        return "9999"
    end = r.get("completedAt") or ""
    return start or ("" if end.startswith("0001-") else end)
best = {}
for r in rows:
    name = r.get("name") or ""
    if name not in best or when(r) >= when(best[name]):
        best[name] = r
out = "".join("%s\t%s\t%s\t%s\n" % (n, r.get("bucket") or "", r.get("link") or "", when(r)) for n, r in best.items())
sys.stdout.buffer.write(out.encode("utf-8"))
'

required=""
load_required() {
  [ -n "$required" ] && return 0
  required="$(gh_ api 'repos/{owner}/{repo}/branches/main/protection/required_status_checks' 2>/dev/null \
    | json_ "$PY_REQUIRED" 2>/dev/null | tr -d '\r')" || required=""
  [ -n "$required" ]
}

view() {  # view <pr>: prints "STATE<TAB>isDraft<TAB>merge sha<TAB>head sha<TAB>verified 1|0"
  gh_ pr view "$1" --json state,isDraft,mergeCommit,headRefOid,body 2>/dev/null \
    | json_ "$PY_VIEW" 2>/dev/null | tr -d '\r'
}
field() { cut -f"$1" <<< "$2"; }

# check_status <pr>: prints "ready", "wait", "red: <names>", or "rerun <run id> <start>".
# A required name with no row is not ready. "skipping" counts as a pass.
# gh pr checks exits 8 (pending) or 1 (failed) and still prints the rows, so its
# exit code is not read. A gh error prints no rows, and that reads as "wait".
check_status() {
  local pr="$1" rows name bucket link ts bad="" pending=0 rerun=""
  rows="$(gh_ pr checks "$pr" --json name,bucket,link,startedAt,completedAt 2>/dev/null \
    | json_ "$PY_CHECKS" 2>/dev/null | tr -d '\r')"
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    bucket="$(awk -F'\t' -v n="$name" '$1==n {print $2; exit}' <<< "$rows")"
    link="$(awk -F'\t' -v n="$name" '$1==n {print $3; exit}' <<< "$rows")"
    ts="$(awk -F'\t' -v n="$name" '$1==n {print $4; exit}' <<< "$rows")"
    case "$bucket" in
      pass|skipping) ;;
      fail) bad="$bad${bad:+, }$name" ;;
      cancel)
        [ -n "$rerun" ] || rerun="$(sed -nE 's#.*/runs/([0-9]+).*#\1#p' <<< "$link") ${ts:-none}"
        pending=1 ;;
      *) pending=1 ;;
    esac
  done <<< "$required"
  if [ -n "$bad" ]; then echo "red: $bad"
  elif [ -n "$rerun" ]; then echo "rerun $rerun"
  elif [ "$pending" = 1 ]; then echo wait
  else echo ready; fi
}

# gate <pr> <sha>: trial merge of exactly <sha>, then premerge on a bot branch.
# Returns 0 to merge. Returns 1 with the BACK reason printed. Returns 2 with a
# note printed for a transient error: the next pass tries again.
gate() {
  local pr="$1" sha="$2" tier="$3" branch="merge-bot/pr-$1" out files try main_before main_after tail_ top own
  if [ ! -e "$WORKDIR/.git" ]; then
    git -C "$ROOT" worktree add -q --detach "$WORKDIR" >/dev/null 2>&1 || { echo "no workdir at $WORKDIR"; return 2; }
  fi
  cd "$WORKDIR" || { echo "cannot enter $WORKDIR"; return 2; }
  # The reset and the clean below are for the bot's own checkout only.
  top="$(git rev-parse --show-toplevel 2>/dev/null)"
  own="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null)"
  if [ -z "$top" ] || [ "$top" = "$own" ]; then
    echo "the workdir is the bot's own checkout (${top:-not a repo}). Set MERGE_BOT_WORKDIR to a checkout of its own"
    return 2
  fi
  # A pass that died left a merge, a change or a stray file here. Remove them.
  git merge --abort 2>/dev/null; git reset --hard -q 2>/dev/null; git clean -fdq 2>/dev/null
  git fetch -q origin "+refs/heads/main:refs/remotes/origin/main" "+refs/pull/$pr/head:refs/merge-bot-pull/$pr" 2>/dev/null \
    || { echo "git fetch failed"; return 2; }
  if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
    git fetch -q origin "$sha" 2>/dev/null && git cat-file -e "$sha^{commit}" 2>/dev/null \
      || { echo "the queued head ${sha:0:9} is not on GitHub: hand over again"; return 1; }
  fi
  # The floor reads the diff the trial merge would land: main to the pinned SHA.
  if [ "$tier" != HIGH ]; then
    files="$(git diff --name-only origin/main..."$sha" 2>/dev/null | grep -E "$HIGH_PATHS_RE" | head -n 3 | tr '\n' ' ')"
    if [ -n "$files" ]; then echo "tier floor HIGH, queued as $tier: ${files}- lane-merge or a person merges it"; return 1; fi
  fi
  if ! out="$(git merge-tree --write-tree --name-only origin/main "$sha" 2>&1)"; then
    files="$(sed -n '2,$p' <<< "$out" | sed '/^$/,$d' | tr '\n' ' ')"
    echo "conflict: ${files:-see git merge-tree}"; return 1
  fi
  for try in 1 2 3; do
    # premerge.sh refuses a detached HEAD, so the SHA gets a bot-owned branch.
    git checkout -q -B "$branch" "$sha" 2>/dev/null || { echo "premerge: checkout of ${sha:0:9} failed"; return 1; }
    main_before="$(git rev-parse origin/main)"
    # 9>&-: premerge and its children do not hold the lock of this pass.
    if ! out="$(bash -c "$PREMERGE_CMD" 9>&- 2>&1)"; then
      tail_="$(tail -n 3 <<< "$out" | one_line)"
      echo "premerge: ${tail_:-exit 1}"; return 1
    fi
    git fetch -q origin "+refs/heads/main:refs/remotes/origin/main" 2>/dev/null || { echo "git fetch after premerge failed"; return 2; }
    main_after="$(git rev-parse origin/main)"
    [ "$main_before" = "$main_after" ] && return 0
    say "PR $pr: main moved during premerge (try $try)"
  done
  echo "main moved during premerge"; return 1
}

# A gh error that a later pass can get past. "unexpected EOF" is here too: when
# the re-read shows the PR still open, the merge did not land, so try again.
transient() { grep -qiE 'error connecting|timed? ?out|unexpected EOF|connection (reset|refused)|HTTP 5[0-9][0-9]|TLS handshake|could not resolve' <<< "$1"; }

handle() {
  local pr="$1" sha="$2" tier="$3" v state st out run ts mark rc reason try
  v="$(view "$pr")" || { say "PR $pr: gh cannot read it, next pass"; return; }
  [ -n "$v" ] || { say "PR $pr: gh cannot read it, next pass"; return; }
  state="$(field 1 "$v")"
  case "$state" in
    MERGED) record "DONE $pr $(field 3 "$v")"; return ;;
    CLOSED) record "BACK $pr closed"; return ;;
    OPEN) ;;
    *) say "PR $pr: state '$state', next pass"; return ;;
  esac
  if [ "$tier" = HIGH ]; then record "BACK $pr HIGH tier: lane-merge or a person merges it"; return; fi
  if [ "$(field 4 "$v")" != "$sha" ]; then record "BACK $pr head moved after the hand-over: hand over again"; return; fi
  if [ "$(field 5 "$v")" != 1 ]; then record "BACK $pr no VERIFY: PASS line in the PR body"; return; fi
  load_required || { say "PR $pr: cannot read the required checks, next pass"; return; }
  st="$(check_status "$pr")"
  case "$st" in
    wait) say "PR $pr: required checks not ready"; return ;;
    red:*) record "BACK $pr $st"; return ;;
    rerun*)
      # --failed keeps the run id, so the mark holds the start time of the cancelled
      # attempt. The same time means the rerun has not started. A new time means
      # the new attempt was cancelled too.
      set -- $st; run="$2"; ts="${3:-none}"; mark="$STATE/rerun-$pr-$run"
      if [ ! -e "$mark" ]; then
        if [ "$DRY" = 1 ]; then say "DRY RUN would: gh run rerun $run --failed"
        else printf '%s\n' "$ts" > "$mark"; gh_ run rerun "$run" --failed >/dev/null 2>&1 || say "PR $pr: rerun of $run failed"; fi
      elif [ -s "$mark" ] && [ "$(cat "$mark")" != "$ts" ]; then
        record "BACK $pr cancelled twice: run $run"
      fi
      return ;;
  esac
  reason="$(gate "$pr" "$sha" "$tier")"; rc=$?
  case "$rc" in
    0) ;;
    2) say "PR $pr: $(one_line <<< "$reason"), next pass"; return ;;
    *) record "BACK $pr $(one_line <<< "$reason")"; return ;;
  esac
  if [ "$DRY" = 1 ]; then
    say "DRY RUN would: gh pr ready $pr (if draft), gh pr merge $pr --squash --match-head-commit $sha"; return
  fi
  if [ "$(field 2 "$v")" = true ]; then gh_ pr ready "$pr" >/dev/null 2>&1; fi
  # --match-head-commit: GitHub refuses the merge when the head moved after the gate.
  # "Base branch was modified": main moved during the call. A retry works.
  for try in 1 2 3; do
    out="$(gh_ pr merge "$pr" --squash --match-head-commit "$sha" 2>&1)" && break
    grep -qi 'base branch was modified' <<< "$out" || break
    [ "$try" -lt 3 ] && sleep "$MERGE_RETRY_WAIT"
  done
  # A gh error can hide a merge that landed, so read the state again.
  v="$(view "$pr")"
  if [ -z "$v" ]; then say "PR $pr: cannot read it after the merge call, next pass"; return; fi
  if [ "$(field 1 "$v")" = MERGED ]; then record "DONE $pr $(field 3 "$v")"
  elif transient "$out" || grep -qi 'base branch was modified' <<< "$out"; then say "PR $pr: the merge call failed ($(one_line <<< "$out")), next pass"
  else record "BACK $pr merge failed: $(one_line <<< "$out")"; fi
}

# The nth hand-over of a PR waits for the nth result line, so a lane can hand a
# PR over again after a BACK. A DONE ends the PR for good: a stray later
# hand-over of a merged PR is skipped, and it never gets a second merge.
declare -A nth=()
seen=""
while IFS= read -r line <&3; do
  line="${line%$'\r'}"
  case "$line" in ''|'#'*) continue ;; esac
  pr="$(cut -f1 <<< "$line")"; task="$(cut -f2 <<< "$line")"; lane="$(cut -f4 <<< "$line")"
  sha="$(cut -f5 <<< "$line")"; tier="$(cut -f6 <<< "$line")"
  if [ "$(awk -F'\t' '{print NF}' <<< "$line")" != 7 ] \
    || [[ ! "$pr" =~ ^[0-9]+$ ]] || [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]] \
    || [[ ! "$tier" =~ ^(LOW|STANDARD|HIGH)$ ]]; then
    say "bad queue line (7 fields: PR task worktree lane head_sha tier note): $(one_line <<< "$line")"
    continue
  fi
  nth[$pr]=$(( ${nth[$pr]:-0} + 1 ))
  case " $seen " in *" $pr "*) continue ;; esac
  if is_done "$pr"; then seen="$seen $pr"; continue; fi
  [ "${nth[$pr]}" -eq "$(( $(results "$pr") + 1 ))" ] || continue
  seen="$seen $pr"
  say "PR $pr at ${sha:0:9} (task $task, $lane, $tier)"
  handle "$pr" "$sha" "$tier"
done 3< "$QUEUE"
exit 0
