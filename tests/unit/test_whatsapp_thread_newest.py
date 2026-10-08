"""``read_whatsapp_chat`` reads the NEWEST messages of a chat. H-277.

The tool says that it reads "the recent messages". Its route,
``GET /whatsapp/chats/{id}/messages``, ordered ``sent_at ASC`` and then applied
the limit. So a chat of more than ``limit`` messages gave its OLDEST ones, and
the agent answered from old messages. The app's thread view reads the same
route, with the same fault. The route now takes the newest ``limit`` messages
and gives them oldest first, for reading.

R8: the route is SQL, so these tests run the REAL route on a REAL Postgres,
under FORCE RLS, as a non-privileged role (``promoted`` / ``app_engine`` of
``test_h3_rls_promotion_rehearsal.py``). The tool test calls the REAL tool,
with the agent's ``_get`` sent to the REAL route. With no
``TENANT_LADDER_DATABASE_URL`` they skip, and a skip is not a pass.

Mutations this file catches (R7), each one run red before the change:

* the route takes the oldest ``limit`` again (``ORDER BY sent_at ASC ...
  LIMIT``, the code before H-277) -> ``test_the_tool_reads_the_newest_twenty``
  and ``test_the_route_gives_the_newest_limit_in_reading_order``;
* the route takes the newest but gives them newest first (no outer order) ->
  the same two tests;
* a tie of ``sent_at`` cuts and orders on different rules ->
  ``test_a_tie_of_sent_at_keeps_one_order``;
* a message with no ``sent_at`` counts as the newest ->
  ``test_a_message_with_no_time_is_the_oldest``.
"""
from __future__ import annotations

import importlib.util
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from gateway.routes.whatsapp.transport import messages as messages_mod
from sqlalchemy import text

from tests.unit.test_email_keep_separate import _as_member, _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
AGENT_DIR = Path(__file__).resolve().parents[2] / "apps" / "agents" / "agent-whatsapp-assistant"
#: The thread route, as the agent's tool calls it.
_THREAD = re.compile(r"\A/whatsapp/chats/([^/]+)/messages\Z")


