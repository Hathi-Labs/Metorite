#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Turn the dev box into the STAGING box, dev.metorite.com (WS-38 S-5,
# project-docs/specs/staging_and_daily_release.md §5.2 to §5.5 and §5.10).
#
# Run ONCE, as root, from any checkout of the repo on the dev box:
#
#   sudo bash deploy/hostinger/bootstrap_staging.sh --secrets /root/staging.secrets
#
# It is safe to run again. Each step checks first, and a value that exists is
# kept, so a second run never rotates a secret.
#
# --secrets FILE is optional. It holds NAME=VALUE lines, and only these names
# are read: AUTH_GOOGLE_ID, AUTH_GOOGLE_SECRET, AUTH_MICROSOFT_ENTRA_ID_ID,
# AUTH_MICROSOFT_ENTRA_ID_SECRET (the STAGING OAuth clients, never production's)
# and CUSTOMER_CONSOLE_ORG_KEY (the `Metorite Staging` organization's key).
# Without them staging serves pages, but nobody can sign in and the chat has
# no model.
#
# What it does, in order (§5.10 is the list it must satisfy):
#   1. Refuses on the production box, and on a box whose .env says otherwise.
#   2. Turns on the firewall: 22, 80 and 443 in, nothing else.
#   3. Installs caddy, rsync, the Postgres client, and a private Node 20.20.2
#      for the acb user. Production runs that version. The box's own Node is
#      not changed, so the agent sessions keep theirs.
#   4. Makes the acb user, as on production, and its staging link key.
#   5. Refuses while another program holds a port that staging needs.
#   6. Clones the repo to /opt/acb/app at the `staging` ref.
#   7. Writes the staging env files: fresh secrets, the staging hosts, and
#      every outbound switch OFF. It holds no production secret.
#   8. Starts the staging database, and rebuilds it from the production copy
#      when the production box already lets the link key in. Otherwise it
#      prints the one line the production box needs, and stops there.
#
# Fence: tests/unit/test_staging_box.py.
# ════════════════════════════════════════════════════════════════════════════
set -euo pipefail

app=/opt/acb/app
repo=https://github.com/Hathi-Labs/Metorite.git
prod_ip=187.127.172.200
web=https://dev.metorite.com
api=https://api.dev.metorite.com
node_ver=v20.20.2
secrets_file=""
[ "${1:-}" = "--secrets" ] && secrets_file="${2:?--secrets needs a file}"

