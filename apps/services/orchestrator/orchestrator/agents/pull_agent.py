"""Phase-0 Pull agent (WBS 0.7). Cited Q&A over the graph mirror.

Pipeline:
  query -> retrieve(query) -> format_context -> LLM (tier 2) -> guardrails
If retrieval yields no hits we fall back to a general-knowledge answer so the
endpoint stays useful while the graph is still being populated.
"""
from __future__ import annotations

from acb_audit import AuditEvent, record
from acb_llm import LLMTier, complete
from acb_llm.guardrails import CitationError, repair_citations, require_citations

from orchestrator.retrieval import format_context, retrieve

_SYSTEM_GROUNDED = (
    "You are the AI Company Brain Pull agent. Answer using ONLY the supplied "
    "Context. Every factual claim must end with one or more citation tokens of "
    "the form [entity:uuid] COPIED EXACTLY from the Context list — UUIDs are "
    "36 characters including 4 hyphens; do NOT shorten, abbreviate, or "
    "regenerate them. If the Context does not contain the answer, say so "
    "explicitly and cite the most relevant entity you did see."
)

_SYSTEM_UNGROUNDED = (
    "You are the AI Company Brain Pull agent. No private project context "
    "matched this query, so answer from general knowledge and say so. "
    "Keep answers concise."
)


async def answer(query: str, *, user_email: str | None = None, trace_id: str | None = None) -> str:
    """Run the full Phase-0 Pull pipeline and return the answer string."""
    # WS-29 acb_graph slice 7: bind the run's tenant so the FORCE-RLS'd
    # project/task/person/deal reads RETURN ROWS instead of 0. Resolved on this
    # (event-loop) frame. flag OFF → the unbound get_session (byte-identical);
    # flag ON + no resolvable org → fail closed to no hits (the Phase-0 graceful
    # ungrounded path handles it), never an unbound read on a FORCE-RLS'd catalog.
    from orchestrator.executor import _graph_session_opener_current
    _open = _graph_session_opener_current()
    if _open is None:
        hits = []
        context_block = ""
    else:
        with _open() as s:
            hits = retrieve(s, query)
            context_block = format_context(hits)

    record(
        AuditEvent(
            actor=f"user:{user_email or 'anonymous'}",
            action="pull_query",
            target="agent:pull",
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
        # No retrieval => no citations required (Phase-0 graceful mode).
        return raw

    try:
        repaired = repair_citations(raw, [(h.kind, str(h.id)) for h in hits])
        return require_citations(repaired)
    except CitationError as exc:
        record(
            AuditEvent(
                actor="agent:pull",
                action="guardrail_block",
                target="agent:pull",
                payload={"reason": str(exc), "raw": raw, "trace_id": trace_id},
            )
        )
        return (
            "I drafted an answer but it failed the citation guardrail "
            "(no [entity:uuid] tokens). Refusing to surface."
        )