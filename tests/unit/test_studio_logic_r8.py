"""First-party logic modules on a real database (STU-B4a, R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.7 layer 3 and §14 B4a. Code:
``gateway/studio/logic/apply.py`` and the door ``gateway/routes/studio/invoke.py``
over ``gateway/studio/objects_store.py``. The hermetic half is
``test_studio_logic.py``.

The real handler runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Run it on a PRIVATE ladder
database, one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_logic_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

The module is ``test_echo``. The registry ships empty, and the fixture adds it.

**Acceptance (B4a).**

a. A good first write and a bad second write leave ZERO rows. The refusal is
   422 and names the code of the store.
b. A good batch writes each record, one audit row for each write with the
   outcome ``ran``, and a change history row with no ``via``.
c. An object that the package lacks, and that ANOTHER package of the org holds,
   or a package of org B holds, is 404 ``object_missing``. Nothing is written.
d. A viewer (J2a, installed read-only) gets 403 ``read_only``, also for a preview.
e. A preview leaves zero rows and sends zero events. A live batch sends one
   event for each applied write, after the commit.
f. A module that raises writes nothing. The answer holds none of its text.
g. 51 writes are refused before the store runs a statement.
h. A dark org gets 403 ``source_not_open`` and the module does not run.
i. A call that is not direct gets 403 ``approval_required`` with no card, also
   with ``ask=1`` and with ``APP_REQUESTS_ORGS`` on.
j. The reader hides a row that the viewer of the call cannot read (C8a).
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.studio_access import Viewer
from fastapi import HTTPException
from gateway.routes.studio import invoke, packages
from gateway.studio import logic, objects_store, packages_store
from gateway.studio.logic import ProposedWrite
from gateway.studio.logic import apply as logic_apply
from ingestion import event_hooks
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_studio_action_door_r8 import (
    DIRECT_KEY,
    _headers,
    _Request,
    _run,
    _user,
)
from tests.unit.test_studio_logic import EchoInput, _echo
from tests.unit.test_studio_package_viewer_r8 import (
    OPS,
    PUBLISHER,
)
from tests.unit.test_studio_package_viewer_r8 import (
    _call as pcall,
)
from tests.unit.test_studio_package_viewer_r8 import (
    _door as pdoor,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@b4a.test"
OWEN = f"owen.{TAG}@b4a.test"
VIEWER = f"vera.{TAG}@b4a.test"
_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
]
_SLUG_LIKE = f"b4a{TAG}-%"
SECRET = "SECRET-TEXT-OF-THE-ERROR"


def _manifest(mid: str) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Echo visits", "purpose": "Try a logic module.",
        "objects": [{"name": "visit", "fields": _FIELDS}],
        "actions": [{"id": "echo", "uses": "logic:test_echo"}],
    }


def _echo_module() -> logic.LogicModule:
    return logic.LogicModule(
        name="test_echo", input_model=EchoInput, reads=frozenset({"visit"}),
        writes=frozenset({("visit", "create"), ("visit", "update"), ("visit", "delete"),
                          ("elsewhere", "create")}),
        run=_echo, title="Echo")


async def _package(org: str, member: str, mid: str, objects: tuple[str, ...]) -> tuple[str, dict]:
    ids: dict[str, str] = {}
    async with tenant_session(org) as db:
        made = await packages_store.create_package(db, member, _manifest(mid))
        for name in objects:
            obj = await objects_store.define_object(
                db, member, package_id=made.id, name=name, fields=_FIELDS)
            assert obj is not None
            ids[name] = obj.id
    return made.id, ids


@pytest.fixture
def lworld(promoted, monkeypatch):  # noqa: F811
    """Org A: a package of Ana with the object ``visit`` and one record, and a
    second package of Ana with the object ``elsewhere``. Org B: a package of a
    member with Ana's email, with ``visit`` and ``elsewhere``. The flag is on
    for org A and off for org B. The module ``test_echo`` is registered."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "studio_logic_orgs", p.org_a)
    monkeypatch.setattr(get_settings(), "studio_direct_key", DIRECT_KEY)
    monkeypatch.setitem(logic.MODULES, "test_echo", _echo_module())
    invoke._IN_FLIGHT.clear()
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, mid=f"b4a{TAG}-{run}")

    async def setup() -> None:
        w.pkg, objs = await _package(w.a, ANA, f"{w.mid}-a", ("visit",))
        w.obj = objs["visit"]
        w.pkg2, objs2 = await _package(w.a, ANA, f"{w.mid}-a2", ("elsewhere",))
        w.pkg_b, objs_b = await _package(w.b, ANA, f"{w.mid}-b", ("visit", "elsewhere"))
        w.obj_b = objs_b["visit"]
        w.else_a, w.else_b = objs2["elsewhere"], objs_b["elsewhere"]
        async with tenant_session(w.a) as db:
            rec = await objects_store.create_record(
                db, ANA, w.obj, {"site": "Seed", "status": "planned"}, package_id=w.pkg,
                viewer=Viewer(ANA))
            assert rec is not None
            w.rec = rec.id

    _run(w, setup)
    yield w
    invoke._IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})


