"""The action door of AI Studio on a real database (STU-B2a, B2b-1, B2c-1 and B2c-2a, R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.2, and §14 B2a, B2b-1, B2c-1 and B2c-2a. Code:
``gateway/routes/studio/invoke.py`` over ``gateway/studio/packages_store.py``
(A3) and ``gateway/studio/objects_store.py`` (C1). The hermetic half is
``test_studio_action_door.py``.

The real handler runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Each call binds its org as the
auth layer does, and the handler opens ``tenant_session()`` with no org. The
packages are made by the A3 store, and the objects and records by the C1
store, each as the app role.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_action_door_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (B2a).**

a. The owner of a package runs its ``object:<name>.get`` action, and gets the
   record as C1 returns it. One ``app_audit`` row lands in the caller's org.
b. Another member of the org, and org B with the same email, get 404
   ``Not found`` on each action. No audit row names the package.
c. An action that the manifest does not declare gets 403 ``not_declared``.
d. Class B and class C get 403 ``approval_required``, and nothing changes.
   A workflow of another org is not in the catalog of this org.
e. A class A action with no executor gets 403 ``source_not_open``.
f. An App Workshop app is not a package, so its id is 404.
g. A record of another package of the same member is 404.
h. A body that names another org or another package changes nothing.
i. With the switch off, each call is 404 and writes no audit row.
j. An action that is not an A2 action id is 404, and writes no row.
k. An object that the package lacks is 404 ``object_missing``, and a record
   id that names nothing is 404 ``Not found``. The audit row tells them apart.
l. A catalog that fails is 503 ``catalog_unavailable``, and the row lands.

**Acceptance (B2b-1).**

m. The owner lists a filtered, sorted page with the right total, and each call
   writes one audit row in the bound org. The row holds no record data.
n. Another member and org B get 404 ``Not found``, and no row names the
   package.
o. An object of another package gets 404 ``object_missing``, also when the
   door holds a true object id. Two packages with one object name each read
   their own records.
p. The five refused codes get 422 ``{code, message, field}``, and the real
   ``query_timeout`` gets 503 with ``Retry-After: 2``.
q. An extra key, ``limit: true`` and ``limit: Infinity`` get 422 and read
   nothing.
r. A value that Postgres refuses (a NUL, a lone surrogate) gets 422, never 500.
s. After each refusal the door session runs a statement, and the audit row
   lands. A real timeout changes nothing for the next call.
t. A body past the cap gets 413 and one audit row. A non-owner and an org
   outside the switch read no byte. No connection of the app role is in a
   transaction during the read.
u. A change during the read is seen. A package that goes and an owner that
   changes get 404. A declaration that changes gets 403. Neither reads a
   record.

**The mutations (R7).** Commit before each, and restore with git after.
Each result below was run on 2026-11-01, with both B2a suites.

* M1. ``invoke.invoke_action``: drop the switch check.
  ``test_r8_the_switch_off_is_404_and_writes_no_row`` and three switch tests
  of the hermetic half go red.
* M2. ``packages_store._GET_SQL``: drop ``AND owner_email = :m``.
  ``test_r8_another_member_of_the_org_gets_404`` goes red. The fake session
  of the hermetic half filters on the member itself, so only R8 sees it.
* M3. ``invoke._declared``: answer the first entry for each id, so the
  declaration check passes each action.
  ``test_r8_an_undeclared_action_is_403_and_audited`` and
  ``test_an_undeclared_action_is_403_not_declared`` go red.
* M4. ``invoke.refusal_for``: drop the class X refusal.
  ``test_a_class_x_row_is_403_class_x`` of the hermetic half goes red. No
  real catalog row is class X, so no R8 test can see it.
* M5. ``invoke.refusal_for``: drop the class B and C refusal.
  ``test_r8_each_write_verb_writes_nothing``,
  ``test_r8_a_source_that_is_not_an_object_never_runs`` and eight hermetic
  tests go red.
* M6a. ``invoke.invoke_action``: open ``_tenant_session`` with the
  ``organization_id`` of the body.
  ``test_r8_a_body_that_names_another_org_changes_nothing`` and
  ``test_the_session_takes_no_org_from_the_request`` go red.
* M6b. ``invoke.invoke_action``: open an unbound ``get_db()`` session. Seven
  tests of this suite go red, the owner test first. (The hermetic half
  hangs on a real engine, so run this one on R8 only.)
* M7a. ``objects_store._GET_RECORD_SQL``: drop ``AND {_IN_PACKAGE}``. No
  test of the door goes red, because the door reads the object by its name
  in the package first. C1's
  ``test_r8_a_record_call_names_its_package_and_its_object`` goes red.
* M7b. ``invoke._run_object_get``: take the package id of the object read
  and the record read from the body key ``package_id``, the id of ANOTHER
  package of the same member. ``test_r8_a_record_of_another_package_is_404``
  and ``test_a_get_reads_with_the_package_id`` go red: the door reads the
  record of the other package.
* M10 (fix round 1). ``invoke.invoke_action``: parse the body before the
  switch check. ``test_a_malformed_body_from_an_org_outside_the_switch_is_404``
  of the hermetic half goes red.
* M11 (fix round 1). ``invoke.invoke_action``: drop the ``try`` around the
  catalog read. ``test_r8_a_catalog_that_fails_is_503_and_audited`` and
  ``test_a_catalog_that_fails_is_503_and_audited`` go red.
* M8. ``invoke.invoke_action``: answer 403 for a package that
  ``get_package`` does not find. ``test_r8_another_member_of_the_org_gets_404``,
  ``test_r8_org_b_gets_404_also_with_the_same_email``,
  ``test_r8_a_code_app_is_404`` and four hermetic tests go red.

**The mutations of B2b-1.** Each result below was run on 2026-11-02, on both
door suites and ``test_one_writer_of_the_four_tables``.

* L1. ``OPEN_EXECUTORS``: drop the list entry. 76 tests go red, first
  ``test_a_list_is_class_a_and_it_runs``.
* L2. ``_run_object_list``: drop the ``QueryError`` catch. 62 tests go red,
  among them ``test_r8_each_refused_query_is_422_and_the_session_stays_usable``
  and ``test_each_refused_query_is_422_with_its_code``.
* L3. ``_query_refusal``: answer 422 for a timeout. ``test_a_query_timeout_is_503_with_retry_after``
  and ``test_r8_a_real_timeout_is_503_with_retry_after_and_the_session_stays_usable``
  go red.
* L4. ``_list_input``: skip the key check. 13 tests go red, among them
  ``test_a_key_outside_the_schema_is_422_and_reads_nothing`` and
  ``test_r8_a_refused_body_is_422_and_reads_nothing``.
* L5. ``_run_object_list``: pass ``obj.package_id`` to the store in place of
  the package of the path. Only
  ``test_r8_a_true_object_id_of_another_package_is_object_missing`` goes red,
  because the fake of the hermetic half cannot tell the two apart.
* L6a. ``invoke_action``: open the session with the ``organization_id`` of the
  body. ``test_the_session_takes_no_org_or_package_from_the_request``,
  ``test_r8_a_refused_body_is_422_and_reads_nothing`` and two B2a tests go red.
* L6b. Add ``package_id`` to the accepted keys, and pass it to the store.
  ``test_the_accepted_keys_are_the_properties_of_the_input_schema`` and two
  refused-body tests go red. With the key check in place, a body cannot name
  a package at all, so no narrower mutation reaches the store.
* L7. ``_query_refusal``: drop the ``Retry-After`` header. The two tests of L3
  go red.
* L8. ``_list_input``: use ``isinstance`` for the int check, so a bool passes.
  Two ``limit`` and ``offset`` tests of each suite go red.
* L9. ``_list_input``: drop the int check. 18 tests go red, among them
  ``test_a_limit_or_offset_that_is_not_an_int_is_422_and_reads_nothing``.
* L10. ``_run_object_list``: treat None from the store as a page.
  ``test_none_from_the_store_is_404_object_missing`` and
  ``test_r8_a_true_object_id_of_another_package_is_object_missing`` go red.
* L11. ``_run_object_list``: check the body after the object read. 37 tests go
  red, because a refused body then reads the object.
* L12. ``_run_object_list``: drop the ``ObjectStoreError`` catch.
  ``test_an_id_that_the_store_refuses_is_404_object_missing`` goes red.
* L13. ``objects_store._time_limit``: drop the SAVEPOINT. Only
  ``test_r8_a_real_timeout_is_503_with_retry_after_and_the_session_stays_usable``
  goes red. So that test holds a real cancel by Postgres.
* L14. ``REFUSALS``: put back the old text.
  ``test_the_refusal_text_names_each_open_verb`` goes red.
* L15. ``invoke.py``: name ``studio_records`` in a SQL text.
  ``test_one_writer_of_the_four_tables`` goes red.

**The mutations of the fix round.** Each result below was run on 2026-11-02,
on both door suites. F4, F5a and F5b are the moves that an earlier review saw
pass all 162 tests.

* F1. ``_read_body``: drop the cap.
  ``test_a_body_past_the_cap_is_413_and_the_read_stops_near_the_cap`` and
  ``test_r8_a_body_past_the_cap_is_413_and_one_audit_row`` go red.
* F2. ``_read_body``: read to the end, then refuse. Only
  ``test_a_body_past_the_cap_is_413_and_the_read_stops_near_the_cap`` goes red.
* F3. ``_read_body``: refuse a body at the cap.
  ``test_a_body_at_the_cap_runs_and_one_byte_more_is_413`` goes red.
* F4. ``invoke_action``: read before the switch. 14 tests go red, among them
  ``test_a_read_that_fails_with_the_switch_off_is_404_and_never_runs`` and
  ``test_r8_the_switch_off_reads_no_byte``.
* F5a. ``_run_executor``: read inside the session of the executor.
  ``test_no_session_is_open_during_the_read`` and
  ``test_r8_no_connection_is_in_a_transaction_during_the_read`` go red.
* F5b. ``invoke_action``: read inside the session of the package. 13 tests go
  red, among them the two tests of F5a.
* F6. ``invoke_action``: read after the app id and before the package. 11 tests
  go red, among them ``test_a_non_owner_gets_404_for_no_byte_read`` and
  ``test_r8_a_non_owner_reads_no_byte``.
* F7. ``_read_body``: call ``request.body()``. 39 tests go red.
* F8. ``_audit_detail``: drop the branch of a 413. The two tests of F1 go red.
* F9. ``_run_object_list``: drop the log line.
  ``test_an_id_that_the_store_refuses_is_404_object_missing`` goes red.
* F10. ``invoke_action``: write an audit row on a raw fault.
  ``test_a_raw_fault_writes_no_audit_row`` goes red.
* F11. ``_json_object``: catch only ``JSONDecodeError``.
  ``test_a_body_that_is_not_a_json_object_is_422_never_500`` and
  ``test_a_number_too_long_to_parse_is_422_never_500`` go red.

**The mutations of the second check.** Each result below was run on 2026-11-02,
on both door suites.

* G1. ``_run_executor``: use the package and the reference of the first check.
  Each case of ``test_r8_a_change_during_the_read_is_seen_and_reads_no_record``
  and ``test_one_check_runs_twice`` go red.
* G2. ``_run_executor``: drop the check of the source. The two ``now_door_op``
  cases of that test go red.
* G3. ``_check``: do not clear the outcome first. The two ``removed`` cases of
  that test go red, because the row keeps the old ``uses``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings, studio_query
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.studio_query import QueryError
from fastapi import HTTPException
from gateway.routes.studio import invoke
from gateway.studio import objects_store, packages_store
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stub2a.test"
BEN = f"ben.{TAG}@stub2a.test"
NOT_FOUND = "Not found"

_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
    {"key": "notes", "type": "text"},
]
_DATA = {"site": "Plant 4, Hosur", "status": "planned", "notes": "Bring the nozzle kit"}


def _manifest(mid: str, wf_a: str, wf_b: str) -> dict[str, Any]:
    return {
        "studio": 1,
        "id": mid,
        "name": "Visits",
        "purpose": "Track install visits.",
        "objects": [{"name": "visit", "fields": _FIELDS}],
        "actions": [
            {"id": "peek", "uses": "object:visit.get"},
            {"id": "find", "uses": "object:visit.list"},
            {"id": "add", "uses": "object:visit.create", "set": {"status": "planned"}},
            {"id": "change", "uses": "object:visit.update", "set": {"status": "done"}},
            {"id": "remove", "uses": "object:visit.delete"},
            {"id": "flow", "uses": f"workflow:{wf_a}"},
            {"id": "flow_b", "uses": f"workflow:{wf_b}"},
            {"id": "pages", "uses": "knowledge.list_pages"},
            {"id": "propose", "uses": "knowledge.propose"},
        ],
    }


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Request:
    """The one part of a request that the handler reads: the stream of the
    body. *on_read* runs at the read, and it may be async. *fails* makes the
    read fail."""

    def __init__(self, payload: Any, on_read: Callable[[], Any] | None = None,
                 fails: bool = False, headers: dict[str, str] | None = None,
                 path: str = "/studio/apps/x/actions/y",
                 query: dict[str, str] | None = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        # The door reads the method, the path and the headers for the proof.
        self.method, self.headers = "POST", dict(headers or {})
        self.url = SimpleNamespace(path=path)
        # A delegated call reads ``request_id`` from the query (B2c-2a).
        self.query_params = dict(query or {})
        self._on_read = on_read
        self._fails = fails
        self.reads = 0

    async def stream(self) -> Any:
        self.reads += 1
        if self._fails:
            raise OSError("the read of the body failed")
        if self._on_read is not None:
            done = self._on_read()
            if inspect.isawaitable(done):
                await done
        yield self._raw


async def _call(org: str, email: str, pid: str, action: str, payload: Any = None,
                request: _Request | None = None) -> tuple[int, Any]:
    """Call the real handler with *org* bound, as the auth layer binds it.
    Answers the status and the answer, or the detail of a refusal."""
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=request or _Request(payload),
                                           user=_user(org, email))
        return 200, found
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _refusal(org: str, email: str, pid: str, action: str,
                   payload: Any = None) -> HTTPException:
    """The refusal of the handler, whole, so a test can read its headers."""
    token = bind_tenant(org)
    try:
        with pytest.raises(HTTPException) as refused:
            await invoke.invoke_action(pid, action, request=_Request(payload),
                                       user=_user(org, email))
        return refused.value
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _package(org: str, member: str, manifest: dict[str, Any]) -> str:
    async with tenant_session(org) as db:
        made = await packages_store.create_package(db, member, manifest)
    return made.id


async def _object_with_record(org: str, pid: str) -> tuple[str, str]:
    async with tenant_session(org) as db:
        obj = await objects_store.define_object(db, ANA, package_id=pid, name="visit",
                                                fields=_FIELDS)
        assert obj is not None
        rec = await objects_store.create_record(db, ANA, obj.id, _DATA, package_id=pid)
        assert rec is not None
    return obj.id, rec.id


def _seed_workflow(p: Any, org: str) -> str:
    wid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO workflows (id, name, owner_email, status, organization_id) "
            "VALUES (:id, :n, :o, 'published', :org)"),
            {"id": wid, "n": f"Flow {TAG}", "o": ANA, "org": org})
    return wid


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Four packages and one code app. Org A: two packages of Ana, one of Ben,
    and a code app of Ana. Org B: one package of a member with Ana's email.
    A published workflow in each org."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    run = uuid.uuid4().hex[:6]
    mid = f"door-{TAG}-{run}"
    wf_a, wf_b = _seed_workflow(p, p.org_a), _seed_workflow(p, p.org_b)
    manifest = _manifest(mid, wf_a, wf_b)
    with p.admin_engine.begin() as c:
        code_app = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id) "
            "VALUES (:s, 'stub2a', :oe, '/tmp/stub2a', CAST(:o AS uuid)) RETURNING id"),
            {"s": f"{mid}-code", "oe": ANA, "o": p.org_a}).scalar_one())
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, code_app=code_app, wf_a=wf_a, wf_b=wf_b)

    async def setup() -> None:
        w.pkg = await _package(w.a, ANA, manifest)
        w.pkg2 = await _package(w.a, ANA, manifest)
        w.pkg_ben = await _package(w.a, BEN, manifest)
        w.pkg_b = await _package(w.b, ANA, manifest)
        # A package of Ana with no object in the registry.
        w.pkg_empty = await _package(w.a, ANA, manifest)
        w.obj, w.rec = await _object_with_record(w.a, w.pkg)
        w.obj2, w.rec2 = await _object_with_record(w.a, w.pkg2)
        w.obj_ben, w.rec_ben = await _object_with_record(w.a, w.pkg_ben)
        w.obj_b, w.rec_b = await _object_with_record(w.b, w.pkg_b)

    _run(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        ids = [w.pkg, w.pkg2, w.pkg_ben, w.pkg_b, w.pkg_empty, w.code_app]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"{mid}-%"})
        c.execute(text("DELETE FROM workflows WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": wf_a, "b": wf_b})


def _audits(world: Any, pid: str) -> list[tuple[str, str, str, dict[str, Any]]]:
    """The audit rows of *pid*, read as the superuser, oldest first."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, user_email, kind, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY id"), {"p": pid}).all()
    out = []
    for org, email, kind, detail in rows:
        out.append((org, email, kind, detail if isinstance(detail, dict) else json.loads(detail)))
    return out


