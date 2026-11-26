"""Task 3978: Discard of a new-mail card asks about the earlier chat draft.

Production, 2026-11-26. A member discarded a new-mail card in the chat. The
draft of that email that the chat saved earlier stayed in Drafts, and the
member saw it there. Now, with ``CHAT_DRAFT_CHOICE_ORGS`` on, Discard asks
"Keep the earlier draft, or delete it?" on the same card
(``email_app_master_plan.md`` §18.13.1).

Each rule names the mutation that turns it red (R7):

1. **Only the member's own "Delete it" on a form with an earlier draft
   deletes.** Mutation: drop the ``earlier`` check or the responder check in
   ``ask_tools`` -> ``test_the_delete_answer_counts_only_for_the_member``.
2. **Flag off is the card of before.** Mutation: drop the ``_draft_choice()``
   check -> ``test_flag_off_asks_nothing``.
3. **No answer, a timeout, a Keep or a close keeps the draft and names it.**
   Mutation: trash on any decline -> ``test_keep_and_no_answer_keep_the_draft``.
4. **A draft that the member changed is never trashed.** Mutation: drop the
   ``target_state`` check -> ``test_a_changed_draft_stays``.
5. **The answer counts only what the provider confirmed.** Mutation: count the
   call -> ``test_an_unconfirmed_trash_never_reads_deleted``.
6. **A draft with a queued send is never asked about.** Mutation: drop the
   ``pending`` check -> ``test_a_draft_with_a_queued_send_asks_nothing``.
7. **The client and the server hold one answer text.** Mutation: change one
   copy -> ``test_the_client_holds_the_same_answer``.
8. **The card of edit_draft asks about a draft that the chat saved** (fix
   round 1, the production path). Mutation: drop ``earlier`` from the
   ``edit_draft`` card, or drop ``_note_reply_draft`` ->
   ``test_edit_card_of_a_reply_draft_asks_and_deletes``.
9. **Each decline of the edit card names the draft and its place.** Mutation:
   return the refusal alone -> ``test_edit_card_declines_say_where_the_draft_is``.
10. **The card states the true effect of a delete.** Mutation: a fixed
    "moves it to Trash" -> ``test_the_card_states_the_effect_of_the_provider``.
11. **A place that was not checked is never claimed.** Mutation: say "stays in
    Drafts" for a gone or unread state -> ``test_an_unchecked_draft_claims_no_place``.
12. **A late "Delete it" is a reject.** Mutation: match an exact REJECT only in
    ``card_answer_agrees`` -> ``test_a_late_delete_answer_reads_as_a_reject``.
13. **A reply draft is never the draft of a new mail.** Mutation: drop the
    ``reply`` check of ``plan_save`` -> ``test_a_reply_draft_is_never_a_new_mail_target``.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import ask_tools as at
from acb_skills import card_revise as cr
from acb_skills import draft_choice as dc
from orchestrator import executor

ROOT = Path(__file__).resolve().parents[2]
_AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"
_REPLY_CARD_TS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "replyCard.ts"
_CARD_ANSWER_TS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "cardAnswer.ts"


def _load():
    spec = importlib.util.spec_from_file_location("ea_draft_decline", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

BOX = "acc-1"
ME = "jane@acme.test"
CHAT_DRAFT = "11111111-1111-4111-8111-111111111111"
MEMBER_DRAFT = "22222222-2222-4222-8222-222222222222"
ORIGINAL = "33333333-3333-4333-8333-333333333333"
_FORM = {
    "kind": "new", "mailbox": "Work · jane@acme.test", "subject": "Proposal",
    "body": "Dear Salil,", "recipients": {"reply": {"to": ["salil@client.test"], "cc": []},
                                          "reply_all": {"to": ["salil@client.test"], "cc": []}},
    "files": [], "notices": [], "note": "",
}


# ── 1. The answer of the card ────────────────────────────────────────────────


def _ask(answer: str | None, *, form: dict[str, Any], responder: str | None = ME,
         monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[Any, str | None, str]:
    """One new-mail card on the native queue path. ``(result, outcome, choice)``.
    *answer* None times the card out."""
    if answer is None:
        async def _late(_fut: Any, _timeout: float, **_kw: Any) -> dict[str, Any]:
            raise TimeoutError
        assert monkeypatch is not None
        monkeypatch.setattr(executor, "wait_user_future", _late)

    async def _tool() -> tuple[Any, str | None, str]:
        result = await at.request_confirmation("Email to salil", "", reply=form)
        return result, at.last_card_outcome(), at.earlier_draft_choice(ME)

    async def _run() -> tuple[Any, str | None, str]:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-3978")
        task = asyncio.ensure_future(_tool())
        if answer is None:
            return await asyncio.wait_for(task, timeout=5)
        card = await asyncio.wait_for(q.get(), timeout=5)
        rid = card["value"]["request_id"]
        assert executor.resolve_user_input(
            rid, answer, was_freeform=False,
            thread_id=executor._pending_user_input.owner_of(rid), responder=responder)
        return await asyncio.wait_for(task, timeout=5)

    return asyncio.run(_run())


def test_the_form_carries_the_earlier_draft() -> None:
    out = at.clean_reply_form({**_FORM, "earlier": "  To salil@client.test:\n Proposal "})
    assert out is not None and out["earlier"] == "To salil@client.test: Proposal"
    assert "earlier" not in (at.clean_reply_form(_FORM) or {})
    assert "earlier" not in (at.clean_reply_form({**_FORM, "earlier": 7}) or {})


def test_the_delete_answer_counts_only_for_the_member(monkeypatch: pytest.MonkeyPatch) -> None:
    form = {**_FORM, "earlier": "To salil@client.test: Proposal"}
    result, outcome, choice = _ask(at.DELETE_EARLIER_ANSWER, form=form)
    assert (result, outcome, choice) == (None, at.CARD_DECLINED, "delete")
    # A form with no earlier draft: the same text is a plain decline.
    assert _ask(at.DELETE_EARLIER_ANSWER, form=_FORM)[2] == ""
    # Another member, or no member, never deletes.
    assert _ask(at.DELETE_EARLIER_ANSWER, form=form, responder="bo@acme.test")[2] == ""
    assert _ask(at.DELETE_EARLIER_ANSWER, form=form, responder=None)[2] == ""
    # A plain Discard (Keep it), and a timeout, keep it.
    assert _ask("REJECT", form=form)[2] == ""
    assert _ask(None, form=form, monkeypatch=monkeypatch)[1:] == (at.CARD_TIMED_OUT, "")


def test_the_client_holds_the_same_answer() -> None:
    src = _REPLY_CARD_TS.read_text(encoding="utf-8")
    assert f"export const DELETE_EARLIER_ANSWER = '{at.DELETE_EARLIER_ANSWER}' as const;" in src
    assert 'EARLIER_QUESTION = "Keep the earlier draft, or delete it?"' in src
    # Fix round 1, item 2: the client draws the server's effect words, and no
    # fixed promise of Trash.
    for effect in dc.DELETE_EFFECTS:
        assert f'"{effect}",' in src
    assert "moves it to Trash" not in src
    # Item 4: the gateway reads a late "Delete it" as the client does.
    from gateway import chat_recovery

    assert chat_recovery.DELETE_EARLIER_ANSWER == at.DELETE_EARLIER_ANSWER
    assert "answer === DELETE_EARLIER_ANSWER" in _CARD_ANSWER_TS.read_text(encoding="utf-8")


def test_clean_earlier_keeps_only_a_known_effect() -> None:
    line = "To salil@client.test: Re: Proposal"
    assert at.clean_earlier({"earlier": line, "earlier_effect": dc.DELETE_EFFECT_GMAIL}) == {
        "earlier": line, "earlier_effect": dc.DELETE_EFFECT_GMAIL}
    # An effect that the server did not choose is dropped, and the line stays.
    assert at.clean_earlier({"earlier": line, "earlier_effect": "Delete moves it to Trash."}) == {
        "earlier": line}
    assert at.clean_earlier({"earlier": "  "}) == {}
    assert at.clean_earlier({"earlier_effect": dc.DELETE_EFFECT_GMAIL}) == {}
    assert at.clean_earlier(None) == {}
    assert dc.delete_effect("gmail") == dc.DELETE_EFFECT_GMAIL
    assert dc.delete_effect("microsoft") == dc.DELETE_EFFECT_OUTLOOK
    assert dc.delete_effect("imap") == dc.delete_effect("") == ""


def _plain(answer: str | None, *, earlier: dict[str, Any] | None,
           responder: str | None = ME, rows: list[dict[str, Any]] | None = None,
           ) -> tuple[Any, dict[str, Any], str]:
    """One PLAIN card on the native queue path: ``(result, event value, choice)``."""
    async def _tool() -> tuple[Any, str]:
        kw: dict[str, Any] = {"earlier": earlier} if earlier is not None else {}
        if rows is not None:
            kw["rows"] = rows
        result = await at.request_confirmation("Save these changes to the draft?", "d", "c", **kw)
        return result, at.earlier_draft_choice(ME)

    async def _run() -> tuple[Any, dict[str, Any], str]:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-3978")
        task = asyncio.ensure_future(_tool())
        card = await asyncio.wait_for(q.get(), timeout=5)
        rid = card["value"]["request_id"]
        assert executor.resolve_user_input(
            rid, answer, was_freeform=False,
            thread_id=executor._pending_user_input.owner_of(rid), responder=responder)
        result, choice = await asyncio.wait_for(task, timeout=5)
        return result, card["value"], choice

    return asyncio.run(_run())


def test_the_plain_card_carries_the_question_and_its_answer() -> None:
    q = {"earlier": "To salil@client.test: Re: Proposal",
         "earlier_effect": dc.DELETE_EFFECT_OUTLOOK}
    result, value, choice = _plain(at.DELETE_EARLIER_ANSWER, earlier=q)
    assert (result, choice) == (False, "delete")
    assert value["earlier"] == q["earlier"] and value["earlier_effect"] == dc.DELETE_EFFECT_OUTLOOK
    # Keep (a plain REJECT), and another member's delete, keep the draft.
    assert _plain("REJECT", earlier=q)[2] == ""
    assert _plain(at.DELETE_EARLIER_ANSWER, earlier=q, responder="bo@acme.test")[2] == ""
    # A card with no question: the same text is a plain decline, and the event
    # is the event of before.
    result, value, choice = _plain(at.DELETE_EARLIER_ANSWER, earlier=None)
    assert (result, choice) == (False, "")
    assert set(value) == {"title", "detail", "context", "request_id"}
    # A card with rows never carries the question.
    rows = [{"id": "r1", "label": "One", "checked": True}]
    _result, value, choice = _plain(at.DELETE_EARLIER_ANSWER, earlier=q, rows=rows)
    assert "earlier" not in value and choice == ""


# ── 2. The tool ──────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.status_code = 200
        self.content = b"x"

    def json(self) -> Any:
        return self._data


def _draft(mid: str, **extra: Any) -> dict[str, Any]:
    return {"id": mid, "account_id": BOX, "folder": "drafts", "thread_id": "T",
            "subject": "Proposal", "received_at": "2026-11-26T06:05:00+00:00",
            "to_addresses": [{"email": "salil@client.test"}], "snippet": "Dear Salil,",
            "has_attachments": False, **extra}


class Mailbox:
    """The gateway routes that the tool calls: one read, the Drafts page, and
    ``DELETE ?report=true``."""

    def __init__(self) -> None:
        self.rows = {CHAT_DRAFT: _draft(CHAT_DRAFT),
                     MEMBER_DRAFT: _draft(MEMBER_DRAFT, has_attachments=True, snippet=""),
                     ORIGINAL: _draft(ORIGINAL, folder="inbox", subject="Proposal")}
        self.deletes: list[str] = []
        self.unconfirmed = False
        self.provider = "gmail"
        self.read_fault: int | None = None

    async def request(self, method: str, path: str, **kw: Any) -> _Resp:
        if method == "GET" and path == "/email/accounts":
            return _Resp([{"id": BOX, "provider": self.provider,
                           "email_address": ME, "display_label": "Work"}])
        if method == "GET" and path == "/email/drafts/scheduled":
            return _Resp([])
        if method == "POST" and path == "/email/draft-reply":
            return _Resp({"draft": "Dear Salil,", "created": True, "draft_id": CHAT_DRAFT,
                          "subject": "Proposal", "to": ["salil@client.test"], "cc": []})
        if method == "GET" and path == "/email/messages":
            rows = [r for r in self.rows.values() if r["folder"] == "drafts"]
            return _Resp({"emails": rows, "total": len(rows)})
        if method == "GET" and path.endswith("/full-body"):
            return _Resp({"body_text": ""})
        if method == "GET" and path.startswith("/email/messages/"):
            if self.read_fault:
                raise agents.GatewayError("Email GET failed", self.read_fault)
            return _Resp(dict(self.rows[path.rsplit("/", 1)[1]]))
        if method == "DELETE":
            mid = path.rsplit("/", 1)[1]
            assert kw.get("params") == {"report": "true"}, "the tool asks for the outcome"
            self.deletes.append(mid)
            self.rows[mid]["folder"] = "trash"
            return _Resp({"id": mid, "discarded": False,
                          "provider": "not_confirmed" if self.unconfirmed else "confirmed"})
        raise AssertionError(f"unexpected call {method} {path}")


class Revise:
    """The one-draft rule (``card_revise``): the state of the earlier draft."""

    OK, GONE, EDITED, FILES = "ok", "gone", "edited", "files"

    def __init__(self, state: str = "ok") -> None:
        self.state = state

    async def target_state(self, draft_id: str, **_kw: Any) -> str:
        assert draft_id == CHAT_DRAFT
        return self.state


@pytest.fixture()
def box(monkeypatch: pytest.MonkeyPatch) -> Mailbox:
    mailbox = Mailbox()
    monkeypatch.setattr(agents, "_request", mailbox.request)
    monkeypatch.setattr(get_settings(), "chat_draft_choice_orgs", "*")
    monkeypatch.setattr(agents, "_current_user_email", lambda: ME)
    monkeypatch.setattr(agents, "_run_thread", lambda: "t-3978")
    monkeypatch.setattr(agents, "_chat_draft_rule", lambda: Revise())
    return mailbox


def _choice(monkeypatch: pytest.MonkeyPatch, choice: str) -> None:
    monkeypatch.setattr(at, "earlier_draft_choice", lambda member: choice if member == ME else "")


_PLAN = agents._ChatPlan(CHAT_DRAFT, to=["salil@client.test"])
_DECLINED = "Declined: nothing was done. The email was not sent."


@pytest.mark.asyncio
async def test_flag_off_asks_nothing(box, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_draft_choice_orgs", "")
    assert await agents._earlier_draft(_PLAN, BOX) is None
    assert agents._with_earlier(dict(_FORM), None) == _FORM
    assert await agents._after_discard(_DECLINED, None) == _DECLINED


@pytest.mark.asyncio
async def test_the_form_names_the_earlier_draft(box):
    earlier = await agents._earlier_draft(_PLAN, BOX)
    assert earlier is not None
    form = agents._with_earlier(dict(_FORM), earlier)
    assert form["earlier"].startswith("To salil@client.test: Proposal")
    # No draft that the one-draft rule names: nothing to ask.
    assert await agents._earlier_draft(agents._ChatPlan(), BOX) is None


@pytest.mark.asyncio
async def test_a_draft_with_a_queued_send_asks_nothing(box):
    queued = agents._ChatPlan(CHAT_DRAFT, [{"draft_id": CHAT_DRAFT, "status": "queued"}])
    assert await agents._earlier_draft(queued, BOX) is None
    assert await agents._earlier_draft(agents._ChatPlan(CHAT_DRAFT, unchecked=True), BOX) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["", "keep"])
async def test_keep_and_no_answer_keep_the_draft(box, monkeypatch, choice):
    earlier = await agents._earlier_draft(_PLAN, BOX)
    _choice(monkeypatch, choice)
    out = await agents._after_discard(_DECLINED, earlier)
    assert box.deletes == []
    assert out.startswith(_DECLINED)
    assert f"stays in Drafts (draft_id {CHAT_DRAFT})" in out
    assert "deleted" not in out.replace("did not ask to delete", "")


@pytest.mark.asyncio
async def test_delete_trashes_the_chat_draft_and_names_what_stays(box, monkeypatch):
    earlier = await agents._earlier_draft(_PLAN, BOX)
    _choice(monkeypatch, "delete")
    out = await agents._after_discard(_DECLINED, earlier)
    assert box.deletes == [CHAT_DRAFT], "only the chat's own draft"
    assert box.rows[MEMBER_DRAFT]["folder"] == "drafts"
    assert out.startswith(_DECLINED + "\nTrashed 1 message(s): 1 draft(s).")
    # The member's draft of the same email stays, and the answer names it.
    assert "1 matching draft(s) are still in Drafts" in out
    assert MEMBER_DRAFT in out.split("are still in Drafts:")[1]


@pytest.mark.asyncio
async def test_an_unconfirmed_trash_never_reads_deleted(box, monkeypatch):
    earlier = await agents._earlier_draft(_PLAN, BOX)
    _choice(monkeypatch, "delete")
    box.unconfirmed = True
    out = await agents._after_discard(_DECLINED, earlier)
    assert "Trashed 0 message(s): the mailbox did not confirm a trash." in out
    assert "Do not say that they were deleted." in out


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["edited", "files"])
async def test_a_changed_draft_stays(box, monkeypatch, state):
    earlier = await agents._earlier_draft(_PLAN, BOX)
    monkeypatch.setattr(agents, "_chat_draft_rule", lambda: Revise(state))
    _choice(monkeypatch, "delete")
    out = await agents._after_discard(_DECLINED, earlier)
    assert box.deletes == []
    assert "It changed after this chat saved it" in out
    assert f"stays in Drafts (draft_id {CHAT_DRAFT})" in out


class _BrokenRevise(Revise):
    async def target_state(self, draft_id: str, **_kw: Any) -> str:
        raise agents.GatewayError("Email GET failed (500)", 500)


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", [Revise("gone"), _BrokenRevise(), None],
                         ids=["gone-or-record-fault", "read-error", "flag-off-while-waiting"])
async def test_an_unchecked_draft_claims_no_place(box, monkeypatch, rule):
    """Fix round 1, item 3: ``target_state`` says ``gone`` also on a record
    read fault, and a read can raise. The flag can go off while the card
    waits. None of them proves a place, so the answer claims none."""
    earlier = await agents._earlier_draft(_PLAN, BOX)
    monkeypatch.setattr(agents, "_chat_draft_rule", lambda: rule)
    _choice(monkeypatch, "delete")
    out = await agents._after_discard(_DECLINED, earlier)
    assert box.deletes == []
    assert out.startswith(_DECLINED), "the decline answer stays, never a tool error"
    assert "This chat deleted nothing" in out and f"draft_id {CHAT_DRAFT}" in out
    assert "look in Drafts" in out
    assert "stays in Drafts" not in out and "left Drafts" not in out
    assert "It changed" not in out


@pytest.mark.asyncio
async def test_the_new_mail_card_asks_and_reports(box, monkeypatch):
    """The path of ``send_email``: the form carries the line, and a Delete on
    the card trashes the draft."""
    shown: list[dict[str, Any]] = []

    async def plan(*_a: Any, **_kw: Any) -> Any:
        return _PLAN

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert path == "/email/new-card"
        return {"card_id": "a" * 32, "to": ["salil@client.test"], "cc": [],
                "subject": "Proposal", "body": "Dear Salil,", "mailbox": "Work"}

    async def ask(form: dict[str, Any], *_a: Any) -> tuple[Any, Any, bool]:
        shown.append(form)
        return _DECLINED, None, False

    monkeypatch.setattr(agents, "_plan_chat_draft", plan)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_ask_until_revised", ask)
    _choice(monkeypatch, "delete")
    out = await agents._new_mail_card(
        BOX, "Work", to=["salil@client.test"], cc=[], bcc=[], subject="Proposal",
        body="Dear Salil,", attachments=None, notices=[], usual="")
    assert shown and shown[0]["earlier"].startswith("To salil@client.test")
    assert box.deletes == [CHAT_DRAFT]
    assert "Trashed 1 message(s)" in out


# ── 3. The card of edit_draft: the production path (fix round 1) ─────────────


class _Redis:
    """Tenant Redis, faked at the seam of ``card_revise``."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, k: Any) -> Any:
        return self.data.get(k.value)

    async def set(self, k: Any, value: Any, **_kw: Any) -> Any:
        self.data[k.value] = value
        return True


