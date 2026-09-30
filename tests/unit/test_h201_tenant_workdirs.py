"""H-201 part 3 — a shared agent works in a tenant dir, never in its clone
(``projects_ai_chat.md`` §21.15).

A SHARED agent (``sharing.instancing`` absent or ``shared``) ran every
tenant in ONE clone, ``repos/<agent>``. Its run output, its inputs and its
``agent-data/`` were one folder for every org, and the session routes showed
that folder to any member who opened a session of the agent.

Now the executor gives each run of a shared agent the folder
``state/<agent>/<slug of o:<organization_id>>``. The tenant comes from the
run binding (``_RUN_ORG`` or ``current_tenant``), never from input. The blob
rows of that folder carry ``instance='o:<org>'``. A run with no tenant is
refused. The gateway's step 2 derives the same folder from
``user.organization_id``, and the ``repos/`` read arm is gone.

The R8 tests run on the H3 rehearsal's phase-4 catalog, as its NOSUPERUSER
NOBYPASSRLS role.

Mutations this suite catches (R7). The spec's table carries the counts.

* the executor falls back to the clone when a run has no tenant;
* the executor keys the tenant dir by nothing (one folder for every org);
* the store key of a tenant dir back to ``''``;
* ``RunWorkspaceRefused`` handled by the generic ``except`` (self-anneal);
* the rehydrate drops the older ``''`` rows, or lets them win;
* the ``repos/`` read arm of ``_allowed_workspace`` put back;
* the tenant arm of ``_allowed_workspace`` takes any tenant;
* step 2 derives the clone for a shared agent;
* the fault-in drops its ``''`` fallback, its clone guard or its tenant check;
* a delete leaves the older ``''`` row;
* ``_git_dir_for`` gives the tenant dir to the git helpers;
* the Projects dispatch drops the tenant;
* ``/agent/run`` or ``/agent/run/async`` drops the caller's tenant;
* the live-run guard removed from ``/agent/run`` and ``/agent/run/async``;
* ``_claim_run_org`` overwrites a live entry of another org;
* a run pops an entry it did not set (the batch or the stream path).

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_h201_tenant_workdirs.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import types
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _CAROL,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_readers_under_rls import _seed_session

_S, _P = "agent-h201ws", "agent-h201wp"
_MIXED = "MIXED CLONE OUTPUT"


def _cfg(instancing: str | None) -> dict:
    return {"name": "x", "sharing": {"instancing": instancing}} if instancing else {"name": "x"}


class _Disk:
    """A scratch clone root: one shared agent, one personal agent."""

    def __init__(self, base: Path) -> None:
        self.repos = base / "agents" / "repos"
        self.shared = self.repos / _S
        self.personal = self.repos / _P
        for d, cfg in ((self.shared, _cfg(None)), (self.personal, _cfg("personal"))):
            d.mkdir(parents=True)
            (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        # Files left in the clone by the old rule. Their tenant is unknown.
        (self.shared / "outputs").mkdir()
        (self.shared / "outputs" / "old-doc.md").write_text(_MIXED, encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {p.relative_to(self.repos).as_posix(): p.read_bytes()
                for p in sorted(self.repos.rglob("*")) if p.is_file()}


@pytest.fixture
def disk(tmp_path, monkeypatch):
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "custom_apps"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [{"name": n} for n in (_S, _P)])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    return _Disk(tmp_path)


def _client(user):
    """The real workspace router. Nothing is stubbed: the write-through and
    the fault-in reach the R8 database."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import workspace

    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _tenant_dir(org: str) -> Path:
    from acb_skills.agent_paths import agent_state_dir, tenant_instance

    return agent_state_dir(_S, tenant_instance(org))


