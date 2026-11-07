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

**The identifier forms (KG-5).** :data:`RESOLVE_FORMS` match one exact
identifier, with no model: an email address or a WhatsApp phone to a person
or a contact (``participant``), a sender domain to a CRM company
(``domain_company``), and an address of ``meeting.attendees`` to a person
(``attendee``). One candidate gives EXTRACTED at 1.0. Two or more give one
AMBIGUOUS row for each candidate, at 1/n (§4.2). The candidates are directory
fields only, never a private field of ``people``. A company domain is the
host of its website. A free-mail domain of ``CONSUMER_DOMAINS``, a host of
:data:`SITE_HOSTS` and a host that two companies share map to no company. A
sender host that names one company also becomes an alias of its customer
entity, through ``registry.add_aliases``.

**Identifier rows follow the data.** An identifier row carries ``created_by``
:data:`RESOLVER`. Each sweep sets its provenance and confidence to the match
of today and deletes it when the match is gone (:func:`_reconcile`). A
removed row (§4.4), a structural row and a page row never change. A host
alias that two companies now share is removed, through
``registry.remove_aliases``.

**When it runs.** The job kind :data:`SWEEP_KIND` runs on the shared job
queue. :func:`seed` is a ``worker.seed_hooks`` hook. It enqueues one job for
each org and UTC night, from :data:`SWEEP_HOUR_UTC`, only for an org that
``GRAPH_SWEEP_ORGS`` names. The worker needs ``JOB_QUEUE`` on too. The
sweep starts no loop of its own and adds no unbound database read.

**The read and the removal.** :func:`read_edges` answers the edges of one
record, in both directions, as ids only. The caller opens each end through
its app as the member (§6, KG-6). :func:`remove_edge` sets ``removed_at`` on
a graph row for a holder of ``knowledge:manage``.

**The tenant comes from the session (R5, R11).** The sweep runs in the
tenant session of the job, and each statement also names the org. A read
and a removal take the org from ``user.organization_id`` beside row level
security, never from request input.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext
from acb_common import get_logger, get_settings
from sqlalchemy import text

from gateway.db import current_tenant, tenant_session
from gateway.jobs import queue, worker
from gateway.kb_entities import registry
from gateway.routes.email.automation.identity import recipient_lists_sql
from gateway.routes.email.mailbox_identity import CONSUMER_DOMAINS
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


#: The relations that a writer of today writes (§4.1). ``links_to`` is the
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

# ── The sweep: one form for each edge it writes ─────────────────────────────

#: A UUID in its text form. The origin of a task holds the email id as text.
_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


@dataclass(frozen=True)
class EdgeForm:
    """One statement of the sweep. ``select`` answers the columns ``s`` and
    ``t``, the ids of the two ends, and names ``:org`` in its own WHERE.

    An ``identifier`` form (KG-5) also answers ``n``: the number of
    candidates of the identifier that gave the row. Its rows carry
    ``created_by`` :data:`RESOLVER`, and each sweep sets them to the match of
    today (:func:`_reconcile`). A structural row never changes.

    An ``ambiguous`` form keeps the lowest ``n`` of each pair of ends.
    ``n`` = 1 gives EXTRACTED at 1.0, and more gives AMBIGUOUS at 1/n
    (§4.2). A form that is not ``ambiguous`` writes EXTRACTED only."""

    relation: str
    source_kind: str
    target_kind: str
    select: str
    ambiguous: bool = False
    identifier: bool = False


#: The ids of `meeting.attendee_ids` that are a row of `people` in the org.
#: The column has no foreign key, so each id is checked (§4.1).
_ATTENDEE_IDS = (
    "SELECT m.id::text AS s, p.id::text AS t FROM meeting m "
    "CROSS JOIN LATERAL unnest(m.attendee_ids) AS a(pid) "
    "JOIN people p ON p.id = a.pid AND p.organization_id = m.organization_id "
    "WHERE m.organization_id = CAST(:org AS uuid)"
)

#: Each structural edge of KG-4, from a stored id (§4.1). The source end of
#: each form holds the id, or the CRM app holds both ends (§4.3).
STRUCTURAL_FORMS: tuple[EdgeForm, ...] = (
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
    EdgeForm("attendee", "meeting", "person", _ATTENDEE_IDS),
)

