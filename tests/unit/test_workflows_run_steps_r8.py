"""STU-E4b: the steps of a playbook run on a REAL database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 row E4. Code:
``runs.get_run_steps`` and the pure parts in ``human_step.py``. The hermetic
half is ``test_workflows_run_steps.py``.

The world is a dedicated database with the full ladder and the four
``generated/`` phases, as a role that is NOT a superuser, NOT the owner and has
NO BYPASSRLS. Two orgs, each with its own members and workflows. Each run is a
REAL run of ``service.start_run``, and each settle is a REAL scan. Each read
goes through the REAL Workflows router, with the org of the caller bound as
the auth layer binds it.

**The fences, by name.**

* R1. A member of org A who asks for a run of org B gets the 404 of
  ``get_run``, and the member of B reads it: ``test_r1_*``.
* R2. A pause row and a task of org B never reach the answer of org A, even
  when a row of B names the run of A: ``test_r2_*``.
* R3. Three steps read done, pending and not_reached. A private task of
  another member shows no task id to a reader who is not its person:
  ``test_r3_*``.
* R4. The state word equals the reading of ``human_step.task_outcome`` for a
  done, archived, trashed, cancelled and deleted task, before and after the
  scan settles the run: ``test_r4_*``.
* R5. A run with no human step answers an empty list, and a refused hold
  reads ``cancelled``: ``test_r5_*``.

**Mutations this suite catches** (each was run on 2026-11-13, see the PR):

* M1. ``runs._one_step``: drop ``mine and`` from the ``task_id`` line. R3 red
  (the person of s1 gets the id of the task of s2).
* M2. ``runs.get_run_steps``: bind the session to the org of the run that the
  id names, found by a probe of each org, and not to the org of the caller.
  R1 red: a member of A reads the run of B, with its title. R2 stays green,
  because each R2 run is a run of A, and M2 binds A for it too.
* M2a. ``runs.get_run_steps``: read with the unbound ``_get_db()`` session.
  R1 red: the person of B gets 404, because the read fails closed.
* U4 of the hermetic half: drop the UUID check. The bad id case of R1 is red,
  because the database refuses the cast and the route answers 500.

R2 has no mutation of its own here. It holds the RLS of the pause read and the
task read: a row of B that names a run of A stays out of each answer.

Run (a PRIVATE database, one R8 suite at a time, because the fixture drops
``<db>_runsteps``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_workflows_run_steps_r8.py -v -rs

Without the database each test SKIPS. A skip is not a pass.
"""
from __future__ import annotations

import itertools
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.routes.workflows import human_step, unattended
from gateway.routes.workflows import router as workflows_router
from gateway.routes.workflows.engine.graph import compile_graph
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit.test_auto_park import (
    _drop_database,
    _node,
    _seed_member,
    _seed_workflow,
)
from tests.unit.test_workflows_human_step_r8 import (
    _archive,
    _on_app_role,
    _one,
    _paused,
    _pauses,
    _run,
    _scan,
    _stage,
    _start,
    _tasks,
    _trash,
)

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL is not set. R8 needs a REAL Postgres with "
        "pgvector. A skip here is not a pass, and CI must set it. Do NOT export "
        "it as DATABASE_URL."
    ),
)

pytestmark = _DB_GATE

_APP_ROLE = "acb_app_runsteps"
_APP_PW = "runsteps_scratch_pw"
_SUFFIX = "_runsteps"

OWNER_A = "owner@a.runsteps.test"
LENA_A = "lena@a.runsteps.test"
RAVI_A = "ravi@a.runsteps.test"
OWNER_B = "owner@b.runsteps.test"
LENA_B = "lena@b.runsteps.test"

SECRET_B = "A step that only org B knows"


def _chain(steps: list[tuple[str, str, str]]) -> dict:
    """start -> each human step -> done."""
    nodes = [_node("start", "trigger", config={"kind": "manual"})]
    for nid, who, title in steps:
        nodes.append(_node(nid, "human_step", title, {"assignee": who, "title": title}))
    nodes.append(_node("done", "output", config={"value": "end"}))
    ids = [n["id"] for n in nodes]
    edges = [{"id": f"{a}-{b}", "source": a, "target": b} for a, b in itertools.pairwise(ids)]
    return compile_graph({"nodes": nodes, "edges": edges})


