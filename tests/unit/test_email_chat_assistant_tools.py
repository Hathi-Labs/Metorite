"""The chat runs the whole mailbox: the tools that were missing (owner, 2026-10-20).

"Via chat we should be able to accomplish all the use cases of operating our
email as if the chat is our assistant." The Email app could edit a draft,
snooze a conversation, find a person, forward some files and act on all mail
of a sender. The chat could not. Each tool here calls the route that the
Email app already uses, and each write asks the member's card first
(``test_email_agent_write_cards.py`` pins that for every write tool).

Each rule names the mutation that turns it red (R7):

0. **Each tool reaches a run**: ``own_tool_scope`` names it. Mutation: drop
   a name -> ``test_each_new_tool_is_in_scope``.
1. **edit_draft keeps what it was not told to change.** With no ``body`` the
   save asks the route to keep the body (an Outlook HTML draft keeps its
   HTML), and a Gmail save gets the whole text from the provider. Mutation:
   send the stored, cut text -> ``test_edit_draft_keeps_the_body_it_cannot_read``.
2. **snooze_email takes a time with an offset in the future only.**
   Mutation: accept a naive time -> ``test_a_snooze_time_needs_an_offset``.
3. **find_contact never guesses**: more than one match says to ask.
4. **forward_email(files=) sends only the named files**, and an unknown name
   sends nothing. Mutation: forward every file ->
   ``test_a_forward_sends_only_the_named_files``.
5. **bulk_manage_inbox counts first, needs a filter, and the route's count
   changes nothing** (R8, on a real database). Mutation: drop ``dry_run`` in
   the route -> ``TestTheBulkCountOnARealDatabase``.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from acb_auth.roles import UserContext, UserRole
from sqlalchemy import text

# ``promoted`` and ``app_engine`` are fixtures, used by name.
from tests.unit.test_email_keep_separate import (
    _account,
    _as_member,
    _assert_non_priv,
    _mail,
    _purge,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_AGENT = (Path(__file__).resolve().parents[2]
          / "apps" / "agents" / "agent-email-assistant" / "agents.py")


def _load():
    spec = importlib.util.spec_from_file_location("ea_assistant_tools", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "a0000000-0000-4000-8000-000000000001"
MSG = "b0000000-0000-4000-8000-000000000001"


class _Gw:
    def __init__(self) -> None:
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.answer: Any = True
        self.draft: dict[str, Any] = {
            "id": "d1", "account_id": BOX, "folder": "drafts", "subject": "Re: Quote",
            "to_addresses": [{"email": "ravi@contoso.test"}],
            "cc_addresses": [{"email": "kim@contoso.test"}], "bcc_addresses": [],
            "body_text": "Hi Ravi,\n\nThe quote is attached.",
        }
        self.full_body: dict[str, Any] = {"body_text": "Hi Ravi,\n\nThe quote is attached."}
        self.mail: dict[str, Any] = {
            "id": MSG, "account_id": BOX, "subject": "Spec", "attachments": [
                {"id": "f1", "filename": "spec.pdf", "size_bytes": 2048},
                {"id": "f2", "filename": "photo.jpg", "size_bytes": 4096}]}
        self.contacts: list[dict[str, Any]] = []
        self.matched = 7


@pytest.fixture()
def gw(monkeypatch) -> _Gw:
    g = _Gw()

    async def get(path, params=None):
        g.gets.append((path, dict(params or {})))
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": "dana@fracktal.test",
                     "display_label": "Fracktal"}]
        if path == "/email/messages/d1":
            return dict(g.draft)
        if path == "/email/messages/d1/full-body":
            return dict(g.full_body)
        if path == f"/email/messages/{MSG}":
            return dict(g.mail)
        if path == "/email/contacts/suggest":
            return list(g.contacts)
        return {}

    async def post(path, body):
        g.writes.append(("POST", path, body))
        if path == "/email/messages/bulk" and body.get("dry_run"):
            return {"matched": g.matched, "affected": 0}
        if path == "/email/messages/bulk":
            return {"affected": g.matched}
        return {"id": "fwd-1", "subject": "Fwd: Spec", "attachments": ["x"]}

    async def put(path, body):
        g.writes.append(("PUT", path, body))
        return {"id": "d1"}

    async def card(**kw):
        g.cards.append(kw)
        return g.answer

    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_put", put)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    return g


# ── 1. edit_draft ────────────────────────────────────────────────────────────


async def test_edit_draft_changes_the_text_and_keeps_the_rest(gw: _Gw) -> None:
    out = await agents.edit_draft(BOX, "d1", body="Hi Ravi,\n\nThe quote is attached. Price holds till Friday.")
    [card] = gw.cards
    assert "Price holds till Friday." in card["context"]
    assert "- To: ravi@contoso.test" in card["context"]
    [(verb, path, body)] = gw.writes
    assert (verb, path) == ("PUT", "/email/drafts")
    assert body["to"] == ["ravi@contoso.test"] and body["cc"] == ["kim@contoso.test"]
    assert body["subject"] == "Re: Quote" and body["keep_html"] is False
    assert "Nothing was sent" in out


def test_each_new_tool_is_in_scope() -> None:
    """``own_tool_scope`` drops each tool that it does not name (review P0)."""
    import json

    config = json.loads((_AGENT.parent / "config.json").read_text(encoding="utf-8"))
    for name in ("edit_draft", "snooze_email", "find_contact", "bulk_manage_inbox"):
        assert name in config["own_tool_scope"], name
        assert getattr(agents, name) in agents._TOOLS, name


async def test_edit_draft_adds_a_cc_and_keeps_the_body(gw: _Gw) -> None:
    gw.full_body = {"body_text": "Hi Ravi,\n\nThe quote is attached.\n\n" + "x" * 9000}
    await agents.edit_draft(BOX, "d1", cc=["kim@contoso.test", "priya@fracktal.test"])
    [card] = gw.cards
    assert "The text of the draft stays as it is." in card["context"]
    [(_, _, body)] = gw.writes
    assert body["cc"] == ["kim@contoso.test", "priya@fracktal.test"]
    assert body["body"] == gw.full_body["body_text"]
    assert body["keep_body"] is True and body["keep_html"] is True


async def test_edit_draft_keeps_the_body_it_cannot_read(gw: _Gw) -> None:
    """An Outlook HTML draft gives no text. The save sends none, and the route
    keeps the body (review P1)."""
    gw.full_body = {}
    out = await agents.edit_draft(BOX, "d1", subject="New subject")
    [(_, _, body)] = gw.writes
    assert body["body"] == "" and body["keep_body"] is True
    assert body["subject"] == "New subject"
    assert body["body"] != gw.draft["body_text"]
    assert "Nothing was sent." in out


async def test_edit_draft_says_when_the_formatting_is_lost(gw: _Gw, monkeypatch) -> None:
    async def put(path, body):
        gw.writes.append(("PUT", path, body))
        return {"id": "d1", "html": "lost"}

    monkeypatch.setattr(agents, "_put", put)
    out = await agents.edit_draft(BOX, "d1", body="Hi")
    assert "formatting and its images could not be kept" in out


async def test_edit_draft_says_no_and_writes_nothing(gw: _Gw) -> None:
    gw.answer = False
    out = await agents.edit_draft(BOX, "d1", body="Hi")
    assert out == "Cancelled. The draft did not change." and gw.writes == []


async def test_edit_draft_refuses_a_message_that_is_not_a_draft(gw: _Gw) -> None:
    gw.draft["folder"] = "inbox"
    out = await agents.edit_draft(BOX, "d1", body="Hi")
    assert "is not a draft" in out and gw.cards == []


# ── 2. snooze_email ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("until", "why"), [
    ("2099-01-05T09:00:00", "no offset"),
    ("2001-01-05T09:00:00+05:30", "has passed"),
    ("tomorrow at 9", "is not a time"),
])
async def test_a_snooze_time_needs_an_offset(gw: _Gw, until: str, why: str) -> None:
    out = await agents.snooze_email(MSG, until)
    assert out.startswith("Nothing changed.") and why in out
    assert gw.writes == [] and gw.cards == []


async def test_a_snooze_shows_the_time_and_posts_it(gw: _Gw) -> None:
    out = await agents.snooze_email(MSG, "2099-01-05T09:00:00+05:30")
    [card] = gw.cards
    assert "Mon 05 Jan 2099, 09:00 (+0530)" in card["title"]
    assert gw.writes == [("POST", f"/email/messages/{MSG}/snooze",
                          {"until": "2099-01-05T09:00:00+05:30"})]
    assert out.startswith("Snoozed")


async def test_an_empty_time_brings_it_back(gw: _Gw) -> None:
    await agents.snooze_email(MSG, "")
    assert gw.writes == [("POST", f"/email/messages/{MSG}/snooze", {"until": None})]


# ── 3. find_contact ──────────────────────────────────────────────────────────


async def test_find_contact_lists_and_says_to_ask(gw: _Gw) -> None:
    gw.contacts = [{"email": "priya@fracktal.test", "name": "Priya N"},
                   {"email": "priya.k@contoso.test", "name": "Priya K"}]
    out = await agents.find_contact("Priya")
    assert out.startswith("2 people match. Ask the member which one")
    assert "- Priya N <priya@fracktal.test>" in out
    assert gw.gets[-1] == ("/email/contacts/suggest", {"q": "Priya", "limit": 8})


async def test_find_contact_never_guesses(gw: _Gw) -> None:
    out = await agents.find_contact("Nobody")
    assert "Ask the member for the address" in out


async def test_find_contact_names_each_source_and_asks_with_options(gw: _Gw) -> None:
    """Owner, 2026-10-22: "add Kiran and Geo from Fracktal to CC". Each hit
    names its source, and a clash sends the model to ask_questions with one
    option for each person."""
    gw.contacts = [
        {"email": "kiran@fracktal.in", "name": "Kiran R", "source": "org"},
        {"email": "kiran@client.test", "name": "Kiran K", "source": "sent"},
        {"email": "kiran@x.test", "name": "Kiran X"},
    ]
    out = await agents.find_contact("Kiran", domain="@Fracktal.in")
    assert gw.gets[-1] == ("/email/contacts/suggest",
                           {"q": "Kiran", "limit": 8, "domain": "fracktal.in"})
    assert "- Kiran R <kiran@fracktal.in> · org:" in out
    assert "- Kiran K <kiran@client.test> · sent:" in out
    assert "- Kiran X <kiran@x.test> · seen:" in out
    assert "ask_questions" in out and "one option for each person" in out
    assert "Never guess" in out


async def test_find_contact_asks_before_it_uses_a_seen_address(gw: _Gw) -> None:
    gw.contacts = [{"email": "geo@x.test", "name": "Geo", "source": "seen"}]
    out = await agents.find_contact("Geo")
    assert out.startswith("One person matches, but the member never wrote")
    assert "ask_questions" in out


async def test_find_contact_uses_a_colleague_at_once(gw: _Gw) -> None:
    gw.contacts = [{"email": "geo@fracktal.in", "name": "Geo", "source": "org"}]
    out = await agents.find_contact("Geo", domain="fracktal")
    assert out.startswith("One person matches. Use this address")
    assert "ask_questions" not in out
    assert "at fracktal" in out


async def test_find_contact_says_a_bad_domain(gw: _Gw, monkeypatch) -> None:
    async def get(path, params=None):
        raise agents.GatewayError("domain must be a domain", 422)

    monkeypatch.setattr(agents, "_get", get)
    out = await agents.find_contact("Geo", domain="a b")
    assert "is not a domain" in out


async def test_edit_draft_card_leads_with_the_added_cc(gw: _Gw) -> None:
    """The card shows who joins the Cc first, and shows a Cc that the new
    list left out, so a dropped address cannot pass in silence."""
    out = await agents.edit_draft(BOX, "d1", cc=["kiran@fracktal.in", "geo@fracktal.in"])
    [card] = gw.cards
    ctx = card["context"]
    assert ctx.startswith("The changes to the recipients:")
    assert "- Added to Cc: kiran@fracktal.in" in ctx
    assert "- Added to Cc: geo@fracktal.in" in ctx
    assert "- Removed from Cc: kim@contoso.test" in ctx
    assert "Cc: kiran@fracktal.in, geo@fracktal.in." in out


# ── 3b. The in-chat Cc guidance (owner, 2026-10-22) ─────────────────────────
#
# Fence ``email-chat-cc-in-chat``: the guidance for an added Cc or Bcc keeps
# the member in the chat (find_contact, ask_questions, edit_draft,
# send_draft) and never sends them to the Email app.


def _cc_block() -> str:
    text = (_AGENT.parent / "instructions.md").read_text(encoding="utf-8")
    start = text.index("**Adding a Cc or a Bcc to a reply.**")
    return text[start:text.index("**Files of the email.**", start)]


def test_the_cc_guidance_stays_in_the_chat() -> None:
    block = _cc_block()
    for tool in ("find_contact", "ask_questions", "edit_draft", "send_draft",
                 "compose_reply"):
        assert f"`{tool}`" in block, tool
    assert "Never tell the member to open\nthe Email app" in block
    # Only the "never" line names the app.
    assert block.count("Email app") == 1
    text = (_AGENT.parent / "instructions.md").read_text(encoding="utf-8")
    assert "Put\n  only the added people in `cc`." not in text
    for doc in (agents.compose_reply.__doc__ or "", agents._CARD_REVISED):
        flat = " ".join(doc.split())
        assert "find_contact" in flat and "edit_draft" in flat
        assert "Never" in flat and "Email app" in flat
    assert "ask_questions" in (agents.find_contact.__doc__ or "")


# ── 4. forward_email(files=) ─────────────────────────────────────────────────


async def test_a_forward_sends_only_the_named_files(gw: _Gw) -> None:
    await agents.forward_email(MSG, ["lee@contoso.test"], files=["SPEC.pdf"])
    [card] = gw.cards
    assert "spec.pdf" in card["context"] and "photo.jpg" not in card["context"]
    [(_, path, body)] = gw.writes
    assert path == "/email/forward" and body["attachment_ids"] == ["f1"]


async def test_two_files_of_one_name_are_never_chosen_in_silence(gw: _Gw) -> None:
    gw.mail["attachments"].append({"id": "f3", "filename": "spec.pdf", "size_bytes": 10})
    out = await agents.forward_email(MSG, ["lee@contoso.test"], files=["spec.pdf"])
    assert out.startswith("Not forwarded.") and "f1, f3" in out
    assert gw.writes == []


async def test_an_unknown_file_forwards_nothing(gw: _Gw) -> None:
    out = await agents.forward_email(MSG, ["lee@contoso.test"], files=["budget.xlsx"])
    assert out.startswith("Not forwarded.") and "spec.pdf" in out
    assert gw.writes == [] and gw.cards == []


# ── 5. bulk_manage_inbox ─────────────────────────────────────────────────────


async def test_a_bulk_act_needs_a_filter(gw: _Gw) -> None:
    out = await agents.bulk_manage_inbox("read")
    assert out.startswith("Nothing changed.") and gw.writes == []


async def test_a_bulk_act_counts_then_asks_then_acts(gw: _Gw) -> None:
    out = await agents.bulk_manage_inbox("read", sender_email="news@acme.test")
    assert gw.writes[0] == ("POST", "/email/messages/bulk",
                            {"action": "read", "dry_run": True, "sender_email": "news@acme.test"})
    [card] = gw.cards
    assert card["title"] == "Mark as read about 7 emails?"
    assert "from news@acme.test" in card["detail"]
    assert gw.writes[1] == ("POST", "/email/messages/bulk",
                            {"action": "read", "sender_email": "news@acme.test"})
    assert out == "Mark as read: 7 emails changed."


async def test_a_bulk_act_with_no_match_asks_nothing(gw: _Gw) -> None:
    gw.matched = 0
    out = await agents.bulk_manage_inbox("archive", folder="inbox")
    assert "Nothing matches" in out and gw.cards == [] and len(gw.writes) == 1


async def test_the_route_count_changes_nothing() -> None:
    """With ``dry_run`` the route runs one COUNT and no UPDATE."""
    from unittest.mock import patch

    from gateway.routes.email.automation import senders as s

    from tests.unit._email_fakes import bind_db

    seen: list[str] = []

    class _DB:
        async def execute(self, clause, params=None):
            seen.append(str(clause))
            return MagicMock(scalar=MagicMock(return_value=4))

        async def commit(self): ...
        async def close(self): ...

    req = s.BulkActionRequest(action="read", account_id=BOX,
                              sender_email="news@acme.test", dry_run=True)
    background = MagicMock()
    with patch.object(s, "_tenant_session", bind_db(_DB())):
        res = await s.bulk_action(req, background, MagicMock(email="u@x.io"))
    assert res == {"matched": 4, "affected": 0}
    assert len(seen) == 1 and seen[0].lstrip().startswith("SELECT COUNT(*)")
    background.add_task.assert_not_called()


@_DB_GATE
class TestTheBulkCountOnARealDatabase:
    """R8: the COUNT runs on Postgres under RLS, counts only the member's mail
    that the act would change, and writes nothing."""

    async def test_the_count_matches_the_act(self, promoted, app_engine):  # noqa: F811
        from gateway.routes.email.automation import senders as s

        _assert_non_priv(app_engine)
        p = promoted
        tag = uuid.uuid4().hex[:8]
        owner = f"member-{tag}@t8g1.test"
        box = _account(p.admin_engine, org=p.org_b, owner=owner, default=True)
        other = _account(p.admin_engine, org=p.org_b, owner=f"mallory-{tag}@t8g1.test")
        news = f"news-{tag}@sender.test"
        for _ in range(3):
            _mail(p.admin_engine, org=p.org_b, account_id=box, sender=news)
        _mail(p.admin_engine, org=p.org_b, account_id=other, sender=news)
        me = UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b)
        try:
            async with _as_member(p, p.org_b):
                count = await s.bulk_action(
                    s.BulkActionRequest(action="read", sender_email=news, dry_run=True),
                    MagicMock(), me)
            assert count == {"matched": 3, "affected": 0}
            with p.admin_engine.begin() as c:
                unread = c.execute(text(
                    "SELECT COUNT(*) FROM email_messages WHERE account_id = CAST(:a AS uuid) "
                    "AND is_read = false"), {"a": box}).scalar()
            assert unread == 3
            async with _as_member(p, p.org_b):
                done = await s.bulk_action(
                    s.BulkActionRequest(action="read", sender_email=news),
                    MagicMock(), me)
            assert done == {"affected": 3}
        finally:
            _purge(p.admin_engine, f"%-{tag}@t8g1.test")




# ── The route keeps the body of an Outlook draft (review P1) ─────────────────


@pytest.mark.parametrize(("gmail", "sent_body"), [(False, None), (True, "Hi Ravi,")])
async def test_the_route_keeps_the_body_when_asked(monkeypatch, gmail, sent_body) -> None:
    from contextlib import asynccontextmanager

    from gateway.routes.email.automation import drafting as dr

    drow = SimpleNamespace(provider_message_id="pm1", thread_id="t1",
                           subject="Re: Quote", body_text="<stored copy>")

    class _Db:
        async def execute(self, *a, **kw):
            return SimpleNamespace(fetchone=lambda: drow)

    @asynccontextmanager
    async def session():
        yield _Db()

    updates: list[dict[str, Any]] = []

    class _Provider:
        async def update_draft(self, pmid, **kw):
            updates.append(kw)
            return "pm1"

    provider = _Provider()

    @asynccontextmanager
    async def provider_session(*a, **kw):
        yield SimpleNamespace(provider=provider, owner_email="dana@fracktal.test")

    upserts: list[dict[str, Any]] = []

    async def upsert(*a, **kw):
        upserts.append(kw)
        return "d1"

    async def nothing(*a, **kw):
        return ""

    async def html(*a, **kw):
        return "<p>Hi</p>", dr.HTML_KEPT

    async def fetch(db, local_id):
        return {"id": local_id}

    monkeypatch.setattr(dr, "_tenant_session", session)
    monkeypatch.setattr(dr, "_assert_account_owner", nothing)
    monkeypatch.setattr(dr, "_account_signature", nothing)
    monkeypatch.setattr(dr, "provider_session", provider_session)
    monkeypatch.setattr(dr, "_upsert_local_draft", upsert)
    monkeypatch.setattr(dr, "_html_to_keep", html)
    monkeypatch.setattr(dr, "_fetch_message_dict", fetch)
    monkeypatch.setattr(dr, "_is_gmail", lambda p: gmail)
    req = dr.DraftUpsertRequest(
        account_id=BOX, draft_id="b0000000-0000-4000-8000-0000000000d1",
        to=["ravi@contoso.test"], cc=["priya@fracktal.test"], subject="New",
        body="Hi Ravi," if gmail else "", keep_html=True, keep_body=True)
    await dr.upsert_draft(req, SimpleNamespace(email="dana@fracktal.test"))
    [kw] = updates
    assert kw["body_text"] == sent_body
    if not gmail:
        assert upserts[0]["body"] == "<stored copy>"


async def test_find_contact_says_closest_match_for_a_fuzzy_hit(gw: _Gw) -> None:
    """Owner, 2026-10-23: "Venna" is Veena. A trigram hit is a guess, so the
    model must confirm it with the member and not use it at once."""
    gw.contacts = [{"email": "veena@fracktal.in", "name": "Veena",
                    "source": "org", "match": "closest"}]
    out = await agents.find_contact("Venna")
    assert "CLOSEST" in out and "No exact match" in out
    assert "Use this address" not in out
    assert "- Veena <veena@fracktal.in>" in out
