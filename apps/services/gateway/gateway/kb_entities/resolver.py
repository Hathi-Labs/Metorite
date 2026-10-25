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

__all__ = ["fold", "matches_query", "resolve_alias", "resolve_person"]

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
