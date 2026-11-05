"""The delete of a Studio package on a real database (STU-A5c-1, R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 A5c-1. Code:
``gateway/routes/studio/packages.py`` (``delete_preview`` and
``delete_package``) over ``gateway/studio/packages_store.py`` and
``gateway/studio/objects_store.py``. The hermetic half is
``test_studio_package_delete.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Each call binds its org as the
auth layer does, and the handler opens ``tenant_session()`` with no org.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    docker exec metorite-scratch-tenant createdb -U acb <private db>
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_package_delete_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The nine cases of the audit (task 1532).**

1. The dead end: member Ben fills the org cap of objects, the publish of
   member Ana is 409, Ben deletes a package, and the publish of Ana passes.
2. Each row of the package goes: the ``apps`` row, the versions, the
   objects, the fields, the records, the hot rows (a relation row too), the
   installs and their contributions, the pins, the grants, the tool grants,
   the files and the data. The ``app_audit`` rows stay, with one
   ``package.delete`` row in the bound org.
3. 1,300 records with a chunk of 100: 202 and then 200.
4. Another member, org B with the same email, and an App Workshop app get
   404, and keep each row.
5. The org lock is free between two steps and after a 202. Two deletes and
   a publish in one org raise no 40P01, and a racer may get 503.
6. A delete against a record write and a publish of the same package: no
   deadlock and no orphan row.
7. A failed audit insert keeps the package and each of its rows.
8. The door, the package read, a publish, the versions, the preview and a
   second delete on the deleted id are 404 and write nothing. The catalog
   holds no row of the id (``?app=`` answers an empty list, as for each id
   that names no package).
9. The mutations: the docstring of ``test_studio_package_delete.py`` names
   each one and its red tests in both halves.

**The lock order.** The ``apps`` row ``FOR UPDATE``, then the org lock.
``test_r8_a_holder_of_the_apps_row_takes_the_org_lock_while_a_delete_waits``
plays the publish between its two locks against a waiting delete. Under the
other order (the org lock first) it deadlocks, so it proves the order of the
A5 row and of the ``_lock_org`` docstring.

**Fix round 1 (2026-11-05).** Four cases more:

10. A call that ends with records left writes one partial row (op
    ``package.delete.partial``) with the records of that call, and the
    final row names ``records_this_call`` and ``records_total``.
11. A failed partial row rolls back the purge of its step.
12. A publish between two steps that adds an object ends the call with 409
    and deletes nothing more.
13. A private install of the owner that the owner shared with a person
    counts as an install of another member, by the one share rule.

``_every_step_ends_its_call`` sets the step bound past the budget, so a call
answers 202 after one step. A budget of 0 would leave the purge no time.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException, Response
from gateway.routes.studio import actions, invoke, packages
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
ANA = f"ana.{TAG}@stua5c.test"
BEN = f"ben.{TAG}@stua5c.test"
NOT_FOUND = "Not found"
_COUNTER = iter(range(10_000))

_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
]


def _mid(label: str = "app") -> str:
    return f"a5c{TAG}-{label}{next(_COUNTER)}"


def _manifest(mid: str, objects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    objs = [{"name": "visit", "fields": _FIELDS}] if objects is None else objects
    return {
        "studio": 1, "id": mid, "name": "Visits", "purpose": "Track install visits.",
        "objects": objs,
        "actions": [{"id": "find", "uses": f"object:{objs[0]['name']}.list"}] if objs else [],
    }


def _shop(mid: str) -> dict[str, Any]:
    """Two objects, and a hot relation field of migration 338."""
    return {"studio": 1, "id": mid, "name": "Shop", "purpose": "Track orders.",
            "objects": [
                {"name": "order", "fields": [{"key": "code", "type": "text", "hot": True}]},
                {"name": "line", "fields": [
                    {"key": "order_ref", "type": "relation", "to": "order", "hot": True},
                    {"key": "amount", "type": "number", "hot": True}]}],
            "actions": []}


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:workflows"]), organization_id=org)


class _Request:
    """The parts of a request that a handler reads. No direct proof, so a
    door call is not direct."""

    def __init__(self, payload: Any = None, raw: bytes | None = None) -> None:
        self._raw = raw if raw is not None else (
            b"" if payload is None else json.dumps(payload).encode("utf-8"))
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")

    async def stream(self) -> Any:
        yield self._raw


_BODY_ROUTES = ("create_package", "save_draft", "publish", "delete_package")


async def _call_full(route: str, org: str, email: str, pid: str | None = None,
                     payload: Any = None, raw: bytes | None = None,
                     **query: Any) -> tuple[int, Any, dict[str, str]]:
    """Call the real handler *route* with *org* bound. Answers the status, the
    answer or the detail, and the headers of a refusal."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email), **query}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in _BODY_ROUTES:
        kwargs["request"] = _Request(payload, raw)
    response = Response()
    if route == "delete_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        out = await handler(**kwargs)
        return response.status_code, out, {}
    except HTTPException as exc:
        return exc.status_code, exc.detail, dict(exc.headers or {})
    finally:
        release_tenant(token)


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, **query: Any) -> tuple[int, Any]:
    status, detail, _ = await _call_full(route, org, email, pid, payload, **query)
    return status, detail


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, and an App Workshop app of Ana in org A. Each package
    has a slug that starts with ``a5c<tag>``, and the teardown removes it."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    run = uuid.uuid4().hex[:6]
    with p.admin_engine.begin() as c:
        code_app = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id) "
            "VALUES (:s, 'stua5c', :oe, '/tmp/stua5c', CAST(:o AS uuid)) RETURNING id"),
            {"s": f"a5c{TAG}-code-{run}", "oe": ANA, "o": p.org_a}).scalar_one())
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, code_app=code_app)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE user_email IN (:a, :b)"), {"a": ANA, "b": BEN})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"a5c{TAG}-%"})