_REAL_RULE = agents._chat_draft_rule


@pytest.fixture()
def record(box, monkeypatch):
    """The real one-draft record (``CHAT_CARD_REVISE_ORGS`` on), in a fake Redis."""
    from acb_common.db import bind_tenant, release_tenant

    fake = _Redis()
    monkeypatch.setattr(cr, "get_tenant_redis", lambda: fake)
    monkeypatch.setattr(get_settings(), "chat_card_revise_orgs", "*")
    monkeypatch.setattr(agents, "_chat_draft_rule", _REAL_RULE)
    token = bind_tenant("org-3978")
    yield fake
    release_tenant(token)


def _edit(answer: str | None, *, responder: str | None = ME,
          ) -> tuple[str, dict[str, Any] | None]:
    """``edit_draft`` on the native queue path: ``(answer of the tool, card)``.
    *answer* None runs with no card channel."""
    async def _tool() -> str:
        return await agents.edit_draft(BOX, CHAT_DRAFT, to=["salil@client.test", "kiran@client.test"])

    async def _run() -> tuple[str, dict[str, Any] | None]:
        if answer is None:
            executor._active_run_queue.set(None)
            return await asyncio.wait_for(_tool(), timeout=5), None
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-3978")
        task = asyncio.ensure_future(_tool())
        card = await asyncio.wait_for(q.get(), timeout=5)
        rid = card["value"]["request_id"]
        assert executor.resolve_user_input(
            rid, answer, was_freeform=False,
            thread_id=executor._pending_user_input.owner_of(rid), responder=responder)
        return await asyncio.wait_for(task, timeout=5), card["value"]

    return asyncio.run(_run())


