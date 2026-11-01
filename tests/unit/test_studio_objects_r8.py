"""WS-STU C1 — the typed object store of AI Studio on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1 row. Code:
``gateway/studio/objects_store.py`` and ``acb_common/studio_fields.py``.
Migration: the file that creates ``studio_objects`` (323 at build).

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_objects_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass. ``test_studio_fields.py`` holds the hermetic half.

**Acceptance (C1).**

a. The migration applies, and all four tables have ENABLE and FORCE RLS.
b. Org B reads and writes nothing of org A. Each foreign key is a pair with
   ``organization_id``, so a row cannot point at a row of another org.
c. Each write is checked: a bad value raises a ``FieldError`` with its key
   and writes nothing.
d. A hot field fills its side-table row in the statement of the record
   write, and only a hot field does.
e. The budget: the 9th hot field of an object and the 25th of an org are
   refused, by the store and by the trigger, also under two writers at once.
f. A record of more than 64 KiB is refused, by the store and by the CHECK.
g. A field turns hot with a backfill, on an object of at most 50,000 records.
h. A deleted package or object leaves no record and no hot row.
i. Only a Studio package owns an object. An App Workshop app owns none
   (STU-B2a). The door reads an object by its name in one package.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. The migration and ``generated/04_policies.sql``: drop the line
  ``ALTER TABLE studio_records FORCE  ROW LEVEL SECURITY``.
  ``test_the_migration_holds_the_rls_the_keys_and_the_caps`` and
  ``test_r8_all_four_tables_have_enable_and_force_rls`` go red.
* M2. The migration: make ``studio_records_object_fk`` a key on
  ``object_id`` alone, to ``studio_objects (id)``. The ``record`` case of
  ``test_r8_a_foreign_key_cannot_cross_an_org`` goes red. The same holds for
  ``studio_objects_package_fk`` and its ``object`` case.
* M4a. ``objects_store.add_field``: drop the ``_check_hot_room`` call.
  ``test_r8_the_ninth_hot_field_of_an_object_is_refused`` and
  ``test_r8_two_writers_at_once_get_one_hot_field`` go red: the trigger
  refuses with a DBAPIError, not a BudgetExceeded.
* M4b. The migration: drop the ``PERFORM pg_advisory_xact_lock`` of the
  trigger. ``test_r8_two_raw_writers_at_once_the_trigger_refuses_the_25th``
  goes red: both writers pass, and the org holds 25 hot fields.
* M5. ``studio_fields.hot_value``: drop ``not field.hot or``.
  ``test_r8_a_hot_field_fills_its_row_on_each_write`` goes red.
* M6. The migration: drop the CHECK ``studio_records_data_size``.
  ``test_r8_an_oversize_record_is_refused`` goes red.
* M10. ``objects_store._locked_fields``: drop ``await _lock_org(db)``, the old
  lock order. ``test_r8_joined_calls_in_two_sessions_never_deadlock`` goes red
  with "deadlock detected".
* M11. ``objects_store``: drop the package filter from ``_SHARE_OBJECT_SQL``
  and from ``_IN_PACKAGE``. ``test_r8_a_record_call_names_its_package_and_its_object``
  goes red.
* M12. ``studio_fields.stored_field``: return ``parse_field_spec(...)`` again.
  ``test_r8_an_older_stored_spec_still_reads_and_writes`` goes red.
* M13 (STU-B2a). ``objects_store._PACKAGE_SQL``: drop ``AND format =
  'manifest'``. ``test_r8_a_code_app_owns_no_object`` goes red.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields as sf
from acb_common.db import tenant_session
from acb_common.studio_fields import FieldError, RecordError
from gateway.studio import objects_store as store
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS studio_objects (" in p.read_text(encoding="utf-8"))
_POLICIES = _ROOT / "infra" / "postgres" / "generated" / "04_policies.sql"
_STORE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "studio" / "objects_store.py"

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc1.test"
_TABLES = ("studio_objects", "studio_object_fields", "studio_records", "studio_record_hot")

_VISIT: list[dict[str, Any]] = [
    {"key": "customer", "type": "relation", "to": "crm.account", "required": True},
    {"key": "engineer", "type": "person"},
    {"key": "when", "type": "datetime", "hot": True},
    {"key": "status", "type": "select", "options": ["planned", "on site", "done"],
     "hot": True},
    {"key": "hours", "type": "number", "hot": True},
    {"key": "visit_day", "type": "date", "hot": True},
    {"key": "paid", "type": "checkbox", "hot": True},
    {"key": "fee", "type": "money", "hot": True},
    {"key": "notes", "type": "text"},
    {"key": "site", "type": "geo"},
]

_RECORD: dict[str, Any] = {
    "customer": {"id": "acct-1"},
    "engineer": "ben@stuc1.test",
    "when": "2026-11-01T09:30:00+05:30",
    "status": "planned",
    "hours": 2.5,
    "visit_day": "2026-11-01",
    "paid": False,
    "fee": {"minor": 125000, "currency": "INR"},
    "notes": "Bring the nozzle kit",
    "site": {"lat": 12.97, "lng": 77.59},
}


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


#: A Studio package, as the superuser seeds it: a row of `apps` with
#: `format = 'manifest'` and a manifest that passes `apps_manifest_package`.
#: Only a package owns an object (STU-B2a), so a code app is no seed here.
_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc1', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc1", "name": "stuc1", "purpose": "The C1 R8 suite."})


@pytest.fixture
def world(promoted):  # noqa: F811
    """Three packages: two of org A and one of org B. Teardown deletes every
    studio row of both orgs, so each test starts with an empty budget."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("a2", p.org_a), ("b", p.org_b)):
            pkgs[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc1-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=pkgs["a"], pkg_a2=pkgs["a2"],
                          pkg_b=pkgs["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE organization_id IN "
                       "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": p.org_a, "b": p.org_b})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc1-{TAG}-{run}-%"})


def _go(world: Any, fn: Any) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _define(org: str, pkg: str, name: str = "visit",
                  fields: list[dict[str, Any]] | None = None) -> store.StudioObject:
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name,
                                         fields=_VISIT if fields is None else fields)
    assert made is not None
    return made