def _run_write(disk: _Disk, org: str, sid: str, rel: str, body: str) -> tuple[dict, str, str]:
    """One run of the shared agent that calls ``write_artifact``, set up the
    way the executor sets it up: the workspace from ``_resolve_run_workspace``
    and the run's tenant bound."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT, write_artifact
    from orchestrator.executor import _resolve_run_workspace

    ws, key = _resolve_run_workspace(disk.shared, _cfg(None), organization_id=org)

    async def _go() -> dict:
        token = bind_tenant(org)
        saved = dict(_WRITE_ARTIFACT_CONTEXT)
        _WRITE_ARTIFACT_CONTEXT.update(
            session_id=sid, agent_name=_S, run_id=f"r-{uuid.uuid4().hex[:6]}",
            workspace_root=ws, instance=key,
            gateway_url="http://127.0.0.1:9", gateway_token="x",
        )
        try:
            out = await write_artifact(rel, body, overwrite=True)
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*rest, return_exceptions=True)
            return out
        finally:
            _WRITE_ARTIFACT_CONTEXT.clear()
            _WRITE_ARTIFACT_CONTEXT.update(saved)
            release_tenant(token)

    return asyncio.run(_go()), ws, key


def _put(org: str, instance: str, path: str, body: str, agent: str = _S) -> None:
    from acb_memory import put_file

    meta = asyncio.run(put_file(
        agent, path, body.encode("utf-8"), mime_type="text/markdown",
        action="create", run_id=None, session_id=None, actor="test",
        instance=instance, organization_id=org))
    assert meta is not None, (org, instance, path)


def _rows(promoted, path: str, agent: str = _S) -> list[tuple[str, str, str]]:  # noqa: F811
    with promoted.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text AS o, instance, "
            "convert_from(content, 'UTF8') AS body FROM agent_blob "
            "WHERE agent_name = :a AND path = :p ORDER BY organization_id, instance"),
            {"a": agent, "p": path}).fetchall()
    return [(r.o, r.instance, r.body) for r in rows]


# ── The executor — no database ──────────────────────────────────────────────

def test_a_run_with_no_tenant_has_no_working_dir(disk) -> None:
    from acb_skills.agent_paths import state_root
    from orchestrator.executor import (
        RunWorkspaceRefused,
        _rehydrate_target,
        _resolve_run_workspace,
    )

    before = disk.snapshot()
    for org in (None, ""):
        with pytest.raises(RunWorkspaceRefused):
            _resolve_run_workspace(disk.shared, _cfg(None), organization_id=org)
        assert _rehydrate_target(disk.shared, "", org) is None
    assert not state_root().exists()
    assert disk.snapshot() == before


def test_two_tenants_get_two_dirs_and_the_code_stays_in_the_clone(disk) -> None:
    from acb_skills.agent_paths import state_root
    from orchestrator.executor import _git_dir_for, _resolve_run_workspace

    ws_a, key_a = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-a")
    ws_b, key_b = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-b")
    assert (key_a, key_b) == ("o:org-a", "o:org-b")
    assert ws_a != ws_b
    for ws, key in ((ws_a, key_a), (ws_b, key_b)):
        p = Path(ws)
        assert p.parent == state_root() / _S
        assert not p.resolve().is_relative_to(disk.repos.resolve())
        assert (p / ".cc-instance").read_text(encoding="utf-8") == key
        # The git helpers, the self-anneal and the self-mutation keep the
        # clone: a tenant dir holds no code.
        assert _git_dir_for(disk.shared, ws, key) == str(disk.shared)


def test_a_personal_agent_is_unchanged(disk) -> None:
    from acb_skills.agent_paths import agent_state_dir
    from orchestrator.executor import _git_dir_for, _rehydrate_target, _resolve_run_workspace

    key = f"u:{_ALICE}"
    ws, store = _resolve_run_workspace(
        disk.personal, _cfg("personal"), instance=key, organization_id="org-a")
    assert (Path(ws), store) == (agent_state_dir(_P, key), key)
    assert _git_dir_for(disk.personal, ws, store) == ws
    assert _rehydrate_target(disk.personal, key, "org-a") == (ws, key, None)


def test_a_planted_link_at_the_state_root_makes_no_tenant_dir(disk) -> None:
    import os

    from acb_skills.agent_paths import state_root
    from gateway.routes.workspace import _tenant_agent_workspace
    from orchestrator.executor import RunWorkspaceRefused, _resolve_run_workspace

    state_root().mkdir(parents=True)
    link = state_root() / _S
    try:
        os.symlink(disk.shared, link, target_is_directory=True)
    except OSError:
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(disk.shared), str(link))
    before = disk.snapshot()
    with pytest.raises(RunWorkspaceRefused):
        _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-a")
    assert _tenant_agent_workspace(_S, "org-a") is None
    assert disk.snapshot() == before


@contextmanager
def _fake_load(disk: _Disk):
    """``load_agent`` for the shared agent, with no agents to run."""
    @contextmanager
    def _load(_name, **_k):
        yield types.SimpleNamespace(
            agent_dir=disk.shared, config=_cfg(None), build_agents=lambda: [])

    yield _load


def test_a_batch_run_with_no_tenant_is_refused_without_a_repair(disk, monkeypatch) -> None:
    from acb_skills.agent_paths import state_root
    from orchestrator import executor

    calls: list[str] = []

    async def _no_anneal(**_k):
        calls.append("anneal")

    async def _no_mutation(**_k):
        calls.append("mutation")

    monkeypatch.setattr(executor, "_self_anneal", _no_anneal)
    import orchestrator.mutation as mutation

    monkeypatch.setattr(mutation, "attempt_self_mutation", _no_mutation)
    before = disk.snapshot()
    with _fake_load(disk) as load:
        monkeypatch.setattr(executor, "load_agent", load)
        with pytest.raises(executor.AgentRunError) as err:
            asyncio.run(executor.run_agent(_S, {"message": "hi"}, run_id="r-none"))
    assert "no tenant" in str(err.value)
    assert calls == []
    assert not state_root().exists()
    assert disk.snapshot() == before


def test_a_stream_run_with_no_tenant_ends_in_a_run_error(disk, monkeypatch) -> None:
    from acb_skills.agent_paths import state_root
    from orchestrator import executor

    async def _collect() -> list[dict]:
        out = []
        async for line in executor.run_agent_stream(
            _S, {"message": "hi"}, run_id="r-s", thread_id=f"t-{uuid.uuid4().hex[:6]}",
        ):
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        return out

    before = disk.snapshot()
    with _fake_load(disk) as load:
        monkeypatch.setattr(executor, "load_agent", load)
        events = asyncio.run(_collect())
    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert errors and errors[0]["code"] == "RunWorkspaceRefused", events
    assert not state_root().exists()
    assert disk.snapshot() == before


@pytest.fixture
def wiring(disk, monkeypatch):
    """The executor's run paths with the model call and the store stubbed, so
    the test reads what a run of the shared agent was given."""
    import acb_memory
    from orchestrator import executor

    seen: dict = {"rehydrate": [], "git": []}

    async def _rehydrate(agent, root, **kw):
        seen["rehydrate"].append((agent, root, kw))
        return 0

    async def _git(agent_dir, *_a, **_k):
        seen["git"].append(str(agent_dir))
        return ""

    async def _detect(_agent, agent_dir, *_a, **_k):
        seen["git"].append(str(agent_dir))

    async def _run_maf(*_a, **_k):
        from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT

        seen["context"] = dict(_WRITE_ARTIFACT_CONTEXT)
        seen.setdefault("run_org", []).append(dict(executor._RUN_ORG))
        return {"answer": "ok"}

    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _rehydrate)
    monkeypatch.setattr(executor, "_install_push_guard", _git)
    monkeypatch.setattr(executor, "_get_current_head", _git)
    monkeypatch.setattr(executor, "_detect_agent_commits", _detect)
    monkeypatch.setattr(executor, "_run_with_maf_agent", _run_maf)
    with _fake_load(disk) as load:
        monkeypatch.setattr(executor, "load_agent", load)
        yield seen


def _expect_tenant_run(seen: dict, disk: _Disk, org: str, *, git: bool = True) -> None:
    tenant = str(_tenant_dir(org))
    assert seen["context"]["workspace_root"] == tenant
    assert seen["context"]["instance"] == f"o:{org}"
    assert seen["rehydrate"] == [(_S, tenant, {
        "instance": f"o:{org}", "organization_id": org, "legacy_instance": ""})]
    if git:
        # The git helpers keep the clone: a tenant dir holds no code.
        assert seen["git"] and set(seen["git"]) == {str(disk.shared)}
    for d in ("inputs", "outputs", "agent-data"):
        assert (Path(tenant) / d).is_dir()
        assert not (disk.shared / d).exists() or d == "outputs"


def test_a_batch_run_is_given_its_tenant_dir(disk, wiring) -> None:
    from orchestrator import executor

    before = disk.snapshot()
    out = asyncio.run(executor.run_agent(
        _S, {"message": "hi"}, run_id="r-w", organization_id="org-w"))
    assert out == {"answer": "ok"}
    _expect_tenant_run(wiring, disk, "org-w")
    assert disk.snapshot() == before


def test_a_stream_run_is_given_its_tenant_dir(disk, wiring) -> None:
    """The stream path resolves the workspace, sets the context and restores
    the store BEFORE it runs an agent. With no agent the run then ends."""
    from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT
    from orchestrator import executor

    async def _drain() -> None:
        async for _line in executor.run_agent_stream(
            _S, {"message": "hi"}, run_id="r-ws",
            thread_id=f"t-{uuid.uuid4().hex[:6]}", organization_id="org-s",
        ):
            pass

    before = disk.snapshot()
    asyncio.run(_drain())
    wiring["context"] = dict(_WRITE_ARTIFACT_CONTEXT)
    # The stream path runs the git helpers only once an agent runs.
    _expect_tenant_run(wiring, disk, "org-s", git=False)
    assert disk.snapshot() == before


def _run_route(route: str, req, user) -> dict | None:
    """Call ``/agent/run`` (sync) or ``/agent/run/async`` the way FastAPI does."""
    from fastapi import BackgroundTasks
    from gateway.routes import agent as agent_routes

    async def _go():
        if route == "sync":
            return (await agent_routes.run_agent_sync(req, user=user)).model_dump()
        tasks = BackgroundTasks()
        out = await agent_routes.run_agent_async(req, tasks, user=user)
        await tasks()
        return out

    return asyncio.run(_go())


@pytest.fixture
def run_routes(monkeypatch):
    """The two run routes with the session checks stubbed and no live relay."""
    import orchestrator.stream_relay as relay
    from gateway.routes import agent as agent_routes

    async def _allowed(*_a, **_k):
        return None

    async def _inactive(*_a, **_k):
        return False

    monkeypatch.setattr(agent_routes, "_resolve_agent_for_run", lambda *_a, **_k: _S)
    monkeypatch.setattr(agent_routes, "assert_can_run_agent_in_session", _allowed)
    monkeypatch.setattr(relay, "is_active", _inactive)
    return agent_routes


def _carol():
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=_CAROL, role=UserRole.EMPLOYEE, organization_id="org-b")


@pytest.mark.parametrize("route", ["sync", "async"])
def test_a_run_route_takes_the_callers_tenant_not_the_threads(
    disk, wiring, run_routes, route,
) -> None:
    """The reviewer's P2-a. ``/agent/run`` and ``/agent/run/async`` passed no
    tenant, so the executor fell to ``_current_run_org()``. That reads
    ``_RUN_ORG[thread_id]`` first, and the thread id is client input. On a
    free thread, a member of org B works in B's dir, and the run's entry goes
    when the run ends."""
    from orchestrator import executor

    tid = f"free-{uuid.uuid4().hex[:6]}"
    req = run_routes.AgentRunRequest(agent=_S, payload={"message": "hi"}, thread_id=tid)
    _run_route(route, req, _carol())
    _expect_tenant_run(wiring, disk, "org-b")
    assert [snap.get(tid) for snap in wiring["run_org"]] == ["org-b"]
    assert tid not in executor._RUN_ORG
    assert not _tenant_dir("org-a").exists()


@pytest.mark.parametrize("route", ["sync", "async"])
def test_a_run_on_a_live_thread_of_another_org_leaves_its_entry(
    disk, wiring, run_routes, route,
) -> None:
    """The verifier's blocker. A run of org A is live on a thread id, and a
    member of org B posts that id. B is refused, and A's ``_RUN_ORG`` entry is
    still ``org-a`` during B's call and after it. Nothing runs in either dir."""
    from orchestrator import executor

    tid = f"org-a-live-{uuid.uuid4().hex[:6]}"
    executor._RUN_ORG[tid] = "org-a"
    req = run_routes.AgentRunRequest(agent=_S, payload={"message": "hi"}, thread_id=tid)
    try:
        out = _run_route(route, req, _carol())
        after = executor._RUN_ORG.get(tid)
    finally:
        executor._RUN_ORG.pop(tid, None)
    # During B's call: had B run, the agent would have seen the entry.
    assert all(snap.get(tid) == "org-a" for snap in wiring.get("run_org", []))
    assert "run_org" not in wiring, "org B's run went ahead on org A's thread"
    assert after == "org-a"
    if route == "sync":
        assert out["status"] == "failed" and "another organization" in out["error"]
    assert wiring["rehydrate"] == []
    assert not _tenant_dir("org-a").exists() and not _tenant_dir("org-b").exists()


