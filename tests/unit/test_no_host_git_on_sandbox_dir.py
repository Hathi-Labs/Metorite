"""WS43-F13 — no host git process starts on a sandbox dir (WS-43e).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.5 rule A and §10 WS43-F13.
A container can write anything in its mounted dir: a ``.git/config`` with
``core.fsmonitor`` set to a program, or a hook. Host git in that dir then runs
the program on the HOST, beside the gateway and its keys.

So each host git site asks ``sandbox_broker.host_git_allowed`` first (through
``refuse_if_sandbox_dir``), and skips a dir that a container mounts now or
mounted:

* ``code_tools._commit_repo_changes``,
* ``executor._git_dir_for``, ``_get_current_head`` (the HEAD capture),
  ``_install_push_guard``, ``_detect_agent_commits`` (the commit scan),
  ``_commit_on_remote``, and ``_repair_dir`` (the self-anneal and the
  self-mutation),
* ``mutation.attempt_self_mutation``, ``_run_mutation_sandbox``,
  ``_stash_pull_before_mutation``, ``_auto_push_commit`` and ``_git_diff``.

Each test plants a hostile ``.git/config`` and hook in a dir that a REAL
broker start (on a fake Docker) recorded as a sandbox dir. Then it calls the
site with every way of starting a host process trapped and recorded.

⚠️ The Custom Apps clauses of WS43-F13 (the read-only cover of a Custom
Apps ``.git`` and the ``_git`` flags of ``apps/durability.py``) belong to
WS-43h, which moves app-builder onto the broker.

Mutations this suite catches (R7), each run red once by hand:

* ``host_git_allowed`` always answers ``True``: every site test;
* ``_commit_repo_changes`` drops its guard: the commit test, and the real
  git test sees the planted fsmonitor run;
* ``_git_dir_for`` returns the dir unchecked: the git-dir test;
* ``_repair_dir`` returns ``git_dir or effective_dir``: the repair test;
* ``_install_push_guard`` drops its guard: the push guard test;
* ``is_sandbox_dir`` fails open on an unreadable list: the fail-closed test.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import FakeDocker, bound_run, configure_env
from tests.unit._sandbox_tools_fakes import ORG_A, new_thread, short_tmp  # noqa: F401

AGENT = "agent-x"
PERSONAL = "u:member@example.com"
HOSTILE_CONFIG = (
    "[core]\n\trepositoryformatversion = 0\n\tfsmonitor = {hook}\n\thooksPath = {hooks}\n"
    "[remote \"origin\"]\n\turl = https://x-token:SECRET@github.com/x/y\n"
)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> sb.SandboxBroker:  # noqa: F811
    env = configure_env(monkeypatch, short_tmp)
    monkeypatch.setattr(env["settings"], "maf_coding_scope", f"code_task:{ORG_A}")
    real = sb.SandboxBroker(docker=FakeDocker())  # type: ignore[arg-type]
    real._note_docker(True)
    monkeypatch.setattr(sb, "_BROKER", real)
    return real


def _plant(ws: Path) -> Path:
    """A hostile ``.git``: an fsmonitor program, a hooks path and a hook."""
    git = ws / ".git"
    hooks = git / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    marker = ws.parent / f"{ws.name}-PWNED"
    hook = hooks / "fsmonitor-evil"
    hook.write_text(f"#!/bin/sh\necho pwned > '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    for name in ("post-commit", "pre-push", "post-checkout"):
        (hooks / name).write_text(hook.read_text(encoding="utf-8"), encoding="utf-8")
        (hooks / name).chmod(0o755)
    (git / "config").write_text(
        HOSTILE_CONFIG.format(hook=hook.as_posix(), hooks=hooks.as_posix()), encoding="utf-8",
    )
    return marker


@pytest.fixture
def mounted(broker: sb.SandboxBroker) -> Path:
    """A personal working dir that a real broker start mounted, with a hostile ``.git``."""
    with bound_run(ORG_A, agent=AGENT, thread=new_thread(), instance=PERSONAL) as ws:
        asyncio.run(broker.acquire())
    _plant(ws)
    assert sb.is_sandbox_dir(ws)
    return ws


@pytest.fixture
def trap(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every host process start is recorded and raises."""
    seen: list[list[str]] = []

    def record(*args: Any, **_kw: Any) -> Any:
        argv = args[0] if args and isinstance(args[0], (list, tuple)) else list(args)
        seen.append([str(a) for a in argv])
        raise AssertionError(f"a host process started: {argv}")

    async def arecord(*args: Any, **kw: Any) -> Any:
        return record(list(args), **kw)

    monkeypatch.setattr(subprocess, "run", record)
    monkeypatch.setattr(subprocess, "Popen", record)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", arecord)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", arecord)
    monkeypatch.setattr(os, "system", record)
    return seen


