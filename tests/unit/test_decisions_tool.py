"""WS-ALN ALN-2a: the chat reaches a decision (D-OP-9, D-OP-13).

Code: ``skill_projects/inbox.py`` ``my_decisions`` (class A) and
``skill_projects/guarded.py`` ``answer_decision`` (class C, one card). The
gateway is a fake. The R8 fence of the routes is ``test_decisions.py``.

* ``answer_decision`` refuses a run with no person present BEFORE any read,
  any card and any POST (AUTO-7a). This is the named exemption of
  ``test_no_person_present.py::_NAMED_TOOL_EXEMPTIONS``.
* It posts only after a confirmed card. A declined card writes nothing.
* While ``DECISIONS_ORGS`` is off (the route answers 404) both tools say one
  plain sentence and raise no card.

**Mutations (R7).** Commit first, restore with ``git checkout -- <file>``.

* M1. ``guarded.answer_decision``: drop the ``no_person_present()`` check.
* M2. ``guarded.answer_decision``: skip the ``_confirm`` card.
* M3. ``guarded.answer_decision``: drop the 404 branch of the pre-card read.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from acb_common.no_person import NO_PERSON_TEXT, no_person_scope
from skill_projects import manifest

from tests.unit._projects_agent_fakes import FakeResponse, approve, deny, fake_gateway, writes

DID = str(uuid.uuid4())
TASK = str(uuid.uuid4())
ROW = {
    "id": DID, "subject_kind": "task", "subject_id": TASK, "decider": "d@acme.test",
    "question": "Ship the extruder fix today?", "state": "open",
    "expires_at": "2026-11-09T00:00:00+00:00",
    "options": [{"key": "yes", "label": "Yes, go ahead"}, {"key": "no", "label": "No, stop"}],
}
_WAIT = 30


def _run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, _WAIT))


def _gateway(monkeypatch, *, rows=None, off=False, post=None):
    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == "/projects/decisions" and method == "GET":
            if off:
                return FakeResponse({"detail": "Not Found"}, 404)
            return {"decisions": [ROW] if rows is None else rows}
        if path == f"/projects/tasks/{TASK}" and method == "GET":
            return {"id": TASK, "title": "Fix the extruder"}
        if path == f"/projects/decisions/{DID}/answer" and method == "POST":
            return post if post is not None else {"decision": {**ROW, "state": "answered"}}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, answer)


def test_the_manifest_maps_the_two_routes_to_two_tools() -> None:
    rows = {(r.method, r.path): (r.tool, r.cls) for r in manifest.MANIFEST}
    assert rows[("GET", "/projects/decisions")] == ("my_decisions", "A")
    assert rows[("POST", "/projects/decisions/{decision_id}/answer")] == ("answer_decision", "C")
    assert "answer_decision" in skill_projects.__all__ and "my_decisions" in skill_projects.__all__


def test_answer_decision_refuses_a_run_with_no_person_before_any_call(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    with no_person_scope():
        said = _run(skill_projects.answer_decision(DID, "yes"))
    assert NO_PERSON_TEXT in said
    assert calls == [] and asked == []


def test_answer_decision_posts_only_after_a_confirmed_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    said = _run(skill_projects.answer_decision(DID, "yes"))
    assert "Yes, go ahead" in said
    assert len(asked) == 1
    card = asked[0]
    assert "Ship the extruder fix today?" in card["context"]
    assert "Yes, go ahead" in card["context"] and "Fix the extruder" in card["context"]
    posted = writes(calls)
    assert [(c["method"], c["path"]) for c in posted] == [
        ("POST", f"/projects/decisions/{DID}/answer")]
    assert posted[0]["json"] == {"answer_key": "yes"}


def test_answer_decision_cannot_post_without_a_confirmed_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = deny(monkeypatch)
    said = _run(skill_projects.answer_decision(DID, "yes"))
    assert "Cancelled" in said and len(asked) == 1
    assert writes(calls) == []


def test_answer_decision_checks_the_key_and_the_row_before_any_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    assert "must be one of" in _run(skill_projects.answer_decision(DID, "maybe"))
    assert "waits for you" in _run(skill_projects.answer_decision(str(uuid.uuid4()), "yes"))
    assert "ONE" in _run(skill_projects.answer_decision(f"{DID},{TASK}", "yes"))
    assert asked == [] and writes(calls) == []


def test_while_the_flag_is_off_both_tools_say_one_sentence_and_raise_no_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch, off=True)
    asked = approve(monkeypatch)
    said = _run(skill_projects.answer_decision(DID, "yes"))
    assert said == "Decisions are not on for this organization yet."
    assert _run(skill_projects.my_decisions()) == said
    assert asked == [] and writes(calls) == []


def test_my_decisions_lists_the_options_and_fences_the_text(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = _run(skill_projects.my_decisions())
    assert DID in out and "«Ship the extruder fix today?»" in out and "yes = «Yes, go ahead»" in out
    _gateway(monkeypatch, rows=[])
    assert "no decision waits for you" in _run(skill_projects.my_decisions())


@pytest.mark.parametrize("status,words", [
    (409, "already has an answer"), (403, "person must answer"), (404, "not found")])
def test_a_refusal_of_the_route_is_one_sentence(monkeypatch, status, words) -> None:
    _gateway(monkeypatch, post=FakeResponse({"detail": "decision_answered"}, status))
    approve(monkeypatch)
    said = _run(skill_projects.answer_decision(DID, "yes"))
    assert words in said and "Nothing was changed" in said
