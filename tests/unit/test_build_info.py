"""`/version` — the deployed SHA, which CLAUDE.md §3.8 demands and nothing served.

The rule is standing: *"Verify delivery by evidence, never by a green job.
Migration ledger lines, the deployed SHA, the log line. Four deploys once
reported success while shipping nothing."*

The deployed SHA was not obtainable. `/health` answered `{"status": "ok",
"env": "dev"}`, no other route carried a build identity, and on 2026-08-25 "is
production running the latest code?" had to be answered by recognising a known
icon bug in a screenshot of the running app.

These cases pin the two things that make the endpoint worth having:

  * it resolves a REAL checkout — asserted against `git rev-parse HEAD` rather
    than against a fixture, because a resolver that agrees with a fixture it
    was written beside proves only that the fixture matches itself;
  * it answers **None**, never a placeholder, when it cannot tell. A caller
    must be able to distinguish "this box cannot report its version" from
    "this box runs a commit named unknown", and a string erases that.

Hermetic apart from one `git rev-parse` in the first test, which is the point
of that test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gateway import build_info


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """`build_sha` is `lru_cache`d for the process lifetime — correct in a
    service, poison across tests. Without this the first case's answer is
    handed to every later one and the fixtures below assert nothing."""
    build_info.build_sha.cache_clear()


def test_resolves_the_real_checkout() -> None:
    """The claim that matters, checked against git itself.

    If this repo is not a git checkout the test SKIPS rather than passing — a
    resolver that returns None in an environment where nothing could be
    resolved is not evidence of anything.
    """
    try:
        expected = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("not a git checkout — nothing to resolve against")

    assert build_info.build_sha() == expected, (
        "build_sha() disagrees with `git rev-parse HEAD` — the endpoint would "
        "report a version this box is not running"
    )


def test_the_environment_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam a container build needs.

    An image has no `.git`, so a future non-checkout deploy stamps the value
    instead. It is checked FIRST so that stamping works even where a stray
    `.git` exists further up the filesystem.
    """
    monkeypatch.setenv("ACB_GIT_SHA", "deadbeef" * 5)
    assert build_info.build_sha() == "deadbeef" * 5


def test_a_blank_override_does_not_mask_the_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ACB_GIT_SHA=` (set but empty) is how a shell exports a variable it
    could not compute. Treating that as an answer would make the endpoint
    report an empty SHA on exactly the box whose deploy script failed to
    resolve one — reporting confidently, from a failure."""
    monkeypatch.setenv("ACB_GIT_SHA", "   ")
    assert build_info.build_sha() != "   "


def test_reads_a_detached_head(tmp_path: Path) -> None:
    """`HEAD` holding a bare SHA. `git reset --hard` (what `vps_pull.sh` runs)
    does not detach, but a hand-recovered box very often is detached, and that
    is precisely when somebody is asking what is deployed."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("a" * 40 + "\n", encoding="utf-8")
    assert build_info._sha_from_git_dir(git_dir) == "a" * 40


def test_reads_a_loose_ref(tmp_path: Path) -> None:
    """The ordinary case: `ref: refs/heads/main` plus a loose ref file."""
    git_dir = tmp_path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("b" * 40, encoding="utf-8")
    assert build_info._sha_from_git_dir(git_dir) == "b" * 40


def test_falls_back_to_packed_refs(tmp_path: Path) -> None:
    """A fresh `git clone` packs its refs and writes no loose file, so a
    resolver that only reads `refs/heads/<branch>` returns None on a
    newly-provisioned box — the first box anyone asks about."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{'c' * 40} refs/heads/main\n"
        f"{'d' * 40} refs/tags/v1\n",
        encoding="utf-8",
    )
    assert build_info._sha_from_git_dir(git_dir) == "c" * 40


def test_packed_refs_peel_lines_are_not_mistaken_for_refs(tmp_path: Path) -> None:
    """An annotated tag writes a `^<sha>` line UNDER its ref, naming the commit
    the tag points at. Parsed as an ordinary line it has one field and no ref
    name; parsed carelessly it can shadow the entry above it. Cheap to get
    right, silent and wrong-by-one-commit when got wrong."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "packed-refs").write_text(
        f"{'e' * 40} refs/tags/v1\n"
        f"^{'f' * 40}\n"
        f"{'1' * 40} refs/heads/main\n",
        encoding="utf-8",
    )
    assert build_info._sha_from_git_dir(git_dir) == "1" * 40


def test_a_worktree_git_file_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a `git worktree` the `.git` entry is a FILE containing
    `gitdir: <path>`, not a directory.

    This repo uses worktrees heavily — nine were checked out while this was
    written — so a resolver that assumes a directory answers None for most
    developers locally, and "the endpoint seems broken on my machine" is how a
    working endpoint gets deleted.
    """
    real_git = tmp_path / "realgit"
    (real_git / "refs" / "heads").mkdir(parents=True)
    (real_git / "HEAD").write_text("ref: refs/heads/wt\n", encoding="utf-8")
    (real_git / "refs" / "heads" / "wt").write_text("9" * 40, encoding="utf-8")

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {real_git}\n", encoding="utf-8")

    monkeypatch.delenv("ACB_GIT_SHA", raising=False)
    monkeypatch.setattr(build_info, "_repo_root", lambda: worktree)
    assert build_info.build_sha() == "9" * 40


def test_unresolvable_answers_none_not_a_placeholder(tmp_path: Path) -> None:
    """The distinction the nullable type exists for.

    "Cannot report a version" and "running a commit called unknown" must not
    look the same to whoever is verifying a deploy at the time — which is the
    only time anyone reads this.
    """
    empty = tmp_path / ".git"
    empty.mkdir()
    assert build_info._sha_from_git_dir(empty) is None


def test_the_route_reports_the_sha() -> None:
    """The endpoint returns what the resolver found, and types `sha` as
    nullable so a box that cannot answer says so rather than 500-ing."""
    from gateway.main import Version

    model = Version(sha=None, env="prod")
    assert model.sha is None
    assert Version(sha="a" * 40, env="prod").sha == "a" * 40


def test_version_route_is_registered() -> None:
    """Structural: the route exists and is reachable without a dependency on
    the database. A version endpoint that needs a query fails in exactly the
    situation where you most want to know what is running."""
    import inspect

    from gateway import main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/version" in paths, "the /version route is not registered"

    # The silent failure mode. The gateway is DEFAULT-DENY at the app level, so
    # a /version that is registered but absent from PUBLIC_ROUTES answers 401 —
    # and the person verifying a deploy reads a 401 from an endpoint they were
    # told to curl as "the box is broken", which is the opposite of what it
    # means. Gating it also defeats the point: the answer is wanted from a
    # laptop or CI step holding no session.
    assert "/version" in main.PUBLIC_ROUTES, (
        "/version is registered but not public — default-deny will 401 it, and "
        "an unauthenticated caller cannot tell that apart from an outage"
    )

    src = inspect.getsource(main.version)
    for forbidden in ("execute(", "session", "_db", "SELECT"):
        assert forbidden not in src, (
            f"/version touches the database (`{forbidden}`) — it must answer "
            "when the database is down, which is when it is most needed"
        )
