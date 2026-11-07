"""The graph of pointers: its relations, its sweep, its read and its removal.

Spec: ``project-docs/specs/knowledge_graph.md`` §3.2 (the table), §4.1 (the
relations), §4.2 (provenance), §4.3 (the evidence rule), §4.4 (removal) and
the KG-4 row of §9. Migration ``kb_links_graph_edges`` (found by content,
R1) widens ``kb_links``.

**Two forms of one table.** A page row has a ``page_id``. Its one writer is
``routes/knowledge/backlinks.sync_page_links``, and the graph reads it as
``links_to``. A graph row has ``page_id`` NULL, a source, a relation, a
provenance and a confidence. Its one writer is :func:`_write_edges` here.
The page reads key on ``page_id``, so a graph row never reaches them.

**The relations are a closed code list.** :data:`RELATIONS` holds each
relation that a writer of today writes. The database holds no CHECK on
``relation``, so a later slice adds its relation here and needs no
migration. ``tests/unit/test_kg_links_writer.py`` pins the list to the
table of §4.1.

**The sweep (KG-4).** :func:`sweep_org` writes the structural edges of one
org from stored ids, with no model. Each statement is
``INSERT ... SELECT ... ON CONFLICT DO NOTHING`` on the partial unique index
``kb_links_graph_key``. So a second sweep writes 0 rows, and a removed row
keeps its key, so no sweep writes that edge again (§4.4). The sweep reads
ids and writes ids. It never builds a name or a snippet (§6).

**When it runs.** The job kind :data:`SWEEP_KIND` runs on the shared job
queue. :func:`seed` is a ``worker.seed_hooks`` hook. It enqueues one job for
each org and UTC night, from :data:`SWEEP_HOUR_UTC`, only for an org that
``GRAPH_SWEEP_ORGS`` names. The worker needs ``JOB_QUEUE`` on too. The
sweep starts no loop of its own and adds no unbound database read.

**The read and the removal.** :func:`read_edges` answers the edges of one
record, in both directions, as ids only. The caller opens each end through
its app as the member (§6, KG-6). :func:`remove_edge` sets ``removed_at`` on
a graph row for a holder of ``knowledge:manage``.

**The neighbour read of search (KG-6, §5).** :func:`neighbour_edges` reads
the edges of the first hits of ``GET /search/company`` in one statement, in
its own tenant session, so ``gateway/retrieval/`` stays free of SQL. It drops
the edges that §4.2 and §4.4 keep out of search, and it answers ids only.
:func:`search_allowed` asks the one reader of ``GRAPH_SEARCH_ORGS``.

**The tenant comes from the session (R5, R11).** The sweep runs in the
tenant session of the job, and each statement also names the org. A read
and a removal take the org from ``user.organization_id`` beside row level
security, never from request input.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext
from acb_common import get_logger, get_settings
from sqlalchemy import text

from gateway.db import current_tenant, tenant_session
from gateway.jobs import queue, worker
from gateway.routes.knowledge import core

_log = get_logger("gateway.kb_entities.graph")

# ── The vocabulary ──────────────────────────────────────────────────────────

#: The node kinds of §3.1. The CHECK ``kb_links_source_kind_check`` of the
#: migration mirrors this tuple (fence: test_kg_links_writer.py).
NODE_KINDS: tuple[str, ...] = (
    "task", "email", "person", "lead", "deal", "contact", "company",
    "artifact", "whatsapp", "page", "entity", "meeting",
)

#: The provenance values of §4.2, and the confidence of an EXTRACTED edge.
PROVENANCES: tuple[str, ...] = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
EXTRACTED_CONFIDENCE = 1.0

#: The ``created_by`` of a row that the sweep writes.
SYSTEM = "system"


@dataclass(frozen=True)
class Relation:
    """One relation of §4.1.

    ``evidence`` names the end that holds the link (§4.3). ``source``: the
    source record holds it. ``app``: both ends and the record that holds the
    link sit under one access rule of one app, named by ``app``.
    """

    name: str
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    provenance: str
    evidence: str
    app: str | None = None


#: The relations that a writer of today writes (§4.1). ``links_to`` is the
#: page row of ``sync_page_links``. The other seven are the KG-4 sweep.
RELATIONS: dict[str, Relation] = {r.name: r for r in (
    Relation("links_to", ("page",), NODE_KINDS, "EXTRACTED", "source"),
    Relation("deal_contact", ("deal",), ("contact",), "EXTRACTED", "app", "crm"),
    Relation("deal_company", ("deal",), ("company",), "EXTRACTED", "source"),
    Relation("contact_company", ("contact",), ("company",), "EXTRACTED", "source"),
    Relation("activity_on", ("deal", "contact"), ("contact", "company"),
             "EXTRACTED", "app", "crm"),
    Relation("origin", ("task",), ("email", "whatsapp"), "EXTRACTED", "source"),
    Relation("task_link", ("task",), ("task",), "EXTRACTED", "source"),
    Relation("attendee", ("meeting",), ("person",), "EXTRACTED", "source"),
)}

#: The node kinds of each app whose one access rule covers both ends.
APP_KINDS: dict[str, frozenset[str]] = {
    "crm": frozenset({"lead", "deal", "contact", "company"}),
}

# ── The sweep: one form for each edge it writes ─────────────────────────────

#: A UUID in its text form. The origin of a task holds the email id as text.
_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


@dataclass(frozen=True)
class EdgeForm:
    """One statement of the sweep. ``select`` answers the columns ``s`` and
    ``t``, the ids of the two ends, and names ``:org`` in its own WHERE."""

    relation: str
    source_kind: str
    target_kind: str
    select: str


#: Each structural edge of KG-4, from a stored id (§4.1). The source end of
#: each form holds the id, or the CRM app holds both ends (§4.3).
SWEEP_FORMS: tuple[EdgeForm, ...] = (
    EdgeForm("deal_contact", "deal", "contact",
             "SELECT dc.deal_id::text AS s, dc.contact_id::text AS t "
             "FROM crm_deal_contacts dc "
             "WHERE dc.organization_id = CAST(:org AS uuid)"),
    EdgeForm("deal_company", "deal", "company",
             "SELECT d.id::text AS s, d.company_id::text AS t FROM crm_deals d "
             "WHERE d.organization_id = CAST(:org AS uuid) AND d.company_id IS NOT NULL"),
    EdgeForm("contact_company", "contact", "company",
             "SELECT c.id::text AS s, c.company_id::text AS t FROM crm_contacts c "
             "WHERE c.organization_id = CAST(:org AS uuid) AND c.company_id IS NOT NULL"),
    # One activity names up to three records. Each pair gets one edge, in the
    # order deal, contact, company, so a pair never gets two directions.
    EdgeForm("activity_on", "deal", "contact",
             "SELECT a.deal_id::text AS s, a.contact_id::text AS t FROM crm_activities a "
             "WHERE a.organization_id = CAST(:org AS uuid) "
             "AND a.deal_id IS NOT NULL AND a.contact_id IS NOT NULL"),
    EdgeForm("activity_on", "deal", "company",
             "SELECT a.deal_id::text AS s, a.company_id::text AS t FROM crm_activities a "
             "WHERE a.organization_id = CAST(:org AS uuid) "
             "AND a.deal_id IS NOT NULL AND a.company_id IS NOT NULL"),
    EdgeForm("activity_on", "contact", "company",
             "SELECT a.contact_id::text AS s, a.company_id::text AS t FROM crm_activities a "
             "WHERE a.organization_id = CAST(:org AS uuid) "
             "AND a.contact_id IS NOT NULL AND a.company_id IS NOT NULL"),
    # Email capture writes the id of the email row in `origin.email_id`.
    EdgeForm("origin", "task", "email",
             "SELECT t.id::text AS s, t.origin->>'email_id' AS t FROM pm_tasks t "
             "WHERE t.organization_id = CAST(:org AS uuid) AND t.origin IS NOT NULL "
             "AND t.origin->>'kind' = 'email' "
             f"AND t.origin->>'email_id' ~ '{_UUID_RE}'"),
    # WhatsApp capture writes the chat id and the provider's message id, and
    # no row id. So the form finds the one message of that chat by both.
    EdgeForm("origin", "task", "whatsapp",
             "SELECT t.id::text AS s, m.id::text AS t FROM pm_tasks t "
             "JOIN wa_messages m ON m.organization_id = t.organization_id "
             "AND m.chat_id::text = t.origin->>'chat_id' "
             "AND m.wa_message_id = t.origin->>'wa_message_id' "
             "WHERE t.organization_id = CAST(:org AS uuid) AND t.origin IS NOT NULL "
             "AND t.origin->>'kind' = 'whatsapp'"),
    EdgeForm("task_link", "task", "task",
             "SELECT l.source_task_id::text AS s, l.target_task_id::text AS t "
             "FROM pm_task_links l WHERE l.organization_id = CAST(:org AS uuid)"),
    # `meeting.attendee_ids` has no foreign key, so each id must be a row of
    # `people` in the same org before it gives an edge (§4.1).
    EdgeForm("attendee", "meeting", "person",
             "SELECT m.id::text AS s, p.id::text AS t FROM meeting m "
             "CROSS JOIN LATERAL unnest(m.attendee_ids) AS a(pid) "
             "JOIN people p ON p.id = a.pid AND p.organization_id = m.organization_id "
             "WHERE m.organization_id = CAST(:org AS uuid)"),
)

#: The one INSERT of a graph row. ``page_id`` is always NULL. The conflict
#: target is the partial unique index ``kb_links_graph_key``.
_INSERT_EDGES_SQL = (
    "INSERT INTO kb_links (organization_id, page_id, source_kind, source_id, "
    "relation, target_kind, target_id, provenance, confidence, created_by) "
    "SELECT DISTINCT CAST(:org AS uuid), CAST(NULL AS uuid), :sk, e.s, :rel, :tk, e.t, "
    "CAST(:prov AS text), CAST(:conf AS real), :by "
    "FROM ({select}) AS e WHERE e.s IS NOT NULL AND e.t IS NOT NULL "
    "ON CONFLICT (organization_id, source_kind, source_id, relation, target_kind, "
    "target_id) WHERE page_id IS NULL DO NOTHING"
)


def check_form(form: EdgeForm) -> Relation:
    """The relation of *form*. Raises ``ValueError`` for a relation outside
    :data:`RELATIONS`, for ``links_to`` (a page row), and for an end whose
    kind the relation does not take."""
    rel = RELATIONS.get(form.relation)
    if rel is None:
        raise ValueError(f"relation {form.relation!r} is not in RELATIONS")
    if rel.name == "links_to":
        raise ValueError("links_to is a page row, and only sync_page_links writes it")
    if form.source_kind not in rel.sources or form.target_kind not in rel.targets:
        raise ValueError(
            f"{form.relation} does not join {form.source_kind} to {form.target_kind}")
    return rel


async def _write_edges(db: Any, org: str, form: EdgeForm) -> int:
    """Write the edges of one form for *org*. Answers the rows written."""
    rel = check_form(form)
    result = await db.execute(
        text(_INSERT_EDGES_SQL.format(select=form.select)),
        {"org": org, "sk": form.source_kind, "rel": rel.name, "tk": form.target_kind,
         "prov": rel.provenance, "conf": EXTRACTED_CONFIDENCE, "by": SYSTEM},
    )
    return int(result.rowcount or 0)


@dataclass
class SweepResult:
    """What one sweep of one org wrote, by relation. Counts only."""

    written: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.written.values())


async def sweep_org(db: Any, org: str) -> SweepResult:
    """Write each structural edge of *org* that is not in ``kb_links`` yet.

    *db* is a session bound to *org*. Each form runs in a savepoint, so a
    form whose source table is absent or fails loses its own rows only.
    """
    result = SweepResult()
    for form in SWEEP_FORMS:
        check_form(form)  # a bad form is a code defect: it raises, never logs
        try:
            async with db.begin_nested():
                made = await _write_edges(db, org, form)
        except Exception as exc:  # one form must not stop the others
            _log.warning("graph.sweep.form_failed", relation=form.relation,
                         source_kind=form.source_kind, target_kind=form.target_kind,
                         error=type(exc).__name__)
            continue
        result.written[form.relation] = result.written.get(form.relation, 0) + made
    return result


# ── The switch and the job ──────────────────────────────────────────────────

#: The job kind of the sweep, on the shared queue.
SWEEP_KIND = "graph.sweep"
#: The UTC hour from which the seed hook enqueues the sweep of the night.
SWEEP_HOUR_UTC = 20
#: The day that :func:`seed` last enqueued for each org. A restart empties it,
#: and the dedupe key keeps one live job.
_SEEDED_DAY: dict[str, str] = {}


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def sweep_allowed(organization_id: str | None) -> bool:
    """True when ``GRAPH_SWEEP_ORGS`` holds the org, or holds ``*``. Default
    OFF. The one reader of the setting. It reads the setting on each call,
    so a reload takes effect with no restart."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "graph_sweep_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


