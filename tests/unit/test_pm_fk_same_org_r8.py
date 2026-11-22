"""Task 3846 — a pm_* key cannot name a row of another tenant (R8).

Migration: the file that adds ``pm_same_org_ref()`` and its 21 attachments.
This file finds it by its CONTENT, never by number (R1). The lane booked 472
at build time. SW-107 merged its own 472 first, so the file became 476 on
2026-11-22.

**The gap.** A foreign key check ignores RLS. 471 closed the gap for the 29
attachments of ``pm_organization_from_parent()``. Nineteen more pm_* keys had
no guard: ``pm_tasks.parent_task_id``, ``type_id``, ``status_id`` and others.
A session bound to org A could set such a key to the id of a row of org B.
SW-107 (472) then added a twentieth key, ``pm_agent_runs.dispatched_by_run_id``,
and the file guards it too. SW-39 (475) added a twenty-first,
``pm_agent_runs.parent_run_id``: a child run names its parent run. A child has
no task, so its probe row is ``kind = 'child'`` with no task and no app.

**What this file proves, as a role that cannot bypass RLS, with two orgs.**

* For EACH attachment of ``pm_same_org_ref``: an INSERT bound to org A that
  names the row of org B is refused with SQLSTATE 23503 and the message of
  the 476 body. A bypass role then finds no such row. One probe names org A
  explicitly, as the app writes it. One names no org, so the org comes from
  the 161 trigger or from the DEFAULT. That second probe pins the name order:
  the check runs after the 161 trigger fills the org.
* The same INSERT that names the row of org A works and gets org A, with the
  org named and with no org named.
* A NULL key works, for each key where NULL makes a valid row.
* A superuser that names the row of org B is refused too. The guard compares
  the orgs, and does not rely on RLS to hide the row.
* The file has 22 transactions: the function, then one for each attachment.
  So a deploy holds the lock of one table at a time, and cannot deadlock with
  a write that takes two of the tables.
* An UPDATE that sets the key to the row of org B is refused (23503). An
  UPDATE that sets the key to the same value works. ``pm_agent_guidance`` and
  ``pm_agent_policies`` are left out: their own guard refuses each UPDATE.
* A row that already links two orgs: an UPDATE of another column works, and
  ``SET <key> = <key>`` works (the rule of 471). An UPDATE that changes the org
  gets 23503 from the 476 body, not 42501 from RLS.
* Eleven deletes over the 21 keys still work: each ``ON DELETE SET NULL`` and
  ``CASCADE`` of a guarded key, and the delete of the org.
* A child run (SW-39) that names a parent run of the other org is refused, as
  the app role and as the admin. A child under a parent of its own org works.
* **The fences (R7).** ``test_the_probes_cover_every_attachment`` reads
  ``pg_trigger``: the set of attachments must EQUAL the set of probes.
  ``test_every_pm_key_has_a_guard`` reads ``pg_constraint``: each single-column
  key of a pm_* table that names a tenant row must have a guard. A new pm_*
  key fails here until it has one.

Run, on a PRIVATE database (one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_pm_fk_same_org_r8.py -q -rs

Without the database every test SKIPS, and the run reads green. A skip is
not a pass.

**The mutations (R7).** Edit the migration in the worktree. The ``promoted``
fixture hashes the ladder files, so the next run builds a new template.

* M1. Remove the attachment ``trg_pm_tasks_same_org_type_id``. The attachment
  fence, the guard fence and the ``pm_tasks.type_id`` probes go red (5).
* M2. The guard refuses on each UPDATE: each attachment is ``BEFORE INSERT OR
  UPDATE`` with no column list, and the body has no changed-link test. The
  cascades ``task_pair`` and ``one_task``, the two straddle controls, the
  column-list fence and the text fence go red (6).
* M2b. Only the changed-link test goes from the body. The column lists stay.
  The control ``SET type_id = type_id`` and the text fence go red (2).
* M3. ``trg_pm_tasks_same_org_type_id`` gets a name that sorts before
  ``trg_pm_tasks_org_from_*``. The guard sees a NULL org and lets the row by.
  The probe ``no_org_named[pm_tasks.type_id]`` goes red (1).
* M4. Remove the attachment ``trg_pm_agent_runs_same_org_dispatched_by_run_id``
  (SW-107's key). The block count, the attachment fence, the guard fence, the
  two cross-org INSERT probes and the UPDATE probe of
  ``pm_agent_runs.dispatched_by_run_id`` go red (6). Run 2026-11-22.
"""

from __future__ import annotations

import datetime as dt
import re
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
_FUNCTION = "CREATE OR REPLACE FUNCTION pm_same_org_ref()"
_SQL_MESSAGE = "names no % row of this organization"
_ME = "probe@pmref.test"
_NOW = dt.datetime(2026, 11, 21, tzinfo=dt.UTC)


def _migration() -> str:
    """The numbered file of the guard, found by its content (R1)."""
    found = []
    for path in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql")):
        body = path.read_text(encoding="utf-8")
        if _FUNCTION in body and _SQL_MESSAGE in body:
            found.append(body)
    assert len(found) == 1, f"expected one migration with the guard, found {len(found)}"
    return found[0]


def _sql_blocks_text() -> str:
    """The SQL of the file after its header: from the first ``BEGIN;`` line."""
    body = _migration().replace("\r\n", "\n")
    return body[body.index("\nBEGIN;\n"):]


