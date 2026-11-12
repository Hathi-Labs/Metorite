"""STB-5 (task 2133): the unattended trigger doors under FORCE RLS.

Spec: ``project-docs/specs/workflows_app.md`` (the H4 slice 6c note) · board
R5 and R8 · HANDOFF H-328.

**The owner's order (2026-11-11).** "Write the R8 evidence, report the
failures, do not fix them." So this file changes no production code. Each
property that the code breaks today is a case with
``xfail(strict=True)``. The reason names the failing site. When H4 slice 6c
binds the org at that site, the case passes, the strict mark turns it red,
and the author of 6c removes the mark.

**The doors.** Four entry points start a run with no ``organization_id``:

* ``triggers.dispatch_event`` reads ``workflow_triggers`` on the unbound
  ``get_db()`` and calls ``start_run`` with no org. It takes no org at all,
  so it cannot tell an event of org A from an event of org B.
* ``hooks.workflow_hook`` reads the workflow by its token on the unbound
  ``get_db()`` and calls ``start_run`` with no org. It reads no delivery id,
  so a replay starts a second run.
* ``runs.run_workflow`` (a member's manual run) reads in the member's
  tenant, then calls ``start_run`` with no org. The run row then goes
  through the unbound ``get_db()``.
* ``scheduler._scan_org`` binds the org of the workflow row. It is the one
  door that works. ``test_workflows_lifecycle_rls_r8.py`` section 9 fences it
  in depth, and this file keeps one cross-org case beside the other doors.

**What passes today, and why.** Under FORCE RLS an unbound read sees no row.
So no door can start a run in another org: it starts no run at all. The
cases that prove "never in org B" pass for real, and they stay green after
6c only if 6c binds the org of the delivery and not the org of each row.

Mutations this suite catches (R7):

* ``dispatch_event`` reads every org and binds the org of each row: the
  event cross-org case goes red, because B's workflow starts;
* the allowlist check dropped in ``_scan_once``: the schedule case goes red;
* the org bind dropped in ``_scan_org``: the schedule case goes red;
* ``load_workflow_or_404`` read unbound in ``run_workflow``: the manual
  cross-org case goes red.

⚠️ **R8.** The subject is a policy. A Python fake agrees with whatever SQL it
is handed. The gate is ``TENANT_LADDER_DATABASE_URL``, never ``DATABASE_URL``.
A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_workflows_triggers_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.workflows import hooks, runs, scheduler, triggers, unattended
from gateway.routes.workflows.engine.graph import compile_graph
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from starlette.requests import Request

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database

_ROOT = Path(__file__).resolve().parents[2]

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

#: Suite-private role name. A role is cluster-wide, so a shared name would
#: clobber another suite's password.
_APP_ROLE = "acb_app_wftrig"
_APP_PW = "wftrig_scratch_pw"

_OWNERS = {"A": "owner@a.wftrig.test", "B": "owner@b.wftrig.test"}

#: One event that both orgs subscribe to.
_SOURCE, _EVENT = "r8trig", "lead.created"

_H6C = "H-328 slice 6c: "


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": nid, "config": config or {}}}


def _trivial() -> dict:
    """trigger -> output. The run touches no other table, so a run row that
    exists proves the door, and nothing else."""
    return compile_graph({
        "nodes": [_node("start", "trigger", {"kind": "manual"}),
                  _node("done", "output", {"value": "end"})],
        "edges": [{"id": "start->done", "source": "start", "target": "done"}],
    })


#: name -> (org tag, trigger kind, trigger config). A hook token is per name.
_WORKFLOWS: dict[str, tuple[str, str, dict]] = {
    "Event in A": ("A", "event", {"source": _SOURCE, "event_type": _EVENT}),
    "Event in B": ("B", "event", {"source": _SOURCE, "event_type": _EVENT}),
    "Hook in A": ("A", "webhook", {}),
    "Hook in B": ("B", "webhook", {}),
    "Tick in A": ("A", "schedule", {"cron": "* * * * *"}),
    "Tick in B": ("B", "schedule", {"cron": "* * * * *"}),
    "Manual in A": ("A", "manual", {}),
    "Manual in B": ("B", "manual", {}),
}
_TICKERS = ("Tick in A", "Tick in B")


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


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
    """A dedicated phase-4 catalog, a NOBYPASSRLS role, and the trigger
    workflows of two orgs."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_wftrig"
    _clone_promoted_database(admin_url, dedicated)

    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    _grant_app_role(ded_admin_url, dedicated)

    orgs = {"A": str(uuid.uuid4()), "B": str(uuid.uuid4())}
    wf: dict[str, str] = {}
    tokens: dict[str, str] = {}
    serialized = json.dumps(_trivial())
    with eng.begin() as conn:
        for tag, oid in orgs.items():
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": f"wftrig-{tag.lower()}", "name": f"wftrig {tag}"})
        for name, (tag, kind, config) in _WORKFLOWS.items():
            wid, token = str(uuid.uuid4()), f"wftrig-{uuid.uuid4().hex}"
            conn.execute(text(
                "INSERT INTO workflows (id, name, description, owner_email, status, "
                "latest_version, hook_token, organization_id) VALUES (:id, :n, '', "
                ":o, 'published', 1, :tok, :org)"),
                {"id": wid, "n": name, "o": _OWNERS[tag], "tok": token, "org": orgs[tag]})
            conn.execute(text(
                "INSERT INTO workflow_versions (workflow_id, version, serialized, "
                "published_by, organization_id) VALUES (:w, 1, CAST(:s AS jsonb), "
                "'seed', :org)"), {"w": wid, "s": serialized, "org": orgs[tag]})
            if kind != "manual":
                # A schedule stays off until its case arms it.
                conn.execute(text(
                    "INSERT INTO workflow_triggers (workflow_id, kind, config, enabled, "
                    "last_fired_at, organization_id) VALUES (CAST(:w AS uuid), :k, "
                    "CAST(:c AS jsonb), :on, now() - make_interval(mins => 10), "
                    "CAST(:o AS uuid))"),
                    {"w": wid, "k": kind, "c": json.dumps(config),
                     "on": kind != "schedule", "o": orgs[tag]})
            wf[name], tokens[name] = wid, token

    ns = SimpleNamespace(
        admin_engine=eng,
        app_dsn=admin_url.set(
            database=dedicated, username=_APP_ROLE, password=_APP_PW,
        ).render_as_string(hide_password=False),
        org_a=orgs["A"], org_b=orgs["B"], wf=wf, tokens=tokens,
    )
    yield ns

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture(autouse=True)
def _clean(promoted, monkeypatch):
    """Every case starts with no run row, the schedules off, an empty
    allowlist and an empty hook rate bucket."""
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM workflow_runs WHERE workflow_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(promoted.wf.values())})
        c.execute(text(
            "UPDATE workflow_triggers SET enabled = (kind <> 'schedule'), "
            "last_fired_at = now() - make_interval(mins => 10) "
            "WHERE workflow_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(promoted.wf.values())})
    _list_orgs(monkeypatch)
    hooks._RATE.clear()


async def _drain_runs() -> None:
    """Let every run task of this loop finish before the engine goes."""
    tasks = [t for t in asyncio.all_tasks()
             if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
    if tasks:
        await asyncio.wait(tasks, timeout=30)


def _on_app_role(promoted, ambient: str | None, body):
    """Run *body* on the app-role engine, with *ambient* as the bound tenant.

    ``None`` is what an unattended door has: no tenant in the context."""

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


def _runs_of(promoted, name: str) -> list:
    with promoted.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT status, trigger_kind, started_by, "
            "CAST(organization_id AS text) AS org FROM workflow_runs "
            "WHERE workflow_id = CAST(:w AS uuid) ORDER BY started_at"),
            {"w": promoted.wf[name]}).fetchall())


