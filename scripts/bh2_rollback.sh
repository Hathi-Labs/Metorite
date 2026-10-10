#!/usr/bin/env bash
# WS-49 BH-2 — turn the gateway sandbox off for 72 hours, and on again.
# Spec: project-docs/specs/box_hardening.md §5 BH-2 item 6.
#
#   sudo bash /opt/acb/app/scripts/bh2_rollback.sh on      # sandbox off for 72 h
#   sudo bash /opt/acb/app/scripts/bh2_rollback.sh off     # sandbox back on
#   sudo bash /opt/acb/app/scripts/bh2_rollback.sh status  # state and expiry
#
# `on` installs deploy/hostinger/rollback/acb-gateway-90-bh2-off.conf as
# /etc/systemd/system/acb-gateway.service.d/90-bh2-off.conf. It writes the
# expiry, now + 72 h, into /etc/acb/bh2-rollback-ack. Then it reloads systemd,
# restarts the gateway and waits for /health.
#
# `off` removes both files, reloads, restarts and waits for /health.
#
# Each command is idempotent. A second `on` does NOT move the expiry: a
# rollback must not become the state. To get 72 h more, run `off`, then `on`.
# `on` refuses an expired rollback for the same reason.
#
# Recovery after the expiry (B2-4). A deploy after the date fails. Fix the
# cause, then run `bh2_rollback.sh off`, then
# `sudo MODE=force bash /opt/acb/app/scripts/vps_pull.sh`.
#
# Every read and write of a root file goes through `sudo` (B2-3). The ack file
# is root:root under /etc/acb, which is root-only. So the script works the same
# from root and from `acb`, and tests/unit/test_bh2_rollback.py stubs `sudo`.
#
# `status` exit codes are the contract that the BH-2 strict check of
# scripts/vps_apply.sh and the WARN of health-watchdog.sh read:
#   0  the rollback is on, and its expiry is in the future (prints WARN)
#   1  the rollback is off: no conf, no ack
#   3  the rollback is expired, malformed, or half there
#   2  usage
#
# The deploy lock (B3). `on` and `off` restart the gateway, and so does a
# deploy. So they take the lock that scripts/vps_apply.sh and vps_pull.sh
# take for a whole apply: $(dirname APP_DIR)/acb-deploy.lock, which is
# /opt/acb/acb-deploy.lock on the box. They wait at most BH2_LOCK_WAIT
# seconds. On a timeout they name the holder, change nothing and exit 4.
# `status` takes NO lock: the strict check runs it while the deploy holds the
# lock, and a lock there would wait on itself. Fence:
# tests/unit/test_bh2_rollback.py (the lock section).
#   4  `on` or `off` did not get the deploy lock, and changed nothing
set -euo pipefail

UNIT="acb-gateway"
DROPIN_DIR="/etc/systemd/system/${UNIT}.service.d"
DROPIN="$DROPIN_DIR/90-bh2-off.conf"
ACK_DIR="/etc/acb"
ACK="$ACK_DIR/bh2-rollback-ack"
TTL_SECONDS=$((72 * 3600))
# An expiry later than now + TTL + this slack was not written by `on`.
EXPIRY_SLACK_SECONDS=300
HEALTH_URL="http://127.0.0.1:8080/health"
HEALTH_TRIES=120

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_CONF="$APP_DIR/deploy/hostinger/rollback/acb-gateway-90-bh2-off.conf"
# The same path as DEPLOY_LOCK in scripts/vps_apply.sh. sudo resets the env,
# so on the box this is always the default. The tests set it.
DEPLOY_LOCK="${DEPLOY_LOCK:-$(dirname "$APP_DIR")/acb-deploy.lock}"
BH2_LOCK_WAIT="${BH2_LOCK_WAIT:-600}"

usage() {
  echo "usage: bh2_rollback.sh on|off|status" >&2
  exit 2
}

now_epoch() { date -u +%s; }

# "since <time>, pid <pid> (<user>)" for whoever holds the deploy lock now.
# It reads the record that vps_apply.sh and take_deploy_lock write.
lock_holder() {
  local hpid="" hsince="" hwho=""
  read -r hpid hsince hwho < "$DEPLOY_LOCK.holder" 2>/dev/null || true
  if [ -n "$hpid" ] && [ -d "/proc/$hpid" ]; then
    echo "since $hsince, pid $hpid ($hwho)"
  else
    echo "(the holder left no record)"
  fi
}

