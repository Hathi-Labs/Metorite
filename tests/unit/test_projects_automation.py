"""WS-27f — the automation write seam and agent dispatch.

Spec: `project-docs/specs/project_management_app.md` §6.3/§6.4 ·
`project-docs/specs/workflows_app.md` §13 U1 and U7.

Two halves, and they are deliberately independent:

- **U1** — a `pm_task` node lets the one automation engine ACT on a task. It
  writes through `projects/automation.apply_task_patch`, the same helpers a
  human PATCH uses, so an automation's edit is indistinguishable in validation
  and identical in the timeline.
- **U7** — assigning `agent:<name>` starts a run, from an event sink rather
  than from inside the assignment handler.

Hermetic: `_get_db` is monkeypatched to a fake and the orchestrator is never
imported (a stand-in module takes `orchestrator.executor`'s place where the
run itself is under test). Nothing here needs Docker or a database.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from gateway.routes.projects import agent_dispatch
from gateway.routes.projects.automation import (
    PATCHABLE_FIELDS,
    TaskPatchError,
    apply_task_patch,
    resolve_status,
    workflow_actor,
)
from gateway.routes.workflows.engine.graph import (
    NODE_TYPES,
    PM_TASK_FIELDS,
    validate_graph,
)
from gateway.routes.workflows.engine.handlers import (
    NodeExecutionError,
    NodeServices,
    execute_node,
)

from tests.unit._projects_fakes import (
    DEFAULT_ORGANIZATION,
    FakeProjectsDB,
    bind_db,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def db() -> FakeProjectsDB:
    return FakeProjectsDB()


@pytest.fixture()
def project(db: FakeProjectsDB):
    """A project with three lanes and one task sitting in the first."""
    proj = db.seed_project(name="Delivery")
    todo = db.seed_status(proj.id, name="To do", category="todo", is_default=True)
    doing = db.seed_status(
        proj.id, name="Doing", category="in_progress", is_default=False, position=20
    )
    done = db.seed_status(
        proj.id, name="Shipped", category="done", is_default=False, position=30
    )
    task = db.seed_task(proj.id, todo.id, title="Ship the thing")
    return SimpleNamespace(
        project=proj, todo=todo, doing=doing, done=done, task=task
    )


def run(coro):
    return asyncio.run(coro)


# ── The copied-vocabulary fence ─────────────────────────────────────────────

def test_the_engine_and_the_app_agree_on_what_a_task_node_may_set():
    """`PM_TASK_FIELDS` is a COPY of the app's list, so it needs a fence.

    Both directions on purpose. One direction catches the engine refusing a
    field the app supports (a maker sees "cannot set X" for something that
    works); the other catches the engine publishing a field the app will reject
    at run time, which is the worse half — it moves the failure from the canvas
    to 3am.
    """
    assert set(PATCHABLE_FIELDS) | {"status"} == PM_TASK_FIELDS


def test_structural_moves_are_not_patchable():
    """`project_id`/`parent_task_id` re-stamp `root_project_id` across a whole
    subtree, which is the move endpoint's job. A node that could set them here
    would leave tasks pointing at another project's status rows."""
    assert "project_id" not in PM_TASK_FIELDS
    assert "parent_task_id" not in PM_TASK_FIELDS
    # And status_id specifically: a status move is a TRANSITION, and `status`
    # (by name) is the supported spelling.
    assert "status_id" not in PM_TASK_FIELDS


# ── The actor ───────────────────────────────────────────────────────────────

def test_the_automation_actor_is_the_workflow_not_the_trigger():
    assert workflow_actor("wf-7") == "system:workflow:wf-7"


# ── resolve_status ──────────────────────────────────────────────────────────

def test_status_resolves_by_lane_name_case_insensitively(db, project):
    row = run(resolve_status(db, project.project.id, "  sHiPpEd "))
    assert str(row.id) == str(project.done.id)


def test_status_falls_back_to_category(db, project):
    """`"done"` keeps working on a project whose lane is called "Shipped" —
    otherwise every reusable automation has to know each project's wording."""
    row = run(resolve_status(db, project.project.id, "done"))
    assert str(row.id) == str(project.done.id)


def test_a_name_beats_a_category_when_both_could_match(db, project):
    # Seed a lane literally named "todo" alongside the category `todo`.
    named = db.seed_status(
        project.project.id, name="todo", category="in_progress",
        is_default=False, position=40,
    )
    row = run(resolve_status(db, project.project.id, "todo"))
    assert str(row.id) == str(named.id)


