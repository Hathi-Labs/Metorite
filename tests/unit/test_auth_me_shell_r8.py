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
    assert b == {"preset": None, "answered": None, "pins": None, "known": None,
                 "cardOrder": None, "newOrder": None, "chat_first_message_at": None,
                 "tour_seen_at": None, "buddy_hello_at": None, "guide": False}


async def test_the_tour_mark_merges_with_the_server_s_time_and_each_save_keeps_it(db) -> None:
    """WS-OB GD-5a: ``tour_seen_at`` is a key-only stamp, as the nudge mark
    is. Postgres runs the real merge and the two nested keeps here. The
    server writes its own time. A layout save keeps both marks. A save that
    names the nudge mark keeps the tour mark only."""
    from datetime import UTC, datetime

    email = _address()
    db.made.append(email)
    user = _member(email)
    nudge = "2026-10-21T09:00:00.000Z"
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                    "pins": ["/tasks"], "chat_first_message_at": nudge})
        before = datetime.now(UTC)
        first = await _put(db.org_a, user, {"tour_seen_at": "2001-01-01T00:00:00.000Z"})
        seen = first["tour_seen_at"]
        # The server's time, and the stars and the nudge mark survive the merge.
        assert seen is not None and seen != "2001-01-01T00:00:00.000Z"
        assert datetime.fromisoformat(seen.replace("Z", "+00:00")) >= before.replace(microsecond=0)
        assert (first["pins"], first["chat_first_message_at"]) == (["/tasks"], nudge)
        # A star save omits both marks and keeps both, in one statement.
        second = await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                             "pins": ["/tasks", "/email"]})
        assert (second["pins"], second["chat_first_message_at"], second["tour_seen_at"]) == (
            ["/tasks", "/email"], nudge, seen)
        # A save that clears the nudge mark on purpose keeps the tour mark.
        third = await _put(db.org_a, user, {"answered": "skipped", "chat_first_message_at": None})
        assert (third["chat_first_message_at"], third["tour_seen_at"]) == (None, seen)
        read = await _get(db.org_a, user)
        other = await _get(db.org_b, user)
    assert read["tour_seen_at"] == seen
    # The mark is per organization, as the layout is.
    assert other["tour_seen_at"] is None
    [(_, raw)] = _stored(db, email)
    assert raw["tour_seen_at"] == seen


async def test_the_first_tour_start_stays_and_the_nudge_mark_still_takes_the_new_time(db) -> None:
    """WS-OB GD-5a: a second stamp of ``tour_seen_at`` keeps the stored time
    (COALESCE in SQL). The nudge mark is not changed by this: a second stamp
    of it still overwrites. A stored JSON null does not count as a time."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        # A stored null (a layout save that never named the mark) takes the first start.
        await _put(db.org_a, user, {"answered": "skipped"})
        first = (await _put(db.org_a, user, {"tour_seen_at": "2001-01-01T00:00:00.000Z"}))["tour_seen_at"]
        assert first is not None
        second = await _put(db.org_a, user, {"tour_seen_at": "2002-02-02T00:00:00.000Z"})
        assert second["tour_seen_at"] == first
        # Both marks in one stamp: the tour mark stays, the nudge mark is written.
        a = await _put(db.org_a, user, {"chat_first_message_at": "2026-10-21T09:00:00.000Z"})
        b = await _put(db.org_a, user, {"chat_first_message_at": "2026-10-22T09:00:00.000Z",
                                        "tour_seen_at": "2003-03-03T00:00:00.000Z"})
        assert a["tour_seen_at"] == first
        assert (b["chat_first_message_at"], b["tour_seen_at"]) == ("2026-10-22T09:00:00.000Z", first)
        # An old tab's save carries the answer's `guide` and still saves.
        c = await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                        "pins": ["/tasks"], "guide": True})
        assert (c["pins"], c["tour_seen_at"]) == (["/tasks"], first)
    [(_, raw)] = _stored(db, email)
    assert "guide" not in raw


async def test_an_old_tab_s_layout_save_never_writes_the_tour_mark(db) -> None:
    """WS-OB GD-5a repair 2: an old tab copies the whole answer back, so its
    star save names ``tour_seen_at`` (null, or a time). A layout save never
    writes the mark. Only the key-only stamp does."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        # No stamp yet: an old tab's null and an old tab's time both leave none.
        a = await _put(db.org_a, user, {"answered": "skipped", "tour_seen_at": None})
        assert a["tour_seen_at"] is None
        b = await _put(db.org_a, user, {"answered": "skipped", "tour_seen_at": "2001-01-01T00:00:00.000Z"})
        assert b["tour_seen_at"] is None
        # The first start stamps. An old tab's save after it keeps the stamp.
        first = (await _put(db.org_a, user, {"tour_seen_at": "2002-02-02T00:00:00.000Z"}))["tour_seen_at"]
        assert first is not None
        c = await _put(db.org_a, user, {"answered": "skipped", "pins": ["/tasks"], "tour_seen_at": None})
        assert (c["pins"], c["tour_seen_at"]) == (["/tasks"], first)
        d = await _put(db.org_a, user, {"answered": "skipped", "tour_seen_at": "2003-03-03T00:00:00.000Z"})
        assert d["tour_seen_at"] == first
    [(_, raw)] = _stored(db, email)
    assert raw["tour_seen_at"] == first


