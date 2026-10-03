"""WS-17 EM-T8a — send from the right mailbox (multi-inbox).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.1. Decisions
D-EM-19, D-EM-20, D-EM-23 and D-EM-26 (§11.2). Defects MB-1 and MB-4 to MB-6
(§11.1).

R7 fences named here:

* ``email-second-connect-picker`` (MB-1): when the member already has a
  mailbox of the provider in the organization and the client sent no hint,
  the authorize leg sends ``prompt=select_account`` and no ``login_hint``. A
  first connect keeps the sign-in hint, and a client hint (a reconnect) wins
  with no question to the database.
* ``email-member-has-mailbox`` (R8): the question reads only the rows of the
  member, of the provider and of the organization, under FORCE RLS as the
  non-privileged role.
* ``email-send-reply-in-mailbox`` (MB-6, R8): ``/send`` answers 404 when the
  mail a reply answers is not in a sending mailbox of the member, and the
  route opens no provider session at all. A local id becomes the provider id
  (MB-5).
* ``email-authorize-check-fails-open-to-picker``: when the mailbox question
  fails, the authorize leg shows the account picker rather than answer 500.
* ``email-chat-reply-mailbox`` (MB-4): the chat reply sends from the mailbox
  of the original mail, and each send card names the From mailbox. A send
  from a mailbox the member does not have stops before its card.

The UI half is fenced in
``workbench/control_plane/src/app/email/lib/mailbox.test.ts``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_multi_inbox.py -v -rs
"""
from __future__ import annotations

import importlib.util
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.transport import oauth, send
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted`` and ``app_engine`` are used by name for fixture injection, so
# the import is load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

SECRET = "em-t8a-test-secret"
ORG = "11111111-2222-3333-4444-555555555555"
MEMBER = "dana@example.com"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "gateway_session_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "msft_oauth_client_id", "test-msft-id", raising=False)
    monkeypatch.setattr(s, "gmail_oauth_client_id", "test-gmail-id", raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")


def _member(email: str = MEMBER, org: str = ORG) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _query(resp) -> dict[str, list[str]]:
    assert resp.status_code in (302, 307)
    return parse_qs(urlparse(resp.headers["location"]).query)


# ── 1. The authorize leg asks Microsoft which account (MB-1) ───────────────


@pytest.fixture()
def has_mailbox(monkeypatch):
    """Answer the mailbox question from a flag, and record each question."""
    state = SimpleNamespace(answer=False, asked=[])

    async def _ask(org, member, provider):
        state.asked.append((org, member, provider))
        return state.answer

    monkeypatch.setattr(oauth, "_member_has_mailbox", _ask)
    return state


async def test_a_first_connect_keeps_the_sign_in_hint(has_mailbox) -> None:
    q = _query(await oauth.oauth_authorize("microsoft", user=_member(),
                                           redirect_after=""))
    assert q["login_hint"] == [MEMBER]
    assert "prompt" not in q


async def test_one_more_mailbox_asks_microsoft_which_account(has_mailbox) -> None:
    has_mailbox.answer = True
    q = _query(await oauth.oauth_authorize("microsoft", user=_member(),
                                           redirect_after=""))
    assert q["prompt"] == ["select_account"]
    assert "login_hint" not in q, (
        "the sign-in hint signs the FIRST mailbox in again, so the member "
        "could never add a second one"
    )


async def test_the_question_names_the_member_in_lower_case(has_mailbox) -> None:
    await oauth.oauth_authorize("microsoft", user=_member(email=" Dana@Example.COM "),
                                redirect_after="")
    assert has_mailbox.asked == [(ORG, "dana@example.com", "microsoft")]


async def test_a_reconnect_hint_wins_and_asks_nothing(has_mailbox) -> None:
    has_mailbox.answer = True
    q = _query(await oauth.oauth_authorize(
        "microsoft", user=_member(), redirect_after="",
        login_hint="box@contoso.test"))
    assert q["login_hint"] == ["box@contoso.test"]
    assert "prompt" not in q
    assert has_mailbox.asked == [], "a client hint needs no database read"


async def test_a_malformed_client_hint_counts_as_no_hint(has_mailbox) -> None:
    has_mailbox.answer = True
    q = _query(await oauth.oauth_authorize(
        "microsoft", user=_member(), redirect_after="", login_hint="not an address"))
    assert q["prompt"] == ["select_account"]
    assert "login_hint" not in q


async def test_a_failed_mailbox_read_shows_the_picker(monkeypatch) -> None:
    async def _boom(org, member, provider):
        raise RuntimeError("database down")

    monkeypatch.setattr(oauth, "_member_has_mailbox", _boom)
    q = _query(await oauth.oauth_authorize("microsoft", user=_member(),
                                           redirect_after=""))
    assert q["prompt"] == ["select_account"]
    assert "login_hint" not in q


async def test_gmail_one_more_mailbox_adds_the_account_chooser(has_mailbox) -> None:
    q = _query(await oauth.oauth_authorize("gmail", user=_member(),
                                           redirect_after=""))
    assert q["prompt"] == ["consent"]
    has_mailbox.answer = True
    q = _query(await oauth.oauth_authorize("gmail", user=_member(),
                                           redirect_after=""))
    assert q["prompt"] == ["consent select_account"]
    assert "login_hint" not in q


# ── 2. R8: the mailbox question on a real database ─────────────────────────


def _seed_account(admin_engine, *, org: str, owner: str,
                  provider: str = "microsoft") -> tuple[str, str]:
    mailbox = f"box-{uuid.uuid4().hex[:8]}@em-t8a.test"
    with admin_engine.begin() as c:
        account_id = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, initial_sync_done, organization_id) "
            "VALUES (:u, :p, :m, 'x', true, CAST(:o AS uuid)) RETURNING id"),
            {"u": owner, "p": provider, "m": mailbox, "o": org}).scalar_one())
    return account_id, mailbox


