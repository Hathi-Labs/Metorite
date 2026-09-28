"""Chat history CRUD — Postgres-backed sessions and messages.

Endpoints
---------
GET    /chat/sessions                          List every room the caller is in
POST   /chat/sessions                          Upsert a session (create or update metadata)
PATCH  /chat/sessions/{session_id}             Update session title / preview / count
DELETE /chat/sessions/{session_id}             Delete session + all its messages (CASCADE)

GET    /chat/sessions/{session_id}/messages    Fetch messages the caller may read
POST   /chat/sessions/{session_id}/messages    Upsert a batch of messages

Authorization moved from ownership to membership (migration 138 + gateway/rooms.py).
Every predicate that used to be ``WHERE user_id = :uid`` is now "is this person
in this room", which is the same question when the room has one member. Two
paths were not gated at all before and are now: ``POST /chat/sessions`` could
overwrite any session's metadata by id, and ``POST .../messages`` could write
messages into any session id. Both were reachable by any authenticated user.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger, get_settings
from fastapi import APIRouter, Depends, HTTPException, status
from gateway.rooms import SESSION_VISIBLE_SQL, RoomAccess, resolve_room_access
from pydantic import BaseModel

_log = get_logger("gateway.chat")

router = APIRouter(
    prefix="/chat", tags=["chat"],
    dependencies=[require_feature_router("chat")],
)

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class SessionUpsertRequest(BaseModel):
    id: str
    agent_name: str = "orchestrator"
    title: str | None = None
    last_preview: str | None = None
    message_count: int = 0


class SessionPatchRequest(BaseModel):
    title: str | None = None
    last_preview: str | None = None
    message_count: int | None = None


class MessageRecord(BaseModel):
    id: str
    #: The three roles the ``chat_message`` CHECK allows, and no other string
    #: (S14 fix round 1). The browser draws every role that is not ``user``
    #: as an agent reply, so a free-form role would pass as one. The route
    #: answers 422 for any other value, before any row is written.
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: int          # epoch-ms in JS; stored as timestamp_ms
    tool_events: list[Any] = []
    progress_lines: list[str] = []
    reasoning: str | None = None
    agent_state: dict[str, Any] | None = None
    custom_events: list[Any] = []
    #: Who produced this turn — a member's email, or an agent's registered name
    #: when ``author_kind == 'agent'``. Clients MAY send it; the server
    #: overrides it for human turns with the authenticated caller, because a
    #: client-supplied author is a client-supplied identity.
    #: A client write that names an agent can only UPDATE a row that the
    #: server created (WS-27bm S14, ``projects_ai_chat.md`` §20). It never
    #: inserts one.
    author_email: str | None = None
    author_kind: str | None = None      # human | agent | system


# ---------------------------------------------------------------------------
# Thin sync helpers (run in a thread to stay non-blocking)
# ---------------------------------------------------------------------------

def _get_sessions(user_id: str) -> list[dict]:
    """Every room this person can open, newest first.

    The list is the room list now, so it carries what the sidebar needs to
    distinguish "mine" from "shared with me" without a second round trip per
    row: the creator, the visibility, and how many people are in it.
    """
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with get_session() as s:
        rows = s.execute(
            text(
                "SELECT s.id, s.agent_name, s.title, s.last_preview, "
                "       s.message_count, s.created_at, s.updated_at, "
                "       s.user_id, COALESCE(s.visibility, 'private') AS visibility, "
                "       (SELECT count(*) FROM chat_session_participant p "
                "          WHERE p.session_id = s.id) AS participant_count "
                "FROM chat_session s "
                f"WHERE {SESSION_VISIBLE_SQL} "
                "ORDER BY s.updated_at DESC"
            ),
            {"uid": user_id},
        ).fetchall()
    return [
        {
            "id": r.id,
            "agentName": r.agent_name,
            "title": r.title,
            "lastPreview": r.last_preview,
            "messageCount": r.message_count,
            "createdAt": r.created_at.isoformat(),
            "updatedAt": r.updated_at.isoformat(),
            "visibility": r.visibility,
            "isOwner": r.user_id == user_id,
            # >1 means somebody else is in here too. The sidebar shows a shared
            # badge on exactly this signal, so it never lies about a solo thread.
            "participantCount": int(r.participant_count or 0),
        }
        for r in rows
    ]


def _upsert_session(user_id: str, req: SessionUpsertRequest) -> None:
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with get_session() as s:
        s.execute(
            text(
                """
                INSERT INTO chat_session (id, user_id, agent_name, title, last_preview, message_count)
                VALUES (:id, :uid, :agent_name, :title, :last_preview, :message_count)
                ON CONFLICT (id) DO UPDATE SET
                    agent_name    = EXCLUDED.agent_name,
                    title         = COALESCE(EXCLUDED.title,        chat_session.title),
                    last_preview  = COALESCE(EXCLUDED.last_preview, chat_session.last_preview),
                    message_count = EXCLUDED.message_count,
                    updated_at    = now()
                WHERE (
                       chat_session.user_id = :uid
                       -- S14 round 3: the creator only while the room has no
                       -- membership. An owner may have removed her.
                       AND NOT EXISTS (
                           SELECT 1 FROM chat_session_participant p0
                           WHERE p0.session_id = chat_session.id
                       )
                   )
                   OR EXISTS (
                       SELECT 1 FROM chat_session_participant p
                       WHERE p.session_id = chat_session.id
                         AND p.subject = :uid
                         AND p.role IN ('owner', 'member')
                   )
                """
            ),
            {
                "id": req.id,
                "uid": user_id,
                "agent_name": req.agent_name,
                "title": req.title,
                "last_preview": req.last_preview,
                "message_count": req.message_count,
            },
        )


def _ensure_session(
    session_id: str,
    user_id: str,
    agent_name: str = "orchestrator",
) -> None:
    """Insert a minimal chat_session row IF one doesn't already exist.

    The authoritative run-end persistence (chat_fold) must be self-sufficient:
    a brand-new session whose first turn's client dies before the frontend's
    own session upsert lands would otherwise hit the chat_message → chat_session
    foreign key and silently lose the message (defeating P0-3). This creates
    the parent row on demand, owned by the acting user, and never clobbers an
    existing session's metadata (DO NOTHING on conflict).

    🔴 **It never raises a role** (S14 fix round 1, ``projects_ai_chat.md``
    §20.4 rule 10). The mint calls it at every run start, as the member who
    starts the run. That member may reach the room only through a group or
    an org grant. So the owner row goes in only for the creator of the
    session, and only while the room has no participant row at all. An
    owner may remove the creator, and a later run must not undo that
    (round 2). The primary agent row goes in only when the room has no
    primary.
    A browser-created session (``_upsert_session``) still gets both on its
    first run, because that writer makes neither.
    """
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with get_session() as s:
        s.execute(
            text(
                """
                INSERT INTO chat_session (id, user_id, agent_name)
                VALUES (:id, :uid, :agent_name)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": session_id, "uid": user_id or "default",
             "agent_name": agent_name or "orchestrator"},
        )
        # A session is a room of one from birth. Creating the owner row here
        # rather than only in migration 138's backfill means membership is
        # never something a session acquires later — every read path can trust
        # the participant table instead of falling back to chat_session.user_id.
        if user_id and "@" in user_id:
            s.execute(
                text(
                    "INSERT INTO chat_session_participant (session_id, subject, role) "
                    "SELECT :id, :uid, 'owner' FROM chat_session "
                    "WHERE id = :id AND user_id = :uid "
                    # Round 2: only a room with no membership yet. A creator
                    # whom an owner removed must not win the role back.
                    "AND NOT EXISTS (SELECT 1 FROM chat_session_participant "
                    "WHERE session_id = :id) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": session_id, "uid": user_id},
            )
        if agent_name:
            s.execute(
                text(
                    "INSERT INTO chat_session_agent (session_id, agent_name, role) "
                    "SELECT :id, :agent, 'primary' WHERE NOT EXISTS ("
                    "  SELECT 1 FROM chat_session_agent "
                    "  WHERE session_id = :id AND role = 'primary') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": session_id, "agent": agent_name},
            )


