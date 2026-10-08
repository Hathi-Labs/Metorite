"""WS-46 P6 — the chat sets every task field the screen can (G5 to G9).

Spec: ``project-docs/specs/projects_agent_parity.md`` §3.3 (the gap rows) and
§12, slice P6. D91. D-PM-38 (``project_management_app.md`` §12.9) for the
subtasks.

Each new argument has the same five parts, and this file holds each one:

1. **A schema the model reads.** The argument is in the tool's signature and
   its docstring says the format.
2. **A check before the card.** A name the project does not define, a value
   of the wrong type, an epic under a parent, or a flag with no act is a
   refusal, and no card shows.
3. **The card shows it,** by NAME, never by id.
4. **The wire carries it.** F2 (``test_projects_field_parity.py``) holds the
   witness of each field. The mutation half below strips each P6 field from
   the request and proves that the F2 check then fails (R7).
5. **A refusal is text.** The create sends its custom values in the POST
   itself, which checks them (#679). So a refused value refuses the whole
   create, and no task is left behind. A refused write after the create
   ends in a ``stopped:`` receipt, as P1's rule does, and never in a raise.

The subtasks are asked ONCE (D-PM-38). A single-task door asks the member
first when the task has subtasks and the member has not said. A bulk act
does not read each subtree, so its card states the rule.

R8: the half at the end runs the body the chat sends on asyncpg, over the
tenant ladder. A row with a type and a start date goes through
``core.insert_row``. The custom values go through the real create route
(``tasks.create_task``), which checks them with ``apply_values``. A refused
value leaves no row. A fake stores whatever it is handed.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import skill_projects
import skill_projects.client as client
from skill_projects import manifest as m
from skill_projects import writes as W

from tests.unit import test_projects_agent_writes as tw
from tests.unit import test_projects_field_parity as f2
from tests.unit._projects_agent_fakes import (
    REPO_ROOT,
    FakeClient,
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    form_stub,
    writes,
)

UUID, OTHER, LIVE = tw.UUID, tw.OTHER, tw.LIVE
EPIC_ID = "5a8fad5b-d9cb-469f-a165-70867728950e"
CHILD = "6a8fad5b-d9cb-469f-a165-70867728950e"
CASCADE_TS = REPO_ROOT / "workbench" / "control_plane" / "src" / "lib" / "subtaskCascade.ts"

#: The destination of a move that asks for two fields.
DEST_FIELDS = [
    {
        "id": tw.FIELD_ID,
        "project_id": OTHER,
        "name": "Cost centre",
        "field_key": "cost_centre",
        "field_type": "number",
        "options": [],
        "required": True,
    },
    {
        "id": tw.TAG_ID,
        "project_id": OTHER,
        "name": "Region",
        "field_key": "region",
        "field_type": "select",
        "options": ["EU", "US"],
        "required": True,
    },
]


@pytest.fixture(autouse=True)
def _an_open_run() -> Any:
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    with artifact_context_scope():
        bind_artifact_context(agent_name="projects-assistant", no_egress=False)
        yield


@pytest.fixture(autouse=True)
def _forms(monkeypatch) -> None:
    form_stub(monkeypatch, tw.FORM_ANSWERS)


def _child(n: int) -> str:
    return str(uuid.UUID(int=0x6A8FAD5B0000000000000000000000 + n))


def _flat(subtasks: int, done: int) -> dict[str, list[tuple[str, str]]]:
    """*subtasks* direct children of each root task, the first *done* closed."""
    rows = [(_child(i), "done" if i < done else "todo") for i in range(subtasks)]
    return {UUID: rows, LIVE: rows}


def _gateway(
    *,
    subtasks: int = 0,
    done: int = 0,
    plan: dict | None = None,
    types=None,
    tree: dict[str, list[tuple[str, str]]] | None = None,
    task: dict | None = None,
):
    """The writes fence's gateway, with a subtree, a richer type list and a plan.

    ``tree`` maps a task id to its direct children, ``(id, category)``, as
    ``/relations`` lists them. A task that is not a key has no children.
    """
    children = tree if tree is not None else _flat(subtasks, done)

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if task is not None and path == f"/projects/tasks/{UUID}" and method == "GET":
            return task
        if path.endswith("/relations"):
            tid = path.split("/")[-2]
            rows = [
                {"id": cid, "title": f"step {cid[-2:]}", "category": cat}
                for cid, cat in children.get(tid, [])
            ]
            return {"subtasks": rows, "links": []}
        if path.endswith("/types") and method == "GET" and types is not None:
            return {"rows": types}
        if path == f"/projects/nodes/{OTHER}/fields" and plan is not None:
            return {"rows": DEST_FIELDS}
        if path == "/projects/tasks/move/preview" and plan is not None:
            return plan
        if path.endswith("/complete") or path.endswith("/archive"):
            flag = (call["params"] or {}).get("include_subtasks")
            key = "subtasks_completed" if path.endswith("/complete") else "subtasks_archived"
            return {**tw.TASK, **({key: subtasks} if flag else {})}
        return tw.responder(call)

    return answer


def _writes_to(calls: list[dict], method: str, template: str) -> list[dict]:
    return [
        c
        for c in writes(calls)
        if c["method"] == method
        and (r := m.route_for(c["method"], c["path"])) is not None
        and r.path == template
    ]


# ── 1. The schema the model reads ───────────────────────────────────────────

_NEW_ARGUMENTS = {
    "create_task": ("start", "type", "fields"),
    "update_task": ("type", "fields", "include_subtasks"),
    "move_task": ("fields", "include_subtasks"),
    "complete": ("include_subtasks",),
    "archive_task": ("include_subtasks",),
    "bulk_update": ("include_subtasks",),
}


@pytest.mark.parametrize("tool", sorted(_NEW_ARGUMENTS))
def test_each_new_argument_is_declared_and_its_docstring_names_it(tool: str) -> None:
    import inspect

    fn = getattr(skill_projects, tool)
    params = inspect.signature(fn).parameters
    doc = fn.__doc__ or ""
    for arg in _NEW_ARGUMENTS[tool]:
        assert arg in params, f"{tool} has no argument {arg}"
        assert params[arg].default == "", f"{tool}.{arg}: an omitted argument sends nothing"
        assert arg in doc, f"{tool}'s docstring does not say what {arg} takes"


# ── 2 and 3. G5 and G6 — the start date and the type at the create ──────────


async def test_create_sends_the_start_and_the_type_by_name_under_one_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_task(UUID, "Weld the frame", start="2026-10-05", type="BUG")
    post = _writes_to(calls, "POST", "/projects/tasks")[0]["json"]
    assert post["start_date"] == "2026-10-05"
    assert post["type_id"] == tw.TYPE_ID
    assert len(asked) == 1
    assert "type: «Bug»" in asked[0]["context"] and "start: «2026-10-05»" in asked[0]["context"]
    assert tw.TYPE_ID not in asked[0]["context"], "the card names the type, not its id"
    assert out.startswith("Created:")


@pytest.mark.parametrize("bad", ["5 Oct", "2026-13-01", "2026-10-5"])
async def test_a_start_that_is_not_a_date_is_refused_before_the_card(bad, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    with pytest.raises(W.GatewayRefusal, match="start is a date, YYYY-MM-DD"):
        await skill_projects.create_task(UUID, "Weld the frame", start=bad)
    assert asked == [] and writes(calls) == []


async def test_an_unknown_type_lists_the_real_ones_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    with pytest.raises(W.GatewayRefusal, match=r"No task type is called «Chore».*«Bug»"):
        await skill_projects.create_task(UUID, "Weld the frame", type="Chore")
    assert asked == [] and writes(calls) == []


async def test_an_epic_type_under_a_parent_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    types = [{"id": EPIC_ID, "name": "Epic", "is_epic": True, "project_id": UUID}]
    calls = fake_gateway(monkeypatch, _gateway(types=types))
    with pytest.raises(W.GatewayRefusal, match="an epic is a top-level task"):
        await skill_projects.create_task(UUID, "Q4 launch", type="epic", parent_task_id=OTHER)
    assert asked == [] and writes(calls) == []
    # With no parent, an epic is fine.
    await skill_projects.create_task(UUID, "Q4 launch", type="epic")
    assert _writes_to(calls, "POST", "/projects/tasks")[0]["json"]["type_id"] == EPIC_ID


# ── G7 — custom field values by name ────────────────────────────────────────


async def test_create_sends_the_values_in_the_create_itself(monkeypatch) -> None:
    """The create route checks each value through ``apply_values`` (#679), so
    the values go in the POST: one atomic write, and no PATCH after it."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.create_task(UUID, "Weld the frame", fields='{"customer": "smb"}')
    post = _writes_to(calls, "POST", "/projects/tasks")[0]["json"]
    assert post["custom_fields"] == {"customer": "SMB"}
    assert _writes_to(calls, "PATCH", "/projects/tasks/{task_id}") == []
    assert len(asked) == 1 and "field Customer: «SMB»" in asked[0]["context"]
    assert "custom_fields" not in asked[0]["context"], "the card names each field by name"
    assert "Fields set:" in out and "field Customer: SMB" in out
    assert "update_task" not in m.COMPOSITE["create_task"]

async def test_update_sends_values_keyed_by_field_key_and_shows_before_after(monkeypatch) -> None:
    asked = approve(monkeypatch)
    task = {**tw.TASK, "custom_fields": {"customer": "SMB"}}

    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/tasks/{UUID}" and call["method"] == "GET":
            return task
        return _gateway()(call)

    calls = fake_gateway(monkeypatch, answer)
    await skill_projects.update_task(UUID, fields='{"Customer": null}')
    patched = _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")
    assert patched[0]["json"] == {"custom_fields": {"customer": None}}, "null empties the field"
    assert "field Customer: «SMB» → «(empty)»" in asked[0]["context"]


@pytest.mark.parametrize(
    ("fields", "words"),
    [
        ('{"Colour": "red"}', "No custom field is called «Colour»"),
        ('{"Customer": "Enterprise"}', "is one of «SMB»"),
        ("Customer=SMB", "fields is a JSON object keyed by field NAME"),
        ("[]", "fields is a JSON object keyed by field NAME"),
    ],
)
async def test_a_bad_field_is_refused_before_the_card(fields, words, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    with pytest.raises(W.GatewayRefusal) as caught:
        await skill_projects.update_task(UUID, fields=fields)
    assert words in str(caught.value)
    assert asked == [] and writes(calls) == []


async def test_a_new_task_cannot_empty_a_field(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    with pytest.raises(W.GatewayRefusal, match="no value to empty"):
        await skill_projects.create_task(UUID, "Weld the frame", fields='{"Customer": null}')
    assert asked == [] and writes(calls) == []


@pytest.mark.parametrize(
    ("kind", "options", "given", "sent"),
    [
        ("number", [], "4200", 4200),
        ("number", [], 12.5, 12.5),
        ("boolean", [], "yes", True),
        ("boolean", [], False, False),
        ("date", [], "2026-10-09", "2026-10-09"),
        ("multi_select", ["EU", "US"], "eu, US, eu", ["EU", "US"]),
        ("text", [], "  PO-77 ", "PO-77"),
        ("url", [], "https://x.example/po", "https://x.example/po"),
    ],
)
def test_a_value_takes_the_wire_shape_of_its_type(kind, options, given, sent) -> None:
    definition = {"name": "F", "field_key": "f", "field_type": kind, "options": options}
    assert W._field_value(definition, given) == sent


@pytest.mark.parametrize(
    ("kind", "given"),
    [("number", "lots"), ("number", True), ("boolean", "maybe"), ("date", "9 Oct"),
     ("url", "x.example")],
)
def test_a_value_of_the_wrong_type_is_refused(kind, given) -> None:
    definition = {"name": "F", "field_key": "f", "field_type": kind, "options": []}
    with pytest.raises(W.GatewayRefusal):
        W._field_value(definition, given)


def _refuse_the_create(call: dict) -> Any:
    """The create route's own 422 for a value (``apply_values``, #679). A
    create with no values passes, as the route lets it."""
    body = call["json"] if isinstance(call["json"], dict) else {}
    if call["method"] == "POST" and call["path"] == "/projects/tasks" and "custom_fields" in body:
        return FakeResponse({"detail": "Custom field 'customer': 'SMB' is not one of []."}, 422)
    return _gateway()(call)


async def test_a_refused_value_refuses_the_create_and_leaves_no_task(monkeypatch) -> None:
    """THE FENCE for G7 at the create. A value the route refuses refuses the
    create itself: no task, no assign, no rule, and the model reads why."""
    from skill_projects.refusals import REFUSED, refusals_as_text

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _refuse_the_create)
    create = refusals_as_text(skill_projects.create_task)
    out = await create(
        UUID, "Weld the frame", assignees="a@x.io", fields='{"Customer": "SMB"}', repeat="daily"
    )
    assert out.startswith(REFUSED) and "'customer'" in out
    assert "Created" not in out, "no task exists, so the receipt claims none"
    assert [(c["method"], c["path"]) for c in writes(calls)] == [("POST", "/projects/tasks")]


async def test_a_values_write_after_the_create_is_a_mutation_the_fence_catches(
    monkeypatch,
) -> None:
    """The mutation: the values leave the POST for a second write. The POST
    then succeeds without them, and a task exists before its values do."""
    approve(monkeypatch)
    real = W._new_task_fields

    async def split(*args: Any, **kwargs: Any) -> Any:
        out = await real(*args, **kwargs)
        out.payload.pop("custom_fields", None)
        return out

    monkeypatch.setattr(W, "_new_task_fields", split)
    calls = fake_gateway(monkeypatch, _refuse_the_create)
    out = await skill_projects.create_task(UUID, "Weld the frame", fields='{"Customer": "SMB"}')
    assert "custom_fields" not in _writes_to(calls, "POST", "/projects/tasks")[0]["json"]
    # The fence above asserts no "Created" and one write. Here a task exists.
    assert out.startswith("Created"), "the mutated create leaves a task behind"


async def test_a_failed_assign_still_shows_the_values_the_create_saved(monkeypatch) -> None:
    approve(monkeypatch)

    def answer(call: dict) -> Any:
        if call["method"] == "PUT":
            return FakeResponse({"detail": "Bad address."}, 422)
        return _gateway()(call)

    calls = fake_gateway(monkeypatch, answer)
    out = await skill_projects.create_task(
        UUID, "Weld the frame", assignees="a@x.io", fields='{"Customer": "SMB"}'
    )
    assert out.startswith("Created #9. The assignees were NOT saved")
    assert "field Customer: SMB" in out, "the values went in with the create"
    assert "not tried" not in out
    assert _writes_to(calls, "PATCH", "/projects/tasks/{task_id}") == []


async def test_update_sets_and_clears_the_type_by_name(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.update_task(UUID, type="bug")
    assert _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")[0]["json"] == {
        "type_id": tw.TYPE_ID
    }
    assert "type: «(none)» → «Bug»" in asked[0]["context"]
    calls.clear()
    await skill_projects.update_task(UUID, clear="type")
    assert _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")[0]["json"] == {"type_id": None}
    with pytest.raises(W.GatewayRefusal, match="both set and cleared"):
        await skill_projects.update_task(UUID, type="bug", clear="type")


# ── G8 — the destination's required fields on a move ────────────────────────


def _plan(**over: Any) -> dict[str, Any]:
    return {
        "task_count": 1,
        "drops": [],
        "required_missing": ["Cost centre", "Region"],
        "crosses_status_set": False,
        "crosses_root": True,
        "subtasks": {"count": 0, "hidden": 0, "refused": []},
        **over,
    }


async def test_a_move_without_the_required_fields_names_each_one(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(plan=_plan()))
    out = await skill_projects.move_task(UUID, destination_project_id=OTHER)
    assert "requires «Cost centre» (number), «Region» (one of «EU», «US»)" in out
    assert "pass fields" in out
    assert asked == [] and writes(calls) == []


async def test_a_move_with_the_answers_goes_through_the_promote_route(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(plan=_plan()))
    out = await skill_projects.move_task(
        UUID, destination_project_id=OTHER, fields='{"Cost centre": "4200", "region": "eu"}'
    )
    sent = _writes_to(calls, "POST", "/projects/tasks/{task_id}/move")
    assert [c["json"] for c in sent] == [
        {"project_id": OTHER, "custom_fields": {"cost_centre": 4200, "region": "EU"}}
    ]
    assert _writes_to(calls, "POST", "/projects/tasks/move") == []
    assert "field Cost centre: «4200»" in asked[0]["context"]
    assert "field Region: «EU»" in asked[0]["context"]
    assert out.startswith("Moved 1 task to")


async def test_an_answer_short_of_the_required_fields_names_what_is_left(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(plan=_plan()))
    out = await skill_projects.move_task(
        UUID, destination_project_id=OTHER, fields='{"Cost centre": 4200}'
    )
    assert "requires «Region» (one of «EU», «US»)" in out and "Cost centre" not in out
    assert asked == [] and writes(calls) == []


async def test_answers_take_one_task_and_a_move_across_spaces(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(plan=_plan(crosses_root=False)))
    out = await skill_projects.move_task(
        f"{UUID},{OTHER}", destination_project_id=OTHER, fields='{"Region": "EU"}'
    )
    assert "fields takes one task" in out
    out = await skill_projects.move_task(
        UUID, destination_project_id=OTHER, fields='{"Region": "EU"}'
    )
    assert "stays inside one space" in out, "an answer the route would drop is refused"
    assert asked == [] and writes(calls) == []


async def test_fields_without_a_destination_are_refused(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _gateway())
    out = await skill_projects.move_task(UUID, parent_task_id=OTHER, fields='{"Region": "EU"}')
    assert "go with destination_project_id" in out and writes(calls) == []


# ── G9 — the subtasks, asked once (D-PM-38) ─────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "kwargs", "default"),
    [
        ("complete", {"task_id": UUID}, "only this task"),
        ("archive_task", {"task_id": LIVE}, "they go with it"),
        ("update_task", {"task_id": UUID, "status": "done"}, "only this task"),
    ],
)
async def test_a_door_asks_about_the_subtasks_first(tool, kwargs, default, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3))
    out = await getattr(skill_projects, tool)(**kwargs)
    assert "3 " in out and "Ask the member once" in out
    assert f"In the app, the default is: {default}." in out
    assert "include_subtasks=yes or include_subtasks=no" in out
    assert asked == [] and writes(calls) == [], "no card and no write before the answer"


