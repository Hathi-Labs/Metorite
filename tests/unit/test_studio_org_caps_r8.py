"""WS-STU C10a — the org caps of Studio records on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 C10a. Code:
``gateway/studio/objects_store._run_steps`` and ``_check_org_caps``, the door
``gateway/routes/studio/invoke._set_write_error``, and the migration that
creates ``studio_org_usage`` (432 at build), with its three triggers and its
backfill. ``test_studio_org_caps.py`` holds the hermetic half.

The store and the door run on a phase-4 promoted scratch database, as the app
role with no BYPASSRLS, so FORCE RLS binds each statement. Two organizations,
and a third one that the org delete test makes and deletes. A test lowers a
cap to the usage of now, because the module shares its orgs.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_org_caps_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (C10a).**

a. A create past a cap gets 422 ``org_cap_reached`` through the door, and
   writes nothing. A create past the byte cap is refused the same way.
b. At the cap, a delete and an update that makes a record smaller pass.
c. The gauge equals ``count(*)`` and ``sum(octet_length(data::text))`` after
   each writer: the store, ``purge_records``, ``drop_object`` and the cascade
   of ``delete_package``.
d. Two orgs never share a row. Org B reads no row of org A as the app role.
e. The delete of an org with records works, and takes its gauge row.
f. Two writers at the cap on two connections: exactly one passes.
g. The backfill of the migration is exact for rows that the gauge did not
   count, and a second run changes nothing. The whole file runs twice.

**The mutations (R7).** Commit before each, and restore with git after.
⚠️ Not run at build (2026-11-13). The hermetic H1 to H3 of
``test_studio_org_caps.py`` were run, and each went red.

* M1. The migration: make the DELETE trigger upsert (INSERT ... ON CONFLICT).
  ``test_r8_an_org_delete_takes_its_records_and_its_gauge_row`` goes red.
* M2. The migration: drop the UPDATE trigger.
  ``test_r8_the_gauge_is_exact_after_each_writer`` goes red at the update.
* M3. ``objects_store._run_steps``: drop the call of ``_check_org_caps``.
  ``test_r8_a_create_past_the_record_cap_is_422_and_writes_nothing`` goes red.
* M4. ``objects_store._check_org_caps``: drop ``and records > records_were``.
  ``test_r8_at_the_cap_a_delete_and_a_smaller_update_pass`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, direct_proof
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store
from gateway.studio import packages_store
from gateway.studio.objects_store import BatchError, OrgCapReached, Write
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc10.test"
DIRECT_KEY = "r8-c10a-direct-key-" + "k" * 30

FIELDS: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "qty", "type": "number", "hot": True},
]
ACTIONS: list[dict[str, Any]] = [
    {"id": "add", "uses": "object:thing.create"},
    {"id": "change", "uses": "object:thing.update"},
]


def _manifest(mid: str) -> dict[str, Any]:
    return {"studio": 1, "id": mid, "name": "Things", "purpose": "Count things.",
            "objects": [{"name": "thing", "fields": FIELDS}], "actions": ACTIONS}


def _migration_path() -> Path:
    """The migration of the gauge, found by CONTENT (R1)."""
    hits = [p for p in sorted((_ROOT / "infra/postgres").glob("[0-9]*.sql"))
            if "CREATE TABLE IF NOT EXISTS studio_org_usage" in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _user(org: str) -> UserContext:
    return UserContext(email=ANA, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Request:
    """The parts of a request that the door reads."""

    def __init__(self, payload: Any, *, path: str, headers: dict[str, str]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", dict(headers)
        self.url = SimpleNamespace(path=path)
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


async def _direct(org: str, pid: str, action: str, payload: Any) -> tuple[int, Any]:
    """The real door handler, with a valid proof for Ana: a direct write."""
    path = f"/studio/apps/{pid}/actions/{action}"
    headers = {direct_proof.HEADER: direct_proof.sign(DIRECT_KEY, ANA, "POST", path)}
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=_Request(
            payload, path=path, headers=headers), user=_user(org))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)
    if isinstance(found, JSONResponse):
        return found.status_code, json.loads(found.body)
    return 200, found


def _run(w: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _package(org: str, manifest: dict[str, Any]) -> tuple[str, store.StudioObject]:
    async with tenant_session(org) as db:
        pid = (await packages_store.create_package(db, ANA, manifest)).id
    async with tenant_session(org) as db:
        obj = await store.define_object(db, ANA, package_id=pid, name="thing", fields=FIELDS)
    assert obj is not None
    return pid, obj


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Two packages of org A and one of org B, each of Ana, with one object."""
    p = promoted
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", "*")
    monkeypatch.setattr(settings, "studio_direct_key", DIRECT_KEY)
    invoke._IN_FLIGHT.clear()
    mid = f"caps-{TAG}-{uuid.uuid4().hex[:6]}"
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, mid=mid)

    async def setup() -> None:
        w.pkg, w.obj = await _package(w.a, _manifest(mid))
        w.pkg2, w.obj2 = await _package(w.a, _manifest(mid))
        w.pkg_b, w.obj_b = await _package(w.b, _manifest(mid))

    _run(w, setup)
    yield w
    invoke._IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_audit WHERE app_id IN "
                       "(SELECT id FROM apps WHERE slug LIKE :t)"), {"t": f"{mid}-%"})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"{mid}-%"})