async def test_the_nudge_mark_round_trips_and_a_layout_save_keeps_it(db) -> None:
    """WS-62 CF-2: ``chat_first_message_at`` rides the jsonb column, and a
    later layout save that does not name it keeps it (§4.7, D-CF-7)."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    stamp = "2026-10-21T09:00:00.000Z"
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"answered": "skipped", "chat_first_message_at": stamp})
        first = await _get(db.org_a, user)
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                    "pins": ["/tasks"]})
        second = await _get(db.org_a, user)
        other = await _get(db.org_b, user)
    assert first["chat_first_message_at"] == stamp
    assert (second["preset"], second["chat_first_message_at"]) == ("engineer", stamp)
    # The mark is per organization, as the layout is.
    assert other["chat_first_message_at"] is None
    [(_, raw)] = _stored(db, email)
    assert raw["chat_first_message_at"] == stamp


async def test_the_mark_alone_merges_and_a_layout_save_keeps_it_in_one_statement(db) -> None:
    """Review of #1206: the stamp is a key-only merge, and the keep is in
    SQL. Neither reads the row first, so a stamp and a star save cannot
    write each other over. Postgres runs the real `||`, `jsonb_set` and
    `COALESCE` here."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    stamp = "2026-10-21T10:00:00.000Z"
    async with tenant_engine_scope(_app_dsn(db)):
        # A new row from the mark alone: the row exists, the rest is null.
        first = await _put(db.org_a, user, {"chat_first_message_at": stamp})
        assert (first["chat_first_message_at"], first["pins"]) == (stamp, None)
        # A star save that omits the mark keeps it, by COALESCE.
        second = await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                             "pins": ["/tasks", "/email"]})
        assert (second["pins"], second["chat_first_message_at"]) == (["/tasks", "/email"], stamp)
        # The mark alone again, over a full layout: the stars survive the merge.
        later = "2026-10-22T10:00:00.000Z"
        third = await _put(db.org_a, user, {"chat_first_message_at": later})
        assert (third["pins"], third["preset"], third["chat_first_message_at"]) == (
            ["/tasks", "/email"], "engineer", later)
        read = await _get(db.org_a, user)
    assert read == third


