#!/usr/bin/env bash
# Generate random secrets and write them to /opt/acb/app/.env.
# Run ON the VPS: bash /opt/acb/app/scripts/setup_secrets.sh
set -euo pipefail
cd /opt/acb/app

POSTGRES_PW=$(openssl rand -hex 16)
LITELLM_KEY=$(openssl rand -hex 32)
SESSION_SECRET=$(openssl rand -hex 16)

echo "Generated random secrets"

# WS-49 BH-2: write INTO .env, so its inode stays. The gateway sandbox
# bind-mounts the inode of .env, and `sed -i` would leave a running gateway
# on the old, unlinked copy. Fence: tests/unit/test_env_inode.py.
edit_env() {
  local tmp
  tmp="$(mktemp ./.env-edit.XXXXXX)"
  sed "$1" .env > "$tmp"
  # The write truncates first. A failure in the middle leaves a short .env,
  # so compare, and keep the full copy when they differ.
  if ! cat "$tmp" > .env || ! cmp -s "$tmp" .env; then
    echo "!! .env write failed, the full copy is at $tmp" >&2
    exit 1
  fi
  rm -f "$tmp"
}

edit_env "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${POSTGRES_PW}/"
edit_env "s/^LITELLM_MASTER_KEY=.*/LITELLM_MASTER_KEY=${LITELLM_KEY}/"
edit_env "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://acb:${POSTGRES_PW}@localhost:5432/acb|"
edit_env "s/^GATEWAY_SESSION_SECRET=.*/GATEWAY_SESSION_SECRET=${SESSION_SECRET}/"

# Add missing vars
grep -q "^GATEWAY_INTERNAL_TOKEN=" .env || echo "GATEWAY_INTERNAL_TOKEN=${LITELLM_KEY}" >> .env
grep -q "^GATEWAY_BASE_URL=" .env || echo "GATEWAY_BASE_URL=http://127.0.0.1:8000" >> .env
grep -q "^EXECUTIVE_EMAILS=" .env || echo "# EXECUTIVE_EMAILS=ceo@fracktal.in,cto@fracktal.in" >> .env

echo ""
echo "Secrets configured:"
grep -E "^(POSTGRES_PASSWORD|LITELLM_MASTER_KEY|GATEWAY_SESSION_SECRET|GATEWAY_INTERNAL_TOKEN)" .env | sed 's/=.*/=***/'
