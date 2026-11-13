"""STU-E9a: the record timer and its breach run on a REAL database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 row E9a (task 2376). Code:
``routes/workflows/scheduler.scan_due_timers`` (called by the org loop of
``scan_due_waits``), ``studio/objects_store.timer_of`` and
``due_timer_records``, the rule kind ``timer`` of ``acb_common/studio_rules.py``
and the delivery key of ``service.start_run`` (migration 420). The hermetic
half is ``test_workflows_timer_scan.py``.

The world is a dedicated copy of the promoted catalog, as a role that is NOT a
superuser, NOT the owner and has NO BYPASSRLS. Two orgs. Each owner has a
private Studio package with the object ``claim``: a hot datetime ``due``, a hot
select ``state`` and the timer ``sla`` that pauses while ``state`` is
``waiting``. The records go through the real write path of the store.

**The fences, by name.**

* T1. A breach starts ONE run for each listening workflow, also after three
  passes and two scans at once: ``test_t1_*``.
* T2. A paused record starts no run, and starts one at the first scan after it
  leaves the pause: ``test_t2_*``.
* T3. A new due value starts one new run: ``test_t3_*``.
* T4. The first scan arms a trigger. A record due before the arm time starts
  no run, and one due after it does: ``test_t4_*``.
* T5. A record of org B never starts a workflow of org A: ``test_t5_*``.
* T6. An org that is not on both lists reads no trigger and starts nothing:
  ``test_t6_*``.
* T7. An author who cannot read the package starts no run, and a workflow with
  a task node starts none: ``test_t7_*``.
* T8. The demo path: the run reaches a human step, and the manager gets ONE
  task: ``test_t8_*``.
* T9. A fired record never holds the head of the list: ``test_t9_*``.
* T10. The write path refuses nothing for a timer, and a timer keeps its
  fields hot: ``test_t10_*``.
* T11. The scan holds one session at a time: ``test_t11_*``.
* T12. A record event never starts a timer trigger: ``test_t12_*``.

**Mutations this suite catches** (each was run before the commit):

* M1. ``scan_due_timers`` passes no ``delivery_key``: T1 red (two runs after
  the race, and one more run on each pass).
* M2. ``_TIMER_DUE_SQL`` drops ``h.v_ts > :armed``: T4 red.
* M3. ``_scan_studio_timers`` calls the timer scan without the
  ``studio_allowed(org)`` check: T6 red.
* M4. ``_TIMER_DUE_SQL`` drops the ``NOT EXISTS`` on ``workflow_runs``: T9 red.
* M5. ``_TIMER_DUE_SQL`` drops the pause clause: T2 red.
* M6. ``objects_store._check_records`` drops the skip of a timer: T10 red.
* M7. ``triggers.event_trigger_matches`` drops the breach guard: T12 red.
* M8. ``scheduler._timer_breaches`` drops the author check: T7 red. T5 stays
  green, because the read of org A cannot see a package of org B (a second
  wall).
* M9. ``scan_due_waits`` drops ``unattended.org_allowed``: T6 red.

Run (a PRIVATE database, because the fixture drops ``<db>_wftimer``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_workflows_timer_r8.py -v -rs

Without the database each test SKIPS. A skip is not a pass.
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

from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from acb_common.studio_access import Viewer
from gateway.routes.workflows import scheduler, triggers, unattended
from gateway.routes.workflows.engine.graph import compile_graph
from gateway.routes.workflows.service import DuplicateDelivery
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

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

#: Every bound on a wait. Broken code goes red and never hangs.
_WAIT = 60

_APP_ROLE = "acb_app_wftimer"
_APP_PW = "wftimer_scratch_pw"
_SUFFIX = "_wftimer"

OWNER_A = "owner@a.wftimer.test"
MANAGER_A = "manager@a.wftimer.test"
M2_A = "m2@a.wftimer.test"
OWNER_B = "owner@b.wftimer.test"

_FIELDS: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "due", "type": "datetime", "hot": True},
    {"key": "state", "type": "select", "options": ["open", "waiting", "closed"], "hot": True},
]
_TIMER_RULE = {"name": "sla", "kind": "timer", "field": "due",
               "pause": {"field": "state", "in": ["waiting"]}}
#: The payload of a breach run: ids and names, never a field value.
_PAYLOAD_KEYS = {"source", "event_type", "package_id", "object", "record_id", "rule", "due_at"}


def _node(nid: str, ntype: str, label: str | None = None, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {"label": label or nid, "config": config or {}}}


def _chain(*middle: dict) -> dict:
    nodes = [_node("start", "trigger", config={"kind": "manual"}), *middle,
             _node("done", "output", config={"value": "end"})]
    ids = [n["id"] for n in nodes]
    edges = [{"id": f"{a}->{b}", "source": a, "target": b} for a, b in itertools.pairwise(ids)]
    return compile_graph({"nodes": nodes, "edges": edges})


_TRIVIAL = _chain()
_STEP = _chain(_node("step", "human_step", "Chase the claim",
                     {"assignee": MANAGER_A, "title": "Chase the overdue claim"}))
_TASK_NODE = _chain(_node("step", "pm_task", config={"task_id": "{{trigger.record_id}}",
                                                     "fields": {"title": "x"}}))

#: name -> (org tag, author, run-model, package tag of the trigger).
_WORKFLOWS: dict[str, tuple[str, str, dict, str]] = {
    "Listen A": ("A", OWNER_A, _TRIVIAL, "A"),
    "Listen A 2": ("A", OWNER_A, _TRIVIAL, "A"),
    "Step A": ("A", OWNER_A, _STEP, "A"),
    "M2 A": ("A", M2_A, _TRIVIAL, "A"),
    "Task node A": ("A", OWNER_A, _TASK_NODE, "A"),
    "A on B": ("A", OWNER_A, _TRIVIAL, "B"),
    "Listen B": ("B", OWNER_B, _TRIVIAL, "B"),
}


def _manifest() -> dict[str, Any]:
    """A2 checks the timer of the object too (a manifest rule)."""
    return {
        "studio": 1, "id": f"tmr-{uuid.uuid4().hex[:6]}", "name": "Claims",
        "purpose": "Chase late claims.",
        "objects": [{"name": "claim", "fields": _FIELDS, "rules": [_TIMER_RULE]},
                    {"name": "ticket", "fields": _FIELDS, "rules": [_TIMER_RULE]}],
        "actions": [{"id": "add", "uses": "object:claim.create"}],
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


def _seed_member(conn, org: str, email: str, *, studio: bool = True) -> None:
    """An active member. *studio* adds ``feature:studio`` by one override."""
    uid = conn.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
        {"e": email, "o": org}).scalar()
    if studio:
        conn.execute(text(
            "INSERT INTO user_permission_override (user_id, permission, effect, reason, "
            "set_by, organization_id) VALUES (:u, 'feature:studio', 'allow', 'E9a R8', "
            "'seed', CAST(:o AS uuid))"), {"u": uid, "o": org})


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


def _on_app_role(w: Any, body: Any, ambient: str | None = None) -> Any:
    """Run *body* on the app-role engine. No tenant is bound unless *ambient*
    names one: an unattended scan has none."""
    from tests.unit._tenant_ladder import tenant_engine_scope

    async def _go() -> Any:
        async with tenant_engine_scope(w.app_dsn):
            token = bind_tenant(ambient) if ambient else None
            try:
                return await asyncio.wait_for(body(), _WAIT)
            finally:
                await _drain_runs()
                if token is not None:
                    release_tenant(token)

    return asyncio.run(_go())


async def _make_package(org: str, member: str) -> SimpleNamespace:
    from gateway.db import tenant_session
    from gateway.studio import objects_store, packages_store

    async with tenant_session(org) as db:
        made = await packages_store.create_package(db, member, _manifest())
        out = SimpleNamespace(id=made.id)
        for name in ("claim", "ticket"):
            obj = await objects_store.define_object(db, member, package_id=made.id,
                                                    name=name, fields=_FIELDS)
            assert obj is not None
            await objects_store.set_constraints(db, obj.id, [_TIMER_RULE], package_id=made.id)
            setattr(out, name, obj.id)
    return out


@pytest.fixture(scope="module")
def world():
    """A dedicated phase-4 catalog, a NOBYPASSRLS role, two orgs, a package of
    each owner, and the workflows that listen to the timer."""
    from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}{_SUFFIX}"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    _grant_app_role(ded_admin_url, dedicated)
    ns = SimpleNamespace(admin_engine=eng, wf={})
    ns.org = {"A": str(uuid.uuid4()), "B": str(uuid.uuid4())}
    ns.owner = {"A": OWNER_A, "B": OWNER_B}
    ns.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    with eng.begin() as conn:
        for tag, oid in ns.org.items():
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) VALUES (:id, :s, :s)"),
                {"id": oid, "s": f"wftimer-{tag.lower()}"})
        _seed_member(conn, ns.org["A"], OWNER_A)
        _seed_member(conn, ns.org["A"], M2_A)
        _seed_member(conn, ns.org["A"], MANAGER_A, studio=False)
        _seed_member(conn, ns.org["B"], OWNER_B)

    async def packages() -> None:
        ns.pkg = {"A": await _make_package(ns.org["A"], OWNER_A),
                  "B": await _make_package(ns.org["B"], OWNER_B)}

    _on_app_role(ns, packages)
    with eng.begin() as conn:
        for name, (tag, author, serialized, _pkg) in _WORKFLOWS.items():
            ns.wf[name] = _seed_workflow(conn, ns.org[tag], author, name, serialized)
    yield ns
    eng.dispose()
    _drop_database(admin_url, dedicated)


def _list(w: Any, *, unattended_tags: str = "AB", studio_tags: str = "AB") -> None:
    w.unattended_orgs = ",".join(w.org[t] for t in unattended_tags)
    w.settings.studio_orgs = ",".join(w.org[t] for t in studio_tags)


@pytest.fixture
def w(world, monkeypatch):
    """Both orgs on both lists. No run, task, record or trigger is left from
    the last test, and each test starts at the first trigger page."""
    from gateway.package_host import flags as host_flags

    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    world.settings = get_settings()
    monkeypatch.setattr(world.settings, "studio_orgs", "")
    # The viewer path of the read rule stays shut: only the owner reads.
    monkeypatch.setattr(host_flags, "package_host_orgs", lambda: frozenset())
    world.unattended_orgs = ""
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=world.unattended_orgs))
    _list(world)
    scheduler._timer_cursor.clear()
    with world.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflow_runs"))
        c.execute(text("DELETE FROM pm_tasks WHERE source = 'automation'"))
        c.execute(text("DELETE FROM studio_records"))
        c.execute(text("DELETE FROM workflow_triggers WHERE workflow_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(world.wf.values())})
    return world


def _config(w: Any, pkg_tag: str, obj: str = "claim", rule: str = "sla") -> dict:
    return {"source": "studio", "event_type": "studio.record.breached",
            "filter": {"package_id": w.pkg[pkg_tag].id, "object": obj, "op": "breach",
                       "timer": rule}}


def _listen(w: Any, *names: str, armed: str | None = "1 hour") -> None:
    """A timer trigger for each of *names*. *armed* is how long ago the
    trigger was armed, by the DB clock. None leaves it for the scan to arm."""
    with w.admin_engine.begin() as c:
        for name in names:
            tag, _author, _graph, pkg_tag = _WORKFLOWS[name]
            c.execute(text(
                "INSERT INTO workflow_triggers (workflow_id, kind, config, enabled, "
                "last_fired_at, organization_id) VALUES (CAST(:wid AS uuid), 'event', "
                "CAST(:cfg AS jsonb), true, CASE WHEN CAST(:ago AS text) IS NULL THEN NULL "
                "ELSE now() - CAST(:ago AS interval) END, CAST(:org AS uuid))"),
                {"wid": w.wf[name], "cfg": json.dumps(_config(w, pkg_tag)), "ago": armed,
                 "org": w.org[tag]})


def _db_iso(w: Any, offset: str) -> str:
    """The DB clock plus *offset*, as the UTC datetime value of a record."""
    with w.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT to_char((now() + CAST(:o AS interval)) AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"), {"o": offset}).scalar())


def _viewer(tag: str) -> Viewer:
    return Viewer({"A": OWNER_A, "B": OWNER_B}[tag])


def _create(w: Any, tag: str, *, due: str, state: str = "open", obj: str = "claim") -> str:
    """One record, through the write path of the store, as the owner."""
    from gateway.db import tenant_session
    from gateway.studio import objects_store

    pkg = w.pkg[tag]

    async def body() -> str:
        async with tenant_session(w.org[tag]) as db:
            rec = await objects_store.create_record(
                db, w.owner[tag], getattr(pkg, obj),
                {"title": "Late claim", "due": due, "state": state},
                package_id=pkg.id, viewer=_viewer(tag))
            assert rec is not None
            return rec.id

    return _on_app_role(w, body)


def _update(w: Any, tag: str, record_id: str, **changes: Any) -> None:
    from gateway.db import tenant_session
    from gateway.studio import objects_store

    pkg = w.pkg[tag]

    async def body() -> None:
        async with tenant_session(w.org[tag]) as db:
            rec = await objects_store.update_record(
                db, w.owner[tag], pkg.claim, record_id, changes, package_id=pkg.id,
                viewer=_viewer(tag))
            assert rec is not None

    _on_app_role(w, body)


def _scan(w: Any) -> int:
    """One pass of the real wait scan: its org loop calls the timer scan."""
    return _on_app_role(w, lambda: scheduler.scan_due_waits())


def _runs(w: Any, name: str) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT id::text AS id, status, trigger_kind, started_by, delivery_key, "
            "trigger_payload, CAST(organization_id AS text) AS org FROM workflow_runs "
            "WHERE workflow_id = CAST(:w AS uuid) ORDER BY started_at"),
            {"w": w.wf[name]}).fetchall())


def _payload(row: Any) -> dict:
    return row.trigger_payload if isinstance(row.trigger_payload, dict) else json.loads(
        row.trigger_payload)


def _armed_at(w: Any, name: str) -> Any:
    with w.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT last_fired_at FROM workflow_triggers WHERE workflow_id = CAST(:w AS uuid)"),
            {"w": w.wf[name]}).scalar()


# ── T1: one run for each breach and each listening workflow ─────────────────


def test_t1_a_breach_starts_one_run_for_each_listening_workflow(w) -> None:
    _listen(w, "Listen A", "Listen A 2")
    due = _db_iso(w, "-1 minute")
    rid = _create(w, "A", due=due)
    _scan(w)
    for name in ("Listen A", "Listen A 2"):
        (run,) = _runs(w, name)
        assert (run.status, run.trigger_kind, run.started_by, run.org) == (
            "succeeded", "event", "scheduler:timer", w.org["A"])
        payload = _payload(run)
        assert set(payload) == _PAYLOAD_KEYS, payload
        assert payload["event_type"] == "studio.record.breached"
        assert (payload["record_id"], payload["rule"], payload["object"]) == (rid, "sla", "claim")
        assert payload["package_id"] == w.pkg["A"].id
        assert payload["due_at"] == due
        assert run.delivery_key == f"timer:sla:{rid}:{due}"
        assert "Late claim" not in json.dumps(payload)  # no field value leaves the store


def test_t1_three_passes_start_no_second_run(w) -> None:
    _listen(w, "Listen A")
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    for _ in range(3):
        _scan(w)
    assert len(_runs(w, "Listen A")) == 1


def test_t1_two_scans_at_once_start_one_run(w, monkeypatch) -> None:
    """Each scan reads the breach before either starts it: a barrier holds
    each at its first start. So the outcome is the unique delivery key alone,
    never the order that the event loop chose."""
    _listen(w, "Listen A")
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    real, state = scheduler.start_run, {"barrier": None, "seen": set(), "dups": 0}

    async def start(**kw: Any) -> str:
        task = asyncio.current_task()
        if task not in state["seen"]:
            state["seen"].add(task)
            if state["barrier"] is None:
                state["barrier"] = asyncio.Barrier(2)
            await asyncio.wait_for(state["barrier"].wait(), 30)
        try:
            return await real(**kw)
        except DuplicateDelivery:
            state["dups"] += 1
            raise

    monkeypatch.setattr(scheduler, "start_run", start)

    async def both() -> None:
        await asyncio.gather(scheduler.scan_due_waits(), scheduler.scan_due_waits())

    _on_app_role(w, both)
    assert len(_runs(w, "Listen A")) == 1
    assert state["dups"] == 1 and len(state["seen"]) == 2, state


# ── T2: the pause ───────────────────────────────────────────────────────────


def test_t2_a_paused_record_starts_no_run_until_it_leaves_the_pause(w) -> None:
    _listen(w, "Listen A")
    rid = _create(w, "A", due=_db_iso(w, "-1 minute"), state="waiting")
    _scan(w)
    _scan(w)
    assert _runs(w, "Listen A") == []
    _update(w, "A", rid, state="open")
    _scan(w)
    (run,) = _runs(w, "Listen A")
    assert _payload(run)["record_id"] == rid
    _scan(w)
    assert len(_runs(w, "Listen A")) == 1


def test_t2_a_record_that_breached_and_then_paused_starts_no_second_run(w) -> None:
    _listen(w, "Listen A")
    rid = _create(w, "A", due=_db_iso(w, "-1 minute"))
    _scan(w)
    _update(w, "A", rid, state="waiting")
    _update(w, "A", rid, state="open")
    _scan(w)
    assert len(_runs(w, "Listen A")) == 1


# ── T3: a new due value ─────────────────────────────────────────────────────


def test_t3_a_new_due_value_starts_one_new_run(w) -> None:
    _listen(w, "Listen A")
    rid = _create(w, "A", due=_db_iso(w, "-10 minutes"))
    _scan(w)
    _update(w, "A", rid, due=_db_iso(w, "-1 minute"))
    _scan(w)
    _scan(w)
    runs = _runs(w, "Listen A")
    assert len(runs) == 2
    assert len({r.delivery_key for r in runs}) == 2
    assert {_payload(r)["record_id"] for r in runs} == {rid}


def test_t3_a_due_value_in_the_future_starts_no_run(w) -> None:
    _listen(w, "Listen A")
    _create(w, "A", due=_db_iso(w, "1 hour"))
    _scan(w)
    assert _runs(w, "Listen A") == []


# ── T4: the arm time ────────────────────────────────────────────────────────


def test_t4_the_first_scan_arms_and_a_record_due_before_the_arm_starts_no_run(w) -> None:
    _listen(w, "Listen A", armed=None)
    rid = _create(w, "A", due=_db_iso(w, "-1 minute"))
    _scan(w)
    armed = _armed_at(w, "Listen A")
    assert armed is not None
    assert _runs(w, "Listen A") == []
    _scan(w)
    _scan(w)
    assert _runs(w, "Listen A") == [], "a record due before the arm time breached"
    assert _armed_at(w, "Listen A") == armed, "a scan moved the arm time"
    # Control: a due value after the arm time and before the DB clock breaches.
    with w.admin_engine.connect() as c:
        after_arm = str(c.execute(text(
            "SELECT to_char((t.last_fired_at + (now() - t.last_fired_at) / 2) AT TIME ZONE "
            "'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') FROM workflow_triggers t "
            "WHERE t.workflow_id = CAST(:w AS uuid)"), {"w": w.wf["Listen A"]}).scalar())
    _update(w, "A", rid, due=after_arm)
    _scan(w)
    (run,) = _runs(w, "Listen A")
    assert _payload(run)["record_id"] == rid


# ── T5: the org of the record ───────────────────────────────────────────────


def test_t5_a_record_of_org_b_never_starts_a_workflow_of_org_a(w) -> None:
    """'A on B' is a workflow of org A that names the package of org B. Both
    orgs are on both lists. The read is bound to org A, so the package is
    unknown there. 'Listen B' runs in org B."""
    _listen(w, "A on B", "Listen B")
    rid = _create(w, "B", due=_db_iso(w, "-1 minute"))
    _scan(w)
    assert _runs(w, "A on B") == []
    (run,) = _runs(w, "Listen B")
    assert (run.org, _payload(run)["record_id"]) == (w.org["B"], rid)
    with w.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM workflow_runs WHERE organization_id = CAST(:o AS uuid)"),
            {"o": w.org["A"]}).scalar() == 0


# ── T6: both lists ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("unattended_tags", "studio_tags", "starts"), [
    ("A", "A", True),
    ("A", "", False),
    ("A", "B", False),
    ("", "A", False),
    ("B", "A", False),
])
def test_t6_an_org_not_on_both_lists_reads_no_trigger_and_starts_nothing(
        w, monkeypatch, unattended_tags: str, studio_tags: str, starts: bool) -> None:
    _listen(w, "Listen A")
    _listen(w, "Listen A 2", armed=None)
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    _list(w, unattended_tags=unattended_tags, studio_tags=studio_tags)
    reads: list[str] = []
    real = scheduler._timer_triggers

    async def spy(org: str) -> list[Any]:
        reads.append(org)
        return await real(org)

    monkeypatch.setattr(scheduler, "_timer_triggers", spy)
    _scan(w)
    assert (len(_runs(w, "Listen A")) == 1) is starts
    assert (w.org["A"] in reads) is starts, reads
    # The unarmed trigger stays unarmed in an org that the scan skips.
    assert (_armed_at(w, "Listen A 2") is not None) is starts


# ── T7: the author and the task node ────────────────────────────────────────


def test_t7_an_author_who_cannot_read_the_package_starts_no_run(w) -> None:
    """M2 holds feature:studio, but the package is private to the owner and
    PACKAGE_HOST_ORGS is off. The owner's workflow on the same breach runs."""
    _listen(w, "M2 A", "Listen A")
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    _scan(w)
    assert _runs(w, "M2 A") == []
    assert len(_runs(w, "Listen A")) == 1


