"""STU-B2c-2d: one parked decision approves one Studio write of a workflow node.

Spec: ``project-docs/specs/ai_studio.md`` row B2c-2d (task 1867). Owner
decision of 2026-11-06, option (a): for an unattended workflow run, the AUTO-7b
parked decision on the owner's private task is the ONE approval. After it, the
``studio_write`` node reaches the Studio door by an internal path
(``invoke.run_workflow_write``) that opens NO second card.

Code: ``routes/workflows/engine/graph.py`` (the node and its graph check),
``engine/handlers.py`` (the seam), ``service._studio_writer`` (the refusals and
the decision read), ``service.resume_run(decided=True)``,
``scheduler.scan_decided_pauses``, ``park.approves_one_write`` and
``studio/invoke.run_workflow_write``.

**Hermetic** (no database): the graph rules, the refusal of each run that is
not the unattended parked path, and the source fences.

**R8** (the class carries ``_DB_GATE``): the real seams on a dedicated
database, as a role that is NOT a superuser and has NO BYPASSRLS, two orgs.

Run (a PRIVATE database, because the fixture drops ``<db>_studiowf``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_workflow_write_r8.py -v -rs

Without the database each R8 test SKIPS. A skip is not a pass.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import action_broker.broker as broker
from acb_common import get_settings
from acb_common.no_person import no_person_scope
from gateway.routes.projects import decisions
from gateway.routes.studio import invoke
from gateway.routes.workflows import park, scheduler, service, unattended
from gateway.routes.workflows.engine.graph import (
    NODE_TYPES,
    compile_graph,
    runtime_gate_of,
    validate_graph,
)
from gateway.routes.workflows.engine.handlers import NodeExecutionError, NodeServices
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY = _ROOT / "apps" / "services" / "gateway" / "gateway"
_WAIT = 60

ORG = "0d4b8f5e-0000-4000-8000-0000000000b2"
PKG = "7a1c0e55-0000-4000-8000-0000000000c3"


def _node(nid: str, ntype: str, label: str | None = None, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": label or nid, "config": config or {}}}


def _edges(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in pairs]


def _write_config(pkg: str = PKG, **over: Any) -> dict:
    return {"package_id": pkg, "action": "add",
            "data": {"site": "Plant 4", "status": "planned"}, **over}


def _graph(*, gates: int = 1, write_cfg: dict | None = None) -> dict:
    """trigger -> gate -> write [-> gate2] -> done."""
    nodes = [_node("start", "trigger", config={"kind": "schedule"}),
             _node("gate", "approval", "Approve the write"),
             _node("write", "studio_write", config=write_cfg or _write_config())]
    chain = ["start", "gate", "write"]
    if gates == 2:
        nodes.append(_node("gate2", "approval", "Approve again"))
        chain.append("gate2")
    nodes.append(_node("done", "output", config={"value": "end"}))
    chain.append("done")
    return {"nodes": nodes, "edges": _edges(*itertools.pairwise(chain))}


def _codes(graph: dict) -> list[str]:
    return [i.code for i in validate_graph(graph)]


# ══ Hermetic ════════════════════════════════════════════════════════════════

# ── The node and its graph check ────────────────────────────────────────────


def test_the_node_type_exists_and_a_valid_graph_compiles() -> None:
    assert "studio_write" in NODE_TYPES
    compiled = compile_graph(_graph())
    assert {b["id"]: b["type"] for b in compiled["blocks"]}["write"] == "studio_write"
    assert runtime_gate_of(compiled, "write") == "gate"


def test_a_write_must_follow_an_approval_directly() -> None:
    nodes = [_node("start", "trigger"), _node("gate", "approval"),
             _node("mid", "set", config={"assignments": {}}),
             _node("write", "studio_write", config=_write_config())]
    two_down = {"nodes": nodes, "edges": _edges(("start", "gate"), ("gate", "mid"),
                                                ("mid", "write"))}
    assert "studio_write_without_approval" in _codes(two_down)
    no_gate = {"nodes": [nodes[0], nodes[3]], "edges": _edges(("start", "write"))}
    assert "studio_write_without_approval" in _codes(no_gate)
    assert runtime_gate_of(compile_graph(_graph()), "done") is None


def test_one_approval_clears_one_write_only() -> None:
    nodes = [_node("start", "trigger"), _node("gate", "approval"),
             _node("w1", "studio_write", config=_write_config()),
             _node("w2", "studio_write", config=_write_config())]
    graph = {"nodes": nodes, "edges": _edges(("start", "gate"), ("gate", "w1"),
                                             ("gate", "w2"))}
    issues = validate_graph(graph)
    assert [i.code for i in issues] == ["approval_clears_one_write"]
    # The run-time twin refuses the same shape, for a draft that skips publish.
    run_model = {"blocks": [{"id": n["id"], "type": n["type"]} for n in nodes],
                 "connections": [{"source": e["source"], "target": e["target"]}
                                 for e in graph["edges"]]}
    assert runtime_gate_of(run_model, "w1") is None
    assert runtime_gate_of(run_model, "w2") is None


@pytest.mark.parametrize(("cfg", "code"), [
    (_write_config(data={"site": "{{trigger.site}}"}), "templated_config"),
    (_write_config(organization_id=ORG), "unknown_field"),
    (_write_config(member="x@y.test"), "unknown_field"),
    (_write_config(pkg="not-a-uuid"), "missing_config"),
    (_write_config(action="Bad Action"), "missing_config"),
    (_write_config(data=["x"]), "missing_config"),
    (_write_config(record_id="nope"), "missing_config"),
])
def test_the_config_is_literal_and_names_four_keys(cfg: dict, code: str) -> None:
    assert code in _codes(_graph(write_cfg=cfg))


# ── The refusals of the seam ────────────────────────────────────────────────

_PARKED = service.ParkedApproval(decision_id=str(uuid.uuid4()), pause_id=str(uuid.uuid4()),
                                 gate="gate")


def _all_flags(monkeypatch, orgs: str = ORG) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_park_orgs", orgs)
    monkeypatch.setattr(settings, "decisions_orgs", orgs)
    monkeypatch.setattr(settings, "studio_orgs", orgs)
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=orgs))


def _writer(**over: Any) -> Any:
    kw: dict[str, Any] = {"run_id": str(uuid.uuid4()), "workflow_id": str(uuid.uuid4()),
                          "organization_id": ORG, "trigger_kind": "schedule",
                          "serialized": compile_graph(_graph()), "parked": _PARKED}
    kw.update(over)
    return service._studio_writer(**kw)


def _no_reads(monkeypatch) -> list[str]:
    """Each read past the refusals is a failure: the owner read, the decision
    read and the door."""
    seen: list[str] = []

    async def _owner(*_a: Any) -> tuple[str, str]:
        seen.append("owner")
        raise AssertionError("the owner was read")

    async def _door(**_k: Any) -> dict:
        seen.append("door")
        raise AssertionError("the door was called")

    monkeypatch.setattr(service, "_workflow_owner", _owner)
    monkeypatch.setattr(invoke, "run_workflow_write", _door)
    return seen


async def _call(writer: Any, *, marked: bool = True) -> Any:
    if not marked:
        return await writer("write", _write_config())
    with no_person_scope():
        return await writer("write", _write_config())


@pytest.mark.parametrize(("over", "marked", "code"), [
    ({"organization_id": None}, True, service.STUDIO_NO_ORG),
    ({"organization_id": ""}, True, service.STUDIO_NO_ORG),
    ({"trigger_kind": "manual"}, True, service.STUDIO_NOT_SCHEDULED),
    ({"trigger_kind": "api"}, True, service.STUDIO_NOT_SCHEDULED),
    ({"trigger_kind": "event"}, True, service.STUDIO_NOT_SCHEDULED),
    ({"trigger_kind": "webhook"}, True, service.STUDIO_NOT_SCHEDULED),
    ({}, False, service.STUDIO_UNMARKED),
    ({"parked": None}, True, service.STUDIO_NO_DECISION),
    ({"parked": service.ParkedApproval(decision_id=str(uuid.uuid4()),
                                       pause_id=str(uuid.uuid4()), gate="done")},
     True, service.STUDIO_BAD_GATE),
])
def test_each_run_that_is_not_the_parked_path_refuses_before_any_read(
    monkeypatch, over: dict, marked: bool, code: str,
) -> None:
    _all_flags(monkeypatch)
    seen = _no_reads(monkeypatch)
    with pytest.raises(NodeExecutionError) as refused:
        asyncio.run(_call(_writer(**over), marked=marked))
    assert f"({code})" in str(refused.value)
    assert seen == []


@pytest.mark.parametrize("off", ["auto_park_orgs", "decisions_orgs", "studio_orgs",
                                 "workflow_unattended_orgs"])
def test_each_of_the_four_flags_off_refuses(monkeypatch, off: str) -> None:
    _all_flags(monkeypatch)
    if off == "workflow_unattended_orgs":
        monkeypatch.setattr(unattended, "get_settings",
                            lambda: SimpleNamespace(workflow_unattended_orgs=""))
    else:
        monkeypatch.setattr(get_settings(), off, "")
    seen = _no_reads(monkeypatch)
    with pytest.raises(NodeExecutionError) as refused:
        asyncio.run(_call(_writer()))
    assert f"({service.STUDIO_FLAG_OFF})" in str(refused.value)
    assert seen == []


def test_the_node_without_a_seam_fails() -> None:
    from gateway.routes.workflows.engine.handlers import execute_node

    async def _none(*_a: Any) -> Any:
        return None

    services = NodeServices(run_agent=_none, run_tool=_none, get_module_code=_none)
    with pytest.raises(NodeExecutionError):
        asyncio.run(execute_node({"id": "write", "type": "studio_write",
                                  "config": _write_config()}, {}, services))


def test_the_internal_door_refuses_with_no_org_or_the_switch_off(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")

    async def go(org: str) -> dict:
        return await invoke.run_workflow_write(
            organization_id=org, member="o@a.test", package_id=PKG, action="add",
            data={}, record_id=None, run_id="r", node_id="write")

    assert asyncio.run(go(""))["code"] == invoke.WORKFLOW_NO_ORG
    assert asyncio.run(go(ORG))["code"] == invoke.WORKFLOW_STUDIO_OFF


# ── The source fences ───────────────────────────────────────────────────────


def _code(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_only_the_workflow_seam_calls_the_internal_door() -> None:
    callers: set[str] = set()
    for path in (_ROOT / "apps").rglob("*.py"):
        if "node_modules" in path.parts or ".venv" in path.parts:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\brun_workflow_write\s*\(", body):
            callers.add(path.relative_to(_ROOT).as_posix())
    assert callers == {
        "apps/services/gateway/gateway/routes/studio/invoke.py",
        "apps/services/gateway/gateway/routes/workflows/service.py",
    }


def test_the_http_route_keeps_its_no_person_refusal_and_never_calls_the_path() -> None:
    import ast

    tree = ast.parse(_code(_GATEWAY / "routes" / "studio" / "invoke.py"))
    route = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "invoke_action")
    src = ast.unparse(route)
    assert "run_workflow_write" not in src
    assert "out.refusal = NO_PERSON" in src


def test_only_the_decided_scan_carries_the_decision() -> None:
    import ast

    hits: set[str] = set()
    for path in (_GATEWAY / "routes").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            func = getattr(node, "func", None)
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if isinstance(node, ast.Call) and name == "resume_run" and any(
                    k.arg == "decided" and not (isinstance(k.value, ast.Constant)
                                                and k.value.value is False)
                    for k in node.keywords):
                hits.add(path.name)
    assert hits == {"scheduler.py"}
    scan = _code(_GATEWAY / "routes" / "workflows" / "scheduler.py")
    body = scan.split("async def scan_decided_pauses", 1)[1].split("\nasync def ", 1)[0]
    assert "decided=True" in body


def test_this_suite_is_named_in_both_ci_skip_guards() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = _code(_ROOT / ".github" / "workflows" / name)
        assert re.search(r"^\s+tests/unit/test_studio_workflow_write_r8\.py \\?$",
                         workflow, re.M), name


# ══ R8 ══════════════════════════════════════════════════════════════════════

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT if _SNAPSHOT is not None else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it. Do NOT export "
        "it as DATABASE_URL."
    ),
)

_APP_ROLE = "acb_app_studiowf"
_APP_PW = "studiowf_scratch_pw"

OWNER_A = "owner@a.studiowf.test"
OWNER_B = "owner@b.studiowf.test"
_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"]},
]


def _manifest() -> dict[str, Any]:
    return {
        "studio": 1,
        "id": f"wfw-{uuid.uuid4().hex[:6]}",
        "name": "Visits",
        "purpose": "Track install visits.",
        "objects": [{"name": "visit", "fields": _FIELDS}],
        "actions": [
            {"id": "add", "uses": "object:visit.create"},
            {"id": "peek", "uses": "object:visit.get"},
        ],
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


def _seed_member(conn, org: str, email: str) -> None:
    conn.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": email, "o": org})


def _seed_workflow(conn, org: str, owner: str, name: str, serialized: dict) -> str:
    wid = str(uuid.uuid4())
    conn.execute(text(
        "INSERT INTO workflows (id, name, description, owner_email, status, "
        "latest_version, organization_id) VALUES (:id, :n, '', :o, 'published', 1, "
        "CAST(:org AS uuid))"), {"id": wid, "n": name, "o": owner, "org": org})
    conn.execute(text(
        "INSERT INTO workflow_versions (workflow_id, version, serialized, published_by, "
        "organization_id) VALUES (:w, 1, CAST(:s AS jsonb), 'seed', CAST(:org AS uuid))"),
        {"w": wid, "s": json.dumps(serialized), "org": org})
    return wid


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


async def _make_package(org: str, member: str) -> tuple[str, str]:
    from gateway.db import tenant_session
    from gateway.studio import objects_store, packages_store

    async with tenant_session(org) as db:
        made = await packages_store.create_package(db, member, _manifest())
        obj = await objects_store.define_object(db, member, package_id=made.id,
                                                name="visit", fields=_FIELDS)
        assert obj is not None
    return made.id, obj.id


@pytest.fixture(scope="module")
def world():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role, two orgs, a package
    of each owner, and the workflows that write to them."""
    from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_studiowf"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    _grant_app_role(ded_admin_url, dedicated)
    ns = SimpleNamespace(admin_engine=eng, wf={})
    ns.org_a, ns.org_b = str(uuid.uuid4()), str(uuid.uuid4())
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    with eng.begin() as conn:
        for oid, slug in ((ns.org_a, "studiowf-a"), (ns.org_b, "studiowf-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                {"id": oid, "s": slug})
        _seed_member(conn, ns.org_a, OWNER_A)
        _seed_member(conn, ns.org_b, OWNER_B)

    async def packages() -> None:
        ns.pkg_a, ns.obj_a = await _make_package(ns.org_a, OWNER_A)
        ns.pkg_b, ns.obj_b = await _make_package(ns.org_b, OWNER_B)

    _on_app_role(ns, packages)
    with eng.begin() as conn:
        for name, org, owner, serialized in (
            ("Write in A", ns.org_a, OWNER_A, compile_graph(_graph(
                write_cfg=_write_config(ns.pkg_a)))),
            ("Write twice gated in A", ns.org_a, OWNER_A, compile_graph(_graph(
                gates=2, write_cfg=_write_config(ns.pkg_a)))),
            ("A writes into B", ns.org_a, OWNER_A, compile_graph(_graph(
                write_cfg=_write_config(ns.pkg_b)))),
            ("Write in B", ns.org_b, OWNER_B, compile_graph(_graph(
                write_cfg=_write_config(ns.pkg_b)))),
        ):
            ns.wf[name] = (_seed_workflow(conn, org, owner, name, serialized), org, serialized)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def w(world, monkeypatch):
    """All four lists on for A and B. No run, decision, record or audit row is
    left from the last test. The broker store and the record event are spies."""
    orgs = f"{world.org_a},{world.org_b}"
    settings = get_settings()
    for key in ("auto_park_orgs", "decisions_orgs", "studio_orgs"):
        monkeypatch.setattr(settings, key, orgs)
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    world.listed = orgs
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=world.listed))
    world.proposals = []

    async def _submit(proposal: Any) -> dict:
        world.proposals.append(proposal)
        return {"action_id": str(uuid.uuid4())}

    monkeypatch.setattr(broker, "submit", _submit)
    world.events = []

    async def _emit(event_type: str, payload: dict) -> bool:
        world.events.append((event_type, payload))
        return True

    monkeypatch.setattr(invoke, "emit_record_event", _emit)
    with world.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflow_runs"))
        c.execute(text("DELETE FROM pm_decisions"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_tasks WHERE source = 'automation'"))
        c.execute(text("DELETE FROM studio_records"))
        c.execute(text("DELETE FROM app_audit"))
        c.execute(text("DELETE FROM pm_agent_runs WHERE app IS NOT NULL"))
    return world


def _one(w: Any, sql: str, **params: Any) -> Any:
    with w.admin_engine.connect() as c:
        return c.execute(text(sql), params).fetchone()


def _all(w: Any, sql: str, **params: Any) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _run(w: Any, run_id: str) -> Any:
    return _one(w, "SELECT status, error, node_results FROM workflow_runs "
                   "WHERE id = CAST(:r AS uuid)", r=run_id)


def _records(w: Any, obj: str) -> int:
    return int(_one(w, "SELECT count(*) AS n FROM studio_records "
                       "WHERE object_id = CAST(:o AS uuid)", o=obj).n)


def _audit(w: Any) -> list[dict]:
    rows = _all(w, "SELECT organization_id::text AS org, user_email, detail FROM app_audit "
                   "ORDER BY id")
    return [{"org": r.org, "user": r.user_email,
             **(r.detail if isinstance(r.detail, dict) else json.loads(r.detail))}
            for r in rows]


def _cards(w: Any) -> int:
    return int(_one(w, "SELECT count(*) AS n FROM pm_agent_runs WHERE app IS NOT NULL").n)


def _decisions_of(w: Any, run_id: str) -> list[Any]:
    return _all(w, "SELECT * FROM pm_decisions WHERE payload ->> 'run_id' = :r "
                   "ORDER BY created_at", r=run_id)


def _start(w: Any, name: str, *, kind: str = "schedule") -> str:
    wid, org, serialized = w.wf[name]

    async def body() -> str:
        return await service.start_run(
            workflow_id=wid, workflow_name=name, version=1, serialized=serialized,
            trigger_kind=kind, trigger_payload={"scheduled_at": "x"},
            started_by="scheduler", organization_id=org)

    return _on_app_role(w, body)


def _park(w: Any, name: str = "Write in A") -> tuple[str, Any]:
    run_id = _start(w, name)
    assert _run(w, run_id).status == "paused"
    made = _decisions_of(w, run_id)
    assert len(made) == 1, made
    return run_id, made[-1]


def _principal(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]), organization_id=org)


