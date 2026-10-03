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

* ``mount_list`` drops ``projects_mounts``: the mount test;
* ``end_run`` keeps the dir: the run-end test;
* the ``await _end_sandbox_run()`` taken out of the stream ``finally``: the
  hook test;
* the route rule ``_is_other_thread_path`` answers ``False``: the route test;
* the route's ``_safe_write`` writes with ``Path.write_bytes``: the route link
  test.
* the route rule reads the ``.cc-instance`` marker again: the route test;
* the ``PYTHONNOUSERSITE`` line or the read-only marker mount is dropped: the
  mount test.

Fix round 1 of PR #603 (each run red once by hand on 2026-10-03):

* the ``projects`` workspace mount is writable again, or ``PYTHONSAFEPATH``
  is dropped: the mount test and the Docker shared-code test;
* ``refused_write`` ignores another thread or another member's skill: the
  host-tools test and the route skill test;
* the PUT route skips ``_apply_write_rules``: the route skill test;
* the verifier's demo, each layer on its own: the read-only mount, the
  ``_place`` rule or ``PYTHONSAFEPATH`` taken out turns the Docker
  ``pandas.py`` test red;
* the sweep shows a file again with no content change, or sweeps
  ``agent-data/``: the two sweep tests.
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
    # Review P1 (fix round 1): every thread of one org mounts this dir, so
    # it is read-only. Only the two nested mounts below are writable.
    assert f"type=bind,source={b.workspace},target=/workspace,readonly" in mounts
    assert f"type=bind,source={b.workspace / b.outputs_rel},target=/workspace/outputs" in mounts
    assert f"type=bind,source={b.run_data},target=/workspace/.run" in mounts
    marker = b.workspace / ".cc-instance"
    assert f"type=bind,source={marker},target=/workspace/.cc-instance,readonly" in mounts
    assert marker.read_text(encoding="utf-8") == b.instance
    argv = sandbox.docker.runs()[0]
    assert "PYTHONNOUSERSITE=1" in argv, "another thread could plant a package in the user site"
    assert "PYTHONSAFEPATH=1" in argv, "python3 -c would import a module from /workspace"
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
    assert "PYTHONNOUSERSITE=1" not in sandbox.docker.runs()[0]
    assert "PYTHONSAFEPATH=1" not in sandbox.docker.runs()[0]
    ws_mount = next(m for m in mounts if m.endswith("target=/workspace") or "target=/workspace," in m)
    assert not ws_mount.endswith(",readonly"), "the code_task target keeps a writable workspace"


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
    """The command writes two files. Only its output folder is mirrored.

    ``agent-data/`` is read-only in the container now (review P1, fix round
    1), so the output folder is the one place a command can change.
    """
    from acb_skills import sandbox_tools as st

    thread = new_thread()
    b = _binding(thread)

    async def container_writes(argv: list[str]) -> None:
        (b.run_data / "rows.csv").write_text("member rows", encoding="utf-8")
        (b.workspace / b.outputs_rel / "chart.svg").write_text("<svg/>", encoding="utf-8")

    sandbox.docker.on_stream = container_writes
    with bound_run(ORG_A, agent=PA, thread=thread):
        out = await st.run_command("python3 /workspace/.run/chart.py")
    kept = sorted(rel for rel, _ in sandbox.mirrored)
    assert kept == [f"{b.outputs_rel}/chart.svg"]
    assert all("rows.csv" not in rel for rel in kept)
    assert sandbox.cards == [f"{b.outputs_rel}/chart.svg"]
    assert "1 file(s) saved" in out


async def test_a_read_only_command_shows_no_card_again(sandbox, host_trap) -> None:  # noqa: F811
    """The verifier, addition 4: the mtime slack of the sweep let an output
    from just before the command through, and it showed a second card."""
    from acb_skills import sandbox_tools as st
    from acb_skills.tenant_file_store import TenantFileStore

    thread = new_thread()
    with bound_run(ORG_A, agent=PA, thread=thread):
        b = sb.read_run_binding()
        store = TenantFileStore(
            workspace=b.workspace, outputs_rel=b.outputs_rel, run_data=b.run_data,
            guard=st._BrokerGuard(sandbox.broker, b), member="member@example.com",
        )
        await store.write("outputs/chart.svg", "<svg/>")
        await store.write("outputs/chart.svg", "<svg/>")  # the same content again
        out = await st.run_command("cat /workspace/outputs/chart.svg")
    assert sandbox.cards == [f"{b.outputs_rel}/chart.svg"], sandbox.cards
    assert [r for r, _ in sandbox.mirrored] == [f"{b.outputs_rel}/chart.svg"]
    assert "file(s) saved" not in out


