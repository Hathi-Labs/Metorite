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


def _addr_list(addrs: tuple | None) -> str | None:
    """A recipient list as JSON, or None for a NULL column."""
    if addrs is None:
        return None
    return json.dumps([{"email": t, "name": ""} for t in addrs])


def _mail(admin, *, org: str, account: str, frm: str, to: tuple = (),
          cc: tuple | None = (), bcc: tuple | None = None,
          thread: str | None = None, folder: str = "inbox",
          subject: str = "Hello", body: str = "Hello there.",
          categories: tuple = (), minutes: int = 0,
          imid: str | None = None) -> str:
    """One stored mail. ``cc`` and ``bcc`` take None for a NULL column."""
    return _insert(admin, (
        "INSERT INTO email_messages (account_id, provider_message_id, "
        "thread_id, folder, from_address, to_addresses, cc_addresses, "
        "bcc_addresses, subject, body_text, snippet, categories, received_at, "
        "internet_message_id, organization_id) VALUES "
        "(CAST(:a AS uuid), :pm, :tid, :f, CAST(:frm AS jsonb), "
        "CAST(:to AS jsonb), CAST(:cc AS jsonb), CAST(:bcc AS jsonb), :s, :b, "
        ":b, CAST(:cats AS text[]), :at, :imid, CAST(:o AS uuid)) RETURNING id"),
        {"a": account, "pm": f"pm-{uuid.uuid4().hex[:12]}", "tid": thread,
         "f": folder, "frm": json.dumps({"email": frm, "name": ""}),
         "to": _addr_list(to), "cc": _addr_list(cc), "bcc": _addr_list(bcc),
         "s": subject, "b": body, "cats": list(categories),
         "at": _T0 + timedelta(minutes=minutes), "imid": imid, "o": org})


def _message_id() -> str:
    """A Message-ID. The seed writes it by hand. In production only the
    Outlook provider stores one (§11.6 edge case 26)."""
    return f"<{uuid.uuid4().hex}@outlook.test>"


def _sent_by_b(f, *, to: tuple, subject: str = "From B",
               thread: str | None = None) -> tuple[str, str]:
    """A mail that the member really sent from mailbox B to mailbox A.

    B's Sent copy and A's inbox copy share one ``internet_message_id``, which
    is the proof ``identity.proven_own_send`` reads. Only the Outlook provider
    stores that column, so in production the proof exists only between two
    Outlook mailboxes. Returns the id of the copy in A, and the Message-ID."""
    imid = _message_id()
    _mail(f.admin, org=f.org, account=f.b, frm=f.b_addr, to=to, folder="sent",
          subject=subject, thread=thread, imid=imid)
    in_a = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=to,
                 subject=subject, thread=thread, imid=imid)
    return in_a, imid