async def _create(org: str, obj: store.StudioObject, data: dict[str, Any]) -> store.Record:
    async with tenant_session(org) as db:
        rec = await store.create_record(db, ANA, obj.id, data, package_id=obj.package_id)
    assert rec is not None
    return rec


def _number_fields(count: int, hot: int, prefix: str = "n") -> list[dict[str, Any]]:
    return [{"key": f"{prefix}{i}", "type": "number", "hot": i < hot} for i in range(count)]


def _admin_one(world: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(sql), params or {}).scalar_one()


def _org_hot(world: Any, org: str) -> int:
    return int(_admin_one(world, "SELECT count(*) FROM studio_object_fields "
                          "WHERE organization_id = CAST(:o AS uuid) AND hot", {"o": org}))


def _hot_rows(world: Any, record_id: str) -> dict[str, tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT field_key, v_text, v_num, v_ts, v_bool FROM studio_record_hot "
            "WHERE record_id = CAST(:r AS uuid)"), {"r": record_id}).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _refused(world: Any, org: str, sql: str, params: dict[str, Any]) -> str:
    """Run *sql* as the app role in *org*. Answers the error text."""
    async def go() -> None:
        async with tenant_session(org) as db:
            await db.execute(text(sql), params)

    with pytest.raises(DBAPIError) as exc:
        _go(world, go)
    return str(exc.value)


_RAW_FIELD = (
    "INSERT INTO studio_object_fields (object_id, key, type, hot) "
    "VALUES (CAST(:oid AS uuid), :key, 'number', true)")


# ── The schema and the wiring ──────────────────────────────────────────────


def test_the_migration_holds_the_rls_the_keys_and_the_caps() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    policies = _POLICIES.read_text(encoding="utf-8")
    for table in _TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in policies
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql
    assert sql.count(
        "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)") == 4
    # Every foreign key is a pair that starts with the org.
    for fk in (
        "FOREIGN KEY (organization_id, package_id)\n        REFERENCES apps (organization_id, id)",
        "FOREIGN KEY (organization_id, object_id)\n        REFERENCES studio_objects (organization_id, id)",
        "FOREIGN KEY (organization_id, object_id, record_id)\n"
        "        REFERENCES studio_records (organization_id, object_id, id)",
        "FOREIGN KEY (organization_id, object_id, field_key)\n"
        "        REFERENCES studio_object_fields (organization_id, object_id, key)",
    ):
        assert fk in sql.replace("\r\n", "\n"), fk
    assert len(re.findall(r"FOREIGN KEY \(organization_id,", sql)) == 5
    assert len(re.findall(r"\bON DELETE CASCADE\b", sql)) >= 9
    # The caps of the module are the caps of the SQL.
    assert f"octet_length(data::text) <= {sf.MAX_RECORD_BYTES}" in sql
    assert f"org_count >= {sf.MAX_HOT_PER_ORG}" in sql
    assert f"object_count >= {sf.MAX_HOT_PER_OBJECT}" in sql
    assert f"char_length(v_text) <= {sf.HOT_TEXT_CHARS}" in sql
    assert "PERFORM pg_advisory_xact_lock(" in sql
    assert "hashtextextended('studio_objects:' || NEW.organization_id::text, 0)" in sql
    assert "'studio_objects:' || CAST(" in store._ORG_LOCK_SQL
    type_check = sql.split("CONSTRAINT studio_object_fields_type CHECK", 1)[1].split(";")[0]
    assert re.findall(r"'(\w+)'", type_check.split("),", 1)[0]) == list(sf.FIELD_TYPES)
    hot_check = sql.split("CONSTRAINT studio_object_fields_hot_type CHECK", 1)[1]
    assert re.findall(r"'(\w+)'", hot_check.split("AND (spec", 1)[0]) == list(sf.HOT_TYPES)
    # The two blocks of 321, so the key exists on a fresh ladder.
    assert "ADD CONSTRAINT apps_org_id_key UNIQUE (organization_id, id)" in sql
    assert "ADD COLUMN IF NOT EXISTS organization_id UUID" in sql
    assert "gtd_" not in sql
    # No statement names an org as a value: the DEFAULT and RLS bind it.
    for name in dir(store):
        if name.endswith("_SQL"):
            stmt = getattr(store, name)
            assert ":org" not in stmt, name
            for part in stmt.split("INSERT INTO")[1:]:
                assert "organization_id" not in part.split(")", 1)[0], name


def test_the_store_holds_no_route_and_no_transaction() -> None:
    """The caller owns the transaction, so C1b can join several calls."""
    src = _STORE.read_text(encoding="utf-8")
    assert "APIRouter" not in src and "gateway.routes" not in src
    found: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and node.attr in (
                "commit", "rollback", "begin", "begin_nested"):
            found.append(node.attr)
        if isinstance(node, ast.Name) and node.id in (
                "tenant_session", "get_db", "get_session_factory"):
            found.append(node.id)
        if isinstance(node, ast.ImportFrom) and node.module and "db" in node.module.split("."):
            found.append(node.module)
    assert found == [], found


def test_r8_all_four_tables_have_enable_and_force_rls(world) -> None:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:t) ORDER BY relname"), {"t": list(_TABLES)}).all()
    assert [tuple(r) for r in rows] == [(t, True, True) for t in sorted(_TABLES)]


# ── The registry ───────────────────────────────────────────────────────────


