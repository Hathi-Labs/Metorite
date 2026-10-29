#!/usr/bin/env bash
# H-259 / WS-25d: the pure rule that decides if the nightly backup needs a person.
#
# Input (stdin): the state that vps-health.yml reads from the box.
#   timer=<output of: systemctl is-active acb-backup.timer>
#   Result=...  ExecMainStatus=...  ExecMainExitTimestamp=...   (systemctl show)
#   ---JOURNAL---   (optional, ignored here)
# Env:
#   NOW_EPOCH        override "now" (tests). Default: date +%s.
#   MAX_AGE_HOURS    last-success limit. Default: 26.
#   LAST_READ_EPOCH  when the box was last read. Used only for an empty state.
#
# Exit: 0 clean, or empty state (unreadable, warn only), 10 alert,
#       11 the box was unreadable for more than MAX_AGE_HOURS in a row.
# Stdout: one line per reason, ALERT: ... / UNREADABLE: ... / OK ...
set -uo pipefail

now="${NOW_EPOCH:-$(date +%s)}"
max_h="${MAX_AGE_HOURS:-26}"
state="$(sed '/^---JOURNAL---/,$d')"

if [ -z "$(printf '%s' "$state" | tr -d '[:space:]')" ]; then
  if [ -n "${LAST_READ_EPOCH:-}" ] && [ $((now - LAST_READ_EPOCH)) -gt $((max_h * 3600)) ]; then
    echo "UNREADABLE: the box gave no state for more than ${max_h} h."
    exit 11
  fi
  echo "UNREADABLE: the box gave no state. Warning only."
  exit 0
fi

val() { printf '%s\n' "$state" | sed -n "s/^$1=//p" | head -n1; }
timer="$(val timer)"
result="$(val Result)"
stamp="$(val ExecMainExitTimestamp)"

alerts=()
[ "$timer" = active ] || alerts+=("acb-backup.timer is not active (${timer:-unknown}).")
if [ "$result" != success ]; then
  alerts+=("acb-backup.service Result is '${result:-unknown}', not success.")
else
  epoch=""
  if [ -n "$stamp" ] && [ "$stamp" != n/a ]; then
    epoch="$(date -d "$stamp" +%s 2>/dev/null || true)"
  fi
  if [ -z "$epoch" ]; then
    alerts+=("acb-backup.service has no recorded run, so no success is known.")
  elif [ $((now - epoch)) -gt $((max_h * 3600)) ]; then
    alerts+=("The last backup success is $(((now - epoch) / 3600)) h old. The limit is ${max_h} h.")
  fi
fi

if [ "${#alerts[@]}" -gt 0 ]; then
  for a in "${alerts[@]}"; do echo "ALERT: $a"; done
  exit 10
fi
echo "OK acb-backup.service last success <${max_h}h"
exit 0
