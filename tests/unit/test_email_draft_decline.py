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
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import ask_tools as at
from orchestrator import executor

ROOT = Path(__file__).resolve().parents[2]
_AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"
_REPLY_CARD_TS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "replyCard.ts"


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
                     MEMBER_DRAFT: _draft(MEMBER_DRAFT, has_attachments=True, snippet="")}
        self.deletes: list[str] = []
        self.unconfirmed = False

    async def request(self, method: str, path: str, **kw: Any) -> _Resp:
        if method == "GET" and path == "/email/messages":
            rows = [r for r in self.rows.values() if r["folder"] == "drafts"]
            return _Resp({"emails": rows, "total": len(rows)})
        if method == "GET" and path.startswith("/email/messages/"):
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
@pytest.mark.parametrize("state,why", [
    ("edited", "It changed after this chat saved it"),
    ("files", "It changed after this chat saved it"),
    ("gone", "It left Drafts before the delete"),
])
async def test_a_changed_draft_stays(box, monkeypatch, state, why):
    earlier = await agents._earlier_draft(_PLAN, BOX)
    monkeypatch.setattr(agents, "_chat_draft_rule", lambda: Revise(state))
    _choice(monkeypatch, "delete")
    out = await agents._after_discard(_DECLINED, earlier)
    assert box.deletes == []
    assert why in out and f"draft_id {CHAT_DRAFT}" in out


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