def _answer(w: Any, org: str, email: str, decision_id: str, key: str) -> Any:
    from gateway.db import tenant_session

    async def body() -> Any:
        async with tenant_session(org) as db:
            return await decisions.answer_decision(
                db, _principal(email, org), decision_id, key, None)

    return _on_app_role(w, body)


def _scan(w: Any) -> int:
    return _on_app_role(w, lambda: scheduler.scan_due_waits(None))


def _expire(w: Any, decision_id: str) -> None:
    with w.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text(
            "UPDATE pm_decisions SET created_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 minute' WHERE id = CAST(:d AS uuid)"),
            {"d": decision_id})


def _race(monkeypatch, parties: int = 2) -> None:
    """Hold each racer at its first UPDATE of a pause until all of them came,
    so the outcome is the compare-and-set alone."""
    state: dict[str, Any] = {"barrier": None}

    class _Held:
        def __init__(self, db: Any) -> None:
            self._db, self._held = db, False

        async def execute(self, clause: Any, *a: Any, **k: Any) -> Any:
            sql = " ".join(str(clause).split()).upper()
            if not self._held and sql.startswith("UPDATE WORKFLOW_RUN_PAUSES"):
                self._held = True
                if state["barrier"] is None:
                    state["barrier"] = asyncio.Barrier(parties)
                await asyncio.wait_for(state["barrier"].wait(), 30)
            return await self._db.execute(clause, *a, **k)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._db, name)

    real_run = service._run_session

    @contextlib.asynccontextmanager
    async def run_session(org: str | None):
        async with real_run(org) as db:
            yield _Held(db)

    monkeypatch.setattr(service, "_run_session", run_session)