def _load_agents() -> Any:
    spec = importlib.util.spec_from_file_location("h277_whatsapp_agents", AGENT_DIR / "agents.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agents = _load_agents()


def _insert(conn: Any, sql: str, **params: Any) -> str:
    return str(conn.execute(text(sql), params).scalar_one())


def _seed(p: Any, tag: str, times: list[datetime | None]) -> dict[str, Any]:
    """One member with ONE chat. Message ``n`` (from 1) says "message n" and is
    sent at ``times[n - 1]``."""
    owner = f"member-{tag}@wa-h277.test"
    ids: dict[str, Any] = {"owner": owner, "m": {}}
    with p.admin_engine.begin() as c:
        ids["acct"] = _insert(
            c, "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, organization_id) VALUES (:u, '+00 000 0000', :p, "
            "'enc', CAST(:o AS uuid)) RETURNING id",
            u=owner, p=f"pn-h277-{tag}", o=p.org_b)
        ids["chat"] = _insert(
            c, "INSERT INTO wa_chats (account_id, wa_chat_id, kind, name, organization_id) "
            "VALUES (CAST(:a AS uuid), :w, 'dm', :n, CAST(:o AS uuid)) RETURNING id",
            a=ids["acct"], w=f"chat-h277-{tag}", n=f"Ravi Traders {tag}", o=p.org_b)
        for n, at in enumerate(times, start=1):
            ids["m"][n] = _insert(
                c, "INSERT INTO wa_messages (account_id, chat_id, wa_message_id, direction, "
                "sender, kind, body_text, sent_at, organization_id) VALUES "
                "(CAST(:a AS uuid), CAST(:ch AS uuid), :w, 'in', "
                "CAST('{\"name\": \"Sunil\"}' AS jsonb), 'text', :b, :at, CAST(:o AS uuid)) "
                "RETURNING id",
                a=ids["acct"], ch=ids["chat"], w=f"wamid.h277.{tag}.{n}",
                b=f"message {n}", at=at, o=p.org_b)
    return ids


def _purge(p: Any, tag: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM wa_accounts WHERE user_id = :u"),
                  {"u": f"member-{tag}@wa-h277.test"})


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


@pytest.fixture
def chat_of(promoted, app_engine):  # noqa: F811
    """A builder of one seeded chat. Each chat it builds is purged at the end."""
    _assert_non_priv(app_engine)
    tags: list[str] = []

    def build(times: list[datetime | None]) -> dict[str, Any]:
        tag = uuid.uuid4().hex[:8]
        tags.append(tag)
        return _seed(promoted, tag, times)

    try:
        yield promoted, build
    finally:
        for tag in tags:
            _purge(promoted, tag)


def _thirty() -> list[datetime | None]:
    return [T0 + timedelta(minutes=n) for n in range(30)]


async def _thread(p: Any, ids: dict[str, Any], limit: int) -> list[str]:
    async with _as_member(p, p.org_b):
        got = await messages_mod.list_messages(
            ids["chat"], limit=limit, around=None, window=2,
            user=_user(ids["owner"], p.org_b))
    return [m.body_text for m in got]


async def test_the_tool_reads_the_newest_twenty(chat_of, monkeypatch) -> None:
    """The REAL tool, with the agent's ``_get`` sent to the REAL route: a chat
    of 30 messages shows messages 11 to 30, oldest first."""
    p, build = chat_of
    ids = build(_thirty())
    seen: list[dict[str, Any]] = []

    async def real_route(path: str, params: dict[str, Any] | None = None) -> Any:
        match = _THREAD.match(path)
        assert match, path
        seen.append(dict(params or {}))
        async with _as_member(p, p.org_b):
            got = await messages_mod.list_messages(
                match.group(1), limit=int((params or {})["limit"]), around=None, window=2,
                user=_user(ids["owner"], p.org_b))
        return [m.model_dump() for m in got]

    monkeypatch.setattr(agents, "_get", real_route)
    out = await agents.read_whatsapp_chat(ids["chat"])
    assert seen == [{"limit": 20}], seen
    assert out.splitlines() == [f"Sunil: message {n}" for n in range(11, 31)]


async def test_the_route_gives_the_newest_limit_in_reading_order(chat_of) -> None:
    p, build = chat_of
    ids = build(_thirty())
    assert await _thread(p, ids, 20) == [f"message {n}" for n in range(11, 31)]
    assert await _thread(p, ids, 1) == ["message 30"]
    # A limit past the chat gives the whole chat, oldest first, as before.
    assert await _thread(p, ids, 100) == [f"message {n}" for n in range(1, 31)]


async def test_a_tie_of_sent_at_keeps_one_order(chat_of) -> None:
    """Four messages at one time. The cut and the order both use ``id`` after
    ``sent_at``, so the page is the last ids, in id order."""
    p, build = chat_of
    ids = build([T0] * 4 + [T0 + timedelta(minutes=1)])
    by_body = {f"message {n}": mid for n, mid in ids["m"].items()}
    tied = sorted(ids["m"][n] for n in range(1, 5))
    got = await _thread(p, ids, 3)
    assert got[-1] == "message 5"
    assert [by_body[b] for b in got[:2]] == tied[2:]


async def test_a_message_with_no_time_is_the_oldest(chat_of) -> None:
    """``NULLS FIRST`` in reading order, as before H-277: a message with no
    ``sent_at`` is the oldest, so the newest page leaves it out."""
    p, build = chat_of
    ids = build([None, T0, T0 + timedelta(minutes=1)])
    assert await _thread(p, ids, 2) == ["message 2", "message 3"]
    assert await _thread(p, ids, 3) == ["message 1", "message 2", "message 3"]
