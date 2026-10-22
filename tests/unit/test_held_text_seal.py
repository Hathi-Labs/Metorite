"""A draft card binds its text, not only its recipients (§21 fix round 1, P1-b).

The review found a held run that approves a harmless ``send_draft`` or
``schedule_send`` card, then changes the text with ``edit_draft`` (whose card
says "Nothing is sent"). The send then mailed the new text. This holds with
the held-card switch OFF too (H-374).

The fix seals the text that the card showed: the subject, the stored text and
the live text at the provider (``drafting.draft_text_seal``). ``expect``
carries the seal, and the send checks it before any provider write. A schedule
stores it and fires through the same send.

R7, each with the mutation that turns it red:

* ``test_the_seal_*`` — drop a part of the seal.
* ``test_an_edit_after_the_card_answers_409_and_sends_nothing`` — drop the
  check in ``send_draft_endpoint``.
* ``test_a_changed_provider_body_sends_nothing`` — seal the stored text only.
* ``test_a_failed_provider_read_sends_nothing`` — let a failed read pass.
* ``test_a_schedule_fires_with_its_seal`` and
  ``test_a_changed_text_fails_the_schedule_with_a_reason`` — drop the key from
  the stored ``expect``, or the reason.
* ``test_the_tools_send_the_seal`` — post ``expect`` without it.
* ``test_an_edit_cancels_a_pending_schedule`` — drop the cancel.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.email.automation import drafting as d
from gateway.routes.email.automation import send_later as sl

from tests.unit import _held_card_fakes as F


class _Provider:
    def __init__(self, live: str = "Hi Kim,\n\nThe quote.") -> None:
        self.live = live
        self.sent: list[str] = []
        self.updated: list[Any] = []
        self.fail = False

    async def get_message_body(self, _pmid: str) -> Any:
        if self.fail:
            raise RuntimeError("down")
        return SimpleNamespace(body_text=self.live, body_html="")

    async def get_draft_recipients(self, _pmid: str) -> dict[str, list[str]]:
        return {"to": [F.KNOWN], "cc": [], "bcc": []}

    async def send_draft(self, pmid: str) -> None:
        self.sent.append(pmid)

    async def update_draft(self, *a: Any, **k: Any) -> str:
        self.updated.append(k)
        return "pm-1"


def _drow(**over: Any) -> SimpleNamespace:
    row = {"provider_message_id": "pm-1", "subject": "Quote",
           "to_addresses": [{"email": F.KNOWN}], "cc_addresses": [], "bcc_addresses": [],
           "body_text": "Hi Kim,\n\nThe quote.", "thread_id": None}
    row.update(over)
    return SimpleNamespace(**row)


async def _seal(drow: SimpleNamespace, provider: _Provider) -> str:
    return await d.draft_text_seal(provider, "pm-1", drow.subject, drow.body_text, "")


async def _send(drow: SimpleNamespace, provider: _Provider, digest: str | None) -> None:
    class _Db:
        async def execute(self, sql: Any, params: Any = None) -> Any:
            q = str(sql)
            if "FROM email_assistant_settings" in q:
                return SimpleNamespace(fetchone=lambda: SimpleNamespace(signature=""))
            if q.startswith("DELETE"):
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace(fetchone=lambda: drow)

    @contextlib.asynccontextmanager
    async def tenant():
        yield _Db()

    @contextlib.asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any):
        yield SimpleNamespace(provider=provider)

    async def owner(*_a: Any, **_k: Any) -> None:
        return None

    req = d.DraftSendRequest(account_id=F.BOX, draft_id=F.DRAFT, expect={
        "to": [F.KNOWN], "cc": [], "bcc": [], "text_digest": digest})
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(d, "_tenant_session", tenant)
        mp.setattr(d, "provider_session", provider_session)
        mp.setattr(d, "_assert_account_owner", owner)
        await d.send_draft_endpoint(req, SimpleNamespace(add_task=lambda *a, **k: None),
                                    user=SimpleNamespace(email=F.MEMBER))
    finally:
        mp.undo()


async def test_the_seal_holds_through_a_reflow_and_changes_with_a_word() -> None:
    p = _Provider()
    base = await _seal(_drow(), p)
    assert base == await _seal(_drow(body_text="Hi  Kim,\n\n\nThe   quote. "), p)
    assert base != await _seal(_drow(body_text="Hi Kim,\n\nThe quote. Also x@evil."), p)
    assert base != await _seal(_drow(subject="Quote, and the payroll"), p)
    assert base != await _seal(_drow(), _Provider(live="Hi Kim, the payroll."))


async def test_a_matching_seal_sends() -> None:
    p = _Provider()
    await _send(_drow(), p, await _seal(_drow(), p))
    assert p.sent == ["pm-1"]


async def test_an_edit_after_the_card_answers_409_and_sends_nothing() -> None:
    p = _Provider()
    shown = await _seal(_drow(), p)
    edited = _drow(body_text="Hi Kim,\n\nPlease see the payroll attached.")
    p.live = edited.body_text
    with pytest.raises(HTTPException) as e:
        await _send(edited, p, shown)
    assert e.value.status_code == 409 and e.value.detail == d.DRAFT_TEXT_CHANGED_DETAIL
    assert p.sent == [] and p.updated == []


async def test_a_changed_provider_body_sends_nothing() -> None:
    """The stored row still holds the old text, and the provider draft holds
    a new one (an edit outside Metorite)."""
    p = _Provider()
    shown = await _seal(_drow(), p)
    p.live = "Hi Kim,\n\nSomething else."
    with pytest.raises(HTTPException) as e:
        await _send(_drow(), p, shown)
    assert e.value.status_code == 409
    assert p.sent == []


async def test_a_failed_provider_read_sends_nothing() -> None:
    p = _Provider()
    shown = await _seal(_drow(), p)
    p.fail = True
    with pytest.raises(HTTPException) as e:
        await _send(_drow(), p, shown)
    assert e.value.status_code == 502
    assert p.sent == []


async def test_an_old_expect_with_no_seal_still_sends() -> None:
    """R6: a schedule stored before the seal carries no key, and it checks
    the recipients only, as before."""
    p = _Provider()
    await _send(_drow(), p, None)
    assert p.sent == ["pm-1"]
    assert d.DraftExpect(**{"to": [], "cc": [], "bcc": []}).text_digest is None


async def test_a_schedule_fires_with_its_seal(monkeypatch) -> None:
    got: list[Any] = []

    async def endpoint(req: Any, background: Any, user: Any) -> None:
        got.append(req.expect)

    async def member(owner: str, org: str) -> Any:
        return SimpleNamespace(email=owner)

    monkeypatch.setattr(sl, "send_draft_endpoint", endpoint)
    monkeypatch.setattr(sl, "_member", member)
    row = SimpleNamespace(account_id=F.BOX, draft_id=F.DRAFT, owner_email=F.MEMBER,
                          expect={"to": [F.KNOWN], "cc": [], "bcc": [], "text_digest": "d" * 64})
    why, _tasks = await sl._send_one(F.ORG, row)
    assert why is None and got[0].text_digest == "d" * 64


def test_a_changed_text_fails_the_schedule_with_a_reason() -> None:
    why = sl._why(HTTPException(status_code=409, detail=d.DRAFT_TEXT_CHANGED_DETAIL))
    assert "text" in why and "not sent" in why


def test_the_schedule_request_stores_the_seal() -> None:
    req = sl.ScheduleRequest(account_id=F.BOX, draft_id=F.DRAFT, send_at="x",
                             expect={"to": [F.KNOWN], "text_digest": "e" * 64})
    assert req.expect.model_dump()["text_digest"] == "e" * 64


# ── The tools ────────────────────────────────────────────────────────────────


@pytest.fixture()
def tool(monkeypatch):
    from acb_skills import ask_tools
    from acb_skills.write_artifact import bind_artifact_context

    F.flag_off(monkeypatch)
    agents = F.load_email_agent("ea_held_seal")
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1")
    calls: dict[str, list[Any]] = {"post": [], "put": [], "cards": []}
    state = {"scheduled": []}

    async def get(path: str, params: Any = None) -> Any:
        if path == f"/email/messages/{F.DRAFT}":
            return {"account_id": F.BOX, "folder": "drafts", "subject": "Quote",
                    "to_addresses": [{"email": F.KNOWN}], "body_text": "Hi"}
        if path == "/email/drafts/seal":
            return {"text_digest": "f" * 64, "subject": "Quote", "body_text": "Hi Kim"}
        if path == "/email/drafts/scheduled":
            return state["scheduled"]
        if path == f"/email/messages/{F.DRAFT}/full-body":
            return {"body_text": "Hi"}
        raise AssertionError(path)

    async def post(path: str, body: Any) -> Any:
        calls["post"].append((path, body))
        return {"id": "s-1"}

    async def put(path: str, body: Any) -> Any:
        calls["put"].append((path, body))
        return {}

    async def mailbox(_aid: str) -> str:
        return F.MEMBER

    async def card(**kw: Any) -> bool:
        calls["cards"].append(kw)
        return True

    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_put", put)
    monkeypatch.setattr(agents, "_mailbox_name", mailbox)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return agents, calls, state


async def test_the_tools_send_the_seal(tool) -> None:
    agents, calls, _state = tool
    assert await agents.send_draft(F.BOX, F.DRAFT) == "Draft sent."
    path, body = calls["post"][-1]
    assert path == "/email/drafts/send" and body["expect"]["text_digest"] == "f" * 64
    # The card shows the text that the seal covers.
    assert "Hi Kim" in calls["cards"][-1]["context"]
    from datetime import UTC, datetime, timedelta

    when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    out = await agents.schedule_send(F.BOX, F.DRAFT, when)
    assert out.startswith("Scheduled")
    path, body = calls["post"][-1]
    assert path == "/email/drafts/schedule" and body["expect"]["text_digest"] == "f" * 64


async def test_an_edit_cancels_a_pending_schedule(tool) -> None:
    agents, calls, state = tool
    state["scheduled"] = [{"id": "s-9", "draft_id": F.DRAFT, "status": "queued",
                           "send_at": "2026-10-23T09:00:00+05:30"},
                          {"id": "s-8", "draft_id": "other", "status": "queued", "send_at": "x"}]
    out = await agents.edit_draft(F.BOX, F.DRAFT, body="Please see the payroll.")
    assert "This cancels the send scheduled for 2026-10-23T09:00:00+05:30." in (
        calls["cards"][-1]["context"])
    assert ("/email/drafts/scheduled/s-9/cancel", {}) in calls["post"]
    assert not any("s-8" in p for p, _ in calls["post"])
    assert "cancelled" in out


async def test_an_edit_with_no_schedule_cancels_nothing(tool) -> None:
    agents, calls, _state = tool
    await agents.edit_draft(F.BOX, F.DRAFT, body="New text.")
    assert "This cancels" not in calls["cards"][-1]["context"]
    assert calls["post"] == []
