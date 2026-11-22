"""Task 3845 — a notes key cannot name a row of another tenant (R8).

Migration: the file that adds ``notes_same_org_ref()`` and its 10
attachments. This file finds it by its CONTENT, never by number (R1). It was
473 at build time.

**The gap.** The notes child tables reference ``meeting`` (or each other)
with a plain foreign key, and a foreign key check ignores RLS. So a session
bound to org A could insert a child row whose parent id is a meeting of org B.
The child took org A from the column DEFAULT, and RLS accepted it. The routes
load the meeting under the bound tenant first, so this is defence in depth
(R5).

**What this file proves, as a role that cannot bypass RLS, with two orgs.**

* For EACH attachment of ``notes_same_org_ref``: an INSERT bound to org A that
  names the parent of org B is refused with SQLSTATE 23503. One probe names
  no org, so the org comes from the DEFAULT (the attack of the task). One
  names org A, as a careful writer does. A bypass role then finds no row.
* The same INSERT that names the parent of org A works and gets org A.
* A superuser that names the parent of org B with org A is refused too. The
  guard compares the orgs, and does not rely on RLS to hide the row.
* An UPDATE that sets the key to the parent of org B is refused (23503).
  ``SET <key> = <key>`` works, and an UPDATE of another column works.
* An UPDATE of the org alone does not call the guard (the column list holds
  only the key). For the app role, RLS WITH CHECK refuses it.
* The two upserts of the routes (``meeting_note`` and ``live_session``) work
  in one org.
* The deletes still work: a meeting with each kind of child, a recording
  alone (``ON DELETE SET NULL`` on ``transcript_segment.recording_id``), and
  a live session with its copilot events.
* **The fences (R7).** ``test_the_probes_cover_every_attachment`` reads
  ``pg_trigger``: the set of attachments must EQUAL the set of probes.
  ``test_every_notes_key_has_a_guard`` reads ``pg_constraint``: each foreign
  key that names a notes table must have an attachment, and each other key of
  a notes table must be in ``OUTSIDE_KEYS`` with a reason. A self-test makes a
  scratch child of ``meeting`` with no guard and proves that the fence finds
  it.

Run, on a PRIVATE database (one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_notes_fk_same_org_r8.py -q -rs

Without the database the R8 tests SKIP, and the run reads green. A skip is
not a pass. The tests of the file text run with no database.

**The mutations (R7).** Edit the migration in the worktree. The ``promoted``
fixture hashes the ladder files, so the next run builds a new template.

* M1. Remove the attachment ``trg_action_item_same_org_meeting_id``. Nine
  tests go red: the two text tests, the four ``action_item.meeting_id``
  probes and the three fence tests.
* M2a. Each attachment is ``BEFORE INSERT OR UPDATE`` with no column list,
  and the body has no changed-link test. Four tests go red: two text tests
  and two straddling-row tests (an UPDATE of another column, and ``SET
  meeting_id = meeting_id``).
* M2b. The body refuses each UPDATE. Twenty-three tests go red, and one is
  the delete of a recording alone: its ``ON DELETE SET NULL`` is an UPDATE.
* M3. A new numbered file makes a child of ``meeting`` with no guard. The
  two fence tests go red and name it.

The delete of a meeting does not reach the guard: Postgres queues the ``SET
NULL`` of a recording after the ``CASCADE`` delete of the segments of the same
meeting. So no mutation turns that test red. It stays as a control.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit import _r8_lists as r8_lists
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_notes_tenant_isolation_r8 import (
    NOTES_TABLES as _ISOLATION_TABLES,
)

_ROOT = Path(__file__).resolve().parents[2]
_FUNCTION = "CREATE OR REPLACE FUNCTION notes_same_org_ref()"
_SQL_MESSAGE = "names no % row of this organization"
_TAG = uuid.uuid4().hex[:8]


def _migration() -> str | None:
    """The numbered file of the guard, found by its content (R1)."""
    found = []
    for path in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql")):
        body = path.read_text(encoding="utf-8")
        if _FUNCTION in body and _SQL_MESSAGE in body:
            found.append(body)
    assert len(found) <= 1, f"expected one migration with the guard, found {len(found)}"
    return found[0] if found else None


def _require_migration() -> str:
    body = _migration()
    assert body is not None, "no migration adds notes_same_org_ref()"
    return body


def _sql_blocks() -> list[str]:
    """The body of each ``BEGIN; ... COMMIT;`` block, in file order."""
    body = _require_migration().replace("\r\n", "\n")
    rest = body[body.index("\nBEGIN;\n"):]
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

_M, _N, _R, _S = "{M}", "{N}", "{R}", "{S}"

PROBES: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {
    ("action_item", "meeting_id"): ("meeting", {
        "meeting_id": _M, "description": f"probe-{_TAG}"}),
    ("copilot_event", "meeting_id"): ("meeting", {
        "session_id": _S, "meeting_id": _M, "text": f"probe-{_TAG}"}),
    ("copilot_event", "session_id"): ("live_session", {
        "session_id": _S, "meeting_id": _M, "text": f"probe-{_TAG}"}),
    ("live_session", "meeting_id"): ("meeting", {
        "meeting_id": _M, "status": "ended"}),
    ("meeting_bot", "meeting_id"): ("meeting", {
        "meeting_id": _M, "meeting_url": "https://meet.google.com/probe"}),
    # The key of a note is its meeting. ``{N}`` is a meeting with no note.
    ("meeting_note", "meeting_id"): ("meeting", {
        "meeting_id": _N, "notes_md": f"probe-{_TAG}"}),
    ("meeting_recording", "meeting_id"): ("meeting", {
        "meeting_id": _M, "artifact_path": f"/tmp/probe-{_TAG}.webm"}),
    ("summary_run", "meeting_id"): ("meeting", {"meeting_id": _M}),
    ("transcript_segment", "meeting_id"): ("meeting", {
        "meeting_id": _M, "text": f"probe-{_TAG}"}),
    ("transcript_segment", "recording_id"): ("meeting_recording", {
        "meeting_id": _M, "recording_id": _R, "text": f"probe-{_TAG}"}),
}

#: The notes tables. Each foreign key that names one of them needs a guard.
#: One list: the isolation suite owns it, and its fence
#: ``test_each_child_of_a_notes_table_is_listed`` makes a new child join it.
NOTES_TABLES = frozenset(t.name for t in _ISOLATION_TABLES)

#: The keys of a notes table that name a table outside the notes set. Each
#: was reviewed, and each carries its reason. Task 3845 left them open.
OUTSIDE_KEYS: dict[tuple[str, str], str] = {
    ("action_item", "assignee_id"): (
        "names person (01). No route writes it today. An open item for its owner."),
    ("action_item", "resulting_task_id"): (
        "names the old task table (01). No route writes it, and "
        "actions.py keeps it NULL. An open item for its owner."),
    ("transcript_segment", "speaker_person_id"): (
        "names person (95). No route writes it today. An open item for its owner."),
}


def _attachments(conn) -> set[tuple[str, str, str]]:
    """(child table, key, parent table) of each attachment of the guard."""
    rows = conn.execute(text(
        "SELECT c.relname, encode(t.tgargs, 'escape') FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE p.proname = 'notes_same_org_ref' AND NOT t.tgisinternal")).all()
    out = set()
    for table, args in rows:
        parent_table, parent_column = args.split("\\000")[:2]
        out.add((table, parent_column, parent_table))
    return out


def _unguarded(conn) -> list[str]:
    """The fence. Each foreign key that names a notes table, or that a notes
    table holds, needs an attachment of the guard or an ``OUTSIDE_KEYS`` row.
    A key with ``organization_id`` in it names the org."""
    keys = conn.execute(text(
        "SELECT c.conrelid::regclass::text, c.confrelid::regclass::text, "
        "  array(SELECT a.attname FROM unnest(c.conkey) k "
        "        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k) "
        "FROM pg_constraint c "
        "WHERE c.contype = 'f' "
        "AND (c.confrelid::regclass::text = ANY(:n) "
        "     OR c.conrelid::regclass::text = ANY(:n))"),
        {"n": sorted(NOTES_TABLES)}).all()
    guarded = {(t, col): parent for t, col, parent in _attachments(conn)}
    missing = []
    for table, parent, columns in keys:
        if parent == "organization" or "organization_id" in columns:
            continue
        if len(columns) != 1:
            missing.append(f"{table}{tuple(columns)} -> {parent} (a composite key)")
            continue
        column = columns[0]
        if guarded.get((table, column)) == parent:
            continue
        if parent not in NOTES_TABLES and (table, column) in OUTSIDE_KEYS:
            continue
        missing.append(f"{table}.{column} -> {parent}")
    return sorted(missing)


# ═══════════════════════════════════════════════════════════════════════════
# 1. The file text. These run with no database.
# ═══════════════════════════════════════════════════════════════════════════


class TestTheMigrationText:
    def test_the_guard_narrows_to_a_changed_link(self) -> None:
        body = _require_migration().replace("\r\n", "\n")
        # The SQL after the header. The header names what the SQL must not do.
        flat = " ".join(body[body.index("\nBEGIN;\n"):].split())
        assert "GET DIAGNOSTICS ref_rows = ROW_COUNT" in flat
        assert "USING ERRCODE = 'foreign_key_violation'" in flat
        assert "IF TG_OP = 'UPDATE'" in flat
        # The org is read through jsonb: on a fresh ladder the generated
        # phases add the column after the numbered files.
        assert "new_row ->> 'organization_id'" in flat
        assert "NEW.organization_id" not in flat
        assert "SECURITY DEFINER" not in flat
        # The lookup names the schema, so a search_path cannot redirect it.
        assert "FROM public.%I p WHERE p.id = $1" in flat

    def test_each_statement_runs_in_its_own_transaction(self) -> None:
        """No deadlock at deploy: the file holds the lock of one table at a
        time. The function first, then one block for each attachment."""
        blocks = _sql_blocks()
        assert len(blocks) == 1 + len(PROBES), len(blocks)
        assert _FUNCTION in blocks[0]
        for block in blocks:
            assert block.startswith("SET LOCAL lock_timeout = '5s';\n"), block
            assert block.count("CREATE OR REPLACE ") == 1, block

    def test_each_attachment_lists_only_its_key(self) -> None:
        """``UPDATE OF <key>``, never ``organization_id``. The column does not
        exist on a fresh ladder when this file runs, and the phase-2 backfill
        changes the org of a child before the org of its parent."""
        seen = set()
        for block in _sql_blocks()[1:]:
            m = re.search(
                r"CREATE OR REPLACE TRIGGER (trg_(\w+?)_same_org_(\w+))\s+"
                r"BEFORE INSERT OR UPDATE OF (\w+) ON (\w+)\s+"
                r"FOR EACH ROW EXECUTE FUNCTION notes_same_org_ref\('(\w+)', '(\w+)'\);",
                block)
            assert m, block
            name_table, name_col = m.group(2), m.group(3)
            key, table, parent, arg_col = m.group(4), m.group(5), m.group(6), m.group(7)
            assert name_table == table and name_col == key == arg_col, block
            assert PROBES[(table, key)][0] == parent, block
            seen.add((table, key))
        assert seen == set(PROBES)

    def test_this_suite_is_named_in_the_ci_skip_guard(self) -> None:
        assert "tests/unit/test_notes_fk_same_org_r8.py" in r8_lists.must_run()


# ═══════════════════════════════════════════════════════════════════════════
# 2. The real database (R8).
# ═══════════════════════════════════════════════════════════════════════════


def _seed_org(conn, org: str, label: str) -> dict[str, str]:
    """One meeting tree for *org*, written by the admin (it bypasses RLS)."""
    def one(sql: str, **kw: Any) -> str:
        return str(conn.execute(text(sql), {"org": org, **kw}).scalar_one())

    r: dict[str, str] = {}
    for key in (_M, _N):
        r[key] = one(
            "INSERT INTO meeting (platform, start_at, title, organization_id) "
            "VALUES ('upload', now(), :t, CAST(:org AS uuid)) RETURNING id",
            t=f"probe-{_TAG}-{label}")
    r[_R] = one(
        "INSERT INTO meeting_recording (meeting_id, artifact_path, organization_id) "
        "VALUES (:m, :p, CAST(:org AS uuid)) RETURNING id",
        m=r[_M], p=f"/tmp/probe-{_TAG}-{label}.webm")
    r[_S] = one(
        "INSERT INTO live_session (meeting_id, status, organization_id) "
        "VALUES (:m, 'ended', CAST(:org AS uuid)) RETURNING id", m=r[_M])
    return r


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """Two seeded trees, one for each org of ``promoted``."""
    with promoted.admin_engine.begin() as conn:
        a = _seed_org(conn, promoted.org_a, "a")
        b = _seed_org(conn, promoted.org_b, "b")
    eng = create_engine(promoted.app_url, future=True, poolclass=NullPool)
    try:
        yield promoted, eng, a, b
    finally:
        eng.dispose()
        with promoted.admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM meeting WHERE id = ANY(CAST(:ids AS uuid[]))"),
                         {"ids": [a[_M], a[_N], b[_M], b[_N]]})


@contextmanager
def _bound(engine, org: str) -> Iterator[Any]:
    """A connection as the app role, bound to ``org``, always rolled back."""
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            yield c
        finally:
            trans.rollback()


def _row(table: str, column: str, own: dict, other: dict) -> dict[str, Any]:
    """The probe row: the bound org's parents, and *column* from *other*."""
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
    key = "meeting_id" if table == "meeting_note" else "id"
    return (f"INSERT INTO {table} ({cols}) VALUES ({vals}) "
            f"RETURNING {key}::text, organization_id::text")


