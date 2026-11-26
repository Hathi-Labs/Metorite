"""Task 3977: "delete the draft" reads Drafts, and the answer counts what left.

Production, 2026-11-26. A thread held two drafts. The chat saved one at 06:05,
and a second one appeared at 06:21 with two PDFs and an empty body. At 06:29 the
member asked to delete the draft in Drafts. The chat trashed only its own id and
said "The draft is deleted". The other draft stayed.

Each rule names the mutation that turns it red (R7):

1. **Flag off is the card and the answer of before.** Mutation: drop the
   ``_draft_choice()`` check -> ``test_flag_off_is_the_trash_of_before``.
2. **A second draft of the thread shows on the card, unticked.** Mutation: match
   by id only -> ``test_two_drafts_of_a_thread_show_on_one_card``.
3. **Only ticked drafts move.** Mutation: trash each match ->
   ``test_only_the_ticked_drafts_move``.
4. **The answer counts what left Drafts and names what stays.** Mutation: count
   ``len(ids)`` -> ``test_a_draft_that_stays_is_named_and_not_counted``.
5. **A run with no rows card acts on the named draft only.** Mutation: ``pick``
   always -> ``test_no_rows_channel_acts_on_the_named_draft_only``.
6. **Nothing says that no draft is left over a cut page.** Mutation: drop *cut*
   -> ``test_a_cut_drafts_page_never_claims_none_left``.

The gateway is faked at ``_request``, the seam under ``_get``, ``_post`` and
``_delete``, and the card at ``request_confirmation``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import httpx
import pytest
from acb_common import get_settings
from acb_skills import draft_choice

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ea_draft_choice", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "acc-1"
BOX2 = "acc-2"
CHAT_DRAFT = "11111111-1111-4111-8111-111111111111"
MEMBER_DRAFT = "22222222-2222-4222-8222-222222222222"
OTHER_DRAFT = "33333333-3333-4333-8333-333333333333"
MAIL = "44444444-4444-4444-8444-444444444444"
MAIL2 = "55555555-5555-4555-8555-555555555555"
FAR_DRAFT = "66666666-6666-4666-8666-666666666666"


@pytest.fixture(autouse=True)
def _open_run() -> None:
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(no_egress=False)


def _draft(mid: str, **extra: Any) -> dict[str, Any]:
    row = {
        "id": mid, "account_id": BOX, "folder": "drafts", "thread_id": "T",
        "subject": "Re: Proposal", "received_at": "2026-11-26T06:05:00+00:00",
        "to_addresses": [{"email": "salil@client.test"}], "snippet": "Dear Salil,",
        "has_attachments": False,
    }
    row.update(extra)
    return row


#: The production pair: the chat's draft, and the one the member made, with two
#: files and an empty body, and no To person.
PAIR = [
    _draft(CHAT_DRAFT),
    _draft(MEMBER_DRAFT, to_addresses=[], snippet="", has_attachments=True,
           subject="Re: Proposal", received_at="2026-11-26T06:21:00+00:00"),
]
UNRELATED = _draft(OTHER_DRAFT, thread_id="U", subject="Lunch",
                   to_addresses=[{"email": "kim@client.test"}])


class _Resp:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.status_code = 200
        self.content = b"x"

    def json(self) -> Any:
        return self._data


class Mailbox:
    """The mirror of one mailbox, and the gateway routes that the tool calls.

    A DELETE moves a message to Trash and answers the outcome of
    ``?report=true``. ``refuse`` gets a 500, ``unconfirmed`` gets
    ``not_confirmed``, and ``lose`` moves the row and then times out. A read
    of ONE message fails the test: the tool reads the batch summaries only."""

    def __init__(self, provider: str = "microsoft") -> None:
        self.provider = provider
        self.rows = {m["id"]: dict(m) for m in [*PAIR, UNRELATED]}
        self.mail(MAIL)
        self.writes: list[tuple[str, str]] = []
        self.refuse: set[str] = set()
        self.unconfirmed: set[str] = set()
        self.lose: set[str] = set()
        self.drafts_reads = 0
        self.fail_drafts_read_after = False
        self.total_extra = 0
        self.delete_route: Any = None

    def summary(self, row: dict[str, Any]) -> dict[str, Any]:
        """The row as ``POST /email/messages/summaries`` answers it."""
        sender = row.get("from_email") or "jane@acme.test"
        return {**row, "from": sender, "from_email": sender,
                "subject": row.get("subject") or "(no subject)"}

    def mail(self, mid: str, **extra: Any) -> None:
        """An inbound mail from Ann in the inbox of the Work mailbox."""
        values = {"folder": "inbox", "thread_id": "M", "subject": "Offer",
                  "from_email": "ann@vendor.test", **extra}
        self.rows[mid] = _draft(mid, **values)

    async def request(self, method: str, path: str, **kw: Any) -> _Resp:
        if method == "GET" and path == "/email/accounts":
            return _Resp([{"id": BOX, "provider": self.provider,
                           "email_address": "jane@acme.test", "label": "Work"},
                          {"id": BOX2, "provider": "gmail",
                           "email_address": "jane@home.test", "label": "Home"}])
        if method == "GET" and path == "/email/messages":
            return self.drafts(kw.get("params") or {})
        if method == "GET":
            raise AssertionError(f"the tool read one message: {path}")
        if method == "DELETE":
            return await self.delete(path.rsplit("/", 1)[1], kw.get("params") or {})
        if method == "POST" and path == "/email/messages/summaries":
            # As the route (``summary_ids``): canonical UUIDs, a bad id drops.
            ids = [str(agents._canonical_id(i)) for i in kw["json"]["ids"]]
            return _Resp({"summaries": [self.summary(self.rows[i]) for i in ids
                                        if i in self.rows]})
        if method == "POST" and path == "/email/messages/bulk":
            ids = kw["json"]["message_ids"]
            self.writes.append(("BULK", ",".join(ids)))
            # As the route: an unknown id or a row in Trash changes nothing.
            moved = [i for i in ids if i in self.rows and self.rows[i]["folder"] != "trash"]
            for mid in moved:
                self.rows[mid]["folder"] = "trash"
            return _Resp({"affected": len(moved)})
        raise AssertionError(f"unexpected call {method} {path}")

    def drafts(self, params: dict[str, Any]) -> _Resp:
        assert params["folder"] == "drafts"
        self.drafts_reads += 1
        if self.fail_drafts_read_after and self.writes:
            raise httpx.ReadTimeout("slow")
        rows = [r for r in self.rows.values()
                if r["folder"] == "drafts" and r["account_id"] == params["account_id"]]
        return _Resp({"emails": rows, "total": len(rows) + self.total_extra})

    async def delete(self, mid: str, params: dict[str, Any]) -> _Resp:
        assert params == {"report": "true"}, "the tool asks for the outcome"
        self.writes.append(("DELETE", mid))
        if self.delete_route is not None:
            return await self.delete_route(mid)
        if mid in self.refuse:
            raise agents.GatewayError("refused", 500, detail="provider")
        self.rows[mid]["folder"] = "trash"
        if mid in self.lose:
            raise httpx.ReadTimeout("slow")
        outcome = "not_confirmed" if mid in self.unconfirmed else "confirmed"
        return _Resp({"id": mid, "provider": outcome, "discarded": False})


class Card:
    def __init__(self, answer: Any = True) -> None:
        self.answer = answer
        self.shown: list[dict[str, Any]] = []

    async def __call__(self, **kw: Any) -> Any:
        from acb_skills.ask_tools import clean_card_rows

        clean_card_rows(kw.get("rows"))  # the check of the real card
        self.shown.append(kw)
        return self.answer


@pytest.fixture()
def box(monkeypatch: pytest.MonkeyPatch) -> Mailbox:
    mailbox = Mailbox()
    monkeypatch.setattr(agents, "_request", mailbox.request)
    monkeypatch.setattr(get_settings(), "chat_draft_choice_orgs", "*")
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    return mailbox


def _card(monkeypatch: pytest.MonkeyPatch, answer: Any = True) -> Card:
    card = Card(answer)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return card


@pytest.mark.asyncio
async def test_flag_off_is_the_trash_of_before(box, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_draft_choice_orgs", "")
    card = _card(monkeypatch)
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert out == "trash: affected 1 message(s)."
    assert box.writes == [("BULK", CHAT_DRAFT)]
    assert len(card.shown) == 1 and not card.shown[0].get("rows")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,line", [
    ("microsoft", "Outlook moves a trashed draft to Deleted Items."),
    ("gmail", "Gmail deletes a trashed draft for good."),
])
async def test_two_drafts_of_a_thread_show_on_one_card(box, monkeypatch, provider, line):
    box.provider = provider
    card = _card(monkeypatch, frozenset({CHAT_DRAFT, MEMBER_DRAFT}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert len(card.shown) == 1
    rows = card.shown[0]["rows"]
    assert [r["id"] for r in rows] == [CHAT_DRAFT, MEMBER_DRAFT]
    assert [r["checked"] for r in rows] == [False, False]
    assert line in card.shown[0]["detail"]
    assert "has files" in rows[1]["hint"] and "no text" in rows[1]["hint"]
    assert box.writes == [("DELETE", CHAT_DRAFT), ("DELETE", MEMBER_DRAFT)]
    assert out.startswith("Trashed 2 message(s): 2 draft(s).")
    assert "No matching draft is left in Drafts" in out
    assert OTHER_DRAFT not in out


@pytest.mark.asyncio
async def test_only_the_ticked_drafts_move(box, monkeypatch):
    _card(monkeypatch, frozenset({MEMBER_DRAFT}))
    await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert box.writes == [("DELETE", MEMBER_DRAFT)]
    assert box.rows[CHAT_DRAFT]["folder"] == "drafts"


@pytest.mark.asyncio
async def test_a_draft_that_stays_is_named_and_not_counted(box, monkeypatch):
    _card(monkeypatch, frozenset({CHAT_DRAFT, MEMBER_DRAFT}))
    box.refuse = {MEMBER_DRAFT}
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")
    assert "1 draft(s) did not move" in out
    assert "1 matching draft(s) are still in Drafts" in out
    trashed, stays = out.split("are still in Drafts:")
    assert f"draft id={CHAT_DRAFT}" in trashed.split("Trashed:")[1]
    assert f"draft id={MEMBER_DRAFT}" in stays
    assert f"draft id={CHAT_DRAFT}" not in stays


@pytest.mark.asyncio
async def test_a_member_pick_of_one_names_the_other(box, monkeypatch):
    _card(monkeypatch, frozenset({CHAT_DRAFT}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")
    assert f"draft id={MEMBER_DRAFT}" in out


@pytest.mark.asyncio
async def test_a_card_that_does_nothing_writes_nothing(box, monkeypatch):
    _card(monkeypatch, frozenset())
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert box.writes == []
    assert "No draft was moved to Trash." in out


@pytest.mark.asyncio
async def test_one_matching_draft_keeps_the_plain_card(box, monkeypatch):
    card = _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", [OTHER_DRAFT])
    assert card.shown[0]["title"] == "Move 1 draft to Trash?"
    assert not card.shown[0].get("rows")
    assert box.writes == [("DELETE", OTHER_DRAFT)]
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")


@pytest.mark.asyncio
async def test_a_trashed_anchor_still_finds_the_other_draft(box, monkeypatch):
    box.rows[CHAT_DRAFT]["folder"] = "trash"
    card = _card(monkeypatch, frozenset({MEMBER_DRAFT}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert [r["id"] for r in card.shown[0]["rows"]] == [MEMBER_DRAFT]
    assert box.writes == [("DELETE", MEMBER_DRAFT)]
    assert out.startswith("Trashed 1 message(s)")


@pytest.mark.asyncio
async def test_already_trashed_with_no_other_draft_claims_nothing(box, monkeypatch):
    for mid in (CHAT_DRAFT, MEMBER_DRAFT):
        box.rows[mid]["folder"] = "trash"
    card = _card(monkeypatch)
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert out.startswith("Nothing changed.")
    assert card.shown == [] and box.writes == []


@pytest.mark.asyncio
async def test_no_rows_channel_acts_on_the_named_draft_only(box, monkeypatch):
    card = _card(monkeypatch, True)
    monkeypatch.setattr("acb_skills.ask_tools.rows_card_channel_open", lambda: False)
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert not card.shown[0].get("rows")
    assert "These matching drafts stay in Drafts:" in card.shown[0]["context"]
    assert box.writes == [("DELETE", CHAT_DRAFT)]
    assert f"draft id={MEMBER_DRAFT}" in out


@pytest.mark.asyncio
async def test_a_mail_that_is_no_draft_keeps_the_card_of_before(box, monkeypatch):
    card = _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", [MAIL])
    assert out == "trash: affected 1 message(s)."
    assert card.shown[0]["title"] == "Move 1 message to Trash?"


@pytest.mark.asyncio
async def test_a_draft_and_a_mail_go_on_one_card(box, monkeypatch):
    card = _card(monkeypatch, frozenset({CHAT_DRAFT, MAIL}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT, MAIL])
    rows = {r["id"]: r["checked"] for r in card.shown[0]["rows"]}
    assert rows == {CHAT_DRAFT: False, MEMBER_DRAFT: False, MAIL: True}
    assert out.startswith("Trashed 2 message(s): 1 draft(s) and 1 other message(s).")


@pytest.mark.asyncio
async def test_a_cut_drafts_page_never_claims_none_left(box, monkeypatch):
    box.total_extra = 5
    _card(monkeypatch, frozenset({CHAT_DRAFT, MEMBER_DRAFT}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert "No matching draft is left" not in out
    assert "longer than one page" in out


def test_the_match_reads_thread_recipient_and_subject():
    anchor = _draft("a")
    drafts = [
        _draft("same-thread", to_addresses=[], subject="x"),
        _draft("same-to", thread_id="Z", subject="x"),
        _draft("same-subject", thread_id="Z", to_addresses=[], subject="Fwd: proposal"),
        _draft("other", thread_id="Z", to_addresses=[], subject="x"),
        _draft("other-box", account_id="acc-2"),
        _draft("sent", folder="sent"),
    ]
    got = [m["id"] for m in draft_choice.matching(drafts, [anchor])]
    assert got == ["same-thread", "same-to", "same-subject"]


@pytest.mark.parametrize("value,org,on", [
    ("", "org-1", False), ("org-1", "org-1", True), ("org-1", "org-2", False),
    ("*", "org-9", True),
])
def test_the_flag_reader(monkeypatch, value, org, on):
    monkeypatch.setattr(get_settings(), "chat_draft_choice_orgs", value)
    assert draft_choice.draft_choice_on(org) is on


# ── Review round 1 (2026-11-26) ──────────────────────────────────────────────
#
# Each test below is red without its fix. The finding number is the one of the
# review round.

from contextlib import asynccontextmanager  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from email_ingestion.providers.imap import IMAPProvider  # noqa: E402
from gateway.routes.email.transport import messages as route  # noqa: E402

USER = SimpleNamespace(email="jane@acme.test")


def _route_db() -> Any:
    """A session whose UPDATE finds the row, as the owner check does."""
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute.return_value = result
    return db


def _bind(db: Any) -> Any:
    @asynccontextmanager
    async def _session():
        yield db
    return _session


def _provider(**overrides: Any) -> Any:
    p = AsyncMock()
    p.authenticate = AsyncMock(return_value=True)
    p.trash_message = AsyncMock(return_value=None)
    p.credentials_dirty = MagicMock(return_value=False)
    p.discarded_drafts = set()
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _wire_route(monkeypatch: pytest.MonkeyPatch, loader: Any) -> None:
    monkeypatch.setattr(route, "_tenant_session", _bind(_route_db()))
    monkeypatch.setattr(route, "_provider_for_message", loader)
    monkeypatch.setattr(route, "_persist_rotated_creds", AsyncMock())


def _loader(provider: Any, pmid: str = "PM-1") -> Any:
    return AsyncMock(return_value=(provider, pmid, BOX, object()))


async def _report(mid: str = CHAT_DRAFT) -> dict[str, Any]:
    import json

    resp = await route.delete_message(mid, user=USER, report=True)
    assert resp.status_code == 200
    return json.loads(resp.body)


# Finding 1: the route says what the provider did, and the tool says it.

@pytest.mark.asyncio
@pytest.mark.parametrize("case,reason", [
    ("unauthenticated", "not_authenticated"),
    ("raises", "provider_error"),
    ("no_provider", "no_provider"),
])
async def test_the_route_reports_each_trash_the_provider_did_not_confirm(
        monkeypatch, case, reason):
    if case == "unauthenticated":
        loader = _loader(_provider(authenticate=AsyncMock(return_value=False)))
    elif case == "raises":
        loader = _loader(_provider(trash_message=AsyncMock(side_effect=RuntimeError("x"))))
    else:
        loader = AsyncMock(side_effect=RuntimeError("no credentials"))
    _wire_route(monkeypatch, loader)
    body = await _report()
    assert body == {"id": CHAT_DRAFT, "provider": "not_confirmed", "reason": reason,
                    "discarded": False}


@pytest.mark.asyncio
async def test_the_route_confirms_a_trash_and_keeps_204_by_default(monkeypatch):
    provider = _provider(credentials_dirty=MagicMock(return_value=True))
    _wire_route(monkeypatch, _loader(provider))
    # A failure of the saved tokens AFTER the trash does not undo it.
    monkeypatch.setattr(route, "_persist_rotated_creds",
                        AsyncMock(side_effect=RuntimeError("db")))
    assert (await _report())["provider"] == "confirmed"
    assert await route.delete_message(CHAT_DRAFT, user=USER) is None


@pytest.mark.asyncio
async def test_an_imap_draft_trash_is_not_confirmed(monkeypatch):
    """A draft that Metorite saved on IMAP has the id ``draft-Drafts``. The
    old trash selected INBOX and reported nothing. It now raises, and the
    route answers ``not_confirmed``."""
    imap = IMAPProvider({})
    imap.authenticate = AsyncMock(return_value=True)  # type: ignore[method-assign]
    imap._get_imap = AsyncMock(side_effect=AssertionError("no connect"))  # type: ignore[method-assign]
    _wire_route(monkeypatch, _loader(imap, "draft-Drafts"))
    body = await _report()
    assert body["provider"] == "not_confirmed" and body["reason"] == "provider_error"


@pytest.mark.asyncio
async def test_an_imap_store_that_is_not_ok_raises():
    server = MagicMock()
    server.select.return_value = ("NO", [b""])
    server.uid.return_value = ("NO", [b"no such message"])
    imap = IMAPProvider({})
    imap._get_imap = AsyncMock(return_value=server)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="STORE"):
        await imap.trash_message("42")


@pytest.mark.asyncio
async def test_a_trash_the_provider_refused_is_never_counted(box, monkeypatch):
    """The tool calls the REAL route, with a provider that does not
    authenticate. The mirror reads Trash, and the answer counts nothing."""
    _wire_route(monkeypatch, _loader(_provider(authenticate=AsyncMock(return_value=False))))

    async def _real(mid: str) -> _Resp:
        box.rows[mid]["folder"] = "trash"  # the route's UPDATE
        resp = await route.delete_message(mid, user=USER, report=True)
        return _Resp(__import__("json").loads(resp.body))

    box.delete_route = _real
    _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", [OTHER_DRAFT])
    assert out.startswith("Trashed 0 message(s): the mailbox did not confirm a trash.")
    assert "did not confirm the trash of 1 draft(s)" in out
    assert "can bring them back" in out
    assert "Do not say that they were deleted." in out


@pytest.mark.asyncio
async def test_other_mail_the_provider_did_not_confirm_is_named(box, monkeypatch):
    _card(monkeypatch, True)
    box.unconfirmed = {MAIL}
    out = await agents.manage_inbox("trash", [OTHER_DRAFT, MAIL])
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")
    assert "did not confirm the trash of 1 other message(s)" in out
    unconfirmed = out.split("Not confirmed by the mailbox provider:")[1]
    assert f"message id={MAIL}" in unconfirmed


# Finding 2: each card names each message, each mailbox, and each unknown id.

@pytest.mark.asyncio
async def test_the_rows_card_names_each_mail_and_no_two_rows_read_the_same(box, monkeypatch):
    box.mail(MAIL2)  # the same sender and subject as MAIL
    box.rows[MAIL2]["received_at"] = box.rows[MAIL]["received_at"]
    card = _card(monkeypatch, frozenset({CHAT_DRAFT}))
    await agents.manage_inbox("trash", [CHAT_DRAFT, MAIL, MAIL2, "not-a-uuid"])
    rows = card.shown[0]["rows"]
    mails = [r for r in rows if r["id"] in (MAIL, MAIL2)]
    assert [r["label"] for r in mails] == ["From ann@vendor.test: Offer"] * 2
    assert all(r["checked"] for r in mails)
    shown = [(r["label"], r["hint"]) for r in rows]
    assert len(set(shown)) == len(shown)
    assert "1 id(s) name no message of yours" in card.shown[0]["detail"]
    assert "Work · jane@acme.test" in card.shown[0]["detail"]


@pytest.mark.asyncio
async def test_the_card_names_the_mailbox_of_each_message(box, monkeypatch):
    box.mail(MAIL2, account_id=BOX2)
    card = _card(monkeypatch, frozenset({CHAT_DRAFT}))
    await agents.manage_inbox("trash", [CHAT_DRAFT, MAIL2])
    detail = card.shown[0]["detail"]
    assert "Mailboxes: Home · jane@home.test, Work · jane@acme.test." in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["no_rows", "whatsapp"])
async def test_the_plain_card_names_each_mail_and_counts_right(box, monkeypatch, channel):
    if channel == "whatsapp":
        monkeypatch.setattr("acb_skills.ask_tools._whatsapp_run", lambda: True)
    else:
        monkeypatch.setattr("acb_skills.ask_tools.rows_card_channel_open", lambda: False)
    box.mail(MAIL2, subject="Invoice")
    card = _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", [CHAT_DRAFT, MAIL, MAIL2])
    shown = card.shown[0]
    assert not shown.get("rows")
    assert shown["title"] == "Move 1 draft and 2 other messages to Trash?"
    assert "From ann@vendor.test: Offer" in shown["context"]
    assert "From ann@vendor.test: Invoice" in shown["context"]
    assert out.startswith("Trashed 3 message(s): 1 draft(s) and 2 other message(s).")


@pytest.mark.asyncio
async def test_the_plain_card_counts_each_staying_draft_that_it_cuts(box, monkeypatch):
    """With no rows card, a long list of staying drafts says how many it cut."""
    monkeypatch.setattr("acb_skills.ask_tools.rows_card_channel_open", lambda: False)
    for n in range(draft_choice.MAX_DRAFTS + 2):
        mid = f"77777777-7777-4777-8777-{n:012d}"
        box.rows[mid] = _draft(mid)
    card = _card(monkeypatch, True)
    await agents.manage_inbox("trash", [CHAT_DRAFT])
    context = card.shown[0]["context"]
    staying = context.split("These matching drafts stay in Drafts:")[1]
    # The pair's other draft and 22 more stay: 23, of which the card lists 20.
    assert staying.count("\n- ") == draft_choice.MAX_DRAFTS
    assert "+3 more" in staying


@pytest.mark.asyncio
async def test_a_long_plain_card_keeps_each_message_that_moves(box, monkeypatch):
    """The card clips its context at 4000 characters, with no count."""
    monkeypatch.setattr("acb_skills.ask_tools.rows_card_channel_open", lambda: False)
    moving = [f"88888888-8888-4888-8888-{n:012d}" for n in range(draft_choice.MAX_IDS)]
    long = "Re: " + "a very long subject " * 20
    for mid in moving:
        box.rows[mid] = _draft(mid, subject=long, snippet="words " * 40)
    for n in range(draft_choice.MAX_DRAFTS + 2):
        mid = f"99999999-9999-4999-8999-{n:012d}"
        box.rows[mid] = _draft(mid, subject=long, snippet="words " * 40)
    card = _card(monkeypatch, False)
    await agents.manage_inbox("trash", moving)
    context = card.shown[0]["context"]
    assert len(context) <= agents._CARD_CONTEXT_MAX
    head, rest = context.split("These matching drafts stay in Drafts:")
    lines = [ln for ln in head.splitlines() if ln.startswith("- ")]
    assert len(lines) == len(set(lines)) == len(moving)
    listed = sum(ln.startswith("- ") for ln in rest.splitlines())
    staying = len([r for r in box.rows.values() if r["folder"] == "drafts"
                   and r["thread_id"] == "T"]) - len(moving)
    assert f"+{staying - listed} more" in rest


def test_the_title_of_one_other_message():
    assert agents._trash_title(1, 1) == "Move 1 draft and 1 other message to Trash?"
    assert agents._trash_title(0, 2) == "Move 2 messages to Trash?"


# Finding 3: one batch read, and a bad id never blocks a good one.

@pytest.mark.asyncio
async def test_a_bad_id_never_blocks_a_draft(box, monkeypatch):
    _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", ["not-a-uuid", OTHER_DRAFT])
    assert box.writes == [("DELETE", OTHER_DRAFT)]
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")
    assert "1 id(s) name no message of yours" in out


@pytest.mark.asyncio
async def test_a_call_with_no_draft_reads_no_drafts(box, monkeypatch):
    _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", [MAIL, "not-a-uuid"])
    assert out == "trash: affected 1 message(s)."
    assert box.drafts_reads == 0


# Finding 4: a trashed inbound mail is no anchor, and blocks nothing.

@pytest.mark.asyncio
async def test_a_trashed_inbound_mail_finds_no_draft(box, monkeypatch):
    box.rows[MAIL].update(folder="trash", thread_id="T")  # the thread of the drafts
    box.mail(MAIL2)
    card = _card(monkeypatch, True)
    out = await agents.manage_inbox("trash", [MAIL, MAIL2])
    assert out == "trash: affected 1 message(s)."
    assert not card.shown[0].get("rows") and box.drafts_reads == 0


@pytest.mark.asyncio
async def test_a_trashed_inbound_mail_beside_a_draft_stays_out(box, monkeypatch):
    box.rows[MAIL].update(folder="trash", thread_id="U")
    card = _card(monkeypatch, True)
    monkeypatch.setattr("acb_skills.ask_tools.rows_card_channel_open", lambda: False)
    out = await agents.manage_inbox("trash", [OTHER_DRAFT, MAIL])
    assert card.shown[0]["title"] == "Move 1 draft to Trash?"
    assert "Already in Trash" in card.shown[0]["context"]
    assert box.writes == [("DELETE", OTHER_DRAFT)]
    assert "1 message(s) that you named were already in Trash." in out


# Finding 5: the read after matches the first anchors only.

@pytest.mark.asyncio
async def test_the_read_after_uses_the_first_anchors_only(box, monkeypatch):
    # FAR_DRAFT shares the To of MEMBER_DRAFT only, not of the named draft.
    box.rows[MEMBER_DRAFT]["to_addresses"] = [{"email": "kim@far.test"}]
    box.rows[FAR_DRAFT] = _draft(FAR_DRAFT, thread_id="Z", subject="Lunch",
                                 to_addresses=[{"email": "kim@far.test"}])
    _card(monkeypatch, frozenset({CHAT_DRAFT}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert f"draft id={MEMBER_DRAFT}" in out
    assert FAR_DRAFT not in out


# Finding 6: the named drafts come first, before the cut of the card.

@pytest.mark.asyncio
async def test_a_named_draft_is_on_the_card_before_the_cut(box, monkeypatch):
    monkeypatch.setattr(draft_choice, "MAX_DRAFTS", 1)
    card = _card(monkeypatch, frozenset({MEMBER_DRAFT}))
    await agents.manage_inbox("trash", [MEMBER_DRAFT])
    assert [r["id"] for r in card.shown[0]["rows"]] == [MEMBER_DRAFT]


# Finding 7: other mail that did not move is counted and named.

@pytest.mark.asyncio
async def test_other_mail_that_did_not_move_is_named(box, monkeypatch):
    box.mail(MAIL2, subject="Invoice")
    box.refuse = {MAIL2}
    _card(monkeypatch, True)
    monkeypatch.setattr("acb_skills.ask_tools.rows_card_channel_open", lambda: False)
    out = await agents.manage_inbox("trash", [OTHER_DRAFT, MAIL, MAIL2])
    assert out.startswith("Trashed 2 message(s): 1 draft(s) and 1 other message(s).")
    assert "1 other message(s) did not move to Trash. They stay where they were." in out
    assert f"message id={MAIL2}" in out.split("Did not move:")[1]


# Finding 8: a lost answer keeps the report, and a repeated id is one message.

@pytest.mark.asyncio
async def test_a_lost_answer_keeps_the_report_of_what_moved(box, monkeypatch):
    _card(monkeypatch, frozenset({CHAT_DRAFT, MEMBER_DRAFT}))
    box.lose = {MEMBER_DRAFT}
    out = await agents.manage_inbox("trash", [CHAT_DRAFT])
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")
    assert "Metorite got no answer for 1 message(s)" in out
    assert f"draft id={CHAT_DRAFT}" in out.split("Trashed:")[1]


@pytest.mark.asyncio
async def test_a_failed_read_after_keeps_the_report(box, monkeypatch):
    _card(monkeypatch, True)
    box.fail_drafts_read_after = True
    out = await agents.manage_inbox("trash", [OTHER_DRAFT])
    assert out.startswith("Trashed 1 message(s): 1 draft(s).")
    assert "could not read Drafts after the act" in out


@pytest.mark.asyncio
async def test_a_repeated_id_is_one_message(box, monkeypatch):
    """The same mail twice, once in braces, draws one row and one write."""
    card = _card(monkeypatch, frozenset({CHAT_DRAFT, MAIL}))
    out = await agents.manage_inbox("trash", [CHAT_DRAFT, MAIL, "{" + MAIL + "}"])
    assert [r["id"] for r in card.shown[0]["rows"]] == [CHAT_DRAFT, MEMBER_DRAFT, MAIL]
    assert box.writes == [("DELETE", CHAT_DRAFT), ("DELETE", MAIL)]
    assert out.startswith("Trashed 2 message(s): 1 draft(s) and 1 other message(s).")


def test_the_summaries_route_gives_the_draft_fields():
    row = SimpleNamespace(
        id=CHAT_DRAFT, thread_id="T", subject="S", from_address={"email": "a@b"},
        received_at=None, is_read=True, has_attachments=False, account_id=BOX,
        folder="drafts", to_addresses='[{"email": "c@d"}]', snippet="Hi")
    assert route._summary_extra(row) == {
        "account_id": BOX, "folder": "drafts", "to_addresses": [{"email": "c@d"}],
        "snippet": "Hi"}


def test_the_head_names_no_zero_part():
    head, _, _ = draft_choice.report(
        draft_choice.Tally(others=[{"id": MAIL, "folder": "inbox"}]), lambda v, _n: v)
    assert head[0] == "Trashed 1 message(s): 1 other message(s)."