async def test_a_command_never_mirrors_another_threads_agent_data(sandbox, host_trap) -> None:  # noqa: F811
    """The verifier, addition 4: T2's sweep mirrored T1's recent agent-data
    files under T2's run. The sweep covers only T2's own output folder."""
    from acb_skills import sandbox_tools as st
    from acb_skills.tenant_file_store import TenantFileStore

    t1, t2 = new_thread(), new_thread()
    with bound_run(ORG_A, agent=PA, thread=t1, member="x@example.com"):
        b1 = sb.read_run_binding()
        store = TenantFileStore(
            workspace=b1.workspace, outputs_rel=b1.outputs_rel, run_data=b1.run_data,
            guard=st._BrokerGuard(sandbox.broker, b1), member="x@example.com",
        )
        await store.write("agent-data/t1-notes.md", "T1 NOTES")
    sandbox.mirrored.clear()
    with bound_run(ORG_A, agent=PA, thread=t2, member="y@example.com"):
        await st.run_command("true")
    assert sandbox.mirrored == [], sandbox.mirrored


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


async def test_the_host_tools_never_write_another_threads_folder_or_skill(sandbox) -> None:  # noqa: F811
    """Review P1 (fix round 1): ``write_artifact``, ``save_note`` and
    ``share_artifact`` refuse another chat's output folder, another member's
    skill folder and the author marker, in a run that is not covered too."""
    import importlib

    from acb_skills.agent_paths import thread_slug
    from acb_skills.note_tools import save_note

    wa = importlib.import_module("acb_skills.write_artifact")
    mine, other = new_thread(), new_thread()
    with bound_run(ORG_A, agent=PA, thread=other, member="x@example.com") as ws:
        (ws / "outputs" / thread_slug(other)).mkdir(parents=True)
        (ws / "outputs" / thread_slug(other) / "private.txt").write_text("T2 DATA")
        skill = ws / "agent-data" / "skills" / "chart"
        skill.mkdir(parents=True)
        (skill / ".metorite-author").write_text("x@example.com")
    with bound_run(ORG_A, agent=PA, thread=mine, member="y@example.com") as ws:
        for path in (
            f"outputs/{thread_slug(other)}/planted.txt",
            "agent-data/skills/chart/scripts/evil.py",
            "agent-data/skills/new/.metorite-author",
        ):
            got = await wa.write_artifact(path, "x", overwrite=True)
            assert "refused" in got.get("error", ""), (path, got)
            assert "Refused" in await save_note(path, "x"), path
        shared = await wa.share_artifact(f"outputs/{thread_slug(other)}/private.txt")
        assert "another chat" in shared.get("error", ""), shared
        listed = await wa.share_artifact("outputs")
        assert all(thread_slug(other) not in a["path"] for a in listed.get("artifacts", []))
        own = await wa.write_artifact(f"outputs/{thread_slug(mine)}/mine.txt", "ok", overwrite=True)
        assert "error" not in own, own
    assert not (ws / "outputs" / thread_slug(other) / "planted.txt").exists()
    assert not (ws / "agent-data" / "skills" / "chart" / "scripts" / "evil.py").exists()


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

    # A container writes the dir it mounts. A rewritten partition marker must
    # not switch the rule off (the rule reads the path, never the marker).
    (_tenant_dir(a) / ".cc-instance").write_text(f"u:{_BOB}", encoding="utf-8")
    assert bob.get(f"/agent/workspace/{s2}/file", params={"path": chart}).status_code == 404
    tree_b = {f["path"] for f in bob.get(f"/agent/workspace/{s2}").json()["files"]}
    assert not any(p.startswith(f"outputs/{slug1}/") for p in tree_b)


