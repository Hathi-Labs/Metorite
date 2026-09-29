#!/usr/bin/env bash
# Alarm: did a chat or run write fail on this box in the last N minutes?
#
# WS-27bm S15, project-docs/specs/projects_ai_chat.md §21.
#
# For two months every chat save on production failed, and nothing said so.
# Both clients swallowed the error, and the gateway logged a warning that
# nobody read. This script counts those warnings. It is cheap on purpose:
# one journalctl read, one grep, and no database.
#
# Usage, on the box:
#     bash scripts/alarm_chat_persist.sh            # the last 10 minutes
#     ALARM_MINUTES=60 bash scripts/alarm_chat_persist.sh
#
# Exit 0 when the count is 0. Exit 1 when it is more than 0, and the script
# prints the count and the last three lines. Exit 2 when journalctl is absent.
#
# The pattern matches the three S15 events (chat_fold.persist_failed,
# run_trace.record_failed, agent.mint_failed) and every other
# *_persist_failed or *_record_failed warning. A broader match is the safer
# failure for an alarm.
#
# It is NOT wired into deploy.yml or a timer yet. §21 says so.
set -euo pipefail

UNIT="${ALARM_UNIT:-acb-gateway}"
MINUTES="${ALARM_MINUTES:-10}"
PATTERN='persist_failed|record_failed|mint_failed'

if ! command -v journalctl >/dev/null 2>&1; then
    echo "alarm_chat_persist: journalctl is not on this machine" >&2
    exit 2
fi

lines="$(journalctl -u "$UNIT" --since "-${MINUTES}min" --no-pager -o cat 2>/dev/null \
    | grep -E "$PATTERN" || true)"
count=0
if [ -n "$lines" ]; then
    count="$(printf '%s\n' "$lines" | wc -l | tr -d ' ')"
fi

if [ "$count" -gt 0 ]; then
    echo "ALARM: $count chat or run write failures in $UNIT in the last $MINUTES min"
    # The event name and the first 160 characters. No payload reaches stdout.
    printf '%s\n' "$lines" | tail -3 | cut -c1-160
    exit 1
fi
echo "ok: 0 chat or run write failures in $UNIT in the last $MINUTES min"
