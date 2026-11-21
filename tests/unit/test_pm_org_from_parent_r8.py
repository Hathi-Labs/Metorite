"""Task 2324 — a pm_* child row cannot name a parent of another tenant (R8).

Migration: the file that replaces ``pm_organization_from_parent()`` with the
refusal of a parent that the caller cannot read. This file finds it by its
CONTENT, never by number (R1). It was 470 at build time.

**The gap.** Under FORCE row level security a parent of another organization
is a row that the trigger cannot read. The 161 body then returned in silence.
The child kept the ``organization_id`` that the writer named (the bound org),
or it got the org from its other attachment. RLS WITH CHECK accepted that org,
because it is the bound org. The foreign key passed, because a foreign key
check ignores RLS. So a session of org A could store a child row that names a
task or a project of org B.

**What this file proves, as a role that cannot bypass RLS, with two orgs.**

* For EACH (table, parent column) attachment of the function: an INSERT bound
  to org A that names org A explicitly, as the app writes it, and names the
  parent of org B. It fails with SQLSTATE 23503 and the message of the 470
  body, and a bypass role then finds no such row. Under the 161 body (M1) the
  row is STORED, so 28 probes go red on "row stored", not on RLS. The probe of
  ``pm_epic_contracts`` goes red on the message: its 402 guard refuses the row.
* The same INSERT with no ``organization_id``, where the old body still stored
  a row: a table with two attachments (the other one fills the org), and
  ``pm_epic_contracts`` (its column has a DEFAULT). A table with one attachment
  and no DEFAULT is left out: RLS refuses its NULL org under each body.
* The same INSERT that names the parent of org A works and gets org A.
* An UPDATE that moves a task into a project of org B, and an UPDATE that puts
  a project under a project of org B, are refused.
* The org clause: an UPDATE that changes ``organization_id`` of a row whose
  parent is hidden gets the 470 refusal (23503), not the RLS refusal (42501).
  The row is seeded with the triggers off, because no write can make it now.
* The trade-off of the header: an UPDATE that keeps the link and the org of
  that row is not refused.
* The INSERT clause (``TG_OP = 'INSERT'``) has no behaviour test. On an INSERT,
  OLD is NULL, so the changed-link clause is true as well, and no INSERT can
  tell the two apart. ``test_the_numbered_migration_holds_the_refusal_and_its_narrowing``
  is its only fence. It stays for the reader.
* Six cascade deletes still work. The 470 body keeps the old silent return for
  an UPDATE that keeps the link, because a cascade ``ON DELETE SET NULL`` of
  another column runs an UPDATE whose parent can already be gone.
* **The fence (R7).** ``test_the_probes_cover_every_attachment`` reads
  ``pg_trigger`` for every trigger on the function. The set of pairs must
  EQUAL the set of probes, so a new attachment fails here until it has a probe.

Run, on a PRIVATE database (one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_pm_org_from_parent_r8.py -q -rs

Without the database every test SKIPS, and the run reads green. A skip is
not a pass.

**The mutations (R7).** Edit the migration in the worktree. The ``promoted``
fixture hashes the ladder files, so the next run builds a new template.

* M1. The 470 body goes back to the 161 body (a silent return on no row). The
  cross-tenant INSERT probes and the two UPDATE probes go red. Each
  explicit-org probe but ``pm_epic_contracts`` goes red on "row stored".
* M2. The 470 body refuses on no row for each write (no ``TG_OP`` and no
  changed-link test). The cascade controls ``root_project``, ``folder`` and
  ``task_pair`` go red.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.unit import _r8_lists as r8_lists
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_FUNCTION = "CREATE OR REPLACE FUNCTION pm_organization_from_parent()"
_MESSAGE = "is not a row of this organization"
_ME = "probe@pmorg.test"


def _migration() -> str:
    """The numbered file of the refusal, found by its content (R1)."""
    found = []
    for path in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql")):
        body = path.read_text(encoding="utf-8")
        if _FUNCTION in body and _MESSAGE in body:
            found.append(body)
    assert len(found) == 1, f"expected one migration with the refusal, found {len(found)}"
    return found[0]


# ── The probes ──────────────────────────────────────────────────────────────
#
# One entry for each attachment: (child table, parent column) -> (parent
# table, the columns of a minimal row). A value in braces names a seeded row
# of the BOUND org: {P} project, {CH} child project, {S} status, {T} task,
# {T2} second task, {V} view, {R} report. The probed column takes the row of
# the OTHER org. A table with two attachments gives the other column a row of
# the bound org, so the failure belongs to the probed column.

_P, _T, _T2, _V, _R, _S = "{P}", "{T}", "{T2}", "{V}", "{R}", "{S}"

PROBES: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {
    ("pm_activities", "project_id"): ("pm_projects", {
        "type": "comment", "created_by": _ME, "project_id": _P, "task_id": _T}),
    ("pm_activities", "task_id"): ("pm_tasks", {
        "type": "comment", "created_by": _ME, "project_id": _P, "task_id": _T}),
    ("pm_custom_fields", "project_id"): ("pm_projects", {
        "field_key": "probe_key", "name": "Probe", "field_type": "text",
        "created_by": _ME, "project_id": _P}),
    ("pm_epic_contracts", "task_id"): ("pm_tasks", {"task_id": _T}),
    ("pm_intake", "duplicate_of_task_id"): ("pm_tasks", {
        "task_id": _T2, "duplicate_of_task_id": _T, "created_by": _ME}),
    ("pm_intake", "task_id"): ("pm_tasks", {
        "task_id": _T, "duplicate_of_task_id": _T2, "created_by": _ME}),
    ("pm_notifications", "task_id"): ("pm_tasks", {
        "recipient": _ME, "kind": "comment", "task_id": _T, "actor": _ME}),
    ("pm_project_grants", "project_id"): ("pm_projects", {
        "project_id": _P, "subject": "user:probe-grant@pmorg.test", "created_by": _ME}),
    ("pm_project_watchers", "project_id"): ("pm_projects", {
        "project_id": _P, "watcher": "probe-watch@pmorg.test"}),
    ("pm_projects", "parent_project_id"): ("pm_projects", {
        "name": "Probe child", "created_by": _ME, "parent_project_id": _P}),
    ("pm_recurrences", "project_id"): ("pm_projects", {
        "project_id": _P, "freq": "daily", "created_by": _ME}),
    ("pm_report_recipients", "report_id"): ("pm_reports", {
        "report_id": _R, "recipient": "probe-report@pmorg.test"}),
    ("pm_reports", "project_id"): ("pm_projects", {
        "name": "Probe report", "created_by": _ME, "project_id": _P}),
    ("pm_tags", "project_id"): ("pm_projects", {
        "name": "probe-tag", "created_by": _ME, "project_id": _P}),
    ("pm_task_assignees", "task_id"): ("pm_tasks", {
        "task_id": _T, "assignee": "probe-assignee@pmorg.test", "assigned_by": _ME}),
    ("pm_task_attachments", "task_id"): ("pm_tasks", {
        "task_id": _T, "attachment_id": "{NEW}", "added_by": _ME}),
    ("pm_task_counters", "project_id"): ("pm_projects", {"project_id": _P}),
    ("pm_task_links", "source_task_id"): ("pm_tasks", {
        "source_task_id": _T, "target_task_id": _T2, "link_type": "relates_to",
        "created_by": _ME}),
    ("pm_task_links", "target_task_id"): ("pm_tasks", {
        "source_task_id": _T2, "target_task_id": _T, "link_type": "relates_to",
        "created_by": _ME}),
    ("pm_task_personal", "task_id"): ("pm_tasks", {
        "task_id": _T, "member_email": "probe-personal@pmorg.test"}),
    ("pm_task_statuses", "project_id"): ("pm_projects", {
        "project_id": _P, "name": "Probe status", "category": "todo"}),
    ("pm_task_types", "project_id"): ("pm_projects", {
        "project_id": _P, "name": "Probe type"}),
    ("pm_task_watchers", "task_id"): ("pm_tasks", {
        "task_id": _T, "watcher": "probe-watch@pmorg.test"}),
    ("pm_tasks", "project_id"): ("pm_projects", {
        "project_id": _P, "root_project_id": _P, "task_number": 9001,
        "status_id": _S, "title": "Probe task", "created_by": _ME}),
    ("pm_tasks", "root_project_id"): ("pm_projects", {
        "project_id": _P, "root_project_id": _P, "task_number": 9002,
        "status_id": _S, "title": "Probe task", "created_by": _ME}),
    ("pm_view_task_positions", "task_id"): ("pm_tasks", {
        "view_id": _V, "task_id": _T, "position": 1.0}),
    ("pm_view_task_positions", "view_id"): ("pm_views", {
        "view_id": _V, "task_id": _T, "position": 1.0}),
    ("pm_view_user_state", "view_id"): ("pm_views", {
        "view_id": _V, "member": "probe-view@pmorg.test"}),
    ("pm_views", "project_id"): ("pm_projects", {
        "project_id": _P, "name": "Probe view", "created_by": _ME}),
}

_PARENT_TOKEN = {"pm_projects": _P, "pm_tasks": _T, "pm_views": _V, "pm_reports": _R}


def _seed_org(conn, org: str) -> dict[str, uuid.UUID]:
    """One small tree for *org*, written by the admin (it bypasses RLS)."""
    def one(sql: str, **kw: Any) -> uuid.UUID:
        return conn.execute(text(sql), {"org": org, "me": _ME, **kw}).scalar_one()

    rows: dict[str, uuid.UUID] = {}
    rows[_P] = one(
        "INSERT INTO pm_projects (name, created_by, organization_id, owns_statuses) "
        "VALUES ('Probe root', :me, CAST(:org AS uuid), true) RETURNING id")
    rows["{CH}"] = one(
        "INSERT INTO pm_projects (name, created_by, organization_id, parent_project_id) "
        "VALUES ('Probe sub', :me, CAST(:org AS uuid), :p) RETURNING id", p=rows[_P])
    rows[_S] = one(
        "INSERT INTO pm_task_statuses (project_id, name, category, organization_id) "
        "VALUES (:p, 'Open', 'todo', CAST(:org AS uuid)) RETURNING id", p=rows[_P])
    for key, number in ((_T, 1), (_T2, 2)):
        rows[key] = one(
            "INSERT INTO pm_tasks (project_id, root_project_id, task_number, status_id, "
            "title, created_by, organization_id) VALUES (:p, :p, :n, :s, 'Probe', :me, "
            "CAST(:org AS uuid)) RETURNING id", p=rows[_P], n=number, s=rows[_S])
    rows[_V] = one(
        "INSERT INTO pm_views (project_id, name, created_by, organization_id) "
        "VALUES (:p, 'Probe view', :me, CAST(:org AS uuid)) RETURNING id", p=rows[_P])
    rows[_R] = one(
        "INSERT INTO pm_reports (project_id, name, created_by, organization_id) "
        "VALUES (:p, 'Probe report', :me, CAST(:org AS uuid)) RETURNING id", p=rows[_P])
    return rows


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """Two seeded trees, one for each org of ``promoted``."""
    with promoted.admin_engine.begin() as conn:
        a = _seed_org(conn, promoted.org_a)
        b = _seed_org(conn, promoted.org_b)
    eng = create_engine(promoted.app_url, future=True)
    try:
        yield promoted, eng, a, b
    finally:
        eng.dispose()


def _bind(conn, org: str) -> None:
    conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org})


def _row(table: str, column: str, own: dict, other: dict) -> dict[str, Any]:
    """The probe row: the bound org's rows, and *column* from *other*."""
    _parent, template = PROBES[(table, column)]
    out: dict[str, Any] = {}
    for col, val in template.items():
        if val == "{NEW}":
            out[col] = uuid.uuid4()
        elif isinstance(val, str) and val.startswith("{"):
            out[col] = (other if col == column else own)[val]
        else:
            out[col] = val
    return out


