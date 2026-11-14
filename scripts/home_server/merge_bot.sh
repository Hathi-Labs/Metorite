#!/usr/bin/env bash
# CIV-28 (D218 point 2): the merge bot. ONE pass of it merges each queued PR
# that is green on GitHub and whose trial merge with today's main is clean.
# A timer calls it every 2 minutes (systemd/metorite-merge-bot.timer).
#
# It uses no model and no Metorite credential. It pushes only one thing: the
# merge commit of a resolved conflict (CIV-34a), with no force, to the head
# branch of a PR of this repo whose base is main. It never touches main, never
# passes --admin or --auto, and never dispatches or reruns deploy.yml. It does
# not test a PR again after main moves (D218, D176): the gate is the trial
# merge plus scripts/premerge.sh.
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
#   a fork, a base that is not main, or a head branch with a protected name
#   (PROTECTED_HEAD_RE below)     -> BACK, before any merge work
#   the PR head is not the queued SHA -> BACK, the lane hands it over again
#   no line of the PR body starts with "VERIFY: PASS" -> BACK
#   a required check red (its newest row) -> BACK <pr> red: <names>
#   a required check missing or pending   -> skip, the next pass looks again
#   a required check cancelled    -> gh run rerun --failed ONCE, then skip.
#                                    A new attempt that is cancelled too -> BACK <pr> cancelled twice
#   the diff touches a HIGH path (HIGH_PATHS_RE below) and the tier is lower -> BACK
#   the trial merge conflicts     -> CIV-34a: merge main into the PR branch and try the
#                                    conflict rules (scripts/merge_conflict_rules.py).
#                                    Every file fits a rule, the checks pass on the merged
#                                    tree, the head branch did not move, and the push
#                                    works -> RESOLVED <pr> <rules>. The bot commits the
#                                    merge only after the checks pass.
#                                    The next pass waits for the new pr-check, as usual.
#                                    Else -> BACK <pr> conflict: no rule fits <files>
#                                    A conflict in a HIGH path, a workflow file, or
#                                    infra/postgres/ is always a BACK.
#   premerge.sh exits 1           -> BACK <pr> premerge: <tail>
#   an auto-revert PR (head auto-revert/...) when the auto-revert pass did not
#   test the tip of main now      -> skip. The next auto-revert pass tests it
#                                    (CIV-31b). No state file of the PR -> BACK
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
#   CONFLICT_RULES [1]  0 turns the CIV-34a conflict rules off: a conflict is a BACK
#   CONFLICT_TEST_CMD [uv run --no-sync pytest -q -p no:cacheprovider]  gets the test
#     files that the resolution reaches (merge_conflict_rules.py tests). The full
#     gate is not here: pr-check runs it on the new head.
#   CONFLICT_TEST_TIMEOUT [900]  seconds for those tests. A selection whose
#     .test_durations time is over it goes BACK with no run.
#   AUTO_REVERT_STATE [~/.cache/metorite-auto-revert]  read only: open/<pr>.json,
#     the last tip of main that the auto-revert pass tested for a revert PR.
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
CONFLICT_RULES="${CONFLICT_RULES:-1}"
CONFLICT_TEST_CMD="${CONFLICT_TEST_CMD:-uv run --no-sync pytest -q -p no:cacheprovider}"
CONFLICT_TEST_TIMEOUT="${CONFLICT_TEST_TIMEOUT:-900}"
# CIV-31b: the state of the auto-revert pass. open/<pr>.json holds the last tip
# of main on which the pass tested the failed tests of that revert PR.
AUTO_REVERT_STATE="${AUTO_REVERT_STATE:-$HOME/.cache/metorite-auto-revert}"
HEADREF=""  # the PR head branch name, set by handle
QUEUED=""   # the head SHA of the hand-over, set by handle
# The bot pushes a resolved merge to the PR head branch. A head branch with one
# of these names is never a PR branch, so the PR goes BACK before any merge work.
PROTECTED_HEAD_RE='^(main|master|release|release/.*|green/.*|HEAD)$'
# CIV-28b: the tier floor. The tier in the queue is the claim of a lane, not
# proof. A diff that touches one of these paths is HIGH, whatever the lane says.
# This is the ONE place for the list. Add a path here and nowhere else.
# Test files are in on purpose: a weakened auth test needs full review too.
HIGH_PATHS_RE='^('
# The schema and every migration.
HIGH_PATHS_RE+='infra/(postgres|customer_console)/'
# The deploy path.
HIGH_PATHS_RE+='|deploy/|\.github/workflows/deploy\.yml$'
# The scripts that apply migrations or generate tenant migrations.
HIGH_PATHS_RE+='|scripts/(apply_[a-z_]*migrations\.sh|gen_tenant_migration\.py)$'
# Identity, roles and permissions: the whole auth package.
HIGH_PATHS_RE+='|packages/acb_auth/'
# The tenancy and RLS seams: the DB seams, the Redis key wrapper, the memory
# tenant guard, the provisioning of an org, and org_settings (it binds a tenant).
HIGH_PATHS_RE+='|packages/acb_common/acb_common/(db|tenant_redis|provisioning|org_settings)\.py$'
HIGH_PATHS_RE+='|packages/acb_graph/acb_graph/db\.py$'
# The operator console: its DB, the operators and their roles, and the seats.
HIGH_PATHS_RE+='|apps/services/customer_console/customer_console/(db|seats|operator_[^/]*)\.py$'
HIGH_PATHS_RE+='|packages/acb_memory/acb_memory/tenant_guard[^/]*$'
# The members, roles and groups of an org, and the admin API of the UI.
HIGH_PATHS_RE+='|apps/services/gateway/gateway/routes/admin/(_common|members|roles|groups)\.py$'
HIGH_PATHS_RE+='|workbench/[^/]+/src/app/api/admin/'
# The row rules of AI Studio: the stores that apply them on each write.
HIGH_PATHS_RE+='|apps/services/gateway/gateway/studio/(batch_store|objects_store)\.py$'
# The readers of a Knowledge document: who a provider reader proves.
HIGH_PATHS_RE+='|apps/services/gateway/gateway/kb_access/'
# App routes and settings pages for auth, billing and credits, matched by folder.
HIGH_PATHS_RE+='|workbench/[^/]+/src/app/.*/(auth|billing|credits)/'
HIGH_PATHS_RE+=')'
# Any file named for auth, billing, credits, RLS, entitlements, permissions or
# grants, in any folder.
HIGH_PATHS_RE+='|(^|/)(auth|billing|credits|rls|entitle|permission|grant)[^/]*\.(py|ts|tsx|sql)$'
# Any code file with a tenancy or visibility part in its name.
HIGH_PATHS_RE+='|(^|/)([^/]*_)?(tenancy|visibility)[^/]*\.(py|ts|tsx|sql)$'
# Any server file with an access part in its name: studio_access, mailbox_access,
# access_requests. A UI file such as access.ts is out: the server decides access.
HIGH_PATHS_RE+='|(^|/)([^/]*_)?access(_[^/]*)?\.(py|sql)$'
# Any server file for sign-in, sign-up, OAuth or seats, or for the sharing of a
# note, a token, an install or a report. The "(_...)?" tail keeps signing.py out.
HIGH_PATHS_RE+='|(^|/)([^/]*_)?(signin|signup|oauth|seats|share|sharing|shared_token|report_scope)(_[^/]*)?\.(py|sql)$'
# Any file named tenant* (the tenancy seams: tenant_redis, tenant_guard,
# tenant_file_store, tenantDoor), or with an rls or row_rules part in its name.
HIGH_PATHS_RE+='|(^|/)(tenant|[^/]*_(rls|row_rules))[^/]*$'
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
# A missing isCrossRepository reads "unknown", and the bot then goes BACK.
cross = {True: "true", False: "false"}.get(d.get("isCrossRepository"), "unknown")
clean = lambda v: (v or "").replace("\t", " ").replace("\n", " ").replace("\r", " ")
row = [d.get("state") or "", "true" if d.get("isDraft") else "false", merged,
       d.get("headRefOid") or "", "1" if verified else "0", clean(d.get("headRefName")),
       cross, clean(d.get("baseRefName"))]
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

