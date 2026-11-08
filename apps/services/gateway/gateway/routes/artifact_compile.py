"""The rate limit of the React artifact compile (task 2432, F1 of STU-L0).

``POST /artifact-compile/slot`` counts one compile of the session's member and
answers 204. Past :data:`COMPILES_PER_WINDOW` compiles in :data:`WINDOW_S`
seconds it answers 429, with ``Retry-After`` and one sentence for the member.

The workbench route ``/api/artifacts/compile`` takes a slot here before it
starts esbuild. esbuild runs in the workbench process, which has no Redis
client. The counter lives in the tenant Redis wrapper
(``acb_common.tenant_redis``), so the limit uses the one Redis seam and the
workbench keeps no limiter of its own.

A store that does not answer refuses the slot (503 with ``Retry-After: 2``),
because the limit guards the box that serves every tenant.
"""
from __future__ import annotations

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Response

_log = get_logger("gateway.artifact_compile")

router = APIRouter(prefix="/artifact-compile", tags=["artifacts"])

#: The compiles of one member in one window. A live preview sends one compile
#: for each pause in the typing, so the cap leaves room for a long edit.
COMPILES_PER_WINDOW = 200

#: The window of the cap, in seconds. It starts at the first compile.
WINDOW_S = 600

#: The namespace of the counter in the tenant Redis wrapper.
CAP_NAMESPACE = "artifact_compile"

NO_MEMBER = "Only a signed-in member can build an artifact. Sign in and try again."
UNAVAILABLE = "Metorite could not check the artifact build limit. Try again in a moment."


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
        f"You built {COMPILES_PER_WINDOW} artifacts in the last {WINDOW_S // 60} minutes, "
        f"which is the limit. Try again in {minutes} minute{'s' if minutes != 1 else ''}."
    )


@router.post("/slot", status_code=204, summary="Count one artifact compile of the member")
async def take_slot(user: UserContext = Depends(get_current_user)) -> Response:
    """204 when the member may compile, 429 past the cap, 503 when Redis is down."""
    member, org = _member_of(user)
    from acb_common import tenant_redis

    over: int | None = None
    try:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(org):
            counter = tenant_redis.key(CAP_NAMESPACE, "member", member)
            count = int(await client.incr(counter))
            if count == 1:
                await client.expire(counter, WINDOW_S)
            if count > COMPILES_PER_WINDOW:
                left = int(await client.ttl(counter))
                if left < 0:
                    # A counter that lost its expiry gets one again, so it
                    # never pins a member for good.
                    await client.expire(counter, WINDOW_S)
                    left = WINDOW_S
                over = max(left, 1)
    except Exception as exc:
        _log.warning("artifact_compile.cap_unavailable", error=type(exc).__name__)
        raise _refuse(503, "unavailable", UNAVAILABLE, {"Retry-After": "2"}) from None
    if over is not None:
        _log.info("artifact_compile.capped", org=org)
        raise _refuse(429, "cap", cap_message(over), {"Retry-After": str(over)})
    return Response(status_code=204)
