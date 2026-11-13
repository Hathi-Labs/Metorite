"""WS-STU C5a: company search over Studio records, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 C5a. Code:
``gateway/studio/search.py`` and the ``studio`` source of
``gateway/retrieval/sources.py``. The hermetic half is ``test_studio_search.py``.

Two organizations, and members of the group of one install. The store runs on a
phase-4 promoted scratch database as the app role, so FORCE RLS binds. The
search runs through the real ``Source.find``, with the tenant bound as a request
binds it.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_search_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``search._candidates``: skip the ``package_host_allowed`` test. The test of
  the flag off for installs goes red.
* M2. ``search._read_package``: read ``obj.fields`` before ``package.project``.
  The test of the fields of a newer version goes red.
* M3. ``search.search_records``: drop the ``search_allowed`` test. The test of
  the flag off goes red.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.studio_access import Viewer
from fastapi import HTTPException, Response
from gateway.retrieval import sources
from gateway.routes.studio import packages
from gateway.studio import objects_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@c5a.test"
ANA = f"ana.{TAG}@c5a.test"
BEN = f"ben.{TAG}@c5a.test"
ZED = f"zed.{TAG}@c5a.test"
OPS = f"group:ops{TAG}"
_EMAIL_LIKE = f"%.{TAG}@c5a.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"c5a{TAG}-%"
_COUNTER = iter(range(10_000))
_MIDS: dict[str, str] = {}
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
WORD = f"zorbax{TAG}"


class _Request:
    def __init__(self, payload: Any = None) -> None:
        import json

        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


def _manifest(mid: str, extra_field: bool = False) -> dict[str, Any]:
    note_fields = [{"key": "title", "type": "text"}, {"key": "body", "type": "text"}]
    if extra_field:
        note_fields.append({"key": "memo", "type": "text"})
    return {
        "studio": 1, "id": mid, "name": "Field notes", "purpose": "Keep notes.",
        "objects": [
            {"name": "note", "fields": note_fields},
            {"name": "draft", "fields": [{"key": "title", "type": "text"}]},
            {"name": "slip", "fields": [{"key": "title", "type": "text"},
                                        {"key": "employee", "type": "person", "hot": True}]},
        ],
        "shell": {"search": ["note", "slip"]},
        "actions": [{"id": "find", "uses": "object:note.list"}],
    }


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish", "install_package"):
        kwargs["request"] = _Request(payload)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route == "install_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        got = await handler(**kwargs)
        return response.status_code or (201 if route == "install_package" else 200), got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _live(org: str, owner: str, extra_field: bool = False) -> str:
    mid = f"c5a{TAG}-app{next(_COUNTER)}"
    status, got = await _call("create_package", org, owner, payload=_manifest(mid, extra_field))
    assert status == 200, got
    pid = str(got["package"]["id"])
    _MIDS[pid] = mid
    status, got = await _call("publish", org, owner, pid, payload={})
    assert status == 200, got
    return pid


async def _write(org: str, owner: str, pid: str, obj_name: str, data: dict[str, Any]) -> str:
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, obj_name, package_id=pid)
        assert obj is not None
        rec = await objects_store.create_record(db, owner, obj.id, data, package_id=pid,
                                                viewer=Viewer(owner))
        assert rec is not None
    return str(rec.id)


async def _search(org: str, email: str, q: str = WORD, k: int = 10) -> list[sources.Hit]:
    token = bind_tenant(org)
    try:
        return await sources.REGISTRY["studio"].find(_user(org, email), q, k)
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _ids(hits: list[sources.Hit]) -> set[str]:
    return {h.id.rsplit("/", 1)[1] for h in hits}


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "studio_search_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org in ((OWEN, p.org_a), (ANA, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (:org, :s, :s) RETURNING id"),
            {"org": p.org_a, "s": OPS.removeprefix("group:")}).scalar_one())
        for email in (OWEN, ANA):
            c.execute(text("INSERT INTO org_group_member (group_id, user_id, organization_id) "
                           "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": p.org_a})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    yield w
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def test_r8_the_owner_finds_a_record_right_after_its_write(world) -> None:
    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        before = await _search(world.a, OWEN)
        rid = await _write(world.a, OWEN, pid, "note",
                           {"title": f"Visit {WORD}", "body": "Plant 4, Hosur"})
        # An object that `shell.search` does not name is never read.
        await _write(world.a, OWEN, pid, "draft", {"title": f"Hidden {WORD}"})
        return pid, rid, before, await _search(world.a, OWEN)

    pid, rid, before, hits = _run(world, go)
    assert before == []
    [hit] = hits
    assert (hit.app, hit.kind, hit.id) == ("studio", "studio", f"{pid}/note/{rid}")
    assert hit.title == f"Visit {WORD}" and hit.snippet == "Plant 4, Hosur"
    assert hit.href == f"/apps/{pid}" and "note" in hit.hint


def test_r8_a_deleted_record_is_gone(world) -> None:
    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        rid = await _write(world.a, OWEN, pid, "note", {"title": f"Gone {WORD}"})
        assert _ids(await _search(world.a, OWEN)) == {rid}
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, "note", package_id=pid)
            assert obj is not None
            assert await objects_store.delete_record(
                db, OWEN, obj.id, rid, package_id=pid, viewer=Viewer(OWEN))
        return await _search(world.a, OWEN)

    assert _run(world, go) == []


def test_r8_a_member_who_neither_owns_nor_installed_finds_nothing(world) -> None:
    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        await _write(world.a, OWEN, pid, "note", {"title": f"Owner only {WORD}"})
        return await _search(world.a, BEN), await _search(world.a, ANA)

    ben, ana = _run(world, go)
    assert ben == [] and ana == []


def test_r8_org_a_records_never_reach_org_b(world) -> None:
    async def go() -> Any:
        pid_a = await _live(world.a, OWEN)
        rid_a = await _write(world.a, OWEN, pid_a, "note", {"title": f"A {WORD}"})
        pid_b = await _live(world.b, ZED)
        rid_b = await _write(world.b, ZED, pid_b, "note", {"title": f"B {WORD}"})
        return rid_a, rid_b, await _search(world.a, OWEN), await _search(world.b, ZED)

    rid_a, rid_b, in_a, in_b = _run(world, go)
    assert _ids(in_a) == {rid_a} and _ids(in_b) == {rid_b}


def test_r8_an_installed_viewer_finds_a_record_and_sees_installed_fields_only(world) -> None:
    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        status, got = await _call("install_package", world.a, OWEN, pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        # Version 2 adds the text field `memo`. The install stays at version 1.
        draft = _manifest(_MIDS[pid], extra_field=True)
        status, got = await _call("save_draft", world.a, OWEN, pid, payload=draft)
        assert status == 200, got
        status, got = await _call("publish", world.a, OWEN, pid, payload={})
        assert status == 200, got
        rid = await _write(world.a, OWEN, pid, "note", {
            "title": f"Shared {WORD}", "body": "Visible body", "memo": "SECRET-MEMO"})
        return rid, await _search(world.a, ANA), await _search(world.a, OWEN), \
            await _search(world.a, BEN)

    rid, ana, owen, ben = _run(world, go)
    [hit] = ana
    assert hit.id.endswith(f"/note/{rid}")
    assert hit.title == f"Shared {WORD}" and hit.snippet == "Visible body"
    assert "SECRET-MEMO" not in repr(hit)
    assert OWEN not in repr(hit)
    # The owner reads the newest version, with the field memo in the snippet.
    assert "SECRET-MEMO" in owen[0].snippet
    assert ben == []


def test_r8_a_row_rule_hides_a_record_from_a_viewer(world) -> None:
    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, "slip", package_id=pid)
            assert obj is not None
            assert await objects_store.set_access(db, obj.id, [
                {"role": "hr", "read": "all"},
                {"role": "employee", "read": {"where": "employee = $me"}},
            ], package_id=pid)
        rid = await _write(world.a, OWEN, pid, "slip", {"title": f"Pay {WORD}", "employee": ANA})
        status, got = await _call("install_package", world.a, OWEN, pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        return rid, await _search(world.a, OWEN), await _search(world.a, ANA)

    rid, owner, viewer = _run(world, go)
    # The owner passes each row rule. A viewer with no role reads no record (C8a).
    assert _ids(owner) == {rid}
    assert viewer == []


def test_r8_the_flag_off_returns_nothing_and_runs_no_query(world, monkeypatch) -> None:
    calls: list[Any] = []
    real = objects_store.query_records

    async def spy(*a: Any, **kw: Any) -> Any:
        calls.append(kw)
        return await real(*a, **kw)

    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        await _write(world.a, OWEN, pid, "note", {"title": f"Off {WORD}"})
        monkeypatch.setattr(objects_store, "query_records", spy)
        monkeypatch.setattr(get_settings(), "studio_search_orgs", "")
        off = await _search(world.a, OWEN)
        monkeypatch.setattr(get_settings(), "studio_search_orgs", "someone-else")
        other = await _search(world.a, OWEN)
        n_off = len(calls)
        monkeypatch.setattr(get_settings(), "studio_search_orgs", world.a)
        return off, other, n_off, await _search(world.a, OWEN)

    off, other, n_off, on = _run(world, go)
    assert off == [] and other == [] and n_off == 0
    assert len(on) == 1 and calls
    assert all(isinstance(c["viewer"], Viewer) for c in calls)


def test_r8_the_package_host_off_hides_the_installs(world, monkeypatch) -> None:
    async def go() -> Any:
        pid = await _live(world.a, OWEN)
        await _write(world.a, OWEN, pid, "note", {"title": f"Host {WORD}"})
        status, got = await _call("install_package", world.a, OWEN, pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        on = await _search(world.a, ANA)
        monkeypatch.setattr(get_settings(), "package_host_orgs", "")
        return on, await _search(world.a, ANA), await _search(world.a, OWEN)

    on, off, owner = _run(world, go)
    assert len(on) == 1 and off == [] and len(owner) == 1
