"""Memory API endpoints — Mem0 episodic memory (WBS 2.5).

Endpoints
---------
GET    /memory/{scope}                    List stored memories in a scope
POST   /memory/{scope}/search             Semantic search over a scope
DELETE /memory/{scope}/{memory_id}        Delete a single memory
POST   /memory/{scope}/add                Manually save a conversation to Mem0

Authorization
-------------
The path parameter is a **scope key**, not a user id — ``acb_memory.scope_key``
mints three shapes and this router must answer for all of them:

    a@b.com        that person's private episodic memory
    agent:<name>   memory shared across every user of that agent
    org:global     organisation-wide memory

``require_internal_auth`` was doing the whole job here, and it was the wrong
job. It proves the CALLER IS THE PLATFORM — which the Next.js BFF is, on behalf
of every signed-in user (``src/app/api/memory/[...path]/route.ts`` forwards the
internal token and the caller's identity headers together). So it stopped
outsiders and nobody else: any member could read, search, or delete any
colleague's private memories by putting their email in a URL, and the code
comment saying "would otherwise be an anonymous IDOR" was right about the shape
and wrong about who was excluded. See ``docs/multiplayer/memory-clearance.md``
§2.1 and ``docs/multiplayer/README.md`` §8 (Phase 0).

Both dependencies now apply: the token proves the request came through the
platform, ``_authorize_scope`` proves this person may touch this scope.
"""
from __future__ import annotations

import asyncio
from typing import Any

from acb_auth import UserContext, get_current_user, require_internal_auth
from acb_auth.roles import UserRole
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

_log = get_logger("gateway.memory")

router = APIRouter(
    prefix="/memory", tags=["memory"],
    dependencies=[Depends(require_internal_auth)],
)

#: Mirrors ``acb_memory.compartments``. Duplicated as constants rather than
#: imported so authorization still works when Mem0 is not installed — a router
#: that fails open because an optional dependency is missing is not a gate.
AGENT_SCOPE_PREFIX = "agent:"
ORG_SCOPE_KEY = "org:global"
ROOM_SCOPE_PREFIX = "room:"
PREFS_SCOPE_PREFIX = "prefs:"


class SearchRequest(BaseModel):
    query: str
    limit: int = 8


class AddRequest(BaseModel):
    messages: list[dict[str, str]]
    agent_id: str = "orchestrator"


def _deny(detail: str, status_code: int = 403) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _authorize_org(user: UserContext, *, write: bool) -> None:
    """Everyone with a role reads org memory; contributing to it is the gated
    act, because an agent that can silently append is how org memory fills with
    noise (migration 131)."""
    needed = "memory:write_org" if write else "memory:read_org"
    if not user.has_permission(needed):
        raise _deny(f"Forbidden: organisation memory needs '{needed}'.")


def _authorize_room(scope: str, user: UserContext, *, write: bool) -> None:
    """A room's memory has exactly the room's audience, so membership already
    answers this — the same predicate the transcript uses, because a room
    compartment holds what the transcript established."""
    thread_id = scope[len(ROOM_SCOPE_PREFIX):]
    if not thread_id:
        raise _deny("Unknown memory scope", 404)
    from gateway.rooms import resolve_room_access
    access = resolve_room_access(
        thread_id, user.email or "",
        organization_id=getattr(user, "organization_id", None),
    )
    if not access.can_read or (write and not access.can_send):
        raise _deny("Forbidden: that is another conversation's memory.")


def _authorize_prefs(scope: str, user: UserContext) -> None:
    """Preferences describe a person and travel with them into rooms, but they
    remain that person's to read and change."""
    owner = scope[len(PREFS_SCOPE_PREFIX):]
    if not owner or owner.strip().lower() != (user.email or "").strip().lower():
        raise _deny("Forbidden: those are somebody else's preferences.")


def _authorize_agent(scope: str, user: UserContext) -> None:
    """Agent memory is shared across the agent's users by design, so the
    question is whether you are one of them — the same ``agents:run:<name>``
    the run path enforces."""
    agent = scope[len(AGENT_SCOPE_PREFIX):]
    if not agent or not user.can_run_agent(agent):
        raise _deny(f"Forbidden: no access to agent '{agent}'.")


def _authorize_person(scope: str, user: UserContext) -> None:
    """A person's episodic memory is theirs alone.

    Deliberately not readable by admins: administering members is not the same
    as reading what they told an agent in private, and nothing in the product
    needs it. If that changes it should arrive as a named feature with an audit
    trail, not as a side effect of holding ``admin:members:manage``.
    """
    if scope.strip().lower() != (user.email or "").strip().lower():
        _log.warning(
            "memory.cross_user_denied",
            actor=(user.email or "")[:40], scope=scope[:40],
        )
        raise _deny("Forbidden: that is somebody else's private memory.")


