"""R8 — the number system, stored and resolved on a REAL Postgres.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.10. Migration
284 adds ``user_settings.number_system``. The org's value is the
``org_settings`` key ``number_system``. ``routes/settings.py`` reads both
with ``acb_common.number_system.READ_SQL``, the same SQL the chat's per-turn
context runs (``executor._read_number_system``).

What this proves, as the NON-privileged role of the phase-4 rehearsal
(``acb_app_h3rls``: no superuser, no BYPASSRLS), on a database with the whole
ladder and two organizations:

- the column is nullable with no default (R6), and its CHECK refuses a third
  system;
- an org with nothing stored resolves to "indian";
- an admin's org value reaches a member with no override, and never the other
  organization;
- a member's override wins over the org's value, and clearing it gives the
  org's value back;
- ``READ_SQL`` reads one organization's values and never another's.

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database.
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import number_system as ns
from acb_common import org_settings
from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE


def _address() -> str:
    return f"num.{uuid.uuid4().hex[:8]}@numsys.test"


@pytest.fixture
def db(promoted, monkeypatch: pytest.MonkeyPatch):  # noqa: F811 — a fixture
    """The promoted catalog. `org_settings` writes as the app role. This
    module's rows go after each test."""
    dsn = promoted.app_url.set(drivername="postgresql").render_as_string(hide_password=False)
    monkeypatch.setattr(org_settings, "_conninfo", lambda: dsn)
    made: list[str] = []
    promoted.made = made
    ns.forget()
    yield promoted
    ns.forget()
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_settings WHERE key = 'number_system'"))
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _app_dsn(ns_) -> str:
    return ns_.app_url.render_as_string(hide_password=False)


def _user(email: str, *, admin: bool = False) -> UserContext:
    grants = ["feature:tasks", "admin:settings:manage"] if admin else ["feature:tasks"]
    return UserContext(email=email, role=UserRole.EXECUTIVE if admin else UserRole.EMPLOYEE,
                       access=build_access(grants, roles=["admin" if admin else "member"]))


async def _get(db, org: str, user: UserContext):
    from gateway.routes.settings import get_number_system

    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(org):
            return await get_number_system(user=user)


async def _put_org(db, org: str, user: UserContext, value: str):
    from gateway.routes.settings import OrgNumberSystemPut, put_org_number_system

    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(org):
            return await put_org_number_system(body=OrgNumberSystemPut(value=value), user=user)


async def _put_me(db, org: str, user: UserContext, value: str | None):
    from gateway.routes.settings import MemberNumberSystemPut, put_member_number_system

    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(org):
            return await put_member_number_system(body=MemberNumberSystemPut(value=value), user=user)


# ── The migration (R6) ──────────────────────────────────────────────────────


def test_the_column_is_nullable_with_no_default(db) -> None:
    with db.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT is_nullable, column_default, data_type FROM information_schema.columns "
            "WHERE table_name = 'user_settings' AND column_name = 'number_system'")).first()
    assert row is not None, "migration 284 did not add user_settings.number_system"
    assert (row[0], row[1], row[2]) == ("YES", None, "text")


def test_the_check_refuses_a_third_system(db) -> None:
    email = _address()
    db.made.append(email)
    with (pytest.raises(Exception, match="user_settings_number_system_check"),
          db.admin_engine.begin() as c):
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, number_system) "
            "VALUES (CAST(:o AS uuid), :u, 'roman')"), {"o": db.org_a, "u": email})


# ── The settings round trip ─────────────────────────────────────────────────


async def test_nothing_stored_resolves_to_indian(db) -> None:
    got = await _get(db, db.org_a, _user(_address()))
    assert (got.org, got.member, got.resolved, got.default) == (None, None, "indian", "indian")


async def test_the_org_value_reaches_a_member_and_stays_in_its_org(db) -> None:
    admin, member = _address(), _address()
    db.made += [admin, member]
    saved = await _put_org(db, db.org_a, _user(admin, admin=True), "international")
    assert (saved.org, saved.resolved) == ("international", "international")
    got = await _get(db, db.org_a, _user(member))
    assert (got.org, got.member, got.resolved) == ("international", None, "international")
    other = await _get(db, db.org_b, _user(member))
    assert (other.org, other.resolved) == (None, "indian")


