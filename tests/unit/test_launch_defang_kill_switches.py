"""WS-29 launch defang — the two startup kill-switches on the gateway lifespan.

Offline: no DB, no Redis, no network. The start functions the gateway lifespan
would call are monkeypatched with async mocks, so these tests assert the GUARD
around the startup call (``main._maybe_start_email_sync`` /
``main._maybe_start_workflow_scheduler``) without booting the whole app or
creating any real background task.

Two always-on loops are OUT of launch scope (Tasks, Calendar, Projects,
User-management + agent chat) and not yet tenant-bound, so after the RLS cutover
they would write UNBOUND under FORCE ROW LEVEL SECURITY. Each loop's startup is
gated behind a DEFAULT-ON env flag; the cutover runbook sets it false to STOP the
loop cleanly. Default ON = byte-identical to today (dark).

Fences (R7):
  - ``email-sync-loop-gated``      — EMAIL_SYNC_ENABLED=false ⇒ the email-sync
    loop is not started; default/true ⇒ started as today.
  - ``workflow-scheduler-gated``   — WORKFLOW_SCHEDULER_ENABLED=false ⇒ neither
    the cron scanner nor the orphan reconciler start; default/true ⇒ both start.

RED-on-removal: drop the ``if not _*_enabled(): return`` guard from either
coroutine (call the start functions unconditionally) and the flag-off tests below
go red — the mocks are awaited when the runbook needs them silent.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from gateway import main

# ---------------------------------------------------------------------------
# The default-ON predicate: OFF only on an explicit falsey token
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # unset / empty / unrecognised = ON (preserve today's behaviour)
        (None, True), ("", True), ("  ", True), ("maybe", True), ("1", True),
        ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        # the cutover runbook's explicit off tokens
        ("0", False), ("false", False), ("FALSE", False), ("no", False),
        ("off", False), (" off ", False),
    ],
)
def test_flag_default_on_semantics(monkeypatch, raw, expected):
    name = "WS29_DEFANG_PROBE"
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)
    assert main._flag_default_on(name) is expected


def test_named_predicates_read_os_environ_at_call_time(monkeypatch):
    """Not a cached Settings field — a value read once at import would freeze the
    flag for the process and the cutover flip would never take."""
    for name, pred in (
        ("EMAIL_SYNC_ENABLED", main._email_sync_enabled),
        ("WORKFLOW_SCHEDULER_ENABLED", main._workflow_scheduler_enabled),
    ):
        monkeypatch.delenv(name, raising=False)
        assert pred() is True  # default ON
        monkeypatch.setenv(name, "false")
        assert pred() is False


# ---------------------------------------------------------------------------
# email-sync-loop-gated
# ---------------------------------------------------------------------------

@pytest.fixture
def email_start(monkeypatch):
    """Intercept the loop launch the guard would call (function-body import)."""
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("email_ingestion.scheduler.start_background_sync", mock)
    return mock


async def test_email_sync_starts_by_default(monkeypatch, email_start):
    monkeypatch.delenv("EMAIL_SYNC_ENABLED", raising=False)
    await main._maybe_start_email_sync()
    email_start.assert_awaited_once()


async def test_email_sync_starts_when_explicitly_true(monkeypatch, email_start):
    monkeypatch.setenv("EMAIL_SYNC_ENABLED", "true")
    await main._maybe_start_email_sync()
    email_start.assert_awaited_once()


@pytest.mark.parametrize("off", ["false", "0", "no", "off", "FALSE"])
async def test_email_sync_does_not_start_when_flag_off(monkeypatch, email_start, off):
    monkeypatch.setenv("EMAIL_SYNC_ENABLED", off)
    await main._maybe_start_email_sync()
    email_start.assert_not_awaited()


# ---------------------------------------------------------------------------
# workflow-scheduler-gated — one flag gates BOTH the reconciler and the scanner
# ---------------------------------------------------------------------------

@pytest.fixture
def workflow_starts(monkeypatch):
    reconcile = AsyncMock(return_value=0)
    scheduler = AsyncMock(return_value=None)
    monkeypatch.setattr("gateway.routes.workflows.reconcile_orphaned_runs", reconcile)
    monkeypatch.setattr("gateway.routes.workflows.start_workflow_scheduler", scheduler)
    return reconcile, scheduler


async def test_workflow_subsystem_starts_by_default(monkeypatch, workflow_starts):
    reconcile, scheduler = workflow_starts
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)
    await main._maybe_start_workflow_scheduler()
    reconcile.assert_awaited_once()
    scheduler.assert_awaited_once()


async def test_workflow_subsystem_starts_when_explicitly_true(monkeypatch, workflow_starts):
    reconcile, scheduler = workflow_starts
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", "true")
    await main._maybe_start_workflow_scheduler()
    reconcile.assert_awaited_once()
    scheduler.assert_awaited_once()


@pytest.mark.parametrize("off", ["false", "0", "no", "off", "FALSE"])
async def test_workflow_subsystem_does_not_start_when_flag_off(
    monkeypatch, workflow_starts, off
):
    reconcile, scheduler = workflow_starts
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENABLED", off)
    await main._maybe_start_workflow_scheduler()
    reconcile.assert_not_awaited()
    scheduler.assert_not_awaited()


# ---------------------------------------------------------------------------
# The two flags are independent — one off must not silence the other
# ---------------------------------------------------------------------------

async def test_flags_are_independent(monkeypatch, email_start, workflow_starts):
    reconcile, scheduler = workflow_starts
    monkeypatch.setenv("EMAIL_SYNC_ENABLED", "false")
    monkeypatch.delenv("WORKFLOW_SCHEDULER_ENABLED", raising=False)

    await main._maybe_start_email_sync()
    await main._maybe_start_workflow_scheduler()

    email_start.assert_not_awaited()
    reconcile.assert_awaited_once()
    scheduler.assert_awaited_once()