def _rule(admin, *, org: str, account: str, name: str,
          created_at: datetime | None = None) -> str:
    if created_at is not None:
        return _insert(admin, (
            "INSERT INTO email_rules (account_id, name, instructions, enabled, "
            "created_at, organization_id) VALUES (CAST(:a AS uuid), :n, :i, "
            "true, :at, CAST(:o AS uuid)) RETURNING id"),
            {"a": account, "n": name, "i": f"when {name}", "at": created_at,
             "o": org})
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

    async def test_the_decide_facts_and_the_cc_note_read_the_set(
        self, family, monkeypatch,
    ):
        """Review round 1: the thread-status facts for ``decide`` and the
        "only Cc'd" note read each mailbox of the member. ``decide`` is dark,
        so the test turns its facts on with a ``shadow`` mode."""
        f = family
        monkeypatch.setattr(replyzero_mod.decide_features, "mode_for",
                            lambda feature: "shadow")
        t = f"tf-{f.tag}"
        _mail(f.admin, org=f.org, account=f.a, frm="ravi@contoso.test",
              to=(f.b_addr,), cc=(f.a_addr,), thread=t, subject="to b, cc a")
        _mail(f.admin, org=f.org, account=f.a, frm="sam@contoso.test",
              to=("kim@contoso.test",), cc=(f.a_addr,), thread=t,
              subject="cc a only", minutes=5)
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(RAVI,),
              thread=t, subject="from b", minutes=10)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            ctx = await replyzero_mod.build_thread_context(db, f.a, t, f.a_addr)
        to_b, cc_only, from_b = ctx.messages
        assert to_b["owner_cc_only"] is False, "in To under B is not Cc-only"
        assert cc_only["owner_cc_only"] is True, "the control: Cc under A only"
        assert from_b["side"] == "owner"
        first, second, _ = ctx.thread_text.split("\n\n---\n\n")
        assert "only Cc'd" not in first
        assert "only Cc'd" in second

    async def test_the_digest_categories_leave_another_mailbox_out(self, family):
        """``_PROJ_SCOPE`` (review round 1): the category breakdown of the
        digest does not count mail from another mailbox of the member."""
        f = family
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr,
              categories=("Newsletter",))
        for i in range(2):
            _mail(f.admin, org=f.org, account=f.a, frm="news@vendor.test",
                  categories=("Newsletter",), minutes=i)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            cats = await digest_mod._digest_categories(
                db, {"aid": f.a, "days": 7, "uid": f.member})
        assert {c["category"]: c["count"] for c in cats} == {"Newsletter": 2}

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

    @pytest.mark.parametrize(("value", "pinned"), [
        ("news@vendor.test", True),           # a stranger: the control
        ("SELF-B", False),                    # mailbox B, exactly
        ("gmail.com", True),                  # B's domain is not refused
        ("SELF-A", False),                    # this mailbox, exactly
        ("fracktal-t8e.test", False),         # this mailbox's domain
        ("SELF-A-LOCAL", False),              # a part of this address
    ])
    async def test_the_pin_guard_refuses_own_mailboxes_only(
        self, family, value, pinned,
    ):
        """Review round 1: THIS mailbox keeps the old substring rule, and the
        other mailboxes of the member are refused on an exact address only."""
        f = family
        value = {"SELF-B": f.b_addr, "SELF-A": f.a_addr,
                 "SELF-A-LOCAL": f.a_addr.split("@")[0]}.get(value, value)
        rid = _rule(f.admin, org=f.org, account=f.a, name="Newsletter")
        async with _as_app(f.p, f.org), _tenant_session() as db:
            got = await rules_mod._upsert_rule_pattern(
                db, f.a, rid, value, False, "AI", "seed", None, None)
        assert got is pinned

    @pytest.mark.parametrize(("case", "checked"), [
        ("proven", False),       # B's Sent copy names A: the send is proved
        ("proven_bcc", False),   # B's Sent copy names A in Bcc only
        ("forged", True),        # From says B, and there is no Sent copy
        ("own_copy", True),      # the only copy is in A itself: not another box
        ("replayed", True),      # round 2: B's real Sent copy went to Ravi
        ("inbox_copy", True),    # round 2: B holds the forgery in its INBOX
        ("empty_id", True),      # round 2: an empty Message-ID proves nothing
        ("stranger", True),      # the positive control
    ])
    async def test_the_cold_check_skips_only_a_proven_own_send(
        self, family, monkeypatch, case, checked,
    ):
        """Review rounds 1 and 2: a forged From of mailbox B gets the cold
        check. Only a Sent copy in ANOTHER mailbox of the member proves the
        send, and that copy names this mailbox in To, Cc or Bcc.

        ``replayed``: B really sent a mail to Ravi. Ravi then forged
        ``From: B`` to A with the same Message-ID. ``inbox_copy``: one forged
        mail went to both A and B, so B holds it in its inbox. Only the
        ``sent`` folder test refuses that one."""
        f = family
        imid = _message_id()
        b_copy = {
            "proven": ("sent", (f.a_addr,), None, imid),
            "proven_bcc": ("sent", (RAVI,), (f.a_addr,), imid),
            "replayed": ("sent", (RAVI,), None, imid),
            "inbox_copy": ("inbox", (f.a_addr, f.b_addr), None, imid),
            "empty_id": ("sent", (f.a_addr,), None, ""),
        }.get(case)
        if b_copy is not None:
            folder, to, bcc, imid = b_copy
            _mail(f.admin, org=f.org, account=f.b, frm=f.b_addr, to=to, bcc=bcc,
                  folder=folder, imid=imid)
            mail = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr,
                         to=(f.a_addr,), imid=imid)
        elif case == "own_copy":
            _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(f.a_addr,),
                  folder="sent", imid=imid)
            mail = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr,
                         to=(f.a_addr,), imid=imid)
        else:
            frm = f.b_addr if case == "forged" else "pitch@vendor.test"
            mail = _mail(f.admin, org=f.org, account=f.a, frm=frm,
                         to=(f.a_addr,), imid=imid)
        llm = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(senders_mod, "_llm_is_cold", llm)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            payload = await engine_mod._email_payload_from_id(
                db, mail, f.member, account_id=f.a)
            await senders_mod._maybe_block_cold(
                db, None, f.a, mail, "pm", payload, "LABEL")
        assert (llm.await_count == 1) is checked

    async def test_a_sent_copy_of_another_member_proves_nothing(
        self, family, monkeypatch,
    ):
        f = family
        imid = _message_id()
        _mail(f.admin, org=f.org, account=f.d, frm=f.b_addr, to=(f.a_addr,),
              folder="sent", imid=imid)
        mail = _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr,
                     to=(f.a_addr,), imid=imid)
        llm = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(senders_mod, "_llm_is_cold", llm)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            payload = await engine_mod._email_payload_from_id(
                db, mail, f.member, account_id=f.a)
            await senders_mod._maybe_block_cold(
                db, None, f.a, mail, "pm", payload, "LABEL")
        assert llm.await_count == 1

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


