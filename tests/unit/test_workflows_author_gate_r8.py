"""The authoring gate of the Workflows API on a real database (STU-J1-3, R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 J1-3, and the owner's rules of
2026-11-10 on task 1818. Code: ``AUTHOR_GATE`` and ``check_author`` in
``gateway/routes/workflows/core.py``, on top of
``require_feature_router("workflows")``. The hermetic half, with the route
table, is ``test_workflows_author_gate.py``.

Each member here holds a REAL system role of org A, seeded by the real
``provision_org_roles`` (migrations 200 and 207). The real ``resolve_access``
reads the role grants and the overrides on a phase-4 promoted scratch
database, as the non-privileged app role, with the org bound, so FORCE RLS
binds. The resolved access goes into the REAL Workflows router over an ASGI
transport, and each handler reads and writes ``workflows`` under RLS. No
access is built by hand. ``WORKFLOW_STUDIO_GATE_ORGS`` lists org A, except in
case f.

Run (a PRIVATE database, one R8 suite at a time, because the ``promoted``
fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_workflows_author_gate_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J1-3).**

a. A member and the owner hold both features with no new row. Each reads,
   creates, edits, duplicates and deletes a workflow, and the row lands in
   org A. A test run of an empty draft passes the gate and gets the 422 of
   the graph check. A run of the unpublished workflow gets the 409 of the
   handler, so no run starts.
b. A member with a ``feature:studio`` deny reads the list, the workflow and
   its runs, and the published run passes the gate (409). Each write and a
   draft run get 403, and no row changes.
c. A guest with a ``feature:workflows`` allow gets the same answers as b.
d. A member with a ``feature:workflows`` deny gets 403 on the reads, the
   published run and each write. The router gate answers first.
e. ``resolve_access`` gives each of them the features that a to d need.
f. With the flag off, the member of b creates a workflow, as before J1-3.
g. WFC-4a-2s (task 3309, ``ai_studio.md`` row WFC-4a-2s): a member who is
   not the owner gets 403 on a Save that adds or changes a ``studio_write``
   step, and the stored draft does not change. The same member keeps the
   write as the GET gave it (a jsonb round trip), and removes it. The owner
   changes it. A member of org B gets 404 on the workflow of org A, because
   the check reads the stored draft in the tenant session, and nothing
   changes. The hermetic half is ``test_workflows_studio_write_owner_rule.py``.

**The mutations (R7).** Each ran on 2026-11-11, and each test named here
went red.

* M1: drop ``dependencies=[AUTHOR_GATE]`` from ``POST /workflows``. Red:
  ``test_a_studio_deny_reads_and_runs_but_does_not_write`` and
  ``test_a_guest_with_workflows_reads_and_runs_but_does_not_write``.
* M2: drop the ``check_author`` call of ``run_workflow``. Red: the same two.

The mutations of case g are in ``test_workflows_studio_write_owner_rule.py``.
They ran on 2026-11-15.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.routes.workflows import router as workflows_router
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]

#: The people of org A: a key, their system role, and their overrides.
PEOPLE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "owner": ("owner", ()),
    "member": ("member", ()),
    "studio_denied": ("member", (("feature:studio", "deny"),)),
    "workflows_denied": ("member", (("feature:workflows", "deny"),)),
    "guest_workflows": ("guest", (("feature:workflows", "allow"),)),
}

#: The people of org B, for the case across orgs (WFC-4a-2s).
PEOPLE_B: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "b_member": ("member", ()),
}


def _email(key: str) -> str:
    return f"{key}.{TAG}@stuj13.test"


def _org_of(world: Any, key: str) -> str:
    return world.b if key in PEOPLE_B else world.a


def _seed_people(c: Any, org: str, people: dict) -> dict[str, str]:
    """Seed the roles of *org* and each person of *people* in it."""
    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
    c.execute(text("SELECT provision_org_roles(:o)"), {"o": org})
    roles = {str(slug): str(rid) for slug, rid in c.execute(text(
        "SELECT slug, id FROM org_role WHERE organization_id = CAST(:o AS uuid)"),
        {"o": org}).all()}
    for key, (role, overrides) in people.items():
        uid = c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
            {"e": _email(key), "o": org}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
            {"u": uid, "r": roles[role], "o": org})
        for permission, effect in overrides:
            c.execute(text(
                "INSERT INTO user_permission_override (user_id, permission, effect, "
                "reason, set_by, organization_id) VALUES (:u, :p, :x, 'J1-3 R8', "
                "'seed@stuj13.test', CAST(:o AS uuid))"),
                {"u": uid, "p": permission, "x": effect, "o": org})
    return roles


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    a, b = promoted.org_a, promoted.org_b
    with promoted.admin_engine.begin() as c:
        _seed_people(c, b, PEOPLE_B)
        roles = _seed_people(c, a, PEOPLE)
        # One draft workflow of org A, with an empty graph: a draft run of it
        # fails the graph check, and a published run of it gets 409. So no
        # case here starts a run.
        wid = str(c.execute(text(
            "INSERT INTO workflows (name, description, owner_email, hook_token, "
            "organization_id) VALUES (:n, '', :e, :t, CAST(:o AS uuid)) RETURNING id"),
            {"n": f"seed {TAG}", "e": _email("owner"), "t": f"tok-{TAG}", "o": a}).scalar())
    yield SimpleNamespace(p=promoted, a=a, b=b, roles=roles, wid=wid)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflows WHERE owner_email LIKE :t"),
                  {"t": f"%.{TAG}@stuj13.test"})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": f"%.{TAG}@stuj13.test"})


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch, world) -> None:
    """The role leg reads ``app_user`` on a bound session (the cutover path),
    and an earlier suite cannot leave the resolver degraded. The flag lists
    org A by its id, so the list parse runs too."""
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    monkeypatch.setattr(get_settings(), "workflow_studio_gate_orgs", world.a)
    access_mod.invalidate()


def _app(users: dict[str, UserContext]) -> FastAPI:
    """The REAL Workflows router. The caller is the resolved member that the
    test header names, and the dependency binds the org, as the auth layer
    does."""
    app = FastAPI()
    app.include_router(workflows_router)

    async def _caller(request: Request) -> UserContext:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app.dependency_overrides[get_current_user] = _caller
    return app


def _run(world: Any, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``fn(call)`` on the app role's engine. ``call(key, method, path,
    body)`` resolves the member of *key* through the real ``resolve_access``,
    then sends the request through the real router."""
    import acb_auth.access as access_mod

    async def go() -> Any:
        users: dict[str, UserContext] = {}
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with (
            tenant_engine_scope(dsn),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(users)),
                              base_url="http://workflows.test") as client,
        ):
            async def resolve(key: str) -> UserContext:
                org = _org_of(world, key)
                token = bind_tenant(org)
                try:
                    access = await access_mod.resolve_access(_email(key), use_cache=False)
                finally:
                    release_tenant(token)
                return UserContext(email=_email(key), role=UserRole.EMPLOYEE,
                                   access=access, organization_id=org)

            async def call(key: str, method: str, path: str, body: Any = None) -> httpx.Response:
                users[key] = await resolve(key)
                token = bind_tenant(_org_of(world, key))
                try:
                    return await client.request(method, path, json=body,
                                                headers={"x-test-as": key})
                finally:
                    release_tenant(token)

            call.resolve = resolve  # type: ignore[attr-defined]
            return await fn(call)

    return asyncio.run(go())


