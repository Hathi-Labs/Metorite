"""The member's kept decisions of a chat, and Undo (HRD-12b, task 3481).

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-12b, over the
store of ``acb_skills.chat_constraints`` (HRD-12a). Dark behind the existing
flag ``CHAT_CONSTRAINTS``: with it off, the read answers ``enabled: false`` and
the Undo answers 404, so the chip draws nothing.

* ``GET  /auth/me/chat-decisions?session_id=…`` the decisions that this chat
  keeps for the member. The chip draws one for each.
* ``POST /auth/me/chat-decisions/undo`` ``{"session_id", "id"}`` takes one
  back. A limit or a correction is not listed here: the card drop owns it.

**Who.** The member and the tenant are the session's (``get_current_user``).
The body names neither (R5, R11). The chat id is a part of a key under the
member's OWN hash (``chat_constraints._member_part``), so a member reaches
their own list, and a chat id of someone else reads as an empty list. No row
of the database is read or written.

**The next turn.** The orchestrator reads the list from Redis again at the
start of each turn (``load_constraints(fresh=True)``), so the decision that
the member took back does not hold in the next reply.

Pattern: ``POST /auth/me/assistant-profile/undo``. Fence:
``tests/unit/test_chat_decisions_route.py``.
"""
from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from acb_skills import chat_constraints as cc
from fastapi import Depends, HTTPException, Query
from gateway.routes.admin.me import me_router
from pydantic import BaseModel, ConfigDict, Field

_log = get_logger("gateway.chat_decisions")

#: A chat id: letters, digits and the marks of the ids that chats carry. It
#: becomes a part of a Redis key, so nothing else passes.
SESSION_ID = re.compile(r"\A[A-Za-z0-9_.:-]{1,128}\Z")
#: An item id (:func:`chat_constraints.item_id`): 12 hex digits.
ITEM_ID = re.compile(r"\A[0-9a-f]{12}\Z")


def _who(user: UserContext) -> tuple[str, str]:
    """``(org, member)`` of the session, or 401. Never from input."""
    email = str(getattr(user, "email", None) or "").strip().lower()
    org = str(getattr(user, "organization_id", None) or "").strip()
    if "@" not in email or not org:
        raise HTTPException(status_code=401, detail="Authentication required")
    return org, email


def _session(value: str) -> str:
    if not SESSION_ID.match(value or ""):
        raise HTTPException(status_code=422, detail="session_id is not a chat id")
    return value


class DecisionOut(BaseModel):
    id: str
    kind: str
    text: str


class DecisionsOut(BaseModel):
    enabled: bool
    items: list[DecisionOut]


class UndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(max_length=128)
    id: str = Field(max_length=12)


@me_router.get("/me/chat-decisions", response_model=DecisionsOut,
               summary="The decisions that this chat keeps for the member (HRD-12b)")
async def get_chat_decisions(
    session_id: str = Query(default="", max_length=128),
    user: UserContext = Depends(get_current_user),
) -> DecisionsOut:
    org, member = _who(user)
    if not cc.chat_constraints_enabled():
        return DecisionsOut(enabled=False, items=[])
    items = await cc.decisions_of(org, _session(session_id), member)
    if items is None:
        raise HTTPException(status_code=503, detail="chat_decisions_unavailable")
    return DecisionsOut(enabled=True, items=[DecisionOut(**i) for i in items])


@me_router.post("/me/chat-decisions/undo",
                summary="Take back one kept decision of this chat (HRD-12b)")
async def undo_chat_decision(
    body: UndoIn,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """404 with ``no_match`` when no kept decision has that id, 404 with
    ``off`` when the flag is off, 503 when Redis cannot say."""
    org, member = _who(user)
    if not cc.chat_constraints_enabled():
        raise HTTPException(status_code=404, detail="off")
    session = _session(body.session_id)
    if not ITEM_ID.match(body.id):
        raise HTTPException(status_code=404, detail=cc.UNDO_NO_MATCH)
    state, gone = await cc.undo_decision(org, session, member, body.id)
    if state == cc.UNDO_NO_MATCH:
        raise HTTPException(status_code=404, detail=cc.UNDO_NO_MATCH)
    if state != cc.UNDONE or gone is None:
        raise HTTPException(status_code=503, detail="chat_decisions_unavailable")
    _log.info("chat_decisions.undone", kind=gone.get("kind", ""))
    return {"undone": {"id": body.id, "kind": gone["kind"], "text": gone["text"]}}
