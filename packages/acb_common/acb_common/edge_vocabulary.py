"""The one vocabulary of the edges between company records (CI-R1a, task 4170).

Spec: ``project-docs/specs/knowledge_graph.md`` §3.1, §4.1 and §9.1, and
``find_engine.md`` §3.5. Fence: ``tests/unit/test_edge_vocabulary.py``.

Two lists lived apart before this slice. The graph held its **relations**
(what a stored ``kb_links`` row says). Find held its **edge codes** (how a
related hit hangs from another hit). Now both live here, and each end of each
relation has one edge code:

* :data:`RELATIONS` is the closed list of the stored relations;
* :data:`CODES` is the closed list of the edge codes of a related hit, with
  the words the chat tool prints for each;
* :func:`record_code` gives the code of a neighbour of one kind. So a stored
  edge answers in the same vocabulary as a live join.

Only constants and pure functions live here: no SQL, no session, no import of
an app. The gateway and ``acb_skills`` both import it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The node kinds of kg §3.1. The CHECK ``kb_links_source_kind_check`` of the
#: KG-4 migration mirrors this tuple (fence: test_kg_links_writer.py).
NODE_KINDS: tuple[str, ...] = (
    "task", "email", "person", "lead", "deal", "contact", "company",
    "artifact", "whatsapp", "page", "entity", "meeting",
)

#: The provenance values of kg §4.2, and the confidence of an EXTRACTED edge.
PROVENANCES: tuple[str, ...] = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
EXTRACTED_CONFIDENCE = 1.0


@dataclass(frozen=True)
class Relation:
    """One stored relation of kg §4.1.

    ``evidence`` names the end that holds the link (§4.3). ``source``: the
    source record holds it. ``app``: both ends and the record that holds the
    link sit under one access rule of one app, named by ``app``.

    ``ambiguous`` is True for a relation that an exact identifier gives
    (KG-5). One candidate gives ``provenance`` (EXTRACTED). Two or more give
    one AMBIGUOUS row for each candidate (§4.2).
    """

    name: str
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    provenance: str
    evidence: str
    app: str | None = None
    ambiguous: bool = False


#: The relations that a writer of today writes (kg §4.1). ``links_to`` is the
#: page row of ``sync_page_links``. The KG-4 sweep writes the next seven, and
#: the KG-5 forms add ``participant``, ``domain_company`` and the address half
#: of ``attendee``.
RELATIONS: dict[str, Relation] = {r.name: r for r in (
    Relation("links_to", ("page",), NODE_KINDS, "EXTRACTED", "source"),
    Relation("deal_contact", ("deal",), ("contact",), "EXTRACTED", "app", "crm"),
    Relation("deal_company", ("deal",), ("company",), "EXTRACTED", "source"),
    Relation("contact_company", ("contact",), ("company",), "EXTRACTED", "source"),
    Relation("activity_on", ("deal", "contact"), ("contact", "company"),
             "EXTRACTED", "app", "crm"),
    Relation("origin", ("task",), ("email", "whatsapp"), "EXTRACTED", "source"),
    Relation("task_link", ("task",), ("task",), "EXTRACTED", "source"),
    Relation("attendee", ("meeting",), ("person",), "EXTRACTED", "source",
             ambiguous=True),
    Relation("participant", ("email", "whatsapp"), ("person", "contact"),
             "EXTRACTED", "source", ambiguous=True),
    Relation("domain_company", ("email",), ("company",), "EXTRACTED", "source"),
)}

#: The node kinds of each app whose one access rule covers both ends.
APP_KINDS: dict[str, frozenset[str]] = {
    "crm": frozenset({"lead", "deal", "contact", "company"}),
}


@dataclass(frozen=True)
class EdgeCode:
    """One edge code of a related hit (find §3.5). It holds no text of a record.

    ``kind`` is the kind of record the edge ends at, and ``words`` is what the
    chat tool prints. A mail edge starts at a ``key`` (an address or a web
    domain), so its record edge stands in ``via``. ``live`` is True for a code
    that a live join of rung 4 gives. A stored edge of ``kb_links`` gives the
    other codes (CI-R1b).
    """

    code: str
    kind: str
    words: str
    key: str | None = None
    live: bool = True


#: Every edge code. The live codes come first, in the order of FIND-3.
CODES: tuple[EdgeCode, ...] = (
    EdgeCode("contact_of", "contact", "a contact"),
    EdgeCode("lead_of", "lead", "the lead"),
    EdgeCode("deal_of", "deal", "a deal"),
    EdgeCode("company_of", "company", "the company"),
    EdgeCode("mail_from", "email", "mail from", key="address"),
    EdgeCode("mail_to", "email", "mail to", key="address"),
    EdgeCode("mail_domain", "email", "mail from the domain of", key="domain"),
    EdgeCode("task_of", "task", "a task", live=False),
    EdgeCode("email_of", "email", "an email", live=False),
    EdgeCode("person_of", "person", "a person", live=False),
    EdgeCode("whatsapp_of", "whatsapp", "a WhatsApp message", live=False),
    EdgeCode("artifact_of", "artifact", "an artifact", live=False),
    EdgeCode("page_of", "page", "a page", live=False),
    EdgeCode("entity_of", "entity", "an entity", live=False),
    EdgeCode("meeting_of", "meeting", "a meeting", live=False),
)

#: The closed list of the edge codes.
EDGES: tuple[str, ...] = tuple(c.code for c in CODES)
#: The live edges from a record to a record, each with the kind it ends at.
RECORD_EDGES: dict[str, str] = {
    c.code: c.kind for c in CODES if c.live and c.key is None}
#: The edges that end in a mail, each with the kind of the key it starts at.
MAIL_EDGES: dict[str, str] = {c.code: c.key for c in CODES if c.key is not None}
#: The words of each edge from a record to a record, live and stored.
RECORD_WORDS: dict[str, str] = {c.code: c.words for c in CODES if c.key is None}
#: The words of each mail edge.
MAIL_WORDS: dict[str, str] = {c.code: c.words for c in CODES if c.key is not None}

_BY_KIND: dict[str, str] = {c.kind: c.code for c in CODES if c.key is None}


def record_code(kind: str) -> str | None:
    """The edge code of a neighbour record of *kind*, or ``None`` for a kind
    that is not a node. Pure."""
    return _BY_KIND.get(kind)


__all__ = [
    "APP_KINDS",
    "CODES",
    "EDGES",
    "EXTRACTED_CONFIDENCE",
    "MAIL_EDGES",
    "MAIL_WORDS",
    "NODE_KINDS",
    "PROVENANCES",
    "RECORD_EDGES",
    "RECORD_WORDS",
    "RELATIONS",
    "EdgeCode",
    "Relation",
    "record_code",
]
