"""The entity card of company retrieval (WS-KG KG-7).

Spec: ``project-docs/specs/knowledge_graph.md`` §3.1, §6, §7.1 and the KG-7
row of §9, and ``knowledge_curation.md`` §6.4. The route is
``GET /search/entity`` in ``company.py``. The tool is ``get_entity`` in
``acb_skills/company_search_tools.py``. No model runs.

1. **The gate.** The route answers 404 unless ``GRAPH_SEARCH_ORGS`` names the
   member's org. ``kb_entities`` is a Knowledge table, so :func:`card` also
   answers 404 when the Knowledge router would: ``KNOWLEDGE_ENABLED``,
   ``KNOWLEDGE_ORGS`` and ``feature:knowledge``.
2. **The resolve.** ``kb_entities.resolver.find_entity`` reads the entities
   that the words name, each under the owner rule of its type (§6.4).
3. **The entity's own record.** An entity whose owner record has a reader
   by id (a person, a CRM company) opens that record through its source, as
   the member (``Source.open``). A record that does not open drops the
   entity, so it gives the same answer as an unknown name. An entity whose
   owner has no reader by id (a project, a workflow, the org, a
   Knowledge-owned entity) keeps the owner rule of step 2 as its check. The
   page of the entity opens through the Knowledge source, and a page that
   does not open is left out with no trace.
4. **The neighbours.** The node of the entity (§3.1) and its page go to the
   graph step of KG-6 (``company._graph_step``), with more items for each
   node and the same budget of §7.1. So each neighbour opens through its
   own app, and a hidden one leaves no trace.

Two matches or more give a list of at most :data:`CANDIDATES_MAX`, each
checked as in step 3. This module holds no SQL and opens no session.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from acb_auth import UserContext
from fastapi import HTTPException

from gateway.retrieval import company
from gateway.retrieval.sources import Hit, holds, source_of_kind

logger = logging.getLogger(__name__)

#: The most entities in the list of an answer with two matches or more.
CANDIDATES_MAX = 5

#: The owner table of each entity whose record has a reader by id, and the
#: node kind of that record (§3.1).
OPENED_KINDS: dict[str, str] = {"people": "person", "crm_companies": "company"}

#: The link of an owner record with no reader by id. The Knowledge
#: directories link the same records (``app/knowledge/lib/entities.ts``).
OWNER_LINKS: dict[str, str] = {
    "pm_projects": "/projects?project={}",
    "workflows": "/studio/workflows/{}",
}

#: The citation prefix of an owner record (``resolver.CITATIONS``).
CITE_PREFIX: dict[str, str] = {
    "people": "person", "crm_companies": "company",
    "pm_projects": "workstream", "workflows": "workflow",
}


def knowledge_open(user: UserContext) -> bool:
    """True when the Knowledge router would admit *user* (its kill switch and
    ``feature:knowledge``). The flag module is read at call time."""
    from gateway.routes.knowledge import flags

    return holds(user, "knowledge") and bool(flags.knowledge_org_allowed(user.organization_id))


async def _open(user: UserContext, kind: str, ids: list[str]) -> dict[str, Hit]:
    """The records of *ids* of *kind* that *user* opens, by id. A refusal of
    the app, or a fault, opens nothing (fail closed)."""
    source = source_of_kind(kind)
    if source is None or not ids:
        return {}
    try:
        found = await source.open(user, list(dict.fromkeys(ids)), kind=kind)
    except HTTPException:
        return {}
    except Exception as exc:
        logger.warning("search.entity open failed",
                       extra={"kind": kind, "error": type(exc).__name__})
        return {}
    return {h.id: h for h in found}


async def _openable(
    user: UserContext, rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Hit], dict[str, Hit]]:
    """Step 3: the rows whose own record opens, their records and their pages.

    Answers ``(rows, own, pages)``. *own* maps the ``ref_id`` of a row to its
    opened record, and *pages* maps a ``page_id`` to its opened page.
    """
    wanted: dict[str, list[str]] = {}
    for row in rows:
        kind = OPENED_KINDS.get(str(row.get("ref_table") or ""))
        if kind and row.get("ref_id"):
            wanted.setdefault(kind, []).append(str(row["ref_id"]))
    opened = {kind: await _open(user, kind, ids) for kind, ids in wanted.items()}
    kept: list[dict[str, Any]] = []
    own: dict[str, Hit] = {}
    for row in rows:
        kind = OPENED_KINDS.get(str(row.get("ref_table") or ""))
        if kind:
            hit = opened.get(kind, {}).get(str(row.get("ref_id") or ""))
            if hit is None:
                continue  # the member cannot open the record: no entity
            own[str(row["ref_id"])] = hit
        kept.append(row)
    page_ids = [str(r["page_id"]) for r in kept if r.get("page_id")]
    pages = await _open(user, "page", page_ids)
    return kept, own, pages


def _node(row: dict[str, Any], own: Hit | None) -> Hit | None:
    """The graph node of the entity (§3.1), as a hit for the KG-6 step.

    A Knowledge-owned entity is the node ``entity:<id>``. Every other entity
    uses its owner record as the node, and only an opened record is one.
    """
    from gateway.kb_entities.registry import KNOWLEDGE_TABLE

    if own is not None:
        return own
    if row.get("ref_table") == KNOWLEDGE_TABLE and row.get("id"):
        return Hit(app="knowledge", kind="entity", id=str(row["id"]),
                   title="", hint="", href="")
    return None


def _entity(row: dict[str, Any], own: Hit | None, page: Hit | None) -> dict[str, Any]:
    """The entity as the tool prints it, with a link and a citation."""
    table = str(row.get("ref_table") or "")
    ref = str(row.get("ref_id") or "")
    if own is not None:
        full_id, href = f"{own.kind}:{own.id}", own.href
    else:
        if row.get("id"):
            full_id = f"entity:{row['id']}"
        elif table in CITE_PREFIX and ref:
            full_id = f"{CITE_PREFIX[table]}:{ref}"
        else:
            full_id = ""
        if table in OWNER_LINKS and ref:
            href = OWNER_LINKS[table].format(quote(ref))
        elif page is not None:
            href = page.href
        else:
            href = f"/knowledge/directories/{quote(str(row.get('type') or ''))}"
    return {
        "type": row.get("type"), "name": row.get("name"),
        "aliases": list(row.get("aliases") or []),
        "entity_id": row.get("id"), "full_id": full_id, "href": href,
    }


async def card(user: UserContext, words: str) -> dict[str, Any]:
    """The answer of ``GET /search/entity`` for *words* (steps 1 to 4).

    ``{query, entity, records, neighbours, skipped}`` for one entity.
    ``{query, candidates}`` otherwise: the list for two matches or more,
    and an empty list for none. An unknown name and an entity that the
    member cannot open give the same empty answer.
    """
    from acb_skills.company_search_tools import ENTITY_HEADER, entity_line, neighbour_line

    from gateway.kb_entities import graph, resolver

    if not knowledge_open(user):
        raise HTTPException(status_code=404, detail="Not Found")
    rows, own, pages = await _openable(user, await resolver.find_entity(user, words))
    if len(rows) != 1:
        return {"query": words, "candidates": [
            _entity(r, own.get(str(r.get("ref_id") or "")), pages.get(str(r.get("page_id"))))
            for r in rows[:CANDIDATES_MAX]]}
    row = rows[0]
    mine = own.get(str(row.get("ref_id") or ""))
    page = pages.get(str(row.get("page_id")))
    entity = _entity(row, mine, page)
    records = [page.as_dict()] if page is not None else []
    spent = (len(ENTITY_HEADER) + 1 + len(entity_line(entity)) + 1
             + sum(len(neighbour_line(r)) + 1 for r in records))
    nodes = [h for h in (_node(row, mine), page) if h is not None]
    skipped: list[str] = []
    near: list[dict[str, Any]] | None = []
    if nodes:
        near = await company._graph_step(
            user, nodes, stamp=False, left=company.GRAPH_TIMEOUT_S, skipped=skipped,
            narrow={"per_hit": graph.NEIGHBOUR_EDGES_PER_HIT, "spent": spent})
    return {"query": words, "entity": entity, "records": records,
            "neighbours": near or [], "skipped": skipped}


__all__ = ["CANDIDATES_MAX", "card", "knowledge_open"]
