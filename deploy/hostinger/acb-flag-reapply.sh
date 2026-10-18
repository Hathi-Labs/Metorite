#!/bin/bash
# A forced re-apply that waits for the deploy lock. acb-flag starts it as acb,
# in a transient unit, after a NEXT_PUBLIC_ change on staging: Next.js bakes
# those values in at build time, so only a rebuild applies them.
#
# vps_pull.sh exits 0 with "nothing to do" while another deploy holds the
# lock, and a later unforced run then sees no new commit and does nothing.
# So this retries until a run really applied, for up to 20 minutes.
#
# It is NOT in sudoers. Only acb-flag (as root) starts it, as user acb.
set -uo pipefail
for _ in $(seq 1 40); do
  out="$(/bin/bash /opt/acb/app/scripts/vps_pull.sh --force 2>&1)"
  rc=$?
  printf '%s\n' "$out"
  case "$out" in
    *"another deploy holds the lock"*) sleep 30 ;;
    *) exit "$rc" ;;
  esac
done
echo "gave up: the deploy lock stayed busy for 20 minutes"
exit 1
