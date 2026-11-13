"""STU-E8b: one parked decision approves a frozen list, and a stopped run resumes once.

Spec: ``project-docs/specs/ai_studio.md`` §14 E8b (task 3096). Code:
``routes/workflows/batch.py``, ``service._studio_batcher`` and
``service.start_batch_run``, ``scheduler.scan_open_batches``,
``routes/studio/invoke.run_workflow_batch`` and the E8a store
(``gateway/studio/batch_store.py``, migrations 438 and 440).
``test_studio_workflow_batch.py`` holds the hermetic half.

**R8.** The real seams on a dedicated database cloned from the phase-4
template, as a role that is NOT a superuser and has NO BYPASSRLS, two orgs.

Run (a PRIVATE database, because the fixture drops ``<db>_studiobatch``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant
    uv run pytest tests/unit/test_studio_workflow_batch_r8.py -v -rs

Without the database each R8 test SKIPS. A skip is not a pass.

**The mutations (R7).** Each was run on 2026-11-13 against this suite, and
each went red. The report of task 3096 names the tests that failed.

* M1. ``batch_store._REBIND_SQL``: drop ``AND run_id = CAST(:old AS uuid)``.
* M2. ``batch.approved_batch``: drop the check ``outcome_of(...) != RESUME``.
* M3. ``invoke._batch_checked``: skip the compare of the list digest.
* M4. ``scheduler._resume_batch``: drop the bound (``found.resumes >= MAX_RESUMES``).
* M5. ``invoke._batch_drive``: roll back a busy chunk (re-raise ``ChunkBusy``).
* M6. ``batch.park_batch``: drop the check ``org_ready``.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import action_broker.broker as broker
from acb_common import get_settings, studio_query
from gateway.routes.projects import decisions
from gateway.routes.studio import invoke
from gateway.routes.workflows import batch, scheduler, service, unattended
from gateway.routes.workflows.engine.graph import compile_graph
from gateway.studio import objects_store
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_WAIT = 240

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
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_studiobatch"
_APP_PW = "studiobatch_scratch_pw"

OWNER_A = "owner@a.studiobatch.test"
OWNER_B = "owner@b.studiobatch.test"
_FIELDS: list[dict[str, Any]] = [
    {"key": "emp", "type": "text"},
    {"key": "amount", "type": "number"},
]


def _node(nid: str, ntype: str, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": nid, "config": config or {}}}


def _items(n: int) -> list[dict]:
    return [{"key": f"e{i:04d}", "data": {"emp": f"e{i:04d}", "amount": 1000 + i}}
            for i in range(n)]


def _graph(pkg: str, n: int) -> dict:
    """trigger -> gate -> batch -> done."""
    nodes = [_node("start", "trigger", {"kind": "schedule"}),
             _node("gate", "approval"),
             _node("batch", "studio_batch", {"package_id": pkg, "action": "pay",
                                             "items": _items(n)}),
             _node("done", "output", {"value": "end"})]
    pairs = itertools.pairwise(["start", "gate", "batch", "done"])
    return {"nodes": nodes,
            "edges": [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in pairs]}


def _manifest() -> dict[str, Any]:
    return {
        "studio": 1,
        "id": f"bat-{uuid.uuid4().hex[:6]}",
        "name": "Payroll",
        "purpose": "Pay lines of a month.",
        "objects": [{"name": "line", "fields": _FIELDS}, {"name": "line2", "fields": _FIELDS}],
        "actions": [{"id": "pay", "uses": "object:line.create"}],
    }


def _drop_database(admin_url: Any, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


def _grant_app_role(ded_admin_url: Any, dedicated: str) -> None:
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


def _seed_member(conn: Any, org: str, email: str) -> None:
    """An active member who holds ``feature:studio`` through one override."""
    uid = conn.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
        {"e": email, "o": org}).scalar()
    conn.execute(text(
        "INSERT INTO user_permission_override (user_id, permission, effect, reason, set_by, "
        "organization_id) VALUES (:u, 'feature:studio', 'allow', 'E8b R8', 'seed', "
        "CAST(:o AS uuid))"), {"u": uid, "o": org})


def _seed_workflow(conn: Any, org: str, owner: str, name: str, serialized: dict) -> str:
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
    for _ in range(3):
        tasks = [t for t in asyncio.all_tasks()
                 if t.get_name().startswith(("workflow-run:", "workflow-resume:"))]
        if not tasks:
            return
        await asyncio.wait(tasks, timeout=_WAIT)


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


async def _make_package(org: str, member: str) -> tuple[str, str, str]:
    from gateway.db import tenant_session
    from gateway.studio import packages_store

    async with tenant_session(org) as db:
        made = await packages_store.create_package(db, member, _manifest())
        one = await objects_store.define_object(db, member, package_id=made.id, name="line",
                                                fields=_FIELDS)
        two = await objects_store.define_object(db, member, package_id=made.id, name="line2",
                                                fields=_FIELDS)
        assert one is not None and two is not None
    return made.id, one.id, two.id


@pytest.fixture(scope="module")
def world():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role, two orgs, a package
    of each owner, and the workflows that batch into them."""
    from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_studiobatch"
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
        for oid, slug in ((ns.org_a, "studiobatch-a"), (ns.org_b, "studiobatch-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                {"id": oid, "s": slug})
        _seed_member(conn, ns.org_a, OWNER_A)
        _seed_member(conn, ns.org_b, OWNER_B)

    async def packages() -> None:
        ns.pkg_a, ns.obj_a, ns.obj2_a = await _make_package(ns.org_a, OWNER_A)
        ns.pkg_b, ns.obj_b, _ = await _make_package(ns.org_b, OWNER_B)

    _on_app_role(ns, packages)
    with eng.begin() as conn:
        for name, org, owner, serialized in (
            ("Pay run in A", ns.org_a, OWNER_A, compile_graph(_graph(ns.pkg_a, 300))),
            ("Small batch in A", ns.org_a, OWNER_A, compile_graph(_graph(ns.pkg_a, 10))),
            ("Pay run in B", ns.org_b, OWNER_B, compile_graph(_graph(ns.pkg_b, 4))),
        ):
            ns.wf[name] = (_seed_workflow(conn, org, owner, name, serialized), org, serialized)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def w(world, monkeypatch):
    """All five lists on for A and B. No run, decision, record, batch or audit
    row is left from the last test. The broker store and the record event are
    spies, and the waits between busy tries are 0."""
    orgs = f"{world.org_a},{world.org_b}"
    settings = get_settings()
    for key in ("auto_park_orgs", "decisions_orgs", "studio_orgs", "studio_batch_orgs"):
        monkeypatch.setattr(settings, key, orgs)
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    world.listed = orgs
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=world.listed))
    monkeypatch.setattr(invoke, "BATCH_RETRY_WAITS", (0.0, 0.0))
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
        c.execute(text("DELETE FROM workflow_run_pauses"))
        c.execute(text("DELETE FROM pm_decisions"))
        c.execute(text("DELETE FROM pm_notifications"))
        c.execute(text("DELETE FROM pm_tasks WHERE source = 'automation'"))
        c.execute(text("DELETE FROM studio_batches"))
        c.execute(text("DELETE FROM studio_records"))
        c.execute(text("DELETE FROM app_audit"))
        c.execute(text("DELETE FROM pm_agent_runs WHERE app IS NOT NULL"))
    return world


