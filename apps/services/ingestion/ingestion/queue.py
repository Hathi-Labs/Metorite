"""Redis Streams queue helpers for the ingestion pipeline (WBS 0.3).

Design
------
Each inbound event is written to a Redis Stream keyed by source:

    ingestion:zoho      — Zoho CRM webhooks
    ingestion:gmail     — Gmail Pub/Sub notifications

The orchestrator workers consume from these streams.  For Phase 0 the webhook
handlers write directly to the stream and return immediately (no backpressure);
the normalisation run is triggered out-of-band by the scheduler or by a simple
blocking consumer started via `python -m ingestion.worker`.

Entries
-------
Each entry is a flat dict of string → string (Redis Streams requirement).
Complex payloads are JSON-encoded into a single "data" field.

Stream names
------------
    STREAM_ZOHO    = "ingestion:zoho"
    STREAM_GMAIL   = "ingestion:gmail"

    STREAM_DLQ     = "ingestion:dlq"   (dead-letter — normalisation failures)
"""
from __future__ import annotations

import json
from typing import Any

import redis

from acb_common import get_logger, get_settings

_log = get_logger("ingestion.queue")

STREAM_ZOHO = "ingestion:zoho"
STREAM_GMAIL = "ingestion:gmail"
STREAM_DLQ = "ingestion:dlq"

# Cap streams at 10 000 entries; older entries are trimmed automatically.
_MAXLEN = 10_000


def _client() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def enqueue(stream: str, event_type: str, payload: dict[str, Any]) -> str:
    """Append an event to a Redis Stream.

    Args:
        stream:     Stream name, e.g. ``STREAM_ZOHO``.
        event_type: The source event type string (e.g. ``"taskUpdated"``).
        payload:    Arbitrary dict; JSON-encoded into the ``"data"`` field.

    Returns:
        The Redis entry ID of the newly created entry (e.g. ``"1719123456789-0"``).
    """
    r = _client()
    entry_id: str = r.xadd(
        stream,
        {
            "event_type": event_type,
            "data": json.dumps(payload, default=str),
        },
        maxlen=_MAXLEN,
        approximate=True,
    )
    _log.debug("queue.enqueued", stream=stream, event_type=event_type, entry_id=entry_id)
    return entry_id


def enqueue_dlq(stream_origin: str, event_type: str, payload: dict[str, Any], error: str) -> str:
    """Move a failed entry to the dead-letter queue for manual review."""
    r = _client()
    entry_id: str = r.xadd(
        STREAM_DLQ,
        {
            "origin_stream": stream_origin,
            "event_type": event_type,
            "data": json.dumps(payload, default=str),
            "error": error[:500],  # cap so Redis does not reject large errors
        },
        maxlen=_MAXLEN,
        approximate=True,
    )
    _log.warning(
        "queue.dlq",
        origin=stream_origin,
        event_type=event_type,
        error=error[:120],
        entry_id=entry_id,
    )
    return entry_id


__all__ = [
    "STREAM_ZOHO",
    "STREAM_GMAIL",
    "STREAM_DLQ",
    "enqueue",
    "enqueue_dlq",
]