def test_r8_define_and_read_an_object(world) -> None:
    async def go() -> tuple[store.StudioObject, store.StudioObject | None]:
        made = await _define(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            return made, await store.get_object(db, made.id)

    made, got = _go(world, go)
    assert got == made
    assert made.package_id == world.pkg_a and made.name == "visit"
    assert [f.key for f in made.fields] == [f["key"] for f in _VISIT]
    assert [f.key for f in made.fields if f.hot] == [
        "when", "status", "hours", "visit_day", "paid", "fee"]
    assert made.created_by == ANA


def test_r8_a_name_and_a_key_are_unique(world) -> None:
    async def go() -> None:
        made = await _define(world.a, world.pkg_a)
        with pytest.raises(store.ObjectNameTaken):
            await _define(world.a, world.pkg_a)
        # Another package of the org may hold the same name.
        await _define(world.a, world.pkg_a2)
        async with tenant_session(world.a) as db:
            with pytest.raises(store.FieldKeyTaken):
                await store.add_field(db, made.id, {"key": "notes", "type": "text"})

    _go(world, go)
    assert _admin_one(world, "SELECT count(*) FROM studio_objects WHERE "
                      "organization_id = CAST(:o AS uuid)", {"o": world.a}) == 2


def test_r8_a_package_of_another_org_is_not_found(world) -> None:
    async def go() -> store.StudioObject | None:
        async with tenant_session(world.a) as db:
            return await store.define_object(db, ANA, package_id=world.pkg_b, name="visit")

    assert _go(world, go) is None
    assert _admin_one(world, "SELECT count(*) FROM studio_objects WHERE "
                      "package_id = CAST(:p AS uuid)", {"p": world.pkg_b}) == 0


def test_r8_a_required_field_cannot_join_an_object_with_records(world) -> None:
    async def go() -> None:
        made = await _define(world.a, world.pkg_a, fields=[{"key": "n", "type": "number"}])
        async with tenant_session(world.a) as db:
            assert await store.add_field(db, made.id, {
                "key": "must", "type": "text", "required": True}) is not None
        await _create(world.a, made, {"must": "x"})
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError):
                await store.add_field(db, made.id, {
                    "key": "later", "type": "text", "required": True})
            assert await store.add_field(db, made.id, {"key": "later", "type": "text"})

    _go(world, go)


def test_r8_the_object_caps_hold(world) -> None:
    """50 objects for each package and 200 for each org. The rows are seeded as
    the superuser, then the store refuses the next one."""
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO studio_objects (organization_id, package_id, name, created_by) "
            "SELECT CAST(:o AS uuid), CAST(:p AS uuid), 'seed_' || g, :m "
            "FROM generate_series(1, 50) g"), {"o": world.a, "p": world.pkg_a, "m": ANA})

    async def define(pkg: str, name: str) -> Any:
        try:
            return await _define(world.a, pkg, name=name, fields=[])
        except store.BudgetExceeded as exc:
            return exc

    assert isinstance(_go(world, lambda: define(world.pkg_a, "one_more")), store.BudgetExceeded)
    assert isinstance(_go(world, lambda: define(world.pkg_a2, "other")), store.StudioObject)
    # 50 + 1 = 51 in the org. Seed 149 more in a third package, to 200. The
    # cap of a package is the store's, so the superuser may pass it.
    with world.p.admin_engine.begin() as c:
        pkg3 = str(c.execute(text(_PACKAGE_INSERT), {
            "s": f"stuc1-{TAG}-cap-{uuid.uuid4().hex[:6]}", "oe": ANA, "org": world.a,
            "manifest": _PACKAGE_MANIFEST}).scalar_one())
        c.execute(text(
            "INSERT INTO studio_objects (organization_id, package_id, name, created_by) "
            "SELECT CAST(:o AS uuid), CAST(:p AS uuid), 'seed_' || g, :m "
            "FROM generate_series(1, 149) g"), {"o": world.a, "p": pkg3, "m": ANA})
    assert _admin_one(world, "SELECT count(*) FROM studio_objects WHERE "
                      "organization_id = CAST(:o AS uuid)", {"o": world.a}) == 200
    got = _go(world, lambda: define(world.pkg_a2, "past_the_org"))
    assert isinstance(got, store.BudgetExceeded) and "200" in str(got)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE id = CAST(:p AS uuid)"), {"p": pkg3})


def test_r8_a_code_app_owns_no_object(world) -> None:
    """STU-B2a closes the gap of C1. `_PACKAGE_SQL` needs `format = 'manifest'`,
    so an App Workshop app of the org, also of the same member, owns no object.
    Both forms of a code app count: `format = 'code'` and a NULL format."""
    run = uuid.uuid4().hex[:6]
    apps: list[str] = []
    with world.p.admin_engine.begin() as c:
        for i, fmt in enumerate(("code", None)):
            apps.append(str(c.execute(text(
                "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
                "format) VALUES (:s, 'stuc1', :oe, '/tmp/stuc1', CAST(:o AS uuid), :f) "
                "RETURNING id"),
                {"s": f"stuc1-{TAG}-code-{run}-{i}", "oe": ANA, "o": world.a,
                 "f": fmt}).scalar_one()))

    async def go() -> list[Any]:
        out = []
        for app in apps:
            async with tenant_session(world.a) as db:
                out.append(await store.define_object(db, ANA, package_id=app, name="visit",
                                                     fields=[]))
        return out

    try:
        assert _go(world, go) == [None, None]
        assert _admin_one(world, "SELECT count(*) FROM studio_objects WHERE package_id IN "
                          "(CAST(:a AS uuid), CAST(:b AS uuid))",
                          {"a": apps[0], "b": apps[1]}) == 0
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM apps WHERE slug LIKE :t"),
                      {"t": f"stuc1-{TAG}-code-{run}-%"})


def test_r8_get_object_by_name_reads_one_package_only(world) -> None:
    """The door reads an object by the name in its manifest. Two packages of
    one org may hold one name, and each read answers its own package."""
    async def go() -> dict[str, Any]:
        mine = await _define(world.a, world.pkg_a, fields=[])
        other = await _define(world.a, world.pkg_a2, fields=[])
        async with tenant_session(world.a) as db:
            got = await store.get_object_by_name(db, "visit", package_id=world.pkg_a)
            got2 = await store.get_object_by_name(db, "visit", package_id=world.pkg_a2)
            none = await store.get_object_by_name(db, "claim", package_id=world.pkg_a)
        async with tenant_session(world.b) as db:
            org_b = await store.get_object_by_name(db, "visit", package_id=world.pkg_a)
        return {"mine": mine, "other": other, "got": got, "got2": got2, "none": none,
                "org_b": org_b}

    out = _go(world, go)
    assert out["got"] is not None and out["got"].id == out["mine"].id
    assert out["got2"] is not None and out["got2"].id == out["other"].id
    assert out["none"] is None and out["org_b"] is None


