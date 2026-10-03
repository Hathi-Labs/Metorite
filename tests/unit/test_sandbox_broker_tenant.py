"""WS43-F3 — the tenant, the roots, the scope and the fair eviction.

Spec ``project-docs/specs/maf_coding_engine.md`` §7.1 rules 1, 2, 4, 5, 8, 12,
13 and 14, §7.7, and §10 WS43-F3. R5 binds: the tenant comes from the run
binding, never from input.

What breaks this fence: the organization comes from input, a run with no
tenant starts a container, a container of org A serves org B, a mount lies
outside the allowed roots, or the eviction breaks the fair share. A full org
stops its own oldest idle container. A full box stops the oldest idle
container of any org.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import (
    FakeDocker,
    bound_run,
    configure_env,
    flag_values,
    mounts_of,
)

ORG_A = "aaaaaaaa-0000-0000-0000-00000000000a"
ORG_B = "bbbbbbbb-0000-0000-0000-00000000000b"
ORG_C = "cccccccc-0000-0000-0000-00000000000c"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    return configure_env(monkeypatch, tmp_path)


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def broker(docker: FakeDocker, env: dict[str, Any], clock: Clock) -> sb.SandboxBroker:
    return sb.SandboxBroker(docker=docker, clock=clock)  # type: ignore[arg-type]


def _label(argv: list[str], key: str) -> str:
    return dict(v.split("=", 1) for v in flag_values(argv, "--label"))[key]


# ── rule 2: the tenant comes from the run binding ───────────────────────────


def test_acquire_takes_no_argument() -> None:
    """No caller can name a tenant, a dir or a thread (R5)."""
    params = inspect.signature(sb.SandboxBroker.acquire).parameters
    assert list(params) == ["self"]


async def test_a_run_with_no_tenant_starts_nothing(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
) -> None:
    ws = env["clone"] / "state" / "agent-x" / "o_x"
    ws.mkdir(parents=True)
    with bound_run(None, workspace=str(ws), instance="o:x"), pytest.raises(
        sb.SandboxRefused, match="no organization"
    ):
        await broker.acquire()
    assert docker.calls == [], "no docker call may run for a run with no tenant"


async def test_no_bound_run_starts_nothing(broker: sb.SandboxBroker, docker: FakeDocker) -> None:
    with pytest.raises(sb.SandboxRefused):
        await broker.acquire()
    assert docker.calls == []


async def test_the_org_comes_from_run_org_and_the_mount_from_the_binding(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    with bound_run(ORG_A, thread="t-1") as ws:
        handle = await broker.acquire()
    argv = docker.runs()[-1]
    assert _label(argv, "metorite.org") == ORG_A
    assert handle.org == ORG_A
    from acb_skills.agent_paths import agent_state_dir, tenant_instance

    tenant_dir = agent_state_dir("agent-x", tenant_instance(ORG_A)).resolve()
    assert ws.resolve() == tenant_dir
    assert mounts_of(argv)[0] == f"type=bind,source={tenant_dir},target=/workspace"


async def test_an_org_in_the_artifact_context_is_ignored(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    """An ``organization_id`` key in the context is not the run binding."""
    from acb_skills.write_artifact import derive_artifact_context

    with bound_run(ORG_A, thread="t-ctx"):
        derive_artifact_context(organization_id=ORG_B, org=ORG_B)
        handle = await broker.acquire()
    assert handle.org == ORG_A
    assert _label(docker.runs()[-1], "metorite.org") == ORG_A


async def test_org_b_dir_is_refused_for_an_org_a_run(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    """A container for org A never mounts org B's dir."""
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance

    b_dir = str(ensure_state_dir("agent-x", tenant_instance(ORG_B)))
    # B's key and B's dir under A's binding.
    with bound_run(ORG_A, workspace=b_dir, instance=tenant_instance(ORG_B)), pytest.raises(
        sb.SandboxRefused, match="another organization"
    ):
        await broker.acquire()
    # A's key with B's dir.
    with bound_run(ORG_A, workspace=b_dir, instance=tenant_instance(ORG_A)), pytest.raises(
        sb.SandboxRefused, match="not its own state dir"
    ):
        await broker.acquire()
    assert docker.runs() == []


