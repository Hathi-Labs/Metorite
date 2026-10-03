"""WS43-F22 — run data and thread-scoped outputs (D86, §16.3).

Spec ``project-docs/specs/maf_coding_engine.md`` §16.3 ("Data hygiene",
"Broker rule 5, for this target", "Outputs are thread-scoped") and §10
WS43-F22, and the narrowed done-when 4 to 6 of WS-43d.

What breaks this fence: a run-data dir lies under the tenant dir, shows in
the container of another thread, outlives its run, reaches the blob store,
``agent-data/`` or ``skills/``, or survives the startup sweep. Or a member of
the same organization, with another session or another thread, can list or
read the sandbox output folder of a thread, through the workspace routes or
from that thread's container.

Three halves:

* the fake-Docker half (the mounts, the run end, the sweep, the startup);
* the route half, on the R8 database as the NOBYPASSRLS app role, with the
  real workspace router and no stub (``TENANT_LADDER_DATABASE_URL``);
* the ``sandbox_docker`` half, on the coding image, which
  ``.github/workflows/sandbox-docker.yml`` runs and which fails on a skip.

Mutations this suite catches (R7), each run red once by hand:

* ``mount_list`` drops ``projects_mounts``: the mount test and the Docker test;
* ``end_run`` keeps the dir: the run-end tests;
* the ``await _end_sandbox_run()`` taken out of one ``finally``: the hook test;
* the route rule ``_is_other_thread_path`` answers ``False``: the route test;
* the sweep subdirs gain ``.run``: the blob-store test.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any

import pytest
from orchestrator import sandbox_broker as sb

from tests.unit._sandbox_broker_fakes import bound_run, configure_env, mounts_of
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    PA,
    host_trap,
    new_thread,
    sandbox,
    short_tmp,
)


def _binding(thread: str) -> sb.RunBinding:
    with bound_run(ORG_A, agent=PA, thread=thread):
        return sb.read_run_binding()


# ═════════════════════════ the fake-Docker half ═════════════════════════════


def test_the_run_data_dir_lies_outside_the_tenant_dir_and_every_kept_folder(sandbox) -> None:  # noqa: F811
    from acb_skills.agent_paths import state_root

    b = _binding(new_thread())
    assert not b.run_data.is_relative_to(b.workspace)
    assert b.run_data.is_relative_to(state_root().resolve() / ".run-data")
    for kept in ("agent-data", "skills", "inputs", "outputs"):
        assert kept not in b.run_data.relative_to(state_root().resolve()).parts


async def test_a_projects_start_mounts_the_thread_folder_and_the_run_data(sandbox) -> None:  # noqa: F811
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        await sandbox.broker.acquire()
        b = sb.read_run_binding()
    mounts = mounts_of(sandbox.docker.runs()[0])
    assert f"type=bind,source={b.workspace},target=/workspace" in mounts
    assert f"type=bind,source={b.workspace / b.outputs_rel},target=/workspace/outputs" in mounts
    assert f"type=bind,source={b.run_data},target=/workspace/.run" in mounts
    assert (b.workspace / b.outputs_rel).is_dir() and b.run_data.is_dir()
    assert (b.workspace / ".run").is_dir(), "the host made no mountpoint, so Docker would, as root"
    assert not any(f"source={b.workspace / 'outputs'}," in m for m in mounts), (
        "the shared outputs/ is never a mount source"
    )


async def test_another_target_gets_neither_nested_mount(sandbox, monkeypatch) -> None:  # noqa: F811
    sandbox.set_scope(monkeypatch, "code_task:*")
    with bound_run(ORG_A, agent="agent-x", thread=new_thread()):
        await sandbox.broker.acquire()
    mounts = mounts_of(sandbox.docker.runs()[0])
    assert not any("/workspace/outputs" in m or "/workspace/.run" in m for m in mounts)


async def test_two_threads_mount_only_their_own_folders(sandbox) -> None:  # noqa: F811
    t1, t2 = new_thread(), new_thread()
    for t in (t1, t2):
        with bound_run(ORG_A, agent=PA, thread=t):
            await sandbox.broker.acquire()
    b1, b2 = _binding(t1), _binding(t2)
    run2 = " ".join(mounts_of(sandbox.docker.runs()[1]))
    assert str(b1.workspace / b1.outputs_rel) not in run2
    assert str(b1.run_data) not in run2
    assert str(b2.workspace / b2.outputs_rel) in run2 and str(b2.run_data) in run2


async def test_a_restart_keeps_both_covers(sandbox) -> None:  # noqa: F811
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        handle = await sandbox.broker.acquire()
        sandbox.docker.sweep_results = [sb.DockerResult(3, "survivors: 99", "")]
        result = await sandbox.broker.exec(handle, "sleep 1 &", 5)
    assert result.restarted
    first, second = (mounts_of(r) for r in sandbox.docker.runs())
    assert first == second


async def test_the_run_data_is_deleted_at_the_run_end_and_the_container_retired(sandbox) -> None:  # noqa: F811
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        handle = await sandbox.broker.acquire()
        await sandbox.broker.release(handle)
        b = sb.read_run_binding()
        (b.run_data / "rows.csv").write_text("member rows", encoding="utf-8")
        assert await sb.end_sandbox_run() is True
        await sandbox.broker.settle()
        assert not b.run_data.exists()
        assert handle.stale and handle.removed
        assert "cid-1" in sandbox.docker.removals()
        await sandbox.broker.acquire()  # the next run of the thread
    assert len(sandbox.docker.runs()) == 2, "the next run reused a container on a deleted dir"
    assert b.run_data.is_dir()


async def test_a_busy_container_is_marked_stale_and_never_reused(sandbox) -> None:  # noqa: F811
    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        handle = await sandbox.broker.acquire()  # still leased
        await sb.end_sandbox_run()
        assert handle.stale and not handle.removed
        await sandbox.broker.release(handle)
        await sandbox.broker.acquire()
    assert len(sandbox.docker.runs()) == 2


async def test_a_failed_batch_run_also_deletes_its_run_data(sandbox, monkeypatch) -> None:  # noqa: F811
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import executor

    thread = new_thread()
    seen: dict[str, Path] = {}

    async def failing_inner(agent_name: str, payload: dict, **kw: Any) -> Any:
        ws = ensure_state_dir(PA, tenant_instance(ORG_A))
        token = executor._stream_relay_thread_id.set(thread)
        executor._RUN_ORG[thread] = ORG_A
        try:
            bind_artifact_context(
                session_id=thread, agent_name=PA, workspace_root=str(ws),
                instance=tenant_instance(ORG_A), member="m@example.com",
            )
            b = sb.read_run_binding()
            await sandbox.broker.ensure_thread_dirs(b)
            seen["run_data"] = b.run_data
        finally:
            executor._RUN_ORG.pop(thread, None)
            executor._stream_relay_thread_id.reset(token)
        raise RuntimeError("the run failed")

    monkeypatch.setattr(executor, "_run_agent_inner", failing_inner)
    with pytest.raises(RuntimeError):
        await executor.run_agent(PA, {}, run_id="r1", thread_id=thread)
    assert seen["run_data"] and not seen["run_data"].exists()


def _awaits_in_finally(fn: Any, name: str) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for stmt in node.finalbody:
                for sub in ast.walk(stmt):
                    if (
                        isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call)
                        and getattr(sub.value.func, "id", None) == name
                    ):
                        return True
    return False


def test_every_run_boundary_ends_its_run_data_in_a_finally() -> None:
    from orchestrator import executor

    for fn in (executor.run_agent_stream, executor.run_agent, executor._run_sub_agent_streaming):
        assert _awaits_in_finally(fn, "_end_sandbox_run"), fn.__name__


async def test_end_run_with_no_sandbox_is_free(sandbox) -> None:  # noqa: F811
    with bound_run(ORG_A, agent=PA, thread=new_thread()):
        assert await sb.end_sandbox_run() is False
    assert sandbox.broker._run_data == {}


async def test_the_run_data_never_reaches_the_blob_store(sandbox, host_trap) -> None:  # noqa: F811
    """The command writes three files. Only the kept ones are mirrored."""
    from acb_skills import sandbox_tools as st

    thread = new_thread()
    b = _binding(thread)

    async def container_writes(argv: list[str]) -> None:
        (b.run_data / "rows.csv").write_text("member rows", encoding="utf-8")
        (b.workspace / b.outputs_rel / "chart.svg").write_text("<svg/>", encoding="utf-8")
        skills = b.workspace / "agent-data" / "skills" / "chart"
        skills.mkdir(parents=True, exist_ok=True)
        (skills / "SKILL.md").write_text("---\n", encoding="utf-8")

    sandbox.docker.on_stream = container_writes
    with bound_run(ORG_A, agent=PA, thread=thread):
        out = await st.run_command("python3 /workspace/.run/chart.py")
    kept = sorted(rel for rel, _ in sandbox.mirrored)
    assert kept == ["agent-data/skills/chart/SKILL.md", f"{b.outputs_rel}/chart.svg"]
    assert all("rows.csv" not in rel for rel in kept)
    assert sandbox.cards == [f"{b.outputs_rel}/chart.svg"]
    assert "2 file(s) saved" in out


async def test_the_startup_sweep_deletes_run_data_a_crash_left(sandbox) -> None:  # noqa: F811
    from acb_skills.agent_paths import state_root

    left = state_root() / ".run-data" / "org" / "thread"
    left.mkdir(parents=True)
    (left / "rows.csv").write_text("member rows", encoding="utf-8")
    await sandbox.broker.startup()
    assert not (state_root() / ".run-data").exists()


def test_a_thread_id_that_names_no_folder_gets_no_sandbox(sandbox) -> None:  # noqa: F811
    with (
        bound_run(ORG_A, agent=PA, thread="Thread With Spaces!"),
        pytest.raises(sb.SandboxRefused, match="cannot name a sandbox folder"),
    ):
        asyncio.run(sandbox.broker.acquire())
    assert sandbox.docker.runs() == []


# ═════════════════════════ the route half (R8) ══════════════════════════════

from tests.unit.test_chat_write_under_rls import (  # noqa: E402,F401 — fixtures by name
    _ALICE,
    _BOB,
    _CAROL,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402,F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_readers_under_rls import _seed_session  # noqa: E402
from tests.unit.test_h201_tenant_workdirs import (  # noqa: E402,F401 — fixtures by name
    _S,
    _client,
    _tenant_dir,
    disk,
)


@_DB_GATE
def test_another_member_cannot_list_or_read_a_threads_output_folder(graph_as_app, disk) -> None:  # noqa: F811
    from acb_skills.agent_paths import instance_slug

    a, b = graph_as_app.org_a, graph_as_app.org_b
    s1 = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    s2 = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    sc = _seed_session(graph_as_app, b, _CAROL, None, agent=_S)
    alice, bob, carol = (_client(_user(e, o)) for e, o in ((_ALICE, a), (_BOB, a), (_CAROL, b)))
    slug1 = instance_slug(s1)
    chart = f"outputs/{slug1}/chart-{uuid.uuid4().hex[:6]}.txt"
    flat = f"outputs/report-{uuid.uuid4().hex[:6]}.md"

    # Alice's run wrote the chart into her thread's folder. Its write-through
    # gives a history row.
    put = alice.put(f"/agent/workspace/{s1}/file", params={"path": chart},
                    json={"content": "T1 CHART"})
    assert put.status_code == 200, put.text
    assert alice.put(f"/agent/workspace/{s1}/file", params={"path": flat},
                     json={"content": "S8 REPORT"}).status_code == 200

    # Alice, in her own session, lists and reads it.
    tree_a = {f["path"] for f in alice.get(f"/agent/workspace/{s1}").json()["files"]}
    assert chart in tree_a and flat in tree_a
    assert alice.get(f"/agent/workspace/{s1}/file", params={"path": chart}).text == "T1 CHART"

    # Bob, same organization, his own session: not listed, not served.
    tree_b = {f["path"] for f in bob.get(f"/agent/workspace/{s2}").json()["files"]}
    assert not any(p.startswith(f"outputs/{slug1}/") for p in tree_b)
    assert flat in tree_b, "a file outside every thread folder stays served (S8)"
    assert bob.get(f"/agent/workspace/{s2}/file", params={"path": chart}).status_code == 404
    hist = bob.get(f"/agent/workspace/{s2}/history").json()["history"]
    assert hist and not any(h["path"].startswith(f"outputs/{slug1}/") for h in hist)
    assert bob.get(f"/agent/workspace/{s2}/history", params={"path": chart}).json() == {"history": []}
    assert bob.put(f"/agent/workspace/{s2}/file", params={"path": chart},
                   json={"content": "BOB"}).status_code == 404
    assert bob.delete(f"/agent/workspace/{s2}/file", params={"path": chart}).status_code == 404
    # The room check of today: Bob is not in Alice's room.
    assert bob.get(f"/agent/workspace/{s1}/file", params={"path": chart}).status_code == 404

    # The fault-in never restores another thread's file for Bob.
    on_disk = _tenant_dir(a) / chart
    on_disk.unlink()
    assert bob.get(f"/agent/workspace/{s2}/file", params={"path": chart}).status_code == 404
    assert not on_disk.exists()
    got = alice.get(f"/agent/workspace/{s1}/file", params={"path": chart})
    assert got.status_code == 200 and got.text == "T1 CHART"

    # Another organization reaches nothing.
    assert carol.get(f"/agent/workspace/{s1}").json()["files"] == []
    assert carol.get(f"/agent/workspace/{sc}/file", params={"path": chart}).status_code == 404


@_DB_GATE
def test_a_link_in_a_workspace_is_never_served_or_written_through(graph_as_app, disk) -> None:  # noqa: F811
    """WS43-F14, §7.5 rule B, at the routes: the safe opener refuses a link."""
    a = graph_as_app.org_a
    s1 = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    alice = _client(_user(_ALICE, a))
    assert alice.get(f"/agent/workspace/{s1}").status_code == 200  # makes the tenant dir
    ws = _tenant_dir(a)
    target = ws / "outputs" / "real.txt"
    target.write_text("REAL", encoding="utf-8")
    os.symlink(target, ws / "outputs" / "alias.txt")
    assert alice.get(f"/agent/workspace/{s1}/file", params={"path": "outputs/alias.txt"}).status_code == 404
    assert alice.put(f"/agent/workspace/{s1}/file", params={"path": "outputs/alias.txt"},
                     json={"content": "THROUGH"}).status_code == 400
    assert target.read_text(encoding="utf-8") == "REAL"
    tree = {f["path"] for f in alice.get(f"/agent/workspace/{s1}").json()["files"]}
    assert "outputs/alias.txt" not in tree


# ═════════════════════════ the sandbox_docker half ══════════════════════════

from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: E402,F401
from tests.unit.test_sandbox_exec_hygiene import (  # noqa: E402,F401
    _REAL_PROCESS_IDS,
    _use_named_volumes,
    bind_mounts_work,
    docker_image,
)

DOCKER_ORG = f"eeeeeeee-0000-0000-0000-{uuid.uuid4().hex[:12]}"


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )


@pytest.fixture
async def real_projects(
    monkeypatch: pytest.MonkeyPatch, short_tmp: Path, docker_image: str,  # noqa: F811
    bind_mounts_work: bool,  # noqa: F811
) -> Any:
    env = configure_env(monkeypatch, short_tmp)
    monkeypatch.setattr(env["settings"], "sandbox_image", docker_image)
    monkeypatch.setattr(env["settings"], "maf_coding_scope", f"projects:{DOCKER_ORG}")
    monkeypatch.setattr(sb, "_process_ids", _REAL_PROCESS_IDS)
    volumes: list[str] = []
    if not bind_mounts_work:
        _use_named_volumes(monkeypatch, docker_image, volumes)
    broker = sb.SandboxBroker()
    monkeypatch.setattr(sb, "_BROKER", broker)
    env.update(broker=broker, bind=bind_mounts_work)
    try:
        yield env
    finally:
        await broker.settle()
        await broker.stop()
        left = _docker("ps", "-aq", "--filter", f"label=metorite.org={DOCKER_ORG}").stdout.split()
        if left:
            _docker("rm", "-f", *left)
        for name in volumes:
            _docker("volume", "rm", "-f", name)


@pytest.mark.sandbox_docker
async def test_docker_a_thread_sees_only_its_own_outputs_and_run_data(real_projects) -> None:
    broker = real_projects["broker"]
    t1, t2 = new_thread(), new_thread()
    with bound_run(DOCKER_ORG, agent=PA, thread=t1):
        h1 = await broker.acquire()
        b1 = sb.read_run_binding()
    wrote = await broker.exec(
        h1, "echo T1 > /workspace/outputs/t1.txt && echo rows > /workspace/.run/rows.csv", 30,
    )
    assert wrote.exit_code == 0, wrote.output
    with bound_run(DOCKER_ORG, agent=PA, thread=t2):
        h2 = await broker.acquire()
    seen = await broker.exec(h2, "ls -A /workspace/outputs /workspace/.run | tr '\\n' ' '", 30)
    assert "t1.txt" not in seen.output and "rows.csv" not in seen.output, seen.output
    read = await broker.exec(h2, "cat /workspace/outputs/t1.txt", 30)
    assert read.exit_code != 0
    # The cover cannot be moved from inside: the mountpoint is busy.
    moved = await broker.exec(h2, "mv /workspace/outputs /workspace/elsewhere", 30)
    assert moved.exit_code != 0
    if real_projects["bind"]:
        assert (b1.workspace / b1.outputs_rel / "t1.txt").read_text().strip() == "T1"
        assert (b1.run_data / "rows.csv").read_text().strip() == "rows"
        assert not (b1.workspace / "outputs" / "t1.txt").exists()
        assert not (b1.workspace / ".run" / "rows.csv").exists()


@pytest.mark.sandbox_docker
async def test_docker_the_run_data_is_gone_after_the_run(real_projects) -> None:
    broker = real_projects["broker"]
    thread = new_thread()
    with bound_run(DOCKER_ORG, agent=PA, thread=thread):
        handle = await broker.acquire()
        b = sb.read_run_binding()
        assert (await broker.exec(handle, "echo rows > /workspace/.run/rows.csv", 30)).exit_code == 0
        await broker.release(handle)
        assert await sb.end_sandbox_run() is True
        await broker.settle()
        assert not b.run_data.exists()
        fresh = await broker.acquire()
    assert fresh is not handle
    if real_projects["bind"]:
        # A named volume (Docker Desktop) outlives the host dir it stands in
        # for, so only the real bind mount of CI and production can show this.
        after = await broker.exec(fresh, "ls -A /workspace/.run | wc -l", 30)
        assert after.output.strip() == "0", "the run data of the last run is still there"
