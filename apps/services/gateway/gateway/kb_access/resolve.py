"""The resolver: which members a provider reader proves (WS-61 KC-4a).

Spec: ``project-docs/specs/knowledge_curation.md`` §8.2, with the defaults
that §8 records for the open questions OQ1 to OQ4.

A row of ``kb_node_access`` is a reader at the provider, in the provider's
words. It is never a grant. This module decides which MEMBERS of the bound
organization a row proves. The answer is a set of emails in the one subject
grammar (``acb_auth.permissions.normalize_grant_subject``). It never holds
``org`` and never holds a ``group:<slug>``: a proof is always one member.

The rules. Each one fails closed:

* **user** — the email, only when it is an active ``app_user`` of the bound
  organization. An address outside the org stays a row at the provider and
  never becomes a subject.
* **group** — proves nobody. A later slice expands a group when the org
  connects a directory that lists its members.
* **domain** (OQ1) — proves each active member whose own email is in that
  domain, only when ``organization.domain`` is set and equals the row's
  domain, case-folded. It never becomes the literal ``org``. With
  ``organization.domain`` NULL, a domain row proves nobody.
* **anyone** (OQ2) — proves nobody, also when the file can be found.
* **the owner** (OQ3) — the ``owner_email`` of a member source proves itself
  on each live node of that source, also when the node has no row.
* **knowledge:manage** (OQ4) — proves nothing.

A node that is not ``live`` has no proven reader.

**The SQL twin.** ``routes/knowledge/core.document_reader_clause`` is the
same rule as a predicate, for one viewer. The R8 suite
``tests/unit/test_kc_access_r8.py`` proves that the two agree for each
member. A change to one is a change to both.

**The tenant comes from the caller's session.** Each read names the org, so
a session with row level security off still reads one organization (R5).
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from acb_auth.permissions import normalize_grant_subject
from sqlalchemy import text

from gateway.kb_sources.base import (
    READER_ANYONE,
    READER_DOMAIN,
    READER_GROUP,
    READER_USER,
    AccessReader,
)

#: The kind of source whose owner is a proven reader of its nodes (OQ3).
MEMBER_SOURCE = "member"

#: The active members of the bound organization, folded.
_MEMBERS_SQL = (
    "SELECT lower(au.email) AS email FROM app_user au "
    "WHERE au.organization_id = CAST(:org AS uuid) AND au.status = 'active'"
)

#: The org's own domain (OQ1). ``organization`` is exempt from row level
#: security, so the id names the org.
_DOMAIN_SQL = "SELECT o.domain FROM organization o WHERE o.id = CAST(:org AS uuid)"


@dataclass(frozen=True)
class NodeReaders:
    """One node as the resolver sees it: live or not, its owner, its rows."""

    live: bool
    owner: str | None
    readers: tuple[AccessReader, ...]


def fold_domain(value: Any) -> str | None:
    """A domain, folded, or ``None`` for an empty value."""
    out = str(value or "").strip(" ").lower()
    return out or None


def email_domain(email: str) -> str | None:
    """The domain of an email with exactly one ``@``, else ``None``."""
    parts = email.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[1]


def member_subject(principal: str, members: AbstractSet[str]) -> str | None:
    """The subject of a ``user`` reader: its email, when it is a member."""
    try:
        subject: str = normalize_grant_subject(principal)
    except ValueError:
        return None
    if "@" not in subject or subject not in members:
        return None
    return subject


def proven_by(reader: AccessReader, *, members: AbstractSet[str],
              org_domain: str | None) -> frozenset[str]:
    """The members that ONE reader proves (the module doc). Never ``org``."""
    if reader.kind == READER_USER:
        subject = member_subject(reader.principal, members)
        return frozenset({subject}) if subject else frozenset()
    if reader.kind == READER_DOMAIN:
        domain = fold_domain(org_domain)
        if domain is None or fold_domain(reader.principal) != domain:
            return frozenset()
        return frozenset(m for m in members if email_domain(m) == domain)
    if reader.kind in (READER_GROUP, READER_ANYONE):
        return frozenset()
    return frozenset()


def proven_readers(readers: Iterable[AccessReader], *, members: AbstractSet[str],
                   org_domain: str | None, owner: str | None = None) -> frozenset[str]:
    """Every member that the readers of one live node prove, and the owner
    of its member source (OQ3). Emails only."""
    out: set[str] = set()
    for reader in readers:
        out |= proven_by(reader, members=members, org_domain=org_domain)
    if owner:
        subject = member_subject(owner, members)
        if subject:
            out.add(subject)
    return frozenset(out)


async def load_members(db: Any, org: str) -> frozenset[str]:
    """The active members of *org*, folded."""
    rows = (await db.execute(text(_MEMBERS_SQL), {"org": org})).fetchall()
    return frozenset(str(r.email) for r in rows if r.email)


async def load_org_domain(db: Any, org: str) -> str | None:
    """``organization.domain`` of *org*, folded, or ``None``."""
    row = (await db.execute(text(_DOMAIN_SQL), {"org": org})).first()
    return fold_domain(row.domain) if row is not None else None


async def load_node(db: Any, org: str, node_id: str) -> NodeReaders | None:
    """The node, the owner of its member source and its readers, or ``None``.

    An integrity read of the filing rule (listed in
    ``tests/unit/test_kb_visibility_one_seam.py``): it applies no member's
    clause, and it returns the readers to the rule, never a document.
    """
    sql = (
        "SELECT n.state, s.kind AS source_kind, s.owner_email, "
        "a.principal_kind, a.principal, a.role FROM kb_source_nodes n "
        "JOIN kb_sources s ON s.id = n.source_id "
        "AND s.organization_id = CAST(:org AS uuid) "
        "LEFT JOIN kb_node_access a ON a.node_id = n.id "
        "AND a.organization_id = CAST(:org AS uuid) "
        "WHERE n.organization_id = CAST(:org AS uuid) AND n.id = CAST(:node AS uuid)"
    )
    rows = (await db.execute(text(sql), {"org": org, "node": node_id})).fetchall()
    if not rows:
        return None
    first = rows[0]
    owner = str(first.owner_email) if first.source_kind == MEMBER_SOURCE and (
        first.owner_email) else None
    readers = tuple(
        AccessReader(kind=str(r.principal_kind), principal=str(r.principal), role=str(r.role))
        for r in rows if r.principal_kind is not None)
    return NodeReaders(live=first.state == "live", owner=owner, readers=readers)


def proven_for(node: NodeReaders | None, *, members: AbstractSet[str],
               org_domain: str | None) -> frozenset[str]:
    """The proven readers of *node*. A node that is not live, or not found,
    proves nobody."""
    if node is None or not node.live:
        return frozenset()
    return proven_readers(node.readers, members=members, org_domain=org_domain,
                          owner=node.owner)


__all__ = [
    "MEMBER_SOURCE",
    "NodeReaders",
    "email_domain",
    "fold_domain",
    "load_members",
    "load_node",
    "load_org_domain",
    "member_subject",
    "proven_by",
    "proven_for",
    "proven_readers",
]
