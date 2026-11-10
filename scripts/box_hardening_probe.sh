#!/usr/bin/env bash
# WS-49 BH-2 — the box hardening probe. Read-only, names and verdicts only.
# Spec: project-docs/specs/box_hardening.md §5 BH-2 item 7 and acceptance 2.
#
#   sudo bash /opt/acb/app/scripts/box_hardening_probe.sh [unit]   # default acb-gateway
#
# Part 1 reads the unit's properties with `systemctl show`, and checks each
# sandbox line of the BH-2 50-hardening.conf.
# Part 2 starts a transient unit with the same sandbox properties
# (`systemd-run --wait --pipe --uid=<the unit's User>`). In it, it runs each
# probe of acceptance 2. These are the paths P1 to P5 of spec §0.
#
# Output: one line per check, `PASS <name>`, `FAIL <name>` or `SKIP <name>`,
# then a summary. Only p5-touch-t2-vendor-fails can SKIP, when its dir is not
# there yet (before BH-7). A step that times out gives `FAIL probe-timeout`.
# The script prints NO value that it reads: no property value, no path list,
# no command output. Only fixed names that this file holds. It drops each
# line from the transient unit that is not of that form.
# tests/unit/test_unit_hardening.py proves it with a canary.
#
# Exit 0 when nothing FAILs (a SKIP is not a FAIL). Exit 1 on any FAIL.
# Exit 2 on a usage or root fault.
# Before the BH-2 50-hardening.conf is on the box, FAILs are the baseline.
#
# Each write probe that succeeds removes its own file at once, so a run leaves
# the box as it found it. The one exception is `sudo -n true`: on a box with
# no sandbox it runs `true` as root, which changes nothing.
set -uo pipefail

UNIT="${1:-acb-gateway}"
case "$UNIT" in
  *[!A-Za-z0-9@._-]*|"") echo "usage: box_hardening_probe.sh [unit]" >&2; exit 2 ;;
esac

# The transient unit needs the system manager, so the probe needs root.
if [ "$(id -u)" != "0" ]; then
  if sudo -n true 2>/dev/null; then
    exec sudo -n bash "${BASH_SOURCE[0]}" "$UNIT"
  fi
  echo "FAIL probe-needs-root (run it with sudo)"
  exit 2
fi

# Bounds, so the probe cannot hang (review fix round 1, P2). A step inside the
# transient unit gets STEP_TIMEOUT s, the unit RuntimeMaxSec=60, and the whole
# systemd-run RUN_TIMEOUT s. Any timeout is `FAIL probe-timeout`. The two
# variables exist for the tests, and take digits only.
STEP_TIMEOUT="${BH2_PROBE_STEP_TIMEOUT:-10}"
RUN_TIMEOUT="${BH2_PROBE_RUN_TIMEOUT:-90}"
[[ "$STEP_TIMEOUT" =~ ^[0-9]{1,3}$ ]] || STEP_TIMEOUT=10
[[ "$RUN_TIMEOUT" =~ ^[0-9]{1,3}$ ]] || RUN_TIMEOUT=90

FAILS=0
SKIPS=0
TOTAL=0
TIMED_OUT=0
pass() { echo "PASS $1"; TOTAL=$((TOTAL + 1)); }
fail() { echo "FAIL $1"; TOTAL=$((TOTAL + 1)); FAILS=$((FAILS + 1)); }
skip() { echo "SKIP $1"; TOTAL=$((TOTAL + 1)); SKIPS=$((SKIPS + 1)); }
timed_out() {  # one `FAIL probe-timeout` line, however many steps timed out
  [ "$TIMED_OUT" = 1 ] && return 0
  TIMED_OUT=1
  fail probe-timeout
}

prop() { timeout 10 systemctl show "$UNIT" -p "$1" --value 2>/dev/null || true; }

# ── Part 1: the unit's properties ─────────────────────────────────────────

expect_prop() {  # $1 = property, $2 = the value it must hold
  if [ "$(prop "$1")" = "$2" ]; then pass "prop-$1"; else fail "prop-$1"; fi
}

expect_prop ActiveState active
expect_prop NoNewPrivileges yes
expect_prop PrivateTmp yes
expect_prop ProtectSystem strict
expect_prop ProtectHome read-only
expect_prop RestrictSUIDSGID yes
expect_prop ProtectProc invisible
expect_prop CapabilityBoundingSet ""
expect_prop AmbientCapabilities ""
expect_prop LockPersonality yes
expect_prop ProtectKernelTunables yes
expect_prop ProtectKernelModules yes
expect_prop ProtectKernelLogs yes
expect_prop ProtectControlGroups yes
expect_prop ProtectClock yes
expect_prop ProtectHostname yes
expect_prop RestrictRealtime yes