def test_an_unknown_status_names_the_lanes_that_do_exist(db, project):
    with pytest.raises(TaskPatchError) as err:
        run(resolve_status(db, project.project.id, "Finished"))
    message = str(err.value)
    assert "Finished" in message
    # The whole point: the maker is told the vocabulary instead of guessing.
    assert "Shipped" in message and "To do" in message


def test_an_empty_status_is_refused_before_it_scans(db, project):
    with pytest.raises(TaskPatchError):
        run(resolve_status(db, project.project.id, "   "))


# ── apply_task_patch ────────────────────────────────────────────────────────

def test_an_unknown_field_is_refused_and_names_the_legal_set(db, project):
    with pytest.raises(TaskPatchError) as err:
        run(apply_task_patch(db, project.task.id, {"clickup_id": "x"}, actor="a"))
    assert "clickup_id" in str(err.value)


def test_a_field_change_writes_the_field_change_activity(db, project):
    result = run(apply_task_patch(
        db, project.task.id, {"title": "Ship it properly"},
        actor=workflow_actor("wf-1"),
    ))
    assert result["changed"] == ["title"]
    assert result["skipped"] is False
    activities = db.activities("field_change")
    assert len(activities) == 1
    # Attribution is the automation, inside the one actor vocabulary.
    assert activities[0]["created_by"] == "system:workflow:wf-1"


def test_setting_a_field_to_the_value_it_already_has_writes_nothing(db, project):
    """The "already in target state" rule (Paca research §9).

    This is what makes a crashed automation walk safe to retry, and it is what
    stops a workflow that fires on every task update from rewriting the same
    value forever and filling the timeline with edits nobody made.
    """
    result = run(apply_task_patch(
        db, project.task.id, {"title": "Ship the thing"}, actor="a",
    ))
    assert result["skipped"] is True
    assert result["changed"] == []
    assert db.activities("field_change") == []
    # And no UPDATE is issued at all. This is the half that a "did anything
    # change?" assertion misses: `update_row` stamps `updated_at = now()`, so a
    # redundant write is invisible in the diff but leaves the task looking
    # freshly touched — and an automation firing on `pm.task.updated` would
    # bump every task it inspected, forever.
    assert db.statements_touching("UPDATE pm_tasks") == []


def test_a_status_move_is_a_transition_not_a_column_write(db, project):
    """Delegated to `apply_status_transition`, which owns `completed_at` and
    the `status_change` activity. A node that wrote the column would look right
    on the board and leave the timeline silent."""
    result = run(apply_task_patch(
        db, project.task.id, {"status": "Shipped"}, actor=workflow_actor("wf-2"),
    ))
    assert result["changed"] == ["status"]
    assert result["status"] == "Shipped"
    moves = db.activities("status_change")
    assert len(moves) == 1
    assert moves[0]["created_by"] == "system:workflow:wf-2"
    # The transition's own effect, not the node's.
    task = next(r for r in db.rows("pm_tasks") if str(r["id"]) == str(project.task.id))
    assert task["completed_at"] is not None


def test_moving_a_task_to_the_status_it_is_already_in_is_a_no_op(db, project):
    result = run(apply_task_patch(
        db, project.task.id, {"status": "To do"}, actor="a",
    ))
    assert result["skipped"] is True
    assert db.activities("status_change") == []


def test_one_node_sets_several_fields_at_once(db, project):
    """Paca's consolidation lesson adopted directly: `update_task` replaced five
    single-field actions. A per-field node set is the thing not to build."""
    result = run(apply_task_patch(
        db, project.task.id,
        {"title": "Renamed", "importance": 1, "status": "Doing"},
        actor="a",
    ))
    assert set(result["changed"]) == {"title", "importance", "status"}
    assert len(db.activities("field_change")) == 1
    assert len(db.activities("status_change")) == 1


def test_a_partly_redundant_patch_writes_only_the_part_that_changed(db, project):
    run(apply_task_patch(db, project.task.id, {"title": "Renamed"}, actor="a"))
    result = run(apply_task_patch(
        db, project.task.id, {"title": "Renamed", "importance": 3}, actor="a",
    ))
    assert result["changed"] == ["importance"]


# ── Graph validation (the publish gate) ─────────────────────────────────────

