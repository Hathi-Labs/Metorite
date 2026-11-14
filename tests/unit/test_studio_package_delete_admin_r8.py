"""The admin path of the delete of a Studio package on a real database (STU-A5c-2, R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 A5c-2. Code:
``gateway/routes/studio/packages.py`` (``_delete_mode``, ``delete_preview``
and ``delete_package``) over ``gateway/studio/packages_store.py``
(``admin_may_delete``, ``_ADMIN_GET_SQL`` and ``_OWNER_ACTIVE_SQL``). The
hermetic half is ``test_studio_package_delete.py`` (groups a2 and c2). The
owner path of A5c-1 is ``test_studio_package_delete_r8.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Each call binds its org as the
auth layer does, and the handler opens ``tenant_session()`` with no org.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<db>
    uv run pytest tests/unit/test_studio_package_delete_admin_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The cases of the row.**

1. An admin of org A frees the object room of a package whose owner has no
   ``app_user`` row: the publish of a member is 409 at the org cap, the admin
   deletes a package of the owner who left, and the publish passes.
2. An admin deletes a package whose owner has a suspended or a removed row.
   Each audit row of the delete, the partial rows of a 202 too, names the
   admin as ``user_email``, with ``by: "admin"`` and the owner.
3. The same admin gets 403 ``owner_active`` for the package of an active
   owner, on the preview and on the DELETE, and nothing changes. An active
   row with an upper-case address counts too.
4. A plain member gets 404 on both routes, also with ``as_admin`` in the
   body, and nothing changes.
5. An admin of org B gets 404 on both routes, and nothing changes.
6. An owner who is active again between two calls of a delete stops it: the
   next call is 403, and the rest of the records stay.

**The mutations (R7).** Each ran alone against this file on 2026-11-11, on
a private database, and each named test went red:

* M1. ``_OWNER_ACTIVE_SQL`` answers false for each owner. Red (4):
  ``test_r8_an_admin_gets_403_owner_active_for_an_active_owner`` (both
  addresses), ``test_r8_an_admin_of_org_b_learns_nothing_of_a_package_of_org_a``
  and ``test_r8_an_owner_who_is_active_again_between_two_calls_stops_the_delete``.
* M2. ``_OWNER_ACTIVE_SQL`` drops ``status = 'active'``. Red (3):
  ``test_r8_an_admin_deletes_the_package_of_an_owner_with_a_row_that_is_not_active``
  (both statuses) and
  ``test_r8_an_owner_who_is_active_again_between_two_calls_stops_the_delete``.
* M3. ``_delete_mode`` drops the ``has_permission`` check. Red (1):
  ``test_r8_a_plain_member_and_an_admin_of_org_b_get_404_and_change_nothing``.
* M4. ``_ADMIN_GET_SQL`` drops the org filter. All 8 stay green: RLS hides
  org A from org B, so RLS is the backstop. The hermetic fence
  ``test_the_admin_statements_bind_the_org_and_the_active_status`` holds the
  filter, and it went red.
* M5. The store writes the owner as ``user_email`` of the audit rows
  (``_end_call`` and ``_finish_delete``). Red (4):
  ``test_r8_an_admin_frees_the_object_room_of_a_package_whose_owner_has_no_row``,
  ``test_r8_an_admin_deletes_the_package_of_an_owner_with_a_row_that_is_not_active``
  (both statuses) and
  ``test_r8_an_owner_who_is_active_again_between_two_calls_stops_the_delete``.
"""

from __future__ import annotations

import asyncio
import json
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
from gateway.routes.studio import packages
from gateway.studio import objects_store, packages_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
#: Ana owns the packages and leaves the org. Ben is an active member. Cara is
#: an admin of org A, Dan a plain member of org A, and Eve an admin of org B.
ANA = f"ana.{TAG}@stua5c2.test"
BEN = f"ben.{TAG}@stua5c2.test"
CARA = f"cara.{TAG}@stua5c2.test"
DAN = f"dan.{TAG}@stua5c2.test"
EVE = f"eve.{TAG}@stua5c2.test"
PEOPLE = (ANA, BEN, CARA, DAN, EVE)
NOT_FOUND = "Not found"
MEMBER = ("feature:studio",)
ADMIN = ("feature:studio", "admin:members:manage")
_COUNTER = iter(range(10_000))