def _insert_sql(table: str, row: dict[str, Any]) -> str:
    cols = ", ".join(row)
    vals = ", ".join(f":{c}" for c in row)
    return f"INSERT INTO {table} ({cols}) VALUES ({vals}) RETURNING organization_id"


def _pg_error(exc: DBAPIError) -> tuple[str | None, str]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None), str(orig)


def _ids():
    return [f"{t}.{c}" for t, c in sorted(PROBES)]


# ═══════════════════════════════════════════════════════════════════════════


class TestTheMigration:
    def test_the_numbered_migration_holds_the_refusal_and_its_narrowing(self) -> None:
        flat = " ".join(_migration().split())
        assert "GET DIAGNOSTICS parent_rows = ROW_COUNT" in flat
        assert "USING ERRCODE = 'foreign_key_violation'" in flat
        assert "IF TG_OP = 'INSERT'" in flat
        assert "IS DISTINCT FROM parent_id::text" in flat
        assert "NEW.organization_id IS DISTINCT FROM OLD.organization_id" in flat
        assert "SECURITY DEFINER" not in flat.split("BEGIN;", 1)[1]

    def test_the_database_runs_the_470_body(self, world) -> None:
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            src = c.execute(text(
                "SELECT prosrc FROM pg_proc WHERE proname = 'pm_organization_from_parent'"
            )).scalar_one()
        assert _MESSAGE in src

    def test_this_suite_is_named_in_the_ci_skip_guard(self) -> None:
        assert "tests/unit/test_pm_org_from_parent_r8.py" in r8_lists.must_run()