def test_r8_sixty_fields_is_the_cap(world) -> None:
    async def go() -> None:
        with pytest.raises(store.BudgetExceeded):
            await _define(world.a, world.pkg_a, fields=_number_fields(61, 0))
        made = await _define(world.a, world.pkg_a, fields=_number_fields(60, 0))
        async with tenant_session(world.a) as db:
            with pytest.raises(store.BudgetExceeded):
                await store.add_field(db, made.id, {"key": "one_more", "type": "number"})

    _go(world, go)


# ── b. Two orgs ────────────────────────────────────────────────────────────


def test_r8_org_b_reads_and_writes_nothing_of_org_a(world) -> None:
    a, b = world.a, world.b

    async def go() -> dict[str, Any]:
        made = await _define(a, world.pkg_a)
        rec = await _create(a, made, _RECORD)
        out: dict[str, Any] = {"object": made, "record": rec}
        async with tenant_session(b) as db:
            out["get_object"] = await store.get_object(db, made.id)
            pkg = made.package_id
            out["get_record"] = await store.get_record(db, made.id, rec.id, package_id=pkg)
            out["create"] = await store.create_record(db, ANA, made.id, _RECORD, package_id=pkg)
            out["update"] = await store.update_record(db, ANA, made.id, rec.id, {"hours": 9},
                                                      package_id=pkg)
            out["delete"] = await store.delete_record(db, made.id, rec.id, package_id=pkg)
            out["add_field"] = await store.add_field(db, made.id, {"key": "x", "type": "text"})
            out["hot"] = await store.set_field_hot(db, made.id, "notes", True)
            out["drop"] = await store.drop_object(db, made.id)
            out["define"] = await store.define_object(
                db, ANA, package_id=world.pkg_a, name="stolen")
            out["raw"] = {t: (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()
                          for t in _TABLES}
        return out

    out = _go(world, go)
    for key in ("get_object", "get_record", "create", "update", "add_field", "define"):
        assert out[key] is None, key
    assert out["delete"] is False and out["hot"] is False and out["drop"] is False
    assert out["raw"] == dict.fromkeys(_TABLES, 0)
    # Org A's rows did not move.
    with world.p.admin_engine.connect() as c:
        data = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                         {"r": out["record"].id}).scalar_one()
        fields = c.execute(text(
            "SELECT count(*) FROM studio_object_fields WHERE object_id = CAST(:o AS uuid)"),
            {"o": out["object"].id}).scalar_one()
    assert data["hours"] == 2.5 and fields == len(_VISIT)


@pytest.mark.parametrize("case", ["object", "field", "record", "hot_record", "hot_field",
                                  "hot_other_object"])
def test_r8_a_foreign_key_cannot_cross_an_org(world, case: str) -> None:
    """A foreign key check skips RLS, so the pair (org, parent) is the fence.
    Each raw INSERT runs as the app role, bound to org A, and names a parent
    row that org A does not hold."""
    async def seed() -> dict[str, Any]:
        mine = await _define(world.a, world.pkg_a, fields=_number_fields(2, 1))
        other = await _define(world.a, world.pkg_a2, fields=_number_fields(2, 1))
        theirs = await _define(world.b, world.pkg_b, fields=_number_fields(2, 1))
        return {
            "mine": mine, "other": other, "theirs": theirs,
            "my_rec": await _create(world.a, mine, {"n0": 1}),
            "their_rec": await _create(world.b, theirs, {"n0": 1}),
        }

    s = _go(world, seed)
    sql, params, constraint = {
        "object": (
            "INSERT INTO studio_objects (package_id, name, created_by) "
            "VALUES (CAST(:p AS uuid), 'x', 'a@x.test')",
            {"p": world.pkg_b}, "studio_objects_package_fk"),
        "field": (
            "INSERT INTO studio_object_fields (object_id, key, type) "
            "VALUES (CAST(:o AS uuid), 'x', 'text')",
            {"o": s["theirs"].id}, "studio_object_fields_object_fk"),
        "record": (
            "INSERT INTO studio_records (object_id, data, created_by, updated_by) "
            "VALUES (CAST(:o AS uuid), '{}', 'a@x.test', 'a@x.test')",
            {"o": s["theirs"].id}, "studio_records_object_fk"),
        "hot_record": (
            "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_num) "
            "VALUES (CAST(:o AS uuid), CAST(:r AS uuid), 'n1', 1)",
            {"o": s["mine"].id, "r": s["their_rec"].id}, "studio_record_hot_record_fk"),
        "hot_field": (
            "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_num) "
            "VALUES (CAST(:o AS uuid), CAST(:r AS uuid), 'no_such_field', 1)",
            {"o": s["mine"].id, "r": s["my_rec"].id}, "studio_record_hot_field_fk"),
        "hot_other_object": (
            "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_num) "
            "VALUES (CAST(:o AS uuid), CAST(:r AS uuid), 'n1', 1)",
            {"o": s["other"].id, "r": s["my_rec"].id}, "studio_record_hot_record_fk"),
    }[case]
    err = _refused(world, world.a, sql, params)
    assert constraint in err, err


# ── c. Each write is checked ───────────────────────────────────────────────