def _sql_blocks() -> list[str]:
    """The body of each ``BEGIN; ... COMMIT;`` block, in file order."""
    rest = _sql_blocks_text()
    out = []
    while "\nBEGIN;\n" in rest:
        start = rest.index("\nBEGIN;\n") + len("\nBEGIN;\n")
        end = rest.index("\nCOMMIT;\n", start)
        out.append(rest[start:end + 1])
        rest = rest[end + len("\nCOMMIT;"):]
    assert rest.strip() == "", f"SQL outside a block: {rest.strip()[:80]}"
    return out


def _message(table: str, column: str, parent: str) -> str:
    return f"{table}.{column} names no {parent} row of this organization"


# ── The probes ──────────────────────────────────────────────────────────────
#
# One entry for each attachment: (child table, key) -> (parent table, the
# columns of a minimal row). A value in braces names a seeded row of the
# BOUND org. The probed key takes the row of the OTHER org.

_P, _S, _T, _T2 = "{P}", "{S}", "{T}", "{T2}"
_TY, _RC, _A, _CONN, _RUN, _RA = "{TY}", "{RC}", "{A}", "{CONN}", "{RUN}", "{RA}"


def _task(number: int, **extra: Any) -> dict[str, Any]:
    return {"project_id": _P, "root_project_id": _P, "task_number": number,
            "status_id": _S, "title": "Probe task", "created_by": _ME, **extra}


def _run(**extra: Any) -> dict[str, Any]:
    return {"task_id": _T, "member_email": _ME, "client_via": "mcp:probe",
            "status": "completed", "started_by": _ME, **extra}


def _child_run(**extra: Any) -> dict[str, Any]:
    """SW-39 (475): a child run has no task and no app. Its parent is its tie."""
    return {"kind": "child", "parent_run_id": _RUN, "member_email": _ME,
            "client_via": "mcp:probe", "status": "in_progress", "started_by": _ME, **extra}


def _run_act(**extra: Any) -> dict[str, Any]:
    return {"run_id": _RUN, "task_id": _T, "type": "thought", "actor": _ME, **extra}


PROBES: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {
    ("pm_activities", "parent_id"): ("pm_activities", {
        "type": "comment", "created_by": _ME, "task_id": _T, "parent_id": _A}),
    ("pm_agent_guidance", "project_id"): ("pm_projects", {
        "project_id": _P, "body": "Probe guidance", "written_by": _ME}),
    ("pm_agent_policies", "project_id"): ("pm_projects", {
        "scope_kind": "project", "project_id": _P, "key": "probe_key",
        "written_by": _ME}),
    ("pm_agent_run_activities", "in_reply_to"): ("pm_agent_run_activities",
                                                 _run_act(in_reply_to=_RA)),
    ("pm_agent_run_activities", "pm_activity_id"): ("pm_activities",
                                                    _run_act(pm_activity_id=_A)),
    ("pm_agent_run_activities", "run_id"): ("pm_agent_runs", _run_act()),
    ("pm_agent_run_activities", "task_id"): ("pm_tasks", _run_act()),
    ("pm_agent_run_strands", "run_id"): ("pm_agent_runs", {
        "run_id": _RUN, "task_id": _T, "member_email": _ME, "reason": "agent_quiet",
        "seen_activity_at": _NOW}),
    ("pm_agent_run_strands", "task_id"): ("pm_tasks", {
        "run_id": _RUN, "task_id": _T, "member_email": _ME, "reason": "agent_quiet",
        "seen_activity_at": _NOW}),
    ("pm_agent_runs", "connection_id"): ("agent_connection", _run(connection_id=_CONN)),
    ("pm_agent_runs", "dispatched_by_run_id"): ("pm_agent_runs",
                                                _run(dispatched_by_run_id=_RUN)),
    ("pm_agent_runs", "parent_run_id"): ("pm_agent_runs", _child_run()),
    ("pm_agent_runs", "task_id"): ("pm_tasks", _run()),
    ("pm_notifications", "activity_id"): ("pm_activities", {
        "recipient": _ME, "kind": "comment", "task_id": _T, "actor": _ME,
        "activity_id": _A}),
    ("pm_projects", "archived_root_id"): ("pm_projects", {
        "name": "Probe archived", "created_by": _ME, "parent_project_id": _P,
        "archived_root_id": _P}),
    ("pm_task_refs", "task_id"): ("pm_tasks", {
        "task_id": _T, "kind": "url", "provider": "other", "ref": "probe-ref",
        "url": "https://probe.test/ref", "created_by": _ME}),
    ("pm_tasks", "merged_into_task_id"): ("pm_tasks", _task(
        9102, merged_into_task_id=_T, archived_at=_NOW)),
    ("pm_tasks", "parent_task_id"): ("pm_tasks", _task(9101, parent_task_id=_T)),
    ("pm_tasks", "recurrence_id"): ("pm_recurrences", _task(9105, recurrence_id=_RC)),
    ("pm_tasks", "status_id"): ("pm_task_statuses", _task(9103)),
    ("pm_tasks", "type_id"): ("pm_task_types", _task(9104, type_id=_TY)),
}

