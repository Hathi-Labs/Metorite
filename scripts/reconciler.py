"""Reconciler v0 — surface stale tasks and quiet deals into the escalation queue.

Detection rules (Phase-0):
  * Stale task   : task.days_in_stage >= 14 AND stage not in (done/closed/...)
  * Quiet deal   : deal.last_activity_at older than 14d AND stage not in
                   (Closed Won / Closed Lost).

For each finding, we write a single AuditEvent with action='escalation' and a
JSON payload that the escalation UI (Streamlit, later) can render directly.

Run:  uv run python -m scripts.reconciler
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import structlog

from acb_audit import AuditEvent, record, refuse_tenantless
from acb_graph import get_session, repo

_log = structlog.get_logger(__name__)


def _audit(event: AuditEvent) -> None:
    """Record *event*, unless it is a row with no org and the bind is on.

    Task 2221 slice 2. This job opens the unbound ``get_session`` and binds
    no tenant. Each ``escalation`` row holds a task title, a customer name or
    a deal value. The ``run_summary`` row counts those records. So each row
    is tenant data, and none is a pure system row. With no org and
    ``ACB_GRAPH_TENANT_BIND`` on, ``refuse_tenantless`` skips it, and it never
    lands under the operator org. With the bind off, the row stays as before.
    Fence: ``tests/unit/test_audit_tick_writers_org.py``.
    """
    if not refuse_tenantless(event):
        record(event)


def _isoformat(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def run(*, task_days: int = 14, deal_days: int = 14) -> dict[str, int]:
    """Scan DB for stale items and emit escalation audit events."""
    counts = {"stale_tasks": 0, "quiet_deals": 0}

    with get_session() as s:
        for t in repo.stale_tasks(s, min_days_in_stage=task_days):
            project_name = t.project.name if t.project else None
            owner_name = t.owner.canonical_name if t.owner else None
            payload = {
                "kind": "stale_task",
                "task_id": str(t.id),
                "task_clickup_id": t.clickup_id,
                "title": t.title,
                "stage": t.stage,
                "days_in_stage": t.days_in_stage,
                "project": project_name,
                "owner": owner_name,
                "updated_at": _isoformat(t.updated_at),
                "cite": f"[task:{t.id}]",
            }
            _audit(
                AuditEvent(
                    actor="job:reconciler",
                    action="escalation",
                    target=f"task:{t.id}",
                    payload=payload,
                )
            )
            counts["stale_tasks"] += 1

        for d in repo.quiet_deals(s, min_days_quiet=deal_days):
            owner_name = d.owner.canonical_name if d.owner else None
            customer_name = d.customer.name if d.customer else None
            days_quiet = None
            if d.last_activity_at is not None:
                last = d.last_activity_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days_quiet = (datetime.now(timezone.utc) - last).days
            payload = {
                "kind": "quiet_deal",
                "deal_id": str(d.id),
                "deal_zoho_id": d.zoho_id,
                "name": d.name,
                "stage": d.stage,
                "customer": customer_name,
                "owner": owner_name,
                "last_activity_at": _isoformat(d.last_activity_at),
                "days_quiet": days_quiet,
                "value_inr": str(d.value_inr) if d.value_inr is not None else None,
                "cite": f"[deal:{d.id}]",
            }
            _audit(
                AuditEvent(
                    actor="job:reconciler",
                    action="escalation",
                    target=f"deal:{d.id}",
                    payload=payload,
                )
            )
            counts["quiet_deals"] += 1

    # One summary audit event so we can tell when a run happened even if zero findings.
    _audit(
        AuditEvent(
            actor="job:reconciler",
            action="run_summary",
            target="reconciler",
            payload={**counts, "task_days": task_days, "deal_days": deal_days},
        )
    )
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task-days", type=int, default=14)
    ap.add_argument("--deal-days", type=int, default=14)
    args = ap.parse_args()
    summary = run(task_days=args.task_days, deal_days=args.deal_days)
    print("=== Reconciler run complete ===")
    for k, v in summary.items():
        print(f"  {k:>14}: {v}")


if __name__ == "__main__":
    main()