class TestTheFence:
    def test_the_probes_cover_every_attachment(self, world) -> None:
        """R7. A new attachment of the function fails here until it has a probe."""
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            rows = c.execute(text(
                "SELECT c.relname, encode(t.tgargs, 'escape') FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE p.proname = 'pm_organization_from_parent' AND NOT t.tgisinternal"
            )).all()
        live = set()
        for table, args in rows:
            parent_table, parent_column = args.split("\\000")[:2]
            live.add((table, parent_column, parent_table))
        probed = {(t, c, parent) for (t, c), (parent, _row) in PROBES.items()}
        assert live == probed, (
            f"attachments with no probe: {sorted(live - probed)}; "
            f"probes with no attachment: {sorted(probed - live)}")


# The probes with no `organization_id` where the 161 body still stored a row:
# a table with two attachments (the other one fills the org), and
# `pm_epic_contracts` (its column has a DEFAULT). On a table with one
# attachment and no DEFAULT, RLS refuses the NULL org under each body, so
# that probe would prove nothing.
_TWO_ATTACHMENTS = {t for t, _c in PROBES if sum(1 for t2, _ in PROBES if t2 == t) > 1}
IMPLICIT = sorted(
    (t, c) for t, c in PROBES if t in _TWO_ATTACHMENTS or t == "pm_epic_contracts")


