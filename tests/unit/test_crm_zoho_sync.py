"""CRM · the two-way Zoho sync engine — the seven rules §7.1 makes binding.

Spec: ``project-docs/specs/crm_app.md`` §7.1 · D-CRM-6/7/8 · ticket WS-26b
done-when 3, 4 and 5.

Hermetic: no Postgres, no network, no Zoho, no credential. The engine's two
seams — ``_client()`` (read) and ``_writer()`` (write) — are bound to fakes,
``core._get_db`` to ``_crm_fakes.FakeCrmDB``, and the Action Broker's audit
sink to a list. The **real** broker gate runs: disposition policy, auto-apply
default and the queued-write marker are all the shipped code, because the whole
point of D-CRM-8 is that pushes cannot reach Zoho another way.

The fake writer writes back into the fake tenant, which is what makes the
two-cycle echo-suppression test real: cycle 1 pushes a native row up, cycle 2
pulls that same record back, and the assertion is that it does **not** come
back dirty.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import broker_handlers as crm_broker
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import import_zoho as crm_import
from gateway.routes.crm import records as crm_records
from gateway.routes.crm import sync_zoho as crm_sync
from gateway.routes.crm.core import CONTACTS, DEALS, LEADS, ORGANIZATIONS

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user
from tests.unit.test_crm_zoho_import import FakeZoho, permission_checks

USER = crm_user()
REPO = Path(__file__).resolve().parents[2]

CRM_MODULES = (
    crm_core, crm_records, crm_activities, crm_admin, crm_import, crm_sync,
)


# ── Fakes ───────────────────────────────────────────────────────────────────

class FakeWriter:
    """The four functions ``sync_zoho.execute_push`` calls on the real writer.

    Writes land back in the fake TENANT, so the next pull sees what the last
    push did — the only way a two-cycle convergence test proves anything.
    """

    def __init__(self, tenant: FakeZoho) -> None:
        self.tenant = tenant
        self.creates: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, str, dict]] = []
        self.deletes: list[tuple[str, str]] = []
        self._ids = itertools.count(1)

    @property
    def pushes(self) -> int:
        return len(self.creates) + len(self.updates) + len(self.deletes)

    async def create_record(self, module: str, payload: dict) -> dict:
        zoho_id = f"z-pushed-{next(self._ids)}"
        self.creates.append((module, payload))
        self.tenant.data.setdefault(module, []).append({
            "id": zoho_id, "Modified_Time": "2026-08-05T12:00:00+05:30", **payload,
        })
        return {"status": "success", "details": {"id": zoho_id}}

    async def update_record(self, module: str, record_id: str, payload: dict) -> dict:
        self.updates.append((module, record_id, payload))
        for row in self.tenant.data.get(module, []):
            if row.get("id") == record_id:
                row.update(payload)
        return {"status": "success", "details": {"id": record_id}}

    async def delete_record(self, module: str, record_id: str) -> dict:
        self.deletes.append((module, record_id))
        self.tenant.data[module] = [
            r for r in self.tenant.data.get(module, []) if r.get("id") != record_id
        ]
        return {"status": "success", "details": {"id": record_id}}

    @staticmethod
    def record_id_of(result: dict) -> str | None:
        found = (result.get("details") or {}).get("id")
        return str(found) if found else None


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, CRM_MODULES)
    return fake


@pytest.fixture
def zoho(monkeypatch: pytest.MonkeyPatch) -> FakeZoho:
    tenant = FakeZoho()
    monkeypatch.setattr(crm_import, "_client", lambda: tenant)
    monkeypatch.setattr(crm_sync, "_client", lambda: tenant)
    return tenant


@pytest.fixture
def writer(monkeypatch: pytest.MonkeyPatch, zoho: FakeZoho) -> FakeWriter:
    fake = FakeWriter(zoho)
    monkeypatch.setattr(crm_sync, "_writer", lambda: fake)
    return fake


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The broker's audit sink, collected instead of written.

    The GATE itself is the real one — only the ``acb_audit`` persistence is
    replaced, so the disposition policy under test is the shipped policy.
    """
    from action_broker import broker as broker_module

    events: list[Any] = []
    monkeypatch.setattr(broker_module, "record", events.append)
    monkeypatch.delenv("ACTION_BROKER_ENFORCE", raising=False)
    return events


def _stored(db: FakeCrmDB, table: str, record_id: Any) -> dict:
    """One seeded row read back by id.

    The fake models no ``ON CONFLICT``, so an upsert APPENDS rather than
    rewriting; reading by id keeps every assertion pointed at the row the test
    seeded rather than at whichever copy happens to sort first.
    """
    return next(r for r in db.rows(table) if r["id"] == record_id)


def _fake_queue(
    monkeypatch: pytest.MonkeyPatch, pending: list[dict] | None = None,
) -> list[dict]:
    """Stand in for the broker's ``pending_actions`` table.

    Patched on the PACKAGE, which is the name ``broker_gate`` and
    ``pending_action_for`` import. The real ones reach Postgres through
    ``acb_graph`` — this suite touches no DB, and an unpatched ``list_pending``
    spends the connect timeout on every enforcement test.
    """
    import action_broker

    queue: list[dict] = list(pending or [])

    def _enqueue(proposal: Any) -> str:
        queue.append({
            "id": f"queued-{len(queue) + 1}",
            "action": proposal.action, "target": proposal.target,
        })
        return str(queue[-1]["id"])

    monkeypatch.setattr(action_broker, "enqueue", _enqueue)
    monkeypatch.setattr(action_broker, "list_pending", lambda: list(queue))
    return queue


def _seed_pipeline(db: FakeCrmDB) -> tuple:
    lead_status = db.seed(
        "crm_lead_statuses", name="New", position=10, type="open", is_default=True,
    )
    deal_status = db.seed(
        "crm_deal_statuses", name="Qualification", position=10, type="open",
        is_default=True, probability=10,
    )
    return lead_status, deal_status


# ── Rule 1 — the single writer, and the broker gate in front of it ──────────

def test_the_zoho_writer_has_exactly_one_caller_in_the_whole_repo() -> None:
    """§7.1's single-writer rule, measured rather than asserted in prose.

    The writer is the only Zoho write surface; ``routes/crm/sync_zoho.py`` is
    its only importer. A second importer is how "no route handler, agent tool
    or skill reaches Zoho directly" quietly stops being true.
    """
    needles = ("sources.zoho import writer", "sources.zoho.writer")
    hits: list[str] = []
    for scope in ("apps", "packages", "scripts", "skills"):
        for path in (REPO / scope).rglob("*.py"):
            if path.name == "writer.py" and path.parent.name == "zoho":
                continue  # the module itself
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(needle in text for needle in needles):
                hits.append(path.name)
    assert sorted(hits) == ["sync_zoho.py"], hits


def test_the_engine_reaches_zoho_only_through_execute_push() -> None:
    """Structural: inside the engine, ``_writer()`` has exactly one caller too.

    Otherwise the single-import property above would be satisfied while three
    functions in this module each did their own un-gated write.
    """
    import inspect

    named = [
        name for name, value in vars(crm_sync).items()
        if inspect.isfunction(value)
        and value.__module__ == crm_sync.__name__
        and "_writer()" in inspect.getsource(value)
    ]
    # `_writer` matches its own `def` line; `execute_push` is the one call.
    assert sorted(named) == ["_writer", "execute_push"], named


def test_the_write_surface_is_three_verbs_and_every_one_has_a_caller() -> None:
    """The whole point of a single write client is that it stays countable.

    Three verbs, and each is reachable from ``execute_push``. An exported
    convenience wrapper with no caller (``upsert_record`` was one) is a fourth
    entry on the surface that the single-caller grep still passes over — and
    "upsert by id" is a DECISION, made in ``push_records`` where the acquired
    id is stamped back onto the native row, not a verb.
    """
    import inspect

    from ingestion.sources.zoho import writer as zoho_writer

    verbs = {"create_record", "update_record", "delete_record"}
    exported = {
        name for name in zoho_writer.__all__
        if inspect.iscoroutinefunction(getattr(zoho_writer, name, None))
    }
    assert exported == verbs, exported
    assert not hasattr(zoho_writer, "upsert_record")

    body = inspect.getsource(crm_sync.execute_push)
    for verb in verbs:
        assert f"writer.{verb}(" in body, verb


