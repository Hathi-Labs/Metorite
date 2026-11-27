"""Task 4015: the read of the reply card, and the write that it describes.

Spec: ``project-docs/specs/chat_parity.md`` §6.4, P-1b.

``POST /tasks/capture/from-reply/preview`` reads the sent mail that
``/from-reply/create`` hangs the task on. The chat card shows that mail. The
rules that this file fences (R7), with fakes only:

1. The read and the write share ONE read of the sent mail (``_sent_mail``),
   with the same member, mailbox and thread.
2. The read checks the mailbox first (``_account_owner``). A thread with no
   message there is a 404. A thread with no sent mail gives no id.
3. The recipients are addresses only, To then Cc then Bcc, each once.
4. A write with no subject keeps the subject of the stored mail. A write that
   names the mail of its card refuses another mail with a 409, before it writes.

The R8 suite of the SQL is ``test_capture_reply_thread_r8.py``.

Run: ``uv run pytest tests/unit/test_capture_reply_thread.py -q``.
"""
from __future__ import annotations

import contextlib
import json
import types
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.tasks import capture_email as ce

ME = "member@x.test"
AID, TID = "a1", "thread-1"


def _row(**kw: Any) -> types.SimpleNamespace:
    base = {"id": "s1", "subject": "Re: the deck", "to_addresses": "[]",
            "cc_addresses": "[]", "bcc_addresses": "[]"}
    return types.SimpleNamespace(**{**base, **kw})