async def test_one_thread_id_in_two_orgs_gives_two_containers(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    """The thread id is client input, so it never lends org A's container."""
    with bound_run(ORG_A, thread="shared-thread") as ws_a:
        a = await broker.acquire()
    with bound_run(ORG_B, thread="shared-thread") as ws_b:
        b = await broker.acquire()
    assert a is not b
    assert a.name != b.name
    assert ws_a.resolve() != ws_b.resolve()
    run_a, run_b = docker.runs()
    assert _label(run_a, "metorite.org") == ORG_A
    assert _label(run_b, "metorite.org") == ORG_B
    assert str(ws_a.resolve()) in mounts_of(run_a)[0]
    assert str(ws_b.resolve()) in mounts_of(run_b)[0]
    assert str(ws_a.resolve()) not in " ".join(mounts_of(run_b))


async def test_reuse_only_on_a_full_match(broker: sb.SandboxBroker, docker: FakeDocker) -> None:
    with bound_run(ORG_A, thread="t-r"):
        first = await broker.acquire()
        await broker.release(first)
        second = await broker.acquire()
    assert first is second
    assert len(docker.runs()) == 1
    with bound_run(ORG_A, agent="agent-y", thread="t-r"):
        other = await broker.acquire()
    assert other is not first
    assert len(docker.runs()) == 2


async def test_a_name_held_by_another_tenant_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    """A stale container under the name is removed only when it is OURS."""
    docker.name_in_use_once = True
    docker.inspect_labels = {"metorite.org": ORG_B, "metorite.agent": "agent-x"}
    with bound_run(ORG_A, thread="t-n"), pytest.raises(sb.SandboxRefused, match="another tenant"):
        await broker.acquire()
    assert docker.removals() == []
    docker.name_in_use_once = True
    docker.inspect_labels = {"metorite.org": ORG_A, "metorite.agent": "agent-x"}
    with bound_run(ORG_A, thread="t-n"):
        await broker.acquire()
    assert len(docker.removals()) == 1


# ── rule 5: the allowed roots ────────────────────────────────────────────────


async def test_a_dir_outside_the_roots_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
) -> None:
    outside = env["tmp"] / "elsewhere"
    outside.mkdir()
    with bound_run(ORG_A, workspace=str(outside)), pytest.raises(sb.SandboxRefused):
        await broker.acquire()
    with bound_run(ORG_A, workspace=str(env["clone"] / "repos" / "agent-x")), pytest.raises(
        sb.SandboxRefused
    ):
        await broker.acquire()
    assert docker.runs() == []


async def test_a_shared_run_with_no_store_key_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
) -> None:
    clone = env["clone"] / "repos" / "agent-x"
    clone.mkdir(parents=True)
    with bound_run(ORG_A, workspace=str(clone), instance=""), pytest.raises(
        sb.SandboxRefused, match="no store key"
    ):
        await broker.acquire()
    assert docker.runs() == []


async def test_the_custom_apps_root_is_for_app_builder_only(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
) -> None:
    app = env["apps"] / "my-app"
    app.mkdir()
    with bound_run(ORG_A, agent="agent-x", workspace=str(app), instance=""), pytest.raises(
        sb.SandboxRefused
    ):
        await broker.acquire()
    with bound_run(ORG_A, agent="app-builder", workspace=str(app), instance=""):
        handle = await broker.acquire()
    assert handle.workspace == app.resolve()
    with bound_run(ORG_A, agent="app-builder", workspace=str(env["apps"]), instance=""), (
        pytest.raises(sb.SandboxRefused, match="root itself")
    ):
        await broker.acquire()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