def _code_tools() -> Any:
    import importlib

    return importlib.import_module("acb_skills.code_tools")


# ── each host git site ───────────────────────────────────────────────────────


def test_the_commit_after_code_task_skips_a_sandbox_dir(mounted: Path, trap: list) -> None:
    assert _code_tools()._commit_repo_changes(mounted, "t") is None
    assert trap == []


def test_the_git_dir_of_a_sandbox_run_is_empty(mounted: Path, trap: list) -> None:
    from orchestrator.executor import _git_dir_for

    assert _git_dir_for(Path("/clone/agent-x"), str(mounted), PERSONAL) == ""
    assert trap == []


def test_the_repair_dir_is_none_for_a_sandbox_dir(mounted: Path, trap: list) -> None:
    """The self-anneal and the self-mutation get no dir. The old fallback
    ``git_dir or effective_dir`` gave them the sandbox dir."""
    from orchestrator.executor import _repair_dir

    assert _repair_dir("", str(mounted)) is None
    assert _repair_dir(str(mounted), str(mounted)) is None
    assert _repair_dir(None, str(mounted)) is None
    assert trap == []


def test_the_head_capture_and_the_remote_check_skip(mounted: Path, trap: list) -> None:
    from orchestrator.executor import _commit_on_remote, _get_current_head

    assert asyncio.run(_get_current_head(str(mounted))) == ""
    assert asyncio.run(_commit_on_remote(str(mounted), "a" * 40)) is False
    assert trap == []