# view <pr>: prints "STATE<TAB>isDraft<TAB>merge sha<TAB>head sha<TAB>verified 1|0<TAB>head branch
# <TAB>cross-repo true|false|unknown<TAB>base branch"
view() {
  gh_ pr view "$1" --json state,isDraft,mergeCommit,headRefOid,body,headRefName,isCrossRepository,baseRefName 2>/dev/null \
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

# reset_to <sha>: drop a merge in progress and any merge commit, back to the PR head.
reset_to() { git -c rerere.enabled=false merge --abort 2>/dev/null; git reset -q --hard "$1" 2>/dev/null; }

# resolve_conflict <pr> <sha> <branch>: CIV-34a. The trial merge of <sha> with main
# conflicts. Merge main into the PR branch here and apply the rules of
# scripts/merge_conflict_rules.py. Check and test the merged tree. Commit the merge
# only when every check passed, and push it only when the head branch on GitHub is
# still <sha>. QUEUED (set by handle) is the SHA of the hand-over. The state file
# pairs it with the pushed head, so the next pass accepts the new head.
# Prints the rules (return 0), or a BACK reason (return 1), or a note (return 2).
# It never force-pushes. A failed check leaves no commit and nothing on GitHub.
# rerere stays off on every merge: the bot workdir shares the .git of ~/Metorite,
# and an rr-cache folder there turns rerere on for every worktree by itself.
resolve_conflict() {
  local pr="$1" sha="$2" branch="$3" unmerged bad="" out rules trailers="" line f changed sel rc est="" full=0 remote
  local -a picked=() resolved=()
  git checkout -q -B "$branch" "$sha" 2>/dev/null || { echo "conflict: checkout of ${sha:0:9} failed"; return 1; }
  git -c rerere.enabled=false -c merge.conflictstyle=diff3 merge --no-commit --no-ff -q origin/main >/dev/null 2>&1
  unmerged="$(git diff --name-only --diff-filter=U 2>/dev/null)"
  if [ -z "$unmerged" ]; then
    reset_to "$sha"; echo "conflict: no rule fits (the merge shows no conflicted file)"; return 1
  fi
  # A HIGH path, a workflow file and infra/postgres/ always go BACK.
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in
      .github/workflows/*|infra/postgres/*) bad="$bad$f " ;;
      *) if grep -qE "$HIGH_PATHS_RE" <<< "$f"; then bad="$bad$f "; fi ;;
    esac
  done <<< "$unmerged"
  if [ -n "$bad" ]; then
    reset_to "$sha"
    echo "conflict: no rule fits ${bad}(a HIGH, workflow or schema path never gets a rule)"; return 1
  fi
  out="$(python3 "$ROOT/scripts/merge_conflict_rules.py" resolve --high-re "$HIGH_PATHS_RE" 2>/dev/null)"; rc=$?
  if [ "$rc" != 0 ] || [ -n "$(git diff --name-only --diff-filter=U 2>/dev/null)" ]; then
    bad="$(sed -n 's/^UNRESOLVED //p' <<< "$out" | tr '\n' ' ')"
    reset_to "$sha"; echo "conflict: no rule fits ${bad:-$(tr '\n' ' ' <<< "$unmerged")}"; return 1
  fi
  rules="$(sed -n 's/^RULE \([^ ]*\) .*/\1/p' <<< "$out" | sort -u | tr '\n' ' ')"; rules="${rules% }"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    trailers="$trailers${trailers:+$'\n'}Conflict-Rule: ${line#RULE }"
    line="${line#RULE }"; resolved+=("${line#* }")
  done < <(grep '^RULE ' <<< "$out")
  # The checks run on the merged tree, before the commit.
  changed="$(git diff --cached --name-only --diff-filter=AM origin/main 2>/dev/null)"
  while IFS= read -r f; do
    case "$f" in
      *.py) python3 -m py_compile "$f" >/dev/null 2>&1 || bad="$bad$f " ;;
      .github/workflows/*.yml|.github/workflows/*.yaml)
        python3 -c 'import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
yaml.safe_load(open(sys.argv[1], encoding="utf-8"))' "$f" >/dev/null 2>&1 || bad="$bad$f " ;;
    esac
  done <<< "$changed"
  if [ -n "$bad" ]; then
    reset_to "$sha"; echo "conflict: resolved by $rules but ${bad}does not parse"; return 1
  fi
  # The tests that the resolution reaches: the conflicted test files and their
  # importers, with no scanners and no safety floor. pr-check runs the full gate
  # on the new head.
  sel="$(python3 "$ROOT/scripts/merge_conflict_rules.py" tests "${resolved[@]}" 9>&- 2>/dev/null | tr -d '\r')"
  while IFS= read -r line; do
    case "$line" in
      FULL*) full=1 ;;
      "SECONDS "*) est="${line#SECONDS }" ;;
      "TEST "*) picked+=("${line#TEST }") ;;
    esac
  done <<< "$sel"
  if [ "$full" = 1 ]; then
    reset_to "$sha"; echo "conflict: resolved by $rules but the resolution reaches every test (FULL), too many to run here"; return 1
  fi
  if ! [[ "$est" =~ ^[0-9]+$ ]]; then
    reset_to "$sha"; echo "conflict: resolved by $rules but the test selection failed"; return 1
  fi
  if [ "$est" -gt "$CONFLICT_TEST_TIMEOUT" ]; then
    reset_to "$sha"
    echo "conflict: resolved by $rules but the tests need ${est} s, over the cap of ${CONFLICT_TEST_TIMEOUT} s"; return 1
  fi
  if [ "${#picked[@]}" -gt 0 ]; then
    if ! out="$(timeout "$CONFLICT_TEST_TIMEOUT" bash -c "$CONFLICT_TEST_CMD \"\$@\"" _ "${picked[@]}" 9>&- 2>&1)"; then
      reset_to "$sha"; echo "conflict: resolved by $rules but the tests failed: $(tail -n 3 <<< "$out" | one_line)"; return 1
    fi
  fi
  if [ "$DRY" = 1 ]; then
    reset_to "$sha"
    say "DRY RUN would push the merge commit of PR $pr (rules: $rules) to $HEADREF"; echo "dry run: resolved by $rules"; return 2
  fi
  # handle refuses these before any merge work. This is the second look, right before a write.
  if [ -z "$HEADREF" ] || grep -qE "$PROTECTED_HEAD_RE" <<< "$HEADREF"; then
    reset_to "$sha"; echo "conflict: resolved by $rules but the head branch '$HEADREF' is protected or unknown"; return 1
  fi
  git -c user.name="${MERGE_BOT_GIT_NAME:-metorite-merge-bot}" -c user.email="${MERGE_BOT_GIT_EMAIL:-merge-bot@metorite.invalid}" \
    -c rerere.enabled=false commit -q -m "Merge main into the branch of PR $pr (merge bot conflict rules)" -m "$trailers" >/dev/null 2>&1 \
    || { reset_to "$sha"; echo "conflict: the merge commit failed after the rules ($rules)"; return 1; }
  # A plain push is a fast-forward only. It still overwrites a branch that the author
  # moved BACK (a reset), so the branch on GitHub must still be <sha>.
  if ! remote="$(git ls-remote origin "refs/heads/$HEADREF" 9>&- 2>/dev/null)"; then
    reset_to "$sha"; echo "conflict: resolved by $rules but git ls-remote failed"; return 2
  fi
  # ls-remote matches a pattern by its tail, so take only the line of the exact ref.
  remote="$(awk -F'\t' -v r="refs/heads/$HEADREF" '$2 == r {print $1; exit}' <<< "$remote")"
  if [ "$remote" != "$sha" ]; then
    reset_to "$sha"
    echo "conflict: resolved by $rules but the head branch moved during the resolution (${remote:0:9}, not ${sha:0:9}): hand over again"; return 1
  fi
  # A plain push, never --force. A rejected push means the head moved or the bot has no write reach.
  if ! out="$(git push -q origin "HEAD:refs/heads/$HEADREF" 9>&- 2>&1)"; then
    reset_to "$sha"; echo "conflict: resolved by $rules but the push failed: $(tail -n 2 <<< "$out" | one_line)"; return 1
  fi
  printf '%s %s\n' "${QUEUED:-$sha}" "$(git rev-parse HEAD)" > "$STATE/resolved-$pr"
  echo "$rules"; return 0
}

# gate <pr> <sha>: trial merge of exactly <sha>, then premerge on a bot branch.
# Returns 0 to merge. Returns 1 with the BACK reason printed. Returns 2 with a
# note printed for a transient error: the next pass tries again.
# Returns 3 with the rules printed: a conflict was resolved and pushed (CIV-34a).
# A 4th argument "conflict-only" stops after the trial merge and returns 4 when
# it is clean. A PR with a conflict gets no GitHub checks, so the bot looks early.
gate() {
  local mode="${4:-}" rules rc changed pr="$1" sha="$2" tier="$3" branch="merge-bot/pr-$1" out files try main_before main_after tail_ top own
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
  git -c rerere.enabled=false merge --abort 2>/dev/null; git reset --hard -q 2>/dev/null; git clean -fdq 2>/dev/null
  git fetch -q origin "+refs/heads/main:refs/remotes/origin/main" "+refs/pull/$pr/head:refs/merge-bot-pull/$pr" 2>/dev/null \
    || { echo "git fetch failed"; return 2; }
  if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
    git fetch -q origin "$sha" 2>/dev/null && git cat-file -e "$sha^{commit}" 2>/dev/null \
      || { echo "the queued head ${sha:0:9} is not on GitHub: hand over again"; return 1; }
  fi
  # The floor reads the diff the trial merge would land: main to the pinned SHA.
  if [ "$tier" != HIGH ]; then
    # --no-renames lists both sides of a rename, so a move out of a HIGH path shows.
    # A failed diff is not "no HIGH path": the gate fails closed.
    changed="$(git diff --no-renames --name-only origin/main..."$sha" 2>/dev/null)" \
      || { echo "tier floor unknown: git diff failed"; return 1; }
    files="$(grep -E "$HIGH_PATHS_RE" <<< "$changed" | head -n 3 | tr '\n' ' ')"
    if [ -n "$files" ]; then echo "tier floor HIGH, queued as $tier: ${files}- lane-merge or a person merges it"; return 1; fi
  fi
  if ! out="$(git merge-tree --write-tree --name-only origin/main "$sha" 2>&1)"; then
    files="$(sed -n '2,$p' <<< "$out" | sed '/^$/,$d' | tr '\n' ' ')"
    if [ "$CONFLICT_RULES" = 0 ]; then echo "conflict: ${files:-see git merge-tree}"; return 1; fi
    rules="$(resolve_conflict "$pr" "$sha" "merge-bot/pr-$pr")"; rc=$?
    echo "$rules"
    [ "$rc" = 0 ] && return 3
    return "$rc"
  fi
  [ "$mode" = conflict-only ] && return 4
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

# revert_tested <pr>: CIV-31b. A revert PR of the auto-revert pass (head branch
# auto-revert/...) merges only when the pass tested the failed tests on the tip
# of main now. A newer tip can hold a fix, and then the pass closes the PR.
# Returns 0 to merge. Returns 1 with a BACK reason (no state file). Returns 2
# with a note: the PR waits in the queue, and the next auto-revert pass tests
# the new tip.
revert_tested() {
  local file="$AUTO_REVERT_STATE/open/$1.json" tested tip
  [ -f "$file" ] || { echo "auto-revert PR with no state file $file: a person merges it"; return 1; }
  tested="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("tip") or "")' "$file" 2>/dev/null | tr -d '\r')"
  git -C "$WORKDIR" fetch -q origin "+refs/heads/main:refs/remotes/origin/main" 2>/dev/null \
    || { echo "git fetch of main failed before the auto-revert check"; return 2; }
  tip="$(git -C "$WORKDIR" rev-parse refs/remotes/origin/main 2>/dev/null)"
  if [ -z "$tested" ] || [ "$tested" != "$tip" ]; then
    echo "auto-revert PR: the pass has not tested main at ${tip:0:9} (last ${tested:0:9}). It waits for the next auto-revert pass"
    return 2
  fi
  return 0
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
  # The bot gates against main and can push to the head branch. So it takes only a
  # PR of this repo, with base main, from a branch that is not a protected name.
  HEADREF="$(field 6 "$v")"
  if [ "$(field 7 "$v")" != false ]; then
    record "BACK $pr the head branch is in a fork (cross-repo: $(field 7 "$v")): the bot takes only a branch of this repo"; return
  fi
  if [ "$(field 8 "$v")" != main ]; then record "BACK $pr the base is '$(field 8 "$v")', not main"; return; fi
  if [ -z "$HEADREF" ] || grep -qE "$PROTECTED_HEAD_RE" <<< "$HEADREF"; then
    record "BACK $pr the head branch '$HEADREF' is protected or empty: the bot pushes only to a PR branch"; return
  fi
  # CIV-34a: after a pushed resolution the head is the merge commit, and the hand-over
  # holds the old SHA. The state file pairs the queued SHA with the pushed head.
  QUEUED="$sha"
  if [ "$(field 4 "$v")" != "$sha" ] && [ -f "$STATE/resolved-$pr" ] \
    && [ "$(cat "$STATE/resolved-$pr")" = "$sha $(field 4 "$v")" ]; then
    sha="$(field 4 "$v")"
  fi
  if [ "$(field 4 "$v")" != "$sha" ]; then record "BACK $pr head moved after the hand-over: hand over again"; return; fi
  if [ "$(field 5 "$v")" != 1 ]; then record "BACK $pr no VERIFY: PASS line in the PR body"; return; fi
  load_required || { say "PR $pr: cannot read the required checks, next pass"; return; }
  st="$(check_status "$pr")"
  case "$st" in
    wait)
      # A PR with a conflict gets no checks from GitHub, so look for a conflict now.
      reason="$(gate "$pr" "$sha" "$tier" conflict-only)"; rc=$?
      case "$rc" in
        3) record "RESOLVED $pr $(one_line <<< "$reason")" ;;
        1) record "BACK $pr $(one_line <<< "$reason")" ;;
        2) say "PR $pr: $(one_line <<< "$reason"), next pass" ;;
        *) say "PR $pr: required checks not ready" ;;
      esac
      return ;;
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
    3) record "RESOLVED $pr $(one_line <<< "$reason")"; return ;;
    2) say "PR $pr: $(one_line <<< "$reason"), next pass"; return ;;
    *) record "BACK $pr $(one_line <<< "$reason")"; return ;;
  esac
  if [[ "$HEADREF" == auto-revert/* ]]; then
    reason="$(revert_tested "$pr")"; rc=$?
    case "$rc" in
      0) ;;
      2) say "PR $pr: $(one_line <<< "$reason"), next pass"; return ;;
      *) record "BACK $pr $(one_line <<< "$reason")"; return ;;
    esac
  fi
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
