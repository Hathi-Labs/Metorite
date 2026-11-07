"""The server marks a held chat for the pill (one_assistant_context.md §9.4, WT-2).

The hold note and the held refusal of a NO_WRITES chat put one CUSTOM event,
``chat_continue_offer``, on the run stream, and only while CHAT_CONTINUE_ORGS
holds the org of the run. Flag off: no event, and the texts stay as they were.
Fence (R7): a flag-blind send -> the flag-off case goes red. A send for another
org -> the other-org case goes red.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from acb_common import get_settings
from acb_skills import egress as eg
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

ORG = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
SESSION = "sess-offer-1"


@pytest.fixture
def stream(monkeypatch):
    """A fake run stream under the name that the sender looks up."""
    queue: asyncio.Queue = asyncio.Queue()
    fake = types.SimpleNamespace(
        resolve_run_queue=lambda key=None: queue if key == SESSION else None)
    monkeypatch.setitem(sys.modules, "orchestrator.executor", fake)
    monkeypatch.setattr("acb_common.db.current_tenant", lambda: ORG)
    monkeypatch.setattr(eg, "hold_is_for_mail", lambda *_a: True)
    monkeypatch.setattr(eg, "held_card_writes_on", lambda *_a: False)
    return queue


def _ctx():
    return {eg.PACK_CALL_KEY: True, "agent_name": "projects-assistant"}


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _flag(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "chat_continue_orgs", value)


@pytest.mark.parametrize("call", ["note", "answer"])
def test_flag_on_sends_one_offer(stream, monkeypatch, call) -> None:
    _flag(monkeypatch, ORG)
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION)
        if call == "note":
            text = eg.egress_hold_note(_ctx())
            assert text == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES
        else:
            text = eg.egress_held_answer("x", _ctx())
            assert text == eg.EGRESS_HELD_PACK_ANSWER_NO_WRITES.format(name="x")
    events = _drain(stream)
    assert events == [{"type": "CUSTOM", "name": eg.CONTINUE_OFFER_EVENT, "value": {}}]
    assert eg.CONTINUE_OFFER_EVENT == "chat_continue_offer"


@pytest.mark.parametrize("value", ["", OTHER])
def test_flag_off_or_other_org_sends_nothing(stream, monkeypatch, value) -> None:
    _flag(monkeypatch, value)
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION)
        assert eg.egress_hold_note(_ctx()) == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES
        assert eg.egress_held_answer("x", _ctx())
    assert _drain(stream) == []


def test_no_run_stream_sends_nothing_and_never_raises(stream, monkeypatch) -> None:
    _flag(monkeypatch, "*")
    with artifact_context_scope():
        bind_artifact_context(session_id="another-session")
        assert eg.offer_continue() is False
    assert _drain(stream) == []


def test_the_cards_form_sends_nothing(stream, monkeypatch) -> None:
    """With HELD_CARD_WRITES_ORGS on the chat has a card, so no pill."""
    _flag(monkeypatch, "*")
    monkeypatch.setattr(eg, "held_card_writes_on", lambda *_a: True)
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION)
        eg.egress_hold_note(_ctx())
        eg.egress_held_answer("x", _ctx())
    assert _drain(stream) == []
