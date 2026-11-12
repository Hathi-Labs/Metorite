#!/usr/bin/env bash
# CIV-25 2a: the stop-the-line rule, as a script that lane-merge calls before each merge.
#
# Usage: stop_the_line.sh <pr number>
#
# WHY. One red pin on main hits every open PR (test_projects_chat_x_pin was red on
# 15 branches). When main's newest completed batch run is red, lane-merge merges
# only the PR that repairs main, and the line stops for the rest.
#
# RULE. Read the newest COMPLETED push run of deploy.yml on main (the batch run, CIV-4).
#   success or cancelled -> GO       (exit 0)
#   failure              -> STOP     (exit 10), unless the PR title carries "[fixes-main]" (GO)
#   no run / gh error    -> UNKNOWN  (exit 0 with a warning: a blind gh must not freeze the queue)
# On STOP the stdout line is `STOP <run id> <sha> <failed jobs>`. The caller opens the
# red-on-main task, and bisect_poll.sh (CIV-5) names the PR that broke main.
# Test hook: STL_RUNS_JSON replaces the gh call. Fence: tests/unit/test_stop_the_line.py.
set -euo pipefail
PR="${1:-}"
case "$PR" in ''|*[!0-9]*) echo "usage: $0 <pr number>" >&2; exit 2;; esac
WORKFLOW="${STL_WORKFLOW:-deploy.yml}"
if [ -n "${STL_RUNS_JSON:-}" ]; then
  runs="$STL_RUNS_JSON"
else
  runs="$(gh run list --workflow "$WORKFLOW" --branch main --event push --status completed --limit 10 \
    --json databaseId,conclusion,headSha 2>/dev/null)" || { echo "UNKNOWN gh cannot list runs" >&2; exit 0; }
fi
row="$(python3 -c '
import json,sys
rs=[r for r in json.loads(sys.argv[1]) if r.get("conclusion") in ("success","failure")]
print("%s %s %s" % (rs[0]["databaseId"], rs[0]["headSha"][:9], rs[0]["conclusion"]) if rs else "")
' "$runs")"
[ -n "$row" ] || { echo "UNKNOWN no completed batch run of main" >&2; exit 0; }
read -r id sha conclusion <<< "$row"
if [ "$conclusion" = success ]; then echo "GO main is green at $sha (run $id)"; exit 0; fi
title="${STL_PR_TITLE:-$(gh pr view "$PR" --json title -q .title 2>/dev/null || true)}"
case "$title" in
  *"[fixes-main]"*) echo "GO PR $PR is marked [fixes-main], and main is red at $sha (run $id)"; exit 0;;
esac
jobs="${STL_FAILED_JOBS:-$(gh run view "$id" --json jobs -q '[.jobs[]|select(.conclusion=="failure")|.name]|join(", ")' 2>/dev/null || true)}"
echo "STOP $id $sha ${jobs:-unknown jobs}"
exit 10
