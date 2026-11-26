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
CHAT_DRAFT = "11111111-1111-4111-8111-111111111111"
MEMBER_DRAFT = "22222222-2222-4222-8222-222222222222"
OTHER_DRAFT = "33333333-3333-4333-8333-333333333333"
MAIL = "44444444-4444-4444-8444-444444444444"


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
    """The mirror of one mailbox. A DELETE moves a draft to Trash."""

    def __init__(self, provider: str = "microsoft") -> None:
        self.provider = provider
        self.rows = {m["id"]: dict(m) for m in [*PAIR, UNRELATED]}
        self.rows[MAIL] = _draft(MAIL, folder="inbox", thread_id="M", subject="Offer")
        self.writes: list[tuple[str, str]] = []
        self.refuse: set[str] = set()
        self.total_extra = 0

    async def request(self, method: str, path: str, **kw: Any) -> _Resp:
        if method == "GET" and path == "/email/accounts":
            return _Resp([{"id": BOX, "provider": self.provider,
                           "email_address": "jane@acme.test", "label": "Work"}])
        if method == "GET" and path == "/email/messages":
            params = kw.get("params") or {}
            assert params["folder"] == "drafts"
            rows = [r for r in self.rows.values()
                    if r["folder"] == "drafts" and r["account_id"] == params["account_id"]]
            return _Resp({"emails": rows, "total": len(rows) + self.total_extra})
        if method == "GET" and path.startswith("/email/messages/"):
            mid = path.rsplit("/", 1)[1]
            if mid not in self.rows:
                raise agents.GatewayError("gone", 404, detail="Message not found")
            return _Resp(dict(self.rows[mid]))
        if method == "DELETE":
            mid = path.rsplit("/", 1)[1]
            self.writes.append((method, mid))
            if mid in self.refuse:
                raise agents.GatewayError("refused", 500, detail="provider")
            self.rows[mid]["folder"] = "trash"
            resp = _Resp(None)
            resp.status_code, resp.content = 204, b""
            return resp
        if method == "POST" and path == "/email/messages/summaries":
            return _Resp({"summaries": []})
        if method == "POST" and path == "/email/messages/bulk":
            ids = kw["json"]["message_ids"]
            self.writes.append(("BULK", ",".join(ids)))
            for mid in ids:
                self.rows[mid]["folder"] = "trash"
            return _Resp({"affected": len(ids)})
        raise AssertionError(f"unexpected call {method} {path}")


class Card:
    def __init__(self, answer: Any = True) -> None:
        self.answer = answer
        self.shown: list[dict[str, Any]] = []

    async def __call__(self, **kw: Any) -> Any:
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
    assert f"draft id={MEMBER_DRAFT}" in out
    assert f"draft id={CHAT_DRAFT}" not in out


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