# ── The KG-5 forms: one exact identifier to its records ─────────────────────
#
# An identifier is an email address, a phone or a domain (§4.2). Each form
# joins the record that holds the identifier to the records that hold the
# same one, and to nothing else. So no third record joins the two ends
# (§4.3). Each form counts the candidates of each identifier in the org, and
# two or more give AMBIGUOUS rows, never a guess. A form reads no private
# field of `people`: `phone`, `personal_email`, `birthday` and
# `emergency_contact` are the private tier (`routes/tasks/people.py`
# PRIVATE_FIELDS). `people.email` is a directory field.

#: The ``created_by`` of a row that an identifier form writes. Only these
#: rows change after they are written (:func:`_reconcile`).
RESOLVER = "system:identifier"


#: An address in its match form: trimmed and in lower case, never casefolded
#: (PZ-5, as ``resolver.resolve_person`` and the email app compare it).
def _address_key(expr: str) -> str:
    return f"lower(btrim({expr}))"


#: The domain of an address, after its last ``@`` (``identity._domain_of``).
def _domain_of_sql(expr: str) -> str:
    return f"substring({_address_key(expr)} from '@([^@]+)$')"


#: A phone in its match form: its digits only. A WhatsApp ``wa_id`` is the
#: digits of the international number, so ``+91 98765 43210`` matches it.
def _phone_key(expr: str) -> str:
    return f"regexp_replace(coalesce({expr}, ''), '[^0-9]', '', 'g')"


#: The E.164 bounds of ``routes/email/transport/contacts._normalize_phone``.
#: A shorter run is an extension, and a longer run is no phone.
_PHONE_OK = "BETWEEN 8 AND 15"

#: Hosts that many companies share for a profile or a site, so a website
#: there names no company. A host under one of them is left out too.
SITE_HOSTS: frozenset[str] = frozenset({
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "sites.google.com", "wixsite.com", "business.site",
    "github.io", "wordpress.com",
})


def _host_list(hosts: frozenset[str]) -> str:
    """*hosts* as a SQL list. Each one is a plain host, checked here."""
    for d in hosts:
        if not re.fullmatch(r"[a-z0-9.-]+", d):
            raise ValueError(f"not a plain host: {d!r}")
    return ", ".join(f"'{d}'" for d in sorted(hosts))


#: ``CONSUMER_DOMAINS`` as a SQL list. Imported, never copied (§4.2).
_CONSUMER_SQL = _host_list(CONSUMER_DOMAINS)
_SITE_SQL = _host_list(SITE_HOSTS)

#: The host of a CRM company website: lower case first, then no scheme, no
#: ``www.``, no port and no path.
_WEBSITE_HOST = (
    "regexp_replace(regexp_replace(lower(btrim(co.website)), "
    "'^[a-z][a-z0-9+.-]*://', ''), '^www[.]|[/:?#].*$', '', 'g')"
)

#: The addresses that an email row holds: its sender, and To, Cc and Bcc
#: through the one recipient seam of the email app.
_EMAIL_ADDRESSES = (
    "SELECT m.id::text AS s, " + _address_key("m.from_address->>'email'") + " AS k "
    "FROM email_messages m WHERE m.organization_id = CAST(:org AS uuid) "
    "AND jsonb_typeof(m.from_address) = 'object' "
    "UNION SELECT m.id::text, " + _address_key("r.v->>'email'") + " "
    "FROM email_messages m CROSS JOIN LATERAL jsonb_array_elements("
    + recipient_lists_sql("m") + ") AS r(v) "
    "WHERE m.organization_id = CAST(:org AS uuid) AND jsonb_typeof(r.v) = 'object'"
)

#: The address candidates of the org: people and CRM contacts. A person and
#: a contact with one address are two candidates.
_ADDRESS_CANDIDATES = (
    "SELECT 'person' AS tk, p.id::text AS t, " + _address_key("p.email") + " AS k "
    "FROM people p WHERE p.organization_id = CAST(:org AS uuid) AND p.email LIKE '%_@_%' "
    "UNION SELECT 'contact', c.id::text, " + _address_key("c.email") + " "
    "FROM crm_contacts c WHERE c.organization_id = CAST(:org AS uuid) "
    "AND c.email LIKE '%_@_%'"
)