def _pg_error(exc: DBAPIError) -> tuple[str | None, str]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None), str(orig)


def _ids(keys) -> list[str]:
    return [f"{t}.{c}" for t, c in keys]


_KEYS = sorted(PROBES)


@_DB_GATE
class TestTheInsertProbes:
    def test_the_app_role_really_cannot_bypass_rls(self, world) -> None:
        _p, eng, _a, _b = world
        with eng.connect() as c:
            row = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert row is not None and not row[0] and not row[1], row

    @pytest.mark.parametrize("explicit", [False, True], ids=["org_default", "org_named"])
    @pytest.mark.parametrize("key", _KEYS, ids=_ids(_KEYS))
    def test_a_parent_of_the_other_org_is_refused(
            self, world, key: tuple[str, str], explicit: bool) -> None:
        """The leak of the task: bound to org A, a child names B's parent."""
        promoted_, eng, a, b = world
        table, column = key
        row = _row(table, column, a, b)
        if explicit:
            row["organization_id"] = promoted_.org_a
        landed = None
        with _bound(eng, promoted_.org_a) as c:
            try:
                landed = c.execute(text(_insert_sql(table, row)), row).first()
            except DBAPIError as exc:
                state, msg = _pg_error(exc)
                assert state == "23503", msg
                assert _message(table, column, PROBES[key][0]) in msg, msg
        assert landed is None, (
            f"{table}.{column}: a session of org A wrote a row that names the "
            f"{PROBES[key][0]} of org B: {tuple(landed)}")

    @pytest.mark.parametrize("key", _KEYS, ids=_ids(_KEYS))
    def test_a_parent_of_the_own_org_works(self, world, key: tuple[str, str]) -> None:
        promoted_, eng, a, _b = world
        table, column = key
        row = _row(table, column, a, a)
        with _bound(eng, promoted_.org_a) as c:
            got = c.execute(text(_insert_sql(table, row)), row).one()
        assert got[1] == promoted_.org_a, got

    @pytest.mark.parametrize("key", _KEYS, ids=_ids(_KEYS))
    def test_a_superuser_is_refused_too(self, world, key: tuple[str, str]) -> None:
        """The guard compares the orgs. It does not rely on RLS to hide the row."""
        promoted_, _eng, a, b = world
        table, column = key
        row = {**_row(table, column, a, b), "organization_id": promoted_.org_a}
        with promoted_.admin_engine.connect() as c:
            trans = c.begin()
            try:
                with pytest.raises(DBAPIError) as exc:
                    c.execute(text(_insert_sql(table, row)), row)
            finally:
                trans.rollback()
        state, msg = _pg_error(exc.value)
        assert state == "23503", msg


