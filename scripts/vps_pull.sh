#!/usr/bin/env bash
# WS-25 — pull-based delivery. The box fetches its own updates.
#
# Why this exists
# ---------------
# Measured 2026-08-05: GitHub's runners cannot reach this VPS on ANY port
# (`ssh: connect to host: Connection timed out` AND `workbench=000000`, curl's
# no-response code), while the box was idle, unrebooted, and answering the
# operator in 240ms. The drop is upstream of the machine and nothing on the
# machine can fix it. Deploys therefore failed for two days while the app stayed
# up and looked fine.
#
# The box reaches GitHub OUTBOUND in 29ms. Inbound broken, outbound fine — so
# delivery is inverted: instead of GitHub pushing to the box, the box pulls.
#
# Why it polls `release` and NOT `main`
# -------------------------------------
# The workflow deploy job runs only after `lint` and `test` pass. A poller
# watching `main` would apply commits whose tests FAILED — trading an outage for
# a worse one, silently. So CI (which runs on GitHub, and reaches GitHub fine)
# fast-forwards a `release` ref once the gates pass, and this script applies
# only that ref. CI gating survives the inversion, and the box needs no GitHub
# credential to check it.
#
# Consequence, deliberate: a commit that never passes CI never reaches the box,
# and the box goes quietly stale rather than applying something unverified.
# `--force` exists for the incident case and says so in the log.
#
# Usage:
#   scripts/vps_pull.sh            # apply origin/$RELEASE_REF if it moved
#   scripts/vps_pull.sh --force    # apply even if HEAD already matches
#   scripts/vps_pull.sh --check    # report only, change nothing (exit 10 = behind)
#
# Env:
#   APP_DIR      (default /opt/acb/app)
#   RELEASE_REF  (default release)
#   STATE_DIR    (default /var/lib/acb) — last-success marker, see D3 below
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/acb/app}"
RELEASE_REF="${RELEASE_REF:-release}"
STATE_DIR="${STATE_DIR:-/var/lib/acb}"
# MODE comes from the argument, or else from the environment. The give-up
# message below has told operators to run `sudo MODE=force bash vps_pull.sh`
# since WS-25, and until 2026-09-26 this line overwrote MODE with "apply", so
# that advice never worked.
MODE="${MODE:-apply}"
case "${1:-}" in
  --force) MODE="force" ;;
  --check) MODE="check" ;;
  "")      ;;
  *)       echo "unknown argument: $1" >&2; exit 2 ;;
esac
case "$MODE" in
  apply|force|check) ;;
  *) echo "unknown MODE: $MODE (expected apply, force or check)" >&2; exit 2 ;;
esac

say()  { printf "\n==> %s\n" "$*"; }
warn() { printf "  !! %s\n" "$*" >&2; }

# --- Never apply as root (H-89) ----------------------------------------------
# 🔴 **ROOT WAS THE WRITER.** This script ran as root under `acb-pull.service`,
# so every `npm ci` and `next build` on the pull path left root-owned files in
# a checkout that the CI path, as the app user, then could not write. On
# 2026-09-26 that was 50220 paths under the workbench's `node_modules`.
#
# The unit now runs as the checkout's owner. This block is the fence for the
# other ways in: the runbook's `sudo … vps_pull.sh`, and the first tick after
# this change lands, while the box still holds the old unit. Root hands the
# state directory to the owner and runs this file again as the owner. Nothing
# else on this path runs as root. The apply reaches root through `sudo` only.
if [ "$(id -u)" = "0" ]; then
  OWNER="$(stat -c '%U' "$APP_DIR")"
  if [ "$OWNER" != "root" ]; then
    OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
    mkdir -p "$STATE_DIR" && chown -R "$OWNER:" "$STATE_DIR" 2>/dev/null || true
    echo "running as root — running again as $OWNER, the owner of $APP_DIR (H-89)"
    exec runuser -u "$OWNER" -- env \
      HOME="$OWNER_HOME" PATH="$OWNER_HOME/.local/bin:$PATH" \
      APP_DIR="$APP_DIR" RELEASE_REF="$RELEASE_REF" STATE_DIR="$STATE_DIR" MODE="$MODE" \
      MAX_FAILS="${MAX_FAILS:-3}" \
      bash "$0" "$@"
  fi