# A NULL key makes a valid row here. `status_id` is NOT NULL. A run or a run
# activity with no task needs an `app` (342). A project policy needs its
# project (the CHECK `pm_agent_policies_scope_shape`). A child run needs its
# parent (the CHECK `pm_agent_runs_child_parent` of 475), so `parent_run_id`
# is not here. A NULL parent still passes the guard: each other probe of
# `pm_agent_runs` inserts a run with no parent through it. The other keys are
# NOT NULL.
NULLABLE = sorted([
    ("pm_activities", "parent_id"),
    ("pm_agent_guidance", "project_id"),
    ("pm_agent_run_activities", "in_reply_to"),
    ("pm_agent_run_activities", "pm_activity_id"),
    ("pm_agent_runs", "connection_id"),
    ("pm_agent_runs", "dispatched_by_run_id"),
    ("pm_notifications", "activity_id"),
    ("pm_projects", "archived_root_id"),
    ("pm_tasks", "merged_into_task_id"),
    ("pm_tasks", "parent_task_id"),
    ("pm_tasks", "recurrence_id"),
    ("pm_tasks", "type_id"),
])

# Their own guard (340, 343, 404) refuses each UPDATE of a saved row.
_APPEND_ONLY = {"pm_agent_guidance", "pm_agent_policies"}
UPDATABLE = sorted(k for k in PROBES if k[0] not in _APPEND_ONLY)

# The keys of a pm_* table that a guard other than these two functions
# covers. Each was reviewed, and each names the trigger that guards it.
NAMED_GUARDS: dict[tuple[str, str], str] = {
    ("pm_cycle_tasks", "task_id"): "pm_cycle_tasks_same_org",
    ("pm_cycles", "project_id"): "pm_cycles_same_org",
    ("pm_project_authorities", "page_id"): "pm_project_authorities_same_org",
    ("pm_project_authorities", "project_id"): "pm_project_authorities_same_org",
    ("pm_project_updates", "project_id"): "pm_project_updates_same_org",
    ("pm_template_applies", "project_id"): "pm_template_applies_same_org",
    ("pm_template_applies", "template_id"): "pm_template_applies_same_org",
    ("pm_templates", "source_project_id"): "pm_templates_same_org",
}


def _seed_org(conn, org: str) -> dict[str, uuid.UUID]:
    """One small tree for *org*, written by the admin (it bypasses RLS)."""
    def one(sql: str, **kw: Any) -> uuid.UUID:
        return conn.execute(text(sql), {"org": org, "me": _ME, **kw}).scalar_one()

    r: dict[str, uuid.UUID] = {}
    r[_P] = one(
        "INSERT INTO pm_projects (name, created_by, organization_id, owns_statuses) "
        "VALUES ('Probe root', :me, CAST(:org AS uuid), true) RETURNING id")
    r[_S] = one(
        "INSERT INTO pm_task_statuses (project_id, name, category, organization_id) "
        "VALUES (:p, 'Open', 'todo', CAST(:org AS uuid)) RETURNING id", p=r[_P])
    r[_TY] = one(
        "INSERT INTO pm_task_types (project_id, name, organization_id) "
        "VALUES (:p, 'Bug', CAST(:org AS uuid)) RETURNING id", p=r[_P])
    r[_RC] = one(
        "INSERT INTO pm_recurrences (project_id, freq, created_by, organization_id) "
        "VALUES (:p, 'daily', :me, CAST(:org AS uuid)) RETURNING id", p=r[_P])
    for key, number in ((_T, 1), (_T2, 2)):
        r[key] = one(
            "INSERT INTO pm_tasks (project_id, root_project_id, task_number, status_id, "
            "title, created_by, organization_id) VALUES (:p, :p, :n, :s, 'Probe', :me, "
            "CAST(:org AS uuid)) RETURNING id", p=r[_P], n=number, s=r[_S])
    r[_A] = one(
        "INSERT INTO pm_activities (type, created_by, organization_id, task_id) "
        "VALUES ('comment', :me, CAST(:org AS uuid), :t) RETURNING id", t=r[_T])
    r[_CONN] = one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id, "
        "expires_at) VALUES (CAST(:org AS uuid), :me, 'probe-client', "
        "now() + interval '1 day') RETURNING id")
    r[_RUN] = one(
        "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
        "status, started_by) VALUES (CAST(:org AS uuid), :t, :me, 'mcp:probe', "
        "'completed', :me) RETURNING id", t=r[_T])
    r[_RA] = one(
        "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, type, "
        "actor) VALUES (CAST(:org AS uuid), :run, :t, 'thought', :me) RETURNING id",
        run=r[_RUN], t=r[_T])
    return r


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
        if isinstance(val, str) and val.startswith("{"):
            out[col] = (other if col == column else own)[val]
        else:
            out[col] = val
    return out


def _insert_sql(table: str, row: dict[str, Any]) -> str:
    cols = ", ".join(row)
    vals = ", ".join(f":{c}" for c in row)
    return f"INSERT INTO {table} ({cols}) VALUES ({vals}) RETURNING id, organization_id"


def _pg_error(exc: DBAPIError) -> tuple[str | None, str]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None), str(orig)


def _ids(keys) -> list[str]:
    return [f"{t}.{c}" for t, c in keys]


# ═══════════════════════════════════════════════════════════════════════════


