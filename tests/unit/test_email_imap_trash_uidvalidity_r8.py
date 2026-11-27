"""R8 for task 4095: the stored Message-ID reads of the IMAP trash.

The hermetic half is ``test_email_imap_trash_uidvalidity.py``. This half
runs the real SQL of ``core._trash_message_id`` and ``core._trash_message_ids``
against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, under FORCE RLS, as the role
``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS). The provider is the real
``IMAPProvider`` on the ``FakeImap`` of ``test_email_imap_trash.py``.

1. The real delete route reads the stored Message-ID of the row. After a
   rebuild of INBOX it moves no mail, and it trashes the right UID.
2. The real bulk reconcile reads the stored Message-ID of each row. A stale
   UID fails, and the revert puts its row back in INBOX.
3. The read prefers ``internet_message_id``, skips an empty one, and sees no
   row of another organization.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_imap_trash_uidvalidity_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from email_ingestion.providers.imap import IMAPProvider
from gateway.routes.email import core
from gateway.routes.email.automation import senders
from gateway.routes.email.transport import messages as route
from sqlalchemy import text

from tests.unit.test_email_imap_trash import FakeImap, _mail
from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _purge
from tests.unit.test_gmail_send_and_drafts import _account, _as_member
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _mailbox(p: Any, org: str) -> SimpleNamespace:
    owner = f"imap-{uuid.uuid4().hex[:8]}@t4095.test"
    aid = _account(p.admin_engine, org=org, owner=owner, provider="imap")
    return SimpleNamespace(
        p=p, admin=p.admin_engine, org=org, aid=aid, owner=owner,
        me=UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=org))


def _seed(m: SimpleNamespace, pmid: str, *, thread_id: str | None,
          imid: str | None = None, folder: str = "inbox") -> str:
    """One row as the IMAP sync writes it: the Message-ID in ``thread_id``
    and no ``internet_message_id``, unless the test names one."""
    with m.admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id,"
            " internet_message_id, thread_id, folder, subject, from_address,"
            " to_addresses, organization_id) VALUES (CAST(:a AS uuid), :p, :i,"
            " :t, :f, 'B', '{}'::jsonb, '[]'::jsonb, CAST(:o AS uuid))"
            " RETURNING id::text"),
            {"a": m.aid, "p": pmid, "i": imid, "t": thread_id, "f": folder,
             "o": m.org}).scalar_one())


def _folder(m: SimpleNamespace, row_id: str) -> str:
    with m.admin.connect() as c:
        return c.execute(text("SELECT folder FROM email_messages WHERE id ="
                              " CAST(:i AS uuid)"), {"i": row_id}).scalar_one()


def _drop(*boxes: SimpleNamespace) -> None:
    for m in boxes:
        with m.admin.begin() as c:
            c.execute(text("DELETE FROM email_messages WHERE account_id ="
                           " CAST(:a AS uuid)"), {"a": m.aid})
        _purge(m.admin, [m.aid])


def _server() -> FakeImap:
    return FakeImap({
        "INBOX": [(_mail("A"), set()), (_mail("B"), set()), (_mail("D"), set())],
        "Trash": [],
    })


def _imap(server: FakeImap) -> IMAPProvider:
    p = IMAPProvider({})
    p._imap = server  # type: ignore[assignment]
    p.authenticate = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return p


def _wire_route(monkeypatch: pytest.MonkeyPatch, m: SimpleNamespace,
                provider: IMAPProvider, pmid: str) -> None:
    """The real route and its real SQL. Only the provider load is a fake,
    because the account holds no real credentials."""
    monkeypatch.setattr(route, "_provider_for_message",
                        AsyncMock(return_value=(provider, pmid, m.aid, object())))
    monkeypatch.setattr(route, "_persist_rotated_creds", AsyncMock())


async def test_the_delete_route_reads_the_stored_id_and_moves_no_other_mail(
        promoted, app_engine, monkeypatch):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _mailbox(promoted, promoted.org_b)
    try:
        row = _seed(m, "2", thread_id="<B@test>")
        server = _server()
        server.rebuild([_mail("D"), _mail("A"), _mail("B")])  # UID 2 is A now
        before = server.snapshot()
        _wire_route(monkeypatch, m, _imap(server), "2")
        async with _as_member(m):
            resp = await route.delete_message(row, user=m.me, report=True)
        assert json.loads(resp.body)["provider"] == "not_confirmed"
        assert server.snapshot() == before

        # The row of B with its right UID: the trash moves B.
        with m.admin.begin() as c:
            c.execute(text("UPDATE email_messages SET provider_message_id = '3',"
                           " folder = 'inbox' WHERE id = CAST(:i AS uuid)"), {"i": row})
        _wire_route(monkeypatch, m, _imap(server), "3")
        async with _as_member(m):
            resp = await route.delete_message(row, user=m.me, report=True)
        assert json.loads(resp.body)["provider"] == "confirmed"
        assert [b for b, _f in server.boxes["Trash"].values()] == [_mail("B")]
        assert _folder(m, row) == "trash"
    finally:
        _drop(m)


async def test_the_bulk_reconcile_reads_each_stored_id(
        promoted, app_engine, monkeypatch):  # noqa: F811
    """The bulk route set each row to Trash. The reconcile trashes B at its
    right UID, refuses the stale UID of D, and the revert puts D back."""
    _assert_non_priv(app_engine)
    m = _mailbox(promoted, promoted.org_b)
    try:
        server = _server()
        server.rebuild([_mail("D"), _mail("A"), _mail("B")])  # B=3, A=2, D=1
        b_row = _seed(m, "3", thread_id="<B@test>", folder="trash")
        d_row = _seed(m, "2", thread_id="<D@test>", folder="trash")  # stale UID
        provider = _imap(server)

        @asynccontextmanager
        async def _session(*_a: Any, **_k: Any):
            yield SimpleNamespace(provider=provider, authed=True)

        monkeypatch.setattr(senders, "provider_session", _session)
        monkeypatch.setattr(senders, "_RECONCILE_ATTEMPTS", 1)
        async with _as_member(m):
            await senders._bulk_reconcile_provider(m.aid, ["3", "2"], "trash")
        assert [b for b, _f in server.boxes["Trash"].values()] == [_mail("B")]
        assert [b for b, _f in server.boxes["INBOX"].values()] == [_mail("D"), _mail("A")]
        assert _folder(m, b_row) == "trash"
        assert _folder(m, d_row) == "inbox"
    finally:
        _drop(m)


async def test_the_read_prefers_the_message_id_column_and_keeps_to_its_tenant(
        promoted, app_engine):  # noqa: F811
    _assert_non_priv(app_engine)
    m = _mailbox(promoted, promoted.org_b)
    other = _mailbox(promoted, promoted.org_a)
    try:
        both = _seed(m, "4", thread_id="t-1", imid="<D@test>")
        empty = _seed(m, "5", thread_id="<E@test>", imid="")
        none = _seed(m, "6", thread_id=None)
        foreign = _seed(other, "7", thread_id="<X@test>")
        # Task 4100: a Drafts or Sent row never falls back to ``thread_id``,
        # which can hold the In-Reply-To of its parent. A row that the delete
        # route already set to ``trash`` keeps the fallback.
        draft = _seed(m, "8", thread_id="<PARENT@test>", folder="drafts")
        sent = _seed(m, "9", thread_id="<PARENT@test>", folder="Sent")
        own = _seed(m, "10", thread_id="<PARENT@test>", imid="<OWN@test>",
                    folder="drafts")
        trashed = _seed(m, "11", thread_id="<T@test>", folder="trash")
        upper = _seed(m, "12", thread_id="<U@test>", folder="INBOX")
        imap = _imap(_server())
        rows = (both, empty, none, foreign, draft, sent, own, trashed, upper)
        async with _as_member(m), core._tenant_session() as db:
            got = {rid: await core._trash_message_id(db, imap, rid) for rid in rows}
            many = await core._trash_message_ids(
                db, imap, m.aid, ["4", "5", "6", "7", "8", "9", "10", "11", "12"])
            gmail_like = await core._trash_message_id(db, SimpleNamespace(), both)
        assert got == {both: "<D@test>", empty: "<E@test>", none: None, foreign: None,
                       draft: None, sent: None, own: "<OWN@test>",
                       trashed: "<T@test>", upper: "<U@test>"}
        assert many == {"4": "<D@test>", "5": "<E@test>", "6": None, "8": None,
                        "9": None, "10": "<OWN@test>", "11": "<T@test>",
                        "12": "<U@test>"}
        assert gmail_like is None
    finally:
        _drop(m, other)
