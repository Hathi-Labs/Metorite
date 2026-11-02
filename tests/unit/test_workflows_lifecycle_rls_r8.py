"""H-328 part (a) under FORCE RLS: the node services bind the run's org.

Spec: ``project-docs/specs/workflows_app.md`` (the H4 slice 6c note) · board
R5 and R8 · HANDOFF H-328.

**The defect this file fences.** A run that carried an organization still ran
its nodes unbound. The module read and the ``pm_task`` write opened ``get_db()``,
and the sweep and the ``notify.whatsapp`` owner read took their tenant from a
resolver that joins ``workflows`` on an unbound session. ``workflows``,
``workflow_modules`` and ``pm_tasks`` have FORCE ROW LEVEL SECURITY, and
production connects as a NOBYPASSRLS role. So each of those nodes failed.

**What this file proves, as the NON-privileged role.** It builds its own
database with the full ladder and the four ``generated/`` phases. The shared
engine connects as a role that is not a superuser, not the owner and has no
BYPASSRLS. Nothing here patches ``_get_db`` or ``tenant_session``.

* a module node and a Projects node run to the end in the run's own org;
* org B's run never reads org A's workflow, module or task;
* the ambient tenant never chooses the org of a node;
* a run with no org keeps the old path, and inherits nothing;
* an agent reaches no Projects node, at the start or through a resume.

**The Projects nodes enter at ``start_run``.** ``run_published_workflow`` refuses
them (:func:`service._member_blind_write_nodes`), because a run writes a task by
primary key with no member check. So the cases that prove the binding of those
nodes go one step below that refusal. The refusal has its own cases, and they
use the real ``run_workflow`` tool.

Mutations this suite catches (R7):

* the module read back on ``_get_db()``: the module node cases go red;
* the task write back on ``_get_db()``: the task node cases go red;
* the sweep or the owner read back on the resolver: its node case goes red;
* ``_execute_run`` passes no org: every node case goes red;
* a session with no explicit org: the ambient cases go red;
* the refusal in ``run_published_workflow`` removed: the agent cases go red,
  and the failure shows the changed task row;
* the refusal in ``resume_run`` removed: the approval cases go red, and the
  failure shows the changed task row;
* ``_execute_run`` falls back to ``current_tenant()``: the ambient case of
  section 5 goes red;
* the org read from the trigger payload: the payload case goes red;
* ``resume_run`` passes the caller's org: the row org case goes red.

⚠️ **R8.** The subject is a policy. A Python fake agrees with whatever SQL it
is handed. The gate is ``TENANT_LADDER_DATABASE_URL``, never ``DATABASE_URL``.
A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_workflows_lifecycle_rls_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.workflows import service
from gateway.routes.workflows.engine import handlers
from gateway.routes.workflows.engine.graph import NODE_TYPES, compile_graph
from gateway.routes.workflows.engine.handlers import NodeExecutionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _ROOT / "infra" / "postgres" / "generated"

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. Do NOT export "
        "it as DATABASE_URL."
    ),
)

# The module fixture needs the promoted catalog. A module gate keeps it from
# running (and erroring) where no ladder database exists.
pytestmark = _DB_GATE

#: Suite-private role name, canonical privileges. A role is cluster-wide, so a
#: shared name would clobber another suite's password.
_APP_ROLE = "acb_app_wflife"
_APP_PW = "wflife_scratch_pw"

_OWNERS = {
    "A": "owner@a.wflife.test",
    "B": "owner@b.wflife.test",
    "C": "owner@c.wflife.test",
}


def _module_source(tag: str) -> str:
    return f"def run(inputs):\n    return {{'marker': 'module-{tag}'}}\n"


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": nid, "config": config or {}}}


def _one_step(ntype: str, config: dict, *, gate: bool = False) -> dict:
    """trigger -> [approval ->] step -> output. The step is under test."""
    nodes = [_node("start", "trigger", {"kind": "manual"})]
    edges = []
    prev = "start"
    if gate:
        nodes.append(_node("gate", "approval", {}))
        edges.append({"id": "start->gate", "source": "start", "target": "gate"})
        prev = "gate"
    nodes.append(_node("step", ntype, config))
    edges.append({"id": f"{prev}->step", "source": prev, "target": "step"})
    nodes.append(_node("done", "output", {"value": "end"}))
    edges.append({"id": "step->done", "source": "step", "target": "done"})
    return compile_graph({"nodes": nodes, "edges": edges})


def _write_then_gate(write: dict, after: dict) -> dict:
    """trigger -> task -> approval -> module -> output."""
    nodes = [
        _node("start", "trigger", {"kind": "manual"}),
        _node("write", "pm_task", write),
        _node("gate", "approval", {}),
        _node("step", "module", after),
        _node("done", "output", {"value": "end"}),
    ]
    edges = [
        {"id": f"{a}->{b}", "source": a, "target": b}
        for a, b in (("start", "write"), ("write", "gate"),
                     ("gate", "step"), ("step", "done"))
    ]
    return compile_graph({"nodes": nodes, "edges": edges})


def _exec_sql_file(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


def _seed_status(conn, project: str, org: str, name: str, position: int, category: str) -> str:
    return str(conn.execute(text(
        "INSERT INTO pm_task_statuses (project_id, name, color, position, "
        "category, organization_id) VALUES (CAST(:p AS uuid), :n, 'gray', "
        ":pos, :cat, CAST(:o AS uuid)) RETURNING id"),
        {"p": project, "n": name, "pos": position, "cat": category, "o": org},
    ).scalar_one())


def _seed_task(conn, project: str, status: str, org: str, title: str, *, days_old: int = 0) -> str:
    return str(conn.execute(text(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id, "
        "created_by, organization_id, task_number, updated_at) "
        "SELECT :t, CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), "
        "'seed@wflife.test', CAST(:o AS uuid), COALESCE(MAX(task_number), 0) + 1, "
        "now() - make_interval(days => :age) FROM pm_tasks "
        "WHERE root_project_id = CAST(:p AS uuid) RETURNING id"),
        {"t": title, "p": project, "s": status, "o": org, "age": days_old},
    ).scalar_one())


def _seed_org_work(conn, org: str, tag: str) -> dict[str, str]:
    """One root project with a sweep policy, one module, and three tasks."""
    project = str(conn.execute(text(
        "INSERT INTO pm_projects (name, status, source, created_by, "
        "organization_id, timezone, owns_statuses, archive_after_months) "
        "VALUES (:n, 'active', 'manual', 'seed@wflife.test', CAST(:o AS uuid), "
        "'UTC', true, 1) RETURNING id"), {"n": f"Delivery {tag}", "o": org},
    ).scalar_one())
    todo = _seed_status(conn, project, org, "To do", 0, "todo")
    done = _seed_status(conn, project, org, "Done", 1, "done")
    module = str(conn.execute(text(
        "INSERT INTO workflow_modules (name, code, status, created_by, "
        "organization_id) VALUES (:n, :c, 'ready', 'seed', CAST(:o AS uuid)) "
        "RETURNING id"),
        {"n": f"module {tag}", "c": _module_source(tag), "o": org},
    ).scalar_one())
    return {
        "project": project,
        "module": module,
        "task": _seed_task(conn, project, todo, org, f"Open in {tag}"),
        "stale": _seed_task(conn, project, done, org, f"Stale in {tag}", days_old=90),
    }


def _seed_workflow(conn, org: str, tag: str, name: str, serialized: dict) -> str:
    wid = str(uuid.uuid4())
    conn.execute(text(
        "INSERT INTO workflows (id, name, description, owner_email, status, "
        "latest_version, organization_id) VALUES (:id, :n, '', :o, 'published', "
        "1, :org)"), {"id": wid, "n": name, "o": _OWNERS[tag], "org": org})
    conn.execute(text(
        "INSERT INTO workflow_versions (workflow_id, version, serialized, "
        "published_by, organization_id) VALUES (:w, 1, CAST(:s AS jsonb), "
        "'seed', :org)"), {"w": wid, "s": json.dumps(serialized), "org": org})
    return wid


def _grant_app_role(ded_admin_url, dedicated: str) -> None:
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
def promoted():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role, two orgs' work."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_wflife"
    _drop_database(admin_url, dedicated)
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    maint.dispose()

    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn:
            _exec_sql_file(conn, (_GENERATED / phase).read_text(encoding="utf-8"))
    _grant_app_role(ded_admin_url, dedicated)

    org_a, org_b, org_c = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    orgs = {"A": org_a, "B": org_b, "C": org_c}
    wf: dict[str, str] = {}
    with eng.begin() as conn:
        for oid, slug in ((org_a, "wflife-a"), (org_b, "wflife-b"), (org_c, "wflife-c")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": slug, "name": slug})
        work_a = _seed_org_work(conn, org_a, "A")
        work_b = _seed_org_work(conn, org_b, "B")
        # Org C holds the refusal cases, so no other case moves their rows.
        work_c = _seed_org_work(conn, org_c, "C")
        second_task = _seed_task(conn, work_a["project"], str(conn.execute(text(
            "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": work_a["task"]}).scalar_one()), org_a, "Second open in A")
        before_gate = _seed_task(conn, work_c["project"], str(conn.execute(text(
            "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": work_c["task"]}).scalar_one()), org_c, "Before the gate in C")

        steps: dict[str, tuple[str, str, dict]] = {
            "Module in A": ("A", "module", {"module_id": work_a["module"]}),
            "Module in B": ("B", "module", {"module_id": work_b["module"]}),
            "Task in A": ("A", "pm_task", {
                "task_id": work_a["task"],
                "fields": {"title": "Renamed by the run", "status": "Done"},
            }),
            "Second task in A": ("A", "pm_task", {
                "task_id": second_task, "fields": {"title": "Renamed under B"},
            }),
            "Sweep in A": ("A", "pm_lifecycle", {}),
            "Sweep in B": ("B", "pm_lifecycle", {}),
            "Notify in A": ("A", "tool", {
                "action": "notify.whatsapp", "args": {"title": "x"},
            }),
            "Reach A module from B": ("B", "module", {"module_id": work_a["module"]}),
            "Reach A task from B": ("B", "pm_task", {
                "task_id": work_a["stale"], "fields": {"title": "Stolen by B"},
            }),
            "Module in C": ("C", "module", {"module_id": work_c["module"]}),
            "Picked task in C": ("C", "pm_task", {
                "task_id": "{{trigger.task_id}}",
                "fields": {"title": "Changed by an agent"},
            }),
            "Fixed task in C": ("C", "pm_task", {
                "task_id": work_c["task"],
                "fields": {"title": "Changed by an agent"},
            }),
            "Sweep in C": ("C", "pm_lifecycle", {}),
        }
        for name, (tag, ntype, config) in steps.items():
            wf[name] = _seed_workflow(conn, orgs[tag], tag, name, _one_step(ntype, config))
        gated: dict[str, tuple[str, dict]] = {
            "Gated module in A": ("A", _one_step(
                "module", {"module_id": work_a["module"]}, gate=True)),
            "Gated task in C": ("C", _one_step("pm_task", {
                "task_id": work_c["task"],
                "fields": {"title": "Changed after the gate"},
            }, gate=True)),
            "Gated sweep in C": ("C", _one_step("pm_lifecycle", {}, gate=True)),
            "Task before the gate in C": ("C", _write_then_gate(
                {"task_id": before_gate, "fields": {"title": "Written before the gate"}},
                {"module_id": work_c["module"]},
            )),
        }
        for name, (tag, serialized) in gated.items():
            wf[name] = _seed_workflow(conn, orgs[tag], tag, name, serialized)

    class _NS:
        pass

    ns = _NS()
    ns.admin_engine = eng
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    ns.org_a, ns.org_b, ns.org_c = org_a, org_b, org_c
    ns.work_a, ns.work_b, ns.work_c = work_a, work_b, work_c
    ns.second_task = second_task
    ns.before_gate = before_gate
    ns.wf = wf
    yield ns

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture(autouse=True)
def _module_child(monkeypatch):
    """The module runner starts a POSIX child (``preexec_fn``, ``resource``).

    Windows has neither, so only there a stand-in runs the seeded source in
    this process. The subject of this file is the READ under RLS. CI runs the
    real child, and ``test_workflows_modules.py`` fences that child.
    """
    if sys.platform != "win32":
        return

    async def _run(code: str, inputs: dict) -> dict:
        namespace: dict[str, Any] = {}
        exec(code, namespace)
        return namespace["run"](inputs)

    monkeypatch.setattr(handlers, "run_module_code", _run)


@pytest.fixture(autouse=True)
def _no_proposal_store(monkeypatch):
    """The approval gate files a proposal. The broker's own R8 suite owns
    that write, so this file records nothing there."""
    import action_broker.broker as broker

    async def _submit(proposal):
        return {"action_id": "pa-1"}

    monkeypatch.setattr(broker, "submit", _submit)


async def _drain_runs() -> None:
    """Let every run task of this loop finish before the engine goes."""
    tasks = [t for t in asyncio.all_tasks()
             if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
    if tasks:
        await asyncio.wait(tasks, timeout=30)


def _on_app_role(promoted, ambient: str | None, body):
    """Run *body* on the app-role engine, with *ambient* as the bound tenant.

    ``None`` is what a scheduled run has: no tenant in the context at all."""

    async def _go():
        async with tenant_engine_scope(promoted.app_dsn):
            token = bind_tenant(ambient) if ambient else None
            try:
                return await body()
            finally:
                await _drain_runs()
                if token is not None:
                    release_tenant(token)

    return asyncio.run(_go())


def _serialized_of(promoted, name: str) -> dict:
    with promoted.admin_engine.connect() as c:
        serialized = c.execute(text(
            "SELECT serialized FROM workflow_versions "
            "WHERE workflow_id = CAST(:w AS uuid)"),
            {"w": promoted.wf[name]}).scalar_one()
    return json.loads(serialized) if isinstance(serialized, str) else serialized


def _run_row(promoted, run_id: str):
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, error, node_results, "
            "CAST(organization_id AS text) AS org "
            "FROM workflow_runs WHERE id = CAST(:r AS uuid)"), {"r": run_id}).fetchone()


def _run_count(promoted, name: str) -> int:
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT count(*) FROM workflow_runs WHERE workflow_id = CAST(:w AS uuid)"),
            {"w": promoted.wf[name]}).scalar_one()


def _pause_statuses(promoted, run_id: str) -> list[str]:
    with promoted.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT status FROM workflow_run_pauses WHERE run_id = CAST(:r AS uuid)"),
            {"r": run_id}).scalars())


def _step_result(row) -> dict:
    results = row.node_results
    results = json.loads(results) if isinstance(results, str) else results
    return results["step"]


def _run_published(
    promoted, name: str, org: str, *, ambient: str | None = None, payload: dict | None = None,
):
    """Start one published workflow in *org* and wait for its end.

    The context binds *ambient*, so by default no tenant is in the context.
    Only the run carries the org, as a scheduled run will."""

    async def body():
        return await service.run_published_workflow(
            name, payload or {}, started_by="h328", organization_id=org)

    started = _on_app_role(promoted, ambient, body)
    assert "run_id" in started, started
    return _run_row(promoted, started["run_id"])


def _start_at_the_seam(promoted, name: str, org: str, *, ambient: str | None = None) -> str:
    """Start one workflow at ``start_run``, under the agent door's refusal.

    Nothing live starts a Projects node with an org. This call proves what the
    node does once a run reaches it, and it builds a run that paused before
    the refusal existed."""
    serialized = _serialized_of(promoted, name)

    async def body():
        return await service.start_run(
            workflow_id=promoted.wf[name],
            workflow_name=name,
            version=1,
            serialized=serialized,
            trigger_kind="api",
            trigger_payload={},
            started_by="h328",
            organization_id=org,
        )

    return _on_app_role(promoted, ambient, body)


def _run_at_the_seam(promoted, name: str, org: str, *, ambient: str | None = None):
    return _run_row(promoted, _start_at_the_seam(promoted, name, org, ambient=ambient))


def _task_row(promoted, task_id: str):
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT t.title, s.name AS status, t.archived_at "
            "FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
            "WHERE t.id = CAST(:t AS uuid)"), {"t": task_id}).fetchone()


def _activities(promoted, task_id: str) -> list:
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT created_by, CAST(organization_id AS text) AS org "
            "FROM pm_activities WHERE task_id = CAST(:t AS uuid)"),
            {"t": task_id}).fetchall()


# ── 0. The catalog really is constrained ─────────────────────────────────────


def test_an_unbound_read_finds_no_module_task_or_workflow(promoted):
    """Precondition: the role cannot see past RLS without a tenant."""
    from gateway.routes.workflows.core import _get_db

    async def body():
        db = await _get_db()
        try:
            return [
                (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
                for table in ("workflow_modules", "pm_tasks", "workflows")
            ]
        finally:
            await db.close()

    assert _on_app_role(promoted, None, body) == [0, 0, 0]


# ── 1. The module node ───────────────────────────────────────────────────────


def test_a_module_node_runs_to_the_end_in_the_runs_org(promoted):
    row = _run_published(promoted, "Module in A", promoted.org_a)
    assert (row.status, row.error, row.org) == ("succeeded", None, promoted.org_a)
    assert _step_result(row)["output"] == {"marker": "module-A"}

    row = _run_published(promoted, "Module in B", promoted.org_b)
    assert (row.status, row.org) == ("succeeded", promoted.org_b)
    assert _step_result(row)["output"] == {"marker": "module-B"}


def test_a_run_of_another_org_never_reads_the_module(promoted):
    row = _run_published(promoted, "Reach A module from B", promoted.org_b)
    assert row.status == "failed"
    step = _step_result(row)
    assert step["status"] == "error" and "module not found" in step["error"]
    assert "module-A" not in json.dumps(step)


def test_the_module_read_returns_nothing_across_orgs(promoted):
    async def read_as(org: str) -> str | None:
        return await service.build_node_services(
            "t", promoted.wf["Module in A"], org,
        ).get_module_code(promoted.work_a["module"])

    async def body():
        return await read_as(promoted.org_a), await read_as(promoted.org_b)

    own, other = _on_app_role(promoted, None, body)
    assert own == _module_source("A")
    assert other is None


def test_the_ambient_tenant_never_chooses_the_nodes_org(promoted):
    """Org B is bound in the context and the run is org A's. The run reads
    A's module, and a read of the ambient tenant would find nothing."""
    row = _run_published(promoted, "Module in A", promoted.org_a, ambient=promoted.org_b)
    assert (row.status, row.org) == ("succeeded", promoted.org_a)
    assert _step_result(row)["output"] == {"marker": "module-A"}