def _probe_across(world, table: str, column: str, *, explicit: bool) -> None:
    """Bound to org A, insert a row that names the parent of org B."""
    promoted_, eng, a, b = world
    row = _row(table, column, a, b)
    if explicit:
        # The app idiom: the writer names the bound org (templates.py,
        # admin.create_type).
        row["organization_id"] = uuid.UUID(promoted_.org_a)
    err: DBAPIError | None = None
    with eng.connect() as c:
        tx = c.begin()
        _bind(c, promoted_.org_a)
        try:
            c.execute(text(_insert_sql(table, row)), row)
            tx.commit()
        except DBAPIError as exc:
            err = exc
            tx.rollback()
    # As the admin (it bypasses RLS): no row of org A names the parent of
    # org B. Then remove any such row, so a mutation run leaves no trace.
    where = f"{column} = :parent AND organization_id = CAST(:org AS uuid)"
    args = {"parent": row[column], "org": promoted_.org_a}
    with promoted_.admin_engine.begin() as c:
        stored = c.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), args).scalar_one()
        c.execute(text(f"DELETE FROM {table} WHERE {where}"), args)
    assert stored == 0, f"row stored: {table}.{column} of org A names a parent of org B"
    assert err is not None, f"{table}.{column}: a parent of org B was accepted"
    state, message = _pg_error(err)
    assert state == "23503", message
    parent_table = PROBES[(table, column)][0]
    assert f"{table}: {parent_table} {column} {_MESSAGE}" in message, message


