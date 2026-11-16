"""EAC-1: the card of a new task that the member edits (task 3468).

Spec: ``project-docs/specs/projects_ai_chat.md`` §32.4, §32.5 row EAC-1 and
§32.6. The R8 half is ``test_task_edit_card_r8.py``.

The rules, each with its fence below (R7):

1. ``task_card.parse_task_edit_answer`` is strict and fails closed: an extra
   key, a missing key, a key twice, a wrong type, a text over its limit, a
   date with a space, a forged project id, a ``reply`` or ``rows`` key and an
   answer with no session member each read as NO answer. Nothing is trimmed.
2. ``PROJECT_EDIT_CARD_ORGS`` is empty by default, and its one reader fails
   closed. A reload applies a change.
3. Off, the card of ``create_task`` is the card of before, byte for byte. A
   held chat, a WhatsApp run and a repeating task get no edit form either.
4. On, a plain ``APPROVE`` writes the original values. An edited answer runs
   the prepare step again AFTER the card, and the write takes its values.
   Only the run's own member may answer. A late approve writes the original.
5. A declined card and a card that a chat message closed use the one table
   of outcomes, and the unsent values stay out of the model text.
6. ``parse_task_edit_answer`` is the ONE parser of the ``edit`` key, and
   ``request_confirmation`` reads ``edit`` nowhere else (a source fence).

Mutations (R7), run on 2026-11-16. Each turned the named tests red:

* M1. ``task_card._values_ok``: allow an extra key (``>=`` for ``!=``).
  ``test_an_extra_key_is_no_answer``.
* M1b. ``task_card._values_ok``: allow a missing key (``<=`` for ``!=``).
  ``test_a_missing_key_is_no_answer``.
* M2. ``task_card.parse_task_edit_answer``: take ``edit`` beside other keys.
  ``test_an_extra_key_beside_edit_is_no_answer``.
* M3. ``task_card.parse_task_edit_answer``: strip the description.
  ``test_whitespace_is_not_trimmed``.
* M4. ``task_card.task_edit_card_on``: ``return True`` for an empty list.
  ``test_the_flag_is_empty_by_default_and_reads_off``.
* M5. ``ask_tools._task_edit_form``: drop the ``task_edit_card_channel_open``
  test. ``test_a_whatsapp_run_gets_no_edit_form``,
  ``test_off_the_card_is_the_card_of_before``.
* M6. ``writes._card_edit``: keep the task of the card (``edited = new``).
  ``test_an_edited_due_date_and_assignee_are_written_as_edited``.
* M7. ``ask_tools._verdict``: read a plain ``APPROVE`` on an edit card as a
  REJECT. ``test_a_plain_approve_writes_the_original_values``.
* M8. ``ask_tools._verdict``: approve an answer that does not parse.
  ``test_a_reply_key_on_an_edit_card_is_a_reject``,
  ``test_a_forged_project_id_writes_nothing``.
* M9. ``ask_tools._task_edit_form``: keep the form with no bound member.
  ``test_a_card_with_no_member_bound_gets_no_form``.
* M10. ``writes._new_task_form``: drop the held and repeating test.
  ``test_a_held_chat_whatsapp_and_a_repeating_task_get_no_edit_form``.
* M11. ``ask_tools._verdict``: read ``result.get("edit")`` too.
  ``test_request_confirmation_reads_edit_nowhere_else``.
* M12. ``writes._card_edit``: no stranger warning.
  ``test_a_stranger_is_written_and_warned_as_the_card_warns``.
* M13. ``ask_tools._block_on``: give ``True`` for an edited answer.
  ``test_an_edited_due_date_and_assignee_are_written_as_edited``.

Review round (P1a), run on 2026-11-16. Before the fix, the old code ran
``_prepare_new_task`` again on the model's raw words: three of the four new
tests below were red on it. Each mutation of the fix turned its test red:

* A1. ``writes._edited_task``: drop the people that the card showed.
  ``test_an_untouched_fuzzy_name_is_not_resolved_again``.
* A2. ``writes._edited_task``: ``exact=False`` for an edited person.
  ``test_an_edited_name_resolves_by_exact_match_only``.
* A3. ``writes._edited_lane``: ``_named`` (with ``decide``) for ``_one_named``.
  ``test_an_edited_status_resolves_by_exact_match_only``.
* A4. ``writes._prepare_new_task``: keep the raw name as ``who``.
  ``test_a_plain_approve_writes_the_resolved_person_of_a_name``.
* A5. ``writes._resolve_assignee_name``: ask ``decide`` with ``exact`` too.
  ``test_an_edited_name_resolves_by_exact_match_only``.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("orchestrator", reason="the orchestrator is not installed")

import acb_skills.ask_tools as at
from acb_skills import task_card as tc
from orchestrator import executor
from skill_projects import writes as W
from skill_projects.refusals import refusals_as_text

from tests.unit._projects_agent_fakes import fake_gateway, writes

ROOT = Path(__file__).resolve().parents[2]
ASK_TOOLS = ROOT / "packages" / "acb_skills" / "acb_skills" / "ask_tools.py"
TASK_CARD = ROOT / "packages" / "acb_skills" / "acb_skills" / "task_card.py"
WRITES = ROOT / "apps" / "skills" / "skill-projects" / "skill_projects" / "writes.py"

MEMBER = "ana@acme.test"
OTHER_MEMBER = "bo@acme.test"
PID = "0f8fad5b-d9cb-469f-a165-70867728950e"
SPACE = "1f8fad5b-d9cb-469f-a165-70867728950e"
TODO = "3f8fad5b-d9cb-469f-a165-70867728950e"
DOING = "4f8fad5b-d9cb-469f-a165-70867728950e"
NEW_ID = "5f8fad5b-d9cb-469f-a165-70867728950e"
STATUSES = [
    {"id": TODO, "name": "To do", "category": "todo"},
    {"id": DOING, "name": "Doing", "category": "in_progress"},
]
PEOPLE = [
    {"assignee": "priya@acme.test", "name": "Priya"},
    {"assignee": "ravi@acme.test", "name": "Ravi"},
]
KNOWN = {"priya@acme.test": "Priya", "ravi@acme.test": "Ravi"}

#: The model's call, as the card shows it.
CALL: dict[str, Any] = {
    "project_id": PID,
    "title": "Call the vendor",
    "status": "to do",
    "assignees": "Priya",
    "due": "2026-12-01",
    "description": "Ask about the nozzle.",
}

#: The form of :data:`CALL`, as the tool builds it.
FORM_VALUES: dict[str, Any] = {
    "due": "2026-12-01",
    "start": "",
    "status": "To do",
    "important": None,
    "leveraged": False,
    "description": "Ask about the nozzle.",
    "assignees": ["priya@acme.test"],
}


def _answer(**changes: Any) -> str:
    """An edit answer: the form's values with *changes*."""
    return "APPROVE " + json.dumps({"edit": {**FORM_VALUES, **changes}})


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """``PROJECT_EDIT_CARD_ORGS`` for this test only: "" is off, "*" is on."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "project_edit_card_orgs", value)


def _gateway(call: dict[str, Any]) -> Any:
    """The routes that ``create_task`` reads and writes, as the member."""
    path, method = call["path"], call["method"]
    params = call.get("params") or {}
    if method == "GET" and path == f"/projects/nodes/{PID}":
        return {"id": PID, "name": "Ops", "status": "active",
                "parent_project_id": SPACE, "personal": False, "kind": "project"}
    if method == "GET" and path == f"/projects/nodes/{PID}/statuses":
        return {"rows": STATUSES}
    if method == "GET" and path == f"/projects/nodes/{PID}/audience":
        return {"count": 2, "sample": ["Ana", "Priya"], "without_access": []}
    if method == "GET" and path == "/projects/assignees":
        q = str(params.get("q") or "").lower()
        return {"people": [p for p in PEOPLE if q in p["name"].lower()], "agents": []}
    if method == "GET" and path == "/projects/people/names":
        asked = str(params.get("emails") or "").split(",")
        return {"names": {e: KNOWN[e] for e in asked if e in KNOWN}}
    if method == "POST" and path == "/projects/tasks":
        body = call["json"]
        return {"id": NEW_ID, "task_number": 9, "title": body["title"], "project_id": PID,
                "status_id": body.get("status_id", TODO), "due_at": body.get("due_at"),
                "assignees": []}
    if method == "PUT" and path == f"/projects/tasks/{NEW_ID}/assignees":
        return {"assignees": call["json"]["assignees"]}
    return {"rows": []}


def _posted(calls: list[dict]) -> list[dict]:
    return [c["json"] for c in writes(calls) if c["path"] == "/projects/tasks"]


def _assigned(calls: list[dict]) -> list[list[str]]:
    return [c["json"]["assignees"] for c in writes(calls) if c["method"] == "PUT"]


async def _card_of(q: asyncio.Queue) -> dict[str, Any]:
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=10)
        if ev.get("name") == "confirmation_requested":
            return ev


def _drive(monkeypatch: pytest.MonkeyPatch, answer: str | None, *,
           responder: str = MEMBER, call: dict[str, Any] | None = None,
           revised: bool = False) -> tuple[dict[str, Any] | None, str, list[dict]]:
    """``create_task`` with the REAL card gate on the native queue path, the
    card answered with *answer* by *responder*. *answer* None expects no card.
    Gives the card event, the tool's text and the gateway calls."""
    calls = fake_gateway(monkeypatch, _gateway, user=MEMBER)
    tool = refusals_as_text(W.create_task)

    async def _run() -> tuple[dict[str, Any] | None, str]:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-eac1")
        task = asyncio.ensure_future(tool(**(call or CALL)))
        if answer is None:
            return None, await asyncio.wait_for(task, timeout=10)
        card = await _card_of(q)
        rid = card["value"]["request_id"]
        if revised:
            executor._pending_user_input[rid].set_result(
                {"answer": answer, "revised": True, "responder": responder})
        else:
            assert executor.resolve_user_input(
                rid, answer, was_freeform=False,
                thread_id=executor._pending_user_input.owner_of(rid), responder=responder)
        return card, await asyncio.wait_for(task, timeout=10)

    card, out = asyncio.run(_run())
    return card, out, calls


