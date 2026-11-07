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
cd "$REPO_DIR"
git fetch --quiet origin main
git switch --quiet --detach origin/main

exec claude -p "/delivery-review" \
  --permission-mode acceptEdits \
  --allowedTools "Bash(python3 scripts/delivery_metrics.py:*)" "Bash(gh api:*)" "Bash(cat:*)" "Bash(tee:*)" "mcp__metorite"