def _saved_by_draft_reply() -> None:
    """The chat saves the reply with draft_reply, as in the report."""
    asyncio.run(agents.draft_reply(ORIGINAL, BOX))


def test_edit_card_of_a_reply_draft_asks_and_deletes(record, box) -> None:
    """Customer report, 2026-11-26: draft_reply saved a draft, and the member
    declined the edit card. Now the card asks, and "Delete it" trashes it."""
    _saved_by_draft_reply()
    out, card = _edit(at.DELETE_EARLIER_ANSWER)
    assert card is not None and card["title"] == "Save these changes to the draft?"
    assert card["earlier"].startswith("To salil@client.test: Proposal")
    assert box.deletes == [CHAT_DRAFT]
    assert "Trashed 1 message(s): 1 draft(s)." in out
    # The member's own draft of the same email stays, and the answer names it.
    assert MEMBER_DRAFT in out.split("are still in Drafts:")[1]


def test_edit_card_keep_says_where_the_draft_is(record, box) -> None:
    _saved_by_draft_reply()
    out, card = _edit("REJECT")
    assert card is not None and "earlier" in card
    assert box.deletes == []
    assert f"stays in Drafts (draft_id {CHAT_DRAFT})" in out


def test_a_reply_draft_is_recorded_as_a_reply(record, box) -> None:
    _saved_by_draft_reply()
    (raw,) = record.data.values()
    assert '"reply": true' in raw and CHAT_DRAFT in raw