# ── 1. The parser ────────────────────────────────────────────────────────────


def test_a_whole_answer_parses_to_its_values_and_the_session_member() -> None:
    edit = tc.parse_task_edit_answer(_answer(due="2026-12-24"), " Ana@Acme.test ")
    assert isinstance(edit, tc.TaskEdit)
    assert edit.values() == {**FORM_VALUES, "due": "2026-12-24"}
    assert edit.responder == MEMBER  # the session's member, never the answer's
    assert edit, "an edit is an approve"


def test_a_plain_approve_is_no_edit() -> None:
    assert tc.parse_task_edit_answer("APPROVE", MEMBER) is None
    assert tc.parse_task_edit_answer("REJECT", MEMBER) is None


@pytest.mark.parametrize("key", sorted(tc.TASK_EDIT_KEYS))
def test_a_missing_key_is_no_answer(key: str) -> None:
    values = {k: v for k, v in FORM_VALUES.items() if k != key}
    assert tc.parse_task_edit_answer("APPROVE " + json.dumps({"edit": values}), MEMBER) is None


@pytest.mark.parametrize("extra", [
    {"project_id": SPACE},  # a forged project id
    {"title": "Another title"},
    {"files": ["f-1"]},
    {"responder": OTHER_MEMBER},
    {"status_id": DOING},
])
def test_an_extra_key_is_no_answer(extra: dict[str, Any]) -> None:
    assert tc.parse_task_edit_answer(_answer(**extra), MEMBER) is None