# ── helpers ─────────────────────────────────────────────────────────────────


def _one(w: Any, sql: str, **params: Any) -> Any:
    with w.admin_engine.connect() as c:
        return c.execute(text(sql), params).fetchone()


def _all(w: Any, sql: str, **params: Any) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).fetchall())


def _run(w: Any, run_id: str) -> Any:
    return _one(w, "SELECT status, error, node_results, trigger_kind, started_by "
                   "FROM workflow_runs WHERE id = CAST(:r AS uuid)", r=run_id)


def _runs(w: Any) -> list[Any]:
    return _all(w, "SELECT id::text AS id, status, started_by FROM workflow_runs "
                   "ORDER BY started_at, id")


def _records(w: Any, obj: str) -> list[Any]:
    return _all(w, "SELECT id::text AS id, data FROM studio_records "
                   "WHERE object_id = CAST(:o AS uuid)", o=obj)


def _emps(w: Any, obj: str) -> list[str]:
    return sorted(r.data["emp"] for r in _records(w, obj))


def _batch_row(w: Any) -> Any:
    return _one(w, "SELECT id::text AS id, run_id::text AS run_id, node_id, status, total, "
                   "resumes, list_digest FROM studio_batches")


def _items_of(w: Any, bid: str) -> list[Any]:
    return _all(w, "SELECT item_key, status, error_code FROM studio_batch_items "
                   "WHERE batch_id = CAST(:b AS uuid) ORDER BY position", b=bid)


