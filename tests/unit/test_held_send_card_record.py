"""The card record of a held send (``email_app_master_plan.md`` §21.4).

A held chat read mail from outside senders. Its sends go through one card
that the gateway builds and RECORDS. The send reads the recorded rows, and
never the call of the model or the request of the browser.

What this file fences (R7). Each test names a mutation that turns it red:

1. The gateway mints the card id and records the offered rows. The rows that
   the card shows are the recorded ones. ``test_the_card_*``.
2. The send reads the recorded rows. An id that the record does not hold
   sends nothing. ``test_an_id_*``.
3. An unticked row is not sent, also when the draft holds it.
   ``test_an_unticked_row_*``.
4. One answer acts once. A second answer, another member, another thread
   and a forged answer each send nothing. ``test_a_second_*``,
   ``test_another_*``, ``test_a_forged_*``.
5. The answer of a held card is recorded only while the switch is on, and
   each route answers 404 while it is off. ``test_*_switch_*``.
6. ``request_confirmation`` takes the gateway's card id and hands back the
   exact answer. ``test_request_confirmation_*``.

The database, Redis and the provider are fakes. No test sends a mail.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import held_card as hc
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    F.flag_on(monkeypatch)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    redis = F.install_redis(monkeypatch)
    db = F.install_db(monkeypatch)
    sends = F.install_sends(monkeypatch)
    return {"redis": redis, "db": db, "sends": sends}


async def _card(kind: str = "draft", **kw: Any) -> dict[str, Any]:
    req = hc.HeldCardRequest(kind=kind, account_id=F.BOX, draft_id=F.DRAFT, **kw)
    return await hc.held_card(req, F.user())


async def _act(card_id: str, answer: str, *, who: str = F.MEMBER,
               thread: str = "chat-thread-1") -> dict[str, Any]:
    req = hc.HeldCardActRequest(card_id=card_id, card_thread_id=thread, answer=answer)
    return await hc.held_card_act(req, BackgroundTasks(), F.user(who))


def _soon() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(days=2)).isoformat()


def _ids(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["label"].split(": ", 1)[1]: r for r in card["rows"]}


# ── 1. The card is built and recorded by the server ──────────────────────────


async def test_the_card_classes_each_person_and_starts_a_new_one_unticked(_world) -> None:
    """Mutation: tick every row in ``recipient_rows`` → red."""
    card = await _card()
    rows = _ids(card)
    assert rows[F.KNOWN]["class"] == "sent" and rows[F.KNOWN]["checked"] is True
    assert rows[F.COLLEAGUE]["class"] == "org" and rows[F.COLLEAGUE]["checked"] is True
    assert rows[F.STRANGER]["class"] == "new" and rows[F.STRANGER]["checked"] is False
    assert rows[F.STRANGER]["hint"] == "new address — not in your contacts"
    assert len(card["card_id"]) == 32
    # The record holds exactly the rows that the card shows, with addresses.
    rec = json.loads(next(iter(_world["redis"].keys.values())))
    assert [r["id"] for r in rec["rows"]] == [r["id"] for r in card["rows"]]
    assert {r["address"] for r in rec["rows"]} == {F.KNOWN, F.COLLEAGUE, F.STRANGER}
    assert rec["owner"] == F.MEMBER and rec["kind"] == "draft"


async def test_the_card_shows_the_whole_text_or_nothing(_world) -> None:
    """Open question 8, the safer choice. Mutation: cut the text → red."""
    _world["db"].draft["body_text"] = "x " * 2000
    with pytest.raises(HTTPException) as e:
        await _card()
    assert e.value.status_code == 422 and "longer than" in e.value.detail
    assert _world["redis"].keys == {}


async def test_the_request_takes_no_recipient_and_no_class() -> None:
    """The model names no recipient and no class. Mutation: drop
    ``extra="forbid"`` → red."""
    for extra in ({"to": [F.STRANGER]}, {"class": "org"}, {"rows": []},
                  {"bcc": [F.STRANGER]}, {"classes": {F.STRANGER: "org"}}):
        with pytest.raises(ValidationError):
            hc.HeldCardRequest(kind="draft", account_id=F.BOX, draft_id=F.DRAFT, **extra)
    for extra in ({"to": [F.STRANGER]}, {"rows": ["to-0"]}, {"body": "x"}):
        with pytest.raises(ValidationError):
            hc.HeldCardActRequest(card_id="a" * 32, card_thread_id="t", answer="x", **extra)


# ── 2-3. The send reads the recorded rows only ───────────────────────────────


async def test_the_send_takes_the_ticked_recorded_rows(_world) -> None:
    card = await _card()
    rows = _ids(card)
    answer = F.rows_answer(rows[F.KNOWN]["id"], rows[F.COLLEAGUE]["id"])
    assert await F.record_answer(card["card_id"], answer)
    out = await _act(card["card_id"], answer)
    assert out["to"] == [F.KNOWN] and out["cc"] == [F.COLLEAGUE]
    sent = _world["sends"].sent
    assert len(sent) == 1
    assert sent[0].expect.model_dump() == {"to": [F.KNOWN], "cc": [F.COLLEAGUE], "bcc": [],
                                         "text_digest": "a" * 64}


async def test_an_unticked_row_is_not_sent_also_when_the_draft_holds_it(_world) -> None:
    """The stranger is on the draft and the member left it unticked. The
    draft loses it before the send. Mutation: send ``rec["held"]`` → red."""
    card = await _card()
    rows = _ids(card)
    answer = F.rows_answer(rows[F.KNOWN]["id"], rows[F.COLLEAGUE]["id"])
    await F.record_answer(card["card_id"], answer)
    await _act(card["card_id"], answer)
    assert _world["sends"].narrowed == [{"to": [F.KNOWN], "cc": [F.COLLEAGUE], "bcc": []}]
    assert F.STRANGER not in json.dumps(_world["sends"].sent[0].expect.model_dump())


async def test_a_ticked_new_row_goes(_world) -> None:
    """The member ticked the new address: it goes, and nothing is narrowed."""
    card = await _card()
    ids = [r["id"] for r in card["rows"]]
    answer = F.rows_answer(*ids)
    await F.record_answer(card["card_id"], answer)
    out = await _act(card["card_id"], answer)
    assert F.STRANGER in out["cc"]
    assert _world["sends"].narrowed == []


async def test_an_id_that_the_record_does_not_hold_sends_nothing(_world) -> None:
    """Mutation: trim the answer to the ids that fit → red."""
    card = await _card()
    answer = F.rows_answer(_ids(card)[F.KNOWN]["id"], "bcc-9")
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer)
    assert e.value.status_code == 409
    assert _world["sends"].sent == []


async def test_a_record_of_another_member_sends_nothing(_world) -> None:
    """Mutation: drop the owner check of ``take_record`` → red."""
    card = await _card()
    answer = F.rows_answer(_ids(card)[F.KNOWN]["id"])
    await F.record_answer(card["card_id"], answer, who=F.OTHER)
    # The answer is on record for the other member, and the record is not.
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer, who=F.OTHER)
    assert e.value.status_code == 409
    assert _world["sends"].sent == []


# ── 4. One answer acts once ──────────────────────────────────────────────────


async def test_a_second_answer_sends_nothing(_world) -> None:
    """The browser can post the answer again, and the card record is gone,
    so the second act sends nothing. Mutation: read the card record with GET
    in place of GETDEL → red."""
    card = await _card()
    answer = F.rows_answer(_ids(card)[F.KNOWN]["id"])
    await F.record_answer(card["card_id"], answer)
    await _act(card["card_id"], answer)
    assert await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer)
    assert e.value.status_code == 409
    assert len(_world["sends"].sent) == 1


async def test_another_member_sends_nothing(_world) -> None:
    card = await _card()
    answer = F.rows_answer(_ids(card)[F.KNOWN]["id"])
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer, who=F.OTHER)
    assert e.value.status_code == 409
    assert _world["sends"].sent == []


async def test_another_thread_sends_nothing(_world) -> None:
    card = await _card()
    answer = F.rows_answer(_ids(card)[F.KNOWN]["id"])
    await F.record_answer(card["card_id"], answer, thread="chat-thread-1")
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer, thread="chat-thread-2")
    assert e.value.status_code == 409
    assert _world["sends"].sent == []


async def test_a_forged_answer_sends_nothing(_world) -> None:
    """The browser posted one answer, and the act names another (a forged
    digest). Mutation: skip ``hmac.compare_digest`` → red."""
    card = await _card()
    rows = _ids(card)
    await F.record_answer(card["card_id"], F.rows_answer(rows[F.KNOWN]["id"]))
    forged = F.rows_answer(rows[F.KNOWN]["id"], rows[F.STRANGER]["id"])
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], forged)
    assert e.value.status_code == 409
    assert _world["sends"].sent == []


async def test_a_bcc_to_a_new_address_is_refused(_world) -> None:
    """Open question 5, the safer choice. Mutation: drop ``_refuse_new_bcc``."""
    _world["db"].draft["bcc_addresses"] = json.dumps([{"email": F.STRANGER}])
    with pytest.raises(HTTPException) as e:
        await _card()
    assert e.value.status_code == 422 and "Bcc" in e.value.detail
    assert _world["redis"].keys == {}


async def test_a_lookalike_domain_carries_a_warning(_world) -> None:
    """Open question 4, the safer choice. Mutation: drop the warning → red."""
    _world["db"].draft["cc_addresses"] = json.dumps([{"email": F.LOOKALIKE}])
    card = await _card()
    row = _ids(card)[F.LOOKALIKE]
    assert row["class"] == "new" and row["checked"] is False
    assert "contoso.test" in row["warning"]


async def test_the_schedule_stores_only_the_ticked_set(_world, monkeypatch) -> None:
    from gateway.routes.email.automation import send_later as sl

    monkeypatch.setattr(sl, "send_later_enabled", lambda: True)
    stored: list[Any] = []

    async def schedule(req: Any, user: Any) -> Any:
        stored.append(req)
        return sl.ScheduleRow(id="s-1", account_id=F.BOX, draft_id=F.DRAFT,
                              send_at=req.send_at, status="queued")

    monkeypatch.setattr(sl, "schedule_draft", schedule)
    card = await _card("schedule", send_at=_soon())
    assert card["title"].startswith("Send this draft at")
    answer = F.rows_answer(_ids(card)[F.KNOWN]["id"])
    await F.record_answer(card["card_id"], answer)
    out = await _act(card["card_id"], answer)
    assert out["action"] == "schedule"
    assert stored[0].expect.model_dump() == {"to": [F.KNOWN], "cc": [], "bcc": [],
                                           "text_digest": "a" * 64}
    assert _world["sends"].sent == []


# ── 5. The switch ────────────────────────────────────────────────────────────


async def test_each_route_is_a_404_while_the_switch_is_off(_world, monkeypatch) -> None:
    """Mutation: drop ``_off()`` from a route → red."""
    card = await _card()
    F.flag_off(monkeypatch)
    with pytest.raises(HTTPException) as e:
        await _card()
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], F.rows_answer("to-0"))
    assert e.value.status_code == 404
    assert _world["sends"].sent == []


async def test_a_send_answer_is_recorded_only_while_the_switch_is_on(monkeypatch) -> None:
    """With the switch off, respond-input records a reply answer only, as
    before §21. Mutation: record a rows answer with the switch off → red."""
    from acb_skills import reply_card as rcard

    F.flag_off(monkeypatch)
    assert not await F.record_answer("b" * 32, F.rows_answer("to-0"))
    F.flag_on(monkeypatch)
    assert await F.record_answer("c" * 32, F.rows_answer("to-0"))
    assert rcard.looks_like_send_answer(F.rows_answer("to-0"))
    for bad in ("APPROVE", 'APPROVE {"rows": []}', 'APPROVE {"rows": ["a"], "x": 1}',
                'APPROVE {"rows": [1]}', "REJECT"):
        assert not rcard.looks_like_send_answer(bad), bad


# ── 6. The card id and the exact answer ──────────────────────────────────────


async def test_request_confirmation_takes_the_card_id_and_gives_the_exact_answer(
    monkeypatch,
) -> None:
    """Mutation: mint a new id, or hand back the stripped text → red."""
    import asyncio

    from acb_skills import ask_tools
    from orchestrator import executor as ex

    queue: asyncio.Queue = asyncio.Queue()
    token = ex._active_run_queue.set(queue)
    card_id = "d" * 32
    raw = '  APPROVE {"rows": ["to-0"]}'

    async def wait(fut: Any, timeout: float) -> dict[str, Any]:
        return {"answer": raw, "responder": F.MEMBER}

    monkeypatch.setattr(ex, "wait_user_future", wait)
    try:
        answer = await ask_tools.request_confirmation(
            title="Send this draft?",
            rows=[{"id": "to-0", "label": "To: a@b.test", "kind": "to", "class": "new",
                   "checked": False}],
            request_id=card_id, with_answer=True)
    finally:
        ex._active_run_queue.reset(token)
    event = queue.get_nowait()
    assert event["value"]["request_id"] == card_id
    assert event["value"]["rows"][0]["class"] == "new"
    assert event["value"]["rows"][0]["kind"] == "to"
    assert isinstance(answer, ask_tools.RowsAnswer)
    assert answer.request_id == card_id and answer.answer == raw
    assert answer.ticked == frozenset({"to-0"})


def test_request_confirmation_refuses_an_id_that_it_did_not_mint() -> None:
    import asyncio

    from acb_skills import ask_tools

    for bad in ("x" * 32, "A" * 32, "a" * 31, "../../x"):
        with pytest.raises(ValueError):
            asyncio.run(ask_tools.request_confirmation(
                title="t", rows=[{"id": "a", "label": "a"}], request_id=bad,
                with_answer=True))


def test_the_client_draws_the_servers_marks() -> None:
    """The words of the marks live on the server, and the client draws the
    same ones. Mutation: change one side → red."""
    from acb_skills import held_card

    src = (F.REPO / "workbench/control_plane/src/lib/sendCard.ts").read_text(encoding="utf-8")
    assert f'NEW_MARK = "{held_card.NEW_MARK}"' in src
    assert f'THREAD_MARK = "{held_card.THREAD_MARK}"' in src


# ── 7. A forward and a new mail (§21.3, the Forward and New rows) ────────────


async def _forward_card(**kw: Any) -> dict[str, Any]:
    body = {"to": [F.KNOWN], "cc": [F.STRANGER], "note": "FYI, the quote.", **kw}
    return await hc.held_card(
        hc.HeldCardRequest(kind="forward", message_id=F.MAIL, **body), F.user())


async def test_a_forward_card_classes_each_person_and_lists_the_mails_files(_world) -> None:
    card = await _forward_card()
    kinds = [(r["kind"], r.get("class"), r["checked"]) for r in card["rows"]]
    assert kinds == [("to", "sent", True), ("cc", "new", False),
                     ("file", None, True), ("file", None, True)]
    assert "FYI, the quote." in card["context"]


async def test_a_forward_sends_only_the_ticked_people_and_files(_world) -> None:
    """Mutation: send every file of the mail, or the unticked Cc → red."""
    card = await _forward_card()
    rows = {r["label"]: r["id"] for r in card["rows"]}
    answer = F.rows_answer(rows[f"To: {F.KNOWN}"], rows['File: "rates.xlsx" (2 KB)'])
    await F.record_answer(card["card_id"], answer)
    out = await _act(card["card_id"], answer)
    fwd = _world["sends"].forwards[0]
    assert fwd.to == [F.KNOWN] and fwd.cc is None and fwd.bcc is None
    assert fwd.attachment_ids == [F.FILE_XLS] and fwd.include_attachments is False
    assert fwd.note == "FYI, the quote."
    assert out["left_out"] == 2


async def test_a_forward_names_a_file_of_the_mail_only(_world) -> None:
    with pytest.raises(HTTPException) as e:
        await _forward_card(files=["passwords.txt"])
    assert e.value.status_code == 422 and "quote.pdf" in e.value.detail
    card = await _forward_card(files=["QUOTE.pdf"])
    assert [r["label"] for r in card["rows"] if r["kind"] == "file"] == [
        'File: "quote.pdf" (4 KB)']


async def test_a_new_mail_sends_the_recorded_text_and_the_ticked_files(_world) -> None:
    card = await hc.held_card(hc.HeldCardRequest(
        kind="new", account_id=F.BOX, to=[F.COLLEAGUE], bcc=[F.KNOWN], subject="Rates",
        note="Here are the rates.", files=[f"attachment:{F.FILE_PDF}"]), F.user())
    ids = [r["id"] for r in card["rows"]]
    answer = F.rows_answer(*ids)
    await F.record_answer(card["card_id"], answer)
    await _act(card["card_id"], answer)
    sent = _world["sends"].mails[0]
    assert sent.to == [F.COLLEAGUE] and sent.bcc == [F.KNOWN]
    assert sent.subject == "Rates" and sent.body_text == "Here are the rates."
    assert _world["sends"].fetched == [F.FILE_PDF]
    assert [a.filename for a in sent.attachments] == [f"{F.FILE_PDF}.pdf"]


async def test_a_new_mail_refuses_a_workspace_file(_world) -> None:
    with pytest.raises(HTTPException) as e:
        await hc.held_card(hc.HeldCardRequest(
            kind="new", account_id=F.BOX, to=[F.KNOWN], subject="x", note="y",
            files=["outputs/rows.csv"]), F.user())
    assert e.value.status_code == 422 and "workspace" in e.value.detail
    assert _world["redis"].keys == {}


@pytest.mark.parametrize(("field", "value"), [
    ("note", "Hi\U000e0041\U000e0042"),           # tag characters
    ("note", "Hi ‮evil"),                     # a bidi control
    ("subject", "Rates\nBcc: x@evil.example"),     # a header in a subject
])
async def test_a_new_mail_with_hidden_text_is_refused(_world, field: str, value: str) -> None:
    body = {"to": [F.KNOWN], "subject": "Rates", "note": "Hello", field: value}
    with pytest.raises(HTTPException) as e:
        await hc.held_card(hc.HeldCardRequest(kind="new", account_id=F.BOX, **body), F.user())
    assert e.value.status_code == 422
    assert _world["redis"].keys == {}


def test_each_kind_names_the_fields_that_it_needs() -> None:
    """An empty id never reaches the SQL as an empty UUID."""
    for kind, body in (("draft", {"account_id": F.BOX}), ("draft", {"draft_id": F.DRAFT}),
                       ("schedule", {"account_id": F.BOX, "draft_id": F.DRAFT}),
                       ("unsubscribe", {"account_id": F.BOX}), ("forward", {"to": ["a@b.test"]}),
                       ("new", {"to": ["a@b.test"]})):
        with pytest.raises(ValidationError):
            hc.HeldCardRequest(kind=kind, **body)


def test_each_kind_takes_only_its_own_fields() -> None:
    """A draft card takes no recipient, and an unsubscribe card no file."""
    for kind, extra in (("draft", {"to": [F.STRANGER]}), ("schedule", {"files": ["a"]}),
                        ("unsubscribe", {"note": "x"}), ("forward", {"subject": "x"}),
                        ("new", {"message_id": F.MAIL}), ("draft", {"email": "a@b.test"})):
        with pytest.raises(ValidationError):
            hc.HeldCardRequest(kind=kind, account_id=F.BOX, **extra)



# ── 8. The text is sealed (fix round 1, P1-b) ────────────────────────────────


async def test_an_edit_after_the_card_sends_nothing(_world) -> None:
    """The card showed one text, and edit_draft changed it before the act.
    Mutation: drop the seal check of ``_act_draft`` → red."""
    card = await _card()
    answer = F.rows_answer(*[r["id"] for r in card["rows"]])
    await F.record_answer(card["card_id"], answer)
    _world["db"].seal = "b" * 64  # edit_draft wrote a new text
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer)
    assert e.value.status_code == 409 and "text" in e.value.detail
    assert _world["sends"].sent == [] and _world["sends"].narrowed == []


async def test_a_narrowed_draft_goes_with_the_seal_of_what_was_written(_world) -> None:
    card = await _card()
    rows = _ids(card)
    answer = F.rows_answer(rows[F.KNOWN]["id"])
    await F.record_answer(card["card_id"], answer)
    seals = iter(["a" * 64, "c" * 64])

    async def seal(owner, account_id, draft_id):
        return next(seals)

    import gateway.routes.email.automation.held_card as mod

    mod_seal = mod.seal_of
    mod.seal_of = seal
    try:
        await _act(card["card_id"], answer)
    finally:
        mod.seal_of = mod_seal
    assert _world["sends"].sent[0].expect.text_digest == "c" * 64
