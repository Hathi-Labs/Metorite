"""WS-STU B6a — the reads of a run with no viewer, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 B6 and B6a, §19.2 and §19.10
row 10, and ``package_host.md`` §5.1. Code: migration 439 (at build, found
here by content), ``package_host/store.py`` (``set_run_as``, ``read_run_as``),
``resolve.install_seen_by``, the route ``PUT
/studio/packages/{id}/install/run-as`` (``routes/studio/packages.py``) and
``invoke.run_as_read``. The hermetic twin is
``tests/unit/test_studio_run_as.py``.

The real create, publish, install, route and run handlers run on a phase-4
promoted scratch database, as the non-privileged app role, so FORCE RLS binds
each statement. Org A has Owen (the owner, a holder of ``studio:publish``) and
Mia in ``ops``, and Ned in ``eng``. Org B has Zed. Each member holds
``feature:studio`` through one override row. Owen's app is installed for
``ops`` and for the org at version 1, and then his draft gains the action
``extra``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_run_as_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (B6a).**

a. Mia, in ``ops``, is the member of the ``ops`` install. A run lists and
   gets the records, and its audit row names ``system:package:<id>`` and Mia.
   The route writes one ``package.run_as`` row.
b. Ned (not in ``ops``), Zed (org B) and an unknown address each get the same
   422, and nothing is stored.
c. A caller without ``studio:publish`` gets 403, and nothing is stored.
d. An org B id in the body changes nothing in org B.
e. Mia leaves ``ops``: the run is refused, reads no record, and keeps its row.
f. Mia is suspended: the run of the org install is refused.
g. An override denies Mia ``feature:studio``: the run is refused.
h. An undeclared action and an object create are refused, and no record
   changes.
i. An action that only the draft declares is refused (the version pin).
j. A paused install and a paused package are refused.
k. An install of org B is not found from org A, and writes no row.
l. An install with no member is refused, and a clear stops the run.
m. ``no_person_present()`` holds during the read, and not after it.
n. Migration 439: three nullable columns with no default, and the CHECKs
   refuse a system actor and an address in upper case.
o. C8a: each read takes the viewer of the member (``invoke._row_viewer``).
   With no role (before C8b), Mia reads no slip of app R, and Owen, the
   owner, reads each one. With the roles of one map, Mia reads her own slip
   with ``bank_account`` hidden and cannot filter on it, and Ned (``hr``)
   reads both. Ned's grant and Owen's scope never reach Mia's run.
p. WS-STU I1a: the pinned version names a connection. The run of Mia is
   refused with ``needs_connection`` while only Owen holds a row, or while
   her own row is ``needs_reconnect``. Her own ``connected`` row runs it.
   With ``STUDIO_CONNECTION_GATE_ORGS`` off for org A, the run reads as
   before I1a.

**J2b-1 (path A).** An org install needs an approved live version, so
``_install`` asks and approves it first, with Amy as the approver
(``_studio_approval.approve_live``).

**R7: red first, then mutate.** Each mutation ran on 2026-11-13, on this file
and its hermetic twin together (85 tests), on the private ladder database.

* M1. ``invoke.run_as_read``: skip the access check (``may_run_as``). Cases f
  and g go red, and 4 tests of the twin (6 failed of 85). Case e stays
  green, because the sight of the group install stops that run too.
* M2. ``invoke._run_as_session``: read the declaration from the draft of the
  owner (``packages_store.get_package``) in place of the pinned version.
  Case i goes red, and 9 tests of the twin (10 failed of 85).

Case o (C8a) ran its mutations on 2026-11-13, on this file and its twin
(89 tests), after the merge of C8a and the move to migration 439.

* M3. ``invoke._run_as_session``: give the read the viewer of the package
  owner, who passes every rule. Both case o tests go red: Mia reads both
  slips. 4 tests of the twin go red too, because the fake session runs no
  SQL (6 failed of 89).
* M4. ``invoke._run_as_session``: give the read the member with every role
  of the manifest. Both case o tests go red: Mia reads both slips (2 failed
  of 89). The twin stays green, because its pinned manifest has no roles.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.no_person import no_person_present
from acb_common.studio_access import Viewer
from fastapi import HTTPException, Response
from gateway.routes.studio import invoke, packages
from gateway.studio import objects_store
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.unit._studio_approval import approve_live
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@b6a.test"
#: Mixed case in ``app_user``, so each read folds it.
MIA = f"Mia.{TAG}@b6a.test"
MIA_LOW = MIA.lower()
NED = f"ned.{TAG}@b6a.test"
ZED = f"zed.{TAG}@b6a.test"
NOBODY = f"nobody.{TAG}@b6a.test"
#: J2b-1 (path A): the approver of each org install. Not the owner.
AMY = f"amy.{TAG}@b6a.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
EXT = f"group:ext{TAG}"
_EMAIL_LIKE = f"%.{TAG}@b6a.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"b6a{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
_DATA = ({"site": "Plant 4, Hosur"}, {"site": "Plant 7, Pune"})


def _manifest(mid: str, extra: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [
            {"id": "peek", "uses": "object:visit.get"},
            {"id": "find", "uses": "object:visit.list"},
            {"id": "add", "uses": "object:visit.create"},
            {"id": "remove", "uses": "object:visit.delete"},
            *(extra or []),
        ],
    }


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The parts of a request that the handlers read: the body stream, the
    method, the path, the headers and the query."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "PUT", {}
        self.url = SimpleNamespace(path="/studio/packages/x/install/run-as")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish", "install_package",
                 "set_install_run_as"):
        kwargs["request"] = _Request(payload)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route == "install_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        got = await handler(**kwargs)
        default = 201 if route == "install_package" else 200
        return response.status_code or default, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _set(w: Any, scope: str, run_as: Any, *, email: str = OWEN,
               grants: tuple[str, ...] = PUBLISHER, extra: dict[str, Any] | None = None,
               pid: str | None = None) -> tuple[int, Any]:
    """The route of B6a, as *email* in org A."""
    body = {"scope": scope, "run_as": run_as, **(extra or {})}
    return await _call("set_install_run_as", w.a, email, pid or w.pid, payload=body,
                       grants=grants)


async def _read(org: str, install: str, action: str, body: Any = None,
                run_id: str = "run-1") -> dict[str, Any]:
    """The run of B6a. No tenant is bound: the run binds its own org."""
    return await invoke.run_as_read(organization_id=org, install_id=install, action=action,
                                    body=body, run_id=run_id)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _live(org: str, email: str, mid: str,
                manifest: dict[str, Any] | None = None) -> str:
    """A new package of *email*, published once."""
    status, got = await _call("create_package", org, email,
                              payload=manifest or _manifest(mid))
    assert status == 200, got
    pid = str(got["package"]["id"])
    status, got = await _call("publish", org, email, pid, payload={})
    assert status == 200, got
    return pid


async def _record(org: str, pid: str, data: dict[str, Any], name: str = "visit") -> str:
    """One record of the object *name*, written by Owen. C8a: the owner passes
    each row rule, so his viewer writes each record."""
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, name, package_id=pid)
        assert obj is not None
        rec = await objects_store.create_record(db, OWEN, obj.id, data, package_id=pid,
                                                viewer=Viewer(OWEN))
        assert rec is not None
    return rec.id


async def _install(org: str, email: str, pid: str, scope: str) -> str:
    if scope == "org":
        # J2b-1 (path A): an org install needs an approved live version.
        await approve_live(org, email, pid, AMY)
    status, got = await _call("install_package", org, email, pid, payload={"scope": scope},
                              grants=PUBLISHER)
    assert status == 201, got
    return str(got["install"]["id"])


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for both. Owen
    publishes app P with two records, installs it for ``ops`` and for the
    org at version 1, then saves a draft that declares ``extra``. Zed
    publishes app Q in org B and installs it for his org."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org in ((OWEN, p.org_a), (MIA, p.org_a), (NED, p.org_a), (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
            c.execute(text(
                "INSERT INTO user_permission_override (user_id, permission, effect, reason, "
                "set_by, organization_id) VALUES (:u, 'feature:studio', 'allow', 'B6a R8', "
                "'seed', :org)"), {"u": users[email], "org": org})
        for group, org, members in ((OPS, p.org_a, (OWEN, MIA)), (ENG, p.org_a, (NED,)),
                                    (EXT, p.org_b, (ZED,))):
            gid = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": org, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, users=users)

    async def setup() -> None:
        mid = f"b6a{TAG}-app{next(_COUNTER)}"
        w.pid = await _live(w.a, OWEN, mid)
        w.recs = [await _record(w.a, w.pid, d) for d in _DATA]
        w.ops = await _install(w.a, OWEN, w.pid, OPS)
        w.org_install = await _install(w.a, OWEN, w.pid, "org")
        draft = _manifest(mid, [{"id": "extra", "uses": "object:visit.list"}])
        status, got = await _call("save_draft", w.a, OWEN, w.pid, payload=draft)
        assert status == 200, got
        w.qid = await _live(w.b, ZED, f"b6a{TAG}-app{next(_COUNTER)}")
        w.q_install = await _install(w.b, ZED, w.qid, "org")

    _run(w, setup)
    yield w
    invoke._READS_IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text(
            "DELETE FROM user_permission_override WHERE user_id IN "
            "(SELECT id FROM app_user WHERE email LIKE :e)"), {"e": _EMAIL_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _admin(world: Any, sql: str, **params: Any) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _run_as_of(world: Any, install: str) -> tuple[Any, Any, Any]:
    """``run_as``, ``run_as_set_by`` and ``run_as_set_at`` of *install*, read
    as the superuser."""
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT run_as, run_as_set_by, run_as_set_at FROM package_install "
            "WHERE id = CAST(:i AS uuid)"), {"i": install}).one()
    return row[0], row[1], row[2]


def _audits(world: Any, pid: str, email: str) -> list[dict[str, Any]]:
    """The rows of *email* for *pid*, oldest first, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND user_email = :e ORDER BY id"),
            {"p": pid, "e": email}).all()
    return [{"org": r[0], **(r[1] if isinstance(r[1], dict) else json.loads(r[1]))}
            for r in rows]


