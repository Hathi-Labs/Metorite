"""The WhatsApp routes of the narrowing pipeline, on a real database. WS-48 N4.

Spec: ``project-docs/specs/data_narrowing_pipeline.md`` §9 N4. The READ step
of ``narrow_and_read`` reads up to 25 kept messages in the background, each one
with a small window of context. That is not the member opening the chats, so
it must change nothing: no row, no status, no "seen" receipt. It reads on
``GET /whatsapp/chats/{id}/messages`` with ``around``. NARROW reads on
``GET /whatsapp/search`` with the new filters. Both are SQL, so both run here.

R8: these tests run the REAL route functions against a REAL Postgres, under
FORCE RLS, as a non-privileged role (``promoted`` / ``app_engine`` of
``test_h3_rls_promotion_rehearsal.py``). With no
``TENANT_LADDER_DATABASE_URL`` they skip, and a skip is not a pass.

Mutations this file catches (R7), each one run red before the change:

* the ``around`` read writes a row (for example it stamps the chat or the
  status) -> ``test_the_around_read_changes_no_row``;
* the window takes more than ``window`` on a side, or loses its order ->
  ``test_the_window_is_bounded_and_ordered``;
* the ``around`` read skips the owner check, or takes an anchor of another
  chat -> ``test_the_around_read_keeps_the_owner_scope``;
* a search filter is wrong SQL, or a ``websearch`` OR reads as AND ->
  ``test_each_search_filter_runs_on_postgres``;
* a filter widens the member scope -> ``test_the_search_never_shows_another_members_chat``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.whatsapp.transport import messages as messages_mod
from sqlalchemy import text

from tests.unit.test_email_keep_separate import _as_member, _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

#: The tables that a read must not change, keyed by the account.
_TABLES = ("wa_chats", "wa_messages", "wa_chat_status", "wa_commitments")


def _insert(conn: Any, sql: str, **params: Any) -> str:
    return str(conn.execute(text(sql), params).scalar_one())


def _seed(p: Any, tag: str) -> dict[str, Any]:
    """One member with a chat and a group, and a stranger of the same org
    with a chat that matches every search."""
    owner, stranger = f"member-{tag}@wa-n4.test", f"stranger-{tag}@wa-n4.test"
    ids: dict[str, Any] = {"owner": owner, "stranger": stranger, "m": {}}
    with p.admin_engine.begin() as c:
        for who, key in ((owner, "acct"), (stranger, "stranger_acct")):
            ids[key] = _insert(
                c, "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
                "credentials_encrypted, organization_id) VALUES (:u, '+00 000 0000', :p, "
                "'enc', CAST(:o AS uuid)) RETURNING id",
                u=who, p=f"pn-{key}-{tag}", o=p.org_b)
        for key, acct, kind, name in (
            ("dm", "acct", "dm", f"Ravi Traders {tag}"),
            ("group", "acct", "group", f"Dealers North {tag}"),
            ("stranger_dm", "stranger_acct", "dm", f"Ravi Traders {tag}"),
        ):
            ids[key] = _insert(
                c, "INSERT INTO wa_chats (account_id, wa_chat_id, kind, name, "
                "organization_id) VALUES (CAST(:a AS uuid), :w, :k, :n, CAST(:o AS uuid)) "
                "RETURNING id",
                a=ids[acct], w=f"{key}-{tag}", k=kind, n=name, o=p.org_b)
        rows = [
            # (key, chat, direction, kind, body, transcript, minutes)
            ("d0", "dm", "in", "text", "Good morning", None, 0),
            ("d1", "dm", "out", "text", "Morning, how can I help", None, 1),
            ("d2", "dm", "in", "text", "What is the price of the pump", None, 2),
            ("d3", "dm", "out", "text", "I will send the rate today", None, 3),
            ("d4", "dm", "in", "voice", "", "please send it fast", 4),
            ("d5", "dm", "in", "image", "photo of the cracked box", None, 5),
            ("d6", "dm", "in", "text", "Thanks", None, 6),
            ("g0", "group", "in", "text", "October stock is low", None, 10),
            ("g1", "group", "in", "text", "What rate for 50 units", None, 11),
            ("s0", "stranger_dm", "in", "text", "What is the price of the pump", None, 2),
        ]
        for key, chat, direction, kind, body, transcript, minutes in rows:
            acct = "stranger_acct" if chat.startswith("stranger") else "acct"
            ids["m"][key] = _insert(
                c, "INSERT INTO wa_messages (account_id, chat_id, wa_message_id, direction, "
                "sender, kind, body_text, transcript_text, sent_at, organization_id) VALUES "
                "(CAST(:a AS uuid), CAST(:ch AS uuid), :w, :d, CAST(:s AS jsonb), :k, :b, :t, "
                ":at, CAST(:o AS uuid)) RETURNING id",
                a=ids[acct], ch=ids[chat], w=f"wamid.{key}.{tag}", d=direction,
                s='{"name": "Sunil Kumar"}', k=kind, b=body, t=transcript,
                at=T0 + timedelta(minutes=minutes), o=p.org_b)
    return ids


def _purge(p: Any, tag: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM wa_accounts WHERE user_id LIKE :u"),
                  {"u": f"%-{tag}@wa-n4.test"})


def _snapshot(p: Any, ids: dict[str, Any]) -> dict[str, str | None]:
    """A digest of every row of the member's account, as the superuser."""
    out: dict[str, str | None] = {}
    with p.admin_engine.connect() as c:
        for table in _TABLES:
            out[table] = c.execute(text(
                f"SELECT md5(string_agg(t::text, ',' ORDER BY t::text)) FROM {table} t "
                "WHERE account_id = CAST(:a AS uuid)"), {"a": ids["acct"]}).scalar_one()
        out["wa_accounts"] = c.execute(text(
            "SELECT md5(t::text) FROM wa_accounts t WHERE id = CAST(:a AS uuid)"),
            {"a": ids["acct"]}).scalar_one()
    return out


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


