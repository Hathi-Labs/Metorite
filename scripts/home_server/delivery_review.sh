#!/usr/bin/env bash
# CIV-10: start the weekly /delivery-review as a headless Claude session.
# metorite-delivery-review.service calls this script. The invocation lives
# HERE and nowhere else.
#
# OWNER: confirm the headless command. The repo holds no other headless
# `claude -p` launcher, so the line below is a placeholder, not a tested one.
# The session needs the Metorite MCP and the Bash tool, and no write tools.
set -euo pipefail

REPO_DIR="${METORITE_REPO:-$HOME/Metorite}"
# CIV-LIVE (task 4060): the "time lost by lane" table, from the stop log of
# the lane keeper. It goes into the prompt text, never into a file: a fixed
# /tmp path on a box with several users can belong to another user. A table
# step that fails gives one line of text, and the review still runs.
LANE_STOPS="${LANE_STOPS:-$HOME/lanes/lane-stops.tsv}"
time_lost() {  # $1: a checkout that holds scripts/home_server/lane_keeper.py
  local now=()
  [ -n "${LANE_STOPS_NOW:-}" ] && now=(--now "$LANE_STOPS_NOW")
  python3 "$1/scripts/home_server/lane_keeper.py" --time-lost --days 7 \
    --stops "$LANE_STOPS" "${now[@]}" 2>/dev/null \
    || echo "### Time lost by lane: the table step failed. Read $LANE_STOPS on the box."
}
# A throwaway worktree of origin/main. The shared checkout of the box stays
# as it is: other sessions work in it, and a switch of its branch would move
# the ground under them.
git -C "$REPO_DIR" fetch --quiet origin main
WORK="$(mktemp -d "${TMPDIR:-/tmp}/delivery-review.XXXXXX")"
cleanup() {
  git -C "$REPO_DIR" worktree remove --force "$WORK" >/dev/null 2>&1 || rm -rf "$WORK"
  git -C "$REPO_DIR" worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT
git -C "$REPO_DIR" worktree add --quiet --detach "$WORK" origin/main
cd "$WORK"
LOST_TABLE="$(time_lost "$WORK")" || LOST_TABLE="### Time lost by lane: the table step failed."

# A path with two slashes is an absolute path in a permission rule. One slash
# is relative to the project root. OWNER: confirm with one dry run.
# No --permission-mode: a headless session cannot answer a prompt, so any tool
# outside the list below is denied. The session writes no file.
claude -p "/delivery-review" \
  --append-system-prompt "The weekly review data, CIV-LIVE. Copy this table into the report task as it is:
$LOST_TABLE" \
  --allowedTools \
  "Bash(python3 scripts/delivery_metrics.py --days 7 --out /tmp/delivery_now.json)" \
  "Bash(python3 scripts/delivery_metrics.py --days 7 --compare /tmp/delivery_prev.json --out /tmp/delivery_now.json)" \
  "Read(//tmp/delivery_now.json)" "Read(//tmp/delivery_prev.json)" "Write(//tmp/delivery_prev.json)" \
  "mcp__metorite__search_tasks" "mcp__metorite__list_tasks" "mcp__metorite__get_task" \
  "mcp__metorite__get_project" "mcp__metorite__list_agent_runs" "mcp__metorite__create_task" "mcp__metorite__add_comment"
