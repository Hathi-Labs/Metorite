"""SW-43c: the chat takes back or changes the member's own answer to an agent.

Spec: ``project-docs/specs/agent_coordination.md`` §3.9 and slice SW-43c.
Code: ``skill_projects/guarded.py`` ``change_agent_answer`` (class C, one card
for each act). With ``withdraw`` true it calls the withdraw route of SW-43a.
Else it calls the replace route of SW-43b. The gateway is a fake. The R8 fence
of the two routes is ``test_agent_runs_r8.py`` (``TestWithdrawAnAnswer``,
``TestReplaceAnAnswer``). The R8 fence of the tool against the real gateway and
the real edge is ``test_agent_answer_chat_r8.py``.

What this file holds:

* the tool refuses a run with no person present BEFORE any read, any card and
  any POST (AUTO-7a). This is the named exemption of
  ``test_no_person_present.py::_NAMED_TOOL_EXEMPTIONS``;
* it posts only after a confirmed card, and the card names the task, the
  question and the answer;
* a costly option (``confirm: true``, SW-43b) puts "Are you sure?" on the card;
* it refuses before any card when the agent already read the answer, when the
  run is of another member, when the flag is off, and for the answer to an
  archive request (D-OP-13);
* the refusals of the two routes come back as one plain sentence.

**Mutations (R7).** Commit first, restore with ``git checkout -- <file>``.

* M1. ``guarded.change_agent_answer``: drop the ``no_person_present()`` check.
  ``test_the_tool_refuses_a_run_with_no_person_before_any_call``.
* M2. ``guarded._replace_card``: build the title with no "Are you sure?".
  ``test_a_costly_option_asks_are_you_sure_on_the_card``.
* M3. ``guarded._live_answer``: drop the ``changeable`` test.
  ``test_an_answer_the_agent_read_gets_no_card``.
* M4. ``guarded._live_answer``: drop the archive request test.
  ``test_the_answer_to_an_archive_request_stays_on_the_card``.
* M5. ``guarded._new_answer``: take the first of two matching options.
  ``test_a_name_that_matches_two_options_is_a_question``.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from acb_common.no_person import NO_PERSON_TEXT, no_person_scope
from skill_projects import guarded
from skill_projects import manifest as m

from tests.unit._projects_agent_fakes import (
    REPO_ROOT,
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    writes,
)

TASK = str(uuid.uuid4())
RUN = str(uuid.uuid4())
QUESTION = str(uuid.uuid4())
ANSWER = str(uuid.uuid4())
ME = "pm@fracktal.in"
#: The two acts of the one tool: the withdraw, and a replace.
ACTS = ({"withdraw": True}, {"option": "no"})
ACT_IDS = ("withdraw", "replace")

OPTIONS = [
    {"id": "go", "label": "Restart it now", "confirm": True},
    {"id": "no", "label": "Not now"},
    {"id": "later", "label": "Restart it tonight"},
]


def _question(*, request: dict | None = None, many: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": QUESTION, "seq": 2, "type": "elicitation",
        "content": {"body": "Restart the box?"},
        "signal_metadata": {"options": OPTIONS, "allow_multiple": many},
    }
    if request is not None:
        row["content_metadata"] = {"request": request}
    return row


def _answer(selected: list[str], labels: list[str], body: str | None = None) -> dict[str, Any]:
    return {"id": ANSWER, "seq": 3, "type": "prompt", "in_reply_to": QUESTION,
            "content": {"body": body, "selected": selected, "labels": labels}}


def _run_row(*, member: str = ME, changeable: bool = True, flag: bool = True) -> dict[str, Any]:
    row: dict[str, Any] = {"id": RUN, "task_id": TASK, "member_email": member,
                           "status": "in_progress", "state": "answered"}
    if flag:
        row["answer"] = {"id": ANSWER, "seq": 3, "changeable": changeable}
    return row


def _gateway(monkeypatch, *, run: dict | None = None, question: dict | None = None,
             answer: dict | None = None, runs_off: bool = False,
             refuse: FakeResponse | None = None) -> list[dict]:
    run = run if run is not None else _run_row()
    question = question if question is not None else _question()
    answer = answer if answer is not None else _answer(["go"], ["Restart it now"])

    def reply(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == f"/projects/tasks/{TASK}" and method == "GET":
            return {"id": TASK, "title": "Fix the extruder", "task_number": 7}
        if path == f"/projects/tasks/{TASK}/agent-runs" and method == "GET":
            if runs_off:
                return FakeResponse({"detail": "Not Found"}, 404)
            return {"rows": [run]}
        if path == f"/projects/agent-runs/{RUN}" and method == "GET":
            return {"run": run, "activities": [question, answer], "cursor": 3}
        if method == "POST" and path.startswith(f"/projects/agent-runs/{RUN}/answer/"):
            if refuse is not None:
                return refuse
            return {"run": run, "activity": {"id": str(uuid.uuid4())}, "created": True}
        return {"rows": [], "total": 0}

    return fake_gateway(monkeypatch, reply, user=ME)


def _posts(calls: list[dict]) -> list[tuple[str, str]]:
    return [(c["method"], c["path"]) for c in writes(calls)]


async def _change(**kwargs: Any) -> str:
    return await skill_projects.change_agent_answer(TASK, **kwargs)


# ── The manifest and the copies ─────────────────────────────────────────────


def test_the_two_routes_are_class_c_and_built() -> None:
    rows = {(r.method, r.path): (r.tool, r.cls) for r in m.MANIFEST}
    assert rows[("POST", "/projects/agent-runs/{run_id}/answer/withdraw")] == (
        "change_agent_answer", "C")
    assert rows[("POST", "/projects/agent-runs/{run_id}/answer/replace")] == (
        "change_agent_answer", "C")
    assert "change_agent_answer" in skill_projects.__all__
    assert "change_agent_answer" not in m.PLANNED
    assert m.UI_ACTIONS["agentRunsApi.withdrawAnswer"] == "change_agent_answer"
    assert m.UI_ACTIONS["agentRunsApi.replaceAnswer"] == "change_agent_answer"


def test_the_copies_of_the_route_words_are_the_route_words() -> None:
    """Two texts that the skill copies. Mutation: reword one -> red."""
    from gateway.routes.projects import agent_runs

    assert guarded.ANSWER_READ == agent_runs._ANSWER_STALE
    source = (REPO_ROOT / "workbench/control_plane/src/app/projects/lib/agentRuns.ts"
              ).read_text(encoding="utf-8")
    found = re.search(r'export const ARE_YOU_SURE = "([^"]+)";', source)
    assert found is not None and found.group(1) == guarded.ARE_YOU_SURE


# ── No person, no read ──────────────────────────────────────────────────────


@pytest.mark.parametrize("act", ACTS, ids=ACT_IDS)
async def test_the_tool_refuses_a_run_with_no_person_before_any_call(act: dict,
                                                                    monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    with no_person_scope():
        said = await _change(**act)
    assert NO_PERSON_TEXT in said
    assert calls == [] and asked == []


# ── The withdraw ────────────────────────────────────────────────────────────


async def test_withdraw_posts_only_after_a_confirmed_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    said = await _change(withdraw=True)
    assert len(asked) == 1
    card = asked[0]
    assert card["title"] == "Take back your answer?"
    for words in ("#7", "Fix the extruder", "Restart the box?", "Restart it now"):
        assert words in card["context"], words
    assert card["context"].split("\n")[1].startswith("impact: 1 answer")
    assert _posts(calls) == [("POST", f"/projects/agent-runs/{RUN}/answer/withdraw")]
    body = writes(calls)[0]["json"]
    assert body["answer_id"] == ANSWER and set(body) == {"answer_id", "idempotency_key"}
    assert f"full_id: {TASK}" in said and "open again" in said


async def test_withdraw_declined_writes_nothing(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = deny(monkeypatch)
    said = await _change(withdraw=True)
    assert said == guarded.CANCELLED and len(asked) == 1
    assert writes(calls) == []


async def test_a_withdraw_with_a_new_answer_is_refused_before_any_call(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    for extra in ({"option": "no"}, {"answer": "Wait"}):
        assert await _change(withdraw=True, **extra) == guarded.ANSWER_BOTH
    assert calls == []


# ── The replace ─────────────────────────────────────────────────────────────


async def test_replace_sends_the_option_id_of_the_label(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    said = await _change(option="not now")
    assert len(asked) == 1
    card = asked[0]
    assert card["title"] == "Change your answer?"
    assert "«Restart it now» → «Not now»" in card["context"]
    assert guarded.ARE_YOU_SURE not in card["title"] + card["context"]
    assert _posts(calls) == [("POST", f"/projects/agent-runs/{RUN}/answer/replace")]
    body = writes(calls)[0]["json"]
    assert body["answer_id"] == ANSWER and body["selected"] == ["no"] and "body" not in body
    assert "«Not now»" in said and f"full_id: {TASK}" in said


async def test_replace_takes_a_text_with_or_without_an_option(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    approve(monkeypatch)
    await _change(answer="Wait until Friday")
    await _change(option="no", answer="And ask me again")
    sent = [c["json"] for c in writes(calls)]
    assert sent[0]["body"] == "Wait until Friday" and "selected" not in sent[0]
    assert sent[1]["selected"] == ["no"] and sent[1]["body"] == "And ask me again"


async def test_a_costly_option_asks_are_you_sure_on_the_card(monkeypatch) -> None:
    """SW-43b: an option with ``confirm: true`` asks twice on the run card. The
    chat card is the one step, so it carries the same words."""
    _gateway(monkeypatch, answer=_answer(["no"], ["Not now"]))
    asked = approve(monkeypatch)
    await _change(option="Restart it now")
    card = asked[0]
    assert card["title"].startswith(guarded.ARE_YOU_SURE)
    assert f"note: {guarded.ARE_YOU_SURE}" in card["context"]
    assert "«Restart it now»" in card["context"]


async def test_the_same_answer_again_changes_nothing(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    assert await _change(option="go") == guarded.ANSWER_SAME
    assert asked == [] and writes(calls) == []


async def test_an_unknown_option_lists_the_options_and_asks_no_card(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    said = await _change(option="maybe")
    assert "matches no option" in said and "«Not now» (id «no»)" in said
    nothing = await _change()
    assert "Give the new answer" in nothing and "«Restart it now»" in nothing
    assert asked == [] and writes(calls) == []


async def test_a_name_that_matches_two_options_is_a_question(monkeypatch) -> None:
    twins = [{"id": "a", "label": "Restart"}, {"id": "b", "label": "restart"}]
    calls = _gateway(monkeypatch, question={**_question(), "signal_metadata": {
        "options": twins, "allow_multiple": False}})
    asked = approve(monkeypatch)
    assert "matches two options" in await _change(option="Restart")
    assert asked == [] and writes(calls) == []


async def test_a_question_of_one_option_refuses_two(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    asked = approve(monkeypatch)
    assert "matches no option" in await _change(option="no, later")
    assert asked == [] and writes(calls) == []
    many = _gateway(monkeypatch, question=_question(many=True))
    await _change(option="no, later")
    assert len(asked) == 1
    assert [c["json"]["selected"] for c in writes(many)] == [["no", "later"]]


# ── The refusals before any card ────────────────────────────────────────────


@pytest.mark.parametrize("act", ACTS, ids=ACT_IDS)
async def test_an_answer_the_agent_read_gets_no_card(act: dict, monkeypatch) -> None:
    calls = _gateway(monkeypatch, run=_run_row(changeable=False))
    asked = approve(monkeypatch)
    said = await _change(**act)
    assert said.startswith("Refused:") and guarded.ANSWER_READ in said
    assert asked == [] and writes(calls) == []
    assert "full_id" not in said, "a refusal has no id line, so no receipt reads it as done"


@pytest.mark.parametrize("act", ACTS, ids=ACT_IDS)
async def test_the_run_of_another_member_gets_no_card(act: dict, monkeypatch) -> None:
    calls = _gateway(monkeypatch, run=_run_row(member="someone@else.test"))
    asked = approve(monkeypatch)
    assert "another member" in await _change(**act)
    assert asked == [] and writes(calls) == []


@pytest.mark.parametrize("act", ACTS, ids=ACT_IDS)
async def test_the_flag_off_and_runs_off_get_one_plain_sentence(act: dict, monkeypatch) -> None:
    off = _gateway(monkeypatch, run=_run_row(flag=False))
    asked = approve(monkeypatch)
    assert await _change(**act) == guarded.ANSWER_CHANGE_OFF
    no_runs = _gateway(monkeypatch, runs_off=True)
    assert await _change(**act) == guarded.ANSWER_RUNS_OFF
    assert asked == [] and writes(off) == [] and writes(no_runs) == []


@pytest.mark.parametrize("act", ACTS, ids=ACT_IDS)
async def test_the_answer_to_an_archive_request_stays_on_the_card(act: dict, monkeypatch) -> None:
    """D-OP-13: an archive request is answered on the task card only, so the
    chat neither takes back nor changes that answer."""
    request = {"kind": "archive_task", "expires_at": "2099-01-01T00:00:00+00:00"}
    calls = _gateway(monkeypatch, question=_question(request=request),
                     answer=_answer(["decline"], ["Decline"]))
    asked = approve(monkeypatch)
    said = await _change(**act)
    assert "archive" in said and f"/projects?task={TASK}" in said
    assert asked == [] and writes(calls) == []


async def test_a_list_of_tasks_is_refused_before_any_call(monkeypatch) -> None:
    calls = _gateway(monkeypatch)
    said = await skill_projects.change_agent_answer(f"{TASK},{uuid.uuid4()}", withdraw=True)
    assert "ONE" in said and calls == []


# ── The refusals of the routes after the card ───────────────────────────────


@pytest.mark.parametrize(("status", "detail", "words"), [
    (409, "The agent already has your answer. Send a comment instead.", "already has your"),
    (409, "This answer is no longer the live answer. The question may be open again.",
     "no longer the live answer"),
    (403, "This agent run belongs to another member.", "only the member"),
    (404, "Not Found", "found no answer"),
    (422, "selected names an id that is not an option.", "not an option"),
])
async def test_a_refusal_of_the_route_is_one_sentence(status: int, detail: str, words: str,
                                                      monkeypatch) -> None:
    """The agent read the answer between the card and the click: the route
    takes its lock, finds the read, and answers 409. The member reads one
    sentence and nothing else."""
    _gateway(monkeypatch, refuse=FakeResponse({"detail": detail}, status))
    approve(monkeypatch)
    said = await _change(option="no")
    assert said.startswith("Refused:") and words in said, said
    assert "full_id" not in said


def test_the_tool_names_only_its_run_paths() -> None:
    """Each run path of the tool is a whole literal of ``guarded.py``, and the
    activity post is not one of them."""
    text = Path(guarded.__file__).read_text(encoding="utf-8")
    assert "agent-runs/{rid}/activities" not in text
    assert sorted(set(re.findall(r'"/projects/agent-runs/[^"]*"', text))) == [
        '"/projects/agent-runs/{rid}"', '"/projects/agent-runs/{rid}/answer/replace"',
        '"/projects/agent-runs/{rid}/answer/withdraw"']