# -- reads as the superuser ----------------------------------------------------------


def _gauge(w: Any, org: str) -> tuple[int, int] | None:
    with w.p.admin_engine.connect() as c:
        row = c.execute(text("SELECT records, bytes FROM studio_org_usage "
                             "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).first()
    return None if row is None else (int(row[0]), int(row[1]))


def _truth(w: Any, org: str) -> tuple[int, int]:
    with w.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT count(*), coalesce(sum(octet_length(data::text)), 0) FROM studio_records "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).one()
    return int(row[0]), int(row[1])


def _exact(w: Any, *orgs: str) -> None:
    for org in orgs:
        assert _gauge(w, org) == _truth(w, org), org


def _count(w: Any, oid: str) -> int:
    with w.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": oid}).scalar_one())


async def _put(org: str, obj: store.StudioObject, *writes: Write) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, ANA, package_id=obj.package_id, writes=list(writes))


def _create(obj: store.StudioObject, qty: int = 1, title: str = "x") -> Write:
    return Write("create", obj.id, data={"title": title, "qty": qty})


# -- a. a write past a cap -------------------------------------------------------------


def test_r8_a_create_past_the_record_cap_is_422_and_writes_nothing(world, monkeypatch) -> None:
    w = world
    _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    _exact(w, w.a)
    records, size = _truth(w, w.a)
    monkeypatch.setattr(store, "MAX_RECORDS_PER_ORG", records)
    before = _count(w, w.obj.id)

    status, got = _run(w, lambda: _direct(w.a, w.pkg, "add", {"data": {"title": "y", "qty": 2}}))
    assert status == 422, got
    assert got["code"] == "org_cap_reached"
    assert got["message"] == (
        f"This organization holds {records:,} records, the most it can hold, "
        "so delete records that you do not use.")
    assert _count(w, w.obj.id) == before
    assert _gauge(w, w.a) == (records, size)
    # The refusal has its own audit reason.
    with w.p.admin_engine.connect() as c:
        detail = c.execute(text("SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
                                "ORDER BY id DESC LIMIT 1"), {"p": w.pkg}).scalar_one()
    detail = detail if isinstance(detail, dict) else json.loads(detail)
    assert (detail["outcome"], detail["reason"]) == ("refused", "org_cap_reached")
    # Org B is under its own gauge, so its create passes.
    _run(w, lambda: _put(w.b, w.obj_b, _create(w.obj_b)))
    _exact(w, w.a, w.b)


def test_r8_a_create_past_the_byte_cap_is_refused_in_the_store(world, monkeypatch) -> None:
    w = world
    _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    records, size = _truth(w, w.a)
    monkeypatch.setattr(store, "MAX_RECORD_BYTES_PER_ORG", size)
    before = _count(w, w.obj.id)

    with pytest.raises(BatchError) as caught:
        _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    assert isinstance(caught.value.cause, OrgCapReached)
    assert caught.value.index == 0
    assert str(caught.value.cause) == (
        f"This organization holds {size:,} bytes of records, the most it can hold, "
        "so delete records that you do not use.")
    assert _count(w, w.obj.id) == before
    assert _gauge(w, w.a) == (records, size)


# -- b. at the cap, a delete and a smaller update pass -----------------------------------


def test_r8_at_the_cap_a_delete_and_a_smaller_update_pass(world, monkeypatch) -> None:
    w = world
    made = _run(w, lambda: _put(w.a, w.obj, _create(w.obj, title="a long title " * 20),
                                _create(w.obj), _create(w.obj)))
    records, size = _truth(w, w.a)
    # One under the usage of now: the org is past each cap.
    monkeypatch.setattr(store, "MAX_RECORDS_PER_ORG", records - 1)
    monkeypatch.setattr(store, "MAX_RECORD_BYTES_PER_ORG", size - 1)

    # A create is refused.
    with pytest.raises(BatchError) as caught:
        _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    assert isinstance(caught.value.cause, OrgCapReached)
    # An update that makes a record smaller passes.
    _run(w, lambda: _put(w.a, w.obj, Write("update", w.obj.id, made[0].id, {"title": "short"})))
    # An update that makes a record bigger is refused.
    with pytest.raises(BatchError) as caught:
        _run(w, lambda: _put(w.a, w.obj, Write("update", w.obj.id, made[1].id,
                                               {"title": "a much longer title " * 40})))
    assert isinstance(caught.value.cause, OrgCapReached)
    # A delete passes.
    _run(w, lambda: _put(w.a, w.obj, Write("delete", w.obj.id, made[2].id)))
    # A create and a delete in one batch keep the count, and the record cap
    # does not refuse it. The bytes went down, so the byte cap does not either.
    _run(w, lambda: _put(w.a, w.obj, _create(w.obj, title=""),
                         Write("delete", w.obj.id, made[1].id)))
    _exact(w, w.a)
    assert _gauge(w, w.a)[0] == records - 1


# -- c. the gauge is exact after each writer ---------------------------------------------


def test_r8_the_gauge_is_exact_after_each_writer(world) -> None:
    w = world
    _exact(w, w.a, w.b)
    made = _run(w, lambda: _put(w.a, w.obj, *[_create(w.obj, qty=i) for i in range(30)]))
    _exact(w, w.a)
    _run(w, lambda: _put(w.a, w.obj, Write("update", w.obj.id, made[0].id,
                                           {"title": "a bigger value " * 30})))
    _exact(w, w.a)
    _run(w, lambda: _put(w.a, w.obj2, *[_create(w.obj2, qty=i) for i in range(12)]))
    _exact(w, w.a)

    async def purge() -> int:
        async with tenant_session(w.a) as db:
            await store.lock_org(db)
            return await store.purge_records(db, w.pkg, limit=10)

    assert _run(w, purge) == 10
    _exact(w, w.a)

    async def drop() -> bool:
        async with tenant_session(w.a) as db:
            return await store.drop_object(db, w.obj2.id)

    assert _run(w, drop) is True
    assert _count(w, w.obj2.id) == 0
    _exact(w, w.a)

    # The whole delete of a package: the purge steps, then the cascade of `apps`.
    async def delete() -> Any:
        async with tenant_session(w.a) as db:
            preview = await packages_store.delete_preview(db, ANA, w.pkg)
        assert preview is not None
        async with tenant_session(w.a) as db:
            return await packages_store.delete_package(db, ANA, w.pkg, confirm=preview.confirm,
                                                       chunk=1_000)

    step = _run(w, delete)
    assert step is not None and step.deleted, step
    assert _count(w, w.obj.id) == 0
    _exact(w, w.a, w.b)

    # Raw SQL as the superuser: the triggers see it too.
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO studio_records (organization_id, object_id, data, created_by, "
            "updated_by) VALUES (CAST(:o AS uuid), CAST(:oid AS uuid), "
            "CAST('{\"title\": \"raw\"}' AS jsonb), :a, :a)"),
            {"o": w.b, "oid": w.obj_b.id, "a": ANA})
    _exact(w, w.a, w.b)