def _rows(world: Any) -> list[tuple[str, str, str]]:
    """The workflows of this suite, as the superuser sees them: name, org and
    the latest update. A refused write changes none of them."""
    with world.p.admin_engine.connect() as c:
        return [(str(n), str(o), str(u)) for n, o, u in c.execute(text(
            "SELECT name, organization_id, updated_at FROM workflows "
            "WHERE owner_email LIKE :t ORDER BY name"),
            {"t": f"%.{TAG}@stuj13.test"}).all()]


# ── e. The features of each person ────────────────────────────────────────


def test_resolve_access_gives_each_person_the_features_of_the_cases(world) -> None:
    async def fn(call: Any) -> dict[str, tuple[bool, bool, bool]]:
        out: dict[str, tuple[bool, bool, bool]] = {}
        for key in PEOPLE:
            user = await call.resolve(key)
            out[key] = (user.access.is_active, user.has_permission("feature:workflows"),
                        user.has_permission("feature:studio"))
        return out

    got = _run(world, fn)
    assert got == {
        "owner": (True, True, True),
        "member": (True, True, True),
        "studio_denied": (True, True, False),
        "workflows_denied": (True, False, True),
        "guest_workflows": (True, True, False),
    }, got


# ── a. A holder of both features ──────────────────────────────────────────


