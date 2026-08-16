"""memory_tools — agent-callable tools for active memory read/write.

Auto-injected into every agent alongside ``web_search`` and ``call_agent``.

These tools give agents ACTIVE control over the Metorite memory systems:

READ tools (query what Metorite knows):
- remember(query) → str
    Search Mem0 episodic memory for relevant past facts about the current user.
    Returns formatted facts the agent can use for continuity.

- recall_timeline(entity_name, query) → str
    Search the Graphiti bi-temporal knowledge graph for time-stamped facts about
    an entity (person, deal, project, company).  Returns timestamped history.

WRITE tools (persist important information):
- save_memory(fact) → str
    Explicitly save a single fact to Mem0 episodic memory for the current user.
    Use when you learn something important about the user that should be remembered.

- save_episode(name, content, source?) → str
    Add an episode to the Graphiti bi-temporal knowledge graph.  Graphiti will
    automatically extract entities, relationships, and timestamps from the content.

Why agents need these:
    Without active tools, memory is passive — the platform enriches the prompt
    before each run but the agent cannot query or write on demand.  These tools
    let agents say "let me check what I know about this customer" or "I should
    remember that this user prefers X."

Usage by agents:
    facts = await remember("Vijay's reporting preferences")
    timeline = await recall_timeline("ABC Corp", "deal stage changes")
    await save_memory("Vijay prefers weekly summaries on Monday mornings")
    await save_episode("Deal closed", "ABC Corp deal worth ₹50L closed today", source="agent-sales")
"""
from __future__ import annotations

import contextvars

from acb_common import get_logger

_log = get_logger("acb_skills.memory_tools")

# ContextVar set by the gateway route before each agent run so the memory
# tools know which user's memory to query / write.  Cleared after the run.
_memory_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_memory_user_id", default=""
)


# ContextVar for the CURRENT AGENT so agent-scoped memory tools know which
# agent's (cross-user) memory to read/write. Set by the gateway per run.
_memory_agent_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_memory_agent_name", default=""
)


# True once somebody on THIS context has deliberately named the acting user —
# a request handler resolving it from the session, or a run binding it from its
# payload. It is what separates "this run legitimately inherits the identity its
# caller resolved" (a sub-agent inside a parent run) from "this run found a
# leftover identity lying around" (a scheduler, a workflow node, the next run on
# a reused task). The second must NOT inherit: see :func:`_bind_memory_user_id`.
_memory_user_named: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_memory_user_named", default=False
)

#: What :func:`_bind_memory_user_id` hands back to :func:`_unbind_memory_user_id`.
MemoryUserBinding = tuple["contextvars.Token[str]", "contextvars.Token[bool]"]


def _set_memory_user_id(user_id: str) -> None:
    """Set the current user ID for memory tool operations.

    Called by the gateway route handler before dispatching an agent run.
    The memory tools (remember, save_memory, save_episode) read this
    context var to determine whose memory to operate on.

    A non-empty *user_id* also marks this context as having deliberately named
    its acting user, so a nested run whose payload names nobody inherits it
    rather than refusing. An empty one names nobody and marks nothing.
    """
    _memory_user_id.set(user_id or "")
    if user_id:
        _memory_user_named.set(True)


def _get_memory_user_id() -> str:
    """Return the current user ID, or empty string if not set."""
    return _memory_user_id.get()


def _bind_memory_user_id(user_id: str) -> MemoryUserBinding:
    """Bind the acting user for exactly one agent run; return a reset token.

    Unlike :func:`_set_memory_user_id` this is **unconditional**, and that is
    the whole point. The previous shape was::

        if _mu:
            _set_memory_user_id(_mu)
            os.environ["ACB_AGENT_USER_EMAIL"] = _mu   # never cleared

    — so a run whose payload named nobody kept whatever the last run left, in a
    ContextVar the caller's task still held and in a process-global env var every
    concurrent run shared. An agent then called the gateway with somebody else's
    address in ``X-User-Email``. Binding the empty string instead means a run
    that cannot name its user has nobody to act as, and the tool clients refuse.

    The one inheritance that IS legitimate is a sub-agent: ``call_agent`` and the
    sub-agent batch path dispatch ``{"message": ..., "mode": "sub_task"}`` with no
    user, from inside a parent run that already bound one on this same context.
    That case is admitted by :data:`_memory_user_named` — which is set by the
    binding and reset with it, so it is true only while a real enclosing scope is
    open, never because a previous run finished and left it behind.

    Pair every call with :func:`_unbind_memory_user_id` in a ``finally``.
    """
    resolved = user_id or ""
    if not resolved and _memory_user_named.get():
        # Inside a scope that named its user: inherit it (sub-agent delegation).
        resolved = _memory_user_id.get()
    return (_memory_user_id.set(resolved), _memory_user_named.set(bool(resolved)))


