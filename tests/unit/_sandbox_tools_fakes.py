"""Shared set-up for the WS-43d suites (WS43-F6, F7, F14, F21, F22).

``sandbox`` gives a broker on a fake Docker, with the scope set to
``projects:<ORG_A>``, a healthy broker, and the D85 seam present. The D85 seam
is ``_tool_injection._withheld_shell_tools`` of PR #598. Until that PR is on
the branch, ``sandbox_broker._host_shell_withheld`` answers ``False``, so the
fixture stands in for it. A test that needs the real answer does not use the
patch.

``host_trap`` makes every way of starting a host process raise, so a test
proves that a command ran only through the broker.
"""
from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import FakeDocker, configure_env

PA = "projects-assistant"
ORG_A = "aaaaaaaa-0000-0000-0000-0000000000a1"
ORG_B = "bbbbbbbb-0000-0000-0000-0000000000b2"


def new_thread() -> str:
    """A thread id as the chat makes it: a UUID."""
    return str(uuid.uuid4())


@dataclass
class Sandbox:
    env: dict[str, Any]
    docker: FakeDocker
    broker: sb.SandboxBroker
    mirrored: list[tuple[str, bytes]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)

    def set_scope(self, monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
        monkeypatch.setattr(self.env["settings"], "maf_coding_scope", scope)


@pytest.fixture
def short_tmp() -> Any:
    """A temp dir with a short path.

    A thread folder sits five levels below the state root, with two UUIDs in
    its path. Under pytest's own long temp names that passes the 260-character
    limit of a Windows path. Production is Linux, where the limit is 4096.
    """
    import shutil
    import tempfile

    base = Path(tempfile.mkdtemp(prefix="w43")).resolve()
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> Sandbox:
    env = configure_env(monkeypatch, short_tmp)
    monkeypatch.setattr(env["settings"], "maf_coding_scope", f"projects:{ORG_A}")
    docker = FakeDocker()
    broker = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    broker._note_docker(True)
    monkeypatch.setattr(sb, "_BROKER", broker)
    monkeypatch.setattr(sb, "_host_shell_withheld", lambda agent: True)
    box = Sandbox(env=env, docker=docker, broker=broker)

    async def mirror(rel: str, data: bytes, **_kw: Any) -> None:
        box.mirrored.append((rel, bytes(data)))

    async def mirror_delete(rel: str, **_kw: Any) -> None:
        box.deleted.append(rel)

    def announce(rel: str, data: bytes) -> str:
        box.cards.append(rel)
        return f"/api/agent/workspace/x/file?path={rel}"

    import importlib

    # ``acb_skills`` exports a FUNCTION named ``write_artifact``, which hides
    # the module of that name from ``import acb_skills.write_artifact as``.
    code_tools = importlib.import_module("acb_skills.code_tools")
    sandbox_tools = importlib.import_module("acb_skills.sandbox_tools")
    write_artifact = importlib.import_module("acb_skills.write_artifact")

    monkeypatch.setattr(write_artifact, "mirror_to_blob_store", mirror)
    monkeypatch.setattr(code_tools, "mirror_to_blob_store", mirror)
    monkeypatch.setattr(write_artifact, "mirror_delete_from_blob_store", mirror_delete)
    monkeypatch.setattr(write_artifact, "announce_artifact", announce)
    monkeypatch.setattr(sandbox_tools, "announce_artifact", announce)
    return box


@pytest.fixture
def host_trap(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every host process start raises, and is recorded."""
    seen: list[str] = []

    def refuse(name: str) -> Any:
        def _raise(*args: Any, **kwargs: Any) -> Any:
            seen.append(name)
            raise AssertionError(f"a host process started through {name}")
        return _raise

    monkeypatch.setattr(subprocess, "run", refuse("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse("create_subprocess_exec"))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", refuse("create_subprocess_shell"))
    import os

    monkeypatch.setattr(os, "system", refuse("os.system"))
    return seen