#: The phone candidates of the org: CRM contacts by phone and by mobile. One
#: contact with both numbers is one candidate. ``people.phone`` is private,
#: so it is never a candidate.
_PHONE_CANDIDATES = (
    "SELECT x.tk, x.t, x.k FROM ("
    "SELECT 'contact' AS tk, c.id::text AS t, " + _phone_key("c.phone") + " AS k "
    "FROM crm_contacts c WHERE c.organization_id = CAST(:org AS uuid) "
    "UNION SELECT 'contact', c.id::text, " + _phone_key("c.mobile") + " "
    "FROM crm_contacts c WHERE c.organization_id = CAST(:org AS uuid)"
    f") AS x WHERE length(x.k) {_PHONE_OK}"
)

#: The sender phone of each WhatsApp message.
_WHATSAPP_PHONES = (
    "SELECT m.id::text AS s, " + _phone_key("m.sender->>'wa_id'") + " AS k "
    "FROM wa_messages m WHERE m.organization_id = CAST(:org AS uuid) "
    "AND jsonb_typeof(m.sender) = 'object'"
)

#: The attendee addresses of each meeting (``meeting.attendees``, migration 99).
_MEETING_ADDRESSES = (
    "SELECT m.id::text AS s, " + _address_key("a.v->>'email'") + " AS k "
    "FROM meeting m CROSS JOIN LATERAL jsonb_array_elements("
    "CASE WHEN jsonb_typeof(m.attendees) = 'array' THEN m.attendees "
    "ELSE '[]'::jsonb END) AS a(v) "
    "WHERE m.organization_id = CAST(:org AS uuid) AND jsonb_typeof(a.v) = 'object'"
)

#: The sender domain of each email, never a free-mail domain (§4.2).
_SENDER_DOMAINS = (
    "SELECT x.s, x.k FROM (SELECT m.id::text AS s, "
    + _domain_of_sql("m.from_address->>'email'") + " AS k "
    "FROM email_messages m WHERE m.organization_id = CAST(:org AS uuid) "
    "AND jsonb_typeof(m.from_address) = 'object') AS x "
    f"WHERE x.k LIKE '%_._%' AND x.k NOT IN ({_CONSUMER_SQL})"
)

#: The website host of each CRM company of the org. Never a free-mail
#: domain, and never a host of :data:`SITE_HOSTS` or under one.
_COMPANY_HOSTS = (
    "SELECT h.t, h.k FROM (SELECT co.id::text AS t, " + _WEBSITE_HOST + " AS k "
    "FROM crm_companies co WHERE co.organization_id = CAST(:org AS uuid) "
    "AND co.website IS NOT NULL) AS h "
    f"WHERE h.k LIKE '%_._%' AND h.k NOT IN ({_CONSUMER_SQL}) "
    f"AND h.k NOT IN ({_SITE_SQL}) AND NOT EXISTS (SELECT 1 FROM "
    f"unnest(ARRAY[{_SITE_SQL}]) AS sh(v) WHERE h.k LIKE '%.' || sh.v)"
)

#: Each host that exactly one company of the org holds. A host that two
#: companies share names no company, so it gives no edge and no alias.
_ONE_COMPANY_HOSTS = (
    f"SELECT min(c.t) AS t, c.k FROM ({_COMPANY_HOSTS}) AS c "
    "GROUP BY c.k HAVING count(DISTINCT c.t) = 1"
)

#: Each host that two companies of the org or more hold.
_SHARED_HOSTS = (
    f"SELECT c.k FROM ({_COMPANY_HOSTS}) AS c GROUP BY c.k HAVING count(DISTINCT c.t) > 1"
)


def _resolve_select(identifiers: str, candidates: str, target_kind: str) -> str:
    """The rows ``s``, ``t`` and ``n`` of one identifier form.

    *identifiers* answers ``s`` (the record that holds the identifier) and
    ``k`` (the identifier). *candidates* answers ``tk``, ``t`` and ``k``.
    ``n`` counts the candidates of ``k`` across every target kind, and the
    form keeps the rows of *target_kind*. Both are code constants.
    """
    return (
        f"WITH ids AS ({identifiers}), cand AS ({candidates}), "
        "counted AS (SELECT c.tk, c.t, c.k, count(*) OVER (PARTITION BY c.k) AS n "
        "FROM cand c WHERE c.k <> '') "
        "SELECT i.s AS s, c.t AS t, c.n AS n FROM ids i "
        f"JOIN counted c ON c.k = i.k WHERE c.tk = '{target_kind}'"
    )


