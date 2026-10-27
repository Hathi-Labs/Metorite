"""R8 — the interview routes of the assistant profile (WS-63 PZ-26).

The routes of ``personalization.md`` §14.4 write the key ``interview`` of
``user_settings.assistant_profile``. This suite runs them against a REAL
Postgres, as the non-privileged role of the phase-4 rehearsal, with two
organizations. A hermetic fake agrees with any SQL it is handed.

What it proves:

- the first mark makes the row, and a later mark merges and keeps every other
  key, ``last_change`` included (a mark is not a change of the member's words);
- ``asked`` sets ``last_asked_at``, the first mark sets ``started_at`` once;
- a full profile (40 facts at the budget) still takes a mark;
- another organization never sees the interview of the same address;
- a chat mark is a 409 with ``learning`` off, and a Settings reset passes;
- a reset keeps every fact, and a call with ``X-Profile-Via`` is a 422;
- forget-all clears the interview;
- the undo of a fact with an area opens the area again;
- an organization outside ``ASSISTANT_PROFILE_ORGS`` behaves as the sibling
  write routes do: the routes never read the org switch, so a write works.
"""
# ruff: noqa: F811 — the imported fixture `db` is the argument of each test
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext
from acb_common import assistant_profile as acp
from acb_common import get_settings
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_assistant_profile_r8 import (  # noqa: F401
    _add_fact,
    _ap,
    _app_dsn,
    _get,
    _member,
    _put,
    _stored,
    _tenant,
    db,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see test_assistant_profile_r8).
pytestmark = _DB_GATE


def _address() -> str:
    return f"interview.{uuid.uuid4().hex[:8]}@pz26.test"


async def _mark(org: str, user: UserContext, area: str, state: str, via: str | None = None):
    with _tenant(org):
        return await _ap().mark_assistant_interview(
            area, body={"state": state}, user=user, x_profile_via=via)


async def _sit(org: str, user: UserContext):
    """The member starts a sitting, so the chat may mark an area `asked`."""
    with _tenant(org):
        return await _ap().start_assistant_interview(body={"sitting": True}, user=user)


async def _add_area_fact(org: str, user: UserContext, text_: str, area: str) -> dict:
    """A chat save of an interview answer, as ``profile_remember`` sends it."""
    with _tenant(org):
        return await _ap().add_fact(body={"text": text_, "kind": "work", "area": area},
                                    user=user, x_profile_via="chat")


def _column(db, email: str) -> dict:
    ((_org, stored),) = _stored(db, email)
    return stored if isinstance(stored, dict) else json.loads(stored)