class _Store:
    """A key store double: the seeded credentials are the text ``x``."""

    def decrypt(self, _blob: str) -> str:
        return "{}"

    def encrypt(self, blob: str) -> str:
        return blob


class _Provider:
    """A provider double that authenticates and records nothing it needs."""

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    async def set_labels(self, *_a, **_kw) -> None:
        return None

    async def move_to_folder(self, *_a, **_kw) -> None:
        return None

    async def create_draft(self, **_kw) -> str:
        return f"pd-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def live(family, monkeypatch):
    """Mailbox A holds a mail that the member sent from B (with B's Sent copy
    as the proof) and a mail of a stranger. An enabled rule of A predates both,
    so the automatic paths and the backfill see them. The cold blocker is on.

    The provider and the key store are doubles, and each path's classifier
    call is replaced by a recorder of the payload it gets."""
    f = family
    f.from_b, _ = _sent_by_b(f, to=(f.a_addr,), subject="from b",
                             thread=f"lb-{f.tag}")
    f.from_x = _mail(f.admin, org=f.org, account=f.a, frm="pitch@vendor.test",
                     to=(f.a_addr,), subject="from x", thread=f"lx-{f.tag}",
                     imid=_message_id(), minutes=1)
    _rule(f.admin, org=f.org, account=f.a, name="Newsletter",
          created_at=_T0 - timedelta(days=1))
    _exec(f.admin, (
        "INSERT INTO email_assistant_settings (account_id, cold_email_blocker, "
        "organization_id) VALUES (CAST(:a AS uuid), 'LABEL', CAST(:o AS uuid))"),
        {"a": f.a, "o": f.org})
    import acb_llm.key_store as key_store
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(runner_mod, "_instantiate_provider",
                        lambda *_a, **_kw: _Provider())
    monkeypatch.setattr(replyzero_mod, "_instantiate_provider",
                        lambda *_a, **_kw: _Provider())
    f.payloads = {}

    async def record_match(_db, _aid, email, message_id=None, **_kw):
        f.payloads[str(message_id)] = email

    async def record_classify(_db, _aid, row, email, **_kw):
        f.payloads[str(row.id)] = email
        return []

    monkeypatch.setattr(runner_mod, "_match_email_to_rule",
                        AsyncMock(side_effect=record_match))
    monkeypatch.setattr(runner_mod, "classify_matches",
                        AsyncMock(side_effect=record_classify))
    monkeypatch.setattr(engine_mod, "classify_matches",
                        AsyncMock(side_effect=record_classify))
    f.llm_cold = AsyncMock(return_value=(False, ""))
    monkeypatch.setattr(senders_mod, "_llm_is_cold", f.llm_cold)
    return f


def _scopes(f) -> tuple[str, str]:
    return (f.payloads[f.from_b]["sender_scope"],
            f.payloads[f.from_x]["sender_scope"])


