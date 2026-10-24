"""R8 — the assistant profile, ``/auth/me/assistant-profile`` (WS-63 PZ-1).

Migration 294 adds ``user_settings.assistant_profile``. The routes in
``routes/admin/assistant_profile.py`` read and write it through the tenant
session. Spec: ``personalization.md`` §4.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a
database with the whole ladder (so 239, 240, 259, 284 and 294) and two
organizations:

- a layout save, a My Day save and a number-system save keep the profile,
  and a profile save keeps each of them;
- a second key does not erase the first, because the write merges one key
  with ``jsonb_set``;
- a member with no settings row gets the calendar seed with the new row;
- another organization neither reads nor writes the member's profile, and
  one address in two organizations has two profiles (D15);
- a fact or an alias is edited and deleted in place, and an absent id is 404;
- concurrent adds lose nothing, because each add is one statement;
- a write over a cap rolls back, so the stored profile does not change;
- "forget everything" sets the column NULL and keeps the row;
- the column is nullable with no default (R6).

A hermetic fake agrees with any SQL it is handed, so none of these could be
proved without the database.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
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
    return f"profile.{uuid.uuid4().hex[:8]}@pz1.test"


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


def _ap():
    from gateway.routes.admin import assistant_profile

    return assistant_profile


async def _get(org: str, user: UserContext) -> dict:
    with _tenant(org):
        return await _ap().get_assistant_profile(user=user)


async def _put(org: str, user: UserContext, key: str, value: object) -> dict:
    with _tenant(org):
        return await _ap().put_assistant_profile(body={"key": key, "value": value}, user=user)


async def _add_fact(org: str, user: UserContext, text_: str, kind: str = "about") -> dict:
    with _tenant(org):
        return await _ap().add_fact(body={"text": text_, "kind": kind}, user=user)


def _stored(ns, email: str) -> list[tuple[str, object]]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, assistant_profile FROM user_settings "
            "WHERE user_id = :u ORDER BY 1"), {"u": email})]


_LAYOUT = {"preset": "engineer", "answered": "answered", "pins": ["/projects", "/tasks"]}
_ADDRESS = {"name": "VJ", "formality": "casual", "greeting": "brief"}


# ── The other columns of the row keep each other ───────────────────────────


async def test_the_layout_my_day_and_number_system_saves_keep_the_profile(db) -> None:
    from gateway.routes.admin.me import get_my_shell, put_my_shell
    from gateway.routes.settings import MemberNumberSystemPut, put_member_number_system
    from gateway.routes.shell.prefs import get_feed_prefs, put_feed_prefs

    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "address", _ADDRESS)
        await _add_fact(db.org_a, user, "Travels every Friday", "routine")
        with _tenant(db.org_a):
            await put_my_shell(body=_LAYOUT, user=user)
            await put_feed_prefs(body={"key": "mutes", "value": ["overdue"]}, user=user)
            await put_member_number_system(
                body=MemberNumberSystemPut(value="international"), user=user)
        profile = await _get(db.org_a, user)
        # And the profile write keeps each of them.
        await _put(db.org_a, user, "learning", False)
        with _tenant(db.org_a):
            shell = await get_my_shell(user=user)
            prefs = await get_feed_prefs(user=user)
    assert profile["address"] == _ADDRESS
    assert [f["text"] for f in profile["facts"]] == ["Travels every Friday"]
    assert (shell["preset"], shell["pins"]) == ("engineer", ["/projects", "/tasks"])
    assert prefs == {"mutes": ["overdue"]}
    with db.admin_engine.connect() as c:
        number = c.execute(text(
            "SELECT number_system FROM user_settings WHERE user_id = :u"),
            {"u": email}).scalar()
    assert number == "international"


async def test_a_second_key_does_not_erase_the_first(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "address", _ADDRESS)
        await _put(db.org_a, user, "style", {"length": "short", "notes": ["Hinglish is fine"]})
        answer = await _put(db.org_a, user, "learning", False)
        again = await _put(db.org_a, user, "address", {"name": "Vijay"})
        read = await _get(db.org_a, user)
    assert answer["address"] == _ADDRESS
    assert answer["style"] == {"length": "short", "notes": ["Hinglish is fine"]}
    assert again == read
    assert read["address"] == {"name": "Vijay"}
    assert read["style"] == {"length": "short", "notes": ["Hinglish is fine"]}
    assert read["learning"] is False
    [(org, stored)] = _stored(db, email)
    assert org == db.org_a
    assert stored["v"] == 1
    assert stored["learning"] is False


async def test_a_member_with_no_row_gets_the_calendar_seed(db) -> None:
    from gateway.routes.tasks.settings import get_user_settings

    email = _address()
    db.made.append(email)
    user = _member(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at, working_hours) "
            "VALUES (gen_random_uuid(), 'Profile Seed', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), "
            "CAST(:wh AS jsonb))"),
            {"e": email, "k": f"manual:{email}", "o": db.org_a,
             "wh": '{"start": "11:00", "end": "15:00"}'})
    assert _stored(db, email) == []
    async with tenant_engine_scope(_app_dsn(db)):
        await _add_fact(db.org_a, user, "First fact")
        with _tenant(db.org_a):
            after = await get_user_settings(user=user)
    # The seed is not the column default, or this test would prove nothing.
    assert (after.day_start_hour, after.day_end_hour) == (10, 16)
    [(org, stored)] = _stored(db, email)
    assert org == db.org_a
    assert stored["v"] == 1
    assert [f["text"] for f in stored["facts"]] == ["First fact"]


# ── The tenant wall ────────────────────────────────────────────────────────


async def test_another_organization_neither_reads_nor_writes_the_profile(
    db, app_engine,  # noqa: F811
) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "address", _ADDRESS)
        fact = await _add_fact(db.org_a, user, "Org A fact")
        # The same address in org B has the empty profile there.
        assert await _get(db.org_b, user) == _ap().empty_profile()
        # Org B cannot edit or delete org A's fact by its id.
        for call in (
            lambda: _ap().edit_fact(fact["id"], body={"text": "taken"}, user=user),
            lambda: _ap().delete_fact(fact["id"], user=user),
        ):
            with _tenant(db.org_b), pytest.raises(HTTPException) as exc:
                await call()
            assert exc.value.status_code == 404
        # A save in org B makes org B's own row, and org A's stays.
        await _put(db.org_b, user, "address", {"name": "Bee"})
        a = await _get(db.org_a, user)
        b = await _get(db.org_b, user)
    assert a["address"] == _ADDRESS
    assert [f["text"] for f in a["facts"]] == ["Org A fact"]
    assert b["address"] == {"name": "Bee"} and b["facts"] == []
    assert [org for org, _ in _stored(db, email)] == sorted([db.org_a, db.org_b])

    # A raw session bound to org B sees no row of org A, and cannot change it.
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": db.org_b})
        seen = c.execute(text(
            "SELECT count(*) FROM user_settings "
            "WHERE user_id = :u AND organization_id = CAST(:a AS uuid)"),
            {"u": email, "a": db.org_a}).scalar()
        changed = c.execute(text(
            "UPDATE user_settings SET assistant_profile = NULL "
            "WHERE user_id = :u AND organization_id = CAST(:a AS uuid)"),
            {"u": email, "a": db.org_a}).rowcount
    assert (seen, changed) == (0, 0)
    assert dict(_stored(db, email))[db.org_a]["address"] == _ADDRESS


async def test_forget_in_one_org_keeps_the_other_org_s_profile(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "address", {"name": "Ay"})
        await _put(db.org_b, user, "address", {"name": "Bee"})
        with _tenant(db.org_b):
            await _ap().delete_assistant_profile(user=user)
        a = await _get(db.org_a, user)
        b = await _get(db.org_b, user)
    assert a["address"] == {"name": "Ay"}
    assert b == _ap().empty_profile()
    stored = dict(_stored(db, email))
    assert stored[db.org_b] is None
    assert stored[db.org_a]["address"] == {"name": "Ay"}


# ── Items ──────────────────────────────────────────────────────────────────


async def test_a_fact_and_an_alias_are_edited_and_deleted_in_place(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    # PZ-5: an alias address must be a person of the org.
    raj, moved_to = _address(), _address()
    db.made += [raj, moved_to]
    with db.admin_engine.begin() as c:
        for who in (raj, moved_to):
            c.execute(text(
                "INSERT INTO people (id, name, email, status, skills, source, "
                "source_key, organization_id, updated_by, updated_at) "
                "VALUES (gen_random_uuid(), 'Raj', :e, 'active', ARRAY[]::text[], "
                "'manual', :k, CAST(:o AS uuid), 'test', now())"),
                {"e": who, "k": f"manual:{who}", "o": db.org_a})
    async with tenant_engine_scope(_app_dsn(db)):
        one = await _add_fact(db.org_a, user, "One", "about")
        two = await _add_fact(db.org_a, user, "Two", "work")
        three = await _add_fact(db.org_a, user, "Three", "routine")
        with _tenant(db.org_a):
            edited = await _ap().edit_fact(two["id"], body={"kind": "preference"}, user=user)
            await _ap().delete_fact(one["id"], user=user)
            with pytest.raises(HTTPException) as gone:
                await _ap().delete_fact(one["id"], user=user)
            with pytest.raises(HTTPException) as absent:
                await _ap().edit_fact("f-none", body={"text": "x"}, user=user)
            alias = await _ap().add_alias(body={"alias": "Raj", "email": raj}, user=user)
            moved = await _ap().edit_alias(alias["id"], body={"email": moved_to}, user=user)
        read = await _get(db.org_a, user)
        with _tenant(db.org_a):
            await _ap().delete_alias(alias["id"], user=user)
        after = await _get(db.org_a, user)
    assert (gone.value.status_code, absent.value.status_code) == (404, 404)
    assert edited == {**two, "kind": "preference"}
    # The order stays, and the third fact is untouched.
    assert read["facts"] == [{**two, "kind": "preference"}, three]
    assert moved == {"id": alias["id"], "alias": "Raj", "email": moved_to}
    assert read["aliases"] == [moved]
    assert after["aliases"] == []
    assert after["facts"] == read["facts"]


async def test_concurrent_adds_lose_nothing(db) -> None:
    """Each add is one statement under the row lock. A read-modify-write
    across two statements would lose some of these."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        # The first wave races the INSERT of the row itself.
        await asyncio.gather(*(_add_fact(db.org_a, user, f"fact {i}") for i in range(6)))
        await asyncio.gather(*(_add_fact(db.org_a, user, f"fact {i}") for i in range(6, 12)))
        read = await _get(db.org_a, user)
    assert sorted(f["text"] for f in read["facts"]) == sorted(f"fact {i}" for i in range(12))
    assert len({f["id"] for f in read["facts"]}) == 12
    [(_, stored)] = _stored(db, email)
    assert len(stored["facts"]) == 12