@pytest.mark.parametrize("body", [
    {"edit": FORM_VALUES, "reply": {}},
    {"edit": FORM_VALUES, "rows": ["r-1"]},
    {"edit": FORM_VALUES, "project_id": SPACE},
    {"reply": FORM_VALUES},
    {"rows": ["r-1"]},
    {},
])
def test_an_extra_key_beside_edit_is_no_answer(body: dict[str, Any]) -> None:
    assert tc.parse_task_edit_answer("APPROVE " + json.dumps(body), MEMBER) is None


@pytest.mark.parametrize("bad", [
    {"due": 20261201}, {"due": None}, {"due": "2026-02-30"}, {"due": "12/01/2026"},
    # Full-width digits: a digit of another script is no date.
    {"due": "".join(chr(0xFF10 + int(c)) if c.isdigit() else c for c in "2026-12-01")},
    {"start": "next week"}, {"start": False},
    {"status": 5}, {"status": None}, {"status": "   "}, {"status": "To\ndo"},
    {"important": "yes"}, {"important": 1}, {"leveraged": None}, {"leveraged": 0},
    {"description": None}, {"description": ["a"]}, {"description": "a\x00b"},
    {"assignees": "priya@acme.test"}, {"assignees": [1]}, {"assignees": [""]},
    {"assignees": ["   "]}, {"assignees": ["a@x.test\nb@x.test"]},
    {"assignees": ["priya@acme.test", "PRIYA@acme.test"]}, {"assignees": None},
])
def test_a_wrong_type_or_shape_is_no_answer(bad: dict[str, Any]) -> None:
    assert tc.parse_task_edit_answer(_answer(**bad), MEMBER) is None


def test_a_text_over_its_limit_is_refused_never_cut() -> None:
    at_limit = _answer(description="d" * tc.DESCRIPTION_MAX, status="s" * tc.STATUS_MAX,
                       assignees=[f"p{i}@acme.test" for i in range(tc.MAX_ASSIGNEES)])
    assert tc.parse_task_edit_answer(at_limit, MEMBER) is not None
    for over in (
        _answer(description="d" * (tc.DESCRIPTION_MAX + 1)),
        _answer(status="s" * (tc.STATUS_MAX + 1)),
        _answer(assignees=["a" * (tc.ASSIGNEE_MAX - 9) + "@acme.test"]),
        _answer(assignees=[f"p{i}@acme.test" for i in range(tc.MAX_ASSIGNEES + 1)]),
        _answer(description="d") + " " * tc.MAX_TASK_EDIT_ANSWER,
    ):
        assert tc.parse_task_edit_answer(over, MEMBER) is None


def test_whitespace_is_not_trimmed() -> None:
    kept = tc.parse_task_edit_answer(
        _answer(description="  keep it  \n", status=" To do ",
                assignees=[" priya@acme.test "]), MEMBER)
    assert kept is not None
    assert kept.description == "  keep it  \n"
    assert kept.status == " To do "
    assert kept.assignees == (" priya@acme.test ",)
    # A date is a date exactly: a space is no date, and nothing trims it.
    assert tc.parse_task_edit_answer(_answer(due=" 2026-12-01"), MEMBER) is None
    assert tc.parse_task_edit_answer(_answer(start="2026-12-01 "), MEMBER) is None


def test_a_key_twice_and_a_constant_that_is_not_json_are_no_answer() -> None:
    values = json.dumps(FORM_VALUES)
    twice = '{"edit": ' + values + ', "edit": ' + values + "}"
    assert tc.parse_task_edit_answer("APPROVE " + twice, MEMBER) is None
    inner = values[:-1] + ', "due": "2026-12-02"}'
    assert tc.parse_task_edit_answer('APPROVE {"edit": ' + inner + "}", MEMBER) is None
    nan = json.dumps({"edit": FORM_VALUES}).replace('"leveraged": false', '"leveraged": NaN')
    assert tc.parse_task_edit_answer("APPROVE " + nan, MEMBER) is None
    assert tc.parse_task_edit_answer("APPROVE {not json", MEMBER) is None
    assert tc.parse_task_edit_answer("APPROVE" + json.dumps({"edit": FORM_VALUES}), MEMBER) is None


