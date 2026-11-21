"""Task 2009 (MTG-13, NM-2): R8 tenant isolation of the notes tables.

Two tenants never see, write or change each other's rows in a notes table.
This suite adds no feature. It proves the tenancy that the MT-1b generated
phases put on the notes tables (``infra/postgres/generated/01_add_columns.sql``,
``03_constraints.sql`` and ``04_policies.sql``).

**The tables (11).** The plan row NM-2
(``project-docs/research/2026-11-plan-audit/meetings.md``) names 10:
``meeting``, ``meeting_recording``, ``transcript_segment``, ``meeting_note``,
``summary_run``, ``action_item``, ``notes_glossary``, ``meeting_bot``,
``live_session`` and ``copilot_config``. Migration ``121_copilot_event.sql``
adds an 11th, ``copilot_event``, and ``routes/notes/copilot.py`` reads it.
This suite covers all 11. Migrations 101, 124, 125, 126, 127, 129 and 135 add
only columns to these tables, so they add no table.

The suite runs on the phase-4 promoted catalog of
``test_h3_rls_promotion_rehearsal.py`` (the ``promoted`` fixture). It connects
as the app role ``acb_app_h3rls``, which is not a superuser, not the owner and
has no BYPASSRLS. It binds a tenant with ``set_config('app.tenant_id', ...)``,
as the ``get_db`` seam does. The admin (superuser) seeds one row for org A and
one for org B in each table.

Fences (R7):

* ``notes-bound-read-own-rows``: a session bound to org A sees A's row and no
  row of any other org, also by B's key. The mirror holds for org B.
* ``notes-write-other-tenant-refused``: an INSERT stamped with org B, from a
  session bound to org A, fails on row level security. The same row stamped
  with org A succeeds (a control, rolled back).
* ``notes-change-other-tenant-noop``: an UPDATE or DELETE of B's row by key,
  from a session bound to org A, touches 0 rows, and B's row stays.
* ``notes-unbound-fail-closed``: a session with no tenant sees 0 rows.
* ``notes-tables-force-rls``: each table in ``NOTES_TABLES`` has ENABLE and
  FORCE row level security and a policy on ``app.tenant_id`` with USING and
  WITH CHECK.
* ``notes-children-listed``: each table with a foreign key to a notes table
  is in ``NOTES_TABLES``. So a new child of ``meeting`` with no RLS goes red
  here. ``notes_glossary`` and ``copilot_config`` have no foreign key to a
  notes table, so only their names in ``NOTES_TABLES`` cover them.

A table that leaks is not repaired here (owner, 2026-11-21: tests only). Its
case carries ``xfail(strict=True)`` with the finding as the reason.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_notes_tenant_isolation_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.unit import _r8_lists as r8_lists

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]

#: Parents of one org: two meetings and one live session. ``spare`` has no
#: child row, so a refused INSERT can name it without a key clash.
Parents = dict[str, str]


@dataclass(frozen=True)
class NotesTable:
    """One notes table: its key column, and the columns of a valid row."""

    name: str
    key: str
    #: (org label, parents, the key of the row) -> the other NOT NULL columns.
    row: Callable[[str, Parents, str], dict[str, Any]]
    #: A new key for an INSERT, from (org label, parents).
    new_key: Callable[[str, Parents], str]


def _uuid_key(_label: str, _p: Parents) -> str:
    return str(uuid.uuid4())


def _child(**extra: Any) -> Callable[[str, Parents, str], dict[str, Any]]:
    """A row with a ``meeting_id``, plus the fixed columns in ``extra``."""

    def _row(_label: str, p: Parents, _key: str) -> dict[str, Any]:
        return {"meeting_id": p["meeting"], **extra}

    return _row


NOTES_TABLES: tuple[NotesTable, ...] = (
    NotesTable(
        "meeting", "id",
        lambda label, _p, _k: {
            "platform": "upload", "start_at": datetime.now(UTC),
            "title": f"r8-{TAG}-{label}",
        },
        _uuid_key,
    ),
    NotesTable("action_item", "id", _child(description=f"r8-{TAG}"), _uuid_key),
    NotesTable(
        "meeting_recording", "id",
        _child(artifact_path=f"/tmp/r8-{TAG}.webm"), _uuid_key,
    ),
    NotesTable("transcript_segment", "id", _child(text=f"r8-{TAG}"), _uuid_key),
    # Keyed by the meeting: a new row needs a meeting with no note.
    NotesTable(
        "meeting_note", "meeting_id",
        lambda _label, _p, _k: {"notes_md": f"r8-{TAG}"},
        lambda _label, p: p["spare"],
    ),
    NotesTable("summary_run", "id", _child(), _uuid_key),
    NotesTable(
        "notes_glossary", "id",
        lambda label, _p, _k: {
            "user_id": f"{label}-{TAG}@r8notes.test", "term": f"term-{TAG}",
        },
        _uuid_key,
    ),
    NotesTable(
        "meeting_bot", "id",
        _child(meeting_url="https://meet.google.com/r8-notes"), _uuid_key,
    ),
    # Keyed by the owner's email, a TEXT primary key.
    NotesTable(
        "copilot_config", "owner_email",
        lambda _label, _p, _k: {"instructions": f"r8-{TAG}"},
        lambda label, _p: f"{label}-{uuid.uuid4().hex[:8]}@r8notes.test",
    ),
    NotesTable("live_session", "id", _child(status="ended"), _uuid_key),
    NotesTable(
        "copilot_event", "id",
        lambda _label, p, _k: {
            "meeting_id": p["meeting"], "session_id": p["session"],
            "text": f"r8-{TAG}",
        },
        _uuid_key,
    ),
)

_BY_NAME = {t.name: t for t in NOTES_TABLES}

#: A table with a foreign key to a notes table, that is not a notes table.
#: Each entry needs a reason. Empty today.
NOT_NOTES_CHILDREN: dict[str, str] = {}

#: Leak findings: table -> the reason of its strict xfail. Empty today.
LEAKS: dict[str, str] = {}


def _params() -> list[Any]:
    return [
        pytest.param(
            t.name, id=t.name,
            marks=[pytest.mark.xfail(strict=True, reason=LEAKS[t.name])]
            if t.name in LEAKS else [],
        )
        for t in NOTES_TABLES
    ]


def _insert(conn, table: NotesTable, key: str, cols: dict[str, Any],
            org: str | None) -> None:
    values = {table.key: key, **cols}
    if org is not None:
        values["organization_id"] = org
    names = ", ".join(values)
    binds = ", ".join(f":{n}" for n in values)
    conn.execute(text(f"INSERT INTO {table.name} ({names}) VALUES ({binds})"), values)


def _row_for(seeded: dict[str, Any], label: str, name: str) -> tuple[str, dict[str, Any]]:
    """The key and the columns that the seed wrote for ``label`` in ``name``."""
    return seeded[label]["keys"][name], seeded[label]["cols"][name]


# ==========================================================================
# The seed: one row per org per table, written by the admin (no RLS).
# ==========================================================================
@pytest.fixture(scope="module")
def seeded(promoted) -> Iterator[dict[str, Any]]:  # noqa: F811
    p = promoted
    out: dict[str, Any] = {}
    with p.admin_engine.begin() as c:
        for label, org in (("a", p.org_a), ("b", p.org_b)):
            parents: Parents = {
                "meeting": str(uuid.uuid4()),
                "spare": str(uuid.uuid4()),
                "session": "",
            }
            keys: dict[str, str] = {}
            cols: dict[str, dict[str, Any]] = {}
            meeting = _BY_NAME["meeting"]
            for mid in (parents["meeting"], parents["spare"]):
                _insert(c, meeting, mid, meeting.row(label, parents, mid), org)
            keys["meeting"] = parents["meeting"]
            cols["meeting"] = {}
            for t in NOTES_TABLES:
                if t.name == "meeting":
                    continue
                key = parents["meeting"] if t.key == "meeting_id" else t.new_key(label, parents)
                row = t.row(label, parents, key)
                _insert(c, t, key, row, org)
                keys[t.name], cols[t.name] = key, row
                if t.name == "live_session":
                    parents["session"] = key
            out[label] = {"org": org, "parents": parents, "keys": keys, "cols": cols}
    yield out
    with p.admin_engine.begin() as c:
        for label in ("a", "b"):
            ent = out[label]
            for name in ("notes_glossary", "copilot_config"):
                t = _BY_NAME[name]
                c.execute(text(f"DELETE FROM {name} WHERE {t.key} = :k"),
                          {"k": ent["keys"][name]})
            # Every other notes row cascades from its meeting.
            c.execute(text("DELETE FROM meeting WHERE id = ANY(:ids)"),
                      {"ids": [ent["parents"]["meeting"], ent["parents"]["spare"]]})


@pytest.fixture
def app_engine(promoted):  # noqa: F811
    """A fresh engine as the app role, with no pool.

    No pool, so each test gets a new connection. After a ``set_config(...,
    true)`` transaction the GUC reads ``''`` on the same connection, and
    ``''::uuid`` raises. A new connection reads NULL, so it fails closed.
    """
    eng = create_engine(promoted.app_url, future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        eng.dispose()


@contextmanager
def _bound(engine, org: str | None) -> Iterator[Any]:
    """A connection as the app role, bound to ``org``, always rolled back."""
    with engine.connect() as c:
        trans = c.begin()
        try:
            if org is not None:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": org})
            yield c
        finally:
            trans.rollback()


def _admin_row(promoted, name: str, key: str) -> Any:  # noqa: F811
    t = _BY_NAME[name]
    with promoted.admin_engine.connect() as c:
        return c.execute(
            text(f"SELECT organization_id::text FROM {name} WHERE {t.key}::text = :k"),
            {"k": key},
        ).first()


# ==========================================================================
# 1. Preconditions and the catalog guard.
# ==========================================================================
def test_the_app_role_really_cannot_bypass_rls(app_engine) -> None:
    """A superuser or a BYPASSRLS role makes every test below vacuous."""
    with app_engine.connect() as c:
        row = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert row is not None
    assert not row[0], "the suite connects as a SUPERUSER, so RLS is bypassed"
    assert not row[1], "the suite role has BYPASSRLS, so RLS is bypassed"


@pytest.mark.parametrize("name", _params())
def test_each_notes_table_forces_a_tenant_policy(promoted, name: str) -> None:  # noqa: F811
    """ENABLE + FORCE RLS, and a policy on ``app.tenant_id`` that has USING and
    WITH CHECK. Covers every table in ``NOTES_TABLES``."""
    with promoted.admin_engine.connect() as c:
        flags = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass(:t)"), {"t": f"public.{name}"}).first()
        policies = c.execute(text(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = :t"), {"t": name}).all()
        has_org = c.execute(text(
            "SELECT attnotnull FROM pg_attribute "
            "WHERE attrelid = to_regclass(:t) AND attname = 'organization_id' "
            "AND NOT attisdropped"), {"t": f"public.{name}"}).first()
    assert flags is not None, f"{name} is not in the promoted catalog"
    assert has_org is not None and has_org[0], (
        f"{name} has no NOT NULL organization_id")
    assert flags[0], f"{name} does not ENABLE row level security"
    assert flags[1], f"{name} does not FORCE row level security (the owner reads all)"
    tenant = [
        pol for pol in policies
        if "app.tenant_id" in (pol.qual or "")
        and "app.tenant_id" in (pol.with_check or "")
    ]
    assert tenant, (
        f"{name} has no policy on app.tenant_id with USING and WITH CHECK: "
        f"{[pol.policyname for pol in policies]}")


def test_each_child_of_a_notes_table_is_listed(promoted) -> None:  # noqa: F811
    """A table with a foreign key to a notes table is a notes table too.

    A new child of ``meeting`` that this suite does not list goes red here,
    so a notes table with no RLS cannot hide.
    """
    listed = {t.name for t in NOTES_TABLES}
    with promoted.admin_engine.connect() as c:
        children = {
            r[0]
            for r in c.execute(text(
                "SELECT DISTINCT cl.relname FROM pg_constraint con "
                "JOIN pg_class cl ON cl.oid = con.conrelid "
                "JOIN pg_class pa ON pa.oid = con.confrelid "
                "JOIN pg_namespace ns ON ns.oid = pa.relnamespace "
                "WHERE con.contype = 'f' AND ns.nspname = 'public' "
                "AND pa.relname = ANY(:names)"), {"names": sorted(listed)})
        }
    missing = sorted(children - listed - set(NOT_NOTES_CHILDREN))
    assert not missing, (
        f"these tables point at a notes table but this suite does not list "
        f"them: {missing}. Add each to NOTES_TABLES, or to NOT_NOTES_CHILDREN "
        f"with a reason.")


# ==========================================================================
# 2. Isolation, one case per table.
# ==========================================================================
@pytest.mark.parametrize("name", _params())
def test_a_bound_read_sees_only_its_own_row(app_engine, seeded, name: str) -> None:
    """Bound to org A: A's row and no foreign row. Bound to org B: the mirror."""
    t = _BY_NAME[name]
    for me, other in (("a", "b"), ("b", "a")):
        mine = seeded[me]["keys"][name]
        theirs = seeded[other]["keys"][name]
        with _bound(app_engine, seeded[me]["org"]) as c:
            rows = c.execute(text(
                f"SELECT {t.key}::text, organization_id::text FROM {name}")).all()
        keys = {r[0] for r in rows}
        orgs = {r[1] for r in rows}
        assert theirs not in keys, (
            f"{name}: org {me.upper()} read the row of org {other.upper()}")
        assert orgs <= {seeded[me]["org"]}, (
            f"{name}: org {me.upper()} read rows of other orgs: {orgs}")
        assert mine in keys, f"{name}: org {me.upper()} cannot read its own row"