async def test_complete_with_yes_sends_the_flag_and_reports_the_count(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3))
    out = await skill_projects.complete(UUID, include_subtasks="yes")
    sent = _writes_to(calls, "POST", "/projects/tasks/{task_id}/complete")
    assert sent[0]["params"] == {"include_subtasks": True}
    assert "3 open subtasks (every level) completed too" in asked[0]["context"]
    assert "Subtasks completed too: 3." in out


async def test_complete_with_no_sends_nothing_and_says_they_stay(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=2))
    await skill_projects.complete(UUID, include_subtasks="no")
    sent = _writes_to(calls, "POST", "/projects/tasks/{task_id}/complete")
    assert sent[0]["params"] == {}
    assert "2 open subtasks (every level) stay as they are" in asked[0]["context"]


async def test_a_task_with_no_open_subtasks_is_not_asked(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=2, done=2))
    out = await skill_projects.complete(UUID)
    assert out.startswith("Done:") and len(writes(calls)) == 1


async def test_archive_with_yes_shelves_the_subtasks_and_says_so(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=2))
    out = await skill_projects.archive_task(LIVE, include_subtasks="yes")
    sent = _writes_to(calls, "POST", "/projects/tasks/{task_id}/archive")
    assert sent[0]["params"] == {"include_subtasks": True}
    assert "2 subtasks (every level) archived with it" in asked[0]["detail"]
    assert "Subtasks archived too: 2." in out


