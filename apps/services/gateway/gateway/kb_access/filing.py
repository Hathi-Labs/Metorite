"""The filing rule of a connected document (WS-61 KC-4a).

Spec: ``project-docs/specs/knowledge_curation.md`` §8.3, and the default OQ4
of §8.

**A document fits a space only when every reader of the space is a proven
reader of the document.** :func:`document_fits_space` answers
``Fit(fits, unproven)``. ``unproven`` names, sorted, each reader of the space
that the document does not prove.

**The readers of a space** are its grant subjects, expanded, and its
``owner_email``:

* ``org`` — every active member of the org;
* ``group:<slug>`` — the active members of that group;
* an email — that address, when it is an active member.

A grant of any level counts, because each level reads. ``knowledge:manage``
is not a reader (OQ4): it opens a space but proves no document, and
``core.visible_documents_clause`` shows a manager only what the provider
proves. An address that is not an active member reads nothing, so it is no
reader.

**The proven readers** come from ``resolve.py``, the same rule as
``core.document_reader_clause``. A node that is not ``live`` proves nobody,
so it fits only a space with no reader.

This module reads the space, its grants, the members and the node's readers.
It returns no document and no page. KC-6 and KC-7 call it when a person
accepts a filing, and again after each access pass. They decide who may ask.

**The tenant comes from the session (R5, R11).** The org is
``user.organization_id``, and each read names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acb_auth import UserContext
from acb_auth.permissions import normalize_grant_subject
from sqlalchemy import text

from gateway.kb_access import resolve
from gateway.routes.knowledge import core

_SPACE_SQL = (
    "SELECT s.id, s.kind, s.owner_email FROM kb_spaces s "
    "WHERE s.organization_id = CAST(:org AS uuid) AND s.id = CAST(:space AS uuid)"
)

_GRANTS_SQL = (
    "SELECT g.subject FROM kb_space_grants g "
    "WHERE g.organization_id = CAST(:org AS uuid) AND g.space_id = CAST(:space AS uuid)"
)

#: The active members of the named groups. The slug folds, so a grant
#: subject in any case counts its group.
_GROUP_MEMBERS_SQL = (
    "SELECT lower(au.email) AS email FROM org_group g "
    "JOIN org_group_member m ON m.group_id = g.id "
    "JOIN app_user au ON au.id = m.user_id "
    "WHERE g.organization_id = CAST(:org AS uuid) "
    "AND au.organization_id = CAST(:org AS uuid) AND au.status = 'active' "
    "AND lower(g.slug) = ANY(CAST(:slugs AS text[]))"
)


@dataclass(frozen=True)
class Fit:
    """The answer of the filing rule. ``unproven`` is sorted."""

    fits: bool
    unproven: tuple[str, ...]


async def space_readers(db: Any, org: str, space: Any,
                        members: frozenset[str]) -> frozenset[str]:
    """Every reader of *space*: its grant subjects expanded, and its owner.

    Emails only, each an active member. ``knowledge:manage`` adds nobody.
    """
    readers: set[str] = set()
    owner = resolve.member_subject(str(space.owner_email or ""), members)
    if owner:
        readers.add(owner)
    rows = (await db.execute(text(_GRANTS_SQL), {"org": org, "space": str(space.id)})).fetchall()
    slugs: list[str] = []
    for row in rows:
        try:
            subject = normalize_grant_subject(str(row.subject))
        except ValueError:
            continue  # not the one grammar: it opens the space to nobody
        if subject == "org":
            readers |= members
        elif subject.startswith("group:"):
            slugs.append(subject[len("group:"):])
        elif subject in members:
            readers.add(subject)
    if slugs:
        found = (await db.execute(text(_GROUP_MEMBERS_SQL), {
            "org": org, "slugs": sorted(set(slugs)),
        })).fetchall()
        readers |= {str(r.email) for r in found if str(r.email) in members}
    return frozenset(readers)


async def document_fits_space(db: Any, user: UserContext, node_id: str | None,
                              space_id: str | None) -> Fit:
    """Does the document *node_id* fit the space *space_id* (the module doc)?

    A node or a space that the org does not hold never fits.
    """
    org = core.org_of(user)
    nid, sid = core.parse_id(node_id), core.parse_id(space_id)
    if nid is None or sid is None:
        return Fit(fits=False, unproven=())
    space = (await db.execute(text(_SPACE_SQL), {"org": org, "space": sid})).first()
    if space is None:
        return Fit(fits=False, unproven=())
    members = await resolve.load_members(db, org)
    org_domain = await resolve.load_org_domain(db, org)
    readers = await space_readers(db, org, space, members)
    node = await resolve.load_node(db, org, nid)
    if node is None:
        return Fit(fits=False, unproven=tuple(sorted(readers)))
    proven = resolve.proven_for(node, members=members, org_domain=org_domain)
    unproven = tuple(sorted(readers - proven))
    return Fit(fits=not unproven, unproven=unproven)


__all__ = ["Fit", "document_fits_space", "space_readers"]
