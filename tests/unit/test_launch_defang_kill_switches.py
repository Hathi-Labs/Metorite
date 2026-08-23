"""WS-29 launch defang — the two startup kill-switches, tested at their HOME.

Offline: no DB, no Redis, no network. Each ``start_X()`` function is probed at
the first side-effect PAST its flag gate — an engine open, a task create, a DB
acquire — so "did the flag let this loop start?" is a hermetic yes/no with no
real background task and no connection.

Two always-on loops are OUT of launch scope (Tasks, Calendar, Projects,
User-management + agent chat) and not yet tenant-bound, so after the RLS cutover
they would write UNBOUND under FORCE ROW LEVEL SECURITY. Each loop's startup is
gated behind a DEFAULT-ON env flag; the cutover runbook sets it false to STOP the
loop cleanly. Default ON = byte-identical to today (dark).

The gate lives INSIDE each start function — never as an ``if`` at the gateway
call site (``main.py``). Two places that both have to agree about what the flag
means is how a loop ends up running with the flag off; this mirrors how
``routes/crm/sync_zoho.start_crm_zoho_sync`` gates itself, and is ratcheted in
``main.py`` by ``test_crm_zoho_sync.py`` (``"sync_enabled" not in main``) and by
``test_main_gates_inside_start_functions_not_at_the_call_site`` below.

Fences (R7):
  - ``email-sync-loop-gated``      — EMAIL_SYNC_ENABLED=false ⇒
    ``email_ingestion.scheduler.start_background_sync`` returns ``{}`` and opens
    no engine; default/true ⇒ it proceeds to start as today.
  - ``workflow-scheduler-gated``   — WORKFLOW_SCHEDULER_ENABLED=false ⇒ neither
    ``start_workflow_scheduler`` (the cron scanner) nor ``reconcile_orphaned_runs``
    (its sibling sweep) starts; default/true ⇒ both proceed.

RED-on-removal: drop the ``if os.getenv(...): return`` guard from any of the
three functions (start unconditionally) and the flag-off tests below go red —
the loop is reached when the runbook needs it silent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from email_ingestion import scheduler as email_scheduler
from gateway.routes.workflows import scheduler as wf_scheduler
from gateway.routes.workflows import service as wf_service

REPO = Path(__file__).resolve().parents[2]

# The falsey tokens the cutover runbook writes to STOP a loop.
OFF_TOKENS = ["false", "0", "no", "off", "FALSE", " off "]


class _PastGate(Exception):
    """Raised by a probe wired at the first side-effect PAST the flag gate, so
    reaching it proves the gate let the start through, and never reaching it
    proves the gate short-circuited."""


# ---------------------------------------------------------------------------
# email-sync-loop-gated — EMAIL_SYNC_ENABLED inside start_background_sync
# ---------------------------------------------------------------------------

@pytest.fixture
def email_probe(monkeypatch):
    """``start_background_sync``'s first act past the gate is opening an engine.

    Make that raise ``_PastGate`` so "got past the gate" is observable without a
    DB. ``_scheduler_running`` is reset False so an ON call actually reaches the
    engine open, and ``DATABASE_URL`` is set so ``_get_db_url`` returns before
    the probe fires rather than consulting real settings.
    """
    monkeypatch.setattr(email_scheduler, "_scheduler_running", False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://probe/none")

    def _boom(*_a, **_k):
        raise _PastGate

    monkeypatch.setattr(email_scheduler, "create_async_engine", _boom)


async def test_email_sync_starts_by_default(monkeypatch, email_probe):
    monkeypatch.delenv("EMAIL_SYNC_ENABLED", raising=False)
    with pytest.raises(_PastGate):
        await email_scheduler.start_background_sync()


async def test_email_sync_starts_when_explicitly_true(monkeypatch, email_probe):
    monkeypatch.setenv("EMAIL_SYNC_ENABLED", "true")
    with pytest.raises(_PastGate):
        await email_scheduler.start_background_sync()


@pytest.mark.parametrize("off", OFF_TOKENS)
async def test_email_sync_does_not_start_when_flag_off(monkeypatch, email_probe, off):
    monkeypatch.setenv("EMAIL_SYNC_ENABLED", off)
    # No _PastGate: the gate returned before the engine open. An empty dict is
    # the "no loops launched" signature.
    assert await email_scheduler.start_background_sync() == {}


# ---------------------------------------------------------------------------
# workflow-scheduler-gated — one flag gates BOTH the scanner AND the reconciler
# ---------------------------------------------------------------------------

@pytest.fixture
def wf_scheduler_probe(monkeypatch):
    """Reset the module task and stub the loop body: a started task must do no
    real work (the real ``_scheduler_loop`` is a while-True that hits the DB)."""
    monkeypatch.setattr(wf_scheduler, "_scheduler_task", None)

    async def _noop_loop():
        return

    monkeypatch.setattr(wf_scheduler, "_scheduler_loop", _noop_loop)


async def test_workflow_scheduler_starts_by_default(monkeypatch, wf_scheduler_probe):
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    await wf_scheduler.start_workflow_scheduler()
    assert wf_scheduler._scheduler_task is not None
    await wf_scheduler.stop_workflow_scheduler()


async def test_workflow_scheduler_starts_when_explicitly_true(
    monkeypatch, wf_scheduler_probe
):
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", "true")
    await wf_scheduler.start_workflow_scheduler()
    assert wf_scheduler._scheduler_task is not None
    await wf_scheduler.stop_workflow_scheduler()


@pytest.mark.parametrize("off", OFF_TOKENS)
async def test_workflow_scheduler_does_not_start_when_flag_off(
    monkeypatch, wf_scheduler_probe, off
):
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", off)
    await wf_scheduler.start_workflow_scheduler()
    assert wf_scheduler._scheduler_task is None


@pytest.fixture
def reconcile_probe(monkeypatch):
    """Track whether ``reconcile_orphaned_runs`` reaches its DB acquire. The
    fake raises, which the function's own try/except swallows into a 0 — so the
    tracker, not the return value, is what distinguishes gated-off from
    reached-the-sweep."""
    calls: list[bool] = []

    async def _fake_get_db():
        calls.append(True)
        raise RuntimeError("no db in this hermetic test")

    monkeypatch.setattr(wf_service, "_get_db", _fake_get_db)
    return calls


async def test_reconcile_runs_by_default(monkeypatch, reconcile_probe):
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    result = await wf_service.reconcile_orphaned_runs()
    assert reconcile_probe == [True]  # got past the gate to the DB sweep
    assert result == 0  # the sentinel is swallowed by reconcile's own try/except


async def test_reconcile_runs_when_explicitly_true(monkeypatch, reconcile_probe):
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", "true")
    result = await wf_service.reconcile_orphaned_runs()
    assert reconcile_probe == [True]
    assert result == 0


@pytest.mark.parametrize("off", OFF_TOKENS)
async def test_reconcile_does_not_run_when_flag_off(monkeypatch, reconcile_probe, off):
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", off)
    result = await wf_service.reconcile_orphaned_runs()
    assert reconcile_probe == []  # gate short-circuited before touching the DB
    assert result == 0


# ---------------------------------------------------------------------------
# Default-ON semantics — unset/empty/unrecognised ⇒ ON, falsey tokens ⇒ OFF
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "enabled"),
    [
        # unset / empty / unrecognised / truthy = ON (preserve today's behaviour)
        (None, True), ("", True), ("  ", True), ("maybe", True), ("1", True),
        ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        # the cutover runbook's explicit off tokens
        ("0", False), ("false", False), ("FALSE", False), ("no", False),
        ("off", False), (" off ", False),
    ],
)
async def test_email_flag_default_on_semantics(monkeypatch, email_probe, raw, enabled):
    """The gate's token vocabulary, read through the real function: a value read
    once at import would freeze the flag for the process and the cutover flip
    would never take, so this exercises ``start_background_sync`` directly."""
    if raw is None:
        monkeypatch.delenv("EMAIL_SYNC_ENABLED", raising=False)
    else:
        monkeypatch.setenv("EMAIL_SYNC_ENABLED", raw)
    if enabled:
        with pytest.raises(_PastGate):
            await email_scheduler.start_background_sync()
    else:
        assert await email_scheduler.start_background_sync() == {}


# ---------------------------------------------------------------------------
# The convention itself: main.py gates NOTHING — it calls all three unconditionally
# ---------------------------------------------------------------------------

def test_main_gates_inside_start_functions_not_at_the_call_site():
    """The structural contract this slice restored (R7): the flag gate lives
    INSIDE each start function, so ``main.py`` has no gating ``if`` for these
    loops, none of the deleted call-site helpers, and no ``sync_enabled`` — the
    same ratchet the other supervised loops keep (see ``test_crm_zoho_sync.py``)."""
    main_src = (
        REPO / "apps" / "services" / "gateway" / "gateway" / "main.py"
    ).read_text(encoding="utf-8")
    before, sep, _after = main_src.partition("\n    yield\n")
    assert sep, "lifespan yield not found"

    # The deleted call-site helpers are gone.
    for name in (
        "_maybe_start_email_sync",
        "_maybe_start_workflow_scheduler",
        "_flag_default_on",
        "_email_sync_enabled",
        "_workflow_scheduler_enabled",
    ):
        assert name not in main_src, name

    # All three loops are started UNCONDITIONALLY, before the yield.
    assert "start_background_sync()" in before
    assert "reconcile_orphaned_runs()" in before
    assert "start_workflow_scheduler()" in before

    # The ratchet: no flag reader may live in main.py.
    assert "sync_enabled" not in main_src
