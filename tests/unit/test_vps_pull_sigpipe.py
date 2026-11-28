"""The commit list of a release must never stop the pull (task 3897).

``scripts/vps_pull.sh`` runs under ``set -euo pipefail``. It listed the
commits of a release with ``git log --oneline ... | head -20``. ``head``
closes the pipe after 20 lines. git then writes into a closed pipe, and
SIGPIPE stops git. Under ``pipefail`` the pipeline exits 141, and ``set -e``
stops the script before the apply. On 2026-11-22 at 14:34 UTC a release of 37
commits stopped the production pull that way.

The fix is ``git log -n 20``. git stops by itself, so no reader closes early.

Two tests:

* The behavioural test takes the listing lines out of the real script. It
  runs each line in bash under the same ``set -euo pipefail``, in a repo where
  the release is far more than 20 commits ahead. Each line must exit 0 and
  print 20 lines.
* The source fence reads the non-comment lines of the script. No ``git log``
  line may pipe into ``head``.

The idiom of ``test_pull_retries_failed_apply.py`` applies here: every
assertion reads NON-COMMENT lines, because the comment beside the fix names
the old shape.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PULL = _ROOT / "scripts/vps_pull.sh"

_BASH = shutil.which("bash")
_GIT = shutil.which("git")

_RELEASE_REF = "release"
_SHOWN = 20
# A short release fits in the pipe buffer (64 KB on Linux). git then writes
# all of it before ``head`` closes the pipe, and the old line passes by luck.
# 200 commits with a 2000-character subject write about 400 KB. git must wait
# for ``head`` to read, so the old line fails every time, on every platform.
_COMMITS = 200
_SUBJECT_CHARS = 2000
_TIMEOUT_S = 120

_GIT_LOG_INTO_HEAD = re.compile(r"\bgit\s+log\b.*\|\s*head\b")


def _executable_lines(path: Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _listing_lines() -> list[str]:
    """The lines of vps_pull.sh that list the commits of the release."""
    return [
        ln.strip()
        for ln in _executable_lines(_PULL)
        if re.search(r"\bgit\s+log\b", ln) and "RELEASE_REF" in ln
    ]


def _env(tmp_path: Path) -> dict[str, str]:
    """A git that reads no config of this machine, and no GIT_* variable of a hook."""
    empty_config = tmp_path / "empty.gitconfig"
    empty_config.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "RELEASE_REF": _RELEASE_REF,
        }
    )
    return env


def _fast_import_stream() -> bytes:
    """One base commit on main, then _COMMITS commits on origin/release."""
    parts: list[bytes] = []

    def commit(ref: str, n: int, subject: str, *, mark: int | None = None, parent: str | None = None) -> None:
        msg = f"{subject}\n".encode("utf-8")
        head = f"commit {ref}\n"
        if mark is not None:
            head += f"mark :{mark}\n"
        head += f"committer t <t@example.invalid> {1_700_000_000 + n} +0000\n"
        head += f"data {len(msg)}\n"
        parts.append(head.encode("utf-8") + msg)
        if parent is not None:
            parts.append(f"from {parent}\n".encode("utf-8"))
        parts.append(b"\n")

    commit("refs/heads/main", 0, "base", mark=1)
    pad = "x" * _SUBJECT_CHARS
    for n in range(1, _COMMITS + 1):
        commit(
            f"refs/remotes/origin/{_RELEASE_REF}",
            n,
            f"release commit {n} {pad}",
            parent=":1" if n == 1 else None,
        )
    return b"".join(parts)


def _release_repo(tmp_path: Path, env: dict[str, str]) -> Path:
    """HEAD is the base. origin/release is _COMMITS commits ahead of it."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str, data: bytes | None = None) -> str:
        r = subprocess.run(
            [_GIT, "-C", str(repo), *args],
            input=data,
            env=env,
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=True,
        )
        return r.stdout.decode("utf-8", "replace").strip()

    git("init", "-q")
    git("symbolic-ref", "HEAD", "refs/heads/main")
    git("fast-import", "--quiet", data=_fast_import_stream())
    ahead = git("rev-list", "--count", f"HEAD..origin/{_RELEASE_REF}")
    assert ahead == str(_COMMITS), f"the fixture is {ahead} commits ahead, not {_COMMITS}"
    return repo


def test_the_script_runs_under_pipefail() -> None:
    """The behavioural test copies this line. If the script drops it, the
    harness below no longer matches the script, and this test says so."""
    assert "set -euo pipefail" in _executable_lines(_PULL)


@pytest.mark.skipif(_BASH is None or _GIT is None, reason="needs bash and git on PATH")
def test_each_listing_line_survives_a_release_longer_than_the_pipe_buffer(tmp_path: Path) -> None:
    lines = _listing_lines()
    assert len(lines) == 2, (
        "vps_pull.sh lists the release commits in two places, --check and the "
        f"apply. Found {len(lines)}: {lines}. Update this test with the script."
    )
    env = _env(tmp_path)
    repo = _release_repo(tmp_path, env)

    # On Windows, `bash` can be the WSL launcher, which cannot read this repo.
    probe = subprocess.run(
        [_BASH, "-c", 'git rev-parse --verify --quiet "origin/$RELEASE_REF"'],
        cwd=repo,
        env=env,
        capture_output=True,
        timeout=_TIMEOUT_S,
    )
    if probe.returncode != 0:
        pytest.skip(f"{_BASH} cannot run git in {repo}: exit {probe.returncode}")

    failures: list[str] = []
    for line in lines:
        r = subprocess.run(
            [_BASH, "-c", f"set -euo pipefail\n{line}\n"],
            cwd=repo,
            env=env,
            capture_output=True,
            timeout=_TIMEOUT_S,
        )
        out = r.stdout.decode("utf-8", "replace").splitlines()
        if r.returncode != 0:
            failures.append(f"{line}\n    exit {r.returncode} (141 is SIGPIPE), {len(out)} line(s)")
        elif len(out) != _SHOWN:
            failures.append(f"{line}\n    printed {len(out)} line(s), not {_SHOWN}")
        elif f"release commit {_COMMITS} " not in out[0]:
            failures.append(f"{line}\n    the first line is not the newest commit: {out[0][:60]}")
    assert not failures, (
        "a commit listing in vps_pull.sh fails under pipefail when the release "
        "is long. Use `git log -n 20`, never `git log | head`:\n" + "\n".join(failures)
    )


def test_no_git_log_line_pipes_into_head() -> None:
    """The source fence. It runs on every platform, with no bash and no git."""
    bad = [ln.strip() for ln in _executable_lines(_PULL) if _GIT_LOG_INTO_HEAD.search(ln)]
    assert not bad, (
        "vps_pull.sh runs under pipefail. `git log ... | head` exits 141 when the "
        "release is long, and the deploy stops before the apply (task 3897). "
        "Use `git log -n 20` instead:\n" + "\n".join(bad)
    )