def test_r8_a_bad_value_is_refused_with_its_key_and_writes_nothing(world) -> None:
    async def go() -> dict[str, Any]:
        made = await _define(world.a, world.pkg_a)
        rec = await _create(world.a, made, _RECORD)
        out: dict[str, Any] = {"made": made, "rec": rec, "errors": []}
        async with tenant_session(world.a) as db:
            for bad in ({"hours": True}, {"status": "cancelled"}, {"engineer": "Ben@x.test"},
                        {"fee": {"minor": 5, "currency": "inr"}}, {"site": {"lat": 91, "lng": 0}},
                        {"colour": "red"}, {"customer": None}):
                with pytest.raises(FieldError) as refused:
                    await store.create_record(db, ANA, made.id, {**_RECORD, **bad},
                                              package_id=made.package_id)
                out["errors"].append(refused.value.field)
                with pytest.raises(FieldError):
                    await store.update_record(db, ANA, made.id, rec.id, bad,
                                              package_id=made.package_id)
            with pytest.raises(RecordError):
                await store.create_record(db, ANA, made.id, ["not", "an", "object"],
                                          package_id=made.package_id)
            # A failed statement would end the transaction, and this read would raise.
            out["after"] = await store.get_record(db, made.id, rec.id,
                                                  package_id=made.package_id)
        return out

    out = _go(world, go)
    assert out["errors"] == ["hours", "status", "engineer", "fee", "site", "colour", "customer"]
    assert out["after"] == out["rec"]
    assert _admin_one(world, "SELECT count(*) FROM studio_records WHERE object_id = "
                      "CAST(:o AS uuid)", {"o": out["made"].id}) == 1


def test_r8_a_record_round_trips_and_an_update_merges(world) -> None:
    async def go() -> tuple[store.Record, store.Record | None, store.Record | None]:
        made = await _define(world.a, world.pkg_a)
        rec = await _create(world.a, made, _RECORD)
        async with tenant_session(world.a) as db:
            changed = await store.update_record(db, f"Ben.{TAG}@STUC1.test", made.id, rec.id,
                                                {"status": "done", "notes": None},
                                                package_id=made.package_id)
            got = await store.get_record(db, made.id, rec.id, package_id=made.package_id)
        return rec, changed, got

    rec, changed, got = _go(world, go)
    assert rec.data["when"] == "2026-11-01T04:00:00Z"
    assert rec.data["customer"] == {"id": "acct-1"}
    assert changed is not None and got == changed
    assert changed.data["status"] == "done" and "notes" not in changed.data
    assert changed.data["hours"] == 2.5
    assert changed.updated_by == f"ben.{TAG}@stuc1.test" and changed.created_by == ANA


def test_r8_a_record_call_names_its_package_and_its_object(world) -> None:
    """A door of package A2 cannot reach a record of package A in the same
    org. It may pass the true object id of A, but with its own package id the
    record calls read, write and delete nothing. Another object of the right
    package reaches nothing either."""
    async def go() -> dict[str, Any]:
        mine = await _define(world.a, world.pkg_a, fields=_number_fields(1, 0))
        other = await _define(world.a, world.pkg_a2, fields=_number_fields(1, 0))
        rec = await _create(world.a, mine, {"n0": 1})
        out: dict[str, Any] = {"rec": rec}
        async with tenant_session(world.a) as db:
            for name, oid, pid in (("wrong_package", mine.id, world.pkg_a2),
                                   ("wrong_object", other.id, world.pkg_a2)):
                out[name] = (
                    await store.get_record(db, oid, rec.id, package_id=pid),
                    await store.update_record(db, ANA, oid, rec.id, {"n0": 2}, package_id=pid),
                    await store.delete_record(db, oid, rec.id, package_id=pid),
                    await store.create_record(db, ANA, oid, {"n0": 3}, package_id=pid)
                    if oid == mine.id else None,
                )
            out["right"] = await store.get_record(db, mine.id, rec.id, package_id=world.pkg_a)
        return out

    out = _go(world, go)
    assert out["wrong_package"] == (None, None, False, None)
    assert out["wrong_object"] == (None, None, False, None)
    assert out["right"] == out["rec"] and out["right"].data == {"n0": 1}
    assert _admin_one(world, "SELECT count(*) FROM studio_records WHERE organization_id = "
                      "CAST(:o AS uuid)", {"o": world.a}) == 1


# ── d. The hot rows ────────────────────────────────────────────────────────


def test_r8_a_hot_field_fills_its_row_on_each_write(world) -> None:
    long_text = "t" * 300

    async def go() -> dict[str, Any]:
        made = await _define(world.a, world.pkg_a, fields=[
            *_VISIT, {"key": "title", "type": "text", "hot": True}])
        rec = await _create(world.a, made, {**_RECORD, "title": long_text})
        out: dict[str, Any] = {"rec": rec, "created": _hot_rows(world, rec.id)}
        async with tenant_session(world.a) as db:
            await store.update_record(db, ANA, made.id, rec.id,
                                      {"hours": 4, "status": None, "paid": True},
                                      package_id=made.package_id)
        out["updated"] = _hot_rows(world, rec.id)
        async with tenant_session(world.a) as db:
            assert await store.delete_record(db, made.id, rec.id, package_id=made.package_id)
        out["deleted"] = _hot_rows(world, rec.id)
        return out

    out = _go(world, go)
    created = out["created"]
    assert created == {
        "when": (None, None, datetime(2026, 11, 1, 4, 0, tzinfo=UTC), None),
        "status": ("planned", None, None, None),
        "hours": (None, Decimal("2.5"), None, None),
        "visit_day": (None, None, datetime(2026, 11, 1, tzinfo=UTC), None),
        "paid": (None, None, None, False),
        "fee": (None, Decimal(125000), None, None),
        "title": ("t" * sf.HOT_TEXT_CHARS, None, None, None),
    }
    updated = out["updated"]
    assert "status" not in updated
    assert updated["hours"] == (None, Decimal(4), None, None)
    assert updated["paid"] == (None, None, None, True)
    assert set(updated) == set(created) - {"status"}
    assert out["deleted"] == {}