def _graph(config: dict) -> dict:
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"config": {}}},
            {"id": "n", "type": "pm_task", "data": {"config": config}},
        ],
        "edges": [{"id": "e", "source": "t", "target": "n"}],
    }


def _codes(graph: dict) -> set[str]:
    return {issue.code for issue in validate_graph(graph)}


def test_the_task_node_type_exists():
    assert "pm_task" in NODE_TYPES


def test_a_valid_task_node_publishes_clean():
    assert _codes(_graph({"task_id": "{{trigger.task_id}}", "fields": {"status": "done"}})) == set()


def test_a_task_node_without_a_target_fails_at_publish():
    assert "missing_config" in _codes(_graph({"fields": {"status": "done"}}))


def test_a_task_node_that_sets_nothing_fails_at_publish():
    assert "missing_config" in _codes(_graph({"task_id": "abc", "fields": {}}))
    assert "missing_config" in _codes(_graph({"task_id": "abc"}))


def test_an_unknown_field_fails_at_publish_not_at_run_time(db):
    """The gate's whole purpose: the maker learns while looking at the canvas,
    not silently at 3am against a field that does not exist."""
    codes = _codes(_graph({"task_id": "abc", "fields": {"assignee": "x"}}))
    assert "unknown_field" in codes


def test_a_task_node_does_NOT_need_an_approval_ancestor():
    """Pinned deliberately, because the default reading of "write" would gate it.

    The `write_without_approval` gate exists for OUTWARD writes through the
    Integration Registry. A task moving to Done is internal, and nobody wants
    an approval step on it — so this must stay un-gated on purpose rather than
    by accident.
    """
    assert "write_without_approval" not in _codes(
        _graph({"task_id": "abc", "fields": {"status": "done"}})
    )


# ── The node handler ────────────────────────────────────────────────────────

def _services(**over) -> NodeServices:
    async def _unused(*a, **k):  # pragma: no cover - never called
        raise AssertionError("wrong seam")

    return NodeServices(
        run_agent=_unused, run_tool=_unused, get_module_code=_unused,
        actor="workflow:test", **over,
    )


def test_the_handler_resolves_refs_before_calling_the_seam():
    seen: dict = {}

    async def _update(task_id: str, fields: dict) -> dict:
        seen["task_id"] = task_id
        seen["fields"] = fields
        return {"changed": ["status"]}

    out = run(execute_node(
        {
            "type": "pm_task",
            "config": {"task_id": "{{trigger.task_id}}", "fields": {"status": "{{trigger.lane}}"}},
        },
        {"trigger": {"task_id": "T-1", "lane": "Shipped"}},
        _services(update_task=_update),
    ))
    assert seen == {"task_id": "T-1", "fields": {"status": "Shipped"}}
    assert out == {"changed": ["status"]}


def test_the_handler_fails_loudly_when_projects_is_not_wired():
    with pytest.raises(NodeExecutionError):
        run(execute_node(
            {"type": "pm_task", "config": {"task_id": "T", "fields": {"status": "done"}}},
            {},
            _services(),
        ))


def test_a_ref_that_resolves_to_nothing_fails_the_node_rather_than_patching_nothing():
    async def _update(task_id: str, fields: dict) -> dict:  # pragma: no cover
        raise AssertionError("should not reach the seam")

    with pytest.raises(NodeExecutionError):
        run(execute_node(
            {"type": "pm_task", "config": {"task_id": "{{trigger.missing}}", "fields": {"status": "x"}}},
            {"trigger": {}},
            _services(update_task=_update),
        ))


# ── U7 — assignment is dispatch ─────────────────────────────────────────────
#
# ⚠️ Every payload here carries `organization_id` because the REAL emitter
# does (`tasks.set_assignees`, WS-27aa) and the sink refuses without it. The
# helper exists so that is one fact in one place: a test that hand-built the
# dict would drift from the emitter, and the drift would read as "dispatch
# is broken".


def _assigned(task_id, assignees, org: str | None = DEFAULT_ORGANIZATION):
    payload = {"task_id": str(task_id), "assignees": assignees}
    if org is not None:
        payload["organization_id"] = org
    return payload


async def _dispatch(payload):
    """The sink, then every run it started.

    The sink starts each run in the background and returns, so a test that
    reads the runs must wait for them. Without the wait, a test passes or
    fails on the order asyncio happens to run its ready callbacks in.
    """
    await agent_dispatch.on_event("projects", "pm.task.assigned", payload)
    await agent_dispatch.wait_for_runs()


