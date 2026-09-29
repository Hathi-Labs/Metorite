"""Rooms — the HTTP surface for who is in a conversation and what it can reach.

Spec: ``docs/multiplayer/README.md`` §4.5 (API surface), §7 (UX),
``project-docs/specs/groups_sessions_authority.md`` §2-§4.

Mounted under ``/chat`` alongside the history CRUD, because a room *is* a chat
session — there is no second object. Everything here reads or writes the two
tables migration 138 and 139 added and nothing else::

    GET    /chat/sessions/{id}/room              the whole room state, one call
    PATCH  /chat/sessions/{id}/room              visibility / floor / history
    POST   /chat/sessions/{id}/participants      invite  {subject, role}
    PATCH  /chat/sessions/{id}/participants/{s}  change a role
    DELETE /chat/sessions/{id}/participants/{s}  remove, or leave (s == you)
    POST   /chat/sessions/{id}/agents            add an agent to the room
    DELETE /chat/sessions/{id}/agents/{name}     remove one
    POST   /chat/sessions/{id}/presence          heartbeat — "I am here"
    GET    /chat/sessions/{id}/room-stream       SSE: room events, live
    GET    /chat/directory                       people and groups you can invite

**One call for the whole room** is deliberate. The header renders participants,
presence, the agents present, and the capability cap together or not at all —
a header assembled from four racing requests shows a room that never existed.

**The capability cap is computed here, not in the UI.** A shared run acts at the
intersection of its participants' access, which means a room can silently lose
an integration when somebody joins. §3 of the spec calls that failure mode
"mystery" and requires the cap be stated. ``_capability_cap`` answers "what does
this room NOT have, and who is the reason" so the header can say it in words.

**Every read and write here binds the caller's tenant** (WS-27bm S15,
``projects_ai_chat.md`` §21). Each helper opens
``acb_graph.tenant_session(organization_id)`` with ``user.organization_id``,
because the room tables are FORCE RLS in production. No helper opens the
unbound ``get_session()``.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from gateway.room_stream import (
    publish_room_event,
    read_presence,
    replay_room_events,
    subscribe_room_events,
    touch_presence,
)
from gateway.rooms import ROOM_ROLES, resolve_room_access
from pydantic import BaseModel

_log = get_logger("gateway.rooms.api")

router = APIRouter(
    prefix="/chat", tags=["rooms"],
    dependencies=[require_feature_router("chat")],
)

VISIBILITIES = ("private", "people", "org")
FLOOR_MODES = ("open", "driver")
HISTORY_VISIBILITIES = ("full", "since_join")

#: A room is a working group, not a broadcast. The limit is on named subjects,
#: not on people: `org` is one subject and may resolve to the whole company.
MAX_PARTICIPANTS = 50


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ParticipantRequest(BaseModel):
    subject: str                    # email | group:<slug> | org
    role: str = "member"


class ParticipantPatch(BaseModel):
    role: str


class RoomPatch(BaseModel):
    visibility: str | None = None
    floor_mode: str | None = None
    history_visibility: str | None = None


class AgentRequest(BaseModel):
    agent_name: str
    role: str = "mentioned"         # primary | mentioned


class PresenceRequest(BaseModel):
    state: str = "viewing"          # viewing | typing


# ---------------------------------------------------------------------------
# Subject vocabulary
# ---------------------------------------------------------------------------

def _valid_subject(subject: str) -> bool:
    """email | ``group:<slug>`` | ``org`` — the room participant vocabulary.

    **NOT** identical to ``routes/apps/grants.is_valid_subject``: that one is
    ``email | agent:<name> | agents:*`` and rejects ``org`` (``grants.py:77``).
    Rooms alone honours ``group:`` — see ``tenancy_and_visibility.md`` §3.2.
    """
    if subject == "org":
        return True
    if subject.startswith("group:"):
        return len(subject) > 6
    return "@" in subject and len(subject) > 2


# ---------------------------------------------------------------------------
# Room state
# ---------------------------------------------------------------------------

def _load_room_state(
    session_id: str, organization_id: str | None,
) -> dict[str, Any]:
    """Participants (with identities), agents, and settings — one transaction."""
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        session_row = s.execute(
            text(
                "SELECT user_id, agent_name, title, "
                "       COALESCE(visibility, 'private') AS visibility, "
                "       COALESCE(floor_mode, 'open') AS floor_mode, "
                "       COALESCE(history_visibility, 'full') AS history_visibility "
                "FROM chat_session WHERE id = :sid"
            ),
            {"sid": session_id},
        ).first()

        parts = s.execute(
            text(
                "SELECT p.subject, p.role, p.added_by, p.added_at, "
                "       u.display_name, u.avatar_url, "
                "       COALESCE(u.status, 'active') AS status, "
                "       g.display_name AS group_name "
                "FROM chat_session_participant p "
                "LEFT JOIN app_user u ON u.email = p.subject "
                "LEFT JOIN org_group g ON ('group:' || g.slug) = p.subject "
                "WHERE p.session_id = :sid "
                "ORDER BY p.added_at"
            ),
            {"sid": session_id},
        ).fetchall()

        agents = s.execute(
            text(
                "SELECT agent_name, role, added_by, added_at "
                "FROM chat_session_agent WHERE session_id = :sid "
                "ORDER BY role DESC, added_at"
            ),
            {"sid": session_id},
        ).fetchall()

    return {
        "session": session_row,
        "participants": [
            {
                "subject": p.subject,
                "role": p.role,
                "kind": ("org" if p.subject == "org"
                         else "group" if p.subject.startswith("group:")
                         else "person"),
                "displayName": p.group_name or p.display_name or p.subject,
                "avatarUrl": p.avatar_url,
                # A suspended member still caps the room (intersect() ANDs
                # is_active), so the UI must be able to SAY that rather than
                # showing an ordinary-looking participant next to a dead room.
                "status": p.status if p.subject and "@" in p.subject else "active",
                "addedBy": p.added_by,
                "addedAt": p.added_at.isoformat() if p.added_at else None,
            }
            for p in parts
        ],
        "agents": [
            {
                "agentName": a.agent_name,
                "role": a.role,
                "addedBy": a.added_by,
                "addedAt": a.added_at.isoformat() if a.added_at else None,
            }
            for a in agents
        ],
    }


async def _capability_cap(session_id: str, members: list[str]) -> dict[str, Any]:
    """What this room cannot reach, and who is the reason.

    The intersection rule is invisible by construction — the agent simply
    doesn't have the credential any more. So compute, per capability that ANY
    member holds, the members who don't: those are exactly the capabilities the
    room lost by being shared, with an attributable cause.

    Costs one ``resolve_access`` per member and is only called for rooms with
    more than one member, so a solo session never pays for it.
    """
    if len(members) < 2:
        return {"capped": [], "inactive": [], "degraded": False}

    from acb_auth import resolve_access

    emails = [m for m in members if "@" in m]
    if len(emails) < 2:
        # Group and `org` subjects are expanded by the authority fold itself
        # (resolve_session_access); naming a cause here would mean naming a
        # person the room's owner never added.
        return {"capped": [], "inactive": [], "degraded": False}

    try:
        resolved = {e: await resolve_access(e) for e in emails}
    except Exception:
        _log.warning("rooms.cap_resolve_failed", session_id=session_id, exc_info=True)
        return {"capped": [], "inactive": [], "degraded": True}

    inactive = sorted(e for e, a in resolved.items() if not a.is_active)

    #: Only scoped capabilities are worth naming — "this room lost Zoho" is
    #: actionable, "this room lost some permission pattern" is not.
    interesting = {
        cap
        for access in resolved.values()
        for cap in access.allowed
        if cap.startswith(("integrations:use:", "agents:run:"))
    }

    capped: list[dict[str, Any]] = []
    for cap in sorted(interesting):
        missing = sorted(e for e, a in resolved.items() if not a.has(cap))
        if missing:
            capped.append({"capability": cap, "missingFor": missing})

    return {"capped": capped, "inactive": inactive, "degraded": False}


@router.get("/sessions/{session_id}/room", summary="Room state: people, agents, cap")
async def get_room(
    session_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if access.unknown_session:
        # A thread that has never been persisted is a room of one, and saying
        # so is more useful than 404: the composer needs an answer before the
        # first turn creates the row.
        return {
            "sessionId": session_id,
            "exists": False,
            "you": _me(access),
            "participants": [],
            "agents": [],
            "presence": [],
            "settings": {
                "visibility": "private", "floorMode": "open",
                "historyVisibility": "full",
            },
            "cap": {"capped": [], "inactive": [], "degraded": False},
        }

    state, presence = await asyncio.gather(
        asyncio.to_thread(_load_room_state, session_id, user.organization_id),
        read_presence(session_id),
    )
    cap = await _capability_cap(session_id, access.members)
    row = state["session"]

    return {
        "sessionId": session_id,
        "exists": True,
        "title": getattr(row, "title", None),
        "createdBy": getattr(row, "user_id", None),
        "you": _me(access),
        "participants": state["participants"],
        "agents": state["agents"],
        "presence": presence,
        "settings": {
            "visibility": access.visibility,
            "floorMode": access.floor_mode,
            "historyVisibility": access.history_visibility,
        },
        "isShared": access.is_shared,
        "cap": cap,
    }


def _me(access) -> dict[str, Any]:
    return {
        "email": access.email,
        "role": access.role,
        "canRead": access.can_read,
        "canSend": access.can_send,
        "canCancel": access.can_cancel,
        "canInvite": access.can_invite,
        "canManage": access.can_manage,
        "sinceMessageTs": access.since_message_ts,
    }


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

def _add_participant(
    session_id: str, subject: str, role: str, added_by: str,
    *, waterline_ms: int | None, organization_id: str | None,
) -> bool:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        # S14 round 3: the creator owns a room with no participant row only
        # through the fallback in resolve_room_access, and the first row ends
        # that fallback. So the first add writes the creator's owner row
        # first, in this same transaction, or she loses her own room.
        s.execute(
            text(
                "INSERT INTO chat_session_participant (session_id, subject, role) "
                "SELECT id, user_id, 'owner' FROM chat_session "
                "WHERE id = :sid AND user_id LIKE '%@%' "
                "AND NOT EXISTS (SELECT 1 FROM chat_session_participant "
                "WHERE session_id = :sid) "
                "ON CONFLICT DO NOTHING"
            ),
            {"sid": session_id},
        )
        count = s.execute(
            text("SELECT count(*) AS n FROM chat_session_participant WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar()
        if (count or 0) >= MAX_PARTICIPANTS:
            raise HTTPException(
                status_code=400,
                detail=f"A room holds at most {MAX_PARTICIPANTS} named participants.",
            )
        result = s.execute(
            text(
                "INSERT INTO chat_session_participant "
                "  (session_id, subject, role, added_by, join_message_ts) "
                "VALUES (:sid, :subject, :role, :by, :waterline) "
                "ON CONFLICT (session_id, subject) DO UPDATE SET role = EXCLUDED.role"
            ),
            {"sid": session_id, "subject": subject, "role": role,
             "by": added_by, "waterline": waterline_ms},
        )
        return result.rowcount > 0


@router.post("/sessions/{session_id}/participants", summary="Add someone to a room")
async def add_participant(
    session_id: str,
    req: ParticipantRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Invite a person, a group, or the whole org into this conversation.

    Sharing a private thread is the moment its clearance drops to the
    intersection, so the response carries the new cap: the caller finds out
    what the room just lost in the same round trip that lost it.
    """
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not access.can_invite:
        raise HTTPException(status_code=403, detail=access.denied("invite people"))
    if access.unknown_session:
        raise HTTPException(
            status_code=409,
            detail="This conversation has no turns yet — send a message before sharing it.",
        )

    subject = req.subject.strip().lower()
    if not _valid_subject(subject):
        raise HTTPException(
            status_code=400,
            detail="A participant is an email address, 'group:<slug>', or 'org'.",
        )
    if req.role not in ROOM_ROLES:
        raise HTTPException(
            status_code=400, detail=f"Role must be one of {', '.join(ROOM_ROLES)}.",
        )

    # The waterline: by default a new participant reads from here on, not the
    # whole history. Rooms whose owner chose 'full' ignore it at read time —
    # it is recorded either way so switching the setting later is not a
    # retroactive disclosure of something we failed to write down.
    waterline = int(time.time() * 1000)

    await asyncio.to_thread(
        _add_participant, session_id, subject, req.role, email,
        waterline_ms=waterline, organization_id=user.organization_id,
    )
    # Sharing turns a private thread into a room. `people` is the honest
    # visibility for "the named subjects below" and mirrors apps.visibility.
    if access.visibility == "private":
        await asyncio.to_thread(
            _set_visibility, session_id, "people", user.organization_id,
        )

    await publish_room_event(session_id, {
        "type": "PARTICIPANT_JOINED",
        "subject": subject, "role": req.role, "addedBy": email,
    })

    fresh = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    cap = await _capability_cap(session_id, fresh.members)
    return {"ok": True, "subject": subject, "role": req.role, "cap": cap}