@pytest.mark.parametrize("key", ["member", "owner"])
def test_a_holder_of_both_features_reads_writes_and_tests(world, key: str) -> None:
    async def fn(call: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        listed = await call(key, "GET", "/workflows")
        out["list"] = (listed.status_code, world.wid in {w["id"] for w in listed.json()})
        out["get"] = (await call(key, "GET", f"/workflows/{world.wid}")).status_code
        created = await call(key, "POST", "/workflows", {"name": f"{key} {TAG}"})
        out["create"] = created.status_code
        new_id = created.json()["id"]
        out["edit"] = (await call(key, "PUT", f"/workflows/{new_id}",
                                  {"description": "edited"})).status_code
        copy = await call(key, "POST", f"/workflows/{new_id}/duplicate")
        out["duplicate"] = copy.status_code
        draft = await call(key, "POST", f"/workflows/{world.wid}/run", {"draft": True})
        out["draft_run"] = (draft.status_code, draft.json()["detail"]["error"])
        out["published_run"] = (await call(key, "POST", f"/workflows/{world.wid}/run",
                                           {"draft": False})).status_code
        out["delete"] = [(await call(key, "DELETE", f"/workflows/{i}")).status_code
                         for i in (new_id, copy.json()["id"])]
        return out

    before = _rows(world)
    got = _run(world, fn)
    assert got == {
        "list": (200, True), "get": 200, "create": 201, "edit": 200, "duplicate": 201,
        "draft_run": (422, "graph_invalid"), "published_run": 409, "delete": [204, 204],
    }, got
    assert _rows(world) == before


def test_a_write_lands_in_the_org_of_the_session(world) -> None:
    async def fn(call: Any) -> str:
        r = await call("member", "POST", "/workflows", {"name": f"zz org {TAG}"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    new_id = _run(world, fn)
    with world.p.admin_engine.begin() as c:
        org = c.execute(text("SELECT organization_id FROM workflows WHERE id = :i"),
                        {"i": new_id}).scalar()
        c.execute(text("DELETE FROM workflows WHERE id = :i"), {"i": new_id})
    assert str(org) == world.a


# ── b, c. A person without `feature:studio` ───────────────────────────────


def _reads_runs_and_writes(world: Any, key: str) -> dict[str, Any]:
    async def fn(call: Any) -> dict[str, Any]:
        wid = world.wid
        out: dict[str, Any] = {
            "list": (await call(key, "GET", "/workflows")).status_code,
            "get": (await call(key, "GET", f"/workflows/{wid}")).status_code,
            "runs": (await call(key, "GET", f"/workflows/{wid}/runs")).status_code,
            "validate": (await call(key, "POST", f"/workflows/{wid}/validate")).status_code,
            "published_run": (await call(key, "POST", f"/workflows/{wid}/run",
                                         {"draft": False})).status_code,
        }
        writes = {
            "draft_run": ("POST", f"/workflows/{wid}/run", {"draft": True}),
            "create": ("POST", "/workflows", {"name": f"refused {TAG}"}),
            "edit": ("PUT", f"/workflows/{wid}", {"name": "renamed"}),
            "duplicate": ("POST", f"/workflows/{wid}/duplicate", None),
            "delete": ("DELETE", f"/workflows/{wid}", None),
            "copilot": ("POST", f"/workflows/{wid}/copilot", {"message": "add a step"}),
        }
        for name, (method, path, body) in writes.items():
            r = await call(key, method, path, body)
            out[name] = (r.status_code, "feature:studio" in str(r.json().get("detail")))
        return out

    return _run(world, fn)


_NO_STUDIO = {
    "list": 200, "get": 200, "runs": 200, "validate": 200, "published_run": 409,
    "draft_run": (403, True), "create": (403, True), "edit": (403, True),
    "duplicate": (403, True), "delete": (403, True), "copilot": (403, True),
}


def test_a_studio_deny_reads_and_runs_but_does_not_write(world) -> None:
    before = _rows(world)
    got = _reads_runs_and_writes(world, "studio_denied")
    assert got == _NO_STUDIO, got
    assert _rows(world) == before


def test_a_guest_with_workflows_reads_and_runs_but_does_not_write(world) -> None:
    before = _rows(world)
    got = _reads_runs_and_writes(world, "guest_workflows")
    assert got == _NO_STUDIO, got
    assert _rows(world) == before


# ── d. A `feature:workflows` deny ─────────────────────────────────────────


def test_a_workflows_deny_blocks_reads_runs_and_writes(world) -> None:
    async def fn(call: Any) -> dict[str, tuple[int, bool]]:
        wid = world.wid
        probes = {
            "list": ("GET", "/workflows", None),
            "get": ("GET", f"/workflows/{wid}", None),
            "published_run": ("POST", f"/workflows/{wid}/run", {"draft": False}),
            "draft_run": ("POST", f"/workflows/{wid}/run", {"draft": True}),
            "create": ("POST", "/workflows", {"name": f"refused {TAG}"}),
            "edit": ("PUT", f"/workflows/{wid}", {"name": "renamed"}),
        }
        out: dict[str, tuple[int, bool]] = {}
        for name, (method, path, body) in probes.items():
            r = await call("workflows_denied", method, path, body)
            out[name] = (r.status_code, "feature:workflows" in str(r.json().get("detail")))
        return out

    before = _rows(world)
    got = _run(world, fn)
    assert set(got.values()) == {(403, True)}, got
    assert _rows(world) == before


# ── f. The flag off ───────────────────────────────────────────────────────


def test_with_the_flag_off_a_studio_deny_still_creates(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "workflow_studio_gate_orgs", "")

    async def fn(call: Any) -> tuple[int, str]:
        r = await call("studio_denied", "POST", "/workflows", {"name": f"zz off {TAG}"})
        return r.status_code, r.json().get("id", "")

    status, new_id = _run(world, fn)
    assert status == 201
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflows WHERE id = :i"), {"i": new_id})


# ── g. Only the owner adds or changes a Studio write (WFC-4a-2s) ──────────

_PKG = "7a1c0e55-0000-4000-8000-0000000000c3"
#: A float, a nested object and a word that is not ASCII, so the keep case
#: proves the compare after a jsonb round trip.
_DATA = {"site": "Plant 4", "weight": 1.5, "meta": {"b": 2, "a": [1, "x"]}, "city": "Pune ✓"}


def _wnode(nid: str, ntype: str, config: dict | None = None, *, x: int = 0) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": x, "y": 0},
            "data": {"label": nid, "config": config or {}}}


def _wconfig(**over: Any) -> dict:
    return {"package_id": _PKG, "action": "add", "data": dict(_DATA), **over}


def _wgraph(*writes: dict) -> dict:
    """trigger -> one approval for each write -> that write."""
    nodes = [_wnode("start", "trigger", {"kind": "schedule"})]
    edges = []
    for i, write in enumerate(writes):
        gate = f"gate{i}"
        nodes += [_wnode(gate, "approval"), write]
        edges += [{"id": f"e{i}a", "source": "start", "target": gate},
                  {"id": f"e{i}b", "source": gate, "target": write["id"]}]
    return {"nodes": nodes, "edges": edges}


def _seed_workflow(world: Any, graph: dict) -> str:
    """A draft of org A, owned by "owner", with *graph*, as the superuser."""
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO workflows (name, description, owner_email, hook_token, graph, "
            "organization_id) VALUES (:n, '', :e, :t, CAST(:g AS jsonb), CAST(:o AS uuid)) "
            "RETURNING id"),
            {"n": f"write {uuid.uuid4().hex[:6]} {TAG}", "e": _email("owner"),
             "t": f"tok-{uuid.uuid4().hex}", "g": json.dumps(graph), "o": world.a}).scalar())