def _seed_message(admin_engine, *, org: str, account_id: str,
                  provider_id: str, thread_id: str) -> str:
    with admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, "
            "received_at, organization_id) VALUES (CAST(:a AS uuid), :pid, "
            ":tid, 'inbox', '{\"email\": \"ravi@contoso.test\"}'::jsonb, "
            "'[]'::jsonb, 'Hello', now(), CAST(:o AS uuid)) RETURNING id"),
            {"a": account_id, "pid": provider_id, "tid": thread_id,
             "o": org}).scalar_one())


def _delete_accounts(admin_engine, *account_ids: str) -> None:
    with admin_engine.begin() as c:
        for account_id in account_ids:
            c.execute(text("DELETE FROM email_messages WHERE account_id = "
                           "CAST(:a AS uuid)"), {"a": account_id})
            c.execute(text("DELETE FROM email_accounts WHERE id = "
                           "CAST(:a AS uuid)"), {"a": account_id})


def _assert_non_priv(app_engine) -> None:  # noqa: F811
    with app_engine.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role — RLS is bypassed"
    )


@_DB_GATE
class TestTheMailboxQuestionOnARealDatabase:

    async def test_it_reads_the_member_the_provider_and_the_org_only(
        self, promoted, app_engine,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        owner = f"Owner-{uuid.uuid4().hex[:8]}@EM-T8A.test"
        account_id, _ = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        member = owner.lower()
        try:
            async with tenant_engine_scope(app_dsn):
                assert await oauth._member_has_mailbox(p.org_b, member, "microsoft")
                assert not await oauth._member_has_mailbox(p.org_a, member, "microsoft")
                assert not await oauth._member_has_mailbox(p.org_b, member, "gmail")
                assert not await oauth._member_has_mailbox(
                    p.org_b, "someone-else@em-t8a.test", "microsoft")
        finally:
            _delete_accounts(p.admin_engine, account_id)


# ── 3. R8: /send replies only to a mail of the sending mailbox (MB-6) ───────


class _Provider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kw):
        self.calls.append(kw)
        return "sent-1"


