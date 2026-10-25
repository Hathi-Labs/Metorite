"""The member's pre-read memory of one chat turn (WS-47 WAC-P2).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §17.3.

ONE builder of the ``memory_context`` block that a chat turn carries into
its run (``executor._context_preamble_parts``). Two callers: the web chat
route (``routes/agent.py``) and the WhatsApp run (``whatsapp_channel/bot_run``).
Before WAC-P2 the block lived inside the web route, so a WhatsApp turn got no
memory. A second copy would drift, so the route's code moved here unchanged.

**What it reads.** Mem0 (the member's private facts), the room's memory, the
member's preferences, the agent's shared memory, the org's memory, and the
knowledge graph. ``acb_memory.resolve_clearance`` decides which compartments
the turn may read. A shared room reads no private compartment.

**The scope.** *user_id* is the member that the caller bound from the server
side: the web session's member, or the WhatsApp link row's member. The org is
the tenant the caller bound (``bind_tenant``), never a request field (R5,
R11). The block is cached for each thread in Redis, keyed by the clearance,
so it stays byte-stable across turns (``acb_memory.get_session_memory``).

Fences: ``tests/unit/test_wac_channel_parity.py`` (the WhatsApp turn) and
``evals/trajectories/test_context_assembly_trajectory.py`` (the web turn).
"""

from __future__ import annotations

import asyncio
from typing import Any

# Mem0 keeps five database connections for the whole process, and it refuses a
# sixth caller at once, which reads as "no memory" and the block is cached for
# ten minutes. So all the Mem0 searches of every turn share two slots.
# One semaphore for each event loop, because a semaphore binds to one loop.
_MEM0_SLOTS = 2
_gates: dict[int, asyncio.Semaphore] = {}


async def _limited(coro: Any) -> Any:
    loop_id = id(asyncio.get_running_loop())
    gate = _gates.setdefault(loop_id, asyncio.Semaphore(_MEM0_SLOTS))
    async with gate:
        return await coro


async def _private_parts(user_id: str, message: str, has_user: bool) -> list[str]:
    """Mem0: this user's private episodic facts. Never in a room — not the
    owner's, not the typer's. The scope key is simply not passed, so there
    is no retrieval to leak (memory-clearance.md §3.3: a boundary, not a
    request in a system prompt)."""
    from acb_memory import get_memory_context

    if not has_user:
        return []
    mem_ctx = await _limited(get_memory_context(user_id, message))
    return ["## Memory from past conversations\n" + mem_ctx] if mem_ctx else []


async def _scoped_parts(clearance: Any, agent_name: str, message: str) -> list[str]:
    """The room, the preferences, the agent's and the org's memory."""
    from acb_memory import get_scoped_context, scope_key

    # The four lookups do not depend on each other, so they run together.
    # gather keeps the order, so the block is byte-identical to a serial read.
    async def _none() -> str:
        return ""

    room, prefs, agent_ctx, org_ctx = await asyncio.gather(
        _limited(get_scoped_context(clearance.room, message, header="Room memory"))
        if clearance.room else _none(),
        _limited(get_scoped_context(clearance.prefs, message, header="Preferences"))
        if clearance.prefs else _none(),
        _limited(get_scoped_context(
            scope_key(agent=agent_name), message, header="Agent memory")),
        _limited(get_scoped_context(
            scope_key(org=True), message, header="Organisation memory")),
    )
    parts: list[str] = []
    # What this room has established. Replaces the personal compartment
    # rather than adding to it: shared work is remembered where everyone in
    # the room can see it.
    if room:
        parts.append("## What this room has established\n" + room)
    # How the person being answered likes to work. Safe to carry into a
    # room: they are in it, and preferences describe them rather than
    # disclosing what they told the agent in private.
    if prefs:
        parts.append("## How to answer this person\n" + prefs)
    # Agent memory: shared across every user of this agent
    if agent_ctx:
        parts.append("## This agent's shared memory\n" + agent_ctx)
    # Org memory: organisation-wide, shared by every agent + user
    if org_ctx:
        parts.append("## Organisation-wide memory\n" + org_ctx)
    return parts


async def _graph_parts(message: str, has_user: bool) -> list[str]:
    """Graphiti: time-aware facts about entities in the query."""
    from acb_memory import search_entity_timeline

    if not has_user:
        return []
    graph_ctx = await search_entity_timeline(message[:80], message)
    return ["## Timeline facts from knowledge graph\n" + graph_ctx] if graph_ctx else []


async def _redis_client() -> Any:
    try:
        from orchestrator.stream_relay import _get_client

        return await _get_client()
    except Exception:  # no Redis → fetch fresh each turn
        return None


async def memory_preread(
    *,
    user_id: str,
    agent_name: str,
    thread_id: str,
    message: str,
    shared: bool = False,
    clearance: Any = None,
) -> str:
    """The memory block of one turn, or ``""``.

    *user_id* is the server-bound member, or ``"anonymous"``. *shared* is
    True in a shared room: then no member's private facts and no graph facts
    are read. *clearance* is the caller's ``acb_memory.resolve_clearance``
    answer, when it has one. Agent and org memory are not one person's, so
    they load for an anonymous user too. ImportError (no ``acb_memory``)
    reads nothing.
    """
    if not message:
        return ""
    try:
        from acb_memory import get_session_memory, resolve_clearance
    except ImportError:
        return ""
    if clearance is None:
        clearance = resolve_clearance(
            actor=user_id if user_id != "anonymous" else "",
            agent_name=agent_name,
            thread_id=thread_id,
            shared=shared,
        )
    has_user = user_id != "anonymous" and not shared

    async def _build_memory_block() -> str:
        private, scoped, graph = await asyncio.gather(
            _private_parts(user_id, message, has_user),
            _scoped_parts(clearance, agent_name, message),
            _graph_parts(message, has_user),
        )
        return "\n\n".join([*private, *scoped, *graph])

    return await get_session_memory(
        redis=await _redis_client(),
        thread_id=thread_id,
        build=_build_memory_block,
        # Without this a thread cached while solo would keep serving the
        # owner's private block to the room for the rest of the TTL, undoing
        # the read rule above.
        clearance=clearance.fingerprint,
    )


__all__ = ["memory_preread"]