async def test_the_member_override_wins_and_clearing_it_gives_the_org_value(db) -> None:
    admin, member = _address(), _address()
    db.made += [admin, member]
    await _put_org(db, db.org_a, _user(admin, admin=True), "international")
    mine = await _put_me(db, db.org_a, _user(member), "indian")
    assert (mine.org, mine.member, mine.resolved) == ("international", "indian", "indian")
    # Another member of the same org still gets the org's value.
    assert (await _get(db, db.org_a, _user(admin))).resolved == "international"
    cleared = await _put_me(db, db.org_a, _user(member), None)
    assert (cleared.member, cleared.resolved) == (None, "international")
    with db.admin_engine.connect() as c:
        stored = c.execute(text(
            "SELECT organization_id::text, number_system FROM user_settings WHERE user_id = :u"),
            {"u": member}).all()
    assert [tuple(r) for r in stored] == [(db.org_a, None)]


async def test_an_override_in_one_org_never_reaches_the_other(db) -> None:
    member = _address()
    db.made.append(member)
    await _put_me(db, db.org_a, _user(member), "international")
    assert (await _get(db, db.org_b, _user(member))).resolved == "indian"


# ── READ_SQL, the chat's read too ───────────────────────────────────────────


def test_read_sql_reads_its_own_org_only(db, app_engine) -> None:  # noqa: F811
    member = _address()
    db.made.append(member)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO org_settings (organization_id, key, value) "
            "VALUES (CAST(:o AS uuid), 'number_system', CAST(:v AS jsonb))"),
            {"o": db.org_a, "v": '{"value": "international"}'})
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, number_system) "
            "VALUES (CAST(:o AS uuid), :u, 'indian')"), {"o": db.org_a, "u": member})

    def read(bound: str, named: str):
        with app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": bound})
            return c.execute(text(ns.READ_SQL), {"org": named, "uid": member}).first()

    row = read(db.org_a, db.org_a)
    assert ns.clean(row[0]) == "international" and row[1] == "indian"
    assert ns.resolve(row[0], row[1]) == "indian"
    # The other org's tenant sees nothing, whichever org the SQL names.
    for named in (db.org_a, db.org_b):
        row = read(db.org_b, named)
        assert (row[0], row[1]) == (None, None)


# ── WS-47 follow-up of #1154: the member in any case ────────────────────────


def _rows_of(db, address: str) -> list[tuple[str, str | None]]:
    with db.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT user_id, number_system FROM user_settings "
            "WHERE organization_id = CAST(:o AS uuid) AND lower(user_id) = lower(:u) "
            "ORDER BY user_id"), {"o": db.org_a, "u": address}).all()]


async def test_a_mixed_case_row_is_read_and_a_save_changes_it_in_place(db) -> None:
    """The gateway once wrote the session's address as it came. The run
    reads its member in lower case, so it missed that row."""
    stored = f"Num.{uuid.uuid4().hex[:6].upper()}@NumSys.Test"
    lower = stored.lower()
    db.made += [stored, lower]
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, number_system) "
            "VALUES (CAST(:o AS uuid), :u, 'international')"), {"o": db.org_a, "u": stored})
    # The run's read: the member in lower case.
    assert (await _get(db, db.org_a, _user(lower))).member == "international"
    assert (await _get(db, db.org_a, _user(stored))).member == "international"
    saved = await _put_me(db, db.org_a, _user(lower), "indian")
    assert (saved.member, saved.resolved) == ("indian", "indian")
    assert _rows_of(db, stored) == [(stored, "indian")], "a save added a second row"


async def test_a_new_row_holds_the_address_in_lower_case(db) -> None:
    given = f"New.{uuid.uuid4().hex[:6].upper()}@NumSys.Test"
    db.made += [given, given.lower()]
    await _put_me(db, db.org_a, _user(given), "international")
    assert _rows_of(db, given) == [(given.lower(), "international")]


def test_of_two_rows_of_one_member_the_one_with_a_value_wins(db, app_engine) -> None:  # noqa: F811
    stored = f"Two.{uuid.uuid4().hex[:6].upper()}@NumSys.Test"
    db.made += [stored, stored.lower()]
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, number_system) "
            "VALUES (CAST(:o AS uuid), :u, 'international'), (CAST(:o AS uuid), :l, NULL)"),
            {"o": db.org_a, "u": stored, "l": stored.lower()})
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": db.org_a})
        row = c.execute(text(ns.READ_SQL), {"org": db.org_a, "uid": stored.lower()}).first()
        found = c.execute(text(ns.ROW_SQL), {"org": db.org_a, "uid": stored.lower()}).first()
    assert row[1] == "international" and found[0] == stored