def _record_row(world: Any, rid: str) -> tuple[Any, ...] | None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT data, updated_by, updated_at FROM studio_records "
            "WHERE id = CAST(:r AS uuid)"), {"r": rid}).first()
    return None if row is None else tuple(row)


def _record_count(world: Any, oid: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_records WHERE object_id = CAST(:o AS uuid)"),
            {"o": oid}).scalar_one())


_EVERY_ACTION = ("peek", "find", "add", "change", "remove", "flow", "flow_b", "pages",
                 "propose", "no_such_action")


# ── a. The owner runs a get ────────────────────────────────────────────────


def test_r8_the_owner_gets_the_record_and_one_audit_row(world) -> None:
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, "peek", {"id": world.rec}))
    assert status == 200, got
    rec = got["record"]
    assert (rec["id"], rec["object_id"], rec["data"]) == (world.rec, world.obj, _DATA)
    assert (rec["created_by"], rec["updated_by"]) == (ANA, ANA)
    rows = _audits(world, world.pkg)
    assert rows == [(world.a, ANA, "action", {
        "action": "peek", "uses": "object:visit.get", "class": "A", "outcome": "ran"})]
    # The row holds no record data.
    for value in _DATA.values():
        assert value not in json.dumps(rows[0][3])


# ── b. The walls of the member and the org ─────────────────────────────────


def test_r8_another_member_of_the_org_gets_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, BEN, world.pkg, a, {"id": world.rec})
                for a in _EVERY_ACTION]

    assert _run(world, go) == [(404, NOT_FOUND)] * len(_EVERY_ACTION)
    assert _audits(world, world.pkg) == []
    # Ben's own package answers Ben, so the wall is the owner, not the org.
    status, got = _run(world, lambda: _call(world.a, BEN, world.pkg_ben, "peek",
                                            {"id": world.rec_ben}))
    assert status == 200 and got["record"]["id"] == world.rec_ben


def test_r8_org_b_gets_404_also_with_the_same_email(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        out = [await _call(world.b, ANA, world.pkg, a, {"id": world.rec})
               for a in _EVERY_ACTION]
        # Org B's own record by its own id, through org A's package.
        out.append(await _call(world.b, ANA, world.pkg, "peek", {"id": world.rec_b}))
        return out

    assert _run(world, go) == [(404, NOT_FOUND)] * (len(_EVERY_ACTION) + 1)
    assert _audits(world, world.pkg) == []
    # An audit row of org B never names org A's package.
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM app_audit WHERE organization_id = CAST(:b AS uuid) "
            "AND app_id = CAST(:p AS uuid)"), {"b": world.b, "p": world.pkg}).scalar_one() == 0


# ── c. The declaration ─────────────────────────────────────────────────────


def test_r8_an_undeclared_action_is_403_and_audited(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("peek_all", "peek2")]

    for status, detail in _run(world, go):
        assert (status, detail["code"]) == (403, "not_declared")
    rows = _audits(world, world.pkg)
    assert [r[3]["reason"] for r in rows] == ["not_declared", "not_declared"]
    assert all(r[3]["outcome"] == "refused" and r[3]["class"] is None for r in rows)


# ── d. Class B and C write nothing ─────────────────────────────────────────


def test_r8_each_write_verb_writes_nothing(world) -> None:
    before = _record_row(world, world.rec)
    count = _record_count(world, world.obj)
    body = {"id": world.rec, "site": "Changed", "status": "done"}

    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, body)
                for a in ("add", "change", "remove")]

    for status, detail in _run(world, go):
        assert (status, detail["code"]) == (403, "approval_required")
    assert _record_row(world, world.rec) == before
    assert _record_count(world, world.obj) == count
    rows = _audits(world, world.pkg)
    assert [(r[3]["action"], r[3]["class"], r[3]["reason"]) for r in rows] == [
        ("add", "B", "approval_required"), ("change", "B", "approval_required"),
        ("remove", "C", "approval_required")]


# ── d and e. A source that is not an object ────────────────────────────────


def test_r8_a_source_that_is_not_an_object_never_runs(world) -> None:
    async def go() -> dict[str, tuple[int, Any]]:
        return {a: await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("flow", "flow_b", "propose", "pages")}

    got = {a: (s, d["code"]) for a, (s, d) in _run(world, go).items()}
    assert got == {
        "flow": (403, "approval_required"),
        # A published workflow of org B is not in the catalog of org A.
        "flow_b": (403, "not_in_catalog"),
        "propose": (403, "approval_required"),
        "pages": (403, "source_not_open"),
    }
    classes = {r[3]["action"]: r[3]["class"] for r in _audits(world, world.pkg)}
    assert classes == {"flow": "B", "flow_b": None, "propose": "B", "pages": "A"}


def test_r8_a_class_a_row_of_another_source_is_source_not_open(world, monkeypatch) -> None:
    """A workflow row is class B today. If a row ever reads class A, the door
    still stops, because the list opens one verb of one source."""
    async def _rows() -> list[dict[str, Any]]:
        return [{"id": f"workflow:{world.wf_a}", "class": "A"}]

    monkeypatch.setattr(invoke.actions, "catalog_rows", _rows)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "flow", {}))
    assert (status, detail["code"]) == (403, "source_not_open")


# ── f, g and h. The package, the record and the body ───────────────────────


def test_r8_an_action_that_is_not_an_id_is_404_and_writes_no_row(world) -> None:
    """An action of 20,000 characters, or a `uses` value in the path, names no
    A2 action id. The door reads nothing for it and writes no audit row."""
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("a" * 20000, "object:visit.get", "a" * 49)]

    assert _run(world, go) == [(404, NOT_FOUND)] * 3
    assert _audits(world, world.pkg) == []


def test_r8_an_object_that_the_package_lacks_is_object_missing(world) -> None:
    """The manifest declares `visit`, and the registry of this package holds no
    such object. A record id that names nothing is a plain 404."""
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg_empty, "peek", {"id": world.rec}),
                await _call(world.a, ANA, world.pkg, "peek", {"id": str(uuid.uuid4())})]

    no_object, no_record = _run(world, go)
    assert no_object == (404, {"code": "object_missing", "message": invoke.NO_OBJECT})
    assert no_record == (404, NOT_FOUND)
    assert [r[3] for r in _audits(world, world.pkg_empty)] == [{
        "action": "peek", "uses": "object:visit.get", "class": "A",
        "outcome": "not_found", "reason": "object_missing"}]
    assert [r[3] for r in _audits(world, world.pkg)] == [{
        "action": "peek", "uses": "object:visit.get", "class": "A",
        "outcome": "not_found"}]


def test_r8_a_catalog_that_fails_is_503_and_audited(world, monkeypatch) -> None:
    async def _fails() -> list[dict[str, Any]]:
        raise RuntimeError("the catalog session failed")

    monkeypatch.setattr(invoke.actions, "catalog_rows", _fails)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "flow",
                                               {"id": world.rec}))
    assert (status, detail) == (503, {"code": "catalog_unavailable",
                                      "message": invoke.CATALOG_DOWN})
    assert _audits(world, world.pkg) == [(world.a, ANA, "action", {
        "action": "flow", "uses": f"workflow:{world.wf_a}", "class": None,
        "outcome": "failed", "reason": "catalog_unavailable"})]


def test_r8_a_code_app_is_404(world) -> None:
    status, detail = _run(world, lambda: _call(world.a, ANA, world.code_app, "peek",
                                               {"id": world.rec}))
    assert (status, detail) == (404, NOT_FOUND)
    assert _audits(world, world.code_app) == []


