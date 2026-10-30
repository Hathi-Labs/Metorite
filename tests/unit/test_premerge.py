"""`scripts/premerge.sh` tests the merge with today's main, before `gh pr merge`.

Spec: HANDOFF H-10 (Metorite #639) · `.claude/commands/next-ticket.md` step 7 ·
Metorite task LD-3.

pr-check tests a PR against main as it was when the run started. Without a
merge queue (owner decision, 2026-10-16), a PR can then merge onto a main that
took the same migration number in between. These tests build a throwaway git
repo with a bare `origin`, so they need git and bash, and no network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "premerge.sh"


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_daily_release.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
pytestmark = pytest.mark.skipif(_BASH is None or shutil.which("git") is None,
                                reason="needs bash and git")

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         encoding="utf-8", env=_ENV, check=True)
    return res.stdout.strip()


def _commit(repo: Path, path: str, text: str = "x\n") -> None:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"add {path}")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone of a bare origin. main holds migration 315. Branch `feat` is cut."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _commit(work, "infra/postgres/315_base.sql")
    _commit(work, "shared.md", "a\nb\nc\nd\ne\n")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", "feat")
    return work


def _advance_main(repo: Path, path: str, text: str = "x\n") -> None:
    """Another PR merges: main moves on while `feat` waits."""
    _git(repo, "checkout", "-q", "main")
    _commit(repo, path, text)
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feat")


def _run(repo: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([_BASH, SCRIPT.as_posix()], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", env={**_ENV, **env}, timeout=120)


def _both_sides_change_shared(repo: Path) -> None:
    """Both sides edit shared.md on different lines: a clean merge with overlap."""
    _commit(repo, "shared.md", "A\nb\nc\nd\ne\n")
    _advance_main(repo, "shared.md", "a\nb\nc\nd\nE\n")


_FAILS = 'echo "FAILED tests/t.py::test_a - boom"; exit 1'


def test_a_test_that_fails_only_when_merged_blocks(repo):
    """Green alone, red together: the case the overlap step exists for."""
    _both_sides_change_shared(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD=_FAILS, PREMERGE_RERUN_CMD="true")
    assert res.returncode == 1
    assert "tests/t.py::test_a" in res.stderr and "pass on the branch alone" in res.stderr
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_a_test_that_fails_alone_too_does_not_block(repo):
    """Six tests fail on the Windows PC and pass in CI. They must not block a
    merge they did not come from (LD-3b, 2026-10-30)."""
    _both_sides_change_shared(repo)
    head = _git(repo, "rev-parse", "HEAD")
    res = _run(repo, PREMERGE_AFFECTED_CMD=_FAILS, PREMERGE_RERUN_CMD=_FAILS)
    assert res.returncode == 0, res.stderr
    assert "did not cause them" in res.stdout and "PASS" in res.stdout
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"


def test_a_run_that_names_no_test_blocks(repo):
    _both_sides_change_shared(repo)
    res = _run(repo, PREMERGE_AFFECTED_CMD="echo boom; exit 2", PREMERGE_RERUN_CMD="true")
    assert res.returncode == 1
    assert "no test is named" in res.stderr


def test_a_branch_that_contains_main_passes(repo):
    _commit(repo, "infra/postgres/316_feat.sql")
    res = _run(repo)
    assert res.returncode == 0, res.stderr
    assert "Merge now" in res.stdout


def test_a_migration_number_main_took_meanwhile_fails_by_name(repo):
    """The R1 collision: both PRs took 316, and the other merged first."""
    _commit(repo, "infra/postgres/316_feat.sql")
    _advance_main(repo, "infra/postgres/316_other.sql")
    res = _run(repo)
    assert res.returncode == 1
    assert "316_feat.sql" in res.stderr and "Take 317" in res.stderr


def test_a_migration_below_mains_highest_fails(repo):
    """Main reached 317. A late 316 would apply after 317 in production."""
    _commit(repo, "infra/postgres/316_feat.sql")
    _advance_main(repo, "infra/postgres/317_other.sql")
    res = _run(repo)
    assert res.returncode == 1
    assert "Take 318" in res.stderr


def test_a_conflict_fails_and_leaves_the_worktree_as_it_was(repo):
    _commit(repo, "notes.md", "feat\n")
    _advance_main(repo, "notes.md")
    head = _git(repo, "rev-parse", "HEAD")
    res = _run(repo)
    assert res.returncode == 1
    assert "conflicts in: notes.md" in res.stderr
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_an_untracked_file_in_the_way_is_named_and_kept(repo):
    """Main adds a file that is untracked here. git refuses the merge. The
    message must say why, and the untracked file must survive."""
    _commit(repo, "feat.md")
    _advance_main(repo, "zone.md")
    (repo / "zone.md").write_text("mine\n", encoding="utf-8")
    res = _run(repo)
    assert res.returncode == 1
    assert "zone.md" in res.stderr and "untracked" in res.stderr
    assert (repo / "zone.md").read_text(encoding="utf-8") == "mine\n"


def test_a_clean_behind_branch_passes_and_the_trial_merge_is_undone(repo):
    _commit(repo, "infra/postgres/317_feat.sql")
    _advance_main(repo, "infra/postgres/316_other.sql")
    head = _git(repo, "rev-parse", "HEAD")
    res = _run(repo)
    assert res.returncode == 0, res.stderr
    assert "PASS" in res.stdout
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "feat"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_uncommitted_work_is_refused(repo):
    (repo / "wip.md").write_text("wip\n", encoding="utf-8")
    _git(repo, "add", "wip.md")
    res = _run(repo)
    assert res.returncode == 1
    assert "uncommitted" in res.stderr


def test_the_collision_fences_are_named():
    body = SCRIPT.read_text(encoding="utf-8")
    for fence in ("test_migration_prefixes.py", "test_handoff_queue.py", "test_decision_ids.py"):
        assert fence in body
        assert (ROOT / "tests" / "unit" / fence).is_file(), fence
