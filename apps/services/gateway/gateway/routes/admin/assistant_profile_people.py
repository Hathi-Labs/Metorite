"""``GET /auth/me/assistant-profile/resolve-person`` — a name to a colleague (WS-63 PZ-5).

Spec: ``project-docs/specs/personalization.md`` §5.4. The orchestrator tool
``acb_skills.profile_tools.resolve_person`` calls this route as the run's
member. The route answers data, and the tool writes the text.

**This is not the graph resolver.** ``acb_graph.resolver.resolve_person``
resolves a Person ENTITY of the knowledge graph. ``/projects/assignees``,
``_resolve_assignee_name``, ``names_of`` and ``sources._people`` answer other
questions. PZ-5 replaces none of them, and none of them is reused here.

**Two tiers, in order** (§5.4, repair round 1 of 2026-10-24):

1. ``alias``: the member's own aliases, ``fold(alias) == fold(name)``. The
   member set them on purpose, so a hit here wins outright. Each hit's
   address must still be in the org (:func:`email_in_org`). An alias of a
   person who left drops, and the lookup falls through.
2. ``directory``: ``people`` (``status = 'active'``) and ``app_user``
   (``status = 'active'``) TOGETHER. Every plausible person counts: the
   preferred name, the name, the display name or the local part of the
   email equal to the input, or (for a one-word input) the first word of a
   name. One candidate acts. Two or more ask. "First rule wins" is gone:
   it let ``priya@`` hide Priya Sharma, so the assistant picked one Priya
   by guess (PZ-5 verify, P1).

An input with an ``@`` matches ``lower(email)`` exactly, in both tiers.
Two rows with one address are one person.

**The People gate (owner decision, 2026-10-23).** Tiers 2 and 3 read the
directory, so they run only for a member who holds ``feature:people``. The
check is ``UserContext.has_permission``, the access intersect that
``require_feature`` reads. A member without it gets tier 1 only, and the
answer says ``directory: false``. The route then runs no ``people`` or
``app_user`` SELECT, apart from the address check of each alias hit.

**Folding.** The ladder has no ``unaccent``, so Python folds both sides:
NFKD, no combining mark, ``casefold``, one space. Each tier reads at most
:data:`DIRECTORY_CAP` rows of the bound tenant.

**Tenancy.** The member comes from ``get_current_user`` only. The tenant
comes from the bound session. Each SQL names the bound tenant beside RLS,
as ``people/core.py::has_login`` does, and an unbound GUC matches no row
(R5). The route takes no flag: the tool is the part behind
``ASSISTANT_PROFILE_ENABLED``.

Fences: ``tests/unit/test_resolve_person.py`` and
``tests/unit/test_resolve_person_r8.py`` (R8, the one-org read).
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.assistant_profile import READ_SQL, stored_profile
from acb_common.db import current_tenant
from fastapi import Depends, HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.routes.admin.me import me_router

logger = logging.getLogger(__name__)

#: The shortest and the longest name, after the white space collapses.
NAME_MIN = 2
NAME_MAX = 80
#: The most rows that one directory tier reads.
DIRECTORY_CAP = 2000
#: The most matches that one answer carries. ``total`` counts them all.
MATCHES_MAX = 5
#: The feature that opens the directory tiers.
PEOPLE_FEATURE = "feature:people"

#: The bound tenant, as ``people/core.py::has_login`` writes it. ``NULLIF``
#: fails closed: an unbound GUC matches no row.
_BOUND = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: Is the address a person or a member of the bound org, who has not left?
#: A person who left is ``alumni``. A member who left is ``suspended`` or
#: ``removed``. One statement, two tables.
EMAIL_IN_ORG_SQL = (
    "SELECT 1 WHERE EXISTS ("
    "SELECT 1 FROM people WHERE lower(email) = :email "
    f"AND organization_id = {_BOUND} "
    "AND COALESCE(status, 'active') <> 'alumni') "
    "OR EXISTS ("
    "SELECT 1 FROM app_user WHERE lower(email) = :email "
    f"AND organization_id = {_BOUND} "
    "AND status IN ('active', 'invited'))"
)

#: Tier 2: the active people of the bound org who have an address.
PEOPLE_SQL = (
    "SELECT name, preferred_name, email FROM people "
    f"WHERE organization_id = {_BOUND} AND status = 'active' "
    "AND email IS NOT NULL AND email <> '' "
    "ORDER BY lower(email) LIMIT :cap"
)

#: Tier 3: the active members of the bound org who have a display name.
MEMBERS_SQL = (
    "SELECT display_name, email FROM app_user "
    f"WHERE organization_id = {_BOUND} AND status = 'active' "
    "ORDER BY lower(email) LIMIT :cap"
)


def fold(value: Any) -> str:
    """*value* for comparison: NFKD, no combining mark, casefold, one space."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def _is_email(value: str) -> bool:
    return "@" in value


def _first(value: str) -> str:
    """The folded first word of *value*, or ``""``. People say first names."""
    words = fold(value).split()
    return words[0] if words else ""