def _grant_app_role(ded_admin_url: Any, dedicated: str) -> None:
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
        for oid, slug in ((ns.org_a, "runsteps-a"), (ns.org_b, "runsteps-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                {"id": oid, "s": slug})
        for email in (OWNER_A, LENA_A, RAVI_A):
            _seed_member(conn, ns.org_a, email)
        for email in (OWNER_B, LENA_B):
            _seed_member(conn, ns.org_b, email)
        for name, org, owner, steps in (
            ("Playbook in A", ns.org_a, OWNER_A, [
                ("s1", LENA_A, "Sign the contract"),
                ("s2", OWNER_A, "Check the bank"),
                ("s3", LENA_A, "Send the welcome")]),
            ("Step in A", ns.org_a, OWNER_A, [("step", LENA_A, "Check the contract")]),
            ("Step for a member of B", ns.org_a, OWNER_A, [("step", LENA_B, "Wrong org")]),
            ("No step in A", ns.org_a, OWNER_A, []),
            ("Step in B", ns.org_b, OWNER_B, [("step", LENA_B, SECRET_B)]),
        ):
            serialized = _chain(steps)
            ns.wf[name] = (_seed_workflow(conn, org, owner, name, serialized), org, serialized)
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def w(world, monkeypatch):
    """The unattended list holds A and B. No run or task is left from the last test."""
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    world.listed = f"{world.org_a},{world.org_b}"
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=world.listed))
    with world.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflow_runs"))
        c.execute(text("DELETE FROM pm_tasks WHERE source = 'automation'"))
    return world


def _get(w: Any, who: str, org: str, path: str) -> httpx.Response:
    """One GET through the REAL Workflows router, as *who* of *org*. The
    dependency binds the org, as the auth layer does, and the session of the
    route takes the org from that binding only."""

    async def body() -> httpx.Response:
        app = FastAPI()
        app.include_router(workflows_router)

        async def _caller(request: Request) -> UserContext:
            bind_tenant(org)
            return UserContext(email=who, role=UserRole.EMPLOYEE, organization_id=org,
                               access=build_access(["feature:workflows"]))

        app.dependency_overrides[get_current_user] = _caller
        token = bind_tenant(org)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://workflows.test") as client:
                return await client.get(path)
        finally:
            release_tenant(token)

    return _on_app_role(w, body)


def _steps_r(w: Any, who: str, org: str, run_id: str) -> httpx.Response:
    return _get(w, who, org, f"/workflows/runs/{run_id}/steps")


def _steps(w: Any, who: str, org: str, run_id: str) -> list[dict[str, Any]]:
    r = _steps_r(w, who, org, run_id)
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == run_id
    return r.json()["steps"]


def _task_of(w: Any, run_id: str, node: str) -> Any:
    found = [t for t in _tasks(w, run_id) if t.origin.get("node_id") == node]
    assert len(found) == 1, found
    return found[0]


# ── R1: another org gets the 404 of get_run ─────────────────────────────────


def test_r1_a_member_of_org_a_gets_404_for_a_run_of_org_b(w) -> None:
    run_b, task_b = _paused(w, "Step in B")
    # Non-vacuous: the person of org B reads the run and the id of her task.
    mine = _steps(w, LENA_B, w.org_b, run_b)
    assert [(s["title"], s["state"], s["task_id"]) for s in mine] == [
        (SECRET_B, "pending", str(task_b.id))]
    for who in (OWNER_A, LENA_A, RAVI_A):
        r = _steps_r(w, who, w.org_a, run_b)
        assert (r.status_code, r.json()) == (404, {"detail": "Run not found"}), (who, r.text)
        assert str(task_b.id) not in r.text and SECRET_B not in r.text
    # The detail route of the run gives the same 404 for the same id.
    detail = _get(w, OWNER_A, w.org_a, f"/workflows/runs/{run_b}")
    assert (detail.status_code, detail.json()) == (404, {"detail": "Run not found"})


