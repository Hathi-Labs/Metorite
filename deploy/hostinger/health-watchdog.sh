#!/usr/bin/env bash
# Metorite on-box health watchdog.
#
# Runs from a systemd timer (acb-health-watchdog.timer). Checks every service
# that has to be up for the app to serve, restarts whatever is down, and
# records a forensic snapshot of network/firewall state on every run.
#
# WHY ON-BOX AND NOT JUST CI: the outages this was written for are ones where
# the VPS is alive but unreachable from the internet. A GitHub Actions health
# check cannot log in to fix that — it is blocked by the very failure it is
# meant to repair. This script keeps working because it never leaves the box.
# The CI workflow (.github/workflows/vps-health.yml) is the external alarm for
# the case this script cannot fix: box healthy locally, still unreachable.
#
# Safe to run by hand:  sudo /opt/acb/app/deploy/hostinger/health-watchdog.sh
# Check mode (no restarts):  ... health-watchdog.sh --dry-run

set -uo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

LOG_DIR=/var/log/acb
LOG=$LOG_DIR/health-watchdog.log
FORENSICS=$LOG_DIR/net-forensics.log
mkdir -p "$LOG_DIR"

# Gateway hostname as served by Caddy — see deploy/hostinger/caddy/Caddyfile.
CADDY_VHOST="${CADDY_VHOST:-api.metorite.com}"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) $*" | tee -a "$LOG"; }

ACTIONS=0
FAILED=0

# ── Startup grace (H-13c) ─────────────────────────────────────────────────
#
# ⚠️ WITHOUT THIS THE WATCHDOG IS A KILL LOOP, and it was one on the box.
#
# The gateway cold-starts in roughly 90–105 seconds — awaited warm-clone
# timeouts, not a hang. The watchdog probes far sooner, gets no answer because
# the service is still starting, restarts it, and the clock goes back to zero.
# The next run does the same. The service never reaches "startup complete", and
# every restart looks like the watchdog working.
#
# So a unit that only RECENTLY entered its current state is left alone. 180s is
# comfortably past the observed cold start with room for a slow boot; below
# ~120s the loop can still close.
#
# This lived only on the box until 2026-08-31, which meant every git-reset
# deploy re-armed the loop until somebody noticed and re-patched by hand.
GRACE_SECONDS="${WATCHDOG_GRACE_SECONDS:-180}"

# Seconds since a unit last entered the active state. Prints a huge number when
# systemd has no timestamp for it (never started, or the property is empty), so
# an unparseable answer NEVER reads as "just started" and never suppresses a
# restart that is genuinely needed. Failing towards acting is the safe
# direction for a watchdog.
unit_age_seconds() {
  local stamp epoch
  stamp="$(systemctl show -p ActiveEnterTimestamp --value "$1" 2>/dev/null || true)"
  [ -z "$stamp" ] && { echo 999999; return; }
  epoch="$(date -d "$stamp" +%s 2>/dev/null || true)"
  [ -z "$epoch" ] && { echo 999999; return; }
  echo $(( $(date +%s) - epoch ))
}

# True while a unit is inside its startup grace.
starting_up() {
  local age
  age="$(unit_age_seconds "$1")"
  [ "$age" -lt "$GRACE_SECONDS" ]
}

# ── Units that must be running ────────────────────────────────────────────
# acb.service is the docker compose stack (postgres/redis). The gateway
# Requires= it, so if it is down the gateway cannot come up — check it first
# and in this order.
UNITS="acb.service acb-gateway acb-workbench caddy"

for unit in $UNITS; do
  if systemctl is-active --quiet "$unit"; then
    log "OK      unit $unit active"
    continue
  fi
  state="$(systemctl is-active "$unit" 2>&1)"
  # `is-active --quiet` fails for `activating` too, so a unit part-way through
  # its own startup reads as DOWN here. Restarting it is the same kill loop the
  # HTTP grace prevents, one level up: systemd is already doing the thing we
  # would be asking it to do.
  if [ "$state" = "activating" ]; then
    log "WAIT    unit $unit is activating, leaving it alone"
    continue
  fi
  log "DOWN    unit $unit is $state"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRYRUN  would restart $unit"
    continue
  fi
  log "ACTION  restarting $unit"
  systemctl restart "$unit" && ACTIONS=$((ACTIONS+1)) || true
  sleep 5
  if systemctl is-active --quiet "$unit"; then
    log "FIXED   $unit is active again"
  else
    log "FAIL    $unit STILL not active after restart"
    FAILED=$((FAILED+1))
    journalctl -u "$unit" --no-pager -n 30 >>"$LOG" 2>&1 || true
  fi
done