async def email_in_org(db: Any, email: str) -> bool:
    """True when *email* is a person or a member of the bound org.

    The one check of an alias address. The alias POST and PATCH call it, and
    tier 1 calls it for each hit. It answers yes or no, and no directory
    data. It needs no feature, because the member typed the address.
    """
    from sqlalchemy import text

    address = (email or "").strip().lower()
    if not address:
        return False
    row = (await db.execute(text(EMAIL_IN_ORG_SQL), {"email": address})).first()
    return row is not None


def _one_each(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    """``(name, email)`` pairs, one per lower-case address, in a fixed order."""
    seen: dict[str, dict[str, str]] = {}
    for name, email in pairs:
        key = email.strip().lower()
        if key and key not in seen:
            seen[key] = {"name": name, "email": email.strip()}
    return sorted(seen.values(), key=lambda m: (fold(m["name"]), m["email"].lower()))


async def _alias_tier(db: Any, uid: str, org: str, wanted: str,
                      address: str) -> list[dict[str, str]]:
    from sqlalchemy import text

    row = (await db.execute(text(READ_SQL), {"org": org, "m": uid})).first()
    aliases = stored_profile(row[0] if row is not None else None)["aliases"]
    if address:
        hits = [a for a in aliases if a["email"].strip().lower() == address]
    else:
        hits = [a for a in aliases if fold(a["alias"]) == wanted]
    found = _one_each([(a["alias"], a["email"]) for a in hits])
    # An alias of a person who left drops, so the lookup falls through.
    return [m for m in found if await email_in_org(db, m["email"])]


async def _directory_tier(db: Any, wanted: str,
                          address: str) -> tuple[list[dict[str, str]], str, bool]:
    """Every plausible person of the directory and the member list.

    Returns ``(matches, source, truncated)``. ``source`` is ``people`` when a
    directory row matched, else ``member``. ``truncated`` is true when a
    table held more than :data:`DIRECTORY_CAP` rows, so a person may be
    missing from the answer. The two tables are read together and deduped
    by address, so a person with a login is one person.
    """
    from sqlalchemy import text

    people_rows = (await db.execute(text(PEOPLE_SQL), {"cap": DIRECTORY_CAP})).fetchall()
    member_rows = (await db.execute(text(MEMBERS_SQL), {"cap": DIRECTORY_CAP})).fetchall()
    truncated = DIRECTORY_CAP in (len(people_rows), len(member_rows))
    people = [(str(r.name or ""), str(r.preferred_name or ""), str(r.email)) for r in people_rows]
    members = [(str(r.display_name or ""), str(r.email)) for r in member_rows]
    one_word = " " not in wanted

    def names_match(*names: str) -> bool:
        if any(n and fold(n) == wanted for n in names):
            return True
        return one_word and wanted in {_first(n) for n in names if n}

    if address:
        from_people = [(p or n or e, e) for n, p, e in people if e.strip().lower() == address]
        from_members = [(d or e, e) for d, e in members if e.strip().lower() == address]
    else:
        from_people = [(p or n or e, e) for n, p, e in people
                       if names_match(p, n) or fold(e.split("@", 1)[0]) == wanted]
        from_members = [(d or e, e) for d, e in members
                        if (d and names_match(d)) or fold(e.split("@", 1)[0]) == wanted]
    found = _one_each(from_people + from_members)
    source = "people" if from_people else "member"
    return found, source, truncated


def _name(value: str) -> str:
    """The name, with its white space collapsed. 422 when it is out of range."""
    out = " ".join(str(value or "").split())
    if not NAME_MIN <= len(out) <= NAME_MAX:
        raise HTTPException(status_code=422,
                            detail=f"Give a name of {NAME_MIN} to {NAME_MAX} characters.")
    return out


@me_router.get("/me/assistant-profile/resolve-person",
               summary="Resolve a name to a colleague for the assistant (PZ-5)")
async def resolve_person_for_assistant(
    name: str = Query(default=""),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The first tier with a match, its matches (at most five) and the count.

    ``{"source": "alias"|"people"|"member"|None, "matches": [{name, email}],
    "total": n, "directory": bool}``. ``directory`` is false when the member
    lacks ``feature:people``, so only the aliases were searched.
    """
    uid = getattr(user, "email", None) or ""
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = _name(name)
    wanted = fold(raw)
    # An address compares in lower case on both sides, never casefolded:
    # casefold turns "Strauß@" into "strauss@" (PZ-5 verify, P3).
    address = raw.strip().lower() if _is_email(raw) else ""
    directory = user.has_permission(PEOPLE_FEATURE)
    org = current_tenant()
    if not org:
        raise HTTPException(status_code=503, detail="unavailable")
    source: str | None = None
    found: list[dict[str, str]] = []
    truncated = False
    try:
        async with _tenant_session() as db:
            found = await _alias_tier(db, uid, org, wanted, address)
            if found:
                source = "alias"
            elif directory:
                found, tier, truncated = await _directory_tier(db, wanted, address)
                source = tier if found else None
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("auth.assistant_profile.resolve_failed",
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return {"source": source, "matches": found[:MATCHES_MAX], "total": len(found),
            "directory": directory, "truncated": truncated}