def _authorize_scope(scope: str, user: UserContext, *, write: bool) -> None:
    """Raise 403/404 unless *user* may touch this memory scope.

    One rule per scope shape, and an unrecognised shape is refused rather than
    waved through — a scope vocabulary that grows must fail closed here, not
    quietly become world-readable.
    """
    scope = (scope or "").strip()
    if not scope:
        raise _deny("Unknown memory scope", 404)

    #: Scopes naming a PERSON or a membership rather than a shared store. A
    #: service principal must assert an identity to reach any of them: omitting
    #: an identity has to fail closed, since omission is how the original hole
    #: happened (`lib/memory.ts` forgot the header and got god mode).
    personal = (
        "@" in scope
        or scope.startswith(PREFS_SCOPE_PREFIX)
        or scope.startswith(ROOM_SCOPE_PREFIX)
    )
    if user.role is UserRole.AGENT and user.has_permission("*"):
        if not personal:
            return
        _log.warning("memory.service_call_without_identity", scope=scope[:40])
        raise _deny(
            "Forbidden: reading a person's memory requires acting as that "
            "person — send X-User-Email alongside the token."
        )

    if scope == ORG_SCOPE_KEY:
        return _authorize_org(user, write=write)
    if scope.startswith(ROOM_SCOPE_PREFIX):
        return _authorize_room(scope, user, write=write)
    if scope.startswith(PREFS_SCOPE_PREFIX):
        return _authorize_prefs(scope, user)
    if scope.startswith(AGENT_SCOPE_PREFIX):
        return _authorize_agent(scope, user)
    if "@" in scope:
        return _authorize_person(scope, user)
    raise _deny("Unknown memory scope", 404)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_mem0() -> Any:
    """Return the MemoryClient, or None if Mem0 is disabled."""
    try:
        from acb_memory import get_memory_client  # noqa: PLC0415
        return get_memory_client()
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{scope}", summary="List all memories in a scope")
async def list_memories(
    scope: str,
    user: UserContext = Depends(get_current_user),
) -> list[dict]:
    """Return all stored episodic memories in *scope*."""
    _authorize_scope(scope, user, write=False)
    client = _get_mem0()
    if client is None:
        return []
    return await client.get_all(scope)


@router.post("/{scope}/search", summary="Semantic search over memories")
async def search_memories(
    scope: str,
    req: SearchRequest,
    user: UserContext = Depends(get_current_user),
) -> list[dict]:
    """Return memories in *scope* semantically relevant to the query."""
    _authorize_scope(scope, user, write=False)
    client = _get_mem0()
    if client is None:
        return []
    return await client.search(scope, req.query, limit=req.limit)


@router.delete("/{scope}/{memory_id}", status_code=204, summary="Delete a memory")
async def delete_memory(
    scope: str,
    memory_id: str,
    user: UserContext = Depends(get_current_user),
) -> None:
    """Delete a single stored memory by ID, from a scope you may write.

    Two checks, because ``MemoryClient.delete`` takes only a memory id and so
    cannot enforce anything itself: authorize the scope, then confirm the
    memory is actually IN it. Without the second, naming your own scope and
    somebody else's memory id would delete their memory — the same IDOR one
    level down, and the one a path-parameter check alone would leave open.
    """
    _authorize_scope(scope, user, write=True)
    client = _get_mem0()
    if client is None:
        return
    owned = {
        str(m.get("id"))
        for m in await client.get_all(scope)
        if isinstance(m, dict) and m.get("id")
    }
    if memory_id not in owned:
        # 404, not 403: whether a memory id exists elsewhere is itself
        # something the caller should not be able to probe for.
        raise HTTPException(status_code=404, detail="Memory not found")
    await client.delete(memory_id)


def _holds_a_covered_turn(
    scope: str, user: UserContext, messages: list[dict[str, str]],
) -> bool:
    """True when *messages* hold a turn of a covered run (H-236 follow-up).

    The chat posts the whole conversation here when its panel closes. A
    covered run's conversation holds member data from a sandboxed turn, and
    the run's own extraction skips it (``routes/agent.py``). The server
    recorded the texts of each covered conversation
    (``executor.remember_no_egress_text``), so this route skips the save too.
    The request names no run and no session, and nothing in it decides this.
    An error skips the save. A member with no covered run is unchanged.
    """
    texts: list[object] = [m.get("content") for m in messages if isinstance(m, dict)]
    try:
        from orchestrator.executor import texts_were_no_egress
    except Exception:  # no record to read: nothing was covered here
        return False
    try:
        covered = texts_were_no_egress(user.email, texts) or texts_were_no_egress(scope, texts)
    except Exception:  # fail closed: skip the save
        covered = True
    if covered:
        _log.info("memory.add_skipped_no_egress", messages=len(texts))
    return covered


@router.post("/{scope}/add", status_code=202, summary="Save a conversation to Mem0")
async def add_memories(
    scope: str,
    req: AddRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Extract and store facts from the provided messages.

    This endpoint is called by the Control Plane after a session ends.
    The extraction runs asynchronously — returns 202 immediately.
    """
    _authorize_scope(scope, user, write=True)
    if _holds_a_covered_turn(scope, user, req.messages):
        return {"status": "skipped", "message_count": len(req.messages)}
    client = _get_mem0()
    if client is None:
        return {"status": "mem0_disabled"}
    # Fire-and-forget: don't block the response
    asyncio.create_task(
        client.add(scope, req.messages, agent_id=req.agent_id)
    )
    return {"status": "queued", "message_count": len(req.messages)}


@router.get("/{scope}/status", summary="Memory system status")
async def memory_status(
    scope: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Return the status of both memory layers for *scope*.

    Gated like the others: the count alone tells you how much a colleague has
    stored, which is not much and is still not yours to know.
    """
    _authorize_scope(scope, user, write=False)
    from acb_common import get_settings  # noqa: PLC0415

    settings = get_settings()
    mem0_enabled: bool = getattr(settings, "mem0_enabled", False)
    graphiti_enabled: bool = getattr(settings, "graphiti_enabled", False)

    mem0_count = 0
    if mem0_enabled:
        client = _get_mem0()
        if client:
            memories = await client.get_all(scope)
            mem0_count = len(memories)

    return {
        "mem0_enabled": mem0_enabled,
        "graphiti_enabled": graphiti_enabled,
        "count": mem0_count,
    }
