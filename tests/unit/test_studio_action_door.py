"""The action door of AI Studio, the hermetic half (STU-B2a and STU-B2b-1).

Spec: ``project-docs/specs/ai_studio.md`` §3.2, and §14 B2a and B2b-1. Code:
``gateway/routes/studio/invoke.py``. The R8 half, on a real Postgres, is
``test_studio_action_door_r8.py``. It proves the walls of the org, of the
member and of the package, and the audit row.

Here the real stores run on a fake session. The fake answers the four
statements that the door runs (``packages_store._GET_SQL``,
``objects_store._OBJECT_BY_NAME_SQL``, ``_FIELDS_SQL`` and
``_GET_RECORD_SQL``), and each one filters on the bound org, as RLS does. It
is patched at ``invoke._tenant_session``. The catalog read and the audit
writer are patched too. So is ``objects_store.query_records``, because its SQL
needs Postgres. The fake runs the real ``page_bounds`` and ``check_shape``, and
the R8 half runs the real store.

This file proves:

- the switch: a not-listed org gets 404 for each body, and opens no session;
- the order of the checks: the switch, the shape of the action, the
  package, the declaration, the class, then the executor;
- the org, the member and the package come from the session and the path,
  never from the body;
- each refusal code, and that a refused call reads no record;
- the get parses the body and reads with the package id;
- the list takes only the keys of the one input schema, refuses a bad page
  value before any read, and passes its input to the store with the package
  id of the path;
- each ``QueryError`` code maps to its answer, and the audit row holds the
  code and no value;
- the body read: it runs after the checks, with no session open, and it stops
  near the cap;
- a catalog that fails is 503, and an object that the package lacks is 404
  ``object_missing``;
- one audit row for each call that reaches a package, with no record data
  and each text capped;
- the object verb table holds the five verbs of the A2 grammar.

**The mutations (R7).** The R8 suite holds the full list and each result.
These turn a test of this file red:

* M1. Drop the switch check. ``test_a_not_listed_org_gets_404_and_opens_no_session``.
* M3. ``_declared`` answers the first entry. ``test_an_undeclared_action_is_403_not_declared``.
* M4. Drop the class X refusal. ``test_a_class_x_row_is_403_class_x``.
* M5. Drop the class B and C refusal. ``test_each_write_verb_is_403_approval_required``.
* M6a. Take the org from the body. ``test_the_session_takes_no_org_from_the_request``.
* M7b. Take the package id from the body. ``test_a_get_reads_with_the_package_id``.
* M8. 403 for a package of another member. ``test_a_package_of_another_member_or_org_is_404``.
* M10. Parse the body before the switch.
  ``test_a_malformed_body_from_an_org_outside_the_switch_is_404``.
* M11. Drop the ``try`` around the catalog read.
  ``test_a_catalog_that_fails_is_503_and_audited``.
* L1 to L4, and L6 to L12 and L14, of B2b-1 turn a test of this file red. L5
  and L13 need the real store, so only the R8 suite sees them.
* F1 to F11 of the fix round each turn a test of this file red.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings, studio_query
from acb_common.app_manifests.base import CLASSES
from acb_common.studio_manifest import (
    OBJECT_OP_CLASSES,
    OBJECT_OPS,
    parse_uses,
    validate_manifest,
)
from acb_common.studio_query import QueryError, list_input_schema
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, invoke
from gateway.studio import objects_store, packages_store

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
ANA = "ana@example.test"
BEN = "ben@example.test"
NOT_FOUND = {"detail": "Not found"}
WORKFLOW = str(uuid.uuid4())
GONE_WORKFLOW = str(uuid.uuid4())
NOW = datetime(2026, 11, 1, tzinfo=UTC)

MANIFEST: dict[str, Any] = {
    "studio": 1,
    "id": "visits",
    "name": "Visits",
    "purpose": "Track install visits.",
    "objects": [{"name": "visit", "fields": [
        {"key": "site", "type": "text"},
        {"key": "notes", "type": "text"},
    ]}],
    "actions": [
        {"id": "peek", "uses": "object:visit.get"},
        {"id": "find", "uses": "object:visit.list"},
        {"id": "add", "uses": "object:visit.create"},
        {"id": "change", "uses": "object:visit.update"},
        {"id": "remove", "uses": "object:visit.delete"},
        {"id": "flow", "uses": f"workflow:{WORKFLOW}"},
        {"id": "gone_flow", "uses": f"workflow:{GONE_WORKFLOW}"},
        {"id": "pages", "uses": "knowledge.list_pages"},
        {"id": "propose", "uses": "knowledge.propose"},
        {"id": "sync", "uses": "integration:zoho.push"},
        {"id": "forbidden", "uses": "knowledge.wipe"},
    ],
}

#: The catalog that the fake ``catalog_rows`` serves. ``knowledge.wipe`` is a
#: class X row that no real catalog holds, so the door's X check has a case.
CATALOG: list[dict[str, Any]] = [
    {"id": f"workflow:{WORKFLOW}", "class": "B", "source": "workflow"},
    {"id": "knowledge.list_pages", "class": "A", "source": "manifest"},
    {"id": "knowledge.propose", "class": "B", "source": "manifest"},
    {"id": "integration:zoho.push", "class": "C", "source": "integration"},
    {"id": "knowledge.wipe", "class": "X", "source": "manifest"},
]

RECORD_DATA = {"site": "Plant 4, Hosur", "notes": "Bring the nozzle kit"}


# ── The fake session ───────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDB:
    """One session bound to *org*. It runs the four statements of the door,
    and each one filters on the bound org, as RLS does."""

    def __init__(self, world: Any, org: str | None) -> None:
        assert org, "a door session opened with no bound org"
        self.w = world
        self.org = org

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql, p, w = str(stmt), dict(params or {}), self.w
        w.statements.append((sql, p))
        if sql == packages_store._GET_SQL:
            pkg = w.packages.get(p["id"])
            if pkg and pkg["org"] == self.org and pkg["owner"] == p["m"]:
                return _Result([(pkg["id"], f"visits-{pkg['id'][:8]}", "Visits", "",
                                 pkg["owner"], "private", "draft",
                                 json.dumps(pkg["manifest"]), None, NOW, NOW)])
            return _Result()
        if sql == objects_store._OBJECT_BY_NAME_SQL:
            for o in w.objects:
                if (o["org"], o["package"], o["name"]) == (self.org, p["pid"], p["name"]):
                    return _Result([(o["id"], o["package"], o["name"], None, ANA, NOW, NOW, [])])
            return _Result()
        if sql == objects_store._FIELDS_SQL:
            return _Result([("site", "text", {}, False), ("notes", "text", {}, False)])
        if sql == objects_store._GET_RECORD_SQL:
            for r in w.records:
                o = next(x for x in w.objects if x["id"] == r["object"])
                if (o["org"], o["package"], r["object"], r["id"]) == (
                        self.org, p["pid"], p["oid"], p["rid"]):
                    return _Result([(r["id"], r["object"], json.dumps(r["data"]), ANA, ANA,
                                     NOW, NOW)])
            return _Result()
        raise AssertionError(f"an unexpected statement: {sql[:80]}")


def _package(w: Any, org: str = ORG, owner: str = ANA,
             manifest: dict[str, Any] | None = None) -> str:
    pid = str(uuid.uuid4())
    w.packages[pid] = {"id": pid, "org": org, "owner": owner,
                       "manifest": manifest or MANIFEST}
    return pid


def _object(w: Any, package: str, org: str = ORG, name: str = "visit") -> str:
    oid = str(uuid.uuid4())
    w.objects.append({"id": oid, "org": org, "package": package, "name": name})
    return oid


def _record(w: Any, obj: str, data: dict[str, Any] | None = None) -> str:
    rid = str(uuid.uuid4())
    w.records.append({"id": rid, "object": obj, "data": data or RECORD_DATA})
    return rid


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(org=None, packages={}, objects=[], records=[], statements=[],
                        log=[], session_args=[], audits=[], catalog=list(CATALOG),
                        catalog_fails=False, queries=[], query_raises=None,
                        store_says_none=False)

    @asynccontextmanager
    async def _session(*args: Any):
        w.session_args.append(args)
        w.log.append("open")
        try:
            yield _FakeDB(w, w.org)
        finally:
            w.log.append("close")

    async def _catalog_rows() -> list[dict[str, Any]]:
        w.log.append("catalog")
        if w.catalog_fails:
            raise RuntimeError("the catalog session failed")
        return list(w.catalog)

    async def _audit(**kw: Any) -> None:
        w.log.append("audit")
        w.audits.append(kw)

    async def _query(db: Any, *, package_id: str, object_id: str, filter: Any = None,
                     sort: Any = None, limit: Any = None, offset: Any = None) -> Any:
        """The store's list on the fake session, under the real page bounds and
        the real shape check. It filters on the bound org, as RLS does."""
        w.queries.append({"package_id": package_id, "object_id": object_id, "filter": filter,
                          "sort": sort, "limit": limit, "offset": offset})
        start, size = studio_query.page_bounds(offset, limit)
        studio_query.check_shape(filter, sort)
        if w.query_raises is not None:
            raise w.query_raises
        held = [o for o in w.objects
                if (o["id"], o["package"], o["org"]) == (object_id, package_id, db.org)]
        if w.store_says_none or not held:
            return None
        rows = [objects_store.Record(r["id"], r["object"], r["data"], ANA, ANA, NOW, NOW)
                for r in w.records if r["object"] == object_id]
        return objects_store.RecordPage(tuple(rows[start:start + size]), len(rows), None,
                                        start, size, "")

    monkeypatch.setattr(invoke, "_tenant_session", _session)
    monkeypatch.setattr(objects_store, "query_records", _query)
    monkeypatch.setattr(actions, "catalog_rows", _catalog_rows)
    monkeypatch.setattr(invoke, "record_app_audit_bound", _audit)
    _open(monkeypatch, "*")
    return w


def _open(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", value)


def _client(world: Any, org: str | None = ORG, email: str = ANA) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        # The auth layer binds the session's org. The fake session reads it.
        world.org = org
        return UserContext(email=email, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:workflows"]), organization_id=org)

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _call(c: TestClient, pid: str, action: str, body: Any = None) -> Any:
    if body is None:
        return c.post(f"/studio/apps/{pid}/actions/{action}")
    return c.post(f"/studio/apps/{pid}/actions/{action}", json=body)


def _raw(c: TestClient, pid: str, action: str, content: str | bytes) -> Any:
    return c.post(f"/studio/apps/{pid}/actions/{action}", content=content,
                  headers={"content-type": "application/json"})


def _setup(world: Any) -> tuple[str, str, str]:
    pid = _package(world)
    oid = _object(world, pid)
    return pid, oid, _record(world, oid)


def _code(r: Any) -> str:
    return r.json()["detail"]["code"]


def _record_reads(world: Any) -> list[dict[str, Any]]:
    return [p for sql, p in world.statements if sql == objects_store._GET_RECORD_SQL]


# ── The switch ─────────────────────────────────────────────────────────────


class TestTheSwitch:
    def test_a_not_listed_org_gets_404_and_opens_no_session(self, monkeypatch, world):
        pid, _, rid = _setup(world)
        _open(monkeypatch, OTHER)
        r = _call(_client(world), pid, "peek", {"id": rid})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.log == [] and world.statements == [] and world.audits == []

    @pytest.mark.parametrize("content", ["{not json", "[" * 20000, "\x00\xff", ""],
                             ids=["not_json", "deep", "bytes", "empty"])
    def test_a_malformed_body_from_an_org_outside_the_switch_is_404(
            self, monkeypatch, world, content):
        """FastAPI parses no body before the switch: the route takes the raw
        request. So a body that is not JSON gets 404, never 422."""
        pid, _, _ = _setup(world)
        _open(monkeypatch, OTHER)
        for action in ("peek", "add", "no_such_action"):
            r = _client(world).post(f"/studio/apps/{pid}/actions/{action}", content=content,
                                    headers={"content-type": "application/json"})
            assert (r.status_code, r.json()) == (404, NOT_FOUND), (action, r.text)
        assert world.log == [] and world.statements == [] and world.audits == []

    def test_empty_opens_no_org(self, monkeypatch, world):
        pid, _, rid = _setup(world)
        _open(monkeypatch, "")
        assert _call(_client(world), pid, "peek", {"id": rid}).status_code == 404

    def test_no_org_gets_404_even_with_star(self, world):
        pid, _, rid = _setup(world)
        r = _call(_client(world, org=None), pid, "peek", {"id": rid})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.log == []

    @pytest.mark.parametrize("listed", ["*", f"{OTHER}, {ORG.upper()}"])
    def test_a_listed_org_and_star_get_200(self, monkeypatch, world, listed):
        pid, _, rid = _setup(world)
        _open(monkeypatch, listed)
        assert _call(_client(world), pid, "peek", {"id": rid}).status_code == 200

    def test_the_route_joins_the_one_studio_router(self):
        paths = {(m, r.path) for r in actions.router.routes for m in r.methods}
        assert ("POST", "/studio/apps/{app_id}/actions/{action}") in paths
        assert invoke.router is actions.router


# ── The package ────────────────────────────────────────────────────────────


class TestThePackage:
    @pytest.mark.parametrize("org,email", [(ORG, BEN), (OTHER, ANA)])
    def test_a_package_of_another_member_or_org_is_404(self, world, org, email):
        pid = _package(world, org=org, owner=email)
        rid = _record(world, _object(world, pid, org=org))
        for action in ("peek", "add", "remove", "flow", "no_such_action"):
            r = _call(_client(world), pid, action, {"id": rid})
            assert (r.status_code, r.json()) == (404, NOT_FOUND), action
        assert _record_reads(world) == [] and world.audits == []
        assert "catalog" not in world.log

    def test_an_id_that_names_nothing_is_404(self, world):
        r = _call(_client(world), str(uuid.uuid4()), "peek", {"id": str(uuid.uuid4())})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.audits == []

    def test_a_bad_app_id_is_422_and_opens_no_session(self, world):
        r = _call(_client(world), "not-a-uuid", "peek", {"id": str(uuid.uuid4())})
        assert r.status_code == 422
        assert world.log == []

    def test_the_session_takes_no_org_from_the_request(self, world):
        pid, _, rid = _setup(world)
        body = {"id": rid, "organization_id": OTHER, "owner_email": BEN, "member": BEN}
        r = _client(world).post(f"/studio/apps/{pid}/actions/peek", json=body,
                                params={"organization_id": OTHER, "owner_email": BEN})
        assert r.status_code == 200, r.text
        assert world.session_args == [(), ()]
        reads = [p for sql, p in world.statements if sql == packages_store._GET_SQL]
        assert reads == [{"m": ANA, "id": pid}] * 2
        assert world.audits[0]["user_email"] == ANA


# ── The declaration and the class ──────────────────────────────────────────


class TestTheChecks:
    @pytest.mark.parametrize("action", ["object:visit.get", "visit.get", "Peek", "9peek",
                                        "a" * 49, "a" * 20000],
                             ids=["uses", "dotted", "upper", "digit", "49", "20000"])
    def test_an_action_that_is_not_an_id_is_404_before_any_read(self, world, action):
        """An action that is not an A2 action id names nothing. It gets 404
        before the session opens, so no audit row holds it."""
        pid, _, rid = _setup(world)
        r = _call(_client(world), pid, action, {"id": rid})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.log == [] and world.audits == []

    def test_an_undeclared_action_is_403_not_declared(self, world):
        pid, _, rid = _setup(world)
        for action in ("peek_all", "peek2", "a" * 48):
            r = _call(_client(world), pid, action, {"id": rid})
            assert r.status_code == 403, action
            assert r.json()["detail"] == {"code": "not_declared",
                                          "message": invoke.REFUSALS["not_declared"]}
        assert _record_reads(world) == []
        assert "catalog" not in world.log

    @pytest.mark.parametrize("action,cls", [("add", "B"), ("change", "B"), ("remove", "C")])
    def test_each_write_verb_is_403_approval_required(self, world, action, cls):
        pid, _, rid = _setup(world)
        r = _call(_client(world), pid, action, {"id": rid, "site": "x"})
        assert (r.status_code, _code(r)) == (403, "approval_required")
        # Nothing runs: no object read, no record read, no catalog read.
        assert [sql for sql, _ in world.statements] == [packages_store._GET_SQL]
        assert "catalog" not in world.log
        assert world.audits[0]["detail"]["class"] == cls

    def test_a_list_is_class_a_and_it_runs(self, world):
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "find", {})
        assert r.status_code == 200, r.text
        assert world.audits[0]["detail"] == {
            "action": "find", "uses": "object:visit.list", "class": "A", "outcome": "ran"}

    @pytest.mark.parametrize("action,uses", [
        ("flow", f"workflow:{WORKFLOW}"),
        ("sync", "integration:zoho.push"),
        ("pages", "knowledge.list_pages"),
    ], ids=["workflow", "integration", "door_op"])
    def test_a_class_a_source_that_is_not_an_object_is_source_not_open(
            self, world, action, uses):
        """The list opens one verb of one source. A class A row of each other
        source still stops, and it reads no object."""
        pid, _, rid = _setup(world)
        world.catalog = [{"id": uses, "class": "A", "source": "x"}]
        r = _call(_client(world), pid, action, {"id": rid})
        assert (r.status_code, _code(r)) == (403, "source_not_open")
        assert r.json()["detail"]["message"] == invoke.REFUSALS["source_not_open"]
        assert [sql for sql, _ in world.statements] == [packages_store._GET_SQL]
        assert world.queries == []

    @pytest.mark.parametrize("action,code,cls", [
        ("flow", "approval_required", "B"),
        ("propose", "approval_required", "B"),
        ("sync", "approval_required", "C"),
        ("pages", "source_not_open", "A"),
        ("gone_flow", "not_in_catalog", None),
    ])
    def test_a_source_that_is_not_an_object_never_runs(self, world, action, code, cls):
        pid, _, rid = _setup(world)
        r = _call(_client(world), pid, action, {"id": rid})
        assert (r.status_code, _code(r)) == (403, code)
        assert [sql for sql, _ in world.statements] == [packages_store._GET_SQL]
        assert world.audits[0]["detail"]["class"] == cls

    def test_a_catalog_that_fails_is_503_and_audited(self, world):
        pid, _, rid = _setup(world)
        world.catalog_fails = True
        r = _call(_client(world), pid, "flow", {"id": rid})
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == {"code": "catalog_unavailable",
                                      "message": invoke.CATALOG_DOWN}
        assert world.audits[0]["detail"] == {
            "action": "flow", "uses": f"workflow:{WORKFLOW}", "class": None,
            "outcome": "failed", "reason": "catalog_unavailable"}
        assert world.log == ["open", "close", "catalog", "audit"]

    def test_a_class_x_row_is_403_class_x(self, world):
        pid, _, rid = _setup(world)
        r = _call(_client(world), pid, "forbidden", {"id": rid})
        assert (r.status_code, _code(r)) == (403, "class_x")
        assert world.audits[0]["detail"]["class"] == "X"

    def test_the_catalog_read_runs_after_the_session_closes(self, world):
        pid, _, rid = _setup(world)
        _call(_client(world), pid, "flow", {"id": rid})
        assert world.log == ["open", "close", "catalog", "audit"]

    def test_the_order_is_package_then_declaration_then_class(self, world):
        """An undeclared action of a package of another member is 404, not
        403. A class B action of the caller's package is 403 before any body
        check, so a bad body does not change the answer."""
        theirs = _package(world, owner=BEN)
        assert _call(_client(world), theirs, "no_such_action").status_code == 404
        mine = _package(world)
        r = _call(_client(world), mine, "add", ["not", "an", "object"])
        assert (r.status_code, _code(r)) == (403, "approval_required")


# ── The one executor ───────────────────────────────────────────────────────


class TestTheGet:
    def test_a_get_answers_the_record_as_the_store_returns_it(self, world):
        pid, oid, rid = _setup(world)
        r = _call(_client(world), pid, "peek", {"id": rid})
        assert r.status_code == 200, r.text
        got = r.json()["record"]
        assert (got["id"], got["object_id"], got["data"]) == (rid, oid, RECORD_DATA)
        assert set(got) == {"id", "object_id", "data", "created_by", "updated_by",
                            "created_at", "updated_at"}

    def test_a_get_reads_with_the_package_id(self, world):
        pid, oid, rid = _setup(world)
        other = _package(world)
        _call(_client(world), pid, "peek", {"id": rid, "package_id": other})
        by_name = [p for sql, p in world.statements
                   if sql == objects_store._OBJECT_BY_NAME_SQL]
        assert by_name == [{"pid": pid, "name": "visit"}]
        assert _record_reads(world) == [{"oid": oid, "rid": rid, "pid": pid}]

    def test_a_record_of_another_package_is_404(self, world):
        mine = _package(world)
        _object(world, mine)
        other = _package(world)
        theirs = _record(world, _object(world, other))
        for body in ({"id": theirs}, {"id": theirs, "package_id": other}):
            r = _call(_client(world), mine, "peek", body)
            assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert [a["detail"]["outcome"] for a in world.audits] == ["not_found"] * 2
        assert all("reason" not in a["detail"] for a in world.audits)

    def test_an_object_that_the_registry_lacks_is_404_object_missing(self, world):
        """The caller owns the package, so the code leaks nothing."""
        pid = _package(world)
        r = _call(_client(world), pid, "peek", {"id": str(uuid.uuid4())})
        assert r.status_code == 404
        assert r.json()["detail"] == {"code": "object_missing", "message": invoke.NO_OBJECT}
        assert _record_reads(world) == []
        assert world.audits[0]["detail"] == {
            "action": "peek", "uses": "object:visit.get", "class": "A",
            "outcome": "not_found", "reason": "object_missing"}

    def test_a_record_that_names_nothing_is_404_not_found(self, world):
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "peek", {"id": str(uuid.uuid4())})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.audits[0]["detail"] == {
            "action": "peek", "uses": "object:visit.get", "class": "A",
            "outcome": "not_found"}

    @pytest.mark.parametrize("body", [None, {}, {"id": 7}, {"id": "not-a-uuid"},
                                      ["a", "list"], "text"])
    def test_a_bad_record_id_is_422(self, world, body):
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "peek", body)
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == invoke.BAD_RECORD_ID
        assert _record_reads(world) == []
        assert world.audits[0]["detail"] == {
            "action": "peek", "uses": "object:visit.get", "class": "A",
            "outcome": "refused", "reason": "bad_input"}

    @pytest.mark.parametrize("content", ["[" * 20000 + "]" * 20000, "{not json",
                                         "\x00\xff"], ids=["deep", "not_json", "bytes"])
    def test_a_body_that_does_not_parse_is_422_never_500(self, world, content):
        pid, _, _ = _setup(world)
        r = _client(world).post(f"/studio/apps/{pid}/actions/peek", content=content,
                                headers={"content-type": "application/json"})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == invoke.BAD_RECORD_ID
        assert world.audits[0]["detail"]["reason"] == "bad_input"

    def test_a_write_action_never_parses_the_body(self, world):
        pid, _, _ = _setup(world)
        r = _client(world).post(f"/studio/apps/{pid}/actions/add", content="{not json",
                                headers={"content-type": "application/json"})
        assert (r.status_code, _code(r)) == (403, "approval_required")


# ── The list ───────────────────────────────────────────────────────────────


#: One input with each key of the schema.
LIST_BODY: dict[str, Any] = {
    "filter": [{"field": "site", "op": "contains", "value": "Plant"}],
    "sort": {"field": "site", "dir": "desc"},
    "limit": 5,
    "offset": 2,
}

#: The four keys, each None: what the store gets for an empty input.
NO_INPUT: dict[str, Any] = {"filter": None, "sort": None, "limit": None, "offset": None}


def _read_nothing(world: Any) -> None:
    """The door read the package, and no object and no record."""
    assert {sql for sql, _ in world.statements} == {packages_store._GET_SQL}
    assert world.queries == []


class TestTheList:
    def test_a_list_answers_the_shape_of_a_record_page(self, world):
        pid, _, rid = _setup(world)
        c = _client(world)
        r = _call(c, pid, "find")
        assert r.status_code == 200, r.text
        got = r.json()
        assert set(got) == {f.name for f in dataclasses.fields(objects_store.RecordPage)}
        assert set(got) == {"records", "total", "total_at_least", "offset", "limit", "line"}
        assert (got["total"], got["total_at_least"], got["offset"], got["limit"], got["line"]) \
            == (1, None, 0, 25, "")
        # A record of a page is the record of a get, key for key.
        assert got["records"] == [_call(c, pid, "peek", {"id": rid}).json()["record"]]

    @pytest.mark.parametrize("content", ["", "{}"], ids=["empty", "object"])
    def test_an_empty_body_means_no_input(self, world, content):
        pid, oid, _ = _setup(world)
        r = _raw(_client(world), pid, "find", content)
        assert r.status_code == 200, r.text
        assert world.queries == [{"package_id": pid, "object_id": oid, **NO_INPUT}]

    def test_a_list_passes_its_input_to_the_store_with_the_package_of_the_path(self, world):
        pid, oid, _ = _setup(world)
        r = _call(_client(world), pid, "find", LIST_BODY)
        assert r.status_code == 200, r.text
        assert world.queries == [{"package_id": pid, "object_id": oid, **LIST_BODY}]
        by_name = [p for sql, p in world.statements
                   if sql == objects_store._OBJECT_BY_NAME_SQL]
        assert by_name == [{"pid": pid, "name": "visit"}]

    @pytest.mark.parametrize("body,offset,limit", [
        ({"limit": 1000}, 0, 100),
        ({"limit": 0}, 0, 25),
        ({"limit": -3}, 0, 25),
        ({"offset": -5}, 0, 25),
    ], ids=["over", "zero", "negative", "offset"])
    def test_an_int_out_of_range_takes_the_bounds_and_the_page_says_so(
            self, world, body, offset, limit):
        """The door checks the type. ``paging.bounds`` is the one rule of the
        range, and the answer holds the values that it used."""
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "find", body)
        assert r.status_code == 200, r.text
        assert (r.json()["offset"], r.json()["limit"]) == (offset, limit)

    @pytest.mark.parametrize("key", ["package_id", "organization_id", "owner_email", "member",
                                     "id", "Filter", "page", ""])
    def test_a_key_outside_the_schema_is_422_and_reads_nothing(self, world, key):
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "find", {"limit": 1, key: "x"})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == {"code": "query_value_invalid",
                                      "message": invoke.BAD_LIST_INPUT, "field": None}
        _read_nothing(world)
        assert world.audits[0]["detail"] == {
            "action": "find", "uses": "object:visit.list", "class": "A",
            "outcome": "refused", "reason": "query_value_invalid"}

    @pytest.mark.parametrize("content", [
        '{"limit": true}', '{"offset": false}', '{"limit": Infinity}', '{"offset": Infinity}',
        '{"offset": -Infinity}', '{"limit": NaN}', '{"limit": 1e400}', '{"limit": "7"}',
        '{"limit": 1.5}', '{"limit": null}', '{"offset": null}', '{"offset": [1]}',
    ])
    def test_a_limit_or_offset_that_is_not_an_int_is_422_and_reads_nothing(self, world, content):
        """``json.loads`` takes ``Infinity``, and ``paging.bounds`` raises
        ``OverflowError`` on it. So the check runs before the store."""
        pid, _, _ = _setup(world)
        r = _raw(_client(world), pid, "find", content)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["message"] == invoke.BAD_LIST_INPUT
        _read_nothing(world)

    def test_a_number_too_long_to_parse_is_422_never_500(self, world):
        """``json.loads`` raises ``ValueError`` past 4,300 digits."""
        pid, _, _ = _setup(world)
        r = _raw(_client(world), pid, "find", '{"offset": ' + "9" * 5000 + "}")
        assert r.status_code == 422, r.text
        _read_nothing(world)

    @pytest.mark.parametrize("content", ["[]", '"text"', "7", "null", "{not json", "   ",
                                         "[" * 20000 + "]" * 20000, b'{"limit": \xff}'],
                             ids=["list", "text", "number", "null", "not_json", "blank",
                                  "deep", "bytes"])
    def test_a_body_that_is_not_a_json_object_is_422_never_500(self, world, content):
        pid, _, _ = _setup(world)
        r = _raw(_client(world), pid, "find", content)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["message"] == invoke.BAD_LIST_INPUT
        _read_nothing(world)

    @pytest.mark.parametrize("code", [c for c in studio_query.QUERY_ERROR_CODES
                                      if c != studio_query.TIMEOUT])
    def test_each_refused_query_is_422_with_its_code(self, world, code):
        pid, _, _ = _setup(world)
        world.query_raises = QueryError(code, "One sentence.", "site")
        r = _call(_client(world), pid, "find", {})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == {"code": code, "message": "One sentence.", "field": "site"}
        assert world.audits[0]["detail"] == {
            "action": "find", "uses": "object:visit.list", "class": "A",
            "outcome": "refused", "reason": code}

    def test_a_query_timeout_is_503_with_retry_after(self, world):
        pid, _, _ = _setup(world)
        world.query_raises = QueryError(studio_query.TIMEOUT, "Try again.")
        r = _call(_client(world), pid, "find", {})
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == {"code": "query_timeout", "message": "Try again."}
        assert r.headers["retry-after"] == "2"
        assert world.audits[0]["detail"] == {
            "action": "find", "uses": "object:visit.list", "class": "A",
            "outcome": "failed", "reason": "query_timeout"}

    @pytest.mark.parametrize("body,code,field", [
        ({"offset": 10_001}, "query_too_big", None),
        ({"filter": "text"}, "query_value_invalid", None),
        ({"filter": [{"field": "site", "op": "matches"}]}, "query_op_not_allowed", "site"),
        ({"sort": {"field": "site", "dir": "up"}}, "query_value_invalid", "site"),
    ], ids=["offset", "filter", "op", "dir"])
    def test_a_refusal_of_the_real_grammar_reaches_the_answer(self, world, body, code, field):
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "find", body)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert (detail["code"], detail["field"]) == (code, field)

    def test_a_refusal_echoes_no_key_and_no_value_of_the_body(self, world):
        pid, _, _ = _setup(world)
        c = _client(world)
        r = _call(c, pid, "find", {"leaky_key": "leaky_value"})
        assert r.status_code == 422
        assert "leaky" not in r.text and "leaky" not in json.dumps(world.audits)
        world.query_raises = QueryError(studio_query.VALUE_INVALID, "One sentence.", "site")
        body = {"filter": [{"field": "site", "op": "eq", "value": "leaky_value"}]}
        assert _call(c, pid, "find", body).status_code == 422
        assert "leaky" not in json.dumps(world.audits)

    def test_an_object_that_the_registry_lacks_is_404_object_missing(self, world):
        pid = _package(world)
        r = _call(_client(world), pid, "find", {})
        assert r.status_code == 404
        assert r.json()["detail"] == {"code": "object_missing", "message": invoke.NO_OBJECT}
        assert world.queries == []
        assert world.audits[0]["detail"] == {
            "action": "find", "uses": "object:visit.list", "class": "A",
            "outcome": "not_found", "reason": "object_missing"}

    def test_none_from_the_store_is_404_object_missing(self, world):
        """The store reads the object again. A delete between the two reads,
        or an object of another package, gives None."""
        pid, _, _ = _setup(world)
        world.store_says_none = True
        r = _call(_client(world), pid, "find", {})
        assert r.status_code == 404
        assert r.json()["detail"] == {"code": "object_missing", "message": invoke.NO_OBJECT}
        assert world.audits[0]["detail"]["reason"] == "object_missing"

    def test_an_id_that_the_store_refuses_is_404_object_missing(self, world, monkeypatch):
        """The body names no id, so the store refused a stored value. The
        caller can change nothing, so the answer is not a 422."""
        pid, _, _ = _setup(world)
        logged: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(invoke, "_log", SimpleNamespace(
            warning=lambda event, **kw: logged.append((event, kw))))
        world.query_raises = objects_store.ObjectStoreError("object_id must be a UUID")
        r = _call(_client(world), pid, "find", {})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "object_missing"
        # It cannot fire today, so a future fault shows in the log. No value.
        assert logged == [("studio.invoke.stored_value_refused", {"error": "ObjectStoreError"})]

    def test_the_session_takes_no_org_or_package_from_the_request(self, world):
        pid, oid, _ = _setup(world)
        other = _package(world)
        c = _client(world)
        query = {"organization_id": OTHER, "owner_email": BEN, "package_id": other}
        r = c.post(f"/studio/apps/{pid}/actions/find", json=query, params=query)
        assert r.status_code == 422, r.text
        r = c.post(f"/studio/apps/{pid}/actions/find", json={}, params=query)
        assert r.status_code == 200, r.text
        assert world.session_args == [()] * 4
        assert world.queries == [{"package_id": pid, "object_id": oid, **NO_INPUT}]
        assert {a["user_email"] for a in world.audits} == {ANA}

    def test_a_package_of_another_member_or_org_is_404_for_a_list(self, world):
        for org, email in ((ORG, BEN), (OTHER, ANA)):
            pid = _package(world, org=org, owner=email)
            _object(world, pid, org=org)
            r = _call(_client(world), pid, "find", {})
            assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.queries == [] and world.audits == []

    def test_the_row_of_a_list_holds_no_record_data(self, world):
        pid, _, _ = _setup(world)
        _call(_client(world), pid, "find", {})
        text = json.dumps(world.audits)
        for value in RECORD_DATA.values():
            assert value not in text

    def test_the_audit_of_a_list_runs_after_the_session_closes(self, world):
        pid, _, _ = _setup(world)
        world.query_raises = QueryError(studio_query.TIMEOUT, "Try again.")
        _call(_client(world), pid, "find", {})
        assert world.log == ["open", "close", "open", "close", "audit"]


# ── The body read ──────────────────────────────────────────────────────────


class _Body:
    """A request that counts the bytes that the door reads. Each read logs, so
    a test sees what is open during it. A test client reads a whole body before
    the app runs, so these tests call the handler with this request."""

    def __init__(self, world: Any, chunk: bytes = b"{}", repeat: int = 1,
                 fails: bool = False) -> None:
        self.world, self.chunk, self.repeat, self.fails = world, chunk, repeat, fails
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")
        self.reads = 0
        self.sent = 0

    async def stream(self) -> Any:
        self.reads += 1
        self.world.log.append("body")
        if self.fails:
            raise OSError("the read of the body failed")
        for _ in range(self.repeat):
            self.sent += len(self.chunk)
            yield self.chunk

    async def body(self) -> bytes:
        raise AssertionError("request.body() has no cap, so the door must not call it")


def _direct(world: Any, pid: str, action: str, request: Any, org: str = ORG,
            email: str = ANA) -> tuple[int, Any]:
    """Call the handler with *request*. Answers the status and the answer, or
    the detail of a refusal."""
    world.org = org
    user = UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:workflows"]), organization_id=org)

    async def go() -> tuple[int, Any]:
        try:
            return 200, await invoke.invoke_action(pid, action, request=request, user=user)
        except HTTPException as exc:
            return exc.status_code, exc.detail

    return asyncio.run(go())


class TestTheBodyRead:
    def test_the_cap_is_one_mib(self):
        assert invoke.MAX_BODY_BYTES == 1024 * 1024

    @pytest.mark.parametrize("action,uses", [("find", "object:visit.list"),
                                             ("peek", "object:visit.get")])
    def test_a_body_past_the_cap_is_413_and_the_read_stops_near_the_cap(
            self, world, action, uses):
        """A stream of 256 MiB. The door stops at the first chunk past the cap,
        so it reads the package, reads no object, and writes the audit row."""
        pid, _, _ = _setup(world)
        body = _Body(world, b"x" * 65_536, repeat=4096)
        status, detail = _direct(world, pid, action, body)
        assert (status, detail) == (413, {"code": "body_too_large",
                                          "message": invoke.BODY_OVER_CAP})
        assert invoke.MAX_BODY_BYTES < body.sent <= invoke.MAX_BODY_BYTES + len(body.chunk)
        assert body.sent < 2 * invoke.MAX_BODY_BYTES
        _read_nothing(world)
        assert world.audits[0]["detail"] == {
            "action": action, "uses": uses, "class": "A",
            "outcome": "refused", "reason": "body_too_large"}

    def test_a_body_at_the_cap_runs_and_one_byte_more_is_413(self, world):
        pid, _, _ = _setup(world)
        c = _client(world)
        at_cap = b"{}" + b" " * (invoke.MAX_BODY_BYTES - 2)
        assert _raw(c, pid, "find", at_cap).status_code == 200
        r = _raw(c, pid, "find", at_cap + b" ")
        assert (r.status_code, _code(r)) == (413, "body_too_large")

    def test_a_read_that_fails_with_the_switch_off_is_404_and_never_runs(
            self, monkeypatch, world):
        pid, _, _ = _setup(world)
        _open(monkeypatch, OTHER)
        body = _Body(world, fails=True)
        assert _direct(world, pid, "find", body) == (404, "Not found")
        assert body.reads == 0 and world.log == []

    @pytest.mark.parametrize("org,email", [(ORG, BEN), (OTHER, ANA)])
    def test_a_non_owner_gets_404_for_no_byte_read(self, world, org, email):
        pid = _package(world, org=org, owner=email)
        _object(world, pid, org=org)
        body = _Body(world, fails=True)
        assert _direct(world, pid, "find", body) == (404, "Not found")
        assert body.reads == 0 and world.audits == []

    def test_a_bad_action_or_a_bad_app_id_reads_no_byte(self, world):
        pid, _, _ = _setup(world)
        body = _Body(world, fails=True)
        assert _direct(world, pid, "a" * 49, body) == (404, "Not found")
        with pytest.raises(HTTPException):
            asyncio.run(invoke.invoke_action("not-a-uuid", "find", request=body, user=UserContext(
                email=ANA, role=UserRole.EMPLOYEE, access=build_access(["feature:workflows"]),
                organization_id=ORG)))
        assert body.reads == 0

    @pytest.mark.parametrize("action,code", [
        ("add", "approval_required"), ("change", "approval_required"),
        ("remove", "approval_required"), ("flow", "approval_required"),
        ("pages", "source_not_open"), ("no_such_action", "not_declared"),
    ])
    def test_a_refused_action_reads_no_byte(self, world, action, code):
        pid, _, _ = _setup(world)
        body = _Body(world, fails=True)
        status, detail = _direct(world, pid, action, body)
        assert (status, detail["code"]) == (403, code)
        assert body.reads == 0

    @pytest.mark.parametrize("action", ["find", "peek"])
    def test_no_session_is_open_during_the_read(self, world, action):
        """A wait on a slow client must not hold a pool connection."""
        pid, _, rid = _setup(world)
        body = _Body(world, json.dumps({"id": rid} if action == "peek" else {}).encode())
        status, _ = _direct(world, pid, action, body)
        assert status == 200
        assert world.log == ["open", "close", "body", "open", "close", "audit"]
        assert body.reads == 1
        # The check runs again after the read, in the session of the executor.
        reads = [sql for sql, _ in world.statements if sql == packages_store._GET_SQL]
        assert len(reads) == 2

    def test_a_raw_fault_writes_no_audit_row(self, world):
        """The door serves or refuses each other call. A raw fault is neither,
        and the session is not safe to write on."""
        pid, _, _ = _setup(world)
        world.query_raises = RuntimeError("the statement failed")
        with pytest.raises(RuntimeError):
            _direct(world, pid, "find", _Body(world))
        assert world.audits == []


# ── The audit ──────────────────────────────────────────────────────────────


class TestTheAudit:
    def test_each_call_that_reaches_a_package_writes_one_row(self, world):
        pid, _, rid = _setup(world)
        c = _client(world)
        for action in ("peek", "add", "flow", "nope"):
            _call(c, pid, action, {"id": rid})
        assert [a["detail"]["action"] for a in world.audits] == ["peek", "add", "flow", "nope"]
        assert all(a["app_id"] == pid and a["kind"] == "action" for a in world.audits)
        details = [a["detail"] for a in world.audits]
        assert details[0] == {"action": "peek", "uses": "object:visit.get", "class": "A",
                              "outcome": "ran"}
        assert details[1] == {"action": "add", "uses": "object:visit.create", "class": "B",
                              "outcome": "refused", "reason": "approval_required"}
        assert details[3] == {"action": "nope", "uses": None, "class": None,
                              "outcome": "refused", "reason": "not_declared"}

    def test_the_row_holds_no_record_data(self, world):
        pid, _, rid = _setup(world)
        _call(_client(world), pid, "peek", {"id": rid})
        text = json.dumps(world.audits)
        for value in RECORD_DATA.values():
            assert value not in text

    def test_each_audit_text_is_capped(self):
        long = "a" * 20000
        out = invoke._Outcome(action=long, uses=long, cls=long, refusal="not_declared")
        detail = invoke._audit_detail(out)
        for key in ("action", "uses", "class"):
            assert len(detail[key]) == invoke.MAX_AUDIT_TEXT, key
        assert len(json.dumps(detail)) < 1000

    def test_the_audit_runs_after_the_session_closes(self, world):
        pid, _, rid = _setup(world)
        _call(_client(world), pid, "peek", {"id": rid})
        assert world.log == ["open", "close", "open", "close", "audit"]


# ── The tables ─────────────────────────────────────────────────────────────


class TestTheTables:
    def test_the_object_verb_table_holds_the_five_verbs_of_the_grammar(self):
        """The class table and the A2 ``uses`` grammar list the same verbs, in
        the same order. A verb in one and not the other fails here."""
        assert tuple(OBJECT_OP_CLASSES) == OBJECT_OPS
        assert len(OBJECT_OPS) == 5
        for verb in OBJECT_OPS:
            ref = parse_uses(f"object:visit.{verb}")
            assert ref is not None and (ref.source, ref.object, ref.op) == (
                "object", "visit", verb)
        for not_a_verb in ("count", "patch", "remove", "read"):
            assert parse_uses(f"object:visit.{not_a_verb}") is None

    def test_each_object_class_is_a_d136_class_and_none_is_x(self):
        assert set(OBJECT_OP_CLASSES.values()) <= set(CLASSES) - {"X"}
        assert OBJECT_OP_CLASSES == {"list": "A", "get": "A", "create": "B",
                                     "update": "B", "delete": "C"}

    def test_parse_uses_names_the_source_of_each_form(self):
        assert parse_uses(f"workflow:{WORKFLOW}").source == "workflow"
        assert parse_uses("integration:zoho.push").source == "integration"
        assert parse_uses("knowledge.list_pages").source == "manifest"
        for bad in ("", "object:visit.get\n", "workflow:not-a-uuid", None, 7):
            assert parse_uses(bad) is None

    def test_the_door_opens_the_object_get_and_the_object_list_only(self):
        assert set(invoke.OPEN_EXECUTORS) == {("object", "get"), ("object", "list")}

    def test_the_accepted_keys_are_the_properties_of_the_input_schema(self):
        assert set(list_input_schema()["properties"]) == invoke.LIST_INPUT_KEYS
        assert {"filter", "sort", "limit", "offset"} == invoke.LIST_INPUT_KEYS

    def test_the_refusal_text_names_each_open_verb(self):
        for _, verb in invoke.OPEN_EXECUTORS:
            assert str(verb) in invoke.REFUSALS["source_not_open"]

    def test_one_check_runs_twice(self):
        """One check before the body read and the same check after it. A
        second read of a package in this module is a second copy of the rule."""
        from pathlib import Path

        src = Path(invoke.__file__).read_text(encoding="utf-8")
        assert src.count("packages_store.get_package(") == 1
        assert src.count("await _check(") == 2
        assert src.count("_declared(package") == 1

    def test_each_refusal_is_one_short_sentence(self):
        assert set(invoke.REFUSALS) == {"not_declared", "class_x", "not_in_catalog",
                                        "approval_required", "source_not_open"}
        messages = {**invoke.REFUSALS, "catalog_unavailable": invoke.CATALOG_DOWN,
                    "object_missing": invoke.NO_OBJECT, "bad_input": invoke.BAD_RECORD_ID,
                    "bad_list": invoke.BAD_LIST_INPUT, "body_too_large": invoke.BODY_OVER_CAP}
        for code, message in messages.items():
            assert re.fullmatch(r"[a-z_]+", code)
            assert message.endswith(".") and message.count(". ") == 0, code
            assert len(message.split()) <= 25, code

    def test_the_test_manifest_is_valid(self):
        assert validate_manifest(MANIFEST) == []

    def test_the_r8_suite_is_named_in_the_ci_skip_guard(self):
        """A fence that never runs is no fence. The `--must-run` list names the
        pytest ARGUMENT, so a mention in a comment does not count."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
        block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
        assert block, "the --must-run argument list is missing"
        assert re.search(r"^[ \t]+tests/unit/test_studio_action_door_r8\.py(?: \\)?$",
                         block.group(1), re.M)


