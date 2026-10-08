"""WS-46 P3: the Projects operations eval, with no model and no Docker.

Spec: ``project-docs/specs/projects_agent_parity.md`` §11 (the eval) and
§12, slice P3. D91.

What this file holds, and why each part matters:

1. **The stub keeps the gateway's rules.** A stranger is refused, a route the
   stub does not serve says so, and a repeat rule goes through the route's
   own ``validate_rule``. A stub that accepts anything would agree with any
   wrong call (R8's warning about fakes).
2. **The scripted sweep passes every task, uncovered and covered,** through
   the REAL executor and the real projects-assistant factory. Only the model
   is ``ScriptedModel``. PO-3 is ``xfail`` until P8 and P9.
3. **Each checker fails a wrong run (R7).** Every rule of every checker is
   made to fail by one mutation: of the known-good sequence, of the card
   answer, or of the tool itself (the P2 refusal wrapper and the strict
   argument check). A rule that no mutation can turn red is a rule that
   passes everything, and §11.4 forbids that.
4. **The model sweep never reaches a Router that is not on this machine.**
   A sweep on the production Router spends credits. It is an owner gate.

No database: the stub serves the API, and P3 changes no SQL.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from evals.coding_engine.checkers import Session, ToolCall
from evals.projects_ops import checkers as C
from evals.projects_ops import dataset as D
from evals.projects_ops import run as R
from evals.projects_ops import scripted as S
from evals.projects_ops import stub_api
from evals.projects_ops import tasks as T
from evals.projects_ops.scripted import tool

DS = D.load()
LAUNCH = DS.project("Launch")


def _failed(record: dict[str, Any]) -> set[str]:
    return {r["rule"] for r in record["rules"] if not r["pass"] and not r["advisory"]}


# ── 1. the stub keeps the gateway's rules ───────────────────────────────────


def test_a_stranger_is_refused_and_the_request_is_recorded() -> None:
    stub = stub_api.OpsStub(DS)
    status, body = stub.handle("GET", "/projects/tree", "stranger@elsewhere.example")
    assert status == 403 and body["detail"]
    assert stub.requests[-1].member == "stranger@elsewhere.example"
    assert stub.requests[-1].status == 403


def test_a_route_the_stub_does_not_serve_says_so() -> None:
    stub = stub_api.OpsStub(DS)
    status, body = stub.handle("POST", f"/projects/tasks/{DS.task(1).id}/complete", T.MEMBER)
    assert status == 404 and body["detail"] == stub_api.NOT_SERVED


def test_a_weekly_rule_with_no_day_is_refused_by_the_routes_own_check() -> None:
    stub = stub_api.OpsStub(DS)
    path = f"/projects/tasks/{DS.task(7).id}/recurrence"
    status, body = stub.handle("PUT", path, T.MEMBER, {"freq": "weekly"})
    assert status == 422 and "weekday" in json.dumps(body).lower()
    status, body = stub.handle("PUT", path, T.MEMBER, {"freq": "weekly", "weekdays": [1]})
    assert status == 200 and body["rule"]["weekdays"] == [1]


def test_the_stub_lists_the_overdue_tasks_of_the_dataset() -> None:
    stub = stub_api.OpsStub(DS)
    _, body = stub.handle("GET", "/projects/tasks?overdue=true", T.MEMBER)
    assert [r["task_number"] for r in body["rows"]] == [t.number for t in D.overdue(DS)]
    assert [t.number for t in D.overdue(DS)] == [2, 3, 5, 8]


def test_a_bulk_write_changes_each_task_and_records_the_body() -> None:
    stub = stub_api.OpsStub(DS)
    ids = [DS.task(2).id, DS.task(3).id]
    status, body = stub.handle("POST", "/projects/tasks/bulk", T.MEMBER,
                               {"task_ids": ids, "patch": {"due_at": "2026-10-12"}})
    assert status == 200 and body["applied"] == 2
    assert stub.tasks[ids[0]]["due_at"] == "2026-10-12"
    assert stub.requests[-1].body == {"task_ids": ids, "patch": {"due_at": "2026-10-12"}}


@pytest.mark.parametrize("today, monday", [
    (date(2026, 10, 6), date(2026, 10, 12)),   # a Tuesday
    (date(2026, 10, 12), date(2026, 10, 19)),  # a Monday: next week's Monday
    (date(2026, 10, 11), date(2026, 10, 12)),  # a Sunday
])
def test_next_monday(today: date, monday: date) -> None:
    assert D.next_monday(today) == monday


# ── the task table ──────────────────────────────────────────────────────────


def test_the_eleven_tasks_each_have_a_checker_and_a_script_or_an_xfail() -> None:
    assert tuple(f"PO-{n}" for n in range(1, 12)) == T.TASK_IDS
    assert set(C.CHECKERS) == set(T.TASK_IDS)
    for spec in T.TASKS:
        assert (spec.id in S.SCRIPTED_IDS) is (spec.xfail is None), spec.id
    po3 = T.by_id("PO-3")
    assert po3.xfail and "P8" in po3.xfail and "P9" in po3.xfail


def test_the_task_selector() -> None:
    assert [t.id for t in T.select("all")] == list(T.TASK_IDS)
    assert [t.id for t in T.select("PO-4, PO-1")] == ["PO-4", "PO-1"]
    with pytest.raises(ValueError):
        T.select("PO-12")


# ── 2. the scripted sweep, through the REAL executor ────────────────────────


@pytest.fixture
def harness(tmp_path: Path):
    from orchestrator import executor

    with R.make_harness(D.load(), DS.organization_id, tmp_path / "state",
                        tier="tier-balanced", scripted_mode=True, covered=False) as h:
        yield h
    executor._RUN_QUEUES.clear()
    executor._pending_user_input.clear()


@pytest.fixture
def covered_harness(tmp_path: Path):
    from orchestrator import executor

    with R.make_harness(D.load(), DS.organization_id, tmp_path / "state",
                        tier="tier-balanced", scripted_mode=True, covered=True) as h:
        yield h
    executor._RUN_QUEUES.clear()
    executor._pending_user_input.clear()


def _run(h: R.OpsHarness, task_id: str, steps: list[Any] | None = None,
         spec: T.TaskSpec | None = None) -> dict[str, Any]:
    return asyncio.run(R.run_task(h, spec or T.by_id(task_id), 1, steps=steps))


def _sweep(h: R.OpsHarness, out: Path) -> list[dict[str, Any]]:
    return asyncio.run(R.sweep(h, list(T.TASKS), 1, out))


def test_the_scripted_sweep_passes_every_task_uncovered(
    harness: R.OpsHarness, tmp_path: Path,
) -> None:
    records = _sweep(harness, tmp_path / "out")
    by_task = {r["task"]: r for r in records}
    for task_id, record in by_task.items():
        want = "xfail" if task_id == "PO-3" else "pass"
        assert record["status"] == want, (task_id, record["failure"])
    assert all(r["no_egress"] == [False] for r in records if r["status"] == "pass")
    assert R.coding.exit_code(records) == R.EXIT_PASS
    assert len(list((tmp_path / "out").glob("PO-*-uncovered-run1.json"))) == 11


def test_the_scripted_sweep_passes_every_task_covered(
    covered_harness: R.OpsHarness, tmp_path: Path,
) -> None:
    """H-236: a covered run binds no_egress, and the Projects tools still do the work."""
    records = _sweep(covered_harness, tmp_path / "out")
    for record in records:
        want = "xfail" if record["task"] == "PO-3" else "pass"
        assert record["status"] == want, (record["task"], record["failure"])
    assert all(r["no_egress"] == [True] for r in records if r["status"] == "pass")


def test_a_record_holds_what_the_stub_saw(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-1")
    writes = [(q["method"], q["path"]) for q in record["requests"] if q["method"] != "GET"]
    assert writes[0] == ("POST", "/projects/tasks")
    assert writes[1][0] == "PUT" and writes[1][1].endswith("/recurrence")
    assert {q["member"] for q in record["requests"]} == {T.MEMBER}
    assert record["cards"][0]["answer"] == T.APPROVE
    assert "every week on Friday" in record["cards"][0]["context"]


# ── 3. each rule fails a wrong run (R7) ─────────────────────────────────────


def _po1(**create: Any) -> list[Any]:
    args = {"project_id": LAUNCH.id, "title": "Send the timesheet", "repeat": "weekly",
            "repeat_on": "5", **create}
    return [tool("create_task", **{k: v for k, v in args.items() if v is not None}),
            ("text", "Done. It repeats every week on Friday.")]


def test_po1_a_rule_in_the_title_fails(harness: R.OpsHarness) -> None:
    """The reported failure (§4): "every Friday" in the title, and no rule."""
    record = _run(harness, "PO-1", _po1(title="Send the timesheet every Friday",
                                        repeat=None, repeat_on=None))
    assert record["status"] == "fail"
    assert {"one_rule", "rule_is_weekly", "title_clean"} <= _failed(record)


def test_po1_the_wrong_day_fails(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-1", _po1(repeat_on="4"))
    assert _failed(record) == {"rule_is_weekly"}


def test_po1_an_answer_that_hides_the_rule_fails(harness: R.OpsHarness) -> None:
    steps = [*_po1()[:-1], ("text", "I made the task.")]
    assert _failed(_run(harness, "PO-1", steps)) == {"answer_says_it_repeats"}


def test_po1_a_declined_card_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-1"), cards=(T.DECLINE,))
    record = _run(harness, "PO-1", _po1(), spec=spec)
    assert {"one_card", "one_create"} <= _failed(record)


def test_po1_a_second_write_fails(harness: R.OpsHarness) -> None:
    steps = [*_po1()[:-1], tool("create_task", project_id=LAUNCH.id, title="Another"),
             ("text", "It repeats.")]
    spec = dataclasses.replace(T.by_id("PO-1"), cards=(T.APPROVE, T.APPROVE))
    assert {"one_card", "one_create"} <= _failed(_run(harness, "PO-1", steps, spec=spec))


def test_po2_no_rule_fails(harness: R.OpsHarness) -> None:
    steps = [tool("create_task", project_id=LAUNCH.id, title="Review the backlog"),
             ("text", f"It repeats every week on {D.weekday_name(DS.today)}.")]
    record = _run(harness, "PO-2", steps)
    assert {"one_rule", "rule_is_weekly"} <= _failed(record)


def test_po2_an_answer_that_names_another_day_fails(harness: R.OpsHarness) -> None:
    other = D.weekday_name(DS.today + timedelta(days=1))
    steps = [*S.steps_for("PO-2", DS)[:-1], ("text", f"It repeats every week on {other}.")]
    assert _failed(_run(harness, "PO-2", steps)) == {"answer_names_the_day"}


def _po4(ids: list[str], due: str, answer: str = "Moved.") -> list[Any]:
    return [tool("list_tasks", overdue=True),
            tool("bulk_update", task_ids=",".join(ids), due=due), ("text", answer)]


def test_po4_an_extra_task_fails(harness: R.OpsHarness) -> None:
    ids = [t.id for t in D.overdue(DS)] + [DS.task(4).id]
    record = _run(harness, "PO-4", _po4(ids, D.next_monday(DS.today).isoformat()))
    assert _failed(record) == {"card_lists_the_overdue", "bulk_holds_the_overdue"}


def test_po4_a_missing_task_fails(harness: R.OpsHarness) -> None:
    ids = [t.id for t in D.overdue(DS)][:-1]
    record = _run(harness, "PO-4", _po4(ids, D.next_monday(DS.today).isoformat()))
    assert _failed(record) == {"card_lists_the_overdue", "bulk_holds_the_overdue"}


def test_po4_the_wrong_day_fails(harness: R.OpsHarness) -> None:
    ids = [t.id for t in D.overdue(DS)]
    tuesday = (D.next_monday(DS.today) + timedelta(days=1)).isoformat()
    assert _failed(_run(harness, "PO-4", _po4(ids, tuesday))) == {"due_is_next_monday"}


def test_po4_a_write_one_by_one_fails(harness: R.OpsHarness) -> None:
    """Four cards and four writes are not "one card for the selection"."""
    monday = D.next_monday(DS.today).isoformat()
    steps = [tool("list_tasks", overdue=True),
             *[tool("update_task", task_id=t.id, due=monday) for t in D.overdue(DS)],
             ("text", "Moved.")]
    spec = dataclasses.replace(T.by_id("PO-4"), cards=(T.APPROVE,) * 4)
    failed = _failed(_run(harness, "PO-4", steps, spec=spec))
    assert {"one_card", "bulk_holds_the_overdue", "nothing_else_written"} <= failed


def test_po5_an_approved_card_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-5"), cards=(T.APPROVE,))
    record = _run(harness, "PO-5", spec=spec, steps=S.steps_for("PO-5", DS))
    assert _failed(record) == {"one_card", "zero_writes"}


def test_po5_an_answer_that_claims_the_move_fails(harness: R.OpsHarness) -> None:
    steps = [*S.steps_for("PO-5", DS)[:-1], ("text", "Done. I moved them to Monday.")]
    assert _failed(_run(harness, "PO-5", steps)) == {"answer_says_nothing_changed"}


def test_po6_a_real_lane_written_instead_fails(harness: R.OpsHarness) -> None:
    steps = [tool("update_task", task_id=DS.task(12).id, status="Done"),
             ("text", "The lanes are To do, In progress, In review and Done.")]
    assert _failed(_run(harness, "PO-6", steps)) == {"zero_writes", "refusal_names_the_lanes"}


def test_po6_an_answer_without_the_lanes_fails(harness: R.OpsHarness) -> None:
    steps = [*S.steps_for("PO-6", DS)[:-1], ("text", "There is no lane called Shipped.")]
    assert _failed(_run(harness, "PO-6", steps)) == {"answer_names_the_lanes"}


def test_po6_a_refusal_the_model_cannot_read_fails(
    harness: R.OpsHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool mutation: without P2's wrapper, the model reads "Function failed"."""
    import skill_projects.refusals as refusals

    monkeypatch.setattr(refusals, "refusals_as_text", lambda fn: fn)
    record = _run(harness, "PO-6")
    assert _failed(record) == {"refusal_names_the_lanes"}
    [call] = [c for c in record["sessions"][0]["tool_calls"] if c["name"] == "update_task"]
    assert not call["result"].startswith(C.REFUSED)