fi

# --- One deploy at a time, and NEVER wait (H-89, H-164) ----------------------
# The timer fires on a cadence, AND the CI path runs the same apply on the same
# checkout. So this takes the SAME lock that `vps_apply.sh` takes, on the same
# file and the same fd, for the whole run: fetch, install, build, swap and
# restart. When another deploy holds it, this exits 0 and changes nothing.
# "Someone else is already applying" is a normal state, and the timer's next
# tick is the retry.
#
# ⚠️ The path must match `vps_apply.sh`'s helper block exactly.
#
# ⚠️ `/tmp/acb-vps-pull.lock` is the OLD lock, and nothing takes it now. It
# guarded the pull path against itself only, and the CI path never took it.
# The file can stay on the box. Do not delete it from here, because a pull
# that runs the old copy of this script still opens it.
# `test_deploy_serialize.py` runs both against one lock file and fails if they
# do not exclude each other.
DEPLOY_LOCK="${DEPLOY_LOCK:-$(dirname "$APP_DIR")/acb-deploy.lock}"
DEPLOY_MARKER="${DEPLOY_MARKER:-$(dirname "$APP_DIR")/acb-deploy.applied}"
[ -e "$DEPLOY_LOCK" ] || ( umask 022; : >> "$DEPLOY_LOCK" ) 2>/dev/null || true
# Read-only on purpose: flock(2) ignores the open mode, so a lock file that
# another user created stays usable.
exec 8<"$DEPLOY_LOCK"
if ! flock -n 8; then
  hpid="" hsince="" hwho=""
  read -r hpid hsince hwho < "$DEPLOY_LOCK.holder" 2>/dev/null || true
  if [ -n "$hpid" ] && [ -d "/proc/$hpid" ]; then
    echo "another deploy holds the lock since $hsince, pid $hpid ($hwho) — nothing to do, the timer retries"
  else
    echo "another deploy holds the lock — nothing to do, the timer retries"
  fi
  exit 0
fi
printf '%s %s %s\n' "$$" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$(id -un)" \
  > "$DEPLOY_LOCK.holder.$$" 2>/dev/null \
  && mv -f "$DEPLOY_LOCK.holder.$$" "$DEPLOY_LOCK.holder" 2>/dev/null || true

cd "$APP_DIR"
# Only a user who does NOT own the checkout needs this, or git refuses every
# command with "detected dubious ownership". Add it once. The unconditional add
# ran every five minutes, and root's ~/.gitconfig held 8375 copies of this one
# line on 2026-09-26.
if [ "$(stat -c '%U' "$APP_DIR")" != "$(id -un)" ] \
   && ! git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$APP_DIR"; then
  git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
fi

say "Fetching origin/$RELEASE_REF"
if ! git fetch --quiet origin "$RELEASE_REF" 2>/dev/null; then
  warn "cannot fetch origin/$RELEASE_REF — either the network is down or CI has"
  warn "never published it. Until CI publishes it, this box will not self-update."
  exit 1
fi

LOCAL="$(git rev-parse HEAD)"
TARGET="$(git rev-parse "origin/$RELEASE_REF")"
printf "    local:  %s\n    target: %s\n" "${LOCAL:0:12}" "${TARGET:0:12}"

