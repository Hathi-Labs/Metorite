"""No hold text sends the member to "a new chat" (one_assistant_context.md §9.1, WT-3).

A held chat offers ONE tap, the pill "Continue in a fresh chat" (WT-2). Two
forms of each ``_NO_WRITES`` text exist. The ``_CONTINUE`` form names the pill
with its exact label. The chooser picks it only when ``offer_continue`` sent the
event of the pill, so a text never promises a pill that the chat cannot draw.
Each other case (the flag off, another org, no run stream, a fault) gets the
plain form, which promises no tap.

What breaks it (R7):

* put "or ask again in a new chat" back in a hold text -> the scan goes red;
* change the label of the pill in ``chatContinue.ts`` alone -> the label
  case goes red;
* return the ``_CONTINUE`` form with no sent event -> the fail-closed cases go
  red.
"""

from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path

import pytest
from acb_common import get_settings
from acb_skills import egress as eg
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "workbench" / "control_plane" / "src"
CHAT_CONTINUE_TS = CLIENT / "lib" / "chatContinue.ts"
CONTINUE_OFFER_TSX = CLIENT / "components" / "ContinueOffer.tsx"

ORG = "11111111-1111-1111-1111-111111111111"
SESSION = "sess-no-new-chat-1"

_TAP = re.compile(r"\btap\b", re.IGNORECASE)


def _hold_texts() -> dict[str, str]:
    """Each ``EGRESS_HOLD_NOTE_*`` and ``EGRESS_HELD_*`` text of the module."""
    return {name: value for name, value in vars(eg).items()
            if name.startswith(("EGRESS_HOLD_NOTE_", "EGRESS_HELD_"))
            and isinstance(value, str)}


def _pill_label() -> str:
    """The label of the pill, read from the client source."""
    source = CHAT_CONTINUE_TS.read_text(encoding="utf-8")
    found = re.search(r'export const CONTINUE_LABEL = "([^"]+)";', source)
    assert found, "CONTINUE_LABEL is gone from chatContinue.ts"
    return found.group(1)


def test_the_scan_is_not_vacuous() -> None:
    names = set(_hold_texts())
    for name in ("EGRESS_HELD_PACK_ANSWER_NO_WRITES", "EGRESS_HOLD_NOTE_PACK_NO_WRITES",
                 "EGRESS_HELD_PACK_ANSWER_NO_WRITES_CONTINUE",
                 "EGRESS_HOLD_NOTE_PACK_NO_WRITES_CONTINUE",
                 "EGRESS_HOLD_NOTE_MAIL", "EGRESS_HELD_MAIL_ANSWER"):
        assert name in names, name
    assert len(names) >= 14, sorted(names)


@pytest.mark.parametrize("name", sorted(_hold_texts()))
def test_no_hold_text_says_new_chat(name: str) -> None:
    text = " ".join(_hold_texts()[name].split()).lower()
    assert "new chat" not in text, name


def test_the_pill_draws_the_label_that_the_texts_name() -> None:
    """The pill draws ``CONTINUE_LABEL``, so the label in the texts is the
    label on the screen."""
    source = CONTINUE_OFFER_TSX.read_text(encoding="utf-8")
    assert "label: CONTINUE_LABEL" in source
    assert _pill_label() == "Continue in a fresh chat"


@pytest.mark.parametrize("name", sorted(_hold_texts()))
def test_a_text_that_offers_the_tap_names_the_pill(name: str) -> None:
    """Only a ``_CONTINUE`` form offers the tap, and it gives the exact label."""
    text = " ".join(_hold_texts()[name].split())
    label = _pill_label()
    if name.endswith("_CONTINUE"):
        assert f'"{label}"' in text, name
        assert _TAP.search(text), name
    else:
        assert label.lower() not in text.lower(), name
        assert not _TAP.search(text), name


# ── The chooser: the tap form only after the event went out ───────────────


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


def _ctx() -> dict:
    return {eg.PACK_CALL_KEY: True, "agent_name": "projects-assistant"}


def _flag(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "chat_continue_orgs", value)


def _both() -> tuple[str, str]:
    return eg.egress_hold_note(_ctx()), eg.egress_held_answer("x", _ctx())


def test_the_event_sent_gives_the_tap_form(stream, monkeypatch) -> None:
    _flag(monkeypatch, ORG)
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION)
        note, answer = _both()
    assert note == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES_CONTINUE
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_NO_WRITES_CONTINUE.format(name="x")


def test_the_flag_off_gives_the_plain_form(stream, monkeypatch) -> None:
    _flag(monkeypatch, "")
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION)
        note, answer = _both()
    assert note == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_NO_WRITES.format(name="x")


def test_no_run_stream_gives_the_plain_form(stream, monkeypatch) -> None:
    """The flag holds the org, but no pill can draw: no tap in the text."""
    _flag(monkeypatch, "*")
    with artifact_context_scope():
        bind_artifact_context(session_id="another-session")
        note, answer = _both()
    assert note == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_NO_WRITES.format(name="x")


def test_a_fault_in_the_flag_gives_the_plain_form(stream, monkeypatch) -> None:
    def _boom(*_a):
        raise RuntimeError("settings fault")

    monkeypatch.setattr("acb_common.chat_continue_flag.continue_allowed", _boom)
    with artifact_context_scope():
        bind_artifact_context(session_id=SESSION)
        note, answer = _both()
    assert note == eg.EGRESS_HOLD_NOTE_PACK_NO_WRITES
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_NO_WRITES.format(name="x")