async def test_a_write_over_a_cap_rolls_back(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        # 15 facts of 300 characters hold about 5,500 characters.
        for i in range(15):
            await _add_fact(db.org_a, user, f"{i:02d}" + "x" * 298)
        before = _stored(db, email)
        with pytest.raises(HTTPException) as over:
            for i in range(15, 20):
                await _add_fact(db.org_a, user, f"{i:02d}" + "x" * 298)
        after_add = _stored(db, email)
        n = len(after_add[0][1]["facts"])
        with pytest.raises(HTTPException) as style:
            await _put(db.org_a, user, "style", {"notes": ["y" * 200] * 5})
    assert over.value.status_code == 422
    assert over.value.detail == "profile exceeds 6000 characters"
    assert 15 <= n < 20
    assert len(before[0][1]["facts"]) == 15
    assert _ap().profile_size(after_add[0][1]) <= _ap().PROFILE_MAX
    assert style.value.status_code == 422
    assert _stored(db, email) == after_add


# ── Forget everything ──────────────────────────────────────────────────────


async def test_forget_sets_the_column_null_and_keeps_the_row(db) -> None:
    from gateway.routes.shell.prefs import put_feed_prefs

    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "address", _ADDRESS)
        with _tenant(db.org_a):
            await put_feed_prefs(body={"key": "mutes", "value": ["overdue"]}, user=user)
            await _ap().delete_assistant_profile(user=user)
        read = await _get(db.org_a, user)
    assert read == _ap().empty_profile()
    with db.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT assistant_profile, my_day_prefs FROM user_settings "
            "WHERE user_id = :u"), {"u": email}).one()
    assert tuple(row) == (None, {"mutes": ["overdue"]})