def _runs(world: Any) -> list[dict[str, Any]]:
    """The rows of the runs of app P: the actor is the package."""
    return _audits(world, world.pid, invoke.run_as_actor(world.pid))


def _records(world: Any, pid: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_records r JOIN studio_objects o ON o.id = r.object_id "
            "WHERE o.package_id = CAST(:p AS uuid)"), {"p": pid}).scalar_one())


# ── a. The member of the group reads, and the rows name both ───────────────


def test_r8_a_member_of_the_group_reads_and_the_row_names_both(world) -> None:
    async def go() -> tuple[Any, ...]:
        named = await _set(world, OPS, MIA)
        found = await _read(world.a, world.ops, "find", {}, run_id="run-a")
        one = await _read(world.a, world.ops, "peek", {"id": world.recs[0]}, run_id="run-b")
        return named, found, one

    named, found, one = _run(world, go)
    assert named[0] == 200, named
    assert named[1]["run_as"] == MIA_LOW
    assert named[1]["install"]["id"] == world.ops
    run_as, set_by, set_at = _run_as_of(world, world.ops)
    assert (run_as, set_by) == (MIA_LOW, OWEN) and set_at is not None
    assert found["ok"] is True and found["outcome"] == "ran", found
    assert sorted(r["data"]["site"] for r in found["result"]["records"]) == sorted(
        d["site"] for d in _DATA)
    # A viewer reads no writer of a record (J2a), and neither does the run.
    assert {r["created_by"] for r in found["result"]["records"]} == {""}
    assert one["ok"] is True and one["result"]["record"]["id"] == world.recs[0], one
    rows = _runs(world)
    assert [r["outcome"] for r in rows] == ["ran", "ran"]
    for row, run_id in zip(rows, ("run-a", "run-b"), strict=True):
        assert row["org"] == world.a
        assert (row["via"], row["run_as"], row["install_id"], row["run_id"]) == (
            invoke.RUN_AS_VIA, MIA_LOW, world.ops, run_id)
        assert row["version"] == 1
        assert "records" not in json.dumps(row) and "Hosur" not in json.dumps(row)
    changes = [r for r in _audits(world, world.pid, OWEN) if r.get("op") == "package.run_as"]
    assert len(changes) == 1
    assert (changes[0]["run_as"], changes[0]["was"], changes[0]["install_id"]) == (
        MIA_LOW, None, world.ops)