def test_a_resumed_run_binds_its_org_for_the_nodes_after_the_gate(promoted):
    """The second door that carries an org: an approval resumes the run in
    the approver's org, and the node after the gate reads in that org."""

    async def start():
        return await service.run_published_workflow(
            "Gated module in A", {}, started_by="h328", organization_id=promoted.org_a)

    run_id = _on_app_role(promoted, None, start)["run_id"]
    assert _run_row(promoted, run_id).status == "paused"

    async def approve():
        return await service.resume_run(run_id, organization_id=promoted.org_a)

    assert _on_app_role(promoted, None, approve)["ok"] is True
    row = _run_row(promoted, run_id)
    assert (row.status, row.org) == ("succeeded", promoted.org_a)
    assert _step_result(row)["output"] == {"marker": "module-A"}


# ── 2. The Projects task node ────────────────────────────────────────────────


def test_a_task_node_writes_and_commits_in_the_runs_org(promoted):
    row = _run_at_the_seam(promoted, "Task in A", promoted.org_a)
    assert (row.status, row.error, row.org) == ("succeeded", None, promoted.org_a)
    output = _step_result(row)["output"]
    assert output["changed"] == ["title", "status"] and output["status"] == "Done"

    task = _task_row(promoted, promoted.work_a["task"])
    assert (task.title, task.status) == ("Renamed by the run", "Done")
    actor = f"system:workflow:{promoted.wf['Task in A']}"
    rows = _activities(promoted, promoted.work_a["task"])
    assert rows and {(a.created_by, a.org) for a in rows} == {(actor, promoted.org_a)}