def test_a_run_pops_only_an_entry_it_set(disk, wiring, run_routes) -> None:
    """A run of org B finds a live entry of org B that another run set. It
    runs, and at its end it leaves that entry for the run that set it."""
    from orchestrator import executor

    tid = f"org-b-live-{uuid.uuid4().hex[:6]}"
    executor._RUN_ORG[tid] = "org-b"
    req = run_routes.AgentRunRequest(agent=_S, payload={"message": "hi"}, thread_id=tid)
    try:
        _run_route("sync", req, _carol())
        after = executor._RUN_ORG.get(tid)
    finally:
        executor._RUN_ORG.pop(tid, None)
    _expect_tenant_run(wiring, disk, "org-b")
    assert after == "org-b"


def test_a_stream_run_pops_only_an_entry_it_set(disk, wiring) -> None:
    """The stream twin of the test above. A stream run of org A finds a live
    entry of org A that another run set. At its end it leaves that entry."""
    from orchestrator import executor

    tid = f"org-a-live-s-{uuid.uuid4().hex[:6]}"
    executor._RUN_ORG[tid] = "org-a"

    async def _drain() -> None:
        async for _ in executor.run_agent_stream(
            _S, {"message": "hi"}, run_id="r2s", thread_id=tid, organization_id="org-a",
        ):
            pass

    try:
        asyncio.run(_drain())
        after = executor._RUN_ORG.get(tid)
    finally:
        executor._RUN_ORG.pop(tid, None)
    assert after == "org-a"