def test_an_answer_with_no_session_member_is_no_answer() -> None:
    for who in ("", "   ", None, "shared-room", 7):
        assert tc.parse_task_edit_answer(_answer(), who) is None
    assert tc.parse_task_edit_answer(None, MEMBER) is None
    assert tc.parse_task_edit_answer(b"APPROVE {}", MEMBER) is None


def test_changed_values_compares_with_the_tools_own_form() -> None:
    form = tc.create_task_form(project_id=PID, **{**FORM_VALUES})
    assert form is not None
    same = tc.parse_task_edit_answer(_answer(), MEMBER)
    assert same is not None and tc.changed_values(form, same) == {}
    edit = tc.parse_task_edit_answer(_answer(due="2026-12-24", important=False), MEMBER)
    assert edit is not None
    assert tc.changed_values(form, edit) == {"due": "2026-12-24", "important": False}


# ── 2. The form ──────────────────────────────────────────────────────────────


def test_the_form_holds_the_project_and_each_value() -> None:
    form = tc.create_task_form(project_id=PID, **FORM_VALUES)
    assert form == {"kind": tc.CREATE_TASK, "project_id": PID, "values": FORM_VALUES}


@pytest.mark.parametrize("bad", [
    {"due": "next friday"}, {"description": "d" * (tc.DESCRIPTION_MAX + 1)},
    {"assignees": [f"p{i}@acme.test" for i in range(tc.MAX_ASSIGNEES + 1)]},
])
def test_a_value_outside_the_form_gives_the_card_of_before(bad: dict[str, Any]) -> None:
    assert tc.create_task_form(project_id=PID, **{**FORM_VALUES, **bad}) is None


@pytest.mark.parametrize("form", [
    {"kind": tc.CREATE_TASK, "project_id": PID},
    {"kind": "update_task", "project_id": PID, "values": FORM_VALUES},
    {"kind": tc.CREATE_TASK, "project_id": PID.upper(), "values": FORM_VALUES},
    {"kind": tc.CREATE_TASK, "project_id": "ops", "values": FORM_VALUES},
    {"kind": tc.CREATE_TASK, "project_id": PID, "values": FORM_VALUES, "late": "x"},
    {"kind": tc.CREATE_TASK, "project_id": PID, "values": {**FORM_VALUES, "title": "x"}},
    "a form",
])
def test_a_bad_form_is_a_programming_error(form: Any) -> None:
    with pytest.raises(ValueError):
        tc.clean_task_edit_form(form)


# ── 3. The flag ──────────────────────────────────────────────────────────────


def test_the_flag_is_empty_by_default_and_reads_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["project_edit_card_orgs"].default == ""
    _flag(monkeypatch, "")
    assert not tc.task_edit_card_on("0f8fad5b-d9cb-469f-a165-70867728950e")
    assert not tc.task_edit_card_on()


def test_the_flag_names_orgs_or_star_and_reads_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    org = "0f8fad5b-d9cb-469f-a165-70867728950e"
    _flag(monkeypatch, f" {org.upper()} ,")
    assert tc.task_edit_card_on(org)
    assert not tc.task_edit_card_on("1f8fad5b-d9cb-469f-a165-70867728950e")
    # No tenant under a list of ids reads off (fail closed).
    assert not tc.task_edit_card_on()
    _flag(monkeypatch, "*")
    assert tc.task_edit_card_on("any-org") and tc.task_edit_card_on()


def test_the_flag_takes_the_org_of_the_run_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common.db import bind_tenant, release_tenant

    org = "0f8fad5b-d9cb-469f-a165-70867728950e"
    _flag(monkeypatch, org)
    token = bind_tenant(org)
    try:
        assert tc.task_edit_card_on()
    finally:
        release_tenant(token)
    token = bind_tenant("1f8fad5b-d9cb-469f-a165-70867728950e")
    try:
        assert not tc.task_edit_card_on()
    finally:
        release_tenant(token)


def test_the_reader_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import acb_common

    def _boom() -> Any:
        raise RuntimeError("settings fault")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert tc.task_edit_card_on("any-org") is False