def _audit(w: Any) -> list[dict]:
    rows = _all(w, "SELECT organization_id::text AS org, user_email, detail FROM app_audit "
                   "ORDER BY id")
    return [{"org": r.org, "user": r.user_email,
             **(r.detail if isinstance(r.detail, dict) else json.loads(r.detail))}
            for r in rows]


def _decisions_of(w: Any, run_id: str) -> list[Any]:
    return _all(w, "SELECT * FROM pm_decisions WHERE payload ->> 'run_id' = :r "
                   "ORDER BY created_at", r=run_id)


def _start(w: Any, name: str) -> str:
    wid, org, serialized = w.wf[name]

    async def body() -> str:
        return await service.start_run(
            workflow_id=wid, workflow_name=name, version=1, serialized=serialized,
            trigger_kind="schedule", trigger_payload={"scheduled_at": "x"},
            started_by="scheduler", organization_id=org)

    return _on_app_role(w, body)


def _park(w: Any, name: str = "Pay run in A") -> tuple[str, Any]:
    run_id = _start(w, name)
    assert _run(w, run_id).status == "paused", _run(w, run_id).error
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


def _decide(w: Any, org: str) -> int:
    """The decided scan alone. A full pass would run the batch scan next, and
    that scan could resume a run that failed fast in the same pass."""
    return _on_app_role(w, lambda: scheduler.scan_decided_pauses(org))


def _kill_in_chunk(monkeypatch, chunk: int) -> dict[str, int]:
    """The process dies in chunk *chunk*: that chunk raises after its writes,
    so its transaction rolls back whole, as a kill leaves it."""
    from gateway.studio import batch_store

    real = batch_store.run_chunk
    seen = {"calls": 0}

    async def dying(*a: Any, **k: Any) -> Any:
        seen["calls"] += 1
        out = await real(*a, **k)
        if seen["calls"] == chunk:
            raise RuntimeError("the process died")
        return out

    monkeypatch.setattr(batch_store, "run_chunk", dying)
    return seen


def _stop_after_two_chunks(w: Any, monkeypatch, name: str = "Pay run in A") -> tuple[str, Any]:
    """Park, answer yes, run, and die in chunk 3 at item 120 of 300. The row is
    left ``running`` with the node results of the park, as a dead process
    leaves it, and the startup sweep then fails it."""
    from gateway.studio import batch_store

    run_id, made = _park(w, name)
    parked_results = _run(w, run_id).node_results
    _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
    real = batch_store.run_chunk
    _kill_in_chunk(monkeypatch, 3)
    assert _decide(w, w.org_a) == 1
    monkeypatch.setattr(batch_store, "run_chunk", real)
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE workflow_runs SET status = 'running', error = NULL, finished_at = NULL, "
            "node_results = CAST(:nr AS jsonb) WHERE id = CAST(:r AS uuid)"),
            {"r": run_id, "nr": json.dumps(parked_results)})
    assert _on_app_role(w, service.reconcile_orphaned_runs) == 1
    assert _run(w, run_id).status == "failed"
    return run_id, made


def _race(monkeypatch, parties: int = 2) -> None:
    """Hold each racer at its UPDATE of the batch until all of them came, so
    the outcome is the compare-and-set alone."""
    state: dict[str, Any] = {"barrier": None}

    class _Held:
        def __init__(self, db: Any) -> None:
            self._db = db

        async def execute(self, clause: Any, *a: Any, **k: Any) -> Any:
            sql = " ".join(str(clause).split()).upper()
            if sql.startswith("UPDATE STUDIO_BATCHES SET RUN_ID"):
                if state["barrier"] is None:
                    state["barrier"] = asyncio.Barrier(parties)
                await asyncio.wait_for(state["barrier"].wait(), 60)
            return await self._db.execute(clause, *a, **k)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._db, name)

    real_run = service._run_session

    @contextlib.asynccontextmanager
    async def run_session(org: str | None):
        async with real_run(org) as db:
            yield _Held(db)

    monkeypatch.setattr(service, "_run_session", run_session)


# ══ R8 ══════════════════════════════════════════════════════════════════════


