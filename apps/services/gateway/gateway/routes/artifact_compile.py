"""The rate limit of the React artifact compile (task 2432, F1 of STU-L0).

``POST /artifact-compile/slot`` counts one compile of the session's member and
answers 204. Past :data:`COMPILES_PER_WINDOW` units in :data:`WINDOW_S`
seconds it answers 429, with ``Retry-After`` and one sentence for the member.

``POST /artifact-compile/charge`` adds :data:`TIMEOUT_CHARGE` units to the same
counter when a compile of the member ran into the 10 s kill. A build that the
workbench had to kill holds the one build slot of the workbench for the whole
timeout, so it costs much more than a build that ends in a second. With the
charge, one member gets about :data:`COMPILES_PER_WINDOW` //
:data:`TIMEOUT_CHARGE` killed builds in a window.

The workbench route ``/api/artifacts/compile`` takes a slot here before it
starts esbuild. esbuild runs in the workbench process, which has no Redis
client. The counter lives in the tenant Redis wrapper
(``acb_common.tenant_redis``), so the limit uses the one Redis seam and the
workbench keeps no limiter of its own.

The counter gives FAIRNESS between members. It does not protect the box: the
workbench's own guards do that (one build at a time, one build for each
member, a bounded queue, the kill at 10 s and the caps on the source and the
output). So a store outage answers 503 here, and the workbench lets the
compile go on (task 2432 fix round, an overnight owner instruction).
"""
from __future__ import annotations

from typing import Literal

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

_log = get_logger("gateway.artifact_compile")

router = APIRouter(prefix="/artifact-compile", tags=["artifacts"])

#: The units of one member in one window. A compile costs 1 unit. A live
#: preview sends one compile for each pause in the typing, so the cap leaves
#: room for a long edit.
COMPILES_PER_WINDOW = 200

#: The extra units of a compile that ran into the 10 s kill.
TIMEOUT_CHARGE = 20

#: The window of the cap, in seconds. It starts at the first compile.
WINDOW_S = 600

#: The namespace of the counter in the tenant Redis wrapper.
CAP_NAMESPACE = "artifact_compile"

NO_MEMBER = "Only a signed-in member can build an artifact. Sign in and try again."
UNAVAILABLE = "Metorite could not check the artifact build limit. Try again in a moment."


class Charge(BaseModel):
    """The one kind of charge: a compile that the workbench killed."""

    kind: Literal["timeout"]


def _refuse(
    status: int, code: str, message: str, headers: dict[str, str] | None = None,
) -> HTTPException:
    """The one shape of a refusal: ``{"detail": {"code", "message"}}``."""
    return HTTPException(
        status_code=status, detail={"code": code, "message": message}, headers=headers,
    )


def _member_of(user: UserContext) -> tuple[str, str]:
    """``(member, org)`` of a person's session, else 403.

    An outside agent, a service caller and a session with no org get no slot.
    """
    member = (user.email or "").strip().lower()
    org = str(user.organization_id or "").strip()
    if user.agent is not None or "@" not in member or member.startswith("system:") or not org:
        raise _refuse(403, "no_member", NO_MEMBER)
    return member, org


def cap_message(seconds_left: int) -> str:
    """The sentence of the 429: what happened, and when to try again."""
    minutes = max(1, -(-seconds_left // 60))
    return (
        f"You built many artifacts in the last {WINDOW_S // 60} minutes, which reached the "
        f"limit. Try again in {minutes} minute{'s' if minutes != 1 else ''}."
    )


async def _count(org: str, member: str, units: int) -> tuple[int, int]:
    """Add *units* to the member's counter. ``(count, seconds left)``.

    A counter with no expiry gets one: a process that died between the INCR
    and the EXPIRE would else pin the member for good. ``ttl`` answers -1
    for a key with no expiry, so the check needs no first-call branch.
    """
    from acb_common import tenant_redis

    client = tenant_redis.get_tenant_redis()
    with tenant_redis.organization_scope(org):
        counter = tenant_redis.key(CAP_NAMESPACE, "member", member)
        count = int(await client.incr(counter, units))
        left = int(await client.ttl(counter))
        if left < 0:
            await client.expire(counter, WINDOW_S)
            left = WINDOW_S
    return count, left


@router.post("/slot", status_code=204, summary="Count one artifact compile of the member")
async def take_slot(user: UserContext = Depends(get_current_user)) -> Response:
    """204 when the member may compile, 429 past the cap, 503 when Redis is down."""
    member, org = _member_of(user)
    try:
        count, left = await _count(org, member, 1)
    except Exception as exc:
        _log.warning("artifact_compile.cap_unavailable", error=type(exc).__name__)
        raise _refuse(503, "unavailable", UNAVAILABLE, {"Retry-After": "2"}) from None
    if count > COMPILES_PER_WINDOW:
        _log.info("artifact_compile.capped", org=org)
        raise _refuse(429, "cap", cap_message(left), {"Retry-After": str(max(left, 1))})
    return Response(status_code=204)


@router.post("/charge", status_code=204, summary="Charge a compile that ran into the kill")
async def charge(body: Charge, user: UserContext = Depends(get_current_user)) -> Response:
    """Add :data:`TIMEOUT_CHARGE` units for a killed compile. 503 when Redis is down."""
    member, org = _member_of(user)
    try:
        await _count(org, member, TIMEOUT_CHARGE)
    except Exception as exc:
        _log.warning("artifact_compile.charge_unavailable", error=type(exc).__name__)
        raise _refuse(503, "unavailable", UNAVAILABLE, {"Retry-After": "2"}) from None
    _log.info("artifact_compile.charged", org=org, kind=body.kind)
    return Response(status_code=204)