@pytest.fixture
def seeded(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    tag = uuid.uuid4().hex[:8]
    ids = _seed(promoted, tag)
    try:
        yield promoted, ids
    finally:
        _purge(promoted, tag)


async def _around(p: Any, ids: dict[str, Any], chat: str, key: str, window: int,
                  email: str | None = None, org: str | None = None) -> list[Any]:
    org = org or p.org_b
    async with _as_member(p, org):
        return await messages_mod.list_messages(
            ids[chat], limit=100, around=ids["m"][key], window=window,
            user=_user(email or ids["owner"], org))


async def _search(p: Any, ids: dict[str, Any], **kwargs: Any) -> list[str]:
    """The keys of the messages that a search gives, in order."""
    by_id = {v: k for k, v in ids["m"].items()}
    params = {"q": None, "limit": 200, "hybrid": False, **kwargs}
    async with _as_member(p, p.org_b):
        got = await messages_mod.search_messages(**params, user=_user(ids["owner"], p.org_b))
    return [by_id.get(m.id, m.id) for m in got]


# ── READ changes nothing ────────────────────────────────────────────────────


async def test_the_around_read_changes_no_row(seeded) -> None:
    p, ids = seeded
    before = _snapshot(p, ids)
    assert all(v is not None for k, v in before.items() if k in {"wa_chats", "wa_messages"})
    got = await _around(p, ids, "dm", "d3", 2)
    assert [m.body_text or m.transcript_text for m in got] == [
        "Morning, how can I help", "What is the price of the pump",
        "I will send the rate today", "please send it fast", "photo of the cracked box"]
    assert {m.chat_name for m in got} == {f"Ravi Traders {ids['owner'][7:15]}"}
    assert {m.chat_kind for m in got} == {"dm"}
    # The plain thread read, as the app sends it, changes nothing too.
    async with _as_member(p, p.org_b):
        await messages_mod.list_messages(ids["dm"], limit=100, around=None, window=2,
                                         user=_user(ids["owner"], p.org_b))
    assert _snapshot(p, ids) == before


async def test_the_window_is_bounded_and_ordered(seeded) -> None:
    p, ids = seeded
    by_id = {v: k for k, v in ids["m"].items()}
    assert [by_id[m.id] for m in await _around(p, ids, "dm", "d3", 0)] == ["d3"]
    assert [by_id[m.id] for m in await _around(p, ids, "dm", "d0", 2)] == ["d0", "d1", "d2"]
    assert [by_id[m.id] for m in await _around(p, ids, "dm", "d6", 1)] == ["d5", "d6"]
    assert [by_id[m.id] for m in await _around(p, ids, "dm", "d3", 10)] == [
        "d0", "d1", "d2", "d3", "d4", "d5", "d6"]


async def test_the_around_read_keeps_the_owner_scope(seeded) -> None:
    p, ids = seeded
    before = _snapshot(p, ids)
    cases = [
        # Another member of the same org reads the member's chat.
        ("dm", "d2", ids["stranger"], p.org_b),
        # The member reads the stranger's chat.
        ("stranger_dm", "s0", ids["owner"], p.org_b),
        # The member of another org, with the same email.
        ("dm", "d2", ids["owner"], p.org_a),
    ]
    for chat, key, email, org in cases:
        with pytest.raises(HTTPException) as caught:
            await _around(p, ids, chat, key, 2, email=email, org=org)
        assert caught.value.status_code == 404, (chat, key, email)
    # An anchor of another chat, in a chat the member owns.
    async with _as_member(p, p.org_b):
        with pytest.raises(HTTPException) as caught:
            await messages_mod.list_messages(
                ids["dm"], limit=100, around=ids["m"]["s0"], window=2,
                user=_user(ids["owner"], p.org_b))
    assert caught.value.status_code == 404
    assert _snapshot(p, ids) == before


# ── NARROW: each filter is real SQL ─────────────────────────────────────────


async def test_each_search_filter_runs_on_postgres(seeded) -> None:
    p, ids = seeded
    # websearch: OR finds either word. The old grammar ANDs them.
    assert sorted(await _search(p, ids, q="price OR rate", websearch=True)) == [
        "d2", "d3", "g1"]
    assert await _search(p, ids, q="price rate", websearch=False) == []
    assert await _search(p, ids, q="price", websearch=False) == ["d2"]
    # A quoted phrase and a minus, in the same grammar.
    assert await _search(p, ids, q='"the rate"', websearch=True) == ["d3"]
    assert await _search(p, ids, q='"the rate" -send', websearch=True) == []
    # contact: a part of the chat name or the sender name, any case.
    assert sorted(await _search(p, ids, contact="dealers north")) == ["g0", "g1"]
    assert len(await _search(p, ids, contact="SUNIL")) == 9
    assert await _search(p, ids, contact="100%_sure") == []
    # The other filters, each alone and newest first.
    assert await _search(p, ids, chat_kind="group") == ["g1", "g0"]
    assert await _search(p, ids, chat_id=ids["dm"], direction="out") == ["d3", "d1"]
    assert await _search(p, ids, has_media=True) == ["d5", "d4"]
    assert len(await _search(p, ids, has_media=False)) == 7
    window = await _search(p, ids, sent_after=T0 + timedelta(minutes=2),
                           sent_before=T0 + timedelta(minutes=4))
    assert window == ["d4", "d3", "d2"]  # both bounds are inclusive
    # A voice note is found by its transcript, with the filters.
    assert await _search(p, ids, q="fast", websearch=True, has_media=True) == ["d4"]
    # The row names its chat.
    async with _as_member(p, p.org_b):
        [row] = await messages_mod.search_messages(
            q="stock", limit=200, hybrid=False, user=_user(ids["owner"], p.org_b))
    assert row.chat_kind == "group" and row.chat_name.startswith("Dealers North")


async def test_the_search_never_shows_another_members_chat(seeded) -> None:
    p, ids = seeded
    stranger_ids = {ids["m"]["s0"]}
    for kwargs in (
        {"q": "price OR pump", "websearch": True},
        {"contact": "Ravi Traders"},
        {"chat_kind": "dm"},
        {"chat_id": ids["stranger_dm"]},
        {"has_media": False, "direction": "in"},
    ):
        got = await _search(p, ids, **kwargs)
        assert not set(got) & {"s0"} and not set(got) & stranger_ids, kwargs
    assert await _search(p, ids, chat_id=ids["stranger_dm"]) == []
    # Another org sees nothing of org B, with any filter.
    async with _as_member(p, p.org_a):
        got = await messages_mod.search_messages(
            q=None, limit=200, hybrid=False, chat_kind="dm",
            user=_user(ids["owner"], p.org_a))
    assert got == []
