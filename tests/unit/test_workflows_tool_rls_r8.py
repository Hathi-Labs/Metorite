"""F13 under FORCE RLS: the workflow agent tools bind the run's tenant.

Spec: ``project-docs/specs/workflows_app.md`` F13 · board R5 and R8 ·
``apps/services/orchestrator/AGENTS.md`` item 8.

**The defect this file fences (2026-10-15).** ``list_published_workflows``
and ``run_published_workflow`` opened the unbound ``get_db()``. ``workflows``
has FORCE ROW LEVEL SECURITY, and production connects as a NOBYPASSRLS role.
So an agent's ``list_workflows`` read zero rows, and ``run_workflow`` found
nothing to run. The hermetic suite (``test_workflows_as_tool.py``) fakes the
service, so it stayed green.

**What this file proves, as the NON-privileged role.** It builds its own
database with the full ladder and the four ``generated/`` phases, so FORCE RLS
is live on every scoped table. The gateway's shared engine connects as a role
that is not a superuser, not the owner and has no BYPASSRLS. Nothing here
patches ``_get_db`` or ``tenant_session``. The tools run the REAL service.

* the run's own org lists and runs its own workflows;
* another org's workflow is never listed, run, or reported;
* no bound org refuses, and reads nothing;
* the tools take no org argument, so tool input cannot choose the tenant.

Mutations this suite catches (R7):

* the service back to the unbound ``_get_db()``: the org's own list is empty;
* the tool takes its org from an argument: the signature fence goes red;
* the run's writes back to unbound: the run never reaches ``succeeded``.

⚠️ **R8.** The subject is a policy. A Python fake agrees with whatever SQL it
is handed. The gate is ``TENANT_LADDER_DATABASE_URL``, never ``DATABASE_URL``.
A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_workflows_tool_rls_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import TenantUnbound, bind_tenant, release_tenant
from gateway.routes.workflows.engine.graph import compile_graph
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
_APP_ROLE = "acb_app_wfrls"
_APP_PW = "wfrls_scratch_pw"

#: The same name in both orgs. Under the old unbound read with BYPASSRLS this
#: was "ambiguous". Under the bound read each org sees exactly its own.
_SHARED = "Lead intake"


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": nid, "config": config or {}}}


def _serialized(marker: str, gate: tuple[str, dict] | None = None) -> dict:
    """trigger -> [gate ->] output. No agent and no tool node, so the run
    ends alone, or pauses at the gate."""
    nodes = [_node("start", "trigger", {"kind": "manual"})]
    edges = []
    prev = "start"
    if gate is not None:
        nodes.append(_node("gate", gate[0], gate[1]))
        edges.append({"id": "start->gate", "source": "start", "target": "gate"})
        prev = "gate"
    nodes.append(_node("done", "output", {"value": marker}))
    edges.append({"id": f"{prev}->done", "source": prev, "target": "done"})
    return compile_graph({"nodes": nodes, "edges": edges})


#: Org A's gate workflows: name -> (gate type, gate config).
_GATED = {
    "Approval in A": ("approval", {}),
    "Long wait in A": ("wait", {"seconds": 3600}),
    "Trigger-timed wait in A": ("wait", {"seconds": "{{trigger.delay}}"}),
    "Short wait in A": ("wait", {"seconds": 0.05}),
}
_A_NAMES = {_SHARED, "Only in A", *_GATED}


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


@pytest.fixture(scope="module")
def promoted():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role, two orgs' workflows."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_wfrls"
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

    # Two orgs, seeded by the admin (the admin escapes RLS). Each org has the
    # shared name, and one name of its own.
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    wf: dict[tuple[str, str], str] = {}
    with eng.begin() as conn:
        for oid, slug in ((org_a, "wfrls-a"), (org_b, "wfrls-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": slug, "name": slug})
        for oid, tag in ((org_a, "A"), (org_b, "B")):
            for name in (_SHARED, f"Only in {tag}"):
                wid = str(uuid.uuid4())
                wf[(tag, name)] = wid
                conn.execute(text(
                    "INSERT INTO workflows (id, name, description, owner_email, "
                    "status, latest_version, organization_id) VALUES "
                    "(:id, :n, :d, :o, 'published', 1, :org)"),
                    {"id": wid, "n": name, "d": f"org {tag}",
                     "o": f"owner@{tag.lower()}.wfrls.test", "org": oid})
                conn.execute(text(
                    "INSERT INTO workflow_versions (workflow_id, version, "
                    "serialized, published_by, organization_id) VALUES "
                    "(:w, 1, CAST(:s AS jsonb), 'seed', :org)"),
                    {"w": wid, "s": json.dumps(_serialized(f"out-{tag}")),
                     "org": oid})
        for name, gate in _GATED.items():
            wid = str(uuid.uuid4())
            wf[("A", name)] = wid
            conn.execute(text(
                "INSERT INTO workflows (id, name, description, owner_email, "
                "status, latest_version, organization_id) VALUES "
                "(:id, :n, 'org A', 'owner@a.wfrls.test', 'published', 1, :org)"),
                {"id": wid, "n": name, "org": org_a})
            conn.execute(text(
                "INSERT INTO workflow_versions (workflow_id, version, "
                "serialized, published_by, organization_id) VALUES "
                "(:w, 1, CAST(:s AS jsonb), 'seed', :org)"),
                {"w": wid, "s": json.dumps(_serialized("out-A", gate)),
                 "org": org_a})

    class _NS:
        pass

    ns = _NS()
    ns.admin_engine = eng
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    ns.org_a, ns.org_b = org_a, org_b
    ns.wf = wf
    yield ns

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    from orchestrator import workflow_tools

    monkeypatch.setattr(workflow_tools, "_POLL_INTERVAL_SECONDS", 0.05)


@pytest.fixture(autouse=True)
def proposals(monkeypatch):
    """The approval gate files its proposal through ``broker.submit``. Record
    it, with the tenant bound at that moment, instead of writing
    ``pending_actions``: the broker's own R8 suite owns that write."""
    import action_broker.broker as broker
    from acb_common.db import current_tenant

    filed: list[dict] = []

    async def _submit(proposal):
        filed.append({"payload": dict(proposal.payload), "org": current_tenant()})
        return {"action_id": f"pa-{len(filed)}"}

    monkeypatch.setattr(broker, "submit", _submit)
    return filed