# -- d. two orgs never share a row ----------------------------------------------------


def test_r8_each_org_reads_its_own_gauge_row_only(world, app_engine) -> None:  # noqa: F811
    w = world
    _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    _run(w, lambda: _put(w.b, w.obj_b, _create(w.obj_b), _create(w.obj_b)))
    _exact(w, w.a, w.b)
    eng = create_engine(w.p.app_url, future=True)
    try:
        for org in (w.a, w.b):
            with eng.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                rows = c.execute(text(
                    "SELECT organization_id::text, records, bytes FROM studio_org_usage")).all()
            assert [(r[0], int(r[1]), int(r[2])) for r in rows] == [(org, *_truth(w, org))]
            # The app role cannot write a row of the other org.
            other = w.b if org == w.a else w.a
            with eng.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                changed = c.execute(text(
                    "UPDATE studio_org_usage SET records = 0 "
                    "WHERE organization_id = CAST(:x AS uuid)"), {"x": other}).rowcount
                assert changed == 0
                c.rollback()
    finally:
        eng.dispose()
    # An unbound session reads nothing.
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM studio_org_usage")).scalar_one() == 0


# -- e. the delete of an org -----------------------------------------------------------


def test_r8_an_org_delete_takes_its_records_and_its_gauge_row(world) -> None:
    w = world
    org = str(uuid.uuid4())
    with w.p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (CAST(:id AS uuid), :s, :s)"), {"id": org, "s": f"c10a-{TAG}"})

    async def fill() -> None:
        _pid, obj = await _package(org, _manifest(w.mid))
        await _put(org, obj, *[_create(obj, qty=i) for i in range(5)])

    _run(w, fill)
    assert _gauge(w, org) == _truth(w, org)
    assert _gauge(w, org)[0] == 5
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": org})
    assert _gauge(w, org) is None
    assert _truth(w, org) == (0, 0)
    _exact(w, w.a, w.b)