def _mid(label: str = "app") -> str:
    return f"a5d{TAG}-{label}{next(_COUNTER)}"


def _manifest(mid: str, objects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    objs = objects if objects is not None else [
        {"name": "visit", "fields": [{"key": "site", "type": "text"},
                                     {"key": "status", "type": "select",
                                      "options": ["planned", "done"], "hot": True}]}]
    return {"studio": 1, "id": mid, "name": "Visits", "purpose": "Track install visits.",
            "objects": objs,
            "actions": [{"id": "find", "uses": f"object:{objs[0]['name']}.list"}] if objs else []}


def _user(org: str, email: str, grants: tuple[str, ...]) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The parts of a request that a handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/packages")

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None, payload: Any = None,
                grants: tuple[str, ...] = MEMBER) -> tuple[int, Any]:
    """Call the real handler *route* with *org* bound, as *email* with *grants*.
    Answers the status and the answer or the detail."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "publish", "delete_package"):
        kwargs["request"] = _Request(payload)
    response = Response()
    if route == "delete_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        out = await handler(**kwargs)
        return response.status_code, out
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B. Ben, Cara and Dan are active in org A, and Eve is
    active in org B. Ana has no row: a test adds the row it needs. The
    teardown removes each package, audit row and member row of the tag."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    with p.admin_engine.begin() as c:
        for email, org in ((BEN, p.org_a), (CARA, p.org_a), (DAN, p.org_a), (EVE, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": email, "o": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE user_email = ANY(:m)"), {"m": list(PEOPLE)})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"a5d{TAG}-%"})
        c.execute(text("DELETE FROM app_user WHERE lower(email) = ANY(:m)"),
                  {"m": [e.lower() for e in PEOPLE]})


# ── Readers and writers (the superuser, so RLS hides nothing) ──────────────


def _scalar(world: Any, sql: str, **params: Any) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _set_owner(world: Any, status: str | None, email: str = ANA) -> None:
    """Ana's ``app_user`` row in org A: *status*, or no row for None."""
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": ANA})
        if status is not None:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid))"),
                {"e": email, "s": status, "o": world.a})


def _exists(world: Any, pid: str) -> bool:
    return int(_scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=pid)) == 1


def _records(world: Any, pid: str) -> int:
    return int(_scalar(world, "SELECT count(*) FROM studio_records r JOIN studio_objects o "
                       "ON o.id = r.object_id WHERE o.package_id = CAST(:p AS uuid)", p=pid))


def _audits(world: Any, pid: str) -> list[tuple[str, str, dict[str, Any]]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, user_email, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY id"), {"p": pid}).all()
    return [(r[0], r[1], r[2] if isinstance(r[2], dict) else json.loads(r[2])) for r in rows]


def _delete_rows(world: Any, pid: str) -> list[tuple[str, str, dict[str, Any]]]:
    ops = (packages_store.DELETE_OP, packages_store.PARTIAL_OP)
    return [a for a in _audits(world, pid) if a[2].get("op") in ops]


def _org_objects(world: Any) -> int:
    return int(_scalar(world, "SELECT count(*) FROM studio_objects "
                       "WHERE organization_id = CAST(:o AS uuid)", o=world.a))


# ── The calls ──────────────────────────────────────────────────────────────


async def _live(org: str, email: str, manifest: dict[str, Any]) -> str:
    status, got = await _call("create_package", org, email, payload=manifest)
    assert status in (200, 201), got
    pid = str(got["package"]["id"])
    status, got = await _call("publish", org, email, pid, payload={})
    assert status == 200, got
    return pid


async def _fill(org: str, email: str, pid: str, n: int) -> None:
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, "visit", package_id=pid)
        assert obj is not None
        oid = obj.id
    left = n
    while left > 0:
        size = min(left, 50)
        async with tenant_session(org) as db:
            await objects_store.apply_writes(db, email, package_id=pid, writes=[
                objects_store.Write("create", oid, data={"site": f"s{i}", "status": "planned"})
                for i in range(size)], viewer=Viewer(email))
        left -= size


