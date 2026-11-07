"""The alias resolver and the person resolver of the registry (WS-61 KC-8a).

Spec: ``project-docs/specs/knowledge_curation.md`` §6.2 (aliases) and §6.4
(the person resolver).

**Aliases.** ``kb_entities.aliases`` holds the other names that documents
use: "the Penrose", "PX-1". :func:`resolve_alias` reads them through the
owner rule of each type (``registry.directory_rows``), so an alias never
reveals a record the caller cannot see. Both sides fold with ``fold()`` of
PZ-5: NFKD, no combining mark, ``casefold``, one space.

**A person.** :func:`resolve_person` maps a name or an address to
``people.id``. It runs only for a holder of ``feature:people``, the owner
rule of a person. The alias tier comes first. Then the directory tier of
PZ-5 (``_directory_tier``) gives the plausible people and members, and each
address maps to ``people.id`` with ``lower(email)``, through the person
rule. A member with no ``people`` row drops. One match acts, and two or more
ask. The PZ-5 helpers ``fold``, ``email_in_org`` and ``_directory_tier`` are
imported, never copied. ``acb_graph`` resolves a different Person model,
and this module never calls it.

**An entity by a name or a citation (KG-7).** :func:`resolve_entity` maps
the words of ``get_entity`` to the entities the caller sees. A citation
(:data:`CITATIONS`) reads one row. A name goes through three tiers: an alias
equal to the words, then a name equal to the words, then a name or an alias
that holds the words. The first tier with a row wins. Each read runs under
the owner rule of its type, through ``registry``. :func:`find_entity` opens
the tenant session for ``gateway/retrieval/``, which holds no SQL.

**A viewer only.** Each function takes the caller's ``UserContext`` and
returns names that the caller may read. A background caller (KC-6, KC-9)
has no viewer and gets entity ids only, from a later door.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext

from gateway.kb_entities import registry
from gateway.routes.admin.assistant_profile_people import (
    _directory_tier,
    email_in_org,
    fold,
)

__all__ = [
    "CITATIONS",
    "find_entity",
    "fold",
    "matches_query",
    "parse_citation",
    "resolve_alias",
    "resolve_entity",
    "resolve_person",
]

#: The rows that hold one alias or more. A code constant, never input.
_HAS_ALIASES = "cardinality(e.aliases) > 0"

#: The people whose address is one of a list. A code constant, never input.
_EMAIL_IN = "lower(o.email) = ANY(CAST(:ke_emails AS text[]))"


def matches_query(row: dict[str, Any], wanted: str) -> bool:
    """True when the folded *wanted* is in the name or in an alias of *row*."""
    if not wanted:
        return True
    if wanted in fold(row.get("name")):
        return True
    return any(wanted in fold(alias) for alias in row.get("aliases") or [])


def _alias_hits(rows: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    return [r for r in rows if any(fold(a) == wanted for a in r.get("aliases") or [])]


async def resolve_alias(
    db: Any, user: UserContext, words: str, kind: str | None = None,
) -> list[dict[str, Any]]:
    """The entities the caller sees with an alias equal to *words*, folded.

    *kind* narrows the search to one type. A record the caller cannot see
    gives no hit, because the read runs under its owner rule.
    """
    wanted = fold(words)
    if not wanted:
        return []
    hits: list[dict[str, Any]] = []
    for each in (kind,) if kind else registry.ENTITY_TYPES:
        rows, _ = await registry.directory_rows(db, user, each, extra_where=_HAS_ALIASES)
        hits.extend(_alias_hits(rows, wanted))
    return hits


def _person_match(row: dict[str, Any]) -> dict[str, Any]:
    return {"people_id": row["ref_id"], "entity_id": row["id"], "name": row["name"]}


async def resolve_person(db: Any, user: UserContext, words: str) -> dict[str, Any]:
    """A name or an address to ``people.id``, for a holder of ``feature:people``.

    ``{"status": "one"|"ask"|"none", "via": "alias"|"directory"|None,
    "matches": [{people_id, entity_id, name}], "directory": bool}``. One
    match acts. Two or more ask. ``directory`` is false when the caller
    lacks ``feature:people``, and then nothing is read.
    """
    raw = " ".join(str(words or "").split())
    wanted = fold(raw)
    empty: dict[str, Any] = {"status": "none", "via": None, "matches": [],
                             "directory": user.has_permission(registry.PEOPLE_FEATURE)}
    if not wanted or not empty["directory"]:
        return empty
    # An address compares in lower case, never casefolded (PZ-5, P3).
    address = raw.lower() if "@" in raw else ""
    via = "alias"
    hits: list[dict[str, Any]] = []
    if not address:
        rows, _ = await registry.directory_rows(db, user, "person", extra_where=_HAS_ALIASES)
        hits = _alias_hits(rows, wanted)
    if not hits:
        via = "directory"
        found, _source, _truncated = await _directory_tier(db, wanted, address)
        emails: list[str] = []
        for match in found:
            email = str(match.get("email") or "").strip().lower()
            if email and email not in emails and await email_in_org(db, email):
                emails.append(email)
        if emails:
            hits, _ = await registry.directory_rows(
                db, user, "person", extra_where=_EMAIL_IN,
                extra_params={"ke_emails": emails},
            )
    seen: dict[str, dict[str, Any]] = {}
    for row in hits:
        if row["ref_id"] and row["ref_id"] not in seen:
            seen[row["ref_id"]] = _person_match(row)
    matches = list(seen.values())
    if not matches:
        return {**empty, "via": None}
    status = "one" if len(matches) == 1 else "ask"
    return {"status": status, "via": via, "matches": matches, "directory": True}


# ── An entity by a name or a citation (WS-KG KG-7) ──────────────────────────

#: Each citation prefix that names an entity, and the type and owner table it
#: reads. ``entity:<id>`` is the row of ``kb_entities`` itself. ``company`` is
#: the node kind of a CRM company (``knowledge_graph.md`` §3.1), so it names
#: a ``customer``.
CITATIONS: dict[str, tuple[str, str] | None] = {
    "entity": None,
    "person": ("person", "people"),
    "company": ("customer", "crm_companies"),
    "workstream": ("workstream", "pm_projects"),
    "workflow": ("workflow", "workflows"),
}

#: The most rows of one type that the name tiers read.
NAME_READ_CAP = 200

#: The most entities that :func:`resolve_entity` answers.
RESOLVE_MAX = 20


def parse_citation(words: str) -> tuple[str, str] | None:
    """``(prefix, id)`` when *words* is a citation of :data:`CITATIONS`, or None.

    Brackets around the citation are dropped. An id that is not a UUID is no
    citation, so the words then go to the name tiers.
    """
    raw = str(words or "").strip().strip("[]()<>").strip()
    prefix, sep, rest = raw.partition(":")
    prefix = prefix.strip().lower()
    if not sep or prefix not in CITATIONS:
        return None
    from gateway.routes.knowledge import core

    ident = core.parse_id(rest.strip())
    return (prefix, ident) if ident else None


def _unique(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows once each, by their owner row, in their order."""
    seen: dict[tuple[Any, Any], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("ref_table"), row.get("ref_id") or row.get("id"))
        seen.setdefault(key, row)
    return list(seen.values())[:RESOLVE_MAX]


