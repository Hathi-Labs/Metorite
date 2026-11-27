"""R8 — the member's time zone, ``GET`` and ``PUT /auth/me/zone`` (RX-7).

The two routes in ``routes/admin/me.py`` read and write
``user_settings.timezone`` through the tenant session, and the write is
``tasks.settings.write_member_settings``, the one write of the row.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (``acb_app_h3rls``), on a database with the whole ladder
and two organizations:

- a guest with no feature saves a zone and reads it back;
- one address keeps a zone in each of its two organizations;
- the first write makes the row with the People seed (D-PC-16), and a later
  write changes ``timezone`` only;
- ``PUT /tasks/settings`` still writes through the same function.

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


def _address() -> str:
    return f"zone.{uuid.uuid4().hex[:8]}@rx7.test"


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


async def _put(org: str, user: UserContext, zone: str) -> dict:
    from gateway.routes.admin.me import ZoneIn, put_my_zone

    with _tenant(org):
        return await put_my_zone(body=ZoneIn(timezone=zone), user=user)


async def _get(org: str, user: UserContext) -> dict:
    from gateway.routes.admin.me import get_my_zone

    with _tenant(org):
        return await get_my_zone(user=user)


def _stored(ns, email: str) -> list[tuple[str, str]]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, timezone FROM user_settings "
            "WHERE user_id = :u ORDER BY 1"), {"u": email})]


async def test_a_guest_saves_a_zone_and_reads_it_back(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        before = await _get(db.org_a, user)
        await _put(db.org_a, user, "Asia/Kolkata")
        after = await _get(db.org_a, user)
    assert before == {"timezone": "UTC"}
    assert after == {"timezone": "Asia/Kolkata"}


async def test_one_address_keeps_a_zone_in_each_organization(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "Asia/Kolkata")
        await _put(db.org_b, user, "Pacific/Auckland")
        a = await _get(db.org_a, user)
        b = await _get(db.org_b, user)
    assert (a["timezone"], b["timezone"]) == ("Asia/Kolkata", "Pacific/Auckland")
    assert sorted(tz for _, tz in _stored(db, email)) == ["Asia/Kolkata", "Pacific/Auckland"]


async def test_another_organization_never_reads_the_zone(db) -> None:
    email = _address()
    db.made.append(email)
    user = _guest(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "Asia/Kolkata")
        b = await _get(db.org_b, user)
    assert b == {"timezone": "UTC"}


async def test_a_second_write_changes_the_zone_and_nothing_else(db) -> None:
    from gateway.routes.tasks.settings import UserSettingsPatch, put_user_settings

    email = _address()
    db.made.append(email)
    user = UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:tasks"], roles=["member"]))
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            await put_user_settings(UserSettingsPatch(buffer_mins=25), user=user)
        await _put(db.org_a, user, "Europe/Paris")
        with _tenant(db.org_a):
            from gateway.routes.tasks.settings import get_user_settings

            got = await get_user_settings(user=user)
    assert got.timezone == "Europe/Paris"
    assert got.buffer_mins == 25