def test_po7_an_invented_argument_that_is_dropped_fails(
    harness: R.OpsHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool mutation: the agent registers its tools as before P2, so MAF
    drops ``rrule`` in silence. The checker must see the drop."""
    from agent_framework import FunctionTool
    from skill_projects.refusals import refusals_as_text

    def lenient(fn: Any) -> Any:
        return FunctionTool(name=fn.__name__, description=fn.__doc__ or "",
                            func=refusals_as_text(fn))

    monkeypatch.setattr(harness._module, "_agent_tool", lenient)
    record = _run(harness, "PO-7")
    assert "invented_argument_refused_by_name" in _failed(record)


def test_po7_no_rule_after_the_refusal_fails(harness: R.OpsHarness) -> None:
    steps = [*S.steps_for("PO-7", DS)[:2], ("text", "It repeats every Monday now.")]
    assert {"set_recurrence_made_the_rule", "rule_on_the_task", "one_card",
            "nothing_else_written"} <= _failed(_run(harness, "PO-7", steps))


def test_po7_the_rule_on_another_task_fails(harness: R.OpsHarness) -> None:
    steps = [tool("set_recurrence", task_id=DS.task(8).id, freq="weekly", weekdays="1"),
             ("text", "It repeats.")]
    assert {"rule_on_the_task"} <= _failed(_run(harness, "PO-7", steps))


def _po8(**create: Any) -> list[Any]:
    """PO-8's create, with one argument changed. ``None`` leaves it out."""
    args = {"project_id": LAUNCH.id, "title": "Fix the badge scanner", "type": "Bug",
            "start": D.next_monday(DS.today).isoformat(),
            "fields": json.dumps({"Customer": "Acme"}), **create}
    return [tool("create_task", **{k: v for k, v in args.items() if v is not None}),
            ("text", "I made the bug for Acme.")]


def test_po8_the_known_good_run_passes(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-8")
    assert record["status"] == "pass", record["failure"]
    assert "type: «Bug»" in record["cards"][0]["context"]
    assert "field Customer: «Acme»" in record["cards"][0]["context"]


def test_po8_no_type_fails(harness: R.OpsHarness) -> None:
    assert _failed(_run(harness, "PO-8", _po8(type=None))) == {"type_is_bug"}


def test_po8_the_wrong_start_fails(harness: R.OpsHarness) -> None:
    tuesday = (D.next_monday(DS.today) + timedelta(days=1)).isoformat()
    assert _failed(_run(harness, "PO-8", _po8(start=tuesday))) == {"start_is_next_monday"}


def test_po8_the_field_in_the_text_and_not_in_its_argument_fails(harness: R.OpsHarness) -> None:
    """The failure P1 named, for a field: the setting goes in the description."""
    record = _run(harness, "PO-8", _po8(fields=None, description="Customer: Acme"))
    assert _failed(record) == {"values_in_the_create", "settings_not_in_text"}


def test_po8_the_wrong_value_fails(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-8", _po8(fields=json.dumps({"Customer": "Globex"})))
    assert _failed(record) == {"values_in_the_create"}


def test_po8_a_declined_card_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-8"), cards=(T.DECLINE,))
    record = _run(harness, "PO-8", _po8(), spec=spec)
    assert {"one_card", "one_create", "type_is_bug", "values_in_the_create"} <= _failed(record)


def test_po8_a_second_write_fails(harness: R.OpsHarness) -> None:
    steps = [*_po8()[:-1], tool("update_task", task_id=DS.task(4).id, title="Order banners"),
             ("text", "Made it for Acme.")]
    spec = dataclasses.replace(T.by_id("PO-8"), cards=(T.APPROVE, T.APPROVE))
    assert {"one_card", "nothing_else_written"} <= _failed(
        _run(harness, "PO-8", steps, spec=spec))


def test_po8_an_answer_without_the_customer_fails(harness: R.OpsHarness) -> None:
    steps = [*_po8()[:-1], ("text", "I made the bug.")]
    assert _failed(_run(harness, "PO-8", steps)) == {"answer_names_the_customer"}


def test_po8_a_value_the_route_refuses_is_refused_by_the_stub() -> None:
    """The stub checks a value with the route's own merge, as it checks a rule:
    at the create (#679), where a refusal leaves no task, and at the edit."""
    stub = stub_api.OpsStub(DS)
    before = len(stub.tasks)
    body = {"project_id": LAUNCH.id, "title": "Fix it", "custom_fields": {"customer": "Initech"}}
    status, answer = stub.handle("POST", "/projects/tasks", T.MEMBER, body)
    assert status == 422 and "Initech" in json.dumps(answer) and len(stub.tasks) == before
    body["custom_fields"] = {"customer": "Acme"}
    status, answer = stub.handle("POST", "/projects/tasks", T.MEMBER, body)
    assert status == 200 and answer["custom_fields"] == {"customer": "Acme"}
    path = f"/projects/tasks/{DS.task(4).id}"
    status, body = stub.handle("PATCH", path, T.MEMBER, {"custom_fields": {"customer": "Initech"}})
    assert status == 422 and "Initech" in json.dumps(body)
    status, body = stub.handle("PATCH", path, T.MEMBER, {"custom_fields": {"customer": "Acme"}})
    assert status == 200 and body["custom_fields"] == {"customer": "Acme"}


def _po10(*rows: dict[str, Any], project: str = "Launch", answer: str = "") -> list[Any]:
    """PO-10's batch. No rows means the known-good rows."""
    return [tool("create_tasks", project_id=DS.project(project).id,
                 tasks=json.dumps(list(rows or S.PO10_ROWS))),
            ("text", answer or "I added 3 tasks to Launch.")]


def test_po10_the_known_good_run_passes(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-10")
    assert record["status"] == "pass", record["failure"]
    [card] = record["cards"]
    assert [r["label"] for r in card["rows"]] == [r["title"] for r in S.PO10_ROWS]
    assert card["rows"][0]["hint"].startswith("Priya Menon (priya.menon@eval.example) · no due")
    assert card["title"] == "Create 3 tasks in «Launch»?"


def test_po10_one_call_per_task_fails(harness: R.OpsHarness) -> None:
    """The failure of 2026-10-06: one create_task, and one card, per task."""
    launch = LAUNCH.id
    steps = [*[tool("create_task", project_id=launch, title=r["title"],
                    assignees=r.get("assignees", "")) for r in S.PO10_ROWS],
             ("text", "I added 3 tasks.")]
    spec = dataclasses.replace(T.by_id("PO-10"), cards=(T.APPROVE,) * 3)
    failed = _failed(_run(harness, "PO-10", steps, spec=spec))
    assert {"one_batch_call", "one_card_with_rows", "one_card"} <= failed


def test_po10_a_declined_card_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-10"), cards=(T.DECLINE,))
    failed = _failed(_run(harness, "PO-10", _po10(), spec=spec))
    assert failed == {"one_card", "three_tasks_in_launch", "priya_holds_one"}


def test_po10_an_unticked_row_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-10"), untick=("row-2",))
    record = _run(harness, "PO-10", _po10(), spec=spec)
    assert _failed(record) == {"three_tasks_in_launch"}
    assert [r["body"]["title"] for r in record["requests"]
            if r["method"] == "POST"] == ["Book the caterer", "Test the projector"]


def test_po10_the_wrong_project_fails(harness: R.OpsHarness) -> None:
    assert _failed(_run(harness, "PO-10", _po10(project="Ops"))) == {"three_tasks_in_launch"}


def test_po10_no_owner_fails(harness: R.OpsHarness) -> None:
    rows = [{"title": r["title"]} for r in S.PO10_ROWS]
    assert _failed(_run(harness, "PO-10", _po10(*rows))) == {"priya_holds_one"}


def test_po10_a_second_write_fails(harness: R.OpsHarness) -> None:
    steps = [*_po10()[:-1], tool("update_task", task_id=DS.task(4).id, title="Order banners"),
             ("text", "I added 3 tasks.")]
    spec = dataclasses.replace(T.by_id("PO-10"), cards=(T.APPROVE, T.APPROVE))
    assert {"one_card", "nothing_else_written"} <= _failed(
        _run(harness, "PO-10", steps, spec=spec))


def test_po10_an_answer_without_the_count_fails(harness: R.OpsHarness) -> None:
    steps = _po10(answer="I added the tasks to Launch.")
    assert _failed(_run(harness, "PO-10", steps)) == {"answer_counts_three"}


def _po11(*names: str, project: str = "Launch", answer: str = "") -> list[Any]:
    """PO-11's batch (H-273). No names means the known-good tags."""
    return [tool("create_tags", project_id=DS.project(project).id,
                 tags=json.dumps(list(names or C.PO11_TAGS))),
            ("text", answer or "I registered 3 tags in Launch.")]


def test_po11_the_known_good_run_passes(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-11")
    assert record["status"] == "pass", record["failure"]
    [card] = record["cards"]
    assert [r["label"] for r in card["rows"]] == list(C.PO11_TAGS)
    assert card["title"] == "Add 3 tags to «Launch»?"


def test_po11_one_call_per_tag_fails(harness: R.OpsHarness) -> None:
    """The owner's turn of 2026-10-08: one create_tag, and one card, per tag."""
    steps = [*[tool("create_tag", project_id=LAUNCH.id, name=n) for n in C.PO11_TAGS],
             ("text", "I registered 3 tags.")]
    spec = dataclasses.replace(T.by_id("PO-11"), cards=(T.APPROVE,) * 3)
    failed = _failed(_run(harness, "PO-11", steps, spec=spec))
    assert {"one_batch_call", "one_card_with_rows", "one_card"} <= failed


def test_po11_an_unticked_row_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-11"), untick=("row-2",))
    assert _failed(_run(harness, "PO-11", _po11(), spec=spec)) == {"three_tags_in_launch"}


def test_po11_a_declined_card_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-11"), cards=(T.DECLINE,))
    assert _failed(_run(harness, "PO-11", _po11(), spec=spec)) == {
        "one_card", "three_tags_in_launch"}


def test_po11_the_wrong_project_fails(harness: R.OpsHarness) -> None:
    assert _failed(_run(harness, "PO-11", _po11(project="Ops"))) == {
        "three_tags_in_launch", "nothing_else_written"}


def test_po11_an_answer_without_the_count_fails(harness: R.OpsHarness) -> None:
    steps = _po11(answer="I registered the tags in Launch.")
    assert _failed(_run(harness, "PO-11", steps)) == {"answer_counts_three"}


def test_the_runner_answers_the_rows_of_a_card_as_drawn() -> None:
    """A card with rows is approved with the rows the tool ticked, minus the
    task's unticks. A decline and a plain card stay plain words."""
    rows = [{"id": "row-1", "checked": True}, {"id": "row-2", "checked": True},
            {"id": "row-3", "checked": False}]
    assert R.card_answer(T.APPROVE, rows, ()) == 'APPROVE {"rows": ["row-1", "row-2"]}'
    assert R.card_answer(T.APPROVE, rows, ("row-2",)) == 'APPROVE {"rows": ["row-1"]}'
    assert R.card_answer(T.DECLINE, rows, ()) == T.DECLINE
    assert R.card_answer(T.APPROVE, [], ()) == T.APPROVE
    assert not hasattr(R, "_answer_form"), "the runner answers no form card"


def test_a_covered_sweep_that_ran_uncovered_fails(
    covered_harness: R.OpsHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cover is read from the executor, not from the flag the sweep set."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_coding_scope", "")
    assert _failed(_run(covered_harness, "PO-1")) == {"cover_as_asked"}


# ── 3b. the rules that no tool sequence can break, on a recorded run ────────


def _evidence(task_id: str, **kw: Any) -> C.Evidence:
    session = Session(member=T.MEMBER, thread_id="t", prompt="p",
                      answer=kw.pop("answer", ""), tool_calls=kw.pop("calls", []))
    return C.Evidence(task_id=task_id, dataset=DS, sessions=[session],
                      no_egress=kw.pop("no_egress", [False]), **kw)


def _req(method: str, path: str, body: Any = None, response: Any = None,
         member: str = T.MEMBER) -> stub_api.OpsRequest:
    return stub_api.OpsRequest(method, path, {}, member, 200, body, response)


def test_a_request_as_another_member_fails() -> None:
    ev = _evidence("PO-6", requests=[_req("GET", "/projects/tree", member="x@other.example")])
    assert not next(r for r in C.common(ev) if r.rule == "acting_member").ok


def test_a_write_before_the_card_fails() -> None:
    """A tool cannot write before its card, so this run is recorded by hand."""
    ids = [t.id for t in D.overdue(DS)]
    monday = D.next_monday(DS.today).isoformat()
    card = C.Card("Change 4 tasks at once?", "",
                  "\n".join(f"task {i}: #{t.number}" for i, t in enumerate(D.overdue(DS))),
                  T.APPROVE, at=1)
    early = _req("PATCH", f"/projects/tasks/{ids[0]}", {"due_at": monday})
    bulk = _req("POST", "/projects/tasks/bulk", {"task_ids": ids, "patch": {"due_at": monday}})
    good = _evidence("PO-4", requests=[_req("GET", "/projects/tasks"), bulk], cards=[card])
    bad = _evidence("PO-4", requests=[early, bulk], cards=[card])
    assert C.passed(C.check(good)), C.first_failure(C.check(good))
    failed = {r.rule for r in C.check(bad) if not r.ok}
    assert failed == {"reads_before_the_card", "nothing_else_written"}


def _po3(source: str = "email", parent: str = LAUNCH.id, delegations: int = 1,
         description: str = "Pack 40 kits by Friday.") -> C.Evidence:
    node = {"id": D.ident("node", "Kits"), "name": "Kits"}
    calls = [ToolCall("call_agent", json.dumps({"agent_name": "email-assistant",
                                                "message": "the requests"}), "…", True)
             for _ in range(delegations)]
    tasks = [_req("POST", "/projects/tasks",
                  {"project_id": node["id"], "title": f"Task {n}", "description": description,
                   "source": source}) for n in range(3)]
    card = C.Card("Create this plan?", "", "This text leaves your mailbox.\n"
                  "Pack 40 kits by Friday.", T.APPROVE, at=0)
    return _evidence("PO-3", calls=calls, cards=[card], requests=[
        _req("POST", "/projects/nodes", {"name": "Kits", "parent_id": parent}, node), *tasks,
    ])


def test_po3_its_checker_passes_a_right_run_and_fails_each_wrong_one() -> None:
    """PO-3 is xfail until P8 and P9, so its checker is proven on recorded runs."""
    assert C.passed(C.check_po3(_po3()))

    def failed(ev: C.Evidence) -> set[str]:
        return {r.rule for r in C.check_po3(ev) if not r.ok}

    assert failed(_po3(source="manual")) == {"three_tasks_from_email"}
    assert failed(_po3(parent=DS.project("Ops").id)) == {
        "subproject_under_launch", "three_tasks_from_email"}
    assert failed(_po3(delegations=0)) == {"one_delegation"}
    assert failed(_po3(description="Text the card never showed.")) == {
        "descriptions_from_the_card"}
    outside = _po3()
    outside.requests.append(_req("GET", "/email/messages/1"))
    assert failed(outside) == {"no_email_route"}


# ── 4. the CLI, and the gate on the model sweep ─────────────────────────────


def test_main_scripted_passes_and_writes_a_summary(tmp_path: Path) -> None:
    assert R.main(["--scripted", "--out", str(tmp_path)]) == R.EXIT_PASS
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "scripted" and summary["covered"] is False
    assert any(line.startswith("PO-3") and "xfail" in line for line in summary["lines"])


def test_main_scripted_covered_passes(tmp_path: Path) -> None:
    assert R.main(["--scripted", "--covered", "--tasks", "PO-1,PO-7",
                   "--out", str(tmp_path)]) == R.EXIT_PASS
    record = json.loads((tmp_path / "PO-7-covered-run1.json").read_text(encoding="utf-8"))
    assert record["covered"] is True and record["no_egress"] == [True]


def test_the_model_sweep_is_no_go_without_a_local_router(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "https://router.example.com")
    assert R.main(["--tasks", "PO-1", "--out", str(tmp_path)]) == R.EXIT_NO_GO
    reason = json.loads((tmp_path / "NO-GO.json").read_text(encoding="utf-8"))["no_go"]
    assert "WS43-G6" in reason and "not on this machine" in reason


# ── PO-9: a saved view with its filters (WS-46 P7) ──────────────────────────


def _po9(**save: Any) -> list[Any]:
    """PO-9's save, with one argument changed. ``None`` leaves it out."""
    args = {"project_id": LAUNCH.id, "name": "Overdue by owner", "view_type": "board",
            "filters": json.dumps({"overdue": True}), "group_by": "assignee", **save}
    return [tool("save_view", **{k: v for k, v in args.items() if v is not None}),
            ("text", "I saved the board view «Overdue by owner».")]


def test_po9_the_known_good_run_passes(harness: R.OpsHarness) -> None:
    record = _run(harness, "PO-9")
    assert record["status"] == "pass", record["failure"]
    assert "group by: «assignee»" in record["cards"][0]["context"]


def test_po9_no_filters_fails(harness: R.OpsHarness) -> None:
    assert _failed(_run(harness, "PO-9", _po9(filters=None))) == {"filters_saved"}


def test_po9_a_list_fails(harness: R.OpsHarness) -> None:
    assert _failed(_run(harness, "PO-9", _po9(view_type="list"))) == {"board"}


def test_po9_no_grouping_fails(harness: R.OpsHarness) -> None:
    assert _failed(_run(harness, "PO-9", _po9(group_by=None))) == {"grouped"}


def test_po9_a_key_the_route_drops_fails(harness: R.OpsHarness, monkeypatch) -> None:
    """The failure the rule exists for: the chat writes a key the route's
    normaliser does not keep, and the app opens a view without the filter."""
    def drops(config: Any) -> dict[str, Any]:
        kept = dict(config or {})
        kept["filters"] = {}
        return kept

    monkeypatch.setattr(stub_api, "normalise_config", drops)
    assert _failed(_run(harness, "PO-9")) == {"route_keeps_it"}


def test_po9_a_declined_card_fails(harness: R.OpsHarness) -> None:
    spec = dataclasses.replace(T.by_id("PO-9"), cards=(T.DECLINE,))
    record = _run(harness, "PO-9", _po9(), spec=spec)
    assert {"one_card", "one_view", "board", "filters_saved"} <= _failed(record)


def test_po9_a_second_write_fails(harness: R.OpsHarness) -> None:
    steps = [*_po9()[:-1], tool("update_task", task_id=DS.task(4).id, title="Order banners"),
             ("text", "I saved Overdue by owner.")]
    spec = dataclasses.replace(T.by_id("PO-9"), cards=(T.APPROVE, T.APPROVE))
    assert {"one_card", "nothing_else_written"} <= _failed(
        _run(harness, "PO-9", steps, spec=spec))


def test_po9_an_answer_without_the_view_fails(harness: R.OpsHarness) -> None:
    steps = [*_po9()[:-1], ("text", "I saved the view.")]
    assert _failed(_run(harness, "PO-9", steps)) == {"answer_names_the_view"}


# ── 3c. the member's words in the answer (owner report, 2026-10-07) ─────────


def _answer_rule(answer: str, rule: str) -> bool:
    return next(r for r in C.common(_evidence("PO-6", answer=answer)) if r.rule == rule).ok


def test_an_answer_that_names_a_tool_fails() -> None:
    """Mutation caught: ``answer_names_no_tool`` that reads nothing."""
    assert not _answer_rule("Use `create_project`, one card.", "answer_names_no_tool")
    assert not _answer_rule("propose_plan drafts them.", "answer_names_no_tool")
    # A name a member wrote is data, and an English word is not a tool name.
    assert _answer_rule("I made «create_project notes» and will assign it.", "answer_names_no_tool")


def test_a_stray_mark_fails_and_a_drawable_name_passes() -> None:
    """Mutation caught: ``stray_marks`` that counts every mark, or none."""
    assert _answer_rule("Task #5 «Notification engine» is due.", "answer_marks_drawable")
    assert not _answer_rule("Inside «Hathi Labs a new project sits.", "answer_marks_drawable")
    assert not _answer_rule("«a «b» c»", "answer_marks_drawable")


# ── where the cards go (projects_ai_chat.md §24, owner 2026-10-08) ──────────

_ROWS = "\n".join(
    f"- #{n} «Task {n}» · unassigned\n  full_id: 0f8fad5b-d9cb-469f-a165-{70867728950 + n:012d}"
    for n in range(11, 16)
)


def _genui(ui: dict[str, Any]) -> ToolCall:
    return ToolCall(C.GENUI_TOOL, json.dumps({"ui": json.dumps(ui)}), '{"ok": true}', True)


def _card_rule(calls: list[ToolCall], rule: str) -> bool:
    return next(r for r in C.common(_evidence("PO-6", calls=calls)) if r.rule == rule).ok


_STATS = {"type": "template", "props": {"name": "statDashboard",
                                        "data": {"stats": [{"label": "Open", "value": 5}]}}}
_PICKER = {"hitl": True, "type": "template",
           "props": {"name": "optionPicker", "data": {"options": [{"id": "a", "label": "A"}]}}}


def test_one_answer_card_passes_and_two_fail() -> None:
    """Mutation caught: ``answer_cards`` that counts nothing, or ``<= 1`` made ``<= 2``."""
    assert _card_rule([_genui(_STATS)], "one_answer_card")
    assert not _card_rule([_genui(_STATS), _genui(_STATS)], "one_answer_card")
    # A view draws an answer card too.
    view = ToolCall("render_board", "{}", "Board of «Launch»", True)
    assert not _card_rule([view, _genui(_STATS)], "one_answer_card")


def test_a_refused_card_is_not_counted() -> None:
    """Mutation caught: ``_drew`` that trusts ``ok`` alone. The tool refuses a
    spec it cannot draw with ``{"ok": false}``, and the model draws it again."""
    refused = ToolCall(C.GENUI_TOOL, json.dumps({"ui": json.dumps(_STATS)}),
                       '{"ok": false, "error": "ui: the renderer has no tree type."}', True)
    assert _card_rule([refused, _genui(_STATS)], "one_answer_card")


def test_a_picker_that_waits_is_not_the_answer_card() -> None:
    """Mutation caught: ``is_ask_card`` that reads a picker as an answer."""
    assert _card_rule([_genui(_PICKER), _genui(_STATS)], "one_answer_card")
    assert C.is_ask_card({"type": "card", "children": [
        {"type": "button", "props": {"label": "Go", "action": "go"}}]})
    assert not C.is_ask_card(_STATS)


def test_a_card_that_repeats_a_read_fails() -> None:
    """Mutation caught: ``recarded_reads`` that finds nothing, or that counts a
    card built from part of a read."""
    read = ToolCall("list_tasks", "{}", _ROWS, True)
    mirror = {"type": "list", "props": {"items": [f"#{n} Task {n}" for n in range(11, 16)]}}
    assert not _card_rule([read, _genui(mirror)], "no_read_recarded")
    # Two of five rows: an answer built from the read, not a copy of it.
    part = {"type": "list", "props": {"items": ["#11 Task 11", "#12 Task 12", "#99 Other"]}}
    assert _card_rule([read, _genui(part)], "no_read_recarded")
    # No read before it: nothing to repeat.
    assert _card_rule([_genui(mirror)], "no_read_recarded")
