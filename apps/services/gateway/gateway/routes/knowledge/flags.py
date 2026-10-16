"""The Knowledge kill switch (WS-61 KB-1).

Spec: ``project-docs/specs/knowledge_base.md`` §13.

This module is the ONE reader of the three settings below. Nothing else in the
gateway reads them.

* ``KNOWLEDGE_ENABLED`` — the kill switch, default OFF.
* ``KNOWLEDGE_ORGS`` — the organization ids that may reach ``/knowledge``,
  with a comma between ids. ``*`` allows each organization with a bound
  tenant. An empty list allows no organization.
* ``KNOWLEDGE_EMBED`` (KB-3) — the organization ids whose chunks get a
  vector, in the same grammar. Empty, the default, means no organization:
  the index stays lexical and the embed door is never called.

:func:`require_knowledge_enabled` is the first router dependency in
``core.py``. It runs BEFORE ``require_feature_router("knowledge")``, so an
organization outside the list gets 404 and never 403. A dark feature does not
announce itself.

The organization comes from ``UserContext.organization_id``, which
``acb_auth`` resolves on the server from the authenticated member. It never
comes from a header, the query or the body (R5, R11).

This module is a leaf. It imports nothing from its siblings. A flip for an
organization is the owner's, gate ``enforcement-flip``. Fence:
``tests/unit/test_kb_flags.py``.
"""

from __future__ import annotations

import functools
from typing import Annotated

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException

#: The value in ``KNOWLEDGE_ORGS`` that allows each organization with a bound
#: tenant.
ALL_ORGS = "*"


def knowledge_enabled() -> bool:
    """True when the kill switch is on. Default OFF."""
    return get_settings().knowledge_enabled is True


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The ids in *raw*, trimmed and in lower case. Cached on the raw value."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def knowledge_orgs() -> frozenset[str]:
    """The organization ids that may reach ``/knowledge``. Empty means none."""
    return _parse_orgs(str(get_settings().knowledge_orgs or ""))


def knowledge_org_allowed(organization_id: str | None) -> bool:
    """True when the switch is on AND the organization is on the list.

    No organization is false, also with ``*``. A caller with no bound tenant
    has no Knowledge to reach.
    """
    if not knowledge_enabled() or not organization_id:
        return False
    org = str(organization_id).strip().lower()
    if not org:
        return False
    orgs = knowledge_orgs()
    return ALL_ORGS in orgs or org in orgs


def knowledge_embed_orgs() -> frozenset[str]:
    """The organization ids whose chunks get a vector. Empty means none."""
    return _parse_orgs(str(get_settings().knowledge_embed or ""))


def knowledge_embed_allowed(organization_id: str | None) -> bool:
    """True when Knowledge serves the organization AND it is on ``KNOWLEDGE_EMBED``.

    False means no call to the embed door: the write keeps a NULL vector,
    and a search answers the lexical half with ``degraded``.
    """
    if not knowledge_org_allowed(organization_id):
        return False
    org = str(organization_id).strip().lower()
    orgs = knowledge_embed_orgs()
    return ALL_ORGS in orgs or org in orgs


async def require_knowledge_enabled(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> None:
    """404 unless the caller's organization may reach Knowledge.

    It reads ``user.organization_id`` and nothing else from the request.
    """
    if not knowledge_org_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
