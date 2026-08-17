"""Catalog search — keyword ranking behind the palette AND the copilot
(spec F15: with many agents, tools, and modules, finding a capability must
not require scrolling a tree).

Deliberately keyword-only for now: the catalog is small, entries are
collected live from the same registries the catalog endpoint serves (plus
modules from the DB, best-effort), and a deterministic token/substring score
ranks them — no index table, no embeddings, nothing to sync.

**Semantic search is deferred to the platform, not bolted on here** —
BO-22 in `FOUNDATION_BUILDOUT_CHECKLIST.md` tracks a Metorite-wide
semantic-search service (one embedding + retrieval seam every app consumes:
this catalog, email, notes, tasks, app content). When it lands, this module
swaps its ranking backend and the API shape doesn't change.

The copilot consumes ``search_catalog`` directly to shortlist capabilities
for its prompt — the same ranking a human sees in the palette.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.workflows.core import _log, _tenant_session, router
from sqlalchemy import text

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@dataclass(slots=True)
class CatalogEntry:
    kind: str  # agent | tool | integration | module | node
    key: str
    label: str
    description: str
    category: str = ""


# ── Entry collection (same live sources as catalog.py) ──────────────────────


def collect_catalog_entries() -> list[CatalogEntry]:
    """Every searchable capability, from the registries the runtime uses."""
    from gateway.routes.workflows.catalog import (
        NODE_TYPE_META,
        _agent_entries,
        _integration_entries,
    )
    from gateway.routes.workflows.tools import list_tools

    entries: list[CatalogEntry] = []
    for meta in NODE_TYPE_META:
        entries.append(
            CatalogEntry(
                kind="node",
                key=str(meta["type"]),
                label=str(meta["label"]),
                description=str(meta["description"]),
                category=str(meta["category"]),
            )
        )
    for agent in _agent_entries():
        if not agent.get("name"):
            continue
        tags = ", ".join(str(t) for t in agent.get("tags") or [])
        entries.append(
            CatalogEntry(
                kind="agent",
                key=str(agent["name"]),
                label=str(agent["name"]),
                description=f"{agent.get('description') or ''} {tags}".strip(),
                category="agent",
            )
        )
    for spec in list_tools():
        entries.append(
            CatalogEntry(
                kind="tool",
                key=spec.action,
                label=spec.label,
                description=spec.description,
                category=spec.integration or "platform",
            )
        )
    for integration in _integration_entries():
        entries.append(
            CatalogEntry(
                kind="integration",
                key=str(integration["service"]),
                label=str(integration["service"]),
                description=(
                    "connected integration"
                    if integration.get("available")
                    else "integration (not configured yet)"
                ),
                category="integration",
            )
        )
    return entries


async def _module_entries() -> list[CatalogEntry]:
    """Modules from the library — best-effort (search works without the DB).

    Reached only from member requests (the `/catalog/search` route and the
    copilot's shortlist), so the tenant is bound in context. The best-effort
    `except` stays OUTSIDE the `async with`: a failure mid-read rolls back and
    is swallowed here, exactly as before — including `TenantUnbound` from a
    caller outside a bound request, which degrades to "no module entries".
    """
    try:
        async with _tenant_session() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, name, description, status FROM workflow_modules "
                        "WHERE status != 'disabled'"
                    ),
                )
            ).fetchall()
    except Exception as exc:
        _log.warning("workflows.search_modules_failed", error=str(exc)[:120])
        return []
    return [
        CatalogEntry(
            kind="module",
            key=str(r.id),
            label=r.name,
            description=r.description or "",
            category=r.status,
        )
        for r in rows
    ]


# ── Scoring ─────────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]+")
_SCORE_THRESHOLD = 0.75


def keyword_score(query: str, label: str, description: str) -> float:
    """Deterministic lexical score: label hits outrank description hits."""
    q = query.lower().strip()
    if not q:
        return 0.0
    label_l, desc_l = label.lower(), description.lower()
    score = 0.0
    if q == label_l:
        score += 6.0
    elif q in label_l:
        score += 4.0
    elif q in desc_l:
        score += 1.5
    label_words = set(_WORD_RE.findall(label_l))
    desc_words = set(_WORD_RE.findall(desc_l))
    for token in _WORD_RE.findall(q):
        if token in label_words:
            score += 2.0
        elif any(token in w for w in label_words):
            score += 1.0
        elif token in desc_words:
            score += 0.75
        elif any(token in w for w in desc_words):
            score += 0.25
    return score


async def search_catalog(
    query: str,
    *,
    kinds: set[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Keyword search over the live capability catalog. Never raises."""
    try:
        entries = collect_catalog_entries()
    except Exception as exc:
        _log.warning("workflows.search_collect_failed", error=str(exc)[:120])
        entries = []
    entries += await _module_entries()

    results = []
    for entry in entries:
        if kinds and entry.kind not in kinds:
            continue
        score = keyword_score(query, entry.label, entry.description)
        if score >= _SCORE_THRESHOLD:
            results.append(
                {
                    "kind": entry.kind,
                    "key": entry.key,
                    "label": entry.label,
                    "description": entry.description,
                    "category": entry.category,
                    "score": round(score, 3),
                }
            )
    results.sort(key=lambda r: -r["score"])
    return {"query": query, "results": results[: max(1, min(limit, MAX_LIMIT))]}


# ── Endpoint ────────────────────────────────────────────────────────────────


@router.get("/catalog/search")
async def catalog_search(
    q: str = "",
    kinds: str = "",
    limit: int = DEFAULT_LIMIT,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Search the capability catalog (palette search + copilot shortlist)."""
    kind_set = {k.strip() for k in kinds.split(",") if k.strip()} or None
    if not q.strip():
        return {"query": q, "results": []}
    return await search_catalog(q.strip(), kinds=kind_set, limit=limit)