@pytest.mark.parametrize("answer", ["REJECT", at.DELETE_EARLIER_ANSWER])
def test_edit_card_declines_say_where_the_draft_is(record, box, answer) -> None:
    """A draft that the chat did not make: no question, never a delete, and
    each decline says plainly that the draft is still in Drafts."""
    out, card = _edit(answer)
    assert card is not None and "earlier" not in card
    assert box.deletes == []
    assert f"The draft stays in Drafts as it was (draft_id {CHAT_DRAFT})" in out


def test_edit_card_with_no_card_channel_keeps_the_draft(record, box) -> None:
    """A WhatsApp run or a run with no card draws no question. The draft stays,
    and the answer says where."""
    _saved_by_draft_reply()
    out, _card = _edit(None)
    assert box.deletes == []
    assert f"stays in Drafts (draft_id {CHAT_DRAFT})" in out


@pytest.mark.asyncio
async def test_edit_card_closed_by_a_message_keeps_the_draft(record, box, monkeypatch) -> None:
    async def closed(*_a: Any, **kw: Any) -> Any:
        assert kw.get("earlier"), "the card asked"
        return at._noted(at.CLOSED_BY_YOUR_MESSAGE, at.REVISED)

    await agents.draft_reply(ORIGINAL, BOX)
    monkeypatch.setattr(agents, "_confirm_destructive", closed)
    _choice(monkeypatch, "")
    out = await agents.edit_draft(BOX, CHAT_DRAFT, to=["salil@client.test"])
    assert box.deletes == []
    assert f"stays in Drafts (draft_id {CHAT_DRAFT})" in out