async def sweep_job(db: Any, payload: dict[str, Any]) -> None:
    """The ``session`` handler of :data:`SWEEP_KIND`. The worker binds the
    org. With the org off the switch, it writes nothing."""
    org = current_tenant()
    if not sweep_allowed(org):
        return
    result = await sweep_org(db, str(org))
    _log.info("graph.sweep.done", total=result.total, **result.written)


async def seed(org: str, now: datetime) -> None:
    """The ``worker.seed_hooks`` hook: one :data:`SWEEP_KIND` job for each
    org and UTC night (Q4). The dedupe key holds the day."""
    if not sweep_allowed(org):
        return
    at = now.astimezone(UTC)
    if at.hour < SWEEP_HOUR_UTC:
        return
    day = at.date().isoformat()
    if _SEEDED_DAY.get(org) == day:
        return
    async with tenant_session(org) as db:
        await queue.enqueue(db, SWEEP_KIND, {}, dedupe_key=f"graph_sweep:{day}", now=now)
    _SEEDED_DAY[org] = day


def register_jobs(*, replace: bool = False) -> None:
    """Register the kind and the seed hook. Safe to call twice."""
    worker.register(SWEEP_KIND, sweep_job, replace=replace, effect="own_rows")
    if seed not in worker.seed_hooks:
        worker.seed_hooks.append(seed)