def test_a_run_of_another_org_never_reads_the_task(promoted):
    target = promoted.work_a["stale"]
    before = _task_row(promoted, target), _activities(promoted, target)
    row = _run_at_the_seam(promoted, "Reach A task from B", promoted.org_b)
    assert row.status == "failed"
    step = _step_result(row)
    assert step["status"] == "error" and "Task not found" in step["error"]
    assert (_task_row(promoted, target), _activities(promoted, target)) == before


def test_the_task_write_ignores_the_ambient_tenant(promoted):
    row = _run_at_the_seam(promoted, "Second task in A", promoted.org_a, ambient=promoted.org_b)
    assert (row.status, row.org) == ("succeeded", promoted.org_a)
    assert _task_row(promoted, promoted.second_task).title == "Renamed under B"


# ── 3. The lifecycle sweep node ──────────────────────────────────────────────


def test_a_sweep_node_sweeps_only_the_runs_org(promoted):
    row = _run_at_the_seam(promoted, "Sweep in A", promoted.org_a)
    assert (row.status, row.error, row.org) == ("succeeded", None, promoted.org_a)
    assert _step_result(row)["output"]["archived"] == 1
    assert _task_row(promoted, promoted.work_a["stale"]).archived_at is not None
    assert _task_row(promoted, promoted.work_b["stale"]).archived_at is None

    # The mirror: a sweep that matched nothing would pass the line above.
    row = _run_at_the_seam(promoted, "Sweep in B", promoted.org_b)
    assert row.status == "succeeded"
    assert _task_row(promoted, promoted.work_b["stale"]).archived_at is not None