async def test_a_dir_with_a_link_part_is_refused(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
) -> None:
    from acb_skills.agent_paths import agent_state_dir, state_root, tenant_instance

    real = env["tmp"] / "real-agent"
    real.mkdir()
    state_root().mkdir(parents=True, exist_ok=True)
    (state_root() / "agent-x").symlink_to(real, target_is_directory=True)
    ws = agent_state_dir("agent-x", tenant_instance(ORG_A))
    ws.mkdir(parents=True)
    with bound_run(ORG_A, workspace=str(ws)), pytest.raises(sb.SandboxRefused, match="link"):
        await broker.acquire()
    assert docker.runs() == []


# ── rule 8: the caps and the fair eviction ───────────────────────────────────


async def _idle(broker: sb.SandboxBroker, clock: Clock, org: str, thread: str) -> sb.SandboxHandle:
    with bound_run(org, thread=thread):
        handle = await broker.acquire()
    await broker.release(handle)
    clock.now += 1
    return handle


async def test_a_full_org_stops_its_own_oldest_idle_container(
    broker: sb.SandboxBroker, docker: FakeDocker, clock: Clock,
) -> None:
    a1 = await _idle(broker, clock, ORG_A, "a1")
    a2 = await _idle(broker, clock, ORG_A, "a2")
    b1 = await _idle(broker, clock, ORG_B, "b1")
    with bound_run(ORG_A, thread="a3"):
        await broker.acquire()
    assert docker.removals() == [a1.name], "org A must stop its OWN oldest idle container"
    assert a1.removed and not a2.removed and not b1.removed


async def test_a_full_box_stops_the_oldest_idle_container_of_any_org(
    broker: sb.SandboxBroker, docker: FakeDocker, clock: Clock,
) -> None:
    b1 = await _idle(broker, clock, ORG_B, "b1")
    a1 = await _idle(broker, clock, ORG_A, "a1")
    b2 = await _idle(broker, clock, ORG_B, "b2")
    a2 = await _idle(broker, clock, ORG_A, "a2")
    with bound_run(ORG_C, thread="c1"):
        await broker.acquire()
    assert docker.removals() == [b1.name], "the box cap stops the oldest idle of ANY org"
    assert not (a1.removed or b2.removed or a2.removed)


async def test_a_full_org_with_no_idle_container_is_busy(
    broker: sb.SandboxBroker, docker: FakeDocker, clock: Clock,
) -> None:
    """It never takes another org's container to make room for itself."""
    with bound_run(ORG_A, thread="a1"):
        await broker.acquire()
    with bound_run(ORG_A, thread="a2"):
        await broker.acquire()
    await _idle(broker, clock, ORG_B, "b1")
    with bound_run(ORG_A, thread="a3"), pytest.raises(sb.SandboxBusy, match="share"):
        await broker.acquire()
    assert docker.removals() == []
    assert len(docker.runs()) == 3


