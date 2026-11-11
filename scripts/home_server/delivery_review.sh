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

# A path with two slashes is an absolute path in a permission rule. One slash
# is relative to the project root. OWNER: confirm with one dry run.
# No --permission-mode: a headless session cannot answer a prompt, so any tool
# outside the list below is denied. The session writes no file.
claude -p "/delivery-review" --allowedTools \
  "Bash(python3 scripts/delivery_metrics.py --days 7 --out /tmp/delivery_now.json)" \
  "Bash(python3 scripts/delivery_metrics.py --days 7 --compare /tmp/delivery_prev.json --out /tmp/delivery_now.json)" \
  "Read(//tmp/delivery_now.json)" "Read(//tmp/delivery_prev.json)" "Write(//tmp/delivery_prev.json)" \
  "mcp__metorite__search_tasks" "mcp__metorite__list_tasks" "mcp__metorite__get_task" \
  "mcp__metorite__get_project" "mcp__metorite__list_agent_runs" "mcp__metorite__create_task" "mcp__metorite__add_comment"