def test_r8_a_record_of_another_package_is_404(world) -> None:
    """Both packages of Ana hold an object `visit`. The door reads the object
    of the package in the path, so a record of the other package is 404."""
    async def go() -> list[tuple[int, Any]]:
        # The second body names the other package. The door reads the package
        # of the path only, so the key changes nothing (M7b).
        return [await _call(world.a, ANA, world.pkg, "peek", body)
                for body in ({"id": world.rec2},
                             {"id": world.rec2, "package_id": world.pkg2})]

    assert _run(world, go) == [(404, NOT_FOUND)] * 2
    assert [r[3]["outcome"] for r in _audits(world, world.pkg)] == ["not_found"] * 2


def test_r8_a_body_that_names_another_org_changes_nothing(world) -> None:
    body = {"id": world.rec, "organization_id": world.b, "owner_email": BEN}
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, "peek", body))
    assert status == 200, got
    assert got["record"]["id"] == world.rec
    rows = _audits(world, world.pkg)
    assert [(r[0], r[1]) for r in rows] == [(world.a, ANA)]


# ── m to s. The list ───────────────────────────────────────────────────────


_LIST_OUTCOME = {"action": "find", "uses": "object:visit.list", "class": "A"}

#: One input for each refused code of the real grammar, and the field it names.
_REFUSED: list[tuple[str, dict[str, Any], str | None]] = [
    ("query_field_unknown", {"filter": [{"field": "nope", "op": "eq", "value": "x"}]}, "nope"),
    ("query_op_not_allowed",
     {"filter": [{"field": "status", "op": "contains", "value": "x"}]}, "status"),
    ("query_value_invalid",
     {"filter": [{"field": "status", "op": "eq", "value": "lost"}]}, "status"),
    ("query_not_indexed", {"sort": {"field": "site"}}, "site"),
    ("query_too_big", {"offset": 10_001}, None),
]


async def _add_records(org: str, oid: str, pid: str, rows: list[tuple[str, str]]) -> list[str]:
    ids = []
    async with tenant_session(org) as db:
        for site, status in rows:
            rec = await objects_store.create_record(
                db, ANA, oid, {"site": site, "status": status, "notes": "x"}, package_id=pid)
            assert rec is not None
            ids.append(rec.id)
    return ids


def _watch_the_door_session(monkeypatch) -> list[int]:
    """After each refused query, run one statement on the door's own session.
    The list that it answers holds one 1 for each statement that ran."""
    real = objects_store.query_records
    ran: list[int] = []

    async def spy(db: Any, **kw: Any) -> Any:
        try:
            return await real(db, **kw)
        except QueryError:
            ran.append((await db.execute(text("SELECT 1"))).scalar_one())
            raise

    monkeypatch.setattr(objects_store, "query_records", spy)
    return ran


def test_r8_the_owner_lists_a_filtered_sorted_page_and_one_audit_row(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        ids = await _add_records(world.a, world.obj, world.pkg, [
            ("Plant 1", "done"), ("Plant 2", "planned"), ("Plant 3", "done"), ("Depot", "planned"),
        ])
        world.ids = ids
        body = {"filter": [{"field": "site", "op": "contains", "value": "Plant"}],
                "sort": {"field": "status", "dir": "asc"}, "limit": 2}
        return [await _call(world.a, ANA, world.pkg, "find", body),
                await _call(world.a, ANA, world.pkg, "find", {**body, "offset": 2})]

    (status1, first), (status2, second) = _run(world, go)
    assert (status1, status2) == (200, 200), (first, second)
    # Four records of five match, and `done` sorts before `planned`.
    assert (first["total"], first["total_at_least"], first["offset"], first["limit"]) == (
        4, None, 0, 2)
    assert first["line"] == "Showing 1-2 of 4. Call again with offset=2 for the rest."
    assert [r["data"]["status"] for r in first["records"]] == ["done", "done"]
    assert {r["data"]["site"] for r in first["records"]} == {"Plant 1", "Plant 3"}
    assert (second["total"], second["offset"]) == (4, 2)
    assert [r["data"]["status"] for r in second["records"]] == ["planned", "planned"]
    assert {r["data"]["site"] for r in second["records"]} == {"Plant 2", _DATA["site"]}
    assert set(first["records"][0]) == {"id", "object_id", "data", "created_by", "updated_by",
                                        "created_at", "updated_at"}
    rows = _audits(world, world.pkg)
    assert rows == [(world.a, ANA, "action", {**_LIST_OUTCOME, "outcome": "ran"})] * 2
    # The row holds no record data and no value of the filter.
    for value in (*_DATA.values(), "Plant"):
        assert value not in json.dumps(rows[0][3])


def test_r8_an_empty_body_lists_the_first_page(world) -> None:
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, "find"))
    assert status == 200, got
    assert [r["id"] for r in got["records"]] == [world.rec]
    assert (got["total"], got["offset"], got["limit"], got["line"]) == (1, 0, 25, "")


def test_r8_another_member_and_org_b_get_404_on_a_list(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, BEN, world.pkg, "find", {}),
                await _call(world.b, ANA, world.pkg, "find", {}),
                await _call(world.a, ANA, world.pkg_b, "find", {})]

    assert _run(world, go) == [(404, NOT_FOUND)] * 3
    assert _audits(world, world.pkg) == [] and _audits(world, world.pkg_b) == []


def test_r8_two_packages_with_one_object_name_read_their_own_records(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, "find", {}),
                await _call(world.a, ANA, world.pkg2, "find", {})]

    (_, mine), (_, other) = _run(world, go)
    assert [r["id"] for r in mine["records"]] == [world.rec]
    assert [r["id"] for r in other["records"]] == [world.rec2]


def test_r8_an_object_that_the_package_lacks_is_object_missing_on_a_list(world) -> None:
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg_empty, "find", {}))
    assert (status, detail) == (404, {"code": "object_missing", "message": invoke.NO_OBJECT})
    assert [r[3] for r in _audits(world, world.pkg_empty)] == [{
        **_LIST_OUTCOME, "outcome": "not_found", "reason": "object_missing"}]


def test_r8_a_true_object_id_of_another_package_is_object_missing(world, monkeypatch) -> None:
    """The name read answers the object of the OTHER package, so the door holds
    a true id. `query_records` still takes the package of the path, so the
    store answers None and the door reads no record (the drop of `package_id`)."""
    real = objects_store.get_object_by_name

    async def wrong(db: Any, name: str, *, package_id: str) -> Any:
        return await real(db, name, package_id=world.pkg2)

    monkeypatch.setattr(objects_store, "get_object_by_name", wrong)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", {}))
    assert (status, detail) == (404, {"code": "object_missing", "message": invoke.NO_OBJECT})


@pytest.mark.parametrize("code,body,field", _REFUSED, ids=[c for c, _, _ in _REFUSED])
def test_r8_each_refused_query_is_422_and_the_session_stays_usable(
        world, monkeypatch, code, body, field) -> None:
    ran = _watch_the_door_session(monkeypatch)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", body))
    assert status == 422, detail
    assert set(detail) == {"code", "message", "field"}
    assert (detail["code"], detail["field"]) == (code, field)
    assert ran == [1]
    assert [r[3] for r in _audits(world, world.pkg)] == [{
        **_LIST_OUTCOME, "outcome": "refused", "reason": code}]


def test_r8_a_real_timeout_is_503_with_retry_after_and_the_session_stays_usable(
        world, monkeypatch) -> None:
    """A second connection holds a lock on the record table, so Postgres itself
    cancels the page statement at the 1 ms limit. The clock stands still, so
    the store's own check never fires first."""
    ran = _watch_the_door_session(monkeypatch)
    monkeypatch.setattr(studio_query, "TIMEOUT_MS", 1)
    monkeypatch.setattr(objects_store, "_clock", lambda: 0.0)
    with world.p.admin_engine.begin() as lock:
        lock.execute(text("LOCK TABLE studio_records IN ACCESS EXCLUSIVE MODE"))
        refused = _run(world, lambda: _refusal(world.a, ANA, world.pkg, "find", {}))
    assert refused.status_code == 503
    assert refused.detail["code"] == "query_timeout"
    assert set(refused.detail) == {"code", "message"}
    assert refused.headers == {"Retry-After": "2"}
    assert ran == [1]
    assert [r[3] for r in _audits(world, world.pkg)] == [{
        **_LIST_OUTCOME, "outcome": "failed", "reason": "query_timeout"}]
    # The limit of the store goes back, and the next call lists the page.
    monkeypatch.setattr(studio_query, "TIMEOUT_MS", 5_000)
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", {}))
    assert (status, got["total"]) == (200, 1)


@pytest.mark.parametrize("body", [
    {"package_id": str(uuid.uuid4())},
    {"organization_id": str(uuid.uuid4())},
    {"limit": 1, "owner_email": BEN},
    {"limit": True},
    {"offset": False},
    {"limit": float("inf")},
    {"offset": float("inf")},
    {"limit": float("nan")},
    {"limit": None},
], ids=["package", "org", "member", "limit_true", "offset_false", "limit_inf", "offset_inf",
        "limit_nan", "limit_null"])
def test_r8_a_refused_body_is_422_and_reads_nothing(world, monkeypatch, body) -> None:
    def _no_read(*_: Any, **__: Any) -> Any:
        raise AssertionError("a refused body must read nothing")

    monkeypatch.setattr(objects_store, "get_object_by_name", _no_read)
    monkeypatch.setattr(objects_store, "query_records", _no_read)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", body))
    assert (status, detail) == (422, {"code": "query_value_invalid",
                                      "message": invoke.BAD_LIST_INPUT, "field": None})
    assert [r[3] for r in _audits(world, world.pkg)] == [{
        **_LIST_OUTCOME, "outcome": "refused", "reason": "query_value_invalid"}]


@pytest.mark.parametrize("op,value", [("eq", "a\x00b"), ("eq", "\ud800"),
                                      ("contains", "a\x00b"), ("contains", "\ud800"),
                                      ("in", ["a\x00b"]), ("in", ["\ud800"])])
def test_r8_a_value_that_postgres_refuses_is_422_never_500(world, op, value) -> None:
    body = {"filter": [{"field": "site", "op": op, "value": value}]}
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", body))
    assert (status, detail["code"], detail["field"]) == (422, "query_value_invalid", "site")


# ── t. The body read ───────────────────────────────────────────────────────


def _connections_in_a_transaction(world: Any) -> int:
    """The connections of the app role that a session holds now. A pooled
    connection with no session reads `idle`."""
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND usename = :role AND state <> 'idle'"),
            {"role": world.p.app_url.username}).scalar_one())


@pytest.mark.parametrize("action", ["find", "peek"])
def test_r8_no_connection_is_in_a_transaction_during_the_read(world, action) -> None:
    held: list[int] = []
    body = {"id": world.rec} if action == "peek" else {}
    request = _Request(body, on_read=lambda: held.append(_connections_in_a_transaction(world)))
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, action, request=request))
    assert status == 200, got
    assert held == [0]


def test_r8_a_body_past_the_cap_is_413_and_one_audit_row(world) -> None:
    payload = {"filter": [], "pad": "x" * invoke.MAX_BODY_BYTES}
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", payload))
    assert (status, detail) == (413, {"code": "body_too_large", "message": invoke.BODY_OVER_CAP})
    assert _audits(world, world.pkg) == [(world.a, ANA, "action", {
        **_LIST_OUTCOME, "outcome": "refused", "reason": "body_too_large"})]


def test_r8_a_non_owner_reads_no_byte(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, BEN, world.pkg, "find", request=_Request(None, fails=True)),
                await _call(world.b, ANA, world.pkg, "find", request=_Request(None, fails=True))]

    assert _run(world, go) == [(404, NOT_FOUND)] * 2
    assert _audits(world, world.pkg) == []


def test_r8_the_switch_off_reads_no_byte(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", world.b)
    request = _Request(None, fails=True)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "find", request=request))
    assert (status, detail) == (404, NOT_FOUND)
    assert request.reads == 0


# ── u. A change during the read ────────────────────────────────────────────


async def _set_action(world: Any, action: str, uses: str | None) -> None:
    """Replace or remove *action* in the draft manifest, through the store, in
    a session of its own."""
    async with tenant_session(world.a) as db:
        package = await packages_store.get_package(db, ANA, world.pkg)
        assert package is not None
        manifest = dict(package.manifest)
        kept = [a for a in manifest["actions"] if a["id"] != action]
        manifest["actions"] = kept + ([{"id": action, "uses": uses}] if uses else [])
        assert await packages_store.update_manifest(db, ANA, world.pkg, manifest) is not None


