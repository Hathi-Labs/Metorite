#!/bin/bash -l
# The Metorite dev toolchain for user dev on a dev or agent machine (task 1534).
# It mirrors scripts/ci_pc/bootstrap.sh, plus what an agent session needs to
# build, test and run the app. Run it after scripts/home_server/setup.sh.
# Safe to run again. Log: ~/dev-toolchain.log. Prove it with dev_verify.sh.
# docs/dev_capacity.md §5.1 says when to run it.
#
# Until task 2623 (2026-11-10) this lived only on metorite-home, as
# ~/.local/bin/metorite-dev-toolchain.
set -uo pipefail
exec > "$HOME/dev-toolchain.log" 2>&1
cd "$HOME/Metorite" || exit 1
ok=() bad=()
step() {
  local name="$1"; shift
  echo; echo "=== $(date +%T) $name"
  if "$@"; then ok+=("$name"); else bad+=("$name"); echo "!!! $name FAILED"; fi
}

step "repo on main" bash -c 'git checkout -q main && git pull -q --ff-only origin main && git log --oneline -1'
step "shellcheck" bash -c 'command -v shellcheck >/dev/null || sudo apt-get install -y -qq shellcheck >/dev/null; shellcheck --version | sed -n 2p'
step "python env" bash -c 'uv python install 3.12 >/dev/null && uv sync -q && uv run python --version'
for app in control_plane operator_console; do
  step "npm ci $app" bash -c "cd workbench/$app && npm ci --no-audit --no-fund --loglevel=error && ls node_modules | wc -l"
done
step "playwright os libs" bash -c 'cd workbench/control_plane && sudo -E env "PATH=$PATH" npx playwright install-deps chromium >/dev/null'
step "playwright chromium" bash -c 'cd workbench/control_plane && npx playwright install chromium >/dev/null && ls ~/.cache/ms-playwright'
step "gitleaks" bash -c '[ -x ~/.local/bin/gitleaks ] || { v=8.18.4; curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${v}/gitleaks_${v}_linux_x64.tar.gz" | tar -xz -C ~/.local/bin gitleaks; }; gitleaks version'
step "codegraph" bash -c 'command -v codegraph >/dev/null || npm install -g --prefix "$HOME/.local" --no-audit --no-fund --loglevel=error @colbymchenry/codegraph@1.2.0; codegraph --version'

echo; echo "=== $(date +%T) done"
echo "OK: ${ok[*]:-none}"
echo "FAILED: ${bad[*]:-none}"