# ── 4. The notify.whatsapp owner read ────────────────────────────────────────


def test_a_notify_node_reads_the_owner_in_the_runs_org(promoted, monkeypatch):
    told: list[tuple] = []

    async def _notify(org, owner, workflow_id, args):
        told.append((org, owner, workflow_id))
        return {"recorded": True}

    monkeypatch.setattr(service, "notify_owner_whatsapp", _notify)
    row = _run_published(promoted, "Notify in A", promoted.org_a)
    assert (row.status, row.error) == ("succeeded", None)
    assert told == [(promoted.org_a, _OWNERS["A"], promoted.wf["Notify in A"])]


def test_the_owner_of_another_orgs_workflow_is_never_read(promoted):
    async def body():
        return await service._workflow_owner(promoted.wf["Notify in A"], promoted.org_b)

    with pytest.raises(NodeExecutionError, match="no owner"):
        _on_app_role(promoted, None, body)


# ── 5. A run with no org keeps today's path, and inherits nothing ────────────


def test_a_run_with_no_org_keeps_the_old_path_and_inherits_nothing(promoted):
    """Org A is bound in the context, and the run carries no org. The services
    stay unbound, as they were. So they read nothing and write nothing. A later
    part of H-328 gives these runs an org. Until then a member's tenant in the
    context must not leak into them."""

    async def body():
        services = service.build_node_services("t", promoted.wf["Task in A"], None)
        code = await services.get_module_code(promoted.work_a["module"])
        with pytest.raises(HTTPException, match="Task not found"):
            await services.update_task(promoted.work_a["task"], {"title": "Unbound"})
        with pytest.raises(NodeExecutionError, match="no resolvable organization"):
            await services.run_lifecycle_sweep()
        with pytest.raises(NodeExecutionError, match="no resolvable organization"):
            await services.run_tool("notify.whatsapp", {"title": "x"}, "t")
        return code

    assert _on_app_role(promoted, promoted.org_a, body) is None
    assert _task_row(promoted, promoted.work_a["task"]).title != "Unbound"


