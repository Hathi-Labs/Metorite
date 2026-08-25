"""Sales Pull agent — WBS 1.5.

Specialised Pull agent for sales-domain questions ("how is Acme doing?",
"which deals are quiet?", "what's stuck in negotiation?"). Reuses the
generic Pull guardrails + LLM tier-2 path from :mod:`pull_agent`, but swaps
the retrieval stage for the sales-curated views in :mod:`sales_views` so
the LLM sees customer-360 aggregates instead of raw entity rows.

For now this is a sibling of `answer()` in pull_agent.py; once Deep Agents
sub-agents (WBS 0.5) land it'll become a registered sub-agent that the
top-level orchestrator delegates to based on the triage label
(``sales_lead`` / ``sales_followup``).
"""
from __future__ import annotations

from acb_audit import AuditEvent, record
from acb_llm import LLMTier, complete
from acb_llm.guardrails import CitationError, repair_citations, require_citations

from orchestrator.retrieval import format_context
from orchestrator.sales_views import sales_context

_SYSTEM_GROUNDED = (
    "You are the AI Company Brain Sales Pull agent. Answer using ONLY the "
    "supplied Context, which is pre-aggregated for sales: customer 360 "
    "summaries (open deal counts, pipeline INR, last activity, owners) and "
    "individual deals (stage, value_inr, days_quiet). Every factual claim "
    "must end with one or more [entity:uuid] citation tokens COPIED EXACTLY "
    "from the Context list — UUIDs are 36 characters with 4 hyphens; do NOT "
    "shorten or regenerate them. If a number is not in the Context, say so."
)

_SYSTEM_UNGROUNDED = (
    "You are the AI Company Brain Sales Pull agent. No matching customer or "
    "deal was found for this query. Reply briefly and say so explicitly so "
    "the user can rephrase or specify a customer name."
)


async def answer(query: str, *, user_email: str | None = None, trace_id: str | None = None) -> str:
    """Run the sales-focused Pull pipeline and return the answer string."""
    # WS-29 acb_graph slice 7: bind the run's tenant so sales_context's
    # customer/deal/person reads (all FORCE-RLS'd) RETURN ROWS instead of 0.
    # Resolved on this (event-loop) frame. flag OFF → the unbound get_session
    # (byte-identical); flag ON + no resolvable org → fail closed to no hits (the
    # ungrounded path handles it), never an unbound read on a FORCE-RLS'd catalog.
    from orchestrator.executor import _graph_session_opener_current
    _open = _graph_session_opener_current()
    if _open is None:
        hits = []
        context_block = ""
    else:
        with _open() as s:
            hits = sales_context(s, query)
            context_block = format_context(hits)

    record(
        AuditEvent(
            actor=f"user:{user_email or 'anonymous'}",
            action="sales_pull_query",
            target="agent:sales_pull",
            payload={"query": query, "hits": len(hits), "trace_id": trace_id},
        )
    )

    if hits:
        system = _SYSTEM_GROUNDED
        user_content = f"Context:\n{context_block}\n\nQuestion: {query}"
    else:
        system = _SYSTEM_UNGROUNDED
        user_content = query

    raw = await complete(
        tier=LLMTier.TIER_2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )

    if not hits:
        return raw

    try:
        repaired = repair_citations(raw, [(h.kind, str(h.id)) for h in hits])
        return require_citations(repaired)
    except CitationError as exc:
        record(
            AuditEvent(
                actor="agent:sales_pull",
                action="guardrail_block",
                target="agent:sales_pull",
                payload={"reason": str(exc), "raw": raw, "trace_id": trace_id},
            )
        )
        return (
            "I drafted a sales answer but it failed the citation guardrail "
            "(no [entity:uuid] tokens). Refusing to surface."
        )


__all__ = ["answer"]