def test_agent_targets_selects_agents_and_ignores_people():
    assert agent_dispatch.agent_targets(
        ["priya@fracktal.in", "agent:Researcher", "agent:researcher", "sam@x.com"]
    ) == ["researcher"]


def test_agent_targets_ignores_a_bare_prefix():
    assert agent_dispatch.agent_targets(["agent:", "agent:  "]) == []


def test_agent_targets_tolerates_a_payload_that_is_not_a_list():
    # An event payload is not a database row; it should not be trusted to have
    # been through the API's normalisation.
    assert agent_dispatch.agent_targets(None) == []
    assert agent_dispatch.agent_targets("agent:x") == []


def test_the_message_names_the_task_and_carries_its_id():
    message = agent_dispatch.build_message(
        SimpleNamespace(id="T-9", task_number=42, title="Fix the thing", description="Details")
    )
    assert "#42" in message
    assert "Fix the thing" in message
    assert "Details" in message
    assert "T-9" in message


def test_a_task_with_no_description_still_produces_a_message():
    message = agent_dispatch.build_message(
        SimpleNamespace(id="T-9", task_number=None, title="Bare", description=None)
    )
    assert "Bare" in message and "T-9" in message


def test_the_sink_ignores_events_from_other_sources_and_types(monkeypatch, db):
    bind_db(monkeypatch, db, (agent_dispatch,))
    calls: list = []
    monkeypatch.setattr(
        agent_dispatch, "_run_and_record",
        lambda *a: calls.append(a) or _noop(),
    )
    run(agent_dispatch.on_event("clickup", "pm.task.assigned", {"assignees": ["agent:x"]}))
    run(agent_dispatch.on_event("projects", "pm.task.updated", {"assignees": ["agent:x"]}))
    assert calls == []


def test_assigning_a_person_dispatches_nothing(monkeypatch, db, project):
    bind_db(monkeypatch, db, (agent_dispatch,))
    calls: list = []
    monkeypatch.setattr(
        agent_dispatch, "_run_and_record",
        lambda *a: calls.append(a) or _noop(),
    )
    run(_dispatch(
        _assigned(project.task.id, ["priya@fracktal.in"]),
    ))
    assert calls == []
    assert db.activities("agent_run") == []


def test_assigning_an_agent_records_the_handoff_BEFORE_the_run(monkeypatch, db, project):
    """Paca's `agent.session.started` move.

    A handoff that is invisible until the agent finishes looks, for its whole
    duration, exactly like a handoff that never happened. So the activity is
    asserted to exist at the moment dispatch is invoked, not afterwards.
    """
    bind_db(monkeypatch, db, (agent_dispatch,))
    seen_at_dispatch: list[int] = []

    async def _fake_run(agent, message, task_id, organization_id) -> None:
        seen_at_dispatch.append(len(db.activities("agent_run")))

    monkeypatch.setattr(agent_dispatch, "_run_and_record", _fake_run)
    run(_dispatch(
        _assigned(project.task.id, ["agent:researcher"]),
    ))
    assert seen_at_dispatch == [1]
    activity = db.activities("agent_run")[0]
    assert activity["created_by"] == "agent:researcher"


def test_a_missing_task_dispatches_nothing(monkeypatch, db):
    bind_db(monkeypatch, db, (agent_dispatch,))
    calls: list = []
    monkeypatch.setattr(
        agent_dispatch, "_run_and_record",
        lambda *a: calls.append(a) or _noop(),
    )
    run(_dispatch(
        _assigned("00000000-0000-0000-0000-000000000000", ["agent:researcher"]),
    ))
    assert calls == []


def test_two_agents_on_one_assignment_each_get_a_run(monkeypatch, db, project):
    bind_db(monkeypatch, db, (agent_dispatch,))
    dispatched: list[str] = []

    async def _fake_run(agent, message, task_id, organization_id) -> None:
        dispatched.append(agent)

    monkeypatch.setattr(agent_dispatch, "_run_and_record", _fake_run)
    run(_dispatch(
        _assigned(project.task.id, ["agent:a", "agent:b"]),
    ))
    assert dispatched == ["a", "b"]


# ── U7's tenant (WS-27aa / H4) ──────────────────────────────────────────────
#
# This module was the H2 ratchet's one Projects exemption. It stopped being one
# by taking an EXPLICIT tenant off the event, not by converting to the ambient
# `tenant_session()` — which for a sink fired from `emit_event`'s fan-out would
# have inherited whoever's request happened to be in context.