def test_t7_a_workflow_with_a_task_node_starts_no_run(w) -> None:
    _listen(w, "Task node A")
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    _scan(w)
    assert _runs(w, "Task node A") == []


# ── T8: the demo path ───────────────────────────────────────────────────────


def _tasks_of(w: Any, run_id: str) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT t.id, t.title, p.personal_owner FROM pm_tasks t "
            "JOIN pm_projects p ON p.id = t.project_id "
            "WHERE t.origin ->> 'run_id' = :r"), {"r": run_id}).fetchall())


def test_t8_the_breach_reaches_the_human_step_and_the_manager_gets_one_task(w) -> None:
    _listen(w, "Step A")
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    _scan(w)
    (run,) = _runs(w, "Step A")
    assert run.status == "paused", run
    (task,) = _tasks_of(w, run.id)
    assert (task.title, task.personal_owner) == ("Chase the overdue claim", MANAGER_A)
    _scan(w)
    _scan(w)
    assert len(_runs(w, "Step A")) == 1
    assert len(_tasks_of(w, run.id)) == 1
    with w.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM pm_tasks t JOIN pm_projects p ON p.id = t.project_id "
            "WHERE p.personal_owner = :m AND t.source = 'automation'"),
            {"m": MANAGER_A}).scalar() == 1