# ── b, c. Who cannot be the member, and who cannot name one ────────────────


@pytest.mark.parametrize("who", [NED, ZED, NOBODY])
def test_r8_a_member_who_cannot_run_the_install_gets_one_422(world, who: str) -> None:
    status, got = _run(world, lambda: _set(world, OPS, who))
    assert (status, got) == (422, {"code": packages.RUN_AS_REFUSED,
                                   "message": packages.NO_RUN_AS_MEMBER})
    assert _run_as_of(world, world.ops) == (None, None, None)
    assert not [r for r in _audits(world, world.pid, OWEN) if r.get("op") == "package.run_as"]


def test_r8_a_caller_without_the_share_right_gets_403(world) -> None:
    status, got = _run(world, lambda: _set(world, OPS, MIA, grants=STUDIO))
    assert status == 403, got
    assert got["message"] == packages.NO_RUN_AS_RIGHT
    assert _run_as_of(world, world.ops) == (None, None, None)


def test_r8_a_member_of_another_owner_gets_404(world) -> None:
    """Mia does not own app P, so the route names nothing, also with the right."""
    status, got = _run(world, lambda: _set(world, OPS, MIA, email=MIA))
    assert (status, got) == (404, packages.NOT_FOUND)
    assert _run_as_of(world, world.ops) == (None, None, None)