def _stored(world: Any, wid: str) -> tuple[Any, str, str]:
    """The draft, its org and its last update, as the superuser reads them."""
    with world.p.admin_engine.connect() as c:
        g, o, u = c.execute(text(
            "SELECT graph, organization_id, updated_at FROM workflows WHERE id = :i"),
            {"i": wid}).one()
    return g, str(o), str(u)


_REFUSAL = ("Only the owner of this workflow may add or change a step that writes a "
            "record. Ask {owner}.")


def test_another_member_cannot_add_or_change_a_write(world) -> None:
    wid = _seed_workflow(world, _wgraph(_wnode("w", "studio_write", _wconfig())))
    before = _stored(world, wid)
    sends = {
        "add": _wgraph(_wnode("w", "studio_write", _wconfig()),
                       _wnode("w2", "studio_write", _wconfig(action="edit"))),
        "change": _wgraph(_wnode("w", "studio_write",
                                 _wconfig(data={**_DATA, "weight": 2.5}))),
        "respell": _wgraph(_wnode("w", " Studio_Write ", _wconfig())),
    }

    async def fn(call: Any) -> dict[str, tuple[int, str]]:
        out = {}
        for name, graph in sends.items():
            r = await call("member", "PUT", f"/workflows/{wid}", {"graph": graph})
            out[name] = (r.status_code, r.json().get("detail"))
        return out

    got = _run(world, fn)
    refusal = (403, _REFUSAL.format(owner=_email("owner")))
    assert got == {"add": refusal, "change": refusal, "respell": refusal}, got
    assert _stored(world, wid) == before