async def _change(world: Any, change: str, action: str) -> None:
    """One change, in a second session, while the door waits on the body."""
    if change == "package_gone":
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM apps WHERE id = CAST(:p AS uuid)"), {"p": world.pkg})
    elif change == "owner_changed":
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE apps SET owner_email = :o WHERE id = CAST(:p AS uuid)"),
                      {"o": BEN, "p": world.pkg})
    else:
        await _set_action(world, action, {
            "removed": None,
            "now_deletes": "object:visit.delete",
            "now_door_op": "knowledge.list_pages",
        }[change])


@pytest.mark.parametrize("action", ["find", "peek"])
@pytest.mark.parametrize("change,answer,row", [
    ("package_gone", (404, NOT_FOUND), None),
    ("owner_changed", (404, NOT_FOUND), None),
    ("removed", (403, "not_declared"), {"uses": None, "class": None}),
    ("now_deletes", (403, "approval_required"), {"uses": "object:visit.delete", "class": "C"}),
    ("now_door_op", (403, "source_not_open"), {"uses": "knowledge.list_pages", "class": None}),
], ids=["package_gone", "owner_changed", "removed", "now_deletes", "now_door_op"])
def test_r8_a_change_during_the_read_is_seen_and_reads_no_record(
        world, monkeypatch, action, change, answer, row) -> None:
    """A slow client passes the first check, and the change lands while the
    door waits on the body. The door then reads no object and no record."""
    def _no_read(*_: Any, **__: Any) -> Any:
        raise AssertionError("the door read a record after the change")

    for name in ("get_object_by_name", "get_record", "query_records"):
        monkeypatch.setattr(objects_store, name, _no_read)
    request = _Request({"id": world.rec} if action == "peek" else {},
                       on_read=lambda: _change(world, change, action))
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, action, request=request))
    assert request.reads == 1
    if row is None:
        assert (status, detail) == answer
        assert _audits(world, world.pkg) == []
        return
    assert (status, detail["code"]) == answer
    assert [r[3] for r in _audits(world, world.pkg)] == [{
        "action": action, **row, "outcome": "refused", "reason": answer[1]}]


# ── i. The switch ──────────────────────────────────────────────────────────


def test_r8_the_switch_off_is_404_and_writes_no_row(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", world.b)

    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("peek", "add", "no_such_action")]

    assert _run(world, go) == [(404, NOT_FOUND)] * 3
    assert _audits(world, world.pkg) == []


# ── v. The direct writes (STU-B2c-1) ───────────────────────────────────────
#
# Acceptance (B2c-1). A member's own Save, edit or move is the approval, and
# the BFF proves it with a signed header.
#
# v. The owner with a valid proof creates, updates and deletes a record. Each
#    call writes one row and one ``app_audit`` row in the bound org.
# w. The audit row and the write are one transaction. A failed audit insert
#    leaves no record.
# x. The bearer and the member email with no proof, or with a proof for
#    another email, path or time, get 403 ``approval_required``. No row is
#    written, and one refused audit row lands.
# y. Another member and org B get 404. A record of another package is 404.
# z. A constraint, a state machine move and a formula each refuse with the 422
#    shape, write nothing, and keep the audit row.
# aa. 3 writers wait on the org lock and get 503 ``store_busy`` with
#    ``Retry-After``. A fourth in-flight writer gets 503 at once and holds no
#    connection. A read of org B still gets one.

import time  # noqa: E402

from acb_auth import AgentScope, direct_proof  # noqa: E402
from gateway.routes.apps import _common as apps_common  # noqa: E402

DIRECT_KEY = "r8-direct-key-" + "k" * 30
_RULES: list[dict[str, Any]] = [
    {"name": "qty_ok", "kind": "range", "field": "qty", "min": 0, "max": 100},
    {"name": "one_site", "kind": "unique", "fields": ["site"]},
    {"name": "flow", "kind": "state_machine", "field": "status", "initial": "planned",
     "moves": [{"from": "planned", "to": "done"}]},
    {"name": "line_total", "kind": "formula", "target": "total", "rounding": "half_up",
     "scale": 2, "expr": {"fn": "mul", "args": [{"ref": "qty"}, {"ref": "price"}]}},
]


@pytest.fixture
def dworld(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "studio_direct_key", DIRECT_KEY)
    invoke._IN_FLIGHT.clear()
    yield world
    invoke._IN_FLIGHT.clear()


def _headers(pid: str, action: str, email: str = ANA, *, method: str = "POST",
             path: str | None = None, now: float | None = None) -> dict[str, str]:
    where = path or f"/studio/apps/{pid}/actions/{action}"
    return {direct_proof.HEADER: direct_proof.sign(DIRECT_KEY, email, method, where, now=now)}


async def _dcall(org: str, email: str, pid: str, action: str, payload: Any, *,
                 headers: dict[str, str] | None = None, user: UserContext | None = None,
                 signed: bool = True) -> tuple[int, Any]:
    """The real handler, with a request that carries a proof for *email*."""
    if headers is None:
        headers = _headers(pid, action, email) if signed else {}
    request = _Request(payload, headers=headers, path=f"/studio/apps/{pid}/actions/{action}")
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=request,
                                           user=user or _user(org, email))
        return 200, found
    except HTTPException as exc:
        return exc.status_code, (exc.detail, dict(exc.headers or {}))
    finally:
        release_tenant(token)


def _body(world: Any, status: int, got: Any) -> Any:
    assert status == 200, got
    return got


def _ran(world: Any, pid: str, action: str) -> list[dict[str, Any]]:
    return [r[3] for r in _audits(world, pid) if r[3]["action"] == action]


def _data_of(world: Any, rid: str) -> dict[str, Any]:
    row = _record_row(world, rid)
    assert row is not None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def test_r8_a_direct_create_update_and_delete_each_write_one_record_and_one_audit_row(
        dworld) -> None:
    w = dworld
    before = _record_count(w, w.obj)

    async def go() -> tuple[Any, Any, Any]:
        made = _body(w, *await _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}))
        rid = made["record"]["id"]
        changed = _body(w, *await _dcall(w.a, ANA, w.pkg, "change",
                                         {"id": rid, "data": {"notes": "Moved"}}))
        gone = _body(w, *await _dcall(w.a, ANA, w.pkg, "remove", {"id": rid}))
        return made, changed, gone

    made, changed, gone = _run(w, go)
    rid = made["record"]["id"]
    assert made["record"]["data"] == _DATA and made["record"]["created_by"] == ANA
    assert changed["record"]["data"]["notes"] == "Moved" and changed["record"]["updated_by"] == ANA
    assert gone == {"deleted": True, "id": rid}
    assert _record_row(w, rid) is None and _record_count(w, w.obj) == before
    rows = _audits(w, w.pkg)
    assert [(r[0], r[1], r[2], r[3]["action"], r[3]["outcome"]) for r in rows] == [
        (w.a, ANA, "action", "add", "ran"), (w.a, ANA, "action", "change", "ran"),
        (w.a, ANA, "action", "remove", "ran")]
    assert [r[3]["class"] for r in rows] == ["B", "B", "C"]
    # Fix round 1: each row names its record, the new id for the create.
    assert [r[3].get("record_id") for r in rows] == [rid, rid, rid]
    assert "Plant 4" not in json.dumps(rows) and "Moved" not in json.dumps(rows)


def test_r8_each_direct_write_commits_its_record_with_its_audit_row(dworld) -> None:
    w = dworld
    status, got = _run(w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}))
    assert status == 200
    assert _record_row(w, got["record"]["id"]) is not None
    assert len(_ran(w, w.pkg, "add")) == 1


def test_r8_a_failed_audit_insert_leaves_no_record(dworld, monkeypatch) -> None:
    """The audit statement fails for real, so the transaction aborts and the
    write goes with it."""
    w = dworld
    count = _record_count(w, w.obj)
    monkeypatch.setattr(apps_common, "_APP_AUDIT_SQL",
                        "INSERT INTO app_audit (no_such_column) VALUES (1)")

    async def go() -> None:
        with pytest.raises(Exception) as err:
            await _dcall(w.a, ANA, w.pkg, "add", {"data": {**_DATA, "site": "Audit fails"}})
        assert not isinstance(err.value, HTTPException)

    _run(w, go)
    assert _record_count(w, w.obj) == count
    assert _audits(w, w.pkg) == []


def test_r8_a_failed_audit_leaves_the_update_and_the_delete_undone(dworld, monkeypatch) -> None:
    w = dworld
    before = _record_row(w, w.rec)
    monkeypatch.setattr(apps_common, "_APP_AUDIT_SQL",
                        "INSERT INTO app_audit (no_such_column) VALUES (1)")

    async def go() -> None:
        for action, body in (("change", {"id": w.rec, "data": {"notes": "Lost"}}),
                             ("remove", {"id": w.rec})):
            with pytest.raises(DBAPIError):
                await _dcall(w.a, ANA, w.pkg, action, body)

    _run(w, go)
    assert _record_row(w, w.rec) == before


@pytest.mark.parametrize("action", ["add", "change", "remove"])
def test_r8_no_proof_is_403_with_one_refused_audit_row_and_no_write(dworld, action) -> None:
    w = dworld
    before, count = _record_row(w, w.rec), _record_count(w, w.obj)
    body = {"add": {"data": _DATA}, "change": {"id": w.rec, "data": {"notes": "No"}},
            "remove": {"id": w.rec}}[action]
    status, (detail, _) = _run(w, lambda: _dcall(w.a, ANA, w.pkg, action, body, signed=False))
    assert (status, detail["code"]) == (403, "approval_required")
    assert _record_row(w, w.rec) == before and _record_count(w, w.obj) == count
    rows = _audits(w, w.pkg)
    assert [(r[3]["action"], r[3]["outcome"], r[3]["reason"]) for r in rows] == [
        (action, "refused", "approval_required")]


@pytest.mark.parametrize("case", ["email", "path", "old", "method", "garbage"])
def test_r8_a_wrong_proof_is_403_and_writes_nothing(dworld, case) -> None:
    w = dworld
    count = _record_count(w, w.obj)
    headers = {
        "email": _headers(w.pkg, "add", BEN),
        "path": _headers(w.pkg, "add", path=f"/studio/apps/{w.pkg}/actions/change"),
        "old": _headers(w.pkg, "add", now=time.time() - 3600),
        "method": _headers(w.pkg, "add", method="GET"),
        "garbage": {direct_proof.HEADER: "v1.1.zz"},
    }[case]
    status, (detail, _) = _run(
        w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}, headers=headers))
    assert (status, detail["code"]) == (403, "approval_required")
    assert _record_count(w, w.obj) == count


def test_r8_an_unset_key_makes_a_valid_looking_proof_403(dworld, monkeypatch) -> None:
    w = dworld
    headers = _headers(w.pkg, "add")
    monkeypatch.setattr(get_settings(), "studio_direct_key", "")
    status, (detail, _) = _run(
        w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}, headers=headers))
    assert (status, detail["code"]) == (403, "approval_required")


def test_r8_a_valid_proof_with_an_agent_scope_is_403(dworld) -> None:
    w = dworld
    user = UserContext(email=ANA, role=UserRole.EMPLOYEE, organization_id=w.a,
                       access=build_access(["feature:studio"]),
                       agent=AgentScope(connection_id="c1"))
    count = _record_count(w, w.obj)
    status, (detail, _) = _run(
        w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}, user=user))
    assert (status, detail["code"]) == (403, "approval_required")
    assert _record_count(w, w.obj) == count


def test_r8_another_member_and_org_b_get_404_and_write_nothing(dworld) -> None:
    w = dworld
    count = _record_count(w, w.obj)

    async def go() -> list[tuple[int, Any]]:
        return [await _dcall(w.a, BEN, w.pkg, "add", {"data": _DATA}),
                await _dcall(w.b, ANA, w.pkg, "add", {"data": _DATA}),
                await _dcall(w.a, BEN, w.pkg, "remove", {"id": w.rec}),
                await _dcall(w.b, ANA, w.pkg, "change", {"id": w.rec, "data": {"notes": "x"}})]

    for status, (detail, _) in _run(w, go):
        assert (status, detail) == (404, NOT_FOUND)
    assert _record_count(w, w.obj) == count
    assert _audits(w, w.pkg) == []