# ── d. An org B id in the body changes nothing ─────────────────────────────


def test_r8_an_org_b_id_in_the_body_changes_nothing(world) -> None:
    extra = {"organization_id": world.b, "install_id": world.q_install, "set_by": ZED}
    status, got = _run(world, lambda: _set(world, OPS, MIA, extra=extra))
    assert status == 200, got
    assert _run_as_of(world, world.ops)[:2] == (MIA_LOW, OWEN)
    assert _run_as_of(world, world.q_install) == (None, None, None)
    # Org A has no install of app Q, so a scope call on it from org A is 404.
    status, got = _run(world, lambda: _set(world, "org", MIA, pid=world.qid))
    assert (status, got) == (404, packages.NOT_FOUND)


# ── e, f, g. The run stops at its next call ────────────────────────────────


def _then_refused(world: Any, install: str, change: Callable[[], None]) -> tuple[Any, Any]:
    """One read that runs, then *change*, then one more read."""
    before = _run(world, lambda: _read(world.a, install, "find", {}))
    change()
    after = _run(world, lambda: _read(world.a, install, "find", {}))
    return before, after


def _assert_stopped(world: Any, before: dict[str, Any], after: dict[str, Any]) -> None:
    assert before["ok"] is True, before
    assert after == {"ok": False, "outcome": "refused", "code": invoke.RUN_AS_LOST,
                     "result": None}, after
    rows = _runs(world)
    assert [r["outcome"] for r in rows] == ["ran", "refused"]
    assert (rows[-1]["reason"], rows[-1]["run_as"]) == (invoke.RUN_AS_LOST, MIA_LOW)


def test_r8_the_run_stops_when_the_member_leaves_the_group(world) -> None:
    status, got = _run(world, lambda: _set(world, OPS, MIA))
    assert status == 200, got
    before, after = _then_refused(world, world.ops, lambda: _admin(
        world, "DELETE FROM org_group_member WHERE user_id = CAST(:u AS uuid)",
        u=world.users[MIA]))
    _assert_stopped(world, before, after)


def test_r8_the_run_stops_when_the_member_is_suspended(world) -> None:
    """The org install shows to each caller, so only the access check stops
    this run (M1)."""
    status, got = _run(world, lambda: _set(world, "org", MIA))
    assert status == 200, got
    before, after = _then_refused(world, world.org_install, lambda: _admin(
        world, "UPDATE app_user SET status = 'suspended' WHERE id = CAST(:u AS uuid)",
        u=world.users[MIA]))
    _assert_stopped(world, before, after)


def test_r8_the_run_stops_when_an_override_denies_the_feature(world) -> None:
    """Mia stays an active member of ``ops``, so only the access check stops
    this run (M1)."""
    status, got = _run(world, lambda: _set(world, OPS, MIA))
    assert status == 200, got
    before, after = _then_refused(world, world.ops, lambda: _admin(
        world, "UPDATE user_permission_override SET effect = 'deny' "
        "WHERE permission = 'feature:studio' AND user_id = CAST(:u AS uuid)",
        u=world.users[MIA]))
    _assert_stopped(world, before, after)


# ── h. The run reads only ──────────────────────────────────────────────────