def _unbind_memory_user_id(binding: MemoryUserBinding | None) -> None:
    """Release a :func:`_bind_memory_user_id` scope. Never raises.

    ``Token.reset`` demands the context it was created in, and the streaming
    executor's teardown does not always run in it — MAF's own telemetry hook hit
    exactly that (see ``executor._disable_agent_telemetry_once``). A failed reset
    must not leave the identity standing for the next run, so the fallback is to
    clear the binding outright, which fails closed rather than open.
    """
    if binding is None:
        return
    user_token, named_token = binding
    try:
        _memory_user_id.reset(user_token)
    except (ValueError, RuntimeError):
        _memory_user_id.set("")
    try:
        _memory_user_named.reset(named_token)
    except (ValueError, RuntimeError):
        _memory_user_named.set(False)


def _set_memory_agent_name(agent_name: str) -> None:
    """Set the current agent name for agent-scoped memory operations.

    Called by the gateway per run alongside _set_memory_user_id. Agent memory
    (recall_agent / save_agent_memory) is shared across ALL users of this agent.
    """
    _memory_agent_name.set(agent_name or "")


def _get_memory_agent_name() -> str:
    """Return the current agent name, or empty string if not set."""
    return _memory_agent_name.get()


# ContextVar holding a predicate `(permission) -> bool` for the acting member,
# set by the executor per run alongside the two above. A plain callable rather
# than a UserContext so this low-level tool package keeps no dependency on
# acb_auth. Default allows everything: a run with no member attached (cron,
# reconciler, webhook) is the platform acting on its own behalf and keeps the
# behaviour it had before org access control existed.
_memory_permission: contextvars.ContextVar = contextvars.ContextVar(
    "_memory_permission", default=None
)


def _set_memory_permission(predicate) -> None:
    """Install the acting member's permission predicate for this run."""
    _memory_permission.set(predicate)


def _may(permission: str) -> bool:
    """True when the acting member holds *permission* (or none is attached)."""
    predicate = _memory_permission.get()
    if predicate is None:
        return True
    try:
        return bool(predicate(permission))
    except Exception:  # noqa: BLE001
        # A broken predicate must not silently open the gate.
        _log.warning("memory_tools.permission_check_failed", permission=permission)
        return False


async def remember(query: str) -> str:
    """Search episodic memory for facts about the current user related to *query*.

    Uses Mem0 (Postgres + pgvector) to find semantically relevant facts from
    all past conversations with this user.  Call this BEFORE making claims about
    the user's preferences, history, or context.

    Args:
        query: What to search for in natural language.
               E.g. "reporting preferences", "pricing discussions", "contact history"

    Returns:
        Formatted facts like "- Vijay prefers weekly Monday reports", or
        "(no relevant memories found)" if nothing matches or Mem0 is disabled.

    Examples:
        facts = await remember("Vijay's preferred communication channel")
        facts = await remember("ABC Corp deal status and contacts")
    """
    try:
        from acb_memory import get_memory_context  # noqa: PLC0415

        user_id = _get_memory_user_id()
        if not user_id:
            _log.debug("memory_tools.remember_no_user_id")
            return "(memory system unavailable — no user context)"

        result = await get_memory_context(user_id, query)
        return result if result else "(no relevant memories found)"
    except ImportError:
        return "(memory system not installed — ask operator to enable Mem0)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.remember_failed", error=str(exc)[:200])
        return f"(memory search failed: {exc})"


async def recall_timeline(entity_name: str, query: str) -> str:
    """Search the bi-temporal knowledge graph for time-stamped facts about an entity.

    Uses Graphiti (Neo4j) to find WHEN things happened — entity stage changes,
    action history, relationship timelines.  Much richer than episodic memory
    for answering "when did X happen?" or "what's the history of Y?"

    Args:
        entity_name: Name of the entity (person, company, deal, project).
        query:       What aspect of the timeline to focus on.
                     E.g. "deal stage changes", "meeting history", "follow-ups"

    Returns:
        Timestamped facts like:
        "2026-05-12: Deal Fracktal-ABC moved to Awaiting PO [source: agent-sales]"
        "2026-05-28: Follow-up sent by Vijay after 16 days stale"
        Returns "(no timeline facts found)" if Graphiti is disabled or nothing matches.

    Examples:
        history = await recall_timeline("ABC Corp", "all deal stage changes")
        history = await recall_timeline("Rahul Kumar", "meetings and follow-ups")
    """
    try:
        from acb_memory import search_entity_timeline  # noqa: PLC0415

        result = await search_entity_timeline(entity_name, query)
        return result if result else "(no timeline facts found)"
    except ImportError:
        return "(knowledge graph not installed — ask operator to enable Graphiti)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.recall_timeline_failed", error=str(exc)[:200])
        return f"(timeline search failed: {exc})"