def _seed_child(c, table: str, column: str, own: dict) -> str:
    row = _row(table, column, own, own)
    return c.execute(text(_insert_sql(table, row)), row).one()[0]


@_DB_GATE
class TestTheUpdateProbes:
    @pytest.mark.parametrize("key", _KEYS, ids=_ids(_KEYS))
    def test_a_move_to_a_parent_of_the_other_org_is_refused(
            self, world, key: tuple[str, str]) -> None:
        promoted_, eng, a, b = world
        table, column = key
        pk = "meeting_id" if table == "meeting_note" else "id"
        target = b[PROBES[key][1][column]]
        with _bound(eng, promoted_.org_a) as c:
            mine = _seed_child(c, table, column, a)
            with pytest.raises(DBAPIError) as exc:
                c.execute(text(
                    f"UPDATE {table} SET {column} = :t WHERE {pk}::text = :k"),
                    {"t": target, "k": mine})
        state, msg = _pg_error(exc.value)
        assert state == "23503", msg
        assert _message(table, column, PROBES[key][0]) in msg, msg

    @pytest.mark.parametrize("key", _KEYS, ids=_ids(_KEYS))
    def test_an_update_that_keeps_the_link_works(self, world, key: tuple[str, str]) -> None:
        promoted_, eng, a, _b = world
        table, column = key
        pk = "meeting_id" if table == "meeting_note" else "id"
        with _bound(eng, promoted_.org_a) as c:
            mine = _seed_child(c, table, column, a)
            same = c.execute(text(
                f"UPDATE {table} SET {column} = {column} WHERE {pk}::text = :k"),
                {"k": mine}).rowcount
            other = c.execute(text(
                f"UPDATE {table} SET organization_id = organization_id "
                f"WHERE {pk}::text = :k"), {"k": mine}).rowcount
        assert (same, other) == (1, 1)

    @pytest.mark.parametrize("key", _KEYS, ids=_ids(_KEYS))
    def test_the_app_cannot_move_a_child_to_the_other_org(
            self, world, key: tuple[str, str]) -> None:
        """The column list holds only the key, so an UPDATE of the org alone
        does not call the guard. RLS WITH CHECK refuses it instead: the app
        role can write only the bound org. If this goes green with no refusal,
        the column list is a hole."""
        promoted_, eng, a, _b = world
        table, column = key
        pk = "meeting_id" if table == "meeting_note" else "id"
        with _bound(eng, promoted_.org_a) as c:
            mine = _seed_child(c, table, column, a)
            with pytest.raises(DBAPIError) as exc:
                c.execute(text(
                    f"UPDATE {table} SET organization_id = :b WHERE {pk}::text = :k"),
                    {"b": promoted_.org_b, "k": mine})
        assert "row-level security" in str(exc.value).lower(), exc.value

    def test_the_two_route_upserts_work_in_one_org(self, world) -> None:
        """``summaries.put_note`` and ``live_session.begin``, same org."""
        promoted_, eng, a, _b = world
        with _bound(eng, promoted_.org_a) as c:
            for md in ("one", "two"):
                c.execute(text(
                    "INSERT INTO meeting_note (meeting_id, notes_md, updated_by, "
                    "updated_at) VALUES (:id, :md, 'probe', now()) "
                    "ON CONFLICT (meeting_id) DO UPDATE SET "
                    "notes_md = EXCLUDED.notes_md, updated_at = now()"),
                    {"id": a[_N], "md": md})
            for src in ("bot", "browser"):
                c.execute(text(
                    "INSERT INTO live_session (meeting_id, source, owner_email) "
                    "VALUES (CAST(:m AS UUID), :s, 'probe@notes.test') "
                    "ON CONFLICT (meeting_id) WHERE status = 'live' "
                    "DO UPDATE SET source = EXCLUDED.source, updated_at = now()"),
                    {"m": a[_N], "s": src})
            note = c.execute(text(
                "SELECT notes_md FROM meeting_note WHERE meeting_id = :id"),
                {"id": a[_N]}).scalar_one()
            live = c.execute(text(
                "SELECT source FROM live_session WHERE meeting_id = :m "
                "AND status = 'live'"), {"m": a[_N]}).scalar_one()
        assert (note, live) == ("two", "browser")