async def _drain_runs() -> None:
    """Let every run task of this loop finish before the engine goes."""
    tasks = [t for t in asyncio.all_tasks()
             if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
    if tasks:
        await asyncio.wait(tasks, timeout=30)


def _as_run(promoted, org: str | None, body):
    """Run *body* on the app-role engine, with *org* bound as a run binds it."""

    async def _go():
        async with tenant_engine_scope(promoted.app_dsn):
            token = bind_tenant(org) if org else None
            try:
                return await body()
            finally:
                await _drain_runs()
                release_tenant(token)

    return asyncio.run(_go())


def _runs(promoted, workflow_id: str) -> list:
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, CAST(organization_id AS text) AS org, trigger_kind "
            "FROM workflow_runs WHERE workflow_id = CAST(:w AS uuid)"),
            {"w": workflow_id}).fetchall()


# ── 0. The catalog really is constrained ─────────────────────────────────────


def test_an_unbound_read_finds_no_workflow(promoted):
    """Precondition: the role cannot see past RLS without a tenant."""
    from gateway.routes.workflows.core import _get_db

    async def body():
        db = await _get_db()
        try:
            return (await db.execute(text(
                "SELECT count(*) FROM workflows WHERE status = 'published'"
            ))).scalar()
        finally:
            await db.close()

    assert _as_run(promoted, None, body) == 0


# ── 1. list_workflows ────────────────────────────────────────────────────────


def test_list_workflows_lists_the_run_orgs_own_workflows(promoted):
    from orchestrator import workflow_tools

    out = _as_run(promoted, promoted.org_a, workflow_tools.list_workflows)
    rows = json.loads(out)
    assert {r["name"] for r in rows} == _A_NAMES
    assert {r["id"] for r in rows} == {promoted.wf[("A", n)] for n in _A_NAMES}
    assert all(r["description"] == "org A" for r in rows)


