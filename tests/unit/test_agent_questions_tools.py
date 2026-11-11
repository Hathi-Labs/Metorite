"""WS-55 EA-18d: the chat reads and answers the agent questions (spec §12.8).

Code: ``skill_projects/reads.py`` ``agent_questions`` (class A) and
``skill_projects/question_answers.py`` ``answer_agent_question`` (class B, one
card, a composite of ``post_agent_activity``). The gateway is a fake. The R8
fence of the routes is ``test_agent_questions_r8.py``.

**Mutations (R7).** Commit first, restore with ``git checkout -- <file>``.

* M1. ``answer_agent_question``: drop the ``no_person_present()`` check.
* M2. ``answer_agent_question``: skip the ``_confirm`` card.
* M3. ``answer_agent_question``: drop the ``_is_archive_request`` refusal.
* M4. ``agent_questions``: drop the 404 branch.
* M5. ``answer_agent_question``: drop the check of ``selected`` against the ids.
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

RUN = str(uuid.uuid4())
TASK = str(uuid.uuid4())
QID = str(uuid.uuid4())
_WAIT = 30

ROW = {
    "run_id": RUN, "activity_id": QID, "task_id": TASK, "task_number": 41,
    "task_title": "Fix the extruder", "agent_name": "Claude", "client_via": "mcp:claude",
    "asked_at": "2026-11-10T08:00:00+00:00",
    "project_path": [{"id": str(uuid.uuid4()), "name": "Plant"}, {"id": str(uuid.uuid4()), "name": "Line 2"}],
    "question": {"kind": "question", "body": "Which nozzle size?",
                 "options": [{"id": "a", "label": "0.4 mm"}, {"id": "b", "label": "0.6 mm"}],
                 "allow_multiple": False},
    "can_answer": True, "why": "created",
}
ARCHIVE_ROW = {**ROW, "run_id": str(uuid.uuid4()),
               "question": {"kind": "archive_request", "body": "Archive this task?",
                            "options": [], "allow_multiple": False}}


def _run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, _WAIT))


def _open_run(*, status="awaiting", archive=False, multiple=False) -> dict:
    meta: dict[str, Any] = {"options": [{"id": "a", "label": "0.4 mm"}, {"id": "b", "label": "0.6 mm"}]}
    if multiple:
        meta["allow_multiple"] = True
    question = {
        "id": QID, "content": {"body": "Which nozzle size?"}, "signal_metadata": meta,
        "content_metadata": {"request": {"kind": "archive_task"}} if archive else None,
    }
    return {"run": {"id": RUN, "status": status, "open_elicitation": question}, "activities": []}


def _gateway(monkeypatch, *, rows=None, off=False, run=None, post=None, total=None, runs=None):
    reads = list(runs or [])

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == "/projects/agent-questions" and method == "GET":
            if off:
                return FakeResponse({"detail": "Not Found"}, 404)
            found = [ROW] if rows is None else rows
            return {"rows": found, "total": len(found) if total is None else total,
                    "counts": {"mine": len(found), "everyone": None}}
        if path == f"/projects/agent-runs/{RUN}" and method == "GET":
            if reads:
                return reads.pop(0) if len(reads) > 1 else reads[0]
            return run if run is not None else _open_run()
        if path == f"/projects/agent-runs/{RUN}/activities" and method == "POST":
            return post if post is not None else {"created": True, "activity": {"id": "x"}}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, answer)


# ── the manifest ────────────────────────────────────────────────────────────


def test_the_manifest_maps_the_read_and_composes_the_write() -> None:
    rows = {(r.method, r.path): (r.tool, r.cls) for r in manifest.MANIFEST}
    assert rows[("GET", "/projects/agent-questions")] == ("agent_questions", "A")
    assert manifest.COMPOSITE["answer_agent_question"] == frozenset({"post_agent_activity"})
    assert "agent_questions" not in manifest.PLANNED
    assert {"agent_questions", "answer_agent_question"} <= set(skill_projects.__all__)


# ── agent_questions ─────────────────────────────────────────────────────────


def test_the_read_passes_scope_project_sort_and_offset(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    project = str(uuid.uuid4())
    _run(skill_projects.agent_questions(scope="everyone", project_id=project, sort="newest", offset=25))
    get = next(c for c in calls if c["path"] == "/projects/agent-questions")
    sent = get.get("params") or {}
    assert sent["scope"] == "everyone" and sent["project"] == project
    assert sent["sort"] == "newest" and sent["offset"] == 25 and sent["limit"] == 25


def test_the_read_lists_the_path_the_options_and_the_run_and_fences_agent_text(monkeypatch) -> None:
    _gateway(monkeypatch)
    out = _run(skill_projects.agent_questions())
    assert "Plant" in out and "Line 2" in out
    assert "«Which nozzle size?»" in out
    assert "a = «0.4 mm»" in out and "b = «0.6 mm»" in out
    assert f"run_id: {RUN}" in out
    assert "answer_agent_question" in out


def test_a_row_the_member_cannot_answer_says_so(monkeypatch) -> None:
    _gateway(monkeypatch, rows=[{**ROW, "can_answer": False}])
    out = _run(skill_projects.agent_questions())
    assert "refuses an answer" in out and "next: answer_agent_question" not in out


def test_a_typed_archive_request_shows_the_card_and_no_answer_tool(monkeypatch) -> None:
    _gateway(monkeypatch, rows=[ARCHIVE_ROW])
    out = _run(skill_projects.agent_questions())
    assert "request to archive" in out and "task card" in out
    assert "next: answer_agent_question" not in out
    assert f"/projects?task={TASK}" in out


def test_the_read_pages(monkeypatch) -> None:
    _gateway(monkeypatch, total=60)
    out = _run(skill_projects.agent_questions())
    assert "offset" in out


def test_an_empty_list_says_so(monkeypatch) -> None:
    _gateway(monkeypatch, rows=[])
    assert "no question waits" in _run(skill_projects.agent_questions())


def test_a_bad_scope_or_sort_is_refused_before_any_call(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    assert "scope is mine or everyone" in _run(skill_projects.agent_questions(scope="all"))
    assert "sort is oldest or newest" in _run(skill_projects.agent_questions(sort="best"))
    assert calls == []


def test_while_the_flag_is_off_the_read_says_so_plainly(monkeypatch) -> None:
    _gateway(monkeypatch, off=True)
    said = _run(skill_projects.agent_questions())
    assert said == skill_projects.reads.QUESTIONS_OFF
    assert "not on for this organization" in said


# ── answer_agent_question ───────────────────────────────────────────────────


def test_the_answer_refuses_a_run_with_no_person_before_any_call(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    with no_person_scope():
        said = _run(skill_projects.answer_agent_question(RUN, answer="0.4"))
    assert NO_PERSON_TEXT in said
    assert calls == [] and asked == []


def test_the_answer_posts_one_prompt_after_a_confirmed_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="Use the small one", selected="a"))
    assert "Answered" in said and RUN in said
    assert len(asked) == 1
    assert "Which nozzle size?" in asked[0]["context"] and "0.4 mm" in asked[0]["context"]
    posted = writes(calls)
    assert [(c["method"], c["path"]) for c in posted] == [
        ("POST", f"/projects/agent-runs/{RUN}/activities")]
    body = posted[0]["json"]
    assert body["type"] == "prompt" and body["selected"] == ["a"]
    assert body["body"] == "Use the small one"
    assert body["idempotency_key"] == skill_projects.question_answers._answer_key(
        QID, "Use the small one", ["a"])
    assert body["idempotency_key"].startswith(f"chat-answer-{QID}-")


def test_a_declined_card_writes_nothing(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = deny(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert "Cancelled" in said and len(asked) == 1
    assert writes(calls) == []


def test_a_typed_archive_request_is_refused_before_any_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch, run=_open_run(archive=True))
    asked = approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="yes"))
    assert said == skill_projects.question_answers.ARCHIVE_ON_CARD
    assert asked == [] and writes(calls) == []
    assert not [c for c in calls if "archive-approval" in c["path"]]


def test_a_run_with_no_open_question_is_refused(monkeypatch) -> None:
    calls = _gateway(monkeypatch, run=_open_run(status="running"))
    asked = approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert said == skill_projects.question_answers.NOT_AWAITING
    assert asked == [] and writes(calls) == []


@pytest.mark.parametrize("kwargs,words", [
    ({}, "needs text"),
    ({"selected": "z"}, "option ids"),
    ({"selected": "a,b"}, "takes one option"),
    ({"answer": "x" * 4001}, "at most"),
])
def test_a_bad_answer_is_refused_before_any_card(monkeypatch, kwargs, words) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    assert words in _run(skill_projects.answer_agent_question(RUN, **kwargs))
    assert asked == [] and writes(calls) == []


def test_several_options_are_allowed_when_the_question_allows_them(monkeypatch) -> None:
    calls = _gateway(monkeypatch, run=_open_run(multiple=True))
    approve(monkeypatch)
    _run(skill_projects.answer_agent_question(RUN, selected="a,b"))
    assert writes(calls)[0]["json"]["selected"] == ["a", "b"]


def test_a_403_of_the_server_is_one_sentence_and_the_tool_holds_no_rule(monkeypatch) -> None:
    _gateway(monkeypatch, post=FakeResponse({"detail": "Only the task creator can answer."}, 403))
    approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert said.startswith(skill_projects.question_answers.REFUSED)
    assert "Nothing was changed" in said


def test_a_retry_that_the_server_replays_says_already_answered(monkeypatch) -> None:
    _gateway(monkeypatch, post={"created": False, "activity": {"content": {"body": "x"}}})
    approve(monkeypatch)
    assert "Already answered" in _run(skill_projects.answer_agent_question(RUN, answer="x"))


def test_a_stale_card_is_refused_and_nothing_is_posted(monkeypatch) -> None:
    newer = _open_run()
    newer["run"]["open_elicitation"]["id"] = str(uuid.uuid4())
    calls = _gateway(monkeypatch, runs=[_open_run(), newer])
    approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert said == skill_projects.question_answers.STALE_CARD
    assert writes(calls) == []


def test_a_text_answer_is_refused_when_the_run_stops_awaiting_after_the_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch, runs=[_open_run(), _open_run(status="running")])
    approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert said == skill_projects.question_answers.STALE_CARD
    assert writes(calls) == []


def test_a_reanswer_after_a_withdraw_gets_a_new_key_and_a_changed_answer_a_new_key() -> None:
    key = skill_projects.question_answers._answer_key
    assert key(QID, "x", ["a"]) == key(QID, "x", ["a"])
    assert key(QID, "x", ["a"]) != key(QID, "y", ["a"])
    assert key(QID, "x", ["a", "b"]) == key(QID, "x", ["b", "a"])
    assert key(QID, "x", ["a"]) != key(QID, "x", ["b"])


def test_a_race_that_returns_another_answer_is_not_called_the_same(monkeypatch) -> None:
    _gateway(monkeypatch, post={"created": False, "activity": {"content": {"body": "other"}}})
    approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert said == skill_projects.question_answers.ANSWER_RECORDED
    assert "same answer" not in said


def test_a_replay_with_the_same_selected_ids_is_the_same_answer(monkeypatch) -> None:
    _gateway(monkeypatch, post={"created": False, "activity": {"content": {"selected": ["a"]}}})
    approve(monkeypatch)
    assert "same answer" in _run(skill_projects.answer_agent_question(RUN, selected="a"))


def test_duplicate_ids_are_refused_before_the_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch, run=_open_run(multiple=True))
    asked = approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, selected="a,a"))
    assert "twice" in said
    assert asked == [] and writes(calls) == []


def test_a_422_of_the_server_is_one_sentence(monkeypatch) -> None:
    _gateway(monkeypatch, post=FakeResponse({"detail": "selected names an id that is not an option."}, 422))
    approve(monkeypatch)
    said = _run(skill_projects.answer_agent_question(RUN, answer="x"))
    assert "Nothing was changed" in said