def test_r8_a_record_of_another_package_is_404_and_stays(dworld) -> None:
    w = dworld
    before = _record_row(w, w.rec2)

    async def go() -> list[tuple[int, Any]]:
        return [await _dcall(w.a, ANA, w.pkg, "change", {"id": w.rec2, "data": {"notes": "x"}}),
                await _dcall(w.a, ANA, w.pkg, "remove", {"id": w.rec2}),
                await _dcall(w.a, ANA, w.pkg, "remove", {"id": str(uuid.uuid4())})]

    for status, (detail, _) in _run(w, go):
        assert (status, detail) == (404, NOT_FOUND)
    assert _record_row(w, w.rec2) == before
    assert [r["outcome"] for r in _ran(w, w.pkg, "change") + _ran(w, w.pkg, "remove")] == [
        "not_found"] * 3


def test_r8_the_switch_off_is_404_and_writes_no_row_for_a_direct_write(
        dworld, monkeypatch) -> None:
    w = dworld
    monkeypatch.setattr(get_settings(), "studio_orgs", w.b)
    status, (detail, _) = _run(w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}))
    assert (status, detail) == (404, NOT_FOUND)
    assert _audits(w, w.pkg) == []


@pytest.mark.parametrize("action,body", [
    ("add", {"data": _DATA, "id": str(uuid.uuid4())}),
    ("add", {"data": _DATA, "organization_id": "x"}),
    ("change", {"id": str(uuid.uuid4())}),
    ("remove", {"id": str(uuid.uuid4()), "data": {}}),
    ("add", {"data": "not an object"}),
    ("change", {"id": "not-a-uuid", "data": {"notes": "x"}}),
    ("remove", {"id": "not-a-uuid"}),
])
def test_r8_a_wrong_body_is_422_and_writes_nothing(dworld, action, body) -> None:
    w = dworld
    count = _record_count(w, w.obj)
    status, (detail, _) = _run(w, lambda: _dcall(w.a, ANA, w.pkg, action, body))
    assert status == 422
    assert set(detail) == {"code", "message", "rule", "fieldErrors"}
    assert _record_count(w, w.obj) == count
    assert [r["outcome"] for r in _ran(w, w.pkg, action)] == ["refused"]


def test_r8_a_body_past_the_cap_is_413_and_writes_nothing(dworld) -> None:
    w = dworld
    count = _record_count(w, w.obj)
    big = {"data": {"notes": "x" * invoke.MAX_BODY_BYTES}}
    status, (detail, _) = _run(w, lambda: _dcall(w.a, ANA, w.pkg, "add", big))
    assert (status, detail["code"]) == (413, "body_too_large")
    assert _record_count(w, w.obj) == count


async def _with_rules(w: Any, formula: bool = False) -> None:
    async with tenant_session(w.a) as db:
        for spec in ({"key": "qty", "type": "number", "hot": True},
                     {"key": "price", "type": "number"},
                     {"key": "total", "type": "number", "hot": True}):
            await objects_store.add_field(db, w.obj, spec)
        await objects_store.set_field_hot(db, w.obj, "site", True)
        if formula:
            # A formula is declared before the first record (C7a).
            assert await objects_store.delete_record(db, ANA, w.obj, w.rec, package_id=w.pkg)
        rules = _RULES if formula else _RULES[:3]
        await objects_store.set_constraints(db, w.obj, rules, package_id=w.pkg)


def test_r8_a_field_error_gives_field_errors_with_the_key(dworld) -> None:
    w = dworld
    status, (detail, _) = _run(
        w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": {"status": "no such option"}}))
    assert status == 422 and detail["code"] == "field_invalid"
    assert detail["fieldErrors"][0]["field"] == "status"
    assert detail["fieldErrors"][0]["message"] == detail["message"]
    assert "no such option" not in json.dumps(detail)


def test_r8_a_range_a_unique_and_a_move_refuse_with_the_shape_and_keep_the_audit_row(
        dworld) -> None:
    w = dworld

    async def go() -> list[Any]:
        await _with_rules(w)
        rng = await _dcall(w.a, ANA, w.pkg, "add", {"data": {"site": "S1", "qty": 500}})
        dup = await _dcall(w.a, ANA, w.pkg, "add", {"data": {"site": _DATA["site"]}})
        moved = await _dcall(w.a, ANA, w.pkg, "change",
                             {"id": w.rec, "data": {"status": "done"}})
        back = await _dcall(w.a, ANA, w.pkg, "change",
                            {"id": w.rec, "data": {"status": "planned"}})
        return [rng, dup, moved, back]

    count = _record_count(w, w.obj)
    rng, dup, moved, back = _run(w, go)
    assert rng[0] == 422 and rng[1][0]["code"] == "rule_refused" and rng[1][0]["rule"] == "qty_ok"
    assert dup[0] == 422 and dup[1][0]["code"] == "rule_refused" and dup[1][0]["rule"] == "one_site"
    assert moved[0] == 200 and moved[1]["record"]["data"]["status"] == "done"
    assert back[0] == 422 and back[1][0]["rule"] == "flow"
    assert back[1][0]["code"] == "rule_refused"
    assert _record_count(w, w.obj) == count
    assert _data_of(w, w.rec)["status"] == "done"
    outcomes = [(r[3]["action"], r[3]["outcome"], r[3].get("reason")) for r in _audits(w, w.pkg)]
    assert outcomes == [("add", "refused", "rule_refused"), ("add", "refused", "rule_refused"),
                        ("change", "ran", None), ("change", "refused", "rule_refused")]
    # A refused create made no record. Each update names its record.
    assert [r[3].get("record_id") for r in _audits(w, w.pkg)] == [None, None, w.rec, w.rec]


def test_r8_a_computed_field_is_refused_and_nothing_is_written(dworld) -> None:
    w = dworld

    async def go() -> list[Any]:
        await _with_rules(w, formula=True)
        refused = await _dcall(w.a, ANA, w.pkg, "add",
                               {"data": {"site": "F1", "qty": 2, "price": 3, "total": 99}})
        same = await _dcall(w.a, ANA, w.pkg, "add",
                            {"data": {"site": "F2", "qty": 2, "price": 3, "total": 6}})
        ok = await _dcall(w.a, ANA, w.pkg, "add", {"data": {"site": "F3", "qty": 2, "price": 3}})
        return [refused, same, ok]

    refused, same, ok = _run(w, go)
    for status, (detail, _) in (refused, same):
        assert status == 422 and detail["code"] == "computed_field"
        assert detail["rule"] == "line_total"
        assert detail["fieldErrors"][0]["field"] == "total"
        assert "99" not in json.dumps(detail)
    assert ok[0] == 200 and str(ok[1]["record"]["data"]["total"]) in ("6", "6.00", "6.0")
    assert _record_count(w, w.obj) == 1  # only the valid create (the setup removed w.rec)
    assert [r[3]["reason"] for r in _audits(w, w.pkg)[:2]] == ["computed_field"] * 2


def test_r8_a_refusal_keeps_its_audit_row(dworld) -> None:
    """The refusal is raised after the session closes, so its audit row
    commits. The row is read as the superuser."""
    w = dworld
    status, (_detail, _) = _run(
        w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": {"status": "no such option"}}))
    assert status == 422
    rows = _audits(w, w.pkg)
    assert len(rows) == 1 and rows[0][3]["reason"] == "field_invalid"


def _hold_the_org_lock(w: Any):
    """Hold the advisory lock of org A on a superuser connection."""
    conn = w.p.admin_engine.connect()
    conn.execute(text(
        "SELECT pg_advisory_xact_lock(hashtextextended('studio_objects:' || :org, 0))"),
        {"org": w.a})
    return conn


def test_r8_busy_writers_get_503_and_the_fourth_is_refused_at_once(dworld) -> None:
    w = dworld
    count = _record_count(w, w.obj)
    lock = _hold_the_org_lock(w)
    held: dict[str, Any] = {}

    async def go() -> list[Any]:
        waiters = [asyncio.ensure_future(
            _dcall(w.a, ANA, w.pkg, "add", {"data": {**_DATA, "site": f"W{i}"}}))
            for i in range(invoke.WRITERS_PER_ORG)]
        for _ in range(100):
            await asyncio.sleep(0.01)
            if invoke._IN_FLIGHT.get(w.a) == invoke.WRITERS_PER_ORG:
                break
        assert {w.a: invoke.WRITERS_PER_ORG} == invoke._IN_FLIGHT
        started = time.monotonic()
        held["fourth"] = await _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA})
        held["fourth_s"] = time.monotonic() - started
        held["in_tx"] = _connections_in_a_transaction(w)
        held["read_b"] = await _call(w.b, ANA, w.pkg_b, "find")
        return list(await asyncio.gather(*waiters))

    try:
        waiters = _run(w, go)
    finally:
        lock.rollback()
        lock.close()
    status, (detail, headers) = held["fourth"]
    assert (status, detail["code"], headers) == (503, "store_busy", {"Retry-After": "1"})
    assert held["fourth_s"] < invoke.objects_store.ORG_LOCK_WAIT_MS / 1000 / 2
    assert held["in_tx"] == invoke.WRITERS_PER_ORG  # the fourth held none
    assert held["read_b"][0] == 200  # a read of org B still got a connection
    for status, (detail, headers) in waiters:
        assert (status, detail["code"], headers) == (503, "store_busy", {"Retry-After": "1"})
    assert _record_count(w, w.obj) == count
    rows = _ran(w, w.pkg, "add")
    assert [(r["outcome"], r["reason"]) for r in rows] == [("failed", "store_busy")] * 3
    assert invoke._IN_FLIGHT == {}


def test_r8_a_writer_after_the_busy_ones_is_admitted_again(dworld) -> None:
    w = dworld
    status, _got = _run(w, lambda: _dcall(w.a, ANA, w.pkg, "add", {"data": _DATA}))
    assert status == 200 and invoke._IN_FLIGHT == {}


# ── ab. The delegated writes (STU-B2c-2a) ──────────────────────────────────
#
# Acceptance (spec row B2c-2a). A class B or C action of a delegated caller
# runs only after the member's card. A refused card writes nothing. The card
# is the approval store of MCP-P1b-1, and the door builds no second store.
#
# ab. A delegated create, update and delete (B, B, C) each answer 202 and
#     write nothing. A resend before the approval is 409 and writes nothing.
#     After the approval, the resend runs the STORED call and reads no byte of
#     its own body. A second resend is 409 ``request_spent`` and writes nothing.
# ac. A declined card writes nothing.
# ad. A retry of the ask gives the first request. The stored call is the door
#     path, the ``uses`` of the action and the body.
# ae. A failed audit rolls the spend and the write back. The request stays
#     approved, and a later resend runs once.
# af. A refused write and a busy store roll the spend back.
# ag. A call over 32 KiB is 413 ``payload_too_large`` and opens no card.
# ah. A run with no person present gets 403 ``no_person_present``. It opens no
#     card and spends no request. A read under the mark still runs.
# ai. With ``APP_REQUESTS_ORGS`` off for the org, a delegated write is 403
#     ``approval_required`` and opens no card.
# aj. A request spends only on its own package, member and action, and only
#     while the action names the same ``uses``.
# ak. A wrong body, and an object that the package lacks, open no card.
#
# Fix round 1 (2026-11-06):
#
# al. A card opens only for a call that asks with ``ask=1``. A delegated write
#     with no marker and no ``request_id`` gets 403 ``approval_required`` while
#     the org flag is on. It opens no card and writes nothing. So an unsigned
#     Save of the member's own screen never reads as saved.
# am. An ask after a closed request (spent, declined or expired) opens a NEW
#     request. A retry while the request is open gives the same one. Two asks
#     at once after a close open one new request.
# an. A resend of an expired request is 409 ``request_expired`` and writes
#     nothing.
#
# **The mutations (R7).** Each was run on 2026-11-06 on a committed tree, and
# restored with git. The two of the acceptance line:
#
# * D1. ``_run_spend``: skip the spend, and run the stored call of
#   ``read_request``. 9 of the 15 tests below go red. Among them are each case
#   of ``test_r8_a_delegated_write_runs_only_after_the_card_and_runs_the_stored_call``
#   (add B, change B, remove C) and each case of
#   ``test_r8_a_declined_card_writes_nothing`` (add B, remove C). In the
#   hermetic half, the three cases of
#   ``test_a_resend_reads_no_byte_and_spends_writes_and_audits_in_one_session``,
#   ``test_a_refused_spend_writes_nothing_and_answers_its_reason`` and
#   ``test_the_direct_path_is_the_only_door_write`` go red.
# * D2. ``_run_spend``: spend in its own ``_tenant_session``, which commits
#   before the write. 4 tests go red: each case of
#   ``test_r8_a_failed_audit_rolls_the_spend_and_the_write_back`` (add B,
#   remove C), ``test_r8_a_refused_write_rolls_the_spend_back`` (B) and
#   ``test_r8_a_busy_store_rolls_the_spend_back_and_a_retry_runs`` (C). In the
#   hermetic half, the three cases of
#   ``test_a_resend_reads_no_byte_and_spends_writes_and_audits_in_one_session``
#   go red.
#
# Fix round 1 ran both again on its head (2026-11-06), over the 22 delegated
# tests of this file. D1: 11 go red, the cases above and also
# ``test_r8_an_ask_after_a_run_opens_a_new_request`` and
# ``test_r8_an_expired_request_is_409_writes_nothing_and_a_new_ask_opens_one``.
# 5 hermetic tests go red. D2: the same 4 R8 tests and 3 hermetic tests go red.
#
# Fix round 2 ran both again on its head (2026-11-06), over the 24 delegated
# tests of this file. D1: 12 go red, the 11 above and
# ``test_r8_three_runs_of_one_change_each_open_a_new_request_at_a_constant_cost``.
# D2: the same 4 tests go red. A typo in the ``_APPROVAL_ROUTES`` entry of the
# decision route turns ``test_the_route_scan_reads_an_f_string`` red.