@pytest.fixture
def gw(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The fakes of the gateway module. Each records its call."""
    state: dict[str, Any] = {"sent": _row(), "thread": True, "owner": True,
                             "calls": [], "writes": []}

    async def owner(_db, uid, account_id):
        state["calls"].append(("owner", uid, account_id))
        if not state["owner"]:
            raise HTTPException(status_code=404, detail="Account not found")
        return "member", ME

    async def sent(_db, uid, account_id, thread_id):
        state["calls"].append(("sent", uid, account_id, thread_id))
        return state["sent"]

    async def thread(_db, uid, account_id, thread_id):
        state["calls"].append(("thread", uid, account_id, thread_id))
        return state["thread"]

    async def none(*_a, **_k):
        return None

    async def people(_db):
        return []

    async def persist(_db, uid, email, draft, _people):
        state["writes"].append({"id": email.id, "subject": email.subject})
        return "item-1", "NEXT", None

    class Source:
        async def update_origin(self, *_a, **_k):
            return None

        async def fetch_item(self, *_a, **_k):
            return {"id": "item-1"}

    monkeypatch.setattr(ce, "_tenant_session", lambda: contextlib.nullcontext())
    monkeypatch.setattr(ce, "_uid", lambda _user: ME)
    monkeypatch.setattr(ce, "_account_owner", owner)
    monkeypatch.setattr(ce, "_sent_mail", sent)
    monkeypatch.setattr(ce, "_thread_in_mailbox", thread)
    monkeypatch.setattr(ce, "_find_existing_commitment", none)
    monkeypatch.setattr(ce, "_route_and_persist", persist)
    monkeypatch.setattr(ce, "_tag_thread_task_category", none)
    monkeypatch.setattr(ce, "item_source", lambda: Source())
    monkeypatch.setattr(ce, "_row_to_item", lambda _r: types.SimpleNamespace(
        disposition="NEXT", assignee=None, due_at=None))
    monkeypatch.setattr(ce, "CaptureFromEmailResponse",
                        lambda **kw: types.SimpleNamespace(**kw))
    import gateway.routes.tasks.people as people_mod

    monkeypatch.setattr(people_mod, "fetch_people_for_clarify", people)
    return state


async def _preview() -> ce.ReplyThreadResponse:
    return await ce.preview_commitment_thread(
        ce.ReplyThreadRequest(account_id=AID, thread_id=TID), user=object())


async def _create(**kw: Any) -> Any:
    return await ce.create_commitment_from_reply(ce.CreateCommitmentRequest(
        account_id=AID, thread_id=TID, draft=ce.CaptureDraftModel(title="Send it"), **kw),
        user=object())


# ── 1. One read for the card and the write ──────────────────────────────────


async def test_the_card_read_and_the_write_read_the_same_sent_mail(gw) -> None:
    """Mutation: give the write its own SQL, or read with other ids -> red."""
    await _preview()
    await _create()
    sent = [c for c in gw["calls"] if c[0] == "sent"]
    assert sent == [("sent", ME, AID, TID)] * 2
    assert gw["writes"][0]["id"] == "s1"


async def test_the_read_checks_the_mailbox_first(gw) -> None:
    """Mutation: read the thread before the owner check -> red."""
    gw["owner"] = False
    with pytest.raises(HTTPException) as err:
        await _preview()
    assert err.value.status_code == 404
    assert gw["calls"] == [("owner", ME, AID)]


async def test_a_thread_with_no_message_is_a_404(gw) -> None:
    gw["sent"], gw["thread"] = None, False
    with pytest.raises(HTTPException) as err:
        await _preview()
    assert err.value.status_code == 404 and err.value.detail == "Thread not found"


async def test_a_thread_with_no_sent_mail_gives_no_id(gw) -> None:
    """Mutation: answer a 404, or fill an id -> red."""
    gw["sent"] = None
    got = await _preview()
    assert (got.email_id, got.subject, got.recipients) == ("", "", [])


async def test_the_read_gives_the_stored_values(gw) -> None:
    gw["sent"] = _row(id="s9", subject=None,
                      to_addresses=json.dumps([{"name": "Ravi", "email": "ravi@x.test"}]))
    got = await _preview()
    assert (got.email_id, got.subject, got.recipients) == ("s9", "", ["ravi@x.test"])


# ── 2. The recipients ───────────────────────────────────────────────────────


def test_the_recipients_are_addresses_in_order_and_once() -> None:
    """Mutation: show a name, drop Cc or Bcc, keep a repeat, or clean an
    address -> red."""
    row = _row(
        to_addresses=json.dumps([{"name": "ceo@company.test", "email": "m@evil.test"},
                                 {"name": "Priya", "email": "Priya@X.test"}]),
        cc_addresses=[{"email": "priya@x.test"}, {"name": "No address"}, {"email": ""},
                      {"email": 7}, "junk", {"email": " ravi@x.test"}],
        bcc_addresses="not json")
    assert ce._recipient_addresses(row) == ["m@evil.test", "Priya@X.test", " ravi@x.test"]


def test_bcc_is_a_recipient_too() -> None:
    row = _row(bcc_addresses=json.dumps([{"email": "boss@x.test"}]))
    assert ce._recipient_addresses(row) == ["boss@x.test"]


# ── 3. The write keeps what the card showed ─────────────────────────────────


async def test_a_write_with_no_subject_keeps_the_stored_subject(gw) -> None:
    """The chat sends no subject. Mutation: store the empty value -> red."""
    await _create(subject="", expect_email_id="s1")
    assert gw["writes"] == [{"id": "s1", "subject": "Re: the deck"}]


async def test_a_write_with_a_subject_keeps_it(gw) -> None:
    """The app popup sends the subject of the reply that it just sent, which
    may not be stored yet. Mutation: drop the request subject -> red."""
    gw["sent"] = None
    await _create(subject="Re: quote")
    assert gw["writes"] == [{"id": f"reply:{TID}", "subject": "Re: quote"}]


@pytest.mark.parametrize("expect", ["s2", "", f"reply:{TID}"])
async def test_a_thread_that_changed_is_a_409_before_any_write(gw, expect) -> None:
    """Mutation: drop the check, or write first -> red."""
    with pytest.raises(HTTPException) as err:
        await _create(expect_email_id=expect)
    assert err.value.status_code == 409 and gw["writes"] == []


async def test_a_thread_that_lost_its_sent_mail_is_a_409(gw) -> None:
    gw["sent"] = None
    with pytest.raises(HTTPException) as err:
        await _create(expect_email_id="s1")
    assert err.value.status_code == 409 and gw["writes"] == []


async def test_no_expected_id_checks_nothing(gw) -> None:
    """The app popup sends none. Mutation: refuse a write with no id -> red."""
    gw["sent"] = None
    await _create()
    assert gw["writes"] == [{"id": f"reply:{TID}", "subject": ""}]
