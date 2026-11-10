#!/usr/bin/env bash
# Provision the `metorite-ci` WSL2 distro: a preset Linux box for local CI.
#
# Run as ROOT inside the distro, once. `scripts/ci_pc/setup.sh` does it for you
# from Windows. Safe to run again: every step checks before it acts.
#
# What the distro is FOR: the whole pr-check suite runs here, on this PC, with
# every dependency already installed, so a run spends its time on tests and not
# on `uv sync`, `npm ci`, apt and image pulls (2-4 minutes per job on GitHub).
#
# ⚠️ ISOLATION, on purpose. CI runs code from a branch. So this distro:
#   * does NOT mount the Windows drives (`automount.enabled=false`), so it
#     cannot read ~/.metorite/secrets, SSH keys or anything else on C:;
#   * cannot start a Windows program (`interop.enabled=false`), which would
#     otherwise run as YOUR Windows user with full access;
#   * runs its own Docker engine, not Docker Desktop's.
# The code reaches it by `git fetch` or by a bundle on stdin, never by a mount.
set -euo pipefail

# Check LINUX paths, never `command -v`: until wsl.conf applies, the Windows
# PATH is visible here, and `docker` or `node` would find the Windows binary.

CI_USER=ci
export DEBIAN_FRONTEND=noninteractive

echo "== base packages"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git build-essential unzip \
  python3 python3-venv lsb-release >/dev/null

echo "== the ci user (no sudo: it never needs root)"
id -u "$CI_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$CI_USER"

echo "== postgresql-client-16 (pg_dump must not be older than the server)"
if [ ! -x /usr/bin/pg_dump ] || ! /usr/bin/pg_dump --version | grep -q ' 16\.'; then
  install -d /usr/share/keyrings
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor --yes -o /usr/share/keyrings/pgdg.gpg
  echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
  apt-get update -qq && apt-get install -y -qq postgresql-client-16 >/dev/null
fi

echo "== Docker engine"
if [ ! -x /usr/bin/dockerd ]; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq && apt-get install -y -qq docker-ce docker-ce-cli containerd.io >/dev/null
fi
usermod -aG docker "$CI_USER"

echo "== Node 20 (the version pr-check uses)"
if [ ! -x /usr/bin/node ] || ! /usr/bin/node --version | grep -q '^v20\.'; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi

echo "== Chromium's system libraries, for the browser suite"
# The browser itself is per-user (~/.cache/ms-playwright). Its OS libraries
# need root, so they are installed here, once, and the ci user needs no sudo.
/usr/bin/npx -y playwright@1 install-deps chromium >/dev/null

echo "== uv, for the ci user"
su - "$CI_USER" -c '[ -x ~/.local/bin/uv ] || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null'

echo "== wsl.conf: systemd on, no Windows drives, no Windows programs"
cat > /etc/wsl.conf <<'CONF'
# Written by scripts/ci_pc/provision.sh. See that file for why.
[boot]
systemd = true

[user]
default = ci

[automount]
enabled = false

[interop]
enabled = false
appendWindowsPath = false
CONF

echo "provision: OK. Restart the distro to apply wsl.conf: wsl --terminate metorite-ci"