def test_execute_run_never_gives_a_run_the_ambient_tenant(promoted):
    """The case above calls the seam. This one is the line in ``_execute_run``
    that feeds it. Org A is bound in the context and the run has no org, so
    org A's module stays out of reach."""
    run_id = str(uuid.uuid4())
    serialized = _serialized_of(promoted, "Module in A")

    async def body():
        service._HUBS[run_id] = service.RunHub()
        await service._execute_run(
            run_id=run_id,
            workflow_id=promoted.wf["Module in A"],
            workflow_name="Module in A",
            serialized=serialized,
            trigger_payload={},
            variables={},
            started_by="h328",
            trigger_kind="manual",
        )
        return service._HUBS.pop(run_id).events

    events = _on_app_role(promoted, promoted.org_a, body)
    step = [e["status"] for e in events if e.get("node_id") == "step"]
    assert "error" in step and "ok" not in step, events
    assert "module-A" not in json.dumps(events, default=str)


def test_a_payload_never_chooses_the_org_of_a_run(promoted):
    """The payload is the agent's input, and each key here names org B."""
    claims = dict.fromkeys(
        ("organization_id", "org", "org_id", "tenant_id"), promoted.org_b)
    row = _run_published(promoted, "Module in A", promoted.org_a, payload=claims)
    assert (row.status, row.error, row.org) == ("succeeded", None, promoted.org_a)
    assert _step_result(row)["output"] == {"marker": "module-A"}