def _patch_session(session_id: str, user_id: str, req: SessionPatchRequest) -> bool:
    """Apply partial update; returns False if session not found."""
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    sets: list[str] = ["updated_at = now()"]
    params: dict = {"id": session_id, "uid": user_id}
    if req.title is not None:
        sets.append("title = :title")
        params["title"] = req.title
    if req.last_preview is not None:
        sets.append("last_preview = :last_preview")
        params["last_preview"] = req.last_preview
    if req.message_count is not None:
        sets.append("message_count = :message_count")
        params["message_count"] = req.message_count

    with get_session() as s:
        result = s.execute(
            text(
                f"UPDATE chat_session s SET {', '.join(sets)} "  # noqa: S608
                "WHERE s.id = :id AND " + SESSION_VISIBLE_SQL
            ),
            params,
        )
        return result.rowcount > 0


def _delete_session(session_id: str, user_id: str) -> bool:
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with get_session() as s:
        # Deliberately NOT the membership predicate: deleting takes the room
        # away from everyone in it, so it stays an owner's act. A member who
        # wants out leaves (DELETE /chat/sessions/{id}/participants/{me}).
        result = s.execute(
            text(
                "DELETE FROM chat_session s "
                "WHERE s.id = :id AND ("
                # S14 round 3: the creator only while the room has no
                # membership, the same rule as resolve_room_access.
                "    (s.user_id = :uid AND NOT EXISTS ("
                "        SELECT 1 FROM chat_session_participant p0"
                "        WHERE p0.session_id = s.id))"
                "    OR EXISTS (SELECT 1 FROM chat_session_participant p"
                "               WHERE p.session_id = s.id AND p.subject = :uid"
                "                 AND p.role = 'owner')"
                ")"
            ),
            {"id": session_id, "uid": user_id},
        )
        return result.rowcount > 0