class TestTheInsertProbes:
    @pytest.mark.parametrize(("table", "column"), sorted(PROBES), ids=_ids())
    def test_a_parent_of_another_org_is_refused_with_the_org_named(
            self, world, table, column) -> None:
        _probe_across(world, table, column, explicit=True)

    @pytest.mark.parametrize(
        ("table", "column"), IMPLICIT, ids=[f"{t}.{c}" for t, c in IMPLICIT])
    def test_a_parent_of_another_org_is_refused_with_no_org_named(
            self, world, table, column) -> None:
        _probe_across(world, table, column, explicit=False)

    @pytest.mark.parametrize(("table", "column"), sorted(PROBES), ids=_ids())
    def test_a_parent_of_the_own_org_is_accepted(self, world, table, column) -> None:
        promoted_, eng, a, _b = world
        row = _row(table, column, a, a)
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                org = c.execute(text(_insert_sql(table, row)), row).scalar_one()
            finally:
                tx.rollback()
        assert str(org) == promoted_.org_a


class TestTheUpdateProbes:
    def _refused(self, world, sql: str, params: dict, needle: str) -> None:
        promoted_, eng, _a, _b = world
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                with pytest.raises(DBAPIError) as caught:
                    c.execute(text(sql), params)
            finally:
                tx.rollback()
        state, message = _pg_error(caught.value)
        assert state == "23503", message
        assert needle in message, message

    def test_a_task_cannot_move_into_a_project_of_another_org(self, world) -> None:
        _p, _eng, a, b = world
        self._refused(
            world, "UPDATE pm_tasks SET project_id = :p WHERE id = :t",
            {"p": b[_P], "t": a[_T]}, f"pm_tasks: pm_projects project_id {_MESSAGE}")

    def test_a_project_cannot_move_under_a_project_of_another_org(self, world) -> None:
        _p, _eng, a, b = world
        self._refused(
            world, "UPDATE pm_projects SET parent_project_id = :p WHERE id = :c",
            {"p": b[_P], "c": a["{CH}"]},
            f"pm_projects: pm_projects parent_project_id {_MESSAGE}")

    def test_an_update_inside_the_own_org_works(self, world) -> None:
        promoted_, eng, a, _b = world
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                n = c.execute(text(
                    "UPDATE pm_tasks SET title = 'Moved', project_id = :p WHERE id = :t"),
                    {"p": a["{CH}"], "t": a[_T]}).rowcount
            finally:
                tx.rollback()
        assert n == 1


@pytest.fixture
def straddling(world):
    """A watcher row of org A that names a task of org B: the shape the gap
    left before 470. No write can make it now, so the admin writes it with
    the triggers off (``session_replication_role = replica``)."""
    promoted_, _eng, _a, b = world
    with promoted_.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        wid = c.execute(text(
            "INSERT INTO pm_task_watchers (task_id, watcher, organization_id) "
            "VALUES (:t, 'straddle@pmorg.test', CAST(:org AS uuid)) RETURNING id"),
            {"t": b[_T], "org": promoted_.org_a}).scalar_one()
    try:
        yield wid
    finally:
        with promoted_.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_task_watchers WHERE id = :w"), {"w": wid})