# -- f. two writers at the cap ---------------------------------------------------------


def test_r8_two_writers_at_the_cap_cannot_both_pass(world, monkeypatch) -> None:
    w = world
    _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    records, _size = _truth(w, w.a)
    # Room for exactly one more record.
    monkeypatch.setattr(store, "MAX_RECORDS_PER_ORG", records + 1)

    async def both() -> list[Any]:
        first_wrote = asyncio.Event()

        async def first() -> str:
            async with tenant_session(w.a) as db:
                await store.apply_writes(db, ANA, package_id=w.pkg, writes=[_create(w.obj)])
                first_wrote.set()
                # Hold the org lock while the second writer starts.
                await asyncio.sleep(0.4)
            return "ok"

        async def second() -> str:
            await first_wrote.wait()
            async with tenant_session(w.a) as db:
                await store.apply_writes(db, ANA, package_id=w.pkg2, writes=[_create(w.obj2)])
            return "ok"

        return await asyncio.gather(first(), second(), return_exceptions=True)

    got = _run(w, both)
    assert got[0] == "ok", got
    assert isinstance(got[1], BatchError) and isinstance(got[1].cause, OrgCapReached), got
    _exact(w, w.a)
    assert _gauge(w, w.a)[0] == records + 1


# -- g. the backfill -------------------------------------------------------------------


def _fill_block() -> str:
    sql = _migration_path().read_text(encoding="utf-8")
    found = re.search(r"DO \$fill\$.*?\$fill\$;", sql, re.S)
    assert found is not None
    return found.group(0)


def test_r8_the_backfill_is_exact_for_rows_that_the_gauge_did_not_count(world) -> None:
    w = world
    _run(w, lambda: _put(w.a, w.obj, *[_create(w.obj, qty=i) for i in range(7)]))
    _run(w, lambda: _put(w.b, w.obj_b, _create(w.obj_b)))
    truth_a, truth_b = _truth(w, w.a), _truth(w, w.b)
    # The rows exist, and the gauge does not know them: one row is wrong and
    # the other is gone.
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_org_usage SET records = 3, bytes = 9 "
                       "WHERE organization_id = CAST(:o AS uuid)"), {"o": w.a})
        c.execute(text("DELETE FROM studio_org_usage WHERE organization_id = CAST(:o AS uuid)"),
                  {"o": w.b})
    block = _fill_block()
    for _ in range(2):
        with w.p.admin_engine.begin() as c:
            c.exec_driver_sql(block)
        assert _gauge(w, w.a) == truth_a
        assert _gauge(w, w.b) == truth_b
    # The whole file runs again and changes nothing.
    raw = w.p.admin_engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(_migration_path().read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()
    _exact(w, w.a, w.b)
    # The triggers still work after the second run, with one change each.
    _run(w, lambda: _put(w.a, w.obj, _create(w.obj)))
    assert _gauge(w, w.a)[0] == truth_a[0] + 1
    _exact(w, w.a)