from acb_common.no_person import no_person_scope  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from gateway import app_requests  # noqa: E402


@pytest.fixture
def rworld(dworld, monkeypatch):
    """The direct world, with the request store on for each org. The proof key
    is set, and a call with no proof is delegated."""
    monkeypatch.setattr(get_settings(), "app_requests_orgs", "*")
    yield dworld
    with dworld.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs WHERE app = 'studio' "
                       "AND member_email IN (:a, :b)"), {"a": ANA, "b": BEN})


async def _delegated(org: str, email: str, pid: str, action: str, payload: Any,
                     request_id: str | None = None, ask: bool = True) -> tuple[int, Any]:
    """The real handler with NO proof, so the call is delegated. With no
    *request_id*, the query holds the ask marker ``ask=1`` unless *ask* is
    False (fix round 1). With *request_id*, the query names it, and the body
    read FAILS: a resend that reads a byte of its body errors. Answers the
    status and the answer, or the detail and the headers of a refusal."""
    query = ({"ask": "1"} if ask else {}) if request_id is None else {"request_id": request_id}
    request = _Request(payload, fails=request_id is not None,
                       path=f"/studio/apps/{pid}/actions/{action}", query=query)
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=request,
                                           user=_user(org, email))
    except HTTPException as exc:
        return exc.status_code, (exc.detail, dict(exc.headers or {}))
    finally:
        release_tenant(token)
    if isinstance(found, JSONResponse):
        return found.status_code, json.loads(found.body)
    return 200, found


async def _decide(w: Any, rid: str, decision: str) -> None:
    """Ana answers her card, through the one store of MCP-P1b-1."""
    async with tenant_session(w.a) as db:
        await app_requests.decide(db, org=w.a, member=ANA, request_id=rid,
                                  decision=decision, idempotency_key=uuid.uuid4().hex)


async def _request(w: Any, rid: str) -> dict[str, Any] | None:
    async with tenant_session(w.a) as db:
        return await app_requests.read_request(db, org=w.a, member=ANA, request_id=rid)


async def _state(w: Any, rid: str) -> str | None:
    found = await _request(w, rid)
    return None if found is None else str(found["state"])


def _studio_runs(w: Any) -> int:
    with w.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pm_agent_runs WHERE app = 'studio' "
            "AND member_email IN (:a, :b)"), {"a": ANA, "b": BEN}).scalar_one())


def _all_data(w: Any) -> str:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT data FROM studio_records WHERE object_id = ANY(CAST(:o AS uuid[]))"),
            {"o": [w.obj, w.obj2]}).all()
    return json.dumps([r[0] for r in rows], default=str)


def _bodies(w: Any) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """The body of the card, and a second body that the resend carries."""
    return {
        "add": ({"data": {**_DATA, "site": "Card site"}},
                {"data": {**_DATA, "site": "Second body"}}),
        "change": ({"id": w.rec, "data": {"notes": "Card note"}},
                   {"id": w.rec, "data": {"notes": "Second body"}}),
        "remove": ({"id": w.rec}, {"id": w.rec2}),
    }


@pytest.mark.parametrize("action,cls", [("add", "B"), ("change", "B"), ("remove", "C")])
def test_r8_a_delegated_write_runs_only_after_the_card_and_runs_the_stored_call(
        rworld, action, cls) -> None:
    w = rworld
    card, second = _bodies(w)[action]
    count, before = _record_count(w, w.obj), _record_row(w, w.rec)

    async def go() -> dict[str, Any]:
        got: dict[str, Any] = {"asked": await _delegated(w.a, ANA, w.pkg, action, card)}
        rid = got["asked"][1]["request_id"]
        got["early"] = await _delegated(w.a, ANA, w.pkg, action, second, rid)
        got["after_early"] = (_record_count(w, w.obj), _record_row(w, w.rec))
        await _decide(w, rid, "approve")
        got["approved"] = await _state(w, rid)
        got["ran"] = await _delegated(w.a, ANA, w.pkg, action, second, rid)
        got["after_ran"] = _record_count(w, w.obj)
        got["again"] = await _delegated(w.a, ANA, w.pkg, action, second, rid)
        got["final"] = await _state(w, rid)
        return got

    got = _run(w, go)
    status, body = got["asked"]
    assert status == 202 and set(body) == {"state", "request_id", "expires_at"}, got["asked"]
    assert body["state"] == "awaiting"
    rid = body["request_id"]
    # Before the approval nothing changed.
    assert got["early"][0] == 409 and got["early"][1][0]["code"] == "request_awaiting"
    assert got["after_early"] == (count, before)
    assert got["approved"] == "approved"
    # After it, the STORED call ran, once.
    status, ran = got["ran"]
    assert status == 200, ran
    if action == "add":
        assert ran["record"]["data"]["site"] == "Card site"
        assert got["after_ran"] == count + 1
    elif action == "change":
        assert ran["record"]["data"]["notes"] == "Card note"
        assert _data_of(w, w.rec)["notes"] == "Card note"
    else:
        assert ran == {"deleted": True, "id": w.rec}
        assert _record_row(w, w.rec) is None and _record_row(w, w.rec2) is not None
    assert "Second body" not in _all_data(w)
    assert got["again"][0] == 409 and got["again"][1][0]["code"] == "request_spent"
    assert _record_count(w, w.obj) == got["after_ran"]
    assert got["final"] == "done"
    rows = _ran(w, w.pkg, action)
    assert [(r["outcome"], r.get("reason"), r.get("request_id"), r["class"]) for r in rows] == [
        ("asked", None, rid, cls), ("refused", "request_awaiting", rid, cls),
        ("ran", None, rid, cls), ("refused", "request_spent", rid, cls)]
    # Each row is of the bound org and of Ana, and holds no record data.
    assert {(r[0], r[1]) for r in _audits(w, w.pkg)} == {(w.a, ANA)}
    assert "Card" not in json.dumps(rows)


@pytest.mark.parametrize("action", ["add", "remove"])
def test_r8_a_declined_card_writes_nothing(rworld, action) -> None:
    w = rworld
    card, second = _bodies(w)[action]
    count, before = _record_count(w, w.obj), _record_row(w, w.rec)

    async def go() -> tuple[Any, Any]:
        rid = (await _delegated(w.a, ANA, w.pkg, action, card))[1]["request_id"]
        await _decide(w, rid, "decline")
        return await _delegated(w.a, ANA, w.pkg, action, second, rid), await _state(w, rid)

    (status, (detail, _)), state = _run(w, go)
    assert (status, detail["code"], state) == (403, "request_declined", "declined")
    assert (_record_count(w, w.obj), _record_row(w, w.rec)) == (count, before)
    assert [r["outcome"] for r in _ran(w, w.pkg, action)] == ["asked", "refused"]


def test_r8_a_retry_of_the_ask_gives_the_first_request(rworld) -> None:
    w = rworld
    card = {"id": w.rec, "data": {"notes": "Card note"}}

    async def go() -> tuple[Any, Any, Any, Any]:
        one = await _delegated(w.a, ANA, w.pkg, "change", card)
        two = await _delegated(w.a, ANA, w.pkg, "change", card)
        other = await _delegated(w.a, ANA, w.pkg, "change",
                                 {"id": w.rec, "data": {"notes": "Another note"}})
        return one, two, other, await _request(w, one[1]["request_id"])

    one, two, other, stored = _run(w, go)
    assert one[0] == two[0] == other[0] == 202
    assert one[1]["request_id"] == two[1]["request_id"] != other[1]["request_id"]
    assert _studio_runs(w) == 2
    assert stored is not None
    assert (stored["source"], stored["app"], stored["subject"], stored["op"], stored["class"]) == (
        "studio", "studio", w.pkg, "change", "B")
    assert stored["client_via"] == invoke.REQUEST_VIA
    assert stored["call"] == {
        "method": "POST", "template": invoke.DOOR_TEMPLATE,
        "path": f"/studio/apps/{w.pkg}/actions/change",
        "query": [["uses", "object:visit.update"]], "body": card, "if_match": None}
    assert stored["card"]["title"] == "Change a record of visit."
    assert stored["card"]["app_name"] == "Visits"
    assert _record_row(w, w.rec) is not None and _data_of(w, w.rec) == _DATA


@pytest.mark.parametrize("action", ["add", "remove"])
def test_r8_a_failed_audit_rolls_the_spend_and_the_write_back(
        rworld, monkeypatch, action) -> None:
    """The audit statement fails for real after the write ran, so the one
    transaction aborts. The spend goes with the write."""
    w = rworld
    card, _ = _bodies(w)[action]
    count = _record_count(w, w.obj)
    good = apps_common._APP_AUDIT_SQL

    async def go() -> tuple[Any, Any, Any, Any]:
        rid = (await _delegated(w.a, ANA, w.pkg, action, card))[1]["request_id"]
        await _decide(w, rid, "approve")
        monkeypatch.setattr(apps_common, "_APP_AUDIT_SQL",
                            "INSERT INTO app_audit (no_such_column) VALUES (1)")
        with pytest.raises(DBAPIError):
            await _delegated(w.a, ANA, w.pkg, action, None, rid)
        monkeypatch.setattr(apps_common, "_APP_AUDIT_SQL", good)
        between = (await _state(w, rid), _record_count(w, w.obj), _record_row(w, w.rec))
        ran = await _delegated(w.a, ANA, w.pkg, action, None, rid)
        return between, ran, await _state(w, rid), _record_count(w, w.obj)

    between, ran, final, after = _run(w, go)
    assert between[0] == "approved", "the spend committed without its write"
    assert between[1] == count and between[2] is not None
    assert ran[0] == 200 and final == "done"
    assert after == (count + 1 if action == "add" else count - 1)
    assert [r["outcome"] for r in _ran(w, w.pkg, action)] == ["asked", "ran"]


def test_r8_a_refused_write_rolls_the_spend_back(rworld) -> None:
    """The store checks a field at the write, not at the ask. The refused write
    keeps its audit row, and the request stays approved."""
    w = rworld
    count = _record_count(w, w.obj)

    async def go() -> tuple[Any, Any]:
        asked = await _delegated(w.a, ANA, w.pkg, "add", {"data": {"status": "no such option"}})
        rid = asked[1]["request_id"]
        await _decide(w, rid, "approve")
        return await _delegated(w.a, ANA, w.pkg, "add", None, rid), await _state(w, rid)

    (status, (detail, _)), state = _run(w, go)
    assert (status, detail["code"]) == (422, "field_invalid")
    assert detail["fieldErrors"][0]["field"] == "status"
    assert state == "approved", "a write that did not run spent the request"
    assert _record_count(w, w.obj) == count
    rows = _ran(w, w.pkg, "add")
    assert [(r["outcome"], r.get("reason")) for r in rows] == [
        ("asked", None), ("refused", "field_invalid")]