def test_an_unbound_write_is_refused(db, app_engine) -> None:  # noqa: F811
    ap = _ap()
    from gateway.routes.tasks.settings import upsert_settings_sql
    from sqlalchemy.exc import DBAPIError

    email = _address()
    db.made.append(email)
    sql = upsert_settings_sql(["assistant_profile"], lambda k: ap._NEW_KEY,
                              update=["assistant_profile"],
                              merge={"assistant_profile": ap._MERGE_KEY})
    with pytest.raises(DBAPIError), app_engine.begin() as c:
        c.execute(text(sql), {"uid": email, "pkey": "learning", "pval": "false"})
    assert _stored(db, email) == []


# ── The column ─────────────────────────────────────────────────────────────


def test_the_column_is_nullable_jsonb_with_no_default(db) -> None:
    with db.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'user_settings' AND column_name = 'assistant_profile'"
        )).one()
    assert tuple(row) == ("jsonb", "YES", None)


# ── A write from the chat (PZ-6a, X-Profile-Via) ───────────────────────────


def _person(db, email: str) -> None:
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, "
            "source_key, organization_id, updated_by, updated_at) "
            "VALUES (gen_random_uuid(), 'Raj', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now())"),
            {"e": email, "k": f"manual:{email}", "o": db.org_a})