# ── The direct writes (STU-B2c-1) ──────────────────────────────────────────

import time  # noqa: E402

from acb_auth import AgentScope, direct_proof  # noqa: E402
from acb_common.studio_fields import FieldError, RecordError  # noqa: E402
from acb_common.studio_rules import (  # noqa: E402
    COMPUTED_FIELD,
    ConstraintViolation,
    FormulaError,
    MoveViolation,
    RuleViolation,
)

DIRECT_KEY = "d" * 40


@pytest.fixture
def dw(world, monkeypatch):
    """The world, with a usable direct key, and a fake ``apply_writes`` and
    audit writer that log into ``world.log`` in the order they run."""
    monkeypatch.setattr(get_settings(), "studio_direct_key", DIRECT_KEY)
    world.writes, world.audits_in, world.write_raises = [], [], None
    world.audit_in_raises, world.write_gate = None, None

    async def _apply(db, actor, *, package_id, writes):
        world.log.append("write")
        world.writes.append((db, actor, package_id, list(writes)))
        if world.write_gate is not None:
            await world.write_gate.wait()
        if world.write_raises is not None:
            raise world.write_raises
        step = writes[0]
        if step.op == "delete":
            return [True]
        return [objects_store.Record(str(step.record_id or uuid.uuid4()), step.object_id,
                                     dict(step.data or {}), actor, actor, NOW, NOW)]

    async def _audit_in(db, **kw):
        world.log.append("audit_in")
        world.audits_in.append((db, kw))
        if world.audit_in_raises is not None:
            raise world.audit_in_raises

    monkeypatch.setattr(objects_store, "apply_writes", _apply)
    monkeypatch.setattr(invoke, "record_app_audit_in", _audit_in)
    invoke._IN_FLIGHT.clear()
    yield world
    invoke._IN_FLIGHT.clear()


