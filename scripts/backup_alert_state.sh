#!/usr/bin/env bash
# H-259 / WS-25d: the pure rule that decides if the nightly backup needs a person.
#
# Input (stdin): the state that vps-health.yml reads from the box.
#   timer=<output of: systemctl is-active acb-backup.timer>
#   Result=...  ExecMainStatus=...                               (systemctl show)
#   BackupTaken=<taken_utc of the newest MANIFEST.txt, 2026-10-29T023119Z>
#   JournalComplete=<time of the last "Backup complete" line, 30 h>  (fallback)
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

# The newest backup manifest is the PRIMARY proof of a success. It is written
# at the end of a run and stays on disk. systemd keeps no run state over a
# reboot, so ExecMainExitTimestamp is n/a after a boot and is never used.
epoch=""
source_name="manifest"
taken="$(val BackupTaken)"
if [[ "$taken" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z$ ]]; then
  epoch="$(date -u -d "${taken:0:10} ${taken:11:2}:${taken:13:2}:${taken:15:2}" +%s 2>/dev/null || true)"
fi
if [ -z "$epoch" ]; then
  # Fallback: the journal line "Backup complete" in the last 30 h.
  jc="$(val JournalComplete)"
  if [ -n "$jc" ]; then
    epoch="$(date -d "$jc" +%s 2>/dev/null || true)"
    source_name="journal"
  fi
fi

alerts=()
warns=()
[ "$timer" = active ] || alerts+=("acb-backup.timer is not active (${timer:-unknown}).")
if [ -n "$result" ] && [ "$result" != success ]; then
  alerts+=("acb-backup.service Result is '$result', not success.")
fi
if [ -z "$epoch" ]; then
  alerts+=("No backup manifest and no 'Backup complete' journal line were found.")
else
  age=$((now - epoch))
  if [ "$age" -lt -300 ]; then
    warns+=("WARN: the $source_name time is in the future (clock skew). Treated as fresh.")
  elif [ "$age" -gt $((max_h * 3600)) ]; then
    alerts+=("The last backup success ($source_name) is $((age / 3600)) h old. The limit is ${max_h} h.")
  fi
fi

if [ "${#alerts[@]}" -gt 0 ]; then
  for a in "${alerts[@]}"; do echo "ALERT: $a"; done
  exit 10
fi
echo "OK acb-backup.service last success <${max_h}h"
for w in "${warns[@]+"${warns[@]}"}"; do echo "$w"; done
exit 0