async def test_a_chat_write_refuses_with_learning_off_and_a_settings_write_works(db) -> None:
    """The header can only restrict: with ``learning`` off every chat write
    is a 409 and the stored profile does not move. Settings (no header)
    still writes, and labels its fact ``settings``."""
    email, raj = _address(), _address()
    db.made += [email, raj]
    _person(db, raj)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        kept = await _add_fact(db.org_a, user, "Kept", "about")
        with _tenant(db.org_a):
            alias = await _ap().add_alias(body={"alias": "Raj", "email": raj}, user=user)
        await _put(db.org_a, user, "learning", False)
        before = _stored(db, email)
        ap = _ap()
        refused: list[int] = []
        with _tenant(db.org_a):
            for call in (
                lambda: ap.add_fact(body={"text": "New", "kind": "about"}, user=user,
                                    x_profile_via="chat"),
                lambda: ap.edit_fact(kept["id"], body={"text": "x"}, user=user,
                                     x_profile_via="chat"),
                lambda: ap.delete_fact(kept["id"], user=user, x_profile_via="whatsapp"),
                lambda: ap.put_assistant_profile(
                    body={"key": "style", "value": {"length": "short"}}, user=user,
                    x_profile_via="chat"),
                lambda: ap.add_alias(body={"alias": "Sam", "email": raj}, user=user,
                                     x_profile_via="chat"),
                lambda: ap.edit_alias(alias["id"], body={"alias": "R"}, user=user,
                                      x_profile_via="chat"),
                lambda: ap.delete_alias(alias["id"], user=user, x_profile_via="chat"),
            ):
                with pytest.raises(HTTPException) as exc:
                    await call()
                refused.append(exc.value.status_code)
                assert exc.value.detail == "learning_off"
        assert _stored(db, email) == before
        settings = await _add_fact(db.org_a, user, "From Settings", "work")
    assert refused == [409] * 7
    assert settings["source"] == "settings"
    assert _stored_learning(db, email) is False


def _stored_learning(db, email: str) -> object:
    ((_org, stored),) = _stored(db, email)
    return stored.get("learning")