def _error_of(w: Any, run_id: str) -> str:
    return str(_run(w, run_id).error or "")


@_DB_GATE
class TestTheStudioWriteOfAWorkflowNodeOnARealDatabase:
    def test_the_engine_cannot_bypass_rls(self, w):
        eng = create_engine(w.app_dsn, future=True)
        try:
            with eng.connect() as c:
                role = c.execute(text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user")).first()
                forced = c.execute(text(
                    "SELECT count(*) FROM pg_class WHERE relname IN "
                    "('pm_decisions', 'workflow_runs', 'studio_records', 'app_audit') "
                    "AND relrowsecurity AND relforcerowsecurity")).scalar()
        finally:
            eng.dispose()
        assert role is not None and not role[0] and not role[1]
        assert forced == 4

    # ── The acceptance: one decision, one write, no second card ─────────

    def test_an_approved_decision_writes_exactly_one_record_and_opens_no_card(self, w):
        run_id, made = _park(w)
        assert _records(w, w.obj_a) == 0
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        assert _scan(w) == 1
        assert _run(w, run_id).status == "succeeded", _error_of(w, run_id)
        assert _records(w, w.obj_a) == 1
        assert _cards(w) == 0, "the write opened a second card"
        assert w.proposals == [], "the write filed a broker proposal"
        audit = _audit(w)
        assert len(audit) == 1
        row = audit[0]
        assert (row["org"], row["user"], row["outcome"], row["via"]) == (
            w.org_a, OWNER_A, "ran", invoke.WORKFLOW_VIA)
        assert (row["run_id"], row["node_id"], row["action"]) == (run_id, "write", "add")
        rec = _one(w, "SELECT id, organization_id::text AS org, created_by FROM studio_records")
        assert row["record_id"] == str(rec.id) and rec.org == w.org_a
        # C3: one record event, after the commit, in the run's org.
        assert [(t, p["organization_id"], p["record_id"]) for t, p in w.events] == [
            ("studio.record.created", w.org_a, str(rec.id))]
        # A second scan finds nothing to settle and writes nothing.
        assert _scan(w) == 0 and _records(w, w.obj_a) == 1

    def test_with_no_approval_nothing_is_written(self, w):
        run_id, _made = _park(w)
        assert _scan(w) == 0
        assert _run(w, run_id).status == "paused"
        assert _records(w, w.obj_a) == 0 and _audit(w) == [] and w.events == []

    def test_a_rejected_decision_writes_nothing(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "no")
        assert _scan(w) == 1
        assert tuple(_run(w, run_id))[:2] == ("cancelled", park.DECLINED)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []

    def test_an_expired_decision_writes_nothing(self, w):
        run_id, made = _park(w)
        _expire(w, str(made.id))
        assert _scan(w) == 1
        assert tuple(_run(w, run_id))[:2] == ("cancelled", park.EXPIRED)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []

    # ── The org wall ────────────────────────────────────────────────────

    def test_a_run_of_org_a_cannot_write_a_package_of_org_b(self, w):
        run_id, made = _park(w, "A writes into B")
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        assert _scan(w) == 1
        assert _run(w, run_id).status == "failed"
        assert f"({invoke.WORKFLOW_NO_PACKAGE})" in _error_of(w, run_id)
        assert _records(w, w.obj_b) == 0 and _records(w, w.obj_a) == 0
        # A 404 at the package read writes no audit row.
        assert _audit(w) == [] and w.events == []

    def test_a_run_of_org_b_writes_in_org_b_only(self, w):
        run_id, made = _park(w, "Write in B")
        _answer(w, w.org_b, OWNER_B, str(made.id), "yes")
        assert _scan(w) == 1
        assert _run(w, run_id).status == "succeeded", _error_of(w, run_id)
        assert (_records(w, w.obj_b), _records(w, w.obj_a)) == (1, 0)
        assert [(a["org"], a["user"]) for a in _audit(w)] == [(w.org_b, OWNER_B)]

    # ── No double write ─────────────────────────────────────────────────

    def test_a_replayed_resume_writes_once(self, w):
        run_id, first = _park(w, "Write twice gated in A")
        _answer(w, w.org_a, OWNER_A, str(first.id), "yes")
        assert _scan(w) == 1
        assert _run(w, run_id).status == "paused"
        assert _records(w, w.obj_a) == 1
        second = _decisions_of(w, run_id)[-1]
        assert second.payload["node_id"] == "gate2"
        _answer(w, w.org_a, OWNER_A, str(second.id), "yes")
        assert _scan(w) == 1
        run = _run(w, run_id)
        assert run.status == "succeeded", run.error
        results = run.node_results if isinstance(run.node_results, dict) else json.loads(
            run.node_results)
        assert results["write"]["cached"] is True
        assert _records(w, w.obj_a) == 1
        assert [a["outcome"] for a in _audit(w)] == ["ran"]

    def test_two_concurrent_scans_write_once(self, w, monkeypatch):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        _race(monkeypatch)

        async def scans() -> list[int]:
            return list(await asyncio.gather(scheduler.scan_decided_pauses(w.org_a),
                                             scheduler.scan_decided_pauses(w.org_a)))

        assert sorted(_on_app_role(w, scans)) == [0, 1]
        assert _run(w, run_id).status == "succeeded"
        assert _records(w, w.obj_a) == 1 and len(_audit(w)) == 1

    def test_a_crashed_run_is_never_resumed_so_it_never_writes_again(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        assert _scan(w) == 1 and _records(w, w.obj_a) == 1
        # A process that died mid-run leaves the row `running`.
        with w.admin_engine.begin() as c:
            c.execute(text("UPDATE workflow_runs SET status = 'running', finished_at = NULL "
                           "WHERE id = CAST(:r AS uuid)"), {"r": run_id})
        assert _on_app_role(w, service.reconcile_orphaned_runs) == 1
        assert _run(w, run_id).status == "failed"
        assert _scan(w) == 0
        assert _records(w, w.obj_a) == 1

    # ── The refusals ────────────────────────────────────────────────────

    def test_the_broker_door_resume_writes_nothing(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        door = _on_app_role(w, lambda: service.resume_run(
            run_id, resumed_by="approvals-inbox", organization_id=w.org_a))
        assert door.get("ok") is True
        assert _run(w, run_id).status == "failed"
        assert f"({service.STUDIO_NO_DECISION})" in _error_of(w, run_id)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []

    def test_a_manual_run_writes_nothing(self, w):
        run_id = _start(w, "Write in A", kind="manual")
        # A person is present, so the gate takes the broker door.
        assert _run(w, run_id).status == "paused"
        assert [p.action for p in w.proposals] == ["workflow.resume_run"]
        _on_app_role(w, lambda: service.resume_run(
            run_id, resumed_by=OWNER_A, organization_id=w.org_a))
        assert _run(w, run_id).status == "failed"
        assert f"({service.STUDIO_NOT_SCHEDULED})" in _error_of(w, run_id)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []

    def test_a_run_with_no_org_refuses(self, w):
        _wid, _org, serialized = w.wf["Write in A"]
        writer = service._studio_writer(
            run_id=str(uuid.uuid4()), workflow_id=w.wf["Write in A"][0],
            organization_id=None, trigger_kind="schedule", serialized=serialized,
            parked=service.ParkedApproval(decision_id=str(uuid.uuid4()),
                                          pause_id=str(uuid.uuid4()), gate="gate"))

        async def body() -> Any:
            with no_person_scope():
                return await writer("write", _write_config(w.pkg_a))

        with pytest.raises(NodeExecutionError) as refused:
            _on_app_role(w, body)
        assert f"({service.STUDIO_NO_ORG})" in str(refused.value)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []

    def test_the_studio_switch_off_for_the_org_refuses(self, w, monkeypatch):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        monkeypatch.setattr(get_settings(), "studio_orgs", w.org_b)
        assert _scan(w) == 1
        assert _run(w, run_id).status == "failed"
        assert f"({service.STUDIO_FLAG_OFF})" in _error_of(w, run_id)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []

    def test_an_empty_unattended_list_starts_nothing_and_settles_nothing(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        w.listed = ""
        assert _scan(w) == 0
        assert _run(w, run_id).status == "paused"
        # No schedule run starts for an org that is not listed.
        with w.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO workflow_triggers (workflow_id, kind, config, enabled, "
                "last_fired_at, organization_id) VALUES (CAST(:w AS uuid), 'schedule', "
                "CAST(:c AS jsonb), true, now() - interval '2 days', CAST(:o AS uuid))"),
                {"w": w.wf["Write in A"][0], "c": json.dumps({"cron": "* * * * *"}),
                 "o": w.org_a})
        try:
            assert _on_app_role(w, scheduler._scan_once) == 0
            assert len(_all(w, "SELECT id FROM workflow_runs")) == 1
        finally:
            with w.admin_engine.begin() as c:
                c.execute(text("DELETE FROM workflow_triggers"))
        assert _records(w, w.obj_a) == 0 and _audit(w) == []
        # Listed again, the same answer writes once.
        w.listed = f"{w.org_a},{w.org_b}"
        assert _scan(w) == 1 and _records(w, w.obj_a) == 1

    def test_an_owner_who_left_the_org_writes_nothing(self, w):
        run_id, made = _park(w)
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        with w.admin_engine.begin() as c:
            c.execute(text("UPDATE app_user SET status = 'suspended' WHERE email = :e"),
                      {"e": OWNER_A})
        try:
            assert _scan(w) == 1
        finally:
            with w.admin_engine.begin() as c:
                c.execute(text("UPDATE app_user SET status = 'active' WHERE email = :e"),
                          {"e": OWNER_A})
        assert _run(w, run_id).status == "failed"
        assert f"({service.STUDIO_NOT_APPROVED})" in _error_of(w, run_id)
        assert _records(w, w.obj_a) == 0 and _audit(w) == []
