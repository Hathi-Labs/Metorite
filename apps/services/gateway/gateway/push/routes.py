"""The five routes of web push (WS-66 PN-1).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §10.2 item 3.

* ``GET /push/public-key`` — the ``applicationServerKey`` that the browser
  subscribes with. 503 while the keys are not configured.
* ``POST /push/subscriptions`` — save this device for the signed-in member,
  or refresh its keys. At the cap the store evicts first (``store.save``).
* ``POST /push/subscriptions/status`` — ``{registered}``: does the
  signed-in member hold a row of this endpoint. One browser holds one
  subscription for every account of the switcher, so the browser alone
  cannot say whether THIS member gets the pushes. A POST, so the endpoint
  stays in the body and out of every URL and access log.
* ``DELETE /push/subscriptions`` — delete the signed-in member's row of one
  endpoint. A sign-out calls it. It never touches another member's row.
* ``POST /push/test`` — send the test push to the signed-in member's own
  devices. One test at a time for each member, and a second gets a 429.

With ``WEB_PUSH_ENABLED`` off, each route answers 404, so a closed feature
looks like no feature. The one exception is a body that is not JSON at all
(``_push_on``). The member and the org come from the session only (R5,
R11). A body that names anything else gets a 422. Gated on
``feature:chat``, as ``/reach`` is.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from gateway.push import endpoints, flags, sender, store

NOT_FOUND = "Not found"


async def _push_on() -> None:
    """404 while ``WEB_PUSH_ENABLED`` is off, before the feature gate.

    ⚠️ FastAPI parses the JSON text of a body BEFORE it runs a dependency. So
    a body that is not JSON at all gets 422 even with the flag off. A JSON
    body of the wrong shape gets this 404, because FastAPI checks the fields
    after the dependencies (fence: ``test_push_platform.py``)."""
    if not flags.enabled():
        raise HTTPException(status_code=404, detail=NOT_FOUND)


router = APIRouter(
    prefix="/push",
    tags=["push"],
    dependencies=[Depends(_push_on), require_feature_router("chat")],
)
NOT_CONFIGURED = "Web push is not set up on this server"

#: The longest user agent we keep, as the column CHECK holds it.
MAX_USER_AGENT = 300

#: The members whose test push runs now, as ``(org, email)``. A second test
#: of the same member answers 429 at once. The check and the add have no
#: ``await`` between them, so one event loop cannot interleave them.
_IN_FLIGHT: set[tuple[str, str]] = set()


def _scope(user: UserContext) -> tuple[str, str]:
    """The session's org and member, or 404 when push is off."""
    if not flags.enabled():
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    org = str(getattr(user, "organization_id", None) or "")
    email = (user.email or "").strip().lower()
    if not org or not email:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return org, email


def _b64url_bytes(value: str) -> bytes | None:
    """The bytes of a base64url string with no padding, or None."""
    if not value or any(c not in _B64URL for c in value):
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return None


_B64URL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


class SubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class SubscriptionBody(BaseModel):
    """``PushSubscription.toJSON()``, and the browser's user agent as a label.

    No member and no org: they come from the session."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    endpoint: str = Field(min_length=1, max_length=endpoints.MAX_ENDPOINT_CHARS)
    expiration_time: float | None = Field(default=None, alias="expirationTime")
    keys: SubscriptionKeys
    user_agent: str | None = Field(default=None, max_length=2000)


class EndpointBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=endpoints.MAX_ENDPOINT_CHARS)


def _subscription(body: SubscriptionBody) -> store.Subscription:
    """The checked device, or a 422 that names the field."""
    why = endpoints.endpoint_refusal(body.endpoint)
    if why is not None:
        raise HTTPException(status_code=422, detail=f"endpoint: {why}")
    p256dh = body.keys.p256dh.rstrip("=")
    auth = body.keys.auth.rstrip("=")
    # An uncompressed P-256 point is 65 bytes and starts with 0x04. The auth
    # secret is 16 bytes.
    point = _b64url_bytes(p256dh)
    if point is None or len(point) != 65 or point[0] != 4:
        raise HTTPException(status_code=422, detail="keys.p256dh: not a P-256 public key")
    secret = _b64url_bytes(auth)
    if secret is None or len(secret) != 16:
        raise HTTPException(status_code=422, detail="keys.auth: not a 16-byte secret")
    expires: datetime | None = None
    if body.expiration_time is not None:
        try:
            expires = datetime.fromtimestamp(body.expiration_time / 1000.0, tz=UTC)
        except (OverflowError, OSError, ValueError):
            raise HTTPException(status_code=422, detail="expirationTime: out of range") from None
    agent = (body.user_agent or "").strip()[:MAX_USER_AGENT] or None
    return store.Subscription(endpoint=body.endpoint, p256dh=p256dh, auth=auth,
                              expiration_time=expires, user_agent=agent)


@router.get("/public-key")
async def push_public_key(user: UserContext = Depends(get_current_user)) -> dict[str, str]:
    """The VAPID public key that the browser subscribes with."""
    _scope(user)
    key = flags.public_key()
    if key is None or flags.subject() is None:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    return {"public_key": key}


@router.post("/subscriptions")
async def push_subscribe(body: SubscriptionBody,
                         user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """Save this device for the signed-in member. Never refused at the cap:
    the store evicts the member's stale and oldest devices first."""
    org, email = _scope(user)
    sub = _subscription(body)
    return {"id": await store.save(org, email, sub), "state": "saved"}


@router.post("/subscriptions/status")
async def push_status(body: EndpointBody,
                      user: UserContext = Depends(get_current_user)) -> dict[str, bool]:
    """``{registered}``: does the signed-in member hold a row of this
    endpoint. Reads only."""
    org, email = _scope(user)
    return {"registered": await store.registered(org, email, body.endpoint)}


@router.delete("/subscriptions")
async def push_unsubscribe(body: EndpointBody,
                           user: UserContext = Depends(get_current_user)) -> dict[str, bool]:
    """Delete the signed-in member's row of one endpoint. Idempotent."""
    org, email = _scope(user)
    return {"removed": await store.remove(org, email, body.endpoint)}


@router.post("/test")
async def push_test(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """Send the test push to the signed-in member's own devices."""
    org, email = _scope(user)
    if not flags.configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    key = (org, email)
    if key in _IN_FLIGHT:
        raise HTTPException(status_code=429, detail="A test notification is on its way")
    _IN_FLIGHT.add(key)
    try:
        report = await sender.send_to_member(org, email, sender.TEST_MESSAGE)
    finally:
        _IN_FLIGHT.discard(key)
    return {"devices": report.devices, "sent": report.sent,
            "removed": report.removed, "failed": report.failed}