def test_list_workflows_never_shows_another_orgs_workflow(promoted):
    from orchestrator import workflow_tools

    out = _as_run(promoted, promoted.org_b, workflow_tools.list_workflows)
    ids = {r["id"] for r in json.loads(out)}
    assert not ids & {promoted.wf[("A", n)] for n in _A_NAMES}
    assert ids == {promoted.wf[("B", _SHARED)], promoted.wf[("B", "Only in B")]}


def test_list_workflows_refuses_with_no_bound_org(promoted):
    from orchestrator import workflow_tools

    out = _as_run(promoted, None, workflow_tools.list_workflows)
    assert "organization" in out.lower()
    assert "No published workflows" not in out


def test_the_service_list_refuses_an_empty_org(promoted):
    from gateway.routes.workflows.service import list_published_workflows

    async def body():
        return await list_published_workflows(organization_id="")

    with pytest.raises(TenantUnbound):
        _as_run(promoted, promoted.org_a, body)


# ── 2. run_workflow / get_workflow_run ───────────────────────────────────────


def test_run_workflow_runs_the_run_orgs_workflow_to_the_end(promoted):
    from orchestrator import workflow_tools

    async def body():
        return await workflow_tools.run_workflow(_SHARED, '{"body": "x"}', wait_seconds=20)

    out = _as_run(promoted, promoted.org_a, body)
    assert "succeeded" in out, out
    assert "out-A" in out and "out-B" not in out
    runs = _runs(promoted, promoted.wf[("A", _SHARED)])
    assert [(r.status, r.org, r.trigger_kind) for r in runs] == [
        ("succeeded", promoted.org_a, "api")]
    assert _runs(promoted, promoted.wf[("B", _SHARED)]) == []


def test_run_workflow_cannot_reach_another_orgs_workflow_by_id(promoted):
    from orchestrator import workflow_tools

    target = promoted.wf[("B", "Only in B")]

    async def body():
        return await workflow_tools.run_workflow(target, "{}")

    out = _as_run(promoted, promoted.org_a, body)
    assert out.startswith("Could not start workflow"), out
    assert _runs(promoted, target) == []


def test_get_workflow_run_never_reports_another_orgs_run(promoted):
    from orchestrator import workflow_tools

    async def start():
        return await workflow_tools.run_workflow("Only in B", "{}", wait_seconds=20)

    out = _as_run(promoted, promoted.org_b, start)
    assert "succeeded" in out, out
    run_id = out.split("Run ", 1)[1].split(" ", 1)[0]

    async def peek():
        return await workflow_tools.get_workflow_run(run_id)

    assert "succeeded" in _as_run(promoted, promoted.org_b, peek)
    assert "lookup failed" in _as_run(promoted, promoted.org_a, peek)


def test_run_workflow_refuses_with_no_bound_org(promoted):
    from orchestrator import workflow_tools

    async def body():
        return await workflow_tools.run_workflow("Only in A", "{}")

    out = _as_run(promoted, None, body)
    assert "organization" in out.lower(), out
    assert _runs(promoted, promoted.wf[("A", "Only in A")]) == []


def test_the_service_run_refuses_an_empty_org(promoted):
    from gateway.routes.workflows.service import run_published_workflow

    async def body():
        return await run_published_workflow(
            "Only in A", {}, started_by="t", organization_id="")

    with pytest.raises(TenantUnbound):
        _as_run(promoted, promoted.org_a, body)


# ── 3. Gates: an approval resumes in the approver's org, a long wait refuses ──


def _pause_rows(promoted, run_id: str) -> list:
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, reason, CAST(organization_id AS text) AS org "
            "FROM workflow_run_pauses WHERE run_id = CAST(:r AS uuid)"),
            {"r": run_id}).fetchall()


def _run_row(promoted, run_id: str):
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, output FROM workflow_runs WHERE id = CAST(:r AS uuid)"),
            {"r": run_id}).fetchone()