def _get_messages(
    session_id: str,
    user_id: str,
    limit: int | None = None,
    before: int | None = None,
    *,
    room: RoomAccess | None = None,
    held_permissions: frozenset[str] | None = None,
) -> list[dict]:
    """Fetch messages this person may read, always returned oldest→newest.

    When ``limit`` is given, returns only the most recent ``limit`` messages
    (windowed lazy-load).  ``before`` is a ``timestamp_ms`` cursor: only
    messages strictly older than it are returned, so the frontend can page
    backwards through history by passing the oldest timestamp it already has.

    When ``limit`` is omitted the full history is returned (backward compatible
    with callers that expect every message, e.g. compaction).

    Two room rules apply on top, and only ever in a room — a solo session takes
    neither branch and returns exactly what it always did:

    * the **waterline**: a late joiner in a ``since_join`` room never sees the
      turns that predate them (``chat_session_participant.join_message_ts``);
    * the **clearance filter**: a turn produced by a run acting on capabilities
      the reader does not hold comes back as a redaction stub rather than
      content (``groups_sessions_authority.md`` §4).
    """
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with get_session() as s:
        if room is None:
            visible = s.execute(
                text(
                    "SELECT 1 FROM chat_session s WHERE s.id = :id AND "
                    + SESSION_VISIBLE_SQL
                ),
                {"id": session_id, "uid": user_id},
            ).first()
            if not visible:
                return []
        elif not room.can_read:
            return []

        cols = (
            "SELECT id, role, content, timestamp_ms, tool_events, progress_lines, "
            "reasoning, agent_state, custom_events, author_email, author_kind, "
            "authority FROM chat_message WHERE session_id = :sid"
            # S14 (§20): the gateway mints the agent row of a run with no
            # content before the first checkpoint. A reader does not see it
            # until it holds content, a tool event, a custom event or
            # reasoning. SQL, not Python, so a LIMIT does not count it.
            " AND NOT (COALESCE(author_kind, CASE role WHEN 'assistant' "
            "THEN 'agent' ELSE 'other' END) = 'agent' "
            "AND content = '' "
            "AND jsonb_array_length(COALESCE(tool_events, '[]'::jsonb)) = 0 "
            "AND jsonb_array_length(COALESCE(custom_events, '[]'::jsonb)) = 0 "
            "AND COALESCE(reasoning, '') = '')"
        )
        params: dict = {"sid": session_id}
        if before is not None:
            cols += " AND timestamp_ms < :before"
            params["before"] = before
        if room is not None and room.since_message_ts is not None:
            cols += " AND timestamp_ms >= :waterline"
            params["waterline"] = room.since_message_ts

        if limit is not None and limit > 0:
            # Newest-first with LIMIT, then reverse to oldest→newest below.
            # Secondary sort by id keeps ties (messages sharing a timestamp_ms,
            # e.g. a user turn and its assistant reply stamped the same ms)
            # deterministic across reads instead of arbitrary.
            cols += " ORDER BY timestamp_ms DESC, id DESC LIMIT :limit"
            params["limit"] = limit
        else:
            cols += " ORDER BY timestamp_ms ASC, id ASC"

        rows = s.execute(text(cols), params).fetchall()

    # When we fetched newest-first (limit path), reverse to chronological order.
    if limit is not None and limit > 0:
        rows = list(reversed(rows))

    return [_render_message(r, user_id, held_permissions) for r in rows]


#: What a reader sees instead of a turn produced above their clearance.
REDACTION_NOTICE = (
    "This turn was produced by a run using access you do not hold, so its "
    "content is not shown to you."
)