def test_the_flag_reloads_and_stays_operator_only() -> None:
    from acb_common.env_guard import is_platform_env
    from gateway import flag_reload as fr

    assert fr.reloadable("PROJECT_EDIT_CARD_ORGS")
    assert is_platform_env("PROJECT_EDIT_CARD_ORGS")
    doc = (ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "`PROJECT_EDIT_CARD_ORGS`" in doc and "task_edit_card_on" in doc
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nPROJECT_EDIT_CARD_ORGS=\n" in example


def test_no_module_reads_the_setting_but_its_one_reader() -> None:
    hits = []
    for base in (ROOT / "packages", ROOT / "apps"):
        for p in base.rglob("*.py"):
            if "project_edit_card_orgs" in p.read_text(encoding="utf-8", errors="replace"):
                hits.append(p.relative_to(ROOT).as_posix())
    assert sorted(hits) == [
        "packages/acb_common/acb_common/settings.py",
        "packages/acb_skills/acb_skills/task_card.py",
    ]


# ── 4. The card gate ─────────────────────────────────────────────────────────


def _gate(monkeypatch: pytest.MonkeyPatch, answer: str, *, responder: str = MEMBER,
          member: str = MEMBER, edit: Any = "form", **kw: Any) -> tuple[dict, Any, Any]:
    """One card of the real gate on the native queue, answered. Gives the
    event value, the answer of the gate and its outcome."""
    form = tc.create_task_form(project_id=PID, **FORM_VALUES) if edit == "form" else edit

    async def _tool() -> tuple[Any, Any]:
        # The card and the read of its outcome run in ONE task, as a tool does.
        got = await at.request_confirmation(
            "Create this task?", "«Call the vendor»", "title: «Call the vendor»",
            fenced=True, close_on_steer=True, responder=member or None, edit=form, **kw)
        return got, at.last_card_outcome()

    async def _run() -> tuple[dict, Any, Any]:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-eac1-gate")
        task = asyncio.ensure_future(_tool())
        card = await _card_of(q)
        rid = card["value"]["request_id"]
        executor.resolve_user_input(rid, answer, was_freeform=False,
                                    thread_id=executor._pending_user_input.owner_of(rid),
                                    responder=responder)
        got, outcome = await asyncio.wait_for(task, timeout=10)
        return card["value"], got, outcome

    return asyncio.run(_run())


def test_on_the_card_carries_the_form(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    value, got, outcome = _gate(monkeypatch, _answer(due="2026-12-24"))
    assert value["edit"] == {"kind": tc.CREATE_TASK, "project_id": PID, "values": FORM_VALUES}
    assert isinstance(got, tc.TaskEdit) and got.due == "2026-12-24"
    assert outcome == at.CARD_APPROVED


def test_a_plain_approve_on_an_edit_card_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    _value, got, outcome = _gate(monkeypatch, "APPROVE")
    assert got is True and outcome == at.CARD_APPROVED


@pytest.mark.parametrize("answer", [
    'APPROVE {"reply": {"action": "send", "body": "x"}}',
    'APPROVE {"rows": ["r-1"]}',
    'APPROVE {"edit": {"due": "2026-12-24"}}',
    "APPROVE " + json.dumps({"edit": {**FORM_VALUES, "project_id": SPACE}}),
    "REJECT",
    "approve please",
])
def test_a_reply_key_on_an_edit_card_is_a_reject(
    monkeypatch: pytest.MonkeyPatch, answer: str,
) -> None:
    _flag(monkeypatch, "*")
    _value, got, outcome = _gate(monkeypatch, answer)
    assert got is False and outcome == at.CARD_DECLINED


def test_only_the_runs_own_member_may_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    _value, got, outcome = _gate(monkeypatch, _answer(due="2026-12-24"), responder=OTHER_MEMBER)
    assert got is False and outcome == at.CARD_OTHER_MEMBER
    _value, got, outcome = _gate(monkeypatch, "APPROVE", responder=OTHER_MEMBER)
    assert got is False and outcome == at.CARD_OTHER_MEMBER


def test_off_the_card_is_the_card_of_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """The event of a card with the form dropped equals the event of a card
    that never had one, byte for byte, but the request id."""
    _flag(monkeypatch, "")
    off, got, _o = _gate(monkeypatch, "APPROVE")
    assert "edit" not in off and got is True
    plain, _g, _o = _gate(monkeypatch, "APPROVE", edit=None)

    def _bytes(v: dict) -> str:
        return json.dumps({k: x for k, x in v.items() if k != "request_id"}, sort_keys=True)

    assert _bytes(off) == _bytes(plain)
    # Off, an edit answer is no approve: the card has no form to edit.
    _v, got, _o = _gate(monkeypatch, _answer(due="2026-12-24"))
    assert got is False


def test_a_card_with_no_member_bound_gets_no_form(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    value, got, _o = _gate(monkeypatch, "APPROVE", member="", responder=MEMBER)
    assert "edit" not in value and got is True


def test_a_whatsapp_run_gets_no_edit_form(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    monkeypatch.setattr(at, "_whatsapp_run", lambda: True)
    assert not at.task_edit_card_channel_open()
    value, _got, _o = _gate(monkeypatch, "APPROVE")
    assert "edit" not in value


def test_a_run_with_no_channel_gets_no_edit_form(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")

    async def _run() -> tuple[bool, Any]:
        executor._active_run_queue.set(None)
        executor._stream_relay_thread_id.set("")
        form = tc.create_task_form(project_id=PID, **FORM_VALUES)
        return at.task_edit_card_channel_open(), await at.request_confirmation(
            "Create this task?", responder=MEMBER, edit=form)

    assert asyncio.run(_run()) == (False, False)


def test_an_edit_form_goes_on_a_plain_card_only() -> None:
    form = tc.create_task_form(project_id=PID, **FORM_VALUES)
    rows = [{"id": "r-1", "label": "one"}]
    for kw in ({"rows": rows}, {"request_id": "a" * 32, "rows": rows, "with_answer": True}):
        with pytest.raises(ValueError):
            asyncio.run(at.request_confirmation("Create?", responder=MEMBER, edit=form, **kw))
    with pytest.raises(ValueError):
        asyncio.run(at.request_confirmation("Create?", responder=MEMBER,
                                            edit={"kind": tc.CREATE_TASK}))


# ── 5. create_task ───────────────────────────────────────────────────────────


def _stub(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[dict]:
    """``request_confirmation`` as a recorder that answers *answer*."""
    asked: list[dict] = []

    async def _gate_stub(**kwargs: Any) -> Any:
        asked.append(dict(kwargs))
        return answer

    monkeypatch.setattr(at, "request_confirmation", _gate_stub)
    return asked


def _tool_run(monkeypatch: pytest.MonkeyPatch, call: dict[str, Any] | None = None) -> str:
    async def _run() -> str:
        q: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(q)
        executor._stream_relay_thread_id.set("t-eac1-stub")
        return await refusals_as_text(W.create_task)(**(call or CALL))

    return asyncio.run(_run())


def test_off_create_task_asks_with_the_kwargs_of_before(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "")
    asked = _stub(monkeypatch, True)
    calls = fake_gateway(monkeypatch, _gateway, user=MEMBER)
    _tool_run(monkeypatch)
    [card] = asked
    assert set(card) == {"title", "detail", "context", "fenced", "close_on_steer"}
    off_body = _posted(calls)
    _flag(monkeypatch, "*")
    asked_on = _stub(monkeypatch, True)
    calls_on = fake_gateway(monkeypatch, _gateway, user=MEMBER)
    _tool_run(monkeypatch)
    [card_on] = asked_on
    # On, the card adds the form and binds the member. Its text is the same.
    assert {k: v for k, v in card_on.items() if k not in ("edit", "responder")} == card
    assert card_on["responder"] == MEMBER
    assert card_on["edit"] == {"kind": tc.CREATE_TASK, "project_id": PID, "values": FORM_VALUES}
    assert _posted(calls_on) == off_body


@pytest.mark.parametrize("case", ["held", "whatsapp", "repeating", "no-address"])
def test_a_held_chat_whatsapp_and_a_repeating_task_get_no_edit_form(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    _flag(monkeypatch, "*")
    asked = _stub(monkeypatch, True)
    fake_gateway(monkeypatch, _gateway, user="shared-room" if case == "no-address" else MEMBER)
    call = dict(CALL)
    if case == "held":
        async def _held() -> bool:
            return True

        monkeypatch.setattr(W, "_held_chat", _held)
    elif case == "whatsapp":
        monkeypatch.setattr(at, "_whatsapp_run", lambda: True)
    elif case == "repeating":
        call.update(repeat="weekly", repeat_on="1")

        async def _clock() -> Any:
            from datetime import date
            return date(2026, 11, 16), "UTC", "UTC"

        monkeypatch.setattr(W, "_member_clock", _clock)
    _tool_run(monkeypatch, call)
    [card] = asked
    assert "edit" not in card and "responder" not in card


def test_a_plain_approve_writes_the_original_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    card, out, calls = _drive(monkeypatch, "APPROVE")
    assert card is not None and "edit" in card["value"]
    [body] = _posted(calls)
    assert body["due_at"] == "2026-12-01" and body["status_id"] == TODO
    assert body["description"] == "Ask about the nozzle."
    assert _assigned(calls) == [["priya@acme.test"]]
    assert out.startswith("Created:"), out


def test_an_edit_that_changes_nothing_writes_what_a_plain_approve_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    _c, _o, plain = _drive(monkeypatch, "APPROVE")
    _c, out, same = _drive(monkeypatch, _answer())
    assert _posted(same) == _posted(plain) and _assigned(same) == _assigned(plain)
    assert out.startswith("Created:"), out


def test_an_edited_due_date_and_assignee_are_written_as_edited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(due="2026-12-24", assignees=["ravi@acme.test"],
                                                 important=True, description="New text."))
    [body] = _posted(calls)
    assert body["due_at"] == "2026-12-24"
    assert body["description"] == "New text."
    assert body["importance"] >= 2  # Important (acb_common.priority.IMPORTANT_AT)
    assert _assigned(calls) == [["ravi@acme.test"]]
    assert "priya" not in json.dumps(_posted(calls) + _assigned(calls))
    assert out.startswith("Created:"), out


def test_an_edited_status_is_resolved_again_after_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    _c, _o, calls = _drive(monkeypatch, _answer(status="doing"))
    [body] = _posted(calls)
    assert body["status_id"] == DOING  # by name, against the project's own lanes
    card_at = next(i for i, c in enumerate(calls) if c["method"] == "POST")
    reads_after = [c["path"] for c in calls[:card_at] if c["method"] == "GET"]
    assert reads_after.count(f"/projects/nodes/{PID}/statuses") == 2, reads_after


#: The model's call with two names that only the ``decide`` engine resolves:
#: "Pri" (no exact person) and "todo" (no exact lane).
FUZZY_CALL: dict[str, Any] = {**CALL, "assignees": "Pri", "status": "todo"}


def _decide(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], int]:
    """``choices.resolve_name`` that picks the close row on its first call for a
    name, and ANOTHER row on each later call. Gives the count of each call."""
    import skill_projects.choices as choices

    seen: dict[tuple[str, str], int] = {}

    async def _pick(rows: list[dict[str, Any]], wanted: str, what: str, **_kw: Any) -> Any:
        n = seen[(what, wanted)] = seen.get((what, wanted), 0) + 1
        if what == "person":
            return PEOPLE[0] if n == 1 else PEOPLE[1]  # Priya, then Ravi
        if what == "status":
            return STATUSES[0] if n == 1 else STATUSES[1]  # To do, then Doing
        return None

    monkeypatch.setattr(choices, "resolve_name", _pick)
    return seen


def test_an_untouched_fuzzy_name_is_not_resolved_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review P1a. The member edits the due date only. The person and the lane
    stay the ones the card showed: no second ``decide`` call can change them,
    and none can refuse after the Approve."""
    _flag(monkeypatch, "*")
    seen = _decide(monkeypatch)
    card, out, calls = _drive(monkeypatch, _answer(due="2026-12-24"), call=FUZZY_CALL)
    assert card is not None
    shown = card["value"]["edit"]["values"]
    # The form holds the RESOLVED values, never the model's raw words.
    assert shown["assignees"] == ["priya@acme.test"] and shown["status"] == "To do"
    [body] = _posted(calls)
    assert body["due_at"] == "2026-12-24"
    assert body["status_id"] == TODO
    assert _assigned(calls) == [["priya@acme.test"]]
    assert seen == {("status", "todo"): 1, ("person", "Pri"): 1}
    assert out.startswith("Created:"), out


def test_a_plain_approve_writes_the_resolved_person_of_a_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verifier's gap: ``who`` is the resolved address of a name-form
    assignee, on the form and on the write."""
    _flag(monkeypatch, "*")
    seen = _decide(monkeypatch)
    card, _o, calls = _drive(monkeypatch, "APPROVE", call=FUZZY_CALL)
    assert card is not None
    assert card["value"]["edit"]["values"]["assignees"] == ["priya@acme.test"]
    assert _assigned(calls) == [["priya@acme.test"]]
    assert seen == {("status", "todo"): 1, ("person", "Pri"): 1}


def test_an_edited_name_resolves_by_exact_match_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edited bare name resolves only when one person has exactly that
    name. A close name is refused in the tool's own words, with no ``decide``
    call and no question after the Approve, and nothing is written."""
    _flag(monkeypatch, "*")
    seen = _decide(monkeypatch)
    _c, out, calls = _drive(monkeypatch, _answer(assignees=["Ravi"]))
    assert _assigned(calls) == [["ravi@acme.test"]], out
    _c, out, calls = _drive(monkeypatch, _answer(assignees=["Rav"]))
    assert "No person is called exactly «Rav»" in out, out
    assert writes(calls) == []
    assert ("person", "Rav") not in seen and ("person", "Ravi") not in seen


def test_an_edited_status_resolves_by_exact_match_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    seen = _decide(monkeypatch)
    _c, out, calls = _drive(monkeypatch, _answer(status="Doin"))
    assert "No status is called «Doin»" in out, out
    assert writes(calls) == []
    assert ("status", "Doin") not in seen


def test_an_unknown_status_is_refused_and_nothing_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(status="Shipped"))
    assert "No status is called «Shipped»" in out, out
    assert writes(calls) == []


def test_a_bad_edited_due_is_refused_and_nothing_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parser refuses a date that is not a date, so the card is a REJECT."""
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(due="2026-02-30"))
    assert out == at.card_outcome_text(at.CARD_DECLINED)
    assert writes(calls) == []


def test_a_stranger_is_written_and_warned_as_the_card_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(assignees=["typo@acme.test"]))
    assert _assigned(calls) == [["typo@acme.test"]]
    assert "  not in the directory: typo@acme.test" in out, out


def test_an_agent_assignee_in_a_run_that_may_not_send_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import acb_skills.egress as egress

    _flag(monkeypatch, "*")
    monkeypatch.setattr(egress, "no_egress_for_this_run", lambda: True)
    _c, out, calls = _drive(monkeypatch, _answer(assignees=["agent:ops-bot"]))
    assert out == W.AGENT_ASSIGNEE_REFUSED
    assert writes(calls) == []


def test_another_members_edit_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(due="2026-12-24"), responder=OTHER_MEMBER)
    assert out == at.card_outcome_text(at.CARD_OTHER_MEMBER)
    assert writes(calls) == []


def test_a_forged_project_id_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(project_id=SPACE))
    assert out == at.card_outcome_text(at.CARD_DECLINED)
    assert writes(calls) == []


def test_a_declined_edit_card_uses_the_one_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, "REJECT")
    assert out == at.card_outcome_text(at.CARD_DECLINED)
    assert writes(calls) == []