async def test_update_with_yes_sends_the_flag_on_a_move_into_done(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=1))
    await skill_projects.update_task(UUID, status="done", include_subtasks="yes")
    sent = _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")
    assert sent[0]["params"] == {"include_subtasks": True}
    assert sent[0]["json"] == {"status_id": tw.S3}


async def test_the_flag_with_no_act_it_belongs_to_is_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(subtasks=1))
    with pytest.raises(W.GatewayRefusal, match="goes with a status change into a Done lane"):
        await skill_projects.update_task(UUID, status="in progress", include_subtasks="yes")
    with pytest.raises(W.GatewayRefusal, match="goes with action archive"):
        await skill_projects.bulk_update(UUID, due="2026-10-09", include_subtasks="yes")
    with pytest.raises(W.GatewayRefusal, match="include_subtasks is yes or no"):
        await skill_projects.complete(UUID, include_subtasks="all of them")
    assert asked == [] and writes(calls) == []


#: A nested subtree: LIVE has two children, one of them has two, and one
#: of those has one more. Five descendants, and #A is closed.
A, B, C, D, E = (_child(n) for n in range(10, 15))
NESTED = {LIVE: [(A, "done"), (B, "todo")], A: [(C, "todo"), (D, "todo")], C: [(E, "todo")]}


