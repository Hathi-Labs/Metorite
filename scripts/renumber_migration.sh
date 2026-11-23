#!/usr/bin/env bash
# renumber_migration.sh <old_number> [new_number] [--dir infra/postgres] [--no-test]
# Moves one migration to a new number and rewrites every reference to its file
# name. With no new number, LANE=<lane> TASK=<task> books one on the box ledger.
# The logic is in renumber_migration.py. Task 3928.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
  exec uv run python "$here/renumber_migration.py" "$@"
fi
exec python3 "$here/renumber_migration.py" "$@"