@pytest.fixture()
def two_mailboxes(promoted, app_engine, monkeypatch):  # noqa: F811
    """One member of org B with mailboxes A and B. Mailbox A holds one mail."""
    _assert_non_priv(app_engine)
    p = promoted
    owner = f"owner-{uuid.uuid4().hex[:8]}@em-t8a.test"
    box_a, _ = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    box_b, _ = _seed_account(p.admin_engine, org=p.org_b, owner=owner)
    mail_a = _seed_message(p.admin_engine, org=p.org_b, account_id=box_a,
                           provider_id="pm-a", thread_id="thread-a")
    provider = _Provider()
    entered: list[str] = []

    @asynccontextmanager
    async def _session(db, user_email, account_id):
        entered.append(account_id)
        yield SimpleNamespace(provider=provider)

    monkeypatch.setattr(send, "provider_session", _session)
    me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
    app_dsn = p.app_url.render_as_string(hide_password=False)

    async def _send(account_id: str, reply_to: str | None, user=me):
        req = send.SendEmailRequest(
            account_id=account_id, to=["ravi@contoso.test"], subject="Re: Hello",
            body_text="Thanks", reply_to_message_id=reply_to)
        token = bind_tenant(p.org_b)
        try:
            async with tenant_engine_scope(app_dsn):
                return await send.send_email(req, background=BackgroundTasks(),
                                             user=user)
        finally:
            release_tenant(token)

    try:
        yield SimpleNamespace(send=_send, provider=provider, entered=entered,
                              box_a=box_a, box_b=box_b, mail_a=mail_a, org=p.org_b)
    finally:
        _delete_accounts(p.admin_engine, box_a, box_b)


@_DB_GATE
class TestTheSendRouteKeepsTheReplyInItsMailbox:

    @pytest.mark.parametrize("which", ["provider id", "local id"])
    async def test_a_reply_from_the_other_mailbox_answers_404(
        self, two_mailboxes, which,
    ):
        t = two_mailboxes
        reply_to = "pm-a" if which == "provider id" else t.mail_a
        with pytest.raises(HTTPException) as exc:
            await t.send(t.box_b, reply_to)
        assert exc.value.status_code == 404
        assert exc.value.detail == send.REPLY_NOT_IN_MAILBOX
        assert t.provider.calls == [], "the provider was asked to reply anyway"
        assert t.entered == [], "the route opened a provider session first"

    async def test_an_unknown_reply_id_answers_404(self, two_mailboxes):
        t = two_mailboxes
        with pytest.raises(HTTPException) as exc:
            await t.send(t.box_a, "pm-nowhere")
        assert exc.value.status_code == 404
        assert t.provider.calls == [] and t.entered == []

    async def test_a_reply_through_the_mailbox_of_another_member_answers_404(
        self, two_mailboxes,
    ):
        t = two_mailboxes
        mallory = UserContext(email="mallory@em-t8a.test", role=UserRole.EMPLOYEE,
                              organization_id=t.org)
        with pytest.raises(HTTPException) as exc:
            await t.send(t.box_a, "pm-a", user=mallory)
        assert exc.value.status_code == 404
        assert t.entered == [], "a reply checked the mailbox of another member"

    async def test_a_malformed_account_id_answers_404(self, two_mailboxes):
        t = two_mailboxes
        with pytest.raises(HTTPException) as exc:
            await t.send("not-a-uuid", "pm-a")
        assert exc.value.status_code == 404
        assert t.entered == []

    @pytest.mark.parametrize("which", ["provider id", "local id"])
    async def test_a_reply_from_its_own_mailbox_threads(self, two_mailboxes, which):
        t = two_mailboxes
        reply_to = "pm-a" if which == "provider id" else t.mail_a
        out = await t.send(t.box_a, reply_to)
        assert out == {"id": "sent-1", "ok": True}
        [call] = t.provider.calls
        assert call["reply_to_message_id"] == "pm-a", (
            "a local id must reach the provider as the provider id (MB-5)"
        )
        assert call["thread_id"] == "thread-a"

    async def test_a_new_mail_needs_no_reply_target(self, two_mailboxes):
        t = two_mailboxes
        await t.send(t.box_b, None)
        [call] = t.provider.calls
        assert call["reply_to_message_id"] is None
        assert call["thread_id"] is None


