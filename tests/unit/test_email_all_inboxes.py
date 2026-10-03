"""WS-17 EM-T8d — All inboxes, the backend half.

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.4 and §11.7.4.
Decision D-EM-22. Defects MB-12 and MB-13.

R7 fences named here:

* ``email-conversation-per-mailbox`` (MB-12, R8): the list of all the
  mailboxes of a member gives one conversation for EACH mailbox of a shared
  thread id, and the thread count of a row counts only its own mailbox. A
  mailbox of another member never adds to a count.
* ``email-unread-inbox-only`` (MB-13, R8): the unread count of a mailbox is
  the unread mail of its Inbox that is not snoozed. Junk, deleted and sent
  mail do not count.

``test_email_conversation_collapse.py`` pins the SQL shape of the key.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_all_inboxes.py -v -rs
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.transport import accounts
from gateway.routes.email.transport import messages as m
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# FastAPI Query() defaults do not resolve on a direct call, so every filter
# gets its real "off" value (as in test_email_conversation_collapse.py).
_OFF = {
    "account_id": None, "folder": "INBOX", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 50,
}


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _account(admin_engine, *, org: str, owner: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": f"box-{uuid.uuid4().hex[:8]}@em-t8d.test",
             "o": org}).scalar_one())


def _mail(admin_engine, *, org: str, account_id: str, thread: str | None = None,
          folder: str = "inbox", read: bool = True, minutes_ago: int = 10,
          snoozed_minutes: int | None = None) -> None:
    now = datetime.now(UTC)
    with admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, thread_id, "
            "folder, from_address, to_addresses, subject, received_at, is_read, "
            "snoozed_until, organization_id) VALUES (CAST(:a AS uuid), :p, :t, :f, "
            "'{\"email\": \"ravi@contoso.test\"}'::jsonb, '[]'::jsonb, 's', :r, :read, "
            ":snooze, CAST(:o AS uuid))"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}", "t": thread,
             "f": folder, "r": now - timedelta(minutes=minutes_ago), "read": read,
             "snooze": (now + timedelta(minutes=snoozed_minutes)
                        if snoozed_minutes is not None else None),
             "o": org})


def _purge(admin_engine, *account_ids: str) -> None:
    with admin_engine.begin() as c:
        for a in account_ids:
            c.execute(text("DELETE FROM email_messages WHERE account_id = CAST(:a AS uuid)"),
                      {"a": a})
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": a})


@_DB_GATE
class TestAllInboxesOnARealDatabase:

    async def test_a_shared_thread_id_gives_one_conversation_per_mailbox(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t8d.test"
        box_a = _account(p.admin_engine, org=p.org_b, owner=owner)
        box_b = _account(p.admin_engine, org=p.org_b, owner=owner)
        other = _account(p.admin_engine, org=p.org_b, owner="mallory@em-t8d.test")
        thread = f"t-{uuid.uuid4().hex[:8]}"
        for minutes in (30, 20):
            _mail(p.admin_engine, org=p.org_b, account_id=box_a, thread=thread,
                  minutes_ago=minutes)
        _mail(p.admin_engine, org=p.org_b, account_id=box_b, thread=thread, minutes_ago=5)
        for minutes in (3, 2, 1):
            _mail(p.admin_engine, org=p.org_b, account_id=other, thread=thread,
                  minutes_ago=minutes)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                out = await m.list_messages(user=me, **{**_OFF, "collapse": True})
                rows = {(e["account_id"], e["thread_id"]): e["thread_count"]
                        for e in out["emails"]}
                assert rows == {(box_a, thread): 2, (box_b, thread): 1}
                assert out["total"] == 2

                one = await m.list_messages(
                    user=me, **{**_OFF, "collapse": True, "account_id": box_a})
                assert [(e["account_id"], e["thread_count"]) for e in one["emails"]] == [
                    (box_a, 2)]
        finally:
            release_tenant(token)
            _purge(p.admin_engine, box_a, box_b, other)

    async def test_the_unread_count_is_the_inbox_that_the_member_sees(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"owner-{uuid.uuid4().hex[:8]}@em-t8d.test"
        box = _account(p.admin_engine, org=p.org_b, owner=owner)
        # Counts: two unread in the Inbox, one of them woke from a snooze.
        _mail(p.admin_engine, org=p.org_b, account_id=box, read=False)
        _mail(p.admin_engine, org=p.org_b, account_id=box, read=False, snoozed_minutes=-5)
        # Does not count: read, junk, deleted, sent, and still snoozed.
        _mail(p.admin_engine, org=p.org_b, account_id=box, read=True)
        for folder in ("junk", "trash", "sent"):
            _mail(p.admin_engine, org=p.org_b, account_id=box, read=False, folder=folder)
        _mail(p.admin_engine, org=p.org_b, account_id=box, read=False, snoozed_minutes=60)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                [listed] = await accounts.list_accounts(user=me)
                assert listed.unread_count == 2
        finally:
            release_tenant(token)
            _purge(p.admin_engine, box)