@pytest.mark.parametrize("route", ["sync", "async"])
def test_a_run_route_refuses_another_persons_live_run(run_routes, monkeypatch, route) -> None:
    """The stream route's live-run guard, now on both batch routes: 409, and
    the same detail."""
    import orchestrator.stream_relay as relay
    from fastapi import HTTPException

    async def _active(*_a, **_k):
        return True

    async def _owner(*_a, **_k):
        return _ALICE

    monkeypatch.setattr(relay, "is_active", _active)
    monkeypatch.setattr(relay, "get_run_actor", _owner)
    req = run_routes.AgentRunRequest(
        agent=_S, payload={"message": "hi"}, thread_id=f"t-{uuid.uuid4().hex[:6]}")
    with pytest.raises(HTTPException) as err:
        _run_route(route, req, _carol())
    assert err.value.status_code == 409
    assert err.value.detail["error"] == "run_in_progress"
    assert err.value.detail["holder"] == _ALICE


def test_the_projects_dispatch_passes_the_tenant(monkeypatch) -> None:
    """The dispatch of a task to an agent runs from an event sink, with no
    tenant bound. Without the event's own tenant the run is refused."""
    import orchestrator.executor as executor
    from gateway.routes.projects import agent_dispatch

    seen: dict = {}

    async def _run(agent, message, **kwargs):
        seen.update(kwargs, agent=agent)
        return {"answer": "ok"}

    async def _record(*_a, **_k):
        return None

    monkeypatch.setattr(executor, "run_agent", _run)
    monkeypatch.setattr(agent_dispatch, "_record_outcome", _record)
    asyncio.run(agent_dispatch._run_and_record(_S, "do it", "task-1", "org-z"))
    assert seen.get("organization_id") == "org-z"