async def save_memory(fact: str) -> str:
    """Explicitly save a single fact to episodic memory for the current user.

    Uses Mem0 to persist a fact that will be retrieved in future conversations.
    Call this when you learn something important about the user that should
    carry forward to future interactions.

    Mem0 handles deduplication, updates (facts evolve over time), and
    semantic retrieval automatically.

    Args:
        fact: A single, self-contained fact about the user.
              E.g. "Prefers weekly Monday morning reports in bullet-point format"
              E.g. "Primary contact at ABC Corp is Rahul Kumar (rahul@abc.com)"

    Returns:
        "Memory saved." on success, or an error description on failure.

    Examples:
        await save_memory("Vijay wants all financial figures in INR lakhs, not crores")
        await save_memory("ABC Corp prefers communication via WhatsApp, not email")
    """
    try:
        from acb_memory import add_memories_background  # noqa: PLC0415

        user_id = _get_memory_user_id()
        if not user_id:
            _log.debug("memory_tools.save_memory_no_user_id")
            return "(cannot save memory — no user context)"

        # Mem0 expects message-format input for fact extraction.
        messages = [
            {"role": "user", "content": f"Remember this fact about me: {fact}"},
            {"role": "assistant", "content": f"I'll remember: {fact}"},
        ]
        await add_memories_background(user_id, messages, agent_id="memory_tool")
        _log.info("memory_tools.save_memory_ok",
                  user_id=user_id[:20], fact=fact[:80])
        return "Memory saved."
    except ImportError:
        return "(memory system not installed — ask operator to enable Mem0)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.save_memory_failed", error=str(exc)[:200])
        return f"(failed to save memory: {exc})"


async def save_episode(
    name: str,
    content: str,
    source: str = "agent",
) -> str:
    """Record an episode in the bi-temporal knowledge graph.

    Uses Graphiti to extract entities, relationships, and timestamps from
    the content.  Future ``recall_timeline`` calls on the mentioned entities
    will include this episode.

    Call this when significant events occur that should be queryable by time:
    - A deal changes stage
    - A meeting produces action items
    - A customer's status changes
    - A project milestone is reached

    Args:
        name:    Short label, e.g. "Deal ABC moved to Negotiation"
        content: Full description. Graphiti extracts entities + relationships.
        source:  Origin label, e.g. "agent-sales", "agent-delivery".
                 Defaults to "agent".

    Returns:
        "Episode recorded." on success, or an error description on failure.

    Examples:
        await save_episode(
            "Deal closed",
            "ABC Corp signed the PO worth ₹50L for 10 Fracktal Works printers. Contact: Rahul Kumar.",
            source="agent-sales",
        )
        await save_episode(
            "Meeting follow-up",
            "Meeting with Delhi NCR prospects on June 14. Action items: send quotes to 3 companies.",
            source="agent-meeting-notes",
        )
    """
    try:
        from acb_memory import add_episode  # noqa: PLC0415

        user_id = _get_memory_user_id() or "default"

        await add_episode(
            name=name,
            content=content,
            source_description=source,
            group_id=user_id,
        )
        _log.info(
            "memory_tools.save_episode_ok",
            name=name[:60],
            source=source,
        )
        return "Episode recorded."
    except ImportError:
        return "(knowledge graph not installed — ask operator to enable Graphiti)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.save_episode_failed", error=str(exc)[:200])
        return f"(failed to record episode: {exc})"


# ---------------------------------------------------------------------------
# Agent memory (shared across ALL users of this agent) + Org memory (global)
# ---------------------------------------------------------------------------


async def recall_agent(query: str) -> str:
    """Search this AGENT'S shared memory — facts learned across ALL users.

    Unlike ``remember`` (this user's private memory), agent memory is shared by
    every person who talks to this agent. Use it for durable knowledge the agent
    itself should carry: how to do its job, standing facts about the domain it
    owns, decisions and outcomes, useful conventions — things true regardless of
    who is asking.

    Args:
        query: What to look up, in natural language.

    Returns:
        Formatted facts, or "(no agent memories found)".

    Examples:
        ctx = await recall_agent("our standard quote-approval workflow")
        ctx = await recall_agent("which suppliers we've had quality issues with")
    """
    try:
        from acb_memory import get_scoped_context, scope_key  # noqa: PLC0415

        agent = _get_memory_agent_name()
        if not agent:
            return "(agent memory unavailable — no agent context)"
        result = await get_scoped_context(
            scope_key(agent=agent), query, header="Agent memory"
        )
        return result if result else "(no agent memories found)"
    except ImportError:
        return "(memory system not installed — ask operator to enable Mem0)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.recall_agent_failed", error=str(exc)[:200])
        return f"(agent memory search failed: {exc})"


