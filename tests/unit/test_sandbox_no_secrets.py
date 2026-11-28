"""SBX-F4, first part: no secret reaches a ``tools`` container (SBX-4b1).

Spec: ``project-docs/specs/agent_sandbox.md`` §5.4 and §12.4 decision 4,
done-when 21 and 22.

What breaks this fence: a platform secret reaches the env of the ``tools``
container, ``METORITE_API_TOKEN`` reaches a build command, the smoke command
gets a value other than ``METORITE_API_TOKEN`` through the exec env file, or
a process that a build command started still lives at the smoke command.

Two halves. The database-free half checks the ``docker run`` argv of the
``tools`` target and the exec env of each step. The ``sandbox_docker`` half
runs a real container, a real proxy and the door kit, with canary values in
the gateway's own env and settings.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import FakeDocker, bound_run, configure_env, flag_values
from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: F401
from tests.unit.test_user_tool_build import (  # noqa: F401
    _build,
    _fixture_package,
    _stop,
    engine,
    package_files,
    passing,
    real_build,
    write_package,
)

ORG = "aaaaaaaa-0000-4000-8000-0000000004b4"
CANARY_INTERNAL = "mtr-internal-canary-4b1-0000000000"
CANARY_LLM = "sk-sbx4b1CanaryLlmKeyValue0000000000"
CANARY_DB = "postgresql://canary:sbx4b1canarypw@db/acb"
CANARY_SESSION = "sbx4b1-session-secret-canary-00000"
#: The env names that a ``tools`` container may hold (§5.4). Each value is
#: fixed by the broker and holds no secret.
TOOLS_ENV = frozenset({
    "HOME", "PATH", "PIP_CACHE_DIR", "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_NO_INPUT",
    "PYTHONNOUSERSITE", "PYTHONSAFEPATH",
})


async def test_the_tools_container_env_holds_no_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Done-when 22, the argv half. The ``docker run`` of a ``tools``
    container sets the fixed names of :data:`TOOLS_ENV` and nothing else.
    Mutation: add any name to the tools env -> red."""
    env = configure_env(monkeypatch, tmp_path)
    s = env["settings"]
    monkeypatch.setattr(s, "maf_coding_scope", "tools:*")
    monkeypatch.setattr(s, "sandbox_egress_enabled", True)
    monkeypatch.setattr(s, "sandbox_egress_door_upstream", "10.0.0.5:8080")
    monkeypatch.setattr(s, "litellm_master_key", CANARY_LLM, raising=False)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", CANARY_INTERNAL)
    docker = FakeDocker()
    broker = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    with bound_run(ORG, thread=str(uuid.uuid4())):
        handle = await broker.acquire_tool()
    (run,) = docker.runs()
    names = {v.split("=", 1)[0] for v in flag_values(run, "--env")}
    assert names == TOOLS_ENV
    assert "--env-file" not in run, "no grant, so no env file at the start"
    text = " ".join(run)
    assert CANARY_LLM not in text and CANARY_INTERNAL not in text
    await broker.release_tool(handle)
    await broker.stop()


async def test_only_the_smoke_command_gets_the_token(engine: Any) -> None:  # noqa: F811
    """Done-when 22, the step half. The install, a build step, gets no
    ``METORITE_API_TOKEN``. The smoke command gets it, and nothing else, in
    its exec env file. Mutation: pass the token to the install -> red."""
    passing(engine.broker)
    async with engine.ute.start_build({"crm:read"}) as build:
        write_package(engine.tool_data / "pkg", package_files())
        assert (await build.run_smoke()).passed
    install, smoke = engine.broker.execs()
    assert "pip install" in install[1] and install[2] == {}
    assert set(smoke[2]) == {"METORITE_API_TOKEN"}
    assert frozenset({"METORITE_API_TOKEN"}) == sb.EXEC_ENV_NAMES


# ── sandbox_docker ──────────────────────────────────────────────────────────

_DUMP_ENV = """\
import json
import os

env = dict(os.environ)
out = {"pages": 2, "value": "fixture"}
with open("/workspace/outputs/result.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh)
print("ENV " + json.dumps(sorted(env)))
print("TOKEN " + str(env.get("METORITE_API_TOKEN", "")[:5]))
"""

