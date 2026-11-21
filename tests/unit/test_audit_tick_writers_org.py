"""Task 2221, slice 2 — the no-database half: system ticks and webhooks.

Eight audit writers record an event with no ``organization_id``. With
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit._persist`` filed each row under the
OPERATOR org (Option-A):

* ``ingestion.scheduler``: ``run_ok`` and ``run_failed`` of the Zoho job and of
  the reconciler job, and ``scheduler_started``.
* ``ingestion.sources.gmail.webhook.receive``: ``received`` (the mailbox).
* ``ingestion.sources.zoho.webhook.receive``: ``received`` (the CRM event).
* ``reconciler.main.run``: ``run_ok`` (the counts).

No writer has a tenant source. The jobs open the unbound ``get_session``.
Each webhook checks one shared secret for the deployment, and no code maps the
mailbox or the module to an org. ``acb_audit`` has no system marker. So each
writer goes through a local ``_audit``: with no org and the bind on,
``refuse_tenantless`` skips the row. With the bind off, the row stays as
before, so the change is dark. A payload field never gives the org (R5).

This file proves the skip and the dark path with no database. The real table
under FORCE RLS is ``test_audit_tick_writers_org_r8.py`` (R8).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
GMAIL_TOKEN = "t2221-gmail-token"
ZOHO_SECRET = "t2221-zoho-secret"
#: Text that stands for one tenant's data in an error message.
TENANT_TEXT = "org B deal Acme owes 12 lakh"
COUNTS = {"stale_tasks": 3, "quiet_deals": 2}

#: The module and the source path of each writer.
WRITERS = {
    "ingestion.scheduler": "apps/services/ingestion/ingestion/scheduler.py",
    "ingestion.sources.gmail.webhook":
        "apps/services/ingestion/ingestion/sources/gmail/webhook.py",
    "ingestion.sources.zoho.webhook":
        "apps/services/ingestion/ingestion/sources/zoho/webhook.py",
    "reconciler.main": "apps/services/reconciler/reconciler/main.py",
}


# ==========================================================================
# Helpers. The R8 file imports these.
# ==========================================================================
async def _drained(coro) -> Any:
    from acb_audit import log as audit

    out = await coro
    await audit.drain()
    return out


def _zoho_job(mp, *, fail: bool) -> None:
    from ingestion import scheduler

    import scripts.zoho_sync as zoho_sync

    async def _main() -> None:
        if fail:
            raise RuntimeError(TENANT_TEXT)

    mp.setattr(zoho_sync, "main", _main)
    asyncio.run(_drained(scheduler._run_zoho()))


def _reconciler_job(mp, *, fail: bool) -> None:
    from ingestion import scheduler

    import scripts.reconciler as core

    def _run(**_k: Any) -> dict[str, int]:
        if fail:
            raise RuntimeError(TENANT_TEXT)
        return dict(COUNTS)

    mp.setattr(core, "run", _run)
    asyncio.run(_drained(scheduler._run_reconciler()))


def _scheduler_started(_mp) -> None:
    """Start the real scheduler, then stop it after the first audit write."""
    from ingestion import scheduler

    async def _go() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(scheduler._serve(), timeout=0.5)

    asyncio.run(_drained(_go()))


async def _post(router, path: str, body: dict, headers: dict[str, str]) -> int:
    import httpx
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ingest.test",
    ) as client:
        got = await client.post(path, json=body, headers=headers)
    return got.status_code


def _quiet_receivers(mp) -> None:
    """No Redis and no event sink. The consumer path enqueues only."""
    from ingestion.sources.gmail import webhook as gmail_webhook
    from ingestion.sources.zoho import webhook as zoho_webhook

    mp.setenv("INGESTION_CONSUMER", "1")
    for mod in (gmail_webhook, zoho_webhook):
        mp.setattr(mod, "enqueue", lambda *_a, **_k: "0-0")
    mp.setattr(gmail_webhook, "get_settings",
               lambda: SimpleNamespace(gmail_pubsub_token=GMAIL_TOKEN))
    mp.setattr(zoho_webhook, "get_settings",
               lambda: SimpleNamespace(zoho_webhook_secret=ZOHO_SECRET))


def _gmail_push(mp, mailbox: str | None = None) -> str:
    """One authenticated Pub/Sub push for *mailbox*. Returns the audit target."""
    from ingestion.sources.gmail import webhook as gmail_webhook

    _quiet_receivers(mp)
    mailbox = mailbox or f"ops-{uuid.uuid4().hex[:8]}@orgb.test"
    data = base64.b64encode(
        json.dumps({"emailAddress": mailbox, "historyId": 42}).encode(),
    ).decode()
    status = asyncio.run(_drained(_post(
        gmail_webhook.router, "/webhooks/gmail",
        {"message": {"data": data, "messageId": "1"}},
        {"Authorization": f"Bearer {GMAIL_TOKEN}"},
    )))
    assert status == 200, "a skipped audit row must not fail the push"
    return f"gmail:{mailbox}"


def _zoho_push(mp, module: str | None = None) -> str:
    """One authenticated Zoho push for *module*. Returns the audit target."""
    from ingestion.sources.zoho import webhook as zoho_webhook

    _quiet_receivers(mp)
    module = module or f"Deals{uuid.uuid4().hex[:6]}"
    status = asyncio.run(_drained(_post(
        zoho_webhook.router, "/webhooks/zoho",
        {"event": "Deals.update", "module": module},
        {"X-Zoho-Token": ZOHO_SECRET},
    )))
    assert status == 200, "a skipped audit row must not fail the push"
    return f"zoho:{module}"


def _reconciler_app(mp) -> None:
    from reconciler import main as reconciler_main

    import scripts.reconciler as core

    mp.setattr(core, "run", lambda **_k: dict(COUNTS))
    mp.setattr(reconciler_main, "configure_logging", lambda *_a, **_k: None)
    assert asyncio.run(_drained(reconciler_main.run())) == sum(COUNTS.values())


#: One case for each event: (id, driver, actor, action, target or None).
CASES: list[tuple[str, Callable[[Any], Any], str, str, str | None]] = [
    ("zoho_sync_run_ok", lambda mp: _zoho_job(mp, fail=False),
     "job:scheduler", "run_ok", "job:zoho_sync"),
    ("zoho_sync_run_failed", lambda mp: _zoho_job(mp, fail=True),
     "job:scheduler", "run_failed", "job:zoho_sync"),
    ("reconciler_run_ok", lambda mp: _reconciler_job(mp, fail=False),
     "job:scheduler", "run_ok", "job:reconciler"),
    ("reconciler_run_failed", lambda mp: _reconciler_job(mp, fail=True),
     "job:scheduler", "run_failed", "job:reconciler"),
    ("scheduler_started", _scheduler_started,
     "job:scheduler", "scheduler_started", "scheduler"),
    ("gmail_received", _gmail_push, "webhook:gmail", "received", None),
    ("zoho_received", _zoho_push, "webhook:zoho", "received", None),
    ("reconciler_app_run_ok", _reconciler_app,
     "job:reconciler-app", "run_ok", "job:reconciler"),
]
CASE_IDS = [c[0] for c in CASES]


def spy_on_writers(mp, sink: Callable[[Any], None]) -> None:
    """Point the ``record`` name of each writer module at *sink*."""
    import importlib

    for name in WRITERS:
        mp.setattr(importlib.import_module(name), "record", sink)


# ==========================================================================
# Fixtures
# ==========================================================================
@pytest.fixture
def events(monkeypatch) -> list[Any]:
    calls: list[Any] = []
    spy_on_writers(monkeypatch, calls.append)
    return calls


# ==========================================================================
# 1. The bind on: no writer reaches the audit seam.
# ==========================================================================
@pytest.mark.parametrize(("case", "drive", "actor", "action", "target"), CASES, ids=CASE_IDS)
def test_with_the_bind_on_a_tick_with_no_tenant_records_nothing(
    events, monkeypatch, case, drive, actor, action, target,
) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    drive(monkeypatch)
    assert events == [], (
        f"{case}: a row with no tenant reached the audit seam, so the "
        "operator org fallback would take it"
    )


# ==========================================================================
# 2. The bind off: each row stays as before (dark).
# ==========================================================================
@pytest.mark.parametrize(("case", "drive", "actor", "action", "target"), CASES, ids=CASE_IDS)
def test_with_the_bind_off_each_row_stays(
    events, monkeypatch, case, drive, actor, action, target,
) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    got_target = drive(monkeypatch)
    want = target or got_target
    assert [(e.actor, e.action, e.target, e.organization_id) for e in events] == [
        (actor, action, want, None),
    ], f"{case}: with the bind off the row must stay as before"


def test_a_failure_row_keeps_its_error_text_with_the_bind_off(events, monkeypatch) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    _zoho_job(monkeypatch, fail=True)
    [ev] = events
    assert TENANT_TEXT in ev.payload["error"]


# ==========================================================================
# 3. The source: each writer has one ``record`` call, inside ``_audit``.
# ==========================================================================
@pytest.mark.parametrize("path", sorted(WRITERS.values()))
def test_each_writer_records_only_through_the_guard(path: str) -> None:
    src = (REPO / path).read_text(encoding="utf-8")
    assert src.count("record(") == 1, (
        f"{path}: a second record( call. Call _audit, which runs "
        "refuse_tenantless first"
    )
    assert "    if not refuse_tenantless(event):\n        record(event)" in src
    assert "organization_id=" not in src, (
        f"{path}: an org on an audit row here must come from a bound tenant, "
        "and this module has none. Never take it from the payload (R5)"
    )