async def _delete_all(org: str, email: str, pid: str, grants: tuple[str, ...],
                      tries: int = 40) -> list[int]:
    """Preview, then DELETE until 200. A 202 takes the token of its answer."""
    status, got = await _call("delete_preview", org, email, pid, grants=grants)
    assert status == 200, got
    token = got["confirm"]
    seen: list[int] = []
    for _ in range(tries):
        status, got = await _call("delete_package", org, email, pid, {"confirm": token},
                                  grants=grants)
        seen.append(status)
        if status == 200:
            return seen
        if status == 202:
            token = got["confirm"]
        elif status == 503:
            await asyncio.sleep(0.05)
        else:
            raise AssertionError((status, got))
    raise AssertionError(f"no 200 in {tries} calls: {seen}")


# ── 1. The admin frees the object room of an owner who left ────────────────


def test_r8_an_admin_frees_the_object_room_of_a_package_whose_owner_has_no_row(world) -> None:
    _set_owner(world, None)
    cap, per = objects_store.MAX_OBJECTS_PER_ORG, objects_store.MAX_OBJECTS_PER_PACKAGE
    room = cap - _org_objects(world)
    assert room >= per, "the org holds too many objects for this test"

    async def go() -> tuple[list[str], tuple[int, Any], list[int], tuple[int, Any]]:
        anas: list[str] = []
        left = room
        while left > 0:
            n = min(left, per)
            objs = [{"name": f"o{i}", "fields": [{"key": "a", "type": "text"}]} for i in range(n)]
            anas.append(await _live(world.a, ANA, _manifest(_mid("big"), objs)))
            left -= n
        status, got = await _call("create_package", world.a, BEN, payload=_manifest(_mid("late")))
        assert status in (200, 201), got
        late = str(got["package"]["id"])
        refused = await _call("publish", world.a, BEN, late, payload={})
        seen = await _delete_all(world.a, CARA, anas[0], ADMIN)
        return anas, refused, seen, await _call("publish", world.a, BEN, late, payload={})

    anas, (status, got), seen, after = _run(world, go)
    assert status == 409, got
    assert seen == [200]
    assert after[0] == 200, after
    assert after[1]["sync"]["objects_created"] == ["visit"]
    assert not _exists(world, anas[0])
    assert all(_exists(world, pid) for pid in anas[1:])
    [(org, who, detail)] = _delete_rows(world, anas[0])
    assert (org, who) == (world.a, CARA)
    assert (detail["op"], detail["by"], detail["admin"], detail["owner"]) == (
        packages_store.DELETE_OP, "admin", CARA, ANA)


# ── 2. A suspended or a removed owner, and the audit rows ──────────────────


@pytest.mark.parametrize("status", ["suspended", "removed"])
def test_r8_an_admin_deletes_the_package_of_an_owner_with_a_row_that_is_not_active(
        world, monkeypatch, status: str) -> None:
    _set_owner(world, status)
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 100)

    async def go() -> tuple[str, list[int]]:
        pid = await _live(world.a, ANA, _manifest(_mid("left")))
        await _fill(world.a, ANA, pid, 250)
        monkeypatch.setattr(packages_store, "STEP_SECONDS", 1000.0)  # one step for each call
        return pid, await _delete_all(world.a, CARA, pid, ADMIN)

    pid, seen = _run(world, go)
    assert seen == [202, 202, 200], seen
    assert not _exists(world, pid)
    rows = _delete_rows(world, pid)
    assert [r[2]["op"] for r in rows] == [packages_store.PARTIAL_OP, packages_store.PARTIAL_OP,
                                          packages_store.DELETE_OP]
    for org, who, detail in rows:
        assert (org, who) == (world.a, CARA), (org, who)
        assert (detail["by"], detail["admin"], detail["owner"]) == ("admin", CARA, ANA)
    assert rows[-1][2]["records_total"] == 250
    assert ANA not in {who for _org, who, _d in rows}


# ── 3. An active owner ─────────────────────────────────────────────────────


