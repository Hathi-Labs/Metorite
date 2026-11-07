"""R8 — the member's appearance, ``GET`` and ``PUT /auth/me/appearance`` (PKG-20).

Spec: ``project-docs/specs/package_host.md`` §9.5 and the PKG-20 row.

The two routes in ``routes/admin/me.py`` read and write
``user_settings.appearance`` through the tenant session, and the write is
``tasks.settings.write_member_settings``, the one write of the row.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (``acb_app_h3rls``), on a database with the whole ladder
and two organizations:

- a guest with no feature saves the record and reads it back, as JSONB;
- one address keeps a copy in each of its two organizations, and another org
  never reads it;
- another member's row stays as it was;
- the first write makes the row with the People seed (D-PC-16), and a later
  write changes ``appearance`` only;
- ``PUT /tasks/settings`` and the zone write do NOT clear it;
- the column is nullable JSONB with no default (R6).

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

pytestmark = _DB_GATE

EMPTY = {"mode": None, "density": None, "accent": None, "updated_at": None}


def _address() -> str:
    return f"look.{uuid.uuid4().hex[:8]}@pkg20.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
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


def _guest(email: str) -> UserContext:
    # No feature at all, so `/tasks/settings` would refuse this caller.
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access([], roles=["guest"]))


def _tasks_member(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:tasks"], roles=["member"]))


async def _put(org: str, user: UserContext, **record) -> dict:
    from gateway.routes.admin.me import AppearanceIn, put_my_appearance

    with _tenant(org):
        return await put_my_appearance(body=AppearanceIn(**record), user=user)


async def _get(org: str, user: UserContext) -> dict:
    from gateway.routes.admin.me import get_my_appearance

    with _tenant(org):
        return await get_my_appearance(user=user)


def _stored(ns, email: str) -> list[tuple]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, appearance, jsonb_typeof(appearance) "
            "FROM user_settings WHERE user_id = :u ORDER BY 1"), {"u": email})]


def _fields(record: dict) -> tuple:
    return (record["mode"], record["density"], record["accent"])


async def test_a_guest_saves_the_record_and_reads_it_back(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        before = await _get(db.org_a, user)
        put = await _put(db.org_a, user, mode="light", density="compact",
                         accent="hsl(347 77% 50%)")
        after = await _get(db.org_a, user)
    assert before == EMPTY
    assert after == put
    assert _fields(after) == ("light", "compact", "hsl(347 77% 50%)")
    assert after["updated_at"]
    [(org, raw, kind)] = _stored(db, email)
    # Stored as a JSONB object, not as a JSON string inside JSONB.
    assert (org, kind) == (db.org_a, "object")
    assert raw["mode"] == "light"


async def test_one_address_keeps_a_copy_in_each_organization(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, mode="light", density="compact", accent="#e11d48")
        await _put(db.org_b, user, mode="dark", density="comfortable", accent=None)
        a = await _get(db.org_a, user)
        b = await _get(db.org_b, user)
    assert _fields(a) == ("light", "compact", "#e11d48")
    assert _fields(b) == ("dark", "comfortable", None)
    assert len(_stored(db, email)) == 2


async def test_another_organization_never_reads_the_copy(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, mode="light", density="compact", accent="#e11d48")
        b = await _get(db.org_b, user)
    assert b == EMPTY


async def test_another_member_s_row_stays_as_it_was(db) -> None:
    mine, theirs = _address(), _address()
    db.made.extend([mine, theirs])
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, _guest(theirs), mode="dark", density="default", accent="red")
        before = await _get(db.org_a, _guest(theirs))
        await _put(db.org_a, _guest(mine), mode="light", density="compact", accent="#fff")
        after = await _get(db.org_a, _guest(theirs))
    assert after == before
    assert _fields(after) == ("dark", "default", "red")


async def test_the_first_write_keeps_the_seed_and_a_later_write_changes_appearance_only(db) -> None:
    from gateway.routes.tasks.settings import (
        UserSettingsPatch,
        get_user_settings,
        put_user_settings,
    )

    email = _address()
    db.made.append(email)
    user = _tasks_member(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at, working_hours) "
            "VALUES (gen_random_uuid(), 'Look Seed', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), "
            "CAST(:wh AS jsonb))"),
            {"e": email, "k": f"manual:{email}", "o": db.org_a,
             "wh": '{"start": "11:00", "end": "15:00"}'})
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            seeded = await get_user_settings(user=user)
        await _put(db.org_a, user, mode="light", density=None, accent=None)
        with _tenant(db.org_a):
            first = await get_user_settings(user=user)
            await put_user_settings(UserSettingsPatch(buffer_mins=25), user=user)
        await _put(db.org_a, user, mode="dark", density="compact", accent="#e11d48")
        with _tenant(db.org_a):
            later = await get_user_settings(user=user)
        got = await _get(db.org_a, user)
    # The seed is not the column default, or this test would prove nothing.
    assert (seeded.day_start_hour, seeded.day_end_hour) == (10, 16)
    assert (first.day_start_hour, first.day_end_hour) == (10, 16)
    assert (later.day_start_hour, later.day_end_hour, later.buffer_mins) == (10, 16, 25)
    assert _fields(got) == ("dark", "compact", "#e11d48")


async def test_the_settings_write_and_the_zone_write_keep_the_appearance(db) -> None:
    from gateway.routes.admin.me import ZoneIn, put_my_zone
    from gateway.routes.tasks.settings import UserSettingsPatch, put_user_settings

    email = _address()
    db.made.append(email)
    user = _tasks_member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        put = await _put(db.org_a, user, mode="light", density="comfortable",
                         accent="oklch(0.62 0.21 20)")
        with _tenant(db.org_a):
            await put_user_settings(UserSettingsPatch(buffer_mins=15, timezone="Asia/Kolkata"),
                                    user=user)
            await put_my_zone(body=ZoneIn(timezone="Europe/Paris"), user=user)
        got = await _get(db.org_a, user)
    assert got == put


async def test_the_shell_layout_write_keeps_the_appearance(db) -> None:
    from gateway.routes.admin.me import put_my_shell

    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        put = await _put(db.org_a, user, mode="light", density="compact", accent="#e11d48")
        with _tenant(db.org_a):
            await put_my_shell(body={"answered": "skipped"}, user=user)
        got = await _get(db.org_a, user)
    assert got == put


async def test_the_last_write_wins_and_takes_a_new_stamp(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        one = await _put(db.org_a, user, mode="light", density="compact", accent="#e11d48")
        two = await _put(db.org_a, user, mode="dark", density=None, accent=None)
        got = await _get(db.org_a, user)
    assert got == two
    assert two["updated_at"] >= one["updated_at"]


def test_the_column_is_nullable_jsonb_with_no_default(db) -> None:
    with db.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'user_settings' AND column_name = 'appearance'"
        )).one()
    assert tuple(row) == ("jsonb", "YES", None)
