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

Task 4036: the mark value is the responder, the session's email in lower
case. A phone can lose the response to an answer that a run took. The
member then taps again, and the 409 says that this member already answered,
so the browser keeps the words out of the composer. A send of them would
act twice. A mark with no responder holds ``"1"``, as before, and names
nobody. The route takes the responder from the session, never from the body.

Fence: ``tests/unit/test_card_answer_once.py``.
"""
from __future__ import annotations

import structlog

_log = structlog.get_logger(__name__)

ANSWERED_NAMESPACE = "answered"
#: A retry arrives in seconds. Ten minutes covers a phone that wakes late.
ANSWERED_TTL_SECONDS = 600
#: The value of a mark that names no responder (the value before task 4036).
_NOBODY = "1"


def _member(responder: str | None) -> str:
    """The responder as the mark keeps it, or "" for nobody."""
    who = (responder or "").strip().lower()
    return who if "@" in who else ""


async def _client():
    from orchestrator.stream_relay import _tenant_client  # noqa: PLC0415

    return await _tenant_client()


async def mark_answered(
    organization_id: str | None, thread_id: str, request_id: str,
    *, responder: str | None = None,
) -> bool:
    """Mark *request_id* as answered by *responder*. True when this call set
    the mark. The first answer keeps the mark, so a later one never renames
    it. *responder* is the session's member, never a value of the request."""
    if not organization_id or not thread_id or not request_id:
        return True
    try:
        from acb_common.tenant_redis import TenantKey, organization_scope  # noqa: PLC0415

        with organization_scope(organization_id):
            r = await _client()
            k = TenantKey(organization_id, ANSWERED_NAMESPACE, (thread_id, request_id))
            value = _member(responder) or _NOBODY
            return bool(await r.set(k, value, nx=True, ex=ANSWERED_TTL_SECONDS))
    except Exception:  # noqa: BLE001
        _log.warning("answer_once.mark_failed", request_id=str(request_id)[:12])
        return True


async def answered_by(
    organization_id: str | None, thread_id: str, request_id: str,
) -> str | None:
    """Who answered *request_id*: None when no run took an answer, "" when
    the mark names nobody, else the responder's email in lower case."""
    if not organization_id or not thread_id or not request_id:
        return None
    try:
        from acb_common.tenant_redis import TenantKey, organization_scope  # noqa: PLC0415

        with organization_scope(organization_id):
            r = await _client()
            k = TenantKey(organization_id, ANSWERED_NAMESPACE, (thread_id, request_id))
            value = await r.get(k)
    except Exception:  # noqa: BLE001
        return None
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return _member(str(value))


async def was_answered(organization_id: str | None, thread_id: str, request_id: str) -> bool:
    """True when a run already took an answer for *request_id*."""
    return await answered_by(organization_id, thread_id, request_id) is not None


def same_responder(marked: str, responder: str | None) -> bool | None:
    """True when *responder* is the member who answered, False when another
    member answered, and None when the mark or the session names nobody."""
    me = _member(responder)
    if not marked or not me:
        return None
    return marked == me