def test_r8_the_engine_cannot_bypass_rls(w):
    eng = create_engine(w.app_dsn, future=True)
    try:
        with eng.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
            forced = c.execute(text(
                "SELECT count(*) FROM pg_class WHERE relname IN ('studio_batches', "
                "'studio_batch_items', 'workflow_runs', 'pm_decisions', 'studio_records') "
                "AND relrowsecurity AND relforcerowsecurity")).scalar()
    finally:
        eng.dispose()
    assert role is not None and not role[0] and not role[1]
    assert forced == 5


def test_r8_one_approval_writes_300_records_once_and_opens_no_card(w):
    run_id, made = _park(w)
    row = _batch_row(w)
    assert (row.run_id, row.node_id, row.status, row.total, row.resumes) == (
        run_id, "batch", "open", 300, 0)
    payload = made.payload
    assert (payload[batch.BATCH_ID], payload[batch.LIST_DIGEST]) == (row.id, row.list_digest)
    assert made.question == 'Let "Pay run in A" add 300 records of line in "Payroll"?'
    assert _records(w, w.obj_a) == []
    _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
    assert _scan(w) == 1
    run = _run(w, run_id)
    assert run.status == "succeeded", run.error
    assert _emps(w, w.obj_a) == [f"e{i:04d}" for i in range(300)]
    assert _batch_row(w).status == "done"
    # ONE approval: one decision, no broker proposal and no request card.
    assert len(_all(w, "SELECT id FROM pm_decisions")) == 1
    assert w.proposals == []
    assert _all(w, "SELECT id FROM pm_agent_runs WHERE app IS NOT NULL") == []
    audit = _audit(w)
    assert len(audit) == 6
    assert {(a["org"], a["user"], a["outcome"], a["via"], a["batch_id"]) for a in audit} == {
        (w.org_a, OWNER_A, "ran", invoke.WORKFLOW_VIA, row.id)}
    assert sum(a["written"] for a in audit) == 300
    assert {a["decision_id"] for a in audit} == {str(made.id)}
    # C3: one event for each record, after the commit, in the run's org.
    assert len(w.events) == 300
    assert {p["organization_id"] for _t, p in w.events} == {w.org_a}
    # A second scan of a done batch starts no run.
    assert _scan(w) == 0
    assert len(_runs(w)) == 1 and len(_records(w, w.obj_a)) == 300


def test_r8_a_stop_after_chunk_2_resumes_once_and_repeats_none(w, monkeypatch):
    """The acceptance of E8b: a kill at item 120 of 300, a boot, then the scan,
    ends with 300 records, one for each key, through ONE new run."""
    run_id, _made = _stop_after_two_chunks(w, monkeypatch)
    assert len(_records(w, w.obj_a)) == 100
    assert [r.status for r in _items_of(w, _batch_row(w).id)].count("pending") == 200
    assert _scan(w) == 1
    runs = _runs(w)
    assert len(runs) == 2
    new = next(r for r in runs if r.id != run_id)
    assert (new.status, new.started_by) == ("succeeded", batch.STARTED_BY), _run(w, new.id).error
    assert _run(w, new.id).trigger_kind == "schedule"
    row = _batch_row(w)
    assert (row.run_id, row.status, row.resumes) == (new.id, "done", 1)
    emps = [r.data["emp"] for r in _records(w, w.obj_a)]
    assert len(emps) == 300 and len(set(emps)) == 300
    # The new run started AT the batch: the nodes before it passed through.
    results = _run(w, new.id).node_results
    results = results if isinstance(results, dict) else json.loads(results)
    assert results["start"].get("cached") is True
    assert results["batch"]["status"] == "ok" and results["done"]["status"] == "ok"
    assert results["batch"]["output"]["written"] == 200
    # The new run read the same decision, and asked no new one.
    assert len(_all(w, "SELECT id FROM pm_decisions")) == 1
    assert w.proposals == []
    assert _scan(w) == 0 and len(_runs(w)) == 2


def test_r8_two_scan_passes_start_one_run(w, monkeypatch):
    _stop = _stop_after_two_chunks(w, monkeypatch)
    _race(monkeypatch)

    async def scans() -> list[int]:
        return list(await asyncio.gather(scheduler.scan_open_batches(w.org_a),
                                         scheduler.scan_open_batches(w.org_a)))

    assert sorted(_on_app_role(w, scans)) == [0, 1]
    assert len(_runs(w)) == 2
    assert _batch_row(w).resumes == 1
    emps = [r.data["emp"] for r in _records(w, w.obj_a)]
    assert len(emps) == 300 and len(set(emps)) == 300