# ── T9: the head of the list ────────────────────────────────────────────────


def test_t9_a_fired_record_never_holds_the_head_of_the_list(w, monkeypatch) -> None:
    """With one breach for each pass, the second pass must reach the second
    record. A read that does not leave out the fired record gives it again,
    the start answers DuplicateDelivery, and the second record waits for good."""
    monkeypatch.setattr(scheduler, "TIMER_BREACHES_PER_TRIGGER", 1)
    _listen(w, "Listen A")
    first = _create(w, "A", due=_db_iso(w, "-2 minutes"))
    second = _create(w, "A", due=_db_iso(w, "-1 minute"))
    _scan(w)
    assert [_payload(r)["record_id"] for r in _runs(w, "Listen A")] == [first]
    _scan(w)
    assert [_payload(r)["record_id"] for r in _runs(w, "Listen A")] == [first, second]


# ── T10: the write path ─────────────────────────────────────────────────────


def test_t10_a_timer_refuses_no_write_and_keeps_its_fields_hot(w) -> None:
    """Every case above writes through the store with the timer present. Here
    the due field of `ticket` goes cold by raw SQL (the only way). A write
    still passes, because the store skips a timer. The store itself refuses
    to turn a field of the timer cold."""
    from gateway.db import tenant_session
    from gateway.studio import objects_store

    pkg = w.pkg["A"]

    async def refuse_cold() -> str:
        async with tenant_session(w.org["A"]) as db:
            try:
                await objects_store.set_field_hot(db, pkg.ticket, "state", False)
            except objects_store.ObjectStoreError as exc:
                return str(exc)
        return ""

    assert "rule `sla` reads it" in _on_app_role(w, refuse_cold)
    with w.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org["A"]})
        c.execute(text(
            "UPDATE studio_object_fields SET hot = false WHERE object_id = CAST(:o AS uuid) "
            "AND key = 'due'"), {"o": pkg.ticket})
    rid = _create(w, "A", due=_db_iso(w, "-1 minute"), obj="ticket")
    assert rid


