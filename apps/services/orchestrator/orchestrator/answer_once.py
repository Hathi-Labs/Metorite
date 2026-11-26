"""HRD-16: a card answer acts once.

``chat_failure_modes.md`` §4 G1. A member double taps a card, or the browser
retries an answer. The first answer resolves the run's parked question. The
repeat finds no parked question, and the restart path then used to hand it
back as a NEW message, so the answer acted twice.

This module keeps one small mark for each answered request id. The route
sets it after a run took the answer, and reads it before the restart path.
A marked request id gets a 409 ``answered_once``. The browser then says that
someone answered the card, and it sends nothing again.

The key goes through the tenant-prefix wrapper (R5c), under the session's
organization. A Redis failure never blocks an answer: the mark is a guard,
and a miss costs the old behaviour.

Fence: ``tests/unit/test_card_answer_once.py``.
"""
from __future__ import annotations

import structlog

_log = structlog.get_logger(__name__)

ANSWERED_NAMESPACE = "answered"
#: A retry arrives in seconds. Ten minutes covers a phone that wakes late.
ANSWERED_TTL_SECONDS = 600


async def _client():
    from orchestrator.stream_relay import _tenant_client  # noqa: PLC0415

    return await _tenant_client()


async def mark_answered(organization_id: str | None, thread_id: str, request_id: str) -> bool:
    """Mark *request_id* as answered. True when this call set the mark."""
    if not organization_id or not thread_id or not request_id:
        return True
    try:
        from acb_common.tenant_redis import TenantKey, organization_scope  # noqa: PLC0415

        with organization_scope(organization_id):
            r = await _client()
            k = TenantKey(organization_id, ANSWERED_NAMESPACE, (thread_id, request_id))
            return bool(await r.set(k, "1", nx=True, ex=ANSWERED_TTL_SECONDS))
    except Exception:  # noqa: BLE001
        _log.warning("answer_once.mark_failed", request_id=str(request_id)[:12])
        return True


async def was_answered(organization_id: str | None, thread_id: str, request_id: str) -> bool:
    """True when a run already took an answer for *request_id*."""
    if not organization_id or not thread_id or not request_id:
        return False
    try:
        from acb_common.tenant_redis import TenantKey, organization_scope  # noqa: PLC0415

        with organization_scope(organization_id):
            r = await _client()
            k = TenantKey(organization_id, ANSWERED_NAMESPACE, (thread_id, request_id))
            return await r.get(k) is not None
    except Exception:  # noqa: BLE001
        return False
