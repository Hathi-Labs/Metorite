"""R8: the Settings routes of the chat summaries (WS-63 PZ-35, D142).

Spec: ``project-docs/specs/personalization.md`` §15.5 and §15.9 (PZ-35).
Routes: ``gateway/routes/admin/assistant_profile.py``. Store:
``gateway/assistant_summaries.py``.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal, on a database with the whole ladder and two orgs:

- Delete: the chat reads ``{"v": 1, "off": true}``, and ``write_marked``
  after it writes nothing;
- the chat of another member gives 404, stays as it was, and no list shows it;
- the list shows the solo chats of the member only, newest first, and skips
  an ``off`` chat and an empty one;
- Delete all, Forget and the removal door each clear every summary of the
  member, and no row of another member or another org. A Delete stays ``off``;
- Forget keeps ``apps.chat.summaries = false``;
- the export holds the summaries, in JSON and in text;
- the switch: the PUT stores it, and ``app_prefs`` reads it.

Mutations, each red here: drop the member from the WHERE of
``turn_off_for_member`` (the 404 test); drop ``SOLO_ROOM_SQL`` from the list
(the shared-chat test); drop ``clear_for_member`` from the Forget route (the
Forget test).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.assistant_profile import app_prefs
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway import assistant_summaries as summaries
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_chat_summary_r8 import (  # noqa: F401 — fixtures and seeds
    ALICE,
    ANSWER,
    BOB,
    _app_dsn,
    _chat,
    _in,
    _message,
    _row,
    _summarise,
    db,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see the gate in the other R8 files).
pytestmark = _DB_GATE


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _user(email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:tasks"], roles=["member"]))


@pytest.fixture(autouse=True)
def _settings_rows(db):  # noqa: F811 — a fixture
    """Remove the settings rows that a test makes for the two members."""
    yield
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id IN (:a, :b)"),
                  {"a": ALICE, "b": BOB})


async def _list(db, who: str = ALICE) -> list[dict[str, Any]]:
    from gateway.routes.admin import assistant_profile as ap

    with _tenant(db.org_a):
        res = await ap.list_chat_summaries(user=_user(who))
    return json.loads(res.body)["summaries"]


async def _seed(db, *, user: str = ALICE, org: str | None = None, subjects=None,
                mid: str = "m1") -> str:
    org = org or db.org_a
    sid = _chat(db, org=org, user_id=user,
                subjects=(user,) if subjects is None else subjects)
    _message(db, org=org, sid=sid, mid=mid, ts=100)
    async with tenant_engine_scope(_app_dsn(db)):
        await _summarise(org, sid, through=mid)
    return sid


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_delete_removes_the_summary_and_a_new_one_may_come_later(db) -> None:
    """Owner decision of 2026-11-02: Delete does not write ``off``. The chat
    keeps a waterline, so only a newer row lets a new summary in."""
    from gateway.routes.admin import assistant_profile as ap

    sid = await _seed(db)
    at = datetime(2026, 11, 1, tzinfo=UTC)
    async with tenant_engine_scope(_app_dsn(db)):
        assert [s["id"] for s in await _list(db)] == [sid]
        with _tenant(db.org_a):
            res = await ap.delete_chat_summary(session_id=sid, user=_user(ALICE),
                                               x_profile_via=None)
        assert res.status_code == 204
        assert await _list(db) == []
        stored = _row(db, sid)["member_summary"]
        assert "off" not in stored, "Delete must not write off"
        assert stored == {"v": 1, "through": "m1", "through_ms": 100}
        # The old waterline: no mark, so the old write changes nothing.
        old = await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                        through="m1", through_ms=100, at=at)
        # A new row comes, and the job marks and writes again.
        _message(db, org=db.org_a, sid=sid, mid="m2", ts=200)
        assert await _in(db.org_a, summaries.mark, sid, through="m2", at=at)
        old_after_mark = await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                                   through="m1", through_ms=100, at=at)
        new = await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                        through="m2", through_ms=200, at=at)
        assert [s["id"] for s in await _list(db)] == [sid]
    assert old is False and old_after_mark is False and new is True


async def test_a_delete_keeps_an_off_chat_off(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    sid = await _seed(db)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.turn_off, sid)
        with _tenant(db.org_a):
            await ap.delete_chat_summary(session_id=sid, user=_user(ALICE), x_profile_via=None)
    assert _row(db, sid)["member_summary"] == {"v": 1, "off": True}


async def test_the_delete_routes_refuse_a_chat_write(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    with _tenant(db.org_a):
        for call in (lambda: ap.delete_chat_summary(session_id="x", user=_user(ALICE),
                                                    x_profile_via="chat"),
                     lambda: ap.delete_chat_summaries(user=_user(ALICE), x_profile_via="chat")):
            with pytest.raises(HTTPException) as err:
                await call()
            assert err.value.status_code == 422


async def test_a_chat_of_another_member_gives_404_and_stays(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    bobs = await _seed(db, user=BOB)
    before = _row(db, bobs)["member_summary"]
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a), pytest.raises(HTTPException) as err:
            await ap.delete_chat_summary(session_id=bobs, user=_user(ALICE),
                                        x_profile_via=None)
        assert err.value.status_code == 404
        assert await _list(db, ALICE) == []
        assert [s["id"] for s in await _list(db, BOB)] == [bobs]
    assert _row(db, bobs)["member_summary"] == before


async def test_the_list_shows_solo_chats_newest_first_and_skips_off_and_empty(db) -> None:
    older = await _seed(db)
    newer = await _seed(db)
    shared = await _seed(db, subjects=(ALICE, "group:eng"))
    off = await _seed(db)
    empty = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET updated_at = :t WHERE id = :s"),
                  {"t": datetime(2026, 10, 30, tzinfo=UTC), "s": newer})
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.turn_off, off)
        got = await _list(db)
    ids = [s["id"] for s in got]
    assert ids[:2] == [newer, older], ids
    assert shared not in ids, "a chat that is not solo must not be listed"
    assert off not in ids and empty not in ids
    assert got[0]["text"] == ANSWER.text and got[0]["follow_ups"] == list(ANSWER.follow_ups)


# ── Delete all, Forget and the removal ──────────────────────────────────────


async def _world_of_summaries(db) -> dict[str, str]:
    return {
        "mine": await _seed(db),
        "also": await _seed(db),
        "off": await _seed(db),
        "bobs": await _seed(db, user=BOB),
        "elsewhere": await _seed(db, org=db.org_b),
    }


async def _read(org: str, sid: str):
    return await _in(org, summaries.read, sid)


async def test_delete_all_clears_the_member_only_and_off_stays_off(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    w = await _world_of_summaries(db)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.turn_off, w["off"])
        with _tenant(db.org_a):
            res = await ap.delete_chat_summaries(user=_user(ALICE), x_profile_via=None)
        assert res.status_code == 204
        assert await _list(db) == []
        mine = [await _read(db.org_a, w[k]) for k in ("mine", "also")]
        off = await _read(db.org_a, w["off"])
        bobs = await _read(db.org_a, w["bobs"])
        other = await _read(db.org_b, w["elsewhere"])
    assert all(s == summaries.Stored(through="m1", through_ms=100) for s in mine)
    assert off == summaries.Stored(off=True), "an off chat stays off"
    assert bobs.text == ANSWER.text and other.text == ANSWER.text


async def test_forget_clears_every_summary_and_keeps_the_switch(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    w = await _world_of_summaries(db)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.turn_off, w["off"])
        with _tenant(db.org_a):
            await ap.put_assistant_profile(
                body={"key": "apps", "value": {"chat": {"summaries": False}}},
                user=_user(ALICE), x_profile_via=None)
            res = await ap.delete_assistant_profile(user=_user(ALICE), x_profile_via=None)
            after = await ap.get_assistant_profile(user=_user(ALICE))
        assert res.status_code == 204
        assert await _list(db) == []
        mine = [await _read(db.org_a, w[k]) for k in ("mine", "also")]
        off = await _read(db.org_a, w["off"])
        bobs = await _read(db.org_a, w["bobs"])
        other = await _read(db.org_b, w["elsewhere"])
    assert all(s.text == "" and not s.follow_ups for s in mine)
    assert off == summaries.Stored(off=True), "a Delete must stay off after Forget"
    assert bobs.text == ANSWER.text and other.text == ANSWER.text
    assert after["apps"]["chat"] == {"summaries": False}


def _make_an_owner(ns) -> None:
    """The org keeps an owner, so the removal of Alice passes the owner rule."""
    with ns.admin_engine.begin() as c:
        role = c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
            "ON CONFLICT DO NOTHING RETURNING id"), {"o": ns.org_a}).scalar()
        if role is None:
            role = c.execute(text(
                "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = 'owner'"), {"o": ns.org_a}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e "
            "ON CONFLICT DO NOTHING"), {"r": role, "e": ns.owner_email, "o": ns.org_a})


async def test_the_removal_door_clears_every_summary_of_the_member(db) -> None:
    from gateway.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import remove_member_rows

    _make_an_owner(db)
    w = await _world_of_summaries(db)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            # Alice is not an owner, so the owner rule lets her go.
            async with tenant_session(db.org_a) as session:
                member = await get_member(session, db.org_a, ALICE)
                await remove_member_rows(session, db.org_a, member)
        mine = [await _read(db.org_a, w[k]) for k in ("mine", "also", "off")]
        bobs = await _read(db.org_a, w["bobs"])
        other = await _read(db.org_b, w["elsewhere"])
    assert all(s.text == "" and not s.follow_ups for s in mine)
    assert bobs.text == ANSWER.text and other.text == ANSWER.text


# ── The export ──────────────────────────────────────────────────────────────


async def test_the_export_holds_the_summaries_in_json_and_text(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    sid = await _seed(db)
    await _seed(db, user=BOB)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            as_json = await ap.export_assistant_profile(user=_user(ALICE), format="json")
            as_text = await ap.export_assistant_profile(user=_user(ALICE), format="text")
    body = json.loads(as_json.body)
    assert [s["id"] for s in body["summaries"]] == [sid]
    assert body["more"] is False
    # No profile, but a summary: no line that says the assistant keeps nothing.
    assert body["text"] == ""
    assert body["summaries"][0]["text"] == ANSWER.text
    out = as_text.body.decode()
    assert "keeps nothing" not in out and out.startswith("Recent chats")
    assert "Recent chats" in out and ANSWER.text in out and ANSWER.follow_ups[0] in out


# ── The switch ──────────────────────────────────────────────────────────────


async def test_the_switch_is_stored_and_read(db) -> None:
    from gateway.routes.admin import assistant_profile as ap

    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            assert (await ap.get_assistant_profile(user=_user(ALICE)))["apps"]["chat"] \
                == {"summaries": True}
            put = await ap.put_assistant_profile(
                body={"key": "apps", "value": {"chat": {"summaries": False}}},
                user=_user(ALICE), x_profile_via=None)
            got = await ap.get_assistant_profile(user=_user(ALICE))
    assert put["apps"]["chat"] == {"summaries": False}
    assert app_prefs({"apps": {"chat": {"summaries": False}}}, "chat")["summaries"] is False
    assert got["apps"]["chat"] == {"summaries": False}
    assert got["apps"]["brief"]["enabled"] is True, "the other app keeps its default"