# ── Readers and writers (the superuser, so RLS hides nothing) ──────────────


def _scalar(world: Any, sql: str, **params: Any) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar_one()


def _object_ids(world: Any, pid: str) -> list[str]:
    with world.p.admin_engine.connect() as c:
        return [str(r[0]) for r in c.execute(text(
            "SELECT id FROM studio_objects WHERE package_id = CAST(:p AS uuid)"),
            {"p": pid}).all()]


def _records(world: Any, pid: str) -> int:
    return int(_scalar(world, "SELECT count(*) FROM studio_records r JOIN studio_objects o "
                       "ON o.id = r.object_id WHERE o.package_id = CAST(:p AS uuid)", p=pid))


def _footprint(world: Any, pid: str, oids: list[str], installs: list[str]) -> dict[str, int]:
    """The rows of each table that the delete must take, by the ids of before."""
    by_app = ("apps", "app_versions", "package_install", "app_pins", "app_grants",
              "app_tool_grants", "app_data", "app_files")
    out: dict[str, int] = {}
    with world.p.admin_engine.connect() as c:
        for table in by_app:
            col = "id" if table == "apps" else "app_id"
            out[table] = int(c.execute(text(
                f"SELECT count(*) FROM {table} WHERE {col} = CAST(:p AS uuid)"),
                {"p": pid}).scalar_one())
        out["studio_objects"] = int(c.execute(text(
            "SELECT count(*) FROM studio_objects WHERE package_id = CAST(:p AS uuid)"),
            {"p": pid}).scalar_one())
        for table in ("studio_object_fields", "studio_records", "studio_record_hot"):
            out[table] = int(c.execute(text(
                f"SELECT count(*) FROM {table} WHERE object_id = ANY(CAST(:o AS uuid[]))"),
                {"o": oids}).scalar_one())
        out["package_contribution"] = int(c.execute(text(
            "SELECT count(*) FROM package_contribution "
            "WHERE install_id = ANY(CAST(:i AS uuid[]))"), {"i": installs}).scalar_one())
    return out