@pytest.mark.parametrize("org", [None, "", "   "])
def test_an_event_without_a_tenant_refuses_instead_of_running_unbound(
    monkeypatch, db, project, org,
):
    """No tenant → no session, no activity, no run. The refusal is a log line
    (`projects.agent_dispatch_refused`), NOT a timeline row: `pm_activities`
    is tenant data, so recording it there would need the very unbound session
    being refused — and under RLS phase 4 would write nothing anyway."""
    bind_db(monkeypatch, db, (agent_dispatch,))
    calls: list = []
    monkeypatch.setattr(
        agent_dispatch, "_run_and_record",
        lambda *a: calls.append(a) or _noop(),
    )
    run(_dispatch(
        _assigned(project.task.id, ["agent:researcher"], org=org),
    ))
    assert calls == []
    assert db.activities("agent_run") == []
    assert db.bound_tenants == []
    assert db.statements == []


def test_the_sink_binds_the_events_tenant_explicitly(monkeypatch, db, project):
    """`[None]` here would mean the ambient form — the inheritance H4 forbids."""
    bind_db(monkeypatch, db, (agent_dispatch,))

    async def _fake_run(agent, message, task_id, organization_id) -> None:
        return None

    monkeypatch.setattr(agent_dispatch, "_run_and_record", _fake_run)
    run(_dispatch(
        _assigned(project.task.id, ["agent:researcher"]),
    ))
    assert db.bound_tenants == [DEFAULT_ORGANIZATION]


@pytest.mark.parametrize("ok", [True, False])
def test_the_outcome_write_opens_its_session_under_the_threaded_tenant(
    monkeypatch, db, project, ok,
):
    """`_record_outcome` outlives the transaction that dispatched it, so the
    org is THREADED down rather than re-read — there is nothing left to read
    it from, and re-resolving would be a second answer to a settled question.

    Both arms, because the failure arm is the one that writes when everything
    else has already gone wrong, and it is the one a refactor forgets.
    """
    bind_db(monkeypatch, db, (agent_dispatch,))
    run(agent_dispatch._record_outcome(
        str(project.task.id), "researcher", DEFAULT_ORGANIZATION,
        ok=ok, detail="the agent exploded",
    ))
    assert db.bound_tenants == [DEFAULT_ORGANIZATION]
    last = db.activities("agent_run")[-1]
    assert last["meta"]["state"] == ("finished" if ok else "failed")
    assert "exploded" in str(last["body"])


def test_the_dispatch_loop_hands_the_events_tenant_to_every_run(
    monkeypatch, db, project,
):
    """The thread from payload → `_run_and_record` → `_record_outcome`, pinned
    at the one join a unit test can see without importing the orchestrator."""
    bind_db(monkeypatch, db, (agent_dispatch,))
    seen: list[tuple] = []

    async def _fake_run(agent, message, task_id, organization_id) -> None:
        seen.append((agent, task_id, organization_id))

    monkeypatch.setattr(agent_dispatch, "_run_and_record", _fake_run)
    run(_dispatch(
        _assigned(project.task.id, ["agent:a", "agent:b"]),
    ))
    assert seen == [
        ("a", str(project.task.id), DEFAULT_ORGANIZATION),
        ("b", str(project.task.id), DEFAULT_ORGANIZATION),
    ]


# ── The run itself: the payload, the reply, the failure, the wait ───────────
#
# From 2026-08-06 until 2026-10-04 the sink handed `run_agent` a STRING, and
# every dispatched run failed with `'str' object has no attribute 'keys'`.
# Every test above replaces `_run_and_record`, so none of them could see it.
# These tests run the REAL `_run_and_record` against a stand-in executor
# module. `test_projects_agent_dispatch_run.py` runs the real executor on a
# real Postgres.


def _executor(monkeypatch, run_agent) -> None:
    """Stand in for `orchestrator.executor`, so this suite imports no runtime."""
    monkeypatch.setitem(
        sys.modules, "orchestrator.executor", SimpleNamespace(run_agent=run_agent),
    )


class _Log:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **fields) -> None:
        self.warnings.append((event, fields))


