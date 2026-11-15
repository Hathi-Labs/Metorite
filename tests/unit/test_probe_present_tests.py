"""CIV-31e: scripts/probe_present_tests.sh, the default probe of
scripts/bisect_main.sh and scripts/home_server/auto_revert.py.

A failed test of a red batch can be new in the range. The good commit does not
hold it. The helper leaves out each test id that is not in the probed tree, so
that commit is good. When the file of an id is in the tree and pytest cannot
collect it, the probe never passes.

Each case runs the REAL helper in a small tree in tmp_path, with a real pytest
of the current interpreter (PROBE_PYTEST_CMD). The end-to-end case runs the
REAL bisect_main.sh with its default probe on a fixture repo whose commits do
not hold the helper.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "probe_present_tests.sh"
BISECT = ROOT / "scripts" / "bisect_main.sh"
PY = Path(sys.executable).as_posix()
#: The runner of the cases: the pytest of this interpreter, not `uv run`.
RUNNER = '"$PY" -m pytest'

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_auto_revert.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("git") is None, reason="needs bash and git"
)

PASSING = "def test_ok():\n    assert True\n"
PARAM = (
    "import pytest\n\n"
    '@pytest.mark.parametrize("state", ["requests closed", "requests open"])\n'
    "def test_rank(state):\n"
    '    assert state != "requests closed"\n'
)


def env(tmp_path: Path, **extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k != "BISECT_TEST_CMD"}
    return {
        **base,
        **GIT_ENV,
        "PY": PY,
        "PROBE_PYTEST_CMD": RUNNER,
        "TMPDIR": tmp_path.as_posix(),
        **extra,
    }


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """A probed tree: the files of one commit."""
    root = tmp_path / "tree"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def probe(tmp_path: Path, root: Path, *ids: str, **extra: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, HELPER.as_posix(), *ids],
        cwd=root,
        env=env(tmp_path, **extra),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


# ── The absent rule ────────────────────────────────────────────────────────


def test_a_file_that_is_not_in_the_tree_is_good(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_other.py": PASSING})
    got = probe(tmp_path, root, "test_new.py::test_x")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "ALL_ABSENT" in got.stderr
    assert "test_new.py::test_x" in got.stderr


def test_a_test_function_that_is_not_in_the_file_is_good(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_a.py": PASSING})
    got = probe(tmp_path, root, "test_a.py::test_new")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "ALL_ABSENT" in got.stderr


def test_a_test_file_with_an_import_error_is_not_good(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_a.py": "import no_such_module_civ31e\n\n" + PASSING})
    got = probe(tmp_path, root, "test_a.py::test_ok")
    assert got.returncode not in (0, 1), got.stdout + got.stderr
    # The id of a function that is not there is not good either: the file
    # does not collect, so nothing is left out.
    got = probe(tmp_path, root, "test_a.py::test_new")
    assert got.returncode not in (0, 1), got.stdout + got.stderr
    assert "ALL_ABSENT" not in got.stderr


def test_a_broken_conftest_is_not_good(tmp_path: Path) -> None:
    root = tree(
        tmp_path,
        {"conftest.py": 'raise RuntimeError("a broken conftest")\n', "test_a.py": PASSING},
    )
    for test_id in ("test_a.py::test_ok", "test_a.py::test_new"):
        got = probe(tmp_path, root, test_id)
        assert got.returncode not in (0, 1), (test_id, got.stdout + got.stderr)
        assert "ALL_ABSENT" not in got.stderr


def test_one_absent_and_one_present_that_passes_is_good(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_a.py": PASSING})
    got = probe(tmp_path, root, "test_gone.py::test_x", "test_a.py::test_ok")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "test_gone.py::test_x" in got.stderr
    assert "1 passed" in got.stdout
    assert "ALL_ABSENT" not in got.stderr


def test_one_absent_and_one_present_that_fails_is_bad(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_a.py": "def test_bad():\n    assert False\n"})
    got = probe(tmp_path, root, "test_gone.py::test_x", "test_a.py::test_bad")
    assert got.returncode == 1, got.stdout + got.stderr
    assert "1 failed" in got.stdout


def test_a_param_id_with_a_space_matches_exactly(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_a.py": PARAM})
    got = probe(tmp_path, root, "test_a.py::test_rank[requests closed]")
    assert got.returncode == 1, got.stdout + got.stderr
    assert "ALL_ABSENT" not in got.stderr
    got = probe(tmp_path, root, "test_a.py::test_rank[requests open]")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "1 passed" in got.stdout
    # A param id that the file does not make is absent.
    got = probe(tmp_path, root, "test_a.py::test_rank[requests gone]")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "ALL_ABSENT" in got.stderr


def test_a_collection_that_prints_no_node_id_leaves_nothing_out(tmp_path: Path) -> None:
    """A second -q makes pytest print "file: count", with no node id. The helper
    cannot read that list, so it runs the id, and pytest says "not found"."""
    root = tree(tmp_path, {"test_a.py": PASSING})
    got = probe(tmp_path, root, "test_a.py::test_new", PROBE_PYTEST_CMD=RUNNER + " -q")
    assert got.returncode not in (0, 1), got.stdout + got.stderr
    assert "ALL_ABSENT" not in got.stderr


def test_no_test_id_is_a_usage_error(tmp_path: Path) -> None:
    root = tree(tmp_path, {"test_a.py": PASSING})
    got = probe(tmp_path, root)
    assert got.returncode == 2


# ── bisect_main.sh with its default probe ─────────────────────────────────


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **GIT_ENV},
    )
    return out.stdout.strip()


def test_the_bisect_names_the_commit_that_added_a_failing_test(tmp_path: Path) -> None:
    """The red batch of 2026-11-14: the failed test is new in the range. The good
    commit does not hold it, nor the helper, and the bisect judges it good."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    shas: dict[int, str] = {}
    for pr in range(101, 106):
        files = {"note.txt": f"{pr}\n"}
        if pr == 101:
            files["calc.py"] = "def add(a, b):\n    return a + b\n"
        subject = f"change number {pr} (#{pr})"
        if pr == 103:
            files["test_rank.py"] = (
                "import pytest\n\nfrom calc import add\n\n"
                '@pytest.mark.parametrize("state", ["requests closed"])\n'
                "def test_rank(state):\n"
                "    assert add(1, 2) == 4\n"
            )
            subject = f"add a test that fails (#{pr})"
        for name, text in files.items():
            (repo / name).write_text(text, encoding="utf-8", newline="\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", subject)
        shas[pr] = git(repo, "rev-parse", "HEAD")
    # No probed commit holds the helper. Only an absolute path can reach it.
    for sha in shas.values():
        assert "probe_present_tests.sh" not in git(repo, "ls-tree", "-r", "--name-only", sha)

    assert BASH is not None
    got = subprocess.run(
        [BASH, BISECT.as_posix(), shas[101], shas[105], "test_rank.py::test_rank[requests closed]"],
        cwd=repo,
        env=env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    # Stdout holds the one answer line. The output of the probes is on stderr.
    assert got.stdout.splitlines() == [
        f"FIRST_BAD {shas[103]} add a test that fails (#103) PR=103"
    ], got.stdout
    assert f"{shas[101][:12]} is good" in got.stderr
    assert "ALL_ABSENT" in got.stderr
    assert "1 failed" in got.stderr
    assert len(git(repo, "worktree", "list").splitlines()) == 1