def test_r8_a_busy_store_rolls_the_spend_back_and_a_retry_runs(rworld) -> None:
    w = rworld

    async def ask() -> str:
        rid = (await _delegated(w.a, ANA, w.pkg, "remove", {"id": w.rec}))[1]["request_id"]
        await _decide(w, rid, "approve")
        return str(rid)

    rid = _run(w, ask)
    lock = _hold_the_org_lock(w)
    try:
        busy = _run(w, lambda: _delegated(w.a, ANA, w.pkg, "remove", None, rid))
    finally:
        lock.rollback()
        lock.close()
    status, (detail, headers) = busy
    assert (status, detail["code"], headers) == (503, "store_busy", {"Retry-After": "1"})
    assert _run(w, lambda: _state(w, rid)) == "approved"
    assert _record_row(w, w.rec) is not None
    status, ran = _run(w, lambda: _delegated(w.a, ANA, w.pkg, "remove", None, rid))
    assert (status, ran) == (200, {"deleted": True, "id": w.rec})
    assert _run(w, lambda: _state(w, rid)) == "done" and invoke._IN_FLIGHT == {}


def test_r8_a_call_over_32_kib_is_413_and_opens_no_card(rworld) -> None:
    w = rworld
    big = {"data": {**_DATA, "notes": "x" * (app_requests.MAX_CALL_BYTES + 1)}}
    status, (detail, _) = _run(w, lambda: _delegated(w.a, ANA, w.pkg, "add", big))
    assert (status, detail["code"]) == (413, "payload_too_large")
    assert _studio_runs(w) == 0
    assert [(r["outcome"], r["reason"]) for r in _ran(w, w.pkg, "add")] == [
        ("refused", "payload_too_large")]


def test_r8_a_run_with_no_person_present_opens_no_card_and_spends_none(rworld) -> None:
    w = rworld

    async def go() -> dict[str, Any]:
        got: dict[str, Any] = {}
        with no_person_scope():
            got["ask"] = await _delegated(w.a, ANA, w.pkg, "add", {"data": _DATA})
            got["read"] = await _delegated(w.a, ANA, w.pkg, "peek", {"id": w.rec})
        got["runs"] = _studio_runs(w)
        # A person asks and approves. A marked run then cannot spend it.
        rid = (await _delegated(w.a, ANA, w.pkg, "remove", {"id": w.rec}))[1]["request_id"]
        await _decide(w, rid, "approve")
        with no_person_scope():
            got["spend"] = await _delegated(w.a, ANA, w.pkg, "remove", None, rid)
        got["state"] = await _state(w, rid)
        return got

    got = _run(w, go)
    for key in ("ask", "spend"):
        status, (detail, _) = got[key]
        assert (status, detail["code"]) == (403, "no_person_present"), key
    assert got["read"][0] == 200, "the mark binds the delegated writes only"
    assert got["runs"] == 0 and got["state"] == "approved"
    assert _record_row(w, w.rec) is not None
    assert [r["reason"] for r in _ran(w, w.pkg, "add")] == ["no_person_present"]


def test_r8_with_the_org_flag_off_a_delegated_write_is_403_and_opens_no_card(
        rworld, monkeypatch) -> None:
    w = rworld
    monkeypatch.setattr(get_settings(), "app_requests_orgs", w.b)
    before = _record_row(w, w.rec)

    async def go() -> list[Any]:
        bodies = _bodies(w)
        out = [await _delegated(w.a, ANA, w.pkg, a, bodies[a][0])
               for a in ("add", "change", "remove")]
        # A request id in the query changes nothing while the org is dark.
        out.append(await _delegated(w.a, ANA, w.pkg, "remove", {"id": w.rec},
                                    str(uuid.uuid4())))
        return out

    for status, (detail, _) in _run(w, go):
        assert (status, detail["code"]) == (403, "approval_required")
    assert _studio_runs(w) == 0 and _record_row(w, w.rec) == before


def test_r8_a_request_spends_only_on_its_own_package_member_and_action(rworld) -> None:
    w = rworld
    card = {"id": w.rec, "data": {"notes": "Card note"}}
    before2, before_ben = _record_row(w, w.rec2), _record_row(w, w.rec_ben)

    async def go() -> dict[str, Any]:
        rid = (await _delegated(w.a, ANA, w.pkg, "change", card))[1]["request_id"]
        await _decide(w, rid, "approve")
        got: dict[str, Any] = {
            "pkg2": await _delegated(w.a, ANA, w.pkg2, "change", None, rid),
            "ben": await _delegated(w.a, BEN, w.pkg_ben, "change", None, rid),
            "org_b": await _delegated(w.b, ANA, w.pkg_b, "change", None, rid),
            "add": await _delegated(w.a, ANA, w.pkg, "add", None, rid),
            "remove": await _delegated(w.a, ANA, w.pkg, "remove", None, rid),
        }
        # The owner points "change" at the delete of the object. The card
        # named an update, so the request does not spend.
        await _set_action(w, "change", "object:visit.delete")
        got["uses"] = await _delegated(w.a, ANA, w.pkg, "change", None, rid)
        got["between"] = await _state(w, rid)
        await _set_action(w, "change", "object:visit.update")
        got["ran"] = await _delegated(w.a, ANA, w.pkg, "change", None, rid)
        return got

    got = _run(w, go)
    codes = {k: (got[k][0], got[k][1][0]["code"])
             for k in ("pkg2", "ben", "org_b", "add", "remove", "uses")}
    assert codes == {
        "pkg2": (404, "request_not_found"), "ben": (404, "request_not_found"),
        "org_b": (404, "request_not_found"), "add": (409, "request_mismatch"),
        "remove": (409, "request_mismatch"), "uses": (409, "request_mismatch")}
    assert got["between"] == "approved"
    assert (_record_row(w, w.rec2), _record_row(w, w.rec_ben)) == (before2, before_ben)
    assert got["ran"][0] == 200 and _data_of(w, w.rec)["notes"] == "Card note"


def test_r8_a_wrong_body_or_a_missing_object_opens_no_card(rworld) -> None:
    w = rworld

    async def go() -> list[Any]:
        return [await _delegated(w.a, ANA, w.pkg, "add", {"data": "not an object"}),
                await _delegated(w.a, ANA, w.pkg, "remove", {"id": w.rec, "data": {}}),
                await _delegated(w.a, ANA, w.pkg_empty, "add", {"data": _DATA})]

    bad, extra, missing = _run(w, go)
    assert (bad[0], bad[1][0]["code"]) == (422, "bad_input")
    assert (extra[0], extra[1][0]["code"]) == (422, "bad_input")
    assert (missing[0], missing[1][0]["code"]) == (404, "object_missing")
    assert _studio_runs(w) == 0
    assert _record_row(w, w.rec) is not None


# ── al to an. Fix round 1 ──────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["add", "change", "remove"])
def test_r8_a_write_with_no_ask_marker_opens_no_card_and_writes_nothing(rworld, action) -> None:
    """The flag is on, and the call has no proof, no ``ask=1`` and no
    ``request_id``: the unsigned Save of the member's own screen. It is 403
    ``approval_required``, as before B2c-2a, and nothing changes."""
    w = rworld
    card, _ = _bodies(w)[action]
    count, before = _record_count(w, w.obj), _record_row(w, w.rec)
    status, (detail, _) = _run(w, lambda: _delegated(w.a, ANA, w.pkg, action, card, ask=False))
    assert (status, detail["code"]) == (403, "approval_required")
    assert _studio_runs(w) == 0
    assert (_record_count(w, w.obj), _record_row(w, w.rec)) == (count, before)
    # The same call with the marker asks.
    status, body = _run(w, lambda: _delegated(w.a, ANA, w.pkg, action, card))
    assert (status, body["state"]) == (202, "awaiting")
    assert _studio_runs(w) == 1


def test_r8_an_ask_after_a_run_opens_a_new_request(rworld) -> None:
    w = rworld
    card = {"id": w.rec, "data": {"notes": "Card note"}}

    async def go() -> tuple[str, Any, str, Any]:
        first = (await _delegated(w.a, ANA, w.pkg, "change", card))[1]["request_id"]
        await _decide(w, first, "approve")
        ran = await _delegated(w.a, ANA, w.pkg, "change", None, first)
        again = await _delegated(w.a, ANA, w.pkg, "change", card)
        retry = await _delegated(w.a, ANA, w.pkg, "change", card)
        return first, ran, again[1]["request_id"], (again, retry)

    first, ran, second, (again, retry) = _run(w, go)
    assert ran[0] == 200
    assert again[0] == 202 and again[1]["state"] == "awaiting"
    assert second != first
    # A retry while the new request waits gives that request again.
    assert retry[0] == 202 and retry[1]["request_id"] == second
    assert _studio_runs(w) == 2


def test_r8_an_ask_after_a_decline_opens_a_new_request(rworld) -> None:
    w = rworld
    card = {"id": w.rec}

    async def go() -> tuple[str, str, str]:
        first = (await _delegated(w.a, ANA, w.pkg, "remove", card))[1]["request_id"]
        same = (await _delegated(w.a, ANA, w.pkg, "remove", card))[1]["request_id"]
        await _decide(w, first, "decline")
        new = (await _delegated(w.a, ANA, w.pkg, "remove", card))[1]["request_id"]
        return first, same, new

    first, same, new = _run(w, go)
    assert same == first, "a retry while the request waits gives the same request"
    assert new != first
    assert _studio_runs(w) == 2 and _record_row(w, w.rec) is not None


def test_r8_two_asks_at_once_after_a_close_open_one_new_request(rworld) -> None:
    w = rworld
    card = {"id": w.rec}

    async def go() -> tuple[str, list[Any]]:
        first = (await _delegated(w.a, ANA, w.pkg, "remove", card))[1]["request_id"]
        await _decide(w, first, "decline")
        both = await asyncio.gather(_delegated(w.a, ANA, w.pkg, "remove", card),
                                    _delegated(w.a, ANA, w.pkg, "remove", card))
        return first, list(both)

    first, both = _run(w, go)
    assert [s for s, _ in both] == [202, 202]
    ids = {b["request_id"] for _, b in both}
    assert len(ids) == 1 and first not in ids
    assert _studio_runs(w) == 2


def test_r8_an_expired_request_is_409_writes_nothing_and_a_new_ask_opens_one(rworld) -> None:
    w = rworld
    card = {"id": w.rec}

    async def go() -> tuple[str, Any, str]:
        first = (await _delegated(w.a, ANA, w.pkg, "remove", card))[1]["request_id"]
        await _decide(w, first, "approve")
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE pm_agent_run_activities SET content_metadata = jsonb_set("
                " content_metadata, '{request,expires_at}',"
                " to_jsonb(CAST(now() - interval '1 minute' AS text)))"
                " WHERE id = CAST(:i AS uuid)"), {"i": first})
        late = await _delegated(w.a, ANA, w.pkg, "remove", None, first)
        new = (await _delegated(w.a, ANA, w.pkg, "remove", card))[1]["request_id"]
        return first, late, new

    first, late, new = _run(w, go)
    status, (detail, _) = late
    assert (status, detail["code"]) == (409, "request_expired")
    assert _record_row(w, w.rec) is not None
    assert new != first
    reasons = [r.get("reason") for r in _ran(w, w.pkg, "remove")]
    assert "request_expired" in reasons


# ── ao. Fix round 2: one read, a constant cost ─────────────────────────────