def test_a_card_closed_by_a_message_keeps_the_values_out_of_the_model_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, "*")
    _c, out, calls = _drive(monkeypatch, _answer(description="UNSENT-EDIT-7731"), revised=True)
    assert out == at.card_outcome_text(at.CLOSED_BY_YOUR_MESSAGE)
    assert "UNSENT-EDIT-7731" not in out
    assert writes(calls) == []


def test_a_late_approve_writes_the_original_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """HRD-22 and RESUME: the card of a late approve takes its grant with no
    answer of its own, so the original values are written. No edit applies."""
    _flag(monkeypatch, "*")

    async def _granted(_card: Any) -> bool:
        return True

    monkeypatch.setattr(at, "_late_approved", _granted)
    card, out, calls = _drive(monkeypatch, None)
    assert card is None
    [body] = _posted(calls)
    assert body["due_at"] == "2026-12-01" and body["status_id"] == TODO
    assert _assigned(calls) == [["priya@acme.test"]]
    assert out.startswith("Created:"), out


# A late EDIT answer (a parked or dead card) goes through the real late route
# in ``test_task_edit_card_late.py``: no grant, and no value reaches the model.


# ── 6. The source fences ─────────────────────────────────────────────────────


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.AST:
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _reads_of_edit(node: ast.AST) -> list[int]:
    """The lines that READ the key ``edit``: ``x["edit"]`` in a load, or
    ``x.get("edit")``."""
    out = []
    for n in ast.walk(node):
        if (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load)
                and isinstance(n.slice, ast.Constant) and n.slice.value == "edit"):
            out.append(n.lineno)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == "edit"):
            out.append(n.lineno)
    return out