def _proof(pid: str, action: str, email: str = ANA, *, method: str = "POST",
           path: str | None = None, now: float | None = None, key: str = DIRECT_KEY) -> dict:
    where = path or f"/studio/apps/{pid}/actions/{action}"
    return {direct_proof.HEADER: direct_proof.sign(key, email, method, where, now=now)}


def _post(c: TestClient, pid: str, action: str, body: Any, headers: dict | None = None) -> Any:
    return c.post(f"/studio/apps/{pid}/actions/{action}", json=body, headers=headers or {})


def _signed_post(dw: Any, pid: str, action: str, body: Any, **over: Any) -> Any:
    return _post(_client(dw), pid, action, body, _proof(pid, action, **over))


BODIES = {
    "add": {"data": {"site": "Plant 9"}},
    "change": {"id": str(uuid.uuid4()), "data": {"notes": "Moved"}},
    "remove": {"id": str(uuid.uuid4())},
}


def _client_as(world: Any, user: UserContext, **kw: Any) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        world.org = ORG
        return user

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app, **kw)


class TestTheDirectWrites:
    @pytest.mark.parametrize("action", ["add", "change", "remove"])
    def test_a_direct_write_runs_and_audits_in_the_same_session(self, dw, action):
        pid, _, _ = _setup(dw)
        r = _signed_post(dw, pid, action, BODIES[action])
        assert r.status_code == 200, r.text
        assert dw.log == ["open", "close", "open", "write", "audit_in", "close"]
        (wdb, actor, package_id, writes), (adb, kw) = dw.writes[0], dw.audits_in[0]
        assert wdb is adb
        assert (actor, package_id, len(writes)) == (ANA, pid, 1)
        assert kw["detail"]["outcome"] == "ran" and kw["kind"] == "action"
        assert dw.audits == []  # the best-effort writer never ran
        assert "Plant 9" not in json.dumps(kw["detail"])

    def test_the_direct_write_and_its_audit_share_one_session(self, dw):
        pid, _, _ = _setup(dw)
        _signed_post(dw, pid, "add", BODIES["add"])
        assert dw.log.count("open") == 2  # the first check and the write session

    def test_create_update_and_delete_send_one_write_each(self, dw):
        pid, oid, _ = _setup(dw)
        rid = BODIES["change"]["id"]
        r1 = _signed_post(dw, pid, "add", BODIES["add"])
        r2 = _signed_post(dw, pid, "change", BODIES["change"])
        r3 = _signed_post(dw, pid, "remove", BODIES["remove"])
        assert [x.status_code for x in (r1, r2, r3)] == [200, 200, 200]
        ops = [w[3][0] for w in dw.writes]
        assert [(o.op, o.object_id) for o in ops] == [("create", oid), ("update", oid),
                                                      ("delete", oid)]
        assert ops[1].record_id == rid and ops[1].data == {"notes": "Moved"}
        assert r1.json()["record"]["data"] == {"site": "Plant 9"}
        assert r3.json() == {"deleted": True, "id": BODIES["remove"]["id"]}

    @pytest.mark.parametrize("action", ["add", "change", "remove"])
    @pytest.mark.parametrize("case", ["none", "bad", "old", "email", "path", "method"])
    def test_a_missing_or_wrong_proof_is_403_and_writes_nothing(self, dw, action, case):
        pid, _, _ = _setup(dw)
        headers = {
            "none": {},
            "bad": {direct_proof.HEADER: "v1.1790000000." + "a" * 64},
            "old": _proof(pid, action, now=time.time() - 120),
            "email": _proof(pid, action, email=BEN),
            "path": _proof(pid, action, path=f"/studio/apps/{pid}/actions/other"),
            "method": _proof(pid, action, method="GET"),
        }[case]
        r = _post(_client(dw), pid, action, BODIES[action], headers)
        assert (r.status_code, _code(r)) == (403, "approval_required")
        assert dw.writes == [] and dw.audits_in == []
        assert [a["detail"]["reason"] for a in dw.audits] == ["approval_required"]

    def test_a_proof_for_a_future_time_is_403(self, dw):
        pid, _, _ = _setup(dw)
        r = _signed_post(dw, pid, "add", BODIES["add"], now=time.time() + 60)
        assert (r.status_code, _code(r)) == (403, "approval_required")
        assert dw.writes == []

    def test_a_valid_proof_with_an_agent_scope_is_403(self, dw):
        pid, _, _ = _setup(dw)
        user = UserContext(email=ANA, role=UserRole.EMPLOYEE, organization_id=ORG,
                           access=build_access(["feature:workflows"]),
                           agent=AgentScope(connection_id="c1"))
        r = _post(_client_as(dw, user), pid, "add", BODIES["add"], _proof(pid, "add"))
        assert (r.status_code, _code(r)) == (403, "approval_required")
        assert dw.writes == []

    @pytest.mark.parametrize("email,role", [
        ("system:internal", UserRole.EMPLOYEE), ("ana@example.test", UserRole.AGENT),
        ("no-at-sign", UserRole.EMPLOYEE)])
    def test_a_system_or_agent_user_with_a_valid_proof_is_403(self, dw, email, role):
        pid = _package(dw, owner=email)  # the package is theirs, so only the proof rule stops them
        _object(dw, pid)
        user = UserContext(email=email, role=role, organization_id=ORG,
                           access=build_access(["feature:workflows"]))
        r = _post(_client_as(dw, user), pid, "add", BODIES["add"],
                  _proof(pid, "add", email=email))
        assert (r.status_code, _code(r)) == (403, "approval_required")
        assert dw.writes == []

    def test_an_unset_key_means_no_write_is_direct(self, dw, monkeypatch):
        pid, _, _ = _setup(dw)
        headers = _proof(pid, "add")
        for value in ("", "short", get_settings().gateway_internal_token):
            monkeypatch.setattr(get_settings(), "studio_direct_key", value)
            r = _post(_client(dw), pid, "add", BODIES["add"], headers)
            assert (r.status_code, _code(r)) == (403, "approval_required")
        assert dw.writes == []

    def test_another_member_and_org_b_get_404(self, dw):
        pid, _, _ = _setup(dw)
        r = _post(_client(dw, email=BEN), pid, "add", BODIES["add"],
                  _proof(pid, "add", email=BEN))
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        r = _post(_client(dw, org=OTHER), pid, "add", BODIES["add"], _proof(pid, "add"))
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert dw.writes == [] and dw.audits_in == []

    def test_the_switch_off_is_404_and_writes_nothing(self, dw, monkeypatch):
        pid, _, _ = _setup(dw)
        _open(monkeypatch, "")
        r = _signed_post(dw, pid, "add", BODIES["add"])
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert dw.writes == []

    def test_a_get_and_a_list_stay_open_without_a_proof(self, dw):
        pid, _, rid = _setup(dw)
        c = _client(dw)
        assert _call(c, pid, "peek", {"id": rid}).status_code == 200
        assert _call(c, pid, "find").status_code == 200

    @pytest.mark.parametrize("action,body", [
        ("add", {}), ("add", {"data": {}, "id": "x"}), ("add", {"data": []}),
        ("add", {"data": {}, "organization_id": ORG}), ("add", {"id": str(uuid.uuid4())}),
        ("change", {"data": {}}), ("change", {"id": str(uuid.uuid4())}),
        ("change", {"id": 7, "data": {}}),
        ("change", {"id": str(uuid.uuid4()), "data": {}, "package_id": "x"}),
        ("remove", {}), ("remove", {"id": str(uuid.uuid4()), "data": {}}),
        ("remove", {"id": ["x"]}),
    ])
    def test_a_body_with_other_keys_is_422_and_writes_nothing(self, dw, action, body):
        pid, _, _ = _setup(dw)
        r = _signed_post(dw, pid, action, body)
        assert r.status_code == 422
        assert r.json()["detail"] == {"code": "bad_input", "message": invoke.BAD_WRITE_INPUT,
                                      "rule": None, "fieldErrors": []}
        assert dw.writes == []
        assert dw.audits_in[0][1]["detail"]["reason"] == "bad_input"

    def test_a_body_that_is_not_json_is_422(self, dw):
        pid, _, _ = _setup(dw)
        r = _client(dw).post(f"/studio/apps/{pid}/actions/add", content="[1",
                             headers={**_proof(pid, "add"), "content-type": "application/json"})
        assert r.status_code == 422 and dw.writes == []

    def test_an_object_that_the_package_lacks_is_object_missing(self, dw):
        pid = _package(dw)
        r = _signed_post(dw, pid, "add", BODIES["add"])
        assert (r.status_code, _code(r)) == (404, "object_missing")
        assert dw.writes == []

    def test_a_body_past_the_cap_is_413_and_writes_nothing(self, dw):
        pid, _, _ = _setup(dw)
        big = {"data": {"notes": "x" * invoke.MAX_BODY_BYTES}}
        r = _signed_post(dw, pid, "add", big)
        assert (r.status_code, _code(r)) == (413, "body_too_large")
        assert dw.writes == []