# Units are not enough: a unit can be "active" while the process inside is
# wedged and answering nothing. Probe the actual listeners.
#   gateway   127.0.0.1:8080/health   (FastAPI)
#   workbench 127.0.0.1:3001          (Next.js — any HTTP status means alive)
#   caddy     127.0.0.1:443           (TLS front door for both hostnames)
# A response counts ONLY if curl reported a real 3-digit HTTP status. Testing
# `!= 000` is not enough: a failed probe can yield an empty or repeated value
# (we observed "000000"), which would sail through and report a false OK — a
# watchdog that lies is worse than no watchdog.
http_ok() { case "$1" in [1-5][0-9][0-9]) return 0 ;; *) return 1 ;; esac; }

probe_http() {
  # $1 unit, $2 url, $3 human label, $4... extra curl args (e.g. --resolve)
  local unit="$1" url="$2" label="$3" code
  shift 3
  code="$(curl -s -k -o /dev/null -m 15 -w '%{http_code}' "$@" "$url" 2>/dev/null || echo 000)"
  if http_ok "$code"; then
    log "OK      $label responding (HTTP $code)"
    return 0
  fi
  # ⚠️ The kill loop lived HERE, not in the unit check above. A cold-starting
  # gateway is `active` to systemd long before it answers /health, so the unit
  # loop passes and this probe is what used to restart it — every run, forever.
  if starting_up "$unit"; then
    log "WAIT    $label not up yet, $unit started $(unit_age_seconds "$unit")s ago (grace ${GRACE_SECONDS}s)"
    return 0
  fi
  log "DOWN    $label not responding at $url"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRYRUN  would restart $unit"
    return 1
  fi
  log "ACTION  restarting $unit (unresponsive listener)"
  systemctl restart "$unit" && ACTIONS=$((ACTIONS+1)) || true
  sleep 8
  code="$(curl -s -k -o /dev/null -m 15 -w '%{http_code}' "$@" "$url" 2>/dev/null || echo 000)"
  if http_ok "$code"; then
    log "FIXED   $label responding again (HTTP $code)"
  else
    log "FAIL    $label STILL not responding after restart"
    FAILED=$((FAILED+1))
    journalctl -u "$unit" --no-pager -n 30 >>"$LOG" 2>&1 || true
  fi
}

probe_http acb-gateway   http://127.0.0.1:8080/health "gateway"
probe_http acb-workbench http://127.0.0.1:3001/       "workbench"
# Caddy must be probed through a hostname it actually serves. A bare
# https://127.0.0.1/ matches no site block in the Caddyfile, so it fails even
# when Caddy is perfectly healthy. --resolve keeps the request local (no DNS,
# no round trip to the public internet) while presenting the right SNI+Host.
probe_http caddy "https://${CADDY_VHOST}/health" "caddy TLS" \
  --resolve "${CADDY_VHOST}:443:127.0.0.1"

# ── Forensic snapshot ─────────────────────────────────────────────────────
# The recurring failure is "box alive, internet cannot reach it". That leaves
# no trace once it clears, so capture the state that would explain it EVERY
# run. When the next outage happens this log is the evidence.
{
  echo "───────── $(ts) ─────────"
  echo "## uptime";           uptime
  echo "## listeners";        ss -lntp 2>/dev/null | head -30
  echo "## default route";    ip route show default
  echo "## ufw";              ufw status verbose 2>/dev/null | head -30 || echo "(ufw absent)"
  echo "## iptables INPUT";   iptables -S INPUT 2>/dev/null | head -40 || echo "(iptables unreadable)"
  echo "## iptables policy";  iptables -L -n 2>/dev/null | head -15 || true
  echo "## fail2ban";         fail2ban-client status 2>/dev/null || echo "(fail2ban absent)"
  echo "## banned IPs";       fail2ban-client status sshd 2>/dev/null || true
  echo "## egress check";     curl -s -o /dev/null -m 10 -w 'api.github.com -> %{http_code}\n' https://api.github.com || echo "egress FAILED"
  echo "## recent kernel net/oom";
  dmesg 2>/dev/null | grep -iE 'oom|killed process|net|eth0|link' | tail -15 || true
} >>"$FORENSICS" 2>&1

# Keep the logs from growing without bound.
for f in "$LOG" "$FORENSICS"; do
  if [ -f "$f" ] && [ "$(stat -c %s "$f" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    tail -c 2097152 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    log "rotated $f"
  fi
done

log "SUMMARY restarts=$ACTIONS still_failing=$FAILED"
# Non-zero only when something is still broken after we tried to fix it, so a
# successful self-heal does not spam the systemd unit as failed.
[ "$FAILED" -eq 0 ]