# ── T11: one session at a time ──────────────────────────────────────────────


def test_t11_the_scan_holds_one_session_at_a_time(w, monkeypatch) -> None:
    """The E3 pool lesson. No session opens inside a session, the author
    resolver runs outside every session, and no session is open at a start."""
    import acb_auth.access as acc
    from gateway.studio import access

    _listen(w, "Listen A", "M2 A")
    _create(w, "A", due=_db_iso(w, "-1 minute"))
    # The depth is kept for each task: a run that a start launched is its own
    # task, and its sessions are not sessions of the scan.
    depth: dict[Any, int] = {}
    opened: list[int] = []
    at: dict[str, list[int]] = {"resolve": [], "start": []}

    def _here() -> int:
        return depth.get(asyncio.current_task(), 0)

    def _count(real: Any) -> Any:
        @contextlib.asynccontextmanager
        async def wrapped(*a: Any, **kw: Any):
            task = asyncio.current_task()
            opened.append(_here())
            depth[task] = _here() + 1
            try:
                async with real(*a, **kw) as s:
                    yield s
            finally:
                depth[task] -= 1
        return wrapped

    real_held, real_start = access.members_holding_studio, scheduler.start_run

    async def held(members: Any, org: str) -> Any:
        at["resolve"].append(_here())
        return await real_held(members, org)

    async def start(**kw: Any) -> str:
        at["start"].append(_here())
        return await real_start(**kw)

    monkeypatch.setattr(scheduler, "_tenant_session", _count(scheduler._tenant_session))
    monkeypatch.setattr(acc, "tenant_session", _count(acc.tenant_session))
    monkeypatch.setattr(access, "members_holding_studio", held)
    monkeypatch.setattr(scheduler, "start_run", start)
    _scan(w)
    assert len(_runs(w, "Listen A")) == 1 and _runs(w, "M2 A") == []
    assert at["resolve"] and all(d == 0 for d in at["resolve"]), at
    assert at["start"] and all(d == 0 for d in at["start"]), at
    assert opened and max(opened) == 0, ("a session opened inside a session", opened)


# ── T12: a record event never starts a timer trigger ────────────────────────


def test_t12_a_record_event_that_copies_the_filter_starts_no_timer_run(w) -> None:
    _listen(w, "Listen A")
    payload = {"package_id": w.pkg["A"].id, "object": "claim", "op": "breach",
               "timer": "sla", "record_id": str(uuid.uuid4())}
    started = _on_app_role(w, lambda: triggers.dispatch_event(
        "studio", "studio.record.breached", payload), ambient=w.org["A"])
    assert started == []
    assert _runs(w, "Listen A") == []