def test_r8_a_field_that_turns_hot_backfills_and_one_that_turns_cold_empties(
        world, monkeypatch) -> None:
    """The backfill reads in pages. Three to a page, ten records: four pages."""
    monkeypatch.setattr(store, "BACKFILL_PAGE", 3)

    async def go() -> dict[str, Any]:
        made = await _define(world.a, world.pkg_a, fields=[
            {"key": "label", "type": "text"}, {"key": "n", "type": "number"}])
        recs = [await _create(world.a, made, {"label": f"L{i}", **({"n": i} if i % 2 else {})})
                for i in range(10)]
        out: dict[str, Any] = {"made": made, "recs": recs}
        async with tenant_session(world.a) as db:
            out["label"] = await store.set_field_hot(db, made.id, "label", True)
            out["n"] = await store.set_field_hot(db, made.id, "n", True)
            out["again"] = await store.set_field_hot(db, made.id, "n", True)
            out["missing"] = await store.set_field_hot(db, made.id, "nope", True)
        out["hot"] = [_hot_rows(world, r.id) for r in recs]
        async with tenant_session(world.a) as db:
            out["cold"] = await store.set_field_hot(db, made.id, "label", False)
            out["object"] = await store.get_object(db, made.id)
        out["after"] = [_hot_rows(world, r.id) for r in recs]
        return out

    out = _go(world, go)
    assert (out["label"], out["n"], out["again"], out["missing"]) == (True, True, True, False)
    for i, rows in enumerate(out["hot"]):
        assert rows["label"] == (f"L{i}", None, None, None)
        assert rows.get("n") == ((None, Decimal(i), None, None) if i % 2 else None)
    assert all("label" not in rows for rows in out["after"])
    assert sum(len(rows) for rows in out["after"]) == 5
    assert [f.key for f in out["object"].fields if f.hot] == ["n"]


def test_r8_a_long_text_field_cannot_turn_hot(world) -> None:
    async def go() -> None:
        made = await _define(world.a, world.pkg_a, fields=[{"key": "body", "type": "longText"}])
        async with tenant_session(world.a) as db:
            with pytest.raises(FieldError):
                await store.set_field_hot(db, made.id, "body", True)

    _go(world, go)
    err = _refused(world, world.a,
                   "UPDATE studio_object_fields SET hot = true WHERE key = 'body'", {})
    assert "studio_object_fields_hot_type" in err


def test_r8_a_record_write_waits_for_a_field_that_turns_hot(world) -> None:
    """The lock order: a field that turns hot locks the object row, and a
    record write waits for it, then reads the field as hot. So the new record
    gets its hot row, and the side table never misses one."""
    hold = 0.6

    async def go() -> tuple[str, float]:
        made = await _define(world.a, world.pkg_a, fields=[{"key": "n", "type": "number"}])
        locked = asyncio.Event()

        async def turn_hot() -> None:
            async with tenant_session(world.a) as db:
                assert await store.set_field_hot(db, made.id, "n", True)
                locked.set()
                await asyncio.sleep(hold)

        async def write() -> tuple[str, float]:
            await locked.wait()
            started = time.monotonic()
            rec = await _create(world.a, made, {"n": 7})
            return rec.id, time.monotonic() - started

        _, (rid, waited) = await asyncio.gather(turn_hot(), write())
        return rid, waited

    rid, waited = _go(world, go)
    assert waited >= hold * 0.5, waited
    assert _hot_rows(world, rid) == {"n": (None, Decimal(7), None, None)}


def test_r8_a_field_that_turns_hot_waits_for_an_open_record_write(world) -> None:
    hold = 0.6

    async def go() -> tuple[str, float]:
        made = await _define(world.a, world.pkg_a, fields=[{"key": "n", "type": "number"}])
        locked = asyncio.Event()

        async def write() -> str:
            async with tenant_session(world.a) as db:
                rec = await store.create_record(db, ANA, made.id, {"n": 8},
                                                package_id=made.package_id)
                locked.set()
                await asyncio.sleep(hold)
            assert rec is not None
            return rec.id

        async def turn_hot() -> float:
            await locked.wait()
            started = time.monotonic()
            async with tenant_session(world.a) as db:
                assert await store.set_field_hot(db, made.id, "n", True)
            return time.monotonic() - started

        rid, waited = await asyncio.gather(write(), turn_hot())
        return rid, waited

    rid, waited = _go(world, go)
    assert waited >= hold * 0.5, waited
    assert _hot_rows(world, rid) == {"n": (None, Decimal(8), None, None)}


# ── e. The budget ──────────────────────────────────────────────────────────


def test_r8_the_ninth_hot_field_of_an_object_is_refused(world) -> None:
    async def go() -> store.StudioObject:
        with pytest.raises(store.BudgetExceeded):
            await _define(world.a, world.pkg_a, fields=_number_fields(9, 9))
        made = await _define(world.a, world.pkg_a, fields=_number_fields(9, 8))
        async with tenant_session(world.a) as db:
            with pytest.raises(store.BudgetExceeded) as add:
                await store.add_field(db, made.id, {"key": "x", "type": "number", "hot": True})
            assert str(sf.MAX_HOT_PER_OBJECT) in str(add.value)
            with pytest.raises(store.BudgetExceeded):
                await store.set_field_hot(db, made.id, "n8", True)
        return made

    made = _go(world, go)
    assert _org_hot(world, world.a) == 8
    # The trigger refuses on its own, not only the store.
    err = _refused(world, world.a, _RAW_FIELD, {"oid": made.id, "key": "raw"})
    assert "an object holds at most 8 hot fields" in err
    assert _org_hot(world, world.a) == 8


async def _fill_org_to(org: str, pkg: str, total: int) -> list[store.StudioObject]:
    """Objects of 8 hot fields, then one with the rest, to *total* in the org."""
    made, left, i = [], total, 0
    while left > 0:
        hot = min(left, sf.MAX_HOT_PER_OBJECT)
        made.append(await _define(org, pkg, name=f"full_{i}",
                                  fields=_number_fields(sf.MAX_HOT_PER_OBJECT, hot)))
        left, i = left - hot, i + 1
    return made


