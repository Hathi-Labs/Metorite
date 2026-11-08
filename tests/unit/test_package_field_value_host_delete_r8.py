"""WS-PKG PKG-14a-3 — a host delete takes its package field values (R8).

Spec: ``project-docs/specs/package_host.md`` §8.3, the PKG-14a-3 row of §10
and PKG-Q30. Migration: the file that creates the function
``package_field_value_host_delete`` (372 at build). The hermetic fence is
``tests/unit/test_package_field_value_host_delete.py``.

Each host delete runs as the app role ``acb_app_h3rls``, bound to one org,
so FORCE RLS binds each statement. Each seed and each count runs as the
superuser, which RLS does not bind. The ``promoted`` fixture builds the full
ladder and the four generated phases from an empty database, so it also
applies the migration of this slice.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_field_value_host_delete_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-14a-3).**

a. Each host table has one trigger: AFTER DELETE, FOR EACH ROW, on no other
   event, and it passes the kind of that table.
b. A host delete of each kind removes the values of that record. The values
   of other records and of org B stay, also org B's values with the same
   record id.
c. A project delete cascades to its tasks, and the values of the tasks go.
   The values of org B with the same task ids stay.
d. A delete as the superuser, which RLS does not bind, still takes no value
   of org B. The org filter of the function is the control.
e. A bulk delete of 50 records takes the 50 records' values.
f. A host delete with no values works.
g. An update of a host record keeps its values.
h. A session of org A can not delete a host record of org B, so it takes
   none of org B's values.
i. The migration applies twice, and each table still has one trigger.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.package_host.field_values import HOST_RECORD_KINDS
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg14a3.test"
_FUNCTION = "package_field_value_host_delete"
_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

#: The host table of each record kind (§8.3).
HOST_TABLES: dict[str, str] = {
    "task": "pm_tasks",
    "crm.lead": "crm_leads",
    "crm.contact": "crm_contacts",
    "crm.company": "crm_companies",
    "crm.deal": "crm_deals",
}


def _migration() -> Path:
    """The migration of this slice, found by CONTENT, never by number."""
    hits = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
            if re.search(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+{_FUNCTION}\s*\(",
                         p.read_text(encoding="utf-8"))]
    assert len(hits) == 1, hits
    return hits[0]


# ── The world ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """For each org: one app, one org install, one project with a status,
    and the CRM statuses. Teardown drops the database with ``promoted``."""
    p = promoted
    orgs: dict[str, SimpleNamespace] = {}
    with p.admin_engine.begin() as c:
        for tag, org in (("a", p.org_a), ("b", p.org_b)):
            o = {"o": org}
            man = json.dumps({"studio": 1, "id": f"h-{tag}-{TAG}", "name": "P", "purpose": "T"})
            app = str(c.execute(text(
                "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
                "format, status, manifest) VALUES (:s, 'pkg14a3', :e, '', CAST(:o AS uuid), "
                "'manifest', 'live', CAST(:m AS jsonb)) RETURNING id"),
                {**o, "s": f"pkg14a3-{TAG}-{tag}", "e": ANA, "m": man}).scalar_one())
            install = str(c.execute(text(
                "INSERT INTO package_install (organization_id, app_id, version, scope_kind, "
                "scope_subject, installed_by) VALUES (CAST(:o AS uuid), CAST(:a AS uuid), 1, "
                "'org', 'org', :e) RETURNING id"), {**o, "a": app, "e": ANA}).scalar_one())
            lead_status = str(c.execute(text(
                "INSERT INTO crm_lead_statuses (organization_id, name, position, type) "
                "VALUES (CAST(:o AS uuid), 'New', 0, 'open') RETURNING id"), o).scalar_one())
            deal_status = str(c.execute(text(
                "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
                "VALUES (CAST(:o AS uuid), 'Qualify', 0, 'open') RETURNING id"), o).scalar_one())
            orgs[tag] = SimpleNamespace(org=org, install=install, lead_status=lead_status,
                                        deal_status=deal_status)
    return SimpleNamespace(p=p, orgs=orgs)


_TASK_NUMBER = iter(range(1, 1_000_000))


def _project(world: Any, tag: str) -> tuple[str, str]:
    """A new project of org *tag* with one status. Answers (project, status)."""
    project, status = str(uuid.uuid4()), str(uuid.uuid4())
    org = world.orgs[tag].org
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
            "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, 'manual', :e, "
            "true)"), {"p": project, "o": org, "n": f"pkg14a3-{TAG}", "e": ANA})
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
            "is_default, organization_id) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), "
            "'To do', 1, 'todo', true, CAST(:o AS uuid))"),
            {"s": status, "p": project, "o": org})
    return project, status


def _record(world: Any, tag: str, kind: str, *, rid: str | None = None,
             project: tuple[str, str] | None = None) -> str:
    """One new host record of *kind* in org *tag*. Answers its id."""
    rid = rid or str(uuid.uuid4())
    o = world.orgs[tag]
    params = {"id": rid, "o": o.org, "n": f"pkg14a3 {TAG}"}
    sql = {
        "crm.lead": ("INSERT INTO crm_leads (id, organization_id, lead_name, status_id) "
                     "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), :n, CAST(:s AS uuid))"),
        "crm.contact": ("INSERT INTO crm_contacts (id, organization_id, first_name) "
                        "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), :n)"),
        "crm.company": ("INSERT INTO crm_companies (id, organization_id, name) "
                        "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), :n)"),
        "crm.deal": ("INSERT INTO crm_deals (id, organization_id, name, status_id) "
                     "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), :n, CAST(:s AS uuid))"),
        "task": ("INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
                 "status_id, title, source, created_by, task_number) VALUES "
                 "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                 "CAST(:s AS uuid), :n, 'manual', :e, :num)"),
    }[kind]
    if kind == "crm.lead":
        params["s"] = o.lead_status
    elif kind == "crm.deal":
        params["s"] = o.deal_status
    elif kind == "task":
        proj, status = project or _project(world, tag)
        params.update({"p": proj, "s": status, "e": ANA, "num": next(_TASK_NUMBER)})
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params)
    return rid


def _put(world: Any, tag: str, kind: str, rid: str, key: str = "warranty") -> None:
    """One value on the record, written as the superuser with its org."""
    o = world.orgs[tag]
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO package_field_value (organization_id, install_id, record_kind, "
            "record_id, field_key, field_type, value) VALUES (CAST(:o AS uuid), "
            "CAST(:i AS uuid), :k, CAST(:r AS uuid), :f, 'text', CAST(:v AS jsonb))"),
            {"o": o.org, "i": o.install, "k": kind, "r": rid, "f": key, "v": json.dumps("x")})


def _count(world: Any, tag: str, kind: str, rids: list[str] | str) -> int:
    """The values of the records, counted as the superuser."""
    ids = [rids] if isinstance(rids, str) else rids
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM package_field_value WHERE organization_id = CAST(:o AS uuid) "
            "AND record_kind = :k AND record_id = ANY(CAST(:r AS uuid[]))"),
            {"o": world.orgs[tag].org, "k": kind, "r": ids}).scalar_one())


def _as_app(engine: Any, org: str, sql: str, params: dict[str, Any]) -> int:
    """Run one statement as the app role, bound to *org*. Answers the rowcount."""
    with engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        return int(c.execute(text(sql), params).rowcount)


def _delete_as_app(engine: Any, world: Any, tag: str, kind: str, rids: list[str]) -> int:
    return _as_app(engine, world.orgs[tag].org,
                   f"DELETE FROM {HOST_TABLES[kind]} WHERE id = ANY(CAST(:r AS uuid[]))",
                   {"r": rids})


# ── a. The triggers ─────────────────────────────────────────────────────────


def _triggers(world: Any, table: str) -> list[tuple[str, int, bytes]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT t.tgname, t.tgtype, t.tgargs FROM pg_trigger AS t "
            "JOIN pg_proc AS p ON p.oid = t.tgfoid "
            "WHERE t.tgrelid = to_regclass(:t) AND p.proname = :f AND NOT t.tgisinternal"),
            {"t": table, "f": _FUNCTION})]


def test_r8_each_host_table_has_one_after_delete_row_trigger(world) -> None:
    assert set(HOST_TABLES) == set(HOST_RECORD_KINDS)
    # tgtype bits: 1 ROW, 2 BEFORE, 4 INSERT, 8 DELETE, 16 UPDATE, 32 TRUNCATE.
    for kind, table in HOST_TABLES.items():
        found = _triggers(world, table)
        assert len(found) == 1, f"{table} has {len(found)} triggers of {_FUNCTION}"
        _, tgtype, tgargs = found[0]
        assert tgtype == 1 | 8, f"{table}: tgtype {tgtype} is not AFTER DELETE FOR EACH ROW"
        assert bytes(tgargs).split(b"\x00")[0].decode() == kind


# ── b. A host delete of each kind ──────────────────────────────────────────


@pytest.mark.parametrize("kind", HOST_RECORD_KINDS)
def test_r8_a_host_delete_takes_the_values_of_its_record(world, app_engine, kind) -> None:  # noqa: F811
    gone = _record(world, "a", kind)
    kept = _record(world, "a", kind)
    _put(world, "a", kind, gone, "one")
    _put(world, "a", kind, gone, "two")
    _put(world, "a", kind, kept)
    # Org B holds a value with the SAME kind and record id, and its own record.
    _put(world, "b", kind, gone)
    b_rec = _record(world, "b", kind)
    _put(world, "b", kind, b_rec)
    assert _count(world, "a", kind, gone) == 2

    assert _delete_as_app(app_engine, world, "a", kind, [gone]) == 1

    assert _count(world, "a", kind, gone) == 0
    assert _count(world, "a", kind, kept) == 1
    assert _count(world, "b", kind, gone) == 1
    assert _count(world, "b", kind, b_rec) == 1


# ── c. A foreign key cascade ───────────────────────────────────────────────


def test_r8_a_project_delete_takes_the_values_of_its_tasks(world, app_engine) -> None:  # noqa: F811
    proj = _project(world, "a")
    tasks = [_record(world, "a", "task", project=proj) for _ in range(3)]
    other = _record(world, "a", "task")
    for t in (*tasks, other):
        _put(world, "a", "task", t)
    for t in tasks:
        _put(world, "b", "task", t)
    assert _count(world, "a", "task", tasks) == 3

    assert _as_app(app_engine, world.orgs["a"].org,
                   "DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)", {"p": proj[0]}) == 1

    with world.p.admin_engine.connect() as c:
        left = c.execute(text("SELECT count(*) FROM pm_tasks WHERE id = ANY(CAST(:r AS uuid[]))"),
                         {"r": tasks}).scalar_one()
    assert left == 0, "the cascade must delete the tasks"
    assert _count(world, "a", "task", tasks) == 0
    assert _count(world, "a", "task", other) == 1
    assert _count(world, "b", "task", tasks) == 3


# ── d. A delete that RLS does not bind ─────────────────────────────────────


@pytest.mark.parametrize("kind", ("task", "crm.lead"))
def test_r8_a_superuser_delete_takes_no_value_of_another_org(world, kind) -> None:
    rid = _record(world, "a", kind)
    _put(world, "a", kind, rid)
    _put(world, "b", kind, rid)
    with world.p.admin_engine.begin() as c:
        c.execute(text(f"DELETE FROM {HOST_TABLES[kind]} WHERE id = CAST(:r AS uuid)"),
                  {"r": rid})
    assert _count(world, "a", kind, rid) == 0
    assert _count(world, "b", kind, rid) == 1, "the org filter of the function is the control"


# ── e. A bulk delete ───────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ("task", "crm.deal"))
def test_r8_a_bulk_delete_of_50_takes_each_value(world, app_engine, kind) -> None:  # noqa: F811
    proj = _project(world, "a") if kind == "task" else None
    rids = [_record(world, "a", kind, project=proj) for _ in range(50)]
    kept = _record(world, "a", kind, project=proj)
    for r in (*rids, kept):
        _put(world, "a", kind, r)
    assert _count(world, "a", kind, rids) == 50

    assert _delete_as_app(app_engine, world, "a", kind, rids) == 50

    assert _count(world, "a", kind, rids) == 0
    assert _count(world, "a", kind, kept) == 1


# ── f. A host delete with no values ────────────────────────────────────────


@pytest.mark.parametrize("kind", HOST_RECORD_KINDS)
def test_r8_a_host_delete_with_no_values_works(world, app_engine, kind) -> None:  # noqa: F811
    rid = _record(world, "a", kind)
    assert _count(world, "a", kind, rid) == 0
    assert _delete_as_app(app_engine, world, "a", kind, [rid]) == 1
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            f"SELECT count(*) FROM {HOST_TABLES[kind]} WHERE id = CAST(:r AS uuid)"),
            {"r": rid}).scalar_one() == 0


# ── g. An update keeps the values ──────────────────────────────────────────


@pytest.mark.parametrize("kind", ("task", "crm.company"))
def test_r8_an_update_keeps_the_values(world, app_engine, kind) -> None:  # noqa: F811
    rid = _record(world, "a", kind)
    _put(world, "a", kind, rid)
    column = "title" if kind == "task" else "name"
    assert _as_app(app_engine, world.orgs["a"].org,
                   f"UPDATE {HOST_TABLES[kind]} SET {column} = 'renamed' "
                   "WHERE id = CAST(:r AS uuid)", {"r": rid}) == 1
    assert _count(world, "a", kind, rid) == 1


# ── h. RLS: org A can not reach a host record of org B ─────────────────────


def test_r8_org_a_can_not_delete_a_host_record_of_org_b(world, app_engine) -> None:  # noqa: F811
    rid = _record(world, "b", "crm.lead")
    _put(world, "b", "crm.lead", rid)
    assert _delete_as_app(app_engine, world, "a", "crm.lead", [rid]) == 0
    assert _count(world, "b", "crm.lead", rid) == 1


# ── i. The migration applies twice ─────────────────────────────────────────


def test_r8_the_migration_applies_twice(world, app_engine) -> None:  # noqa: F811
    path = str(_migration())
    eng = create_engine(world.p.admin_engine.url, future=True)
    try:
        for _ in range(2):
            with eng.connect() as c:
                _exec_file(c, path)
                c.commit()
    finally:
        eng.dispose()
    for table in HOST_TABLES.values():
        assert len(_triggers(world, table)) == 1, table
    rid = _record(world, "a", "crm.contact")
    _put(world, "a", "crm.contact", rid)
    assert _delete_as_app(app_engine, world, "a", "crm.contact", [rid]) == 1
    assert _count(world, "a", "crm.contact", rid) == 0
