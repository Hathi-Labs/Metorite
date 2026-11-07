#!/usr/bin/env bash
# CIV-5: the poller of the bisect timer (user unit of `dev` on the home box).
#
# One pass: it looks at the newest completed batch run of main. When that run
# is red and new, it reads the failed test ids, bisects the range since the
# last green batch, and prints one `BISECT_RESULT <json>` line on stdout.
# It does not post to Metorite. It has no Metorite credentials, by design.
# A lane session reads the line and posts it on a task.
# It reads runs only. It never reruns or cancels one.
#
# The batch run (CIV-4) is a `push` run of .github/workflows/deploy.yml on
# main. The job "Unit tests (a signal on a push, a gate on a release)" is its
# suite.
#
# ⚠️ KNOWN GAP: the batch job writes no JUnit file today. Only the R8 shards
# upload `r8-junit-shard-N`. This script downloads the artifact named in
# BISECT_ARTIFACT (default `unit-junit`). Until the batch job uploads a file
# of that name, the download fails and the run is recorded with the message
# "no failed test id". The workflow change is a later slice. Run
# scripts/bisect_main.sh by hand with the ids of the log in the meantime.
#
# ⚠️ The bad sha is the head sha of the run. A batch tests the tip of main,
# which can be newer. Then the check "the bad commit fails" can exit 3.
#
# Environment:
#   BISECT_STATE_DIR  Default ~/.cache/metorite-bisect. `handled` holds one
#                     run id per line. A run is recorded after the bisect
#                     ends with the exit code 0, 3, 4 or 5. A crash is retried.
#   BISECT_ARTIFACT   The JUnit artifact name. Default unit-junit.
#   BISECT_REPO       The `owner/name` for gh. Default: the repo of the checkout.
#
# Install by hand (the owner does this, see scripts/home_server/systemd/):
#   metorite-bisect-poll.service and metorite-bisect-poll.timer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="${BISECT_STATE_DIR:-$HOME/.cache/metorite-bisect}"
ARTIFACT="${BISECT_ARTIFACT:-unit-junit}"

# Every gh call goes through here. Tests replace this function.
gh_cmd() {
  if [ -n "${BISECT_REPO:-}" ]; then
    gh "$@" -R "$BISECT_REPO"
  else
    gh "$@"
  fi
}

# The bisect itself. Tests replace this function.
run_bisect_script() {
  # Fetch first, so the shas of the runs are local. A fetch moves no checkout.
  git -C "$ROOT" fetch -q --no-tags origin main >&2 || log "fetch of main failed. The bisect uses the shas it has."
  (cd "$ROOT" && bash scripts/bisect_main.sh "$@")
}

log() { echo "bisect_poll: $*" >&2; }

# pick_red <handled file>: reads the run list on stdin. It prints "<id> <sha>"
# when the newest completed run is red and new.
pick_red() {
  python3 -I -c '
import json, sys
handled = set(open(sys.argv[1]).read().split()) if len(sys.argv) > 1 else set()
runs = [r for r in json.load(sys.stdin) if r.get("status") == "completed"]
runs.sort(key=lambda r: r["databaseId"], reverse=True)
if runs and runs[0].get("conclusion") == "failure" and str(runs[0]["databaseId"]) not in handled:
    print(runs[0]["databaseId"], runs[0]["headSha"])
' "$@"
}

# pick_green <red id>: reads the green list on stdin. It prints the head sha
# of the newest green run older than the red run.
pick_green() {
  python3 -I -c '
import json, sys
red = int(sys.argv[1])
runs = [r for r in json.load(sys.stdin)
        if r.get("conclusion") == "success" and r["databaseId"] < red]
runs.sort(key=lambda r: r["databaseId"], reverse=True)
if runs:
    print(runs[0]["headSha"])
' "$@"
}

record() {
  mkdir -p "$STATE_DIR"
  echo "$1" >> "$STATE_DIR/handled"
}

emit() {
  # emit <run id> <good> <bad> <exit code> <result line> <ids...>
  python3 -I -c '
import json, sys
run, good, bad, code, result = sys.argv[1:6]
print("BISECT_RESULT " + json.dumps({
    "run_id": int(run), "good_sha": good, "bad_sha": bad,
    "exit_code": int(code), "result": result, "test_ids": sys.argv[6:]}))
' "$@"
}

main() {
  mkdir -p "$STATE_DIR"
  touch "$STATE_DIR/handled"

  local list red run_id bad good tmp ids_text result code
  list="$(gh_cmd run list --workflow deploy.yml --branch main --event push --limit 5 \
    --json databaseId,conclusion,headSha,status)"
  red="$(printf '%s' "$list" | pick_red "$STATE_DIR/handled")"
  if [ -z "$red" ]; then
    log "no new red batch run."
    return 0
  fi
  run_id="${red%% *}"
  bad="${red##* }"
  log "red batch run ${run_id} at ${bad:0:12}."

  good="$(gh_cmd run list --workflow deploy.yml --branch main --event push --status success \
    --limit 20 --json databaseId,conclusion,headSha,status | pick_green "$run_id")"
  if [ -z "$good" ]; then
    log "no earlier green batch run is known, so there is no range."
    emit "$run_id" "" "$bad" 3 "no earlier green batch run"
    record "$run_id"
    return 0
  fi

  tmp="$(mktemp -d "${TMPDIR:-/tmp}/bisect-poll.XXXXXX")"
  trap 'rm -rf "$tmp"' RETURN
  mkdir "$tmp/junit"
  if ! gh_cmd run download "$run_id" -n "$ARTIFACT" -D "$tmp/junit" >/dev/null 2>&1; then
    log "the artifact ${ARTIFACT} of run ${run_id} is not there (known gap)."
  fi
  ids_text="$(python3 -I "$ROOT/scripts/junit_failed_ids.py" "$tmp/junit")"
  if [ -z "$ids_text" ]; then
    log "run ${run_id}: no failed test id in the JUnit artifact. The bisect does not run."
    emit "$run_id" "$good" "$bad" 3 "no failed test id"
    record "$run_id"
    return 0
  fi
  local -a ids
  mapfile -t ids <<<"$ids_text"

  code=0
  result="$(run_bisect_script "$good" "$bad" "${ids[@]}")" || code=$?
  case "$code" in
    0 | 3 | 4 | 5) ;;
    *)
      log "the bisect crashed (exit ${code}). The run stays unhandled."
      return "$code"
      ;;
  esac
  emit "$run_id" "$good" "$bad" "$code" "$(printf '%s\n' "$result" | tail -n 1)" "${ids[@]}"
  record "$run_id"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