def test_parse_task_edit_answer_is_the_one_parser_of_the_edit_key() -> None:
    defs, calls = [], []
    for base in (ROOT / "packages", ROOT / "apps"):
        for p in base.rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "parse_task_edit_answer" not in text:
                continue
            for n in ast.walk(ast.parse(text)):
                if isinstance(n, ast.FunctionDef) and n.name == "parse_task_edit_answer":
                    defs.append(p.relative_to(ROOT).as_posix())
                if (isinstance(n, ast.Call) and getattr(n.func, "id", "")
                        == "parse_task_edit_answer"):
                    calls.append(p.relative_to(ROOT).as_posix())
    assert defs == ["packages/acb_skills/acb_skills/task_card.py"]
    assert calls == ["packages/acb_skills/acb_skills/ask_tools.py"]
    # In task_card, only the parser reads the values of the body. The shape
    # test names the key and reads no value (review P2).
    tree = _tree(TASK_CARD)
    parser = _function(tree, "parse_task_edit_answer")
    users = {
        f.name for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(n, ast.Name) and n.id == "EDIT_KEY" for n in ast.walk(f))
    }
    assert users == {"parse_task_edit_answer", "is_task_edit_answer"}
    assert any(isinstance(n, ast.Name) and n.id == "EDIT_KEY" for n in ast.walk(parser))
    shape = _function(tree, "is_task_edit_answer")
    assert not [n for n in ast.walk(shape) if isinstance(n, ast.Subscript)
                and isinstance(n.ctx, ast.Load) and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)], "the shape test reads no value by key"