def _audits(world: Any, pid: str) -> list[tuple[str, str, str, dict[str, Any]]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, user_email, kind, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY id"), {"p": pid}).all()
    return [(r[0], r[1], r[2], r[3] if isinstance(r[3], dict) else json.loads(r[3]))
            for r in rows]


def _deletes(world: Any, pid: str) -> list[dict[str, Any]]:
    return [a[3] for a in _audits(world, pid) if a[3].get("op") == "package.delete"]


def _partials(world: Any, pid: str) -> list[dict[str, Any]]:
    return [a[3] for a in _audits(world, pid) if a[3].get("op") == "package.delete.partial"]


def _every_step_ends_its_call(monkeypatch: Any) -> None:
    """A step bound past the budget: each step that leaves records is the
    last of its call, so a call answers 202 after one step."""
    monkeypatch.setattr(packages_store, "STEP_SECONDS", 1000.0)


def _org_objects(world: Any) -> int:
    return int(_scalar(world, "SELECT count(*) FROM studio_objects "
                       "WHERE organization_id = CAST(:o AS uuid)", o=world.a))


def _seed_links(world: Any, pid: str) -> list[str]:
    """An org install with one contribution, a private install of Ben, a pin, a
    grant, a tool grant, a file and a data row, by direct INSERT. Answers the
    install ids."""
    with world.p.admin_engine.begin() as c:
        org = {"o": world.a, "p": pid}
        group = str(c.execute(text(
            "INSERT INTO package_install (organization_id, app_id, version, scope_kind, "
            "scope_subject, installed_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 1, "
            "'org', 'org', :m) RETURNING id"), {**org, "m": ANA}).scalar_one())
        mine = str(c.execute(text(
            "INSERT INTO package_install (organization_id, app_id, version, scope_kind, "
            "scope_subject, installed_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 1, "
            "'private', :m, :m) RETURNING id"), {**org, "m": BEN}).scalar_one())
        c.execute(text(
            "INSERT INTO package_contribution (organization_id, install_id, point, key) "
            "VALUES (CAST(:o AS uuid), CAST(:i AS uuid), 'nav', 'visits')"),
            {"o": world.a, "i": group})
        c.execute(text("INSERT INTO app_pins (organization_id, app_id, user_email) "
                       "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :m)"), {**org, "m": ANA})
        c.execute(text("INSERT INTO app_grants (organization_id, app_id, subject, role, "
                       "granted_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :s, 'use', :m)"),
                  {**org, "s": BEN, "m": ANA})
        c.execute(text("INSERT INTO app_tool_grants (organization_id, app_id, user_email, tool) "
                       "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :m, 'x')"), {**org, "m": ANA})
        c.execute(text("INSERT INTO app_files (organization_id, app_id, path, content, sha256) "
                       "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'a.txt', 'a', 'h')"), org)
        c.execute(text("INSERT INTO app_data (organization_id, app_id, table_name, key, "
                       "updated_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 't', 'k', :m)"),
                  {**org, "m": ANA})
    return [group, mine]


# ── The calls ──────────────────────────────────────────────────────────────


async def _new(org: str, email: str, manifest: dict[str, Any]) -> str:
    status, got = await _call("create_package", org, email, payload=manifest)
    assert status == 201 or status == 200, got
    return str(got["package"]["id"])


async def _publish(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _call("publish", org, email, pid, payload={})


async def _live(org: str, email: str, manifest: dict[str, Any]) -> str:
    pid = await _new(org, email, manifest)
    status, got = await _publish(org, email, pid)
    assert status == 200, got
    return pid


async def _fill(org: str, email: str, pid: str, n: int, name: str = "visit") -> None:
    """*n* records of the object *name*, in batches of the store."""
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, name, package_id=pid)
        assert obj is not None
        oid = obj.id
    left = n
    while left > 0:
        size = min(left, 50)
        async with tenant_session(org) as db:
            await objects_store.apply_writes(db, email, package_id=pid, writes=[
                objects_store.Write("create", oid, data={"site": f"s{i}", "status": "planned"})
                for i in range(size)])
        left -= size


async def _preview(org: str, email: str, pid: str) -> tuple[int, Any]:
    return await _call("delete_preview", org, email, pid)


async def _delete(org: str, email: str, pid: str, token: str) -> tuple[int, Any, dict[str, str]]:
    return await _call_full("delete_package", org, email, pid, payload={"confirm": token})


async def _delete_all(org: str, email: str, pid: str, tries: int = 40) -> list[int]:
    """Preview, then DELETE until 200. A 409 takes the new token, a 202 takes
    the token of its answer, and a 503 tries again."""
    status, got = await _preview(org, email, pid)
    assert status == 200, got
    token = got["confirm"]
    seen: list[int] = []
    for _ in range(tries):
        status, got, _h = await _delete(org, email, pid, token)
        seen.append(status)
        if status == 200:
            return seen
        if status == 409:
            token = got["preview"]["confirm"]
        elif status == 202:
            token = got["confirm"]
        elif status == 503:
            await asyncio.sleep(0.05)
        else:
            raise AssertionError((status, got))
    raise AssertionError(f"no 200 in {tries} calls: {seen}")


def _ok_or_busy(got: Any, *ok: int) -> None:
    """A racer answers a status of *ok*, or 503 ``store_busy`` with
    ``Retry-After``. A deadlock (40P01) or any other error escapes the handler,
    and the app would answer 500, so it fails here."""
    if isinstance(got, BaseException):
        state = getattr(getattr(got, "orig", None), "sqlstate", None)
        raise AssertionError(f"a racer raised {type(got).__name__} (sqlstate {state})")
    status, detail, headers = got
    assert status in (*ok, 503), got
    if status == 503:
        assert detail["code"] == "store_busy" and headers.get("Retry-After"), got


# ── 1. The dead end of the org cap ─────────────────────────────────────────


def test_r8_the_dead_end_of_the_org_cap_is_freed_by_a_delete(world) -> None:
    cap, per = objects_store.MAX_OBJECTS_PER_ORG, objects_store.MAX_OBJECTS_PER_PACKAGE
    room = cap - _org_objects(world)
    assert room >= per, "the org holds too many objects for this test"

    async def go() -> tuple[list[str], str, tuple[int, Any], list[int], tuple[int, Any]]:
        bens: list[str] = []
        left = room
        while left > 0:
            n = min(left, per)
            objs = [{"name": f"o{i}", "fields": [{"key": "a", "type": "text"}]} for i in range(n)]
            bens.append(await _live(world.a, BEN, _manifest(_mid("big"), objs)))
            left -= n
        late = await _new(world.a, ANA, _manifest(_mid("late")))
        refused = await _publish(world.a, ANA, late)
        seen = await _delete_all(world.a, BEN, bens[0])
        return bens, late, refused, seen, await _publish(world.a, ANA, late)

    bens, _late, (status, got), seen, after = _run(world, go)
    assert status == 409, got
    [problem] = got["problems"]
    assert problem["kind"] == "store_refused" and "delete a package" in problem["message"]
    assert seen == [200]
    assert after[0] == 200, after
    assert after[1]["sync"]["objects_created"] == ["visit"]
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=bens[0]) == 0


# ── 2. Every row goes, and the audit stays ─────────────────────────────────


def test_r8_every_row_of_the_package_goes_and_the_audit_rows_stay(world) -> None:
    async def go() -> tuple[str, str]:
        pid = await _live(world.a, ANA, _shop(_mid("shop")))
        await _publish(world.a, ANA, pid)
        async with tenant_session(world.a) as db:
            order = await objects_store.get_object_by_name(db, "order", package_id=pid)
            line = await objects_store.get_object_by_name(db, "line", package_id=pid)
            assert order is not None and line is not None
            made = await objects_store.create_record(db, ANA, order.id, {"code": "PO-1"},
                                                     package_id=pid)
            assert made is not None
            for amount in (3, 4):
                await objects_store.create_record(
                    db, ANA, line.id, {"order_ref": {"id": made.id}, "amount": amount}, package_id=pid)
        other = await _live(world.a, ANA, _manifest(_mid("keep")))
        return pid, other

    pid, other = _run(world, go)
    slug = _scalar(world, "SELECT slug FROM apps WHERE id = CAST(:p AS uuid)", p=pid)
    installs = _seed_links(world, pid)
    oids = _object_ids(world, pid)
    before = _footprint(world, pid, oids, installs)
    assert all(v > 0 for v in before.values()), before
    assert _scalar(world, "SELECT count(*) FROM studio_record_hot "
                   "WHERE object_id = ANY(CAST(:o AS uuid[])) AND field_key = 'order_ref'",
                   o=oids) == 2
    kept_audits = len(_audits(world, pid))

    async def preview_then_delete() -> tuple[tuple[int, Any], list[int]]:
        return await _preview(world.a, ANA, pid), await _delete_all(world.a, ANA, pid)

    (status, preview), seen = _run(world, preview_then_delete)
    assert status == 200 and seen == [200]
    assert preview["totals"] == {"objects": 2, "fields": 3, "records": 3,
                                 "records_capped": False, "versions": 2, "installs": 2,
                                 "other_member_installs": 2}
    assert _footprint(world, pid, oids, installs) == dict.fromkeys(before, 0)
    audits = _audits(world, pid)
    assert len(audits) == kept_audits + 1
    org, who, kind, detail = audits[-1]
    assert (org, who, kind) == (world.a, ANA, "action")
    assert detail == {"op": "package.delete", "by": "owner", "owner": ANA, "name": "Shop",
                      "slug": slug, "objects": 2, "fields": 3, "records_this_call": 3,
                      "records_total": 3,
                      "versions": 2, "installs": 2, "other_member_installs": 2}
    assert "Track orders" not in json.dumps(detail)
    # Another package of the same member keeps its rows.
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=other) == 1
    assert len(_object_ids(world, other)) == 1