async def test_a_full_box_with_no_idle_container_is_busy(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    for org, thread in ((ORG_A, "a1"), (ORG_A, "a2"), (ORG_B, "b1"), (ORG_B, "b2")):
        with bound_run(org, thread=thread):
            await broker.acquire()
    with bound_run(ORG_C, thread="c1"), pytest.raises(sb.SandboxBusy, match="maximum"):
        await broker.acquire()
    assert docker.removals() == []


async def test_the_caps_come_from_settings(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any], clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_max_per_org", 1)
    a1 = await _idle(broker, clock, ORG_A, "a1")
    with bound_run(ORG_A, thread="a2"):
        await broker.acquire()
    assert docker.removals() == [a1.name]


# ── the scope (MAF_CODING_SCOPE) and covers() ───────────────────────────────


async def test_an_empty_scope_starts_no_container(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "maf_coding_scope", "")
    with bound_run(ORG_A), pytest.raises(sb.SandboxRefused, match="MAF_CODING_SCOPE"):
        await broker.acquire()
    assert docker.calls == []


@pytest.mark.parametrize("scope,agent,allowed", [
    (f"code_task:{ORG_A}", "agent-x", True),
    (f"code_task:{ORG_B}", "agent-x", False),
    (f"app_builder:{ORG_A}", "agent-x", False),
    (f"app_builder:{ORG_A}", "app-builder", True),
    (f"code_task:{ORG_A}", "app-builder", False),
    ("code_task:*", "agent-x", True),
    ("bogus:*,code_task:*", "agent-x", False),
])
async def test_the_scope_names_a_target_per_org(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, scope: str, agent: str, allowed: bool,
) -> None:
    monkeypatch.setattr(env["settings"], "maf_coding_scope", scope)
    if agent == "app-builder":
        app = env["apps"] / "app-one"
        app.mkdir(exist_ok=True)
        ctx = bound_run(ORG_A, agent=agent, workspace=str(app), instance="")
    else:
        ctx = bound_run(ORG_A, agent=agent)
    with ctx:
        if allowed:
            await broker.acquire()
        else:
            with pytest.raises(sb.SandboxRefused, match="MAF_CODING_SCOPE"):
                await broker.acquire()
    assert bool(docker.runs()) is allowed


def test_the_scope_parser() -> None:
    assert sb.parse_maf_coding_scope("") == frozenset()
    assert sb.parse_maf_coding_scope(f" code_task:{ORG_A} , app_builder:* ,") == frozenset({
        ("code_task", ORG_A), ("app_builder", "*"),
    })
    for bad in ("code_tasks:x", "shell:*", "*", "code_task", "code_task:", ":x", "code_task:a b"):
        with pytest.raises(ValueError):
            sb.parse_maf_coding_scope(bad)


def test_covers_is_false_for_every_agent_until_ws43f(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "maf_coding_scope", "code_task:*,app_builder:*")
    for agent in ("agent-x", "app-builder", "metorite", ""):
        for org in (ORG_A, "*", ""):
            assert sb.covers(agent, org) is False
            assert sb.SandboxBroker.covers(agent, org) is False


# ── rule 13: the startup sweep ALWAYS runs ───────────────────────────────────


async def test_the_startup_sweep_runs_with_an_empty_scope(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "maf_coding_scope", "")
    docker.ps_ids = ["old1", "old2"]
    await broker.startup()
    assert ["ps", "-aq", "--filter", "label=metorite.sandbox=1"] in docker.calls
    assert ["rm", "-f", "old1", "old2"] in docker.calls


async def test_the_startup_sweep_survives_no_docker(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sb.shutil, "which", lambda _name: None)
    broker = sb.SandboxBroker()
    await broker.startup()  # never raises


async def test_the_lifespan_schedules_the_sweep_and_acquire_waits(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    broker.start()
    with bound_run(ORG_A, thread="t-s"):
        await broker.acquire()
    assert docker.verbs()[:1] == ["ps"], "acquire must wait for the startup sweep"
    await broker.stop()


def test_the_gateway_lifespan_starts_the_broker_unconditionally() -> None:
    main = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/main.py"
    before, sep, after = main.read_text(encoding="utf-8").partition("\n    yield\n")
    assert sep
    assert "start_sandbox_broker()" in before
    assert "stop_sandbox_broker()" in after
    block = before[before.index("start_sandbox_broker"):][:200]
    assert "maf_coding_scope" not in block.lower() and "if " not in block


# ── rule 5: the sandbox-dir list ─────────────────────────────────────────────


async def test_a_mounted_dir_stays_a_sandbox_dir_after_its_container_stops(
    broker: sb.SandboxBroker, docker: FakeDocker, clock: Clock, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with bound_run(ORG_A, thread="t-d") as ws:
        handle = await broker.acquire()
    assert broker.is_sandbox_dir(ws)
    assert broker.is_sandbox_dir(ws / "agent-data" / "x.py")
    await broker.release(handle)
    clock.now += 10_000
    assert await broker.reap_once() == 1
    fresh = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    assert fresh.is_sandbox_dir(ws), "the list must persist across a restart"
    with pytest.raises(sb.SandboxRefused, match="Host git refuses"):
        fresh.refuse_if_sandbox_dir(ws)
    assert not fresh.is_sandbox_dir(env["clone"] / "repos")
    listing = fresh.state_dir() / "sandbox_dirs.txt"
    from acb_skills.agent_paths import state_root

    assert not listing.resolve().is_relative_to(state_root().resolve())


async def test_startup_trims_the_list_to_dirs_that_exist(
    broker: sb.SandboxBroker, docker: FakeDocker,
) -> None:
    import shutil

    with bound_run(ORG_A, thread="t-1") as ws1:
        await broker.acquire()
    with bound_run(ORG_B, thread="t-2") as ws2:
        await broker.acquire()
    shutil.rmtree(ws1)
    fresh = sb.SandboxBroker(docker=docker)  # type: ignore[arg-type]
    await fresh.startup()
    text = (fresh.state_dir() / "sandbox_dirs.txt").read_text(encoding="utf-8")
    assert str(ws2.resolve()) in text and str(ws1.resolve()) not in text


async def test_a_state_dir_inside_a_mount_root_stops_the_broker(
    broker: sb.SandboxBroker, docker: FakeDocker, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        env["settings"], "sandbox_state_dir", str(env["clone"] / "state" / "broker")
    )
    with bound_run(ORG_A), pytest.raises(sb.SandboxUnavailable, match="mount root"):
        await broker.acquire()
    assert docker.runs() == []


# ── rule 12: the reaper ──────────────────────────────────────────────────────


async def test_the_reaper_stops_idle_and_old_containers_only(
    broker: sb.SandboxBroker, docker: FakeDocker, clock: Clock, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(env["settings"], "sandbox_idle_ttl_seconds", 100)
    monkeypatch.setattr(env["settings"], "sandbox_max_lifetime_seconds", 1000)
    idle = await _idle(broker, clock, ORG_A, "idle")
    with bound_run(ORG_B, thread="busy"):
        busy = await broker.acquire()
    clock.now += 50
    assert await broker.reap_once() == 0
    clock.now += 60
    assert await broker.reap_once() == 1
    assert idle.removed and not busy.removed, "the idle TTL never stops a leased container"
    clock.now += 2000
    async with broker.host_files(busy):
        assert await broker.reap_once() == 0, "no container stops under its lock"
    assert await broker.reap_once() == 1, "a leaked lease must not outlive the lifetime"
    assert busy.removed
    with pytest.raises(sb.SandboxRefused, match="stopped"):
        await broker.exec(busy, "true", 5)


async def test_a_failing_start_evicts_nobody(
    broker: sb.SandboxBroker, docker: FakeDocker, clock: Clock, env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every input of a start is checked before a container is evicted for it."""
    a1 = await _idle(broker, clock, ORG_A, "a1")
    await _idle(broker, clock, ORG_A, "a2")
    with bound_run(ORG_A, thread="a3") as ws:
        (ws / "sub").mkdir()
        (ws / "sub" / ".git").mkdir()
        with pytest.raises(sb.SandboxRefused, match="below its root"):
            await broker.acquire()
    monkeypatch.setattr(env["settings"], "sandbox_image", "x:latest")
    with bound_run(ORG_A, thread="a4"), pytest.raises(sb.SandboxRefused, match="pinned"):
        await broker.acquire()
    assert docker.removals() == [] and not a1.removed


# ── rule 14: no fallback to the host ─────────────────────────────────────────


async def test_a_failed_start_raises_and_runs_nothing_on_the_host(
    broker: sb.SandboxBroker, docker: FakeDocker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import subprocess

    spawned: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *a, **k: spawned.append(a)
    )
    monkeypatch.setattr(asyncio, "create_subprocess_shell", lambda *a, **k: spawned.append(a))
    docker.fail_run = True
    with bound_run(ORG_A, thread="t-f"), pytest.raises(sb.SandboxUnavailable):
        await broker.acquire()
    assert spawned == [], "no host process may start when the sandbox fails"
    assert [c[0] for c in docker.calls] == ["run"]


async def test_no_docker_binary_is_unavailable_not_a_host_run(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sb.shutil, "which", lambda _name: None)
    broker = sb.SandboxBroker()
    with bound_run(ORG_A, thread="t-nd"), pytest.raises(sb.SandboxUnavailable, match="Docker"):
        await broker.acquire()