def test_r8_a_failed_run_with_no_yes_is_never_resumed(w):
    run_id, made = _park(w)
    # A path that sets a parked run failed with no answer.
    with w.admin_engine.begin() as c:
        c.execute(text("UPDATE workflow_runs SET status = 'failed' WHERE id = CAST(:r AS uuid)"),
                  {"r": run_id})
    assert _scan(w) == 0
    assert len(_runs(w)) == 1 and _records(w, w.obj_a) == []
    # A no ends nothing more: the run was failed, and still nothing resumes.
    _answer(w, w.org_a, OWNER_A, str(made.id), "no")
    assert _scan(w) == 0
    assert len(_runs(w)) == 1 and _records(w, w.obj_a) == []
    assert _batch_row(w).status == "open"


def test_r8_a_declined_park_writes_nothing_and_starts_nothing(w):
    run_id, made = _park(w)
    _answer(w, w.org_a, OWNER_A, str(made.id), "no")
    assert _scan(w) == 1
    assert _run(w, run_id).status == "cancelled"
    assert _scan(w) == 0
    assert len(_runs(w)) == 1 and _records(w, w.obj_a) == []


def test_r8_a_changed_list_writes_nothing_and_ends_the_batch(w):
    run_id, made = _park(w)
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE workflow_run_pauses SET snapshot = jsonb_set(snapshot, "
            "'{serialized,blocks,2,config,items,0,data,amount}', '999999') "
            "WHERE run_id = CAST(:r AS uuid)"), {"r": run_id})
    _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
    assert _scan(w) == 1
    run = _run(w, run_id)
    assert run.status == "failed" and f"({invoke.WORKFLOW_MISMATCH})" in run.error
    assert _records(w, w.obj_a) == [] and w.events == []
    row = _batch_row(w)
    assert row.status == "failed"
    items = _items_of(w, row.id)
    assert (items[0].status, items[0].error_code) == ("failed", "write_mismatch")
    assert [i.status for i in items[1:]] == ["pending"] * 299
    assert [(a["outcome"], a["reason"]) for a in _audit(w)] == [
        ("refused", invoke.WORKFLOW_MISMATCH)]
    # The batch is not open, so no scan starts it again.
    assert _scan(w) == 0 and len(_runs(w)) == 1


def test_r8_a_changed_package_after_the_park_writes_nothing(w):
    """The action now names another object, so each write of the list
    differs from the frozen writes."""
    run_id, made = _park(w, "Small batch in A")
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE apps SET manifest = jsonb_set(manifest, '{actions,0,uses}', "
            "'\"object:line2.create\"') WHERE id = CAST(:p AS uuid)"), {"p": w.pkg_a})
    try:
        _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
        assert _scan(w) == 1
    finally:
        with w.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE apps SET manifest = jsonb_set(manifest, '{actions,0,uses}', "
                "'\"object:line.create\"') WHERE id = CAST(:p AS uuid)"), {"p": w.pkg_a})
    run = _run(w, run_id)
    assert run.status == "failed" and f"({invoke.WORKFLOW_MISMATCH})" in run.error
    assert _records(w, w.obj_a) == [] and _records(w, w.obj2_a) == []


def test_r8_the_door_refuses_a_digest_that_is_not_the_frozen_one(w):
    run_id, _made = _park(w, "Small batch in A")
    row = _batch_row(w)

    async def body() -> dict:
        return await invoke.run_workflow_batch(
            organization_id=w.org_a, member=OWNER_A, package_id=w.pkg_a, action="pay",
            items=_items(10), run_id=run_id, node_id="batch", batch_id=row.id,
            expected_digest="0" * 64, decision_id=str(uuid.uuid4()))

    got = _on_app_role(w, body)
    assert (got["ok"], got["code"]) == (False, invoke.WORKFLOW_MISMATCH)
    assert _records(w, w.obj_a) == []