def _render_message(
    r: Any, viewer: str, held: frozenset[str] | None,
) -> dict[str, Any]:
    """One stored row as the client sees it — redacted if the reader is not cleared.

    The rule (``groups_sessions_authority.md`` §4): a turn carries the clearance
    of the run that produced it. Somebody who was in the room when it ran has
    already seen it, so they always see it again. Anyone else must hold every
    capability the run held — otherwise the model may have laundered restricted
    content into text that looks innocuous, and the only safe render is a stub.

    ``authority`` is NULL on every solo and pre-135 row, which is why this
    function is a no-op for them rather than a filter they have to pass.
    """
    body = {
        "id": r.id,
        "role": r.role,
        "content": r.content,
        "timestamp": r.timestamp_ms,
        "toolEvents": r.tool_events or [],
        "progressLines": r.progress_lines or [],
        "reasoning": r.reasoning,
        "agentState": r.agent_state,
        "customEvents": r.custom_events or [],
        "authorEmail": getattr(r, "author_email", None),
        "authorKind": getattr(r, "author_kind", None),
    }

    authority = getattr(r, "authority", None)
    if not isinstance(authority, dict):
        return body
    if viewer in (authority.get("members") or []):
        return body
    caps = [c for c in (authority.get("caps") or []) if isinstance(c, str)]
    if not caps:
        return body
    missing = [c for c in caps if held is None or c not in held]
    if not missing:
        return body

    return {
        **body,
        "content": REDACTION_NOTICE,
        "toolEvents": [],
        "progressLines": [],
        "reasoning": None,
        "customEvents": [],
        "redacted": True,
        "redactedCaps": sorted(missing),
    }


#: Columns whose value is a RUN ARTIFACT — accumulated from the agent's event
#: stream, never authored by hand. Their upsert is MONOTONIC (see below); the
#: contract test in tests/unit/test_chat_message_upsert.py enforces that.
MONOTONIC_MESSAGE_COLUMNS = ("tool_events", "progress_lines", "custom_events")