@pytest.mark.parametrize("name", _params())
def test_a_read_by_the_other_tenants_key_returns_nothing(
        app_engine, seeded, name: str) -> None:
    """A direct read of B's row by its key, bound to org A, finds 0 rows."""
    t = _BY_NAME[name]
    for me, other in (("a", "b"), ("b", "a")):
        theirs = seeded[other]["keys"][name]
        with _bound(app_engine, seeded[me]["org"]) as c:
            n = c.execute(text(
                f"SELECT count(*) FROM {name} WHERE {t.key}::text = :k"),
                {"k": theirs}).scalar_one()
            by_org = c.execute(text(
                f"SELECT count(*) FROM {name} WHERE organization_id = :o"),
                {"o": seeded[other]["org"]}).scalar_one()
        assert n == 0, f"{name}: org {me.upper()} read B's row by its key"
        assert by_org == 0, (
            f"{name}: org {me.upper()} read rows of org {other.upper()} by org id")


@pytest.mark.parametrize("name", _params())
def test_a_write_stamped_with_the_other_tenant_is_refused(
        app_engine, seeded, promoted, name: str) -> None:  # noqa: F811
    """WITH CHECK: bound to org A, an INSERT stamped org B fails on RLS.

    The control first writes the same row stamped org A, and rolls it back. So
    the refusal comes from the org stamp, and not from a key or a foreign key.
    """
    t = _BY_NAME[name]
    a = seeded["a"]
    key = t.new_key("a", a["parents"])
    cols = t.row("a", a["parents"], key)
    with _bound(app_engine, a["org"]) as c:
        control = c.begin_nested()
        _insert(c, t, key, cols, a["org"])
        control.rollback()
        with pytest.raises(DBAPIError) as exc:
            _insert(c, t, key, cols, seeded["b"]["org"])
    assert "row-level security" in str(exc.value).lower(), (
        f"{name}: expected a WITH CHECK refusal, got: {exc.value}")
    assert _admin_row(promoted, name, key) is None, f"{name}: the refused row landed"