def test_r8_an_org_off_the_batch_flag_opens_no_batch_and_files_no_card(w, monkeypatch):
    monkeypatch.setattr(get_settings(), "studio_batch_orgs", w.org_b)
    run_id = _start(w, "Small batch in A")
    run = _run(w, run_id)
    assert (run.status, run.error) == ("failed", batch.BATCH_OFF_ERROR)
    assert _all(w, "SELECT id FROM studio_batches") == []
    assert _decisions_of(w, run_id) == [] and w.proposals == []
    assert _all(w, "SELECT id FROM workflow_run_pauses") == []


def test_r8_a_stopped_batch_of_an_org_off_the_lists_starts_nothing(w, monkeypatch):
    _stop_after_two_chunks(w, monkeypatch)
    monkeypatch.setattr(get_settings(), "studio_batch_orgs", w.org_b)
    assert _scan(w) == 0
    monkeypatch.setattr(get_settings(), "studio_batch_orgs", f"{w.org_a},{w.org_b}")
    w.listed = w.org_b
    assert _scan(w) == 0
    assert len(_runs(w)) == 1 and len(_records(w, w.obj_a)) == 100
    # Listed again, the same batch resumes once.
    w.listed = f"{w.org_a},{w.org_b}"
    assert _scan(w) == 1 and len(_records(w, w.obj_a)) == 300


def test_r8_org_b_never_resumes_a_batch_of_org_a(w, monkeypatch):
    _stop_after_two_chunks(w, monkeypatch)
    assert _on_app_role(w, lambda: scheduler.scan_open_batches(w.org_b)) == 0

    async def peek() -> list[Any]:
        from gateway.db import tenant_session

        async with tenant_session(w.org_b) as db:
            return list((await db.execute(text(batch.OPEN_SQL), {"org": w.org_a})).fetchall())

    assert _on_app_role(w, peek) == []
    assert len(_runs(w)) == 1 and len(_records(w, w.obj_a)) == 100
    assert _records(w, w.obj_b) == []


def test_r8_an_item_that_always_times_out_ends_failed_after_the_bound(w, monkeypatch):
    """Item 5 of 10 times out at each try. Each run commits the items before it,
    then fails busy. The scan starts MAX_RESUMES new runs, and then it ends the
    item that blocks with query_timeout, and the batch ends failed."""
    real = objects_store.apply_writes

    async def slow_on_five(db: Any, actor: str, *, package_id: str, writes: Any,
                           **kw: Any) -> Any:
        if any((x.data or {}).get("emp") == "e0005" for x in writes):
            raise objects_store.BatchError(
                0, studio_query.QueryError(studio_query.TIMEOUT, "too slow"))
        return await real(db, actor, package_id=package_id, writes=writes, **kw)

    monkeypatch.setattr(objects_store, "apply_writes", slow_on_five)
    run_id, made = _park(w, "Small batch in A")
    _answer(w, w.org_a, OWNER_A, str(made.id), "yes")
    assert _decide(w, w.org_a) == 1
    first = _run(w, run_id)
    assert first.status == "failed" and f"({invoke.STORE_BUSY})" in first.error
    # The busy chunk kept its earlier groups: items 0 to 4.
    assert _emps(w, w.obj_a) == [f"e{i:04d}" for i in range(5)]
    for n in range(1, batch.MAX_RESUMES + 1):
        assert _scan(w) == 1, n
        assert _batch_row(w).resumes == n
    assert len(_runs(w)) == 1 + batch.MAX_RESUMES
    assert all(r.status == "failed" for r in _runs(w))
    assert _scan(w) == 0
    row = _batch_row(w)
    assert row.status == "failed"
    items = _items_of(w, row.id)
    assert [(i.item_key, i.status, i.error_code) for i in items[4:7]] == [
        ("e0004", "done", None), ("e0005", "failed", "query_timeout"), ("e0006", "pending", None)]
    assert _emps(w, w.obj_a) == [f"e{i:04d}" for i in range(5)]
    assert _scan(w) == 0 and len(_runs(w)) == 1 + batch.MAX_RESUMES


def test_r8_a_run_of_org_b_writes_in_org_b_only(w):
    run_id, made = _park(w, "Pay run in B")
    _answer(w, w.org_b, OWNER_B, str(made.id), "yes")
    assert _scan(w) == 1
    assert _run(w, run_id).status == "succeeded", _run(w, run_id).error
    assert (len(_records(w, w.obj_b)), len(_records(w, w.obj_a))) == (4, 0)
    assert {(a["org"], a["user"]) for a in _audit(w)} == {(w.org_b, OWNER_B)}
