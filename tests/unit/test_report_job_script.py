"""SW-116 (task 4206) — the lane script ``scripts/report_job.mjs``.

Spec: ``project-docs/specs/agent_coordination.md`` §3.12.11, "Who reports a
job". A lane calls the script at each change of state of an external job.
The script must never block a lane, so each path exits 0. Its own fence is
``scripts/report_job.test.mjs`` (``node --test``). This file runs it in the
unit suite, so ``scripts/affected_tests.py`` and pr-check reach it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_TEST = _REPO / "scripts" / "report_job.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_node_fence_of_the_lane_script_passes() -> None:
    done = subprocess.run(
        ["node", "--test", str(_TEST)], cwd=_REPO, capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=False,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    assert "# fail 0" in done.stdout, done.stdout[-4000:]