@pytest.mark.parametrize("name", _params())
def test_a_change_of_the_other_tenants_row_touches_nothing(
        app_engine, seeded, promoted, name: str) -> None:  # noqa: F811
    """Bound to org A, an UPDATE and a DELETE of B's row by key touch 0 rows,
    and B's row stays with org B."""
    t = _BY_NAME[name]
    theirs = seeded["b"]["keys"][name]
    with _bound(app_engine, seeded["a"]["org"]) as c:
        upd = c.execute(text(
            f"UPDATE {name} SET organization_id = organization_id "
            f"WHERE {t.key}::text = :k"), {"k": theirs}).rowcount
        moved = c.execute(text(
            f"UPDATE {name} SET organization_id = :a WHERE {t.key}::text = :k"),
            {"a": seeded["a"]["org"], "k": theirs}).rowcount
        gone = c.execute(text(
            f"DELETE FROM {name} WHERE {t.key}::text = :k"), {"k": theirs}).rowcount
    assert (upd, moved, gone) == (0, 0, 0), (
        f"{name}: org A changed B's row (update {upd}, move {moved}, delete {gone})")
    row = _admin_row(promoted, name, theirs)
    assert row is not None and row[0] == seeded["b"]["org"], (
        f"{name}: B's row is gone or moved: {row}")