class TestTheMigration:
    def test_the_numbered_migration_holds_the_guard_and_its_narrowing(self) -> None:
        flat = " ".join(_migration().split())
        assert "GET DIAGNOSTICS ref_rows = ROW_COUNT" in flat
        assert "USING ERRCODE = 'foreign_key_violation'" in flat
        assert "IF TG_OP = 'UPDATE'" in flat
        assert "IS NOT DISTINCT FROM ref_id::text" in flat
        assert "NEW.organization_id IS NOT DISTINCT FROM OLD.organization_id" in flat
        assert "ref_org IS DISTINCT FROM NEW.organization_id" in flat
        body = " ".join(_sql_blocks_text().split())
        assert "SECURITY DEFINER" not in body
        # The guard never derives an org: no assignment to NEW.
        assert "NEW.organization_id :=" not in body
        assert "pm_organization_from_parent" not in body
        # The lookup names the schema, so a search_path cannot redirect it.
        assert "SELECT organization_id FROM public.%I WHERE id = $1" in body

    def test_each_statement_runs_in_its_own_transaction(self) -> None:
        """No deadlock at deploy: the file holds the lock of one table at a
        time. 21 blocks: the function, then one block for each attachment."""
        blocks = _sql_blocks()
        assert len(blocks) == 1 + len(PROBES), len(blocks)
        assert "CREATE OR REPLACE FUNCTION pm_same_org_ref()" in blocks[0]
        for block in blocks:
            assert block.startswith("SET LOCAL lock_timeout = '5s';\n"), block
            assert block.count("CREATE OR REPLACE ") == 1, block
        for block in blocks[1:]:
            assert block.startswith(
                "SET LOCAL lock_timeout = '5s';\nCREATE OR REPLACE TRIGGER "), block

    def test_the_database_runs_the_476_body(self, world) -> None:
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            src = c.execute(text(
                "SELECT prosrc FROM pg_proc WHERE proname = 'pm_same_org_ref'"
            )).scalar_one()
        assert _SQL_MESSAGE in src

    def test_this_suite_is_named_in_the_ci_skip_guard(self) -> None:
        assert "tests/unit/test_pm_fk_same_org_r8.py" in r8_lists.must_run()


def _attachments(conn, function: str) -> set[tuple[str, str, str]]:
    rows = conn.execute(text(
        "SELECT c.relname, encode(t.tgargs, 'escape') FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE p.proname = :f AND NOT t.tgisinternal"), {"f": function}).all()
    out = set()
    for table, args in rows:
        parent_table, parent_column = args.split("\\000")[:2]
        out.add((table, parent_column, parent_table))
    return out


class TestTheFence:
    def test_the_probes_cover_every_attachment(self, world) -> None:
        """R7. A new attachment of the guard fails here until it has a probe."""
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            live = _attachments(c, "pm_same_org_ref")
        probed = {(t, c, parent) for (t, c), (parent, _row) in PROBES.items()}
        assert live == probed, (
            f"attachments with no probe: {sorted(live - probed)}; "
            f"probes with no attachment: {sorted(probed - live)}")

    def test_each_attachment_checks_the_key_and_the_org_only(self, world) -> None:
        """The column list of each attachment is ``<key>, organization_id``.
        An UPDATE of another column must not call the guard (the cascade path)."""
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            rows = c.execute(text(
                "SELECT c.relname, encode(t.tgargs, 'escape'), pg_get_triggerdef(t.oid) "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE p.proname = 'pm_same_org_ref' AND NOT t.tgisinternal")).all()
        for table, args, definition in rows:
            column = args.split("\\000")[1]
            assert (f"BEFORE INSERT OR UPDATE OF {column}, organization_id ON "
                    f"public.{table} FOR EACH ROW") in definition, definition

    def test_every_pm_key_has_a_guard(self, world) -> None:
        """R7. Each single-column foreign key of a pm_* table that names a row
        of a tenant table has a same-org guard. A key with the org in it is
        same-org by construction. A key to ``organization`` is the org."""
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            keys = c.execute(text(
                "SELECT c.conrelid::regclass::text, c.confrelid::regclass::text, "
                "  array(SELECT a.attname FROM unnest(c.conkey) k "
                "        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k) "
                "FROM pg_constraint c "
                "WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE 'pm\\_%'"
            )).all()
            guarded = (_attachments(c, "pm_organization_from_parent")
                       | _attachments(c, "pm_same_org_ref"))
            named = {(r[0], r[1]): r[2] for r in c.execute(text(
                "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal "
                "AND c.relname LIKE 'pm\\_%'")).all()}
        by_key = {(t, col): parent for t, col, parent in guarded}
        missing, wrong_parent = [], []
        for table, parent, columns in keys:
            if parent == "organization" or "organization_id" in columns:
                continue
            assert len(columns) == 1, f"{table}{columns}: a composite key with no org"
            key = (table, columns[0])
            if key in NAMED_GUARDS:
                definition = named.get((table, NAMED_GUARDS[key]))
                assert definition is not None, f"{key}: guard gone"
                # The guard fires on an INSERT, and on an UPDATE of the key
                # and of the org.
                assert "BEFORE INSERT OR UPDATE" in definition, definition
                listed = re.search(r"UPDATE OF ([\w, ]+) ON ", definition)
                if listed is not None:
                    cols = {col.strip() for col in listed.group(1).split(",")}
                    assert {columns[0], "organization_id"} <= cols, definition
            elif key not in by_key:
                missing.append(f"{table}.{columns[0]} -> {parent}")
            elif by_key[key] != parent:
                wrong_parent.append(f"{table}.{columns[0]}: {by_key[key]} != {parent}")
        assert not missing, f"pm_* keys with no same-org guard: {missing}"
        assert not wrong_parent, f"guards that read the wrong table: {wrong_parent}"


# ── The INSERT probes ───────────────────────────────────────────────────────


def _probe_across(world, table: str, column: str, *, explicit: bool) -> None:
    """Bound to org A, insert a row whose *column* names the row of org B."""
    promoted_, eng, a, b = world
    row = _row(table, column, a, b)
    if explicit:
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
    # As the admin (it bypasses RLS): no row of org A names the row of org B.
    # Then remove any such row, so a mutation run leaves no trace.
    where = f"{column} = :ref AND organization_id = CAST(:org AS uuid)"
    args = {"ref": row[column], "org": promoted_.org_a}
    with promoted_.admin_engine.begin() as c:
        stored = c.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), args).scalar_one()
        c.execute(text(f"DELETE FROM {table} WHERE {where}"), args)
    assert stored == 0, f"row stored: {table}.{column} of org A names a row of org B"
    assert err is not None, f"{table}.{column}: a row of org B was accepted"
    state, message = _pg_error(err)
    assert state == "23503", message
    assert _message(table, column, PROBES[(table, column)][0]) in message, message


