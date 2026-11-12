#!/usr/bin/env bash
# CIV-20 slice 1: the one way a lane hands a PR to the merge bot (CIV-28).
#
#   bash scripts/home_server/lane_handover.sh <PR> <task> <worktree> <lane> <tier> <note>
#
# <tier> is LOW, STANDARD or HIGH: the risk tier of the slice. The bot never
# merges HIGH (D218 keeps the full loop for it), and it logs a BACK for it.
# It checks what sent a third of the hand-overs back (BACK lines of 2026-11-07),
# and it appends one line to ~/lanes/merge-bot-queue.tsv only when every check
# passes. The fields, with tabs between them, under one "#" header line:
#   PR  task  worktree  lane  head_sha  tier  note
# The head SHA pins the merge: the bot merges that commit and no other.
# The old ~/lanes/merge-queue.tsv stays as history. Nothing reads it now.
#   1. the worktree is clean, and its HEAD is the head of the PR;
#   2. a line of the PR body starts with "VERIFY: PASS" (the verifier passed).
#      The PR body has a "Visual review:" line when the diff touches
#      workbench/control_plane/src (DESIGN_SYSTEM.md section 11);
#   3. origin/main merges into the branch with no conflict;
#   4. each new migration number is above main's highest, and no other open PR
#      adds the same number (R1);
#   (There is no box-run check since D218, 2026-11-12: GitHub pr-check is the gate,
#   and scripts/home_server/merge_bot.sh waits for it.)
# A failed check prints its reason and exits 1. Nothing is appended.
# Settings for a test: HANDOVER_BASE (default origin/main), HANDOVER_QUEUE.
# Fence: tests/unit/test_lane_handover.py.
set -uo pipefail

[ $# -ge 6 ] || { echo "usage: lane_handover.sh <PR> <task> <worktree> <lane> <LOW|STANDARD|HIGH> <note>" >&2; exit 2; }
PR="$1"; TASK="$2"; WT="$3"; LANE="$4"; TIER="$5"; shift 5; NOTE="$*"
BASE="${HANDOVER_BASE:-origin/main}"
QUEUE="${HANDOVER_QUEUE:-$HOME/lanes/merge-bot-queue.tsv}"
fail() { echo "HANDOVER REFUSED: $*" >&2; exit 1; }
for v in "$PR" "$TASK"; do
  case "$v" in ''|*[!0-9]*) fail "the PR and the task are numbers: got '$PR' and '$TASK'";; esac
done
case "$TIER" in LOW|STANDARD|HIGH) ;; *) fail "the tier is LOW, STANDARD or HIGH: got '$TIER'";; esac
case "$LANE$NOTE" in *$'\t'*|*$'\n'*) fail "the lane or the note holds a tab or a newline. The queue is one TSV line.";; esac
[ -d "$WT" ] || fail "no worktree at $WT"
cd "$WT" || fail "cannot enter $WT"
WT="$(pwd -P)"  # lane-merge runs from another directory, so the queue needs an absolute path

# 1. Clean tree, and the PR head is this HEAD.
[ -z "$(git status --porcelain)" ] || fail "the worktree has uncommitted changes. Commit and push them first."
head_pr="$(gh pr view "$PR" --json headRefOid -q .headRefOid)" || fail "gh cannot read PR $PR"
[ "$head_pr" = "$(git rev-parse HEAD)" ] || fail "PR $PR head is ${head_pr:0:9}, and this worktree is at $(git rev-parse --short=9 HEAD). Push first."

git fetch -q origin "${BASE#origin/}" 2>/dev/null || echo "warning: git fetch failed, so $BASE may be stale" >&2
base="$(git merge-base HEAD "$BASE")" || fail "no merge base with $BASE"
files="$(git diff --name-only "$base" HEAD)"

# 2. The VERIFY: PASS line, and the Visual review line.
body="$(gh pr view "$PR" --json body -q .body)" || fail "gh cannot read the body of PR $PR"
grep -q '^VERIFY: PASS' <<< "$body" \
  || fail "no line of the PR body starts with 'VERIFY: PASS'. The merge bot merges only a verified PR. Add the verifier's result to the body, then hand over again."