# 🔴 **THE CHECKOUT IS NOT THE DELIVERY, AND CONFUSING THEM COST THREE DAYS.**
#
# `HEAD = TARGET` says the code ARRIVED. It says nothing about whether the apply
# that follows it SUCCEEDED — and `vps_apply.sh` synchronises the checkout as
# one of its first acts, hundreds of lines before it builds anything. So an
# apply that dies in the middle still leaves HEAD on the target, and the next
# tick reads that as "nothing to do". Forever. A box in this state never
# retries, and every signal it emits says it is fine.
#
# Measured 2026-08-29 on the production box:
#
#   HEAD           16f5dccd   (four merges past the last success)
#   last-pull-sha  24636e7c   written 14:10, and never since
#   last-pull-ok   19:44      fresh, because a no-op touches it
#
# Both markers already existed. Only `last-pull-ok` was being read, and it is
# the one that cannot distinguish "up to date" from "failing every time".
#
# ⚠️ So gate on the LAST SUCCESSFULLY APPLIED sha, not on HEAD. `last-pull-sha`
# is written only inside the success branch below, which makes it the only
# honest record of delivery on the box. A failed apply now retries on the next
# tick instead of being latched out by its own partial progress.
#
# ⚠️ A box with no marker at all (fresh bring-up, or one that predates this)
# applies once and then settles. That is the correct bias: re-applying is
# idempotent, and never applying is the failure this exists to end.
#
# 🟢 **A CI DEPLOY COUNTS TOO (2026-09-26).** `last-pull-sha` records only what
# THIS path applied, so a commit the CI path had just shipped read as "not yet
# applied", and this path built it a second time: CI at 06:34, root at 06:47.
# `vps_apply.sh` now writes one marker for BOTH paths, and only at its last
# line. APPLIED_SHA reads it, so either path's complete apply satisfies the
# gate. `last-pull-sha` stays the record of this path's own successes.
LAST_OK_SHA="$(cat "$STATE_DIR/last-pull-sha" 2>/dev/null || echo '')"
APPLIED_SHA="$(cut -d' ' -f1 "$DEPLOY_MARKER" 2>/dev/null || echo '')"
if [ "$LOCAL" = "$TARGET" ] && { [ "$LAST_OK_SHA" = "$TARGET" ] || [ "$APPLIED_SHA" = "$TARGET" ]; } && [ "$MODE" != "force" ]; then
  echo "    already at ${TARGET:0:12}, skipping"
  # Touch the marker even on a no-op: D3 asks whether delivery is WORKING, and
  # "nothing to do" is a healthy answer. Without this, a box that is simply
  # up to date looks identical to a box whose poller died three weeks ago.
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$STATE_DIR/last-pull-ok" 2>/dev/null || true
  exit 0
fi

# 🔴 **A RETRY THAT CANNOT SUCCEED MUST STOP RETRYING.**
#
# The retry above is deliberate and right: gate on the last SUCCESSFUL sha, so
# a half-finished apply is tried again instead of latching itself out. What it
# did not consider is an apply that fails the SAME way every time.
#
# `vps_apply.sh` restarts the gateway around line 590 and builds the workbench
# around line 700. So a build that always fails still bounces production first,
# every single tick. Measured 2026-09-20: a stale generated type made the build
# fail for eleven hours, and the five-minute timer restarted the live gateway
# ~130 times. Every in-flight request during those restarts got a 500, and the
# only thing anybody saw was an occasional unreadable error in the UI.
#
# ⚠️ **The damage was not the failed deploy. It was the retrying.** One failed
# apply is a deploy that did not land. A hundred and thirty is an outage.
#
# So: three attempts at one target, then stop and say so loudly. The count is
# keyed to the TARGET sha, so a new commit always gets its own three attempts
# and nothing needs clearing by hand. A transient failure — a network blip, a
# busy box — still retries, which is the case the retry was built for.
FAIL_SHA="$(cat "$STATE_DIR/last-fail-sha" 2>/dev/null || echo '')"
FAIL_N="$(cat "$STATE_DIR/last-fail-count" 2>/dev/null || echo 0)"
case "$FAIL_N" in ''|*[!0-9]*) FAIL_N=0 ;; esac
MAX_FAILS="${MAX_FAILS:-3}"

if [ "$FAIL_SHA" = "$TARGET" ] && [ "$FAIL_N" -ge "$MAX_FAILS" ] && [ "$MODE" != "force" ]; then
  warn "apply has failed $FAIL_N times at ${TARGET:0:12} — NOT retrying"
  warn "each attempt restarts the gateway before it fails, so retrying is an outage"
  warn "fix the cause, then: sudo bash $0 --force   (or sudo MODE=force bash $0, or clear $STATE_DIR/last-fail-*)"
  # Non-zero so `systemctl --failed` keeps showing it. A box that has given up
  # must not look healthy — that is the failure WS-25 exists to end.
  exit 11