#: Upsert for one chat_message row.
#:
#: FOUR writers reach the same row: the gateway's mint when a run starts
#: (routes/agent.py ``_mint_run_row``, S14), the Next translator's 3s
#: checkpoints (app/api/agent/chat/route.ts), the gateway's run-boundary fold
#: (chat_fold.persist_final_assistant_message), and the browser re-POSTing its
#: whole message list whenever anything changes (lib/sessions.saveMessages).
#: Blind ``= EXCLUDED.*`` made this last-writer-wins, so the LEANEST writer won:
#: a client whose SSE dropped mid-run re-POSTed a content-only snapshot over the
#: fold's complete row and erased the tool timeline plus the generative_ui cards
#: — inline AG-UI silently vanishing from earlier turns.
#:
#: So: an EMPTY incoming array never erases a stored non-empty one, and a NULL
#: incoming reasoning/agent_state never erases stored ones. These fields only
#: ever grow within a turn, so "keep what we have" is always the safe merge.
#: ``content`` stays a plain overwrite — it is genuinely rewritten in place as
#: the answer streams and is un-folded at RUN_FINISHED.
#: Authorship is SET ONCE. The same three writers race on a row, and two of
#: them (the browser re-POSTing its list, the translator's checkpoints) send
#: whatever the client has — which for an assistant row folded by the gateway
#: is nothing. COALESCE-keeping the stored value means the first writer to know
#: who produced a turn is the one that decides, and no later writer can rename
#: an author. That is the property attribution needs: a name in a transcript
#: must not be rewritable by anyone who can POST to the session.
#:
#: ⚠️ **One exception: the run's fold may set the author of an AGENT turn
#: again** (WS-27bm S12, ``projects_ai_chat.md`` §18). An ``@name`` turn's
#: checkpoint lands first, with the room's agent. Only the fold knows which
#: agent ran, so ``:author_from_run`` lets it replace ``author_email``. The
#: ``WHERE`` below lets the fold reach an agent row only, so the ``CASE`` has
#: no kind test of its own (S13). Only ``chat_fold`` passes True. Every
#: client write keeps the COALESCE, and ``authority`` keeps it for every
#: writer.
#:
#: 🔴 **The ``WHERE`` decides who may update a row, by its stored kind** (S12
#: fix round 1, S13 ``projects_ai_chat.md`` §19). The row id comes from the
#: client — the browser names it, and the fold's ``_persist_message_id`` is
#: the client's ``assistant_message_id`` — so without it, any room sender
#: could put their own words in another member's turn. The stored kind is
#: ``author_kind``. A NULL kind is an agent row when ``role`` is
#: ``assistant``, a system row when ``role`` is ``system``, and a human row
#: for every other role.
#:
#: * **Human row:** only a human write by the SAME member. ``_attribute``
#:   stamps a human write's ``author_email`` from the authenticated caller,
#:   never from the body. The check also requires
#:   ``EXCLUDED.author_kind = 'human'``, because a body that claims an agent
#:   turn can name any ``author_email``. The fold never writes a human row.
#: * **Agent row, the fold:** only when the stored ``run_member_email`` is
#:   NULL or is the member who started this run. The fold sets
#:   ``run_final_at``, which seals the row for clients.
#: * **Agent row, a client:** only an agent write by the member who started
#:   the run (``run_member_email``), and only before the seal. A NULL run
#:   member means that only the fold may update the row. That covers legacy
#:   rows and rows written by old code during the deploy.
#: * **System row:** no write updates it.
#:
#: 🔴 **Only the server creates an agent row or a system row** (S14,
#: ``projects_ai_chat.md`` §20). The ``INSERT`` is a ``SELECT`` with a guard.
#: It gives a new row only when ``:may_insert`` is true, which
#: ``_upsert_messages`` sets for a human write, the fold and the mint. For
#: every other write the ``SELECT`` gives a row only when the id exists, so
#: the conflict and the ``WHERE`` decide. A client insert of an agent row
#: would reach the model context of every member, because the history keeps
#: agent and system rows. ``VALUES`` coerced each parameter to its column
#: type and a ``SELECT`` does not, so every parameter carries a ``CAST``.
#:
#: **The mint only inserts** (``:mint``). The gateway creates the agent row
#: of a run before it opens the stream. On an existing id the ``WHERE`` is
#: false, so a mint never changes a row, even the caller's own reply.
#:
#: **The first content sets the time** (S14 fix round 1). The mint stamps the
#: server clock when the request arrives, and the prompt carries the browser
#: clock. A fast browser clock would sort the reply above its prompt. So while
#: the stored content is empty, a permitted write moves ``timestamp_ms``
#: forward to its own value. That is the time the first checkpoint had before
#: S14. Only a write that passes the ``WHERE`` reaches the ``SET``.
#:
#: ``run_member_email`` is set once, by the first writer of an agent row.
#: A conflict that fails the ``WHERE`` leaves the row alone and raises
#: nothing. ``RETURNING id`` then gives no row, and ``_upsert_messages``
#: reports that id as unchanged.
_MESSAGE_UPSERT_SQL = """
    INSERT INTO chat_message
        (id, session_id, role, content, timestamp_ms,
         tool_events, progress_lines, reasoning, agent_state, custom_events,
         author_email, author_kind, authority,
         run_member_email, run_final_at)
    SELECT
        CAST(:id AS text), CAST(:sid AS text), CAST(:role AS text),
        CAST(:content AS text), CAST(:ts AS bigint),
        CAST(:tool_events AS jsonb), CAST(:progress_lines AS jsonb),
        CAST(:reasoning AS text), CAST(:agent_state AS jsonb),
        CAST(:custom_events AS jsonb),
        CAST(:author_email AS text), CAST(:author_kind AS text),
        CAST(:authority AS jsonb),
        CAST(:run_member_email AS text),
        CASE WHEN CAST(:author_from_run AS boolean) THEN now() END
    WHERE CAST(:may_insert AS boolean)
       OR EXISTS (SELECT 1 FROM chat_message
                  WHERE session_id = CAST(:sid AS text)
                    AND id = CAST(:id AS text))
    ON CONFLICT (session_id, id) DO UPDATE SET
        content        = EXCLUDED.content,
        timestamp_ms   = CASE WHEN chat_message.content = ''
            THEN GREATEST(chat_message.timestamp_ms, EXCLUDED.timestamp_ms)
            ELSE chat_message.timestamp_ms END,
        author_email   = CASE
            WHEN CAST(:author_from_run AS boolean)
                 AND EXCLUDED.author_email IS NOT NULL
            THEN EXCLUDED.author_email
            ELSE COALESCE(chat_message.author_email, EXCLUDED.author_email) END,
        author_kind    = COALESCE(chat_message.author_kind,  EXCLUDED.author_kind),
        authority      = COALESCE(chat_message.authority,    EXCLUDED.authority),
        run_member_email = COALESCE(
            chat_message.run_member_email, EXCLUDED.run_member_email),
        run_final_at   = COALESCE(EXCLUDED.run_final_at, chat_message.run_final_at),
        tool_events    = CASE
            WHEN jsonb_array_length(COALESCE(EXCLUDED.tool_events, '[]'::jsonb)) > 0
            THEN EXCLUDED.tool_events ELSE chat_message.tool_events END,
        progress_lines = CASE
            WHEN jsonb_array_length(COALESCE(EXCLUDED.progress_lines, '[]'::jsonb)) > 0
            THEN EXCLUDED.progress_lines ELSE chat_message.progress_lines END,
        reasoning      = COALESCE(EXCLUDED.reasoning, chat_message.reasoning),
        agent_state    = COALESCE(EXCLUDED.agent_state, chat_message.agent_state),
        custom_events  = CASE
            WHEN jsonb_array_length(COALESCE(EXCLUDED.custom_events, '[]'::jsonb)) > 0
            THEN EXCLUDED.custom_events ELSE chat_message.custom_events END
    WHERE NOT CAST(:mint AS boolean)
      AND CASE COALESCE(
            chat_message.author_kind,
            CASE chat_message.role
                WHEN 'assistant' THEN 'agent'
                WHEN 'system'    THEN 'system'
                ELSE 'human' END)
        WHEN 'human' THEN
            NOT CAST(:author_from_run AS boolean)
            AND EXCLUDED.author_kind = 'human'
            AND lower(chat_message.author_email) = lower(EXCLUDED.author_email)
        WHEN 'agent' THEN
            CASE WHEN CAST(:author_from_run AS boolean) THEN
                chat_message.run_member_email IS NULL
                OR lower(chat_message.run_member_email)
                   = lower(EXCLUDED.run_member_email)
            ELSE
                EXCLUDED.author_kind = 'agent'
                AND chat_message.run_final_at IS NULL
                AND lower(chat_message.run_member_email)
                    = lower(EXCLUDED.run_member_email)
            END
        ELSE false
        END
    RETURNING id
"""


