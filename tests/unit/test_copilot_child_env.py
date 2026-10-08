"""BH-F2 — the Copilot CLI child holds no secret of the gateway.

Spec ``project-docs/specs/box_hardening.md`` §5 BH-1, acceptance 1 to 4, and
HANDOFF H-270. The CLI has a shell tool. A prompt injection that reaches the
tool can read the CLI's env, so that env must hold no gateway secret.

Each test starts a REAL child through a real ``CopilotClient``: a stub CLI
that writes its env to a file and exits. The SDK then fails to connect, which
the tests expect. A secret planted in ``os.environ`` must not reach the file.
The SDK token must, as ``COPILOT_SDK_AUTH_TOKEN``.

The paths:

1. ``MetoriteCopilotAgent.start`` (Tier 1.5 and ``code_session``).
2. ``gateway.main._list_copilot_models``, which the startup warmup and
   ``/copilot/models`` call, through the route itself.
3. ``executor._tier2_copilot_client`` (Tier 2).
4. The upstream wrapper (``_agent.py`` line 772), which
   ``MetoriteCopilotAgent.start`` now refuses to reach.

``test_the_stub_sees_a_leak_without_env`` proves the stub can go red: the same
client with no ``env=`` hands the canary to the child.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import pytest

TOKEN = "ghu_bh1_sdk_token"
CANARIES = {
    "DATABASE_URL": "postgresql://u:bh1canary@h/db",
    "GATEWAY_INTERNAL_TOKEN": "bh1canary-internal",
    "OPENAI_API_KEY": "sk-bh1canary",
    "BH1_CANARY_SECRET": "bh1canary",
}
TOKEN_NAMES = ("COPILOT_GITHUB_TOKEN", "GITHUB_COPILOT_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """A stub CLI, and the file it writes its env to."""
    out = tmp_path / "child_env.json"
    script = tmp_path / "stub_cli.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(out)!r}, 'w', encoding='utf-8').write(json.dumps(dict(os.environ)))\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        cli = tmp_path / "stub_cli.cmd"
        cli.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        cli = tmp_path / "stub_cli"
        cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        cli.chmod(0o755)
    for name, value in CANARIES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GH_TOKEN", "gho_bh1canary")
    return str(cli), out


def _child_env(out: Path) -> dict[str, str]:
    assert out.is_file(), "the stub CLI never started"
    return json.loads(out.read_text(encoding="utf-8"))


def _assert_clean(env: dict[str, str]) -> None:
    for name in CANARIES:
        assert name not in env, f"{name} reached the Copilot CLI child"
    for name in TOKEN_NAMES:
        assert name not in env, f"{name} reached the Copilot CLI child by name"
    assert not [k for k, v in env.items() if "bh1canary" in v]
    assert "PATH" in env
    if os.name != "nt":
        assert "HOME" in env
    assert env.get("COPILOT_SDK_AUTH_TOKEN") == TOKEN, "the SDK token must still arrive"


async def _start_and_fail(start: object) -> None:
    with pytest.raises(BaseException):  # noqa: B017 - the stub exits on purpose
        await asyncio.wait_for(start, 60)  # type: ignore[arg-type]


async def test_metorite_agent_start(stub: tuple[str, Path]) -> None:
    from orchestrator.copilot_agent import MetoriteCopilotAgent

    cli, out = stub
    agent = MetoriteCopilotAgent(
        name="bh1", instructions="x", default_options={"cli_path": cli}
    )
    try:
        await _start_and_fail(agent.start())
    finally:
        with contextlib.suppress(BaseException):
            await agent.stop()
    _assert_clean(_child_env(out))


async def test_gateway_model_listing(stub: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the route. The CLI path comes from COPILOT_CLI_PATH, which
    copilot_env() keeps, so the SDK finds the stub as it finds an operator's
    override."""
    from gateway import main as gw

    cli, out = stub
    monkeypatch.setenv("COPILOT_CLI_PATH", cli)
    monkeypatch.setitem(gw._copilot_models_cache, "data", None)
    result = await asyncio.wait_for(gw.copilot_models(), 60)
    assert result["source"] != "live"
    _assert_clean(_child_env(out))


async def test_tier2_client(stub: tuple[str, Path]) -> None:
    from orchestrator.executor import _tier2_copilot_client

    cli, out = stub
    client = _tier2_copilot_client({"cli_path": cli})
    try:
        await _start_and_fail(client.start())
    finally:
        with contextlib.suppress(BaseException):
            await client.stop()
    _assert_clean(_child_env(out))


async def test_no_token_never_reaches_the_upstream_client(
    stub: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no token, the upstream start builds a client with no env. The
    agent must refuse before that, and no child must start."""
    from agent_framework.exceptions import AgentException
    from orchestrator.copilot_agent import MetoriteCopilotAgent

    cli, out = stub
    monkeypatch.delenv("GITHUB_TOKEN")
    agent = MetoriteCopilotAgent(
        name="bh1", instructions="x", default_options={"cli_path": cli}
    )
    with pytest.raises(AgentException):
        await agent.start()
    assert agent._client is None
    assert not out.exists()


async def test_the_stub_sees_a_leak_without_env(stub: tuple[str, Path]) -> None:
    """The red half: the SDK's own default hands the child every secret."""
    from copilot import CopilotClient, RuntimeConnection

    cli, out = stub
    client = CopilotClient(
        connection=RuntimeConnection.for_stdio(path=cli), github_token=TOKEN
    )
    try:
        await _start_and_fail(client.start())
    finally:
        with contextlib.suppress(BaseException):
            await client.stop()
    env = _child_env(out)
    assert env.get("BH1_CANARY_SECRET") == "bh1canary"
    with pytest.raises(AssertionError):
        _assert_clean(env)
