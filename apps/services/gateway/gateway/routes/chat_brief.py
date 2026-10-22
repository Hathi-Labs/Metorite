"""``POST /chat/sessions/{session_id}/brief`` — the day's brief as the first
turn of a fresh chat (WS-62 CF-5, ``chat_first_surface.md`` §4.6, D-CF-6).

A phone that opens a fresh chat with ``NEXT_PUBLIC_CHAT_FIRST`` on asks for
the brief once. This route answers in one of two ways:

* **200** ``{posted, message_id, preview}`` — it wrote ONE assistant row into
  the session. Its content is the brief's lead line, and its one
  ``generative_ui`` event is a ``reportCard`` whose ``pills`` are the brief's
  lines. A tap on a pill sends that line as the member's turn.
* **204** — nothing to post. The session already holds a message, the member
  already got today's brief in another chat, the day has no brief, or the
  feed could not be read in full. The chat opens empty, with the starter
  pills (§4.6).

**One composition** (D-CF-6, accepted 2026-10-22). The brief is
``reach.plan.brief_parts`` over ``reach.loop.snapshot``: the same feed read
and the same lines that the WhatsApp brief sends (``proactive_reach.md``
§9.1). Nothing here writes a line of its own, and no model runs. My Day's own
``brief`` card (``my_day_feed.md`` MD-7) is not built yet. When it is, it reads
the same parts.

**Only the server creates an agent row** (D-PM-39, ``projects_ai_chat.md``
§20). The row goes in through the one minter, ``routes/agent.py``
``_mint_run_row``, which also makes the session row when the browser's own
upsert has not landed yet. The route then fills the minted row through the
one message seam, ``_upsert_messages``, as the member who asked. The S13
``WHERE`` admits that write: the mint stamped the member as the row's run
member, and no fold sealed it. ``test_rooms.py`` keeps the mint keyword in
``routes/agent.py`` alone, and this module names it nowhere.

**Once a day, for every device.** The stamp is one key,
``chat_brief_posted_on``, in ``user_settings.my_day_prefs``, written through
``routes/shell/prefs.py`` ``_write`` (MD-2d's one-key merge). It holds the
member's local date (``Day.feed_day``), so a chat opened at 23:50 and one at
00:10 are two days. A layout save never touches it (migration 259).

**Scope** (R5e). The tenant is the bound organization, and the member is the
session's. The session must be one the member can send in
(``rooms.resolve_room_access``). Another member's session answers 404, as
``GET .../messages`` would read empty. A room shared with a second person
answers 204: the brief is one member's morning, not a room's.

Fences: ``tests/unit/test_chat_brief_turn.py`` (pure) and
``tests/unit/test_chat_brief_turn_r8.py`` (the real database).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Response, status
from gateway.reach import loop, plan
from gateway.rooms import resolve_room_access

_log = get_logger("gateway.chat_brief")

router = APIRouter(
    prefix="/chat", tags=["chat"],
    dependencies=[require_feature_router("chat")],
)

#: The key of the once-a-day mark in ``user_settings.my_day_prefs``.
BRIEF_STAMP_KEY = "chat_brief_posted_on"
#: The agent a brief row names when the session names none.
DEFAULT_AGENT = "orchestrator"
#: The template the card draws with.
BRIEF_TEMPLATE = "reportCard"


def _now() -> datetime:
    """The route's one clock. The R8 suite pins it, so the brief's "waiting
    40 min" and the stamp's date come out the same on every run."""
    return datetime.now(UTC)


def brief_message_id(day: date) -> str:
    """The row id of the day's brief in a session: one for each day, so a
    second post into the same session is a conflict the mint ignores."""
    return f"brief-{day.isoformat()}"


def brief_card(parts: plan.BriefParts) -> dict[str, Any]:
    """The ``generative_ui`` value: a ``reportCard`` with the lines as pills.

    The shape is the catalog's (``genUITemplates.tsx`` ``TEMPLATE_CATALOG``,
    mirrored in ``write_artifact.py``): ``title``, ``pills`` and ``note``.
    """
    data: dict[str, Any] = {"title": parts.title, "pills": list(parts.lines)}
    if parts.rest > 0:
        data["note"] = f"+{parts.rest} more on My Day"
    return {"type": "template", "props": {"name": BRIEF_TEMPLATE, "data": data}}


def brief_event(parts: plan.BriefParts) -> dict[str, Any]:
    """The custom event of the row, as ``chat_fold`` stores one. A cutoff of
    0 and no segments draw the text first and the card under it."""
    return {"name": "generative_ui", "value": brief_card(parts), "segmentCutoff": 0}


# ── The reads ────────────────────────────────────────────────────────────────


