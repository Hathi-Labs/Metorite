"""Run with: uv run python -m reconciler.main

Cron entry point (WBS 1.6). Delegates the actual stale-task / quiet-deal scan
to ``scripts.reconciler`` so that the same code path is exercised whether the
job is invoked from APScheduler (apps.services.ingestion.scheduler) or from a one-shot
`docker compose run reconciler` command.
"""
from __future__ import annotations

import asyncio

from acb_audit import AuditEvent, record, refuse_tenantless
from acb_common import configure_logging, get_logger, get_settings


def _audit(event: AuditEvent) -> None:
    """Record *event*, unless it is a row with no org and the bind is on.

    Task 2221 slice 2. This job binds no tenant: ``scripts.reconciler`` opens
    the unbound ``get_session``. The row counts the stale tasks and the quiet
    deals of the tenants it saw, and ``acb_audit`` has no system marker. So
    with no org and ``ACB_GRAPH_TENANT_BIND`` on, ``refuse_tenantless`` skips
    the row, and it never lands under the operator org. With the bind off,
    the row stays as before. The ``reconciler.done`` log line keeps the
    counts. Fence: ``tests/unit/test_audit_tick_writers_org_r8.py`` (R8).
    """
    if not refuse_tenantless(event):
        record(event)


async def run(*, task_days: int = 14, deal_days: int = 14) -> int:
    """Run the reconciler and return the number of escalations emitted."""
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("reconciler")
    log.info("reconciler.start", env=settings.acb_env)

    # Lazy import keeps `python -m reconciler.main --help` cold-start cheap.
    from scripts import reconciler as core

    loop = asyncio.get_running_loop()
    counts = await loop.run_in_executor(
        None, lambda: core.run(task_days=task_days, deal_days=deal_days)
    )
    escalations = sum(counts.values())
    log.info("reconciler.done", **counts, escalations=escalations)
    _audit(
        AuditEvent(
            actor="job:reconciler-app",
            action="run_ok",
            target="job:reconciler",
            payload={**counts, "escalations": escalations},
        )
    )
    return escalations


def main() -> None:  # entry point for `python -m reconciler.main`
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
