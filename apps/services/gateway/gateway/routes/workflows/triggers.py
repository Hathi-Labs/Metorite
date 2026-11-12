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

# H4 and H-328 part (c): the org of an event is the tenant that the EMITTER
# bound. `dispatch_event` reads `current_tenant()` ONCE at entry. A member
# request binds it (`projects/core.py` emit, `studio/preview.py`
# `emit_record_event`), so the org is a trusted fact of the request. The other
# emitters (`routes/agent.py` `receive_webhook`, the Zoho and Gmail receivers
# and `ingestion/consumer.py`) bind no tenant, so they start nothing, as
# before. With no tenant, or with an org that is not on
# `WORKFLOW_UNATTENDED_ORGS`, the door logs one line and starts nothing.
# ⚠️ Never take the org from the payload: it is request input (R5, R11). Never
# loop over each org: an event of org A would then start B's workflow.
from acb_common.db import current_tenant
from gateway.routes.workflows import unattended
from gateway.routes.workflows.core import _log, _tenant_session, parse_jsonb
from gateway.routes.workflows.service import (
    RunRejected,
    _member_blind_write_nodes,
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
    """Start a run for every published workflow of the bound org that is bound
    to this event.

    Best-effort and never raises — event delivery must not break the webhook
    receivers that call it. Returns ``[{workflow_id, run_id}, …]``.
    """
    started: list[dict[str, str]] = []
    org = current_tenant()
    if not org or not unattended.org_allowed(org):
        _log.info(
            "workflows.event_skipped_no_listed_org",
            source=source,
            event_type=event_type,
            bound=bool(org),
        )
        return started
    try:
        matches: list[tuple[Any, dict[str, Any]]] = []
        async with _tenant_session(org) as db:
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
                if _member_blind_write_nodes(serialized):
                    # A task node writes `pm_tasks` with no member check. An
                    # event names no member to check, so the door refuses.
                    _log.warning(
                        "workflows.event_run_refused_task_node",
                        workflow_id=str(row.workflow_id),
                    )
                    continue
                matches.append((row, serialized))
        # The session is closed before `start_run`: the run opens its own.
        for row, serialized in matches:
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
                    organization_id=org,
                )
                started.append({"workflow_id": str(row.workflow_id), "run_id": run_id})
            except RunRejected as exc:
                _log.warning(
                    "workflows.event_run_rejected",
                    workflow_id=str(row.workflow_id),
                    error=str(exc),
                )
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
