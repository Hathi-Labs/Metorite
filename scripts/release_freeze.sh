#!/usr/bin/env bash
# release_freeze.sh: is a merge to main allowed now? The ONE implementation of the
# release freeze (CIV, task 3415). premerge.sh (step 00) and merge_bot.sh call it.
#
#   bash scripts/release_freeze.sh
#
# THE RULE. No merge from 04:20, 12:20 and 20:20 UTC (70 minutes before the 05:30,
# 13:30 and 21:30 release slots) until the release run of that slot has started,
# or slot + 30 minutes, whichever is first.
# Started = a workflow_dispatch run of deploy.yml whose title holds "nightly"
# (the box's slot dispatch, deploy.yml run-name), created from slot - 15 minutes on.
# A plain dispatch or a hotfix is not the release run, so it does not end the window.
# No answer from gh: the window ends at the slot time (fail closed).
#
# WHY. A slot ships the newest green commit of main. A merge in the window can turn
# main's last batch before the slot red, and then the slot ships an older commit.
#
# Override: PREMERGE_FREEZE_OK=<task number, digits only> for a Rung 0 fix that the
# owner or the orchestrator approved. The script prints the task number.
#
# EXIT 0: merge allowed (outside a window, the release run started, or overridden).
# EXIT 75: frozen. Wait, and try again later. It is not a defect of the branch.
# EXIT 1: PREMERGE_FREEZE_OK is not digits, or PREMERGE_NOW is not epoch seconds.
#
# Test hooks: PREMERGE_NOW (epoch seconds), PREMERGE_RUNS_CMD (a shell command
# that prints one "<createdAt><TAB><title>" line per run, in place of gh).
# Fence: tests/unit/test_release_freeze.py.
set -uo pipefail

# gh reads the repo of this checkout, wherever the caller runs.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || { echo "release freeze: cannot enter the checkout of $0" >&2; exit 1; }

now="${PREMERGE_NOW:-$(date -u +%s)}"
[[ "$now" =~ ^[0-9]+$ ]] || { echo "release freeze: PREMERGE_NOW must be epoch seconds. Got: $now" >&2; exit 1; }
hm() { date -u -d "@$1" +%H:%M; }

# One line per deploy.yml dispatch run: "<createdAt><TAB><displayTitle>".
runs() {
  if [ -n "${PREMERGE_RUNS_CMD:-}" ]; then
    eval "$PREMERGE_RUNS_CMD"
    return
  fi
  timeout 20 gh run list --workflow deploy.yml --event workflow_dispatch --limit 20 \
    --json createdAt,displayTitle --jq '.[] | .createdAt + "\t" + .displayTitle'
}

day="$(date -u -d "@$now" +%F)"
for slot_h in 05:30 13:30 21:30; do
  slot="$(date -u -d "$day $slot_h" +%s)"
  start=$(( slot - 4200 )); end=$(( slot + 1800 ))
  [ "$now" -ge "$start" ] && [ "$now" -lt "$end" ] || continue
  rc=0
  ans="$(runs 2>/dev/null)" || rc=$?
  started=0
  if [ "$rc" = 0 ]; then
    while IFS=$'\t' read -r created title; do
      [ -n "$created" ] || continue
      case "$title" in *nightly*) ;; *) continue ;; esac
      c="$(date -u -d "$created" +%s 2>/dev/null || echo 0)"
      [ "$c" -ge $(( slot - 900 )) ] && started=1
    done <<< "$ans"
  else
    end="$slot"
    [ "$now" -ge "$end" ] && exit 0
  fi
  [ "$started" = 1 ] && exit 0
  if [ -n "${PREMERGE_FREEZE_OK:-}" ]; then
    if ! [[ "$PREMERGE_FREEZE_OK" =~ ^[0-9]+$ ]]; then
      echo "release freeze: PREMERGE_FREEZE_OK must be a task number (digits only). Got: $PREMERGE_FREEZE_OK" >&2
      exit 1
    fi
    echo "release freeze OVERRIDDEN for task $PREMERGE_FREEZE_OK (window $(hm "$start") to $(hm "$end") UTC)"
    exit 0
  fi
  echo "release freeze: no merge from $(hm "$start") UTC until the $slot_h UTC release run starts (or $(hm "$end") UTC at the latest). It is $(hm "$now") UTC. Wait. A Rung 0 fix that the owner approved: PREMERGE_FREEZE_OK=<task number>." >&2
  exit 75
done
exit 0