register_jobs()

# ── The read and the removal (§4.4) ─────────────────────────────────────────


@dataclass(frozen=True)
class Edge:
    """One edge, as ids only. A page row reads as ``links_to`` from its page."""

    id: str
    source_kind: str
    source_id: str
    relation: str
    target_kind: str
    target_id: str
    provenance: str
    confidence: float
    removed: bool


#: The most edges that one read answers.
MAX_EDGES = 200

# A page row reads as `links_to`, from `page:<page_id>`. Its provenance is
# EXTRACTED until the tightening half sets the column (§3.2).
_EDGES_SQL = (
    "SELECT l.id::text AS id, "
    "COALESCE(l.source_kind, 'page') AS source_kind, "
    "COALESCE(l.source_id, l.page_id::text) AS source_id, "
    "COALESCE(l.relation, 'links_to') AS relation, "
    "l.target_kind, l.target_id, "
    "COALESCE(l.provenance, 'EXTRACTED') AS provenance, "
    "COALESCE(l.confidence, 1.0) AS confidence, "
    "(l.removed_at IS NOT NULL) AS removed "
    "FROM kb_links l WHERE l.organization_id = CAST(:org AS uuid) "
    "AND ((l.page_id IS NULL AND ((l.source_kind = :kind AND l.source_id = :rid) "
    "OR (l.target_kind = :kind AND l.target_id = :rid))) "
    "OR (l.page_id IS NOT NULL AND ((:kind = 'page' AND l.page_id::text = :rid) "
    "OR (l.target_kind = :kind AND l.target_id = :rid)))) "
    "AND (:with_removed OR l.removed_at IS NULL) "
    "ORDER BY l.created_at, l.id LIMIT :n"
)


