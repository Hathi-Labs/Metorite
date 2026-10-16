"""The apply deploys ONE NAMED COMMIT, never the tip of a moving branch.

WS-38, ``project-docs/specs/staging_and_daily_release.md`` §4 and §5.6 phase 1.

Until 2026-10-16 ``scripts/vps_apply.sh`` resolved its target with
``git rev-parse origin/main``. Both of its callers meant something else:

* ``scripts/vps_pull.sh`` passed ``DEPLOY_REF=<the release sha>``, and the
  apply ignored it.
* ``deploy.yml`` meant "the commit this run tested", and the apply took
  whatever had merged a second later.

While ``release`` moved on every merge, the gap was a few seconds wide. Under
the daily release, ``main`` runs a day ahead of ``release``. An apply of
``origin/main`` then ships the day's untried merges to customers, from a
5-minute timer, with nobody choosing it.

The rule this file fences:

1. With ``DEPLOY_REF`` set, the apply deploys exactly that commit.
2. With it unset, the apply deploys ``origin/$RELEASE_REF`` (``release``).
3. A commit that is not on ``origin/main`` is refused, and nothing runs.
4. Both callers name the commit.

The behavioural tests reuse the real-prologue sandbox of
``test_deploy_reexec.py``. They need Linux and run in CI.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.unit.test_deploy_reexec import _Box, _git, _script, linux_only

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PULL = _ROOT / "scripts/vps_pull.sh"
_DEPLOY_YML = _ROOT / ".github/workflows/deploy.yml"


# ── Structural: every platform ───────────────────────────────────────────────


def test_the_pull_path_names_the_release_commit() -> None:
    body = _PULL.read_text(encoding="utf-8")
    assert 'DEPLOY_REF="$TARGET"' in body
    assert 'TARGET="$(git rev-parse "origin/$RELEASE_REF")"' in body


def test_the_push_path_names_the_commit_it_tested() -> None:
    body = _DEPLOY_YML.read_text(encoding="utf-8")
    assert "DEPLOY_REF=${GITHUB_SHA}" in body, (
        "deploy.yml must send its own GITHUB_SHA. Without it the apply falls "
        "back to origin/release, which is right only after publish-release ran."
    )


def test_the_apply_never_resolves_the_tip_of_main() -> None:
    lines = [
        ln for ln in (_ROOT / "scripts/vps_apply.sh").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for ln in lines:
        assert "rev-parse origin/main" not in ln, ln
        assert "reset --hard origin/main" not in ln, ln


# ── Behavioural: Linux only ──────────────────────────────────────────────────


class _ReleaseBox(_Box):
    """``release`` at commit A, ``main`` one merge ahead at commit B, and the
    checkout at A. That is the shape of every hour between two releases."""

    def __init__(self, tmp: pathlib.Path) -> None:
        super().__init__(tmp, _script("OLD"), _script("NEW"))
        seed = tmp / "seed"
        # _commit publishes release with each commit. Move it back to A, as
        # the daily clock leaves it until the next release.
        _git("push", "-q", "--force", "origin", f"{self.sha_a}:refs/heads/release", cwd=seed)
        # A commit that never reached main, for the refusal case.
        _git("checkout", "-q", "-b", "side", cwd=seed)
        self.sha_side = _commit_to(seed, _script("SIDE"), "side")

    def head(self) -> str:
        return _git("rev-parse", "HEAD", cwd=self.app)


def _commit_to(seed: pathlib.Path, script: str, branch: str) -> str:
    target = seed / "scripts/vps_apply.sh"
    target.write_text(script, encoding="utf-8", newline="\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", branch, cwd=seed)
    _git("push", "-q", "origin", f"HEAD:{branch}", cwd=seed)
    return _git("rev-parse", "HEAD", cwd=seed)


def _apply(box: _ReleaseBox, **extra: str) -> subprocess.CompletedProcess:
    """The push path's shape: the script on stdin, the env on the command."""
    return subprocess.run(
        ["bash", "-s"], input=_script("OLD"), env=box.env(**extra), cwd=box.tmp,
        capture_output=True, text=True, timeout=120,
    )


@linux_only
class TestTheTargetIsOneNamedCommit:
    def test_with_no_ref_it_deploys_release_and_not_main(self, tmp_path: pathlib.Path) -> None:
        box = _ReleaseBox(tmp_path)
        res = _apply(box)
        assert res.returncode == 0, res.stdout + res.stderr
        assert box.head() == box.sha_a, "the apply shipped main's untried merge"
        assert box.read(box.steps) == ["OLD"]
        assert f"target {box.sha_a[:12]}, from origin/release" in res.stdout

    def test_a_named_ref_is_deployed_exactly(self, tmp_path: pathlib.Path) -> None:
        box = _ReleaseBox(tmp_path)
        res = _apply(box, DEPLOY_REF=box.sha_b)
        assert res.returncode == 0, res.stdout + res.stderr
        assert box.head() == box.sha_b
        # The target's own copy ran its own step (the re-exec still holds).
        assert box.read(box.steps) == ["NEW"]

    def test_a_named_older_commit_is_not_moved_forward(self, tmp_path: pathlib.Path) -> None:
        box = _ReleaseBox(tmp_path)
        res = _apply(box, DEPLOY_REF=box.sha_a)
        assert res.returncode == 0, res.stdout + res.stderr
        assert box.head() == box.sha_a

    @pytest.mark.parametrize("which", ["side", "garbage"])
    def test_a_commit_that_is_not_on_main_is_refused(
        self, tmp_path: pathlib.Path, which: str
    ) -> None:
        box = _ReleaseBox(tmp_path)
        ref = box.sha_side if which == "side" else "no-such-ref"
        res = _apply(box, DEPLOY_REF=ref)
        assert res.returncode == 1, res.stdout + res.stderr
        assert box.head() == box.sha_a, "the checkout moved on a refused target"
        assert box.read(box.steps) == [], "a step ran for a refused target"
        assert "This round did NOTHING" in res.stdout
        assert not box.marker.exists()


class _AheadBox(_ReleaseBox):
    """``release`` already at B, and a stale caller that names A. That is a
    rerun of an old release run after a newer release shipped."""

    def __init__(self, tmp: pathlib.Path) -> None:
        super().__init__(tmp)
        _git("push", "-q", "--force", "origin", f"{self.sha_b}:refs/heads/release",
             cwd=tmp / "seed")


@linux_only
class TestTheBoxNeverMovesBackPastRelease:
    @pytest.mark.parametrize("force", ["0", "1"], ids=["plain", "forced-like-a-ci-rerun"])
    def test_a_ref_older_than_release_is_refused(
        self, tmp_path: pathlib.Path, force: str
    ) -> None:
        box = _AheadBox(tmp_path)
        res = _apply(box, DEPLOY_REF=box.sha_a, DEPLOY_FORCE=force)
        assert res.returncode == 1, res.stdout + res.stderr
        assert "Refusing to roll production back" in res.stdout
        assert box.read(box.steps) == []
        assert not box.marker.exists()

    def test_release_itself_is_still_deployed(self, tmp_path: pathlib.Path) -> None:
        box = _AheadBox(tmp_path)
        res = _apply(box, DEPLOY_REF=box.sha_b)
        assert res.returncode == 0, res.stdout + res.stderr
        assert box.head() == box.sha_b