async def _door(org: str, email: str, pid: str, payload: Any, *, signed: bool = True,
                query: dict[str, str] | None = None, action: str = "echo") -> tuple[int, Any]:
    """The real handler. A signed call is a direct call of the member."""
    path = f"/studio/apps/{pid}/actions/{action}"
    request = _Request(payload, headers=_headers(pid, action, email) if signed else {},
                       path=path, query=query or {})
    token = bind_tenant(org)
    try:
        return 200, await invoke.invoke_action(pid, action, request=request,
                                               user=_user(org, email))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _w(op: str, obj: str = "visit", id: str | None = None, data: Any = None) -> dict[str, Any]:
    return {"object": obj, "op": op, "id": id, "data": data}


def _count(w: Any, oid: str) -> int:
    with w.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_records WHERE object_id = CAST(:o AS uuid)"),
            {"o": oid}).scalar_one())


def _audits(w: Any, pid: str) -> list[dict[str, Any]]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) ORDER BY id"),
            {"p": pid}).all()
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


def _changes(w: Any, oid: str) -> list[tuple[str, str | None]]:
    with w.p.admin_engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(text(
            "SELECT op, via_kind FROM studio_record_changes "
            "WHERE object_id = CAST(:o AS uuid) ORDER BY id"), {"o": oid}).all()]


def _spy_module(monkeypatch) -> list[int]:
    """Count the runs of the module."""
    ran: list[int] = []

    async def run(inp: Any, reader: Any) -> Any:
        ran.append(1)
        return await _echo(inp, reader)

    monkeypatch.setitem(logic.MODULES, "test_echo", _echo_module().__class__(
        **{**_echo_module().__dict__, "run": run}))
    return ran


_GOOD = {"site": "Hosur", "status": "planned"}


# ── a. All or none ─────────────────────────────────────────────────────────


def test_r8_a_good_first_write_and_a_bad_second_write_leave_zero_rows(lworld) -> None:
    w = lworld
    before, history = _count(w, w.obj), _changes(w, w.obj)
    bad = {"writes": [_w("create", data=_GOOD), _w("create", data={"no_such_field": "x"})]}
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, bad))
    assert status == 422 and got["code"] == "field_invalid", got
    assert _count(w, w.obj) == before and _changes(w, w.obj) == history
    rows = _audits(w, w.pkg)
    assert [(r["outcome"], r["reason"]) for r in rows] == [("refused", "field_invalid")]
    assert "Hosur" not in json.dumps(rows)


def test_r8_a_rule_of_the_second_write_also_rolls_the_first_back(lworld) -> None:
    w = lworld
    before = _count(w, w.obj)
    bad = {"writes": [_w("create", data=_GOOD), _w("update", id=str(uuid.uuid4()),
                                                   data={"site": "x"})]}
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, bad))
    assert status == 404, got
    assert _count(w, w.obj) == before


# ── b. A good batch ────────────────────────────────────────────────────────