def test_r8_a_draft_with_no_version_and_no_object_is_deleted(world) -> None:
    async def go() -> tuple[str, tuple[int, Any], list[int]]:
        pid = await _new(world.a, ANA, _manifest(_mid("draft")))
        return pid, await _preview(world.a, ANA, pid), await _delete_all(world.a, ANA, pid)

    pid, (status, preview), seen = _run(world, go)
    assert status == 200 and preview["objects"] == [] and preview["totals"]["versions"] == 0
    assert seen == [200]
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=pid) == 0
    [detail] = _deletes(world, pid)
    assert (detail["objects"], detail["records_this_call"], detail["versions"]) == (0, 0, 0)


def test_r8_the_preview_counts_up_to_the_cap(world) -> None:
    async def go() -> tuple[list[Any], list[Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("cap")))
        await _fill(world.a, ANA, pid, 12)
        async with tenant_session(world.a) as db:
            small = await objects_store.package_counts(db, pid, cap=5)
            exact = await objects_store.package_counts(db, pid, cap=12)
        return small, exact

    small, exact = _run(world, go)
    assert small == [objects_store.ObjectCount("visit", 2, 5, True)]
    assert exact == [objects_store.ObjectCount("visit", 2, 12, False)]


def test_r8_the_purge_takes_the_hot_rows_and_stops_at_its_limit(world) -> None:
    async def go() -> tuple[str, int, int]:
        pid = await _live(world.a, ANA, _manifest(_mid("purge")))
        await _fill(world.a, ANA, pid, 30)
        async with tenant_session(world.a) as db:
            await objects_store.lock_org(db)
            first = await objects_store.purge_records(db, pid, limit=20)
        async with tenant_session(world.a) as db:
            await objects_store.lock_org(db)
            second = await objects_store.purge_records(db, pid, limit=20)
        return pid, first, second

    pid, first, second = _run(world, go)
    assert (first, second) == (20, 10)
    oids = _object_ids(world, pid)
    assert _records(world, pid) == 0
    assert _scalar(world, "SELECT count(*) FROM studio_record_hot "
                   "WHERE object_id = ANY(CAST(:o AS uuid[]))", o=oids) == 0
    # The purge leaves the objects. The delete of the apps row takes them.
    assert len(oids) == 1


# ── 3. More than one chunk ─────────────────────────────────────────────────


def test_r8_1300_records_with_a_chunk_of_100_give_202_and_then_200(world, monkeypatch) -> None:
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 100)

    async def go() -> tuple[str, tuple[int, Any, Any], tuple[int, int], tuple[int, Any, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("big")))
        await _fill(world.a, ANA, pid, 1300)
        status, got = await _preview(world.a, ANA, pid)
        assert status == 200 and got["totals"]["records"] == 1300
        real = packages_store.STEP_SECONDS
        _every_step_ends_its_call(monkeypatch)
        first = await _delete(world.a, ANA, pid, got["confirm"])
        between = (_records(world, pid), len(_deletes(world, pid)), len(_partials(world, pid)))
        monkeypatch.setattr(packages_store, "STEP_SECONDS", real)
        second = await _delete(world.a, ANA, pid, first[1]["confirm"])
        return pid, first, between, second

    pid, first, between, second = _run(world, go)
    assert first[0] == 202, first
    assert first[1]["deleted"] is False and first[1]["remaining"] == 1200
    assert first[1]["records_removed"] == 100
    # The 202 holds: 1,200 records are left, and one partial row names the 100.
    assert between == (1200, 0, 1)
    assert _partials(world, pid)[0]["records_this_call"] == 100
    assert second[0] == 200, second
    assert second[1] == {"deleted": True, "package_id": pid, "records_removed": 1200}
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=pid) == 0
    # One final row. It names the records of its call and the total.
    [detail] = _deletes(world, pid)
    assert (detail["records_this_call"], detail["records_total"]) == (1200, 1300)


def test_r8_a_stale_token_is_409_and_deletes_nothing(world) -> None:
    async def go() -> tuple[str, tuple[int, Any, Any], int, list[int]]:
        pid = await _live(world.a, ANA, _manifest(_mid("stale")))
        await _fill(world.a, ANA, pid, 3)
        _status, got = await _preview(world.a, ANA, pid)
        await _fill(world.a, ANA, pid, 1)
        refused = await _delete(world.a, ANA, pid, got["confirm"])
        kept = _records(world, pid)
        return pid, refused, kept, await _delete_all(world.a, ANA, pid)

    pid, (status, detail, _h), kept, seen = _run(world, go)
    assert status == 409 and detail["code"] == "counts_changed"
    # The 409 deleted nothing. The delete after it writes one audit row.
    assert kept == 4 and len(_deletes(world, pid)) == 1
    assert detail["preview"]["totals"]["records"] == 4
    assert seen == [200]