class TestTheOrgClause:
    def test_a_change_of_org_with_a_hidden_parent_gets_the_470_refusal(
            self, world, straddling) -> None:
        """Without the org clause the trigger returns in silence, and RLS
        WITH CHECK refuses the row with 42501. The clause gives 23503 first."""
        promoted_, eng, _a, _b = world
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                with pytest.raises(DBAPIError) as caught:
                    c.execute(text(
                        "UPDATE pm_task_watchers SET organization_id = CAST(:o AS uuid) "
                        "WHERE id = :w"), {"o": promoted_.org_b, "w": straddling})
            finally:
                tx.rollback()
        state, message = _pg_error(caught.value)
        assert state == "23503", message
        assert f"pm_task_watchers: pm_tasks task_id {_MESSAGE}" in message, message

    def test_an_update_that_keeps_the_link_and_the_org_is_not_refused(
            self, world, straddling) -> None:
        """The trade-off of the header, pinned: such a row stays writable."""
        promoted_, eng, _a, _b = world
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                n = c.execute(text(
                    "UPDATE pm_task_watchers SET watcher = 'straddle-2@pmorg.test' "
                    "WHERE id = :w"), {"w": straddling}).rowcount
            finally:
                tx.rollback()
        assert n == 1


# ── The cascade controls ────────────────────────────────────────────────────
#
# A delete of a parent runs the `ON DELETE SET NULL` updates of seven keys on
# attached tables. The parent of the updated row can already be gone, so the
# trigger finds no row. The 470 body keeps the old silent return there,
# because the update keeps the link. When the body refused on each write (M2),
# the deletes `root_project`, `folder` and `task_pair` failed. The other three
# passed in that cascade order, so they are controls and do not tell M2 apart.

_ORG_C = "c2324000-0000-4000-8000-000000002324"


def _seed_cascade_tree(conn, org: str) -> dict[str, uuid.UUID]:
    def one(sql: str, **kw: Any) -> uuid.UUID:
        return conn.execute(text(sql), {"org": org, "me": _ME, **kw}).scalar_one()

    def proj(name: str, **kw: Any) -> uuid.UUID:
        cols = ", ".join(kw)
        vals = ", ".join(f":{k}" for k in kw)
        return one(
            f"INSERT INTO pm_projects (name, created_by, organization_id, {cols}) "
            f"VALUES (:name, :me, CAST(:org AS uuid), {vals}) RETURNING id", name=name, **kw)

    def task(project: uuid.UUID, root: uuid.UUID, number: int, status: uuid.UUID,
             **kw: Any) -> uuid.UUID:
        cols = "".join(f", {k}" for k in kw)
        vals = "".join(f", :{k}" for k in kw)
        return one(
            "INSERT INTO pm_tasks (project_id, root_project_id, task_number, status_id, "
            f"title, created_by, organization_id{cols}) VALUES (:pr, :ro, :n, :st, 'C', :me, "
            f"CAST(:org AS uuid){vals}) RETURNING id",
            pr=project, ro=root, n=number, st=status, **kw)

    t: dict[str, uuid.UUID] = {}
    t["P"] = proj("P", owns_statuses=True)
    t["CH"] = proj("CH", parent_project_id=t["P"], archived_root_id=t["P"])
    s = one("INSERT INTO pm_task_statuses (project_id, name, category, organization_id) "
            "VALUES (:p, 'Open', 'todo', CAST(:org AS uuid)) RETURNING id", p=t["P"])
    ty = one("INSERT INTO pm_task_types (project_id, name, organization_id) "
             "VALUES (:p, 'Bug', CAST(:org AS uuid)) RETURNING id", p=t["P"])
    rc = one("INSERT INTO pm_recurrences (project_id, freq, created_by, organization_id) "
             "VALUES (:p, 'daily', :me, CAST(:org AS uuid)) RETURNING id", p=t["P"])
    t["T1"] = task(t["P"], t["P"], 1, s, type_id=ty, recurrence_id=rc)
    t["T2"] = task(t["CH"], t["P"], 2, s, parent_task_id=t["T1"])
    conn.execute(text(
        "UPDATE pm_tasks SET archived_at = now(), merged_into_task_id = :m WHERE id = :t"),
        {"m": t["T1"], "t": t["T2"]})
    conn.execute(text(
        "INSERT INTO pm_intake (task_id, duplicate_of_task_id, organization_id, created_by) "
        "VALUES (:a, :b, CAST(:org AS uuid), :me)"),
        {"a": t["T2"], "b": t["T1"], "org": org, "me": _ME})
    a1 = one("INSERT INTO pm_activities (type, created_by, organization_id, task_id) "
             "VALUES ('comment', :me, CAST(:org AS uuid), :t) RETURNING id", t=t["T1"])
    one("INSERT INTO pm_activities (type, created_by, organization_id, task_id, parent_id) "
        "VALUES ('comment', :me, CAST(:org AS uuid), :t, :a) RETURNING id", t=t["T2"], a=a1)
    one("INSERT INTO pm_activities (type, created_by, organization_id, project_id, parent_id) "
        "VALUES ('comment', :me, CAST(:org AS uuid), :p, :a) RETURNING id", p=t["CH"], a=a1)
    # A folder with two sibling projects, and subtasks that cross them.
    t["F"] = proj("F", owns_statuses=True, kind="folder")
    s2 = one("INSERT INTO pm_task_statuses (project_id, name, category, organization_id) "
             "VALUES (:p, 'Open', 'todo', CAST(:org AS uuid)) RETURNING id", p=t["F"])
    t["C1"] = proj("C1", parent_project_id=t["F"])
    t["C2"] = proj("C2", parent_project_id=t["F"])
    x = task(t["C1"], t["F"], 1, s2)
    y = task(t["C2"], t["F"], 2, s2, parent_task_id=x)
    task(t["C1"], t["F"], 3, s2, parent_task_id=y)
    conn.execute(text(
        "INSERT INTO pm_intake (task_id, duplicate_of_task_id, organization_id, created_by) "
        "VALUES (:a, :b, CAST(:org AS uuid), :me)"),
        {"a": y, "b": x, "org": org, "me": _ME})
    return t