def test_r8_an_undeclared_action_and_an_object_write_are_refused(world) -> None:
    status, got = _run(world, lambda: _set(world, OPS, MIA))
    assert status == 200, got
    count = _records(world, world.pid)

    async def go() -> list[dict[str, Any]]:
        return [await _read(world.a, world.ops, "nope", {}),
                await _read(world.a, world.ops, "add", {"data": {"site": "Plant 9"}}),
                await _read(world.a, world.ops, "remove", {"id": world.recs[0]})]

    nope, add, remove = _run(world, go)
    assert (nope["code"], add["code"], remove["code"]) == (
        invoke.NOT_DECLARED, invoke.RUN_AS_READS_ONLY, invoke.RUN_AS_READS_ONLY)
    assert not any(r["ok"] for r in (nope, add, remove))
    assert _records(world, world.pid) == count
    rows = _runs(world)
    assert [(r["outcome"], r["reason"]) for r in rows] == [
        ("refused", invoke.NOT_DECLARED), ("refused", invoke.RUN_AS_READS_ONLY),
        ("refused", invoke.RUN_AS_READS_ONLY)]
    assert (rows[1]["class"], rows[2]["class"]) == ("B", "C")


# ── i. The version pin ─────────────────────────────────────────────────────


def test_r8_an_action_of_the_draft_only_is_refused(world) -> None:
    """Owen owns the app and names himself for the org install. His draft
    declares ``extra``, and the install pins version 1, which does not (M2)."""
    status, got = _run(world, lambda: _set(world, "org", OWEN))
    assert status == 200, got

    async def go() -> list[dict[str, Any]]:
        return [await _read(world.a, world.org_install, "extra", {}),
                await _read(world.a, world.org_install, "find", {})]

    extra, find = _run(world, go)
    assert (extra["ok"], extra["code"]) == (False, invoke.NOT_DECLARED), extra
    assert find["ok"] is True, find


# ── j. A paused install or package ─────────────────────────────────────────


def test_r8_a_paused_install_and_a_paused_package_are_refused(world) -> None:
    status, got = _run(world, lambda: _set(world, OPS, MIA))
    assert status == 200, got
    _admin(world, "UPDATE package_install SET state = 'paused' WHERE id = CAST(:i AS uuid)",
           i=world.ops)
    paused_install = _run(world, lambda: _read(world.a, world.ops, "find", {}))
    _admin(world, "UPDATE package_install SET state = 'active' WHERE id = CAST(:i AS uuid)",
           i=world.ops)
    _admin(world, "UPDATE apps SET paused_at = now(), paused_by = 'admin' "
           "WHERE id = CAST(:p AS uuid)", p=world.pid)
    paused_app = _run(world, lambda: _read(world.a, world.ops, "find", {}))
    for got in (paused_install, paused_app):
        assert (got["ok"], got["code"]) == (False, invoke.RUN_AS_LOST), got


# ── k. An install of another org ───────────────────────────────────────────


def test_r8_an_install_of_another_org_is_not_found(world) -> None:
    _admin(world, "UPDATE package_install SET run_as = :m, run_as_set_by = :m, "
           "run_as_set_at = now() WHERE id = CAST(:i AS uuid)", m=ZED, i=world.q_install)

    async def go() -> list[dict[str, Any]]:
        return [await _read(world.a, world.q_install, "find", {}),
                await _read(world.b, world.ops, "find", {})]

    for got in _run(world, go):
        assert got == {"ok": False, "outcome": "not_found", "code": invoke.RUN_AS_NOT_FOUND,
                       "result": None}, got
    assert _audits(world, world.qid, invoke.run_as_actor(world.qid)) == []
    assert _runs(world) == []


# ── l. No member, and a clear ──────────────────────────────────────────────


def test_r8_an_install_with_no_member_is_refused_and_a_clear_stops_the_run(world) -> None:
    async def go() -> list[Any]:
        none = await _read(world.a, world.ops, "find", {})
        named = await _set(world, OPS, MIA)
        ran = await _read(world.a, world.ops, "find", {})
        cleared = await _set(world, OPS, None)
        stopped = await _read(world.a, world.ops, "find", {})
        return [none, named, ran, cleared, stopped]

    none, named, ran, cleared, stopped = _run(world, go)
    assert (none["ok"], none["code"]) == (False, invoke.RUN_AS_NONE), none
    assert named[0] == 200 and ran["ok"] is True
    assert cleared == (200, {"install": cleared[1]["install"], "run_as": None}), cleared
    assert (stopped["ok"], stopped["code"]) == (False, invoke.RUN_AS_NONE), stopped
    run_as, set_by, set_at = _run_as_of(world, world.ops)
    assert (run_as, set_by) == (None, OWEN) and set_at is not None
    rows = _runs(world)
    assert [(r["outcome"], r.get("reason"), r["run_as"]) for r in rows] == [
        ("refused", invoke.RUN_AS_NONE, None), ("ran", None, MIA_LOW),
        ("refused", invoke.RUN_AS_NONE, None)]
    changes = [r for r in _audits(world, world.pid, OWEN) if r.get("op") == "package.run_as"]
    assert [(c["run_as"], c["was"]) for c in changes] == [(MIA_LOW, None), (None, MIA_LOW)]