@pytest.mark.parametrize("name", _params())
def test_an_unbound_session_sees_zero_rows(app_engine, seeded, name: str) -> None:
    """Fail closed: with no ``app.tenant_id`` the GUC is NULL, so no row."""
    with _bound(app_engine, None) as c:
        n = c.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()
    assert n == 0, f"{name}: an UNBOUND session read {n} rows (not fail closed)"


# ==========================================================================
# 3. The same natural key in two orgs (a key over all orgs, not per org).
# ==========================================================================
_GLOBAL_KEY = (
    "the unique key is over all orgs, not per org. A second org cannot hold "
    "the same {what}, and the unique error tells it that another org holds it"
)


@pytest.mark.parametrize("name", [
    pytest.param("copilot_config", id="copilot_config", marks=pytest.mark.xfail(
        strict=True,
        reason="copilot_config: PRIMARY KEY (owner_email); "
        + _GLOBAL_KEY.format(what="owner_email"))),
    pytest.param("notes_glossary", id="notes_glossary", marks=pytest.mark.xfail(
        strict=True,
        reason="notes_glossary: UNIQUE (user_id, lower(term)); "
        + _GLOBAL_KEY.format(what="user and term"))),
])
def test_the_same_natural_key_in_two_orgs_does_not_clash(
        app_engine, seeded, name: str) -> None:
    """Bound to org B, a row with the natural key of A's row is a new row."""
    t = _BY_NAME[name]
    _a_key, a_cols = _row_for(seeded, "a", name)
    b = seeded["b"]
    if name == "copilot_config":
        key, cols = seeded["a"]["keys"][name], {"instructions": f"r8-{TAG}-b"}
    else:
        key, cols = str(uuid.uuid4()), dict(a_cols)
    with _bound(app_engine, b["org"]) as c:
        _insert(c, t, key, cols, b["org"])


# ==========================================================================
# 4. The suite is armed: CI names it, so a missing DSN cannot hide a skip.
# ==========================================================================
def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    assert "tests/unit/test_notes_tenant_isolation_r8.py" in r8_lists.must_run()