async def save_agent_memory(fact: str) -> str:
    """Save a fact into this AGENT'S shared memory (visible to all its users).

    Use for knowledge the agent should retain regardless of who it's talking to —
    domain facts, learned procedures, decisions, outcomes. Do NOT put a single
    user's private preference here (use ``save_memory`` for that).

    Args:
        fact: A single, self-contained fact.

    Returns:
        "Agent memory saved." or an error description.

    Examples:
        await save_agent_memory("PO approvals over 5L require the founder's sign-off")
        await save_agent_memory("Vendor Acme has a 3-week lead time on aluminium")
    """
    try:
        from acb_memory import add_scoped_memories, scope_key  # noqa: PLC0415

        agent = _get_memory_agent_name()
        if not agent:
            return "(cannot save — no agent context)"
        messages = [
            {"role": "user", "content": f"Remember this for your work: {fact}"},
            {"role": "assistant", "content": f"Saved to agent memory: {fact}"},
        ]
        await add_scoped_memories(
            scope_key(agent=agent), messages, agent_id=agent
        )
        _log.info("memory_tools.save_agent_memory_ok", agent=agent[:30], fact=fact[:80])
        return "Agent memory saved."
    except ImportError:
        return "(memory system not installed — ask operator to enable Mem0)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.save_agent_memory_failed", error=str(exc)[:200])
        return f"(failed to save agent memory: {exc})"


async def recall_org(query: str) -> str:
    """Search ORGANISATION-WIDE memory — facts shared across every agent + user.

    Org memory is the company's shared brain: standing facts about the
    organisation, people, policies, and priorities that every agent should know.
    Read it when a question touches company-level context, not just this agent's
    or this user's.

    Args:
        query: What to look up, in natural language.

    Returns:
        Formatted facts, or "(no organisation memories found)".

    Examples:
        ctx = await recall_org("our fiscal year and reporting cadence")
        ctx = await recall_org("company-wide priorities this quarter")
    """
    try:
        from acb_memory import get_scoped_context, scope_key  # noqa: PLC0415

        result = await get_scoped_context(
            scope_key(org=True), query, header="Organisation memory"
        )
        return result if result else "(no organisation memories found)"
    except ImportError:
        return "(memory system not installed — ask operator to enable Mem0)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.recall_org_failed", error=str(exc)[:200])
        return f"(organisation memory search failed: {exc})"


async def save_org_memory(fact: str) -> str:
    """Save a fact into ORGANISATION-WIDE memory (shared by every agent + user).

    Use ONLY for genuinely company-level knowledge every agent should have —
    org structure, policies, standing priorities, key relationships. This is the
    broadest scope; prefer ``save_agent_memory`` or ``save_memory`` when a fact
    belongs to one agent or one user. Confirm with the user before writing
    org-wide facts that others will rely on.

    Args:
        fact: A single, self-contained organisation-level fact.

    Returns:
        "Organisation memory saved." or an error description.

    Examples:
        await save_org_memory("Fracktal's fiscal year runs April-March")
        await save_org_memory("All customer refunds above 1L need finance approval")
    """
    if not _may("memory:write_org"):
        # Org memory is read by every agent for every user, so an unauthorised
        # write is not a private mistake — it becomes shared context. Refuse
        # with a message the agent can relay rather than failing the run.
        _log.info("memory_tools.save_org_memory_denied")
        return (
            "You do not have permission to write organisation-wide memory "
            "(missing 'memory:write_org'). An organization admin can grant it. "
            "Saving this as a personal memory instead may be what you want."
        )
    try:
        from acb_memory import add_scoped_memories, scope_key  # noqa: PLC0415

        agent = _get_memory_agent_name() or "org"
        messages = [
            {"role": "user", "content": f"Remember this company-wide fact: {fact}"},
            {"role": "assistant", "content": f"Saved to organisation memory: {fact}"},
        ]
        await add_scoped_memories(scope_key(org=True), messages, agent_id=agent)
        _log.info("memory_tools.save_org_memory_ok", fact=fact[:80])
        return "Organisation memory saved."
    except ImportError:
        return "(memory system not installed — ask operator to enable Mem0)"
    except Exception as exc:  # noqa: BLE001
        _log.warning("memory_tools.save_org_memory_failed", error=str(exc)[:200])
        return f"(failed to save organisation memory: {exc})"
