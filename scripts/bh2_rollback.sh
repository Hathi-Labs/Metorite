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
# `status` exit codes are the contract that the BH-2 strict check reads:
#   0  the rollback is on, and its expiry is in the future (prints WARN)
#   1  the rollback is off: no conf, no ack
#   3  the rollback is expired, malformed, or half there
#   2  usage
set -euo pipefail

UNIT="acb-gateway"
DROPIN_DIR="/etc/systemd/system/${UNIT}.service.d"
DROPIN="$DROPIN_DIR/90-bh2-off.conf"
ACK_DIR="/etc/acb"
ACK="$ACK_DIR/bh2-rollback-ack"
TTL_SECONDS=$((72 * 3600))
HEALTH_URL="http://127.0.0.1:8080/health"
HEALTH_TRIES=120

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_CONF="$APP_DIR/deploy/hostinger/rollback/acb-gateway-90-bh2-off.conf"

usage() {
  echo "usage: bh2_rollback.sh on|off|status" >&2
  exit 2
}

now_epoch() { date -u +%s; }

fmt_utc() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

# Prints the expiry epoch of the ack, or nothing when the file is absent or
# malformed. A malformed file counts as expired (fail closed).
ack_expiry() {
  sudo test -f "$ACK" || return 0
  sudo cat "$ACK" 2>/dev/null | sed -n 's/^EXPIRES_EPOCH=\([0-9][0-9]*\)$/\1/p' | tail -n 1
}

have_ack() { sudo test -f "$ACK"; }
have_dropin() { sudo test -f "$DROPIN"; }

# Sets STATE to one of: off, on, expired, partial-conf, partial-ack.
# Sets EXPIRES to the epoch, or to an empty string.
read_state() {
  local conf=0 ack=0 now
  have_dropin && conf=1
  have_ack && ack=1
  EXPIRES="$(ack_expiry)"
  now="$(now_epoch)"
  if [ "$conf" = 0 ] && [ "$ack" = 0 ]; then
    STATE="off"
  elif [ "$ack" = 1 ] && { [ -z "$EXPIRES" ] || [ "$now" -ge "$EXPIRES" ]; }; then
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
  read_state
  if [ "$STATE" = "expired" ]; then
    echo "!! the BH-2 rollback expired or its ack is malformed." >&2
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
      if [ -n "$EXPIRES" ]; then
        echo "BH-2 rollback: EXPIRED at $(fmt_utc "$EXPIRES")"
      else
        echo "BH-2 rollback: EXPIRED (the ack holds no valid EXPIRES_EPOCH line)"
      fi
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