def test_r8_a_good_batch_writes_each_record_one_audit_row_each_and_no_via(lworld) -> None:
    w = lworld
    before = _count(w, w.obj)
    body = {"writes": [_w("create", data=_GOOD),
                       _w("update", id=w.rec, data={"status": "done"}),
                       _w("create", data={"site": "Pune", "status": "done"})]}
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, body))
    assert status == 200, got
    assert [(r["object"], r["op"]) for r in got["writes"]] == [
        ("visit", "create"), ("visit", "update"), ("visit", "create")]
    assert got["writes"][1]["id"] == w.rec and got["writes"][0]["id"]
    assert "Hosur" not in json.dumps(got)
    assert _count(w, w.obj) == before + 2
    rows = _audits(w, w.pkg)
    assert [(r["outcome"], r["op"], r["uses"], r["class"]) for r in rows] == [
        ("ran", "create", "logic:test_echo", "B"), ("ran", "update", "logic:test_echo", "B"),
        ("ran", "create", "logic:test_echo", "B")]
    assert all("Hosur" not in json.dumps(r) and "Pune" not in json.dumps(r) for r in rows)
    history = _changes(w, w.obj)
    assert [h[1] for h in history] == [None] * len(history) and len(history) == 4


# ── c. The package is the wall ─────────────────────────────────────────────


def test_r8_an_object_of_another_package_or_org_is_refused_and_writes_nothing(lworld) -> None:
    w = lworld
    counts = (_count(w, w.obj), _count(w, w.else_a), _count(w, w.else_b), _count(w, w.obj_b))
    body = {"writes": [_w("create", data=_GOOD), _w("create", obj="elsewhere", data=_GOOD)]}
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, body))
    assert (status, got["code"]) == (404, "object_missing"), got
    assert counts == (_count(w, w.obj), _count(w, w.else_a), _count(w, w.else_b),
                      _count(w, w.obj_b))
    assert [(r["outcome"], r["reason"]) for r in _audits(w, w.pkg)] == [
        ("not_found", "object_missing")]


def test_r8_org_b_and_another_member_get_404_on_the_package_of_org_a(lworld) -> None:
    w = lworld
    body = {"writes": [_w("create", data=_GOOD)]}

    async def go() -> list[tuple[int, Any]]:
        return [await _door(w.b, ANA, w.pkg, body), await _door(w.a, OWEN, w.pkg, body)]

    for status, got in _run(w, go):
        assert status == 404 and got == "Not found", got
    assert _count(w, w.obj) == 1


# ── d. A viewer ────────────────────────────────────────────────────────────


@pytest.fixture
def installed(lworld, monkeypatch):
    """A package of Owen with the logic action, installed for the group ``ops``,
    which holds Owen and Vera. Vera is a viewer."""
    w = lworld
    p = w.p
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        users = {}
        for email in (OWEN, VIEWER):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": p.org_a}).scalar_one())
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (:org, :s, :s) RETURNING id"),
            {"org": p.org_a, "s": OPS.removeprefix("group:")}).scalar_one())
        for email in (OWEN, VIEWER):
            c.execute(text("INSERT INTO org_group_member (group_id, user_id, organization_id) "
                           "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": p.org_a})

    async def setup() -> None:
        status, got = await pcall("create_package", w.a, OWEN, payload=_manifest(f"{w.mid}-v"))
        assert status == 200, got
        w.vpid = str(got["package"]["id"])
        status, got = await pcall("publish", w.a, OWEN, w.vpid, payload={})
        assert status == 200, got
        status, got = await pcall("install_package", w.a, OWEN, w.vpid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got

    _run(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_group WHERE slug = :s"),
                  {"s": OPS.removeprefix("group:")})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": [OWEN, VIEWER]})


def test_r8_a_viewer_gets_read_only_and_nothing_is_written(installed, monkeypatch) -> None:
    w = installed
    ran = _spy_module(monkeypatch)
    body = {"writes": [_w("create", data=_GOOD)]}

    async def go() -> list[tuple[int, Any]]:
        return [await pdoor(w.a, VIEWER, w.vpid, "echo", body),
                await pdoor_preview(w.a, VIEWER, w.vpid, body)]

    for status, got in _run(w, go):
        assert (status, got["code"]) == (403, "read_only"), got
    assert ran == []
    with w.p.admin_engine.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM studio_records r JOIN studio_objects o ON o.id = r.object_id "
            "WHERE o.package_id = CAST(:p AS uuid)"), {"p": w.vpid}).scalar_one()
    assert n == 0