# ── m. No person is present ────────────────────────────────────────────────


def test_r8_the_run_has_no_person_present(world, monkeypatch) -> None:
    seen: list[bool] = []
    real = invoke._run_object_list

    async def spy(*args: Any, **kwargs: Any) -> None:
        seen.append(no_person_present())
        await real(*args, **kwargs)

    monkeypatch.setattr(invoke, "_run_object_list", spy)
    status, got = _run(world, lambda: _set(world, OPS, MIA))
    assert status == 200, got

    async def go() -> tuple[dict[str, Any], bool]:
        got = await _read(world.a, world.ops, "find", {})
        return got, no_person_present()

    found, after = _run(world, go)
    assert found["ok"] is True, found
    assert seen == [True]
    assert after is False


# ── o. The row rules of C8a bind the run as they bind its member ───────────


def _slips(mid: str) -> dict[str, Any]:
    """App R: a ``slip`` names its ``employee``. ``hr`` reads all, and
    ``employee`` reads the slips where ``employee = $me``, with
    ``bank_account`` hidden."""
    return {
        "studio": 1, "id": mid, "name": "Pay slips", "purpose": "Hold the pay slip of each person.",
        "roles": ["hr", "employee"],
        "objects": [{"name": "slip", "fields": [
            {"key": "site", "type": "text"},
            {"key": "employee", "type": "person", "hot": True},
            {"key": "bank_account", "type": "text"}],
            "access": [
                {"role": "hr", "read": "all"},
                {"role": "employee", "read": {"where": "employee = $me"},
                 "hide": ["bank_account"]}]}],
        "actions": [{"id": "peek", "uses": "object:slip.get"},
                    {"id": "find", "uses": "object:slip.list"}],
    }


@pytest.fixture
def ruled(world):
    """Owen publishes app R (:func:`_slips`) with one slip of Mia and one of
    Ned, and installs it for ``ops`` and for the org. Mia runs the ``ops``
    install. Ned runs the org install."""
    async def setup() -> SimpleNamespace:
        r = SimpleNamespace()
        mid = f"b6a{TAG}-app{next(_COUNTER)}"
        r.pid = await _live(world.a, OWEN, mid, _slips(mid))
        r.mia = await _record(world.a, r.pid, {"site": "Plant 4, Hosur", "employee": MIA_LOW,
                                               "bank_account": "IN-1111"}, "slip")
        r.ned = await _record(world.a, r.pid, {"site": "Plant 7, Pune", "employee": NED,
                                               "bank_account": "IN-2222"}, "slip")
        r.ops = await _install(world.a, OWEN, r.pid, OPS)
        r.org = await _install(world.a, OWEN, r.pid, "org")
        for scope, member in ((OPS, MIA), ("org", NED)):
            status, got = await _set(world, scope, member, pid=r.pid)
            assert status == 200, got
        return r

    return _run(world, setup)


def _sites(got: dict[str, Any]) -> list[str]:
    assert got["ok"] is True and got["outcome"] == "ran", got
    return sorted(r["data"]["site"] for r in got["result"]["records"])


def _keys(got: dict[str, Any]) -> set[str]:
    return {k for r in got["result"]["records"] for k in r["data"]}


