"""STU-E4: the human step node on a REAL database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.3 and §14 row E4. Code:
``routes/workflows/human_step.py``, ``service._hold_at_human_step``,
``service.resume_run`` and ``scheduler.scan_done_tasks``. The hermetic half is
``test_workflows_human_step.py``.

The world is a dedicated database with the full ladder (migration 427 too) and
the four ``generated/`` phases, as a role that is NOT a superuser, NOT the
owner and has NO BYPASSRLS. Two orgs, each with its own members and workflow.

**The fences, by name.**

* S1. The step makes one task for the assignee and a ``task`` pause, and the
  task links back to the run: ``test_s1_*``.
* S2. A done task resumes the run ONCE, under two scans or a scan racing
  another: ``test_s2_*``.
* S3. A cancelled or deleted task fails the run with a plain message, once:
  ``test_s3_*``.
* S4. A task of another org never resumes a run, and a scan of one org
  never touches another: ``test_s4_*``.
* S5. Refusals before any write: an org off the list, a person of another org,
  a suspended person: ``test_s5_*``.
* S6. Nothing but the scan resumes the pause: ``test_s6_*``.

**Mutations this suite catches** (each was run before the push):

* M1. ``_TASK_SQL``: drop the three ``origin`` predicates (S4 red, the
  untied task case).
* M2. ``human_step.end_step`` and ``resume_run``: drop ``AND status =
  'pending'`` (the race cases of S2 and S3 red).
* M3. ``resume_run``: drop ``AND reason <> 'task'`` (S6 red).
* M4. ``human_step.task_outcome``: ``cancelled`` gives ``resume`` (S3 red).
* M5. ``scheduler.scan_done_tasks``: read the task with an unbound session
  (S4 red, because RLS then shows no row to the app role).
* M6. ``human_step.open_step``: drop the ``_owner_in_org`` check (S5 red).
* M7. ``human_step.open_step``: ``announce=True`` (the event spy of S1 red).
* M8. ``service._hold_at_human_step``: drop the ``org_ready`` check (S5 red).

Run (a PRIVATE database, because the fixture drops ``<db>_humanstep``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_workflows_human_step_r8.py -v -rs

Without the database each test SKIPS. A skip is not a pass.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common.no_person import no_person_present
from gateway.routes.projects import personal
from gateway.routes.workflows import human_step, scheduler, service, unattended
from gateway.routes.workflows.engine.graph import compile_graph
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.unit.test_auto_park import (
    _drop_database,
    _node,
    _race,
    _seed_member,
    _seed_workflow,
)

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. Do NOT export "
        "it as DATABASE_URL."
    ),
)

pytestmark = _DB_GATE

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 60

_APP_ROLE = "acb_app_humanstep"
_APP_PW = "humanstep_scratch_pw"
_SUFFIX = "_humanstep"

OWNER_A = "owner@a.humanstep.test"
LENA_A = "lena@a.humanstep.test"
GONE_A = "gone@a.humanstep.test"
OWNER_B = "owner@b.humanstep.test"
LENA_B = "lena@b.humanstep.test"


def _graph(assignee: str) -> dict:
    nodes = [
        _node("start", "trigger", config={"kind": "manual"}),
        _node("step", "human_step", "Check the contract",
              {"assignee": assignee, "title": "Check the contract",
               "description": "Read page 3."}),
        _node("done", "output", config={"value": "end"}),
    ]
    edges = [{"id": "a", "source": "start", "target": "step"},
             {"id": "b", "source": "step", "target": "done"}]
    return compile_graph({"nodes": nodes, "edges": edges})


def _grant_app_role(ded_admin_url, dedicated: str) -> None:
    """The suite's own role: canonical privileges, no BYPASSRLS."""
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))


