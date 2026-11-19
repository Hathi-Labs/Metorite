"""R8: the store of the model of a connection (WS-SEM SEM-12, task 3742).

Spec: ``project-docs/specs/semantic_model.md`` §10.4 and §14. Module:
``gateway/connections/semantic_model.py``. Migration:
``infra/postgres/<n>_connection_semantic_models.sql``, found by its content.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the table: its columns, FORCE RLS, its policy and its partial unique
   index. A second run of the migration changes nothing;
2. a draft takes the next version, the org of the bound tenant, and
   ``made_by`` from the body. Two concurrent drafts get two numbers;
3. org B cannot read a row of org A, and the policy refuses a row stamped
   with org A from a session of org B;
4. org B writes a draft for a connection of org A, and the writer refuses
   it with the refusal of a missing connection. No row exists in either org;
5. a delete of the connection removes its versions;
6. a second ``approved`` row for one connection fails. A draft with
   ``approved_at`` set fails the CHECK;
7. ``approve`` leaves exactly one approved row and archives the old one,
   in one transaction: when its second step fails, the first is undone;
8. ``accept_part`` changes one part of a draft only, and runs ``shape_only``;
9. with the flag off a write refuses with ``off`` and writes no row;
10. (SEM-12b) a body over the cap in jsonb text bytes refuses with
    ``too_big`` and writes no row, and a body at the cap is written with
    ``octet_length(body::text)`` equal to the count of ``body_bytes``.

A hermetic fake agrees with any SQL that it gets, so none of this could be
proved without the database.

Mutations, each red here: drop the partial unique index (case 6, the
second approved row). Drop the policy (case 3, the read of org B). Drop the
connection select ``_LOCK_SQL`` AND the ``FROM integration_connections`` of
``_INSERT_SQL`` (case 4: the insert names the connection by its id alone,
and the foreign key check skips RLS). A drop of only one of the two keeps
case 4 green, because each one guards the other. Swap the two steps of
``approve`` (case 7, the index trips). Count the compact JSON in
``body_bytes`` (case 10: the CHECK of the migration trips, not ``too_big``).

Not a fence here: a drop of ``FORCE`` in the migration. The template of this
suite applies ``generated/04``, which forces RLS again, and the app role does
not own the table. The fence of ``FORCE`` is
``test_semantic_shape_only.py::test_the_migration_holds_the_shape_of_section_10_4``.
Case 2 (two concurrent drafts) shows that the lock orders the writers. A drop
of the lock does not turn it red on each run, because the race window is
short.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS connection_semantic_models ("
_HASH = "0123456789abcdef" * 4


def _migration() -> Path:
    """The migration of the table, found by its content and not its number."""
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _body(label: str = "Sales vouchers", admin: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "v": 1,
        "code": {"entities": [{"key": "Sales", "label": label, "records": 12}]},
        "ai": {},
        "admin": {},
    }
    if admin:
        body["admin"] = {"instructions": [{"text": "Revenue is billed, not draft."}]}
    return body


@pytest.fixture
def db(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The world with the flag on, and each connection that a test makes
    removed after it (the cascade takes its versions)."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "connection_model_orgs", "*", raising=False)
    made: list[str] = []
    promoted.made = made
    try:
        yield promoted
    finally:
        with promoted.admin_engine.begin() as c:
            for cid in made:
                c.execute(text("DELETE FROM integration_connections WHERE id = CAST(:c AS uuid)"),
                          {"c": cid})


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _connection(ns, org: str) -> str:
    """One MCP connection of *org*, seeded as the superuser."""
    with ns.admin_engine.begin() as c:
        cid = c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app) "
            "VALUES (CAST(:o AS uuid), :p, 'studio') RETURNING id::text"),
            {"o": org, "p": f"mcp-sem12-{uuid.uuid4().hex[:10]}"}).scalar_one()
    ns.made.append(cid)
    return cid


def _rows(ns, cid: str) -> list[tuple]:
    """Each version of *cid*, in any org, as the superuser."""
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, version, state, made_by, approved_role, "
            "approved_at IS NOT NULL FROM connection_semantic_models "
            "WHERE connection_id = CAST(:c AS uuid) ORDER BY version"), {"c": cid})]


async def _call(ns, org: str, fn: str, *args: Any) -> Any:
    """Call one function of the writer in a session bound to *org*, as the app
    role."""
    from acb_common.db import tenant_session
    from gateway.connections import semantic_model as sm

    async with tenant_engine_scope(_app_dsn(ns)), tenant_session(org) as s:
        return await getattr(sm, fn)(s, *args)


async def _refused(ns, org: str, fn: str, *args: Any) -> str:
    from gateway.connections.semantic_model import ModelRefused

    with pytest.raises(ModelRefused) as err:
        await _call(ns, org, fn, *args)
    return str(err.value)


# ── 1. The table ────────────────────────────────────────────────────────────


def test_1_the_table_has_its_columns_force_rls_and_one_approved_index(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'connection_semantic_models'"))}
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'connection_semantic_models'::regclass")).first()
        policy = c.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'connection_semantic_models'")).first()
        index = c.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'connection_semantic_models_one_approved'")).scalar()
        fk = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'connection_semantic_models'::regclass AND contype = 'f' "
            "AND pg_get_constraintdef(oid) LIKE '%integration_connections%'")).scalar()
    assert set(cols) == {
        "id", "organization_id", "connection_id", "version", "state", "body",
        "shape_hash", "made_by", "approved_role", "approved_at", "untrusted_origin",
        "created_at", "updated_at"}
    assert cols["approved_at"] == ("timestamp with time zone", "YES")
    assert cols["organization_id"][1] == "NO"
    assert tuple(rls) == (True, True)
    assert "app.tenant_id" in policy.qual and "app.tenant_id" in policy.with_check
    assert index is not None and "UNIQUE" in index and "'approved'" in index
    assert fk is not None and "ON DELETE CASCADE" in fk


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_table_has_its_columns_force_rls_and_one_approved_index(db)


# ── 2. A draft ──────────────────────────────────────────────────────────────


async def test_2_a_draft_takes_the_next_version_and_the_bound_org(db) -> None:
    cid = _connection(db, db.org_a)
    assert await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False) == 1
    assert await _call(db, db.org_a, "save_draft", cid, _body(admin=True), _HASH, True) == 2
    assert _rows(db, cid) == [(db.org_a, 1, "draft", "code", None, False),
                              (db.org_a, 2, "draft", "admin", None, False)]
    # No reader returns a draft.
    assert await _call(db, db.org_a, "read_approved", cid) is None


async def test_2_two_concurrent_drafts_get_two_numbers(db) -> None:
    """Mutation: drop the lock of the connection row, and two drafts read the
    same highest version. One then fails the unique key."""
    from acb_common.db import tenant_session
    from gateway.connections import semantic_model as sm

    cid = _connection(db, db.org_a)

    async def one() -> int:
        async with tenant_session(db.org_a) as s:
            version = await sm.save_draft(s, cid, _body(), _HASH, False)
            await asyncio.sleep(0.05)  # hold the lock, so the others wait
            return version

    async with tenant_engine_scope(_app_dsn(db)):
        got = await asyncio.gather(*(one() for _ in range(4)))
    assert sorted(got) == [1, 2, 3, 4]
    assert [r[1] for r in _rows(db, cid)] == [1, 2, 3, 4]


async def test_2_two_concurrent_approves_leave_one_approved_row(db) -> None:
    """Two admins approve two drafts of one connection at once. The lock of
    the connection orders them, so one approved row stays, and the other
    version is archived. Mutation: drop the archive step of ``approve``, and
    the second approve trips the partial unique index."""
    from acb_common.db import tenant_session
    from gateway.connections import semantic_model as sm

    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    await _call(db, db.org_a, "save_draft", cid, _body("Sales register"), _HASH, False)

    async def one(version: int) -> None:
        async with tenant_session(db.org_a) as s:
            await sm.approve(s, cid, version, "admin")
            await asyncio.sleep(0.05)  # hold the lock, so the other waits

    async with tenant_engine_scope(_app_dsn(db)):
        await asyncio.gather(one(1), one(2))
    states = sorted(r[2] for r in _rows(db, cid))
    assert states == ["approved", "archived"]


# ── 3. Org B reads nothing of org A ─────────────────────────────────────────


async def test_3_org_b_cannot_read_a_row_of_org_a(db) -> None:
    """Mutation: drop the policy, and org B reads the row of org A. A drop of
    ``FORCE`` in the migration does not turn this suite red (see the module
    docstring). Its fence is the hermetic migration-shape test."""
    from acb_common.db import tenant_session

    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    await _call(db, db.org_a, "approve", cid, 1, "admin")
    assert (await _call(db, db.org_a, "read_approved", cid)).version == 1
    assert await _call(db, db.org_b, "read_approved", cid) is None
    async with tenant_engine_scope(_app_dsn(db)):
        async with tenant_session(db.org_b) as s:
            seen = (await s.execute(text(
                "SELECT count(*) FROM connection_semantic_models "
                "WHERE connection_id = CAST(:c AS uuid)"), {"c": cid})).scalar()
        assert seen == 0
        # The policy refuses a row stamped with org A, from a session of org B.
        with pytest.raises(DBAPIError):
            async with tenant_session(db.org_b) as s:
                await s.execute(text(
                    "INSERT INTO connection_semantic_models (organization_id, "
                    "connection_id, version, body, shape_hash, made_by) VALUES "
                    "(CAST(:o AS uuid), CAST(:c AS uuid), 9, '{}'::jsonb, :h, 'code')"),
                    {"o": db.org_a, "c": cid, "h": _HASH})


# ── 4. Org B cannot name the connection of org A ────────────────────────────


async def test_4_org_b_cannot_write_a_draft_for_a_connection_of_org_a(db) -> None:
    """The foreign key check skips RLS, so only the select under the bound
    tenant stops this write."""
    cid = _connection(db, db.org_a)
    assert await _refused(db, db.org_b, "save_draft", cid, _body(), _HASH, False) == (
        "connection")
    # A missing connection gets the same refusal, so the refusal leaks nothing.
    missing = str(uuid.uuid4())
    assert await _refused(db, db.org_b, "save_draft", missing, _body(), _HASH, False) == (
        "connection")
    assert await _refused(db, db.org_b, "save_draft", "not-a-uuid", _body(), _HASH,
                          False) == "connection"
    assert _rows(db, cid) == []
    with db.admin_engine.connect() as c:
        for org in (db.org_a, db.org_b):
            assert c.execute(text(
                "SELECT count(*) FROM connection_semantic_models "
                "WHERE organization_id = CAST(:o AS uuid) "
                "AND connection_id = CAST(:c AS uuid)"), {"o": org, "c": cid}).scalar() == 0


async def test_4_org_b_cannot_accept_or_approve_a_draft_of_org_a(db) -> None:
    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    assert await _refused(db, db.org_b, "approve", cid, 1, "admin") == "connection"
    assert await _refused(db, db.org_b, "accept_part", cid, 1, "admin", "instructions",
                          [{"text": "Use billed revenue."}]) == "connection"
    assert _rows(db, cid) == [(db.org_a, 1, "draft", "code", None, False)]


# ── 5. The cascade ──────────────────────────────────────────────────────────


async def test_5_a_delete_of_the_connection_removes_its_versions(db) -> None:
    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    assert len(_rows(db, cid)) == 2
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE id = CAST(:c AS uuid)"),
                  {"c": cid})
    assert _rows(db, cid) == []


# ── 6. The constraints ──────────────────────────────────────────────────────


def _insert(c, org: str, cid: str, version: int, state: str, role: str | None,
            at: bool) -> None:
    c.execute(text(
        "INSERT INTO connection_semantic_models (organization_id, connection_id, version, "
        "state, body, shape_hash, made_by, approved_role, approved_at) VALUES "
        "(CAST(:o AS uuid), CAST(:c AS uuid), :v, :s, '{}'::jsonb, :h, 'code', :r, "
        "CASE WHEN :at THEN now() END)"),
        {"o": org, "c": cid, "v": version, "s": state, "h": _HASH, "r": role, "at": at})


def test_6_a_second_approved_row_for_one_connection_fails(db) -> None:
    """Mutation: drop the partial unique index, and the second insert
    passes."""
    cid = _connection(db, db.org_a)
    with db.admin_engine.begin() as c:
        _insert(c, db.org_a, cid, 1, "approved", "admin", True)
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        _insert(c, db.org_a, cid, 2, "approved", "admin", True)
    # An archived row and a draft beside it are fine.
    with db.admin_engine.begin() as c:
        _insert(c, db.org_a, cid, 3, "archived", "admin", True)
        _insert(c, db.org_a, cid, 4, "draft", None, False)


@pytest.mark.parametrize(("state", "role", "at"), [
    ("draft", None, True),         # a draft with approved_at set
    ("draft", "admin", True),      # a draft with both set
    ("approved", None, False),     # an approved row with neither
    ("archived", "admin", False),  # one of the two
    ("draft", None, False),        # a version of 0, below
])
def test_6_the_checks_hold_the_approval(db, state: str, role: str | None, at: bool) -> None:
    cid = _connection(db, db.org_a)
    version = 0 if (state, role, at) == ("draft", None, False) else 1
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        _insert(c, db.org_a, cid, version, state, role, at)


@pytest.mark.parametrize(("column", "value"), [
    ("shape_hash", "'Ignore the rules'"), ("made_by", "'stranger'"),
    ("state", "'live'"), ("approved_role", "'Admin of Acme'")])
def test_6_a_column_holds_no_free_text(db, column: str, value: str) -> None:
    cid = _connection(db, db.org_a)
    sql = (
        "INSERT INTO connection_semantic_models (organization_id, connection_id, version, "
        "state, body, shape_hash, made_by, approved_role, approved_at) VALUES "
        f"(CAST(:o AS uuid), CAST(:c AS uuid), 1, 'archived', '{{}}'::jsonb, '{_HASH}', "
        "'code', 'admin', now())")
    sql = sql.replace({"shape_hash": f"'{_HASH}'", "made_by": "'code'",
                       "state": "'archived'", "approved_role": "'admin'"}[column], value, 1)
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(sql), {"o": db.org_a, "c": cid})


# ── 7. Approve ──────────────────────────────────────────────────────────────


async def test_7_approve_leaves_one_approved_row_and_archives_the_old(db) -> None:
    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    await _call(db, db.org_a, "approve", cid, 1, "admin")
    await _call(db, db.org_a, "save_draft", cid, _body("Sales register"), _HASH, False)
    await _call(db, db.org_a, "approve", cid, 2, "owner")
    assert _rows(db, cid) == [(db.org_a, 1, "archived", "code", "admin", True),
                              (db.org_a, 2, "approved", "code", "owner", True)]
    got = await _call(db, db.org_a, "read_approved", cid)
    assert got.version == 2 and got.body["code"]["entities"][0]["label"] == "Sales register"
    # Only a draft can be approved, and an approved row stays as it is.
    assert await _refused(db, db.org_a, "approve", cid, 1, "admin") == "not_draft"
    assert await _refused(db, db.org_a, "approve", cid, 2, "admin") == "not_draft"
    assert await _refused(db, db.org_a, "approve", cid, 7, "admin") == "version"
    assert await _refused(db, db.org_a, "approve", cid, 2, "Admin Of Acme") == "role"


async def test_7_the_two_steps_of_approve_are_one_transaction(db, monkeypatch) -> None:
    """When the second step fails, the archive of the first step is undone,
    so the old version stays approved."""
    from gateway.connections import semantic_model as sm

    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    await _call(db, db.org_a, "approve", cid, 1, "admin")
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    monkeypatch.setattr(sm, "_APPROVE_SQL", sm._APPROVE_SQL + " AND false")
    assert await _refused(db, db.org_a, "approve", cid, 2, "admin") == "not_draft"
    assert [r[2] for r in _rows(db, cid)] == ["approved", "draft"]


# ── 8. Accept a part ────────────────────────────────────────────────────────


async def test_8_accept_part_changes_one_part_of_a_draft(db) -> None:
    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    body = await _call(db, db.org_a, "accept_part", cid, 1, "admin", "instructions",
                       [{"text": "Revenue is billed, not draft."}])
    assert body["admin"] == {"instructions": [{"text": "Revenue is billed, not draft."}]}
    assert _rows(db, cid)[0][3] == "admin"
    # shape_only runs on the new body: a figure in the admin's words is refused.
    assert await _refused(db, db.org_a, "accept_part", cid, 1, "admin", "instructions",
                          [{"text": "Revenue is 5000."}]) == (
        "shape:admin.instructions:text_value")
    # A part of another section, and a part that is not in the closed set.
    assert await _refused(db, db.org_a, "accept_part", cid, 1, "code", "measures",
                          []) == "part"
    # Remove the part again.
    body = await _call(db, db.org_a, "accept_part", cid, 1, "admin", "instructions", None)
    assert body["admin"] == {} and _rows(db, cid)[0][3] == "code"
    await _call(db, db.org_a, "approve", cid, 1, "admin")
    assert await _refused(db, db.org_a, "accept_part", cid, 1, "admin", "instructions",
                          [{"text": "Too late."}]) == "not_draft"


async def test_8_a_body_that_is_not_shape_only_writes_no_row(db) -> None:
    cid = _connection(db, db.org_a)
    bad = _body()
    bad["code"]["entities"][0]["balance"] = 125000
    assert await _refused(db, db.org_a, "save_draft", cid, bad, _HASH, False) == (
        "shape:figure")
    assert _rows(db, cid) == []


# ── 9. The flag ─────────────────────────────────────────────────────────────


async def test_9_with_the_flag_off_a_write_refuses_off(db, monkeypatch) -> None:
    from acb_common import get_settings

    cid = _connection(db, db.org_a)
    await _call(db, db.org_a, "save_draft", cid, _body(), _HASH, False)
    await _call(db, db.org_a, "approve", cid, 1, "admin")
    monkeypatch.setattr(get_settings(), "connection_model_orgs", db.org_b, raising=False)
    assert await _refused(db, db.org_a, "save_draft", cid, _body(), _HASH, False) == "off"
    assert await _refused(db, db.org_a, "approve", cid, 1, "admin") == "off"
    assert await _refused(db, db.org_a, "accept_part", cid, 1, "admin", "instructions",
                          None) == "off"
    assert await _call(db, db.org_a, "read_approved", cid) is None
    assert len(_rows(db, cid)) == 1


# ── 10. The size cap agrees with the CHECK (SEM-12b) ────────────────────────


def _word(n: int) -> str:
    """A run of letters for *n*: a key and an option hold no digit."""
    out = ""
    while True:
        out = chr(ord("a") + n % 26) + out
        n //= 26
        if n == 0:
            return out


def _sized_body(target: int) -> dict[str, Any]:
    """A shape-only body whose jsonb text is exactly *target* bytes. It
    fills ``code.fields`` with options of 80 letters, then pads the label
    of the last field."""
    from gateway.connections.semantic_model import body_bytes

    fields: list[dict[str, Any]] = []
    body: dict[str, Any] = {"v": 1, "code": {
        "entities": [{"key": "Sales", "label": "Sales vouchers"}], "fields": fields},
        "ai": {}, "admin": {}}
    while True:
        field: dict[str, Any] = {"entity": "Sales", "key": "Fld" + _word(len(fields)),
                                 "label": "Ledger", "options": []}
        fields.append(field)
        while len(field["options"]) < 100:
            head = "Opt " + _word(len(field["options"])) + " "
            option = head + "a" * (80 - len(head))
            field["options"].append(option)
            if body_bytes(body) > target:
                # Take the last option back. When the room left is more than
                # a label pad, add a shorter option. Then pad the label.
                field["options"].pop()
                room = target - body_bytes(body)
                if room > 70:
                    field["options"].append(option[:room - 34])
                pad = target - body_bytes(body)
                assert 0 <= pad <= 70, pad
                field["label"] = "Ledger" + "s" * pad
                assert body_bytes(body) == target
                return body


async def test_10_a_body_over_the_cap_in_jsonb_text_is_refused_too_big(db) -> None:
    """The compact JSON is under the cap, and the jsonb text is over it.
    Mutation: count the compact JSON in ``body_bytes``, and the INSERT
    raises the CHECK of the migration, not ``too_big``."""
    import json

    from gateway.connections.semantic_model import BODY_BYTES_MAX
    from gateway.crm_sync.catalogue import shape_only

    body = _sized_body(BODY_BYTES_MAX + 1)
    assert shape_only(body) is None
    compact = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(compact) < BODY_BYTES_MAX
    cid = _connection(db, db.org_a)
    assert await _refused(db, db.org_a, "save_draft", cid, body, _HASH, False) == "too_big"
    assert _rows(db, cid) == []


async def test_10_a_body_at_the_cap_is_written_and_the_check_agrees(db) -> None:
    """The Python count and ``octet_length(body::text)`` are the same number,
    so the CHECK of the migration and ``too_big`` meet at one cap."""
    from gateway.connections.semantic_model import BODY_BYTES_MAX

    for target in (BODY_BYTES_MAX, BODY_BYTES_MAX - 1):
        body = _sized_body(target)
        cid = _connection(db, db.org_a)
        assert await _call(db, db.org_a, "save_draft", cid, body, _HASH, False) == 1
        with db.admin_engine.connect() as c:
            size = c.execute(text(
                "SELECT octet_length(body::text) FROM connection_semantic_models "
                "WHERE connection_id = CAST(:c AS uuid)"), {"c": cid}).scalar_one()
        assert size == target <= BODY_BYTES_MAX


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_connection_semantic_models\.sql", _migration().name)