def test_r8_three_runs_of_one_change_each_open_a_new_request_at_a_constant_cost(
        rworld) -> None:
    """A checklist toggle, asked, approved and run three times in a row. Each
    ask opens a new request with the same number of statements, so the cost
    does not grow with the closed requests of the change."""
    from acb_common.db import get_engine
    from sqlalchemy import event

    w = rworld
    card = {"id": w.rec, "data": {"status": "done"}}
    counts: list[int] = []
    ids: list[str] = []

    async def go() -> None:
        engine = get_engine().sync_engine
        seen: list[str] = []

        def _count(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            for _ in range(3):
                seen.clear()
                status, body = await _delegated(w.a, ANA, w.pkg, "change", card)
                assert status == 202 and body["state"] == "awaiting", body
                counts.append(len(seen))
                ids.append(body["request_id"])
                await _decide(w, body["request_id"], "approve")
                ran = await _delegated(w.a, ANA, w.pkg, "change", None, body["request_id"])
                assert ran[0] == 200, ran
        finally:
            event.remove(engine, "before_cursor_execute", _count)

    _run(w, go)
    assert len(set(ids)) == 3
    assert counts[0] == counts[1] == counts[2], counts
    assert _studio_runs(w) == 3


def test_r8_the_read_of_the_open_request_is_a_bounded_index_scan(rworld) -> None:
    """With the plain scan off, the planner reads ``pm_agent_runs`` through
    ``pm_agent_runs_app_member``, and the window of a live request on
    ``created_at`` is part of the index condition. So the read never walks the
    whole history of the member. ``clock_timestamp()`` is volatile and cannot
    bound an index, so the window reads ``now()``."""
    w = rworld
    params = {"org": w.a, "me": ANA, "app": "studio",
              "binding": f"session:{ANA}:studio:{w.pkg}", "digest": "0" * 64,
              "window": app_requests.OPEN_WINDOW_SECONDS}
    with w.p.admin_engine.begin() as c:
        c.execute(text("SET LOCAL enable_seqscan = off"))
        lines = [r[0] for r in c.execute(
            text(f"EXPLAIN {app_requests._OPEN_OF_CHANGE_SQL}"), params)]
    plan = "\n".join(lines)
    assert "Seq Scan on pm_agent_runs" not in plan, plan
    at = next(i for i, line in enumerate(lines) if "on pm_agent_runs r" in line)
    assert "pm_agent_runs_app_member" in lines[at], plan
    cond = lines[at + 1]
    assert "Index Cond" in cond and "created_at >" in cond, plan


# ── ap. The chat tools of B2c-2b on the real door ──────────────────────────
#
# Acceptance (spec row B2c-2b). The three write tools of Mira call the door
# over HTTP as the member, with no proof, as a chat does. An ``httpx``
# transport carries each request into a FastAPI app that holds the REAL Studio
# router, and it records the method, the path, the query and the body.
#
# ap. A create from chat asks ONCE: one request, nothing written, and the
#     result says not saved. The member's list of open requests (``list_open``,
#     the list of Integrations, AI agents, Waiting for you) holds that Studio
#     request. A resend before the approval writes nothing. After the member
#     approves, the resend runs the stored change once, with no body. A second
#     resend writes nothing.
# aq. An update that the member declines writes nothing, and the tool says so.
#     A delete after the approval deletes the record once.
# ar. With ``APP_REQUESTS_ORGS`` off, a write from chat opens no request and
#     says that record changes from chat are not open yet.
# as. Fix round 1: on the real package read, a key that is no field opens no
#     request, and the tool says so before any ask.
#
# **The mutations (R7).** Each ran on 2026-11-06 on a committed tree, and was
# restored with git. See the docstring of ``test_studio_record_writes.py``.

import httpx  # noqa: E402
from acb_auth import get_current_user  # noqa: E402
from acb_skills import studio_tools as st  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi import Request as _HttpRequest  # noqa: E402
from gateway.routes import agent_requests  # noqa: E402
from gateway.routes.studio import router as studio_router  # noqa: E402

#: The plain record actions of the package of the tools: no ``set``, so the
#: tools may use each one.
_PLAIN_ACTIONS: list[dict[str, Any]] = [
    {"id": "find", "uses": "object:visit.list"},
    {"id": "peek", "uses": "object:visit.get"},
    {"id": "add", "uses": "object:visit.create"},
    {"id": "change", "uses": "object:visit.update"},
    {"id": "remove", "uses": "object:visit.delete"},
]


class _Seen(httpx.AsyncBaseTransport):
    """The ASGI transport into *app*, which records each request first."""

    def __init__(self, app: FastAPI, seen: list[tuple[str, str, str, bytes]]) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.seen = seen

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path, request.url.query.decode(),
                          request.content))
        return await self.inner.handle_async_request(request)


@pytest.fixture
def tworld(rworld, monkeypatch):
    """The request world, with a package of Ana whose record actions are
    plain, and the chat tools wired to the real Studio router."""
    w = rworld
    mid = f"door-{TAG}-tools{uuid.uuid4().hex[:6]}"
    manifest = {"studio": 1, "id": mid, "name": "Visits", "purpose": "Track install visits.",
                "objects": [{"name": "visit", "fields": _FIELDS}], "actions": _PLAIN_ACTIONS}

    async def setup() -> None:
        w.tpkg = await _package(w.a, ANA, manifest)
        w.tobj, w.trec = await _object_with_record(w.a, w.tpkg)

    _run(w, setup)
    app = FastAPI()
    app.include_router(studio_router)

    async def as_member(request: _HttpRequest) -> UserContext:
        return _user(w.a, request.headers.get("X-User-Email", ""))

    app.dependency_overrides[get_current_user] = as_member
    w.seen = []
    real = httpx.AsyncClient
    monkeypatch.setattr(st.httpx, "AsyncClient",
                        lambda **kw: real(transport=_Seen(app, w.seen), **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: ANA)
    yield w
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE app_id = CAST(:p AS uuid)"), {"p": w.tpkg})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = CAST(:p AS uuid)"),
                  {"p": w.tpkg})
        c.execute(text("DELETE FROM apps WHERE id = CAST(:p AS uuid)"), {"p": w.tpkg})


def _chat(w: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` as a chat runs a tool: the org of the run is bound, as
    the gateway binds it for the member of the internal bearer."""
    async def go() -> Any:
        token = bind_tenant(w.a)
        try:
            return await fn()
        finally:
            release_tenant(token)
    return _run(w, go)


async def _open_rows(w: Any) -> list[dict[str, Any]]:
    """The open requests of Ana: the store read of the list route."""
    async with tenant_session(w.a) as db:
        return (await app_requests.list_open(db, org=w.a, member=ANA)).rows


def _door_posts(w: Any) -> list[tuple[str, str, bytes]]:
    return [(path.rsplit("/", 1)[-1], query, body) for method, path, query, body in w.seen
            if method == "POST"]


def test_r8_ap_a_chat_create_asks_once_and_the_resend_after_the_approval_runs_once(
        tworld, monkeypatch) -> None:
    w = tworld
    count = _record_count(w, w.tobj)
    data = {**_DATA, "site": "Chat site"}
    monkeypatch.setattr(get_settings(), "agent_access_enabled", True)

    async def go() -> dict[str, Any]:
        got: dict[str, Any] = {"asked": await st.studio_record_create(w.tpkg, "visit", data=data)}
        rid = got["asked"]["request_id"]
        got["after_ask"] = _record_count(w, w.tobj)
        got["open"] = await _open_rows(w)
        # The list route of Integrations, AI agents, Waiting for you, as Ana.
        await agent_requests._require_access(user=_user(w.a, ANA))
        got["route"] = await agent_requests.list_app_requests(user=_user(w.a, ANA))
        got["early"] = await st.studio_record_create(w.tpkg, "visit", request_id=rid)
        got["after_early"] = _record_count(w, w.tobj)
        await _decide(w, rid, "approve")
        got["open_after"] = await _open_rows(w)
        got["ran"] = await st.studio_record_create(w.tpkg, "visit", request_id=rid)
        got["after_ran"] = _record_count(w, w.tobj)
        got["again"] = await st.studio_record_create(w.tpkg, "visit", request_id=rid)
        return got

    got = _chat(w, go)
    asked = got["asked"]
    assert (asked["ok"], asked["saved"], asked["state"]) == (False, False, "awaiting"), asked
    assert asked["note"] == st._ASKED_NOTE and asked["expires_at"]
    rid = asked["request_id"]
    assert got["after_ask"] == count, "an ask writes nothing"
    # The member sees the one Studio request in the list of Integrations.
    mine = [r for r in got["open"] if r["request_id"] == rid]
    assert len(mine) == 1 and len(got["open"]) == 1, got["open"]
    row = mine[0]
    assert (row["app"], row["client_via"], row["state"], row["class"]) == (
        "studio", "studio:door", "awaiting", "B")
    assert row["card"]["client"] == "Studio" and row["card"]["title"] == "Add a record to visit."
    assert row["call"]["body"] == {"data": data} and row["subject"] == w.tpkg
    assert [r["request_id"] for r in got["route"]["rows"]] == [rid]
    # Before the approval, nothing runs.
    assert got["early"]["saved"] is False and got["early"]["code"] == "request_awaiting"
    assert got["after_early"] == count
    assert got["open_after"] == [], "an answered request leaves the list"
    # After it, the resend runs the stored change once.
    ran = got["ran"]
    assert (ran["ok"], ran["saved"]) == (True, True), ran
    assert got["after_ran"] == count + 1
    assert _data_of(w, ran["record_id"])["site"] == "Chat site"
    again = got["again"]
    assert (again["saved"], again["code"]) == (False, "request_spent"), again
    assert _record_count(w, w.tobj) == count + 1
    # The wire: one ask with the marker and the body, then three resends with
    # the request and NO body. No call names a decision.
    posts = _door_posts(w)
    assert posts[0][:2] == ("add", "ask=1") and json.loads(posts[0][2]) == {"data": data}
    assert posts[1:] == [("add", f"request_id={rid}", b"")] * 3
    assert all("decision" not in path for _m, path, _q, _b in w.seen)
    rows = _ran(w, w.tpkg, "add")
    assert [(r["outcome"], r.get("reason"), r.get("request_id")) for r in rows] == [
        ("asked", None, rid), ("refused", "request_awaiting", rid), ("ran", None, rid),
        ("refused", "request_spent", rid)]


def test_r8_aq_a_declined_update_writes_nothing_and_an_approved_delete_runs_once(
        tworld) -> None:
    w = tworld
    before = _record_row(w, w.trec)

    async def go() -> dict[str, Any]:
        got: dict[str, Any] = {}
        asked = await st.studio_record_update(w.tpkg, "visit", w.trec, {"notes": "From chat"})
        got["asked"] = asked
        await _decide(w, asked["request_id"], "decline")
        got["declined"] = await st.studio_record_update(
            w.tpkg, "visit", request_id=asked["request_id"])
        got["after_decline"] = _record_row(w, w.trec)
        gone = await st.studio_record_delete(w.tpkg, "visit", w.trec)
        got["gone_asked"] = gone
        got["after_gone_ask"] = _record_row(w, w.trec)
        await _decide(w, gone["request_id"], "approve")
        got["deleted"] = await st.studio_record_delete(
            w.tpkg, "visit", request_id=gone["request_id"])
        return got

    got = _chat(w, go)
    assert got["asked"]["saved"] is False and got["asked"]["state"] == "awaiting"
    declined = got["declined"]
    assert (declined["saved"], declined["code"]) == (False, "request_declined")
    assert "nothing was written" in declined["errors"][0]["message"]
    assert got["after_decline"] == before, "a declined card writes nothing"
    assert got["gone_asked"]["saved"] is False and got["after_gone_ask"] == before
    deleted = got["deleted"]
    assert (deleted["ok"], deleted["saved"], deleted["record_id"]) == (True, True, w.trec)
    assert _record_row(w, w.trec) is None
    assert [q for _a, q, b in _door_posts(w) if b == b""] == [
        f"request_id={got['asked']['request_id']}",
        f"request_id={got['gone_asked']['request_id']}"]


def test_r8_ar_with_the_org_flag_off_a_chat_write_opens_no_request(tworld, monkeypatch) -> None:
    w = tworld
    monkeypatch.setattr(get_settings(), "app_requests_orgs", "")
    runs = _studio_runs(w)

    async def go() -> dict[str, Any]:
        return await st.studio_record_create(w.tpkg, "visit", data=dict(_DATA))

    out = _chat(w, go)
    assert (out["ok"], out["saved"], out["code"], out["status"]) == (
        False, False, "approval_required", 403)
    assert out["errors"][0]["message"] == st.WRITE_REFUSALS["approval_required"]
    assert _studio_runs(w) == runs
    assert [r["outcome"] for r in _ran(w, w.tpkg, "add")] == ["refused"]


def test_r8_as_a_key_that_is_no_field_opens_no_request_on_the_real_package_read(
        tworld) -> None:
    """Fix round 1 (P2), on the real package read: its ``objects`` hold the
    fields that the tool checks. A key that is no field gets its sentence
    before an ask, so no card waits for a change that the store refuses."""
    w = tworld
    runs = _studio_runs(w)

    async def go() -> dict[str, Any]:
        return await st.studio_record_create(w.tpkg, "visit", data={"site": "A", "sight": "B"})

    out = _chat(w, go)
    assert out["saved"] is False and out["errors"][0]["message"] == st._UNKNOWN_FIELD.format(
        object="visit", keys="sight")
    assert _studio_runs(w) == runs and _door_posts(w) == []
    assert _ran(w, w.tpkg, "add") == []
