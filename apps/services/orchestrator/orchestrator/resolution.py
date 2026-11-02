"""Live LLM tiebreaker for the entity resolver (WBS 1.2).

The :mod:`acb_graph.resolver` keeps itself sync + dependency-light, so the
real LLM call lives here in the orchestrator where LiteLLM is already a
dependency. This module exposes :func:`resolve_with_llm` with the same
signature as the stub in `acb_graph.resolver` and is the function the
orchestrator should pass to higher-level resolution flows.
"""
from __future__ import annotations

import re
from typing import Iterable
from uuid import UUID

from acb_audit import AuditEvent, record
from acb_graph.resolver import LLM_TIEBREAK_PROMPT, ResolutionCandidate
from acb_llm import LLMTier, complete

_INTEGER_RX = re.compile(r"(-?\d+)")


def _format_incoming(incoming: dict[str, str | None]) -> str:
    """Render the incoming record for the prompt template."""
    rows = [f"  {k}: {v!r}" for k, v in incoming.items() if v]
    return "\n".join(rows) or "  (no fields)"


def _format_candidates(
    candidates: list[ResolutionCandidate],
    candidate_summaries: dict[UUID, str],
) -> str:
    out: list[str] = []
    for i, c in enumerate(candidates, start=1):
        summary = candidate_summaries.get(c.entity_id, "(no summary)")
        out.append(f"  {i}. id={c.entity_id} score={c.score:.2f} reason={c.reason}\n     {summary}")
    return "\n".join(out) or "  (no candidates)"


def _parse_choice(raw: str, n_candidates: int) -> int | None:
    """Extract the integer in 0..N from a (possibly chatty) LLM response."""
    if raw is None:
        return None
    text = raw.strip()
    # Strict first: bare integer on its own (incl. negative).
    try:
        v = int(text)
        return v if 0 <= v <= n_candidates else None
    except ValueError:
        pass
    # Loose: first integer in the body.
    m = _INTEGER_RX.search(text)
    if not m:
        return None
    v = int(m.group(1))
    return v if 0 <= v <= n_candidates else None


async def resolve_with_llm(
    *,
    incoming: dict[str, str | None],
    candidates: Iterable[ResolutionCandidate],
    candidate_summaries: dict[UUID, str],
) -> UUID | None:
    """Ask the tier-1 LLM whether INCOMING matches any CANDIDATE.

    Returns the chosen ``entity_id`` (merge) or ``None`` (keep separate).
    Always best-effort: any exception is swallowed after auditing and we
    return ``None`` so the caller falls back to "keep separate".
    """
    cands = list(candidates)
    if not cands:
        return None

    prompt = LLM_TIEBREAK_PROMPT.format(
        incoming=_format_incoming(incoming),
        candidates=_format_candidates(cands, candidate_summaries),
    )

    try:
        raw = await complete(
            tier=LLMTier.TIER_1,
            # One integer: a pick among the candidates (task 1128).
            job="structured",
            messages=[
                {"role": "system", "content": "Reply with a single integer. No prose."},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - audit + degrade gracefully
        record(
            AuditEvent(
                actor="resolver:llm",
                action="tiebreak_error",
                target="entity_resolution",
                payload={"reason": f"{type(exc).__name__}: {exc}"},
            )
        )
        return None

    choice = _parse_choice(raw, len(cands))
    record(
        AuditEvent(
            actor="resolver:llm",
            action="tiebreak",
            target="entity_resolution",
            payload={
                "n_candidates": len(cands),
                "raw": raw,
                "choice": choice,
            },
        )
    )
    if not choice:  # 0 or None => keep separate
        return None
    return cands[choice - 1].entity_id


__all__ = ["resolve_with_llm", "_parse_choice", "_format_incoming", "_format_candidates"]
