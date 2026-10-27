#!/usr/bin/env bash
# Preset the `metorite-ci` distro for local CI: the repo, the Python and Node
# dependencies, the browser, and the Docker images. Run as the `ci` user,
# after provision.sh. `scripts/ci_pc/setup.sh` does it for you from Windows.
#
# Safe to run again. run_suite.sh refreshes the dependencies itself when a
# lockfile moves, so you only run this once, or to repair the distro.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
REPO="$HOME/metorite"

echo "== the repo (a clone from GitHub, never a mount of C:)"
[ -d "$REPO/.git" ] || git clone -q https://github.com/Hathi-Labs/Metorite.git "$REPO"
cd "$REPO"
git fetch -q origin
git checkout -q -f --detach origin/main

echo "== Python 3.12 and the project environment"
uv python install 3.12 >/dev/null
uv sync -q

echo "== Node dependencies, both apps"
for app in control_plane operator_console; do
  (cd "workbench/$app" && npm ci --no-audit --no-fund --loglevel=error \
    && sha256sum package-lock.json | cut -d' ' -f1 > node_modules/.ci-lock)
done

echo "== Chromium for the browser suite (its OS libraries came from provision.sh)"
(cd workbench/control_plane && npx playwright install chromium >/dev/null)

echo "== gitleaks, the version pr-check pins"
if [ ! -x "$HOME/.local/bin/gitleaks" ]; then
  v=8.18.4
  curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${v}/gitleaks_${v}_linux_x64.tar.gz" \
    | tar -xz -C "$HOME/.local/bin" gitleaks
fi

echo "== Docker images the jobs use"
for img in postgres:16 pgvector/pgvector:pg16 caddy:2.11.4; do docker pull -q "$img" >/dev/null; done

if [ -f scripts/ci_pc/Dockerfile ]; then
  echo "== the job image (each job runs in it, on a private network)"
  docker build -q --build-arg CI_UID="$(id -u)" --build-arg CI_GID="$(id -g)" \
    -t "metorite-ci-job:$(sha256sum scripts/ci_pc/Dockerfile | cut -c1-12)-$(id -u)-$(id -g)" scripts/ci_pc >/dev/null
fi

echo "bootstrap: OK. Run from Windows: bash scripts/ci_local.sh"