_LIST_PROCS = """\
import json
import os

procs = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            procs.append(fh.read().replace(b"\\0", b" ").decode("utf-8", "replace").strip())
    except OSError:
        continue
with open("/workspace/outputs/result.json", "w", encoding="utf-8") as fh:
    json.dump({"pages": 2, "value": "fixture"}, fh)
print("PROCS " + json.dumps(procs))
"""


@pytest.mark.sandbox_docker
async def test_the_tools_container_holds_no_secret(
    real_build: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Done-when 22, the docker half (SBX-F4, first part). The env of the
    smoke command holds ``METORITE_API_TOKEN`` and no platform secret of
    §5.4. A build command holds no ``METORITE_API_TOKEN``, and a probe of the
    whole container finds no canary."""
    real = real_build
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", CANARY_INTERNAL)
    monkeypatch.setenv("DATABASE_URL", CANARY_DB)
    monkeypatch.setattr(real.settings, "litellm_master_key", CANARY_LLM, raising=False)
    monkeypatch.setattr(real.settings, "gateway_session_secret", CANARY_SESSION, raising=False)
    files = _fixture_package(_DUMP_ENV, reads=["crm:read"])
    seen: dict[str, str] = {}
    canaries = (CANARY_INTERNAL, CANARY_LLM, CANARY_DB, CANARY_SESSION)

    def between(n: int) -> dict[str, bytes]:
        return files

    real_round = real.ute.ToolBuild.run_smoke

    async def run_smoke(build: Any) -> Any:
        broker = real.broker
        command = await broker.exec(build.handle, "env | cut -d= -f1 | sort", 60)
        seen["build"] = command.output
        probe = await broker.exec(
            build.handle,
            "grep -rIl --exclude-dir=proc --exclude-dir=sys "
            + " ".join(f"-e '{c}'" for c in canaries) + " / 2>/dev/null | head -5; echo PROBE-DONE",
            120,
        )
        seen["probe"] = probe.output
        return await real_round(build)

    monkeypatch.setattr(real.ute.ToolBuild, "run_smoke", run_smoke)
    try:
        (result,) = await _build(real, files, {"crm:read"}, between=between)
    finally:
        await _stop(real.broker)
    assert result.passed, (result.error_class, result.output[-800:])
    env_line = next(x for x in result.output.splitlines() if x.startswith("ENV "))
    names = set(json.loads(env_line[4:]))
    assert "METORITE_API_TOKEN" in names
    assert not {"GATEWAY_INTERNAL_TOKEN", "DATABASE_URL", "LITELLM_MASTER_KEY",
                "GATEWAY_SESSION_SECRET"} & names, names
    assert "TOKEN msbx_" in result.output
    for canary in canaries:
        assert canary not in result.output
    assert "METORITE_API_TOKEN" not in seen["build"], "a build command holds the token"
    body = seen["probe"].replace("PROBE-DONE", "").strip()
    assert "PROBE-DONE" in seen["probe"] and "/" not in body, seen["probe"]


@pytest.mark.sandbox_docker
async def test_no_build_process_lives_at_the_smoke_command(
    real_build: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Done-when 21 (decision 4). A build command starts a process in the
    background. The grant of the smoke step recreates the container, so no
    process of the build lives at the smoke command, and none can read its
    ``/proc/<pid>/environ``. The smoke command reads ``/proc``."""
    real = real_build
    files = _fixture_package(_LIST_PROCS, reads=["crm:read"])
    real_round = real.ute.ToolBuild.run_smoke
    started: dict[str, str] = {}

    async def run_smoke(build: Any) -> Any:
        ran = await real.broker.exec(
            build.handle,
            "(setsid nohup sleep 3001 >/dev/null 2>&1 &) ; "
            "(nohup python3 -c 'import time; time.sleep(3002)' >/dev/null 2>&1 &) ; echo STARTED",
            30,
        )
        started["output"] = ran.output
        return await real_round(build)

    monkeypatch.setattr(real.ute.ToolBuild, "run_smoke", run_smoke)
    try:
        (result,) = await _build(real, files, {"crm:read"})
    finally:
        await _stop(real.broker)
    assert "STARTED" in started["output"]
    assert result.passed, (result.error_class, result.output[-800:])
    line = next(x for x in result.output.splitlines() if x.startswith("PROCS "))
    procs = json.loads(line[6:])
    assert procs, "the smoke command saw no process at all"
    assert not [p for p in procs if "3001" in p or "3002" in p], procs
