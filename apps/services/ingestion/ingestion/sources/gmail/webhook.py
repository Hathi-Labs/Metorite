"""Gmail push-notification receiver (WBS 1.3).

Google Workspace pushes Gmail change events into a Cloud Pub/Sub topic which
in turn POSTs the message to this endpoint. Each notification body is a
base64-encoded JSON `{ emailAddress, historyId }` blob.

We verify the bearer token (an audience we configure on the Pub/Sub subscription),
ack-200 immediately, best-effort `enqueue` the decoded notification onto
`ingestion:gmail`, and fan it out to the event-sink registry (workflow event
triggers) — the shape BO-20f settled on, per §BO-20. The `list_history(historyId)`
follow-up chain is still TODO.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from acb_audit import AuditEvent, record
from acb_common import get_logger, get_settings
from ingestion.queue import STREAM_GMAIL, enqueue

router = APIRouter(prefix="/webhooks/gmail", tags=["ingestion:gmail"])
_log = get_logger("ingestion.gmail")

#: The event type Gmail notifications carry on the bus. A Pub/Sub push carries
#: only ``emailAddress`` + ``historyId`` — there is no provider event name to
#: pass through — so this single string IS the Gmail vocabulary. It is
#: user-visible: workflow event triggers match on it (``triggers.py``), so it is
#: prescribed by FOUNDATION_BUILDOUT_CHECKLIST.md §BO-20 (BO-20f), not chosen here.
EVENT_HISTORY_UPDATED = "historyUpdated"

#: Source string used for the bus + the sink registry.
SOURCE = "gmail"


def _verify_bearer(authorization: str | None) -> bool:
    """Verify the shared bearer token Pub/Sub sends on every push."""
    expected = get_settings().gmail_pubsub_token
    if not expected or not authorization or not authorization.lower().startswith("bearer "):
        return False
    presented = authorization.split(" ", 1)[1].strip()
    # Compare bytes, not str: `hmac.compare_digest` raises TypeError on a str
    # containing non-ASCII, which would turn a garbage credential into a 500
    # instead of a 401 on a PUBLIC_ROUTES endpoint.
    return hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))


def _decode_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract the Gmail notification payload from a Pub/Sub envelope.

    Always returns a dict. A push whose data decodes to valid JSON that is not
    an object (``[1,2]``, ``null``, ``"hi"``) is treated exactly like an
    undecodable one — the caller reads ``emailAddress`` / ``historyId`` off the
    result, and returning a list or ``None`` here would raise ``AttributeError``
    and 5xx a push that can never be decoded, making Pub/Sub retry it forever.
    """
    msg = envelope.get("message") or {}
    data = msg.get("data")
    if not data:
        return {}
    try:
        decoded = base64.b64decode(data).decode("utf-8")
        parsed = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.post("")
async def receive(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    if not _verify_bearer(authorization):
        raise HTTPException(status_code=401, detail="invalid bearer")
    envelope = await request.json()
    notification = _decode_envelope(envelope)
    email_address = notification.get("emailAddress")
    history_id = notification.get("historyId")
    _log.info("gmail.webhook", email=email_address, history_id=history_id)
    record(
        AuditEvent(
            actor="webhook:gmail",
            action="received",
            target=f"gmail:{email_address or 'unknown'}",
            payload={"history_id": history_id},
        )
    )
    if not isinstance(notification, dict) or not notification:
        # `_decode_envelope` returns {} for a dataless, malformed, or non-object
        # push (the isinstance arm is belt-and-braces for a stubbed/overridden
        # decoder — the helper itself already guarantees a dict). There
        # is nothing to enqueue (replaying an empty dict replays nothing) and
        # nothing a workflow could act on — firing a trigger with neither
        # mailbox nor historyId would start runs that can only fail. Ack anyway:
        # a non-2xx would make Pub/Sub retry the same undecodable push.
        _log.warning("gmail.webhook.undecodable", envelope_keys=sorted(envelope)[:5])
        return {"status": "accepted"}

    # §BO-20 Q1 cutover: with INGESTION_CONSUMER on, the consumer drains the
    # stream and is the dispatch path, so this receiver must NOT also emit
    # inline — one event, one fan-out, never both.
    from ingestion.consumer import consumer_enabled

    consumer_on = consumer_enabled()

    # Always enqueue to Redis Streams for audit trail and replay capability.
    try:
        enqueue(STREAM_GMAIL, EVENT_HISTORY_UPDATED, notification)
    except Exception as exc:  # noqa: BLE001
        # Redis unavailable — log and continue; do NOT return 5xx to Pub/Sub
        # (that triggers retries which would make the backlog worse).
        _log.warning("gmail.queue.unavailable", error=str(exc))
        if consumer_on:
            # After the cutover the queue is the ONLY path, so a failed enqueue
            # is a dropped event — the accepted §BO-20 Q1 regression. Loud on
            # its own key; do NOT re-emit inline (that is double dispatch).
            _log.warning(
                "gmail.queue.dropped",
                gmail_event=EVENT_HISTORY_UPDATED,
                error=str(exc)[:160],
            )

    # Fan out to registered event sinks (workflow event triggers) — a no-op
    # unless the gateway wired a sink in at startup (event_hooks.py).
    if not consumer_on:
        from ingestion.event_hooks import emit_event

        background_tasks.add_task(
            emit_event, SOURCE, EVENT_HISTORY_UPDATED, notification
        )

    # TODO WBS 1.3 follow-up: list_history + normalise + triage chain.
    return {"status": "accepted"}


__all__ = [
    "EVENT_HISTORY_UPDATED",
    "SOURCE",
    "_decode_envelope",
    "_verify_bearer",
    "router",
]