# ── 6. An agent reaches no Projects node ─────────────────────────────────────


def _agent_run(promoted, name: str, payload: dict) -> str:
    """The real ``run_workflow`` tool, as a member's agent calls it in org C."""
    from orchestrator import workflow_tools

    async def body():
        return await workflow_tools.run_workflow(name, json.dumps(payload), wait_seconds=20)

    return _on_app_role(promoted, promoted.org_c, body)


def test_each_projects_node_type_is_refused():
    """A new ``pm_*`` node type joins the refusal, or this case goes red."""
    assert {t for t in NODE_TYPES if t.startswith("pm_")} == service._MEMBER_BLIND_WRITE_TYPES


@pytest.mark.parametrize("name", ["Picked task in C", "Fixed task in C"])
def test_an_agent_cannot_run_a_task_node(promoted, name):
    """The agent picks the target through the payload, or a fixed id holds it.

    The rows come first. A mutation that removes the refusal then fails on the
    changed task, which is the defect itself."""
    target = promoted.work_c["task"]
    before = _task_row(promoted, target), _activities(promoted, target)
    out = _agent_run(promoted, name, {"task_id": target})
    assert (_task_row(promoted, target), _activities(promoted, target)) == before
    assert out.startswith("Could not start workflow") and "Projects tasks" in out, out
    assert target not in out and "Open in C" not in out, out
    assert _run_count(promoted, name) == 0