@pytest.fixture
def straddling(world):
    """A row that already links two orgs: an ``action_item`` of org A whose
    meeting is of org B. The admin writes it with the triggers off
    (``session_replication_role = replica``), as an old row could be."""
    promoted_, _eng, a, b = world
    with promoted_.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        sid = str(c.execute(text(
            "INSERT INTO action_item (meeting_id, description, organization_id) "
            "VALUES (:m, 'straddle', CAST(:org AS uuid)) RETURNING id"),
            {"m": b[_M], "org": promoted_.org_a}).scalar_one())
    try:
        yield sid, a, b
    finally:
        with promoted_.admin_engine.begin() as c:
            c.execute(text("DELETE FROM action_item WHERE id = :i"), {"i": sid})


@_DB_GATE
class TestTheStraddlingRow:
    """The trade-off of 471: a row that already links two orgs stays writable
    on an UPDATE that does not touch the link. A guard that checks each
    UPDATE breaks it."""

    def test_an_update_of_another_column_works(self, world, straddling) -> None:
        promoted_, eng, _a, _b = world
        sid, _a2, _b2 = straddling
        with _bound(eng, promoted_.org_a) as c:
            n = c.execute(text(
                "UPDATE action_item SET status = 'rejected' WHERE id = :i"),
                {"i": sid}).rowcount
        assert n == 1

    def test_set_key_to_itself_works(self, world, straddling) -> None:
        promoted_, eng, _a, _b = world
        sid, _a2, _b2 = straddling
        with _bound(eng, promoted_.org_a) as c:
            n = c.execute(text(
                "UPDATE action_item SET meeting_id = meeting_id WHERE id = :i"),
                {"i": sid}).rowcount
        assert n == 1

    def test_a_move_to_the_own_org_works(self, world, straddling) -> None:
        """The repair of such a row: point it at a meeting of its own org."""
        promoted_, eng, _a, _b = world
        sid, a, _b2 = straddling
        with _bound(eng, promoted_.org_a) as c:
            n = c.execute(text(
                "UPDATE action_item SET meeting_id = :m WHERE id = :i"),
                {"m": a[_M], "i": sid}).rowcount
        assert n == 1


