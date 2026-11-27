#!/usr/bin/env bash
# One guarded flip of a per-org flag on the production box (task 4086).
# Run it on the box as acb. scripts/flag_flip.py holds the guards and the doc:
#   bash scripts/flag_flip.sh TOOL_PACKS_ORGS --add <org id> --task <n>
#   bash scripts/flag_flip.sh --help
# Fence: tests/unit/test_flag_flip.py.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app="${FLAG_FLIP_APP_DIR:-/opt/acb/app}"
py="${FLAG_FLIP_PYTHON:-$app/.venv/bin/python}"
if [ ! -x "$py" ]; then
  echo "flag_flip: REFUSED: no python at $py. Run it on the box, or set FLAG_FLIP_PYTHON." >&2
  exit 2
fi
exec "$py" "$here/flag_flip.py" "$@"
