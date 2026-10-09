"""An edited message supersedes the last one (owner, 2026-10-09).

The pure half of ``gateway/chat_supersede.py``: the note the new run reads,
and the plan that decides which rows one edit removes. The database half is
``test_chat_supersede_rls.py`` (R8).

Mutations this suite catches (R7):

* the note loses the completed write steps, or lists a read or a failed call;
* the note passes its cap, for many steps or a long earlier text;
* the route takes the note from the request instead of composing it from the
  stored rows;
* the plan lets an earlier message, or another person's message, be edited.
"""
from __future__ import annotations

import inspect

import pytest

from gateway.chat_supersede import (
    NOTE_TAG,
    SUPERSEDE_NOTE_MAX_CHARS,
    SupersedePlan,
    SupersedeRefused,
    Step,
    compose_supersede_note,
    is_side_effect_tool,
    plan_supersede,
    steps_from_rows,
)

_ME = "alice@example.test"


def _tool(name: str, result: str = "", status: str = "done") -> dict:
    return {"id": name, "name": name, "args": {}, "result": result, "status": status}


def _rows(*, later_user: bool = False) -> list[dict]:
    rows = [
        {"id": "u1", "role": "user", "content": "first", "author_email": _ME, "author_kind": "human"},
        {"id": "a1", "role": "assistant", "content": "ok", "author_kind": "agent", "tool_events": []},
        {"id": "u2", "role": "user", "content": "Create 3 tasks for the bootloader review",
         "author_email": _ME, "author_kind": "human"},
        {"id": "a2", "role": "assistant", "content": "Created three tasks.", "author_kind": "agent",
         "tool_events": [
             _tool("list_projects", "3 projects"),
             _tool("create_task", 'Created task "Review bootloader" (#41)'),
             _tool("create_task", 'Created task "Test bootloader" (#42)'),
             _tool("update_task", "boom", status="error"),
             _tool("assign_task", "", status="running"),
             _tool("ask_user", "which project?"),
         ]},
    ]
    if later_user:
        rows.append({"id": "u3", "role": "user", "content": "and more",
                     "author_email": "bob@example.test", "author_kind": "human"})
    return rows


# ── The note ────────────────────────────────────────────────────────────────

def test_the_note_lists_the_completed_writes_from_the_stored_rows():
    plan = plan_supersede(_rows(), "u2", actor=_ME)
    note = compose_supersede_note(plan)
    assert note.startswith(NOTE_TAG)
    assert "Create 3 tasks for the bootloader review" in note   # the earlier text
    assert note.count("- create_task:") == 2
    assert '"Review bootloader" (#41)' in note
    # A call the cancel cut off is kept, and marked as unknown.
    assert "- assign_task (cut off by the edit, it may have finished)" in note
    # A read, a failed call and a control tool changed nothing.
    for absent in ("list_projects", "update_task", "ask_user"):
        assert absent not in note, absent
    assert "Do not do these steps again" in note
    assert "undo a step only when the member asks" in note


def test_a_reply_that_changed_nothing_gives_one_sentence():
    rows = _rows()
    rows[3]["tool_events"] = [_tool("search_tasks", "none")]
    note = compose_supersede_note(plan_supersede(rows, "u2", actor=_ME))
    body = note[len(NOTE_TAG):].strip()
    assert body.count(". ") == 0 and body.endswith(".")
    assert "changed nothing" in body
    assert "\n" not in note


def test_the_note_is_capped_for_many_steps_and_a_long_earlier_text():
    plan = SupersedePlan(
        old_text="x" * 5000,
        removed_ids=["u", "a"],
        steps=[Step(name=f"create_task_{i}", result="r" * 500, status="done") for i in range(200)],
    )
    note = compose_supersede_note(plan)
    assert len(note) <= SUPERSEDE_NOTE_MAX_CHARS
    assert "more steps" in note          # the rest are counted, not dropped silently
    assert "Do not do these steps again" in note   # the rule survives the cap


def test_sub_agent_writes_count():
    rows = [{"id": "a", "role": "assistant", "tool_events": [{
        "id": "c", "name": "call_agent", "status": "done", "result": "delegated",
        "subAgentTools": [{"name": "create_deal", "status": "done", "result": "deal #9"}],
    }]}]
    names = [s.name for s in steps_from_rows(rows)]
    assert names == ["call_agent", "create_deal"]


@pytest.mark.parametrize("name,write", [
    ("create_task", True), ("pm.update_project", True), ("send_email", True),
    ("write_artifact", True), ("get_task", False), ("crm__list_deals", False),
    ("web_search", False), ("manage_todo_list", False), ("ask_questions", False),
])
def test_side_effect_classification(name, write):
    assert is_side_effect_tool(name) is write


def test_the_route_composes_the_note_on_the_server():
    """The browser sends an id. The note is composed from the stored rows,
    and nothing in the request can supply or extend it."""
    from gateway.routes import agent

    src = inspect.getsource(agent.run_agent_stream_endpoint)
    assert 'req.payload.pop("supersedes"' in src
    assert "_supersede_note = compose_supersede_note(_plan)" in src
    # The only writer of the note into the message is the composed value.
    assert src.count("_supersede_note}") == 1
    # No request field names a note.
    assert "supersede_note\"" not in src and "supersede_note'" not in src
    # It goes in AFTER memory read the member's words.
    assert src.index('req.payload["message"] = (\n            f"{_supersede_note}') > src.index("_mem_message = ")


# ── The plan ────────────────────────────────────────────────────────────────

def test_only_the_last_user_message_may_be_superseded():
    with pytest.raises(SupersedeRefused) as err:
        plan_supersede(_rows(), "u1", actor=_ME)
    assert (err.value.code, err.value.status) == ("not_last", 409)


def test_a_later_message_by_someone_else_also_blocks_the_edit():
    with pytest.raises(SupersedeRefused) as err:
        plan_supersede(_rows(later_user=True), "u2", actor=_ME, shared=True)
    assert err.value.code == "not_last"


def test_only_the_author_may_supersede():
    with pytest.raises(SupersedeRefused) as err:
        plan_supersede(_rows(), "u2", actor="bob@example.test")
    assert (err.value.code, err.value.status) == ("not_yours", 403)


def test_an_unattributed_row_is_mine_only_in_a_solo_thread():
    rows = _rows()
    rows[2]["author_email"] = None
    assert plan_supersede(rows, "u2", actor=_ME).removed_ids == ["u2", "a2"]
    with pytest.raises(SupersedeRefused):
        plan_supersede(rows, "u2", actor=_ME, shared=True)


def test_the_new_turn_is_kept():
    rows = _rows() + [
        {"id": "u-new", "role": "user", "content": "edited", "author_email": _ME, "author_kind": "human"},
        {"id": "a-new", "role": "assistant", "content": "", "author_kind": "agent"},
    ]
    plan = plan_supersede(rows, "u2", actor=_ME, keep_ids=["u-new", "a-new"])
    assert plan.removed_ids == ["u2", "a2"]


def test_a_missing_message_is_not_found():
    with pytest.raises(SupersedeRefused) as err:
        plan_supersede(_rows(), "nope", actor=_ME)
    assert err.value.status == 404