# ── 4. The chat send tools name and keep the mailbox (MB-4) ────────────────

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load_agents():
    spec = importlib.util.spec_from_file_location("ea_multi_inbox", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agents()

_ACCOUNTS = [
    {"id": "box-a", "email_address": "dana@fracktal.in", "label": "Fracktal"},
    {"id": "box-b", "email_address": "dana@outlook.com", "label": "Personal"},
]


@pytest.fixture()
def chat(monkeypatch):
    rec = SimpleNamespace(posts=[], cards=[], answer=True)

    async def fake_get(path, params=None):
        if path == "/email/accounts":
            return _ACCOUNTS
        if path == "/email/messages/m1":
            return {"account_id": "box-a", "subject": "Hello",
                    "from_address": {"name": "Ravi", "email": "ravi@contoso.test"}}
        return {}

    async def fake_post(path, body):
        rec.posts.append((path, body))
        return {"id": "sent-1", "draft": "Hi Ravi", "created": False}

    async def confirm(**kw):
        rec.cards.append(kw)
        return rec.answer

    monkeypatch.setattr(agents, "_get", fake_get)
    monkeypatch.setattr(agents, "_post", fake_post)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", confirm)
    return rec


async def test_a_chat_reply_goes_out_from_the_mailbox_of_the_mail(chat) -> None:
    out = await agents.send_email("box-b", body="Thanks!", reply_to_email_id="m1")
    [(path, payload)] = chat.posts
    assert path == "/email/send"
    assert payload["account_id"] == "box-a"
    assert payload["reply_to_message_id"] == "m1"
    [card] = chat.cards
    assert card["detail"].startswith("From Fracktal · dana@fracktal.in · To ravi@contoso.test")
    assert "from Fracktal · dana@fracktal.in" in out
    assert "not from the mailbox you named" in out


async def test_a_chat_reply_from_the_right_mailbox_says_nothing_more(chat) -> None:
    out = await agents.send_email("box-a", body="Thanks!", reply_to_email_id="m1")
    assert chat.posts[0][1]["account_id"] == "box-a"
    assert "not from the mailbox you named" not in out


async def test_a_new_chat_mail_names_its_from_mailbox(chat) -> None:
    await agents.send_email("box-b", body="Hi", to=["kim@contoso.test"], subject="S")
    [card] = chat.cards
    assert card["detail"].startswith("From Personal · dana@outlook.com · To kim@contoso.test")
    assert chat.posts[0][1]["account_id"] == "box-b"


async def test_a_send_from_an_unknown_mailbox_stops_before_the_card(chat) -> None:
    out = await agents.send_email("box-z", body="Hi", to=["kim@contoso.test"],
                                  subject="S")
    assert "No connected mailbox has the id box-z" in out
    assert chat.cards == [] and chat.posts == []


async def test_a_declined_card_sends_nothing(chat) -> None:
    chat.answer = False
    out = await agents.send_email("box-b", body="Hi", to=["kim@contoso.test"],
                                  subject="S")
    assert "Send cancelled" in out
    assert chat.posts == []


async def test_the_draft_send_card_names_its_mailbox(chat) -> None:
    await agents.send_draft("box-b", "d1")
    [card] = chat.cards
    assert card["detail"].startswith("From Personal · dana@outlook.com")
    assert chat.posts == [("/email/drafts/send", {"account_id": "box-b", "draft_id": "d1"})]


async def test_a_chat_draft_is_made_in_the_mailbox_of_the_mail(chat) -> None:
    out = await agents.draft_reply("m1", "box-b")
    assert chat.posts == [("/email/draft-reply", {
        "account_id": "box-a", "message_id": "m1", "create_draft": False})]
    # The first line names the mailbox, and the chat card reads its id.
    assert out.split("\n", 1)[0] == (
        "Draft from Fracktal · dana@fracktal.in (mailbox box-a):")
    assert out.endswith("\n\nHi Ravi")