# Each hidden path must be in InaccessiblePaths, with or without the "-".
HIDDEN="$(prop InaccessiblePaths)"
for want in /run/user /run/docker.sock /var/run/docker.sock /etc/acb /etc/sudoers.d; do
  name="hidden-$(echo "$want" | tr -s '/.' '-' | sed 's/^-//')"
  found=0
  for p in $HIDDEN; do
    [ "${p#-}" = "$want" ] && found=1
  done
  if [ "$found" = 1 ]; then pass "$name"; else fail "$name"; fi
done

# These must never be writable (OD-5, W1, P5). A parent of one counts too.
WRITABLE="$(prop ReadWritePaths)"
for never in /opt/acb/app/.venv /opt/acb/t2-vendor /opt/acb/app/infra/enabled_models.json \
             /opt/acb/app/scripts /opt/acb/app/deploy /opt/acb/app/.git; do
  name="not-writable-$(echo "$never" | tr -s '/.' '-' | sed 's/^-//')"
  bad=0
  for p in $WRITABLE; do
    p="${p#-}"
    p="${p%/}"
    case "$never/" in "$p"/*) bad=1 ;; esac
  done
  if [ "$bad" = 0 ]; then pass "$name"; else fail "$name"; fi
done

# ── Part 2: the probes of acceptance 2, in a transient unit ───────────────
#
# The transient unit copies the unit's sandbox properties, and nothing else.
# It does NOT load the EnvironmentFile, so no secret enters it.

RUN_USER="$(prop User)"
[ -n "$RUN_USER" ] || RUN_USER="acb"

COPY_PROPS="NoNewPrivileges PrivateTmp ProtectSystem ProtectHome ReadWritePaths ReadOnlyPaths
InaccessiblePaths ProtectProc RestrictSUIDSGID CapabilityBoundingSet AmbientCapabilities
LockPersonality ProtectKernelTunables ProtectKernelModules ProtectKernelLogs
ProtectControlGroups ProtectClock ProtectHostname RestrictRealtime SupplementaryGroups"
# An empty value is meaningful for these two: it means "no capability".
EMPTY_OK=" CapabilityBoundingSet AmbientCapabilities "

RUN_ARGS=(--wait --pipe --collect "--uid=$RUN_USER" -p RuntimeMaxSec=60)
for name in $COPY_PROPS; do
  value="$(prop "$name")"
  if [ -z "$value" ] && [ "${EMPTY_OK#* "$name" }" = "$EMPTY_OK" ]; then
    continue
  fi
  RUN_ARGS+=(-p "$name=$value")
done

# The probes. Each prints one fixed line. Every command's own output goes to
# /dev/null, or into a variable that is matched and never printed.
# shellcheck disable=SC2016  # the $ signs expand in the transient unit
INNER='
ok() { echo "PASS $1"; }
no() { echo "FAIL $1"; }
APP=/opt/acb/app
TAG=".bh2-probe-$$"
# Each step that talks to a daemon or a socket runs under timeout. Exit 124
# is a timeout: the check FAILs, and so does probe-timeout.
T() { timeout "$STEP_T" "$@"; }
fails_ok() {  # $1 = check name, $2 = exit code of a probe that must fail
  case "$2" in
    0) no "$1" ;;
    124) no "$1"; no probe-timeout ;;
    *) ok "$1" ;;
  esac
}

# P1: sudo with no password.
T sudo -n true >/dev/null 2>&1
fails_ok p1-sudo-n-true-fails $?

# P3 and P5 (variant 3): the checkout is read-only. The spec wants EROFS.
erofs() {  # $1 = check name, $2 = dir
  err="$(touch "$2/$TAG" 2>&1 >/dev/null)"; rc=$?
  if [ "$rc" = 0 ]; then rm -f "$2/$TAG"; no "$1"; return; fi
  case "$err" in *"Read-only file system"*) ok "$1" ;; *) no "$1" ;; esac
}
erofs p3-touch-scripts-erofs "$APP/scripts"
erofs p3-touch-deploy-hostinger-erofs "$APP/deploy/hostinger"

# P5 (variants 1, 2 and 3): any failure passes.
cannot_write() {  # $1 = check name, $2 = dir
  if touch "$2/$TAG" >/dev/null 2>&1; then rm -f "$2/$TAG"; no "$1"; else ok "$1"; fi
}
cannot_write p5-touch-git-hooks-fails "$APP/.git/hooks"
cannot_write p5-touch-venv-fails "$APP/.venv"
# Before BH-7 the dir does not exist, and a failed touch proves nothing.
if [ -d /opt/acb/t2-vendor ]; then
  cannot_write p5-touch-t2-vendor-fails /opt/acb/t2-vendor
else
  echo "SKIP p5-touch-t2-vendor-fails"
fi

# The gateway still writes its own .env.
if test -w "$APP/.env"; then ok env-still-writable; else no env-still-writable; fi

# P4: the user manager. /run/user/<uid> exists only while a session is open,
# so the second check asks for /run/user itself.
if ls "/run/user/$(id -u)" >/dev/null 2>&1; then no p4-ls-run-user-uid-fails; else ok p4-ls-run-user-uid-fails; fi
if ls /run/user >/dev/null 2>&1; then no p4-run-user-hidden; else ok p4-run-user-hidden; fi

# P2: the docker socket.
T docker ps >/dev/null 2>&1
fails_ok p2-docker-ps-fails $?

# Setgid is refused, so crontab cannot read the spool. "no crontab for"
# means it COULD read the spool, so that answer is a FAIL too.
err="$(T crontab -l 2>&1 >/dev/null)"; rc=$?
case "$rc:$err" in
  124:*) no setgid-crontab-l-fails; no probe-timeout ;;
  0:*|*"no crontab for"*) no setgid-crontab-l-fails ;;
  *) ok setgid-crontab-l-fails ;;
esac

# The root-only files: /etc/acb holds the rollback ack and, later, the
# off-box key.
if ls /etc/acb >/dev/null 2>&1; then no etc-acb-hidden; else ok etc-acb-hidden; fi

echo "BH2-PROBE-END"
'
# The names that the transient unit may answer, each once. A line with any
# other name is dropped, so not even a lower-case value can pass through.
INNER_NAMES=" p1-sudo-n-true-fails p3-touch-scripts-erofs p3-touch-deploy-hostinger-erofs"
INNER_NAMES+=" p5-touch-git-hooks-fails p5-touch-venv-fails p5-touch-t2-vendor-fails"
INNER_NAMES+=" env-still-writable p4-ls-run-user-uid-fails p4-run-user-hidden"
INNER_NAMES+=" p2-docker-ps-fails setgid-crontab-l-fails etc-acb-hidden "
INNER_CHECKS=12

# The transient unit's stderr goes to a file that is grepped, never printed.
# systemd-run runs without --quiet, so --wait writes "Finished with result:"
# there.
ERRF="$(mktemp)"
trap 'rm -f "$ERRF"' EXIT
OUT="$(timeout "$RUN_TIMEOUT" systemd-run "${RUN_ARGS[@]}" -- \
       /bin/bash -c "STEP_T=$STEP_TIMEOUT
$INNER" 2>"$ERRF")"
RUN_RC=$?
# 124: timeout(1) ended systemd-run. "result: timeout": RuntimeMaxSec ended it.
if [ "$RUN_RC" = 124 ] || grep -q "result: timeout" "$ERRF" 2>/dev/null; then
  timed_out
fi

SEEN=0
END=0
DONE_NAMES=" "
while IFS= read -r line; do
  if [[ "$line" =~ ^(PASS|FAIL|SKIP)\ ([a-z0-9-]+)$ ]]; then
    verdict="${BASH_REMATCH[1]}"
    check="${BASH_REMATCH[2]}"
    if [ "$check" = "probe-timeout" ] && [ "$verdict" = "FAIL" ]; then
      timed_out
      continue
    fi
    # Known, and not seen before. Anything else is dropped.
    [[ "$INNER_NAMES" == *" $check "* ]] || continue
    [[ "$DONE_NAMES" == *" $check "* ]] && continue
    # Only the t2-vendor check may SKIP: its dir comes with BH-7.
    if [ "$verdict" = "SKIP" ] && [ "$check" != "p5-touch-t2-vendor-fails" ]; then
      continue
    fi
    DONE_NAMES+="$check "
    SEEN=$((SEEN + 1))
    case "$verdict" in
      PASS) pass "$check" ;;
      SKIP) skip "$check" ;;
      *) fail "$check" ;;
    esac
  elif [ "$line" = "BH2-PROBE-END" ]; then
    END=1
  fi
  # Any other line is dropped, unread. It could hold a value.
done <<< "$OUT"

if [ "$RUN_RC" != 0 ] || [ "$END" != 1 ] || [ "$SEEN" != "$INNER_CHECKS" ]; then
  fail transient-unit-ran-every-probe
fi

echo "SUMMARY $UNIT: $((TOTAL - FAILS - SKIPS)) PASS, $SKIPS SKIP, $FAILS FAIL"
[ "$FAILS" = 0 ]