def _list_orgs(monkeypatch, *orgs: str) -> None:
    """Set ``WORKFLOW_UNATTENDED_ORGS`` for one case. No arg is the shipped
    default, an empty list."""
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(
        unattended, "get_settings",
        lambda: SimpleNamespace(workflow_unattended_orgs=",".join(orgs)),
    )


def _request(body: bytes, headers: dict[str, str] | None = None) -> Request:
    """A POST to the hook route, as the ASGI server hands it over."""
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": "POST", "path": "/workflows/hooks/x",
        "query_string": b"", "http_version": "1.1",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope, receive)


def _post_hook(promoted, name: str, *, ambient: str | None = None,
               body: bytes = b'{"lead": 1}', headers: dict | None = None):
    """POST the hook of *name*. Returns the response dict or the HTTPException."""

    async def go():
        try:
            return await hooks.workflow_hook(promoted.tokens[name], _request(body, headers))
        except HTTPException as exc:
            return exc

    return _on_app_role(promoted, ambient, go)


# ── 0. The catalog really is constrained ─────────────────────────────────────


def test_an_unbound_read_finds_no_trigger_and_no_workflow(promoted):
    """Precondition: the role cannot see past RLS without a tenant. The
    doors below read with exactly this session."""
    from gateway.routes.workflows.core import _get_db

    async def body():
        db = await _get_db()
        try:
            return [(await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
                    for t in ("workflow_triggers", "workflows")]
        finally:
            await db.close()

    assert _on_app_role(promoted, None, body) == [0, 0]
    with promoted.admin_engine.connect() as c:  # and the rows do exist
        assert c.execute(text(
            "SELECT count(*) FROM workflow_triggers WHERE workflow_id = "
            "ANY(CAST(:ids AS uuid[]))"), {"ids": list(promoted.wf.values())}
        ).scalar() == 6


# ── 1. The event door (triggers.dispatch_event) ──────────────────────────────


@pytest.mark.parametrize("ambient", [None, "A"])
def test_an_event_never_starts_a_run_in_another_org(promoted, ambient):
    """An event that org A's receiver delivers never starts org B's
    workflow, though B subscribes to the same source and event type."""
    amb = promoted.org_a if ambient == "A" else None
    started = _on_app_role(promoted, amb, lambda: triggers.dispatch_event(
        _SOURCE, _EVENT, {"organization_id": promoted.org_a}))
    assert promoted.wf["Event in B"] not in {s["workflow_id"] for s in started}
    assert _runs_of(promoted, "Event in B") == []


@pytest.mark.xfail(strict=True, reason=(
    _H6C + "triggers.py:56 reads workflow_triggers on the unbound get_db(), "
    "which sees no row under FORCE RLS, and triggers.py:81 calls start_run "
    "with no organization_id. dispatch_event takes no org, so no event "
    "trigger fires in any org."))
def test_an_event_of_org_a_starts_the_workflow_of_org_a(promoted):
    started = _on_app_role(promoted, promoted.org_a, lambda: triggers.dispatch_event(
        _SOURCE, _EVENT, {"organization_id": promoted.org_a}))
    assert [s["workflow_id"] for s in started] == [promoted.wf["Event in A"]]
    (row,) = _runs_of(promoted, "Event in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "event", promoted.org_a)
    assert _runs_of(promoted, "Event in B") == []


# ── 2. The webhook door (hooks.workflow_hook) ────────────────────────────────


@pytest.mark.parametrize("ambient", [None, "B"])
def test_a_hook_of_org_a_never_starts_a_run_in_org_b(promoted, ambient):
    """A's token never starts B's workflow, and B bound in the context does
    not move A's run into B."""
    amb = promoted.org_b if ambient == "B" else None
    _post_hook(promoted, "Hook in A", ambient=amb)
    assert _runs_of(promoted, "Hook in B") == []
    assert all(r.org == promoted.org_a for r in _runs_of(promoted, "Hook in A"))
    with promoted.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM workflow_runs WHERE organization_id = CAST(:o AS uuid)"),
            {"o": promoted.org_b}).scalar() == 0


@pytest.mark.xfail(strict=True, reason=(
    _H6C + "hooks.py:75 reads the workflow by hook_token on the unbound "
    "get_db(), which sees no row under FORCE RLS, so a valid token answers "
    "404 Unknown hook. hooks.py:133 then calls start_run with no "
    "organization_id."))
def test_a_hook_of_org_a_starts_one_run_in_org_a(promoted):
    result = _post_hook(promoted, "Hook in A")
    assert isinstance(result, dict), getattr(result, "detail", result)
    (row,) = _runs_of(promoted, "Hook in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "webhook", promoted.org_a)


def _as_if_6c_bound_the_hook(promoted, monkeypatch, org: str) -> None:
    """Simulate the read and the start that 6c will bind, in the TEST only.

    The hook read gets a session with ``app.tenant_id`` set for its one
    transaction (``set_config(..., true)``), and ``start_run`` gets *org*.
    So the replay case below fails on the missing dedupe alone."""
    real_get_db, real_start = hooks._get_db, hooks.start_run

    async def bound_db():
        db = await real_get_db()
        await db.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
        return db

    async def start(**kw):
        return await real_start(**kw, organization_id=org)

    monkeypatch.setattr(hooks, "_get_db", bound_db)
    monkeypatch.setattr(hooks, "start_run", start)


def test_the_simulated_binding_starts_a_hook_run_in_org_a(promoted, monkeypatch):
    """Control for the replay case: with the binding simulated, one POST
    starts one run in A. So the replay case measures the dedupe, not RLS."""
    _as_if_6c_bound_the_hook(promoted, monkeypatch, promoted.org_a)
    result = _post_hook(promoted, "Hook in A")
    assert isinstance(result, dict), getattr(result, "detail", result)
    (row,) = _runs_of(promoted, "Hook in A")
    assert (row.status, row.org) == ("succeeded", promoted.org_a)


@pytest.mark.xfail(strict=True, reason=(
    _H6C + "hooks.py:67-146 keeps no delivery id and no idempotency key. "
    "workflow_runs has no dedupe column, so a replayed delivery starts a "
    "second run."))
def test_a_replayed_delivery_starts_exactly_one_run(promoted, monkeypatch):
    _as_if_6c_bound_the_hook(promoted, monkeypatch, promoted.org_a)
    delivery = {"X-CC-Delivery": "d-1", "Idempotency-Key": "d-1"}
    first = _post_hook(promoted, "Hook in A", headers=delivery)
    second = _post_hook(promoted, "Hook in A", headers=delivery)
    assert isinstance(first, dict), getattr(first, "detail", first)
    assert not isinstance(second, dict) or second.get("run_id") == first["run_id"]
    assert len(_runs_of(promoted, "Hook in A")) == 1


# ── 3. The schedule door (scheduler._scan_org) ───────────────────────────────


def _arm(promoted, *names: str) -> None:
    with promoted.admin_engine.begin() as c:
        for name in names:
            c.execute(text(
                "UPDATE workflow_triggers SET enabled = true, "
                "last_fired_at = now() - make_interval(mins => 10) "
                "WHERE workflow_id = CAST(:w AS uuid)"), {"w": promoted.wf[name]})


@pytest.mark.parametrize("ambient", [None, "B"])
def test_a_schedule_of_org_a_never_starts_a_run_in_org_b(promoted, monkeypatch, ambient):
    """Only A is listed. A's due tick runs in A. B's due tick does not run,
    and B bound in the context does not choose the org."""
    _list_orgs(monkeypatch, promoted.org_a)
    _arm(promoted, *_TICKERS)
    amb = promoted.org_b if ambient == "B" else None
    assert _on_app_role(promoted, amb, scheduler._scan_once) == 1
    (row,) = _runs_of(promoted, "Tick in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "schedule", promoted.org_a)
    assert _runs_of(promoted, "Tick in B") == []


def test_each_listed_org_runs_its_own_schedule_in_its_own_org(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    _arm(promoted, *_TICKERS)
    assert _on_app_role(promoted, None, scheduler._scan_once) == 2
    assert [r.org for r in _runs_of(promoted, "Tick in A")] == [promoted.org_a]
    assert [r.org for r in _runs_of(promoted, "Tick in B")] == [promoted.org_b]


# ── 4. The manual door (runs.run_workflow) ───────────────────────────────────


def _manual(promoted, name: str, ambient: str):
    member = SimpleNamespace(email=_OWNERS["A"])

    async def go():
        try:
            return await runs.run_workflow(promoted.wf[name], runs.RunRequest(), user=member)
        except HTTPException as exc:
            return exc

    return _on_app_role(promoted, ambient, go)


def test_a_member_of_org_a_cannot_run_the_workflow_of_org_b(promoted):
    result = _manual(promoted, "Manual in B", promoted.org_a)
    assert isinstance(result, HTTPException) and result.status_code == 404
    assert _runs_of(promoted, "Manual in B") == []


@pytest.mark.xfail(strict=True, reason=(
    _H6C + "runs.py:100 calls start_run with no organization_id, so "
    "service._run_session(None) writes the run row on the unbound get_db(). "
    "Under FORCE RLS the INSERT into workflow_runs fails, and a member's "
    "manual run never starts."))
def test_a_manual_run_starts_in_the_members_org(promoted):
    result = _manual(promoted, "Manual in A", promoted.org_a)
    assert isinstance(result, dict), result
    (row,) = _runs_of(promoted, "Manual in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "manual", promoted.org_a)


# ── 5. CI must run this suite ────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard():
    for wf in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        assert "tests/unit/test_workflows_triggers_r8.py" in workflow, wf
