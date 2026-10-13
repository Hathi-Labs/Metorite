"""R8 — My Day's feed preferences, ``GET`` and ``PUT /shell/feed/prefs`` (MD-2d).

Migration 259 adds ``user_settings.my_day_prefs``. The two routes in
``routes/shell/prefs.py`` read and write it through the tenant session.
Spec: ``my_day_feed.md`` §13.3 and §16 MD-2d.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a
database with the whole ladder (so 239, 240 and 259) and two organizations:

- a saved correction survives a layout save through ``PUT /auth/me/shell``,
  and a layout survives a preference save (the §13.3 fence);
- a second key does not erase the first, because the write merges one key
  with ``jsonb_set``;
- a member with no settings row gets the calendar seed with the new row
  (D-PC-16);
- another organization neither reads nor writes the member's preferences;
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
    return f"prefs.{uuid.uuid4().hex[:8]}@md2d.test"


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


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:tasks", "feature:projects"], roles=["member"]),
    )


async def _put(org: str, user: UserContext, key: str, value: object) -> dict:
    from gateway.routes.shell.prefs import put_feed_prefs

    with _tenant(org):
        return await put_feed_prefs(body={"key": key, "value": value}, user=user)


async def _get(org: str, user: UserContext) -> dict:
    from gateway.routes.shell.prefs import get_feed_prefs

    with _tenant(org):
        return await get_feed_prefs(user=user)


async def _put_shell(org: str, user: UserContext, body: dict) -> dict:
    from gateway.routes.admin.me import put_my_shell

    with _tenant(org):
        return await put_my_shell(body=body, user=user)


async def _get_shell(org: str, user: UserContext) -> dict:
    from gateway.routes.admin.me import get_my_shell

    with _tenant(org):
        return await get_my_shell(user=user)


def _stored(ns, email: str) -> list[tuple[str, object]]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, my_day_prefs FROM user_settings "
            "WHERE user_id = :u ORDER BY 1"), {"u": email})]


_LAYOUT = {"preset": "engineer", "answered": "answered", "pins": ["/projects", "/tasks"]}
_LINE = "Acme's \"newsletter\" never needs me — skip it"


# ── The §13.3 fence: a layout save and a preference save keep each other ───


async def test_a_correction_survives_a_layout_save(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "corrections", [_LINE])
        await _put_shell(db.org_a, user, _LAYOUT)
        prefs = await _get(db.org_a, user)
        shell = await _get_shell(db.org_a, user)
    assert prefs == {"corrections": [_LINE]}
    assert (shell["preset"], shell["pins"]) == ("engineer", ["/projects", "/tasks"])


async def test_a_layout_survives_a_preference_save(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        # The layout makes the row, so the column is NULL when the merge runs.
        await _put_shell(db.org_a, user, _LAYOUT)
        await _put(db.org_a, user, "mutes", ["block_soon"])
        shell = await _get_shell(db.org_a, user)
        prefs = await _get(db.org_a, user)
    assert (shell["preset"], shell["answered"], shell["pins"]) == (
        "engineer", "answered", ["/projects", "/tasks"])
    assert prefs == {"mutes": ["block_soon"]}


# ── One key at a time ──────────────────────────────────────────────────────


async def test_a_second_key_does_not_erase_the_first(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "mutes", ["overdue", "block_soon"])
        await _put(db.org_a, user, "corrections", [_LINE])
        answer = await _put(db.org_a, user, "open_in_app", ["needs_reply"])
        # A new value for one key replaces that key only.
        again = await _put(db.org_a, user, "mutes", ["no_plan_yet"])
        read = await _get(db.org_a, user)
    assert answer == {"mutes": ["overdue", "block_soon"], "corrections": [_LINE],
                      "open_in_app": ["needs_reply"]}
    assert again == read == {"mutes": ["no_plan_yet"], "corrections": [_LINE],
                             "open_in_app": ["needs_reply"]}
    [(org, stored)] = _stored(db, email)
    assert org == db.org_a
    assert stored == read


# ── A member with no settings row ──────────────────────────────────────────


async def test_a_member_with_no_row_gets_the_calendar_seed(db) -> None:
    """``/tasks/settings`` seeds the day window from the People schedule while
    the member has no row. The preference write makes the row, so it carries
    the seed, and the calendar opens on the same hours after it."""
    from gateway.routes.tasks.settings import get_user_settings

    email = _address()
    db.made.append(email)
    user = _member(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at, working_hours) "
            "VALUES (gen_random_uuid(), 'Prefs Seed', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), "
            "CAST(:wh AS jsonb))"),
            {"e": email, "k": f"manual:{email}", "o": db.org_a,
             "wh": '{"start": "11:00", "end": "15:00"}'})
    assert _stored(db, email) == []
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            before = await get_user_settings(user=user)
        await _put(db.org_a, user, "mutes", ["overdue"])
        with _tenant(db.org_a):
            after = await get_user_settings(user=user)
    # The seed is not the column default, or this test would prove nothing.
    assert (before.day_start_hour, before.day_end_hour) == (10, 16)
    assert (after.day_start_hour, after.day_end_hour) == (10, 16)
    assert _stored(db, email) == [(db.org_a, {"mutes": ["overdue"]})]


# ── The tenant wall ────────────────────────────────────────────────────────


async def test_another_organization_neither_reads_nor_writes_the_prefs(db, app_engine) -> None:  # noqa: F811
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "corrections", [_LINE])
        # The same address in org B has no preferences there.
        assert await _get(db.org_b, user) == {}
        # A save in org B makes org B's own row, and org A's stays.
        await _put(db.org_b, user, "corrections", ["Org B line"])
        a = await _get(db.org_a, user)
        b = await _get(db.org_b, user)
    assert a == {"corrections": [_LINE]}
    assert b == {"corrections": ["Org B line"]}
    assert [org for org, _ in _stored(db, email)] == sorted([db.org_a, db.org_b])

    # A raw session bound to org B sees no row of org A, and cannot change it.
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": db.org_b})
        seen = c.execute(text(
            "SELECT count(*) FROM user_settings "
            "WHERE user_id = :u AND organization_id = CAST(:a AS uuid)"),
            {"u": email, "a": db.org_a}).scalar()
        changed = c.execute(text(
            "UPDATE user_settings SET my_day_prefs = CAST('{}' AS jsonb) "
            "WHERE user_id = :u AND organization_id = CAST(:a AS uuid)"),
            {"u": email, "a": db.org_a}).rowcount
    assert (seen, changed) == (0, 0)
    assert dict(_stored(db, email))[db.org_a] == {"corrections": [_LINE]}


def test_an_unbound_write_is_refused(db, app_engine) -> None:  # noqa: F811
    from gateway.routes.shell.prefs import _MERGE_PREFS, _NEW_PREFS
    from gateway.routes.tasks.settings import upsert_settings_sql
    from sqlalchemy.exc import DBAPIError

    email = _address()
    db.made.append(email)
    sql = upsert_settings_sql(["my_day_prefs"], lambda k: _NEW_PREFS,
                              update=["my_day_prefs"], merge={"my_day_prefs": _MERGE_PREFS})
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text(sql), {"uid": email, "pkey": "mutes", "pval": '["overdue"]'})
    assert _stored(db, email) == []


# ── The column ─────────────────────────────────────────────────────────────


def test_the_column_is_nullable_jsonb_with_no_default(db) -> None:
    with db.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'user_settings' AND column_name = 'my_day_prefs'"
        )).one()
    assert tuple(row) == ("jsonb", "YES", None)