def _batch(cause: Exception) -> objects_store.BatchError:
    return objects_store.BatchError(0, cause)


_CAUSES: list[tuple[Exception, int, str]] = [
    (FieldError("site", "Field `site` is too long."), 422, "field_invalid"),
    (ConstraintViolation("range", "visit", "Rule `range`: out of range."), 422, "rule_refused"),
    (MoveViolation("flow", "visit", "a", "b", "Rule `flow`: no such move."), 422, "rule_refused"),
    (FormulaError("sum", "visit", "div_zero", "Rule `sum` failed."), 422, "rule_refused"),
    (FormulaError("sum", "visit", COMPUTED_FIELD, "Field `total` is computed.", field="total"),
     422, COMPUTED_FIELD),
    (RuleViolation("any", "Rule `any` refused."), 422, "rule_refused"),
    (RecordError("The record is too large."), 422, "record_invalid"),
    (objects_store.BudgetExceeded("too many"), 422, "record_invalid"),
    (objects_store.ObjectStoreError("a bad id"), 422, "record_invalid"),
]


class TestTheDirectRefusals:
    @pytest.mark.parametrize("cause,status,code", _CAUSES,
                             ids=[f"{c[2]}-{i}" for i, c in enumerate(_CAUSES)])
    def test_each_refusal_maps_to_the_422_shape(self, dw, cause, status, code):
        pid, _, _ = _setup(dw)
        dw.write_raises = _batch(cause)
        r = _signed_post(dw, pid, "add", BODIES["add"])
        assert r.status_code == status
        detail = r.json()["detail"]
        assert set(detail) == {"code", "message", "rule", "fieldErrors"}
        assert detail["code"] == code and isinstance(detail["message"], str)
        if isinstance(cause, RuleViolation):
            assert detail["rule"] == cause.rule
        if isinstance(cause, FieldError):
            assert detail["fieldErrors"] == [{"field": "site", "message": cause.message}]
        if code == COMPUTED_FIELD:
            assert detail["fieldErrors"][0]["field"] == "total"
        # The session leaves cleanly, and the audit row is written first.
        assert dw.log[-3:] == ["write", "audit_in", "close"]
        assert dw.audits_in[0][1]["detail"] == {
            "action": "add", "uses": "object:visit.create", "class": "B",
            "outcome": "refused", "reason": code}

    def test_a_refusal_raised_before_a_batch_maps_too(self, dw):
        pid, _, _ = _setup(dw)
        dw.write_raises = objects_store.ObjectStoreError("record_id must be a UUID")
        r = _signed_post(dw, pid, "change", BODIES["change"])
        assert (r.status_code, _code(r)) == (422, "record_invalid")

    def test_store_busy_is_503_before_object_store_error(self, dw):
        pid, _, _ = _setup(dw)
        assert issubclass(objects_store.StoreBusy, objects_store.ObjectStoreError)
        dw.write_raises = _batch(objects_store.StoreBusy("busy"))
        r = _signed_post(dw, pid, "add", BODIES["add"])
        assert r.status_code == 503 and r.headers["Retry-After"] == "1"
        assert _code(r) == "store_busy"
        assert dw.audits_in[0][1]["detail"]["outcome"] == "failed"

    def test_a_record_that_is_not_there_is_404(self, dw):
        pid, _, _ = _setup(dw)
        dw.write_raises = _batch(objects_store.RecordNotFound("none"))
        r = _signed_post(dw, pid, "remove", BODIES["remove"])
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert dw.audits_in[0][1]["detail"]["outcome"] == "not_found"

    def test_a_rule_check_timeout_is_503_with_retry_after_2(self, dw):
        pid, _, _ = _setup(dw)
        dw.write_raises = _batch(QueryError(studio_query.TIMEOUT, "took too long"))
        r = _signed_post(dw, pid, "change", BODIES["change"])
        assert r.status_code == 503 and r.headers["Retry-After"] == "2"

    def test_a_raw_fault_is_500_and_writes_no_audit_row(self, dw):
        pid, _, _ = _setup(dw)
        dw.write_raises = _batch(RuntimeError("the statement failed"))
        c = TestClient(_client(dw).app, raise_server_exceptions=False)
        r = _post(c, pid, "add", BODIES["add"], _proof(pid, "add"))
        assert r.status_code == 500
        assert dw.audits_in == [] and dw.audits == []

    def test_a_failed_audit_fails_the_call(self, dw):
        pid, _, _ = _setup(dw)
        dw.audit_in_raises = RuntimeError("the audit insert failed")
        c = TestClient(_client(dw).app, raise_server_exceptions=False)
        r = _post(c, pid, "add", BODIES["add"], _proof(pid, "add"))
        assert r.status_code == 500
        assert dw.log[-3:] == ["write", "audit_in", "close"]
        assert dw.audits == []

    def test_the_refusal_is_raised_after_the_session_closes(self, dw, monkeypatch):
        """An HTTPException inside the block would roll back the audit row."""
        pid, _, _ = _setup(dw)
        dw.write_raises = _batch(FieldError("site", "Field `site` is too long."))
        seen: list[str] = []
        real = invoke._answer

        def spy(out):
            seen.append(dw.log[-1])
            return real(out)

        monkeypatch.setattr(invoke, "_answer", spy)
        _signed_post(dw, pid, "add", BODIES["add"])
        assert seen == ["close"]


