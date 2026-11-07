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
AMBIGUOUS row for each candidate, at 1/n (§4.2). A free-mail domain of
``CONSUMER_DOMAINS`` never maps to a company. A sender domain that names one
company also becomes an alias of its customer entity, through
``registry.add_aliases``.

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
    Relation("domain_company", ("email",), ("company",), "EXTRACTED", "source",
             ambiguous=True),
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

    An ``ambiguous`` form (KG-5) also answers ``n``: the number of
    candidates of the identifier that gave the row. The writer keeps the
    lowest ``n`` of each pair of ends. ``n`` = 1 gives EXTRACTED at 1.0, and
    more gives AMBIGUOUS at 1/n (§4.2)."""

    relation: str
    source_kind: str
    target_kind: str
    select: str
    ambiguous: bool = False


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
    # `meeting.attendee_ids` has no foreign key, so each id must be a row of
    # `people` in the same org before it gives an edge (§4.1).
    EdgeForm("attendee", "meeting", "person",
             "SELECT m.id::text AS s, p.id::text AS t FROM meeting m "
             "CROSS JOIN LATERAL unnest(m.attendee_ids) AS a(pid) "
             "JOIN people p ON p.id = a.pid AND p.organization_id = m.organization_id "
             "WHERE m.organization_id = CAST(:org AS uuid)"),
)

# ── The KG-5 forms: one exact identifier to its records ─────────────────────
#
# An identifier is an email address, a phone or a domain (§4.2). Each form
# joins the record that holds the identifier to the records that hold the
# same one, and to nothing else. So no third record joins the two ends
# (§4.3). Each form counts the candidates of each identifier in the org, and
# two or more give AMBIGUOUS rows, never a guess.

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


def _consumer_list() -> str:
    """``CONSUMER_DOMAINS`` as a SQL list. Imported, never copied (§4.2)."""
    for d in CONSUMER_DOMAINS:
        if not re.fullmatch(r"[a-z0-9.-]+", d):
            raise ValueError(f"a consumer domain is not a plain host: {d!r}")
    return ", ".join(f"'{d}'" for d in sorted(CONSUMER_DOMAINS))


_CONSUMER_SQL = _consumer_list()

#: The host of a CRM company website: no scheme, no ``www.``, no port or path.
_WEBSITE_HOST = (
    "lower(regexp_replace(regexp_replace(btrim(co.website), "
    "'^[A-Za-z][A-Za-z0-9+.-]*://', ''), '^www[.]|[/:?#].*$', '', 'g'))"
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

#: The phone candidates of the org: people, and CRM contacts by phone and
#: by mobile. One contact with both numbers is one candidate.
_PHONE_CANDIDATES = (
    "SELECT x.tk, x.t, x.k FROM ("
    "SELECT 'person' AS tk, p.id::text AS t, " + _phone_key("p.phone") + " AS k "
    "FROM people p WHERE p.organization_id = CAST(:org AS uuid) "
    "UNION SELECT 'contact', c.id::text, " + _phone_key("c.phone") + " "
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

#: The domain candidates of the org: the host of a CRM company website and
#: the domain of its address. A free-mail domain is never a candidate.
_DOMAIN_CANDIDATES = (
    "SELECT x.tk, x.t, x.k FROM ("
    "SELECT 'company' AS tk, co.id::text AS t, " + _WEBSITE_HOST + " AS k "
    "FROM crm_companies co WHERE co.organization_id = CAST(:org AS uuid) "
    "AND co.website IS NOT NULL "
    "UNION SELECT 'company', co.id::text, " + _domain_of_sql("co.email") + " "
    "FROM crm_companies co WHERE co.organization_id = CAST(:org AS uuid) "
    f") AS x WHERE x.k LIKE '%_._%' AND x.k NOT IN ({_CONSUMER_SQL})"
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


#: Each KG-5 form (§4.1). The source end holds the identifier (§4.3).
RESOLVE_FORMS: tuple[EdgeForm, ...] = (
    EdgeForm("participant", "email", "person",
             _resolve_select(_EMAIL_ADDRESSES, _ADDRESS_CANDIDATES, "person"), True),
    EdgeForm("participant", "email", "contact",
             _resolve_select(_EMAIL_ADDRESSES, _ADDRESS_CANDIDATES, "contact"), True),
    EdgeForm("participant", "whatsapp", "person",
             _resolve_select(_WHATSAPP_PHONES, _PHONE_CANDIDATES, "person"), True),
    EdgeForm("participant", "whatsapp", "contact",
             _resolve_select(_WHATSAPP_PHONES, _PHONE_CANDIDATES, "contact"), True),
    EdgeForm("domain_company", "email", "company",
             _resolve_select(_SENDER_DOMAINS, _DOMAIN_CANDIDATES, "company"), True),
    # The address half of `attendee`. It runs after the id half of KG-4, so
    # a person that `attendee_ids` names keeps its EXTRACTED row.
    EdgeForm("attendee", "meeting", "person",
             _resolve_select(
                 _MEETING_ADDRESSES,
                 "SELECT 'person' AS tk, p.id::text AS t, " + _address_key("p.email")
                 + " AS k FROM people p WHERE p.organization_id = CAST(:org AS uuid) "
                 "AND p.email LIKE '%_@_%'",
                 "person"), True),
)

#: Every form of the sweep, in order: the stored ids first, then KG-5.
SWEEP_FORMS: tuple[EdgeForm, ...] = STRUCTURAL_FORMS + RESOLVE_FORMS

#: The one INSERT of a graph row. ``page_id`` is always NULL. The conflict
#: target is the partial unique index ``kb_links_graph_key``. ``{count}`` is
#: ``1`` for a stored id, or the lowest ``x.n`` for an identifier form.
_INSERT_EDGES_SQL = (
    "INSERT INTO kb_links (organization_id, page_id, source_kind, source_id, "
    "relation, target_kind, target_id, provenance, confidence, created_by) "
    "SELECT DISTINCT CAST(:org AS uuid), CAST(NULL AS uuid), :sk, e.s, :rel, :tk, e.t, "
    "CAST(CASE WHEN e.n > 1 THEN 'AMBIGUOUS' ELSE CAST(:prov AS text) END AS text), "
    "CAST(CAST(:conf AS real) / GREATEST(e.n, 1) AS real), :by "
    "FROM (SELECT x.s, x.t, min({count}) AS n FROM ({select}) AS x "
    "GROUP BY x.s, x.t) AS e WHERE e.s IS NOT NULL AND e.t IS NOT NULL "
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
    if form.ambiguous and not rel.ambiguous:
        raise ValueError(f"{form.relation} is never AMBIGUOUS, so no form may count candidates")
    return rel


async def _write_edges(db: Any, org: str, form: EdgeForm) -> int:
    """Write the edges of one form for *org*. Answers the rows written."""
    rel = check_form(form)
    count = "x.n" if form.ambiguous else "1"
    result = await db.execute(
        text(_INSERT_EDGES_SQL.format(select=form.select, count=count)),
        {"org": org, "sk": form.source_kind, "rel": rel.name, "tk": form.target_kind,
         "prov": rel.provenance, "conf": EXTRACTED_CONFIDENCE, "by": SYSTEM},
    )
    return int(result.rowcount or 0)


@dataclass
class SweepResult:
    """What one sweep of one org wrote, by relation. Counts only.

    ``aliases`` counts the domains that the sweep added to the aliases of a
    customer entity. ``total`` counts edges only."""

    written: dict[str, int] = field(default_factory=dict)
    aliases: int = 0

    @property
    def total(self) -> int:
        return sum(self.written.values())


#: The most domain aliases that one sweep looks at.
ALIAS_BATCH = 1000

#: Each sender domain of the org that names exactly one CRM company. The
#: sweep adds the domain to the aliases of that customer entity, so a later
#: code match (KG-10) finds the company by its domain. Ids and domains only.
_ALIAS_DOMAINS_SQL = (
    f"WITH cand AS ({_DOMAIN_CANDIDATES}), "
    "counted AS (SELECT c.t, c.k, count(*) OVER (PARTITION BY c.k) AS n FROM cand c) "
    "SELECT DISTINCT c.t AS company_id, c.k AS domain FROM counted c "
    f"WHERE c.n = 1 AND c.k IN (SELECT d.k FROM ({_SENDER_DOMAINS}) AS d) "
    "ORDER BY c.t, c.k LIMIT :n"
)


async def _write_domain_aliases(db: Any, org: str) -> int:
    """Add each one-company sender domain to the aliases of its customer
    entity, through the registry. Answers the aliases added."""
    rows = (await db.execute(text(_ALIAS_DOMAINS_SQL),
                             {"org": org, "n": ALIAS_BATCH})).fetchall()
    added = 0
    for row in rows:
        if len(row.domain) > registry.ALIAS_MAX:
            continue
        entity = await registry.ensure_entity(db, "crm_companies", row.company_id)
        if entity is not None:
            added += await registry.add_aliases(db, entity, [row.domain])
    return added


async def sweep_org(db: Any, org: str) -> SweepResult:
    """Write each edge of *org* that is not in ``kb_links`` yet, then the
    domain aliases of KG-5.

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
    try:
        async with db.begin_nested():
            result.aliases = await _write_domain_aliases(db, org)
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
    _log.info("graph.sweep.done", total=result.total, aliases=result.aliases,
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
    "RESOLVE_FORMS",
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