def test_edit_card_flag_off_is_the_card_of_before(record, box, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "chat_draft_choice_orgs", "")
    _saved_by_draft_reply()
    assert record.data == {}, "flag off: draft_reply records nothing new"
    out, card = _edit(at.DELETE_EARLIER_ANSWER)
    assert card is not None and set(card) == {"title", "detail", "context", "request_id"}
    assert box.deletes == []
    assert "Drafts" not in out and CHAT_DRAFT not in out


@pytest.mark.parametrize("provider,effect", [
    ("gmail", dc.DELETE_EFFECT_GMAIL), ("microsoft", dc.DELETE_EFFECT_OUTLOOK), ("imap", None)])
def test_the_card_states_the_effect_of_the_provider(record, box, provider, effect) -> None:
    """Fix round 1, item 2: Gmail deletes a trashed draft for good. The card
    says the true effect, and promises nothing for a provider it cannot name."""
    box.provider = provider
    _saved_by_draft_reply()
    _out, card = _edit("REJECT")
    assert card is not None and card.get("earlier_effect") == effect


@pytest.mark.asyncio
async def test_a_reply_draft_is_never_a_new_mail_target(record, box) -> None:
    await agents.draft_reply(ORIGINAL, BOX)
    did = await cr.plan_save(act=cr.NEW_MAIL, thread="t-3978", account_id=BOX, member=ME,
                             to=["salil@client.test"], subject="Proposal")
    assert did == ""
    # An approved edit rewrites the entry, and it stays a reply draft.
    await cr.note_draft(thread="t-3978", account_id=BOX, member=ME, draft_id=CHAT_DRAFT,
                        to=["salil@client.test"], subject="Proposal", digest="x",
                        replaces=CHAT_DRAFT, only_if_known=True)
    assert await cr.plan_save(act=cr.NEW_MAIL, thread="t-3978", account_id=BOX, member=ME,
                              to=["salil@client.test"], subject="Proposal") == ""