def _start_approval_run(promoted) -> str:
    from orchestrator import workflow_tools

    async def body():
        return await workflow_tools.run_workflow("Approval in A", "{}", wait_seconds=20)

    out = _as_run(promoted, promoted.org_a, body)
    assert "paused" in out and "approvals inbox" in out, out
    return out.split("Run ", 1)[1].split(" ", 1)[0]


def _approve(promoted, org: str | None, run_id: str) -> dict:
    """What ``broker.approve`` does in the approver's request: it runs the
    registered handler with the approver's org bound. The payload names org B
    on purpose: a payload field must never choose the tenant."""
    from types import SimpleNamespace

    from gateway.routes.workflows.broker_handlers import _resume_run_handler

    proposal = SimpleNamespace(
        payload={"run_id": run_id, "organization_id": promoted.org_b})

    async def body():
        return await _resume_run_handler(proposal)

    return _as_run(promoted, org, body)


def test_the_gate_pause_is_written_with_the_runs_org(promoted, proposals):
    run_id = _start_approval_run(promoted)
    assert [(p.status, p.reason, p.org) for p in _pause_rows(promoted, run_id)] == [
        ("pending", "approval", promoted.org_a)]
    assert _run_row(promoted, run_id).status == "paused"
    assert [(f["payload"]["run_id"], f["org"]) for f in proposals] == [
        (run_id, promoted.org_a)]


def test_an_approval_in_the_runs_org_resumes_it_to_the_end(promoted):
    run_id = _start_approval_run(promoted)
    result = _approve(promoted, promoted.org_a, run_id)
    assert result["ok"] is True, result
    row = _run_row(promoted, run_id)
    assert row.status == "succeeded"
    output = json.loads(row.output) if isinstance(row.output, str) else row.output
    assert output == ["out-A"]
    assert [p.status for p in _pause_rows(promoted, run_id)] == ["resolved"]


def test_an_approval_from_another_org_resumes_nothing(promoted):
    run_id = _start_approval_run(promoted)
    result = _approve(promoted, promoted.org_b, run_id)
    assert result["ok"] is False and "no run" in result["error"], result
    assert _run_row(promoted, run_id).status == "paused"
    assert [p.status for p in _pause_rows(promoted, run_id)] == ["pending"]


def test_an_approval_with_no_bound_org_resumes_nothing(promoted):
    run_id = _start_approval_run(promoted)
    result = _approve(promoted, None, run_id)
    assert result["ok"] is False, result
    assert _run_row(promoted, run_id).status == "paused"


@pytest.mark.parametrize("name", ["Long wait in A", "Trigger-timed wait in A"])
def test_run_workflow_refuses_a_wait_that_can_pause(promoted, name):
    """Only the wait scan resumes a long wait, and it is not tenant-bound
    (H4 slice 6c). So the agent cannot start a run that would stay paused."""
    from orchestrator import workflow_tools

    async def body():
        return await workflow_tools.run_workflow(name, '{"delay": 7200}')

    out = _as_run(promoted, promoted.org_a, body)
    assert out.startswith("Could not start workflow") and "wait node" in out, out
    assert _runs(promoted, promoted.wf[("A", name)]) == []


def test_run_workflow_still_runs_a_short_inline_wait(promoted):
    from orchestrator import workflow_tools

    async def body():
        return await workflow_tools.run_workflow("Short wait in A", "{}", wait_seconds=20)

    out = _as_run(promoted, promoted.org_a, body)
    assert "succeeded" in out, out


# ── 4. Tool input never chooses the tenant (R5 / R11) ────────────────────────


def test_no_workflow_tool_takes_an_org_argument():
    """The trio's parameters are the agent's input. An org among them would
    let a prompt choose the tenant. The org comes from the run binding."""
    from orchestrator import workflow_tools

    for fn in (workflow_tools.list_workflows, workflow_tools.run_workflow,
               workflow_tools.get_workflow_run):
        for name in inspect.signature(fn).parameters:
            assert not any(k in name.lower() for k in ("org", "tenant")), (
                fn.__name__, name)