say()  { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() { printf '\n!! %s\n' "$*" >&2; exit 1; }
val()  { { grep -E "^$1=" "$2" 2>/dev/null || true; } | tail -1 | cut -d= -f2-; }
gen()  { openssl rand -hex 24; }
# set_kv FILE NAME VALUE [keep]: write NAME=VALUE. With `keep`, an existing
# value stays. The file keeps its mode, and its temp file is private.
set_kv() {
  local file="$1" name="$2" value="$3" keep="${4:-}"
  if [ "$keep" = "keep" ] && grep -qE "^$name=." "$file" 2>/dev/null; then return 0; fi
  ( umask 077; { grep -v "^$name=" "$file" 2>/dev/null || true; } > "$file.tmp" )
  printf '%s=%s\n' "$name" "$value" >> "$file.tmp"
  cat "$file.tmp" > "$file"; rm -f "$file.tmp"
}

# ── 1. Never on production ──────────────────────────────────────────────────
say "Checking that this is not the production box"
if hostname -I | tr ' ' '\n' | grep -qxF "$prod_ip"; then
  fail "this box has the production address $prod_ip. Refusing."
fi
if grep -qE '^(app|api)\.metorite\.com' /etc/caddy/Caddyfile 2>/dev/null; then
  fail "/etc/caddy/Caddyfile serves a production host. Refusing."
fi
if [ -f "$app/.env" ] && [ "$(val DEPLOY_ENV "$app/.env")" != "staging" ]; then
  fail "$app/.env exists and does not say DEPLOY_ENV=staging. Refusing."
fi
[ "$(id -u)" = "0" ] || fail "run as root: sudo bash $0"
declare -A secret=()
if [ -n "$secrets_file" ]; then
  [ -r "$secrets_file" ] || fail "cannot read $secrets_file"
  for name in AUTH_GOOGLE_ID AUTH_GOOGLE_SECRET AUTH_MICROSOFT_ENTRA_ID_ID \
              AUTH_MICROSOFT_ENTRA_ID_SECRET CUSTOMER_CONSOLE_ORG_KEY; do
    v="$(val "$name" "$secrets_file")"
    [ -z "$v" ] && continue
    [[ "$v" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || fail "$name in $secrets_file holds a character that is not allowed"
    secret[$name]="$v"
  done
  note "read ${#secret[@]} secret(s) from $secrets_file"
fi

# ── 2. Firewall first ───────────────────────────────────────────────────────
# The gateway listens on 0.0.0.0:8080. Without this, a request reaches it
# directly, past the identity-header strip of Caddyfile.staging (§5.10 item 1).
say "Firewall: 22, 80 and 443 in, nothing else"
command -v ufw >/dev/null || apt-get install -y -qq ufw >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw --force enable >/dev/null
ufw status | sed 's/^/    /'

# ── 3. Packages ─────────────────────────────────────────────────────────────
say "Packages"
command -v docker >/dev/null || fail "docker is not installed. Install it, then run this again."
apt-get update -qq
apt-get install -y -qq git rsync postgresql-client zstd curl ca-certificates gnupg \
  debian-keyring debian-archive-keyring apt-transport-https >/dev/null
if ! command -v caddy >/dev/null; then
  [ -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg ] || \
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  [ -f /etc/apt/sources.list.d/caddy-stable.list ] || \
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
      > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi
note "caddy $(caddy version | cut -d' ' -f1)"

# ── 4. The acb user ─────────────────────────────────────────────────────────
say "The acb user"
id acb >/dev/null 2>&1 || useradd -m -s /bin/bash acb
usermod -aG docker acb
# vps_apply.sh runs `sudo` for systemctl, install and caddy, as on production.
# The app units run with NoNewPrivileges, so the app process cannot use it.
printf 'acb ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/acb-staging
chmod 0440 /etc/sudoers.d/acb-staging
visudo -cf /etc/sudoers.d/acb-staging >/dev/null
install -d -m 0755 -o acb -g acb /home/acb/.local /home/acb/.local/bin /opt/acb /opt/acb/data /var/lib/acb
install -d -m 0700 -o acb -g acb /home/acb/.ssh
[ -x /home/acb/.local/bin/uv ] || sudo -u acb -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
# Node 20.20.2, private to acb. Every acb unit puts /home/acb/.local/bin first
# in PATH, so the staging build uses production's version.
if [ "$(/home/acb/.local/bin/node -v 2>/dev/null || true)" != "$node_ver" ]; then
  tarball="node-$node_ver-linux-x64.tar.xz"
  tmpd="$(mktemp -d)"
  curl -fsSL "https://nodejs.org/dist/$node_ver/$tarball" -o "$tmpd/$tarball"
  curl -fsSL "https://nodejs.org/dist/$node_ver/SHASUMS256.txt" -o "$tmpd/SHASUMS256.txt"
  (cd "$tmpd" && grep " $tarball\$" SHASUMS256.txt | sha256sum -c --quiet -) || fail "the Node tarball failed its checksum"
  rm -rf /home/acb/.local/node && install -d -o acb -g acb /home/acb/.local/node
  tar -xJf "$tmpd/$tarball" -C /home/acb/.local/node --strip-components=1
  chown -R acb:acb /home/acb/.local/node
  for b in node npm npx; do ln -sf /home/acb/.local/node/bin/$b /home/acb/.local/bin/$b; done
  rm -rf "$tmpd"
fi
note "node $(/home/acb/.local/bin/node -v) for acb (the box keeps $(node -v 2>/dev/null || echo none))"
[ -f /home/acb/.ssh/staging_link ] || \
  sudo -u acb ssh-keygen -q -t ed25519 -N '' -C acb-staging-link -f /home/acb/.ssh/staging_link

# ── 5. Ports ────────────────────────────────────────────────────────────────
# 3001 matters most: an agent's `next dev -p 3001` there would be served at
# dev.metorite.com during a staging restart (§5.10 item 6).
say "Ports"
busy=""
for p in 80 443 3001 8080 8091 5435 18090; do
  holder="$(ss -ltnpH "sport = :$p" 2>/dev/null | grep -oE 'users:\(\("[^"]+",pid=[0-9]+' | head -1 || true)"
  [ -z "$holder" ] && continue
  pid="${holder##*pid=}"
  owner="$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  comm="$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  # docker-proxy runs as root for ANY user's container, so it proves nothing,
  # except on 5435, which is the staging database's own container.
  if [ "$comm" = "docker-proxy" ] && [ "$p" != "5435" ]; then owner="docker:$comm"; fi
  case "$owner" in acb|root|caddy) ;; *) busy="$busy $p(${holder#users:((\"}, user $owner)" ;; esac
done
[ -z "$busy" ] || fail "these ports are held by another user:$busy. Stop those programs, then run this again."
note "free, or held by staging itself"

# ── 6. The checkout ─────────────────────────────────────────────────────────
say "The checkout at $app"
[ -d "$app/.git" ] || sudo -u acb git clone -q "$repo" "$app"
sudo -u acb git -C "$app" fetch -q origin main release staging
[ -f "$app/.env" ] || sudo -u acb git -C "$app" checkout -q --detach origin/staging
note "at $(sudo -u acb git -C "$app" rev-parse --short HEAD)"
# The production box's host keys, pinned in the repo (H-200) with NO host
# field. ssh needs one, so the address is added here, as ci_pin_host_key does.
grep -vE '^(#|$)' "$app/deploy/hostinger/known_hosts" | sed "s/^/$prod_ip /" > /home/acb/.ssh/known_hosts_prod
chown acb:acb /home/acb/.ssh/known_hosts_prod && chmod 0644 /home/acb/.ssh/known_hosts_prod

# ── 7. The env files ────────────────────────────────────────────────────────
say "The staging env files (no production secret)"
install -d -m 0755 /etc/acb
for f in /etc/acb/staging-db.env /etc/acb/staging.env; do
  [ -f "$f" ] || ( umask 077; : > "$f" )
done
set_kv /etc/acb/staging-db.env POSTGRES_PASSWORD "$(gen)" keep
set_kv /etc/acb/staging.env STAGING_PROD_HOST "$prod_ip" keep
set_kv /etc/acb/staging.env STAGING_APP_DB_PW "$(gen)" keep
db_pw="$(val POSTGRES_PASSWORD /etc/acb/staging-db.env)"
app_pw="$(val STAGING_APP_DB_PW /etc/acb/staging.env)"

env="$app/.env"
[ -f "$env" ] || { install -m 0600 -o acb -g acb /dev/null "$env"; }
set_kv "$env" DEPLOY_ENV staging
# Fresh, and never production's. Without production's ACB_MASTER_KEY, the
# credentials in the copy stay ciphertext (they are blanked anyway).
set_kv "$env" ACB_MASTER_KEY "$(gen)" keep
set_kv "$env" GATEWAY_INTERNAL_TOKEN "$(gen)" keep
set_kv "$env" GATEWAY_PUBLIC_URL "$api"
set_kv "$env" WORKBENCH_PUBLIC_URL "$web"
set_kv "$env" AGENT_AUTHORIZE_URL "$web/connect/authorize"
set_kv "$env" SMOKE_BASE_URL "$web"
set_kv "$env" POSTGRES_USER postgres
set_kv "$env" POSTGRES_DB postgres
set_kv "$env" POSTGRES_PASSWORD "$db_pw"
set_kv "$env" PG_MODE local
set_kv "$env" PGHOST 127.0.0.1
set_kv "$env" PGPORT 5435
set_kv "$env" PGPASSWORD "$db_pw"
set_kv "$env" PGSSLMODE disable
# The database is rebuilt each night, so a pre-migration dump protects nothing.
# It would also fail: this box's pg_dump is 16, and the server is 17.
set_kv "$env" SKIP_PRE_MIGRATION_BACKUP 1
set_kv "$env" DATABASE_URL "postgresql+psycopg://acb_app:$app_pw@127.0.0.1:5435/postgres"
set_kv "$env" REDIS_URL "redis://localhost:6379/0"
set_kv "$env" CUSTOMER_CONSOLE_URL "http://127.0.0.1:18090"
# Every switch that reaches a person or a third party: OFF (§5.10 item 3).
for off in WHATSAPP_BRIDGE_ENABLED=0 MEETING_BOT_ENABLED=0 EMAIL_BACKGROUND_SYNC_ENABLED=false \
           EMAIL_SYNC_ENABLED=false MEMBER_INVITE_EMAIL_ENABLED=false REACH_ENABLED=false \
           WHATSAPP_ASSISTANT_ENABLED=false; do
  set_kv "$env" "${off%%=*}" "${off#*=}"
done
[ -n "${secret[CUSTOMER_CONSOLE_ORG_KEY]:-}" ] && set_kv "$env" CUSTOMER_CONSOLE_ORG_KEY "${secret[CUSTOMER_CONSOLE_ORG_KEY]}"
chown acb:acb "$env" && chmod 0600 "$env"

wb="$app/workbench/control_plane/.env.local"
[ -f "$wb" ] || { install -m 0600 -o acb -g acb /dev/null "$wb"; }
set_kv "$wb" AUTH_SECRET "$(gen)" keep
set_kv "$wb" AUTH_TRUST_HOST true
set_kv "$wb" AUTH_URL "$web"
set_kv "$wb" GATEWAY_BASE_URL "http://127.0.0.1:8080"
set_kv "$wb" GATEWAY_INTERNAL_TOKEN "$(val GATEWAY_INTERNAL_TOKEN "$env")"
set_kv "$wb" CUSTOMER_CONSOLE_URL "http://127.0.0.1:18090"
set_kv "$wb" WORKBENCH_PUBLIC_URL "$web"
set_kv "$wb" DEPLOY_ENV staging
for off in MEMBER_INVITE_EMAIL_ENABLED=false EMAIL_OTP_ENABLED=false \
           CUSTOMER_CONSOLE_RESOLVE_ENABLED=false SELF_SERVE_SIGNUP_ENABLED=false; do
  set_kv "$wb" "${off%%=*}" "${off#*=}"
done
for name in AUTH_GOOGLE_ID AUTH_GOOGLE_SECRET AUTH_MICROSOFT_ENTRA_ID_ID \
            AUTH_MICROSOFT_ENTRA_ID_SECRET CUSTOMER_CONSOLE_ORG_KEY; do
  [ -n "${secret[$name]:-}" ] && set_kv "$wb" "$name" "${secret[$name]}"
done
chown acb:acb "$wb" && chmod 0600 "$wb"
note "written: $env, $wb, /etc/acb/staging.env, /etc/acb/staging-db.env"

# ── 8. The database, and the first rebuild ──────────────────────────────────
say "The staging database"
for u in "$app"/deploy/hostinger/staging/*.service "$app"/deploy/hostinger/staging/*.timer \
         "$app"/deploy/hostinger/staging/*.slice; do
  install -m 0644 "$u" /etc/systemd/system/
done
install -d -m 0755 -o root -g root /usr/local/lib/acb-staging
install -m 0755 -o root -g root "$app/scripts/staging_refresh.sh" /usr/local/lib/acb-staging/staging_refresh.sh
systemctl daemon-reload
systemctl enable --now acb-staging-db.service >/dev/null
for _ in $(seq 1 60); do
  docker exec acb-staging-db pg_isready -q -U postgres 2>/dev/null && break
  sleep 2
done
docker exec acb-staging-db pg_isready -q -U postgres || fail "acb-staging-db did not start"
note "acb-staging-db is up on 127.0.0.1:5435"

line="restrict,port-forwarding,permitopen=\"127.0.0.1:8090\",permitlisten=\"127.0.0.1:1\",command=\"/usr/bin/rrsync -ro /var/lib/acb-staging-export/current\" $(cut -d' ' -f1,2 /home/acb/.ssh/staging_link.pub) acb-staging-link"
if sudo -u acb rsync --list-only \
     -e "ssh -i /home/acb/.ssh/staging_link -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/acb/.ssh/known_hosts_prod" \
     "acb@$prod_ip:./" >/dev/null 2>&1; then
  say "The production copy is reachable. Rebuilding staging from it"
  bash /usr/local/lib/acb-staging/staging_refresh.sh --force
  say "Staging is up at $web"
else
  say "ONE STEP LEFT, on the production box"
  note "Add this line to /home/acb/.ssh/authorized_keys on $prod_ip:"
  note ""
  note "$line"
  note ""
  note "Then run, here:  sudo systemctl start acb-staging-refresh.service"
  note "It pulls last night's copy, rebuilds the database and deploys the staging ref."
fi