async def read_edges(
    db: Any, user: UserContext, kind: str, record_id: str, *,
    with_removed: bool = False, limit: int = MAX_EDGES,
) -> list[Edge]:
    """The edges of ``<kind>:<record_id>``, in both directions, as ids only.

    A removed edge is left out unless *with_removed* is set (the admin view
    of §4.4). The read checks no visibility of either end. The caller opens
    each end through its app as the member (§6), and an end the member
    cannot open is the same as no edge.
    """
    if kind not in NODE_KINDS:
        raise ValueError(f"kind {kind!r} is not a node kind")
    rows = (await db.execute(text(_EDGES_SQL), {
        "org": core.org_of(user), "kind": kind, "rid": str(record_id),
        "with_removed": bool(with_removed), "n": max(1, min(int(limit), MAX_EDGES)),
    })).fetchall()
    return [Edge(id=r.id, source_kind=r.source_kind, source_id=r.source_id,
                 relation=r.relation, target_kind=r.target_kind, target_id=r.target_id,
                 provenance=r.provenance, confidence=float(r.confidence),
                 removed=bool(r.removed)) for r in rows]


_REMOVE_SQL = (
    "UPDATE kb_links SET removed_at = now(), removed_by = :by "
    "WHERE organization_id = CAST(:org AS uuid) AND id = CAST(:eid AS uuid) "
    "AND page_id IS NULL AND removed_at IS NULL RETURNING id"
)