async def _direct_only(task_id: str) -> Any:
    """The P6 count before review round 1: the direct children only."""
    rows = NESTED.get(task_id, [])
    return W.SubtreeCount(len(rows), sum(1 for _i, c in rows if c != "done"))


async def test_the_count_reaches_every_level_the_cascade_reaches(monkeypatch) -> None:
    """The archive cascade shelves every visible descendant (``cascade.
    archive_subtree``), so the card counts five here, not the two children."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(tree=NESTED))
    out = await skill_projects.archive_task(LIVE)
    assert "has 5 subtasks (every level)" in out
    await skill_projects.archive_task(LIVE, include_subtasks="yes")
    assert "5 subtasks (every level) archived with it" in asked[0]["detail"]
    count = await W._subtask_counts(LIVE)
    assert (count.total, count.open, count.capped) == (5, 4, False), "a closed child's own"
    assert len([c for c in calls if c["path"].endswith("/relations")]) >= 6


async def test_a_direct_children_count_fails_the_every_level_check(monkeypatch) -> None:
    """The mutation: count the children only, and the card says 2, not 5."""
    from skill_projects import guarded

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway(tree=NESTED))
    monkeypatch.setattr(guarded, "_subtask_counts", _direct_only)
    await skill_projects.archive_task(LIVE, include_subtasks="yes")
    assert "5 subtasks" not in asked[0]["detail"]


async def test_complete_counts_the_open_ones_at_every_level(monkeypatch) -> None:
    approve(monkeypatch)
    tree = {UUID: NESTED[LIVE], A: NESTED[A], C: NESTED[C]}
    fake_gateway(monkeypatch, _gateway(tree=tree))
    out = await skill_projects.complete(UUID)
    assert "has 4 open subtasks (every level)" in out, "under a closed child, too"


async def test_a_huge_tree_is_read_to_a_cap_and_the_card_says_at_least(monkeypatch) -> None:
    approve(monkeypatch)
    monkeypatch.setattr(W, "SUBTREE_READS", 2)
    fake_gateway(monkeypatch, _gateway(tree=NESTED))
    out = await skill_projects.archive_task(LIVE)
    assert "has at least 4 subtasks (every level)" in out


# ── Review round 1 — the edit form asks the subtasks question itself ───────


def _edit_answer(**over: Any) -> dict[str, str]:
    form = {
        "title": "Fix the extruder", "description": "", "status": "Done", "due": "2026-10-01",
        "start": "", "important": True, "leveraged": False, "estimate_mins": 30, "tags": "",
        **over,
    }
    return {"Edit #7": "Review changes — " + json.dumps(form)}


async def test_the_edit_form_asks_the_subtasks_question_and_the_edit_applies(
    monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    drawn = form_stub(monkeypatch, _edit_answer())
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3))
    out = await skill_projects.edit_task(UUID)
    field = next(f for f in drawn[0]["props"]["data"]["fields"] if f["name"] == "subtasks")
    assert field["options"] == ["Only this task", "Complete them too"]
    assert field["value"] == "Only this task", "D-PM-38 decision 2 is the default"
    assert "3 open subtasks (every level)" in field["label"]
    patched = _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")
    assert len(patched) == 1, f"the edit did not apply: {out!r}"
    assert patched[0]["params"] == {}, "Only this task sends no flag"
    body = patched[0]["json"]
    assert body["status_id"] == tw.S3 and body["due_at"] == "2026-10-01"
    assert body["estimate_mins"] == 30, "the form's other edits go with it"
    assert "2 open subtasks" not in asked[0]["context"]
    assert "3 open subtasks (every level) stay as they are" in asked[0]["context"]


async def test_the_edit_form_can_complete_the_subtasks_too(monkeypatch) -> None:
    asked = approve(monkeypatch)
    form_stub(monkeypatch, _edit_answer(subtasks="Complete them too"))
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3))
    await skill_projects.edit_task(UUID)
    patched = _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")
    assert patched[0]["params"] == {"include_subtasks": True}
    assert "3 open subtasks (every level) completed too" in asked[0]["context"]


async def test_the_edit_form_without_a_done_status_sends_no_flag(monkeypatch) -> None:
    approve(monkeypatch)
    form_stub(monkeypatch, _edit_answer(status="In progress", subtasks="Complete them too"))
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3))
    await skill_projects.edit_task(UUID)
    patched = _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")
    assert patched[0]["params"] == {} and patched[0]["json"]["status_id"] == tw.S2


async def test_a_form_with_no_subtasks_choice_strands_the_edit(monkeypatch) -> None:
    """The mutation: the form does not read the subtasks, so it draws no
    choice. update_task then asks after the submit, and nothing applies."""
    from skill_projects import forms

    async def none(_task_id: str) -> Any:
        return W.SubtreeCount()

    approve(monkeypatch)
    form_stub(monkeypatch, _edit_answer())
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3))
    monkeypatch.setattr(forms, "_subtask_counts", none)
    out = await skill_projects.edit_task(UUID)
    assert "Ask the member once" in out
    assert _writes_to(calls, "PATCH", "/projects/tasks/{task_id}") == []


# ── Review round 1 — a task already in the Done lane cascades nothing ──────


async def test_a_task_already_done_is_not_asked_and_shows_no_cascade(monkeypatch) -> None:
    asked = approve(monkeypatch)
    done_task = {**tw.TASK, "status_id": tw.S3}
    calls = fake_gateway(monkeypatch, _gateway(subtasks=3, task=done_task))
    await skill_projects.update_task(UUID, status="done", title="Renamed")
    assert "subtasks" not in asked[0]["context"]
    assert _writes_to(calls, "PATCH", "/projects/tasks/{task_id}")[0]["params"] == {}
    with pytest.raises(W.GatewayRefusal, match="status change into a Done lane"):
        await skill_projects.update_task(UUID, status="done", include_subtasks="yes")


# ── Review round 1 — the nits ───────────────────────────────────────────────


def test_a_blank_answer_to_a_required_field_is_no_answer() -> None:
    missing = ["PO number", "Count"]
    card = {"field PO number": "  ", "field Count": "0"}
    assert W._unanswered(missing, card, {"po_number": "  ", "count": 0}) == ["PO number"]
    assert W._unanswered(missing, {"field PO number": "[]"}, {"po_number": []}) == missing


@pytest.mark.parametrize("given", [["a", "b"], {"a": 1}])
def test_a_text_field_refuses_a_list_or_an_object(given) -> None:
    for kind in ("text", "url"):
        definition = {"name": "F", "field_key": "f", "field_type": kind, "options": []}
        with pytest.raises(W.GatewayRefusal, match="takes text, not a list"):
            W._field_value(definition, given)


async def test_a_move_asks_then_carries_the_subtasks(monkeypatch) -> None:
    asked = approve(monkeypatch)
    plan = _plan(required_missing=[], subtasks={"count": 2, "hidden": 0, "refused": []})
    calls = fake_gateway(monkeypatch, _gateway(plan=plan))
    out = await skill_projects.move_task(UUID, destination_project_id=OTHER)
    assert "has 2 subtasks" in out and "they go with it" in out
    assert asked == [] and writes(calls) == []
    await skill_projects.move_task(UUID, destination_project_id=OTHER, include_subtasks="yes")
    preview = [c for c in calls if c["path"] == "/projects/tasks/move/preview"][-1]["json"]
    assert preview["include_subtasks"] is True, "the preview counts what the subtasks cost"
    body = _writes_to(calls, "POST", "/projects/tasks/move")[0]["json"]
    assert body["include_subtasks"] is True
    assert "2 subtasks (every level) moved too" in asked[0]["context"]


async def test_a_move_over_a_hidden_subtask_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    plan = _plan(required_missing=[], subtasks={"count": 1, "hidden": 2, "refused": []})
    calls = fake_gateway(monkeypatch, _gateway(plan=plan))
    out = await skill_projects.move_task(
        UUID, destination_project_id=OTHER, include_subtasks="yes"
    )
    assert "2 subtasks of these tasks are hidden from you" in out
    assert asked == [] and writes(calls) == []


async def test_bulk_asks_once_and_the_card_states_the_rule(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.bulk_update(f"{UUID},{OTHER}", action="archive")
    body = _writes_to(calls, "POST", "/projects/tasks/bulk")[0]["json"]
    assert "include_subtasks" not in body
    assert "subtasks: «stay as they are" in asked[0]["context"]
    calls.clear()
    await skill_projects.bulk_update(f"{UUID},{OTHER}", action="archive", include_subtasks="yes")
    body = _writes_to(calls, "POST", "/projects/tasks/bulk")[0]["json"]
    assert body == {"task_ids": [UUID, OTHER], "action": "archive", "include_subtasks": True}
    assert "subtasks: «archived with each task»" in asked[1]["context"]


async def test_a_declined_card_writes_nothing_for_any_p6_call(monkeypatch) -> None:
    deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway(plan=_plan()))
    await skill_projects.create_task(
        UUID, "Weld", start="2026-10-05", type="bug", fields='{"Customer": "SMB"}'
    )
    await skill_projects.update_task(UUID, status="done", include_subtasks="yes")
    await skill_projects.move_task(
        UUID, destination_project_id=OTHER, fields='{"Cost centre": 1, "Region": "US"}'
    )
    await skill_projects.complete(UUID, include_subtasks="yes")
    assert writes(calls) == []


# ── The D-PM-38 defaults are the app's, in one place each ───────────────────


def _ts_defaults(text: str) -> dict[str, bool]:
    block = re.search(r"CASCADE_DEFAULTS\s*=\s*\{(?P<body>[^}]*)\}", text)
    assert block, "subtaskCascade.ts has no CASCADE_DEFAULTS object"
    return {
        k: v == "true"
        for k, v in re.findall(r"^\s*(\w+)\s*:\s*(true|false)\s*,", block["body"], re.MULTILINE)
    }


def test_the_chat_names_the_same_defaults_as_the_app() -> None:
    """The question names the app's default. A copy that drifts would tell
    the member the app does what it does not."""
    assert _ts_defaults(CASCADE_TS.read_text(encoding="utf-8")) == W.CASCADE_DEFAULTS


def test_the_defaults_check_fails_on_a_drift() -> None:
    """The mutation: one default flipped in the TypeScript text."""
    text = CASCADE_TS.read_text(encoding="utf-8").replace("archive: true", "archive: false")
    assert _ts_defaults(text) != W.CASCADE_DEFAULTS


# ── 4. R7 — each P6 field, stripped from the wire, turns F2 red ─────────────

#: Every field P6 moved into ``SENDS``. One mutation each.
P6_SENDS: tuple[tuple[tuple[str, str], str], ...] = (
    (("POST", "/projects/tasks"), "start_date"),
    (("POST", "/projects/tasks"), "custom_fields"),
    (("POST", "/projects/tasks"), "type_id"),
    (("PATCH", "/projects/tasks/{task_id}"), "type_id"),
    (("PATCH", "/projects/tasks/{task_id}"), "custom_fields"),
    (("PATCH", "/projects/tasks/{task_id}"), "include_subtasks"),
    (("POST", "/projects/tasks/{task_id}/move"), "project_id"),
    (("POST", "/projects/tasks/{task_id}/move"), "custom_fields"),
    (("POST", "/projects/tasks/{task_id}/move"), "include_subtasks"),
    (("POST", "/projects/tasks/move/preview"), "include_subtasks"),
    (("POST", "/projects/tasks/move"), "include_subtasks"),
    (("POST", "/projects/tasks/{task_id}/archive"), "include_subtasks"),
    (("POST", "/projects/tasks/{task_id}/complete"), "include_subtasks"),
    (("POST", "/projects/tasks/bulk"), "include_subtasks"),
)


def test_every_p6_field_is_a_sends_row_and_no_p6_gap_is_planned() -> None:
    for key, name in P6_SENDS:
        assert name in m.SENDS.get(key, {}), f"{key} · {name} is not in SENDS"
    planned = {g for names in m.FIELD_PLANNED.values() for g in names.values()}
    assert not planned & {"G5", "G6", "G7", "G8", "G9"}
    assert not {"G5", "G6", "G7", "G8", "G9"} & set(m.FIELD_GAPS)
    assert "custom_fields" not in m.FIELD_EXEMPT.get(("POST", "/projects/tasks"), {}), (
        "the create sends its values itself since #679"
    )


class _Stripping(FakeClient):
    """The fake client, with ONE field removed from ONE route's request.

    It stands for a tool that declares the argument and never sends it.
    """

    def __init__(self, calls, responder, route, name) -> None:
        super().__init__(calls, responder)
        self._route, self._name = route, name

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        path = "/" + url.split("/", 3)[-1]
        row = m.route_for(method, path)
        if row is not None and (row.method, row.path) == self._route:
            kwargs = dict(kwargs)
            for part in ("json", "params"):
                if isinstance(kwargs.get(part), dict):
                    kwargs[part] = {k: v for k, v in kwargs[part].items() if k != self._name}
        return await super().request(method, url, **kwargs)


async def _witness_holds(key, name, monkeypatch, *, strip: bool) -> bool:
    """F2's wire check for one field, with the field stripped or not."""
    # The first witness is the P6 tool's. WS-46 P13 added a second for some.
    witness = m.witnesses(m.SENDS[key][name])[0]
    tool, _, arg = witness.partition(".")
    approve(monkeypatch)
    calls: list[dict] = []
    if strip:
        factory = lambda **_kw: _Stripping(calls, f2._responder, key, name)  # noqa: E731
    else:
        factory = lambda **_kw: FakeClient(calls, f2._responder)  # noqa: E731
    monkeypatch.setattr(client, "httpx", SimpleNamespace(AsyncClient=factory))
    monkeypatch.setattr(client, "current_user_email", lambda: "pm@fracktal.in")
    await getattr(skill_projects, tool)(**f2._call_for(key, name, tool, arg))
    where = f2.route_fields()[key][name]
    sent = [
        c for c in calls if (r := m.route_for(c["method"], c["path"])) and (r.method, r.path) == key
    ]
    return any(f2._carries(c, name, where) for c in sent)