async def pdoor_preview(org: str, email: str, pid: str, body: Any) -> tuple[int, Any]:
    request = _Request(body, path=f"/studio/apps/{pid}/actions/echo", query={"preview": "1"})
    token = bind_tenant(org)
    try:
        return 200, await invoke.invoke_action(pid, "echo", request=request,
                                               user=_user(org, email))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


# ── e. The preview and the events ──────────────────────────────────────────


def test_r8_a_preview_leaves_zero_rows_and_sends_zero_events_and_a_live_batch_sends_each(
        lworld) -> None:
    w = lworld
    got_events: list[tuple[str, dict[str, Any]]] = []

    async def sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
        got_events.append((event_type, payload))

    body = {"writes": [_w("create", data=_GOOD), _w("update", id=w.rec, data={"status": "done"}),
                       _w("create", data={"site": "Pune"})]}
    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(sink)
    try:
        before, history = _count(w, w.obj), _changes(w, w.obj)
        status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, body, signed=False,
                                            query={"preview": "1"}))
        assert status == 200, got
        assert got["preview"] is True and got["simulated"]["uses"] == "logic:test_echo"
        assert [(r["op"], r["id"] is None) for r in got["writes"]] == [
            ("create", True), ("update", False), ("create", True)]
        assert _count(w, w.obj) == before and _changes(w, w.obj) == history
        assert got_events == []
        assert {r["outcome"] for r in _audits(w, w.pkg)} == {"simulated"}
        status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, body))
        assert status == 200, got
        assert sorted(e[0] for e in got_events) == [
            "studio.record.created", "studio.record.created", "studio.record.updated"]
        assert {e[1]["actor"] for e in got_events} == {ANA}
        assert {e[1]["organization_id"] for e in got_events} == {w.a}
        # A refused batch fires no event.
        got_events.clear()
        bad = {"writes": [_w("create", data=_GOOD), _w("create", data={"x": 1})]}
        assert _run(w, lambda: _door(w.a, ANA, w.pkg, bad))[0] == 422
        assert got_events == []
    finally:
        event_hooks.clear_event_sinks()


# ── f, g. A module that raises, and the cap ────────────────────────────────


def test_r8_a_module_that_raises_writes_nothing_and_leaks_no_text(lworld, monkeypatch) -> None:
    w = lworld
    before = _count(w, w.obj)

    async def run(inp: Any, reader: Any) -> Any:
        await reader("visit")
        raise RuntimeError(SECRET)

    monkeypatch.setitem(logic.MODULES, "test_echo", logic.LogicModule(
        **{**_echo_module().__dict__, "run": run}))
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, {}))
    assert (status, got["code"]) == (422, "logic_failed"), got
    assert SECRET not in json.dumps(got) and SECRET not in json.dumps(_audits(w, w.pkg))
    assert _count(w, w.obj) == before


def test_r8_51_writes_are_refused_before_the_store_runs_a_write(lworld, monkeypatch) -> None:
    w = lworld
    calls: list[int] = []
    real = objects_store.apply_writes

    async def spy(*a: Any, **kw: Any) -> Any:
        calls.append(1)
        return await real(*a, **kw)

    monkeypatch.setattr(objects_store, "apply_writes", spy)
    before = _count(w, w.obj)
    body = {"writes": [_w("create", data=_GOOD) for _ in range(51)]}
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, body))
    assert (status, got["code"]) == (422, "too_many_writes"), got
    assert calls == [] and _count(w, w.obj) == before
    # The cap itself passes: 50 writes in one batch.
    ok = {"writes": [_w("create", data=_GOOD) for _ in range(50)]}
    status, got = _run(w, lambda: _door(w.a, ANA, w.pkg, ok))
    assert status == 200 and len(got["writes"]) == 50 and len(calls) == 1
    assert _count(w, w.obj) == before + 50


