"""EM-T2c — the chat context and the task-close hop read only an OWNED mailbox (R8).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.5, subsection
EM-T2c, items 1, 2 and 8. D-EM-4: a mailbox is private to the member who
connects it.

The static fence (``test_email_owner_scope_fence.py``) reads one function at a
time and does not follow data. This file covers the two leaks that it cannot
see, against the real phase-4 catalog of ``test_h3_rls_promotion_rehearsal``,
as the non-privileged role ``acb_app_h3rls``. Both members are in ONE
organization, so row level security does not hide the mailbox. Only the owner
check does.

R7 fences named here:

* ``email-chat-context-owned-only``: member B sends the ``account_id`` of the
  mailbox of member A to ``POST /email/ai/chat``, with zero mailboxes and with
  two of her own. The context holds no count, no category and no address of
  that mailbox, the agent payload does not carry its id, and the chat model is
  not read from its settings.
* ``email-task-close-owned-only``: member B closes a task whose origin is a
  mailbox of member A. The thread status of that mailbox does not change, and
  no label reconciliation runs. Member A closing the same task marks it DONE.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_chat_context_owner.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common import db as common_db
from acb_common.db import bind_tenant, release_tenant
from fastapi import BackgroundTasks
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import chat as chat_mod
from gateway.routes.tasks import email_link
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: A category name that no real mailbox carries, so a hit in the context can
#: only come from the seeded mailbox of member A.
SECRET_CATEGORY = "Zeppelinletter"
SECRET_MODEL = "tier-secret-of-a"


@contextmanager
def _bound(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _seed_account(admin, *, org: str, owner: str) -> tuple[str, str]:
    mailbox = f"box-{uuid.uuid4().hex[:8]}@em-t2c.test"
    with admin.begin() as c:
        acc = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, organization_id) "
            "VALUES (:u, 'microsoft', :m, 'x', CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "m": mailbox, "o": org}).scalar_one())
    return acc, mailbox


def _seed_mailbox_of_a(admin, *, org: str, owner: str) -> tuple[str, str, str]:
    """A mailbox with three inbox messages, one NEEDS_REPLY thread, a sender
    category and a chat model. Returns (account id, address, thread id)."""
    acc, mailbox = _seed_account(admin, org=org, owner=owner)
    thread = f"t-{uuid.uuid4().hex[:10]}"
    with admin.begin() as c:
        for i in range(3):
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "thread_id, folder, from_address, to_addresses, subject, "
                "body_text, snippet, received_at, is_read, organization_id) "
                "VALUES (CAST(:a AS uuid), :pm, :tid, 'inbox', "
                "CAST(:frm AS jsonb), CAST(:to AS jsonb), 'hello', 'body', "
                "'body', :rcv, false, CAST(:o AS uuid))"),
                {"a": acc, "pm": f"pm-{uuid.uuid4().hex[:12]}", "tid": thread,
                 "frm": json.dumps({"email": f"s{i}@sender.test", "name": "S"}),
                 "to": json.dumps([{"email": mailbox}]),
                 "rcv": datetime.now(UTC), "o": org})
        c.execute(text(
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "reason, organization_id) VALUES (CAST(:a AS uuid), :tid, "
            "'NEEDS_REPLY', 'seed', CAST(:o AS uuid))"),
            {"a": acc, "tid": thread, "o": org})
        c.execute(text(
            "INSERT INTO email_senders (account_id, email, category, "
            "organization_id) VALUES (CAST(:a AS uuid), 's0@sender.test', :cat, "
            "CAST(:o AS uuid))"),
            {"a": acc, "cat": SECRET_CATEGORY, "o": org})
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, chat_model, "
            "organization_id) VALUES (CAST(:a AS uuid), :cm, CAST(:o AS uuid))"),
            {"a": acc, "cm": SECRET_MODEL, "o": org})
    return acc, mailbox, thread


def _purge(admin, account_ids: list[str]) -> None:
    with admin.begin() as c:
        for acc in account_ids:
            c.execute(text("DELETE FROM email_accounts WHERE id = CAST(:a AS uuid)"),
                      {"a": acc})


def _thread_status(admin, account_id: str, thread: str) -> str | None:
    with admin.connect() as c:
        return c.execute(text(
            "SELECT status FROM email_thread_status "
            "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
            {"a": account_id, "t": thread}).scalar()


def _assert_nothing_of_a(context: str, *, mailbox_a: str) -> None:
    assert "Inbox snapshot" not in context, (
        "the context carries an inbox snapshot of a mailbox the member does "
        "not own"
    )
    assert SECRET_CATEGORY not in context, (
        "the context carries a sender category of another member's mailbox"
    )
    assert mailbox_a not in context, (
        "the context names the address of another member's mailbox"
    )


@_DB_GATE
class TestTheChatContextReadsOnlyAnOwnedMailbox:

    @pytest.mark.parametrize("own_boxes", [0, 2])
    async def test_a_foreign_account_id_reads_nothing_of_that_mailbox(
        self, promoted, app_engine, own_boxes,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        member_a = f"a-{uuid.uuid4().hex[:8]}@em-t2c.test"
        member_b = f"b-{uuid.uuid4().hex[:8]}@em-t2c.test"
        acc_a, mailbox_a, _ = _seed_mailbox_of_a(
            p.admin_engine, org=p.org_b, owner=member_a)
        own = [_seed_account(p.admin_engine, org=p.org_b, owner=member_b)[0]
               for _ in range(own_boxes)]
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                with _bound(p.org_b):
                    resolved, parts = await chat_mod._build_chat_context(
                        member_b, acc_a, None, True)
            assert resolved != acc_a, (
                "the chat context resolved to a mailbox the member does not own"
            )
            assert resolved is None, (
                "with zero or two mailboxes there is no single owned mailbox"
            )
            _assert_nothing_of_a("\n".join(parts), mailbox_a=mailbox_a)
        finally:
            _purge(p.admin_engine, [acc_a, *own])

    async def test_the_owner_still_gets_her_snapshot(
        self, promoted, app_engine,  # noqa: F811
    ):
        """The positive control: the fence is the owner check, not a broken
        query. Member A, naming her own mailbox, gets its snapshot."""
        _assert_non_priv(app_engine)
        p = promoted
        member_a = f"a-{uuid.uuid4().hex[:8]}@em-t2c.test"
        acc_a, mailbox_a, _ = _seed_mailbox_of_a(
            p.admin_engine, org=p.org_b, owner=member_a)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                with _bound(p.org_b):
                    resolved, parts = await chat_mod._build_chat_context(
                        member_a, acc_a, None, True)
            context = "\n".join(parts)
            assert resolved == acc_a
            assert "Inbox: 3 messages, 3 unread" in context
            assert "Needs reply (Reply Zero): 1" in context
            assert f"{SECRET_CATEGORY}: 1" in context
            assert mailbox_a in context
        finally:
            _purge(p.admin_engine, [acc_a])

    async def test_ai_chat_sends_no_foreign_id_and_reads_no_foreign_model(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Item 2: the route reads ``_account_models`` with the RESOLVED id."""
        _assert_non_priv(app_engine)
        p = promoted
        member_a = f"a-{uuid.uuid4().hex[:8]}@em-t2c.test"
        member_b = f"b-{uuid.uuid4().hex[:8]}@em-t2c.test"
        acc_a, mailbox_a, _ = _seed_mailbox_of_a(
            p.admin_engine, org=p.org_b, owner=member_a)

        seen: dict = {"model_ids": []}
        real_models = assistant_mod._account_models

        async def _spy_models(db, account_id):
            seen["model_ids"].append(account_id)
            return await real_models(db, account_id)

        def _fake_stream(agent, payload, **kw):
            seen["payload"] = payload
            seen["model"] = kw.get("model")

            async def _gen():
                if False:  # pragma: no cover - an empty stream
                    yield ""
            return _gen()

        import orchestrator.executor as executor_mod

        monkeypatch.setattr(assistant_mod, "_account_models", _spy_models)
        monkeypatch.setattr(executor_mod, "run_agent_stream", _fake_stream)
        user = UserContext(email=member_b, role=UserRole.EMPLOYEE,
                           organization_id=p.org_b)
        req = chat_mod.AIChatRequest(
            messages=[{"role": "user", "content": "what is in my inbox?"}],
            account_id=acc_a)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn):
                with _bound(p.org_b):
                    await chat_mod.ai_chat(req, user, BackgroundTasks())
            payload = seen["payload"]
            assert payload["account_id"] != acc_a, (
                "the agent payload carries the id of another member's mailbox"
            )
            _assert_nothing_of_a(payload["memory_context"], mailbox_a=mailbox_a)
            assert acc_a not in seen["model_ids"], (
                "ai_chat read the settings of a mailbox the member does not own"
            )
            assert seen["model"] != SECRET_MODEL
        finally:
            _purge(p.admin_engine, [acc_a])