async def remove_edge(db: Any, user: UserContext, edge_id: str) -> bool:
    """Remove one graph edge (§4.4). True when this call removed it.

    Only a holder of ``knowledge:manage`` may remove an edge, and anyone
    else gets 403. The row stays with ``removed_at`` and ``removed_by``, so
    the sweep never writes the edge again. A page row is never removed here,
    because its page writes it again on each save: a member edits the page.
    """
    if not core.is_manager(user):
        raise core.forbidden("Removing a graph edge needs knowledge:manage.")
    eid = core.parse_id(edge_id)
    if eid is None:
        return False
    row = (await db.execute(text(_REMOVE_SQL), {
        "org": core.org_of(user), "eid": eid, "by": core.viewer_email(user),
    })).fetchone()
    return row is not None


# ── The neighbour read of search (KG-6, §5) ─────────────────────────────────


def search_allowed(organization_id: str | None) -> bool:
    """True when ``GRAPH_SEARCH_ORGS`` holds the org, or holds ``*``. Default
    OFF. It reads the setting on each call, so a reload takes effect with no
    restart. Off, ``GET /search/company`` reads no edge and its answer does
    not change, and ``GET /search/entity`` answers 404 (KG-7).

    The one reader is ``acb_skills.company_search_tools.graph_search_enabled``,
    so the gateway and the orchestrator's tool list read one flag."""
    from acb_skills.company_search_tools import graph_search_enabled

    return graph_search_enabled(organization_id)


#: The least confidence of an INFERRED edge that search uses (§4.2).
INFERRED_FLOOR = 0.5
#: The most edges that the neighbour read answers for one hit. The step keeps
#: 3 of them, after each app drops the ones that the member cannot open.
NEIGHBOUR_EDGES_PER_HIT = 30
#: The most hits that one neighbour read takes.
MAX_NEIGHBOUR_NODES = 10


@dataclass(frozen=True)
class Neighbour:
    """One edge of a hit, from the side of the hit, as ids only.

    ``hit_kind`` and ``hit_id`` name the hit. ``kind`` and ``id`` name the
    other end. ``order`` is the place of the edge in the read: EXTRACTED
    first, then INFERRED by confidence, then the newer edge.
    """

    hit_kind: str
    hit_id: str
    kind: str
    id: str
    relation: str
    provenance: str
    confidence: float
    order: int