#: The address half of `attendee`, with the id half at ``n`` = 1. So a person
#: that `attendee_ids` names stays EXTRACTED, and its row is never stale.
_ATTENDEE_SELECT = (
    "SELECT r.s AS s, r.t AS t, r.n AS n FROM ("
    + _resolve_select(
        _MEETING_ADDRESSES,
        "SELECT 'person' AS tk, p.id::text AS t, " + _address_key("p.email")
        + " AS k FROM people p WHERE p.organization_id = CAST(:org AS uuid) "
        "AND p.email LIKE '%_@_%'",
        "person")
    + ") AS r UNION ALL SELECT i.s, i.t, 1 FROM (" + _ATTENDEE_IDS + ") AS i"
)

#: Each KG-5 form (§4.1). The source end holds the identifier (§4.3).
RESOLVE_FORMS: tuple[EdgeForm, ...] = (
    EdgeForm("participant", "email", "person",
             _resolve_select(_EMAIL_ADDRESSES, _ADDRESS_CANDIDATES, "person"),
             ambiguous=True, identifier=True),
    EdgeForm("participant", "email", "contact",
             _resolve_select(_EMAIL_ADDRESSES, _ADDRESS_CANDIDATES, "contact"),
             ambiguous=True, identifier=True),
    EdgeForm("participant", "whatsapp", "contact",
             _resolve_select(_WHATSAPP_PHONES, _PHONE_CANDIDATES, "contact"),
             ambiguous=True, identifier=True),
    # A shared host gives no row at all, so this form is never AMBIGUOUS.
    EdgeForm("domain_company", "email", "company",
             f"SELECT d.s AS s, o.t AS t, 1 AS n FROM ({_SENDER_DOMAINS}) AS d "
             f"JOIN ({_ONE_COMPANY_HOSTS}) AS o ON o.k = d.k",
             identifier=True),
    # It runs after the id half of KG-4, so a person that `attendee_ids`
    # names keeps its structural row.
    EdgeForm("attendee", "meeting", "person", _ATTENDEE_SELECT,
             ambiguous=True, identifier=True),
)

#: Every form of the sweep, in order: the stored ids first, then KG-5.
SWEEP_FORMS: tuple[EdgeForm, ...] = STRUCTURAL_FORMS + RESOLVE_FORMS

#: The pairs of one form, with the lowest ``n`` of each pair. ``{count}`` is
#: ``1`` for a form that is not ``ambiguous``, or ``x.n``.
_PAIRS = "SELECT x.s, x.t, min({count}) AS n FROM ({select}) AS x GROUP BY x.s, x.t"
_PROV = "CASE WHEN e.n > 1 THEN 'AMBIGUOUS' ELSE CAST(:prov AS text) END"
_CONF = "CAST(CAST(:conf AS real) / GREATEST(e.n, 1) AS real)"

#: The one INSERT of a graph row. ``page_id`` is always NULL. The conflict
#: target is the partial unique index ``kb_links_graph_key``.
_INSERT_EDGES_SQL = (
    "INSERT INTO kb_links (organization_id, page_id, source_kind, source_id, "
    "relation, target_kind, target_id, provenance, confidence, created_by) "
    "SELECT DISTINCT CAST(:org AS uuid), CAST(NULL AS uuid), :sk, e.s, :rel, :tk, e.t, "
    f"CAST({_PROV} AS text), {_CONF}, :by "
    f"FROM ({_PAIRS}) AS e WHERE e.s IS NOT NULL AND e.t IS NOT NULL "
    "ON CONFLICT (organization_id, source_kind, source_id, relation, target_kind, "
    "target_id) WHERE page_id IS NULL DO NOTHING"
)

#: The rows of one identifier form that the sweep may change: its own rows,
#: never a removed row (§4.4), never a structural or a page row.
_OWN_ROWS = (
    "l.organization_id = CAST(:org AS uuid) AND l.page_id IS NULL "
    "AND l.removed_at IS NULL AND l.created_by = :by AND l.source_kind = :sk "
    "AND l.relation = :rel AND l.target_kind = :tk"
)