fi

if [ "$MODE" = "check" ]; then
  echo "    BEHIND — $(git rev-list --count HEAD.."origin/$RELEASE_REF") commit(s)"
  git log --oneline HEAD.."origin/$RELEASE_REF" | head -20
  exit 10
fi

say "Applying $(git rev-list --count HEAD.."origin/$RELEASE_REF" 2>/dev/null || echo '?') commit(s)"
git log --oneline HEAD.."origin/$RELEASE_REF" 2>/dev/null | head -20 | sed 's/^/    /'

# --- Read the apply script from the TARGET, not the working tree -------------
# The apply script's own first act is to synchronise the checkout — which
# rewrites files under $APP_DIR, including scripts/. bash reads a script
# incrementally by byte offset, so a script that rewrites itself mid-execution
# runs garbage from that offset on. `git show` reads the new version out of the
# object database WITHOUT touching the working tree, and we run it from a path
# nothing is about to overwrite. This is the two-stage bootstrap.
APPLY_SRC="scripts/vps_apply.sh"
if ! git cat-file -e "$TARGET:$APPLY_SRC" 2>/dev/null; then
  warn "$APPLY_SRC does not exist at $TARGET — refusing to guess how to deploy"
  exit 1
fi
TMP_APPLY="$(mktemp /tmp/acb-vps-apply.XXXXXX.sh)"
trap 'rm -f "$TMP_APPLY"' EXIT
git show "$TARGET:$APPLY_SRC" > "$TMP_APPLY"
chmod 700 "$TMP_APPLY"

say "Running $APPLY_SRC from $TARGET"
# DEPLOY_LOCK_HELD: this process already holds the lock on fd 8, and the
# apply inherits it. A second acquire would open a NEW file description and
# wait on itself.
FORCE_FLAG=0
[ "$MODE" = "force" ] && FORCE_FLAG=1
if APP_DIR="$APP_DIR" DEPLOY_REF="$TARGET" DEPLOY_LOCK_HELD=1 DEPLOY_FORCE="$FORCE_FLAG" \
     DEPLOY_LOCK="$DEPLOY_LOCK" DEPLOY_MARKER="$DEPLOY_MARKER" bash "$TMP_APPLY"; then
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$STATE_DIR/last-pull-ok" 2>/dev/null || true
  echo "$TARGET" > "$STATE_DIR/last-pull-sha" 2>/dev/null || true
  # Clear the breaker. A success is the only thing that should, and it must
  # happen here rather than on the next tick — otherwise a box that recovers
  # stays latched out by its own history.
  rm -f "$STATE_DIR/last-fail-sha" "$STATE_DIR/last-fail-count" 2>/dev/null || true
  say "Applied $(git rev-parse --short HEAD)"
else
  rc=$?
  # Count this failure against THIS target, so the breaker above can stop a
  # loop that cannot win. A different target resets the count to 1.
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  if [ "$FAIL_SHA" = "$TARGET" ]; then
    FAIL_N=$((FAIL_N + 1))
  else
    FAIL_N=1
  fi
  echo "$TARGET" > "$STATE_DIR/last-fail-sha" 2>/dev/null || true
  echo "$FAIL_N" > "$STATE_DIR/last-fail-count" 2>/dev/null || true
  warn "attempt $FAIL_N of $MAX_FAILS at ${TARGET:0:12}"
  # Exit non-zero so systemd marks the unit failed and `systemctl --failed`
  # shows it. A deploy that fails silently is the whole reason WS-25 exists:
  # for two days the only signal was a red tick on a page nobody was watching.
  warn "apply FAILED (exit $rc) — box left at $(git rev-parse --short HEAD)"
  warn "the checkout may be partially updated; inspect before re-running"
  exit "$rc"
fi
