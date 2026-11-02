"""WS-AUTO AUTO-7a on a real database: a refusal writes nothing (R8).

Spec: ``project-docs/specs/proactive_agent.md`` §4.2. The hermetic half is
``test_no_person_present.py``. A hermetic fake agrees with any write, so "no
write" is proved here, on real rows, as the NOBYPASSRLS app role of the
phase-4 rehearsal.

Each test has the same shape. A marked turn tries to settle a real row, and
the row stays as it was. Then a person does the same act, and it works. So the
fence cannot pass because the act is broken.

* a job handler, in both worker modes, tries to approve and to reject a real
  ``pending_actions`` row;
* a marked turn tries to resume a paused run. A person then approves. The
  resumed run of a schedule stays marked, and a manual run does not;
* a marked turn tries to answer a real question and a real archive request.

Run (a PRIVATE database, because ``promoted`` drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/auto7a_r8
    uv run pytest tests/unit/test_no_person_present_r8.py -v -rs

Without the database each R8 test here SKIPS. A skip is not a pass.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.no_person import (
    NO_PERSON_PRESENT,
    NO_PERSON_TEXT,
    no_person_present,
    no_person_scope,
)
from acb_skills.ask_tools import request_confirmation
from gateway.jobs import queue, worker
from gateway.routes.workflows import service
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_action_broker_tenancy_r8 import _as_tenant, broker_as_app  # noqa: F401
from tests.unit.test_agent_runs_r8 import (  # noqa: F401
    _act,
    _key,
    _select,
    _session,
    _start,
    _status,
    w,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
TAG = uuid.uuid4().hex[:8]
SESSION_KIND = f"test.auto7a{TAG}.session"
DETACHED_KIND = f"test.auto7a{TAG}.detached"
ACTION = f"auto7a{TAG}.write"
#: The frozen clock of the jobs.
T = datetime(2099, 3, 10, 9, 0, tzinfo=UTC)
PERSON = "alice@a.test"


def _proposal(action: str, payload: dict[str, Any]) -> Any:
    from action_broker import AuthorityTier, propose

    return propose("agent:auto7a", action, "t:auto7a", payload, authority=AuthorityTier.SUGGEST)


def _row(p: Any, row_id: str) -> Any:
    """The row as the superuser sees it, past every policy."""
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, reviewed_by, reviewed_at FROM pending_actions "
            "WHERE id = CAST(:i AS uuid)"), {"i": row_id}).first()


def _dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def _refused(answer: dict[str, Any]) -> bool:
    return answer.get("ok") is False and answer.get("error") == NO_PERSON_PRESENT


# ── The job worker and the Action Broker ────────────────────────────────────


@pytest.fixture
def jobs(broker_as_app, monkeypatch):  # noqa: F811
    """An empty worker registry. Teardown deletes the rows of this suite."""
    monkeypatch.setattr(worker, "_HANDLERS", {})
    yield broker_as_app
    with broker_as_app.admin_engine.begin() as c:
        c.execute(text("DELETE FROM jobs WHERE kind LIKE :k"), {"k": f"test.auto7a{TAG}.%"})


@_DB_GATE
def test_r8_a_job_cannot_settle_a_row_and_a_person_can(jobs) -> None:
    """In both worker modes. The handler holds a real id, and it tries each
    door, with no mark of its own: the worker set it."""
    from action_broker import approve, enqueue, register_action_handler, reject

    p, org = jobs, jobs.org_a
    written: list[Any] = []

    async def _write(proposal: Any) -> str:
        written.append(proposal.id)
        return "written"

    register_action_handler(ACTION, _write)
    with _as_tenant(org):
        row_id = enqueue(_proposal(ACTION, {"body": "from the page"}))
    assert row_id is not None
    said: dict[str, Any] = {}

    async def _session_job(_db: Any, payload: dict[str, Any]) -> None:
        said["session_marked"] = no_person_present()
        said["session_approve"] = await approve(payload["id"], "agent")
        said["session_reject"] = reject(payload["id"], "agent")

    async def _detached_job(org_: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        said["detached_marked"] = no_person_present()
        said["detached_approve"] = await approve(payload["id"], "agent")
        said["detached_card"] = await request_confirmation(
            "Approve it?", non_interactive_default="approve")
        async with tenant_session(org_) as db:
            assert await queue.hold(db, claim)
            assert await queue.complete(db, claim, now=T) == 1

    worker.register(SESSION_KIND, _session_job)
    worker.register(DETACHED_KIND, _detached_job, mode="detached", idempotent=True)

    async def go() -> list[str | None]:
        async with tenant_engine_scope(_dsn(p)):
            token = bind_tenant(org)
            try:
                async with tenant_session(org) as db:
                    await queue.enqueue(db, SESSION_KIND, {"id": row_id}, now=T)
                    await queue.enqueue(db, DETACHED_KIND, {"id": row_id}, now=T)
                return [await worker.run_one(org, T), await worker.run_one(org, T)]
            finally:
                release_tenant(token)

    assert asyncio.run(go()) == ["done", "done"]
    assert said["session_marked"] is True and said["detached_marked"] is True
    for key in ("session_approve", "session_reject", "detached_approve"):
        assert _refused(said[key]), key
    assert said["detached_card"] is False
    stored = _row(p, row_id)
    assert (stored.status, stored.reviewed_by, stored.reviewed_at) == ("pending", None, None)
    assert written == []

    with _as_tenant(org):
        res = asyncio.run(approve(row_id, PERSON))
    assert res["ok"] is True and res["status"] == "applied"
    stored = _row(p, row_id)
    assert (stored.status, stored.reviewed_by) == ("applied", PERSON)
    assert len(written) == 1


@_DB_GATE
def test_r8_a_person_can_reject_after_a_marked_reject_is_refused(broker_as_app) -> None:  # noqa: F811
    from action_broker import enqueue, reject

    p, org = broker_as_app, broker_as_app.org_a
    with _as_tenant(org):
        row_id = enqueue(_proposal(ACTION, {}))
        with no_person_scope():
            assert _refused(reject(row_id, "agent"))
    assert _row(p, row_id).status == "pending"
    with _as_tenant(org):
        assert reject(row_id, PERSON)["status"] == "rejected"
    stored = _row(p, row_id)
    assert (stored.status, stored.reviewed_by) == ("rejected", PERSON)


# ── The workflow resume ─────────────────────────────────────────────────────


def _paused_run(p: Any, org: str, kind: str) -> tuple[str, str]:
    """One paused run of *kind*, with its pending approval pause."""
    workflow_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO workflows (id, name, description, owner_email, status, "
            "latest_version, organization_id) VALUES (:id, :n, '', 'owner@a.test', "
            "'published', 1, CAST(:org AS uuid))"),
            {"id": workflow_id, "n": f"wf-{run_id[:8]}", "org": org})
        c.execute(text(
            "INSERT INTO workflow_runs (id, workflow_id, workflow_name, version, "
            "trigger_kind, trigger_payload, status, started_by, organization_id) "
            "VALUES (:id, :w, 'wf', 1, :k, '{}'::jsonb, 'paused', 'server', "
            "CAST(:org AS uuid))"),
            {"id": run_id, "w": workflow_id, "k": kind, "org": org})
        c.execute(text(
            "INSERT INTO workflow_run_pauses (run_id, node_id, snapshot, reason, status, "
            "organization_id) VALUES (:r, 'gate', '{}'::jsonb, 'approval', 'pending', "
            "CAST(:org AS uuid))"),
            {"r": run_id, "org": org})
    return workflow_id, run_id


def _run_state(p: Any, run_id: str) -> tuple[str, str]:
    with p.admin_engine.connect() as c:
        run = c.execute(text("SELECT status FROM workflow_runs WHERE id = CAST(:i AS uuid)"),
                        {"i": run_id}).scalar_one()
        pause = c.execute(text(
            "SELECT status FROM workflow_run_pauses WHERE run_id = CAST(:i AS uuid)"),
            {"i": run_id}).scalar_one()
    return run, pause


async def _drain_runs() -> None:
    tasks = [t for t in asyncio.all_tasks()
             if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
    if tasks:
        await asyncio.wait(tasks, timeout=30)


@_DB_GATE
@pytest.mark.parametrize(("kind", "marked"), [("schedule", True), ("manual", False)])
def test_r8_a_marked_turn_cannot_resume_and_a_person_can(
    broker_as_app, monkeypatch, kind: str, marked: bool,  # noqa: F811
) -> None:
    """The person's resume is legal, through the real approve and the real
    broker handler. The rest of a scheduled run stays marked, from the stored
    kind, so a second gate in that run cannot pass on its own word."""
    from action_broker import approve, enqueue
    from gateway.routes.workflows.broker_handlers import register_handlers

    p, org = broker_as_app, broker_as_app.org_a
    register_handlers()
    _, run_id = _paused_run(p, org, kind)
    with _as_tenant(org):
        row_id = enqueue(_proposal("workflow.resume_run", {"run_id": run_id}))
    turn: dict[str, Any] = {}

    async def _engine(*_a: Any, **_k: Any) -> Any:
        turn["marked"] = no_person_present()
        turn["card"] = await request_confirmation("Next?", non_interactive_default="approve")
        turn["resume"] = await service.resume_run(run_id, organization_id=org)
        return SimpleNamespace(status="succeeded", error=None, state={}, node_results={},
                               outputs=[], paused_nodes=[], wait_until={})

    monkeypatch.setattr(service, "execute_workflow", _engine)

    async def go() -> dict[str, Any]:
        async with tenant_engine_scope(_dsn(p)):
            token = bind_tenant(org)
            try:
                with no_person_scope():
                    said = {
                        "approve": await approve(row_id, "agent"),
                        "resume": await service.resume_run(
                            run_id, resumed_by="agent", organization_id=org),
                    }
                said["state"] = _run_state(p, run_id)
                said["row"] = _row(p, row_id).status
                said["person"] = await approve(row_id, PERSON)
                await _drain_runs()
                return said
            finally:
                release_tenant(token)

    said = asyncio.run(go())
    assert _refused(said["approve"]) and _refused(said["resume"])
    assert said["state"] == ("paused", "pending") and said["row"] == "pending"

    assert said["person"]["ok"] is True and said["person"]["status"] == "applied"
    assert _run_state(p, run_id) == ("succeeded", "resolved")
    assert turn["marked"] is marked
    if marked:
        assert turn["card"] is False and _refused(turn["resume"])
    else:
        # A person watches a manual run, so the refusal is not what stops it.
        assert not _refused(turn["resume"])


# ── The elicitation answer ──────────────────────────────────────────────────


def _prompts(p: Any, run_id: str) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM pm_agent_run_activities "
            "WHERE run_id = CAST(:r AS uuid) AND type = 'prompt'"), {"r": run_id}).scalar_one())


def _is_refusal(answer: Any) -> bool:
    return answer.status_code == 403 and answer.json()["detail"] == {
        "error": NO_PERSON_PRESENT, "message": NO_PERSON_TEXT,
    }


@_DB_GATE
async def test_r8_a_marked_turn_cannot_answer_a_question_and_a_person_can(w) -> None:  # noqa: F811
    """The real route, in process, in the context of the marked turn."""
    async with _session(w.p) as call:
        run = await _start(w, call)
        asked = await _act(call, w.agent_a, run["id"], **_select())
        assert asked.status_code == 201, asked.text
        assert (await _status(call, w.a, run["id"]))[0] == "awaiting"

        with no_person_scope():
            refused = await _act(call, w.a, run["id"], type="prompt", selected=["pg"])
        assert _is_refusal(refused), refused.text
        assert _prompts(w.p, run["id"]) == 0
        assert (await _status(call, w.a, run["id"]))[0] == "awaiting"

        answered = await _act(call, w.a, run["id"], type="prompt", selected=["pg"])
        assert answered.status_code == 201, answered.text
        assert _prompts(w.p, run["id"]) == 1
        assert (await _status(call, w.a, run["id"]))[0] == "in_progress"


@_DB_GATE
async def test_r8_a_marked_turn_cannot_approve_an_archive_and_a_person_can(w) -> None:  # noqa: F811
    """The route archives first, and writes the answer second. So the refusal
    must roll the archive back with it."""

    def _archived() -> Any:
        with w.p.admin_engine.connect() as c:
            return c.execute(text("SELECT archived_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                             {"t": w.task_private}).scalar_one()

    async with _session(w.p) as call:
        asked = await call(w.agent_a, "POST", f"/projects/tasks/{w.task_private}/archive-request",
                           {"idempotency_key": _key()})
        assert asked.status_code == 201, asked.text
        run_id = asked.json()["run"]["id"]
        path = f"/projects/agent-runs/{run_id}/archive-approval"

        with no_person_scope():
            refused = await call(w.a, "POST", path,
                                 {"decision": "approve", "idempotency_key": _key()})
        assert _is_refusal(refused), refused.text
        assert _archived() is None, "the refusal did not roll the archive back"
        assert _prompts(w.p, run_id) == 0

        approved = await call(w.a, "POST", path,
                              {"decision": "approve", "idempotency_key": _key()})
        assert approved.status_code == 200, approved.text
        assert approved.json()["archived"] is True
        assert _archived() is not None


# ── The guard of this suite ─────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+tests/unit/test_no_person_present_r8\.py \\?$", workflow, re.M)


