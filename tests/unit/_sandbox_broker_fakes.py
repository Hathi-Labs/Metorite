"""Shared fakes for the WS-43c sandbox broker fences (WS43-F2 to WS43-F4).

``FakeDocker`` stands in for ``sandbox_broker.DockerCLI``. It records every
argv and answers the way the real CLI does for the calls the broker makes.
``bound_run`` binds a run the way the executor does: the tenant in
``executor._RUN_ORG`` under the run's thread id, and the agent, the thread,
the store key and the working dir in the run's artifact context.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

#: A pinned reference of the right shape. No test pulls it.
PINNED_IMAGE = "metorite/coding-sandbox@sha256:" + "a" * 64


class FakeDocker:
    """Records each docker argv and answers like the real CLI."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_run = False
        self.ps_ids: list[str] = []
        self.sweep_results: list[sb.DockerResult] = []
        self.stream_results: list[Any] = []
        self.inspect_labels: dict[str, str] = {}
        self.name_in_use_once = False
        self.on_stream: Callable[[list[str]], Any] | None = None
        self.block_run: asyncio.Event | None = None
        self.block_rm: asyncio.Event | None = None
        self.block_ps: asyncio.Event | None = None
        self.block_sweep: asyncio.Event | None = None
        self.started = 0

    async def run(self, args: Sequence[str], *, timeout: float) -> sb.DockerResult:
        argv = list(args)
        self.calls.append(argv)
        verb = argv[0]
        if verb == "run":
            if self.name_in_use_once:
                self.name_in_use_once = False
                return sb.DockerResult(125, "", "Conflict. The container name is already in use")
            if self.fail_run:
                return sb.DockerResult(125, "", "docker: Error response from daemon: boom")
            if self.block_run is not None:
                await self.block_run.wait()
            self.started += 1
            return sb.DockerResult(0, f"cid-{self.started}\n", "")
        if verb == "exec" and sb.PID_PROBE in argv:
            return sb.DockerResult(0, "7\n", "")
        if verb == "exec" and sb.KILL_SWEEP in argv:
            if self.block_sweep is not None:
                await self.block_sweep.wait()
            if self.sweep_results:
                return self.sweep_results.pop(0)
            return sb.DockerResult(0, "clean\n", "")
        if verb == "ps":
            if self.block_ps is not None:
                await self.block_ps.wait()
            return sb.DockerResult(0, "".join(f"{i}\n" for i in self.ps_ids), "")
        if verb == "inspect":
            import json

            return sb.DockerResult(0, "cid-stale|" + json.dumps(self.inspect_labels), "")
        if verb == "rm" and self.block_rm is not None:
            await self.block_rm.wait()
        return sb.DockerResult(0, "", "")

    async def stream(
        self, args: Sequence[str], *, timeout: float, head: int, tail: int,
    ) -> sb.StreamResult:
        argv = list(args)
        self.calls.append(argv)
        if self.on_stream is not None:
            await self.on_stream(argv)
        if self.stream_results:
            return self.stream_results.pop(0)
        return sb.StreamResult(0, "ok\n", 3, False, "", False)

    # ── views ───────────────────────────────────────────────────────────────

    def verbs(self) -> list[str]:
        return [c[0] for c in self.calls]

    def runs(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "run"]

    def command_execs(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "exec" and sb.EXEC_WRAPPER in c]

    def sweeps(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "exec" and sb.KILL_SWEEP in c]

    def removals(self) -> list[str]:
        """Every container id that an ``rm -f`` named, in order."""
        return [i for c in self.calls if c[:2] == ["rm", "-f"] for i in c[2:]]


def mounts_of(argv: Sequence[str]) -> list[str]:
    """Every ``--mount`` value of a ``docker run`` argv, in order."""
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]


def flag_values(argv: Sequence[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Settings for a broker under test: a fake clone dir, a pinned image, and
    a scope that names every organization. A test narrows what it needs."""
    from acb_common import get_settings

    settings = get_settings()
    clone = tmp_path / "agents"
    apps = tmp_path / "custom_apps"
    clone.mkdir()
    apps.mkdir()
    monkeypatch.setattr(settings, "agents_clone_dir", str(clone))
    monkeypatch.setattr(settings, "custom_apps_root", str(apps))
    monkeypatch.setattr(settings, "sandbox_state_dir", "")
    monkeypatch.setattr(settings, "sandbox_image", PINNED_IMAGE)
    monkeypatch.setattr(settings, "maf_coding_scope", "code_task:*,app_builder:*")
    monkeypatch.setattr(settings, "sandbox_min_free_disk_mb", 0)
    monkeypatch.setattr(sb, "_process_ids", lambda: (1000, 1000))
    return {"settings": settings, "clone": clone, "apps": apps, "tmp": tmp_path}


@contextlib.contextmanager
def bound_run(
    org: str | None,
    agent: str = "agent-x",
    thread: str = "thread-1",
    *,
    workspace: str | None = None,
    instance: str | None = None,
) -> Iterator[Path]:
    """Bind a run as the executor does, and yield its working dir.

    With no *instance*, a run of *org* gets its tenant key ``o:<org>`` and
    the tenant dir ``state/<agent>/<slug of o:<org>>``, as
    ``executor._tenant_workspace`` makes it.
    """
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator import executor

    key = instance if instance is not None else (tenant_instance(org) if org else "")
    if workspace is None:
        workspace = str(ensure_state_dir(agent, key)) if key else ""
    token = executor._stream_relay_thread_id.set(thread)
    if org:
        executor._RUN_ORG[thread] = org
    try:
        with artifact_context_scope():
            bind_artifact_context(
                session_id=thread, agent_name=agent, workspace_root=workspace,
                instance=key, member="member@example.com",
            )
            yield Path(workspace) if workspace else Path()
    finally:
        if org and executor._RUN_ORG.get(thread) == org:
            executor._RUN_ORG.pop(thread, None)
        executor._stream_relay_thread_id.reset(token)