@_DB_GATE
class TestTheLiveRulePathsReadTheSet:
    """Review round 1: each path that builds a rule payload in production
    gives it each mailbox of the member. A mutant that drops
    ``self_addresses`` at one of these sites makes its case fail."""

    async def test_the_automatic_run_and_its_cold_check(self, live):
        f = live
        async with _as_app(f.p, f.org):
            await runner_mod._run_rules_job(f.a, 10, False, f.member)
        assert _scopes(f) == ("self", "external")
        assert f.llm_cold.await_count == 1, "only the stranger reaches the check"
        sender = f.llm_cold.await_args.args[0]["from"]
        assert sender == "pitch@vendor.test"

    async def test_the_run_of_one_message(self, live):
        f = live
        async with _as_app(f.p, f.org):
            for mid in (f.from_b, f.from_x):
                await runner_mod.run_rules_on_message(
                    runner_mod.RuleRunMessageRequest(
                        account_id=f.a, message_id=mid, is_test=True),
                    user=f.user)
        assert _scopes(f) == ("self", "external")

    async def test_process_past_emails(self, live, monkeypatch):
        f = live
        monkeypatch.setattr(runner_mod, "_download_past_range",
                            AsyncMock(return_value=None))
        async with _as_app(f.p, f.org):
            await runner_mod._process_past_emails_job(
                f.a, None, None, 50, True, f.member)
        assert _scopes(f) == ("self", "external")

    async def test_the_preview_on_recent_mail(self, live):
        f = live
        async with _as_app(f.p, f.org):
            await runner_mod.test_rules_recent(
                runner_mod.RuleTestRecentRequest(account_id=f.a), user=f.user)
        assert _scopes(f) == ("self", "external")

    async def test_the_reply_zero_backfill(self, live):
        f = live
        async with _as_app(f.p, f.org):
            await replyzero_mod._maybe_classify_threads(f.a)
        assert _scopes(f) == ("self", "external")


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
                         AsyncMock(return_value="none")), \
            patch.object(actions_mod, "draft_skip_in_pair",
                         AsyncMock(return_value=None)):
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
    f.thread_a = f"ta-{f.tag}"
    f.mail_a = _mail(f.admin, org=f.org, account=f.a, frm=RAVI, to=(f.a_addr,),
                     thread=f.thread_a, subject="Quote")
    f.rule_a = _rule(f.admin, org=f.org, account=f.a, name="Newsletter")
    f.rule_b = _rule(f.admin, org=f.org, account=f.b, name="Newsletter")
    # Review round 1: a LABEL rule and a conversation rule of mailbox A.
    f.label_rule_a = _rule(f.admin, org=f.org, account=f.a, name="Receipt")
    _exec(f.admin, (
        "INSERT INTO email_actions (rule_id, type, label, organization_id) "
        "VALUES (CAST(:r AS uuid), 'LABEL', 'Receipt', CAST(:o AS uuid))"),
        {"r": f.label_rule_a, "o": f.org})
    f.fyi_rule_a = _rule(f.admin, org=f.org, account=f.a, name="FYI")
    return f


def _categories_of(f, message_id: str) -> list[str]:
    with f.admin.connect() as c:
        return list(c.execute(text(
            "SELECT categories FROM email_messages WHERE id = CAST(:m AS uuid)"),
            {"m": message_id}).scalar() or [])


