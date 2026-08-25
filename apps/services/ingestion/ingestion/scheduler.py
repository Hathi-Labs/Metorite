"""Nightly scheduler — wraps the Phase-0 jobs with APScheduler.

Run as a foreground process; long-lived. In production deploy as a systemd
service or `docker compose` worker. Times are local-server time.

    uv run python -m ingestion.scheduler          # default schedule (below)
    uv run python -m ingestion.scheduler --once   # run every job immediately and exit

Default schedule (Asia/Kolkata):
    02:50  zoho_sync
    03:10  reconciler.run()

⚠️ The 02:30 ``clickup_sync`` job was REMOVED 2026-08-24 by **D52** (board WS-39
S1), which retired ClickUp outright — there is no connector to poll. Do not
re-add a job here for a project-management source: Metorite is the PM system of
record (root ``AGENTS.md`` constraint 8, amended).
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from acb_audit import AuditEvent, record

_log = structlog.get_logger(__name__)


async def _run_zoho() -> None:
    from scripts import zoho_sync

    _log.info("scheduler.zoho_sync.start")
    try:
        await zoho_sync.main()
        record(AuditEvent(actor="job:scheduler", action="run_ok", target="job:zoho_sync", payload={}))
    except Exception as exc:
        _log.exception("scheduler.zoho_sync.failed")
        record(
            AuditEvent(
                actor="job:scheduler",
                action="run_failed",
                target="job:zoho_sync",
                payload={"error": str(exc)[:500]},
            )
        )


async def _run_reconciler() -> None:
    from scripts import reconciler

    _log.info("scheduler.reconciler.start")
    try:
        # reconciler.run is sync — push to executor so we do not block the loop.
        loop = asyncio.get_running_loop()
        counts: dict[str, Any] = await loop.run_in_executor(None, reconciler.run)
        record(
            AuditEvent(
                actor="job:scheduler",
                action="run_ok",
                target="job:reconciler",
                payload=counts,
            )
        )
    except Exception as exc:
        _log.exception("scheduler.reconciler.failed")
        record(
            AuditEvent(
                actor="job:scheduler",
                action="run_failed",
                target="job:reconciler",
                payload={"error": str(exc)[:500]},
            )
        )


async def _run_once() -> None:
    await _run_zoho()
    await _run_reconciler()


def build_scheduler(*, tz: str = "Asia/Kolkata") -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(_run_zoho, CronTrigger(hour=2, minute=50, timezone=tz), id="zoho_sync")
    sched.add_job(_run_reconciler, CronTrigger(hour=3, minute=10, timezone=tz), id="reconciler")
    return sched


async def _serve() -> None:
    sched = build_scheduler()
    sched.start()
    record(
        AuditEvent(
            actor="job:scheduler",
            action="scheduler_started",
            target="scheduler",
            payload={
                "jobs": [
                    {"id": j.id, "trigger": str(j.trigger)}
                    for j in sched.get_jobs()
                ]
            },
        )
    )
    _log.info("scheduler.started", jobs=[j.id for j in sched.get_jobs()])
    try:
        # Block forever — APScheduler runs on the same event loop.
        await asyncio.Event().wait()
    finally:
        sched.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="Run all three jobs once and exit.")
    args = ap.parse_args()
    if args.once:
        asyncio.run(_run_once())
    else:
        asyncio.run(_serve())


if __name__ == "__main__":
    main()