def _insert_own(world, table: str, column: str, *, explicit: bool,
                null_key: bool = False) -> str:
    promoted_, eng, a, _b = world
    row = _row(table, column, a, a)
    if null_key:
        row[column] = None
    if explicit:
        row["organization_id"] = uuid.UUID(promoted_.org_a)
    with eng.connect() as c:
        tx = c.begin()
        _bind(c, promoted_.org_a)
        try:
            _id, org = c.execute(text(_insert_sql(table, row)), row).one()
        finally:
            tx.rollback()
    return str(org)


class TestTheInsertProbes:
    @pytest.mark.parametrize(("table", "column"), sorted(PROBES), ids=_ids(sorted(PROBES)))
    def test_a_row_of_another_org_is_refused_with_the_org_named(
            self, world, table, column) -> None:
        _probe_across(world, table, column, explicit=True)

    @pytest.mark.parametrize(("table", "column"), sorted(PROBES), ids=_ids(sorted(PROBES)))
    def test_a_row_of_another_org_is_refused_with_no_org_named(
            self, world, table, column) -> None:
        """The org comes from the 161 trigger or the DEFAULT. The guard must
        run after the 161 trigger, or it sees a NULL org and lets the row by."""
        _probe_across(world, table, column, explicit=False)

    @pytest.mark.parametrize(("table", "column"), sorted(PROBES), ids=_ids(sorted(PROBES)))
    @pytest.mark.parametrize("explicit", [True, False], ids=["org_named", "no_org"])
    def test_a_row_of_the_own_org_is_accepted(
            self, world, table, column, explicit) -> None:
        promoted_ = world[0]
        assert _insert_own(world, table, column, explicit=explicit) == promoted_.org_a

    @pytest.mark.parametrize(("table", "column"), NULLABLE, ids=_ids(NULLABLE))
    def test_a_null_key_is_accepted(self, world, table, column) -> None:
        promoted_ = world[0]
        org = _insert_own(world, table, column, explicit=True, null_key=True)
        assert org == promoted_.org_a

    def test_a_role_that_bypasses_rls_is_refused_too(self, world) -> None:
        """The admin is a superuser, so it reads the type of org B. The guard
        compares the orgs, and does not rely on RLS to hide the row."""
        promoted_, _eng, a, b = world
        row = _row("pm_tasks", "type_id", a, b)
        row["organization_id"] = uuid.UUID(promoted_.org_a)
        with promoted_.admin_engine.connect() as c:
            tx = c.begin()
            try:
                with pytest.raises(DBAPIError) as caught:
                    c.execute(text(_insert_sql("pm_tasks", row)), row)
            finally:
                tx.rollback()
        state, message = _pg_error(caught.value)
        assert state == "23503", message
        assert _message("pm_tasks", "type_id", "pm_task_types") in message, message


# ── The UPDATE probes ───────────────────────────────────────────────────────


class TestTheUpdateProbes:
    @pytest.mark.parametrize(("table", "column"), UPDATABLE, ids=_ids(UPDATABLE))
    def test_an_update_to_a_row_of_another_org_is_refused(
            self, world, table, column) -> None:
        """Bound to org A: make a row of org A, then point its key at org B.
        The same transaction then keeps the link, and clears it where it can."""
        promoted_, eng, a, b = world
        row = _row(table, column, a, a)
        token = PROBES[(table, column)][1][column]
        nullable = (table, column) in NULLABLE
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                rid = c.execute(text(_insert_sql(table, row)), row).one()[0]
                sp = c.begin_nested()
                with pytest.raises(DBAPIError) as caught:
                    c.execute(text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                              {"v": b[token], "id": rid})
                sp.rollback()
                same = c.execute(text(
                    f"UPDATE {table} SET {column} = {column} WHERE id = :id"),
                    {"id": rid}).rowcount
                back = c.execute(text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                                 {"v": a[token], "id": rid}).rowcount
                cleared = (c.execute(text(
                    f"UPDATE {table} SET {column} = NULL WHERE id = :id"),
                    {"id": rid}).rowcount if nullable else 1)
            finally:
                tx.rollback()
        state, message = _pg_error(caught.value)
        assert state == "23503", message
        assert _message(table, column, PROBES[(table, column)][0]) in message, message
        assert (same, back, cleared) == (1, 1, 1)

    def test_a_self_reference_inside_the_own_org_works(self, world) -> None:
        """``pm_tasks.parent_task_id`` names its own table: a task moves under
        another task of the org, and a subtask of that subtask works."""
        promoted_, eng, a, _b = world
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                n1 = c.execute(text(
                    "UPDATE pm_tasks SET parent_task_id = :p WHERE id = :t"),
                    {"p": a[_T], "t": a[_T2]}).rowcount
                row = _row("pm_tasks", "parent_task_id", a, a)
                row["parent_task_id"] = a[_T2]
                row["task_number"] = 9201
                n2 = len(c.execute(text(_insert_sql("pm_tasks", row)), row).all())
            finally:
                tx.rollback()
        assert (n1, n2) == (1, 1)


