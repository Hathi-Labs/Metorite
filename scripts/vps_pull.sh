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
MODE="apply"
case "${1:-}" in
  --force) MODE="force" ;;
  --check) MODE="check" ;;
  "")      ;;
  *)       echo "unknown argument: $1" >&2; exit 2 ;;
esac

say()  { printf "\n==> %s\n" "$*"; }
warn() { printf "  !! %s\n" "$*" >&2; }

# --- Never run two of these at once ------------------------------------------
# The timer fires on a cadence; a slow apply must not have the next tick start a
# second one on the same working tree. flock makes overlap impossible rather
# than unlikely — a second invocation exits 0 quietly, because "someone else is
# already applying" is a normal state, not a fault worth alerting on.
LOCK="/tmp/acb-vps-pull.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another apply is in progress — nothing to do"
  exit 0
fi

cd "$APP_DIR"
# The unit runs as root while the checkout is owned by acb; without this git
# refuses every command with "detected dubious ownership" and the timer fails
# in a way that reads like a network problem.
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

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
LAST_OK_SHA="$(cat "$STATE_DIR/last-pull-sha" 2>/dev/null || echo '')"
if [ "$LOCAL" = "$TARGET" ] && [ "$LAST_OK_SHA" = "$TARGET" ] && [ "$MODE" != "force" ]; then
  echo "    already current"
  # Touch the marker even on a no-op: D3 asks whether delivery is WORKING, and
  # "nothing to do" is a healthy answer. Without this, a box that is simply
  # up to date looks identical to a box whose poller died three weeks ago.
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$STATE_DIR/last-pull-ok" 2>/dev/null || true
  exit 0
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
if APP_DIR="$APP_DIR" DEPLOY_REF="$TARGET" bash "$TMP_APPLY"; then
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$STATE_DIR/last-pull-ok" 2>/dev/null || true
  echo "$TARGET" > "$STATE_DIR/last-pull-sha" 2>/dev/null || true
  say "Applied $(git rev-parse --short HEAD)"
else
  rc=$?
  # Exit non-zero so systemd marks the unit failed and `systemctl --failed`
  # shows it. A deploy that fails silently is the whole reason WS-25 exists:
  # for two days the only signal was a red tick on a page nobody was watching.
  warn "apply FAILED (exit $rc) — box left at $(git rev-parse --short HEAD)"
  warn "the checkout may be partially updated; inspect before re-running"
  exit "$rc"
fi
