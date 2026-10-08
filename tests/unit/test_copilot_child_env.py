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
4. The upstream ``GitHubCopilotAgent.start`` (``_agent.py`` line 772), which
   builds a client with no env. ``orchestrator.copilot_agent`` binds a guard on
   the CLASS (fix round 1), so a plain agent, as ``apps/agents/*/agents.py``
   builds it, gets ``copilot_env()`` on every path: ``async with``, the batch
   run ``_run_with_maf_agent`` (``POST /agent/run``, workflows, the email
   consult, the self-anneal retries) and the sub-agent block of
   ``_run_sub_agent_streaming`` (``call_agent``).
5. No token: the client gets no ``github_token``, so the CLI keeps its
   logged-in user, and its env is still clean (BH-D3).

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
from typing import ClassVar

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


def _assert_clean_no_token(env: dict[str, str]) -> None:
    for name in CANARIES:
        assert name not in env, f"{name} reached the Copilot CLI child"
    for name in TOKEN_NAMES:
        assert name not in env, name
    assert "COPILOT_SDK_AUTH_TOKEN" not in env
    assert not [k for k, v in env.items() if "bh1canary" in v]


async def test_no_token_still_starts_with_a_clean_env(
    stub: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no token, the CLI's logged-in user must still work (BH-D3, fix
    round 1). The client starts with no github_token, and the env stays clean."""
    from orchestrator.copilot_agent import MetoriteCopilotAgent

    cli, out = stub
    monkeypatch.delenv("GITHUB_TOKEN")
    agent = MetoriteCopilotAgent(
        name="bh1", instructions="x", default_options={"cli_path": cli}
    )
    try:
        await _start_and_fail(agent.start())
    finally:
        with contextlib.suppress(BaseException):
            await agent.stop()
    _assert_clean_no_token(_child_env(out))


def _plain_agent(cli: str) -> object:
    """A plain GitHubCopilotAgent, built as apps/agents/*/agents.py builds it."""
    import orchestrator  # noqa: F401 - the package import installs the guard
    from agent_framework_github_copilot import GitHubCopilotAgent

    return GitHubCopilotAgent(
        instructions="x",
        default_options={"model": "tier-balanced", "mcp_servers": {}, "cli_path": cli},
    )


async def test_plain_agent_async_with(stub: tuple[str, Path]) -> None:
    """The reviewer's probe: ``async with`` on a plain agent."""
    cli, out = stub
    agent = _plain_agent(cli)
    with contextlib.suppress(BaseException):
        async with agent:  # type: ignore[attr-defined]
            pass
    _assert_clean(_child_env(out))


async def test_plain_agent_through_the_batch_run(stub: tuple[str, Path]) -> None:
    """``_run_with_maf_agent``: POST /agent/run, workflows, the email consult,
    projects dispatch and the self-anneal retries all start the agent here."""
    from orchestrator.executor import _run_with_maf_agent

    cli, out = stub
    with pytest.raises(BaseException):  # noqa: B017 - the stub exits on purpose
        await asyncio.wait_for(
            _run_with_maf_agent(
                [_plain_agent(cli)], agent_name="bh1", run_id="bh1-run",
                thread_id="bh1-thread", event_payload={"message": "hi"},
                integrations={},
            ),
            60,
        )
    _assert_clean(_child_env(out))


async def test_plain_agent_through_the_sub_agent_block(
    stub: tuple[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_run_sub_agent_streaming`` (``call_agent``): its Copilot branch runs
    ``async with agent:`` on the loaded plain agent."""
    import gateway.routes.agent as agent_routes
    from orchestrator import executor

    cli, out = stub
    plain = _plain_agent(cli)

    class _Loaded:
        config: ClassVar[dict[str, object]] = {"name": "bh1", "sharing": {"instancing": "personal"}}
        agent_dir = tmp_path

        def build_agents(self) -> list[object]:
            return [plain]

    @contextlib.contextmanager
    def fake_load(*_a: object, **_k: object):  # type: ignore[no-untyped-def]
        yield _Loaded()

    async def allowed(_name: str) -> None:
        return None

    monkeypatch.setattr(
        agent_routes, "_load_dynamic_agents",
        lambda: [{"name": "bh1", "agent_runtime": "github-copilot"}],
    )
    monkeypatch.setattr(executor, "load_agent", fake_load)
    monkeypatch.setattr(executor, "_assert_may_run_agent", allowed)
    # Keep the run off the real ~/.acb/agents state dir.
    monkeypatch.setattr(
        executor, "_resolve_run_workspace", lambda *_a, **_k: (str(tmp_path), "")
    )
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        # A personal sub-agent needs the parent's member for its working dir.
        bind_artifact_context(member="bh1@example.com")
        answer = await asyncio.wait_for(
            executor._run_sub_agent_streaming("bh1", "hi", "bh1-run"), 60
        )
    assert "failed" in answer, answer
    _assert_clean(_child_env(out))


def test_the_orchestrator_package_installs_the_guard() -> None:
    """Every process that imports any orchestrator module has the guard, so a
    NEW path that starts a plain agent cannot reach the upstream client."""
    import subprocess

    code = (
        "import orchestrator.sandbox_broker\n"
        "from agent_framework_github_copilot import GitHubCopilotAgent as G\n"
        "print(getattr(G.start, '_ws49_bh1_child_env_guard', False))\n"
    )
    done = subprocess.run(  # the test's own child, not gateway code
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180,
    )
    assert done.stdout.strip().splitlines()[-1] == "True", done.stderr[-2000:]


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