# ── The run side on the R8 database ─────────────────────────────────────────

@_DB_GATE
def test_two_orgs_write_to_separate_dirs_and_separate_rows(graph_as_app, disk) -> None:  # noqa: F811
    a, b = graph_as_app.org_a, graph_as_app.org_b
    rel = f"outputs/report-{uuid.uuid4().hex[:6]}.md"
    before = disk.snapshot()
    res_a, ws_a, _key_a = _run_write(disk, a, "sid-a", rel, "FROM A")
    res_b, ws_b, _key_b = _run_write(disk, b, "sid-b", rel, "FROM B")

    assert res_a["path"] == res_b["path"] == rel
    assert (Path(ws_a) / rel).read_text(encoding="utf-8") == "FROM A"
    assert (Path(ws_b) / rel).read_text(encoding="utf-8") == "FROM B"
    assert disk.snapshot() == before
    # One row per tenant. With the old key ('' for both) the second write hit
    # the first row's primary key, and RLS refused it.
    assert _rows(graph_as_app, rel) == sorted([
        (a, f"o:{a}", "FROM A"), (b, f"o:{b}", "FROM B")])


@_DB_GATE
def test_the_rehydrate_restores_only_the_runs_tenant(graph_as_app, disk) -> None:  # noqa: F811
    from acb_memory import rehydrate_workspace
    from orchestrator.executor import _rehydrate_target

    a, b = graph_as_app.org_a, graph_as_app.org_b
    tag = uuid.uuid4().hex[:6]
    new, old, notes, other = (f"outputs/a-{tag}.md", f"outputs/a-{tag}.md",
                              f"agent-data/NOTES-{tag}.md", f"outputs/b-{tag}.md")
    _put(a, f"o:{a}", new, "A NEW")
    _put(a, "", old, "A OLD")          # an older row at the same path
    _put(a, "", notes, "A NOTES")      # an older row the tenant key lacks
    _put(b, f"o:{b}", other, "B OUT")
    _put(b, "", f"agent-data/B-{tag}.md", "B NOTES")

    before = disk.snapshot()
    for org in (a, b):
        target = _rehydrate_target(disk.shared, "", org)
        assert target is not None
        asyncio.run(rehydrate_workspace(
            _S, target[0], instance=target[1], organization_id=org,
            legacy_instance=target[2]))

    ta, tb = _tenant_dir(a), _tenant_dir(b)
    assert (ta / new).read_text(encoding="utf-8") == "A NEW"
    assert (ta / notes).read_text(encoding="utf-8") == "A NOTES"
    assert not (ta / other).exists() and not (ta / f"agent-data/B-{tag}.md").exists()
    assert (tb / other).read_text(encoding="utf-8") == "B OUT"
    assert (tb / f"agent-data/B-{tag}.md").read_text(encoding="utf-8") == "B NOTES"
    assert not (tb / new).exists() and not (tb / notes).exists()
    assert disk.snapshot() == before