def _message_count(session_id: str, organization_id: str) -> int:
    """How many rows the session holds, in the bound tenant. A minted row with
    no content counts: a run started, so the chat is not fresh."""
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        return int(s.execute(
            text("SELECT count(*) FROM chat_message WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar() or 0)


async def _local_today(org: str, user: UserContext, now: datetime) -> date:
    """The member's local date, by their day start (``Day.feed_day``)."""
    from gateway import my_day_loop

    day = await my_day_loop._read_day(org, user)
    return day.feed_day(now)


async def _stamped_on(uid: str) -> str | None:
    """The stored ``chat_brief_posted_on``, or None."""
    from gateway.db import tenant_session as _tenant_session
    from gateway.routes.shell import prefs

    async with _tenant_session() as db:
        row = await prefs._read(db, uid)
    raw = row[0] if row is not None else None
    if isinstance(raw, dict):
        value = raw.get(BRIEF_STAMP_KEY)
        return value if isinstance(value, str) else None
    return None


async def _stamp(uid: str, day: date) -> None:
    from gateway.db import tenant_session as _tenant_session
    from gateway.routes.shell import prefs

    async with _tenant_session() as db:
        await prefs._write(db, uid, BRIEF_STAMP_KEY, day.isoformat())


# ── The write ────────────────────────────────────────────────────────────────


def _post_row(
    session_id: str, message_id: str, *, member: str, agent_name: str,
    organization_id: str, lead: str, event: dict[str, Any], now_ms: int,
) -> bool:
    """Mint the agent row, fill it, and set the session's preview. True when
    the row holds the brief after the write."""
    from acb_graph import tenant_session
    from gateway.routes.agent import _mint_run_row
    from gateway.routes.chat import MessageRecord, _upsert_messages
    from sqlalchemy import text

    # The one minter (S14). It makes the session row too, when the browser's
    # own upsert has not landed, and it never raises.
    _mint_run_row(
        session_id, message_id, member=member, agent_name=agent_name,
        organization_id=organization_id,
    )
    declined = _upsert_messages(
        session_id,
        [MessageRecord(
            id=message_id, role="assistant", content=lead, timestamp=now_ms,
            custom_events=[event], author_email=agent_name, author_kind="agent",
        )],
        actor_email=member, agent_name=agent_name,
        organization_id=organization_id,
    )
    if message_id in declined:
        return False
    with tenant_session(organization_id) as s:
        s.execute(
            text(
                "UPDATE chat_session SET last_preview = :p, "
                "message_count = GREATEST(message_count, 1), updated_at = now() "
                "WHERE id = :id"
            ),
            {"p": lead, "id": session_id},
        )
    return True


# ── The route ────────────────────────────────────────────────────────────────


def _nothing(response: Response) -> Response:
    response.status_code = status.HTTP_204_NO_CONTENT
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/brief",
    summary="Post the day's brief as the first assistant turn of a fresh chat (CF-5)",
)
async def post_brief(
    session_id: str,
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> Any:
    """See the module docstring. 200 with the row's id, or 204 and no row."""
    org = str(getattr(user, "organization_id", None) or "")
    email = (user.email or "").strip().lower()
    if not org or not email:
        raise HTTPException(status_code=404, detail="Session not found")

    room = await asyncio.to_thread(
        resolve_room_access, session_id, email, organization_id=org,
    )
    if not room.can_read:
        # The same answer as a session that does not exist for this caller.
        raise HTTPException(status_code=404, detail="Session not found")
    if not room.can_send:
        raise HTTPException(status_code=room.refusal_status, detail=room.denied("post the brief"))
    if room.is_shared:
        return _nothing(response)

    if await asyncio.to_thread(_message_count, session_id, org) > 0:
        return _nothing(response)

    now = _now()
    today = await _local_today(org, user, now)
    if await _stamped_on(email) == today.isoformat():
        return _nothing(response)

    snap = await loop.snapshot(org, user, now)
    if not snap.complete:
        _log.info("chat_brief.read_incomplete", session_id=session_id[:12])
        return _nothing(response)
    parts = plan.brief_parts(snap.cards, snap.local_date, now=snap.now)
    if parts is None:
        return _nothing(response)

    lead = plan.brief_lead(parts)
    message_id = brief_message_id(snap.local_date)
    agent = (room.agent_name or "").strip() or DEFAULT_AGENT
    posted = await asyncio.to_thread(
        _post_row, session_id, message_id, member=email, agent_name=agent,
        organization_id=org, lead=lead, event=brief_event(parts),
        now_ms=int(now.timestamp() * 1000),
    )
    if not posted:
        raise HTTPException(status_code=503, detail="The brief could not be posted. Try again.")
    await _stamp(email, snap.local_date)
    return {"posted": True, "message_id": message_id, "preview": lead}
