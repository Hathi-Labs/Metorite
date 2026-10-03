"""WS-17 EM-T8g-1 — "Keep separate", the server half.

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.7, under
"EM-T8g-1". Decisions D-EM-4, D-EM-27, D-EM-28 and D-EM-30 (§11.2).

R7 fences named here:

* ``email-keep-separate-column`` (R8): the column ``in_all_inboxes`` is NOT
  NULL with a default of true. A row from before the migration reads true,
  also after a second run of the migration. The test finds the migration by
  CONTENT, never by number (R1).
* ``email-keep-separate-api`` (R8): the list, the create, the default and the
  update each return ``in_all_inboxes``. A ``PATCH`` writes it and does not
  restart the sync loop. A ``PATCH`` by another member answers 404 and writes
  nothing. ``"yes"`` answers 422 and opens no session. The unread count stays
  per mailbox, so a separate mailbox reports its own (review round 1, F1).
* ``email-keep-separate-reads`` (R8): with no ``account_id``, the list, the
  facets, search and ``/senders`` hold no row of a separate mailbox. That
  includes the dispositions of ``email_newsletters`` (also for
  ``folder=inbox`` with ``include_archived``, F3) and the label chips of a
  sender that writes to both mailboxes (F4). With the ``account_id`` of the
  separate mailbox, each read holds its rows.
* ``email-keep-separate-owner-scope`` (R8): a thread load, a read by id and a
  bulk act by ids reach a separate mailbox, because ``manage_inbox`` sends mail
  ids with no ``account_id``. A thread load WITH an ``account_id`` stays in
  that mailbox (F5, D-EM-22). A bulk act BY FILTER with no ``account_id``
  leaves out a separate mailbox, and its own ``account_id`` reaches it (F7).
* ``email-keep-separate-self`` (R8): a mail from a separate mailbox is still
  ``self`` in another mailbox (D-EM-27). The Sent-copy proof still reads the
  separate mailbox, and ``/senders`` never lists its address. Compose-assist
  still finds a mail of a separate mailbox for a reply from another From
  (``drafting._reply_target``, F2).

The agent half, ``email-chat-binding-skips-separate``, is in
``test_email_chat_binding.py``.

**R8.** The real SQL against the phase-4-promoted two-org catalog of
``test_h3_rls_promotion_rehearsal``, as the role ``acb_app_h3rls``
(NOSUPERUSER, NOBYPASSRLS). The admin engine seeds and reads the rows.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_keep_separate.py -v -rs
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_llm.key_store as key_store_mod
import email_ingestion.scheduler as sched
from acb_auth import get_current_user
from acb_auth.deps import require_authenticated
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from gateway.routes import email as email_pkg
from gateway.routes.email import core
from gateway.routes.email.automation import drafting as drafting_mod
from gateway.routes.email.automation import identity
from gateway.routes.email.automation import senders as senders_mod
from gateway.routes.email.transport import accounts
from gateway.routes.email.transport import messages as messages_mod
from gateway.routes.email.transport import search as search_mod
from pydantic import ValidationError
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_LADDER = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_NUMBERED = re.compile(r"^\d+_.*\.sql$")
_MIGRATION_MARK = "ADD COLUMN IF NOT EXISTS in_all_inboxes"

# FastAPI Query() defaults do not resolve on a direct call, so each filter
# gets its real "off" value.
_LIST_OFF: dict[str, Any] = {
    "account_id": None, "folder": "INBOX", "label": None, "uncategorized": False,
    "query": None, "thread_id": None, "received_after": None,
    "received_before": None, "is_read": None, "is_starred": None,
    "has_attachments": None, "importance": None, "from_email": None,
    "sender_category": None, "sort": "newest", "collapse": False,
    "page": 1, "page_size": 50,
}
_SEARCH_OFF: dict[str, Any] = {
    "q": None, "account_id": None, "folder": None, "label": None,
    "labels": None, "uncategorized": False, "from_addr": None, "to_addr": None,
    "received_after": None, "received_before": None, "is_read": None,
    "is_starred": None, "has_attachments": None, "sender_category": None,
    "importance": None, "hybrid": False, "light": False, "page": 1,
    "page_size": 50,
}
_SENDERS_OFF: dict[str, Any] = {
    "account_id": None, "folder": None, "include_archived": False,
    "limit": 200, "offset": 0,
}


def _migration() -> Path:
    """The one numbered migration that adds the column, found by content."""
    hits = [p for p in sorted(_LADDER.iterdir())
            if _NUMBERED.match(p.name)
            and _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, f"want one migration that adds the column: {hits}"
    return hits[0]


# ── hermetic ─────────────────────────────────────────────────────────────────


class TestTheMigrationText:

    def test_it_is_expand_only(self):
        sql = _migration().read_text(encoding="utf-8")
        body = "\n".join(line for line in sql.splitlines()
                         if not line.lstrip().startswith("--"))
        assert ("ADD COLUMN IF NOT EXISTS in_all_inboxes BOOLEAN NOT NULL "
                "DEFAULT true") in " ".join(body.split())
        # Expand only (R6): no rename, no drop, no table, and no UPDATE.
        for word in ("DROP ", "RENAME", "CREATE TABLE", "UPDATE "):
            assert word not in body.upper(), word


class TestTheUpdateModel:
    """``email-keep-separate-api``: a value that is not a JSON boolean."""

    @pytest.mark.parametrize("bad", ["yes", "false", "true", 0, 1])
    def test_a_value_that_is_not_a_boolean_is_refused(self, bad):
        with pytest.raises(ValidationError):
            accounts.AccountUpdateModel(in_all_inboxes=bad)

    def test_a_boolean_and_no_value_pass(self):
        assert accounts.AccountUpdateModel(in_all_inboxes=False).in_all_inboxes is False
        assert accounts.AccountUpdateModel().in_all_inboxes is None

    def test_the_answer_model_defaults_to_all_inboxes(self):
        model = accounts.EmailAccountModel(
            id="a1", provider="microsoft", email_address="me@x.test")
        assert model.in_all_inboxes is True

    def test_yes_answers_422_and_opens_no_session(self, monkeypatch):
        opened: list[Any] = []

        @asynccontextmanager
        async def _no_session(*args: Any, **kwargs: Any):
            opened.append((args, kwargs))
            raise AssertionError("the route opened a session")
            yield  # pragma: no cover

        monkeypatch.setattr(accounts, "_tenant_session", _no_session)
        user = UserContext(
            email="member@t8g1.test", role=UserRole.EMPLOYEE,
            organization_id=str(uuid.uuid4()),
            access=EffectiveAccess(role_granted=frozenset({"feature:email"})))
        app = FastAPI(dependencies=[require_authenticated(public=())])
        app.include_router(email_pkg.router)
        app.dependency_overrides[get_current_user] = lambda: user
        with TestClient(app) as client:
            resp = client.patch(
                f"/email/accounts/{uuid.uuid4()}", json={"in_all_inboxes": "yes"})
        assert resp.status_code == 422, resp.text
        assert opened == []


class TestTheScopeHelper:
    """``core._account_scope`` takes a keyword-only flag (scope item 3)."""

    def test_with_no_flag_the_text_does_not_change(self):
        params: dict[str, Any] = {"uid": "m@x.test"}
        assert core._account_scope(None, params) == (
            "em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid)")
        assert core._account_scope("a-1", params) == (
            "em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid"
            " AND id = :aid)")
        assert params == {"uid": "m@x.test", "aid": "a-1"}

    def test_the_flag_is_keyword_only(self):
        with pytest.raises(TypeError):
            core._account_scope(None, {}, True)  # type: ignore[misc]

    def test_a_named_mailbox_is_never_left_out(self):
        params: dict[str, Any] = {}
        frag = core._account_scope("a-1", params, pooled_only=True)
        assert "in_all_inboxes" not in frag and params == {"aid": "a-1"}
        assert "in_all_inboxes" in core._account_scope(None, {}, pooled_only=True)


# ── R8 helpers ───────────────────────────────────────────────────────────────


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _account(admin, *, org: str, owner: str, pooled: bool = True,
             default: bool = False, address: str | None = None) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id, in_all_inboxes, is_default) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid), :p, :d) "
            "RETURNING id"),
            {"u": owner, "m": address or f"box-{uuid.uuid4().hex[:8]}@t8g1.test",
             "o": org, "p": pooled, "d": default}).scalar_one())


def _address(admin, account_id: str) -> str:
    with admin.connect() as c:
        return str(c.execute(text(
            "SELECT email_address FROM email_accounts WHERE id = CAST(:a AS uuid)"),
            {"a": account_id}).scalar_one())


def _mail(admin, *, org: str, account_id: str, sender: str,
          folder: str = "inbox", subject: str = "s", body: str = "b",
          thread: str | None = None, categories: list[str] | None = None,
          to: list[str] | None = None, message_id: str | None = None,
          minutes_ago: int = 10) -> str:
    with admin.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, body_text, "
            "received_at, is_read, categories, internet_message_id, "
            "organization_id) VALUES (CAST(:a AS uuid), :p, :t, :f, "
            "CAST(:frm AS jsonb), CAST(:to AS jsonb), :s, :b, :r, false, "
            "CAST(:cats AS text[]), :imid, CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "p": f"pm-{uuid.uuid4().hex[:10]}",
             "t": thread or f"t-{uuid.uuid4().hex[:8]}", "f": folder,
             "frm": json.dumps({"name": "N", "email": sender}),
             "to": json.dumps([{"email": e} for e in (to or [])]),
             "s": subject, "b": body,
             "r": datetime.now(UTC) - timedelta(minutes=minutes_ago),
             "cats": categories or [], "imid": message_id,
             "o": org}).scalar_one())


def _newsletter(admin, *, org: str, account_id: str, sender: str,
                status: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO email_newsletters (account_id, email, status, "
            "organization_id) VALUES (CAST(:a AS uuid), :e, :s, CAST(:o AS uuid))"),
            {"a": account_id, "e": sender, "s": status, "o": org})


def _purge(admin, owner_pattern: str) -> None:
    with admin.begin() as c:
        c.execute(text(
            "DELETE FROM email_accounts WHERE user_id LIKE :u"), {"u": owner_pattern})


def _in_all_inboxes(admin, account_id: str) -> bool:
    with admin.connect() as c:
        return bool(c.execute(text(
            "SELECT in_all_inboxes FROM email_accounts WHERE id = CAST(:a AS uuid)"),
            {"a": account_id}).scalar_one())


@asynccontextmanager
async def _as_member(p, org: str):
    """Bind ``org`` and point the shared engine at the app role."""
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


def _ids(out: dict[str, Any]) -> set[str]:
    return {str(e["id"]) for e in out["emails"]}


# ── R8 ───────────────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheColumn:
    """``email-keep-separate-column``."""

    def test_it_is_not_null_with_a_default_of_true(self, promoted):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            row = c.execute(text(
                "SELECT is_nullable, column_default, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = 'email_accounts' "
                "AND column_name = 'in_all_inboxes'")).one()
        assert row.data_type == "boolean"
        assert row.is_nullable == "NO"
        assert row.column_default == "true"

    def test_a_row_from_before_the_migration_reads_true(self, promoted):  # noqa: F811
        """Drop the column, write an old row, run the migration twice, and
        read the row. The transaction rolls back, so the catalog of the other
        tests does not change."""
        sql = _migration().read_text(encoding="utf-8")
        # The file holds its own BEGIN and COMMIT. Inside the transaction of
        # this test they would commit it, so the test runs the body only.
        body = "\n".join(line for line in sql.splitlines()
                         if line.strip().upper() not in ("BEGIN;", "COMMIT;"))
        with promoted.admin_engine.connect() as c:
            tx = c.begin()
            try:
                c.execute(text("ALTER TABLE email_accounts DROP COLUMN in_all_inboxes"))
                old = c.execute(text(
                    "INSERT INTO email_accounts (user_id, provider, email_address, "
                    "credentials_encrypted, organization_id) VALUES "
                    "('old@t8g1.test', 'microsoft', 'old@t8g1.test', 'x', "
                    "CAST(:o AS uuid)) RETURNING id"),
                    {"o": promoted.org_b}).scalar_one()
                for _ in range(2):
                    with c.connection.dbapi_connection.cursor() as cur:
                        cur.execute(body)
                got = c.execute(text(
                    "SELECT in_all_inboxes FROM email_accounts WHERE id = :i"),
                    {"i": old}).scalar_one()
                assert got is True
            finally:
                tx.rollback()


@_DB_GATE
class TestTheApi:
    """``email-keep-separate-api``."""

    async def test_each_account_read_returns_the_field(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"member-{uuid.uuid4().hex[:8]}@t8g1.test"
        home = _account(p.admin_engine, org=p.org_b, owner=owner, default=True)
        nda = _account(p.admin_engine, org=p.org_b, owner=owner, pooled=False)
        # Unread Inbox mail: two in home, one in the separate mailbox (F1).
        for box, n in ((home, 2), (nda, 1)):
            for _ in range(n):
                _mail(p.admin_engine, org=p.org_b, account_id=box,
                      sender="u@sender.test")
        restarts: list[tuple[str, str]] = []

        async def _refresh(account_id: str, **_k: Any) -> None:
            restarts.append(("refresh", account_id))

        async def _remove(account_id: str, **_k: Any) -> None:
            restarts.append(("remove", account_id))

        monkeypatch.setattr(sched, "refresh_account_sync", _refresh)
        monkeypatch.setattr(sched, "remove_account_sync", _remove)
        monkeypatch.setattr(key_store_mod, "get_key_store", lambda: SimpleNamespace(
            encrypt=lambda raw: "blob-" + json.loads(raw)["imap_host"]))
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_member(p, p.org_b):
                rows = await accounts.list_accounts(user=me)
                listed = {a.id: a.in_all_inboxes for a in rows}
                unread = {a.id: a.unread_count for a in rows}
                made = await accounts.set_default_account(nda, user=me)
                kept = await accounts.update_account(
                    home, accounts.AccountUpdateModel(in_all_inboxes=False), user=me)
                assert _in_all_inboxes(p.admin_engine, home) is False
                back = await accounts.update_account(
                    home, accounts.AccountUpdateModel(in_all_inboxes=True), user=me)
                renamed = await accounts.update_account(
                    nda, accounts.AccountUpdateModel(label="NDA"), user=me)
                # The three PATCHes above restart no sync. Keep the log of
                # them apart, because the create below starts a sync.
                patch_restarts = list(restarts)
                created = await accounts.create_account(
                    accounts.CreateAccountRequest(
                        provider="imap", email_address=f"imap-{owner}",
                        credentials={
                            "imap_host": "h", "imap_port": 993,
                            "imap_username": "u", "imap_password": "p",
                            "smtp_host": "h", "smtp_port": 465}),
                    user=me)
            assert listed == {home: True, nda: False}
            # F1: the count stays per mailbox. A separate mailbox reports its
            # own count, and the default route counts it too.
            assert unread == {home: 2, nda: 1}
            assert made.unread_count == 1
            assert made.in_all_inboxes is False
            assert kept.in_all_inboxes is False
            assert back.in_all_inboxes is True
            assert _in_all_inboxes(p.admin_engine, home) is True
            # A field that the PATCH did not name keeps its value.
            assert renamed.in_all_inboxes is False
            assert created.in_all_inboxes is True
            assert _in_all_inboxes(p.admin_engine, created.id) is True
            # No PATCH of the field restarted the sync loop. The create did.
            assert patch_restarts == []
            assert restarts == [("refresh", created.id)]
        finally:
            _purge(p.admin_engine, owner)

    async def test_a_patch_of_the_mailbox_of_another_member_is_404(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"member-{tag}@t8g1.test"
        mallory = f"mallory-{tag}@t8g1.test"
        nda = _account(p.admin_engine, org=p.org_b, owner=owner, pooled=False)
        pooled = _account(p.admin_engine, org=p.org_b, owner=owner)
        restarts: list[str] = []

        async def _record(account_id: str, **_k: Any) -> None:
            restarts.append(account_id)

        monkeypatch.setattr(sched, "refresh_account_sync", _record)
        monkeypatch.setattr(sched, "remove_account_sync", _record)
        her = UserContext(email=mallory, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_member(p, p.org_b):
                for target, value in ((nda, True), (pooled, False)):
                    with pytest.raises(HTTPException) as err:
                        await accounts.update_account(
                            target, accounts.AccountUpdateModel(in_all_inboxes=value),
                            user=her)
                    assert err.value.status_code == 404
            assert _in_all_inboxes(p.admin_engine, nda) is False
            assert _in_all_inboxes(p.admin_engine, pooled) is True
            assert restarts == []
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")


@_DB_GATE
class TestTheReadsOfMoreThanOneMailbox:
    """``email-keep-separate-reads`` and ``email-keep-separate-self``."""

    def _seed(self, p) -> SimpleNamespace:
        tag = uuid.uuid4().hex[:8]
        s = SimpleNamespace(tag=tag, owner=f"member-{tag}@t8g1.test")
        org = p.org_b
        s.box_a = _account(p.admin_engine, org=org, owner=s.owner, default=True)
        s.box_s = _account(p.admin_engine, org=org, owner=s.owner, pooled=False)
        s.box_x = _account(p.admin_engine, org=org, owner=f"mallory-{tag}@t8g1.test")
        s.addr_a = _address(p.admin_engine, s.box_a)
        s.addr_s = _address(p.admin_engine, s.box_s)
        s.pool = f"pool-{tag}@sender.test"
        s.nda = f"nda-{tag}@sender.test"
        s.shared = f"shared-{tag}@sender.test"
        s.status = f"status-{tag}@sender.test"
        mail = _mail
        a, sep, x = s.box_a, s.box_s, s.box_x
        s.m_pool = mail(p.admin_engine, org=org, account_id=a, sender=s.pool,
                        subject="Quarterly zebra plan", categories=["PoolTag"])
        s.m_status = mail(p.admin_engine, org=org, account_id=a, sender=s.status)
        # Mail from the separate mailbox to this one: still self (D-EM-27).
        s.m_self = mail(p.admin_engine, org=org, account_id=a, sender=s.addr_s)
        # Archived, so only a disposition can bring its sender into the list.
        s.m_shared = mail(p.admin_engine, org=org, account_id=a, sender=s.shared,
                          folder="archive")
        s.m_nda = mail(p.admin_engine, org=org, account_id=sep, sender=s.nda,
                       subject="Secret zebra plan", categories=["NdaTag"],
                       thread=f"nda-thread-{tag}")
        s.m_nda2 = mail(p.admin_engine, org=org, account_id=sep, sender=s.nda,
                        thread=f"nda-thread-{tag}", minutes_ago=5)
        # F4: one sender writes to both mailboxes, with a label in each.
        s.both = f"both-{tag}@sender.test"
        s.m_both_a = mail(p.admin_engine, org=org, account_id=a, sender=s.both,
                          categories=["Newsletter"])
        s.m_both_s = mail(p.admin_engine, org=org, account_id=sep, sender=s.both,
                          categories=["Receipt"])
        mail(p.admin_engine, org=org, account_id=x, sender=f"x-{tag}@sender.test",
             subject="Other zebra plan", categories=["XTag"])
        # The dispositions of the separate mailbox stay with it.
        for sender in (s.shared, s.status):
            _newsletter(p.admin_engine, org=org, account_id=sep, sender=sender,
                        status="UNSUBSCRIBED")
        s.me = UserContext(email=s.owner, role=UserRole.EMPLOYEE, organization_id=org)
        return s

    async def test_all_inboxes_leaves_out_a_separate_mailbox(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        s = self._seed(p)
        mine = {s.m_pool, s.m_status, s.m_self, s.m_both_a}
        senders_in_a = {s.pool, s.status, s.both}
        try:
            async with _as_member(p, p.org_b):
                listed = await messages_mod.list_messages(user=s.me, **_LIST_OFF)
                collapsed = await messages_mod.list_messages(
                    user=s.me, **{**_LIST_OFF, "collapse": True})
                facets = await messages_mod.message_facets(
                    account_id=None, folder="INBOX", user=s.me)
                found = await search_mod.search_messages(
                    user=s.me, **{**_SEARCH_OFF, "q": "zebra"})
                filters_only = await search_mod.search_messages(
                    user=s.me, **_SEARCH_OFF)
                senders = await senders_mod.list_senders(user=s.me, **_SENDERS_OFF)
                in_inbox = await senders_mod.list_senders(
                    user=s.me, **{**_SENDERS_OFF, "folder": "inbox"})
                # F3: with include_archived, only the folder clause can bring
                # in the archived mail of a sender with a disposition.
                in_inbox_all = await senders_mod.list_senders(
                    user=s.me, **{**_SENDERS_OFF, "folder": "inbox",
                                  "include_archived": True})
            assert _ids(listed) == mine and listed["total"] == 4
            assert _ids(collapsed) == mine and collapsed["total"] == 4
            assert facets["total"] == 4
            assert "pooltag" in facets["labels"] and "ndatag" not in facets["labels"]
            assert "newsletter" in facets["labels"] and "receipt" not in facets["labels"]
            assert _ids(found) == {s.m_pool} and found["total"] == 1
            assert _ids(filters_only) == mine | {s.m_shared}
            by_email = {r["email"]: r for r in senders["senders"]}
            # No sender of the separate mailbox, and no disposition of it.
            assert set(by_email) == senders_in_a, sorted(by_email)
            assert by_email[s.status]["status"] == "UNHANDLED"
            assert senders["total"] == 3
            # F4: the chips of a sender in both mailboxes hold no label of the
            # separate mailbox.
            both = by_email[s.both]
            assert both["count"] == 1
            assert both["categories"] == ["Newsletter"]
            assert both["category_counts"] == {"Newsletter": 1}
            assert both["labelled"] == 1
            assert {r["email"] for r in in_inbox["senders"]} == senders_in_a
            assert {r["email"] for r in in_inbox_all["senders"]} == senders_in_a
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_its_own_account_id_still_reads_it(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        s = self._seed(p)
        try:
            async with _as_member(p, p.org_b):
                listed = await messages_mod.list_messages(
                    user=s.me, **{**_LIST_OFF, "account_id": s.box_s})
                facets = await messages_mod.message_facets(
                    account_id=s.box_s, folder="INBOX", user=s.me)
                found = await search_mod.search_messages(
                    user=s.me, **{**_SEARCH_OFF, "q": "zebra", "account_id": s.box_s})
                senders = await senders_mod.list_senders(
                    user=s.me, **{**_SENDERS_OFF, "account_id": s.box_s})
                pooled_box = await messages_mod.list_messages(
                    user=s.me, **{**_LIST_OFF, "account_id": s.box_a})
            assert _ids(listed) == {s.m_nda, s.m_nda2, s.m_both_s}
            assert "ndatag" in facets["labels"] and facets["total"] == 3
            assert "receipt" in facets["labels"]
            assert _ids(found) == {s.m_nda}
            by_email = {r["email"]: r for r in senders["senders"]}
            assert set(by_email) == {s.nda, s.both}
            assert by_email[s.both]["categories"] == ["Receipt"]
            assert _ids(pooled_box) == {s.m_pool, s.m_status, s.m_self, s.m_both_a}
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")

    async def test_a_separate_mailbox_is_still_self(
        self, promoted, app_engine,  # noqa: F811
    ):
        """``email-keep-separate-self``: the self set, the Sent-copy proof and
        the "never list the member" rule of ``/senders`` all keep it."""
        _assert_non_priv(app_engine)
        p = promoted
        s = self._seed(p)
        imid = f"<t8g1-{s.tag}@proof.test>"
        _mail(p.admin_engine, org=p.org_b, account_id=s.box_s, sender=s.addr_s,
              folder="sent", to=[s.addr_a], message_id=imid)
        in_a = _mail(p.admin_engine, org=p.org_b, account_id=s.box_a,
                     sender=s.addr_s, to=[s.addr_a], message_id=imid)
        try:
            async with _as_member(p, p.org_b):
                async with core._tenant_session() as db:
                    me = await identity.resolve_self(db, s.box_a)
                    proven = await identity.proven_own_send(db, s.box_a, in_a)
                    sql_set = {r.addr for r in (await db.execute(
                        text(identity.SELF_ADDRESSES_SQL),
                        {"aid": s.box_a})).fetchall()}
                    # F2: compose-assist answers a mail of the separate
                    # mailbox from mailbox A, and still finds that mail.
                    target = await drafting_mod._reply_target(
                        db, s.box_a, s.m_nda, s.owner, from_any_owned_mailbox=True)
                senders = await senders_mod.list_senders(user=s.me, **_SENDERS_OFF)
            assert target is not None and str(target.account_id) == s.box_s
            assert s.addr_s.lower() in me.self_addresses
            assert sql_set == {s.addr_a.lower(), s.addr_s.lower()}
            assert identity.sender_scope(
                s.addr_s, s.addr_a, self_addresses=me.self_addresses) == "self"
            assert proven is True
            assert s.addr_s.lower() not in {r["email"] for r in senders["senders"]}
        finally:
            _purge(p.admin_engine, f"%-{s.tag}@t8g1.test")


@_DB_GATE
class TestTheReadsThatKeepTheOwnerScopeOnly:
    """``email-keep-separate-owner-scope``."""

    async def test_a_thread_load_a_read_by_id_and_a_bulk_act_reach_it(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"member-{tag}@t8g1.test"
        home = _account(p.admin_engine, org=p.org_b, owner=owner, default=True)
        nda = _account(p.admin_engine, org=p.org_b, owner=owner, pooled=False)
        thread = f"nda-{tag}"
        first = _mail(p.admin_engine, org=p.org_b, account_id=nda,
                      sender=f"n-{tag}@sender.test", thread=thread, minutes_ago=20)
        second = _mail(p.admin_engine, org=p.org_b, account_id=nda,
                       sender=f"n-{tag}@sender.test", thread=thread, minutes_ago=5)
        # F5: the pooled mailbox holds the same thread id (edge case 13).
        other = _mail(p.admin_engine, org=p.org_b, account_id=home,
                      sender=f"h-{tag}@sender.test", thread=thread, minutes_ago=1)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        tasks = BackgroundTasks()
        try:
            async with _as_member(p, p.org_b):
                loaded = await messages_mod.list_messages(
                    user=me, **{**_LIST_OFF, "thread_id": thread})
                in_nda = await messages_mod.list_messages(
                    user=me, **{**_LIST_OFF, "thread_id": thread, "account_id": nda})
                in_home = await messages_mod.list_messages(
                    user=me, **{**_LIST_OFF, "thread_id": thread, "account_id": home})
                one = await messages_mod.get_message(first, user=me)
                bulk = await senders_mod.bulk_action(
                    senders_mod.BulkActionRequest(
                        action="star", message_ids=[first, second]),
                    tasks, user=me)
            # With no account_id, a thread load keeps the owner scope only.
            assert [e["id"] for e in loaded["emails"]] == [first, second, other]
            # F5: with an account_id, a thread load stays in that mailbox.
            assert [e["id"] for e in in_nda["emails"]] == [first, second]
            assert [e["id"] for e in in_home["emails"]] == [other]
            assert str(one.id) == first
            assert bulk == {"affected": 2}
            with p.admin_engine.connect() as c:
                starred = c.execute(text(
                    "SELECT count(*) FROM email_messages WHERE account_id = "
                    "CAST(:a AS uuid) AND is_starred"), {"a": nda}).scalar_one()
            assert starred == 2
            # The provider half is a task for the mailbox of the mail.
            assert [t.args[0] for t in tasks.tasks] == [nda]
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")

    async def test_a_bulk_act_by_filter_leaves_out_a_separate_mailbox(
        self, promoted, app_engine,  # noqa: F811
    ):
        """F7 (D-EM-30): a bulk act BY FILTER with no ``account_id`` acts on
        All inboxes, so a separate mailbox keeps its mail. Its own
        ``account_id`` reaches it."""
        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"member-{tag}@t8g1.test"
        home = _account(p.admin_engine, org=p.org_b, owner=owner, default=True)
        nda = _account(p.admin_engine, org=p.org_b, owner=owner, pooled=False)
        sender = f"bulk-{tag}@sender.test"
        for box in (home, nda):
            _mail(p.admin_engine, org=p.org_b, account_id=box, sender=sender)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)

        def _folders() -> dict[str, str]:
            with p.admin_engine.connect() as c:
                return {str(r.account_id): r.folder for r in c.execute(text(
                    "SELECT account_id, folder FROM email_messages "
                    "WHERE account_id IN (CAST(:h AS uuid), CAST(:n AS uuid))"),
                    {"h": home, "n": nda}).fetchall()}

        try:
            async with _as_member(p, p.org_b):
                by_filter = await senders_mod.bulk_action(
                    senders_mod.BulkActionRequest(action="archive", sender_email=sender),
                    BackgroundTasks(), user=me)
                after_filter = _folders()
                by_own_id = await senders_mod.bulk_action(
                    senders_mod.BulkActionRequest(
                        action="archive", sender_email=sender, account_id=nda),
                    BackgroundTasks(), user=me)
            assert by_filter == {"affected": 1}
            assert after_filter == {home: "archive", nda: "inbox"}
            assert by_own_id == {"affected": 1}
            assert _folders() == {home: "archive", nda: "archive"}
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")