@pytest.fixture(scope="module")
def cascade(world):
    """A third org with the cascade tree. Each test deletes in a transaction
    and rolls it back, so the tree serves every test."""
    promoted_, eng, _a, _b = world
    with promoted_.admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (CAST(:id AS uuid), 'pmorg-c', 'pmorg-c')"), {"id": _ORG_C})
        tree = _seed_cascade_tree(conn, _ORG_C)
    return promoted_, eng, tree


_DELETES = {
    "root_project": ("DELETE FROM pm_projects WHERE id = :x", ("P",)),
    "folder": ("DELETE FROM pm_projects WHERE id = :x", ("F",)),
    "one_sibling": ("DELETE FROM pm_projects WHERE id = :x", ("C1",)),
    "task_pair": ("DELETE FROM pm_tasks WHERE id = ANY(:x)", ("T1", "T2")),
    "one_task": ("DELETE FROM pm_tasks WHERE id = :x", ("T1",)),
}


class TestTheCascadeControls:
    @pytest.mark.parametrize("case", sorted(_DELETES))
    def test_a_delete_as_the_app_role_still_works(self, cascade, case) -> None:
        _p, eng, tree = cascade
        sql, keys = _DELETES[case]
        target: Any = [tree[k] for k in keys] if len(keys) > 1 else tree[keys[0]]
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, _ORG_C)
            try:
                deleted = c.execute(text(sql), {"x": target}).rowcount
            finally:
                tx.rollback()
        assert deleted == len(keys)

    def test_a_delete_of_the_org_still_works(self, cascade) -> None:
        """The admin deletes the org, as the offboard path does. The app role
        has no row of ``organization`` to delete."""
        promoted_, _eng, _tree = cascade
        with promoted_.admin_engine.connect() as c:
            tx = c.begin()
            try:
                deleted = c.execute(text(
                    "DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": _ORG_C}).rowcount
            finally:
                tx.rollback()
        assert deleted == 1