@pytest.mark.parametrize(("key", "name"), P6_SENDS, ids=[f"{k[0]} {k[1]} {n}" for k, n in P6_SENDS])
async def test_a_tool_that_drops_a_p6_field_fails_the_wire_check(key, name, monkeypatch) -> None:
    assert await _witness_holds(key, name, monkeypatch, strip=False), "the witness holds as built"
    assert not await _witness_holds(key, name, monkeypatch, strip=True), (
        f"F2 still passes with {name} stripped from {key[0]} {key[1]}: the check proves nothing"
    )


# ── 5. A refusal reaches the model as text ──────────────────────────────────


async def test_a_p6_refusal_reaches_the_model_as_text(monkeypatch) -> None:
    from skill_projects.refusals import REFUSED, refusals_as_text

    calls = fake_gateway(monkeypatch, _gateway())
    wrapped = refusals_as_text(skill_projects.update_task)
    out = await wrapped(UUID, fields='{"Colour": "red"}')
    assert out.startswith(REFUSED) and "«Customer»" in out and "Next:" in out
    assert writes(calls) == []


# ── R8 — the write the chat sends, on asyncpg, over the tenant ladder ───────

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()
_r8 = pytest.mark.skipif(
    not _TENANT_URL, reason="TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres."
)


@pytest.fixture(scope="module")
def ladder():
    pytest.importorskip("sqlalchemy")
    if not _TENANT_URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    from sqlalchemy import create_engine

    from tests.unit._tenant_ladder import apply_ladder

    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def project(ladder):
    """A project with a lane, a task type and a select field."""
    from sqlalchemy import text

    made: dict[str, str] = {}
    with ladder.begin() as c:
        org = str(
            c.execute(text("SELECT id FROM organization ORDER BY created_at LIMIT 1")).scalar_one()
        )
        made["org"] = org
        made["project"] = str(
            c.execute(
                text(
                    "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
                    " timezone, owns_statuses) VALUES (:n, 'active', 'manual', 'p6@example.test',"
                    " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id"
                ),
                {"n": f"p6-{uuid.uuid4().hex[:6]}", "o": org},
            ).scalar_one()
        )
        made["status"] = str(
            c.execute(
                text(
                    "INSERT INTO pm_task_statuses (project_id, name, color, position, category)"
                    " VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo') RETURNING id"
                ),
                {"p": made["project"]},
            ).scalar_one()
        )
        made["type"] = str(
            c.execute(
                text(
                    "INSERT INTO pm_task_types (project_id, name, organization_id)"
                    " VALUES (CAST(:p AS uuid), 'Bug', CAST(:o AS uuid)) RETURNING id"
                ),
                {"p": made["project"], "o": org},
            ).scalar_one()
        )
    yield made
    with ladder.begin() as c:
        c.execute(
            text("DELETE FROM pm_tasks WHERE project_id = CAST(:p AS uuid)"), {"p": made["project"]}
        )
        for table in ("pm_task_types", "pm_task_statuses"):
            c.execute(
                text(f"DELETE FROM {table} WHERE project_id = CAST(:p AS uuid)"),
                {"p": made["project"]},
            )
        c.execute(text("DELETE FROM pm_projects WHERE id = CAST(:p AS uuid)"), {"p": made["project"]})