def test_an_agent_cannot_run_a_sweep_node(promoted):
    target = promoted.work_c["stale"]
    out = _agent_run(promoted, "Sweep in C", {})
    assert _task_row(promoted, target).archived_at is None
    assert out.startswith("Could not start workflow") and "Projects tasks" in out, out
    assert _run_count(promoted, "Sweep in C") == 0


def test_an_agent_still_runs_a_workflow_with_only_a_module_node(promoted):
    out = _agent_run(promoted, "Module in C", {"task_id": promoted.work_c["task"]})
    assert "succeeded" in out, out
    row = _run_row(promoted, out.split("Run ", 1)[1].split(" ", 1)[0])
    assert _step_result(row)["output"] == {"marker": "module-C"}


# ── 7. An approval never resumes a run into a Projects node ──────────────────


def _pause_at_the_gate(promoted, name: str) -> str:
    """A paused run in org C. The refusal stops an agent from starting one
    now, so this is a run that paused before the refusal existed."""
    run_id = _start_at_the_seam(promoted, name, promoted.org_c)
    assert _run_row(promoted, run_id).status == "paused"
    return run_id


def _approve(promoted, run_id: str) -> dict:
    """The real broker handler, with the approver's org bound."""
    from gateway.routes.workflows.broker_handlers import _resume_run_handler

    async def body():
        return await _resume_run_handler(SimpleNamespace(payload={"run_id": run_id}))

    return _on_app_role(promoted, promoted.org_c, body)