# ── 4. The walls ───────────────────────────────────────────────────────────


def _walls(world: Any, org: str, email: str, pid: str) -> list[tuple[int, Any]]:
    async def go() -> list[tuple[int, Any]]:
        token = "a" * 64
        status, got = await _preview(world.a, ANA, pid) if pid != world.code_app else (0, None)
        if status == 200:
            token = got["confirm"]
        out = [await _preview(org, email, pid)]
        status, detail, _h = await _delete(org, email, pid, token)
        out.append((status, detail))
        return out
    return _run(world, go)


def test_r8_a_package_of_another_member_is_404_and_keeps_its_rows(world) -> None:
    async def go() -> str:
        pid = await _live(world.a, ANA, _manifest(_mid("wall")))
        await _fill(world.a, ANA, pid, 3)
        return pid

    pid = _run(world, go)
    assert _walls(world, world.a, BEN, pid) == [(404, NOT_FOUND)] * 2
    assert _records(world, pid) == 3 and _deletes(world, pid) == []


def test_r8_org_b_with_the_same_email_gets_404(world) -> None:
    pid = _run(world, lambda: _live(world.a, ANA, _manifest(_mid("orgb"))))
    assert _walls(world, world.b, ANA, pid) == [(404, NOT_FOUND)] * 2
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=pid) == 1
    assert _scalar(world, "SELECT count(*) FROM app_audit WHERE organization_id = CAST(:b AS uuid) "
                   "AND app_id = CAST(:p AS uuid)", b=world.b, p=pid) == 0


def test_r8_an_app_workshop_app_gets_404(world) -> None:
    assert _walls(world, world.a, ANA, world.code_app) == [(404, NOT_FOUND)] * 2
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)",
                   p=world.code_app) == 1


def test_r8_a_body_that_names_the_owner_does_not_let_another_member_delete(world) -> None:
    async def go() -> tuple[str, tuple[int, Any, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("body")))
        _status, got = await _preview(world.a, ANA, pid)
        body = {"confirm": got["confirm"], "owner_email": ANA, "member": ANA,
                "organization_id": world.a, "as_admin": True}
        return pid, await _call_full("delete_package", world.a, BEN, pid, payload=body)

    pid, (status, detail, _h) = _run(world, go)
    assert (status, detail) == (404, NOT_FOUND)
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=pid) == 1


def test_r8_with_the_switch_off_both_routes_are_404_and_write_nothing(world, monkeypatch) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _live(world.a, ANA, _manifest(_mid("off")))
        status, got = await _preview(world.a, ANA, pid)
        monkeypatch.setattr(get_settings(), "studio_orgs", "")
        out = [await _preview(world.a, ANA, pid)]
        status, detail, _h = await _delete(world.a, ANA, pid, got["confirm"])
        return pid, [*out, (status, detail)]

    pid, got = _run(world, go)
    assert got == [(404, NOT_FOUND)] * 2
    assert _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)", p=pid) == 1
    assert _deletes(world, pid) == []


# ── 5. The locks ───────────────────────────────────────────────────────────


def test_r8_the_org_lock_is_free_between_two_steps(world, monkeypatch) -> None:
    """Each step holds the org lock for 0.2 s. A writer of another package of
    the org writes during the delete, between the steps. One transaction for
    all steps would hold the lock for the whole delete, and each writer would
    wait past the bound of 1 s."""
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 100)
    real = objects_store.purge_records
    marks: list[float] = []

    # A small step bound, so the steps of the slow purge stay in one call.
    monkeypatch.setattr(packages_store, "STEP_SECONDS", 0.5)

    async def slow(db: Any, package_id: str, *, limit: int = 500,
                   deadline: float | None = None) -> int:
        marks.append(time.monotonic())
        got = await real(db, package_id, limit=limit, deadline=deadline)
        await asyncio.sleep(0.2)
        return got

    async def go() -> tuple[list[int], list[float], list[str]]:
        pid = await _live(world.a, ANA, _manifest(_mid("slow")))
        other = await _live(world.a, BEN, _manifest(_mid("busy")))
        await _fill(world.a, ANA, pid, 500)
        monkeypatch.setattr(objects_store, "purge_records", slow)
        done: list[float] = []
        errors: list[str] = []
        task = asyncio.create_task(_delete_all(world.a, ANA, pid))
        while not task.done():
            try:
                await _fill(world.a, BEN, other, 1)
                done.append(time.monotonic())
            except objects_store.BatchError as exc:
                errors.append(type(exc.cause).__name__)
            await asyncio.sleep(0.02)
        return await task, done, errors

    seen, done, errors = _run(world, go)
    assert seen == [200]
    assert len(marks) == 6, marks
    inside = [t for t in done if marks[0] < t < marks[-1]]
    assert len(inside) >= 2, (marks, done)
    assert errors == []


def test_r8_after_a_202_the_org_lock_is_free(world, monkeypatch) -> None:
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)
    _every_step_ends_its_call(monkeypatch)

    async def go() -> tuple[tuple[int, Any, Any], float]:
        pid = await _live(world.a, ANA, _manifest(_mid("pause")))
        await _fill(world.a, ANA, pid, 25)
        _status, got = await _preview(world.a, ANA, pid)
        first = await _delete(world.a, ANA, pid, got["confirm"])
        start = time.monotonic()
        async with tenant_session(world.a) as db:
            await objects_store.lock_org(db)
        return first, time.monotonic() - start

    first, waited = _run(world, go)
    assert first[0] == 202 and first[1]["remaining"] == 15
    assert waited < 0.5