def test_r1_an_unknown_and_a_bad_id_get_the_same_404(w) -> None:
    for run_id in (str(uuid.uuid4()), "not-a-uuid"):
        r = _steps_r(w, OWNER_A, w.org_a, run_id)
        assert (r.status_code, r.json()) == (404, {"detail": "Run not found"})


# ── R2: a row of org B never reaches the answer of org A ────────────────────


def test_r2_a_pause_row_of_org_b_that_names_a_run_of_a_never_shows(w) -> None:
    """A row of B is newer than the pause of A and names the run of A. Under
    RLS the session of A does not see it, so the step keeps the task of A."""
    run_a, task_a = _paused(w, "Step in A")
    run_b, task_b = _paused(w, "Step in B")
    with w.admin_engine.begin() as c:
        # No trigger may move the row into org A: the planted row stays a row of B.
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(
            "INSERT INTO workflow_run_pauses (run_id, node_id, snapshot, reason, status, "
            "organization_id, created_at) VALUES (CAST(:r AS uuid), 'step', "
            "CAST(:s AS jsonb), 'task', 'pending', CAST(:o AS uuid), "
            "now() + interval '1 minute')"),
            {"r": run_a, "o": w.org_b,
             "s": json.dumps({"task_id": str(task_b.id), "assignee": LENA_A})})
    planted = _one(w, "SELECT 1 FROM workflow_run_pauses WHERE run_id = CAST(:r AS uuid) "
                      "AND organization_id = CAST(:o AS uuid)", r=run_a, o=w.org_b)
    assert planted is not None, "the planted row carries org B"
    r = _steps_r(w, LENA_A, w.org_a, run_a)
    assert r.status_code == 200
    assert r.json()["steps"] == [{
        "node_id": "step", "title": "Check the contract", "assignee": LENA_A,
        "state": "pending", "task_id": str(task_a.id)}]
    assert str(task_b.id) not in r.text and SECRET_B not in r.text
    assert run_b not in r.text


def test_r2_a_task_of_org_b_named_by_a_pause_of_a_never_shows(w) -> None:
    """The pause of A names a task of B, and that task carries the origin of
    the run of A (as case S4 of the human step suite). The session of A does
    not see the task, so its person gets no id, and the step reads as gone."""
    run_a, _ = _paused(w, "Step in A")
    _, task_b = _paused(w, "Step in B")
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE pm_tasks SET origin = jsonb_set(jsonb_set(origin, '{run_id}', "
            "to_jsonb(CAST(:r AS text))), '{node_id}', '\"step\"') WHERE id = :t"),
            {"r": run_a, "t": task_b.id})
        c.execute(text(
            "UPDATE workflow_run_pauses SET snapshot = jsonb_set(snapshot, '{task_id}', "
            "to_jsonb(CAST(:t AS text))) WHERE run_id = CAST(:r AS uuid)"),
            {"t": str(task_b.id), "r": run_a})
    r = _steps_r(w, LENA_A, w.org_a, run_a)
    assert r.status_code == 200
    assert r.json()["steps"] == [{
        "node_id": "step", "title": "Check the contract", "assignee": LENA_A,
        "state": "cancelled", "task_id": None}]
    assert str(task_b.id) not in r.text


# ── R3: three steps, and a private task of another member ───────────────────


