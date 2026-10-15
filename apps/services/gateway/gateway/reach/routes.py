"""The two routes of Reach (WS-59 RE-1a, ``proactive_reach.md`` §11).

* ``GET /reach/preview`` — what Reach would send the signed-in member now: the
  brief, the interrupt, and the state of the channel. ``held`` is True when
  the interrupt waits for the member's next message to the bot (RE-2a). It
  sends nothing and writes nothing.
* ``POST /reach/brief`` — send the signed-in member's brief now. It skips the
  once-a-day guard, and keeps every other check: the switch, the org list,
  the member allowlist, the link and the 24-hour window.
* ``POST /reach/tell-me`` — "Tell me on WhatsApp" for the signed-in member
  (RE-5, §11.4 item 2). It records one request with ``source=chat`` and
  answers what Reach will do with it. The assistant's tool calls it.
* ``GET /reach/status`` — ``{available, linked}`` for the signed-in member.
  The Rules UI reads it to offer the action or not.

The first two answer 404 when Reach is off for the member, so a closed
feature looks like no feature. ``tell-me`` answers ``off`` instead and
records nothing, because the assistant must tell the member why.
``status`` answers ``available: false``. The member and the org come from the session only (R5,
R11). Gated on ``feature:chat``, as the WhatsApp channel's own router is,
because Reach speaks through that channel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gateway.reach import flags, loop, outbox, plan, requests
from gateway.reach import whatsapp as channel

router = APIRouter(
    prefix="/reach",
    tags=["reach"],
    dependencies=[require_feature_router("chat")],
)

NOT_FOUND = "Not found"


def _scope(user: UserContext) -> tuple[str, str]:
    """The session's org and member, or 404 when Reach is off for them."""
    org = str(getattr(user, "organization_id", None) or "")
    email = (user.email or "").strip().lower()
    if not org or not email or not flags.member_allowed(org, email):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return org, email


@router.get("/preview")
async def reach_preview(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The brief and the interrupt Reach would send now. Sends nothing."""
    org, email = _scope(user)
    snap = await loop.snapshot(org, user)
    if not snap.complete:
        raise HTTPException(status_code=503, detail="My Day could not be read in full")
    logged = await outbox.logged_pairs(org, email)
    candidates = plan.interrupt_candidates(snap.cards, logged, snap.now)
    wa_id, is_open = await channel.reach_state(org, email)
    held = (wa_id is not None and not is_open and bool(candidates)
            and flags.needs_you_template() is None)
    return {
        "brief": plan.compose_brief(snap.cards, loop.app_url(), snap.local_date,
                                    now=snap.now),
        "interrupts": (plan.compose_interrupt(candidates, loop.app_url(), now=snap.now)
                       if candidates else None),
        "linked": wa_id is not None,
        "window_open": is_open,
        "held": held,
    }


@router.get("/status")
async def reach_status(user: UserContext = Depends(get_current_user)) -> dict[str, bool]:
    """``{available, linked}`` of "Tell me on WhatsApp" for the signed-in
    member. Always 200: Reach off reads ``available: false``, so the Rules UI
    can hide the action (review of RE-5, P1-4)."""
    org = str(getattr(user, "organization_id", None) or "")
    available, linked = await requests.reach_status(org, user.email)
    return {"available": available, "linked": linked}


@router.post("/brief")
async def reach_brief_now(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """Send the signed-in member's brief now."""
    org, email = _scope(user)
    snap = await loop.snapshot(org, user)
    if not snap.complete:
        return {"state": "skipped", "reason": "read_incomplete"}
    # One brief for each minute at most: a double click sends once.
    minute = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")
    key = plan.dedupe_key("brief-now", email, [minute])
    return result_body(await loop.send_brief(org, email, snap, key=key,
                                             template=flags.brief_template()))


def result_body(outcome: str) -> dict[str, Any]:
    """The answer of ``POST /reach/brief``: ``{state, reason}``. The client
    (``people/lib/reach.ts`` ``sendResult``) reads exactly these two keys."""
    if outcome == "empty":
        return {"state": "skipped", "reason": "nothing_to_say"}
    if outcome == "duplicate":
        return {"state": "skipped", "reason": "already_sent"}
    state, _, reason = outcome.partition(":")
    return {"state": state, "reason": reason or None}


class TellMeBody(BaseModel):
    """What the member wants on WhatsApp. No recipient: it is the member."""

    title: str
    body: str | None = None


#: What the assistant says for each state of ``POST /reach/tell-me``.
TELL_ME_ANSWERS = {
    "sent": "Sent to your WhatsApp in about a minute.",
    "waits": ("It waits for your next WhatsApp message to Metorite "
              "(WhatsApp's 24-hour rule)."),
    "no_link": ("Your WhatsApp is not linked yet. Link it in My Profile, "
                "then ask again."),
    "off": "Reach is not on for you yet.",
    "limit": ("You asked for 20 WhatsApp messages in the last 24 hours. That is "
              "the limit, so this one did not go. Ask again later."),
}


def tell_me_body(state: str, request_id: str | None = None) -> dict[str, Any]:
    return {"state": state, "request_id": request_id, "answer": TELL_ME_ANSWERS[state]}


@router.post("/tell-me")
async def reach_tell_me(req: TellMeBody,
                        user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """Record a request for the signed-in member, and say what happens next.

    The member and the org come from the session only. The body names no
    recipient, so the request can reach nobody else (R5, R11)."""
    org = str(getattr(user, "organization_id", None) or "")
    email = (user.email or "").strip().lower()
    if not org or not email or not flags.member_allowed(org, email) \
            or not channel.channel_ready(org):
        return tell_me_body("off")
    wa_id, is_open = await channel.reach_state(org, email)
    if wa_id is None:
        return tell_me_body("no_link")
    try:
        rid = await requests.tell_me(org, email, source="chat", title=req.title,
                                     body=req.body, requested_by=email)
    except requests.RequestRefused as exc:
        if exc.reason == "daily_limit":
            return tell_me_body("limit")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except requests.RequestInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return tell_me_body("sent" if is_open else "waits", rid)
