#!/usr/bin/env bash
# Pull latest images, bring the stack up, wait for healthchecks, run smoke.
# Idempotent. Safe to run on every git pull.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/acb/app}"
cd "$APP_DIR"

say()  { printf "\n==> %s\n" "$*"; }

# ── Preserve runtime agent registry ───────────────────────────────────────
# agents.json is mutated by the Control Plane UI (Add Agent).  If the caller
# did a `git reset --hard` before this script, the runtime registrations
# would be lost.  Restore from a pre-reset backup when available.
if [ -s /tmp/acb-agents.json.bak ]; then
  cp /tmp/acb-agents.json.bak apps/services/gateway/agents.json
  say "Restored runtime agents.json ($(wc -l < apps/services/gateway/agents.json) lines)"
fi

say "Pulling images"
docker compose -f infra/docker-compose.yml pull

# ── Memory-layer: disabled by default on 4GB VPS to save ~500MB RAM ──
# MEM0 (episodic) still works via Postgres+pgvector without Neo4j.
# Re-enable GRAPHITI_ENABLED=true + --profile memory after upgrading VPS.
say "Ensuring memory-layer env vars (Neo4j disabled for low-memory VPS)"
ENV_FILE="/opt/acb/app/.env"
for _var in MEM0_ENABLED GRAPHITI_ENABLED; do
  if ! grep -qE "^${_var}=" "$ENV_FILE" 2>/dev/null; then
    case "$_var" in
      MEM0_ENABLED)       echo "MEM0_ENABLED=true" >> "$ENV_FILE" ;;
      GRAPHITI_ENABLED)   echo "GRAPHITI_ENABLED=false" >> "$ENV_FILE" ;;
    esac
    printf "    + added %s to .env\n" "$_var"
  fi
done
# Ensure GRAPHITI stays off for this VPS size (idempotent)
if grep -qE '^GRAPHITI_ENABLED=true' "$ENV_FILE" 2>/dev/null; then
  sed -i 's/^GRAPHITI_ENABLED=true/GRAPHITI_ENABLED=false/' "$ENV_FILE"
  say "Disabled GRAPHITI (Neo4j) — saves ~500MB RAM; re-enable after VPS upgrade"
fi

say "Ensuring OAuth env vars"
for _var in MICROSOFT_TENANT_ID AUTH_MICROSOFT_ENTRA_ID_TENANT GATEWAY_PUBLIC_URL WORKBENCH_PUBLIC_URL; do
  if ! grep -qE "^${_var}=" "$ENV_FILE" 2>/dev/null; then
    case "$_var" in
      MICROSOFT_TENANT_ID)             echo "MICROSOFT_TENANT_ID=3a83c19d-ef37-4934-b61a-0d33750ca82e" >> "$ENV_FILE" ;;
      AUTH_MICROSOFT_ENTRA_ID_TENANT)   echo "AUTH_MICROSOFT_ENTRA_ID_TENANT=3a83c19d-ef37-4934-b61a-0d33750ca82e" >> "$ENV_FILE" ;;
      GATEWAY_PUBLIC_URL)              echo "GATEWAY_PUBLIC_URL=https://api.metorite.fracktal.in" >> "$ENV_FILE" ;;
      WORKBENCH_PUBLIC_URL)            echo "WORKBENCH_PUBLIC_URL=https://metorite.fracktal.in" >> "$ENV_FILE" ;;
    esac
    printf "    + added %s to .env\n" "$_var"
  fi
done

say "Booting stack (core only — memory profile disabled for 4GB VPS)"
set -a && source /opt/acb/app/.env && set +a
docker compose -f infra/docker-compose.yml --profile core up -d --remove-orphans

say "Waiting for healthchecks (up to 90s)"
deadline=$(( $(date +%s) + 90 ))
while [ $(date +%s) -lt $deadline ]; do
  unhealthy=$(docker ps --filter "label=com.docker.compose.project=acb" --format '{{.Names}}\t{{.Status}}' \
              | awk '$0 ~ /unhealthy|starting/ {print $1}')
  if [ -z "$unhealthy" ]; then break; fi
  sleep 3
done
docker ps --filter "label=com.docker.compose.project=acb" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

say "Applying database migrations (02+ — initdb only mounts 00/01)"
APP_DIR="$APP_DIR" bash scripts/apply_migrations.sh

say "Syncing python deps"
# `uv sync` on EVERY deploy, not only when .venv is absent.
#
# It used to be gated on `[ ! -d .venv ]`, which meant a dependency added to
# any workspace package reached a box exactly once — the day that box was
# first provisioned. Every deploy after that ran the new code against the old
# environment, and the symptom is an ImportError at request time rather than
# at deploy time. `uv sync` is a no-op costing ~1s when the lockfile has not
# moved, so the gate bought nothing and hid a whole class of failure.
if ! command -v uv >/dev/null; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync

# ── systemd units (BO-23) ────────────────────────────────────────────────────
# The repo is the source of truth for the box's units; without this step a unit
# added here only ever reaches the machine if somebody remembers to copy it by
# hand. That is how `acb-backup.timer` came to exist in the spec for days while
# nothing was scheduled — the tooling shipped and the schedule did not.
#
# Files only, plus `enable --now` for TIMERS. Services are deliberately left
# alone: restarting the gateway mid-deploy is the compose stack's job above,
# not this loop's, and a surprise restart here would be much harder to explain
# than a stale unit file.
say "Syncing systemd units"
UNITS_CHANGED=0
for unit in deploy/hostinger/*.service deploy/hostinger/*.timer; do
  [ -e "$unit" ] || continue
  name="$(basename "$unit")"
  if ! sudo cmp -s "$unit" "/etc/systemd/system/$name"; then
    sudo install -m 0644 "$unit" "/etc/systemd/system/$name"
    echo "  installed $name"
    UNITS_CHANGED=1
  fi
done
if [ "$UNITS_CHANGED" = "1" ]; then
  sudo systemctl daemon-reload
fi
for timer in deploy/hostinger/*.timer; do
  [ -e "$timer" ] || continue
  sudo systemctl enable --now "$(basename "$timer")" >/dev/null 2>&1 \
    || echo "  !! could not enable $(basename "$timer") — check: systemctl status $(basename "$timer")"
done
systemctl list-timers --no-pager 'acb-*' 2>/dev/null | head -5 || true

say "Restarting Caddy"
sudo systemctl restart caddy || true

say "Probing services"
uv run python scripts/check_infra.py || {
  echo "infra probe failed; check: docker compose -f infra/docker-compose.yml logs --tail=100"
  exit 1
}

echo
echo "Deployed. Visit https://<your-gateway-host>/health to sanity-check."