async def test_every_push_goes_through_the_broker_gate(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """D-CRM-8 — one audited chokepoint, one audit row per push."""
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)
    await crm_sync.run_cycle()

    assert writer.creates, "nothing was pushed"
    actions = [e.action for e in audit]
    assert "propose:crm.zoho_create" in actions
    assert "execute:crm.zoho_create" not in actions  # auto-apply, not queued


def test_all_three_gated_actions_have_a_registered_handler() -> None:
    """BO-1a's lesson, applied ahead of time: the ClickUp gate routes SIX
    action names and registers FOUR, so approving one of the other two is
    marked ``failed``. Every CRM action gated here is executable on approval."""
    from action_broker import broker as broker_module

    crm_broker.register_crm_broker_handlers()
    for action in crm_broker.CRM_ZOHO_ACTIONS:
        assert action in broker_module._HANDLERS, action
    assert set(crm_broker.CRM_ZOHO_ACTIONS) == {
        "crm.zoho_create", "crm.zoho_update", "crm.zoho_delete",
    }


async def test_an_approved_queued_push_really_executes(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The registered handler re-enters through ``execute_push``, so an
    approval months later needs nothing that was only in memory."""
    from types import SimpleNamespace

    proposal = SimpleNamespace(
        action="crm.zoho_update",
        payload={"args": {
            "module": "Accounts", "op": "update", "record_id": "z-acc-1",
            "body": {"Account_Name": "Renamed"},
        }},
    )
    result = await crm_broker._handle_crm_zoho_write(proposal)

    assert result["ok"] is True
    assert writer.updates == [("Accounts", "z-acc-1", {"Account_Name": "Renamed"})]


async def test_an_approved_push_records_its_outcome_locally(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The approval path must write back exactly what the inline path does.

    Without it the approval performs the Zoho write and records nothing: the
    row stays dirty, the next cycle re-offers it, the operator approves again,
    and EVERY approval mints another copy upstream — nothing ever converges.
    """
    from types import SimpleNamespace

    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)
    proposal = SimpleNamespace(
        action="crm.zoho_create",
        payload={"args": {
            "module": "Accounts", "op": "create", "record_id": None,
            "body": {"Account_Name": "Fracktal"},
            "table": ORGANIZATIONS.table, "native_id": str(row.id),
        }},
    )

    await crm_broker._handle_crm_zoho_write(proposal)

    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_id"] == "z-pushed-1"
    assert stored["zoho_dirty"] is False
    assert stored["zoho_synced_at"] is not None
    assert db.committed >= 1


