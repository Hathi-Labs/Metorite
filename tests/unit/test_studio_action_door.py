"""The action door of AI Studio, the hermetic half (STU-B2a).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 and §14 B2a. Code:
``gateway/routes/studio/invoke.py``. The R8 half, on a real Postgres, is
``test_studio_action_door_r8.py``. It proves the walls of the org, of the
member and of the package, and the audit row.

Here the real stores run on a fake session. The fake answers the four
statements that the door runs (``packages_store._GET_SQL``,
``objects_store._OBJECT_BY_NAME_SQL``, ``_FIELDS_SQL`` and
``_GET_RECORD_SQL``), and each one filters on the bound org, as RLS does. It
is patched at ``invoke._tenant_session``. The catalog read and the audit
writer are patched too.

This file proves:

- the switch: a not-listed org gets 404, and opens no session;
- the order of the checks: the switch, the package, the declaration, the
  class, then the executor;
- the org and the member come from the session, never from the request;
- each refusal code, and that a refused call reads no record;
- the one executor, ``object:<name>.get``, reads with the package id;
- one audit row for each call that reaches a package, with no record data;
- the object verb table holds the five verbs of the A2 grammar.

**The mutations (R7).** The R8 suite holds the full list and each result.
These turn a test of this file red:

* M1. Drop the switch check. ``test_a_not_listed_org_gets_404_and_opens_no_session``.
* M3. Skip the declaration check. ``test_an_undeclared_action_is_403_not_declared``.
* M4. Drop the class X refusal. ``test_a_class_x_row_is_403_class_x``.
* M5. Drop the class B and C refusal. ``test_each_write_verb_is_403_approval_required``.
* M6a. Take the org from the body. ``test_the_session_takes_no_org_from_the_request``.
* M7b. Call ``get_record`` with no ``package_id``. ``test_a_get_reads_with_the_package_id``.
* M8. 403 for a package of another member. ``test_a_package_of_another_member_or_org_is_404``.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.app_manifests.base import CLASSES
from acb_common.studio_manifest import (
    OBJECT_OP_CLASSES,
    OBJECT_OPS,
    parse_uses,
    validate_manifest,
)
from fastapi import FastAPI
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
                    return _Result([(o["id"], o["package"], o["name"], None, ANA, NOW, NOW)])
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
                        log=[], session_args=[], audits=[], catalog=list(CATALOG))

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
        return list(w.catalog)

    async def _audit(**kw: Any) -> None:
        w.log.append("audit")
        w.audits.append(kw)

    monkeypatch.setattr(invoke, "_tenant_session", _session)
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
        assert world.session_args == [()]
        reads = [p for sql, p in world.statements if sql == packages_store._GET_SQL]
        assert reads == [{"m": ANA, "id": pid}]
        assert world.audits[0]["user_email"] == ANA


# ── The declaration and the class ──────────────────────────────────────────


class TestTheChecks:
    def test_an_undeclared_action_is_403_not_declared(self, world):
        pid, _, rid = _setup(world)
        for action in ("peek_all", "object:visit.get", "visit.get"):
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

    def test_a_list_is_class_a_with_no_executor(self, world):
        pid, _, _ = _setup(world)
        r = _call(_client(world), pid, "find", {})
        assert (r.status_code, _code(r)) == (403, "source_not_open")
        assert world.audits[0]["detail"]["class"] == "A"

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
        _call(_client(world), pid, "peek", {"id": rid})
        by_name = [p for sql, p in world.statements
                   if sql == objects_store._OBJECT_BY_NAME_SQL]
        assert by_name == [{"pid": pid, "name": "visit"}]
        assert _record_reads(world) == [{"oid": oid, "rid": rid, "pid": pid}]

    def test_a_record_of_another_package_is_404(self, world):
        mine = _package(world)
        _object(world, mine)
        other = _package(world)
        theirs = _record(world, _object(world, other))
        r = _call(_client(world), mine, "peek", {"id": theirs})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert world.audits[0]["detail"]["outcome"] == "not_found"

    def test_an_object_that_the_registry_lacks_is_404(self, world):
        pid = _package(world)
        r = _call(_client(world), pid, "peek", {"id": str(uuid.uuid4())})
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
        assert _record_reads(world) == []

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

    def test_a_body_too_deep_to_parse_is_never_500(self, world):
        pid, _, _ = _setup(world)
        deep = "[" * 20000 + "]" * 20000
        r = _client(world).post(f"/studio/apps/{pid}/actions/peek", content=deep,
                                headers={"content-type": "application/json"})
        assert r.status_code in (400, 422), r.text


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

    def test_the_audit_runs_after_the_session_closes(self, world):
        pid, _, rid = _setup(world)
        _call(_client(world), pid, "peek", {"id": rid})
        assert world.log == ["open", "close", "audit"]


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

    def test_b2a_opens_one_executor(self):
        assert {("object", "get")} == set(invoke.OPEN_EXECUTORS)

    def test_each_refusal_is_one_short_sentence(self):
        assert set(invoke.REFUSALS) == {"not_declared", "class_x", "not_in_catalog",
                                        "approval_required", "source_not_open"}
        for code, message in invoke.REFUSALS.items():
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