def test_r8_the_run_reads_with_the_scope_of_its_member_and_never_the_owner(world,
                                                                          ruled) -> None:
    """C8b gives a member the roles of the install, so until then Mia holds
    no role, and the grants of app R give her nothing. The run reads as Mia:
    no slip, also her own. Owen owns app R and passes every rule, and his
    scope does not leak into her run (M3, M4)."""
    async def go() -> list[dict[str, Any]]:
        return [await _read(world.a, ruled.ops, "find", {}),
                await _read(world.a, ruled.ops, "peek", {"id": ruled.mia}),
                await _set(world, "org", OWEN, pid=ruled.pid),
                await _read(world.a, ruled.org, "find", {}),
                await _read(world.a, ruled.ops, "find", {})]

    mia, mia_own, named, owen, mia_again = _run(world, go)
    assert _sites(mia) == [] and mia["result"]["total"] == 0, mia
    assert (mia_own["ok"], mia_own["outcome"], mia_own["result"]) == (False, "not_found", None)
    assert named[0] == 200, named
    assert _sites(owen) == ["Plant 4, Hosur", "Plant 7, Pune"]
    assert "bank_account" in _keys(owen)
    assert _sites(mia_again) == []
    rows = _audits(world, ruled.pid, invoke.run_as_actor(ruled.pid))
    assert [(r["run_as"], r["outcome"]) for r in rows] == [
        (MIA_LOW, "ran"), (MIA_LOW, "not_found"), (OWEN, "ran"), (MIA_LOW, "ran")]
    assert "IN-1111" not in json.dumps(rows) and "IN-2222" not in json.dumps(rows)


def test_r8_the_run_applies_the_row_rules_and_the_hides_of_its_member(world, ruled,
                                                                      monkeypatch) -> None:
    """The roles of C8b, given here by one map over ``_row_viewer``: Mia holds
    ``employee`` and Ned holds ``hr``. Mia's run reads her slip only, with
    ``bank_account`` hidden, and cannot filter on it. Ned's run reads both
    slips. His grant never reaches Mia's run, and the run never asks for the
    viewer of Owen, who named both members (M3, M4). When Mia loses the
    role, her next run reads nothing."""
    roles: dict[str, frozenset[str]] = {MIA_LOW: frozenset({"employee"}),
                                        NED: frozenset({"hr"})}
    asked: list[str] = []
    real = invoke._row_viewer

    def row_viewer(member: str) -> Viewer:
        asked.append(member)
        return Viewer(real(member).email, roles.get(member, frozenset()))

    monkeypatch.setattr(invoke, "_row_viewer", row_viewer)
    hidden = {"filter": [{"field": "bank_account", "op": "eq", "value": "IN-2222"}]}

    async def go() -> list[dict[str, Any]]:
        return [await _read(world.a, ruled.ops, "find", {}),
                await _read(world.a, ruled.ops, "peek", {"id": ruled.mia}),
                await _read(world.a, ruled.ops, "peek", {"id": ruled.ned}),
                await _read(world.a, ruled.ops, "find", hidden),
                await _read(world.a, ruled.org, "find", {}),
                await _read(world.a, ruled.org, "find", hidden),
                await _read(world.a, ruled.ops, "find", {})]

    mia, own, other, probe, ned, ned_probe, mia_again = _run(world, go)
    assert _sites(mia) == ["Plant 4, Hosur"]
    assert _keys(mia) == {"site", "employee"}
    assert own["ok"] is True and own["result"]["record"]["id"] == ruled.mia
    assert "bank_account" not in own["result"]["record"]["data"]
    assert (other["ok"], other["outcome"], other["result"]) == (False, "not_found", None)
    assert (probe["ok"], probe["outcome"]) == (False, "refused"), probe
    assert _sites(ned) == ["Plant 4, Hosur", "Plant 7, Pune"]
    assert _keys(ned) == {"site", "employee", "bank_account"}
    assert _sites(ned_probe) == ["Plant 7, Pune"]
    assert _sites(mia_again) == ["Plant 4, Hosur"]
    assert asked == [MIA_LOW, MIA_LOW, MIA_LOW, MIA_LOW, NED, NED, MIA_LOW]
    assert OWEN not in asked

    roles[MIA_LOW] = frozenset()
    lost = _run(world, lambda: _read(world.a, ruled.ops, "find", {}))
    assert _sites(lost) == []
    rows = _audits(world, ruled.pid, invoke.run_as_actor(ruled.pid))
    assert "IN-1111" not in json.dumps(rows) and "IN-2222" not in json.dumps(rows)



# ── p. A connection that the member of the run lacks (WS-STU I1a) ──────────
#
# R7. Mutation R1, run on 2026-11-14: ``invoke._run_as_session``: drop the
# call of ``_needs_connection``. This case goes red, and so does section 3b
# of the hermetic twin. R2 (the review fix): ``invoke._needs_connection``:
# drop the check of ``connection_gate_on``. Run ``run-p0`` goes red.