def test_r8_two_deletes_and_a_publish_in_one_org_never_deadlock(world, monkeypatch) -> None:
    """Eight rounds. Two deletes of two packages and a publish of a third
    package, at once, on real connections. A chunk of 10 makes each delete a
    run of steps. A racer may get 503 ``store_busy`` (the org lock waits 1 s),
    and none raises 40P01 or answers 500."""
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)
    plain = [{"key": "name", "type": "text"}]

    async def go() -> list[list[Any]]:
        rounds = []
        for i in range(8):
            one = await _live(world.a, ANA, _manifest(_mid("d1")))
            two = await _live(world.a, BEN, _manifest(_mid("d2")))
            await _fill(world.a, ANA, one, 25)
            await _fill(world.a, BEN, two, 25)
            pub = await _new(world.a, ANA, _manifest(
                _mid("p"), [{"name": "first", "fields": plain},
                            {"name": "second", "fields": plain}]))
            tokens = [(await _preview(world.a, ANA, one))[1]["confirm"],
                      (await _preview(world.a, BEN, two))[1]["confirm"]]
            calls = [_delete(world.a, ANA, one, tokens[0]), _delete(world.a, BEN, two, tokens[1]),
                     _call_full("publish", world.a, ANA, pub, payload={})]
            if i % 2:
                calls.reverse()
            got = await asyncio.gather(*calls, return_exceptions=True)
            rounds.append(list(got))
            # Each package ends deleted: a 503 tries again.
            for pid, who in ((one, ANA), (two, BEN)):
                if _scalar(world, "SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)",
                           p=pid):
                    await _delete_all(world.a, who, pid)
        return rounds

    for got in _run(world, go):
        for answer in got:
            _ok_or_busy(answer, 200, 202)


def test_r8_a_holder_of_the_apps_row_takes_the_org_lock_while_a_delete_waits(world) -> None:
    """A publish holds the ``apps`` row and then takes the org lock. The
    holder plays that publish, and the delete starts between its two locks.
    In the one order the delete waits at the ``apps`` row and holds no org
    lock, so the holder gets the org lock at once. With the org lock first,
    the delete would hold it and wait for the row: a deadlock."""
    async def go() -> tuple[tuple[int, Any, Any], float]:
        pid = await _live(world.a, ANA, _manifest(_mid("order")))
        await _fill(world.a, ANA, pid, 3)
        _status, got = await _preview(world.a, ANA, pid)
        async with tenant_session(world.a) as hold:
            assert await packages_store.get_package(hold, ANA, pid, for_update=True)
            task = asyncio.create_task(_delete(world.a, ANA, pid, got["confirm"]))
            await asyncio.sleep(0.5)
            assert not task.done()
            start = time.monotonic()
            await objects_store.lock_org(hold)
            waited = time.monotonic() - start
        return await task, waited

    (status, detail, _h), waited = _run(world, go)
    assert waited < 0.5
    assert status == 200, detail


def test_r8_a_delete_waits_for_a_publish_that_holds_the_apps_row(world, monkeypatch) -> None:
    """While a holder keeps the ``apps`` row, the delete purges nothing: its
    first statement waits for the row. A delete with no row lock would purge
    and commit chunks under the holder."""
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)

    async def go() -> tuple[str, int, tuple[int, Any, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("wait")))
        await _fill(world.a, ANA, pid, 25)
        _status, got = await _preview(world.a, ANA, pid)
        async with tenant_session(world.a) as hold:
            assert await packages_store.get_package(hold, ANA, pid, for_update=True)
            task = asyncio.create_task(_delete(world.a, ANA, pid, got["confirm"]))
            await asyncio.sleep(0.6)
            mid = _records(world, pid)
            assert not task.done()
        return pid, mid, await task

    pid, mid, (status, detail, _h) = _run(world, go)
    assert mid == 25
    assert status == 200, detail
    assert _records(world, pid) == 0


# ── 6. A delete, a record write and a publish of one package ───────────────


def test_r8_a_delete_a_record_write_and_a_publish_of_one_package_leave_no_orphan(
        world, monkeypatch) -> None:
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)

    async def writer(pid: str, oid: str) -> list[str]:
        out = []
        for i in range(20):
            try:
                async with tenant_session(world.a) as db:
                    got = await objects_store.create_record(
                        db, ANA, oid, {"site": f"w{i}"}, package_id=pid)
                out.append("ok" if got is not None else "gone")
            except objects_store.StoreBusy:
                out.append("busy")
            await asyncio.sleep(0.01)
        return out

    async def go() -> list[tuple[str, list[str], Any, Any, list[Any]]]:
        rounds = []
        for _ in range(4):
            pid = await _live(world.a, ANA, _manifest(_mid("race")))
            await _fill(world.a, ANA, pid, 30)
            [oid] = _object_ids(world, pid)
            got = await asyncio.gather(
                _delete_all(world.a, ANA, pid, tries=80), writer(pid, oid),
                _call_full("publish", world.a, ANA, pid, payload={}),
                return_exceptions=True)
            rounds.append((pid, [oid], *got))
        return rounds

    for pid, oids, seen, writes, published in _run(world, go):
        assert not isinstance(seen, BaseException), seen
        assert seen[-1] == 200
        assert not isinstance(writes, BaseException), writes
        assert set(writes) <= {"ok", "gone", "busy"}
        _ok_or_busy(published, 200, 404)
        assert _footprint(world, pid, oids, []) == dict.fromkeys(
            _footprint(world, pid, oids, []), 0)


# ── 7. The audit and the delete are one transaction ────────────────────────