def _upsert_messages(
    session_id: str,
    messages: list[MessageRecord],
    *,
    actor_email: str | None = None,
    agent_name: str | None = None,
    authority: dict[str, Any] | None = None,
    author_from_run: bool = False,
    mint: bool = False,
) -> list[str]:
    """Write a batch of turns, stamping who produced each one.

    Attribution is derived here, not trusted from the client: a human turn is
    the authenticated caller's, full stop. ``role`` still decides which side of
    the conversation a turn sits on (the model's vocabulary); ``author_*``
    decides whose face the room renders next to it, and the two never mix.

    ``author_from_run`` is for ``chat_fold.persist_final_assistant_message``
    ONLY (WS-27bm S12). With it, the write may replace the author of a stored
    agent turn with the agent that ran, and it seals the row (S13). It never
    changes a human turn. A route handler must never pass it, and
    ``test_rooms.py`` checks the source of both callers.

    ``actor_email`` is also the run member of an agent row (S13): the caller
    for a client write, and the member who started the run for the fold.

    ``mint`` is for ``routes/agent.py`` ONLY (WS-27bm S14, §20). The gateway
    creates the agent row of a run before it opens the stream. A mint only
    inserts. When a row with that id exists, the mint changes nothing.

    Only the server creates an agent row or a system row (S14). A client
    write of a new agent row or a new system row inserts nothing, and its id
    is in the return value. A client may still update an agent row that the
    server created, under the S13 rules.

    Returns the ids whose write the SQL declined, in request order.
    """
    from acb_graph import get_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    if not messages:
        return []

    authority_json = json.dumps(authority) if authority else None
    # An empty member is no member: store NULL, so only the fold may update.
    run_member = (actor_email or "").strip().lower() or None
    declined: list[str] = []

    with get_session() as s:
        for m in messages:
            kind, author = _attribute(m, actor_email, agent_name)
            # S14: a client inserts a human row only. The fold and the mint
            # are the server, and they may insert an agent row.
            may_insert = kind == "human" or bool(author_from_run) or bool(mint)
            result = s.execute(
                text(_MESSAGE_UPSERT_SQL),
                {
                    "id": m.id,
                    "sid": session_id,
                    "role": m.role,
                    "content": m.content,
                    "ts": m.timestamp,
                    "tool_events": json.dumps(m.tool_events),
                    "progress_lines": json.dumps(m.progress_lines),
                    "reasoning": m.reasoning,
                    "agent_state": json.dumps(m.agent_state) if m.agent_state is not None else None,
                    "custom_events": json.dumps(m.custom_events),
                    "author_email": author,
                    "author_kind": kind,
                    # Only agent output carries a clearance — a human's own
                    # words are theirs regardless of what the run could reach.
                    "authority": authority_json if kind == "agent" else None,
                    "author_from_run": bool(author_from_run),
                    "mint": bool(mint),
                    "may_insert": may_insert,
                    # Only an agent row belongs to a run (S13).
                    "run_member_email": run_member if kind == "agent" else None,
                },
            )
            # RETURNING gives no row when the WHERE declined the update, or
            # when the insert guard declined a new row (S14).
            if result.first() is None:
                declined.append(m.id)
    return declined