# ── The session routes on the R8 database ───────────────────────────────────

@_DB_GATE
def test_each_org_reads_only_its_own_output(graph_as_app, disk) -> None:  # noqa: F811
    a, b = graph_as_app.org_a, graph_as_app.org_b
    rel = f"outputs/r-{uuid.uuid4().hex[:6]}.md"
    _run_write(disk, a, "sid-a", rel, "ORG A OUTPUT")
    _run_write(disk, b, "sid-b", rel, "ORG B OUTPUT")
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sb = _seed_session(graph_as_app, b, _CAROL, None, agent=_S)
    alice, carol = _client(_user(_ALICE, a)), _client(_user(_CAROL, b))

    tree_a = alice.get(f"/agent/workspace/{sa}")
    assert tree_a.status_code == 200 and tree_a.json()["root"] == str(_tenant_dir(a))
    assert rel in {f["path"] for f in tree_a.json()["files"]}
    assert "outputs/old-doc.md" not in {f["path"] for f in tree_a.json()["files"]}
    assert alice.get(f"/agent/workspace/{sa}/file", params={"path": rel}).text == "ORG A OUTPUT"
    assert carol.get(f"/agent/workspace/{sb}/file", params={"path": rel}).text == "ORG B OUTPUT"

    # Carol cannot reach org A's output through any route.
    assert carol.get(f"/agent/workspace/{sa}").json()["files"] == []
    assert carol.get(f"/agent/workspace/{sa}/file", params={"path": rel}).status_code == 404
    assert carol.get(f"/agent/workspace/{sa}/history").json() == {"history": []}
    hist_b = carol.get(f"/agent/workspace/{sb}/history", params={"path": rel}).json()["history"]
    assert hist_b and all(h["session_id"] != "sid-a" for h in hist_b)
    assert carol.get("/agent/artifacts", params={"agent": _S}).json()["artifacts"] == []
    # A row of org B that stores org A's tenant dir, or the clone, reads as
    # absent. Step 2 then gives Carol her own folder.
    for stored in (_tenant_dir(a), disk.shared):
        sid = _seed_session(graph_as_app, b, _CAROL, str(stored), agent=_S)
        got = carol.get(f"/agent/workspace/{sid}/file", params={"path": rel})
        assert got.text == "ORG B OUTPUT", stored
        old = carol.get(f"/agent/workspace/{sid}/file", params={"path": "outputs/old-doc.md"})
        assert old.status_code == 404 and _MIXED not in old.text