# ── The child run (SW-39, 475) ──────────────────────────────────────────────

_CHILD_SQL = (
    "INSERT INTO pm_agent_runs (organization_id, kind, parent_run_id, member_email, "
    "client_via, status, started_by) VALUES (CAST(:org AS uuid), 'child', :p, :me, "
    "'mcp:probe', 'in_progress', :me) RETURNING id, organization_id, task_id, parent_run_id")


class TestTheChildRun:
    """A child run has no task and no app. Only its parent ties it to an org,
    so the guard reads the parent run and never ``task_id``."""

    def test_a_child_under_a_parent_of_another_org_is_refused(self, world) -> None:
        """As the app role bound to org A, and as the admin, which reads the
        run of org B. Each gets 23503 from the 476 body."""
        promoted_, eng, _a, b = world
        args = {"org": promoted_.org_a, "p": b[_RUN], "me": _ME}
        errors = []
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                with pytest.raises(DBAPIError) as caught:
                    c.execute(text(_CHILD_SQL), args)
                errors.append(caught.value)
            finally:
                tx.rollback()
        with promoted_.admin_engine.connect() as c:
            tx = c.begin()
            try:
                with pytest.raises(DBAPIError) as caught:
                    c.execute(text(_CHILD_SQL), args)
                errors.append(caught.value)
            finally:
                tx.rollback()
        for err in errors:
            state, message = _pg_error(err)
            assert state == "23503", message
            assert _message("pm_agent_runs", "parent_run_id", "pm_agent_runs") in message
        with promoted_.admin_engine.connect() as c:
            stored = c.execute(text("SELECT count(*) FROM pm_agent_runs "
                                    "WHERE parent_run_id = :p"), {"p": b[_RUN]}).scalar_one()
        assert stored == 0

    def test_a_child_under_a_parent_of_its_own_org_is_accepted(self, world) -> None:
        promoted_, eng, a, _b = world
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, promoted_.org_a)
            try:
                _id, org, task, parent = c.execute(text(_CHILD_SQL), {
                    "org": promoted_.org_a, "p": a[_RUN], "me": _ME}).one()
            finally:
                tx.rollback()
        assert (str(org), task, parent) == (promoted_.org_a, None, a[_RUN])

    def test_a_delete_of_the_parent_deletes_the_child(self, cascade) -> None:
        """The CASCADE of ``parent_run_id``, as the app role."""
        _p, eng, tree = cascade
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, _ORG_C)
            try:
                before = c.execute(text("SELECT count(*) FROM pm_agent_runs WHERE id = :c"),
                                   {"c": tree["C1"]}).scalar_one()
                c.execute(text("DELETE FROM pm_agent_runs WHERE id = :r"), {"r": tree["R1"]})
                after = c.execute(text("SELECT count(*) FROM pm_agent_runs WHERE id = :c"),
                                  {"c": tree["C1"]}).scalar_one()
            finally:
                tx.rollback()
        assert (before, after) == (1, 0)


@pytest.fixture
def straddling(world):
    """Two rows of org A that already name a row of org B: the shape the gap
    left before 476. No write can make them now, so the admin writes them with
    the triggers off (``session_replication_role = replica``). A task whose
    type is of org B, and a ref whose task is of org B."""
    promoted_, _eng, a, b = world
    with promoted_.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        tid = c.execute(text(
            "INSERT INTO pm_tasks (project_id, root_project_id, task_number, status_id, "
            "title, created_by, organization_id, type_id) VALUES (:p, :p, 9301, :s, "
            "'Straddle', :me, CAST(:org AS uuid), :ty) RETURNING id"),
            {"p": a[_P], "s": a[_S], "me": _ME, "org": promoted_.org_a,
             "ty": b[_TY]}).scalar_one()
        rid = c.execute(text(
            "INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "created_by) VALUES (CAST(:org AS uuid), :t, 'url', 'other', 'straddle', "
            "'https://probe.test/s', :me) RETURNING id"),
            {"org": promoted_.org_a, "t": b[_T], "me": _ME}).scalar_one()
    try:
        yield tid, rid
    finally:
        with promoted_.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_task_refs WHERE id = :r"), {"r": rid})
            c.execute(text("DELETE FROM pm_tasks WHERE id = :t"), {"t": tid})


def _as_a(world, sql: str, params: dict) -> int:
    promoted_, eng, _a, _b = world
    with eng.connect() as c:
        tx = c.begin()
        _bind(c, promoted_.org_a)
        try:
            return c.execute(text(sql), params).rowcount
        finally:
            tx.rollback()