def _handler_call(dw: Any, pid: str, action: str, body: Any) -> Any:
    """The handler with a signed request, for the tests that run many calls
    in one event loop. Answers the status, the answer and the headers."""
    raw = json.dumps(body).encode()
    path = f"/studio/apps/{pid}/actions/{action}"
    headers = _proof(pid, action)

    class _Req:
        method = "POST"
        url = SimpleNamespace(path=path)

        async def stream(self):
            yield raw

    _Req.headers = headers  # type: ignore[attr-defined]
    user = UserContext(email=ANA, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access(["feature:workflows"]))

    async def go() -> tuple[int, Any, Any]:
        try:
            return 200, await invoke.invoke_action(pid, action, request=_Req(), user=user), None
        except HTTPException as exc:
            return exc.status_code, exc.detail, exc.headers

    return go()


class TestTheWriterCap:
    def test_the_fourth_writer_of_an_org_is_503_and_holds_no_session(self, dw):
        pid, _, _ = _setup(dw)
        dw.org = ORG

        async def run() -> list[Any]:
            dw.write_gate = asyncio.Event()
            tasks = [asyncio.ensure_future(_handler_call(dw, pid, "add", BODIES["add"]))
                     for _ in range(3)]
            for _ in range(50):
                await asyncio.sleep(0)
            assert invoke._IN_FLIGHT == {ORG: 3}
            opened = dw.log.count("open")
            # A door with no cap would wait on the gate for ever, so the wait is bounded.
            fourth = await asyncio.wait_for(_handler_call(dw, pid, "add", BODIES["add"]), 5)
            assert dw.log.count("open") == opened + 1  # only its first check
            dw.write_gate.set()
            return [fourth, *await asyncio.gather(*tasks)]

        fourth, *rest = asyncio.run(run())
        assert fourth[0] == 503 and fourth[1]["code"] == "store_busy"
        assert fourth[2] == {"Retry-After": "1"}
        assert [r[0] for r in rest] == [200, 200, 200]
        assert invoke._IN_FLIGHT == {}
        assert len(dw.audits_in) == 3  # the busy call wrote no row

    def test_the_slot_goes_back_after_a_fault_and_an_empty_org_has_no_entry(self):
        invoke._IN_FLIGHT.clear()
        with pytest.raises(RuntimeError), invoke._writer_slot("o", True) as held:
            assert held and invoke._IN_FLIGHT == {"o": 1}
            raise RuntimeError("fault")
        assert invoke._IN_FLIGHT == {}

    def test_two_full_orgs_leave_a_third_org_its_first_writer_and_a_read_takes_no_slot(self):
        """Fix round 1: the total cap binds only the second and later writers
        of an org, so two busy orgs cannot shut out every other org."""
        invoke._IN_FLIGHT.clear()
        with contextlib.ExitStack() as stack:
            held = [stack.enter_context(invoke._writer_slot(f"o{i // 3}", True))
                    for i in range(6)]
            assert held == [True] * 6 and invoke._IN_FLIGHT == {"o0": 3, "o1": 3}
            with invoke._writer_slot("o9", True) as first:
                assert first is True and invoke._IN_FLIGHT["o9"] == 1
                with invoke._writer_slot("o9", True) as second:
                    assert second is False
            with invoke._writer_slot("o0", True) as fourth:
                assert fourth is False
            with invoke._writer_slot("o0", False) as read:
                assert read is False
            assert invoke._IN_FLIGHT == {"o0": 3, "o1": 3}
        assert invoke._IN_FLIGHT == {}

    def test_many_first_writers_pass_the_total_and_then_no_second_writer_does(self):
        invoke._IN_FLIGHT.clear()
        with contextlib.ExitStack() as stack:
            firsts = [stack.enter_context(invoke._writer_slot(f"o{i}", True))
                      for i in range(invoke.WRITERS_TOTAL + 2)]
            assert firsts == [True] * (invoke.WRITERS_TOTAL + 2)
            with invoke._writer_slot("o0", True) as second:
                assert second is False
        assert invoke._IN_FLIGHT == {}

    def test_a_second_writer_counts_against_the_total(self):
        invoke._IN_FLIGHT.clear()
        with contextlib.ExitStack() as stack:
            held = [stack.enter_context(invoke._writer_slot(org, True))
                    for org in ("o0", "o1", "o2", "o0", "o1", "o2")]
            assert held == [True] * 6
            with invoke._writer_slot("o0", True) as third:
                assert third is False  # 6 in flight, and o0 has one already
        assert invoke._IN_FLIGHT == {}

    def test_two_full_orgs_leave_the_first_write_of_a_third_org_a_slot(self, dw):
        """Through the handler: the first write of the org runs, and a second
        write of the same org at that time gets 503 at once."""
        pid, _, _ = _setup(dw)
        dw.org = ORG
        busy = {"org-x": invoke.WRITERS_PER_ORG, "org-y": invoke.WRITERS_PER_ORG}
        invoke._IN_FLIGHT.update(busy)

        async def run() -> tuple[Any, Any, dict[str, int]]:
            dw.write_gate = asyncio.Event()
            first = asyncio.ensure_future(_handler_call(dw, pid, "add", BODIES["add"]))
            for _ in range(50):
                await asyncio.sleep(0)
            during = dict(invoke._IN_FLIGHT)
            second = await asyncio.wait_for(_handler_call(dw, pid, "add", BODIES["add"]), 5)
            dw.write_gate.set()
            return await first, second, during

        first, second, during = asyncio.run(run())
        assert during == {**busy, ORG: 1}
        assert first[0] == 200
        assert (second[0], second[1]["code"], second[2]) == (
            503, "store_busy", {"Retry-After": "1"})
        assert busy == invoke._IN_FLIGHT
