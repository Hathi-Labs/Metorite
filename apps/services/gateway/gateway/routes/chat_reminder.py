"""``POST /chat/sessions/{session_id}/reminder-open`` — a tap on a reminder
link opens the chat (WS-66 RM-4, ``calendar_events_reminders.md`` §5.7, D114).

A push, a feed card or a Calendar pin opens ``/chat?reminder=<id>``. The page
starts a fresh chat in the one assistant and calls this route ONCE, with
``{reminder_id}``. The route:

1. opens the reminder through the ONE open,
   ``routes/projects/reminders.open_reminder``. It loads the member's own
   row (404 for any other), writes the ``opened`` ledger row, and closes a
   ``notice``;
2. posts the first assistant row through the brief's mint path
   (``chat_brief._post_row``): the text that code built, and one
   ``generative_ui`` event, the ``reminderCard`` with the chips. No model
   runs;
3. answers ``{posted, message_id, preview, reminder, context}``. The dock
   publishes ``context`` as the page facts while this chat is open, so a
   typed answer reaches the model with the reminder block (§5.7 item 4).

**The server builds every word** (D-PM-39). The body names the reminder id
only. The text, the card and the facts come from the member's own row, read
here. The client never sends them.

**Only the server creates an agent row** (D-PM-39, ``projects_ai_chat.md``
§20). The row goes in through ``_post_row``, the one minter's caller of the
brief, and this module names the mint keyword nowhere (``test_rooms.py``).
The row id is ``reminder-<reminder_id>``. The key of ``chat_message`` is
``(session_id, id)``, so a second call in the same chat posts no second row,
and it writes no second ``opened`` row either.

**Scope** (R5e). The flag ``CALENDAR_REMINDERS`` off answers 404 before any
read. The tenant is the bound organization, and the member is the session's.
The member must hold ``feature:projects``, the gate of the reminder routes,
or the answer is 404. The session gets every check of ``post_brief``: a
session the member cannot read is 404, one they cannot send in gets the
room's refusal, and a room shared with a second person answers 204 with no
row and no ledger write, because a reminder is private.

Fences: ``tests/unit/test_reminder_context.py`` (pure, and the source rules)
and ``tests/unit/test_reminders_r8.py`` (the real database).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from acb_auth import UserContext, get_current_user, require_feature_router
from fastapi import APIRouter, Depends, HTTPException, Response, status
from gateway.rooms import resolve_room_access
from gateway.routes.chat_brief import DEFAULT_AGENT, _post_row
from gateway.routes.projects import reminders
from pydantic import BaseModel

router = APIRouter(
    prefix="/chat", tags=["chat"],
    dependencies=[require_feature_router("chat")],
)

#: The template the first row draws its chips with (``genUITemplates.tsx``).
REMINDER_TEMPLATE = "reminderCard"
#: The feature that gates the reminder routes (``/projects``).
REMINDER_FEATURE = "feature:projects"


class ReminderOpenIn(BaseModel):
    """The one field the client sends. Every word comes from the row."""

    reminder_id: UUID


def reminder_message_id(reminder_id: Any) -> str:
    """The row id of a reminder's first message in a chat: one for each
    reminder, so a second post into the same chat is a conflict that the
    mint ignores."""
    return f"reminder-{reminder_id}"


def reminder_event(card: dict[str, Any]) -> dict[str, Any]:
    """The custom event of the row, as ``chat_fold`` stores one. A cutoff of
    0 draws the text first and the card under it."""
    return {
        "name": "generative_ui",
        "value": {"type": "template", "props": {"name": REMINDER_TEMPLATE, "data": card}},
        "segmentCutoff": 0,
    }


async def _require_reminders() -> None:
    """404 while ``CALENDAR_REMINDERS`` is off, before FastAPI checks the
    body, as each reminder route answers (``reminders._require_on``)."""
    if not reminders.reminders_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _has_row(session_id: str, message_id: str, organization_id: str) -> bool:
    """Whether the chat already holds the reminder's row, in the bound
    tenant."""
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        return s.execute(
            text("SELECT 1 FROM chat_message WHERE session_id = :sid AND id = :id"),
            {"sid": session_id, "id": message_id},
        ).first() is not None


@router.post(
    "/sessions/{session_id}/reminder-open",
    dependencies=[Depends(_require_reminders)],
    summary="Open a reminder in the chat: the first message and the context block (RM-4)",
)
async def open_reminder_in_chat(
    session_id: str,
    payload: ReminderOpenIn,
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> Any:
    """See the module docstring. 200 with the row's id and the context, 204
    for a shared room, 404 for anything the member may not reach."""
    org = str(getattr(user, "organization_id", None) or "")
    email = (user.email or "").strip()
    if not org or not email:
        raise HTTPException(status_code=404, detail="Session not found")
    if not user.has_permission(REMINDER_FEATURE):
        # The same answer as a reminder that is not the member's.
        raise HTTPException(status_code=404, detail="Reminder not found")

    room = await asyncio.to_thread(
        resolve_room_access, session_id, email, organization_id=org,
    )
    if not room.can_read:
        raise HTTPException(status_code=404, detail="Session not found")
    if not room.can_send:
        raise HTTPException(status_code=room.refusal_status, detail=room.denied("open a reminder"))
    if room.is_shared:
        # A reminder is one member's. It never lands in a room.
        response.status_code = status.HTTP_204_NO_CONTENT
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    message_id = reminder_message_id(payload.reminder_id)
    there = await asyncio.to_thread(_has_row, session_id, message_id, org)
    # 404 for a reminder that is not the member's, before any write.
    opened = await reminders.open_reminder(user, payload.reminder_id, record=not there)
    posted = False
    if not there:
        agent = (room.agent_name or "").strip() or DEFAULT_AGENT
        posted = await asyncio.to_thread(
            _post_row, session_id, message_id, member=email, agent_name=agent,
            organization_id=org, lead=opened["message"],
            event=reminder_event(opened["card"]),
            now_ms=int(datetime.now(UTC).timestamp() * 1000),
        )
        # Two calls at once: the other one wrote the row, and this one's fill
        # was declined. The chat holds the row either way.
        if not posted and not await asyncio.to_thread(_has_row, session_id, message_id, org):
            raise HTTPException(
                status_code=503, detail="The reminder could not open in the chat. Try again.",
            )
    return {
        "posted": posted,
        "message_id": message_id,
        "preview": opened["message"],
        "reminder": opened["reminder"],
        "context": opened["context"],
    }