class TestTheStraddlingRow:
    def test_an_update_of_another_column_is_not_refused(self, world, straddling) -> None:
        """The trade-off of the header, pinned: such a row stays writable."""
        tid, _rid = straddling
        assert _as_a(world, "UPDATE pm_tasks SET title = 'Straddle 2' WHERE id = :t",
                     {"t": tid}) == 1

    def test_an_update_that_sets_the_same_link_is_not_refused(
            self, world, straddling) -> None:
        """The column list calls the guard, and the body sees an unchanged link."""
        tid, _rid = straddling
        assert _as_a(world, "UPDATE pm_tasks SET type_id = type_id WHERE id = :t",
                     {"t": tid}) == 1

    def test_a_change_of_org_with_a_hidden_row_gets_the_476_refusal(
            self, world, straddling) -> None:
        """Without the org clause the guard returns in silence, and RLS WITH
        CHECK refuses the row with 42501. The clause gives 23503 first."""
        promoted_ = world[0]
        _tid, rid = straddling
        with pytest.raises(DBAPIError) as caught:
            _as_a(world, "UPDATE pm_task_refs SET organization_id = CAST(:o AS uuid) "
                  "WHERE id = :r", {"o": promoted_.org_b, "r": rid})
        state, message = _pg_error(caught.value)
        assert state == "23503", message
        assert _message("pm_task_refs", "task_id", "pm_tasks") in message, message


# ── The cascade controls ────────────────────────────────────────────────────
#
# Each guarded key with ON DELETE SET NULL or CASCADE, deleted as the app
# role. The guard keeps the old silent return for an UPDATE that keeps the
# link, and its column list skips an UPDATE of another key. When the guard
# refused on each UPDATE (M2), `task_pair` and `one_task` failed. In
# `task_pair` the cascade clears `T3.merged_into_task_id` while
# `T3.parent_task_id` names `T2`, which the same statement deleted. The other
# deletes are controls.

_ORG_C = "c3846000-0000-4000-8000-000000003846"


def _seed_cascade_tree(conn, org: str) -> dict[str, uuid.UUID]:
    def one(sql: str, **kw: Any) -> uuid.UUID:
        return conn.execute(text(sql), {"org": org, "me": _ME, **kw}).scalar_one()

    def root(name: str, **kw: Any) -> uuid.UUID:
        cols = "".join(f", {k}" for k in kw)
        vals = "".join(f", :{k}" for k in kw)
        return one(
            f"INSERT INTO pm_projects (name, created_by, organization_id, owns_statuses{cols}) "
            f"VALUES (:name, :me, CAST(:org AS uuid), true{vals}) RETURNING id",
            name=name, **kw)

    def task(number: int, **kw: Any) -> uuid.UUID:
        cols = "".join(f", {k}" for k in kw)
        vals = "".join(f", :{k}" for k in kw)
        return one(
            "INSERT INTO pm_tasks (project_id, root_project_id, task_number, status_id, "
            f"title, created_by, organization_id{cols}) VALUES (:p, :p, :n, :s, 'C', :me, "
            f"CAST(:org AS uuid){vals}) RETURNING id", p=t["P"], n=number, s=t["S"], **kw)

    t: dict[str, uuid.UUID] = {}
    t["P"] = root("P")
    t["P2"] = root("P2")
    t["Q"] = root("Q", archived_root_id=t["P2"])
    t["S"] = one("INSERT INTO pm_task_statuses (project_id, name, category, organization_id) "
                 "VALUES (:p, 'Open', 'todo', CAST(:org AS uuid)) RETURNING id", p=t["P"])
    t["TY"] = one("INSERT INTO pm_task_types (project_id, name, organization_id) "
                  "VALUES (:p, 'Bug', CAST(:org AS uuid)) RETURNING id", p=t["P"])
    t["RC"] = one("INSERT INTO pm_recurrences (project_id, freq, created_by, organization_id) "
                  "VALUES (:p, 'daily', :me, CAST(:org AS uuid)) RETURNING id", p=t["P"])
    t["T1"] = task(1, type_id=t["TY"], recurrence_id=t["RC"])
    t["T2"] = task(2, parent_task_id=t["T1"], type_id=t["TY"])
    t["T3"] = task(3, parent_task_id=t["T2"], merged_into_task_id=t["T1"],
                   archived_at=_NOW)
    t["A1"] = one("INSERT INTO pm_activities (type, created_by, organization_id, task_id) "
                  "VALUES ('comment', :me, CAST(:org AS uuid), :t) RETURNING id", t=t["T1"])
    t["A2"] = one("INSERT INTO pm_activities (type, created_by, organization_id, task_id, "
                  "parent_id) VALUES ('comment', :me, CAST(:org AS uuid), :t, :a) RETURNING id",
                  t=t["T2"], a=t["A1"])
    one("INSERT INTO pm_notifications (recipient, kind, task_id, actor, activity_id, "
        "organization_id) VALUES (:me, 'comment', :t, :me, :a, CAST(:org AS uuid)) "
        "RETURNING id", t=t["T2"], a=t["A1"])
    t["CONN"] = one("INSERT INTO agent_connection (organization_id, member_email, client_id, "
                    "expires_at) VALUES (CAST(:org AS uuid), :me, 'cascade-client', "
                    "now() + interval '1 day') RETURNING id")
    for run, tk in (("R1", "T1"), ("R2", "T2")):
        t[run] = one(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via, "
            "status, started_by, connection_id) VALUES (CAST(:org AS uuid), :t, :me, "
            "'mcp:cascade', 'completed', :me, :cn) RETURNING id", t=t[tk], cn=t["CONN"])
        one("INSERT INTO pm_agent_run_strands (organization_id, run_id, task_id, member_email, "
            "reason, seen_activity_at) VALUES (CAST(:org AS uuid), :r, :t, :me, "
            "'agent_quiet', now()) RETURNING id", r=t[run], t=t[tk])
        one("INSERT INTO pm_task_refs (organization_id, task_id, kind, provider, ref, url, "
            "created_by) VALUES (CAST(:org AS uuid), :t, 'url', 'other', :ref, "
            "'https://probe.test/c', :me) RETURNING id", t=t[tk], ref=f"ref-{tk}")
    # SW-107: R2 names R1 as its dispatcher. A delete of R1 sets it to NULL.
    one("UPDATE pm_agent_runs SET dispatched_by_run_id = :d WHERE id = :r RETURNING id",
        d=t["R1"], r=t["R2"])
    # SW-39: C1 is a child run under R1, with no task. A delete of R1 deletes it.
    t["C1"] = one("INSERT INTO pm_agent_runs (organization_id, kind, parent_run_id, "
                  "member_email, client_via, status, started_by, connection_id) VALUES "
                  "(CAST(:org AS uuid), 'child', :r, :me, 'mcp:cascade', 'in_progress', :me, "
                  ":cn) RETURNING id", r=t["R1"], cn=t["CONN"])
    t["RA1"] = one("INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, "
                   "type, actor) VALUES (CAST(:org AS uuid), :r, :t, 'thought', :me) "
                   "RETURNING id", r=t["R1"], t=t["T1"])
    t["RA2"] = one("INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, "
                   "type, actor, in_reply_to, pm_activity_id) VALUES (CAST(:org AS uuid), "
                   ":r, :t, 'response', :me, :ra, :a) RETURNING id",
                   r=t["R2"], t=t["T2"], ra=t["RA1"], a=t["A1"])
    one("INSERT INTO pm_agent_guidance (organization_id, project_id, body, written_by) "
        "VALUES (CAST(:org AS uuid), :p, 'Cascade guidance', :me) RETURNING id", p=t["P"])
    one("INSERT INTO pm_agent_policies (organization_id, scope_kind, project_id, key, "
        "written_by) VALUES (CAST(:org AS uuid), 'project', :p, 'cascade_key', :me) "
        "RETURNING id", p=t["P"])
    return t


