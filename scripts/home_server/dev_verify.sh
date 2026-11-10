#!/bin/bash -l
# Prove the Metorite dev toolchain on a dev or agent machine works end to end
# (task 1534). Run it after dev_toolchain.sh. Every step must pass, and the
# R8 test must not skip. Log: ~/dev-verify.log. docs/dev_capacity.md §5.1.
#
# Until task 2623 (2026-11-10) this lived only on metorite-home, as
# ~/.local/bin/metorite-dev-verify.
set -uo pipefail
exec > "$HOME/dev-verify.log" 2>&1
cd "$HOME/Metorite" || exit 1
ok=() bad=()
step() {
  local name="$1"; shift
  echo; echo "=== $(date +%T) $name"
  if "$@"; then ok+=("$name"); else bad+=("$name"); echo "!!! $name FAILED"; fi
}

step "codegraph index" bash -c 'codegraph init . && codegraph status . | head -12'
step "unit tests, no DB" uv run pytest tests/unit/test_task_routine.py tests/unit/test_agent_model_routing.py -q -p no:cacheprovider
step "dev DB up" bash scripts/dev_db.sh
step "R8 test on the real DB" bash -c 'eval "$(bash scripts/dev_db.sh --export)" && uv run pytest tests/unit/test_workflows_lifecycle_rls_r8.py -q -rs -p no:cacheprovider | tail -5 && ! uv run pytest tests/unit/test_workflows_lifecycle_rls_r8.py -q -rs -p no:cacheprovider | grep -q " skipped"'
step "typecheck control_plane" bash -c 'cd workbench/control_plane && NODE_OPTIONS=--max-old-space-size=4096 npx tsc --noEmit && echo tsc clean'
step "dev_up start" bash scripts/dev_up.sh
step "dev_up check" bash scripts/dev_up.sh --check
step "dev_up status" bash scripts/dev_up.sh --status
step "dev_up stop" bash scripts/dev_up.sh --stop

echo; echo "=== $(date +%T) done"
echo "OK: ${ok[*]:-none}"
echo "FAILED: ${bad[*]:-none}"