def test_r8_a_failed_audit_keeps_the_package_and_its_rows(world, monkeypatch) -> None:
    async def setup() -> str:
        pid = await _live(world.a, ANA, _manifest(_mid("audit")))
        await _fill(world.a, ANA, pid, 3)
        return pid

    pid = _run(world, setup)
    oids = _object_ids(world, pid)
    before = _footprint(world, pid, oids, [])
    audits = _audits(world, pid)
    broken = packages_store._AUDIT_SQL.replace(":kind", "'gone-' || :kind")
    assert broken != packages_store._AUDIT_SQL

    async def go() -> Any:
        _status, got = await _preview(world.a, ANA, pid)
        monkeypatch.setattr(packages_store, "_AUDIT_SQL", broken)
        try:
            return await _delete(world.a, ANA, pid, got["confirm"])
        except DBAPIError as exc:
            return exc

    got = _run(world, go)
    assert isinstance(got, DBAPIError), got
    assert _footprint(world, pid, oids, []) == before
    assert _records(world, pid) == 3
    assert _audits(world, pid) == audits


# ── 8. The deleted id ──────────────────────────────────────────────────────


def test_r8_each_route_on_a_deleted_id_is_404_and_writes_nothing(world) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]], Any]:
        pid = await _live(world.a, ANA, _manifest(_mid("gone")))
        status, got = await _preview(world.a, ANA, pid)
        await _delete(world.a, ANA, pid, got["confirm"])
        world.audit_rows = len(_audits(world, pid))
        token = bind_tenant(world.a)
        try:
            try:
                await invoke.invoke_action(pid, "find", request=_Request({}),
                                           user=_user(world.a, ANA))
                door: tuple[int, Any] = (200, None)
            except HTTPException as exc:
                door = (exc.status_code, exc.detail)
            catalog = await actions.list_actions(user=_user(world.a, ANA), app=pid)
        finally:
            release_tenant(token)
        out = [door,
               await _call("get_package", world.a, ANA, pid),
               await _publish(world.a, ANA, pid),
               await _call("list_versions", world.a, ANA, pid),
               await _preview(world.a, ANA, pid)]
        status, detail, _h = await _delete(world.a, ANA, pid, got["confirm"])
        return pid, [*out, (status, detail)], catalog

    pid, got, catalog = _run(world, go)
    assert got == [(404, NOT_FOUND)] * 6
    assert catalog == {"actions": []}
    assert len(_audits(world, pid)) == world.audit_rows
    assert [a[3].get("op") for a in _audits(world, pid)][-1] == "package.delete"
    assert _scalar(world, "SELECT count(*) FROM app_versions WHERE app_id = CAST(:p AS uuid)",
                   p=pid) == 0


def test_r8_a_second_delete_after_200_is_404_and_writes_no_second_row(world) -> None:
    async def go() -> tuple[str, tuple[int, Any, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid("twice")))
        _status, got = await _preview(world.a, ANA, pid)
        first = await _delete(world.a, ANA, pid, got["confirm"])
        assert first[0] == 200, first
        return pid, await _delete(world.a, ANA, pid, got["confirm"])

    pid, (status, detail, _h) = _run(world, go)
    assert (status, detail) == (404, NOT_FOUND)
    assert len(_deletes(world, pid)) == 1


# ── Fix round 1: the partial row, a publish between steps, the share rule ──


def test_r8_a_202_call_writes_one_partial_row_in_the_step_of_its_purge(
        world, monkeypatch) -> None:
    """P2-1. Each call that ends with records left writes one partial row,
    with the records of that call. The final row names the records of its
    call and the total of all calls."""
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)
    real = packages_store.STEP_SECONDS

    async def go() -> tuple[str, list[Any], list[list[dict[str, Any]]]]:
        pid = await _live(world.a, ANA, _manifest(_mid("partial")))
        await _fill(world.a, ANA, pid, 25)
        _status, got = await _preview(world.a, ANA, pid)
        _every_step_ends_its_call(monkeypatch)
        answers, rows = [], []
        token = got["confirm"]
        for _ in range(2):
            answers.append(await _delete(world.a, ANA, pid, token))
            token = answers[-1][1]["confirm"]
            rows.append(_partials(world, pid))
        monkeypatch.setattr(packages_store, "STEP_SECONDS", real)
        answers.append(await _delete(world.a, ANA, pid, token))
        return pid, answers, rows

    pid, answers, rows = _run(world, go)
    assert [a[0] for a in answers] == [202, 202, 200], answers
    assert [a[1]["remaining"] for a in answers[:2]] == [15, 5]
    assert [[r["records_this_call"] for r in found] for found in rows] == [[10], [10, 10]]
    assert {r["reason"] for r in rows[-1]} == {"budget"}
    [detail] = _deletes(world, pid)
    assert (detail["records_this_call"], detail["records_total"]) == (5, 25)


def test_r8_a_failed_partial_audit_rolls_back_the_purge_of_its_step(world, monkeypatch) -> None:
    """P2-1. The partial row is in the transaction of the purge of the last
    step, so a failed insert keeps each record of that step."""
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)

    async def setup() -> str:
        pid = await _live(world.a, ANA, _manifest(_mid("partfail")))
        await _fill(world.a, ANA, pid, 25)
        return pid

    pid = _run(world, setup)
    broken = packages_store._AUDIT_SQL.replace(":kind", "'gone-' || :kind")

    async def go() -> Any:
        _status, got = await _preview(world.a, ANA, pid)
        _every_step_ends_its_call(monkeypatch)
        monkeypatch.setattr(packages_store, "_AUDIT_SQL", broken)
        try:
            return await _delete(world.a, ANA, pid, got["confirm"])
        except DBAPIError as exc:
            return exc

    got = _run(world, go)
    assert isinstance(got, DBAPIError), got
    assert _records(world, pid) == 25
    assert _partials(world, pid) == []


