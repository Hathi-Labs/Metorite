"""The two routes of Reach (WS-59 RE-1a, ``proactive_reach.md`` §11).

* ``GET /reach/preview`` — what Reach would send the signed-in member now: the
  brief, the interrupt, and the state of the channel. It sends nothing and
  writes nothing.
* ``POST /reach/brief`` — send the signed-in member's brief now. It skips the
  once-a-day guard, and keeps every other check: the switch, the org list,
  the member allowlist, the link and the 24-hour window.

Both answer 404 when Reach is off for the member, so a closed feature looks
like no feature. The member and the org come from the session only (R5,
R11). Gated on ``feature:chat``, as the WhatsApp channel's own router is,
because Reach speaks through that channel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from fastapi import APIRouter, Depends, HTTPException

from gateway.reach import flags, loop, outbox, plan
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
    wa_id = await channel.linked_phone(org, email)
    return {
        "brief": plan.compose_brief(snap.cards, loop.app_url(), snap.local_date,
                                    now=snap.now),
        "interrupts": (plan.compose_interrupt(candidates, loop.app_url(), now=snap.now)
                       if candidates else None),
        "linked": wa_id is not None,
        "window_open": bool(wa_id) and await channel.window_open(org, str(wa_id)),
    }


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
    return result_body(await loop.send_brief(org, email, snap, key=key))


def result_body(outcome: str) -> dict[str, Any]:
    """The answer of ``POST /reach/brief``: ``{state, reason}``. The client
    (``people/lib/reach.ts`` ``sendResult``) reads exactly these two keys."""
    if outcome == "empty":
        return {"state": "skipped", "reason": "nothing_to_say"}
    if outcome == "duplicate":
        return {"state": "skipped", "reason": "already_sent"}
    state, _, reason = outcome.partition(":")
    return {"state": state, "reason": reason or None}