def test_r8_the_twenty_fifth_hot_field_of_an_org_is_refused(world) -> None:
    async def go() -> store.StudioObject:
        await _fill_org_to(world.a, world.pkg_a, sf.MAX_HOT_PER_ORG)
        roomy = await _define(world.a, world.pkg_a2, fields=_number_fields(2, 0))
        async with tenant_session(world.a) as db:
            with pytest.raises(store.BudgetExceeded) as add:
                await store.add_field(db, roomy.id, {"key": "x", "type": "number", "hot": True})
            assert str(sf.MAX_HOT_PER_ORG) in str(add.value)
            with pytest.raises(store.BudgetExceeded):
                await store.set_field_hot(db, roomy.id, "n0", True)
        with pytest.raises(store.BudgetExceeded):
            await _define(world.a, world.pkg_a2, name="late", fields=_number_fields(1, 1))
        # The budget is for each org: org B has room.
        await _define(world.b, world.pkg_b, fields=_number_fields(3, 3))
        return roomy

    roomy = _go(world, go)
    assert _org_hot(world, world.a) == sf.MAX_HOT_PER_ORG
    assert _org_hot(world, world.b) == 3
    err = _refused(world, world.a, _RAW_FIELD, {"oid": roomy.id, "key": "raw"})
    assert "an organization holds at most 24 hot fields" in err
    assert _org_hot(world, world.a) == sf.MAX_HOT_PER_ORG


def _race(world: Any, first: Any, second: Any, hold: float = 0.6) -> tuple[Any, Any, float]:
    """Run two writers on two connections. The first holds its transaction
    open for *hold* seconds after its write. The second starts then."""
    async def go() -> tuple[Any, Any, float]:
        objects = await _fill_org_to(world.a, world.pkg_a, sf.MAX_HOT_PER_ORG - 1)
        one = await _define(world.a, world.pkg_a2, name="one", fields=_number_fields(1, 0))
        two = await _define(world.a, world.pkg_a2, name="two", fields=_number_fields(1, 0))
        assert len(objects) == 3
        locked = asyncio.Event()

        async def a() -> Any:
            async with tenant_session(world.a) as db:
                got = await first(db, one.id)
                locked.set()
                await asyncio.sleep(hold)
            return got

        async def b() -> tuple[Any, float]:
            await locked.wait()
            started = time.monotonic()
            try:
                async with tenant_session(world.a) as db:
                    got: Any = await second(db, two.id)
            except Exception as exc:  # the refusal is the result under test
                got = exc
            return got, time.monotonic() - started

        r1, (r2, waited) = await asyncio.gather(a(), b())
        return r1, r2, waited

    return _go(world, go)


def test_r8_two_writers_at_once_get_one_hot_field(world) -> None:
    """Two connections, 23 hot fields in the org. The first writer holds the
    advisory lock of the org. The second waits for its commit, counts 24, and
    gets the store's clean refusal, never a DBAPIError."""
    async def add(db: Any, oid: str) -> Any:
        return await store.add_field(db, oid, {"key": "h", "type": "number", "hot": True})

    r1, r2, waited = _race(world, add, add)
    assert isinstance(r1, sf.FieldSpec)
    assert isinstance(r2, store.BudgetExceeded), r2
    assert waited >= 0.3, waited
    assert _org_hot(world, world.a) == sf.MAX_HOT_PER_ORG


def test_r8_two_raw_writers_at_once_the_trigger_refuses_the_25th(world) -> None:
    """The same race with no store: two raw INSERTs as the app role. The
    trigger takes the lock and counts again, so the second is refused."""
    async def raw(db: Any, oid: str) -> Any:
        await db.execute(text(_RAW_FIELD), {"oid": oid, "key": "h"})
        return True

    r1, r2, _ = _race(world, raw, raw)
    assert r1 is True
    assert isinstance(r2, DBAPIError) and "studio_object_fields_hot_budget" in str(r2), r2
    assert _org_hot(world, world.a) == sf.MAX_HOT_PER_ORG


# ── f. The size cap ────────────────────────────────────────────────────────


def _record_of_size(size: int) -> dict[str, Any]:
    data = {f"t{i}": "" for i in range(4)}
    pad = size - len(sf.record_json(data).encode("utf-8"))
    for i in range(4):
        take = min(pad, sf.MAX_LONG_TEXT_CHARS)
        data[f"t{i}"] = "€" * (take // 3) + "y" * (take % 3)
        pad -= take
    return data


def test_r8_an_oversize_record_is_refused(world) -> None:
    """The store measures its JSON text, and the CHECK measures the jsonb
    text. A record of exactly 64 KiB is stored at that length. One byte more,
    and the store refuses, and so does the CHECK on its own."""
    fields = [{"key": f"t{i}", "type": "longText"} for i in range(4)]
    exact = _record_of_size(sf.MAX_RECORD_BYTES)
    assert len(sf.record_json(exact).encode("utf-8")) == sf.MAX_RECORD_BYTES
    over = {**exact, "t3": exact["t3"] + "y"}

    async def go() -> tuple[store.StudioObject, store.Record]:
        made = await _define(world.a, world.pkg_a, fields=fields)
        rec = await _create(world.a, made, exact)
        async with tenant_session(world.a) as db:
            with pytest.raises(RecordError):
                await store.create_record(db, ANA, made.id, over, package_id=made.package_id)
            assert await store.get_record(db, made.id, rec.id,
                                          package_id=made.package_id) == rec
        return made, rec

    made, rec = _go(world, go)
    assert _admin_one(world, "SELECT octet_length(data::text) FROM studio_records "
                      "WHERE id = CAST(:r AS uuid)", {"r": rec.id}) == sf.MAX_RECORD_BYTES
    err = _refused(world, world.a,
                   "INSERT INTO studio_records (object_id, data, created_by, updated_by) "
                   "VALUES (CAST(:o AS uuid), CAST(:d AS jsonb), 'a@x.test', 'a@x.test')",
                   {"o": made.id, "d": json.dumps(over, ensure_ascii=False)})
    assert "studio_records_data_size" in err
    assert _admin_one(world, "SELECT count(*) FROM studio_records WHERE object_id = "
                      "CAST(:o AS uuid)", {"o": made.id}) == 1


# ── g. The backfill cap ────────────────────────────────────────────────────


def test_r8_the_backfill_refuses_past_50000_records(world) -> None:
    """At 50,000 records a field turns hot, and its rows fill. At 50,001 the
    store refuses, and the field stays cold."""
    made = _go(world, lambda: _define(world.a, world.pkg_a, fields=[
        {"key": "n", "type": "number"}, {"key": "m", "type": "number"}]))

    def seed(count: int, with_value: int) -> None:
        with world.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO studio_records (organization_id, object_id, data, created_by, "
                "updated_by) SELECT CAST(:org AS uuid), CAST(:o AS uuid), "
                "CASE WHEN g <= :v THEN jsonb_build_object('n', g) ELSE '{}'::jsonb END, :m, :m "
                "FROM generate_series(1, :c) g"),
                {"org": world.a, "o": made.id, "v": with_value, "c": count, "m": ANA})

    seed(sf.MAX_HOT_BACKFILL, 10)

    async def hot(key: str) -> Any:
        try:
            async with tenant_session(world.a) as db:
                return await store.set_field_hot(db, made.id, key, True)
        except store.BudgetExceeded as exc:
            return exc

    assert _go(world, lambda: hot("n")) is True
    with world.p.admin_engine.connect() as c:
        values = sorted(r[0] for r in c.execute(text(
            "SELECT v_num FROM studio_record_hot WHERE object_id = CAST(:o AS uuid) "
            "AND field_key = 'n'"), {"o": made.id}))
    assert values == [Decimal(i) for i in range(1, 11)]
    seed(1, 0)
    refused = _go(world, lambda: hot("m"))
    assert isinstance(refused, store.BudgetExceeded) and "50000" in str(refused)
    assert _admin_one(world, "SELECT hot FROM studio_object_fields WHERE object_id = "
                      "CAST(:o AS uuid) AND key = 'm'", {"o": made.id}) is False


