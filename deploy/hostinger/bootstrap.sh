#!/usr/bin/env bash
# One-time server bootstrap. Idempotent. Run as the `acb` user (non-root w/ sudo).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/acb/app}"
cd "$APP_DIR"

say()  { printf "\n==> %s\n" "$*"; }
warn() { printf "    !! %s\n" "$*" >&2; }

# 1. Sanity
say "Sanity"
[ "$EUID" -ne 0 ] || { warn "do not run as root; use the acb user"; exit 1; }
sudo -n true 2>/dev/null || { warn "user lacks sudo; fix usermod -aG sudo acb"; exit 1; }

# 2. Docker
say "Docker"
if ! command -v docker >/dev/null; then
  warn "docker not found; installing (Ubuntu 24.04)"
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  warn "you may need to log out/in for the docker group to take effect"
fi
docker --version
docker compose version

# 3. Firewall (UFW)
say "Firewall"
if ! command -v ufw >/dev/null; then sudo apt-get install -y ufw; fi
sudo ufw --force reset >/dev/null
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # Caddy HTTP (ACME challenge)
sudo ufw allow 443/tcp    # Caddy HTTPS
sudo ufw --force enable

# 4. Caddy (reverse proxy + auto-TLS)
say "Caddy"
if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y caddy
fi
sudo install -m 0644 "$APP_DIR/deploy/hostinger/caddy/Caddyfile" /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy || sudo systemctl restart caddy

# 5. .env
say "Environment file"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  warn "wrote $APP_DIR/.env from template; EDIT IT before running deploy.sh"
fi

# 6. GitHub Copilot SDK sandbox runtime (Tier 1.5 agents) — M2.6
# Every GitHub-sourced agent runs copilot.exe, which needs pwsh for its shell
# tool and a Copilot-enabled GITHUB_TOKEN. Missing any of these silently breaks
# the Tier 1.5 path, so we install + validate here and FAIL the bootstrap loudly.
say "GitHub Copilot SDK sandbox runtime"

# 6a. PowerShell (pwsh) — required by copilot.exe shell tool on Linux
if ! command -v pwsh >/dev/null; then
  warn "pwsh not found; installing PowerShell"
  source /etc/os-release
  sudo apt-get update -y
  sudo apt-get install -y wget apt-transport-https software-properties-common
  wget -q "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/packages-microsoft-prod.deb" \
    -O /tmp/packages-microsoft-prod.deb
  sudo dpkg -i /tmp/packages-microsoft-prod.deb
  rm -f /tmp/packages-microsoft-prod.deb
  sudo apt-get update -y
  sudo apt-get install -y powershell
fi
pwsh --version || { warn "pwsh install failed — copilot.exe shell tool will not work"; exit 1; }

# 6b. uv — manages per-agent-repo virtualenvs in the persistent clone cache
if ! command -v uv >/dev/null; then
  warn "uv not found; installing"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version || { warn "uv install failed"; exit 1; }

# 6c. Validate GITHUB_TOKEN before the gateway starts. A broken token silently
# degrades every github-copilot agent, so abort the bootstrap if it is invalid.
say "Validating GITHUB_TOKEN"
GH_TOKEN_VALUE="${GITHUB_TOKEN:-}"
if [ -z "$GH_TOKEN_VALUE" ] && [ -f "$APP_DIR/.env" ]; then
  GH_TOKEN_VALUE="$(grep -E '^GITHUB_TOKEN=' "$APP_DIR/.env" | head -n1 | cut -d= -f2- | tr -d '"')"
fi
if [ -z "$GH_TOKEN_VALUE" ]; then
  warn "GITHUB_TOKEN is not set in environment or .env — github-copilot agents will fail."
  warn "Set it before running deploy.sh (Integration Registry → GitHub)."
else
  GH_LOGIN="$(curl -fsS -H "Authorization: Bearer ${GH_TOKEN_VALUE}" \
    -H "X-GitHub-Api-Version: 2022-11-28" https://api.github.com/user \
    | grep -oE '"login"[[:space:]]*:[[:space:]]*"[^"]+"' | head -n1 | cut -d'"' -f4 || true)"
  if [ -n "$GH_LOGIN" ]; then
    printf "    GITHUB_TOKEN authenticated as: %s\n" "$GH_LOGIN"
  else
    warn "GITHUB_TOKEN failed api.github.com/user check — token is invalid or expired."
    exit 1
  fi
fi

# 7. systemd unit so the compose stack survives reboots
say "Systemd unit (acb.service)"
# WS-49 BH-8: the unit is a repo file now, deploy/hostinger/acb.service. This
# step wrote it from a heredoc, so no copy of it was in the repo. It also
# started the memory profile, which put Neo4j on the public internet.
# vps_apply.sh keeps the box copy in step with the repo file on each deploy.
# ⚠️ The repo file names /opt/acb/app. Another APP_DIR needs its own unit.
if [ "$APP_DIR" != "/opt/acb/app" ]; then
  warn "APP_DIR is $APP_DIR, but acb.service names /opt/acb/app. Edit the installed unit."
fi
sudo install -m 0644 "$APP_DIR/deploy/hostinger/acb.service" /etc/systemd/system/acb.service
sudo systemctl daemon-reload
sudo systemctl enable acb.service

cat <<'EOF'

OK. Bootstrap complete.

Next:
  1. nano /opt/acb/app/.env             # fill in secrets
  2. nano /opt/acb/app/deploy/hostinger/caddy/Caddyfile   # set your hostnames
  3. sudo systemctl reload caddy
  4. bash /opt/acb/app/deploy/hostinger/deploy.sh
EOF