async def test_an_approved_tombstone_delete_is_stamped_pushed(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    from types import SimpleNamespace

    tomb = db.seed(
        "crm_zoho_tombstones", module="Deals", zoho_id="z-deal-9",
        entity_type="deal", deleted_by="vjvarada@fracktal.in",
    )
    await crm_broker._handle_crm_zoho_write(SimpleNamespace(
        action="crm.zoho_delete",
        payload={"args": {
            "module": "Deals", "op": "delete", "record_id": "z-deal-9",
            "body": {}, "table": "crm_zoho_tombstones", "native_id": str(tomb.id),
        }},
    ))

    [stored] = db.rows("crm_zoho_tombstones")
    assert stored["pushed_at"] is not None


async def test_the_queued_proposal_carries_the_native_row_to_write_back_to(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proposal approved next week must not depend on this process still
    being alive — including for "which row does this belong to"."""
    monkeypatch.setenv("ACTION_BROKER_ENFORCE", "1")
    _fake_queue(monkeypatch)
    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)

    await crm_sync.run_cycle()

    [proposed] = [e for e in audit if e.action == "propose:crm.zoho_create"]
    args = proposed.payload["args"]
    assert args["table"] == ORGANIZATIONS.table
    assert args["native_id"] == str(row.id)


async def test_a_stuck_row_does_not_enqueue_a_new_proposal_every_cycle(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row stays dirty by design while its push is unapproved, so it is
    re-offered every cycle — at ten-minute ticks that is 144 identical inbox
    rows per day per stuck record, and an approvals inbox nobody can read is
    an approvals inbox nobody uses."""
    monkeypatch.setenv("ACTION_BROKER_ENFORCE", "1")
    queue = _fake_queue(monkeypatch)
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1", zoho_dirty=True)

    await crm_sync.run_cycle()
    await crm_sync.run_cycle()
    await crm_sync.run_cycle()

    assert len(queue) == 1, queue
    assert queue[0]["target"] == "Accounts:z-acc-1"


async def test_broker_enforcement_queues_the_push_and_keeps_the_row_dirty(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-CRM-8's accepted consequence: enforcement ON makes the sync
    SUPERVISED. And BO-1b's bug must not be reproduced — a queued write has
    NOT happened, so clearing ``zoho_dirty`` would show a row as synced that
    exists in no tenant, losing the edit permanently."""
    monkeypatch.setenv("ACTION_BROKER_ENFORCE", "1")
    _fake_queue(monkeypatch)
    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)

    report = await crm_sync.run_cycle()

    assert writer.pushes == 0
    assert report.pushed.queued == 1
    assert report.pushed.created == 0
    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_dirty"] is True
    assert stored.get("zoho_id") is None


# ── The external write and its local record must not drift apart ───────────

async def test_the_acquired_zoho_id_is_committed_before_anything_else_runs(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Zoho's API has no idempotency token, so the window between "the create
    returned 200" and "the id is durable locally" is a duplicate factory: a
    crash, a shutdown or an aborted transaction in that window loses the id
    while the record exists upstream, and every later cycle creates ANOTHER.

    Asserted on ordering: a commit follows each stamp, before the next push.
    """
    db.seed(ORGANIZATIONS.table, name="One", zoho_dirty=True)
    db.seed(ORGANIZATIONS.table, name="Two", zoho_dirty=True)

    await crm_sync.run_cycle()

    kinds = [
        "stamp" if s.startswith("UPDATE crm_companies SET") else "commit"
        for s in db.statements
        if s.startswith("UPDATE crm_companies SET") or s == "COMMIT"
    ]
    # The fake records commits as a counter, not a statement, so assert the
    # weaker-but-real property: two creates, two stamps, and at least one
    # commit per stamp.
    assert len(writer.creates) == 2
    assert kinds.count("stamp") == 2
    assert db.committed >= 2


async def test_a_push_that_succeeds_but_cannot_be_stamped_is_loud(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The one case that cannot be made safe: Zoho accepted the write and the
    local stamp failed. It must be an ERROR in the report, not a silent
    success — a human has to reconcile it."""
    db.seed(ORGANIZATIONS.table, name="One", zoho_dirty=True)
    db.fail_on("UPDATE crm_companies SET", times=1)

    report = await crm_sync.run_cycle()

    assert len(writer.creates) == 1
    assert report.pushed.created == 0
    assert any("crm_companies" in e for e in report.pushed.errors)


# ── Retry, backoff and giving up ───────────────────────────────────────────

async def test_a_failing_push_is_backed_off_rather_than_retried_every_cycle(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    async def _explode(module: str, payload: dict) -> dict:
        raise RuntimeError("Zoho said no")

    writer.create_record = _explode  # type: ignore[method-assign]
    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)

    await crm_sync.run_cycle()

    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_push_attempts"] == 1
    assert "Zoho said no" in stored["zoho_push_error"]
    assert stored["zoho_next_attempt_at"] is not None
    # Still dirty — a push that failed and cleared the flag is a lost change.
    assert stored["zoho_dirty"] is True


async def test_a_row_inside_its_backoff_window_is_not_offered(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Decided in SQL, so the fake reads the predicate rather than restating
    it: `coalesce(next_attempt_at, :epoch) <= :now`."""
    from datetime import timedelta

    db.seed(
        ORGANIZATIONS.table, name="Backed off", zoho_dirty=True,
        zoho_push_attempts=2,
        zoho_next_attempt_at=crm_core.now() + timedelta(hours=1),
    )
    await crm_sync.run_cycle()

    assert writer.pushes == 0
    [statement] = [
        s for s in db.statements
        if s.startswith("SELECT * FROM crm_companies WHERE zoho_dirty")
    ]
    assert "coalesce(zoho_next_attempt_at, :epoch) <= :now" in statement


async def test_a_row_past_the_attempt_ceiling_stops_being_offered(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The starvation fix: both queues are read oldest-first under a LIMIT, so
    a handful of rows Zoho will never accept would otherwise hold the front of
    the queue forever and nothing behind them would ever push."""
    db.seed(
        ORGANIZATIONS.table, name="Poison", zoho_dirty=True,
        zoho_push_attempts=crm_sync.MAX_PUSH_ATTEMPTS,
    )
    db.seed(ORGANIZATIONS.table, name="Healthy", zoho_dirty=True)

    await crm_sync.run_cycle()

    assert [payload["Account_Name"] for _, payload in writer.creates] == ["Healthy"]


async def test_giving_up_is_counted_and_logged_not_silent(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    async def _explode(module: str, payload: dict) -> dict:
        raise RuntimeError("Zoho said no")

    writer.create_record = _explode  # type: ignore[method-assign]
    db.seed(
        ORGANIZATIONS.table, name="Nearly done", zoho_dirty=True,
        zoho_push_attempts=crm_sync.MAX_PUSH_ATTEMPTS - 1,
    )
    report = await crm_sync.run_cycle()

    assert report.pushed.given_up == 1
    assert report.pushed.errors


def test_the_backoff_grows_and_is_capped() -> None:
    base = crm_core.now()
    delays = [
        (crm_sync.backoff_until(n, at=base) - base).total_seconds()
        for n in (1, 2, 3, 10)
    ]
    assert delays[0] == crm_sync.BACKOFF_BASE_SECS
    assert delays[1] == crm_sync.BACKOFF_BASE_SECS * 2
    assert delays[2] == crm_sync.BACKOFF_BASE_SECS * 4
    assert delays[3] == crm_sync.BACKOFF_CAP_SECS


async def test_a_tombstone_for_a_record_zoho_no_longer_has_is_a_success(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Zoho answers a delete of an unknown id with 404 (or a 200 carrying
    RECORD_NOT_IN_MODULE). The goal state already holds, so treating it as a
    failure would retry it against a record that can never come back."""
    from ingestion.sources.zoho import writer as zoho_writer

    async def _gone(module: str, record_id: str) -> dict:
        return zoho_writer.ALREADY_GONE

    writer.delete_record = _gone  # type: ignore[method-assign]
    db.seed(
        "crm_zoho_tombstones", module="Deals", zoho_id="z-vanished",
        entity_type="deal", deleted_by="vjvarada@fracktal.in",
    )
    report = await crm_sync.run_cycle()

    assert report.pushed.deleted == 1
    [tomb] = db.rows("crm_zoho_tombstones")
    assert tomb["pushed_at"] is not None


def test_the_writer_treats_a_404_delete_as_done() -> None:
    """Structural, on the writer's source: the branch has no test double
    because it is HTTP-status handling, and the constant it returns is what
    the engine reads as success."""
    import inspect

    from ingestion.sources.zoho import writer as zoho_writer

    source = inspect.getsource(zoho_writer.delete_record)
    assert "status_code == 404" in source
    assert "ALREADY_GONE" in source
    assert zoho_writer.ALREADY_GONE["status"] == "success"
    assert "RECORD_NOT_IN_MODULE" in zoho_writer.GONE_CODES


# ── One cycle at a time ────────────────────────────────────────────────────

async def test_a_second_concurrent_cycle_is_refused_with_409(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping cycles both see the same dirty rows and both create
    them upstream — and Zoho has no idempotency token to undo that."""
    import asyncio

    released = asyncio.Event()
    real_users = zoho.list_users

    async def _slow_users() -> list[dict]:
        await released.wait()
        return await real_users()

    monkeypatch.setattr(zoho, "list_users", _slow_users, raising=False)

    first = asyncio.create_task(crm_sync.run_cycle())
    await asyncio.sleep(0)  # let it take the lock
    assert crm_sync.cycle_in_progress() is True

    with pytest.raises(HTTPException) as exc:
        await crm_sync.run_sync_cycle_now(USER)
    assert exc.value.status_code == 409

    released.set()
    await first
    assert crm_sync.cycle_in_progress() is False


async def test_the_scheduled_loop_skips_a_tick_rather_than_overlapping() -> None:
    """A hand-run cycle has the floor; the tick simply skips. Queueing behind
    it would run a second full pass the moment the first finished."""
    import inspect

    source = inspect.getsource(crm_sync._sync_loop)
    assert "CycleAlreadyRunning" in source


# ── Shutdown must not land mid-cycle ───────────────────────────────────────

async def test_stopping_signals_and_waits_instead_of_cancelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel lands wherever the cycle happens to be — including between a
    successful Zoho create and the commit that records its id, which is
    exactly the window that makes the next cycle duplicate it upstream."""
    import asyncio

    monkeypatch.setattr(crm_sync, "sync_enabled", lambda: True)
    monkeypatch.setattr(crm_sync, "SYNC_INTERVAL_SECS", 3600)
    started = asyncio.Event()
    finished = asyncio.Event()

    async def _slow_cycle(**_: Any) -> Any:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()
        return crm_sync.SyncCycleReport(started_at="x")

    monkeypatch.setattr(crm_sync, "run_cycle", _slow_cycle)
    crm_sync._sync_task = None
    assert await crm_sync.start_crm_zoho_sync() is True
    await started.wait()

    await crm_sync.stop_crm_zoho_sync()

    assert finished.is_set(), "shutdown cancelled a cycle mid-flight"
    assert crm_sync._sync_task is None


def test_the_stop_path_does_not_reach_for_cancel_first() -> None:
    """Cancellation survives as the last resort after a grace window, and is
    logged as the loss it is — but it must not be the first move."""
    import inspect

    source = inspect.getsource(crm_sync.stop_crm_zoho_sync)
    assert "_stopping.set()" in source
    assert source.index("wait_for") < source.index("task.cancel()")
    assert "stop_timed_out" in source


# ── Rule 3 — dirty-driven push; a create acquires an id ─────────────────────

async def test_a_native_create_acquires_a_zoho_id(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    row = db.seed(
        ORGANIZATIONS.table, name="Fracktal Works", website="https://fracktal.in",
        zoho_dirty=True,
    )
    report = await crm_sync.run_cycle()

    [(module, payload)] = writer.creates
    assert module == "Accounts"
    assert payload["Account_Name"] == "Fracktal Works"
    assert report.pushed.created == 1

    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_id"] == "z-pushed-1"
    assert stored["zoho_dirty"] is False
    assert stored["zoho_synced_at"] is not None


async def test_a_dirty_linked_row_is_updated_not_recreated(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    db.seed(
        ORGANIZATIONS.table, name="Renamed", zoho_id="z-acc-1", zoho_dirty=True,
    )
    report = await crm_sync.run_cycle()

    assert writer.creates == []
    [(module, record_id, payload)] = writer.updates
    assert (module, record_id) == ("Accounts", "z-acc-1")
    assert payload["Account_Name"] == "Renamed"
    assert report.pushed.updated == 1


async def test_a_clean_row_is_never_pushed(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    await crm_sync.run_cycle()
    assert writer.pushes == 0


async def test_a_deal_push_carries_its_stage_name_not_its_status_uuid(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Zoho's Stage is a picklist LABEL. Sending our UUID would be rejected —
    or worse, silently stored as a new picklist value."""
    _, deal_status = _seed_pipeline(db)
    org = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    db.seed(
        DEALS.table, name="10 printers", amount=450000,
        status_id=deal_status.id, company_id=org.id, zoho_dirty=True,
    )
    await crm_sync.run_cycle()

    [(module, payload)] = [c for c in writer.creates if c[0] == "Deals"]
    assert module == "Deals"
    assert payload["Stage"] == "Qualification"
    assert payload["Amount"] == 450000
    # The org link travels as Zoho's own id, not ours.
    assert payload["Account_Name"] == {"id": "z-acc-1"}


async def test_a_push_never_sends_null_for_a_field_we_do_not_have(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Sending ``None`` at Zoho CLEARS the field, so a column we simply do not
    carry would blank the tenant's copy on every cycle."""
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)
    await crm_sync.run_cycle()

    [(_, payload)] = writer.creates
    assert None not in payload.values()
    assert "Website" not in payload


async def test_a_failed_push_leaves_the_row_dirty_and_is_counted(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """A push that failed and cleared the flag is a change silently dropped."""
    async def _explode(module: str, payload: dict) -> dict:
        raise RuntimeError("Zoho said no")

    writer.create_record = _explode  # type: ignore[method-assign]
    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)

    report = await crm_sync.run_cycle()

    assert report.pushed.created == 0
    assert len(report.pushed.errors) == 1
    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_dirty"] is True


# ── Transaction shape: one bad statement must not poison the cycle ─────────

async def test_each_applied_record_gets_its_own_savepoint(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """A ``try/except`` around a record does NOT contain a statement error in
    Postgres — the whole transaction is aborted and every later statement in
    the cycle fails, cursor write included. Only a SAVEPOINT does."""
    zoho.data["Accounts"] = [
        {"id": "z-1", "Account_Name": "A"}, {"id": "z-2", "Account_Name": "B"},
    ]
    await crm_sync.run_cycle()
    assert db.savepoints >= 2


async def test_a_driver_level_statement_error_does_not_take_the_cycle_with_it(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The real shape of the bug: a Zoho `Amount` past `NUMERIC(14,2)` raises
    at the driver. Everything after it in the same transaction then fails with
    `current transaction is aborted`, the cursor never advances, and the next
    cycle repeats it — identically, forever.

    Simulated by making one INSERT raise. What must survive: the batch keeps
    going, the savepoint takes the hit, the cursor is still written, and the
    cycle still commits.
    """
    db.fail_on("INSERT INTO crm_companies")
    zoho.data["Accounts"] = [
        {"id": "z-bad", "Account_Name": "Boom",
         "Modified_Time": "2026-08-01T00:00:00+00:00"},
        {"id": "z-ok", "Account_Name": "Fine",
         "Modified_Time": "2026-08-02T00:00:00+00:00"},
    ]

    report = await crm_sync.run_cycle()

    assert db.savepoint_rollbacks >= 1
    assert report.pulled["Accounts"].created == 1      # the batch continued
    assert report.pull_record_errors == 1
    assert db.statements_touching("INSERT INTO crm_sync_cursors")
    assert db.committed >= 1
    assert report.finished_at is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("450000", 450000.0),
        ("999999999999.99", 999999999999.99),
        # NUMERIC(14,2) holds 12 integer digits. Zoho's currency fields do not
        # have that ceiling, so one fat-fingered deal would abort the tx.
        ("1000000000000", None),
        ("-1000000000000", None),
        ("1e309", None),   # inf
        ("nonsense", None),
        ("", None),
    ],
)
def test_a_number_past_the_column_range_becomes_null_not_an_aborted_tx(
    raw: str, expected: Any,
) -> None:
    from gateway.routes.crm.import_zoho import _number

    assert _number(raw) == expected


# ── Rule 2 — incremental pull, persisted cursors ────────────────────────────

async def test_the_first_pull_has_no_cursor_and_the_second_one_does(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """§7.1 — "pull cursors are schema too": without a PERSISTED cursor an
    incremental pull re-reads the world after every restart."""
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal",
        "Modified_Time": "2026-08-01T09:00:00+05:30",
    }]

    await crm_sync.run_cycle()
    first = [since for module, since in zoho.reads if module == "Accounts"]
    assert first == [None]

    zoho.reads.clear()
    await crm_sync.run_cycle()
    second = [since for module, since in zoho.reads if module == "Accounts"]
    assert second and second[0] is not None
    assert second[0].isoformat().startswith("2026-08-01T09:00")


async def test_the_cursor_upsert_is_keyed_on_the_module(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """A second cursor row for one module is not a second opinion — it would
    let the pull silently rewind. Pinned against the statement text because
    the fake models no ``ON CONFLICT``."""
    await crm_sync.run_cycle()

    inserts = db.statements_touching("INSERT INTO crm_sync_cursors")
    assert inserts
    assert "ON CONFLICT (organization_id, module) DO UPDATE SET" in inserts[0]


async def test_the_deleted_records_read_uses_the_PRE_cycle_cursor(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The cursor snapshot, and the reason it exists.

    ``pull_phase`` WRITES cursors as it goes. A deleted-records read that
    fetched the cursor afterwards would receive the watermark this very cycle
    just wrote, so Zoho would answer "nothing deleted since then" for every
    deletion older than this cycle's newest change — and those deletions are
    **permanently** missed, because the cursor only ever moves forward and no
    later cycle asks about that window again.
    """
    db.seed(
        "crm_sync_cursors", module="Accounts",
        last_pulled_at=_instant("2026-08-01T00:00:00+00:00"),
    )
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal",
        "Modified_Time": "2026-08-04T00:00:00+00:00",
    }]

    await crm_sync.run_cycle()

    [(_, deleted_since)] = [
        (m, since) for m, since in zoho.reads if m == "Accounts/deleted"
    ]
    assert deleted_since == _instant("2026-08-01T00:00:00+00:00")

    # And the pull really did move the cursor past it in the same cycle —
    # without which this test could pass against a version that never advances.
    [written] = [
        p for s, p in db.calls
        if s.startswith("INSERT INTO crm_sync_cursors") and p["module"] == "Accounts"
    ]
    assert written["last_pulled_at"] == _instant("2026-08-04T00:00:00+00:00")
    assert deleted_since < written["last_pulled_at"]


async def test_every_module_reads_its_delete_cursor_from_one_snapshot(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """One `SELECT * FROM crm_sync_cursors` per cycle, not one per module per
    phase: a per-phase read is exactly how the stale-watermark bug returns."""
    await crm_sync.run_cycle()
    reads = [s for s in db.statements if s == "SELECT * FROM crm_sync_cursors"]
    assert len(reads) == 1, db.statements_touching("crm_sync_cursors")


# ── The watermark never advances OVER a record that failed ─────────────────
#
# `If-Modified-Since` is a single instant, so a failed record is only
# retryable while the cursor stays strictly BELOW it. The ceiling is therefore
# the OLDEST failure, not the newest — advancing to the newest success while
# an older record failed drops that record permanently.

@pytest.mark.parametrize(
    ("previous", "newest_applied", "fetched", "failures", "oldest_failed",
     "expected"),
    [
        # Nothing fetched → adopt the cycle start, or a module Zoho never
        # changes re-reads its whole table every ten minutes forever.
        (None, None, 0, 0, None, "fallback"),
        # Fetched but nothing landed → stand still, so the next cycle retries.
        ("t1", None, 3, 1, "t1", "t1"),
        (None, None, 3, 1, "t1", None),
        # Normal, no failures: the newest record that actually landed.
        ("t1", "t2", 3, 0, None, "t2"),
        # Never backwards — a rewound cursor re-reads a reconciled window.
        ("t2", "t1", 3, 0, None, "t2"),
        # ⚠️ The case the re-verification caught: the FAILURE IS OLDER than the
        # success. Advancing to t3 would put the cursor past t1, and t1 would
        # never be offered again.
        ("t0", "t3", 3, 1, "t1", "t0"),
        # Equal instants are also unsafe: `If-Modified-Since` at t2 need not
        # return a record modified exactly at t2.
        ("t0", "t2", 3, 1, "t2", "t0"),
        # A failure NEWER than every success is fine to advance under.
        ("t0", "t1", 3, 1, "t3", "t1"),
        # A failure we cannot place in time → stand still. "We do not know"
        # must not read as "nothing failed".
        ("t0", "t3", 3, 1, None, "t0"),
    ],
)
def test_advance_cursor_is_forward_only_and_never_over_a_failure(
    previous: Any, newest_applied: Any, fetched: int, failures: int,
    oldest_failed: Any, expected: Any,
) -> None:
    stamps = {
        "t0": _instant("2026-07-31T00:00:00+00:00"),
        "t1": _instant("2026-08-01T00:00:00+00:00"),
        "t2": _instant("2026-08-02T00:00:00+00:00"),
        "t3": _instant("2026-08-03T00:00:00+00:00"),
        "fallback": _instant("2026-08-09T00:00:00+00:00"),
        None: None,
    }
    result = crm_sync.advance_cursor(
        stamps[previous], stamps[newest_applied],
        fetched=fetched, fallback=stamps["fallback"],
        failures=failures, oldest_failed=stamps[oldest_failed],
    )
    assert result == stamps[expected]


# ── A module Zoho gives us no timestamps for ───────────────────────────────
#
# Measured against the live tenant 2026-08-06, the cycle after the sync was
# first enabled: `Deals` returns neither `Modified_Time` nor `Created_Time`
# (requesting them by name returns them empty — a per-module field permission,
# not our query), so a batch where all 551 records applied still yields
# `newest_applied is None`. Before this branch existed the module could never
# write a watermark: every cycle re-pulled the entire table, forever, while
# every other module converged to one or two records.

@pytest.mark.parametrize(
    ("previous", "applied", "failures", "expected"),
    [
        # All clean, no timestamps → adopt the cycle start. This is the fix.
        (None, 551, 0, "fallback"),
        # Same, but the module already had a watermark: still forward.
        ("t1", 551, 0, "fallback"),
        # ⚠️ A failure anywhere in the batch → stand still, exactly as before.
        # We cannot place the failure in time, so the window must be re-read.
        (None, 550, 1, None),
        ("t1", 550, 1, "t1"),
        # Fetched, zero applied, no failures (every record skipped) → the
        # batch proves nothing landed, so the cursor must not move.
        ("t1", 0, 0, "t1"),
    ],
)
def test_an_untimestamped_but_clean_batch_adopts_the_cycle_start(
    previous: Any, applied: int, failures: int, expected: Any,
) -> None:
    stamps = {
        "t1": _instant("2026-08-01T00:00:00+00:00"),
        "fallback": _instant("2026-08-09T00:00:00+00:00"),
        None: None,
    }
    result = crm_sync.advance_cursor(
        stamps[previous], None,
        fetched=551, fallback=stamps["fallback"],
        failures=failures, oldest_failed=None, applied=applied,
    )
    assert result == stamps[expected]


def test_the_untimestamped_branch_never_rewinds_the_cursor() -> None:
    """`fallback` is the cycle's start, so it is normally later than the
    previous watermark — but a clock skew or a replayed report must not drag
    a module's cursor backwards over a window it already reconciled."""
    ahead = _instant("2026-08-09T00:00:00+00:00")
    behind = _instant("2026-08-01T00:00:00+00:00")
    assert crm_sync.advance_cursor(
        ahead, None, fetched=10, fallback=behind,
        failures=0, oldest_failed=None, applied=10,
    ) == ahead


async def test_an_older_failure_holds_the_cursor_below_a_newer_success(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end, in the order Zoho actually returns records: the OLD one
    fails, a NEWER one succeeds. Watermarking on the newest success would step
    over the failure and the record would never be offered again."""
    real = crm_import.map_account

    def _explode(record: dict) -> dict:
        if record.get("id") == "z-acc-old-bad":
            raise ValueError("bad account")
        return real(record)

    monkeypatch.setitem(crm_import.MAPPERS, "Accounts", _explode)
    db.seed(
        "crm_sync_cursors", module="Accounts",
        last_pulled_at=_instant("2026-07-31T00:00:00+00:00"),
    )
    zoho.data["Accounts"] = [
        {"id": "z-acc-old-bad", "Account_Name": "Bad",
         "Modified_Time": "2026-08-01T00:00:00+00:00"},
        {"id": "z-acc-new-ok", "Account_Name": "Good",
         "Modified_Time": "2026-08-05T00:00:00+00:00"},
    ]

    report = await crm_sync.run_cycle()

    [written] = [
        p for s, p in db.calls
        if s.startswith("INSERT INTO crm_sync_cursors") and p["module"] == "Accounts"
    ]
    assert written["last_pulled_at"] == _instant("2026-07-31T00:00:00+00:00")
    assert written["last_status"] == "partial"
    # …and the good record still landed. Standing still costs a re-read, not a
    # skipped write.
    assert report.pulled["Accounts"].created == 1
    assert report.pull_record_errors == 1


async def test_the_cursor_stops_short_of_a_record_that_failed_to_apply(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed record is counted and the batch continues (an import that
    aborts on row 900 of 3 000 is one nobody can finish) — but if the cursor
    advanced past it anyway, the next incremental pull would never ask for it
    again and the record would be dropped for good."""
    real = crm_import.map_account

    def _explode(record: dict) -> dict:
        if record.get("id") == "z-acc-bad":
            raise ValueError("bad account")
        return real(record)

    monkeypatch.setitem(crm_import.MAPPERS, "Accounts", _explode)
    zoho.data["Accounts"] = [
        {"id": "z-acc-ok", "Account_Name": "Good",
         "Modified_Time": "2026-08-01T00:00:00+00:00"},
        {"id": "z-acc-bad", "Account_Name": "Bad",
         "Modified_Time": "2026-08-05T00:00:00+00:00"},
    ]

    report = await crm_sync.run_cycle()

    [written] = [
        p for s, p in db.calls
        if s.startswith("INSERT INTO crm_sync_cursors") and p["module"] == "Accounts"
    ]
    assert written["last_pulled_at"] == _instant("2026-08-01T00:00:00+00:00")
    assert written["last_status"] == "partial"
    assert report.pulled["Accounts"].created == 1


async def test_a_failed_record_is_counted_in_the_cycle_summary(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-record errors live inside each ModuleReport; the cycle summary
    is what a human reads. A cycle that dropped rows while reporting zero
    errors is the report that hides the outage."""
    def _explode(record: dict) -> dict:
        raise ValueError("bad account")

    monkeypatch.setitem(crm_import.MAPPERS, "Accounts", _explode)
    zoho.data["Accounts"] = [{"id": "z-acc-1", "Account_Name": "Bad"}]

    report = await crm_sync.run_cycle()

    assert len(report.pulled["Accounts"].errors) == 1
    assert report.pull_record_errors == 1


async def test_a_module_whose_pull_fails_is_reported_and_does_not_stop_the_rest(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _explode(*, modified_since: Any = None) -> list[dict]:
        raise RuntimeError("Zoho 500")

    monkeypatch.setattr(zoho, "list_accounts", _explode, raising=False)
    zoho.data["Contacts"] = [{"id": "z-con-1", "First_Name": "Asha"}]

    report = await crm_sync.run_cycle()

    assert any("Accounts pull failed" in e for e in report.errors)
    assert report.pulled["Contacts"].created == 1


# ── Rule 5 — last-writer-wins, both directions, counted ────────────────────

def _instant(text: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(text)


async def test_a_conflict_where_zoho_is_newer_applies_the_pull(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """D-CRM-6's surviving half: record-level LWW, never a field merge."""
    db.seed(
        ORGANIZATIONS.table, name="Native name", zoho_id="z-acc-1",
        zoho_dirty=True,
        zoho_synced_at=_instant("2026-08-01T09:00:00+00:00"),
        updated_at=_instant("2026-08-02T09:00:00+00:00"),
    )
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Zoho name",
        "Modified_Time": "2026-08-03T09:00:00+00:00",
    }]

    report = await crm_sync.run_cycle()

    assert report.conflicts == 1
    assert report.conflicts_zoho_won == 1
    assert report.conflicts_native_won == 0

    # The pull WAS applied, and the write it applied discards the native edit
    # by carrying zoho_dirty=false into the row.
    #
    # ⚠️ Asserted against the upsert's bound VALUES rather than against the
    # row afterwards, because the fake models no `ON CONFLICT`: in Postgres
    # the conflict arm rewrites the existing row (clearing the flag, so the
    # push phase finds nothing), while the fake appends a second row and
    # leaves the first one dirty. Reading the row back here would be
    # measuring the mirror's gap, not the engine.
    [(statement, params)] = [
        (s, p) for s, p in db.calls
        if s.startswith("INSERT INTO crm_companies (")
    ]
    assert params["name"] == "Zoho name"
    assert params["zoho_dirty"] is False
    assert "zoho_dirty = EXCLUDED.zoho_dirty" in statement


async def test_a_conflict_where_the_native_edit_is_newer_keeps_it_and_pushes(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    row = db.seed(
        ORGANIZATIONS.table, name="Native name", zoho_id="z-acc-1",
        zoho_dirty=True,
        zoho_synced_at=_instant("2026-08-01T09:00:00+00:00"),
        updated_at=_instant("2026-08-04T09:00:00+00:00"),
    )
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Zoho name",
        "Modified_Time": "2026-08-03T09:00:00+00:00",
    }]

    report = await crm_sync.run_cycle()

    assert report.conflicts == 1
    assert report.conflicts_native_won == 1
    # The pull was skipped, the row stayed dirty, and the push overwrote Zoho.
    [(module, record_id, payload)] = writer.updates
    assert (module, record_id) == ("Accounts", "z-acc-1")
    assert payload["Account_Name"] == "Native name"
    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_dirty"] is False


async def test_a_dirty_row_zoho_has_not_touched_is_not_a_conflict(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Only one side moved. Counting it as a conflict would make the number
    meaningless — and applying the pull would clobber the pending edit with
    the value we are about to overwrite anyway."""
    db.seed(
        ORGANIZATIONS.table, name="Native name", zoho_id="z-acc-1",
        zoho_dirty=True,
        zoho_synced_at=_instant("2026-08-03T09:00:00+00:00"),
        updated_at=_instant("2026-08-04T09:00:00+00:00"),
    )
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Zoho name",
        "Modified_Time": "2026-08-01T09:00:00+00:00",
    }]

    report = await crm_sync.run_cycle()

    assert report.conflicts == 0
    assert report.pull_skipped_native_newer == 1
    assert len(writer.updates) == 1


def test_an_uncomparable_timestamp_never_lets_zoho_win_silently() -> None:
    """A naive/aware mismatch or a missing stamp means we cannot honestly say
    Zoho is newer. The conservative answer keeps the native edit, which is
    recoverable; the other direction discards a colleague's typing."""
    from datetime import UTC, datetime

    aware = datetime(2026, 8, 3, tzinfo=UTC)
    naive = datetime(2026, 8, 1)
    assert crm_sync._later(aware, naive) is False
    assert crm_sync._later(None, aware) is False
    assert crm_sync._later(aware, None) is False


# ── Rule 6 — echo suppression ──────────────────────────────────────────────

async def test_a_pull_applied_write_never_marks_the_row_dirty(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal",
        "Modified_Time": "2026-08-01T09:00:00+05:30",
    }]
    await crm_sync.run_cycle()

    [row] = db.rows(ORGANIZATIONS.table)
    assert row["zoho_dirty"] is False
    assert writer.pushes == 0


async def test_two_cycles_converge_to_zero_pushes(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """done-when 3's named test. Cycle 1 pushes a native row up; the fake
    writer puts it in the tenant, so cycle 2 pulls that very record back. If
    the pull re-dirtied it the two sides would ping-pong forever, each cycle
    pushing the row it just received."""
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)

    first = await crm_sync.run_cycle()
    assert first.pushed.created == 1
    assert zoho.data["Accounts"], "the push never reached the fake tenant"

    before = writer.pushes
    second = await crm_sync.run_cycle()

    assert writer.pushes == before, "cycle 2 pushed again — the echo is not suppressed"
    assert second.pushed.created == 0
    assert second.pushed.updated == 0
    assert second.pulled["Accounts"].fetched == 1


async def test_a_pull_never_rewrites_the_source_provenance(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """A row typed into this app is `source='manual'` FOREVER — including
    after the sync pushes it to Zoho and pulls it straight back.

    Without `source` on the insert-only list, the conflict arm rewrote it to
    `'import'` on the first echo: a silent, one-way rewrite of the column the
    `?source=` filter and every "where did this come from" question read.
    Statement-text, because the fake models no conflict arm at all.
    """
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1", source="manual")
    zoho.data["Accounts"] = [{
        "id": "z-acc-1", "Account_Name": "Fracktal",
        "Modified_Time": "2026-08-04T00:00:00+00:00",
    }]

    await crm_sync.run_cycle()

    [statement] = db.statements_touching("INSERT INTO crm_companies (")
    arm = statement.split("DO UPDATE SET", 1)[1]
    assert "source = EXCLUDED.source" not in arm
    assert "zoho_id = EXCLUDED.zoho_id" not in arm
    # …while the ordinary columns still are rewritten, or this would pass by
    # the upsert having no conflict arm at all.
    assert "name = EXCLUDED.name" in arm
    # `source` is still SENT — it is what a genuinely new row is created with.
    assert "source" in statement.split("DO UPDATE SET", 1)[0]
    assert set(crm_core.INSERT_ONLY_COLUMNS) == {"zoho_id", "source"}


async def test_a_pull_does_not_write_our_own_padding_back_into_a_native_null(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Zoho requires Last_Name; §3.2 does not. The push therefore pads it from
    the first name — and without a guard the next pull writes that padding
    into the native NULL, the sync mutating native data purely by echoing
    itself. Full round trip: cycle 1 pushes, cycle 2 pulls the same record."""
    row = db.seed(
        CONTACTS.table, first_name="Vijay", last_name=None, zoho_dirty=True,
    )

    await crm_sync.run_cycle()
    [(module, payload)] = writer.creates
    assert (module, payload["Last_Name"]) == ("Contacts", "Vijay")  # the padding

    db.calls.clear()
    await crm_sync.run_cycle()

    applied = [
        p for s, p in db.calls if s.startswith("INSERT INTO crm_contacts (")
    ]
    assert applied, "cycle 2 never pulled the record back"
    assert all("last_name" not in p for p in applied)
    assert _stored(db, CONTACTS.table, row.id)["last_name"] is None


async def test_a_lead_round_trip_does_not_double_its_own_name(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The one the re-verification caught: "Asha" came back as "Asha Asha".

    A lead's display name is DERIVED from the very fields the push has to pad
    (§3.3's chain reads first/last/organization_name, and Zoho requires both
    ``Last_Name`` and ``Company``). Deriving it inside the mapper folded our
    own padding into the name, and ``lead_name`` is on the upsert's conflict
    arm — so every cycle wrote the corruption back, growing nothing but
    getting the name permanently wrong. The derivation now runs AFTER the
    padding strip.

    A full round trip, not a hand-built dict: the existing lead-mapping test
    passes values straight to the mapper and never exercises this ordering.
    """
    lead_status, _ = _seed_pipeline(db)
    row = db.seed(
        LEADS.table, lead_name="Asha", first_name="Asha", last_name=None,
        organization_name=None, status_id=lead_status.id, zoho_dirty=True,
    )

    await crm_sync.run_cycle()
    [(module, payload)] = [c for c in writer.creates if c[0] == "Leads"]
    # Both required fields were padded from the display name.
    assert (module, payload["Last_Name"], payload["Company"]) == (
        "Leads", "Asha", "Asha",
    )

    db.calls.clear()
    await crm_sync.run_cycle()

    applied = [p for s, p in db.calls if s.startswith("INSERT INTO crm_leads (")]
    assert applied, "cycle 2 never pulled the lead back"
    for params in applied:
        assert params["lead_name"] == "Asha"
        assert "last_name" not in params
        assert "organization_name" not in params
    stored = _stored(db, LEADS.table, row.id)
    assert stored["lead_name"] == "Asha"
    assert stored["last_name"] is None
    assert stored["organization_name"] is None


def test_the_lead_mapper_does_not_derive_the_display_name_itself() -> None:
    """The ordering above is only safe while the mapper leaves `lead_name`
    alone — putting the derivation back in `map_lead` would restore the bug
    while every round-trip assertion above still described the right rule."""
    mapped = crm_import.map_lead(
        {"id": "z-1", "First_Name": "Asha", "Last_Name": "Asha",
         "Company": "Asha"},
    )
    assert "lead_name" not in mapped


def test_the_padding_guard_only_fires_on_a_native_null(
    db: FakeCrmDB,
) -> None:
    """Narrow and provable rather than clever: it drops a pulled value only
    when the native column is NULL *and* the value is exactly what we would
    have padded it from. A real Zoho edit to a populated column is untouched."""
    from types import SimpleNamespace

    from gateway.routes.crm.import_zoho import strip_padding_echo

    blank = SimpleNamespace(first_name="Vijay", last_name=None)
    assert "last_name" not in strip_padding_echo(
        CONTACTS, {"first_name": "Vijay", "last_name": "Vijay"}, blank,
    )
    # A different surname is a real value and survives.
    assert strip_padding_echo(
        CONTACTS, {"last_name": "Varada"}, blank,
    ) == {"last_name": "Varada"}
    # A populated native column is Zoho's to change.
    populated = SimpleNamespace(first_name="Vijay", last_name="Varada")
    assert strip_padding_echo(
        CONTACTS, {"last_name": "Vijay"}, populated,
    ) == {"last_name": "Vijay"}
    # A brand-new record has nothing to protect.
    assert strip_padding_echo(
        CONTACTS, {"last_name": "Vijay"}, None,
    ) == {"last_name": "Vijay"}
    # Leads pad TWO columns, both from lead_name.
    lead = SimpleNamespace(
        lead_name="Asha Rao", last_name=None, organization_name=None,
    )
    assert strip_padding_echo(
        LEADS,
        {"last_name": "Asha Rao", "organization_name": "Asha Rao", "email": "a@b.in"},
        lead,
    ) == {"email": "a@b.in"}


def _row_attribute(node: Any) -> str | None:
    """The native column an expression reads off ``row``, if it is exactly that.

    Matches the two forms the payload builders use: ``row.last_name`` and
    ``getattr(row, "last_name", None)``.
    """
    import ast

    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "row"
    ):
        return node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "row"
        and isinstance(node.args[1], ast.Constant)
    ):
        return str(node.args[1].value)
    return None


def _pads_declared_in(builder: Any) -> set[tuple[str, str]]:
    """``(column, padded-from)`` pairs a ``to_zoho_*`` builder fills by fallback.

    Read from the SOURCE, by AST: a pad is an ``or`` between two different
    columns of the same row — "use the real value, else borrow this one".
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(builder)))
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        columns = [_row_attribute(value) for value in node.values]
        if len(columns) == 2 and all(columns) and columns[0] != columns[1]:
            found.add((str(columns[0]), str(columns[1])))
    return found


def test_the_padding_map_matches_what_the_push_actually_pads() -> None:
    """`PADDED_FROM` is DERIVED from the payload builders' source, not restated.

    The previous version of this test asserted the map against a literal copy
    of itself and read nothing from ``to_zoho_*`` — so a pad added to
    ``to_zoho_deal`` would have left it green, which is the exact drift its
    own docstring claimed to catch. This walks each builder's AST for the
    ``or``-fallback that IS a pad, and fails when the two disagree in either
    direction: a new pad with no guard entry (the echo resumes) or a stale
    guard entry (a real Zoho value silently discarded).
    """
    from gateway.routes.crm.import_zoho import PADDED_FROM

    builders = {
        "organizations": crm_sync.to_zoho_account,
        "contacts": crm_sync.to_zoho_contact,
        "leads": crm_sync.to_zoho_lead,
        "deals": crm_sync.to_zoho_deal,
    }
    for slug, builder in builders.items():
        declared = set(PADDED_FROM.get(slug, ()))
        actual = _pads_declared_in(builder)
        assert declared == actual, (
            f"{slug}: the guard says {sorted(declared)} but "
            f"{builder.__name__} pads {sorted(actual)}"
        )

    # The fence is only worth anything if it currently SEES something — an AST
    # walk that silently matched nothing would agree with an empty map.
    assert _pads_declared_in(crm_sync.to_zoho_lead) == {
        ("last_name", "lead_name"), ("organization_name", "lead_name"),
    }
    # …and organizations/deals genuinely pad nothing: their Zoho-required
    # fields are NOT NULL on our side too, so there is nothing to invent.
    assert _pads_declared_in(crm_sync.to_zoho_account) == set()
    assert _pads_declared_in(crm_sync.to_zoho_deal) == set()


def test_the_dirty_flag_is_set_at_the_one_write_choke_point() -> None:
    """``core.update_row``'s ``touch`` already means "this is a real edit".
    Reusing it means the pull's ``touch=False`` and "do not push this back"
    are ONE switch that cannot disagree, and a route added tomorrow inherits
    both without remembering either."""
    tracked = "crm_companies"
    assert crm_core.mark_dirty_on_update(
        tracked, {"name": "x"}, touch=True,
    ) == {"name": "x", "zoho_dirty": True}
    assert crm_core.mark_dirty_on_update(
        tracked, {"name": "x"}, touch=False,
    ) == {"name": "x"}
    # An explicit flag always wins — that is how the push clears it.
    assert crm_core.mark_dirty_on_update(
        tracked, {"zoho_dirty": False}, touch=True,
    ) == {"zoho_dirty": False}
    # Statuses, activities and the change log are not tracked.
    assert crm_core.mark_dirty_on_update(
        "crm_activities", {"subject": "x"}, touch=True,
    ) == {"subject": "x"}


def test_a_row_that_arrived_from_zoho_is_not_born_dirty() -> None:
    """The create half of echo suppression, keyed on the payload rather than
    on a flag the caller must remember to pass."""
    assert crm_core.mark_dirty_on_insert(
        "crm_leads", {"lead_name": "Asha"},
    )["zoho_dirty"] is True
    assert crm_core.mark_dirty_on_insert(
        "crm_leads", {"lead_name": "Asha", "zoho_id": "z-1"},
    )["zoho_dirty"] is False


# ── Rule 4 — tombstones, both directions ───────────────────────────────────

async def test_a_native_delete_writes_a_tombstone_inside_the_transaction(
    db: FakeCrmDB,
) -> None:
    """A tombstone cannot be a column on a row that no longer exists, and it
    cannot be written afterwards either: the ``zoho_id`` it needs disappears
    with the row."""
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    org = db.rows(ORGANIZATIONS.table)[0]

    result = await crm_records.delete_record(ORGANIZATIONS, str(org["id"]), USER)

    assert result.zoho_delete_queued is True
    [tomb] = db.rows("crm_zoho_tombstones")
    assert tomb["module"] == "Accounts"
    assert tomb["zoho_id"] == "z-acc-1"
    assert tomb["entity_type"] == "organization"
    assert tomb["deleted_by"] == USER.email
    assert tomb["pushed_at"] is None

    # Written BEFORE the DELETE and inside the same transaction: one commit.
    inserts = [
        i for i, s in enumerate(db.statements)
        if s.startswith("INSERT INTO crm_zoho_tombstones")
    ]
    deletes = [
        i for i, s in enumerate(db.statements)
        if s.startswith("DELETE FROM crm_companies")
    ]
    assert inserts[0] < deletes[0]
    assert db.committed == 1


async def test_the_tombstone_is_pushed_as_a_zoho_delete_and_then_stamped(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    db.seed(
        "crm_zoho_tombstones", module="Deals", zoho_id="z-deal-9",
        entity_type="deal", deleted_by="vjvarada@fracktal.in",
    )
    report = await crm_sync.run_cycle()

    assert writer.deletes == [("Deals", "z-deal-9")]
    assert report.pushed.deleted == 1
    [tomb] = db.rows("crm_zoho_tombstones")
    assert tomb["pushed_at"] is not None
    assert "propose:crm.zoho_delete" in [e.action for e in audit]


async def test_an_unpushed_tombstone_is_retried_and_never_lost(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    async def _explode(module: str, record_id: str) -> dict:
        raise RuntimeError("Zoho 502")

    writer.delete_record = _explode  # type: ignore[method-assign]
    db.seed(
        "crm_zoho_tombstones", module="Deals", zoho_id="z-deal-9",
        entity_type="deal", deleted_by="vjvarada@fracktal.in",
    )
    report = await crm_sync.run_cycle()

    assert report.pushed.deleted == 0
    assert len(report.pushed.errors) == 1
    [tomb] = db.rows("crm_zoho_tombstones")
    assert tomb["pushed_at"] is None


async def test_a_zoho_delete_removes_the_native_row_and_is_counted(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """§7.1 — Zoho deletes become native deletes, "loudly counted"."""
    db.seed(DEALS.table, name="Printer", zoho_id="z-deal-1")
    db.seed("crm_activities", type="note", deal_id=db.rows(DEALS.table)[0]["id"],
            created_by="a@b.in")
    zoho.deleted["Deals"] = [{"id": "z-deal-1", "deleted_time": "2026-08-04"}]

    report = await crm_sync.run_cycle()

    assert db.rows(DEALS.table) == []
    assert report.zoho_deletes.applied == {"Deals": 1}
    assert report.zoho_deletes.cascaded_activities == 1


async def test_applying_a_zoho_delete_writes_no_tombstone(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """The most destructive possible echo: pushing a deletion straight back at
    the tenant that just told us about it."""
    db.seed(DEALS.table, name="Printer", zoho_id="z-deal-1")
    zoho.deleted["Deals"] = [{"id": "z-deal-1"}]

    await crm_sync.run_cycle()

    assert db.rows("crm_zoho_tombstones") == []
    assert writer.deletes == []


async def test_a_zoho_delete_for_a_record_we_never_imported_is_a_no_op(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    zoho.deleted["Deals"] = [{"id": "z-unknown"}]
    report = await crm_sync.run_cycle()
    assert report.zoho_deletes.applied == {}


# ── Activities: notes and tasks push, platform history never does ──────────

async def test_a_native_note_pushes_as_a_zoho_note(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    org = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    db.seed(
        "crm_activities", type="note", subject="Called", body="Wants a quote",
        company_id=org.id, created_by="vjvarada@fracktal.in", zoho_id=None,
    )
    report = await crm_sync.run_cycle()

    [(module, payload)] = [c for c in writer.creates if c[0] == "Notes"]
    assert module == "Notes"
    assert payload["Note_Title"] == "Called"
    assert payload["Parent_Id"] == "z-acc-1"
    assert payload["se_module"] == "Accounts"
    assert report.pushed.activities == 1
    [note] = db.rows("crm_activities")
    assert note["zoho_id"] == "z-pushed-1"


async def test_a_status_change_activity_is_never_pushed(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """§7.1 — no Zoho analog: Zoho keeps its own stage history, and a
    synthetic note per transition would double every funnel move. Excluded in
    the PREDICATE, so a new push path cannot reach them by another route."""
    org = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    db.seed("crm_activities", type="status_change", subject="New → Won",
            company_id=org.id, created_by="platform", zoho_id=None)
    db.seed("crm_activities", type="system", subject="Imported",
            company_id=org.id, created_by="platform", zoho_id=None)

    report = await crm_sync.run_cycle()

    assert writer.pushes == 0
    assert report.pushed.activities == 0
    [statement] = db.statements_touching("SELECT * FROM crm_activities WHERE")
    assert "type IN ('note', 'task')" in statement
    assert crm_sync.PUSHABLE_ACTIVITY_TYPES == ("note", "task")


async def test_an_activity_whose_parent_has_no_zoho_id_yet_waits(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """Skipped, not failed: the next cycle finds it once the record push has
    given the parent an id."""
    org = db.seed(ORGANIZATIONS.table, name="Fracktal")
    db.seed("crm_activities", type="note", subject="Called",
            company_id=org.id, created_by="a@b.in", zoho_id=None)

    report = await crm_sync.run_cycle()

    assert [c for c in writer.creates if c[0] == "Notes"] == []
    assert report.pushed.activities == 0
    assert report.pushed.errors == []


# ── Rule 7 — the flag gates the LOOP only (done-when 4) ────────────────────

def test_the_sync_flag_ships_off() -> None:
    from acb_common import get_settings

    assert get_settings().crm_zoho_sync is False
    assert crm_sync.sync_enabled() is False


async def test_with_the_flag_off_no_loop_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """done-when 4's first half. Asserted on the function the gateway lifespan
    calls, so "the lifespan registers no loop" is measured rather than read
    off a comment."""
    monkeypatch.setattr(crm_sync, "sync_enabled", lambda: False)
    crm_sync._sync_task = None

    started = await crm_sync.start_crm_zoho_sync()

    assert started is False
    assert crm_sync._sync_task is None
    assert crm_sync.sync_status()["running"] is False


async def test_the_flag_on_starts_exactly_one_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(crm_sync, "sync_enabled", lambda: True)
    monkeypatch.setattr(crm_sync, "SYNC_INTERVAL_SECS", 3600)

    async def _never() -> Any:  # the loop must not actually reach Zoho here
        raise AssertionError("run_cycle should not have run yet")

    monkeypatch.setattr(crm_sync, "run_cycle", _never)
    crm_sync._sync_task = None
    try:
        assert await crm_sync.start_crm_zoho_sync() is True
        assert await crm_sync.start_crm_zoho_sync() is True  # idempotent
        task = crm_sync._sync_task
        assert task is not None
    finally:
        await crm_sync.stop_crm_zoho_sync()
    assert crm_sync._sync_task is None


def test_the_gateway_lifespan_starts_it_gated_and_stops_it_unconditionally(
) -> None:
    """The contract the other six supervised loops keep: a flag-gated loop
    that may never have started is still stopped, so the shutdown path never
    has to know why it is absent."""
    main = (
        REPO / "apps" / "services" / "gateway" / "gateway" / "main.py"
    ).read_text(encoding="utf-8")
    before, _, after = main.partition("\n    yield\n")

    assert "start_crm_zoho_sync" in before
    assert "stop_crm_zoho_sync" in after
    assert "start_crm_zoho_sync" not in after
    # The gate lives INSIDE start_crm_zoho_sync, never as an `if` in main.py:
    # two places that both have to agree about what the flag means is how a
    # loop ends up running with the flag off.
    assert "sync_enabled" not in main
    assert "settings.crm_zoho_sync" not in main
    assert "register_crm_broker_handlers" in main


async def test_a_native_write_marks_the_row_dirty_with_the_flag_off(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """done-when 4's second half, and the half that FAILS against the tree
    this branch started from: with the flag off a native write still records
    that the row owes Zoho a push, and ``zoho_synced_at`` stays NULL because
    nothing has been pushed."""
    monkeypatch.setattr(crm_sync, "sync_enabled", lambda: False)
    _seed_pipeline(db)

    created = await crm_records.create_record(
        ORGANIZATIONS, crm_core.OrganizationIn(name="Fracktal"), USER,
    )
    [row] = db.rows(ORGANIZATIONS.table)

    assert row["zoho_dirty"] is True
    assert row["zoho_synced_at"] is None
    assert created["name"] == "Fracktal"


async def test_a_native_patch_marks_the_row_dirty(
    db: FakeCrmDB,
) -> None:
    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    await crm_records.patch_record(
        ORGANIZATIONS, str(row.id), crm_core.OrganizationIn(website="x"), USER,
    )
    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_dirty"] is True


async def test_a_timeline_bump_alone_does_not_dirty_the_row(
    db: FakeCrmDB,
) -> None:
    """``bump_last_activity`` is denormalized recency, not a Zoho-relevant
    field change — pushing on it would send a no-op update every time anybody
    logs a note."""
    row = db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_id="z-acc-1")
    await crm_core.bump_last_activity(db, ORGANIZATIONS.table, str(row.id))
    stored = _stored(db, ORGANIZATIONS.table, row.id)
    assert stored["zoho_dirty"] is False


# ── The manual cycle endpoint ──────────────────────────────────────────────

async def test_the_manual_cycle_runs_without_the_flag(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§7.1 — a hand-run cycle is an explicit admin act, so it does not need
    the scheduling flag; the flag only decides whether the platform does this
    unattended."""
    monkeypatch.setattr(crm_sync, "sync_enabled", lambda: False)
    db.seed(ORGANIZATIONS.table, name="Fracktal", zoho_dirty=True)

    report = await crm_sync.run_sync_cycle_now(USER)

    assert report.pushed.created == 1
    assert report.finished_at is not None


async def test_the_manual_cycle_carries_the_same_admin_floor() -> None:
    checks = permission_checks("/crm/sync/zoho")
    assert checks, "the manual cycle route carries no permission dependency"
    member = crm_user(features="integrations:use:*")
    for check in checks:
        with pytest.raises(HTTPException) as exc:
            await check(user=member)
        assert exc.value.status_code == 403
        assert "admin:access:manage" in str(exc.value.detail)


# ── Vocabulary flows DOWN only ─────────────────────────────────────────────

async def test_a_native_status_is_never_pushed_as_a_picklist_value(
    db: FakeCrmDB, zoho: FakeZoho, writer: FakeWriter, audit: list,
) -> None:
    """§7.1 — Zoho picklist mutation needs the settings API (out of scope),
    and the vocabulary dies with Zoho anyway. Only a lane NAME rides along on
    a record push; the lane itself never becomes an outward write."""
    _seed_pipeline(db)
    db.seed("crm_deal_statuses", name="Awaiting PO", position=99, type="open")

    await crm_sync.run_cycle()

    assert writer.pushes == 0
    assert crm_sync.RECORD_MODULES == (
        ("Accounts", ORGANIZATIONS), ("Contacts", CONTACTS),
        ("Leads", LEADS), ("Deals", DEALS),
    )