async def test_a_chat_fact_takes_its_channel_as_source(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            chat = await _ap().add_fact(body={"text": "A", "kind": "about"}, user=user,
                                        x_profile_via="chat")
            wa = await _ap().add_fact(body={"text": "B", "kind": "about"}, user=user,
                                      x_profile_via="whatsapp")
            with pytest.raises(HTTPException) as bad:
                await _ap().add_fact(body={"text": "C", "kind": "about"}, user=user,
                                     x_profile_via="settings")
            with pytest.raises(HTTPException) as switch:
                await _ap().put_assistant_profile(body={"key": "learning", "value": True},
                                                  user=user, x_profile_via="chat")
        read = await _get(db.org_a, user)
    assert (chat["source"], wa["source"]) == ("chat", "whatsapp")
    assert (bad.value.status_code, switch.value.status_code) == (422, 422)
    assert [f["text"] for f in read["facts"]] == ["A", "B"]



# ── PZ-6a fix round 1: forget-all, the no-row race, and the last change ────


async def test_a_chat_forget_all_is_a_422_and_changes_nothing(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _add_fact(db.org_a, user, "Kept", "about")
        before = _stored(db, email)
        with _tenant(db.org_a), pytest.raises(HTTPException) as exc:
            await _ap().delete_assistant_profile(user=user, x_profile_via="chat")
    assert exc.value.status_code == 422
    assert _stored(db, email) == before


async def test_a_chat_write_never_lands_on_a_row_with_learning_off(db, monkeypatch) -> None:
    """The no-row race: the gate reads no row (learning defaults to true),
    then a Settings write makes the row with the switch off. The chat
    upsert carries the guard, so it changes nothing and is a 409."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    ap = _ap()
    real_gate = ap._learning_gate

    async def _gate_then_switch_off(dbs, uid, via) -> None:
        await real_gate(dbs, uid, via)
        with db.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
                "VALUES (CAST(:o AS uuid), :u, CAST('{\"learning\": false}' AS jsonb))"),
                {"o": db.org_a, "u": email})

    monkeypatch.setattr(ap, "_learning_gate", _gate_then_switch_off)
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a), pytest.raises(HTTPException) as exc:
            await ap.add_fact(body={"text": "Raced", "kind": "about"}, user=user,
                              x_profile_via="chat")
    assert (exc.value.status_code, exc.value.detail) == (409, "learning_off")
    ((_org, stored),) = _stored(db, email)
    assert stored == {"learning": False}


async def _chat(call, org: str):
    with _tenant(org):
        return await call()


async def _undo(org: str, user: UserContext) -> dict:
    return await _chat(lambda: _ap().undo_assistant_profile(user=user, x_profile_via="chat"),
                       org)


async def test_set_then_undo_restores_the_prior_whole_object(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    ap = _ap()
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, user, "style", {"length": "short", "language": "en"})
        await _chat(lambda: ap.put_assistant_profile(
            body={"key": "style", "value": {"length": "detailed"}}, user=user,
            x_profile_via="chat"), db.org_a)
        ((_o, mid),) = _stored(db, email)
        assert mid["last_change"]["prior"] == {"length": "short", "language": "en"}
        assert "last_change" not in await _get(db.org_a, user)
        done = await _undo(db.org_a, user)
        read = await _get(db.org_a, user)
    assert done == {"undone": {"op": "put", "key": "style"}}
    assert read["style"] == {"length": "short", "language": "en"}
    ((_o, stored),) = _stored(db, email)
    assert "last_change" not in stored


async def test_alias_change_and_remove_then_undo_restore(db) -> None:
    email, raj, raj2 = _address(), _address(), _address()
    db.made += [email, raj, raj2]
    _person(db, raj)
    _person(db, raj2)
    user = _member(email)
    ap = _ap()
    async with tenant_engine_scope(_app_dsn(db)):
        with _tenant(db.org_a):
            alias = await ap.add_alias(body={"alias": "Raj", "email": raj}, user=user)
        await _chat(lambda: ap.edit_alias(alias["id"], body={"email": raj2}, user=user,
                                          x_profile_via="chat"), db.org_a)
        await _undo(db.org_a, user)
        changed_back = (await _get(db.org_a, user))["aliases"]
        await _chat(lambda: ap.delete_alias(alias["id"], user=user, x_profile_via="chat"),
                    db.org_a)
        assert (await _get(db.org_a, user))["aliases"] == []
        await _undo(db.org_a, user)
        removed_back = (await _get(db.org_a, user))["aliases"]
    assert changed_back == [{"id": alias["id"], "alias": "Raj", "email": raj}]
    assert removed_back == changed_back


async def test_forget_then_undo_re_adds_the_text(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    ap = _ap()
    async with tenant_engine_scope(_app_dsn(db)):
        fact = await _chat(lambda: ap.add_fact(body={"text": "I work from Pune", "kind": "work"},
                                               user=user, x_profile_via="chat"), db.org_a)
        await _chat(lambda: ap.delete_fact(fact["id"], user=user, x_profile_via="chat"),
                    db.org_a)
        await _undo(db.org_a, user)
        read = await _get(db.org_a, user)
    assert [(f["id"], f["text"], f["source"]) for f in read["facts"]] == [
        (fact["id"], "I work from Pune", "chat")]


async def test_a_stale_change_and_a_second_undo_refuse(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    ap = _ap()
    async with tenant_engine_scope(_app_dsn(db)):
        await _chat(lambda: ap.add_fact(body={"text": "One", "kind": "about"}, user=user,
                                        x_profile_via="chat"), db.org_a)
        two = await _chat(lambda: ap.add_fact(body={"text": "Two", "kind": "about"},
                                              user=user, x_profile_via="chat"), db.org_a)
        await _undo(db.org_a, user)
        assert [f["text"] for f in (await _get(db.org_a, user))["facts"]] == ["One"]
        with pytest.raises(HTTPException) as again:
            await _undo(db.org_a, user)
        await _chat(lambda: ap.add_fact(body={"text": "Three", "kind": "about"}, user=user,
                                        x_profile_via="chat"), db.org_a)
        with db.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE user_settings SET assistant_profile = jsonb_set(assistant_profile, "
                "ARRAY['last_change', 'at'], to_jsonb(1)) WHERE user_id = :u"), {"u": email})
        with pytest.raises(HTTPException) as stale:
            await _undo(db.org_a, user)
        with pytest.raises(HTTPException) as web:
            await _chat(lambda: ap.undo_assistant_profile(user=user), db.org_a)
        read = await _get(db.org_a, user)
    assert two["text"] == "Two"
    assert (again.value.status_code, again.value.detail) == (404, "nothing_to_undo")
    assert (stale.value.status_code, stale.value.detail) == (404, "too_old")
    assert web.value.status_code == 422
    assert [f["text"] for f in read["facts"]] == ["One", "Three"]


async def test_an_undo_after_a_settings_edit_of_the_saved_fact_refuses(db) -> None:
    """PZ-6a fix round 2 (P2): the chat saved a fact, then the member
    edited it in Settings. A spoken undo must not delete the edited fact.
    The record holds the item as written, and the undo compares it."""
    email = _address()
    db.made.append(email)
    user = _member(email)
    ap = _ap()
    async with tenant_engine_scope(_app_dsn(db)):
        fact = await _chat(lambda: ap.add_fact(body={"text": "I like tea", "kind": "preference"},
                                               user=user, x_profile_via="chat"), db.org_a)
        mid = _stored(db, email)[0][1]
        assert mid["last_change"]["after"] == fact
        await _chat(lambda: ap.edit_fact(fact["id"], body={"text": "I like green tea"},
                                         user=user), db.org_a)
        with pytest.raises(HTTPException) as changed:
            await _undo(db.org_a, user)
        read = await _get(db.org_a, user)
    assert (changed.value.status_code, changed.value.detail) == (404, "changed_since")
    assert [(f["id"], f["text"]) for f in read["facts"]] == [(fact["id"], "I like green tea")]


async def test_an_undo_of_an_unchanged_saved_fact_removes_it(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    ap = _ap()
    async with tenant_engine_scope(_app_dsn(db)):
        await _chat(lambda: ap.add_fact(body={"text": "I like tea", "kind": "preference"},
                                        user=user, x_profile_via="chat"), db.org_a)
        await _undo(db.org_a, user)
        read = await _get(db.org_a, user)
    assert read["facts"] == []
