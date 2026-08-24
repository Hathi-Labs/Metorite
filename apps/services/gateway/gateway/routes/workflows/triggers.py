"""Event triggers — bind workflows to platform events (spec F10, RFC §7).

The successor to ``agent_registry.json``'s hard-coded ``webhook_routes``: a
``workflow_triggers`` row with ``kind='event'`` and config
``{"source": "zoho", "event_type": "Contacts.edit"}`` (empty/omitted
``event_type`` matches every event from that source). Two entry points feed
this, both existing plumbing:

- the signed generic webhook ``POST /agent/webhook/{source}`` (routes/agent.py)
  calls :func:`dispatch_event` right after its agent routing, and
- the native provider receivers (Zoho and Gmail) emit through the ingestion
  event-hook registry (``ingestion.event_hooks``), which the gateway wires to
  :func:`dispatch_event` at startup — the lower layer never imports upward
  (the ``post_sync.py`` hook pattern).

All matches converge on the same run entrypoint as every other trigger kind.
"""

from __future__ import annotations

from typing import Any

# H4: event consumer, not a request handler — `dispatch_event` fires from the
# webhook receivers' sink fan-out with no member session, so there is no bound
# tenant to inherit and inheriting an ambient one is exactly what H4 forbids.
# Stays on the unbound `get_db()` until H4 threads an explicit tenant through
# the event payload (`tenant_session(org_id)` derived from the workflow row's
# organization).
from gateway.routes.workflows.core import _get_db, _log, parse_jsonb
from gateway.routes.workflows.service import (
    RunRejected,
    load_version_serialized,
    start_run,
)
from sqlalchemy import text


def event_trigger_matches(config: dict[str, Any], source: str, event_type: str) -> bool:
    """Pure matcher: source must equal; empty event_type matches all."""
    if str(config.get("source") or "").strip() != source:
        return False
    wanted = str(config.get("event_type") or "").strip()
    return not wanted or wanted == event_type


async def dispatch_event(
    source: str, event_type: str, payload: dict[str, Any]
) -> list[dict[str, str]]:
    """Start a run for every published workflow bound to this event.

    Best-effort and never raises — event delivery must not break the webhook
    receivers that call it. Returns ``[{workflow_id, run_id}, …]``.
    """
    started: list[dict[str, str]] = []
    try:
        db = await _get_db()
        try:
            rows = (
                await db.execute(
                    text(
                        """SELECT t.config, w.id AS workflow_id, w.name,
                                  w.latest_version, w.variables
                             FROM workflow_triggers t
                             JOIN workflows w ON w.id = t.workflow_id
                            WHERE t.kind = 'event' AND t.enabled
                              AND w.status = 'published'
                              AND w.latest_version IS NOT NULL"""
                    ),
                )
            ).fetchall()
            for row in rows:
                config = parse_jsonb(row.config, {}) or {}
                if not event_trigger_matches(config, source, event_type):
                    continue
                serialized = await load_version_serialized(
                    db, str(row.workflow_id), int(row.latest_version)
                )
                if serialized is None:
                    continue
                try:
                    run_id = await start_run(
                        workflow_id=str(row.workflow_id),
                        workflow_name=row.name,
                        version=int(row.latest_version),
                        serialized=serialized,
                        trigger_kind="event",
                        trigger_payload={
                            "source": source,
                            "event_type": event_type,
                            **payload,
                        },
                        variables=parse_jsonb(row.variables, {}),
                        started_by=f"event:{source}",
                    )
                    started.append({"workflow_id": str(row.workflow_id), "run_id": run_id})
                except RunRejected as exc:
                    _log.warning(
                        "workflows.event_run_rejected",
                        workflow_id=str(row.workflow_id),
                        error=str(exc),
                    )
        finally:
            await db.close()
    except Exception as exc:
        _log.warning(
            "workflows.event_dispatch_failed",
            source=source,
            event_type=event_type,
            error=str(exc)[:160],
        )
    if started:
        _log.info(
            "workflows.event_dispatched",
            source=source,
            event_type=event_type,
            runs=len(started),
        )
    return started