@_r8
async def test_r8_the_create_body_lands_on_asyncpg(project) -> None:
    """The POST body the chat sends (a type id and a start date) through the
    create route's ``insert_row``, then values by key through
    ``apply_values`` and ``update_row``, the seams both routes share."""
    pytest.importorskip("asyncpg")
    from gateway.routes.projects import core
    from gateway.routes.projects.custom_fields import apply_values
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    definitions = [
        {"field_key": "customer", "name": "Customer", "field_type": "select",
         "options": ["SMB", "Enterprise"]},
    ]
    eng = create_async_engine(
        _TENANT_URL.replace("+psycopg", "+asyncpg"), future=True, poolclass=NullPool
    )
    try:
        async with eng.begin() as conn:
            row = await core.insert_row(conn, "pm_tasks", {
                "project_id": project["project"],
                "root_project_id": project["project"],
                "status_id": project["status"],
                "title": "Weld the frame",
                "created_by": "p6@example.test",
                "organization_id": project["org"],
                "task_number": 1,
                # What create_task sends for start="2026-10-05" and type="bug".
                "start_date": "2026-10-05",
                "type_id": project["type"],
            })
            assert str(row.type_id) == project["type"]
            assert str(row.start_date) == "2026-10-05"
            merged, changes = apply_values(row.custom_fields, {"customer": "SMB"}, definitions)
            after = await core.update_row(
                conn, "pm_tasks", str(row.id), {"custom_fields": merged}
            )
            stored = after.custom_fields
            stored = json.loads(stored) if isinstance(stored, str) else stored
            assert stored == {"customer": "SMB"} and changes["customer"]["to"] == "SMB"
    finally:
        await eng.dispose()