def _set_visibility(
    session_id: str, visibility: str, organization_id: str | None,
) -> None:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        s.execute(
            text("UPDATE chat_session SET visibility = :v, updated_at = now() WHERE id = :sid"),
            {"sid": session_id, "v": visibility},
        )


@router.patch("/sessions/{session_id}/participants/{subject}", summary="Change a room role")
async def patch_participant(
    session_id: str,
    subject: str,
    req: ParticipantPatch,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not access.can_invite:
        raise HTTPException(status_code=403, detail=access.denied("change roles"))
    if req.role not in ROOM_ROLES:
        raise HTTPException(
            status_code=400, detail=f"Role must be one of {', '.join(ROOM_ROLES)}.",
        )

    changed = await asyncio.to_thread(
        _update_participant_role, session_id, subject.strip().lower(), req.role,
        user.organization_id,
    )
    if not changed:
        raise HTTPException(status_code=404, detail="Not a participant of this room")

    await publish_room_event(session_id, {
        "type": "PARTICIPANT_ROLE_CHANGED",
        "subject": subject, "role": req.role, "changedBy": email,
    })
    return {"ok": True}


def _update_participant_role(
    session_id: str, subject: str, role: str, organization_id: str | None,
) -> bool:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        # A room without an owner cannot be administered by anyone — the same
        # invariant the org keeps for its own owner (admin/_common.py).
        if role != "owner":
            remaining = s.execute(
                text(
                    "SELECT count(*) FROM chat_session_participant "
                    "WHERE session_id = :sid AND role = 'owner' AND subject <> :subject"
                ),
                {"sid": session_id, "subject": subject},
            ).scalar()
            current = s.execute(
                text(
                    "SELECT role FROM chat_session_participant "
                    "WHERE session_id = :sid AND subject = :subject"
                ),
                {"sid": session_id, "subject": subject},
            ).scalar()
            if current == "owner" and not remaining:
                raise HTTPException(
                    status_code=400,
                    detail="A room keeps at least one owner. Make someone else "
                           "an owner first.",
                )
        result = s.execute(
            text(
                "UPDATE chat_session_participant SET role = :role "
                "WHERE session_id = :sid AND subject = :subject"
            ),
            {"sid": session_id, "subject": subject, "role": role},
        )
        return result.rowcount > 0


@router.delete(
    "/sessions/{session_id}/participants/{subject}",
    summary="Remove someone, or leave",
)
async def remove_participant(
    session_id: str,
    subject: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Anyone may remove themselves; removing someone else takes invite rights."""
    email = user.email or ""
    subject = subject.strip().lower()
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")

    leaving = subject == email.lower()
    if not leaving and not access.can_invite:
        raise HTTPException(status_code=403, detail=access.denied("remove people"))

    removed = await asyncio.to_thread(
        _remove_participant, session_id, subject, user.organization_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Not a participant of this room")

    await publish_room_event(session_id, {
        "type": "PARTICIPANT_LEFT",
        "subject": subject, "removedBy": email, "self": leaving,
    })
    return {"ok": True, "left": leaving}


def _remove_participant(
    session_id: str, subject: str, organization_id: str | None,
) -> bool:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        remaining = s.execute(
            text(
                "SELECT count(*) FROM chat_session_participant "
                "WHERE session_id = :sid AND role = 'owner' AND subject <> :subject"
            ),
            {"sid": session_id, "subject": subject},
        ).scalar()
        current = s.execute(
            text(
                "SELECT role FROM chat_session_participant "
                "WHERE session_id = :sid AND subject = :subject"
            ),
            {"sid": session_id, "subject": subject},
        ).scalar()
        if current is None:
            return False
        if current == "owner" and not remaining:
            raise HTTPException(
                status_code=400,
                detail="The last owner cannot leave a room. Hand it over or "
                       "delete the conversation.",
            )
        result = s.execute(
            text(
                "DELETE FROM chat_session_participant "
                "WHERE session_id = :sid AND subject = :subject"
            ),
            {"sid": session_id, "subject": subject},
        )
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Room settings
# ---------------------------------------------------------------------------

@router.patch("/sessions/{session_id}/room", summary="Change room settings")
async def patch_room(
    session_id: str,
    req: RoomPatch,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not access.can_manage:
        raise HTTPException(status_code=403, detail=access.denied("change room settings"))

    updates: dict[str, str] = {}
    if req.visibility is not None:
        if req.visibility not in VISIBILITIES:
            raise HTTPException(status_code=400, detail="Unknown visibility")
        updates["visibility"] = req.visibility
    if req.floor_mode is not None:
        if req.floor_mode not in FLOOR_MODES:
            raise HTTPException(status_code=400, detail="Unknown floor mode")
        updates["floor_mode"] = req.floor_mode
    if req.history_visibility is not None:
        if req.history_visibility not in HISTORY_VISIBILITIES:
            raise HTTPException(status_code=400, detail="Unknown history visibility")
        updates["history_visibility"] = req.history_visibility
    if not updates:
        return {"ok": True, "changed": []}

    await asyncio.to_thread(
        _update_room, session_id, updates, user.organization_id,
    )
    await publish_room_event(session_id, {
        "type": "ROOM_SETTINGS_CHANGED", "changedBy": email, "settings": updates,
    })
    return {"ok": True, "changed": sorted(updates)}


def _update_room(
    session_id: str, updates: dict[str, str], organization_id: str | None,
) -> None:
    from acb_graph import tenant_session
    from sqlalchemy import text

    # Column names come from the closed vocabularies above, never from input.
    sets = ", ".join(f"{col} = :{col}" for col in updates)
    with tenant_session(organization_id) as s:
        s.execute(
            text(f"UPDATE chat_session SET {sets}, updated_at = now() WHERE id = :sid"),
            {"sid": session_id, **updates},
        )


# ---------------------------------------------------------------------------
# Agents in the room
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/agents", summary="Add an agent to a room")
async def add_room_agent(
    session_id: str,
    req: AgentRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Bring a second (or third) agent into the conversation.

    The agent must be one the CALLER may run, and — because a shared run acts at
    the intersection — one every participant may run. Checking only the caller
    would let someone add an agent the room can't actually use, which surfaces
    later as an unexplainable 403 mid-run.
    """
    from acb_auth import assert_can_run_agent_in_session

    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not access.can_manage:
        raise HTTPException(status_code=403, detail=access.denied("add agents"))

    agent_name = req.agent_name.strip()
    if not agent_name:
        raise HTTPException(status_code=400, detail="An agent name is required")
    if req.role not in ("primary", "mentioned"):
        raise HTTPException(status_code=400, detail="Role must be primary or mentioned")

    await assert_can_run_agent_in_session(user, agent_name, session_id)

    await asyncio.to_thread(
        _add_agent, session_id, agent_name, req.role, email,
        user.organization_id,
    )
    await publish_room_event(session_id, {
        "type": "AGENT_ADDED", "agentName": agent_name,
        "role": req.role, "addedBy": email,
    })
    return {"ok": True, "agentName": agent_name, "role": req.role}


def _add_agent(
    session_id: str, agent_name: str, role: str, added_by: str,
    organization_id: str | None,
) -> None:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        if role == "primary":
            # Exactly one primary: an unaddressed turn must have exactly one
            # answer, or two agents reply to the same message.
            s.execute(
                text(
                    "UPDATE chat_session_agent SET role = 'mentioned' "
                    "WHERE session_id = :sid AND role = 'primary'"
                ),
                {"sid": session_id},
            )
        s.execute(
            text(
                "INSERT INTO chat_session_agent (session_id, agent_name, role, added_by) "
                "VALUES (:sid, :agent, :role, :by) "
                "ON CONFLICT (session_id, agent_name) DO UPDATE SET role = EXCLUDED.role"
            ),
            {"sid": session_id, "agent": agent_name, "role": role, "by": added_by},
        )
        if role == "primary":
            # chat_session.agent_name stays the room's primary: every existing
            # read path (history, workspace, avatars) resolves the agent from
            # there, and a divergence between the two would be invisible.
            s.execute(
                text("UPDATE chat_session SET agent_name = :agent WHERE id = :sid"),
                {"sid": session_id, "agent": agent_name},
            )


@router.delete("/sessions/{session_id}/agents/{agent_name}", summary="Remove an agent")
async def remove_room_agent(
    session_id: str,
    agent_name: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not access.can_manage:
        raise HTTPException(status_code=403, detail=access.denied("remove agents"))

    removed = await asyncio.to_thread(
        _remove_agent, session_id, agent_name, user.organization_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="That agent is not in this room")
    await publish_room_event(session_id, {
        "type": "AGENT_REMOVED", "agentName": agent_name, "removedBy": email,
    })
    return {"ok": True}


def _remove_agent(
    session_id: str, agent_name: str, organization_id: str | None,
) -> bool:
    from acb_graph import tenant_session
    from sqlalchemy import text

    with tenant_session(organization_id) as s:
        role = s.execute(
            text(
                "SELECT role FROM chat_session_agent "
                "WHERE session_id = :sid AND agent_name = :agent"
            ),
            {"sid": session_id, "agent": agent_name},
        ).scalar()
        if role is None:
            return False
        if role == "primary":
            raise HTTPException(
                status_code=400,
                detail="This is the room's primary agent. Make another agent "
                       "primary before removing it.",
            )
        s.execute(
            text(
                "DELETE FROM chat_session_agent "
                "WHERE session_id = :sid AND agent_name = :agent"
            ),
            {"sid": session_id, "agent": agent_name},
        )
        return True


# ---------------------------------------------------------------------------
# Presence + live room events
# ---------------------------------------------------------------------------

@router.post("/sessions/{session_id}/presence", summary="Heartbeat — I am here")
async def heartbeat(
    session_id: str,
    req: PresenceRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # A solo session has nobody to tell. Skipping the write keeps single-player
    # free of a Redis round trip every 15 seconds.
    if not access.is_shared:
        return {"ok": True, "presence": []}

    await touch_presence(session_id, email, state=req.state)
    return {"ok": True, "presence": await read_presence(session_id)}


@router.get("/sessions/{session_id}/room-stream", summary="Live room events (SSE)")
async def room_stream(
    session_id: str,
    request: Request,
    since: str = "0",
    user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """Replay from ``since``, then tail live.

    Deliberately separate from ``/agent/run/{tid}/reconnect``: that stream is
    one run and is reset when the next one starts, this one is the room and
    outlives every run in it. A client holds two cursors and merges — which is
    what a room *is*, a sequence of runs with people around them.
    """
    email = user.email or ""
    access = await asyncio.to_thread(
        resolve_room_access, session_id, email,
        organization_id=user.organization_id,
    )
    if not access.can_read:
        raise HTTPException(status_code=404, detail="Conversation not found")

    async def _gen():
        try:
            for evt in await replay_room_events(session_id, since_id=since):
                yield f"data: {json.dumps(evt, default=str)}\n\n"
            yield 'data: {"type":"ROOM_REPLAY_DONE"}\n\n'

            last_beat = 0.0
            async for evt in subscribe_room_events(session_id, since_id="$"):
                if await request.is_disconnected():
                    break
                if evt.get("type") == "ROOM_PING":
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(evt, default=str)}\n\n"
                # Watching is presence: a viewer who never sends would
                # otherwise be invisible in the rail they are looking at.
                now = time.time()
                if now - last_beat > 15:
                    last_beat = now
                    await touch_presence(session_id, email)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.warning("rooms.stream_failed", session_id=session_id, exc_info=True)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# The share picker's directory
# ---------------------------------------------------------------------------

@router.get("/directory", summary="People and groups you can share with")
async def directory(
    user: UserContext = Depends(get_current_user),
    q: str | None = None,
) -> dict[str, Any]:
    """Active members and groups, for the share sheet's picker.

    Not admin-gated, unlike ``/admin/members``: you cannot invite someone you
    cannot name, and a colleague roster is not privileged information inside an
    org. It returns identity only — no roles, no permissions, no status beyond
    "active" — so it never becomes a back door onto the access model.
    """
    return await asyncio.to_thread(
        _directory, (q or "").strip().lower(), user.organization_id,
    )


def _directory(q: str, organization_id: str | None) -> dict[str, Any]:
    from acb_graph import tenant_session
    from sqlalchemy import text

    like = f"%{q}%" if q else "%"
    with tenant_session(organization_id) as s:
        people = s.execute(
            text(
                "SELECT email, display_name, avatar_url FROM app_user "
                "WHERE COALESCE(status, 'active') = 'active' "
                "  AND (lower(email) LIKE :q OR lower(COALESCE(display_name, '')) LIKE :q) "
                "ORDER BY COALESCE(display_name, email) LIMIT 100"
            ),
            {"q": like},
        ).fetchall()
        groups = s.execute(
            text(
                "SELECT g.slug, g.display_name, "
                "       (SELECT count(*) FROM org_group_member m WHERE m.group_id = g.id) AS n "
                "FROM org_group g "
                "WHERE lower(g.slug) LIKE :q OR lower(g.display_name) LIKE :q "
                "ORDER BY g.display_name LIMIT 50"
            ),
            {"q": like},
        ).fetchall()

    return {
        "people": [
            {"subject": p.email, "displayName": p.display_name or p.email,
             "avatarUrl": p.avatar_url, "kind": "person"}
            for p in people
        ],
        "groups": [
            {"subject": f"group:{g.slug}", "displayName": g.display_name,
             "memberCount": int(g.n or 0), "kind": "group"}
            for g in groups
        ],
    }
