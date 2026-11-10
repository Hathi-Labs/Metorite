"""CRM · search of note text by meaning (WS-53 CRM-Q27).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q27 items 12 to 14.

    GET /crm/notes/search?q=&limit=     the notes nearest to q, by meaning

**The org list comes first.** An org outside ``CRM_NOTE_SEARCH_ORGS``
(``flags.note_search_allowed``) gets ``degraded: true`` and no hits, with no
embed call and no SQL. A name in that list is OWNER-GATE, because each embed
request costs vendor money that no credit row pays.

**The vector comes before the session.** For a listed org, the route makes
the vector of ``q`` through the ONE embed door (``kb_index/embed.embed``)
with no session open. It stops at ``kb_index.query.QUERY_EMBED_TIMEOUT_S``
(0.8 s). With no vector, the answer is ``degraded: true`` with no hits.

**The read.** One tenant session reads the nearest notes of the org from
``crm_note_vectors`` by cosine distance, joined to ``crm_activities``. The
org comes from the session (R5), and RLS binds the read. A hit holds the
activity id, its parent, the time of the note and a snippet of at most
:data:`SNIPPET_CHARS` characters. It holds no vector and no distance.

**The guards.** The router of ``core.py`` runs the CRM switch,
``feature:crm`` and ``require_crm_access``, as for the timeline routes. The
CRM is visible to the whole org (D-CRM-3), so the read needs no record
scope (§18.5, CRM-Q12, is not in scope).

**No text in a log.** One log line for each search holds the org, the
count of hits, ``degraded`` and the time, never the query.

Fences: ``tests/unit/test_crm_note_vectors.py`` and
``tests/unit/test_crm_note_vectors_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query
from gateway.kb_index import embed as embed_door
from gateway.kb_index.index import vector_literal
from gateway.kb_index.query import QUERY_EMBED_TIMEOUT_S
from gateway.routes.crm import flags
from gateway.routes.crm.core import _tenant_session, current_tenant, router, wire
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.crm.note_search")

#: The most hits of one answer (§18.11.2 item 12).
MAX_LIMIT = 20

#: The hits of an answer when the caller names no limit.
DEFAULT_LIMIT = 10

#: The most characters of a query. A longer one gives 422.
QUERY_MAX_CHARS = 500

#: The most characters of a snippet (§18.11.2 item 13).
SNIPPET_CHARS = 200

#: The entity slug of each parent column, in the order a hit names one. A
#: note can hang off a deal and a contact, and the deal is the closer one.
_PARENTS: tuple[tuple[str, str], ...] = (
    ("deal_id", "deals"),
    ("lead_id", "leads"),
    ("contact_id", "contacts"),
    ("company_id", "organizations"),
)


class NoteParent(BaseModel):
    """The record a note hangs off: an entity and its record id, or a mirrored
    module and its module record id."""

    entity: str | None = None
    record_id: str | None = None
    module: str | None = None
    module_record_id: str | None = None


class NoteHit(BaseModel):
    activity_id: str
    parent: NoteParent
    at: str | None = None
    snippet: str


class NoteSearchResponse(BaseModel):
    hits: list[NoteHit]
    degraded: bool


#: The nearest notes of the org. RLS filters AFTER the HNSW scan, so an
#: iterative scan keeps reading until the LIMIT is met (as ``kb_index.query``).
_SEARCH_SQL = (
    "SELECT a.id::text AS activity_id, a.deal_id::text AS deal_id, "
    "a.lead_id::text AS lead_id, a.contact_id::text AS contact_id, "
    "a.company_id::text AS company_id, a.module_record_id::text AS module_record_id, "
    "m.module AS module, a.subject, a.body, "
    "COALESCE(a.occurred_at, a.created_at) AS at "
    "FROM crm_note_vectors v "
    "JOIN crm_activities a ON a.id = v.activity_id "
    "AND a.organization_id = v.organization_id "
    "LEFT JOIN crm_module_records m ON m.id = a.module_record_id "
    "AND m.organization_id = a.organization_id "
    "WHERE v.organization_id = CAST(:o AS uuid) AND v.embedding IS NOT NULL "
    "AND a.type = 'note' "
    "ORDER BY v.embedding <=> CAST(:vec AS vector), v.activity_id "
    "LIMIT :n"
)


def snippet(subject: Any, body: Any) -> str:
    """The subject and the body in one line, at most :data:`SNIPPET_CHARS`."""
    parts = [" ".join(str(p or "").split()) for p in (subject, body)]
    line = " — ".join(p for p in parts if p)
    if len(line) <= SNIPPET_CHARS:
        return line
    return line[:SNIPPET_CHARS - 1].rstrip() + "…"


def _parent(row: Any) -> NoteParent:
    for column, entity in _PARENTS:
        value = getattr(row, column, None)
        if value:
            return NoteParent(entity=entity, record_id=str(value))
    if row.module_record_id:
        return NoteParent(module=row.module, module_record_id=str(row.module_record_id))
    return NoteParent()


async def embed_query(org: str, words: str) -> list[float] | None:
    """The vector of ``words``, or ``None``. Call it with no session open.

    ``None`` when the door is down, answers no usable vector, or takes more
    than ``QUERY_EMBED_TIMEOUT_S``."""
    # Inside the function: ``crm_sync`` imports ``routes.crm``, as ``sync.py``
    # records, so a module-level import here would close a cycle.
    from gateway.crm_sync.note_vectors import usable

    try:
        vectors = await asyncio.wait_for(
            embed_door.embed([words], org=org, timeout=QUERY_EMBED_TIMEOUT_S),
            timeout=QUERY_EMBED_TIMEOUT_S,
        )
    except Exception as exc:  # a timeout is an answer: degraded
        _log.warning("crm.note_search.embed_failed", organization_id=org,
                     error=type(exc).__name__)
        return None
    return usable(vectors[0]) if vectors else None


def _line(org: str | None, started: float, *, hits: int, degraded: bool, reason: str) -> None:
    _log.info("crm.note_search", organization_id=org, hits=hits, degraded=degraded,
              reason=reason, elapsed_ms=int((time.monotonic() - started) * 1000))


@router.get("/notes/search", response_model=NoteSearchResponse)
async def search_notes(
    q: str = Query("", max_length=QUERY_MAX_CHARS),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> NoteSearchResponse:
    """The notes of the caller's org nearest to ``q`` by meaning."""
    started = time.monotonic()
    # The org of the session (R5), never a value of the request.
    org = current_tenant()
    # The org list FIRST: an org outside it costs no embed call and no SQL.
    if not org or not flags.note_search_allowed(org):
        _line(org, started, hits=0, degraded=True, reason="off")
        return NoteSearchResponse(hits=[], degraded=True)
    # `q` goes to the embed door only, never into SQL text or a bound value.
    words = " ".join(str(q or "").split())
    if not words:
        raise HTTPException(status_code=422, detail="q is empty.")

    vector = await embed_query(org, words)
    if vector is None:
        _line(org, started, hits=0, degraded=True, reason="no_vector")
        return NoteSearchResponse(hits=[], degraded=True)

    async with _tenant_session() as db:
        await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        rows = (await db.execute(text(_SEARCH_SQL), {
            "o": org, "vec": vector_literal(vector), "n": int(limit),
        })).all()
    hits = [
        NoteHit(activity_id=r.activity_id, parent=_parent(r), at=wire(r.at),
                snippet=snippet(r.subject, r.body))
        for r in rows
    ]
    _line(org, started, hits=len(hits), degraded=False, reason="done")
    return NoteSearchResponse(hits=hits, degraded=False)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "SNIPPET_CHARS",
    "NoteHit",
    "NoteParent",
    "NoteSearchResponse",
    "embed_query",
    "search_notes",
    "snippet",
]