def _status_rows(f, account: str, thread: str) -> int:
    return _count(f.admin, (
        "SELECT count(*) FROM email_thread_status "
        "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
        {"a": account, "t": thread})


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

    async def test_feedback_refuses_a_mail_of_another_mailbox(
        self, pair, monkeypatch,
    ):
        """Review round 1: a LABEL rule of A with a mail of B put A's label on
        B's mail, locally and at the provider, and answered 200.

        Round 2: the 404 rolls back the local rows, so only the provider shows
        a write that ran before the check. A recording provider counts each
        label write: 0 for the mail of B, and 1 for the control mail of A."""
        f = pair
        writes: list[tuple[str, tuple]] = []

        class _Recorder(_Provider):
            def __init__(self, message_id: str) -> None:
                self.message_id = message_id

            async def set_labels(self, pmid, add=None, remove=None) -> None:
                writes.append((self.message_id, tuple(add or ())))

        async def _for_message(_db, message_id, _owner):
            return _Recorder(str(message_id)), "pm", f.a, None

        monkeypatch.setattr(actions_mod, "_provider_for_message", _for_message)
        async with _as_app(f.p, f.org):
            with pytest.raises(HTTPException) as exc:
                await rules_mod.rule_feedback(rules_mod.RuleFeedbackRequest(
                    account_id=f.a, sender=RAVI, expected=f.label_rule_a,
                    message_id=f.mail_b), user=f.user)
            assert writes == [], "a provider label write reached the mail of B"
            ok = await rules_mod.rule_feedback(rules_mod.RuleFeedbackRequest(
                account_id=f.a, sender=RAVI, expected=f.label_rule_a,
                message_id=f.mail_a), user=f.user)
        assert exc.value.status_code == 404
        assert ok["label_correction"]["added"] == ["Receipt"], "the control"
        assert writes == [(f.mail_a, ("Receipt",))], "one write, on the mail of A"
        assert _categories_of(f, f.mail_b) == [], "nothing was written on B"

    async def test_feedback_refuses_a_thread_of_another_mailbox(
        self, pair, monkeypatch,
    ):
        """Review round 1: an FYI rule of A with a thread of B wrote a status
        row (A, thread of B)."""
        f = pair
        import acb_llm.key_store as key_store
        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(replyzero_mod, "_instantiate_provider",
                            lambda *_a, **_kw: _Provider())
        async with _as_app(f.p, f.org):
            with pytest.raises(HTTPException) as exc:
                await rules_mod.rule_feedback(rules_mod.RuleFeedbackRequest(
                    account_id=f.a, sender=RAVI, expected=f.fyi_rule_a,
                    thread_id=f.thread_b), user=f.user)
            ok = await rules_mod.rule_feedback(rules_mod.RuleFeedbackRequest(
                account_id=f.a, sender=RAVI, expected=f.fyi_rule_a,
                thread_id=f.thread_a), user=f.user)
        assert exc.value.status_code == 404
        assert ok["status_correction"]["ok"] is True, "the control writes A's row"
        assert _status_rows(f, f.a, f.thread_b) == 0
        assert _status_rows(f, f.a, f.thread_a) == 1

    async def test_guidance_refuses_a_rule_of_another_mailbox(self, pair):
        f = pair
        async with _as_app(f.p, f.org):
            with pytest.raises(HTTPException) as exc:
                await rules_mod.add_rule_guidance(rules_mod.RuleGuidanceRequest(
                    account_id=f.a, guidance="teach B", rule_id=f.rule_b),
                    user=f.user)
            ok = await rules_mod.add_rule_guidance(rules_mod.RuleGuidanceRequest(
                account_id=f.a, guidance="teach A", rule_id=f.rule_a),
                user=f.user)
        assert exc.value.status_code == 404
        assert ok == {"ok": True}
        assert _count(f.admin, (
            "SELECT count(*) FROM email_rule_guidance "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": f.a}) == 1


# ══════════════════════════════════════════════════════════════════════════
# 4b. email-self-each-mailbox, D-EM-27 reading (review round 1): mail between
#     the member's own mailboxes is never NEEDS_REPLY and never AWAITING
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def threads(family, monkeypatch):
    """``own``: B wrote to A, and A wrote back to B. ``mixed``: the same, with
    Ravi on Cc, so it has a participant outside the member's mailboxes.

    The status model must not run for ``own``. It answers REPLY for ``mixed``."""
    f = family
    f.own, f.mixed = f"own-{f.tag}", f"mix-{f.tag}"
    for t, cc in ((f.own, ()), (f.mixed, (RAVI,))):
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(f.a_addr,),
              cc=cc, thread=t, subject="plan")
        _mail(f.admin, org=f.org, account=f.a, frm=f.a_addr, to=(f.b_addr,),
              cc=cc, thread=t, folder="sent", subject="Re: plan", minutes=5)
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(f.a_addr,),
              cc=cc, thread=t, subject="Re: plan", minutes=10)
    f.status_model = AsyncMock(return_value=("REPLY", True))
    monkeypatch.setattr(replyzero_mod, "_llm_determine_thread_status",
                        f.status_model)
    return f


def _status(f, thread: str) -> tuple[str, str] | None:
    with f.admin.connect() as c:
        row = c.execute(text(
            "SELECT status, reason FROM email_thread_status "
            "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
            {"a": f.a, "t": thread}).first()
    return (row[0], row[1]) if row else None


@_DB_GATE
class TestMailBetweenOwnMailboxesIsNeverOpen:

    async def test_the_participant_rule(self, threads):
        f = threads
        async with _as_app(f.p, f.org), _tenant_session() as db:
            own = await replyzero_mod._thread_is_self_only(db, f.a, f.own)
            mixed = await replyzero_mod._thread_is_self_only(db, f.a, f.mixed)
            none = await replyzero_mod._thread_is_self_only(db, f.a, "no-such")
        assert (own, mixed, none) == (True, False, False)

    @pytest.mark.parametrize(("reply", "self_only"), [
        # (to, cc, bcc) of A's Sent reply. None is a NULL column.
        (("B", None, None), True),       # NULL lists are not an outsider
        (("RAVI", None, None), False),   # an outsider beside a NULL Cc
        (("B", (), ("RAVI",)), False),   # an outsider in Bcc only
        ((None, (), ("RAVI",)), False),  # empty To and Cc, an outsider in Bcc
        ((None, None, None), False),     # no recipient at all: unknown
    ], ids=["null-lists", "outsider-null-cc", "bcc-outsider", "bcc-only",
            "no-recipients"])
    async def test_the_participant_rule_reads_bcc_and_unknown_lists(
        self, family, reply, self_only,
    ):
        """Review round 2 (F4): Bcc is a recipient list, a NULL list is an
        empty one, and a mail with no recipient at all keeps the status."""
        f = family
        t = f"pr-{uuid.uuid4().hex[:8]}"
        named = {"B": (f.b_addr,), "RAVI": (RAVI,), None: ()}
        to, cc, bcc = reply
        _mail(f.admin, org=f.org, account=f.a, frm=f.b_addr, to=(f.a_addr,),
              cc=None, thread=t, subject="plan")
        _mail(f.admin, org=f.org, account=f.a, frm=f.a_addr, to=named[to],
              cc=None if cc is None else tuple(named[c][0] for c in cc),
              bcc=None if bcc is None else tuple(named[c][0] for c in bcc),
              thread=t, folder="sent", subject="Re: plan", minutes=5)
        async with _as_app(f.p, f.org), _tenant_session() as db:
            got = await replyzero_mod._thread_is_self_only(db, f.a, t)
        assert got is self_only

    async def test_a_done_row_stays_done(self, threads):
        """Round 2 (M5): an automated self-only write never reopens or
        replaces a thread that the member marked Done."""
        f = threads
        _exec(f.admin, (
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "reason, organization_id) VALUES (CAST(:a AS uuid), :t, 'DONE', "
            "'Marked done', CAST(:o AS uuid))"), {"a": f.a, "t": f.own, "o": f.org})
        async with _as_app(f.p, f.org), _tenant_session() as db:
            await replyzero_mod.recompute_thread_status(
                db, f.a, f.own, trigger="backfill", acc_email=f.a_addr)
        assert _status(f, f.own)[0] == "DONE"

    async def test_the_status_authority_files_it_as_fyi(self, threads):
        f = threads
        async with _as_app(f.p, f.org), _tenant_session() as db:
            own = await replyzero_mod.recompute_thread_status(
                db, f.a, f.own, trigger="backfill", acc_email=f.a_addr)
            mixed = await replyzero_mod.recompute_thread_status(
                db, f.a, f.mixed, trigger="backfill", acc_email=f.a_addr)
        assert own == ("FYI", "FYI")
        assert _status(f, f.own) == ("FYI", replyzero_mod.SELF_ONLY_REASON)
        assert mixed[0] in ("NEEDS_REPLY", "AWAITING"), "the control stays open"
        assert f.status_model.await_count == 1, "no model call for own mail"

    async def test_the_resolver_asks_no_model_for_it(self, threads):
        f = threads
        async with _as_app(f.p, f.org), _tenant_session() as db:
            own = await replyzero_mod._determine_status_of(
                db, f.a, SimpleNamespace(thread_id=f.own, id=None))
            mixed = await replyzero_mod._determine_status_of(
                db, f.a, SimpleNamespace(thread_id=f.mixed, id=None))
        assert own == ("FYI", True)
        assert mixed == ("REPLY", True)
        assert f.status_model.await_count == 1

    async def test_the_projection_never_opens_it(self, threads):
        """The backstop: a Reply rule that the per-message match picked
        becomes FYI, and the FYI label replaces the Reply label."""
        f = threads
        reply = [{"rule": {"name": "Reply"}, "reason": "asks"}]
        async with _as_app(f.p, f.org), _tenant_session() as db:
            own = await replyzero_mod.project_reply_status_from_matches(
                db, f.a, SimpleNamespace(thread_id=f.own, id=None,
                                         received_at=None), reply)
            mixed = await replyzero_mod.project_reply_status_from_matches(
                db, f.a, SimpleNamespace(thread_id=f.mixed, id=None,
                                         received_at=None), reply)
        assert (own, mixed) == ("FYI", "Needs Reply")
        assert _status(f, f.own) == ("FYI", replyzero_mod.SELF_ONLY_REASON)
        assert _status(f, f.mixed)[0] == "NEEDS_REPLY"


# ══════════════════════════════════════════════════════════════════════════
# 2b. email-drafter-sending-mailbox (review round 1): the nudge and the
#     saved draft name the mailbox too
# ══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheNudgeSpeaksAsTheMailbox:

    async def test_the_follow_up_nudge_names_the_mailbox(self, family, monkeypatch):
        f = family
        sent = _mail(f.admin, org=f.org, account=f.a, frm=f.a_addr, to=(RAVI,),
                     folder="sent", thread=f"fu-{f.tag}", subject="Quote",
                     body="Here is the quote.")
        _exec(f.admin, (
            "INSERT INTO email_assistant_settings (account_id, "
            "follow_up_awaiting_days, follow_up_auto_draft, organization_id) "
            "VALUES (CAST(:a AS uuid), 1, true, CAST(:o AS uuid))"),
            {"a": f.a, "o": f.org})
        _exec(f.admin, (
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "last_message_id, last_message_at, reason, organization_id) VALUES "
            "(CAST(:a AS uuid), :t, 'AWAITING', CAST(:m AS uuid), "
            "now() - interval '10 days', 'seed', CAST(:o AS uuid))"),
            {"a": f.a, "t": f"fu-{f.tag}", "m": sent, "o": f.org})
        import acb_llm.key_store as key_store
        from gateway.routes.email.automation import followups as followups_mod
        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(followups_mod, "_instantiate_provider",
                            lambda *_a, **_kw: _Provider())
        drafter = AsyncMock(return_value="Hi Ravi, any news?")
        monkeypatch.setattr(followups_mod, "_agent_draft_reply", drafter)
        async with _as_app(f.p, f.org):
            out = await followups_mod._maybe_send_follow_up_reminders(f.a)
        assert out["drafted"] == 1
        nudge = drafter.await_args.args[0]
        assert (nudge["self"], nudge["self_label"]) == (f.a_addr, "Fracktal-t8e")


async def test_a_saved_draft_copy_stores_the_mailbox_as_from(monkeypatch) -> None:
    """``/drafts/save`` stored a blank From in the local copy (review round 1)."""
    upsert = AsyncMock(return_value="local-1")
    provider = SimpleNamespace(create_draft=AsyncMock(return_value="pd-1"))

    @asynccontextmanager
    async def _session(db, user_email, **kw):
        yield SimpleNamespace(authed=True, provider=provider, account_id="acc-b",
                              provider_message_id="pm-1")

    db = AsyncMock()
    db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=SimpleNamespace(
        subject="Quote", thread_id="t1", from_address={"email": RAVI})))
    for name, value in (
        ("_tenant_session", bind_db(db)),
        ("_assert_account_owner", AsyncMock()),
        ("_account_signature", AsyncMock(return_value="")),
        ("resolve_self", AsyncMock(return_value=SelfIdentity(address=BOX))),
        ("provider_session", _session),
        ("_upsert_local_draft", upsert),
        ("_fetch_message_dict", AsyncMock(return_value={})),
    ):
        monkeypatch.setattr(drafting_mod, name, value, raising=False)
    out = await drafting_mod.save_draft(
        drafting_mod.SaveDraftRequest(account_id="acc-b", message_id="m1",
                                      body="Thanks"),
        user=SimpleNamespace(email=SIGN_IN))
    assert out["created"] is True
    assert upsert.await_args.kwargs["owner_email"] == BOX


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