if grep -q '^workbench/control_plane/src/' <<< "$files"; then
  grep -qE '^Visual review:[[:space:]]*[^[:space:]<]' <<< "$body" \
    || fail "the diff changes workbench/control_plane/src, and no line of the PR body starts with 'Visual review:'. Name the four captures, or write 'Visual review: no UI change'. Then edit the body."
fi

# 3. A trial merge of main, in memory. The worktree stays as it is.
if ! out="$(git merge-tree --write-tree --name-only "$BASE" HEAD 2>&1)"; then
  conflicts="$(sed -n '2,$p' <<< "$out" | sed '/^$/,$d' | tr '\n' ' ')"
  fail "merging $BASE conflicts in: ${conflicts:-see git merge-tree}. Merge it, resolve, push."
fi
# The union driver keeps both sides of a clash on INDEX.md, so look for a repeated spec row.
merged="$(head -n1 <<< "$out")"
if git show "$merged:project-docs/INDEX.md" > "${TMPDIR:-/tmp}/handover-index.$$" 2>/dev/null; then
  dup="$(python3 - "${TMPDIR:-/tmp}/handover-index.$$" <<'PY'
import re, sys
row = re.compile(r"^\|\s*\*{0,2}`?([\w./-]+\.md)`?\*{0,2}\s*\|")
seen, section = {}, ""
for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("## "):
        section = line
    m = row.match(line)
    if m:
        seen[(section, m.group(1))] = seen.get((section, m.group(1)), 0) + 1
print(" ".join(sorted(k[1] for k, v in seen.items() if v > 1)))
PY
)"
  rm -f "${TMPDIR:-/tmp}/handover-index.$$"
  [ -z "$dup" ] || fail "after the merge, project-docs/INDEX.md lists a spec twice: $dup. Merge $BASE, remove the repeat, push."
fi

# 4. Migration numbers (R1).
num() { basename "$1" | { grep -oE '^[0-9]+' || true; } | sed 's/^0*//'; }
main_max=0
while IFS= read -r f; do
  n="$(num "$f")"; [ -n "$n" ] && [ "$n" -gt "$main_max" ] && main_max="$n"
done < <(git ls-tree --name-only "$BASE" infra/postgres/ | grep -E '/[0-9]+_[^/]*\.sql$' || true)
mine=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  n="$(num "$f")"; [ -n "$n" ] || continue
  [ "$n" -gt "$main_max" ] || fail "migration $(basename "$f") is not above main's highest number ($main_max). Take $((main_max + 1)) (R1)."
  mine="$mine $n"
done < <(git diff --name-only --diff-filter=AR "$base" HEAD -- infra/postgres/ | grep -E '/[0-9]+_[^/]*\.sql$' || true)
if [ -n "$mine" ]; then
  others="$(gh pr list --state open --limit 200 --json number,files \
    -q ".[] | select(.number != $PR) | .number as \$p | .files[].path | select(test(\"^infra/postgres/[0-9]+_.*[.]sql\$\")) | \"\(\$p) \(.)\"")" \
    || fail "gh cannot list the open PRs, so the migration check cannot run"
  for n in $mine; do
    hit="$(while read -r p path; do [ -n "$p" ] && [ "$(num "$path")" = "$n" ] && echo "PR $p"; done <<< "$others" | head -1)"
    [ -z "$hit" ] || fail "migration number $n is also taken by $hit (R1). Take another number."
  done
fi

mkdir -p "$(dirname "$QUEUE")"
[ -s "$QUEUE" ] || printf '# PR\ttask\tworktree\tlane\thead_sha\ttier\tnote\n' >> "$QUEUE"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$PR" "$TASK" "$WT" "$LANE" "$head_pr" "$TIER" "$NOTE" >> "$QUEUE"
echo "handed over: PR $PR (task $TASK) at ${head_pr:0:9}, tier $TIER, appended to $QUEUE"
