"""Task 4011: a snooze keeps a group mention in Needs reply (R8).

**The defect.** ``transport/snooze.py`` calls ``recompute_chat_status`` with no
``account_phone``. ``mentioned`` was then always False, so a snooze moved a
group chat that @mentions the member from NEEDS_REPLY to FYI.

This suite calls the REAL snooze, unsnooze and list routes on the phase-4
catalog of ``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS
role. The status of each chat comes first from the REAL ``classify_chats``
sweep.

R7 fence named here:

* ``wa-snooze-keeps-the-mention``: a snooze and an unsnooze leave the status
  and the reason of a chat as the sweep wrote them.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_whatsapp_snooze_mention_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from fastapi import FastAPI
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures used by name, so the import is
# load-bearing even though ruff reads it as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_whatsapp_webhook_under_rls import _assert_non_priv

pytestmark = _DB_GATE

_PHONE = "+919990388000"
_MENTIONED = (_PHONE.lstrip("+"),)
_OTHER = ("919111111111",)

#: chat key -> (kind, mentions of the last inbound message, status, reason)
_CHATS = {
    "group_mention": ("group", _MENTIONED, "NEEDS_REPLY", "you were @mentioned"),
    "group_other": ("group", _OTHER, "FYI", "group message, not addressed to you"),
    "dm": ("dm", (), "NEEDS_REPLY", "they messaged and await your reply"),
}


def _seed(p: Any, tag: str) -> dict[str, str]:
    """One number of one member in org A, with the three chats of ``_CHATS``."""
    ids = {"email": f"member-{tag}@wa-4011.test"}
    with p.admin_engine.begin() as c:
        ids["acct"] = str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "credentials_encrypted, organization_id) VALUES (:u, :n, :p, 'enc', "
            "CAST(:o AS uuid)) RETURNING id"),
            {"u": ids["email"], "n": _PHONE, "p": f"pn-4011-{tag}",
             "o": p.org_a}).scalar_one())
        for key, (kind, mentions, _status, _reason) in _CHATS.items():
            ids[key] = str(c.execute(text(
                "INSERT INTO wa_chats (account_id, wa_chat_id, kind, name, "
                "last_message_at, organization_id) VALUES (CAST(:a AS uuid), :w, :k, "
                ":w, :at, CAST(:o AS uuid)) RETURNING id"),
                {"a": ids["acct"], "w": f"{key}-{tag}", "k": kind,
                 "at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
                 "o": p.org_a}).scalar_one())
            c.execute(text(
                "INSERT INTO wa_messages (account_id, chat_id, wa_message_id, "
                "direction, kind, body_text, mentions, sent_at, organization_id) "
                "VALUES (CAST(:a AS uuid), CAST(:ch AS uuid), :w, 'in', 'text', "
                "'please confirm', :m, :at, CAST(:o AS uuid))"),
                {"a": ids["acct"], "ch": ids[key], "w": f"wamid.{key}.{tag}",
                 "m": list(mentions),
                 "at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC), "o": p.org_a})
    return ids


@pytest.fixture
def seeded(promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    tag = uuid.uuid4().hex[:8]
    ids = _seed(promoted, tag)
    try:
        yield promoted, ids
    finally:
        with promoted.admin_engine.begin() as c:
            c.execute(text("DELETE FROM wa_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": ids["acct"]})


async def _call(
    p: Any, email: str, org: str, method: str, path: str, body: Any = None,
) -> httpx.Response:
    """One request through the real WhatsApp router. The override binds the
    tenant from the user, as the real auth dependency does."""
    from gateway.routes.whatsapp import router

    who = UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:whatsapp"]), organization_id=org,
    )

    async def _caller() -> UserContext:
        bind_tenant(org)
        return who

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://gateway.test") as client,
        ):
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


async def _sweep(p: Any, account_id: str) -> None:
    """The real post-sync hook, bound to org A as the webhook binds it."""
    from gateway.routes.whatsapp.automation.replyzero import classify_chats

    token = bind_tenant(p.org_a)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            await classify_chats(account_id)
    finally:
        release_tenant(token)


def _status(p: Any, chat_id: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, reason, snoozed_until, organization_id::text AS o "
            "FROM wa_chat_status WHERE chat_id = CAST(:c AS uuid)"),
            {"c": chat_id}).first()


def _tomorrow() -> dict[str, str]:
    return {"until": (datetime.now(UTC) + timedelta(days=1)).isoformat()}


async def _stream(p: Any, ids: dict[str, str], stream: str) -> set[str]:
    resp = await _call(p, ids["email"], p.org_a, "GET",
                       f"/whatsapp/chats?stream={stream}")
    assert resp.status_code == 200, resp.text
    return {chat["id"] for chat in resp.json()}


@pytest.mark.parametrize("key", list(_CHATS))
async def test_a_snooze_and_an_unsnooze_keep_the_status(seeded, key):
    p, ids = seeded
    _kind, _mentions, status, reason = _CHATS[key]
    await _sweep(p, ids["acct"])
    row = _status(p, ids[key])
    assert (row.status, row.reason) == (status, reason)

    resp = await _call(p, ids["email"], p.org_a, "POST",
                       f"/whatsapp/chats/{ids[key]}/snooze", _tomorrow())
    assert resp.status_code == 200, resp.text
    row = _status(p, ids[key])
    assert (row.status, row.reason) == (status, reason)
    assert row.snoozed_until is not None
    assert ids[key] in await _stream(p, ids, "snoozed")
    assert ids[key] not in await _stream(p, ids, "needs_reply")

    resp = await _call(p, ids["email"], p.org_a, "POST",
                       f"/whatsapp/chats/{ids[key]}/unsnooze")
    assert resp.status_code == 200, resp.text
    row = _status(p, ids[key])
    assert (row.status, row.reason) == (status, reason)
    assert row.snoozed_until is None
    assert (ids[key] in await _stream(p, ids, "needs_reply")) is (
        status == "NEEDS_REPLY")


async def test_a_snooze_of_a_chat_with_no_status_row_reads_the_mention(seeded):
    """No sweep ran, so the snooze route writes the first status row."""
    p, ids = seeded
    chat = ids["group_mention"]
    assert _status(p, chat) is None

    resp = await _call(p, ids["email"], p.org_a, "POST",
                       f"/whatsapp/chats/{chat}/snooze", _tomorrow())

    assert resp.status_code == 200, resp.text
    row = _status(p, chat)
    assert (row.status, row.reason) == ("NEEDS_REPLY", "you were @mentioned")
    assert row.snoozed_until is not None
    assert row.o == p.org_a


async def test_a_session_of_another_org_cannot_snooze_the_chat(seeded):
    """The same email in org B gets 404, and the row does not change."""
    p, ids = seeded
    chat = ids["group_mention"]
    await _sweep(p, ids["acct"])
    before = _status(p, chat)

    resp = await _call(p, ids["email"], p.org_b, "POST",
                       f"/whatsapp/chats/{chat}/snooze", _tomorrow())

    assert resp.status_code == 404, resp.text
    assert tuple(_status(p, chat)) == tuple(before)
