"""vps_pull.sh lists the commits it applies without a pipe that pipefail can kill.

2026-11-22 14:34 UTC: acb-pull.service exited 141 at ``==> Applying 37
commit(s)``. The listing was ``git log ... | head -20``. With more than 20
commits, ``head`` exits early, ``git`` gets SIGPIPE, and ``set -o pipefail`` makes
the pipeline fail, so ``set -e`` ended the round before the apply ran.

Fence for the rule: the listing lines of ``scripts/vps_pull.sh`` never pipe
``git log`` into ``head``, and they survive 37 commits under pipefail.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

_PULL = pathlib.Path(__file__).resolve().parents[2] / "scripts/vps_pull.sh"
_BASH = shutil.which("bash")


def _listing_lines() -> list[str]:
    body = _PULL.read_text(encoding="utf-8")
    return [ln for ln in body.splitlines() if re.match(r"\s*git log --oneline\b", ln)]


def test_the_commit_listing_has_no_head_pipe() -> None:
    lines = _listing_lines()
    assert len(lines) == 2, "expected the check-mode and the apply-mode listing"
    for ln in lines:
        assert "| head" not in ln, f"git log into head dies of SIGPIPE under pipefail: {ln}"
        assert "-n 20" in ln


@pytest.mark.skipif(_BASH is None, reason="needs bash")
def test_the_listing_survives_more_than_twenty_commits_under_pipefail(tmp_path) -> None:
    def git(*a: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "base")
    for i in range(37):
        # A long subject fills the pipe buffer, so git is still writing when
        # head exits. Short lines would let git finish first and hide the bug.
        git("commit", "-q", "--allow-empty", "-m", f"c{i} " + "x" * 30000)
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    git("update-ref", "refs/remotes/origin/release", tip)
    git("reset", "-q", "--hard", "HEAD~37")

    for line in _listing_lines():
        script = f'set -euo pipefail\nRELEASE_REF=release\n{line}\necho survived\n'
        r = subprocess.run(
            [_BASH, "-c", script], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert r.returncode == 0, (line, r.returncode, r.stderr)
        assert r.stdout.rstrip().endswith("survived")
        assert len(r.stdout.strip().splitlines()) == 21  # 20 commits + "survived"
