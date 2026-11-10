#!/usr/bin/env bash
# CIV-5: name the first PR that broke main.
#
# Usage: scripts/bisect_main.sh <good_sha> <bad_sha> <test id>...
#
# main uses squash merges. Each commit on its first-parent line is one PR,
# and its subject ends with "(#1234)". The script binary-searches the commits
# in (good, bad], and it runs only the test ids you give (pytest node ids).
# It does not call `git bisect`, because that moves the HEAD of the caller.
# Each probe uses its own detached worktree in a temp dir. The EXIT trap
# removes it, also after a signal. The checkout of the caller stays as it was.
#
# Environment:
#   BISECT_TEST_CMD    The probe command. The script runs it with `bash -c`
#                      in the probe worktree, and the test ids are "$@".
#                      Default: uv run python -m pytest -x -q -p no:cacheprovider -- "$@"
#                      Exit 0 = good, 1 = bad. Any other code = the commit
#                      cannot be judged. The search skips it, as `git bisect skip` does.
#   BISECT_TIMEOUT_S   Limit of one probe in seconds. Default 1800.
#   BISECT_MAX_PROBES  Limit of probes, the two checks included. Default 12.
#
# The ci_local.sh lanes are not called here. To reuse a prepared lane or venv,
# set BISECT_TEST_CMD to a command that does.
#
# Stdout: the answer line, `FIRST_BAD <sha> <subject> PR=<number or none>`.
# Stderr: one PROBE line per probe, and the reasons.
# Exit codes:
#   0  the first bad commit is found
#   2  the arguments are wrong
#   3  an assumption failed: the good commit fails, the bad commit passes,
#      or the range is empty. The script does not bisect.
#   4  adjacent commits cannot be judged. The script prints the range.
#   5  the probe cap stopped the search.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <good_sha> <bad_sha> <test id>..." >&2
  exit 2
fi
good_arg="$1"
bad_arg="$2"
shift 2
test_ids=("$@")

TEST_CMD="${BISECT_TEST_CMD:-uv run python -m pytest -x -q -p no:cacheprovider -- \"\$@\"}"
TIMEOUT_S="${BISECT_TIMEOUT_S:-1800}"
MAX_PROBES="${BISECT_MAX_PROBES:-12}"

repo="$(git rev-parse --show-toplevel)"
good="$(git -C "$repo" rev-parse --verify --quiet "${good_arg}^{commit}")" || { echo "bisect_main: unknown revision: $good_arg" >&2; exit 2; }
bad="$(git -C "$repo" rev-parse --verify --quiet "${bad_arg}^{commit}")" || { echo "bisect_main: unknown revision: $bad_arg" >&2; exit 2; }

probes=0
probe_dir=""
probe_pid=""
VERDICT=""

say() { echo "$*" >&2; }

cleanup() {
  local code=$?
  trap - EXIT
  if [ -n "$probe_pid" ]; then
    kill -TERM "$probe_pid" 2>/dev/null || true
    wait "$probe_pid" 2>/dev/null || true
    probe_pid=""
  fi
  if [ -n "$probe_dir" ]; then
    git -C "$repo" worktree remove --force "$probe_dir/wt" >/dev/null 2>&1 || true
    rm -rf "$probe_dir"
    git -C "$repo" worktree prune >/dev/null 2>&1 || true
    probe_dir=""
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP

# probe <sha>: sets VERDICT to good, bad or skip.
probe() {
  local sha="$1" rc=0
  probes=$((probes + 1))
  if [ "$probes" -gt "$MAX_PROBES" ]; then
    say "PROBE CAP: ${MAX_PROBES} probes used."
    exit 5
  fi
  say "PROBE ${probes} ${sha:0:12} $(git -C "$repo" log -1 --format=%s "$sha")"
  probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/bisect.XXXXXX")"
  git -C "$repo" worktree add --detach -q "$probe_dir/wt" "$sha"
  (
    cd "$probe_dir/wt"
    exec timeout "$TIMEOUT_S" bash -c "$TEST_CMD" bisect "${test_ids[@]}"
  ) &
  probe_pid=$!
  wait "$probe_pid" || rc=$?
  probe_pid=""
  git -C "$repo" worktree remove --force "$probe_dir/wt" >/dev/null 2>&1 || true
  rm -rf "$probe_dir"
  probe_dir=""
  case "$rc" in
    0) VERDICT=good ;;
    1) VERDICT=bad ;;
    *) VERDICT=skip; say "  ${sha:0:12} cannot be judged (exit ${rc}). The search skips it." ;;
  esac
  say "  ${sha:0:12} is ${VERDICT}"
}

mapfile -t commits < <(git -C "$repo" rev-list --first-parent --reverse "${good}..${bad}")
n=${#commits[@]}
if [ "$n" -eq 0 ]; then
  say "ASSUMPTION FAILED: the range (${good:0:12}, ${bad:0:12}] holds no first-parent commit."
  exit 3
fi
if [ "${commits[$((n - 1))]}" != "$bad" ]; then
  say "ASSUMPTION FAILED: the bad commit is not on the first-parent line above the good commit."
  exit 3
fi

# Check the two assumptions first.
probe "$good"
if [ "$VERDICT" != "good" ]; then
  say "ASSUMPTION FAILED: the good commit ${good:0:12} does not pass (${VERDICT})."
  exit 3
fi
probe "$bad"
if [ "$VERDICT" != "bad" ]; then
  say "ASSUMPTION FAILED: the bad commit ${bad:0:12} does not fail (${VERDICT})."
  exit 3
fi

# commits[hi] is known bad. Every index below lo is known good or irrelevant.
lo=0
hi=$((n - 1))
declare -A skipped=()
while [ "$lo" -lt "$hi" ]; do
  mid=$(((lo + hi) / 2))
  # Try the middle. When it cannot be judged, try the neighbours in turn.
  cand=-1
  for off in 0 1 -1 2 -2 3 -3 4 -4 5 -5 6 -6 7 -7 8 -8 9 -9; do
    i=$((mid + off))
    if [ "$i" -ge "$lo" ] && [ "$i" -lt "$hi" ] && [ -z "${skipped[$i]:-}" ]; then
      probe "${commits[$i]}"
      if [ "$VERDICT" = "skip" ]; then
        skipped[$i]=1
        continue
      fi
      cand=$i
      break
    fi
  done
  if [ "$cand" -lt 0 ]; then
    say "The first bad commit is in this range. No commit in it can be judged:"
    for ((k = lo; k <= hi; k++)); do
      say "  ${commits[$k]:0:12} $(git -C "$repo" log -1 --format=%s "${commits[$k]}")"
    done
    echo "UNJUDGEABLE_RANGE ${commits[$lo]} ${commits[$hi]}"
    exit 4
  fi
  if [ "$VERDICT" = "good" ]; then
    lo=$((cand + 1))
  else
    hi=$cand
  fi
done

sha="${commits[$hi]}"
subject="$(git -C "$repo" log -1 --format=%s "$sha")"
pr=none
if [[ "$subject" =~ \(#([0-9]+)\)$ ]]; then
  pr="${BASH_REMATCH[1]}"
fi
echo "FIRST_BAD ${sha} ${subject} PR=${pr}"