async def test_the_stars_and_known_round_trip_in_jsonb(db) -> None:
    """The star model (owner, 2026-10-11) stores ``known`` beside ``pins``."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered",
                                    "pins": ["/projects"],
                                    "known": ["/tasks", "/projects", "/chat"]})
        a = await _get(db.org_a, user)
    assert a["pins"] == ["/projects"]
    assert a["known"] == ["/tasks", "/projects", "/chat"]
    [(_, stored)] = _stored(db, email)
    assert stored["known"] == ["/tasks", "/projects", "/chat"]


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


async def test_the_first_zone_save_keeps_the_calendar_seed(db) -> None:
    """RX-5: the shell saves the browser's zone for a member with no row. That
    write makes the row, so it carries the seed, as the layout write does. A
    second save changes the zone and leaves the hours."""
    from gateway.routes.tasks.settings import (
        UserSettingsPatch,
        get_user_settings,
        put_user_settings,
    )

    email = _address()
    db.made.append(email)
    user = _member(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at, working_hours) "
            "VALUES (gen_random_uuid(), 'Zone Seed', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), "
            "CAST(:wh AS jsonb))"),
            {"e": email, "k": f"manual:{email}", "o": db.org_a,
             "wh": '{"start": "11:00", "end": "19:00"}'})
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            seeded = await get_user_settings(user=user)
            await put_user_settings(patch=UserSettingsPatch(timezone="Asia/Kolkata"), user=user)
            saved = await get_user_settings(user=user)
            await put_user_settings(patch=UserSettingsPatch(day_start_hour=6), user=user)
            await put_user_settings(patch=UserSettingsPatch(timezone="Asia/Dubai"), user=user)
            later = await get_user_settings(user=user)
    assert seeded.day_start_hour == 10
    assert (saved.timezone, saved.day_start_hour, saved.day_end_hour) == (
        "Asia/Kolkata", seeded.day_start_hour, seeded.day_end_hour)
    assert (later.timezone, later.day_start_hour) == ("Asia/Dubai", 6)


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


async def test_the_hello_mark_is_written_by_the_route_only_and_each_save_keeps_it(db) -> None:
    """WS-BUD BUD-1: ``buddy_hello_at`` is claimed by the hello route alone
    (``stamp-first``, the first stamp stays). A client PUT never writes it,
    never blocks the hello, and a layout save keeps it. Postgres runs the
    real merge."""
    from gateway.routes.admin import me

    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, {"preset": "engineer", "answered": "answered", "pins": ["/tasks"]})
        # A client PUT of the mark alone writes nothing and keeps the layout.
        alone = await _put(db.org_a, user, {"buddy_hello_at": "2001-01-01T00:00:00.000Z"})
        assert alone["buddy_hello_at"] is None and alone["pins"] == ["/tasks"]
        # So the route's claim still wins, once.
        with _tenant(db.org_a):
            async with me._tenant_session() as s:
                won = await me._write_shell(s, email, {"buddy_hello_at": "2010-01-01T00:00:00.000Z"}, "stamp-first")
                again = await me._write_shell(s, email, {"buddy_hello_at": "2011-01-01T00:00:00.000Z"}, "stamp-first")
        first = me.stored_shell(won)["buddy_hello_at"]
        assert first == "2010-01-01T00:00:00.000Z" == me.stored_shell(again)["buddy_hello_at"]
        # The tour mark and the nudge mark merge beside it and leave it.
        tour = await _put(db.org_a, user, {"tour_seen_at": "2003-03-03T00:00:00.000Z"})
        assert (tour["buddy_hello_at"], tour["pins"]) == (first, ["/tasks"])
        both = await _put(db.org_a, user, {"chat_first_message_at": "2026-10-22T09:00:00.000Z",
                                           "buddy_hello_at": "2004-04-04T00:00:00.000Z"})
        assert (both["buddy_hello_at"], both["tour_seen_at"]) == (first, tour["tour_seen_at"])
        # A layout save keeps it, with the key omitted, null, or a time.
        for extra in ({}, {"buddy_hello_at": None}, {"buddy_hello_at": "2005-05-05T00:00:00.000Z"}):
            kept = await _put(db.org_a, user, {"answered": "skipped", **extra})
            assert kept["buddy_hello_at"] == first
        other = await _get(db.org_b, user)
    # The mark is per organization, as the layout is.
    assert other["buddy_hello_at"] is None
    [(_, raw)] = _stored(db, email)
    assert raw["buddy_hello_at"] == first