@pytest.fixture(scope="module")
def cascade(world):
    """A third org with the cascade tree. Each test deletes in a transaction
    and rolls it back, so the tree serves every test."""
    promoted_, eng, _a, _b = world
    with promoted_.admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (CAST(:id AS uuid), 'pmref-c', 'pmref-c')"), {"id": _ORG_C})
        tree = _seed_cascade_tree(conn, _ORG_C)
    return promoted_, eng, tree


# case -> (table, keys to delete, the keys that the delete runs over)
_DELETES: dict[str, tuple[str, tuple[str, ...]]] = {
    # pm_tasks.status_id (via the statuses), every key of the tree, and the
    # guidance and policy of the project.
    "root_project": ("pm_projects", ("P",)),
    # pm_projects.archived_root_id: SET NULL on Q.
    "archived_root": ("pm_projects", ("P2",)),
    # parent_task_id and merged_into_task_id of T3 together, with T2 gone.
    "task_pair": ("pm_tasks", ("T1", "T2")),
    # parent_task_id, merged_into_task_id, then the CASCADE of each task key:
    # pm_task_refs, pm_agent_runs, pm_agent_run_activities, pm_agent_run_strands.
    "one_task": ("pm_tasks", ("T1",)),
    "task_type": ("pm_task_types", ("TY",)),
    "recurrence": ("pm_recurrences", ("RC",)),
    # pm_activities.parent_id, pm_notifications.activity_id and
    # pm_agent_run_activities.pm_activity_id.
    "activity": ("pm_activities", ("A1",)),
    # pm_agent_run_activities.in_reply_to.
    "run_activity": ("pm_agent_run_activities", ("RA1",)),
    # pm_agent_run_activities.run_id, pm_agent_run_strands.run_id, the
    # SET NULL of pm_agent_runs.dispatched_by_run_id on R2, and the CASCADE of
    # pm_agent_runs.parent_run_id on the child C1.
    "run": ("pm_agent_runs", ("R1",)),
    # pm_agent_runs.connection_id, on R1, R2 and the child C1.
    "connection": ("agent_connection", ("CONN",)),
}


class TestTheCascadeControls:
    @pytest.mark.parametrize("case", sorted(_DELETES))
    def test_a_delete_as_the_app_role_still_works(self, cascade, case) -> None:
        _p, eng, tree = cascade
        table, keys = _DELETES[case]
        with eng.connect() as c:
            tx = c.begin()
            _bind(c, _ORG_C)
            try:
                deleted = c.execute(text(f"DELETE FROM {table} WHERE id = ANY(:x)"),
                                    {"x": [tree[k] for k in keys]}).rowcount
            finally:
                tx.rollback()
        assert deleted == len(keys)

    def test_a_delete_of_the_org_still_works(self, cascade) -> None:
        """The admin deletes the org, as the offboard path does."""
        promoted_, _eng, _tree = cascade
        with promoted_.admin_engine.connect() as c:
            tx = c.begin()
            try:
                deleted = c.execute(text(
                    "DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": _ORG_C}).rowcount
            finally:
                tx.rollback()
        assert deleted == 1