async def test_the_first_mark_makes_the_row_and_sets_the_times(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _sit(db.org_a, user)
        first = await _mark(db.org_a, user, "goals", "asked", "chat")
        await _add_area_fact(db.org_a, user, "Ship the CRM import", "goals")
        second = await _mark(db.org_a, user, "goals", "answered", "chat")
        read = await _get(db.org_a, user)
    assert set(first) == {"started_at", "sitting_at", "last_asked_at", "areas"}
    assert first["areas"]["goals"]["state"] == "asked"
    assert second["started_at"] == first["started_at"]
    assert second["last_asked_at"] == first["last_asked_at"]
    assert second["areas"]["goals"]["state"] == "answered"
    assert read["interview"] == second
    assert _column(db, email)["v"] == 1


async def test_a_mark_keeps_every_other_key_and_the_last_change(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "address", {"name": "VJ"})
        with _tenant(db.org_a):
            await _ap().add_fact(body={"text": "Ships Q4", "kind": "work"}, user=user,
                                 x_profile_via="chat")
        before = _column(db, email)
        await _mark(db.org_a, user, "role", "skipped", "chat")
        await _mark(db.org_a, user, "people", "never")
    after = _column(db, email)
    assert after["last_change"] == before["last_change"]
    assert after["address"] == before["address"] and after["facts"] == before["facts"]
    assert set(after["interview"]["areas"]) == {"role", "people"}
    assert "last_asked_at" not in after["interview"]


async def test_start_sets_started_at_once_and_no_last_asked(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            one = await _ap().start_assistant_interview(user=user)
            two = await _ap().start_assistant_interview(user=user)
    assert set(one) == {"started_at"} and two == one
    assert acp.interview_due_line(one, acp._parse_time("2030-01-01T00:00:00Z")) == ""


async def test_a_full_profile_still_takes_a_mark(db) -> None:
    """40 facts at the budget: the interview must not count against it."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    size = 140
    while True:
        facts = [{"id": f"f{n:03d}", "text": "x" * size, "kind": "work"} for n in range(40)]
        if acp.profile_size({"v": 1, "learning": True, "facts": facts}) <= acp.PROFILE_MAX:
            break
        size -= 1
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "learning", True)
        with db.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE user_settings SET assistant_profile = CAST(:p AS jsonb) "
                "WHERE user_id = :u"),
                {"p": json.dumps({"v": 1, "learning": True, "facts": facts}), "u": email})
        await _sit(db.org_a, user)
        for area in acp.AREAS:
            await _mark(db.org_a, user, area, "asked", "chat")
        stored = _column(db, email)
    assert len(stored["facts"]) == 40 and len(stored["interview"]["areas"]) == 10
    assert acp.profile_size(stored) > acp.PROFILE_MAX  # it passed because it is excluded


async def test_another_organization_never_sees_the_interview(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _sit(db.org_a, user)
        await _mark(db.org_a, user, "goals", "asked", "chat")
        in_b = await _get(db.org_b, user)
        await _mark(db.org_b, user, "role", "never")
        in_a = await _get(db.org_a, user)
        with _tenant(db.org_b):
            await _ap().reset_assistant_interview(user=user)
        still_a = await _get(db.org_a, user)
    assert "interview" not in in_b
    assert set(in_a["interview"]["areas"]) == {"goals"}
    assert still_a["interview"] == in_a["interview"]


async def test_a_chat_mark_is_a_409_with_learning_off_and_settings_passes(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "learning", False)
        for via in ("chat", "whatsapp"):
            with pytest.raises(HTTPException) as exc:
                await _mark(db.org_a, user, "goals", "asked", via)
            assert (exc.value.status_code, exc.value.detail) == (409, "learning_off")
        with _tenant(db.org_a), pytest.raises(HTTPException) as start:
            await _ap().start_assistant_interview(user=user, x_profile_via="chat")
        assert start.value.status_code == 409
        assert "interview" not in _column(db, email)
        done = await _mark(db.org_a, user, "goals", "never")  # Settings
    assert done["areas"]["goals"]["state"] == "never"


async def test_a_reset_keeps_the_facts_and_a_chat_reset_is_a_422(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _add_area_fact(db.org_a, user, "Ships Q4", "goals")
        await _mark(db.org_a, user, "goals", "answered", "chat")
        await _mark(db.org_a, user, "role", "skipped", "chat")
        with _tenant(db.org_a):
            for call in (
                lambda: _ap().reset_assistant_interview_area("goals", user=user,
                                                             x_profile_via="chat"),
                lambda: _ap().reset_assistant_interview(user=user, x_profile_via="whatsapp"),
            ):
                with pytest.raises(HTTPException) as exc:
                    await call()
                assert exc.value.status_code == 422
            assert set(_column(db, email)["interview"]["areas"]) == {"goals", "role"}
            await _ap().reset_assistant_interview_area("goals", user=user)
            assert set(_column(db, email)["interview"]["areas"]) == {"role"}
            with pytest.raises(HTTPException) as bad:
                await _ap().reset_assistant_interview_area("hobbies", user=user)
            assert bad.value.status_code == 422
            await _ap().reset_assistant_interview(user=user)
        stored = _column(db, email)
    assert "interview" not in stored
    assert [f["text"] for f in stored["facts"]] == ["Ships Q4"]


async def test_forget_all_clears_the_interview(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _sit(db.org_a, user)
        await _mark(db.org_a, user, "goals", "asked", "chat")
        with _tenant(db.org_a):
            await _ap().delete_assistant_profile(user=user)
        read = await _get(db.org_a, user)
    assert "interview" not in read
    assert _stored(db, email)[0][1] is None


async def test_the_undo_of_a_fact_with_an_area_opens_the_area_again(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            fact = await _ap().add_fact(
                body={"text": "Ship Q4", "kind": "work", "area": "goals"}, user=user,
                x_profile_via="chat")
        assert fact["area"] == "goals"
        await _mark(db.org_a, user, "goals", "answered", "chat")
        await _mark(db.org_a, user, "role", "skipped", "chat")
        with _tenant(db.org_a):
            undone = await _ap().undo_assistant_profile(user=user, x_profile_via="chat")
        read = await _get(db.org_a, user)
    assert undone["undone"]["op"] == "post"
    assert read["facts"] == [] and set(read["interview"]["areas"]) == {"role"}
    assert "goals" in acp.open_areas(read["interview"], acp._parse_time("2030-01-01T00:00:00Z"))


async def test_a_bad_area_or_state_is_a_422(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        for area, state in (("hobbies", "asked"), ("goals", "maybe")):
            with pytest.raises(HTTPException) as exc:
                await _mark(db.org_a, user, area, state)
            assert exc.value.status_code == 422
        with _tenant(db.org_a), pytest.raises(HTTPException) as extra:
            await _ap().mark_assistant_interview(
                "goals", body={"state": "asked", "at": "2020-01-01T00:00:00Z"}, user=user)
        assert extra.value.status_code == 422
    assert _stored(db, email) == []


async def test_a_mark_moves_the_change_count(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _sit(db.org_a, user)
        before = acp.generation(email)
        await _mark(db.org_a, user, "goals", "asked", "chat")
    assert acp.generation(email) == before + 1


async def test_an_org_outside_the_list_behaves_like_the_sibling_routes(
        db, monkeypatch) -> None:
    """PZ-25: no write route reads the org switch, and the interview routes
    follow them. The list holds another org, and a mark and a fact both work."""
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "assistant_profile_orgs", db.org_b, raising=False)
    assert acp.enabled_for(db.org_a) is False
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        fact = await _add_fact(db.org_a, user, "Ships Q4", "work")
        await _sit(db.org_a, user)
        mark = await _mark(db.org_a, user, "goals", "asked", "chat")
        read = await _get(db.org_a, user)
    assert fact["text"] == "Ships Q4" and mark["areas"]["goals"]["state"] == "asked"
    assert read["interview"] == mark


async def test_asked_from_the_chat_needs_a_sitting_or_a_due_question(db) -> None:
    """A member who said no (started_at only) is never asked. A sitting, a due
    question and Settings each allow the mark."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            declined = await _ap().start_assistant_interview(user=user, x_profile_via="chat")
        assert set(declined) == {"started_at"}
        for state in ("asked",):
            with pytest.raises(HTTPException) as exc:
                await _mark(db.org_a, user, "goals", state, "chat")
            assert exc.value.status_code == 422
        assert _column(db, email)["interview"] == declined  # nothing changed
        # Other states are not limited, and Settings is not limited.
        await _mark(db.org_a, user, "goals", "skipped", "chat")
        settings = await _mark(db.org_a, user, "role", "asked")
        assert settings["areas"]["role"]["state"] == "asked"
        # Settings `asked` set last_asked_at now, so nothing is due yet.
        with pytest.raises(HTTPException):
            await _mark(db.org_a, user, "people", "asked", "chat")
        # A sitting allows it.
        sat = await _sit(db.org_a, user)
        assert "sitting_at" in sat and sat["started_at"] == declined["started_at"]
        ok = await _mark(db.org_a, user, "people", "asked", "chat")
        assert ok["areas"]["people"]["state"] == "asked"


async def test_a_due_question_allows_an_asked_mark_from_the_chat(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    old = "2026-01-01T10:00:00Z"
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "learning", True)
        with db.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE user_settings SET assistant_profile = CAST(:p AS jsonb) "
                "WHERE user_id = :u"),
                {"p": json.dumps({"v": 1, "learning": True, "interview": {
                    "started_at": old, "last_asked_at": old}}), "u": email})
        done = await _mark(db.org_a, user, "role", "asked", "chat")
    assert done["areas"]["role"]["state"] == "asked"