# Three arms, one shape. A graph row read from its source, a row of either
# form read from its target, and a page row read from its page. A page row
# reads as `links_to`, EXTRACTED, until the tightening half sets its columns
# (§3.2). The filter of §4.2 and §4.4 runs on the shaped row: no AMBIGUOUS
# edge, no removed edge, and no INFERRED edge below the floor. A provenance
# that is not EXTRACTED or INFERRED gives nothing (fail closed). Only an edge
# to a kind in `:open_kinds` counts, BEFORE the cap of each hit, so many edges
# to a kind that no app opens (a meeting, a URL) cannot push out the others.
_NEIGHBOURS_SQL = (
    "WITH n AS (SELECT k.kind, k.rid FROM unnest(CAST(:kinds AS text[]), "
    "CAST(:rids AS text[])) AS k(kind, rid)), "
    "e AS ("
    "SELECT n.kind AS hit_kind, n.rid AS hit_id, l.target_kind AS kind, "
    "l.target_id AS rid, l.relation, l.provenance, l.confidence, l.created_at, "
    "l.removed_at, l.id "
    "FROM kb_links l JOIN n ON l.source_kind = n.kind AND l.source_id = n.rid "
    "WHERE l.organization_id = CAST(:org AS uuid) AND l.page_id IS NULL "
    "UNION ALL "
    "SELECT n.kind, n.rid, COALESCE(l.source_kind, 'page'), "
    "COALESCE(l.source_id, l.page_id::text), COALESCE(l.relation, 'links_to'), "
    "COALESCE(l.provenance, 'EXTRACTED'), COALESCE(l.confidence, 1.0), "
    "l.created_at, l.removed_at, l.id "
    "FROM kb_links l JOIN n ON l.target_kind = n.kind AND l.target_id = n.rid "
    "WHERE l.organization_id = CAST(:org AS uuid) "
    "UNION ALL "
    "SELECT n.kind, n.rid, l.target_kind, l.target_id, "
    "COALESCE(l.relation, 'links_to'), COALESCE(l.provenance, 'EXTRACTED'), "
    "COALESCE(l.confidence, 1.0), l.created_at, l.removed_at, l.id "
    "FROM kb_links l JOIN n ON n.kind = 'page' AND l.page_id::text = n.rid "
    "WHERE l.organization_id = CAST(:org AS uuid) AND l.page_id IS NOT NULL"
    "), "
    "kept AS ("
    "SELECT e.*, row_number() OVER (PARTITION BY e.hit_kind, e.hit_id ORDER BY "
    "(e.provenance = 'EXTRACTED') DESC, e.confidence DESC, e.created_at DESC, e.id) AS k "
    "FROM e WHERE e.removed_at IS NULL "
    "AND e.kind = ANY(CAST(:open_kinds AS text[])) "
    "AND e.provenance IN ('EXTRACTED', 'INFERRED') "
    "AND (e.provenance = 'EXTRACTED' OR e.confidence >= :floor) "
    "AND NOT (e.kind = e.hit_kind AND e.rid = e.hit_id)"
    ") "
    "SELECT hit_kind, hit_id, kind, rid, relation, provenance, confidence, k "
    "FROM kept WHERE k <= :per ORDER BY hit_kind, hit_id, k"
)


async def neighbour_edges(
    user: UserContext, nodes: list[tuple[str, str]], *, kinds: tuple[str, ...] | list[str],
    per_hit: int = NEIGHBOUR_EDGES_PER_HIT,
) -> list[Neighbour]:
    """The usable edges of each node of *nodes*, in both directions, one hop.

    *nodes* holds ``(kind, id)`` pairs, the first hits of a search. The read
    opens its own tenant session, so ``gateway/retrieval/`` holds no SQL.
    Row level security binds the tenant of the request, and the statement
    also names ``user.organization_id``. The org never comes from input.

    It drops each AMBIGUOUS edge, each removed edge and each INFERRED edge
    below :data:`INFERRED_FLOOR` (§4.2, §4.4). It answers ids only and checks
    no visibility. The caller opens each other end through its app, as the
    member (§6). An end that the member cannot open is the same as no edge.

    *kinds* names the kinds of the other end that the caller can open. The
    caller derives it from the kinds of its sources, so the list cannot
    drift. An edge to any other kind is left out before the cap of each hit.
    """
    pairs = [(str(k), str(i)) for k, i in nodes if k in NODE_KINDS and str(i or "")]
    pairs = list(dict.fromkeys(pairs))[:MAX_NEIGHBOUR_NODES]
    open_kinds = sorted({str(k) for k in kinds or () if k in NODE_KINDS})
    if not pairs or not open_kinds:
        return []
    org = core.org_of(user)
    async with tenant_session() as db:
        rows = (await db.execute(text(_NEIGHBOURS_SQL), {
            "org": org, "kinds": [k for k, _ in pairs], "rids": [i for _, i in pairs],
            "floor": INFERRED_FLOOR, "per": max(1, int(per_hit)), "open_kinds": open_kinds,
        })).fetchall()
    return [Neighbour(hit_kind=r.hit_kind, hit_id=r.hit_id, kind=r.kind, id=str(r.rid),
                      relation=r.relation, provenance=r.provenance,
                      confidence=float(r.confidence), order=int(r.k)) for r in rows]


__all__ = [
    "APP_KINDS",
    "INFERRED_FLOOR",
    "MAX_EDGES",
    "NODE_KINDS",
    "PROVENANCES",
    "RELATIONS",
    "SWEEP_FORMS",
    "SWEEP_KIND",
    "Edge",
    "EdgeForm",
    "Neighbour",
    "Relation",
    "SweepResult",
    "check_form",
    "neighbour_edges",
    "read_edges",
    "remove_edge",
    "search_allowed",
    "seed",
    "sweep_allowed",
    "sweep_job",
    "sweep_org",
]