def test_another_member_cannot_turn_a_stored_step_into_a_write(world) -> None:
    """The stored step has the id and the config of a write, with another type."""
    wid = _seed_workflow(world, _wgraph(_wnode("w", "set", _wconfig())))
    before = _stored(world, wid)

    async def fn(call: Any) -> tuple[int, str]:
        r = await call("member", "PUT", f"/workflows/{wid}",
                       {"graph": _wgraph(_wnode("w", "studio_write", _wconfig()))})
        return r.status_code, r.json().get("detail")

    assert _run(world, fn) == (403, _REFUSAL.format(owner=_email("owner")))
    assert _stored(world, wid) == before


def test_another_member_keeps_and_then_removes_a_write(world) -> None:
    write = _wnode("w", "studio_write", _wconfig())
    wid = _seed_workflow(world, _wgraph(write))

    async def fn(call: Any) -> dict[str, int]:
        graph = (await call("member", "GET", f"/workflows/{wid}")).json()["graph"]
        for node in graph["nodes"]:
            node["position"] = {"x": 500, "y": 40}
            node["data"]["label"] = "Moved"
            node["data"]["config"] = dict(reversed(list(node["data"]["config"].items())))
        kept = await call("member", "PUT", f"/workflows/{wid}", {"graph": graph})
        gone = await call("member", "PUT", f"/workflows/{wid}", {"graph": _wgraph()})
        return {"kept": kept.status_code, "gone": gone.status_code}

    _, _, first = _stored(world, wid)
    got = _run(world, fn)
    assert got == {"kept": 200, "gone": 200}, got
    graph, org, last = _stored(world, wid)
    assert graph == _wgraph() and org == world.a and last != first


def test_the_owner_may_change_a_write(world) -> None:
    wid = _seed_workflow(world, _wgraph(_wnode("w", "studio_write", _wconfig())))
    changed = _wgraph(_wnode("w", "studio_write", _wconfig(action="edit")),
                      _wnode("w2", "studio_write", _wconfig()))

    async def fn(call: Any) -> int:
        return (await call("owner", "PUT", f"/workflows/{wid}", {"graph": changed})).status_code

    assert _run(world, fn) == 200
    assert _stored(world, wid)[0] == changed


def test_a_member_of_another_org_gets_404_and_changes_nothing(world) -> None:
    """The check reads the stored draft in the tenant session of the Save. So
    a member of org B never reads the draft of org A, and gets the 404 of a
    workflow that does not exist."""
    wid = _seed_workflow(world, _wgraph(_wnode("w", "studio_write", _wconfig())))
    before = _stored(world, wid)
    sent = _wgraph(_wnode("w", "studio_write", _wconfig()),
                   _wnode("w2", "studio_write", _wconfig(action="edit")))

    async def fn(call: Any) -> dict[str, Any]:
        user = await call.resolve("b_member")
        return {
            "access": (user.organization_id, user.has_permission("feature:workflows")),
            "get": (await call("b_member", "GET", f"/workflows/{wid}")).status_code,
            "put": [(r.status_code, r.json().get("detail")) for r in [
                await call("b_member", "PUT", f"/workflows/{wid}", {"graph": sent}),
                await call("b_member", "PUT", f"/workflows/{wid}", {"graph": _wgraph()}),
            ]],
        }

    got = _run(world, fn)
    assert got == {"access": (world.b, True), "get": 404,
                   "put": [(404, "Workflow not found")] * 2}, got
    assert _stored(world, wid) == before