# ── R8 — the chat's custom values through the REAL create route (#679) ──────
#
# The fixtures of the route's own fence, reused rather than copied: two roots,
# a lane and four fields in ``home``, one field that only ``away`` defines.
from tests.unit.test_projects_create_custom_values import (  # noqa: E402
    _create_on_db,
    _ladder,  # noqa: F401 — a fixture `seeded` needs
    _stored,
    seeded,  # noqa: F401 — a fixture
)


async def _chat_values(monkeypatch, fields: str, rows: list[dict[str, Any]]) -> Any:
    """The ``custom_fields`` that ``create_task`` puts in its POST, over a
    project that lists *rows* as its fields."""

    def answer(call: dict) -> Any:
        if call["path"].endswith("/fields") and call["method"] == "GET":
            return {"rows": rows}
        return _gateway()(call)

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, answer)
    await skill_projects.create_task(UUID, "Weld the frame", fields=fields)
    return _writes_to(calls, "POST", "/projects/tasks")[0]["json"]["custom_fields"]


def _home_rows() -> list[dict[str, Any]]:
    return [
        {"name": "Hours", "field_key": "hours", "field_type": "number", "options": []},
        {"name": "Tier", "field_key": "tier", "field_type": "select",
         "options": ["gold", "silver"]},
        # A field the chat read, which the root does not define (a drift).
        {"name": "Away only", "field_key": "away_only", "field_type": "text", "options": []},
    ]


@_r8
async def test_r8_the_chat_values_are_stored_shaped_by_the_create_route(
    seeded, monkeypatch  # noqa: F811
) -> None:
    custom = await _chat_values(monkeypatch, '{"Hours": "2.5", "tier": "GOLD"}', _home_rows())
    assert custom == {"hours": 2.5, "tier": "gold"}
    created = await _create_on_db(seeded, monkeypatch, custom)
    assert created["custom_fields"] == {"hours": 2.5, "tier": "gold"}
    assert _stored(seeded) == [{"hours": 2.5, "tier": "gold"}]


@_r8
async def test_r8_a_value_the_create_route_refuses_leaves_no_task(
    seeded, monkeypatch  # noqa: F811
) -> None:
    """THE R8 FENCE for the atomic create: the route refuses the value, and
    no row lands. A values PATCH after the create would leave one."""
    from fastapi import HTTPException

    custom = await _chat_values(monkeypatch, '{"Away only": "x"}', _home_rows())
    with pytest.raises(HTTPException) as refused:
        await _create_on_db(seeded, monkeypatch, custom)
    assert refused.value.status_code == 422 and "'away_only'" in str(refused.value.detail)
    assert _stored(seeded) == []
