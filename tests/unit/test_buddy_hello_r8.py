"""R8 - ``POST /chat/sessions/{id}/buddy-hello`` against a REAL Postgres
(WS-BUD BUD-1, D177, ``onboarding.md`` section 13).

The real handler, the real room check, the real minter, the real message
seam and the real ``stamp-first`` SQL, as the NON-privileged role of the
phase-4 rehearsal, on a database with the whole ladder and two organizations.

What this proves:

* a fresh chat gets ONE sealed assistant row: the copy with the member's first
  name, one ``generative_ui`` event of the ``buddyHello`` template with three
  chips, the agent as author, the member as run member, in the member's org;
* the chat is named "Welcome";
* the stamp is taken first: a second chat (a second tab) answers 204 and gets
  no row, and several calls at the same moment make exactly one row;
* ``GUIDE_ORGS`` off answers 404, writes no row and takes no stamp;
* another member's chat answers 404 and gets no row;
* a member with no display name gets "Hi there";
* a failed post gives the stamp back, so the next visit tries again.

**The mutation (R7).** Commit first, restore with git after. In
``post_buddy_hello`` post before ``_claim_hello`` and drop the ``won`` test:
``test_two_tabs_at_once_make_one_row`` goes red. Drop the ``_set_title``
call: ``test_a_fresh_chat_gets_one_sealed_hello_named_welcome`` goes red.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_buddy_hello_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@bud1.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def graph_as_app(world, app_engine, monkeypatch):  # noqa: F811
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield world
    finally:
        eng.dispose()


@pytest.fixture
def guide_on(monkeypatch):
    import acb_common.guide_flags as gf

    monkeypatch.setattr(gf, "guide_orgs", lambda: frozenset({"*"}))


def _member(email: str, org: str):
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access(["feature:chat"], roles=["member"]),
    )


def _seed(ns, email: str, org: str, name: str | None) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :n, 'employee', 'active', :o) ON CONFLICT DO NOTHING"),
            {"e": email, "n": name, "o": org})


def _unseed(ns, email: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
        c.execute(text("DELETE FROM app_user WHERE email = :u"), {"u": email})


def _new_session(sid: str, email: str, org: str) -> None:
    from gateway.routes.chat import SessionUpsertRequest, _upsert_session

    _upsert_session(email, SessionUpsertRequest(id=sid), organization_id=org)


def _rows(ns, sid: str) -> list:
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, role, content, custom_events, author_email, author_kind, "
            "run_member_email, organization_id::text AS org, run_final_at IS NOT NULL AS sealed "
            "FROM chat_message WHERE session_id = :s ORDER BY timestamp_ms"), {"s": sid}).fetchall()


def _title(ns, sid: str):
    with ns.admin_engine.connect() as c:
        return c.execute(text("SELECT title FROM chat_session WHERE id = :s"), {"s": sid}).scalar()


def _stamp(ns, email: str):
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT shell_prefs->>'buddy_hello_at' FROM user_settings WHERE user_id = :u"),
            {"u": email}).scalar()


async def _post(user, sid: str):
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.chat_brief import post_buddy_hello

    token = bind_tenant(user.organization_id)
    try:
        return await post_buddy_hello(sid, Response(), user=user)
    finally:
        release_tenant(token)


def _status(got) -> int:
    return got.status_code if isinstance(got, Response) else 200


def _dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def test_the_copy_is_one_constant_with_the_first_name() -> None:
    from gateway.routes.chat_brief import HELLO_COPY, hello_text

    assert hello_text("Priya Shah").startswith("Hi Priya, welcome to Metorite.")
    assert hello_text("  ").startswith("Hi there, welcome to Metorite.")
    assert hello_text(None) == hello_text("")
    assert hello_text("Priya") == HELLO_COPY.format(name="Priya")
    for banned in ("model", "claude", "gpt", "jev", "orchestrator"):
        assert banned not in HELLO_COPY.lower()


async def test_a_fresh_chat_gets_one_sealed_hello_named_welcome(graph_as_app, guide_on):
    ns = graph_as_app
    org, email = ns.org_a, _address("alice")
    _seed(ns, email, org, "Alice Anders")
    sid, sid2 = f"bud1-{uuid.uuid4().hex[:12]}", f"bud1-{uuid.uuid4().hex[:12]}"
    user = _member(email, org)
    try:
        _new_session(sid, email, org)
        _new_session(sid2, email, org)
        async with tenant_engine_scope(_dsn(ns)):
            first = await _post(user, sid)
            # A second tab mints its own session: the stamp stops it.
            other = await _post(user, sid2)
        assert _status(first) == 200
        assert first["posted"] is True and first["title"] == "Welcome"
        assert _status(other) == 204
        [row] = _rows(ns, sid)
        assert (row.role, row.author_kind, row.author_email, row.run_member_email) == (
            "assistant", "agent", "orchestrator", email)
        assert row.content.startswith("Hi Alice, welcome to Metorite.")
        assert row.org == org and row.sealed is True
        [event] = row.custom_events
        props = event["value"]["props"]
        assert event["name"] == "generative_ui" and props["name"] == "buddyHello"
        assert [c["label"] for c in props["data"]["chips"]] == [
            "Show me around", "What can you do here?", "I'll look around"]
        assert _title(ns, sid) == "Welcome"
        assert _rows(ns, sid2) == []
        assert _stamp(ns, email) is not None
    finally:
        _unseed(ns, email)


async def test_two_tabs_at_once_make_one_row(graph_as_app, guide_on):
    ns = graph_as_app
    org, email = ns.org_a, _address("bob")
    _seed(ns, email, org, "Bob")
    sids = [f"bud1-{uuid.uuid4().hex[:12]}" for _ in range(4)]
    user = _member(email, org)
    try:
        for sid in sids:
            _new_session(sid, email, org)
        async with tenant_engine_scope(_dsn(ns)):
            got = await asyncio.gather(*[_post(user, sid) for sid in sids])
        assert sorted(_status(g) for g in got) == [200, 204, 204, 204]
        assert sum(len(_rows(ns, sid)) for sid in sids) == 1
    finally:
        _unseed(ns, email)


async def test_the_switch_off_answers_404_and_takes_no_stamp(graph_as_app, monkeypatch):
    import acb_common.guide_flags as gf

    monkeypatch.setattr(gf, "guide_orgs", lambda: frozenset())
    ns = graph_as_app
    org, email = ns.org_a, _address("cara")
    _seed(ns, email, org, "Cara")
    sid = f"bud1-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(_dsn(ns)):
            with pytest.raises(HTTPException) as err:
                await _post(_member(email, org), sid)
        assert err.value.status_code == 404
        assert _rows(ns, sid) == [] and _stamp(ns, email) is None
    finally:
        _unseed(ns, email)


async def test_another_member_s_chat_is_404_and_a_member_with_no_name_is_greeted(
        graph_as_app, guide_on):
    ns = graph_as_app
    org, owner, intruder = ns.org_a, _address("dan"), _address("eve")
    _seed(ns, owner, org, "Dan")
    _seed(ns, intruder, org, None)
    sid = f"bud1-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, owner, org)
        async with tenant_engine_scope(_dsn(ns)):
            with pytest.raises(HTTPException) as err:
                await _post(_member(intruder, org), sid)
            assert err.value.status_code == 404
            assert _rows(ns, sid) == [] and _stamp(ns, intruder) is None
            own = f"bud1-{uuid.uuid4().hex[:12]}"
            _new_session(own, intruder, org)
            got = await _post(_member(intruder, org), own)
        assert _status(got) == 200
        assert got["preview"].startswith("Hi there, welcome to Metorite.")
    finally:
        _unseed(ns, owner)
        _unseed(ns, intruder)


async def test_a_failed_post_gives_the_stamp_back(graph_as_app, guide_on, monkeypatch):
    from gateway.routes import chat_brief

    ns = graph_as_app
    org, email = ns.org_a, _address("fay")
    _seed(ns, email, org, "Fay")
    sid = f"bud1-{uuid.uuid4().hex[:12]}"
    user = _member(email, org)
    real_post = chat_brief._post_row
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(_dsn(ns)):
            monkeypatch.setattr(chat_brief, "_post_row", lambda *a, **k: False)
            with pytest.raises(HTTPException) as err:
                await _post(user, sid)
            assert err.value.status_code == 503
            assert _stamp(ns, email) is None
            monkeypatch.setattr(chat_brief, "_post_row", real_post)
            again = await _post(user, sid)
        assert _status(again) == 200
    finally:
        _unseed(ns, email)


async def test_a_failed_title_keeps_the_stamp_and_the_row(graph_as_app, guide_on, monkeypatch):
    """The row is posted, so a title fault must not release the stamp: a
    later visit would post a SECOND hello."""
    from gateway.routes import chat_brief

    ns = graph_as_app
    org, email = ns.org_a, _address("gus")
    _seed(ns, email, org, "Gus")
    sid, sid2 = f"bud1-{uuid.uuid4().hex[:12]}", f"bud1-{uuid.uuid4().hex[:12]}"
    user = _member(email, org)

    def _boom(*a, **k):
        raise RuntimeError("title away")

    try:
        _new_session(sid, email, org)
        _new_session(sid2, email, org)
        async with tenant_engine_scope(_dsn(ns)):
            monkeypatch.setattr(chat_brief, "_set_title", _boom)
            first = await _post(user, sid)
            later = await _post(user, sid2)
        assert _status(first) == 200 and first["posted"] is True
        assert _status(later) == 204
        assert len(_rows(ns, sid)) == 1 and _rows(ns, sid2) == []
        assert _stamp(ns, email) is not None
    finally:
        _unseed(ns, email)