@pytest.mark.parametrize("address", ["same", "upper"])
def test_r8_an_admin_gets_403_owner_active_for_an_active_owner(world, address: str) -> None:
    _set_owner(world, "active", ANA if address == "same" else ANA.upper())

    async def go() -> tuple[str, str, tuple[int, Any], tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("active")))
        await _fill(world.a, ANA, pid, 5)
        status, got = await _call("delete_preview", world.a, ANA, pid)
        assert status == 200, got
        token = got["confirm"]
        preview = await _call("delete_preview", world.a, CARA, pid, grants=ADMIN)
        delete = await _call("delete_package", world.a, CARA, pid, {"confirm": token},
                             grants=ADMIN)
        return pid, token, preview, delete

    pid, _token, preview, delete = _run(world, go)
    for status, detail in (preview, delete):
        assert status == 403, detail
        assert detail == {"code": "owner_active", "message": packages_store.OWNER_ACTIVE_TEXT,
                          "records_removed": 0}
    assert _exists(world, pid) and _records(world, pid) == 5
    assert _delete_rows(world, pid) == []


def test_r8_an_admin_of_org_b_learns_nothing_of_a_package_of_org_a(world) -> None:
    """Ben is an active member of org A. An admin of org A gets 403 for his
    package. An admin of org B gets 404, not 403: the admin read and the
    owner check run in the bound org only, so org B learns nothing."""
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        pid = await _live(world.a, BEN, _manifest(_mid("ben")))
        return (await _call("delete_preview", world.a, CARA, pid, grants=ADMIN),
                await _call("delete_preview", world.b, EVE, pid, grants=ADMIN))

    in_a, in_b = _run(world, go)
    assert in_a[0] == 403 and in_a[1]["code"] == "owner_active"
    assert in_b == (404, NOT_FOUND)


# ── 4 and 5. A plain member and an admin of org B ──────────────────────────


def test_r8_a_plain_member_and_an_admin_of_org_b_get_404_and_change_nothing(world) -> None:
    _set_owner(world, None)

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _live(world.a, ANA, _manifest(_mid("keep")))
        await _fill(world.a, ANA, pid, 5)
        status, got = await _call("delete_preview", world.a, CARA, pid, grants=ADMIN)
        assert status == 200, got
        token = got["confirm"]
        body = {"confirm": token, "as_admin": True, "owner_email": ANA}
        return pid, [
            await _call("delete_preview", world.a, DAN, pid),
            await _call("delete_package", world.a, DAN, pid, body),
            await _call("delete_preview", world.b, EVE, pid, grants=ADMIN),
            await _call("delete_package", world.b, EVE, pid, body, grants=ADMIN),
        ]

    pid, answers = _run(world, go)
    assert answers == [(404, NOT_FOUND)] * 4, answers
    assert _exists(world, pid) and _records(world, pid) == 5
    assert _delete_rows(world, pid) == []


# ── 6. An owner who comes back during a delete ─────────────────────────────


def test_r8_an_owner_who_is_active_again_between_two_calls_stops_the_delete(
        world, monkeypatch) -> None:
    _set_owner(world, "suspended")
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 100)

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("back")))
        await _fill(world.a, ANA, pid, 250)
        status, got = await _call("delete_preview", world.a, CARA, pid, grants=ADMIN)
        assert status == 200, got
        monkeypatch.setattr(packages_store, "STEP_SECONDS", 1000.0)  # one step for each call
        first = await _call("delete_package", world.a, CARA, pid, {"confirm": got["confirm"]},
                            grants=ADMIN)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE app_user SET status = 'active' WHERE lower(email) = :e"),
                      {"e": ANA})
        second = await _call("delete_package", world.a, CARA, pid,
                             {"confirm": first[1]["confirm"]}, grants=ADMIN)
        return pid, first, second

    pid, first, second = _run(world, go)
    assert first[0] == 202, first
    assert second[0] == 403 and second[1]["code"] == "owner_active", second
    assert _exists(world, pid) and _records(world, pid) == 150
    [(org, who, detail)] = _delete_rows(world, pid)
    assert (org, who, detail["op"], detail["records_this_call"]) == (
        world.a, CARA, packages_store.PARTIAL_OP, 100)
