"""STB-5 (task 2133): the unattended trigger doors under FORCE RLS.

Spec: ``project-docs/specs/workflows_app.md`` (the H4 slice 6c note) · board
R5 and R8 · HANDOFF H-328.

**History.** STB-5 (task 2133) wrote this file on the owner's order of
2026-11-11: "Write the R8 evidence, report the failures, do not fix them."
Four cases were strict xfails. H-328 part (c) (H4 slice 6c, task 856) bound
the org at each door and removed the four marks.

**The doors.** Each door now passes an org to ``start_run``:

* ``triggers.dispatch_event`` reads ``current_tenant()`` once. Only a member
  request that emits binds one. No tenant, or an org that is not on
  ``WORKFLOW_UNATTENDED_ORGS``, starts nothing. The org in the payload is
  input, and the door never reads it.
* ``hooks.workflow_hook`` loops over the listed orgs and reads the token in
  ``_tenant_session(org)``. A token of an org that is not listed answers 404,
  the same as a bad token. A replayed delivery key starts no second run
  (``workflow_runs.delivery_key``, migration 420).
* ``runs.run_workflow`` (a member's manual run) passes the org of the row
  that the member read. It refuses a task node, and a long wait in an org
  that is not listed.
* ``scheduler._scan_org`` binds the org of the workflow row.
  ``test_workflows_lifecycle_rls_r8.py`` section 9 fences it in depth, and
  this file keeps one cross-org case beside the other doors.

The hook and the event door also refuse a task node, as the scan does.

Mutations this suite catches (R7):

* ``dispatch_event`` reads every org and binds the org of each row: the
  event cross-org case goes red, because B's workflow starts;
* ``dispatch_event`` takes the org from the payload: the case with no
  tenant and an org in the payload goes red;
* the allowlist check dropped in the hook door: the unlisted hook case goes
  red;
* the ``ON CONFLICT`` dropped in ``start_run``: the replay case goes red. A
  conflict on every key: the two-key case goes red;
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
#: A second member of org A. M1 is the owner above.
M2 = "member2@a.wftrig.test"

#: The Studio packages of org A (STU-E3 access fix). All are owned by M1.
#: PKG_CLAIMS is private and shared with nobody. PKG_ORG has an org install.
#: PKG_SHARED has a private install that M1 shared with M2.
PKG_CLAIMS = "6f0b2d3a-0000-4000-8000-0000000000c1"
PKG_ORG = "6f0b2d3a-0000-4000-8000-0000000000c2"
PKG_SHARED = "6f0b2d3a-0000-4000-8000-0000000000c3"


#: One event that both orgs subscribe to.
_SOURCE, _EVENT = "r8trig", "lead.created"


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


def _chain(ntype: str, config: dict) -> dict:
    """trigger -> one node -> output."""
    return compile_graph({
        "nodes": [_node("start", "trigger", {"kind": "manual"}),
                  _node("step", ntype, config),
                  _node("done", "output", {"value": "end"})],
        "edges": [{"id": "start->step", "source": "start", "target": "step"},
                  {"id": "step->done", "source": "step", "target": "done"}],
    })


#: A task write that no run reaches: each door refuses it before the start.
_TASK_WRITE = {"task_id": "{{trigger.task_id}}", "fields": {"title": "r8"}}

#: The event type of the workflow with a task node, so it never matches the
#: event of the trivial workflows.
_TASK_EVENT = "task.created"

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
    "Task manual in A": ("A", "manual", {}),
    "Wait manual in A": ("A", "manual", {}),
    "Task hook in A": ("A", "webhook", {}),
    "Task event in A": ("A", "event", {"source": _SOURCE, "event_type": _TASK_EVENT}),
    # STU-E3: Studio record triggers, filtered by package and object.
    "Claim in A": ("A", "event", {"source": "studio", "event_type": "studio.record.created",
                                  "filter": {"package_id": PKG_CLAIMS, "object": "claim"}}),
    "Invoice in A": ("A", "event", {"source": "studio", "event_type": "studio.record.created",
                                    "filter": {"package_id": PKG_CLAIMS, "object": "invoice"}}),
    "Claim 2 in A": ("A", "event", {"source": "studio", "event_type": "studio.record.created",
                                    "filter": {"object": "claim"}}),
    "Claim in B": ("B", "event", {"source": "studio", "event_type": "studio.record.created",
                                  "filter": {"package_id": PKG_CLAIMS, "object": "claim"}}),
}
# STU-E3 access fix: the same triggers, with an author who is not the owner.
_WORKFLOWS.update({
    "Private by M2": ("A", "event", {"source": "studio", "event_type": "studio.record.created",
                                     "filter": {"package_id": PKG_CLAIMS}}),
    "Org package by M2": ("A", "event", {"source": "studio", "event_type": "studio.record.created",
                                         "filter": {"package_id": PKG_ORG}}),
    "Org package by M1": ("A", "event", {"source": "studio", "event_type": "studio.record.created",
                                         "filter": {"package_id": PKG_ORG}}),
    "Shared package by M2": ("A", "event", {"source": "studio",
                                            "event_type": "studio.record.created",
                                            "filter": {"package_id": PKG_SHARED}}),
})
#: The author of a workflow when it is not the owner of its org.
_AUTHOR = {"Private by M2": M2, "Org package by M2": M2, "Shared package by M2": M2}
_TICKERS = ("Tick in A", "Tick in B")

#: name -> the run-model of a workflow that is not trivial.
_GRAPHS: dict[str, dict] = {
    "Task manual in A": _chain("pm_task", _TASK_WRITE),
    "Wait manual in A": _chain("wait", {"seconds": 3600}),
    "Task hook in A": _chain("pm_task", _TASK_WRITE),
    "Task event in A": _chain("pm_task", _TASK_WRITE),
}


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


def _seed_packages(conn, org: str) -> None:
    """Three packages of M1 in org A, as ``packages_store`` leaves them."""
    owner = _OWNERS["A"]
    for pid, slug, scope, installed in (
        (PKG_CLAIMS, "claims", None, False),
        (PKG_ORG, "orgwide", "org", True),
        (PKG_SHARED, "shared", "private", True),
    ):
        conn.execute(text(
            "INSERT INTO apps (id, slug, name, description, owner_email, visibility, "
            "status, manifest, workspace_path, format, organization_id) VALUES "
            "(CAST(:id AS uuid), :slug, :slug, '', :o, 'private', 'draft', "
            "CAST('{\"studio\": 1}' AS jsonb), '', 'manifest', CAST(:org AS uuid))"),
            {"id": pid, "slug": f"wftrig-{slug}", "o": owner, "org": org})
        if not installed:
            continue
        conn.execute(text(
            "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
            "bundle_sha256, published_by, organization_id) VALUES (CAST(:id AS uuid), 1, "
            "CAST('{\"studio\": 1}' AS jsonb), '', '', :o, CAST(:org AS uuid))"),
            {"id": pid, "o": owner, "org": org})
        conn.execute(text(
            "INSERT INTO package_install (app_id, version, scope_kind, scope_subject, "
            "installed_by, organization_id) VALUES (CAST(:id AS uuid), 1, :k, :sub, :o, "
            "CAST(:org AS uuid))"),
            {"id": pid, "k": scope, "sub": "org" if scope == "org" else owner,
             "o": owner, "org": org})
    conn.execute(text(
        "INSERT INTO app_grants (app_id, subject, role, granted_by, organization_id) "
        "VALUES (CAST(:id AS uuid), :m2, 'use', :o, CAST(:org AS uuid))"),
        {"id": PKG_SHARED, "m2": M2, "o": owner, "org": org})


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
    trivial = json.dumps(_trivial())
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
                {"id": wid, "n": name, "o": _AUTHOR.get(name, _OWNERS[tag]), "tok": token,
                 "org": orgs[tag]})
            conn.execute(text(
                "INSERT INTO workflow_versions (workflow_id, version, serialized, "
                "published_by, organization_id) VALUES (:w, 1, CAST(:s AS jsonb), "
                "'seed', :org)"),
                {"w": wid, "s": json.dumps(_GRAPHS[name]) if name in _GRAPHS else trivial,
                 "org": orgs[tag]})
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

        _seed_packages(conn, orgs["A"])

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
        ).scalar() == sum(1 for _t, kind, _c in _WORKFLOWS.values() if kind != "manual")


def test_the_promoted_clone_has_the_delivery_key_and_its_index(promoted):
    """Migration 420 reached the catalog that the doors run against."""
    with promoted.admin_engine.connect() as c:
        col = c.execute(text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'workflow_runs' AND column_name = 'delivery_key'"
        )).fetchone()
        idx = c.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'workflow_runs' "
            "AND indexname = 'workflow_runs_delivery_key_uniq'")).scalar()
    assert col is not None and tuple(col) == ("text", "YES")
    assert idx is not None and "UNIQUE" in idx, idx
    assert "(workflow_id, delivery_key)" in idx and "delivery_key IS NOT NULL" in idx, idx


# ── 1. The event door (triggers.dispatch_event) ──────────────────────────────


def _dispatch(promoted, ambient: str | None, event: str = _EVENT, org: str | None = None):
    """Emit one event with *ambient* bound. The payload names *org* (A by
    default), as a receiver's payload can."""
    return _on_app_role(promoted, ambient, lambda: triggers.dispatch_event(
        _SOURCE, event, {"organization_id": org or promoted.org_a}))