# ── A chat mark `answered` needs a saved answer (incident 2026-10-27) ──────
#
# The model marked `goals` answered after its save was refused, so the area
# closed and the member's answer was lost. The route now refuses that mark
# from the chat. Mutation: remove `_check` in `mark_assistant_interview`,
# and the three tests below go red.


async def test_a_chat_answered_with_nothing_saved_is_a_422_and_the_area_stays_open(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _sit(db.org_a, user)
        await _mark(db.org_a, user, "goals", "asked", "chat")
        # A fact of ANOTHER area, and a fact with no area, are no answer.
        await _add_area_fact(db.org_a, user, "I run the sales team", "role")
        await _add_fact(db.org_a, user, "I like tea")
        for via in ("chat", "whatsapp"):
            with pytest.raises(HTTPException) as exc:
                await _mark(db.org_a, user, "goals", "answered", via)
            assert exc.value.status_code == 422
            assert exc.value.detail == {"reason": acp.NO_ANSWER,
                                        "message": acp.NO_ANSWER_TEXT}
        assert _column(db, email)["interview"]["areas"]["goals"]["state"] == "asked"
        # The save of the answer, then the mark passes.
        await _add_area_fact(db.org_a, user, "ship the CRM import", "goals")
        done = await _mark(db.org_a, user, "goals", "answered", "chat")
    assert done["areas"]["goals"]["state"] == "answered"


async def test_detail_and_people_take_their_own_evidence(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        for area in ("detail", "people"):
            with pytest.raises(HTTPException) as exc:
                await _mark(db.org_a, user, area, "answered", "chat")
            assert exc.value.status_code == 422
        await _put(db.org_a, user, "style", {"length": "short"})
        await _mark(db.org_a, user, "detail", "answered", "chat")
        # One saved alias. The org check of an alias address is not under
        # test here, so the row is written as the alias route stores it.
        with db.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE user_settings SET assistant_profile = jsonb_set("
                "assistant_profile, '{aliases}', CAST(:a AS jsonb), true) "
                "WHERE user_id = :u"),
                {"a": json.dumps([{"id": "a0001", "alias": "Raj",
                                   "email": "raj@pz26.test"}]), "u": email})
        done = await _mark(db.org_a, user, "people", "answered", "chat")
    assert {a: v["state"] for a, v in done["areas"].items()} == {
        "detail": "answered", "people": "answered"}


async def test_settings_and_the_other_states_are_not_limited(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        settings = await _mark(db.org_a, user, "goals", "answered")  # no header
        skipped = await _mark(db.org_a, user, "role", "skipped", "chat")
        never = await _mark(db.org_a, user, "energy", "never", "chat")
    assert settings["areas"]["goals"]["state"] == "answered"
    assert skipped["areas"]["role"]["state"] == "skipped"
    assert never["areas"]["energy"]["state"] == "never"


async def test_a_replace_with_an_area_answers_the_area(db) -> None:
    """``profile_remember(replaces=...)`` PATCHes the old fact with the area,
    so the mark after a replace still passes."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        old = await _add_fact(db.org_a, user, "I want to hire", "work")
        with pytest.raises(HTTPException):
            await _mark(db.org_a, user, "goals", "answered", "chat")
        with _tenant(db.org_a):
            edited = await _ap().edit_fact(
                old["id"], body={"text": "hire two engineers", "kind": "work",
                                 "area": "goals"}, user=user, x_profile_via="chat")
        done = await _mark(db.org_a, user, "goals", "answered", "chat")
    assert edited["area"] == "goals" and edited["text"] == "hire two engineers"
    assert done["areas"]["goals"]["state"] == "answered"