@_DB_GATE
def test_the_upload_to_a_shared_agent_works_again(graph_as_app, disk) -> None:  # noqa: F811
    a, b = graph_as_app.org_a, graph_as_app.org_b
    name = f"spec-{uuid.uuid4().hex[:6]}.txt"
    before = disk.snapshot()
    for email, org, body in ((_ALICE, a, b"alice spec"), (_CAROL, b, b"carol spec")):
        sid = _seed_session(graph_as_app, org, email, None, agent=_S)
        c = _client(_user(email, org))
        up = c.post(f"/agent/workspace/{sid}/upload", files={"files": (name, body)})
        assert up.status_code == 200, up.text
        assert up.json()[0]["path"] == f"inputs/{name}"
        assert (_tenant_dir(org) / "inputs" / name).read_bytes() == body
        # The write-through lands in the caller's tenant, under o:<org>.
        assert (org, f"o:{org}", body.decode()) in _rows(graph_as_app, f"inputs/{name}")
        # The other change routes work in the tenant dir too.
        base = f"/agent/workspace/{sid}"
        assert c.put(f"{base}/file", params={"path": "agent-data/NOTES.md"},
                     json={"content": email}).status_code == 200
        assert c.post(f"{base}/promote", json={"path": f"inputs/{name}"}).status_code == 200
    assert disk.snapshot() == before
    assert _rows(graph_as_app, f"agent-data/{name}") == sorted([
        (a, f"o:{a}", "alice spec"), (b, f"o:{b}", "carol spec")])
    assert (_tenant_dir(a) / "agent-data" / "NOTES.md").read_text(encoding="utf-8") == _ALICE


@_DB_GATE
def test_a_personal_agent_session_is_unchanged(graph_as_app, disk) -> None:  # noqa: F811
    from acb_skills.agent_paths import agent_state_dir

    a = graph_as_app.org_a
    sid = _seed_session(graph_as_app, a, _ALICE, None, agent=_P)
    c = _client(_user(_ALICE, a))
    up = c.post(f"/agent/workspace/{sid}/upload", files={"files": ("p.txt", b"mine")})
    assert up.status_code == 200, up.text
    assert (agent_state_dir(_P, f"u:{_ALICE}") / "inputs" / "p.txt").read_bytes() == b"mine"
    assert c.get(f"/agent/workspace/{sid}").json()["root"] == str(
        agent_state_dir(_P, f"u:{_ALICE}"))


@_DB_GATE
def test_the_projects_chat_document_flow(graph_as_app, disk) -> None:  # noqa: F811
    """``write_artifact`` → the ArtifactCard link → ``GET .../file``. The link
    still opens after the disk copy is gone, through the fault-in."""
    a, b = graph_as_app.org_a, graph_as_app.org_b
    sa = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    sb = _seed_session(graph_as_app, b, _CAROL, None, agent=_S)
    res, ws, _key = _run_write(disk, a, sa, f"plan-{uuid.uuid4().hex[:6]}.md", "# The plan")
    url = res["download_url"]
    assert url.startswith(f"/api/agent/workspace/{sa}/file?path=outputs/")
    route = url[len("/api"):]
    alice = _client(_user(_ALICE, a))

    got = alice.get(route)
    assert got.status_code == 200 and got.text == "# The plan"
    (Path(ws) / res["path"]).unlink()
    got = alice.get(route)
    assert got.status_code == 200 and got.text == "# The plan"
    assert (Path(ws) / res["path"]).is_file()

    carol = _client(_user(_CAROL, b))
    assert carol.get(route).status_code == 404
    own = carol.get(f"/agent/workspace/{sb}/file", params={"path": res["path"]})
    assert own.status_code == 404 and "The plan" not in own.text