# ── h, i. The switch and the card ──────────────────────────────────────────


def test_r8_a_dark_org_gets_source_not_open_and_the_module_does_not_run(lworld,
                                                                         monkeypatch) -> None:
    w = lworld
    ran = _spy_module(monkeypatch)
    status, got = _run(w, lambda: _door(w.b, ANA, w.pkg_b, {"writes": [_w("create", data=_GOOD)]}))
    assert (status, got["code"]) == (403, "source_not_open"), got
    assert ran == [] and _count(w, w.obj_b) == 0
    # The same call in org A runs, so the switch is the only difference.
    assert _run(w, lambda: _door(w.a, ANA, w.pkg, {}))[0] == 200
    assert ran == [1]


def test_r8_a_call_that_is_not_direct_gets_approval_required_and_opens_no_card(
        lworld, monkeypatch) -> None:
    w = lworld
    monkeypatch.setattr(get_settings(), "app_requests_orgs", "*")
    ran = _spy_module(monkeypatch)
    body = {"writes": [_w("create", data=_GOOD)]}

    async def go() -> list[tuple[int, Any]]:
        return [await _door(w.a, ANA, w.pkg, body, signed=False),
                await _door(w.a, ANA, w.pkg, body, signed=False, query={"ask": "1"}),
                await _door(w.a, ANA, w.pkg, body, signed=False,
                            query={"request_id": str(uuid.uuid4())})]

    for status, got in _run(w, go):
        assert (status, got["code"]) == (403, "approval_required"), got
    assert ran == [] and _count(w, w.obj) == 1
    with w.p.admin_engine.connect() as c:
        runs = c.execute(text(
            "SELECT count(*) FROM pm_agent_runs WHERE app = 'studio' "
            "AND organization_id = CAST(:o AS uuid)"), {"o": w.a}).scalar_one()
    assert runs == 0


# ── j. The reader is the caller's ──────────────────────────────────────────

_SHEET_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "notes", "type": "text"},
]
_SHEET_ACCESS: list[dict[str, Any]] = [
    {"role": "payee", "read": {"where": "employee = $me"}, "write": "all"},
]


def test_r8_the_reader_hides_a_row_that_the_caller_cannot_read(lworld) -> None:
    """C8a. The reader runs ``query_records`` with the viewer of the call. The
    owner reads each row. A member who is not the owner, with no role, reads
    none. So a module never sees a row that its caller cannot read."""
    w = lworld
    seen: dict[str, list[Any]] = {}

    async def run(inp: Any, reader: Any) -> list[ProposedWrite]:
        seen[inp.read] = await reader("sheet")
        return []

    module = logic.LogicModule(**{**_echo_module().__dict__, "reads": frozenset({"sheet"}),
                                  "run": run})

    async def go() -> None:
        async with tenant_session(w.a) as db:
            sheet = await objects_store.define_object(
                db, ANA, package_id=w.pkg, name="sheet", fields=_SHEET_FIELDS)
            assert sheet is not None
            assert await objects_store.set_access(db, sheet.id, _SHEET_ACCESS, package_id=w.pkg)
            made = await objects_store.create_record(
                db, ANA, sheet.id, {"employee": ANA, "notes": "private"}, package_id=w.pkg,
                viewer=Viewer(ANA))
            assert made is not None
        for who in (ANA, OWEN):
            async with tenant_session(w.a) as db:
                await logic_apply.run_module(
                    db, module=module, body={"read": who}, package_id=w.pkg, viewer=Viewer(who))

    token = bind_tenant(w.a)
    try:
        _run(w, go)
    finally:
        release_tenant(token)
    assert [r["data"]["notes"] for r in seen[ANA]] == ["private"]
    assert seen[OWEN] == []