@_DB_GATE
class TestTheCascadeControls:
    """The deletes still work. A guard that refuses each UPDATE breaks the
    delete of a recording, because its ``ON DELETE SET NULL`` is an UPDATE
    (task 2324 found the same for the pm_* tables)."""

    def _tree(self, c) -> dict[str, str]:
        def one(sql: str, **kw: Any) -> str:
            return str(c.execute(text(sql), kw).scalar_one())

        t: dict[str, str] = {}
        t["meeting"] = one(
            "INSERT INTO meeting (platform, start_at, title) "
            "VALUES ('upload', now(), :t) RETURNING id", t=f"tree-{_TAG}")
        m = t["meeting"]
        t["recording"] = one(
            "INSERT INTO meeting_recording (meeting_id, artifact_path) "
            "VALUES (:m, '/tmp/tree.webm') RETURNING id", m=m)
        t["segment"] = one(
            "INSERT INTO transcript_segment (meeting_id, recording_id, text) "
            "VALUES (:m, :r, 'tree') RETURNING id", m=m, r=t["recording"])
        t["session"] = one(
            "INSERT INTO live_session (meeting_id, status) "
            "VALUES (:m, 'ended') RETURNING id", m=m)
        t["event"] = one(
            "INSERT INTO copilot_event (session_id, meeting_id, text) "
            "VALUES (:s, :m, 'tree') RETURNING id", s=t["session"], m=m)
        t["action"] = one(
            "INSERT INTO action_item (meeting_id, description) "
            "VALUES (:m, 'tree') RETURNING id", m=m)
        t["run"] = one(
            "INSERT INTO summary_run (meeting_id) VALUES (:m) RETURNING id", m=m)
        t["bot"] = one(
            "INSERT INTO meeting_bot (meeting_id, meeting_url) "
            "VALUES (:m, 'https://meet.google.com/tree') RETURNING id", m=m)
        one("INSERT INTO meeting_note (meeting_id, notes_md) "
            "VALUES (:m, 'tree') RETURNING meeting_id", m=m)
        return t

    def test_the_delete_of_a_meeting_takes_every_child(self, world) -> None:
        promoted_, eng, _a, _b = world
        with _bound(eng, promoted_.org_a) as c:
            t = self._tree(c)
            gone = c.execute(text("DELETE FROM meeting WHERE id = :m"),
                             {"m": t["meeting"]}).rowcount
            left = {
                table: c.execute(text(
                    f"SELECT count(*) FROM {table} WHERE meeting_id = :m"),
                    {"m": t["meeting"]}).scalar_one()
                for table in ("meeting_recording", "transcript_segment",
                              "live_session", "copilot_event", "action_item",
                              "summary_run", "meeting_bot", "meeting_note")
            }
        assert gone == 1
        assert not any(left.values()), left

    def test_the_delete_of_a_recording_keeps_its_segment(self, world) -> None:
        """``ON DELETE SET NULL``: the segment stays, with no recording."""
        promoted_, eng, _a, _b = world
        with _bound(eng, promoted_.org_a) as c:
            t = self._tree(c)
            gone = c.execute(text("DELETE FROM meeting_recording WHERE id = :r"),
                             {"r": t["recording"]}).rowcount
            seg = c.execute(text(
                "SELECT recording_id FROM transcript_segment WHERE id = :s"),
                {"s": t["segment"]}).one()
        assert gone == 1
        assert seg[0] is None

    def test_the_delete_of_a_live_session_takes_its_events(self, world) -> None:
        promoted_, eng, _a, _b = world
        with _bound(eng, promoted_.org_a) as c:
            t = self._tree(c)
            gone = c.execute(text("DELETE FROM live_session WHERE id = :s"),
                             {"s": t["session"]}).rowcount
            left = c.execute(text("SELECT count(*) FROM copilot_event WHERE id = :e"),
                             {"e": t["event"]}).scalar_one()
        assert (gone, left) == (1, 0)