# ── 4. The held card ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_held_form_card_asks_and_deletes(box, monkeypatch) -> None:
    """``_held_card`` -> ``_held_form_card`` with ``earlier``: the held form
    carries the question, and a Delete on it trashes the chat's draft."""
    shown: list[dict[str, Any]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert path == "/email/held-card"
        return {"card_id": "b" * 32, "form": dict(_FORM)}

    async def ask(form: dict[str, Any], *_a: Any) -> tuple[Any, Any, bool]:
        shown.append(form)
        return _DECLINED, None, False

    monkeypatch.setattr(at, "reply_card_channel_open", lambda: True)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_ask_until_revised", ask)
    _choice(monkeypatch, "delete")
    out = await agents._held_card(
        {"kind": "new", "account_id": BOX}, "Not sent", "The email was not sent.",
        form=True, plan=_PLAN)
    assert shown and shown[0]["earlier"].startswith("To salil@client.test")
    assert shown[0]["earlier_effect"] == dc.DELETE_EFFECT_GMAIL
    assert box.deletes == [CHAT_DRAFT]
    assert "Trashed 1 message(s)" in out


# ── 5. A late answer ─────────────────────────────────────────────────────────


def test_a_late_delete_answer_reads_as_a_reject() -> None:
    """Fix round 1, item 4: a late "Delete it" goes as a new message. The chat
    draws it as a reject, and the display agrees with the envelope."""
    from gateway.chat_recovery import card_answer_agrees, card_answer_display, compose_card_answer

    q = "Save these changes to the draft?"
    envelope = compose_card_answer(q, at.DELETE_EARLIER_ANSWER)
    display = card_answer_display("confirmation", {}, q, at.DELETE_EARLIER_ANSWER, "r1")
    assert display["decision"] == "reject"
    assert card_answer_agrees(envelope, display)
    # The event left the stream: the answer alone still reads as a confirmation.
    assert card_answer_display("", None, q, at.DELETE_EARLIER_ANSWER, "r1")["decision"] == "reject"
    # Any other text after REJECT is no reject.
    assert not card_answer_agrees(compose_card_answer(q, 'REJECT {"earlier":"x"}'), display)