def test_r3_three_steps_and_the_task_id_goes_only_to_its_person(w) -> None:
    run_id, t1 = _paused(w, "Playbook in A")
    _stage(w, str(t1.id), "done")
    assert _scan(w) == 1
    # The run went past s1 and now waits on s2, the task of the owner.
    assert _run(w, run_id).status == "paused"
    t2 = _task_of(w, run_id, "s2")
    assert [(p.status, p.reason) for p in _pauses(w, run_id)] == [
        ("resolved", "task"), ("pending", "task")]

    lena = _steps(w, LENA_A, w.org_a, run_id)
    owner = _steps(w, OWNER_A, w.org_a, run_id)
    ravi_r = _steps_r(w, RAVI_A, w.org_a, run_id)
    ravi = ravi_r.json()["steps"]
    want = [
        ("s1", "Sign the contract", LENA_A, "done"),
        ("s2", "Check the bank", OWNER_A, "pending"),
        ("s3", "Send the welcome", LENA_A, "not_reached"),
    ]
    for steps in (lena, owner, ravi):
        assert [(s["node_id"], s["title"], s["assignee"], s["state"]) for s in steps] == want
        assert all(set(s) == {"node_id", "title", "assignee", "state", "task_id"}
                   for s in steps)
    assert [s["task_id"] for s in lena] == [str(t1.id), None, None]
    assert [s["task_id"] for s in owner] == [None, str(t2.id), None]
    # A member who may read the run and is no person of a task gets no id.
    assert [s["task_id"] for s in ravi] == [None, None, None]
    assert str(t1.id) not in ravi_r.text and str(t2.id) not in ravi_r.text


# ── R4: the state word is the reading of task_outcome ───────────────────────


def _change(w: Any, task_id: Any, change: str) -> None:
    if change == "done":
        _stage(w, str(task_id), "done")
    elif change == "done_archived":
        _stage(w, str(task_id), "done")
        _archive(w, task_id)
    elif change == "archived":
        _archive(w, task_id)
    elif change == "trashed":
        _trash(w, task_id, LENA_A)
    elif change == "cancelled":
        _stage(w, str(task_id), "cancelled")
    elif change == "deleted":
        with w.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_tasks WHERE id = :t"), {"t": task_id})
    else:
        assert change == "open"


def _outcome(w: Any, task_id: str, run_id: str) -> str:
    """The reading of the scan, in a session of org A."""
    from gateway.db import tenant_session

    async def body() -> str:
        async with tenant_session(w.org_a) as db:
            row = await human_step.read_task(db, task_id, run_id, "step", LENA_A, w.org_a)
        return human_step.task_outcome(row)

    return _on_app_role(w, body)


_WORD = {human_step.RESUME: "done", human_step.WAIT: "pending"}


@pytest.mark.parametrize("change,word", [
    ("open", "pending"),
    ("done", "done"),
    ("done_archived", "done"),
    ("archived", "cancelled"),
    ("trashed", "cancelled"),
    ("cancelled", "cancelled"),
    ("deleted", "cancelled"),
])
def test_r4_the_state_word_is_the_reading_of_task_outcome(w, change: str, word: str) -> None:
    run_id, task = _paused(w, "Step in A")
    _change(w, task.id, change)
    step = _steps(w, LENA_A, w.org_a, run_id)[0]
    assert step["state"] == _WORD.get(_outcome(w, str(task.id), run_id), "cancelled") == word
    assert step["task_id"] == (None if change == "deleted" else str(task.id))
    if word == "pending":
        return
    # The scan settles the run on the same reading, and the word stays.
    assert _scan(w) == 1
    assert _run(w, run_id).status == ("succeeded" if word == "done" else "failed")
    again = _steps(w, LENA_A, w.org_a, run_id)[0]
    assert again["state"] == word
    assert again["task_id"] == step["task_id"]


# ── R5: no human step, and a refused hold ───────────────────────────────────


def test_r5_a_run_with_no_human_step_answers_an_empty_list(w) -> None:
    run_id = _start(w, "No step in A")
    assert _run(w, run_id).status == "succeeded"
    assert _pauses(w, run_id) == []
    assert _steps(w, OWNER_A, w.org_a, run_id) == []


def test_r5_a_refused_hold_reads_cancelled_with_no_task(w) -> None:
    """The person is a member of another org, so the hold makes no task and
    ends the run. With no pause, the graph comes from the published version."""
    run_id = _start(w, "Step for a member of B")
    assert _run(w, run_id).status == "failed"
    assert _pauses(w, run_id) == [] and _tasks(w, run_id) == []
    assert _steps(w, OWNER_A, w.org_a, run_id) == [{
        "node_id": "step", "title": "Wrong org", "assignee": LENA_B,
        "state": "cancelled", "task_id": None}]