async def resolve_entity(db: Any, user: UserContext, words: str) -> list[dict[str, Any]]:
    """The entities that *words* names, each under its owner rule (§6.4).

    A citation reads its one row. A name reads three tiers, and the first
    tier with a row wins: an alias equal to the folded words
    (:func:`resolve_alias`), a name equal to them, then a name or an alias
    that holds them. A row the caller cannot see is never read, so an
    unknown name and a hidden entity give the same empty answer.
    """
    cited = parse_citation(words)
    if cited is not None:
        prefix, ident = cited
        if CITATIONS[prefix] is None:
            row = await registry.get_entity(db, user, ident)
            return [row] if row else []
        kind, table = CITATIONS[prefix]  # type: ignore[misc]
        rows, _ = await registry.directory_rows(
            db, user, kind, ref_table=table, ref_id=ident, cap=1)
        return rows[:1]
    wanted = fold(words)
    if not wanted:
        return []
    alias = _unique(await resolve_alias(db, user, words))
    if alias:
        return alias
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for each in registry.ENTITY_TYPES:
        rows, _ = await registry.directory_rows(db, user, each, q=wanted, cap=NAME_READ_CAP)
        for row in rows:
            if fold(row.get("name")) == wanted:
                exact.append(row)
            elif matches_query(row, wanted):
                partial.append(row)
    return _unique(exact) or _unique(partial)


async def find_entity(user: UserContext, words: str) -> list[dict[str, Any]]:
    """:func:`resolve_entity` in its own tenant session (KG-7).

    Row level security binds the tenant of the request, and each rule also
    names ``user.organization_id``. So ``gateway/retrieval/`` holds no SQL.
    """
    from gateway.db import tenant_session

    async with tenant_session() as db:
        return await resolve_entity(db, user, words)
