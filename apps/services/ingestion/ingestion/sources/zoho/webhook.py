"""Zoho CRM webhook receiver (WBS 1.1).

Zoho doesn't sign webhook payloads natively, so we follow the common pattern
of provisioning a shared secret in the webhook URL (``?token=<secret>``) or as
a custom header (``X-Zoho-Token``) and verifying it server-side.

Mount under the gateway (preferred for v1) or run standalone via
``uv run uvicorn ingestion.sources.zoho.webhook:standalone --reload``.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, FastAPI, Header, HTTPException, Query, Request

from acb_audit import AuditEvent, record
from acb_common import get_logger, get_settings
from ingestion.queue import STREAM_ZOHO, enqueue

router = APIRouter(prefix="/webhooks/zoho", tags=["ingestion:zoho"])
_log = get_logger("ingestion.zoho")

#: Source string used for the bus + the sink registry. The event type is the
#: provider's own value (see `receive`), so this is the whole Zoho vocabulary
#: prescribed by FOUNDATION_BUILDOUT_CHECKLIST.md §BO-20 (BO-20f).
SOURCE = "zoho"


def _verify(token: str | None) -> bool:
    expected = get_settings().zoho_webhook_secret
    if not expected:
        # Mis-config: refuse all calls rather than silently accept.
        return False
    if not token:
        return False
    # Compare bytes, not str: `hmac.compare_digest` raises TypeError on a str
    # containing non-ASCII (e.g. `?token=%C3%A9`), which would turn a garbage
    # credential into a 500 instead of a 401 on a PUBLIC_ROUTES endpoint.
    return hmac.compare_digest(expected.encode("utf-8"), token.encode("utf-8"))


@router.post("")
async def receive(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str | None = Query(default=None),
    x_zoho_token: str | None = Header(default=None),
) -> dict[str, str]:
    """Receive a Zoho CRM notification. Verifies a shared secret, audit-logs it,
    enqueues it onto ``ingestion:zoho`` and fans it out to the event-sink
    registry (workflow event triggers) — the shape BO-20f settled on, per §BO-20."""
    if not _verify(token or x_zoho_token):
        raise HTTPException(status_code=401, detail="invalid token")
    payload: dict[str, Any] = await request.json()
    module = (
        payload.get("module")
        or payload.get("Module")
        or (payload.get("data") or [{}])[0].get("module")
    )
    # `str(...)` is load-bearing: `event` is a wire token (it becomes the Redis
    # Streams field the consumer keys on, and the `event_type` workflow triggers
    # match). Zoho is not obliged to send a string — a push carrying
    # `{"event": {...}}` would make the real `xadd` raise
    # `DataError: Invalid input of type: 'dict'`, which the handler below
    # swallows as `zoho.queue.unavailable` (blaming Redis for a payload-shape
    # problem) while the sink fan-out still starts a run.
    event = str(payload.get("event") or payload.get("operation") or "unknown")
    # NB: the key must NOT be `event=` — that is structlog's own message
    # parameter, and passing it raised TypeError on every authenticated push
    # (the receiver 500'd before this ticket added any code). The retired
    # ClickUp receiver sidestepped it with a `<source>_event=` kwarg; mirror that.
    _log.info("zoho.webhook", zoho_event=event, module=module)
    record(
        AuditEvent(
            actor="webhook:zoho",
            action="received",
            target=f"zoho:{module or 'unknown'}",
            payload={"event": event, "size": len(str(payload))},
        )
    )
    # §BO-20 Q1 cutover: with INGESTION_CONSUMER on, the consumer drains the
    # stream and is the dispatch path, so this receiver must NOT also emit
    # inline — one event, one fan-out, never both.
    from ingestion.consumer import consumer_enabled

    consumer_on = consumer_enabled()

    # Always enqueue to Redis Streams for audit trail and replay capability.
    try:
        enqueue(STREAM_ZOHO, event, payload)
    except Exception as exc:  # noqa: BLE001
        # Redis unavailable — log and continue; do NOT return 5xx to Zoho
        # (that triggers retries which would make the backlog worse).
        _log.warning("zoho.queue.unavailable", error=str(exc))
        if consumer_on:
            # After the cutover the queue is the ONLY path, so a failed enqueue
            # is a dropped event — the accepted §BO-20 Q1 regression. Loud on
            # its own key; do NOT re-emit inline (that is double dispatch).
            _log.warning(
                "zoho.queue.dropped",
                zoho_event=event,
                error=str(exc)[:160],
            )

    # Fan out to registered event sinks (workflow event triggers) — a no-op
    # unless the gateway wired a sink in at startup (event_hooks.py).
    if not consumer_on:
        from ingestion.event_hooks import emit_event

        background_tasks.add_task(emit_event, SOURCE, event, payload)

    return {"status": "accepted"}


# Lightweight standalone app for local testing.
standalone = FastAPI(title="acb-ingest-zoho-webhook", version="0.1.0")
standalone.include_router(router)