def test_the_push_guard_writes_no_hook(
    mounted: Path, trap: list, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No hook in a sandbox ``.git``, and an empty dir never means the cwd."""
    from orchestrator.executor import _install_push_guard

    (mounted / ".git" / "hooks" / "pre-push").unlink()
    asyncio.run(_install_push_guard(str(mounted)))
    assert not (mounted / ".git" / "hooks" / "pre-push").exists()
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    asyncio.run(_install_push_guard(""))
    assert list((tmp_path / ".git" / "hooks").iterdir()) == []
    assert trap == []


def test_the_commit_scan_skips(mounted: Path, trap: list) -> None:
    from orchestrator.executor import _detect_agent_commits

    queue = mounted / ".git" / "cc-commits-queue"
    queue.write_text("a" * 40 + "\n", encoding="utf-8")
    asyncio.run(_detect_agent_commits(AGENT, str(mounted), "run-1", since_sha="b" * 40))
    assert trap == []
    assert queue.read_text(encoding="utf-8").strip() == "a" * 40, "the scan read the queue"


def test_the_self_mutation_skips_and_costs_no_attempt(mounted: Path, trap: list) -> None:
    from orchestrator import mutation

    result = asyncio.run(mutation.attempt_self_mutation(
        AGENT, "run-sbx", RuntimeError("boom"), agent_dir=str(mounted),
    ))
    assert result.attempted is False
    assert "sandbox dir" in (result.skipped_reason or "")
    assert "run-sbx" not in mutation._MUTATION_ATTEMPTS
    assert trap == []


def test_the_mutation_git_helpers_skip(mounted: Path, trap: list) -> None:
    from acb_common import get_settings
    from orchestrator import mutation

    ws = str(mounted)
    assert asyncio.run(mutation._git_diff(ws, "a" * 40)) == ""
    assert asyncio.run(mutation._auto_push_commit(ws, "a" * 40)) is False
    asyncio.run(mutation._stash_pull_before_mutation(ws, AGENT))
    staged = asyncio.run(mutation._run_mutation_sandbox(
        AGENT, "run-x", "run-x", {"local_clone_dir": ws}, get_settings(),
    ))
    assert staged == (False, "", "", "", None)
    assert trap == []


# ── the control and the fail-closed rule ─────────────────────────────────────


def test_a_dir_no_container_mounted_still_runs_host_git(
    broker: sb.SandboxBroker, trap: list, tmp_path: Path,
) -> None:
    """Flag OFF = unchanged: a dir that no container mounted keeps host git.
    It also proves that the trap sees a host git start."""
    from orchestrator.executor import _get_current_head

    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    assert sb.host_git_allowed(plain) is True
    assert asyncio.run(_get_current_head(str(plain))) == ""
    assert trap and trap[0][:2] == ["git", "rev-parse"]


def test_an_unreadable_dir_list_fails_closed(
    broker: sb.SandboxBroker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def broken() -> set[Path]:
        raise sb.SandboxUnavailable("the state dir lies inside a mount root")

    monkeypatch.setattr(broker, "_sandbox_dirs", broken)
    assert sb.is_sandbox_dir(tmp_path) is True
    assert sb.host_git_allowed(tmp_path) is False


def test_an_unreadable_dir_list_with_an_empty_scope_keeps_host_git(
    broker: sb.SandboxBroker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Review P2-1: with no target in the scope, no container can start, so a
    bad state dir never stops the push guard and the commit scan of every run."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_coding_scope", "")

    def broken() -> set[Path]:
        raise sb.SandboxUnavailable("the state dir lies inside a mount root")

    monkeypatch.setattr(broker, "_sandbox_dirs", broken)
    assert sb.is_sandbox_dir(tmp_path) is False
    assert sb.host_git_allowed(tmp_path) is True


def test_the_default_state_dir_reads_the_dir_list(
    monkeypatch: pytest.MonkeyPatch, short_tmp: Path,  # noqa: F811
) -> None:
    """The default settings never take the fail-closed branch."""
    configure_env(monkeypatch, short_tmp)
    fresh = sb.SandboxBroker(docker=FakeDocker())  # type: ignore[arg-type]
    assert fresh._sandbox_dirs() == set()
    assert fresh.is_sandbox_dir(short_tmp) is False


def test_an_empty_path_is_never_a_git_dir() -> None:
    assert sb.host_git_allowed("") is False
    assert sb.host_git_allowed(None) is False


def test_a_stopped_container_still_marks_its_dir(
    broker: sb.SandboxBroker, mounted: Path,
) -> None:
    """The container's writes stay after it stops, so the dir stays a sandbox dir."""
    asyncio.run(broker.sweep())
    assert broker._live == {}
    assert sb.is_sandbox_dir(mounted) and sb.is_sandbox_dir(mounted / "agent-data")


# ── real git: the planted fsmonitor never runs ───────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("git") is None,
    reason="the fsmonitor hook needs a POSIX shell and git; CI runs it",
)
def test_real_git_never_runs_the_planted_fsmonitor(broker: sb.SandboxBroker) -> None:
    """With real git: ``git status`` in a dir with the planted config runs the
    fsmonitor program. The commit after ``code_task`` must not start it."""
    with bound_run(ORG_A, agent=AGENT, thread=new_thread(), instance=PERSONAL) as ws:
        asyncio.run(broker.acquire())
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "agent-data").mkdir(exist_ok=True)
    (ws / "tracked.txt").write_text("x\n", encoding="utf-8")
    marker = _plant(ws)
    assert _code_tools()._commit_repo_changes(ws, "t") is None
    assert not marker.exists(), "host git ran the planted fsmonitor"

    # The control: the same plant in a dir that no container mounted runs.
    plain = ws.parent.parent / "control-repo"
    subprocess.run(["git", "init", "-q", str(plain)], check=True)
    (plain / "tracked.txt").write_text("x\n", encoding="utf-8")
    control = _plant(plain)
    subprocess.run(["git", "status", "--porcelain"], cwd=plain, capture_output=True)
    assert control.exists(), "the plant does not trigger, so this test proves nothing"
