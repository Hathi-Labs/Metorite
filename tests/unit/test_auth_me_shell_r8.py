"""R8 — the member's shell layout, ``GET`` and ``PUT /auth/me/shell`` (NS-7).

Migration 240 adds ``user_settings.shell_prefs``. The two routes in
``routes/admin/me.py`` read and write it through the tenant session. Spec:
``navigation_shell.md`` §8.2.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a
database with the whole ladder (so 239 and 240) and two organizations:

- one address saves a layout in each of its two organizations, and each
  organization reads back its own;
- a guest with no feature saves a pin (done-when 2);
- "Skip for now" is stored as a choice, so the shell does not ask again;
- a ``PUT`` of null resets the member to the preset and keeps the row;
- the first write keeps the calendar's People seed (D-PC-16), because
  ``/tasks/settings`` reads "no row" as "seed from the work schedule";
- the column is nullable with no default (R6).

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database.
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
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
    return f"shell.{uuid.uuid4().hex[:8]}@ns7.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    """The promoted catalog, with this module's rows removed after."""
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
            c.execute(text("DELETE FROM people WHERE email = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _user(email: str, roles: list[str], perms: list[str]) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms, roles=roles),
    )


def _member(email: str) -> UserContext:
    return _user(email, ["member"], ["feature:tasks", "feature:projects"])


def _guest(email: str) -> UserContext:
    # No feature at all, so `/tasks/settings` would refuse this caller.
    return _user(email, ["guest"], [])


async def _put(org: str, user: UserContext, body: dict | None) -> dict:
    from gateway.routes.admin.me import put_my_shell

    with _tenant(org):
        return await put_my_shell(body=body, user=user)


async def _get(org: str, user: UserContext) -> dict:
    from gateway.routes.admin.me import get_my_shell

    with _tenant(org):
        return await get_my_shell(user=user)


def _stored(ns, email: str) -> list[tuple[str, object]]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, shell_prefs FROM user_settings "
            "WHERE user_id = :u ORDER BY 1"), {"u": email})]


# ── One address, two organizations ─────────────────────────────────────────


async def test_one_address_keeps_a_layout_in_each_organization(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                    "pins": ["/projects", "/tasks"]})
        await _put(db.org_b, user, {"preset": "founder", "answered": "answered",
                                    "pins": ["/email"]})
        a = await _get(db.org_a, user)
        b = await _get(db.org_b, user)
    assert (a["preset"], a["pins"]) == ("engineer", ["/projects", "/tasks"])
    assert (b["preset"], b["pins"]) == ("founder", ["/email"])
    rows = _stored(db, email)
    assert [org for org, _ in rows] == sorted([db.org_a, db.org_b])


async def test_another_organization_never_reads_the_layout(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered"})
        b = await _get(db.org_b, user)
    # Org B never asked this member, so the shell asks there too.
    assert b == {"preset": None, "answered": None, "pins": None,
                 "cardOrder": None, "newOrder": None}


# ── The guest, the skip and the reset ──────────────────────────────────────


async def test_a_guest_with_no_feature_saves_a_pin(db) -> None:
    email = _address()
    db.made.append(email)
    guest = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        saved = await _put(db.org_a, guest, {"pins": ["/chat"]})
        read = await _get(db.org_a, guest)
    assert saved["pins"] == ["/chat"]
    assert read["pins"] == ["/chat"]
    assert read["answered"] is None


async def test_skip_is_stored_as_a_choice(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        assert (await _get(db.org_a, user))["answered"] is None
        await _put(db.org_a, user, {"answered": "skipped"})
        read = await _get(db.org_a, user)
    assert read["answered"] == "skipped"
    assert read["preset"] is None


async def test_a_put_of_null_resets_and_keeps_the_row(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered"})
        await _put(db.org_a, user, None)
        read = await _get(db.org_a, user)
    assert read["answered"] is None
    assert _stored(db, email) == [(db.org_a, None)]


async def test_a_second_save_changes_only_the_layout(db) -> None:
    from gateway.routes.tasks.settings import UserSettingsPatch, put_user_settings

    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            await put_user_settings(
                patch=UserSettingsPatch(timezone="Asia/Kolkata"),
                user=user)
        await _put(db.org_a, user, {"pins": ["/calendar"]})
    with db.admin_engine.connect() as c:
        zone = c.execute(text(
            "SELECT timezone FROM user_settings WHERE user_id = :u"), {"u": email}).scalar()
    assert zone == "Asia/Kolkata"


# ── The calendar seed survives the first write ─────────────────────────────


async def test_the_first_write_keeps_the_calendar_seed(db) -> None:
    """``/tasks/settings`` seeds the day window from the People schedule while
    the member has no row. The layout write makes the row, so it carries the
    seed, and the calendar opens on the same hours after it."""
    from gateway.routes.tasks.settings import get_user_settings

    email = _address()
    db.made.append(email)
    user = _member(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at, working_hours) "
            "VALUES (gen_random_uuid(), 'Shell Seed', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), "
            "CAST(:wh AS jsonb))"),
            {"e": email, "k": f"manual:{email}", "o": db.org_a,
             "wh": '{"start": "11:00", "end": "15:00"}'})
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            before = await get_user_settings(user=user)
        await _put(db.org_a, user, {"answered": "skipped"})
        with _tenant(db.org_a):
            after = await get_user_settings(user=user)
    # The seed is not the column default, or this test would prove nothing.
    assert (before.day_start_hour, before.day_end_hour) == (10, 16)
    assert (after.day_start_hour, after.day_end_hour) == (10, 16)
    assert _stored(db, email)[0][0] == db.org_a


# ── The column ─────────────────────────────────────────────────────────────


def test_the_column_is_nullable_jsonb_with_no_default(db) -> None:
    with db.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'user_settings' AND column_name = 'shell_prefs'"
        )).one()
    assert tuple(row) == ("jsonb", "YES", None)


def test_an_unbound_write_is_refused(db, app_engine) -> None:  # noqa: F811
    from gateway.routes.tasks.settings import upsert_settings_sql
    from sqlalchemy.exc import DBAPIError

    email = _address()
    db.made.append(email)
    sql = upsert_settings_sql(["shell_prefs"], lambda k: f":{k} ::jsonb",
                              update=["shell_prefs"])
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text(sql), {"uid": email, "shell_prefs": '{"pins": []}'})
    assert _stored(db, email) == []