@_DB_GATE
def test_a_member_cannot_change_another_members_skill_through_the_routes(graph_as_app, disk) -> None:  # noqa: F811
    """Review P1 (fix round 1). The first member who writes into a skill
    folder becomes its author. Another member of the org gets 403 for a PUT,
    a DELETE or a promote into it, and the author marker is reserved."""
    a = graph_as_app.org_a
    s1 = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    s2 = _seed_session(graph_as_app, a, _BOB, None, agent=_S)
    alice, bob = _client(_user(_ALICE, a)), _client(_user(_BOB, a))
    script = f"agent-data/skills/s{uuid.uuid4().hex[:6]}/scripts/plot.py"
    made = alice.put(f"/agent/workspace/{s1}/file", params={"path": script},
                     json={"content": "print(1)"})
    assert made.status_code == 200, made.text
    top = script.rsplit("/scripts/", 1)[0]
    assert (_tenant_dir(a) / top / ".metorite-author").read_text() == _ALICE.lower()
    assert bob.put(f"/agent/workspace/{s2}/file", params={"path": script},
                   json={"content": "import exfil"}).status_code == 403
    assert bob.delete(f"/agent/workspace/{s2}/file", params={"path": script}).status_code == 403
    up = bob.post(f"/agent/workspace/{s2}/upload", files={"files": ("e.py", b"import exfil")})
    assert up.status_code == 200, up.text
    assert bob.post(f"/agent/workspace/{s2}/promote", json={
        "path": up.json()[0]["path"], "dest": f"{top}/scripts/e.py",
    }).status_code == 403
    assert bob.put(f"/agent/workspace/{s2}/file", params={"path": f"{top}/.metorite-author"},
                   json={"content": _BOB}).status_code == 400
    assert (_tenant_dir(a) / script).read_text() == "print(1)"
    assert (_tenant_dir(a) / top / ".metorite-author").read_text() == _ALICE.lower()


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
    # The verifier's M7b: a RELATIVE link that never leaves the dir, as the
    # last name and as a dir on the way. Only RESOLVE_NO_SYMLINKS (or the
    # O_NOFOLLOW walk) refuses the second one.
    os.symlink("real.txt", ws / "outputs" / "rel-alias.txt")
    os.symlink(".", ws / "outputs" / "here", target_is_directory=True)
    for rel in ("outputs/rel-alias.txt", "outputs/here/real.txt"):
        assert alice.get(f"/agent/workspace/{s1}/file", params={"path": rel}).status_code == 404, rel
        assert alice.put(f"/agent/workspace/{s1}/file", params={"path": rel},
                         json={"content": "THROUGH"}).status_code == 400, rel
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
    bind_mounts_work,
    docker_image,
)

DOCKER_ORG = f"eeeeeeee-0000-0000-0000-{uuid.uuid4().hex[:12]}"


def _use_seeded_volumes(monkeypatch: pytest.MonkeyPatch, image: str, made: list[str]) -> None:
    """Docker Desktop only: a named volume per mount source, seeded from the host.

    Docker Desktop refuses a host bind mount (spec §5.2). The ``projects``
    workspace is read-only, so its stand-in volume must already hold the
    mountpoints of the nested mounts, as the host dir does in production. So
    each volume starts as a copy of its host dir (``docker cp``), owned by
    the test uid. A file source (the read-only marker cover) is left out,
    because the read-only workspace volume already holds that file. CI runs
    every test on real bind mounts.
    """
    names: dict[str, str] = {}

    def args(self: sb.Mount) -> list[str]:
        if self.source.is_file():
            return []
        key = str(self.source)
        if key not in names:
            name = f"mtr-test-{uuid.uuid4().hex[:10]}"
            assert _docker("volume", "create", name).returncode == 0
            helper = _docker(
                "create", "--user", "0:0", "--mount", f"type=volume,source={name},target=/v",
                image, "chown", "-R", "1000:1000", "/v",
            ).stdout.strip()
            assert helper, "the helper container did not start"
            copied = _docker("cp", f"{self.source}{os.sep}.", f"{helper}:/v")
            assert copied.returncode == 0, copied.stderr
            started = _docker("start", "-a", helper)
            assert started.returncode == 0, started.stderr
            _docker("rm", "-f", helper)
            names[key] = name
            made.append(name)
        spec = f"type=volume,source={names[key]},target={self.target}"
        return ["--mount", spec + (",readonly" if self.readonly else "")]

    monkeypatch.setattr(sb.Mount, "args", args)


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
        _use_seeded_volumes(monkeypatch, docker_image, volumes)
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
    # The partition marker that the gateway reads is read-only in the container.
    marked = await broker.exec(h2, "echo u:evil > /workspace/.cc-instance", 30)
    assert marked.exit_code != 0
    # No user site, so a package planted in another thread's `.local` never imports.
    site = await broker.exec(h2, "python3 -c 'import site; print(site.ENABLE_USER_SITE)'", 30)
    assert site.output.strip() == "False", site.output
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