@_DB_GATE
class TestTheTaskCloseWritesOnlyAnOwnedMailbox:

    @pytest.fixture()
    def reconciles(self, monkeypatch):
        calls: list = []

        async def _spy(account_id, thread_id, label):
            calls.append((account_id, thread_id, label))

        from gateway.routes.email import automation

        monkeypatch.setattr(automation, "_reconcile_labels_bg", _spy)
        return calls

    async def test_a_non_owner_close_leaves_the_thread_alone(
        self, promoted, app_engine, reconciles,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        member_a = f"a-{uuid.uuid4().hex[:8]}@em-t2c.test"
        member_b = f"b-{uuid.uuid4().hex[:8]}@em-t2c.test"
        acc_a, _, thread = _seed_mailbox_of_a(
            p.admin_engine, org=p.org_b, owner=member_a)
        task = SimpleNamespace(origin={
            "kind": "email", "account_id": acc_a, "thread_id": thread})
        app_dsn = p.app_url.render_as_string(hide_password=False)
        try:
            async with tenant_engine_scope(app_dsn),                     common_db.tenant_session(p.org_b) as db:
                await email_link.propagate_task_done_to_thread(
                    db, task, closer_email=member_b)
            assert _thread_status(p.admin_engine, acc_a, thread) == "NEEDS_REPLY", (
                "a member closed a task and wrote the thread status of a "
                "mailbox she does not own"
            )
            assert reconciles == [], (
                "a non-owner close reconciled the labels of another mailbox"
            )

            # The positive control: the owner's close marks it DONE.
            async with tenant_engine_scope(app_dsn),                     common_db.tenant_session(p.org_b) as db:
                await email_link.propagate_task_done_to_thread(
                    db, task, closer_email=member_a.upper())
            assert _thread_status(p.admin_engine, acc_a, thread) == "DONE"
            assert reconciles == [(acc_a, thread, "Done")]
        finally:
            _purge(p.admin_engine, [acc_a])
