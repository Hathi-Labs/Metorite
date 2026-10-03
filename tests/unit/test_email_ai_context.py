"""WS-17 EM-T8e-1 — self, the drafter and the server checks (gateway).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.5, under
"EM-T8e-1". Decisions D-EM-18, D-EM-19 and D-EM-27 (§11.2). Defect MB-14.

R7 fences named here:

* ``email-self-each-mailbox`` (R8): a mail from another mailbox of the member
  is ``self``. A mailbox of the same member in a second organization is not.
  That case runs as the non-privileged role, because the owner role bypasses
  row level security. Each caller of item 1 has a case: the payload, the
  thread status, the conversation check, the cold check, the digest, the pin
  guard, the cleanup scope and the sender categories.
* ``email-drafter-sending-mailbox``: the prompt and the local draft copy name
  the sending mailbox, never the sign-in address of the member.
* ``email-reply-other-mailbox-thread`` (R8): a reply from mailbox B to a mail
  of mailbox A reads the thread of A, and the voice items of B.
* ``email-pair-refused`` (R8): ``POST /email/reply-zero/resolve``,
  ``POST /email/rules/test`` and ``POST /email/rules/feedback`` answer 404 for
  a pair that does not match, and they write nothing.
* ``email-ai-context-one-mailbox`` (R8, D-EM-18): two mailboxes of one member,
  each with its own marker. For each background loader, the read for A holds
  no marker of B.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_ai_context.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email import digest as digest_mod
from gateway.routes.email.automation import actions as actions_mod
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import cleanup as cleanup_mod
from gateway.routes.email.automation import drafting as drafting_mod
from gateway.routes.email.automation import engine as engine_mod
from gateway.routes.email.automation import learning as learning_mod
from gateway.routes.email.automation import replyzero as replyzero_mod
from gateway.routes.email.automation import rules as rules_mod
from gateway.routes.email.automation import runner as runner_mod
from gateway.routes.email.automation import senders as senders_mod
from gateway.routes.email.automation import voice_profile as voice_mod
from gateway.routes.email.automation.identity import (
    SELF_ADDRESSES_SQL,
    SelfIdentity,
    resolve_self,
    sender_scope,
)
from gateway.routes.email.core import _date_range_clause, _tenant_session
from sqlalchemy import text

from tests.unit._email_fakes import bind_db
from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: The width of ``email_embeddings.embedding`` (migration 73).
_DIM = 1536
_UNIT = [1.0] + [0.0] * (_DIM - 1)
_VEC = "[" + ",".join(str(x) for x in _UNIT) + "]"

#: Every seeded mail is two days old, so each one is in a 7-day window.
_T0 = datetime.now(UTC) - timedelta(days=2)

RAVI = "ravi@contoso.test"


# ── Seeding (as the owner role, which escapes the policies under test) ────


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _assert_non_priv(app_eng) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


def _insert(admin, sql: str, params: dict) -> str:
    with admin.begin() as c:
        row = c.execute(text(sql), params).first()
    return str(row[0]) if row else ""


def _account(admin, *, org: str, owner: str, address: str) -> str:
    return _insert(admin, (
        "INSERT INTO email_accounts (user_id, provider, email_address, "
        "credentials_encrypted, initial_sync_done, organization_id) "
        "VALUES (:u, 'microsoft', :m, 'x', true, CAST(:o AS uuid)) RETURNING id"),
        {"u": owner, "m": address, "o": org})


def _mail(admin, *, org: str, account: str, frm: str, to: tuple = (),
          cc: tuple = (), thread: str | None = None, folder: str = "inbox",
          subject: str = "Hello", body: str = "Hello there.",
          categories: tuple = (), minutes: int = 0) -> str:
    return _insert(admin, (
        "INSERT INTO email_messages (account_id, provider_message_id, "
        "thread_id, folder, from_address, to_addresses, cc_addresses, subject, "
        "body_text, snippet, categories, received_at, organization_id) VALUES "
        "(CAST(:a AS uuid), :pm, :tid, :f, CAST(:frm AS jsonb), "
        "CAST(:to AS jsonb), CAST(:cc AS jsonb), :s, :b, :b, "
        "CAST(:cats AS text[]), :at, CAST(:o AS uuid)) RETURNING id"),
        {"a": account, "pm": f"pm-{uuid.uuid4().hex[:12]}", "tid": thread,
         "f": folder, "frm": json.dumps({"email": frm, "name": ""}),
         "to": json.dumps([{"email": t, "name": ""} for t in to]),
         "cc": json.dumps([{"email": t, "name": ""} for t in cc]),
         "s": subject, "b": body, "cats": list(categories),
         "at": _T0 + timedelta(minutes=minutes), "o": org})


def _rule(admin, *, org: str, account: str, name: str) -> str:
    return _insert(admin, (
        "INSERT INTO email_rules (account_id, name, instructions, enabled, "
        "organization_id) VALUES (CAST(:a AS uuid), :n, :i, true, "
        "CAST(:o AS uuid)) RETURNING id"),
        {"a": account, "n": name, "i": f"when {name}", "o": org})


def _exec(admin, sql: str, params: dict) -> None:
    with admin.begin() as c:
        c.execute(text(sql), params)


def _count(admin, sql: str, params: dict) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar() or 0)


def _purge(admin, *accounts: str) -> None:
    """Each child table of a mailbox cascades on its delete."""
    ids = [a for a in accounts if a]
    _exec(admin, "DELETE FROM email_accounts WHERE id = ANY(CAST(:a AS uuid[]))",
          {"a": ids})


@asynccontextmanager
async def _as_app(p, org: str):
    """The non-privileged role with ``org`` bound, as the request seam binds it."""
    app_dsn = p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(app_dsn):
            yield
    finally:
        release_tenant(token)


def _from(row) -> str:
    raw = row.from_address
    frm = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    return (frm.get("email") or "").lower()


@pytest.fixture()
def family(promoted, app_engine):  # noqa: F811
    """One member with mailboxes A and B in org B, and C in org A.

    D is a mailbox of another member in org B. The sign-in address of the
    member is the address of none of the mailboxes, so a prompt that names
    it is wrong on sight. B is a consumer address, so its label is
    "Personal" (``mailbox_identity``)."""
    _assert_non_priv(app_engine)
    p = promoted
    t = _tag()
    f = SimpleNamespace(
        p=p, admin=p.admin_engine, org=p.org_b, other_org=p.org_a, tag=t,
        member=f"member-{t}@signin-t8e.test",
        stranger=f"stranger-{t}@signin-t8e.test",
        a_addr=f"vj-{t}@fracktal-t8e.test", b_addr=f"vj-{t}@gmail.com",
        c_addr=f"vj-{t}@other-org-t8e.test", d_addr=f"x-{t}@fracktal-t8e.test")
    f.a = _account(f.admin, org=f.org, owner=f.member, address=f.a_addr)
    f.b = _account(f.admin, org=f.org, owner=f.member, address=f.b_addr)
    f.c = _account(f.admin, org=f.other_org, owner=f.member, address=f.c_addr)
    f.d = _account(f.admin, org=f.org, owner=f.stranger, address=f.d_addr)
    f.user = UserContext(email=f.member, role=UserRole.EMPLOYEE,
                         organization_id=f.org)
    try:
        yield f
    finally:
        _purge(f.admin, f.a, f.b, f.c, f.d)


# ══════════════════════════════════════════════════════════════════════════
# 1. email-self-each-mailbox (R8): "self" is each mailbox of the member
# ══════════════════════════════════════════════════════════════════════════


def test_one_address_still_works_and_a_set_adds_the_other_mailboxes() -> None:
    """``routes/crm/auto_lead.py`` calls ``sender_scope`` with ONE address."""
    assert sender_scope("me@acme.test", "me@acme.test") == "self"
    assert sender_scope("ops@acme.test", "me@acme.test") == "internal"
    assert sender_scope("vj@gmail.com", "me@acme.test") == "external"
    assert sender_scope("VJ@Gmail.com", "me@acme.test",
                        self_addresses={"vj@gmail.com"}) == "self"
    # The internal domain stays the domain of the current mailbox.
    assert sender_scope("pa@gmail.com", "me@acme.test",
                        self_addresses={"vj@gmail.com"}) == "external"


@_DB_GATE
class TestSelfIsEachMailboxOfTheMember:

    async def test_the_set_is_the_mailboxes_of_the_member_in_this_org(self, family):
        f = family
        async with _as_app(f.p, f.org), _tenant_session() as db:
            me = await resolve_self(db, f.a)
        assert me.address == f.a_addr
        assert me.label == "Fracktal-t8e"
        assert me.self_addresses == {f.a_addr, f.b_addr}
        assert f.c_addr not in me.self_addresses, (
            "a mailbox of the same member in ANOTHER organization is not self")
        assert f.d_addr not in me.self_addresses, "another member is not self"
        assert sender_scope(f.b_addr, f.a_addr,
                            self_addresses=me.self_addresses) == "self"
        assert sender_scope(f.c_addr, f.a_addr,
                            self_addresses=me.self_addresses) == "external"
        # The bind decides it. In org A the member has one mailbox, and the
        # mailboxes of org B are not there at all.
        async with _as_app(f.p, f.other_org), _tenant_session() as db:
            there = await resolve_self(db, f.c)
            unseen = await resolve_self(db, f.a)
        assert there.self_addresses == {f.c_addr}
        assert unseen == SelfIdentity()

    def test_the_org_predicate_holds_where_rls_does_not_bind(self, family):
        """The owner role bypasses row level security, so only the
        organization predicate of ``SELF_ADDRESSES_SQL`` keeps mailbox C out
        here. It is the defence for a session that no tenant binds."""
        f = family
        with f.admin.connect() as c:
            got = {r[0] for r in c.execute(text(SELF_ADDRESSES_SQL), {"aid": f.a})}
        assert got == {f.a_addr, f.b_addr}

    async def test_the_rule_match_payload_reads_another_mailbox_as_self(self, family):
        f = family
        from_b = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(f.a_addr,))
        from_c = _mail(f.admin, org=f.org, account=f.a, frm=f.c_addr, to=(f.a_addr,))
        to_b = _mail(f.admin, org=f.org, account=f.a, frm=RAVI, to=(f.b_addr,),
                     cc=(f.a_addr,))
        async with _as_app(f.p, f.org), _tenant_session() as db:
            pb = await engine_mod._email_payload_from_id(
                db, from_b, f.member, account_id=f.a)
            pc = await engine_mod._email_payload_from_id(
                db, from_c, f.member, account_id=f.a)
            pr = await engine_mod._email_payload_from_id(
                db, to_b, f.member, account_id=f.a)
        assert pb["sender_scope"] == "self"
        assert pc["sender_scope"] == "external"
        # In To under mailbox B is a direct recipient (D-EM-27).
        assert pr["recipient_role"] == "direct"

    async def test_the_thread_and_the_conversation_read_another_mailbox_as_ours(
        self, family,
    ):
        f = family
        t1, t2 = f"t1-{f.tag}", f"t2-{f.tag}"
        _mail(f.admin, org=f.org, account=f.a, frm=RAVI, thread=t1, minutes=0)
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(RAVI,),
              thread=t1, minutes=5)
        _mail(f.admin, org=f.org, account=f.a, frm=RAVI, thread=t2, minutes=0)
        _mail(f.admin, org=f.org, account=f.a, frm=f.c_addr, to=(RAVI,),
              thread=t2, minutes=5)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            ours = await replyzero_mod.build_thread_context(db, f.a, t1, f.a_addr)
            theirs = await replyzero_mod.build_thread_context(db, f.a, t2, f.a_addr)
            conv1 = await replyzero_mod._thread_is_conversation(db, f.a, t1)
            conv2 = await replyzero_mod._thread_is_conversation(db, f.a, t2)
        assert ours is not None and ours.our_side_last, (
            "a reply from another mailbox of the member left the thread awaiting")
        assert ours.thread_text.rstrip().split("---")[-1].count("(you sent)") == 1
        assert theirs is not None and not theirs.our_side_last
        assert conv1 is True
        assert conv2 is False

    async def test_the_digest_leaves_another_mailbox_out(self, family):
        f = family
        _mail(f.admin, org=f.org, account=f.a, frm=RAVI, subject="from ravi")
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, subject="from b")
        _mail(f.admin, org=f.org, account=f.a, frm=f.c_addr, subject="from c")
        thread = f"aw-{f.tag}"
        last = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(RAVI,),
                     thread=thread, subject="the quote", folder="sent")
        _exec(f.admin, (
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "last_message_id, last_message_at, reason, organization_id) VALUES "
            "(CAST(:a AS uuid), :t, 'AWAITING', CAST(:m AS uuid), now(), 'seed', "
            "CAST(:o AS uuid))"),
            {"a": f.a, "t": thread, "m": last, "o": f.org})
        async with _as_app(f.p, f.org), _tenant_session() as db:
            totals = await digest_mod._digest_totals(db, {"aid": f.a, "days": 7})
            awaiting = await digest_mod._digest_awaiting(db, f.a)
        assert totals["inbox"] == 2, "mail from mailbox B is not new inbound mail"
        assert [w["who"] for w in awaiting] == [RAVI], (
            "the last message is ours, so the counterparty is its recipient")

    async def test_the_cleanup_scope_leaves_another_mailbox_out(self, family):
        f = family
        for frm in (RAVI, f.b_addr, f.c_addr):
            _mail(f.admin, org=f.org, account=f.a, frm=frm, subject=f"from {frm}")
        async with _as_app(f.p, f.org), _tenant_session() as db:
            rows = await cleanup_mod._uncategorized_inbox(
                db, f.a, 50, internal_domains=frozenset({"fracktal-t8e.test"}))
        senders = {_from(r) for r in rows}
        assert f.b_addr not in senders
        assert {RAVI, f.c_addr} <= senders

    async def test_the_pin_guard_never_pins_another_mailbox(self, family):
        f = family
        rid = _rule(f.admin, org=f.org, account=f.a, name="Newsletter")
        async with _as_app(f.p, f.org), _tenant_session() as db:
            pinned_b = await rules_mod._upsert_rule_pattern(
                db, f.a, rid, f.b_addr, False, "AI", "seed", None, None)
            pinned_x = await rules_mod._upsert_rule_pattern(
                db, f.a, rid, "news@vendor.test", False, "AI", "seed", None, None)
        assert pinned_b is False
        assert pinned_x is True

    async def test_the_cold_check_never_flags_another_mailbox(self, family, monkeypatch):
        f = family
        from_b = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(f.a_addr,))
        from_x = _mail(f.admin, org=f.org, account=f.a, frm="pitch@vendor.test",
                       to=(f.a_addr,))
        llm = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(senders_mod, "_llm_is_cold", llm)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            pb = await engine_mod._email_payload_from_id(
                db, from_b, f.member, account_id=f.a)
            await senders_mod._maybe_block_cold(
                db, None, f.a, from_b, "pm-b", pb, "LABEL")
            assert llm.await_count == 0, "mail from mailbox B reached the cold check"
            px = await engine_mod._email_payload_from_id(
                db, from_x, f.member, account_id=f.a)
            await senders_mod._maybe_block_cold(
                db, None, f.a, from_x, "pm-x", px, "LABEL")
        assert llm.await_count == 1, "the positive control: a stranger is checked"

    async def test_the_sender_categories_leave_another_mailbox_out(self, family):
        f = family
        for i in range(3):
            _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr,
                  categories=("Newsletter",), minutes=i)
            _mail(f.admin, org=f.org, account=f.a, frm="news@vendor.test",
                  categories=("Newsletter",), minutes=i)
        async with _as_app(f.p, f.org):
            await senders_mod._categorize_senders_job(f.a, 25)
        with f.admin.connect() as c:
            got = {r[0] for r in c.execute(text(
                "SELECT email FROM email_senders WHERE account_id = CAST(:a AS uuid)"),
                {"a": f.a})}
        assert "news@vendor.test" in got
        assert f.b_addr not in got


# ══════════════════════════════════════════════════════════════════════════
# 2. email-drafter-sending-mailbox: the drafter speaks as the mailbox (MB-14)
# ══════════════════════════════════════════════════════════════════════════

SIGN_IN = "member@signin-t8e.test"
BOX = "sales@fracktal-t8e.test"


def _fake_completion(captured: dict):
    async def fake(*, model, messages, **_kw):
        captured["messages"] = messages
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "Hi Ravi,\n\nHere is the quote."
        return resp, model
    return fake


def _prompt(captured: dict) -> str:
    return "\n".join(m["content"] for m in captured["messages"])


async def test_the_reply_prompt_names_the_sending_mailbox() -> None:
    captured: dict = {}
    with patch("acb_llm.context.acompletion_with_fallback",
               _fake_completion(captured)):
        await drafting_mod._llm_draft_reply(
            {"from": RAVI, "from_name": "Ravi", "subject": "Quote",
             "body": "Please send the quote.", "self": BOX, "self_label": "Sales"},
            about="", signature="", user_email=SIGN_IN)
    prompt = _prompt(captured)
    assert f"You are drafting as: Sales <{BOX}>" in prompt
    assert SIGN_IN not in prompt


async def test_with_no_mailbox_the_reply_prompt_names_nobody() -> None:
    captured: dict = {}
    with patch("acb_llm.context.acompletion_with_fallback",
               _fake_completion(captured)):
        await drafting_mod._llm_draft_reply(
            {"from": RAVI, "from_name": "Ravi", "subject": "Quote",
             "body": "Please send the quote."},
            about="", signature="", user_email=SIGN_IN)
    prompt = _prompt(captured)
    assert "You are drafting as" not in prompt
    assert SIGN_IN not in prompt, "the sign-in address is not a safe stand-in"


async def test_the_compose_prompt_names_the_sending_mailbox() -> None:
    captured: dict = {}
    with patch("acb_llm.context.acompletion_with_fallback",
               _fake_completion(captured)):
        await drafting_mod._llm_compose_assist(
            about="", signature="", current_body="", instruction="Ask for a quote",
            mode="new", recipient=RAVI, user_email=SIGN_IN, sender=f"Sales <{BOX}>")
    prompt = _prompt(captured)
    assert f"You are writing as: Sales <{BOX}>" in prompt
    assert SIGN_IN not in prompt


async def test_new_mail_in_compose_assist_names_the_sending_mailbox(monkeypatch) -> None:
    compose = AsyncMock(return_value="Hello Ravi")
    for name, value in (
        ("_tenant_session", bind_db(AsyncMock())),
        ("_assert_account_owner", AsyncMock()),
        ("_load_assistant_about", AsyncMock(return_value=("", ""))),
        ("_account_models", AsyncMock(return_value={"compose": "tier-fast"})),
        ("resolve_self", AsyncMock(return_value=SelfIdentity(
            address=BOX, label="Sales", self_addresses=frozenset({BOX})))),
        ("_llm_compose_assist", compose),
    ):
        monkeypatch.setattr(drafting_mod, name, value)
    await drafting_mod._compose_assist_run(
        drafting_mod.ComposeAssistRequest(account_id="acc-b", mode="new", to=[RAVI]),
        SimpleNamespace(email=SIGN_IN))
    assert compose.await_args.kwargs["sender"] == f"Sales <{BOX}>"


async def test_the_draft_reply_copy_stores_the_mailbox_as_from(monkeypatch) -> None:
    upsert = AsyncMock(return_value="local-1")
    provider = SimpleNamespace(create_draft=AsyncMock(return_value="pd-1"))

    @asynccontextmanager
    async def _session(db, user_email, **kw):
        yield SimpleNamespace(authed=True, provider=provider, account_id="acc-b",
                              provider_message_id="pm-1")

    for name, value in (
        ("_tenant_session", bind_db(AsyncMock())),
        ("_assert_account_owner", AsyncMock()),
        ("_build_reply_context", AsyncMock(return_value={
            "from": RAVI, "from_name": "Ravi", "subject": "Quote",
            "body": "Please send it.", "thread_id": "t1",
            "self": BOX, "self_label": "Sales"})),
        ("_load_assistant_about", AsyncMock(return_value=("", ""))),
        ("_account_models", AsyncMock(return_value={"compose": "tier-fast"})),
        ("_agent_draft_reply", AsyncMock(return_value="Hello Ravi")),
        ("_store_ai_draft", AsyncMock()),
        ("provider_session", _session),
        ("_upsert_local_draft", upsert),
    ):
        monkeypatch.setattr(drafting_mod, name, value)
    out = await drafting_mod.draft_reply_smart(
        drafting_mod.DraftReplyRequest(account_id="acc-b", message_id="m1",
                                       create_draft=True),
        user=SimpleNamespace(email=SIGN_IN))
    assert out["created"] is True
    assert upsert.await_args.kwargs["owner_email"] == BOX


async def test_a_rule_draft_copy_stores_the_mailbox_as_from() -> None:
    upsert = AsyncMock(return_value="local-1")
    provider = SimpleNamespace(create_draft=AsyncMock(return_value="pd-1"))
    with patch.object(actions_mod, "_upsert_local_draft", upsert), \
            patch.object(actions_mod, "_resolve_existing_thread_draft",
                         AsyncMock(return_value="none")):
        done = await actions_mod._apply_rule_actions(
            AsyncMock(), provider, "m1", "pm-1",
            [{"type": "DRAFT_EMAIL", "content": "Thanks, we are on it."}],
            {"from": RAVI, "subject": "Quote", "body": "Send it.",
             "thread_id": "t1", "self": BOX},
            "", "", SIGN_IN, account_id="acc-b")
    assert done == ["DRAFT_EMAIL"]
    assert upsert.await_args.kwargs["owner_email"] == BOX


async def test_a_payload_with_no_self_reads_the_mailbox_row() -> None:
    """``approve_execution`` and ``retry_failed_executions`` build a payload
    with no ``self``. The copy still names the mailbox, from its row."""
    upsert = AsyncMock(return_value="local-1")
    provider = SimpleNamespace(create_draft=AsyncMock(return_value="pd-1"))
    resolve = AsyncMock(return_value=SelfIdentity(address=BOX))
    with patch.object(actions_mod, "_upsert_local_draft", upsert), \
            patch.object(actions_mod, "resolve_self", resolve):
        done = await actions_mod._apply_rule_actions(
            AsyncMock(), provider, "m1", "pm-1",
            [{"type": "FORWARD", "to_address": "boss@fracktal-t8e.test"}],
            {"from": RAVI, "subject": "Quote", "body": "Send it."},
            "", "", SIGN_IN, account_id="acc-b")
    assert done == ["FORWARD"]
    assert upsert.await_args.kwargs["owner_email"] == BOX
    assert resolve.await_args.args[1] == "acc-b"


# ══════════════════════════════════════════════════════════════════════════
# 3. email-reply-other-mailbox-thread (R8): the thread of A, the voice of B
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def reply_across(family, monkeypatch):
    """Mailbox A holds a thread with Ravi. Each of A and B has its own sent
    mail to Ravi, reply memory, embedding, settings and voice profile."""
    f = family
    thread = f"ta-{f.tag}"
    _mail(f.admin, org=f.org, account=f.a, frm=RAVI, to=(f.a_addr,),
          thread=thread, subject="Quote", body="THREAD-OF-A: the first ask.")
    f.mail = _mail(f.admin, org=f.org, account=f.a, frm=RAVI, to=(f.a_addr,),
                   thread=thread, subject="Re: Quote",
                   body="Please send the price list.", minutes=10)
    f.mail_d = _mail(f.admin, org=f.org, account=f.d, frm=RAVI, to=(f.d_addr,),
                     thread=f"td-{f.tag}", subject="Quote", body="Of member D.")
    for acc, addr, mark in ((f.a, f.a_addr, "A"), (f.b, f.b_addr, "B")):
        sent = _mail(f.admin, org=f.org, account=acc, frm=addr, to=(RAVI,),
                     folder="sent", thread=f"s{mark}-{f.tag}", subject="Old",
                     body=f"VOICE-OF-{mark}: kind regards.", minutes=-100)
        _exec(f.admin, (
            "INSERT INTO email_embeddings (message_id, account_id, embedding, "
            "model, content_hash, organization_id) VALUES (CAST(:m AS uuid), "
            "CAST(:a AS uuid), CAST(:v AS vector), 'seed', 'seed', CAST(:o AS uuid))"),
            {"m": sent, "a": acc, "v": _VEC, "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_learned_patterns (account_id, pattern, scope_type, "
            "scope_value, organization_id) VALUES (CAST(:a AS uuid), :p, 'SENDER', "
            ":s, CAST(:o AS uuid))"),
            {"a": acc, "p": f"MEMORY-OF-{mark}", "s": RAVI, "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_assistant_settings (account_id, about, signature, "
            "organization_id) VALUES (CAST(:a AS uuid), :ab, :sig, CAST(:o AS uuid))"),
            {"a": acc, "ab": f"ABOUT-OF-{mark}", "sig": f"SIG-OF-{mark}", "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_voice_profiles (account_id, style_guide, status, "
            "organization_id) VALUES (CAST(:a AS uuid), :g, 'READY', "
            "CAST(:o AS uuid))"),
            {"a": acc, "g": f"VOICE-PROFILE-OF-{mark}", "o": f.org})
    from email_ingestion import email_embeddings
    monkeypatch.setattr(email_embeddings, "embed_query",
                        AsyncMock(return_value=list(_UNIT)))
    return f


@_DB_GATE
class TestAReplyFromAnotherMailbox:

    async def test_it_reads_the_thread_of_a_and_the_voice_of_b(self, reply_across):
        f = reply_across
        async with _as_app(f.p, f.org), _tenant_session() as db:
            ctx = await drafting_mod._build_reply_context(
                db, f.b, f.mail, f.member, from_any_owned_mailbox=True)
        assert ctx is not None
        assert "THREAD-OF-A" in ctx["thread"]
        assert "VOICE-OF-B" in ctx["sender_examples"]
        assert "VOICE-OF-A" not in ctx["sender_examples"]
        assert "MEMORY-OF-B" in ctx["reply_memories"]
        assert "MEMORY-OF-A" not in ctx["reply_memories"]
        assert "VOICE-OF-B" in ctx["sent_examples"]
        assert "VOICE-OF-A" not in ctx["sent_examples"]
        assert (ctx["self"], ctx["self_label"]) == (f.b_addr, "Personal")

    async def test_draft_reply_and_another_member_get_no_context(self, reply_across):
        f = reply_across
        async with _as_app(f.p, f.org), _tenant_session() as db:
            plain = await drafting_mod._build_reply_context(
                db, f.b, f.mail, f.member)
            other = await drafting_mod._build_reply_context(
                db, f.b, f.mail_d, f.member, from_any_owned_mailbox=True)
        assert plain is None, "/draft-reply keeps the mail in its own mailbox"
        assert other is None, "the owner predicate keeps another member's mail out"

    async def test_compose_assist_uses_the_voice_and_signature_of_b(
        self, reply_across, monkeypatch,
    ):
        f = reply_across
        seen: dict = {}

        async def fake_agent_draft(email, about, signature, user_email, **_kw):
            seen.update(email=email, about=about, signature=signature)
            return "Hello Ravi,\n\nHere is the price list."

        monkeypatch.setattr(drafting_mod, "_agent_draft_reply", fake_agent_draft)
        async with _as_app(f.p, f.org):
            out = await drafting_mod._compose_assist_run(
                drafting_mod.ComposeAssistRequest(
                    account_id=f.b, mode="reply", message_id=f.mail), f.user)
        assert "THREAD-OF-A" in seen["email"]["thread"]
        assert "ABOUT-OF-B" in seen["about"]
        assert "VOICE-PROFILE-OF-B" in seen["about"]
        assert "OF-A" not in seen["about"]
        assert seen["signature"] == "SIG-OF-B"
        assert "SIG-OF-B" in out["draft"]


# ══════════════════════════════════════════════════════════════════════════
# 4. email-pair-refused (R8): the three routes refuse a pair that differs
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def pair(family):
    f = family
    f.thread_b = f"tb-{f.tag}"
    f.mail_b = _mail(f.admin, org=f.org, account=f.b, frm=RAVI, to=(f.b_addr,),
                     thread=f.thread_b, subject="Quote")
    f.rule_a = _rule(f.admin, org=f.org, account=f.a, name="Newsletter")
    f.rule_b = _rule(f.admin, org=f.org, account=f.b, name="Newsletter")
    return f


@_DB_GATE
class TestThePairIsRefused:

    @pytest.mark.parametrize("branch", ["done", "reopen", "dismiss"])
    async def test_resolve_refuses_a_thread_of_another_mailbox(self, pair, branch):
        f = pair
        req = replyzero_mod.ThreadResolveRequest(
            account_id=f.a, thread_id=f.thread_b,
            done=branch == "done", dismiss=branch == "dismiss")
        async with _as_app(f.p, f.org):
            with pytest.raises(HTTPException) as exc:
                await replyzero_mod.resolve_thread(req, BackgroundTasks(), user=f.user)
            ok = await replyzero_mod.resolve_thread(
                replyzero_mod.ThreadResolveRequest(
                    account_id=f.b, thread_id=f.thread_b, dismiss=True),
                BackgroundTasks(), user=f.user)
        assert exc.value.status_code == 404
        assert ok["ok"] is True, "the positive control: the right pair passes"
        assert _count(f.admin, (
            "SELECT count(*) FROM email_thread_status "
            "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
            {"a": f.a, "t": f.thread_b}) == 0

    async def test_rule_test_refuses_a_mail_of_another_mailbox(self, pair, monkeypatch):
        f = pair
        monkeypatch.setattr(runner_mod, "_match_email_to_rule",
                            AsyncMock(return_value=None))
        async with _as_app(f.p, f.org):
            with pytest.raises(HTTPException) as exc:
                await runner_mod.test_rules(runner_mod.RuleTestRequest(
                    account_id=f.a, email_id=f.mail_b), user=f.user)
            ok = await runner_mod.test_rules(runner_mod.RuleTestRequest(
                account_id=f.b, email_id=f.mail_b), user=f.user)
        assert exc.value.status_code == 404
        assert ok["matched"] is False

    @pytest.mark.parametrize("where", ["expected", "matched_rule_ids"])
    async def test_feedback_refuses_a_rule_of_another_mailbox(self, pair, where):
        f = pair
        bad = (rules_mod.RuleFeedbackRequest(
            account_id=f.a, sender="news@vendor.test", expected=f.rule_b,
            pin_sender=True) if where == "expected" else
            rules_mod.RuleFeedbackRequest(
                account_id=f.a, sender="news@vendor.test", expected="none",
                matched_rule_ids=[f.rule_b], pin_sender=True))
        async with _as_app(f.p, f.org):
            with pytest.raises(HTTPException) as exc:
                await rules_mod.rule_feedback(bad, user=f.user)
            ok = await rules_mod.rule_feedback(rules_mod.RuleFeedbackRequest(
                account_id=f.a, sender="news@vendor.test", expected=f.rule_a,
                pin_sender=True), user=f.user)
        assert exc.value.status_code == 404
        assert ok["created"] is True, "the positive control learns the pattern"
        assert _count(f.admin, (
            "SELECT count(*) FROM email_rule_patterns "
            "WHERE rule_id = CAST(:r AS uuid)"), {"r": f.rule_b}) == 0


# ══════════════════════════════════════════════════════════════════════════
# 5. email-ai-context-one-mailbox (R8, D-EM-18): each loader reads one mailbox
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def marked(family):
    """A and B carry the same kinds of rows, marked ``MARK-A`` and ``MARK-B``.

    The thread id is the SAME in both mailboxes, so a loader that forgot
    ``account_id`` would mix them. Only B holds the conversation evidence of
    the shared sender."""
    f = family
    f.shared = f"shared-{f.tag}@sender.test"
    f.thread = f"tshared-{f.tag}"
    f.rules = {}
    history = learning_mod._AUTO_LEARN_MIN_CONSISTENT - 1
    for acc, addr, m in ((f.a, f.a_addr, "a"), (f.b, f.b_addr, "b")):
        mark = f"MARK-{m.upper()}"
        rid = _rule(f.admin, org=f.org, account=acc, name=f"Rule {mark}")
        f.rules[m] = rid
        _exec(f.admin, (
            "INSERT INTO email_rule_patterns (account_id, rule_id, pattern_type, "
            "value, exclude, source, organization_id) VALUES (CAST(:a AS uuid), "
            "CAST(:r AS uuid), 'FROM', :v, false, 'FIX', CAST(:o AS uuid))"),
            {"a": acc, "r": rid, "v": f"pattern-{m}@sender.test", "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_rule_guidance (account_id, rule_id, guidance, "
            "organization_id) VALUES (CAST(:a AS uuid), CAST(:r AS uuid), :g, "
            "CAST(:o AS uuid))"),
            {"a": acc, "r": rid, "g": f"guidance {mark}", "o": f.org})
        first = _mail(f.admin, org=f.org, account=acc, frm=f.shared, to=(addr,),
                      thread=f.thread, subject=f"thread {mark}",
                      body=f"thread body {mark}")
        last = _mail(f.admin, org=f.org, account=acc, frm=f.shared, to=(addr,),
                     thread=f.thread, subject=f"thread {mark} 2",
                     body=f"thread body {mark} 2", minutes=5)
        for i in range(history):
            hm = _mail(f.admin, org=f.org, account=acc, frm=f.shared,
                       thread=f"h{m}{i}-{f.tag}", subject=f"history {mark}",
                       body=f"history body {mark}", minutes=-50 - i)
            _exec(f.admin, (
                "INSERT INTO email_executed_rules (account_id, rule_id, rule_name, "
                "message_id, from_address, status, organization_id) VALUES "
                "(CAST(:a AS uuid), CAST(:r AS uuid), :n, CAST(:m AS uuid), :f, "
                "'APPLIED', CAST(:o AS uuid))"),
                {"a": acc, "r": rid, "n": f"Hist {mark}", "m": hm,
                 "f": f.shared, "o": f.org})
        sent = _mail(f.admin, org=f.org, account=acc, frm=addr,
                     to=(f.shared, f"only-{m}@sender.test"), folder="sent",
                     thread=f"s{m}-{f.tag}", subject=f"sent {mark}",
                     body=f"sent body {mark}", minutes=-100)
        _exec(f.admin, (
            "INSERT INTO email_embeddings (message_id, account_id, embedding, "
            "model, content_hash, organization_id) VALUES (CAST(:m AS uuid), "
            "CAST(:a AS uuid), CAST(:v AS vector), 'seed', 'seed', CAST(:o AS uuid))"),
            {"m": sent, "a": acc, "v": _VEC, "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_assistant_settings (account_id, about, signature, "
            "organization_id) VALUES (CAST(:a AS uuid), :ab, :sig, CAST(:o AS uuid))"),
            {"a": acc, "ab": f"about {mark}", "sig": f"sig {mark}", "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_voice_profiles (account_id, style_guide, status, "
            "organization_id) VALUES (CAST(:a AS uuid), :g, 'READY', "
            "CAST(:o AS uuid))"), {"a": acc, "g": f"voice {mark}", "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_learned_patterns (account_id, pattern, scope_type, "
            "scope_value, organization_id) VALUES (CAST(:a AS uuid), :p, 'GLOBAL', "
            "'', CAST(:o AS uuid))"), {"a": acc, "p": f"learned {mark}", "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "last_message_id, last_message_at, reason, organization_id) VALUES "
            "(CAST(:a AS uuid), :t, 'NEEDS_REPLY', CAST(:m AS uuid), now(), "
            "'seed', CAST(:o AS uuid))"),
            {"a": acc, "t": f.thread, "m": last, "o": f.org})
        assert first and sent
    _mail(f.admin, org=f.org, account=f.b, frm=f.shared, to=(f.b_addr,),
          thread=f"ev-{f.tag}", subject="evidence MARK-B",
          categories=("Needs Reply",), minutes=-10)
    return f


async def _in_a(f, read):
    """Run ``read(db)`` as the non-privileged role, bound to the org."""
    async with _as_app(f.p, f.org), _tenant_session() as db:
        return await read(db)


def _only_a(blob: object) -> None:
    dump = json.dumps(blob, default=str)
    assert "MARK-A" in dump, "the positive control: A's own rows are read"
    assert "MARK-B" not in dump, "a loader for mailbox A read a row of mailbox B"


@_DB_GATE
class TestEachLoaderReadsOneMailbox:

    async def test_the_rules(self, marked):
        rules = await _in_a(marked, lambda db: rules_mod._load_rules(db, marked.a))
        _only_a([r["name"] for r in rules])

    async def test_the_rule_patterns(self, marked):
        pats = await _in_a(
            marked, lambda db: engine_mod._load_rule_patterns(db, marked.a))
        values = [v for d in pats.values() for pt in d.values() for _, v in pt]
        assert values == ["pattern-a@sender.test"]

    async def test_the_rule_guidance(self, marked):
        _only_a(await _in_a(
            marked, lambda db: engine_mod._load_rule_guidance(db, marked.a)))

    async def test_the_sender_history(self, marked):
        _only_a(await _in_a(marked, lambda db: engine_mod._fetch_sender_history(
            db, marked.a, marked.shared)))

    async def test_the_thread_context(self, marked):
        ctx = await _in_a(marked, lambda db: replyzero_mod.build_thread_context(
            db, marked.a, marked.thread, marked.a_addr))
        _only_a(ctx.thread_text)

    async def test_the_prior_contact_of_the_cold_check(self, marked):
        async def read(db):
            return (await senders_mod._has_prior_contact(
                        db, marked.a, "only-a@sender.test"),
                    await senders_mod._has_prior_contact(
                        db, marked.a, "only-b@sender.test"))
        assert await _in_a(marked, read) == (True, False)

    async def test_the_sender_pin_evidence(self, marked):
        async def read(db):
            return (await learning_mod._sender_is_a_correspondent(
                        db, marked.a, marked.shared),
                    await learning_mod._sender_is_a_correspondent(
                        db, marked.b, marked.shared),
                    await learning_mod._sender_consistent_for_rule(
                        db, marked.a, marked.shared, marked.rules["a"]))
        correspondent_a, correspondent_b, consistent_a = await _in_a(marked, read)
        assert correspondent_a is False, "the evidence of mailbox B pinned mailbox A"
        assert correspondent_b is True
        assert consistent_a is True, "a rule of mailbox B vetoed the pin in A"

    async def test_the_voice_samples(self, marked):
        _only_a(await _in_a(marked, lambda db: voice_mod._fetch_sample_bodies(
            db, marked.a, ["sent"], None, None)))

    async def test_the_sent_few_shot(self, marked, monkeypatch):
        from email_ingestion import email_embeddings
        monkeypatch.setattr(email_embeddings, "embed_query",
                            AsyncMock(return_value=list(_UNIT)))
        _only_a(await _in_a(marked, lambda db: drafting_mod._fetch_sent_fewshot(
            db, marked.a, "subject", "body")))

    async def test_the_assistant_context(self, marked):
        _only_a(await _in_a(
            marked, lambda db: assistant_mod._load_assistant_about(db, marked.a)))

    async def test_the_process_past_range(self, marked):
        async def read(db):
            clause, params = _date_range_clause(marked.a, None, None)
            return [r[0] for r in (await db.execute(text(
                f"SELECT em.subject FROM email_messages em WHERE {clause}"),
                params)).fetchall()]
        _only_a(await _in_a(marked, read))

    async def test_the_digest_window(self, marked):
        async def read(db):
            return (await digest_mod._digest_totals(
                        db, {"aid": marked.a, "days": 7}),
                    await digest_mod._digest_backlog_aging(db, marked.a, 50))
        totals, backlog = await _in_a(marked, read)
        history = learning_mod._AUTO_LEARN_MIN_CONSISTENT - 1
        assert totals["inbox"] == 2 + history, "only the inbox mail of mailbox A"
        _only_a([b["subject"] for b in backlog])

    async def test_the_pending_embeddings(self, marked, monkeypatch):
        from email_ingestion.email_embeddings import select_pending_embeddings
        monkeypatch.setattr(get_settings(), "email_semantic_search_enabled", True,
                            raising=False)
        pending = await _in_a(
            marked, lambda db: select_pending_embeddings(db, marked.a, batch=200))
        assert pending is not None
        _only_a(list(pending.texts))