def _assert_refused(promoted, run_id: str, result: dict) -> None:
    assert result["ok"] is False and "Projects tasks" in result["error"], result
    row = _run_row(promoted, run_id)
    assert (row.status, row.org) == ("cancelled", promoted.org_c)
    assert "Projects tasks" in row.error and promoted.work_c["task"] not in row.error
    assert _pause_statuses(promoted, run_id) == ["rejected"]


def test_an_approval_never_resumes_a_run_into_a_task_node(promoted):
    target = promoted.work_c["task"]
    before = _task_row(promoted, target), _activities(promoted, target)
    run_id = _pause_at_the_gate(promoted, "Gated task in C")
    result = _approve(promoted, run_id)
    assert (_task_row(promoted, target), _activities(promoted, target)) == before
    _assert_refused(promoted, run_id, result)


def test_an_approval_never_resumes_a_run_into_a_sweep_node(promoted):
    target = promoted.work_c["stale"]
    run_id = _pause_at_the_gate(promoted, "Gated sweep in C")
    result = _approve(promoted, run_id)
    assert _task_row(promoted, target).archived_at is None
    _assert_refused(promoted, run_id, result)


def test_an_approval_resumes_past_a_task_node_that_already_ran(promoted):
    """The replay does not run the node again, so the run has no write left."""
    target = promoted.before_gate
    run_id = _pause_at_the_gate(promoted, "Task before the gate in C")
    assert _task_row(promoted, target).title == "Written before the gate"
    written = len(_activities(promoted, target))

    assert _approve(promoted, run_id)["ok"] is True
    row = _run_row(promoted, run_id)
    assert (row.status, row.error, row.org) == ("succeeded", None, promoted.org_c)
    assert _step_result(row)["output"] == {"marker": "module-C"}
    assert len(_activities(promoted, target)) == written


def test_a_resumed_run_takes_its_org_from_the_run_row(promoted, monkeypatch):
    """RLS makes the caller's org equal the stored org, so only the spelling
    can differ. The resumed run must carry the row's value, not the caller's."""

    async def start():
        return await service.run_published_workflow(
            "Gated module in A", {}, started_by="h328", organization_id=promoted.org_a)

    run_id = _on_app_role(promoted, None, start)["run_id"]
    seen: dict[str, Any] = {}
    real = service._execute_run

    async def _spy(**kw: Any) -> None:
        seen.update(kw)
        await real(**kw)

    monkeypatch.setattr(service, "_execute_run", _spy)

    async def approve():
        return await service.resume_run(run_id, organization_id=promoted.org_a.upper())

    assert _on_app_role(promoted, None, approve)["ok"] is True
    assert seen["organization_id"] == promoted.org_a


# ── 8. CI must run this suite ────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard():
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert "tests/unit/test_workflows_lifecycle_rls_r8.py" in workflow