def test_only_the_late_resend_asks_the_shape_of_an_edit_answer() -> None:
    """``chat_recovery.card_resend`` is the one caller of the shape test, and
    no late path reads the key of an answer."""
    calls = []
    for base in (ROOT / "packages", ROOT / "apps"):
        for p in base.rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "is_task_edit_answer" not in text:
                continue
            for n in ast.walk(ast.parse(text)):
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "is_task_edit_answer":
                    calls.append(p.relative_to(ROOT).as_posix())
    assert calls == ["apps/services/gateway/gateway/chat_recovery.py"]
    recovery = ROOT / "apps" / "services" / "gateway" / "gateway" / "chat_recovery.py"
    late = ROOT / "packages" / "acb_skills" / "acb_skills" / "late_approve.py"
    assert _reads_of_edit(_tree(recovery)) == [] and _reads_of_edit(_tree(late)) == []
    resend = _function(_tree(recovery), "card_resend")
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "is_task_edit_answer"
               for n in ast.walk(resend))


def test_request_confirmation_reads_edit_nowhere_else() -> None:
    tree = _tree(ASK_TOOLS)
    gate = _function(tree, "request_confirmation")
    assert _reads_of_edit(tree) == [], "ask_tools reads the edit key of an answer"
    parses = [n for n in ast.walk(gate) if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "parse_task_edit_answer"]
    assert len(parses) == 1, "one parse of an edit answer, in the verdict"
    verdict = _function(gate, "_verdict")
    assert parses[0] in list(ast.walk(verdict))
    # The one write of the key is the card event, and nothing writes it twice.
    stores = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript)
              and isinstance(n.ctx, ast.Store) and isinstance(n.slice, ast.Constant)
              and n.slice.value == "edit"]
    event = _function(gate, "_event")
    assert len(stores) == 1 and stores[0] in list(ast.walk(event))
    # No reader of the answer in the Projects tools either.
    assert _reads_of_edit(_tree(WRITES)) == []


def test_the_tool_text_does_not_change() -> None:
    """The model reads the docstring of create_task. EAC-1 adds no parameter
    and no word there (cal-tasks owns the tool text, §32.4 Ownership)."""
    import inspect

    params = set(inspect.signature(W.create_task).parameters)
    assert "edit" not in params and "form" not in params
    assert "edit card" not in (W.create_task.__doc__ or "").lower()