@pytest.mark.sandbox_docker
async def test_docker_run_command_runs_in_the_container(real_projects, monkeypatch) -> None:
    """``run_command`` itself, on the real broker: the command runs in the
    container (its host name is the container id), and its output file lands
    in the thread's own folder."""
    from acb_skills import sandbox_tools as st

    broker = real_projects["broker"]
    assert await broker.probe_docker() is True
    # The D85 seam (PR #598) is not on this branch yet. See _sandbox_tools_fakes.
    monkeypatch.setattr(sb, "_host_shell_withheld", lambda agent: True)
    thread = new_thread()
    with bound_run(DOCKER_ORG, agent=PA, thread=thread):
        assert sb.covers(PA, DOCKER_ORG) is True
        out = await st.run_command(
            "echo host=$(hostname) && id -u && echo chart > /workspace/outputs/chart.txt", 30,
        )
        b = sb.read_run_binding()
    handle = broker._live[b.name]
    # Docker names the container's host after its id, so the command ran there.
    assert "exit 0" in out and f"host={handle.container_id[:12]}" in out, out
    if real_projects["bind"]:
        assert (b.workspace / b.outputs_rel / "chart.txt").read_text().strip() == "chart"
        assert str(os.getuid()) in out


@pytest.mark.sandbox_docker
async def test_docker_a_thread_never_imports_or_writes_shared_code(real_projects) -> None:
    """Review P1 (fix round 1): every thread of one org mounts the same dir.

    Member X's run left a module at the root, in ``agent-data/`` and in
    ``inputs/``, and a script. Member Y's thread imports none of them by name,
    and can write only its own output folder and its run data.
    """
    broker = real_projects["broker"]
    ty = new_thread()
    payload = "print('PLANTED-RAN')\n"
    with bound_run(DOCKER_ORG, agent=PA, thread=ty, member="y@example.com") as ws:
        # What member X's run left in the shared dir, before Y's thread starts.
        for rel in ("evil.py", "agent-data/evil.py", "inputs/evil.py", "agent-data/scripts/run.py"):
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        hy = await broker.acquire()
    seen = await broker.exec(hy, "ls /workspace/agent-data/evil.py /workspace/evil.py", 30)
    assert seen.exit_code == 0, f"the planted files are not in the workspace: {seen.output}"
    for cwd in ("/workspace", "/workspace/agent-data", "/workspace/inputs"):
        got = await broker.exec(hy, f"cd {cwd} && python3 -c 'import evil'", 30)
        assert "PLANTED-RAN" not in got.output and got.exit_code != 0, (cwd, got.output)
    script = await broker.exec(
        hy, "printf 'import evil\\n' > /workspace/.run/y.py && cd /workspace && python3 /workspace/.run/y.py", 30,
    )
    assert "PLANTED-RAN" not in script.output and script.exit_code != 0, script.output
    for path in ("/workspace/new.py", "/workspace/agent-data/x", "/workspace/inputs/x",
                 "/workspace/agent-data/scripts/run.py"):
        wrote = await broker.exec(hy, f"echo x > {path}", 30)
        assert wrote.exit_code != 0, f"the thread wrote {path}"
    for path in ("/workspace/outputs/ok", "/workspace/.run/ok"):
        assert (await broker.exec(hy, f"echo x > {path}", 30)).exit_code == 0, path


