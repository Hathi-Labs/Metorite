"""Fence for scripts/worktree.sh remove (LD-6).

`remove` once passed --force to `git worktree remove` every time, and --force
deletes exactly the uncommitted and untracked files. These tests make a temp
repo with a worktree and check:

* a dirty worktree is refused, its files stay, and the dev stack is not
  stopped (the check runs before anything else);
* `remove --force <slug>` still deletes it;
* a clean worktree goes without --force.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "worktree.sh"


def _bash() -> str | None:
    if os.name == "nt":
        # Git's bash, not WSL's bash.exe, as in test_dev_up.py.
        git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


_BASH = _bash()
pytestmark = pytest.mark.skipif(_BASH is None, reason="needs bash")

# A fake dev_up.sh: when worktree.sh calls it, it leaves a marker file.
FAKE_DEV_UP = '#!/usr/bin/env bash\ntouch "$WT_TEST_MARKER"\n'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.fixture()
def repo(tmp_path: Path) -> dict:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", origin.as_posix())
    main = tmp_path / "app"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "scripts").mkdir()
    (main / "scripts" / "dev_up.sh").write_text(FAKE_DEV_UP, encoding="utf-8", newline="\n")
    (main / ".gitignore").write_text(".dev_up/\nnode_modules/\n", encoding="utf-8", newline="\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "init")
    _git(main, "remote", "add", "origin", origin.as_posix())
    _git(main, "push", "-q", "origin", "main")
    env = {
        **os.environ,
        "METORITE_WT_ROOT": (tmp_path / "wt").as_posix(),
        "WT_TEST_MARKER": (tmp_path / "dev_up_was_called").as_posix(),
    }
    return {"tmp": tmp_path, "main": main, "env": env}


def _run(repo: dict, *args: str) -> subprocess.CompletedProcess:
    # A `git` shim first on PATH logs every git call, then runs the real git,
    # so a test can see whether --force reached `git worktree remove`.
    # cygpath turns C:/... into /c/..., because a colon splits PATH.
    shim_dir = repo["tmp"] / "shim"
    if not shim_dir.exists():
        shim_dir.mkdir()
        real = subprocess.run(
            [_BASH, "-c", "command -v git"], capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
        (shim_dir / "git").write_text(
            '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$WT_TEST_GITLOG"\n'
            f'exec "{real}" "$@"\n',
            encoding="utf-8", newline="\n",
        )
    runner = 'd="$(cygpath -u "$0" 2>/dev/null || printf %s "$0")"; PATH="$d:$PATH" exec bash "$@"'
    return subprocess.run(
        [_BASH, "-c", runner, shim_dir.as_posix(), SCRIPT.as_posix(), *args],
        cwd=repo["main"], env={**repo["env"], "WT_TEST_GITLOG": (repo["tmp"] / "git.log").as_posix()},
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def _removes(repo: dict) -> list[str]:
    log = repo["tmp"] / "git.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [line for line in lines if "worktree remove" in line]


def _new(repo: dict, slug: str) -> Path:
    res = _run(repo, "new", slug)
    assert res.returncode == 0, res.stdout + res.stderr
    path = repo["tmp"] / f"wt-{slug}"
    assert path.is_dir()
    # An ignored dev stack state, so remove would stop the (fake) stack.
    (path / ".dev_up").mkdir()
    (path / ".dev_up" / "state").write_text("slot=1\n", encoding="utf-8")
    return path


def test_remove_refuses_a_dirty_worktree_and_keeps_its_files(repo: dict) -> None:
    path = _new(repo, "dirty")
    (path / "scripts" / "dev_up.sh").write_text(FAKE_DEV_UP + "# edited\n", encoding="utf-8", newline="\n")
    (path / "notes.txt").write_text("unsaved\n", encoding="utf-8")

    res = _run(repo, "remove", "dirty")

    assert res.returncode == 1, res.stdout + res.stderr
    assert "uncommitted or untracked work" in res.stdout
    assert "remove --force dirty" in res.stdout
    assert (path / "notes.txt").read_text(encoding="utf-8") == "unsaved\n"
    assert "# edited" in (path / "scripts" / "dev_up.sh").read_text(encoding="utf-8")
    # The refusal comes before the dev stack stops.
    assert not (repo["tmp"] / "dev_up_was_called").exists()


def test_remove_force_deletes_a_dirty_worktree(repo: dict) -> None:
    path = _new(repo, "forced")
    (path / "notes.txt").write_text("unsaved\n", encoding="utf-8")

    res = _run(repo, "remove", "--force", "forced")

    assert res.returncode == 0, res.stdout + res.stderr
    assert not path.exists()
    assert any("--force" in line for line in _removes(repo)), _removes(repo)


def test_remove_takes_a_clean_worktree_without_force(repo: dict) -> None:
    path = _new(repo, "clean")

    res = _run(repo, "remove", "clean")

    assert res.returncode == 0, res.stdout + res.stderr
    assert not path.exists()
    # Without the override, --force never reaches git.
    removes = _removes(repo)
    assert removes and not any("--force" in line for line in removes), removes
    # A clean worktree with a dev stack: the stack is stopped first.
    assert (repo["tmp"] / "dev_up_was_called").exists()
