"""Mutation-runner `docker run` hardening (BO-7 cheap win 3/3).

The mutation container was the concrete "existing sandbox pattern" BO-7 wants
generalized to the normal agent load path, but it shipped with zero resource/
capability limits. Locks the hardening flags actually reach the `docker run`
invocation — the full flow needs Docker, so this fakes the subprocess call and
inspects the command it was built with.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from orchestrator.mutation import _run_mutation_sandbox


class _FakeProc:
    returncode = 0

    async def communicate(self):
        return b"", b""

    def kill(self):
        pass


def _fake_telemetry() -> dict:
    return {
        "local_clone_dir": None,
        "repo_url": "https://github.com/example/agent-x.git",
        "bot_name": "acb-bot",
        "bot_email": "acb-bot@example.com",
        "error_type": "RuntimeError",
        "error_message": "boom",
        "error": "RuntimeError: boom",
    }


@pytest.mark.asyncio
async def test_docker_run_includes_hardening_flags(monkeypatch):
    captured: dict[str, list[str]] = {}

    async def _fake_exec(*args, **kwargs):
        captured["cmd"] = list(args)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    # H-363: the container never gets github_token. A Copilot-only token
    # starts it.
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "fake-copilot-only-token")

    settings = types.SimpleNamespace(
        github_token="fake-token",
        gateway_internal_token="",
        litellm_master_key="",
        litellm_base_url="http://host.docker.internal:8080",
        mutation_model="tier-powerful",
        mutation_sandbox_image="acb-mutation-runner:latest",
        mutation_timeout_seconds=600,
        mutation_memory_limit="2g",
        mutation_cpu_limit="2",
        mutation_pids_limit=512,
    )

    await _run_mutation_sandbox(
        "agent-x", "run-1", "run1", _fake_telemetry(), settings,
    )

    cmd = captured["cmd"]
    assert "--cap-drop" in cmd and cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--cap-add" in cmd and cmd[cmd.index("--cap-add") + 1] == "DAC_OVERRIDE"
    assert "--security-opt" in cmd and cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    assert "--memory" in cmd and cmd[cmd.index("--memory") + 1] == "2g"
    assert "--cpus" in cmd and cmd[cmd.index("--cpus") + 1] == "2"
    assert "--pids-limit" in cmd and cmd[cmd.index("--pids-limit") + 1] == "512"


@pytest.mark.asyncio
async def test_docker_run_hardening_uses_settings_overrides(monkeypatch):
    captured: dict[str, list[str]] = {}

    async def _fake_exec(*args, **kwargs):
        captured["cmd"] = list(args)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    # H-363: the container never gets github_token. A Copilot-only token
    # starts it.
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "fake-copilot-only-token")

    settings = types.SimpleNamespace(
        github_token="fake-token",
        gateway_internal_token="",
        litellm_master_key="",
        litellm_base_url="http://host.docker.internal:8080",
        mutation_model="tier-powerful",
        mutation_sandbox_image="acb-mutation-runner:latest",
        mutation_timeout_seconds=600,
        mutation_memory_limit="4g",
        mutation_cpu_limit="1.5",
        mutation_pids_limit=128,
    )

    await _run_mutation_sandbox(
        "agent-x", "run-1", "run1", _fake_telemetry(), settings,
    )

    cmd = captured["cmd"]
    assert cmd[cmd.index("--memory") + 1] == "4g"
    assert cmd[cmd.index("--cpus") + 1] == "1.5"
    assert cmd[cmd.index("--pids-limit") + 1] == "128"