#: The verifier's live demo (fix round 1): a root ``pandas.py`` that copies
#: the run data of whoever imports it into a shared folder.
_EXFIL_PANDAS = (
    "import os, shutil\n"
    "os.makedirs('/workspace/agent-data/leak', exist_ok=True)\n"
    "shutil.copy('/workspace/.run/rows.csv', '/workspace/agent-data/leak/rows.csv')\n"
    "print('EXFIL-RAN')\n"
)


@pytest.mark.sandbox_docker
async def test_docker_a_planted_root_pandas_never_leaks_another_threads_rows(
    real_projects, monkeypatch,
) -> None:
    """EXACTLY the verifier's demo, with each of the three layers checked on
    its own, so the fence goes red if any one of them goes:

    1. ``_place``: T1's ``file_access_write`` of a root ``pandas.py`` is refused.
    2. ``PYTHONSAFEPATH``: with the file there anyway, T2's plain
       ``python3 -c "import pandas"`` imports the real pandas, not the file.
    3. The read-only mount: run by its full path, the file still cannot write
       ``agent-data/leak/``, so T2's rows never reach a shared folder, the
       blob store or T1.
    """
    from acb_skills import sandbox_tools as st
    from acb_skills.tenant_file_store import TenantFileStore

    broker = real_projects["broker"]
    assert await broker.probe_docker() is True
    # The D85 seam (PR #598) is not on this branch yet. See _sandbox_tools_fakes.
    monkeypatch.setattr(sb, "_host_shell_withheld", lambda agent: True)
    mirrored: list[str] = []

    async def mirror(rel: str, data: bytes, **_kw: Any) -> None:
        mirrored.append(rel)

    import importlib

    monkeypatch.setattr(importlib.import_module("acb_skills.write_artifact"), "mirror_to_blob_store", mirror)
    t1, t2 = new_thread(), new_thread()
    # 1. T1, member X, tries to plant pandas.py with the file tools.
    with bound_run(DOCKER_ORG, agent=PA, thread=t1, member="x@example.com"):
        b1 = sb.read_run_binding()
        x_store = TenantFileStore(
            workspace=b1.workspace, outputs_rel=b1.outputs_rel, run_data=b1.run_data,
            guard=st._BrokerGuard(broker, b1), member="x@example.com",
        )
        with pytest.raises(ValueError):
            await x_store.write("pandas.py", _EXFIL_PANDAS)
    assert not (b1.workspace / "pandas.py").exists(), "_place let a root module through"
    # The file is planted anyway, past the store, so layers 2 and 3 are tested
    # on their own.
    (b1.workspace / "pandas.py").write_text(_EXFIL_PANDAS, encoding="utf-8")
    # T2, member Y, has member rows in its run data.
    with bound_run(DOCKER_ORG, agent=PA, thread=t2, member="y@example.com"):
        b2 = sb.read_run_binding()
        y_store = TenantFileStore(
            workspace=b2.workspace, outputs_rel=b2.outputs_rel, run_data=b2.run_data,
            guard=st._BrokerGuard(broker, b2), member="y@example.com",
        )
        await y_store.write(".run/rows.csv", "name,salary\nY,100\n")
        # 2. A plain import, from the cwd /workspace.
        imported = await st.run_command(
            "cd /workspace && python3 -c 'import pandas; print(pandas.__version__)'", 60,
        )
        # 3. The file run by its full path still has nowhere to write.
        ran = await st.run_command("cd /workspace && python3 /workspace/pandas.py", 60)
        leak = await st.run_command("ls -A /workspace/agent-data/leak 2>/dev/null | wc -l", 30)
    assert "EXFIL-RAN" not in imported and "exit 0" in imported, imported
    assert "EXFIL-RAN" not in ran and "exit 0" not in ran.split("\n", 1)[0], ran
    assert leak.splitlines()[1].strip() == "0", leak
    assert not (b2.workspace / "agent-data" / "leak").exists()
    assert not any("leak" in rel or "rows.csv" in rel for rel in mirrored), mirrored
    with bound_run(DOCKER_ORG, agent=PA, thread=t1, member="x@example.com"):
        assert await x_store.read("agent-data/leak/rows.csv") is None
