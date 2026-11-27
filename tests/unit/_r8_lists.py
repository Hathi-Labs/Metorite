"""The R8 guard lists, read for the suites that name themselves in them (CIV-33c-2).

The lists are the ``r8`` key of ``tests/fences.json``. Both workflows read that
key, so no suite checks a workflow for its name. Each R8 suite asks here
whether it is in ``must_run``. The reader of the guard is
``scripts/ci_unit_guard.py`` (``fence_lists``). Fence: test_ci_unit_guard.py.
"""

from __future__ import annotations

import json
from pathlib import Path

_FENCES = Path(__file__).resolve().parents[2] / "tests" / "fences.json"


def _r8() -> dict[str, list[str]]:
    return json.loads(_FENCES.read_text(encoding="utf-8"))["r8"]


def must_run() -> list[str]:
    """The files that the R8 guard needs in a report."""
    return _r8()["must_run"]


def disarmed() -> list[str]:
    """The skip reasons that mean an R8 gate is off."""
    return _r8()["disarmed"]


def never_skip() -> list[str]:
    """The files that the R8 guard needs to run with no skip."""
    return _r8()["never_skip"]