@_DB_GATE
def test_an_old_document_opens_for_its_own_tenant_only(graph_as_app, disk) -> None:  # noqa: F811
    """A document the Projects chat linked before the tenant dir existed.
    Its session stores the clone, and the clone copy is commingled. Its blob
    row carries ``instance=''`` and the tenant of the run that wrote it (S15).
    The link opens for that tenant, from the row, and for nobody else."""
    a, b = graph_as_app.org_a, graph_as_app.org_b
    rel = f"outputs/old-{uuid.uuid4().hex[:6]}.md"
    (disk.shared / rel).write_text(_MIXED, encoding="utf-8")
    _put(a, "", rel, "A OLD DOC")
    sa = _seed_session(graph_as_app, a, _ALICE, str(disk.shared), agent=_S)
    sb = _seed_session(graph_as_app, b, _CAROL, str(disk.shared), agent=_S)

    got = _client(_user(_ALICE, a)).get(f"/agent/workspace/{sa}/file", params={"path": rel})
    assert got.status_code == 200 and got.text == "A OLD DOC"
    assert (_tenant_dir(a) / rel).read_text(encoding="utf-8") == "A OLD DOC"
    other = _client(_user(_CAROL, b)).get(f"/agent/workspace/{sb}/file", params={"path": rel})
    assert other.status_code == 404
    assert _MIXED not in other.text and "A OLD DOC" not in other.text
    assert not (_tenant_dir(b) / rel).exists()


@_DB_GATE
def test_the_fault_in_writes_no_clone_and_no_other_tenant(graph_as_app, disk) -> None:  # noqa: F811
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance
    from gateway.routes.workspace import _faultin_from_store

    a, b = graph_as_app.org_a, graph_as_app.org_b
    rel = f"outputs/fi-{uuid.uuid4().hex[:6]}.md"
    _put(a, "", rel, "A ROW")
    # A row of org B that carries org A's key (planted, or written by a bug).
    # RLS lets org B read it, so only the tenant check stops the fault-in from
    # copying org B's bytes into org A's dir.
    _put(b, f"o:{a}", rel, "B ROW")
    before = disk.snapshot()
    assert asyncio.run(_faultin_from_store(disk.shared, rel, a)) is False
    assert disk.snapshot() == before
    ta = ensure_state_dir(_S, tenant_instance(a))
    assert asyncio.run(_faultin_from_store(ta, rel, b)) is False
    assert not (ta / rel).exists()
    assert asyncio.run(_faultin_from_store(ta, rel, a)) is True
    assert (ta / rel).read_text(encoding="utf-8") == "A ROW"


@_DB_GATE
def test_a_delete_in_a_tenant_dir_also_drops_the_older_row(graph_as_app, disk) -> None:  # noqa: F811
    from acb_memory import rehydrate_workspace
    from orchestrator.executor import _rehydrate_target

    a = graph_as_app.org_a
    rel = f"outputs/del-{uuid.uuid4().hex[:6]}.md"
    _put(a, "", rel, "OLD")
    _run_write(disk, a, "sid-del", rel, "NEW")
    sid = _seed_session(graph_as_app, a, _ALICE, None, agent=_S)
    r = _client(_user(_ALICE, a)).delete(f"/agent/workspace/{sid}/file", params={"path": rel})
    assert r.status_code == 200, r.text
    assert _rows(graph_as_app, rel) == []
    target = _rehydrate_target(disk.shared, "", a)
    asyncio.run(rehydrate_workspace(_S, target[0], instance=target[1],
                                    organization_id=a, legacy_instance=target[2]))
    assert not (_tenant_dir(a) / rel).exists()