#: A provider of the registry that no other suite names.
TALLY = f"mcp:tally-{TAG}"


def _connect(world: Any, member: str, status: str = "connected") -> str:
    with world.p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status) VALUES (CAST(:o AS uuid), :p, 'integrations', "
            "'member', :m, :s) RETURNING id"),
            {"o": world.a, "p": TALLY, "m": member.lower(), "s": status}).scalar_one())


@pytest.fixture
def tally(world):
    yield world
    _admin(world, "DELETE FROM integration_connections WHERE provider = :p", p=TALLY)


def test_r8_a_connection_that_the_member_lacks_refuses_the_run(tally, monkeypatch) -> None:
    world = tally
    _admin(world, "UPDATE app_versions SET manifest = manifest || CAST(:m AS jsonb) "
                  "WHERE app_id = CAST(:p AS uuid) AND version = 1",
           m=json.dumps({"connections": [TALLY]}), p=world.pid)

    def find(run_id: str) -> dict[str, Any]:
        return _run(world, lambda: _read(world.a, world.ops, "find", {}, run_id=run_id))

    named = _run(world, lambda: _set(world, OPS, MIA))
    assert named[0] == 200, named
    # The gate off for org A: the run is as before I1a (mutation R2 drops it).
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", world.b)
    assert find("run-p0")["outcome"] == "ran"
    monkeypatch.setattr(get_settings(), "studio_connection_gate_orgs", world.a)
    assert find("run-p1")["code"] == invoke.NEEDS_CONNECTION
    _connect(world, OWEN)                         # the owner holds it, Mia does not
    assert find("run-p2")["code"] == invoke.NEEDS_CONNECTION
    rid = _connect(world, MIA, status="needs_reconnect")
    assert find("run-p3")["code"] == invoke.NEEDS_CONNECTION
    _admin(world, "UPDATE integration_connections SET status = 'connected' "
                  "WHERE id = CAST(:i AS uuid)", i=rid)
    ran = find("run-p4")
    assert (ran["ok"], ran["outcome"]) == (True, "ran"), ran
    rows = [r for r in _runs(world) if r["run_id"].startswith("run-p")]
    assert [r.get("reason") for r in rows] == [None] + [invoke.NEEDS_CONNECTION] * 3 + [None]
    assert {r["run_as"] for r in rows} == {MIA_LOW}


# ── n. Migration 439 ───────────────────────────────────────────────────────


def _migration() -> Path:
    found = [p for p in (_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql")
             if "package_install_run_as_member" in p.read_text(encoding="utf-8")]
    assert len(found) == 1, found
    return found[0]


def test_r8_the_migration_adds_three_nullable_columns_with_no_default(world) -> None:
    text_ = _migration().read_text(encoding="utf-8")
    assert "SET lock_timeout" in text_ and "NOT VALID" in text_ and "VALIDATE CONSTRAINT" in text_
    with world.p.admin_engine.connect() as c:
        cols = {r[0]: (r[1], r[2]) for r in c.execute(text(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'package_install' AND column_name LIKE 'run_as%'")).all()}
        valid = {r[0]: r[1] for r in c.execute(text(
            "SELECT conname, convalidated FROM pg_constraint "
            "WHERE conname LIKE 'package_install_run_as_%'")).all()}
    assert cols == {"run_as": ("YES", None), "run_as_set_by": ("YES", None),
                    "run_as_set_at": ("YES", None)}
    assert valid == {"package_install_run_as_member": True,
                     "package_install_run_as_stamp": True}


@pytest.mark.parametrize(("run_as", "set_by"), [
    ("system:package:x@b6a.test", OWEN), ("Mia@b6a.test", OWEN), ("group:ops@b6a.test", OWEN),
    ("no-at-sign", OWEN), ("mia@b6a.test", None), ("mia@b6a.test", "Owen@b6a.test")])
def test_r8_the_checks_refuse_a_bad_member_or_stamp(world, run_as: str, set_by: Any) -> None:
    with pytest.raises(IntegrityError):
        _admin(world, "UPDATE package_install SET run_as = :r, run_as_set_by = :b, "
               "run_as_set_at = CASE WHEN CAST(:b AS text) IS NULL THEN NULL ELSE now() END "
               "WHERE id = CAST(:i AS uuid)", r=run_as, b=set_by, i=world.ops)
    assert _run_as_of(world, world.ops) == (None, None, None)