def test_r8_a_publish_between_two_steps_that_adds_an_object_ends_the_call_with_409(
        world, monkeypatch) -> None:
    """P3-1. The member confirmed a package with one object. A publish
    between the first and the second step adds ``invoices``. The second step
    finds the new name, deletes nothing, and the call answers 409 with the
    new preview. The first step holds, and its partial row names it."""
    monkeypatch.setattr(packages_store, "DELETE_CHUNK", 10)
    two = [{"name": "visit", "fields": _FIELDS},
           {"name": "invoices", "fields": [{"key": "no", "type": "text"}]}]
    real_session = packages._tenant_session
    state = {"armed": False, "inside": False, "opened": 0}
    published: list[tuple[int, Any]] = []
    holder: dict[str, str] = {}

    @asynccontextmanager
    async def spy(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if state["armed"] and not state["inside"]:
            state["opened"] += 1
            # 1: the owner read, 2: the first step, 3: the second step.
            if state["opened"] == 3:
                state["inside"] = True
                try:
                    published.append(await _publish(world.a, ANA, holder["pid"]))
                finally:
                    state["inside"] = False
        async with real_session(*args, **kwargs) as db:
            yield db

    async def go() -> tuple[str, tuple[int, Any, Any]]:
        mid = _mid("between")
        pid = await _live(world.a, ANA, _manifest(mid))
        holder["pid"] = pid
        await _fill(world.a, ANA, pid, 30)
        status, saved = await _call("save_draft", world.a, ANA, pid, payload=_manifest(mid, two))
        assert status == 200, saved
        _status, got = await _preview(world.a, ANA, pid)
        assert [o["name"] for o in got["objects"]] == ["visit"]
        monkeypatch.setattr(packages, "_tenant_session", spy)
        state["armed"] = True
        return pid, await _delete(world.a, ANA, pid, got["confirm"])

    pid, (status, detail, _h) = _run(world, go)
    assert [p[0] for p in published] == [200], published
    assert status == 409, detail
    assert detail["code"] == "counts_changed"
    assert [o["name"] for o in detail["preview"]["objects"]] == ["invoices", "visit"]
    assert _records(world, pid) == 20
    assert sorted(_scalar(world, "SELECT array_agg(name) FROM studio_objects "
                          "WHERE package_id = CAST(:p AS uuid)", p=pid)) == ["invoices", "visit"]
    [row] = _partials(world, pid)
    assert (row["records_this_call"], row["reason"]) == (10, "changed")
    assert _deletes(world, pid) == []


def test_r8_a_private_install_that_the_owner_shared_counts_as_another_members(world) -> None:
    """P3-2. The count of the installs of other members reads the one share
    rule of ``package_host/sharing.py``. A private install of the owner
    counts once the owner shares it with a person, and a grant to an agent
    shares nothing."""
    pid = _run(world, lambda: _live(world.a, ANA, _manifest(_mid("shared"))))
    org = {"o": world.a, "p": pid}
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO package_install (organization_id, app_id, version, scope_kind, "
            "scope_subject, installed_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 1, "
            "'private', :m, :m)"), {**org, "m": ANA})
        c.execute(text("INSERT INTO app_grants (organization_id, app_id, subject, role, "
                       "granted_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'agent:x', "
                       "'use', :m)"), {**org, "m": ANA})

    def others() -> int:
        status, got = _run(world, lambda: _preview(world.a, ANA, pid))
        assert status == 200, got
        assert got["totals"]["installs"] == 1
        return int(got["totals"]["other_member_installs"])

    alone = others()
    with world.p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO app_grants (organization_id, app_id, subject, role, "
                       "granted_by) VALUES (CAST(:o AS uuid), CAST(:p AS uuid), :s, 'use', :m)"),
                  {**org, "s": BEN, "m": ANA})
    assert (alone, others()) == (0, 1)


def test_r8_a_purge_after_a_bulk_load_reads_a_bounded_number_of_rows(world, app_engine) -> None:  # noqa: F811
    """The live 202 check of 2026-11-05 found it. ANALYZE sees the object with
    no record, and then a bulk load adds 20,000. With ``id IN (subselect)``
    the planner ran the LIMIT again for each record, so one purge of 500 read
    millions of buffers and passed its 5 s deadline on each step. The ARRAY
    form runs the subselect once and reads each record by its key."""
    pid = _run(world, lambda: _live(world.a, ANA, _manifest(_mid("bulk"))))
    [oid] = _object_ids(world, pid)
    with world.p.admin_engine.begin() as c:
        c.execute(text("ALTER TABLE studio_records SET (autovacuum_enabled = false)"))
        c.execute(text("ANALYZE studio_records"))
        c.execute(text(
            "INSERT INTO studio_records (organization_id, object_id, data, created_by, updated_by) "
            "SELECT CAST(:o AS uuid), CAST(:i AS uuid), jsonb_build_object('site', 's' || g), "
            ":m, :m FROM generate_series(1, 20000) g"), {"o": world.a, "i": oid, "m": ANA})
    try:
        with app_engine.connect() as c:
            trans = c.begin()
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.a})
            plan = c.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                                  + objects_store._PURGE_SQL), {"oid": oid, "n": 500}).scalar_one()
            trans.rollback()
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text("ALTER TABLE studio_records RESET (autovacuum_enabled)"))
    top = (plan if isinstance(plan, list) else json.loads(plan))[0]["Plan"]
    blocks = int(top["Shared Hit Blocks"]) + int(top["Shared Read Blocks"])
    assert blocks < 30_000, blocks
    assert _records(world, pid) == 20_000
