"""The email pack rules hold three lines (WS-67 OA-7j).

OA-7h live run 3 (2026-10-28) caught three faults that the pack rules
caused. Each test below is one pin, and each has one mutation (R7):

* delete the search line -> the model drafts for a named person blind;
* delete or weaken the "send it" line -> a second ``compose_reply``;
* delete the draft-only-on-request line -> an unasked draft.

The rules ride only with the pack, so the flag-off persona and the handoff
rules must not hold them, and the rules block stays short.
"""
from __future__ import annotations

import re

from acb_skills import handoff as h
from acb_skills import tool_packs as tp


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


RULES = _flat(tp.PACKS["email"].rules)


def test_a_named_person_is_found_before_compose_reply() -> None:
    assert (
        "To reply to a named person, find that person's address first with"
        " find_contact or query_inbox." in RULES
    )


def test_send_it_means_send_draft_never_a_new_draft() -> None:
    assert (
        '"Send it", "go ahead" and "ok send" mean: call send_draft on the draft'
        " of the working set. Never write a new draft for them." in RULES
    )
    assert "call compose_reply or send_draft at once" not in RULES


def test_a_draft_needs_a_request() -> None:
    assert (
        "Draft only when the member asks for a draft or a reply. A correction,"
        " a question or a summary is never that request." in RULES
    )


def test_the_rules_block_stays_short() -> None:
    # The block was 2,362 characters before OA-7j. The budget is about 600.
    assert len(tp.PACKS["email"].rules) < 2362 + 600


def test_the_flag_off_persona_and_handoff_do_not_hold_the_pack_lines() -> None:
    import orchestrator.agents as pkg

    persona = _flat(pkg._mod._orchestrator_instructions(False, False, False))
    for line in ("find_contact or query_inbox", "call send_draft on the draft"):
        assert line not in persona
        assert line not in _flat(h._RULES)