# ── h. The cascades ────────────────────────────────────────────────────────


def _counts(world: Any, org: str) -> dict[str, int]:
    with world.p.admin_engine.connect() as c:
        return {t: int(c.execute(text(
            f"SELECT count(*) FROM {t} WHERE organization_id = CAST(:o AS uuid)"),
            {"o": org}).scalar_one()) for t in _TABLES}


def test_r8_a_dropped_object_and_a_deleted_package_leave_nothing(world) -> None:
    async def go() -> tuple[str, str]:
        one = await _define(world.a, world.pkg_a)
        two = await _define(world.a, world.pkg_a2)
        await _create(world.a, one, _RECORD)
        await _create(world.a, two, _RECORD)
        async with tenant_session(world.a) as db:
            assert await store.drop_object(db, one.id) is True
            assert await store.drop_object(db, one.id) is False
        return one.id, two.id

    _, two = _go(world, go)
    counts = _counts(world, world.a)
    assert counts == {"studio_objects": 1, "studio_object_fields": len(_VISIT),
                      "studio_records": 1, "studio_record_hot": 6}
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE id = CAST(:p AS uuid)"), {"p": world.pkg_a2})
    assert _counts(world, world.a) == dict.fromkeys(_TABLES, 0)
    assert two


# ── Fix round 1: the lock order and an older stored spec ───────────────────


def test_r8_joined_calls_in_two_sessions_never_deadlock(world) -> None:
    """The verifier's interleaving, on two real connections. Session one writes
    a record and holds its transaction. Session two then adds a field. Session
    one then adds a field in the same transaction. Every write takes the org
    lock first, so session two waits for session one, and both complete. With
    the old order, session one held the object row and session two held the
    org lock, and Postgres reported a deadlock."""
    hold = 0.6

    async def go() -> dict[str, Any]:
        made = await _define(world.a, world.pkg_a, fields=[{"key": "n", "type": "number"}])
        started = asyncio.Event()

        async def first() -> Any:
            async with tenant_session(world.a) as db:
                rec = await store.create_record(db, ANA, made.id, {"n": 1},
                                                package_id=made.package_id)
                started.set()
                await asyncio.sleep(hold)
                added = await store.add_field(db, made.id, {"key": "one", "type": "text"})
            return rec, added

        async def second() -> Any:
            await started.wait()
            begun = time.monotonic()
            async with tenant_session(world.a) as db:
                added = await store.add_field(db, made.id, {"key": "two", "type": "text"})
            return added, time.monotonic() - begun

        (rec, one), (two, waited) = await asyncio.wait_for(
            asyncio.gather(first(), second()), timeout=30)
        async with tenant_session(world.a) as db:
            after = await store.get_object(db, made.id)
        return {"rec": rec, "one": one, "two": two, "waited": waited, "after": after}

    out = _go(world, go)
    assert out["rec"] is not None and out["one"] is not None and out["two"] is not None
    assert out["waited"] >= 0.3, out["waited"]
    assert [f.key for f in out["after"].fields] == ["n", "one", "two"]


def test_r8_an_older_stored_spec_still_reads_and_writes(world) -> None:
    """A rule that a later slice adds binds a new spec only. Here the stored
    options differ only by case, which `parse_field_spec` now refuses. The row
    goes in by SQL, as an older store wrote it. Reads and writes still work."""
    made = _go(world, lambda: _define(world.a, world.pkg_a, fields=[
        {"key": "n", "type": "number"}]))
    old_spec = {"options": ["Open", "open"], "required": False, "many": False}
    with pytest.raises(FieldError):
        sf.parse_field_spec({"key": "state", "type": "select", **old_spec})
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO studio_object_fields (organization_id, object_id, key, type, spec, "
            "position) VALUES (CAST(:org AS uuid), CAST(:o AS uuid), 'state', 'select', "
            "CAST(:s AS jsonb), 1)"), {"org": world.a, "o": made.id, "s": json.dumps(old_spec)})

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with tenant_session(world.a) as db:
            out["object"] = await store.get_object(db, made.id)
            rec = await store.create_record(db, ANA, made.id, {"n": 1, "state": "open"},
                                            package_id=made.package_id)
            out["rec"] = rec
            assert rec is not None
            out["got"] = await store.get_record(db, made.id, rec.id, package_id=made.package_id)
            out["deleted"] = await store.delete_record(db, made.id, rec.id,
                                                       package_id=made.package_id)
        return out

    out = _go(world, go)
    [state] = [f for f in out["object"].fields if f.key == "state"]
    assert state.options == ("Open", "open")
    assert out["rec"].data == {"n": 1, "state": "open"} and out["got"] == out["rec"]
    assert out["deleted"] is True