def _attribute(
    m: MessageRecord, actor_email: str | None, agent_name: str | None,
) -> tuple[str | None, str | None]:
    """(author_kind, author_email) for one record.

    A client may TELL us a turn is an agent's. It may not tell us which HUMAN
    authored a turn: that is an identity claim, and the authenticated caller
    is the only answer we accept.

    Since S14 (``projects_ai_chat.md`` §20) the agent claim of a client
    decides only how the upsert treats the write. A client write with the
    kind ``agent`` or ``system`` inserts no row. It may update an agent row
    that the mint or the fold created, under the S13 rules, and the author
    that the server stored first stays.
    """
    if m.role == "system":
        return "system", None
    if m.role == "user" and m.author_kind != "agent":
        return "human", (actor_email or None)
    # Only the role ``user`` is a human turn (S14 fix round 1).
    return "agent", (m.author_email or agent_name or None)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.get("/sessions", summary="List chat sessions")
async def list_sessions(
    user: UserContext = Depends(get_current_user),
) -> list[dict]:
    return await asyncio.to_thread(_get_sessions, user.email or "default")


@router.post("/sessions", status_code=status.HTTP_200_OK, summary="Upsert a chat session")
async def upsert_session(
    req: SessionUpsertRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    await asyncio.to_thread(_upsert_session, user.email or "default", req)
    return {"ok": True, "id": req.id}


@router.patch("/sessions/{session_id}", summary="Update session metadata")
async def patch_session(
    session_id: str,
    req: SessionPatchRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    found = await asyncio.to_thread(_patch_session, session_id, user.email or "default", req)
    if not found:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    found = await asyncio.to_thread(_delete_session, session_id, user.email or "default")
    if not found:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}/messages", summary="Fetch messages for a session")
async def get_messages(
    session_id: str,
    user: UserContext = Depends(get_current_user),
    limit: int | None = None,
    before: int | None = None,
) -> list[dict]:
    """Return messages oldest→newest, subject to this reader's room access.

    Optional query params enable windowed lazy-loading:
    - ``limit``: return only the most recent N messages.
    - ``before``: a ``timestamp_ms`` cursor; only messages older than it are
      returned (used together with ``limit`` to page backwards on scroll-up).
    """
    email = user.email or "default"
    room = await asyncio.to_thread(resolve_room_access, session_id, email)
    if not room.can_read:
        # Historically a non-owner got [] rather than 403, and clients rely on
        # that: a session the browser knows locally but the server has never
        # seen must render empty, not error.
        return []

    held = await _held_permissions(user, room)
    return await asyncio.to_thread(
        _get_messages, session_id, email, limit, before,
        room=room, held_permissions=held,
    )


async def _held_permissions(
    user: UserContext, room: RoomAccess,
) -> frozenset[str] | None:
    """The capabilities this reader holds, or ``None`` when nothing is filtered.

    Resolving access costs a round trip, so it is only paid in a shared room —
    a solo transcript has no rows carrying a clearance to compare against.
    """
    if not room.is_shared:
        return None
    access = getattr(user, "access", None)
    allowed = getattr(access, "allowed", None)
    if allowed:
        return frozenset(allowed)
    try:
        from acb_auth import resolve_access
        resolved = await resolve_access(user.email or "")
        return frozenset(resolved.allowed)
    except Exception:
        # Fail closed on the FILTER, not on the read: an unknown clearance
        # redacts the handful of rows that carry one and shows the rest.
        _log.warning("chat.clearance_resolve_failed", exc_info=True)
        return frozenset()


@router.post(
    "/sessions/{session_id}/messages",
    status_code=status.HTTP_200_OK,
    summary="Upsert a batch of messages",
)
async def save_messages(
    session_id: str,
    messages: list[MessageRecord],
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Persist the client's view of a conversation.

    This endpoint had no authorization at all: any authenticated caller could
    write turns into any session id, which in a single-owner world was invisible
    and in a shared one is forgery. It now requires the ability to send in the
    room, and stamps the authenticated caller as the author of every human turn.

    The answer is ``{"ok": true, "saved": n, "unchanged": [ids]}``. An id in
    ``unchanged`` is a row that the caller may not update (S13, §19).
    """
    if len(messages) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 messages per upsert")

    email = user.email or "default"
    room = await asyncio.to_thread(resolve_room_access, session_id, email)
    if not room.can_send:
        raise HTTPException(status_code=403, detail=room.denied("save messages"))

    # A declined write is not an error (S13, §19.4 rule 5). The row stays as
    # it was, and ``unchanged`` names it so the caller can tell.
    unchanged = await asyncio.to_thread(
        _upsert_messages, session_id, messages,
        actor_email=email, agent_name=room.agent_name,
    )
    return {
        "ok": True,
        "saved": len(messages) - len(unchanged),
        "unchanged": unchanged,
    }


class MessageFeedbackRequest(BaseModel):
    message_id: str
    vote: str          # "up" | "down"
    session_id: str | None = None


@router.post("/feedback", summary="Record thumbs up/down feedback on a message")
async def record_message_feedback(
    req: MessageFeedbackRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Persist a 👍/👎 vote as an audit event (actor = the voting user)."""
    if req.vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="vote must be 'up' or 'down'")
    from acb_audit import AuditEvent, record  # noqa: PLC0415

    def _write() -> None:
        record(
            AuditEvent(
                actor=f"human:{user.email or 'anonymous'}",
                action="message_feedback",
                target=f"message:{req.message_id}",
                payload={"vote": req.vote, "session_id": req.session_id},
            )
        )

    await asyncio.to_thread(_write)
    return {"ok": True}


@router.get(
    "/active-sessions",
    summary="List session IDs that currently have an active (running) agent",
)
async def list_active_sessions(
    user: UserContext = Depends(get_current_user),
) -> list[dict]:
    """Return sessions whose agents are currently executing.

    Scans Redis ``cc:active:*`` keys (set by the executor's stream relay)
    and cross-references with the ``chat_session`` table to include
    agent names and titles.  Falls back to an empty list when Redis is
    unavailable — the frontend will rely on its local chatStore in that
    case.

    Used by the conversations sidebar to show a pulsing green dot next
    to sessions that are still running in the background, even after a
    browser refresh.
    """
    user_id = user.email or "default"
    active_threads: list[str] = []

    # ── Scan Redis for cc:active:* keys ────────────────────────────────
    try:
        import redis.asyncio as aioredis  # noqa: PLC0415
        settings = get_settings()
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            cursor = 0
            while True:
                cursor, keys = await r.scan(
                    cursor, match="cc:active:*", count=100
                )
                for k in keys:
                    # Strip the "cc:active:" prefix to recover the thread_id.
                    tid = k.removeprefix("cc:active:")
                    if tid:
                        active_threads.append(tid)
                if cursor == 0:
                    break
        finally:
            await r.aclose()
    except Exception:  # noqa: BLE001
        _log.warning("chat.active_sessions_redis_failed", exc_info=True)
        return []  # Redis unavailable — frontend falls back to local store

    if not active_threads:
        return []

    # ── Cross-reference with Postgres for agent name + title ───────────
    try:
        from acb_graph import get_session  # noqa: PLC0415
        from sqlalchemy import text  # noqa: PLC0415

        with get_session() as s:
            rows = s.execute(
                text(
                    "SELECT s.id, s.agent_name, s.title "
                    "FROM chat_session s "
                    "WHERE s.id = ANY(:ids) AND " + SESSION_VISIBLE_SQL
                ),
                {"ids": active_threads, "uid": user_id},
            ).fetchall()

        result = [
            {
                "threadId": r.id,
                "agentName": r.agent_name,
                "title": r.title,
            }
            for r in rows
        ]

        # Threads that are active in Redis but have no session row yet (the
        # agent started before the frontend's upsert landed) still belong in
        # the list. Threads that DO have a row and were filtered out belong to
        # someone else: including them leaked a live thread id to every user,
        # which the old `not in found_ids` fallback did on every poll.
        with get_session() as s:
            known = {
                row.id for row in s.execute(
                    text("SELECT id FROM chat_session WHERE id = ANY(:ids)"),
                    {"ids": active_threads},
                ).fetchall()
            }
        for tid in active_threads:
            if tid not in known:
                result.append({
                    "threadId": tid,
                    "agentName": "unknown",
                    "title": None,
                })
        return result
    except Exception:  # noqa: BLE001
        # Postgres unavailable — return thread IDs without metadata.
        return [
            {"threadId": tid, "agentName": "unknown", "title": None}
            for tid in active_threads
        ]