#: Set the provenance and the confidence of today on each row that still
#: matches. A row that holds them already is not touched.
_UPDATE_EDGES_SQL = (
    f"UPDATE kb_links l SET provenance = {_PROV}, confidence = {_CONF} "
    f"FROM ({_PAIRS}) AS e WHERE {_OWN_ROWS} "
    "AND l.source_id = e.s AND l.target_id = e.t "
    f"AND (l.provenance IS DISTINCT FROM {_PROV} OR l.confidence IS DISTINCT FROM {_CONF})"
)

#: Delete each row whose match no longer holds.
_DELETE_STALE_SQL = (
    f"DELETE FROM kb_links l WHERE {_OWN_ROWS} "
    "AND (l.source_id, l.target_id) NOT IN (SELECT x.s, x.t FROM ({select}) AS x "
    "WHERE x.s IS NOT NULL AND x.t IS NOT NULL)"
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
    if form.ambiguous and not rel.ambiguous:
        raise ValueError(f"{form.relation} is never AMBIGUOUS, so no form may count candidates")
    if form.ambiguous and not form.identifier:
        raise ValueError("only an identifier form counts candidates")
    return rel


def _params(org: str, form: EdgeForm, rel: Relation) -> dict[str, Any]:
    return {"org": org, "sk": form.source_kind, "rel": rel.name, "tk": form.target_kind,
            "prov": rel.provenance, "conf": EXTRACTED_CONFIDENCE,
            "by": RESOLVER if form.identifier else SYSTEM}


def _sql(template: str, form: EdgeForm) -> str:
    return template.format(select=form.select, count="x.n" if form.ambiguous else "1")


async def _write_edges(db: Any, org: str, form: EdgeForm) -> int:
    """Write the edges of one form for *org*. Answers the rows written."""
    rel = check_form(form)
    result = await db.execute(text(_sql(_INSERT_EDGES_SQL, form)), _params(org, form, rel))
    return int(result.rowcount or 0)


async def _reconcile(db: Any, org: str, form: EdgeForm) -> tuple[int, int]:
    """Bring the rows of one identifier form to the match of today.

    It deletes each own row whose match no longer holds, and it sets the
    provenance and the confidence of each other own row to 1/n of today.
    A removed row and a structural row are never touched. Answers
    ``(updated, deleted)``.
    """
    rel = check_form(form)
    if not form.identifier:
        raise ValueError("only an identifier form changes its rows")
    params = _params(org, form, rel)
    deleted = await db.execute(text(_sql(_DELETE_STALE_SQL, form)), params)
    updated = await db.execute(text(_sql(_UPDATE_EDGES_SQL, form)), params)
    return int(updated.rowcount or 0), int(deleted.rowcount or 0)


@dataclass
class SweepResult:
    """What one sweep of one org changed. Counts only.

    ``written``, ``updated`` and ``deleted`` count edges by relation.
    ``aliases`` and ``aliases_removed`` count the domain aliases of customer
    entities. ``total`` counts the edges written. ``changes`` counts every
    row that the sweep changed, so a sweep with no change gives 0."""

    written: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    aliases: int = 0
    aliases_removed: int = 0

    @property
    def total(self) -> int:
        return sum(self.written.values())

    @property
    def changes(self) -> int:
        return (self.total + sum(self.updated.values()) + sum(self.deleted.values())
                + self.aliases + self.aliases_removed)


#: The most domain aliases that one sweep adds, and the most that it
#: removes. Read at call time, so a test can lower it.
ALIAS_BATCH = 1000


def _alias_add_sql() -> str:
    """Each one-company host that an email of the org sends from, and that
    the customer entity of that company does not hold as an alias yet. So
    each night's batch moves on. Ids and hosts only."""
    return (
        f"SELECT o.t AS company_id, o.k AS domain FROM ({_ONE_COMPANY_HOSTS}) AS o "
        f"WHERE o.k IN (SELECT d.k FROM ({_SENDER_DOMAINS}) AS d) "
        "AND NOT EXISTS (SELECT 1 FROM kb_entities e "
        "WHERE e.organization_id = CAST(:org AS uuid) AND e.ref_table = 'crm_companies' "
        "AND e.ref_id = CAST(o.t AS uuid) AND EXISTS (SELECT 1 FROM unnest(e.aliases) "
        "AS ea(v) WHERE lower(ea.v) = o.k)) "
        "ORDER BY o.t, o.k LIMIT :n"
    )


def _alias_remove_sql() -> str:
    """Each customer entity that holds a host as an alias that two companies
    of the org now share. Ids and hosts only."""
    return (
        "SELECT e.id::text AS entity_id, c.k AS domain "
        f"FROM ({_COMPANY_HOSTS}) AS c JOIN kb_entities e "
        "ON e.organization_id = CAST(:org AS uuid) AND e.ref_table = 'crm_companies' "
        "AND e.ref_id = CAST(c.t AS uuid) "
        f"WHERE c.k IN ({_SHARED_HOSTS}) "
        "AND EXISTS (SELECT 1 FROM unnest(e.aliases) AS ea(v) WHERE lower(ea.v) = c.k) "
        "ORDER BY 1, 2 LIMIT :n"
    )


async def _sync_domain_aliases(db: Any, org: str) -> tuple[int, int]:
    """Add each one-company sender host to the aliases of its customer
    entity, and remove each host alias that two companies now share. Both go
    through the registry. Answers ``(added, removed)``."""
    gone = (await db.execute(text(_alias_remove_sql()),
                             {"org": org, "n": ALIAS_BATCH})).fetchall()
    removed = 0
    for row in gone:
        removed += await registry.remove_aliases(db, row.entity_id, [row.domain])
    rows = (await db.execute(text(_alias_add_sql()),
                             {"org": org, "n": ALIAS_BATCH})).fetchall()
    added = 0
    for row in rows:
        if len(row.domain) > registry.ALIAS_MAX:
            continue
        entity = await registry.ensure_entity(db, "crm_companies", row.company_id)
        if entity is not None:
            added += await registry.add_aliases(db, entity, [row.domain])
    return added, removed


async def sweep_org(db: Any, org: str) -> SweepResult:
    """Bring the graph rows of *org* to today, then the domain aliases.

    A structural form writes each edge that is not in ``kb_links`` yet. An
    identifier form first reconciles its own rows, then writes. *db* is a
    session bound to *org*. Each form runs in a savepoint, so a form whose
    source table is absent or fails loses its own rows only.
    """
    result = SweepResult()
    for form in SWEEP_FORMS:
        check_form(form)  # a bad form is a code defect: it raises, never logs
        try:
            async with db.begin_nested():
                updated = deleted = 0
                if form.identifier:
                    updated, deleted = await _reconcile(db, org, form)
                made = await _write_edges(db, org, form)
        except Exception as exc:  # one form must not stop the others
            _log.warning("graph.sweep.form_failed", relation=form.relation,
                         source_kind=form.source_kind, target_kind=form.target_kind,
                         error=type(exc).__name__)
            continue
        name = form.relation
        result.written[name] = result.written.get(name, 0) + made
        if form.identifier:
            result.updated[name] = result.updated.get(name, 0) + updated
            result.deleted[name] = result.deleted.get(name, 0) + deleted
    try:
        async with db.begin_nested():
            result.aliases, result.aliases_removed = await _sync_domain_aliases(db, org)
    except Exception as exc:  # the edges stay when the aliases fail
        _log.warning("graph.sweep.aliases_failed", error=type(exc).__name__)
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
    _log.info("graph.sweep.done", total=result.total, changes=result.changes,
              aliases=result.aliases, aliases_removed=result.aliases_removed,
              **result.written)


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


__all__ = [
    "APP_KINDS",
    "MAX_EDGES",
    "NODE_KINDS",
    "PROVENANCES",
    "RELATIONS",
    "RESOLVER",
    "RESOLVE_FORMS",
    "SITE_HOSTS",
    "STRUCTURAL_FORMS",
    "SWEEP_FORMS",
    "SWEEP_KIND",
    "Edge",
    "EdgeForm",
    "Relation",
    "SweepResult",
    "check_form",
    "read_edges",
    "remove_edge",
    "seed",
    "sweep_allowed",
    "sweep_job",
    "sweep_org",
]