# Take the deploy lock on fd 9, or exit 4 and change nothing. The lock holds
# until this script exits. The file is opened READ-ONLY, as vps_apply.sh does,
# because flock(2) ignores the open mode. So a lock file that root made stays
# usable by the app user, and the other way round.
take_deploy_lock() {
  if ! command -v flock >/dev/null 2>&1; then
    echo "!! flock is missing, so this script cannot take the deploy lock $DEPLOY_LOCK." >&2
    echo "   Nothing changed." >&2
    exit 4
  fi
  if [ ! -e "$DEPLOY_LOCK" ]; then
    ( umask 022; : >> "$DEPLOY_LOCK" ) 2>/dev/null || true
    if [ "$(id -u)" = "0" ]; then
      chown "$(stat -c '%U:%G' "$APP_DIR")" "$DEPLOY_LOCK" 2>/dev/null || true
    fi
  fi
  if ! exec 9<"$DEPLOY_LOCK"; then
    echo "!! cannot open the deploy lock $DEPLOY_LOCK. Nothing changed." >&2
    exit 4
  fi
  if ! flock -n 9; then
    echo "    waiting up to ${BH2_LOCK_WAIT}s: a deploy holds $DEPLOY_LOCK $(lock_holder)"
    if ! flock -w "$BH2_LOCK_WAIT" 9; then
      echo "!! a deploy still holds $DEPLOY_LOCK after ${BH2_LOCK_WAIT}s, $(lock_holder)." >&2
      echo "   Nothing changed. Run this command again when the deploy ends." >&2
      exit 4
    fi
  fi
  if printf '%s %s %s\n' "$$" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "bh2_rollback:$(id -un)" \
       > "$DEPLOY_LOCK.holder.$$" 2>/dev/null; then
    mv -f "$DEPLOY_LOCK.holder.$$" "$DEPLOY_LOCK.holder" 2>/dev/null || true
  fi
  if [ "$(id -u)" = "0" ] && [ -e "$DEPLOY_LOCK.holder" ]; then
    chown "$(stat -c '%U:%G' "$APP_DIR")" "$DEPLOY_LOCK.holder" 2>/dev/null || true
  fi
  echo "    took the deploy lock $DEPLOY_LOCK"
}

fmt_utc() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

# Prints the raw EXPIRES_EPOCH value of the ack, or nothing when the file is
# absent or has no such line. check_expiry decides if the value is trusted.
ack_expiry() {
  sudo test -f "$ACK" || return 0
  sudo cat "$ACK" 2>/dev/null | sed -n 's/^EXPIRES_EPOCH=//p' | tail -n 1
}

have_ack() { sudo test -f "$ACK"; }
have_dropin() { sudo test -f "$DROPIN"; }

# Sets EXPIRY_WHY to an empty string when EXPIRES is a date that the script
# trusts, and to the reason when it does not. $1 = now (epoch).
# Each test fails closed, BEFORE any arithmetic, so a huge value cannot make
# `[ -ge ]` error out and fall through to "on" (review fix round 1, P2).
check_expiry() {
  local now="$1"
  if ! [[ "$EXPIRES" =~ ^[0-9]{1,12}$ ]]; then
    EXPIRY_WHY="the ack holds no valid EXPIRES_EPOCH line"
  elif [ "$EXPIRES" -gt $((now + TTL_SECONDS + EXPIRY_SLACK_SECONDS)) ]; then
    EXPIRY_WHY="the expiry is more than 72 h ahead, so the ack is not trusted"
  elif [ "$now" -ge "$EXPIRES" ]; then
    EXPIRY_WHY="expired at $(fmt_utc "$EXPIRES")"
  else
    EXPIRY_WHY=""
  fi
}

# Sets STATE to one of: off, on, expired, partial-conf, partial-ack.
# Sets EXPIRES to the raw value of the ack, and EXPIRY_WHY (check_expiry).
read_state() {
  local conf=0 ack=0 now
  have_dropin && conf=1
  have_ack && ack=1
  EXPIRES="$(ack_expiry)"
  now="$(now_epoch)"
  check_expiry "$now"
  if [ "$conf" = 0 ] && [ "$ack" = 0 ]; then
    STATE="off"
  elif [ "$ack" = 1 ] && [ -n "$EXPIRY_WHY" ]; then
    STATE="expired"
  elif [ "$conf" = 1 ] && [ "$ack" = 0 ]; then
    STATE="partial-conf"
  elif [ "$conf" = 0 ]; then
    STATE="partial-ack"
  else
    STATE="on"
  fi
}