@pytest.mark.parametrize("ambient", [None, "A"])
def test_an_event_never_starts_a_run_in_another_org(promoted, monkeypatch, ambient):
    """An event that org A's receiver delivers never starts org B's
    workflow, though B subscribes to the same source and event type. Both
    orgs are listed, so a door that loops over each org would start B's."""
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    amb = promoted.org_a if ambient == "A" else None
    started = _on_app_role(promoted, amb, lambda: triggers.dispatch_event(
        _SOURCE, _EVENT, {"organization_id": promoted.org_a}))
    assert promoted.wf["Event in B"] not in {s["workflow_id"] for s in started}
    assert _runs_of(promoted, "Event in B") == []


def test_an_event_of_org_a_starts_the_workflow_of_org_a(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    started = _dispatch(promoted, promoted.org_a)
    assert [s["workflow_id"] for s in started] == [promoted.wf["Event in A"]]
    (row,) = _runs_of(promoted, "Event in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "event", promoted.org_a)
    assert _runs_of(promoted, "Event in B") == []


def test_an_event_with_b_bound_runs_in_b_and_never_in_a(promoted, monkeypatch):
    """B is bound and the payload names A. The bound tenant decides: B's
    workflow runs in B, and A's does not run."""
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    started = _dispatch(promoted, promoted.org_b, org=promoted.org_a)
    assert [s["workflow_id"] for s in started] == [promoted.wf["Event in B"]]
    (row,) = _runs_of(promoted, "Event in B")
    assert (row.status, row.org) == ("succeeded", promoted.org_b)
    assert _runs_of(promoted, "Event in A") == []


def test_an_event_of_an_unlisted_org_starts_no_run(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_b)
    assert _dispatch(promoted, promoted.org_a) == []
    assert _runs_of(promoted, "Event in A") == []
    assert _runs_of(promoted, "Event in B") == []


def test_an_event_with_no_tenant_and_an_org_in_the_payload_starts_nothing(
    promoted, monkeypatch,
):
    """The payload is input. With no tenant bound, the door starts nothing,
    though the payload names a listed org."""
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    assert _dispatch(promoted, None, org=promoted.org_a) == []
    assert _runs_of(promoted, "Event in A") == []
    assert _runs_of(promoted, "Event in B") == []


def test_an_event_never_starts_a_workflow_with_a_task_node(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    assert _dispatch(promoted, promoted.org_a, event=_TASK_EVENT) == []
    assert _runs_of(promoted, "Task event in A") == []


# ── 2. The webhook door (hooks.workflow_hook) ────────────────────────────────


@pytest.mark.parametrize("ambient", [None, "B"])
def test_a_hook_of_org_a_never_starts_a_run_in_org_b(promoted, monkeypatch, ambient):
    """A's token never starts B's workflow, and B bound in the context does
    not move A's run into B. Both orgs are listed."""
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    amb = promoted.org_b if ambient == "B" else None
    result = _post_hook(promoted, "Hook in A", ambient=amb)
    assert isinstance(result, dict), getattr(result, "detail", result)
    assert _runs_of(promoted, "Hook in B") == []
    (row,) = _runs_of(promoted, "Hook in A")
    assert (row.status, row.org) == ("succeeded", promoted.org_a)
    with promoted.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM workflow_runs WHERE organization_id = CAST(:o AS uuid)"),
            {"o": promoted.org_b}).scalar() == 0


def test_a_hook_of_org_a_starts_one_run_in_org_a(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    result = _post_hook(promoted, "Hook in A")
    assert isinstance(result, dict), getattr(result, "detail", result)
    assert result["status"] == "queued"
    (row,) = _runs_of(promoted, "Hook in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "webhook", promoted.org_a)


def test_a_hook_of_an_unlisted_org_answers_404_and_starts_no_run(promoted, monkeypatch):
    """Only B is listed. A's valid token answers the same 404 as a bad token,
    so a caller cannot learn that the token exists."""
    _list_orgs(monkeypatch, promoted.org_b)
    result = _post_hook(promoted, "Hook in A")
    assert isinstance(result, HTTPException), result
    assert (result.status_code, result.detail) == (404, "Unknown hook")
    promoted.tokens["no such hook"] = f"wftrig-{uuid.uuid4().hex}"
    try:
        bad = _post_hook(promoted, "no such hook")
    finally:
        del promoted.tokens["no such hook"]
    assert (bad.status_code, bad.detail) == (result.status_code, result.detail)
    assert _runs_of(promoted, "Hook in A") == []


def test_a_hook_never_starts_a_workflow_with_a_task_node(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    result = _post_hook(promoted, "Task hook in A")
    assert isinstance(result, HTTPException) and result.status_code == 409, result
    assert _runs_of(promoted, "Task hook in A") == []


def test_one_keyed_delivery_starts_one_hook_run_in_org_a(promoted, monkeypatch):
    """Control for the replay case: one POST with a key starts one run in A.
    So the replay case measures the dedupe, not RLS."""
    _list_orgs(monkeypatch, promoted.org_a)
    result = _post_hook(promoted, "Hook in A", headers={"Idempotency-Key": "d-0"})
    assert isinstance(result, dict), getattr(result, "detail", result)
    assert result["status"] == "queued"
    (row,) = _runs_of(promoted, "Hook in A")
    assert (row.status, row.org) == ("succeeded", promoted.org_a)


def test_a_replayed_delivery_starts_exactly_one_run(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    delivery = {"X-CC-Delivery": "d-1", "Idempotency-Key": "d-1"}
    first = _post_hook(promoted, "Hook in A", headers=delivery)
    second = _post_hook(promoted, "Hook in A", headers=delivery)
    assert isinstance(first, dict), getattr(first, "detail", first)
    assert isinstance(second, dict), getattr(second, "detail", second)
    assert second == {"run_id": first["run_id"], "status": "duplicate"}
    assert len(_runs_of(promoted, "Hook in A")) == 1


def test_a_replay_by_the_delivery_header_alone_starts_one_run(promoted, monkeypatch):
    """With no ``Idempotency-Key``, ``X-CC-Delivery`` is the key."""
    _list_orgs(monkeypatch, promoted.org_a)
    first = _post_hook(promoted, "Hook in A", headers={"X-CC-Delivery": "d-2"})
    second = _post_hook(promoted, "Hook in A", headers={"X-CC-Delivery": "d-2"})
    assert second == {"run_id": first["run_id"], "status": "duplicate"}
    assert len(_runs_of(promoted, "Hook in A")) == 1


def test_two_delivery_keys_start_two_runs(promoted, monkeypatch):
    """The dedupe drops only a replay. Two keys, and two POSTs with no key,
    each start a run."""
    _list_orgs(monkeypatch, promoted.org_a)
    for headers in ({"Idempotency-Key": "k-1"}, {"Idempotency-Key": "k-2"}, None, None):
        result = _post_hook(promoted, "Hook in A", headers=headers)
        assert isinstance(result, dict) and result["status"] == "queued", result
    runs_a = _runs_of(promoted, "Hook in A")
    assert len(runs_a) == 4
    assert {r.org for r in runs_a} == {promoted.org_a}


def test_an_over_long_delivery_key_answers_400_and_starts_no_run(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    result = _post_hook(promoted, "Hook in A",
                        headers={"Idempotency-Key": "k" * (hooks.MAX_DELIVERY_KEY_CHARS + 1)})
    assert isinstance(result, HTTPException) and result.status_code == 400, result
    assert _runs_of(promoted, "Hook in A") == []


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


def test_a_manual_run_starts_in_the_members_org(promoted):
    """No org is listed: a person pressed Run, so no list gates it."""
    result = _manual(promoted, "Manual in A", promoted.org_a)
    assert isinstance(result, dict), result
    (row,) = _runs_of(promoted, "Manual in A")
    assert (row.status, row.trigger_kind, row.org) == ("succeeded", "manual", promoted.org_a)
    with promoted.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM workflow_runs WHERE organization_id = CAST(:o AS uuid)"),
            {"o": promoted.org_b}).scalar() == 0


def test_a_manual_run_with_a_task_node_is_refused(promoted, monkeypatch):
    """The org is listed, and the door still refuses a task node."""
    _list_orgs(monkeypatch, promoted.org_a)
    result = _manual(promoted, "Task manual in A", promoted.org_a)
    assert isinstance(result, HTTPException) and result.status_code == 409, result
    assert _runs_of(promoted, "Task manual in A") == []


def test_a_manual_long_wait_in_an_unlisted_org_is_refused(promoted, monkeypatch):
    """Only B is listed, so no scan would resume A's pause."""
    _list_orgs(monkeypatch, promoted.org_b)
    result = _manual(promoted, "Wait manual in A", promoted.org_a)
    assert isinstance(result, HTTPException) and result.status_code == 409, result
    assert _runs_of(promoted, "Wait manual in A") == []


def test_a_manual_long_wait_in_a_listed_org_starts_and_pauses(promoted, monkeypatch):
    """Control: the wait refusal follows the list. A listed org starts the
    run, and the run pauses in A for the wait scan."""
    _list_orgs(monkeypatch, promoted.org_a)
    result = _manual(promoted, "Wait manual in A", promoted.org_a)
    assert isinstance(result, dict), result
    (row,) = _runs_of(promoted, "Wait manual in A")
    assert (row.status, row.org) == ("paused", promoted.org_a)


# ── 5. CI must run this suite ────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard():
    for wf in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        assert "tests/unit/test_workflows_triggers_r8.py" in workflow, wf


# ── STU-E3: the Studio record trigger ────────────────────────────────────────

_REC = {"package_id": PKG_CLAIMS, "object": "claim", "op": "create", "record_id": "r1"}


def _emit_record(promoted, ambient: str | None, payload: dict | None = None):
    return _on_app_role(promoted, ambient, lambda: triggers.dispatch_event(
        "studio", "studio.record.created", {**(payload or _REC)}))


def test_a_record_event_starts_only_the_matching_trigger_of_the_bound_org(promoted, monkeypatch):
    """The filter picks the claim workflow. The invoice workflow of the same
    org and the claim workflow of org B (both listed) start nothing."""
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    started = _emit_record(promoted, promoted.org_a)
    assert {s["workflow_id"] for s in started} == {
        promoted.wf["Claim in A"], promoted.wf["Claim 2 in A"]}
    (row,) = _runs_of(promoted, "Claim in A")
    assert (row.trigger_kind, row.org, row.started_by) == (
        "event", promoted.org_a, "event:studio")
    assert _runs_of(promoted, "Invoice in A") == []
    assert _runs_of(promoted, "Claim in B") == []


def test_a_record_event_with_no_bound_org_starts_nothing(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    assert _emit_record(promoted, None, {**_REC, "organization_id": promoted.org_a}) == []
    assert _runs_of(promoted, "Claim in A") == []


def test_a_record_event_of_an_unlisted_org_starts_nothing(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_b)
    assert _emit_record(promoted, promoted.org_a) == []
    assert _runs_of(promoted, "Claim in A") == []


def test_two_matching_triggers_of_one_workflow_start_one_run(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO workflow_triggers (workflow_id, kind, config, enabled, "
            "organization_id) VALUES (CAST(:w AS uuid), 'event', CAST(:c AS jsonb), "
            "true, CAST(:o AS uuid))"),
            {"w": promoted.wf["Claim in A"], "o": promoted.org_a,
             "c": json.dumps({"source": "studio", "event_type": "studio.record.created"})})
    try:
        started = _emit_record(promoted, promoted.org_a)
        ids = [s["workflow_id"] for s in started]
        assert sorted(ids) == sorted([promoted.wf["Claim in A"], promoted.wf["Claim 2 in A"]])
        assert len(_runs_of(promoted, "Claim in A")) == 1
    finally:
        with promoted.admin_engine.begin() as c:
            c.execute(text(
                "DELETE FROM workflow_triggers WHERE workflow_id = CAST(:w AS uuid) "
                "AND NOT (config ? 'filter')"), {"w": promoted.wf["Claim in A"]})


# ── STU-E3 access fix: the author must read the package ─────────────────────


def _host_on(monkeypatch, *orgs: str) -> None:
    """``PACKAGE_HOST_ORGS`` for one case. The viewer path of the Studio read
    rule opens only for a listed org, as in the action door."""
    from gateway.package_host import flags

    monkeypatch.setattr(flags, "package_host_orgs", lambda: frozenset(orgs))


def _started_names(promoted, started) -> set[str]:
    """The names of the started workflows. 'Claim 2 in A' (owner M1, no package
    in its filter) matches each record of object claim, so it is left out."""
    ids = {s["workflow_id"] for s in started}
    return {name for name, wid in promoted.wf.items()
            if wid in ids and name != "Claim 2 in A"}


def test_a_write_in_a_private_package_starts_no_run_of_another_author(promoted, monkeypatch):
    """M1 writes in M1's private package. The workflow of M2 starts no run, and
    the workflow of M1 does."""
    _list_orgs(monkeypatch, promoted.org_a)
    _host_on(monkeypatch, promoted.org_a)
    started = _emit_record(promoted, promoted.org_a)
    names = _started_names(promoted, started)
    assert "Private by M2" not in names
    assert "Claim in A" in names
    assert {s["workflow_id"] for s in started} >= {promoted.wf["Claim 2 in A"]}
    assert _runs_of(promoted, "Private by M2") == []


def test_an_author_with_a_share_of_the_package_starts(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    _host_on(monkeypatch, promoted.org_a)
    started = _emit_record(promoted, promoted.org_a, {**_REC, "package_id": PKG_SHARED})
    assert _started_names(promoted, started) == {"Shared package by M2"}
    (row,) = _runs_of(promoted, "Shared package by M2")
    assert (row.trigger_kind, row.org) == ("event", promoted.org_a)


def test_an_org_wide_package_starts_the_workflow_of_every_author(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    _host_on(monkeypatch, promoted.org_a)
    started = _emit_record(promoted, promoted.org_a, {**_REC, "package_id": PKG_ORG})
    assert _started_names(promoted, started) == {"Org package by M1", "Org package by M2"}


def test_a_viewer_author_needs_the_package_host_flag_as_the_door_does(promoted, monkeypatch):
    """With the flag off, only the owner reads (the rule of ``invoke._check``)."""
    _list_orgs(monkeypatch, promoted.org_a)
    _host_on(monkeypatch)
    started = _emit_record(promoted, promoted.org_a, {**_REC, "package_id": PKG_ORG})
    assert _started_names(promoted, started) == {"Org package by M1"}


@pytest.mark.parametrize("package_id", [
    None, "", "pkg-claims", "not-a-uuid", "6f0b2d3a-0000-4000-8000-0000000000ff",
    6, ["x"],
])
def test_a_record_event_without_a_readable_package_starts_nothing(
        promoted, monkeypatch, package_id):
    """Fail closed. 'Claim 2 in A' has a filter with no package, so only the
    access check can stop it."""
    _list_orgs(monkeypatch, promoted.org_a)
    _host_on(monkeypatch, promoted.org_a)
    payload = {k: v for k, v in _REC.items() if k != "package_id"}
    if package_id is not None:
        payload["package_id"] = package_id
    assert _emit_record(promoted, promoted.org_a, payload) == []
    assert _runs_of(promoted, "Claim 2 in A") == []


def test_an_event_bound_to_org_b_never_reads_a_package_of_org_a(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a, promoted.org_b)
    _host_on(monkeypatch, promoted.org_a, promoted.org_b)
    assert _emit_record(promoted, promoted.org_b) == []
    assert _runs_of(promoted, "Claim in B") == []
    assert _runs_of(promoted, "Claim in A") == []


def test_a_read_error_starts_nothing(promoted, monkeypatch):
    from gateway.studio import packages_store

    _list_orgs(monkeypatch, promoted.org_a)

    async def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(packages_store, "get_package", boom)
    assert _emit_record(promoted, promoted.org_a) == []


def test_other_event_sources_skip_the_package_check(promoted, monkeypatch):
    _list_orgs(monkeypatch, promoted.org_a)
    started = _on_app_role(promoted, promoted.org_a, lambda: triggers.dispatch_event(
        _SOURCE, _EVENT, {}))
    assert _started_names(promoted, started) == {"Event in A"}