@_DB_GATE
class TestTheFence:
    def test_the_database_runs_the_guard(self, world) -> None:
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            src = c.execute(text(
                "SELECT prosrc FROM pg_proc WHERE proname = 'notes_same_org_ref'"
            )).scalar_one_or_none()
        assert src is not None, "the database has no notes_same_org_ref()"
        assert _SQL_MESSAGE in src

    def test_the_probes_cover_every_attachment(self, world) -> None:
        """R7. A new attachment fails here until it has a probe."""
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            live = _attachments(c)
        probed = {(t, col, parent) for (t, col), (parent, _r) in PROBES.items()}
        assert live == probed, (
            f"attachments with no probe: {sorted(live - probed)}; "
            f"probes with no attachment: {sorted(probed - live)}")

    def test_every_notes_key_has_a_guard(self, world) -> None:
        """R7. Each foreign key that names a notes table has a same-org guard.
        Each other key of a notes table is in ``OUTSIDE_KEYS`` with a reason."""
        promoted_, _eng, _a, _b = world
        with promoted_.admin_engine.connect() as c:
            missing = _unguarded(c)
        assert not missing, (
            f"these notes keys have no same-org guard: {missing}. Attach "
            f"notes_same_org_ref() in a new migration, and add a probe here.")

    def test_the_fence_finds_a_new_child_with_no_guard(self, world) -> None:
        """The fence is armed: a scratch child of ``meeting`` with no guard
        goes red. The admin makes it in a transaction that rolls back."""
        promoted_, _eng, _a, _b = world
        scratch = f"notes_scratch_{_TAG}"
        with promoted_.admin_engine.connect() as c:
            trans = c.begin()
            try:
                c.execute(text(
                    f"CREATE TABLE {scratch} (id uuid PRIMARY KEY, "
                    f"meeting_id uuid REFERENCES meeting(id) ON DELETE CASCADE)"))
                missing = _unguarded(c)
            finally:
                trans.rollback()
        assert missing == [f"{scratch}.meeting_id -> meeting"], missing
