"""WS-39 SP-5a: cycles (sprints) in the chat (Metorite task 964, D195).

Spec: ``project-docs/specs/project_management_app.md`` §13.2 (the calls). Code:
``skill_projects/cycles.py``: ``list_cycles`` and ``cycle_detail`` (class A),
``create_cycle`` and ``plan_cycle`` (class B), ``start_cycle`` and
``close_cycle`` (class C). The gateway is a fake. The R8 fence of the routes is
``test_sprint_store_r8.py``, and it also holds the same-org rule that the chat
relies on (``test_a_task_of_another_org_goes_in_no_cycle``).

* Each route has a manifest row and a built tool, in its class.
* A flag that is off shows one sentence and makes no card and no write.
* A bulk add and a removal show every task on ONE card.
* A start and a close show the counts on ONE card, and a declined card writes nothing.
* Bad input is a sentence before any card: a date, a name, 51 tasks, a list of cycles.
* A refusal of the route (409) comes back as a sentence, and the answer says how
  many tasks went in.

**The mutations (R7).** Copy the file aside and restore it with ``cp``.
``S`` is ``apps/skills/skill-projects/skill_projects``.

* M1. ``S/cycles.py`` ``plan_cycle``: skip the ``_confirm`` card.
  ``test_a_bulk_add_shows_one_card_with_every_task_then_writes`` goes red.
* M2. ``S/cycles.py`` ``_is_off``: always return False.
  ``test_with_the_flag_off_no_tool_makes_a_card_or_a_write`` goes red.
* M3. ``S/cycles.py`` ``close_cycle``: skip the ``carry_over`` question.
  ``test_a_close_asks_where_the_unfinished_tasks_go`` goes red.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import cycles as C
from skill_projects import manifest
from skill_projects.client import GatewayRefusal

from tests.unit._projects_agent_fakes import FakeResponse, approve, deny, fake_gateway, writes

PID = str(uuid.uuid4())
PLANNED = str(uuid.uuid4())
ACTIVE = str(uuid.uuid4())
NEXT = str(uuid.uuid4())
NEW = str(uuid.uuid4())
T1, T2, T3 = (str(uuid.uuid4()) for _ in range(3))
NODE = {"id": PID, "name": "Launch", "parent_project_id": str(uuid.uuid4())}
BASE = {"goal": "Ship it", "starts_on": "2026-11-16", "ends_on": "2026-11-27"}
PLANNED_ROW = {**BASE, "id": PLANNED, "project_id": PID, "name": "Sprint 7", "state": "planned"}
ACTIVE_ROW = {**BASE, "id": ACTIVE, "project_id": PID, "name": "Sprint 6", "state": "active"}
NEXT_ROW = {**BASE, "id": NEXT, "project_id": PID, "name": "Sprint 8", "state": "planned"}


def _task(tid: str, number: int, category: str = "todo") -> dict[str, Any]:
    return {"id": tid, "task_number": number, "title": f"Task {number}", "status_name": category,
            "status_category": category, "estimate_mins": 60, "archived": False}


def _gateway(monkeypatch, *, tasks=None, cycles=None, off=False, post_refusal=None,  # noqa: C901
             timeout_on=None, already=None, held=None):
    members = tasks if tasks is not None else [_task(T1, 1), _task(T2, 2, "done")]
    rows = {PLANNED: PLANNED_ROW, ACTIVE: ACTIVE_ROW, NEXT: NEXT_ROW}

    def answer(call: dict) -> Any:
        path, method = call["path"], call["method"]
        if path == f"/projects/nodes/{PID}" and method == "GET":
            return NODE
        if (path.endswith("/cycles") or path.startswith("/projects/cycles/")) and off:
            return FakeResponse({"detail": "Not Found"}, 404)
        if path == f"/projects/nodes/{PID}/cycles" and method == "GET":
            return {"project_id": PID, "cycles": cycles if cycles is not None else [
                {**ACTIVE_ROW, "tasks": 2, "hidden": 0}, {**NEXT_ROW, "tasks": 0, "hidden": 0}]}
        if path == f"/projects/nodes/{PID}/cycles" and method == "POST":
            return {"cycle": {**PLANNED_ROW, "id": NEW, **call["json"]}}
        for cid, row in rows.items():
            if path == f"/projects/cycles/{cid}" and method == "GET":
                return {"cycle": row, "tasks": members, "hidden": 0, "committed": None, "capacity": []}
        if path.startswith("/projects/tasks/") and method == "GET":
            tid = path.rsplit("/", 1)[1]
            if tid == T3:
                return FakeResponse({"detail": "Task not found"}, 404)
            row = {"id": tid, "title": f"Task {tid[:4]}", "task_number": 9}
            if held and tid in held:
                row["cycle"] = held[tid]
            return row
        if timeout_on and method == "POST" and path.endswith("/tasks") \
                and call["json"].get("task_id") == timeout_on:
            raise httpx.ReadTimeout("slow")
        if already and method == "POST" and path.endswith("/tasks") \
                and call["json"].get("task_id") == already:
            return {"cycle_id": PLANNED, "task_id": already, "added": False}
        if method in ("POST", "DELETE") and post_refusal and path.endswith("/tasks") \
                and call["json"] and call["json"].get("task_id") == post_refusal:
            return FakeResponse({"detail": {"error": "task_in_cycle",
                                            "message": 'This task is in the cycle "Sprint 5".'}}, 409)
        if path.endswith("/start"):
            return {"cycle": {**PLANNED_ROW, "state": "active"}, "committed_tasks": 2, "hidden": 0}
        if path.endswith("/close"):
            return {"cycle": {**ACTIVE_ROW, "state": "closed"}, "moved": {"next": 0, "backlog": 1}}
        return {"ok": True}

    return fake_gateway(monkeypatch, answer)


def test_the_manifest_maps_the_routes_to_the_built_tools() -> None:
    got = {(r.method, r.path): (r.tool, r.cls) for r in manifest.MANIFEST if "/cycles" in r.path}
    built = {t for t, _ in got.values() if t in skill_projects.__all__}
    assert built == {"list_cycles", "cycle_detail", "create_cycle", "plan_cycle",
                     "start_cycle", "close_cycle"}
    assert {t: c for t, c in got.values() if t in built} == {
        "list_cycles": "A", "cycle_detail": "A", "create_cycle": "B", "plan_cycle": "B",
        "start_cycle": "C", "close_cycle": "C"}
    # The two tools that wait for a later slice stay planned, with their reason.
    assert set(manifest.PLANNED) >= {"update_cycle", "delete_cycle"}
    assert not set(manifest.PLANNED) & built


def test_the_bounds_equal_the_routes() -> None:
    cycles = pytest.importorskip("gateway.routes.projects.cycles")
    core = pytest.importorskip("gateway.routes.projects.core")
    assert (C.NAME_MAX, C.GOAL_MAX, C.SPAN_MAX_DAYS, C.CHOICES_MAX) == (
        cycles.NAME_MAX, cycles.GOAL_MAX, cycles.SPAN_MAX_DAYS, cycles.CHOICES_MAX)
    assert set(C._CLOSING) == set(core.CLOSING_CATEGORIES)


async def test_the_reads_list_the_cycles_and_one_cycle(monkeypatch) -> None:
    asked = approve(monkeypatch)
    _gateway(monkeypatch)
    listed = await skill_projects.list_cycles(PID)
    assert "«Sprint 6»" in listed and f"cycle_id {ACTIVE}" in listed and "active" in listed
    one = await skill_projects.cycle_detail(PLANNED)
    assert "«Task 1»" in one and f"task_id {T1}" in one and "2026-11-16 to 2026-11-27" in one
    assert asked == [], "a read asks nothing"


async def test_with_the_flag_off_no_tool_makes_a_card_or_a_write(monkeypatch) -> None:
    """The routes answer 404 while PROJECTS_SPRINTS_ORGS lacks the org. Each tool
    reads first, so a flag that is off shows one sentence."""
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, off=True)
    outs = [
        await skill_projects.list_cycles(PID),
        await skill_projects.cycle_detail(PLANNED),
        await skill_projects.create_cycle(PID, "S", "2026-11-30", "2026-12-04"),
        await skill_projects.plan_cycle(PLANNED, add_task_ids=T1),
        await skill_projects.start_cycle(PLANNED),
        await skill_projects.close_cycle(ACTIVE, "backlog"),
    ]
    assert all(o == C.CYCLES_OFF for o in outs), outs
    assert asked == [] and writes(calls) == []


async def test_a_missing_cycle_is_not_the_flag(monkeypatch) -> None:
    approve(monkeypatch)

    def answer(call: dict) -> Any:
        return FakeResponse({"detail": "Cycle not found"}, 404)

    fake_gateway(monkeypatch, answer)
    with pytest.raises(GatewayRefusal) as err:
        await skill_projects.cycle_detail(PLANNED)
    assert "Cycle not found" in str(err.value) and C.CYCLES_OFF not in str(err.value)


async def test_a_new_cycle_shows_one_card_then_posts(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.create_cycle(PID, "Sprint 9", "2026-11-30", "2026-12-11", "Ship")
    assert len(asked) == 1 and "«Sprint 9»" in asked[0]["context"]
    sent = [(c["method"], c["path"], c["json"]) for c in writes(calls)]
    assert sent == [("POST", f"/projects/nodes/{PID}/cycles", {
        "name": "Sprint 9", "starts_on": "2026-11-30", "ends_on": "2026-12-11", "goal": "Ship"})]
    assert "Planned the cycle" in out and f"cycle_id: {NEW}" in out, out


@pytest.mark.parametrize("kwargs", [
    {"name": " ", "starts_on": "2026-11-30", "ends_on": "2026-12-11"},
    {"name": "S", "starts_on": "next monday", "ends_on": "2026-12-11"},
    {"name": "S", "starts_on": "2026-12-11", "ends_on": "2026-11-30"},
    {"name": "S", "starts_on": "2026-01-01", "ends_on": "2027-06-01"},
    {"name": "S" * 121, "starts_on": "2026-11-30", "ends_on": "2026-12-11"},
    {"name": "S", "starts_on": "2026-11-30", "ends_on": "2026-12-11", "goal": "g" * 501},
])
async def test_bad_cycle_input_is_a_sentence_before_any_call(kwargs, monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.create_cycle(PID, **kwargs)
    assert asked == [] and calls == []
    assert "Ask the member" in out or "Make it shorter" in out, out


async def test_a_declined_new_cycle_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.create_cycle(PID, "S", "2026-11-30", "2026-12-04")
    assert out == C.CANCELLED and writes(calls) == []


async def test_a_bulk_add_shows_one_card_with_every_task_then_writes(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=f"{T2},{T1},{T1}")
    assert len(asked) == 1, "one card for the whole bulk add"
    assert asked[0]["context"].count("«Task") == 2, "each task once, the repeat dropped"
    sent = [(c["method"], c["path"], c["json"]) for c in writes(calls)]
    assert sent == [("POST", f"/projects/cycles/{PLANNED}/tasks", {"task_id": t})
                    for t in sorted((T1, T2))], "the adds go in id order"
    assert "2 of 2 task changes made" in out and "cycle_id:" in out
    assert out.count("added: #9 «Task") == 2, "each task is named, not a bare id"


async def test_a_removal_goes_through_the_delete_route(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.plan_cycle(PLANNED, remove_task_ids=T1)
    assert len(asked) == 1
    assert [(c["method"], c["path"]) for c in writes(calls)] == [
        ("DELETE", f"/projects/cycles/{PLANNED}/tasks/{T1}")]
    assert "removed: #9 «Task" in out and "1 of 1 task changes made" in out


async def test_a_declined_plan_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch)
    assert await skill_projects.plan_cycle(PLANNED, add_task_ids=T1) == C.CANCELLED
    assert writes(calls) == []


async def test_a_task_that_is_hidden_or_of_another_org_stops_before_the_card(monkeypatch) -> None:
    """The task read answers 404 for a task of another org or one hidden from the
    member. The tool names it and shows no card."""
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=f"{T1},{T3}")
    assert T3 in out and "Nothing was changed" in out
    assert asked == [] and writes(calls) == []


async def test_bad_plan_input_is_a_sentence_before_any_call(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    many = ",".join(str(uuid.uuid4()) for _ in range(C.MAX_BATCH + 1))
    assert "too many" in await skill_projects.plan_cycle(PLANNED, add_task_ids=many)
    assert "both lists" in await skill_projects.plan_cycle(PLANNED, add_task_ids=T1, remove_task_ids=T1)
    assert "Name the tasks" in await skill_projects.plan_cycle(PLANNED)
    with pytest.raises(GatewayRefusal):
        await skill_projects.plan_cycle("not-a-uuid", add_task_ids=T1)
    with pytest.raises(GatewayRefusal):
        await skill_projects.plan_cycle(PLANNED, add_task_ids="nope")
    assert asked == [] and calls == []


async def test_a_closed_cycle_takes_no_change(monkeypatch) -> None:
    asked = approve(monkeypatch)
    closed = {**PLANNED_ROW, "state": "closed"}

    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/cycles/{PLANNED}":
            return {"cycle": closed, "tasks": [], "hidden": 0, "committed": None, "capacity": []}
        return {"id": T1, "title": "t", "task_number": 1}

    calls = fake_gateway(monkeypatch, answer)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=T1)
    assert "is closed" in out and asked == [] and writes(calls) == []


async def test_a_409_after_the_first_add_is_a_partial_receipt(monkeypatch) -> None:
    """A task in another open cycle answers 409. The earlier add stays. The
    answer has a `stopped:` line, names each task, and keeps its id line, so
    the card reads "partial" and not done."""
    approve(monkeypatch)
    first, second = sorted((T1, T2))
    calls = _gateway(monkeypatch, post_refusal=second)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=f"{T1},{T2}")
    lines = out.split("\n")
    assert any(ln.startswith("stopped: #9 «Task " + second[:4]) for ln in lines), out
    assert any(ln.startswith("added: #9 «Task " + first[:4]) for ln in lines), out
    assert "1 of 2 task changes made" in out and "cycle_id:" in out
    assert "«" in next(ln for ln in lines if ln.startswith("stopped:")), "the detail is fenced"
    assert [c["json"]["task_id"] for c in writes(calls)] == [first, second]


async def test_a_409_at_the_first_task_is_a_refusal_with_no_id_line(monkeypatch) -> None:
    """Nothing went in, so the answer has no id line and the card cannot show done."""
    approve(monkeypatch)
    first, second = sorted((T1, T2))
    _gateway(monkeypatch, post_refusal=first)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=f"{T1},{T2}")
    assert "stopped:" in out and "not tried: #9 «Task " + second[:4] in out
    assert "_id:" not in out and "Nothing was changed" in out


async def test_a_timeout_after_the_first_add_keeps_the_receipt(monkeypatch) -> None:
    """A transport fault on add 2 must not escape as a raise: add 1 landed."""
    approve(monkeypatch)
    first, second = sorted((T1, T2))
    _gateway(monkeypatch, timeout_on=second)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=f"{T1},{T2}")
    assert f"added: #9 «Task {first[:4]}»" in out
    assert "stopped:" in out and "ReadTimeout" in out and "may or may not" in out
    assert f"cycle_id: {PLANNED}" in out


async def test_a_task_in_the_cycle_already_is_not_counted_as_added(monkeypatch) -> None:
    approve(monkeypatch)
    _first, second = sorted((T1, T2))
    _gateway(monkeypatch, already=second)
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=f"{T1},{T2}")
    assert "1 of 2 task changes made" in out and f"already in: #9 «Task {second[:4]}»" in out
    only = await skill_projects.plan_cycle(PLANNED, add_task_ids=second)
    assert "0 of 1" in only and "_id:" not in only


async def test_a_task_in_another_open_cycle_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, held={T1: {"id": NEXT, "name": "Sprint 5", "state": "active"}})
    out = await skill_projects.plan_cycle(PLANNED, add_task_ids=T1)
    assert "«Sprint 5»" in out and "Nothing was changed" in out
    assert asked == [] and writes(calls) == []


async def test_a_start_shows_the_counts_on_one_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, cycles=[{**PLANNED_ROW, "tasks": 2, "hidden": 0}])
    out = await skill_projects.start_cycle(PLANNED)
    assert len(asked) == 1
    assert "impact: The start commits 2 tasks" in asked[0]["context"]
    assert "«Task 1»" in asked[0]["context"], "the start card names the tasks it commits"
    assert [(c["method"], c["path"], c["json"]) for c in writes(calls)] == [
        ("POST", f"/projects/cycles/{PLANNED}/start", {})]
    assert "Started the cycle" in out and "2 tasks committed" in out


async def test_a_start_sends_the_goal_the_member_gave(monkeypatch) -> None:
    approve(monkeypatch)
    calls = _gateway(monkeypatch, cycles=[{**PLANNED_ROW, "tasks": 2, "hidden": 0}])
    await skill_projects.start_cycle(PLANNED, goal="A new goal")
    assert writes(calls)[0]["json"] == {"goal": "A new goal"}


async def test_a_start_needs_a_goal_and_one_active_cycle_at_most(monkeypatch) -> None:
    asked = approve(monkeypatch)
    no_goal = {**PLANNED_ROW, "goal": None}

    def answer(call: dict) -> Any:
        if call["path"] == f"/projects/cycles/{PLANNED}":
            return {"cycle": no_goal, "tasks": [], "hidden": 0, "committed": None, "capacity": []}
        return {}

    calls = fake_gateway(monkeypatch, answer)
    assert "has no goal" in await skill_projects.start_cycle(PLANNED)
    assert asked == [] and writes(calls) == []
    # A second active cycle on the node: the sibling list names it.
    _gateway(monkeypatch)
    out = await skill_projects.start_cycle(PLANNED)
    assert "is active on the same project" in out and asked == []


async def test_a_started_or_closed_cycle_does_not_start_again(monkeypatch) -> None:
    approve(monkeypatch)
    _gateway(monkeypatch)
    assert "already started" in await skill_projects.start_cycle(ACTIVE)


async def test_a_close_asks_where_the_unfinished_tasks_go(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.close_cycle(ACTIVE)
    assert "1 tasks of" in out and f"cycle_id {NEXT}" in out and "backlog" in out
    assert asked == [] and writes(calls) == []


async def test_a_close_to_the_backlog_sends_one_choice_for_each_unfinished_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    out = await skill_projects.close_cycle(ACTIVE, "backlog")
    assert len(asked) == 1
    ctx = asked[0]["context"]
    assert "impact: The close ends" in ctx and "1 unfinished tasks move to the backlog" in ctx
    assert [(c["method"], c["path"], c["json"]) for c in writes(calls)] == [
        ("POST", f"/projects/cycles/{ACTIVE}/close",
         {"choices": [{"task_id": T1, "choice": "backlog"}]})]
    assert "Closed the cycle" in out


async def test_a_close_to_the_next_cycle_names_it_and_sends_its_id(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    await skill_projects.close_cycle(ACTIVE, NEXT)
    assert "«Sprint 8»" in asked[0]["context"]
    assert writes(calls)[0]["json"] == {"choices": [{"task_id": T1, "choice": "next", "cycle_id": NEXT}]}


async def test_a_close_with_a_cycle_name_says_carry_over(monkeypatch) -> None:
    approve(monkeypatch)
    _gateway(monkeypatch)
    with pytest.raises(GatewayRefusal) as err:
        await skill_projects.close_cycle(ACTIVE, "Sprint 8")
    assert "carry_over" in str(err.value)


async def test_a_close_refuses_the_cycle_itself_and_a_closed_target(monkeypatch) -> None:
    asked = approve(monkeypatch)
    _gateway(monkeypatch)
    assert "cycle that closes" in await skill_projects.close_cycle(ACTIVE, ACTIVE)
    assert asked == []


async def test_a_close_with_nothing_unfinished_needs_no_choice(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch, tasks=[_task(T1, 1, "done"), _task(T2, 2, "cancelled")])
    await skill_projects.close_cycle(ACTIVE)
    assert len(asked) == 1 and writes(calls)[0]["json"] == {"choices": []}


async def test_a_declined_start_or_close_writes_nothing(monkeypatch) -> None:
    deny(monkeypatch)
    calls = _gateway(monkeypatch, cycles=[{**PLANNED_ROW, "tasks": 2, "hidden": 0}])
    assert await skill_projects.start_cycle(PLANNED) == C.CANCELLED
    assert await skill_projects.close_cycle(ACTIVE, "backlog") == C.CANCELLED
    assert writes(calls) == []


async def test_a_list_of_cycles_is_refused_for_a_start_and_a_close(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = _gateway(monkeypatch)
    assert "ONE" in await skill_projects.start_cycle(f"{PLANNED},{ACTIVE}")
    assert "ONE" in await skill_projects.close_cycle(f"{PLANNED},{ACTIVE}", "backlog")
    assert asked == [] and calls == []


def test_the_text_fences_hold_for_the_module() -> None:
    from pathlib import Path

    source = Path(C.__file__).read_text(encoding="utf-8")
    for phrase in ("you cannot", "admin can", "may not edit"):
        assert phrase not in source.lower(), phrase