@pytest.fixture(scope="module")
def world():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role and two orgs."""
    from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}{_SUFFIX}"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    _grant_app_role(ded_admin_url, dedicated)

    ns = SimpleNamespace(admin_engine=eng)
    ns.org_a, ns.org_b = str(uuid.uuid4()), str(uuid.uuid4())
    ns.wf = {}
    with eng.begin() as conn:
        for oid, slug in ((ns.org_a, "humanstep-a"), (ns.org_b, "humanstep-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                {"id": oid, "s": slug})
        for email in (OWNER_A, LENA_A):
            _seed_member(conn, ns.org_a, email)
        _seed_member(conn, ns.org_a, GONE_A, status="suspended")
        for email in (OWNER_B, LENA_B):
            _seed_member(conn, ns.org_b, email)
        for name, org, owner, who in (
            ("Step in A", ns.org_a, OWNER_A, LENA_A),
            ("Step for a member of B", ns.org_a, OWNER_A, LENA_B),
            ("Step for a suspended member", ns.org_a, OWNER_A, GONE_A),
            ("Step in B", ns.org_b, OWNER_B, LENA_B),
        ):
            serialized = _graph(who)
            ns.wf[name] = (_seed_workflow(conn, org, owner, name, serialized), org, serialized)
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def w(world, monkeypatch):
    """The unattended list holds A and B. No run, task or event is left from
    the last test. The event of a new task is a spy, and so is each resume."""
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    world.listed = f"{world.org_a},{world.org_b}"
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=world.listed))
    world.created_events = []
    real_emit = personal.emit

    async def _emit(event: str, payload: dict) -> None:
        if event == "pm.task.created":
            world.created_events.append(payload)
        await real_emit(event, payload)

    monkeypatch.setattr(personal, "emit", _emit)
    world.executed = []
    real_execute = service._execute_run

    async def _execute(**kw: Any) -> None:
        world.executed.append({"run_id": kw["run_id"], "kind": kw["trigger_kind"]})
        await real_execute(**kw)

    monkeypatch.setattr(service, "_execute_run", _execute)
    world.marks = []
    real_engine = service.execute_workflow

    async def _engine(*a: Any, **kw: Any) -> Any:
        world.marks.append(no_person_present())
        return await real_engine(*a, **kw)

    monkeypatch.setattr(service, "execute_workflow", _engine)
    with world.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflow_runs"))
        c.execute(text("DELETE FROM pm_tasks WHERE source = 'automation'"))
    return world


async def _drain_runs() -> None:
    tasks = [t for t in asyncio.all_tasks()
             if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
    if tasks:
        await asyncio.wait(tasks, timeout=30)


def _on_app_role(w: Any, body: Any) -> Any:
    """Run *body* on the app-role engine, with no tenant in the context."""
    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _go() -> Any:
        async with tenant_engine_scope(w.app_dsn):
            try:
                return await asyncio.wait_for(body(), _WAIT)
            finally:
                await _drain_runs()

    return asyncio.run(_go())


def _start(w: Any, name: str, *, kind: str = "manual") -> str:
    wid, org, serialized = w.wf[name]

    async def body() -> str:
        return await service.start_run(
            workflow_id=wid, workflow_name=name, version=1, serialized=serialized,
            trigger_kind=kind, trigger_payload={}, started_by="tester",
            organization_id=org)

    return _on_app_role(w, body)


def _one(w: Any, sql: str, **params: Any) -> Any:
    with w.admin_engine.connect() as c:
        return c.execute(text(sql), params).fetchone()


def _all(w: Any, sql: str, **params: Any) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _run(w: Any, run_id: str) -> Any:
    return _one(w, "SELECT status, error, node_results FROM workflow_runs "
                   "WHERE id = CAST(:r AS uuid)", r=run_id)


def _pauses(w: Any, run_id: str) -> list[Any]:
    return _all(w, "SELECT id, status, reason, snapshot FROM workflow_run_pauses "
                   "WHERE run_id = CAST(:r AS uuid) ORDER BY created_at", r=run_id)


def _tasks(w: Any, run_id: str) -> list[Any]:
    return _all(w, "SELECT t.*, s.category, p.personal_owner "
                   "FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
                   "JOIN pm_projects p ON p.id = t.project_id "
                   "WHERE t.origin ->> 'run_id' = :r", r=run_id)


def _snap(row: Any) -> dict:
    return row.snapshot if isinstance(row.snapshot, dict) else json.loads(row.snapshot)


def _paused(w: Any, name: str = "Step in A", **kw: Any) -> tuple[str, Any]:
    """A run that paused at its human step on one task."""
    run_id = _start(w, name, **kw)
    assert _run(w, run_id).status == "paused", _run(w, run_id)
    made = _tasks(w, run_id)
    assert len(made) == 1, made
    return run_id, made[0]


def _stage(w: Any, task_id: str, category: str) -> None:
    """Move the task to the stage *category*, as a member's drag on the board
    would. The lane is added to the personal project when it has none."""
    with w.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, category, position, "
            "organization_id) SELECT t.project_id, :n, :c, 95, t.organization_id "
            "FROM pm_tasks t WHERE t.id = CAST(:t AS uuid) AND NOT EXISTS ("
            "SELECT 1 FROM pm_task_statuses s WHERE s.project_id = t.project_id "
            "AND s.category = :c)"), {"n": category.title(), "c": category, "t": task_id})
        c.execute(text(
            "UPDATE pm_tasks SET status_id = (SELECT s.id FROM pm_task_statuses s "
            "WHERE s.project_id = pm_tasks.project_id AND s.category = :c LIMIT 1) "
            "WHERE id = CAST(:t AS uuid)"), {"c": category, "t": task_id})


def _scan(w: Any) -> int:
    return _on_app_role(w, lambda: scheduler.scan_due_waits())


def _resumes(w: Any, run_id: str) -> int:
    return sum(1 for e in w.executed if e["run_id"] == run_id) - 1


# ── S1: the task, the pause and the link ────────────────────────────────────


def test_s1_the_step_makes_one_linked_task_and_pauses_on_it(w) -> None:
    run_id, task = _paused(w)
    pauses = _pauses(w, run_id)
    assert [(p.reason, p.status) for p in pauses] == [("task", "pending")]
    assert _snap(pauses[0])["task_id"] == str(task.id)
    assert task.personal_owner == LENA_A
    assert task.title == "Check the contract"
    assert task.source == "automation"
    assert task.origin["kind"] == "workflow_run"
    assert task.origin["run_id"] == run_id and task.origin["node_id"] == "step"
    assert task.category in ("backlog", "todo")
    assigned = _all(w, "SELECT assignee FROM pm_task_assignees WHERE task_id = :t",
                    t=task.id)
    assert [a.assignee for a in assigned] == [LENA_A]
    # No `pm.task.created` event: it could start a workflow that makes the next task.
    assert w.created_events == []
    # The run holds the first node as done and the step as waiting.
    results = _run(w, run_id).node_results
    assert results["step"]["status"] == "waiting"


def test_s1_an_open_task_resumes_nothing(w) -> None:
    run_id, task = _paused(w)
    for category in ("backlog", "todo", "in_progress"):
        _stage(w, str(task.id), category)
        assert _scan(w) == 0
        assert _run(w, run_id).status == "paused"
    assert _resumes(w, run_id) == 0


# ── S2: a done task resumes the run once ────────────────────────────────────


def test_s2_a_done_task_resumes_the_run_once(w) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "done")
    assert _scan(w) == 1
    run = _run(w, run_id)
    assert run.status == "succeeded", run
    assert run.node_results["step"]["output"] == {
        "completed": True, "task_id": str(task.id)}
    assert [p.status for p in _pauses(w, run_id)] == ["resolved"]
    # A second scan finds no pending pause, and starts nothing.
    assert _scan(w) == 0
    assert _resumes(w, run_id) == 1
    # The first execution had a person present, and the resumed one has none.
    assert w.marks == [False, True]


def test_s2_a_scheduled_run_stays_marked_after_the_step(w) -> None:
    run_id, task = _paused(w, kind="schedule")
    _stage(w, str(task.id), "done")
    assert _scan(w) == 1
    assert _run(w, run_id).status == "succeeded"
    assert w.marks == [True, True]


def test_s2_two_scans_at_once_resume_the_run_once(w, monkeypatch) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "done")
    _race(monkeypatch, 2)

    async def both() -> list[int]:
        org = w.org_a
        return list(await asyncio.gather(
            scheduler.scan_done_tasks(org), scheduler.scan_done_tasks(org)))

    counts = _on_app_role(w, both)
    assert sorted(counts) == [0, 1], counts
    assert _resumes(w, run_id) == 1
    assert _run(w, run_id).status == "succeeded"


# ── S3: a cancelled or deleted task fails the step ──────────────────────────


def test_s3_a_cancelled_task_fails_the_run_once(w) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "cancelled")
    assert _scan(w) == 1
    run = _run(w, run_id)
    assert (run.status, run.error) == ("failed", human_step.CANCELLED_ERROR)
    assert [p.status for p in _pauses(w, run_id)] == ["rejected"]
    assert _scan(w) == 0
    assert _resumes(w, run_id) == 0
    # The plain message names no id.
    assert str(task.id) not in run.error


def test_s3_two_scans_at_once_end_a_cancelled_run_once(w, monkeypatch) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "cancelled")
    _race(monkeypatch, 2)

    async def both() -> list[int]:
        org = w.org_a
        return list(await asyncio.gather(
            scheduler.scan_done_tasks(org), scheduler.scan_done_tasks(org)))

    assert sorted(_on_app_role(w, both)) == [0, 1]
    assert _run(w, run_id).status == "failed"
    assert _resumes(w, run_id) == 0


def test_s3_a_deleted_task_fails_the_run(w) -> None:
    run_id, task = _paused(w)
    with w.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_tasks WHERE id = :t"), {"t": task.id})
    assert _scan(w) == 1
    run = _run(w, run_id)
    assert (run.status, run.error) == ("failed", human_step.GONE_ERROR)
    assert _resumes(w, run_id) == 0


# ── S4: another org never settles a run ─────────────────────────────────────


def test_s4_a_task_of_another_org_never_resumes_the_run(w) -> None:
    """The pause row of A names a DONE task of B. Even when that task carries
    the origin of A's run, the read in A's session finds no row (RLS), so the
    run does not resume. It fails closed."""
    run_b, task_b = _paused(w, "Step in B")
    run_a, _ = _paused(w, "Step in A")
    # Park B's own run for good, so B's scan has nothing of its own to settle.
    with w.admin_engine.begin() as c:
        c.execute(text("UPDATE workflow_run_pauses SET status = 'resolved' "
                       "WHERE run_id = CAST(:r AS uuid)"), {"r": run_b})
        c.execute(text("UPDATE workflow_runs SET status = 'cancelled' "
                       "WHERE id = CAST(:r AS uuid)"), {"r": run_b})
        c.execute(text(
            "UPDATE pm_tasks SET origin = jsonb_set(jsonb_set(origin, '{run_id}', "
            "to_jsonb(CAST(:r AS text))), '{node_id}', '\"step\"') WHERE id = :t"),
            {"r": run_a, "t": task_b.id})
        c.execute(text(
            "UPDATE workflow_run_pauses SET snapshot = jsonb_set(snapshot, '{task_id}', "
            "to_jsonb(CAST(:t AS text))) WHERE run_id = CAST(:r AS uuid)"),
            {"t": str(task_b.id), "r": run_a})
    _stage(w, str(task_b.id), "done")
    _scan(w)
    run = _run(w, run_a)
    assert run.status == "failed" and run.error == human_step.GONE_ERROR
    assert _resumes(w, run_a) == 0


def test_s4_a_task_that_is_not_tied_to_the_run_never_resumes_it(w) -> None:
    """A DONE task of the same org, with the origin of another run."""
    run_id, task = _paused(w)
    other_run = str(uuid.uuid4())
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_tasks SET origin = jsonb_set(origin, '{run_id}', "
            "to_jsonb(CAST(:o AS text))) WHERE id = :t"), {"o": other_run, "t": task.id})
    _stage(w, str(task.id), "done")
    _scan(w)
    run = _run(w, run_id)
    assert run.status == "failed" and run.error == human_step.GONE_ERROR
    assert _resumes(w, run_id) == 0


def test_s4_the_scan_of_one_org_never_touches_a_run_of_another(w) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "done")
    assert _on_app_role(w, lambda: scheduler.scan_done_tasks(w.org_b)) == 0
    assert _run(w, run_id).status == "paused"
    assert _resumes(w, run_id) == 0
    # A scan of the right org then settles it.
    assert _on_app_role(w, lambda: scheduler.scan_done_tasks(w.org_a)) == 1
    assert _run(w, run_id).status == "succeeded"


def test_s4_an_org_off_the_list_is_never_scanned(w) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "done")
    w.listed = w.org_b
    assert _scan(w) == 0
    assert _run(w, run_id).status == "paused"


# ── S5: refusals before any write ───────────────────────────────────────────


def test_s5_an_org_off_the_list_makes_no_task(w) -> None:
    w.listed = w.org_b
    run_id = _start(w, "Step in A")
    run = _run(w, run_id)
    assert (run.status, run.error) == ("failed", human_step.NOT_READY_ERROR)
    assert _tasks(w, run_id) == [] and _pauses(w, run_id) == []


@pytest.mark.parametrize("name", ["Step for a member of B", "Step for a suspended member"])
def test_s5_a_person_who_is_not_an_active_member_gets_no_task(w, name) -> None:
    run_id = _start(w, name)
    run = _run(w, run_id)
    assert (run.status, run.error) == ("failed", human_step.REFUSED_ERROR)
    assert _tasks(w, run_id) == [] and _pauses(w, run_id) == []
    assert _all(w, "SELECT 1 FROM pm_tasks WHERE source = 'automation'") == []


def test_s5_the_agent_and_manual_doors_refuse_when_the_org_is_off_the_list(w) -> None:
    w.listed = w.org_b
    _, org, _ = w.wf["Step in A"]

    async def body() -> dict:
        return await service.run_published_workflow(
            "Step in A", {}, started_by="agent", organization_id=org)

    refused = _on_app_role(w, body)
    assert "human step" in refused["error"]
    assert _all(w, "SELECT 1 FROM workflow_runs") == []
    w.listed = f"{w.org_a},{w.org_b}"
    started = _on_app_role(w, body)
    assert "run_id" in started, started
    assert _run(w, started["run_id"]).status == "paused"


# ── S6: nothing but the scan resumes the pause ──────────────────────────────


def test_s6_an_approval_card_cannot_resume_a_human_step(w) -> None:
    run_id, task = _paused(w)
    _stage(w, str(task.id), "done")  # even a done task: the door is the scan's

    async def door() -> dict:
        return await service.resume_run(
            run_id, resumed_by="approvals-inbox", organization_id=w.org_a)

    assert _on_app_role(w, door)["ok"] is False
    pause_id = str(_pauses(w, run_id)[0].id)

    async def named() -> dict:
        return await service.resume_run(
            run_id, resumed_by="x", organization_id=w.org_a, pause_id=pause_id)

    assert _on_app_role(w, named)["ok"] is False
    assert _run(w, run_id).status == "paused"
    assert _resumes(w, run_id) == 0


def test_s6_the_task_flag_never_resumes_an_approval_pause(w) -> None:
    """``task_done`` is for the ``task`` reason only."""
    run_id, _ = _paused(w)
    with w.admin_engine.begin() as c:
        c.execute(text("UPDATE workflow_run_pauses SET reason = 'approval' "
                       "WHERE run_id = CAST(:r AS uuid)"), {"r": run_id})
    pause_id = str(_pauses(w, run_id)[0].id)

    async def named() -> dict:
        return await service.resume_run(
            run_id, resumed_by="x", organization_id=w.org_a, pause_id=pause_id,
            task_done=True)

    assert _on_app_role(w, named)["ok"] is False
    assert _run(w, run_id).status == "paused"


def test_s6_the_reason_check_accepts_task_and_refuses_an_unknown_reason(w) -> None:
    run_id, _ = _paused(w)
    with pytest.raises(IntegrityError, match="reason_check"), w.admin_engine.begin() as c:
        c.execute(text("INSERT INTO workflow_run_pauses (run_id, node_id, reason, organization_id) "
                       "SELECT id, 'step', 'bogus', organization_id FROM workflow_runs "
                       "WHERE id = CAST(:r AS uuid)"), {"r": run_id})
    with w.admin_engine.begin() as c:
        c.execute(text("INSERT INTO workflow_run_pauses (run_id, node_id, reason, organization_id) "
                       "SELECT id, 'step', 'task', organization_id FROM workflow_runs "
                       "WHERE id = CAST(:r AS uuid)"), {"r": run_id})
