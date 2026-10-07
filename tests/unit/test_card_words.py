"""The words a member reads on a confirmation card (owner report, 2026-10-07).

Three rules, each with the mutation that breaks it:

1. Every card key has a label in the ONE map, ``cardFields.ts``
   ``CARD_FIELDS``. ``_card_words.py`` reads that map, and the shared fakes
   check every card a test draws. Mutation: delete ``"tags_add"`` from the
   map, and ``test_bulk_names_every_task_and_sends_the_status_by_name`` fails.
2. No wire word outside a fence. Mutation: put ``tags_add → q4`` back in the
   bulk detail (``guarded.py`` ``_bulk_impact``).
3. The server text keeps its fence. The client draws a «name» as a token,
   and the text it receives still holds the marks, which the card parser
   relies on. Mutation: drop ``data()`` in ``_bulk_changes``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._card_words import card_key, card_keys, card_problems
from tests.unit._projects_agent_fakes import approve, fake_gateway
from tests.unit.test_projects_agent_writes import OTHER, TASK, UUID, responder


def test_the_map_is_read_out_of_the_typescript() -> None:
    keys = card_keys()
    assert len(keys) > 100
    assert {"tags_add", "due_at", "task N", "field *", "impact"} <= keys


def test_a_key_reads_as_the_client_reads_it() -> None:
    assert card_key("task 12") == "task N"
    assert card_key("field Customer") == "field *"
    assert card_key("due_at") == "due_at"


def test_a_raw_key_and_a_wire_word_are_problems_and_a_fenced_one_is_not() -> None:
    assert card_problems({"title": "Change it?", "context": "brand_new_key: «x»"})
    assert card_problems({"title": "Change it?", "detail": "1 task changed: tags_add → q4"})
    assert card_problems({"title": "Archive?", "context": "undo: unarchive_task"})
    # A member may write any word, so a word inside the marks is data.
    assert not card_problems({"title": "Rename «my_file_name»?", "context": "title: «snake_case»"})
    # An address and a path are not wire words.
    assert not card_problems({"title": "Assign?", "detail": "to a_b@x.io", "context": "email: a_b@x.io"})


async def test_the_bulk_card_names_no_wire_word_and_keeps_its_fences(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    await skill_projects.bulk_update(f"{UUID},{OTHER}", status="done", tags_add="q4,Bug")
    card = asked[0]
    assert card_problems(card) == []
    assert "tags_add" not in card["detail"] and "→" not in card["detail"]
    context = card["context"]
    # The change is a field the client draws as tag pills, fenced.
    assert "tags_add: «q4», «Bug»" in context
    assert "status: «done»" in context
    # The task names keep their fence in the text the client receives.
    assert "task 1: #7 «Fix the extruder»" in context


async def test_a_long_title_is_whole_when_the_card_holds_it(monkeypatch) -> None:
    long = "Fix Inbox Cleaner 'Keep Approved' doing nothing when the conversation is long"
    asked = approve(monkeypatch)

    def answer(call: dict) -> object:
        if call["method"] == "GET" and call["path"] == f"/projects/tasks/{UUID}":
            return {**TASK, "title": long}
        return responder(call)

    fake_gateway(monkeypatch, answer)
    await skill_projects.bulk_update(UUID, tags_add="Bug")
    # The UI cuts a long title with CSS and a tooltip. The server does not.
    assert f"task 1: #7 «{long}»" in asked[0]["context"]


def test_a_clip_lands_before_the_fence() -> None:
    from skill_projects.guarded import TITLE_CLIP, _short_ref

    ref = _short_ref({"title": "x" * (TITLE_CLIP + 20), "task_number": 9})
    assert ref.startswith("#9 «") and ref.endswith("…»")
    assert ref.count("«") == 1 and ref.count("»") == 1


async def test_every_projects_card_says_it_is_fenced(monkeypatch) -> None:
    """The client draws «marks» as tokens only on a card the server fenced.
    Mutation caught: ``_confirm`` that drops ``fenced=True``."""
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    await skill_projects.bulk_update(UUID, tags_add="Bug")
    assert asked[0]["fenced"] is True


async def test_a_flag_turned_off_reads_no_on_the_personal_card(monkeypatch) -> None:
    """Review round 1: ``data(False)`` is ``«»``, which the card drew as
    "none". Mutation caught: ``_overlay_shown`` that fences a bool as is."""
    from skill_projects.guarded import _overlay_shown

    assert _overlay_shown(False) == "«no»"
    assert _overlay_shown(True) == "«yes»"
    assert _overlay_shown(None) == "cleared"
    assert _overlay_shown({"email": "p@x.io"}) == "«p@x.io»"