restart_and_check() {
  echo "==> daemon-reload, restart $UNIT"
  sudo systemctl daemon-reload
  sudo systemctl restart "$UNIT"
  check_health
}

check_health() {
  local i
  for ((i = 1; i <= HEALTH_TRIES; i++)); do
    if systemctl is-active --quiet "$UNIT" \
       && curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL" 2>/dev/null; then
      echo "    $UNIT is active and /health answers"
      return 0
    fi
    sleep 1
  done
  echo "!! $UNIT did not answer $HEALTH_URL after $HEALTH_TRIES tries" >&2
  echo "   check: sudo journalctl -u $UNIT --no-pager -n 40" >&2
  return 1
}

write_ack() {
  local now exp tmp
  now="$(now_epoch)"
  exp=$((now + TTL_SECONDS))
  tmp="$(mktemp)"
  {
    echo "# WS-49 BH-2 rollback. Written by scripts/bh2_rollback.sh on."
    echo "# The deploy honours the rollback until this date, and then fails."
    echo "EXPIRES_EPOCH=$exp"
    echo "EXPIRES_UTC=$(fmt_utc "$exp")"
  } > "$tmp"
  # mkdir -p keeps the mode of a /etc/acb that exists. A new one is root-only.
  sudo mkdir -p -m 0700 "$ACK_DIR"
  sudo install -m 0644 "$tmp" "$ACK"
  rm -f "$tmp"
  echo "    wrote $ACK, expires $(fmt_utc "$exp")"
}

cmd_on() {
  [ -f "$SRC_CONF" ] || { echo "!! missing $SRC_CONF" >&2; exit 1; }
  take_deploy_lock
  read_state
  if [ "$STATE" = "expired" ]; then
    echo "!! the BH-2 rollback is not valid: $EXPIRY_WHY." >&2
    echo "   Run 'bh2_rollback.sh off' first. Run 'on' again only if you still need it." >&2
    exit 3
  fi
  local changed=0
  if ! sudo cmp -s "$SRC_CONF" "$DROPIN"; then
    sudo mkdir -p "$DROPIN_DIR"
    sudo install -m 0644 "$SRC_CONF" "$DROPIN"
    echo "    installed $DROPIN"
    changed=1
  fi
  if ! have_ack; then
    write_ack
    changed=1
  fi
  if [ "$changed" = 1 ]; then
    restart_and_check
  else
    echo "    the BH-2 rollback is already on, expires $(fmt_utc "$EXPIRES")"
    check_health
  fi
  echo "WARN BH-2 rolled back until $(fmt_utc "$(ack_expiry)")"
}

cmd_off() {
  local changed=0
  take_deploy_lock
  if have_dropin; then
    sudo rm -f "$DROPIN"
    echo "    removed $DROPIN"
    changed=1
  fi
  if have_ack; then
    sudo rm -f "$ACK"
    echo "    removed $ACK"
    changed=1
  fi
  if [ "$changed" = 1 ]; then
    restart_and_check
    echo "    the BH-2 rollback is off. If a deploy failed on the expiry, run:"
    echo "    sudo MODE=force bash $APP_DIR/scripts/vps_pull.sh"
    # WS-49 BH-6: a deploy skips the sync of the root copy while a rollback
    # is on, and records its sha as applied. So no later pull syncs it.
    echo "    WARN BH-6: the root copy at /usr/local/lib/acb may be stale. A deploy"
    echo "    during the rollback skipped its sync. Run the forced deploy above to sync it."
  else
    echo "    the BH-2 rollback is already off"
    check_health
  fi
}

cmd_status() {
  read_state
  case "$STATE" in
    off)
      echo "BH-2 rollback: off"
      exit 1 ;;
    on)
      echo "BH-2 rollback: on, expires $(fmt_utc "$EXPIRES")"
      echo "WARN BH-2 rolled back until $(fmt_utc "$EXPIRES")"
      exit 0 ;;
    expired)
      echo "BH-2 rollback: EXPIRED ($EXPIRY_WHY)"
      exit 3 ;;
    partial-conf)
      echo "BH-2 rollback: PARTIAL, $DROPIN is there and $ACK is not"
      exit 3 ;;
    partial-ack)
      echo "BH-2 rollback: PARTIAL, $ACK is there and $DROPIN is not"
      exit 3 ;;
  esac
}

[ "$#" -eq 1 ] || usage
case "$1" in
  on) cmd_on ;;
  off) cmd_off ;;
  status) cmd_status ;;
  *) usage ;;
esac