def test_the_run_is_handed_a_dict_payload_and_the_tenant_as_a_keyword(
    monkeypatch, db, project,
):
    bind_db(monkeypatch, db, (agent_dispatch,))
    calls: list[tuple] = []

    async def run_agent(agent, payload, **kwargs):
        calls.append((agent, payload, kwargs))
        return {"answer": "done", "result": "done"}

    _executor(monkeypatch, run_agent)
    run(_dispatch(_assigned(project.task.id, ["agent:researcher"])))
    [(agent, payload, kwargs)] = calls
    assert agent == "researcher"
    assert isinstance(payload, dict), f"run_agent was handed {type(payload).__name__}"
    assert "Ship the thing" in payload["message"]
    assert payload["source"] == agent_dispatch.RUN_SOURCE
    # The tenant is a keyword. The payload is agent-visible (R5, R11).
    assert kwargs == {"organization_id": DEFAULT_ORGANIZATION}
    assert "organization_id" not in payload


@pytest.mark.parametrize("result, shown", [
    ({"answer": "A", "result": "R"}, "R"),
    ({"answer": "A"}, "A"),
    ({"result": {"content": "C"}}, "C"),
    ({"answer": "", "result": ""}, "Finished with no reply."),
    (None, "Finished with no reply."),
])
def test_the_timeline_shows_the_reply_and_never_the_result_dict(
    monkeypatch, db, project, result, shown,
):
    bind_db(monkeypatch, db, (agent_dispatch,))

    async def run_agent(agent, payload, **kwargs):
        return result

    _executor(monkeypatch, run_agent)
    run(_dispatch(_assigned(project.task.id, ["agent:researcher"])))
    last = db.activities("agent_run")[-1]
    assert last["meta"]["state"] == "finished"
    assert last["body"] == shown


def test_a_long_reply_is_cut_at_the_limit(monkeypatch, db, project):
    bind_db(monkeypatch, db, (agent_dispatch,))

    async def run_agent(agent, payload, **kwargs):
        return {"result": "x" * (agent_dispatch.REPLY_LIMIT + 50)}

    _executor(monkeypatch, run_agent)
    run(_dispatch(_assigned(project.task.id, ["agent:researcher"])))
    assert len(db.activities("agent_run")[-1]["body"]) == agent_dispatch.REPLY_LIMIT


@pytest.mark.parametrize("error, detail", [
    (RuntimeError("the model is down"), "the model is down"),
    # An exception with no message still says what failed.
    (RuntimeError(), "RuntimeError"),
])
def test_a_failed_run_closes_the_handoff_and_logs_a_warning(
    monkeypatch, db, project, error, detail,
):
    """The 'keys' failure was a timeline row on a task nobody opened, and no
    log line. A failure now writes both."""
    bind_db(monkeypatch, db, (agent_dispatch,))
    log = _Log()
    monkeypatch.setattr(agent_dispatch, "_log", log)

    async def run_agent(agent, payload, **kwargs):
        raise error

    _executor(monkeypatch, run_agent)
    run(_dispatch(_assigned(project.task.id, ["agent:researcher"])))
    last = db.activities("agent_run")[-1]
    assert last["meta"]["state"] == "failed"
    assert last["body"] == f"Agent run failed: {detail}"
    [(event, fields)] = log.warnings
    assert event == "projects.agent_dispatch_failed"
    assert fields["error"] == detail and fields["task_id"] == str(project.task.id)


def test_the_sink_returns_while_the_run_is_still_running(monkeypatch, db, project):
    """`PUT /tasks/{id}/assignees` awaits this sink. A sink that awaited the
    run held the member's request open for the whole run."""
    bind_db(monkeypatch, db, (agent_dispatch,))
    release = asyncio.Event()

    async def run_agent(agent, payload, **kwargs):
        await release.wait()
        return {"result": "done"}

    _executor(monkeypatch, run_agent)

    async def go() -> list[str]:
        await asyncio.wait_for(
            agent_dispatch.on_event(
                "projects", "pm.task.assigned",
                _assigned(project.task.id, ["agent:researcher"]),
            ),
            timeout=5,
        )
        before = [a["meta"]["state"] for a in db.activities("agent_run")]
        assert agent_dispatch._RUNS, "the sink started no run"
        release.set()
        await agent_dispatch.wait_for_runs()
        return before

    assert run(go()) == ["started"]
    assert [a["meta"]["state"] for a in db.activities("agent_run")] == [
        "started", "finished",
    ]
    assert not agent_dispatch._RUNS, "a finished run stayed in the set"


async def _noop() -> None:
    return None
