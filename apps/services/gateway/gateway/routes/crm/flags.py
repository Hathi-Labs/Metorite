"""The CRM kill switch (WS-53 CRM-0).

Spec: ``project-docs/specs/crm_platform.md``, the CRM-0 row.

This module is the ONE reader of the two settings below. Nothing else in the
gateway reads them.

* ``CRM_ENABLED`` — the kill switch, default OFF.
* ``CRM_ORGS`` — the organization ids that may reach ``/crm``, with a comma
  between ids. ``*`` allows each organization with a bound tenant. An empty
  list allows no organization.
* ``CRM_MIRROR`` — the kill switch of the Zoho connection routes (WS-53
  CRM-Z2), default OFF. :func:`require_crm_mirror` is their dependency. It
  adds to ``CRM_ENABLED`` and never replaces it.
* ``CRM_MIRROR_ORGS`` — the organization ids that may use the Zoho mirror (WS-53
  CRM-L2). The grammar is the one of ``CRM_ORGS``: ids with a comma between
  them, or ``*``. An empty list allows no organization, so the mirror fails
  closed. Since the CRM is live for each organization, this list keeps the
  Zoho mirror to the organizations that the owner names.
* ``CRM_FIELD_STUDIO_ORGS`` — the organization ids that may use the field
  studio of a Native CRM (WS-53 CRM-C4a). The grammar is the one of
  ``CRM_ORGS``. An empty list allows no organization, so each studio route
  answers 404. :func:`require_field_studio` is their dependency.

:func:`require_crm_enabled` is the router dependency in ``core.py``. It runs
BEFORE ``require_feature_router("crm")``, so an organization outside the list
gets 404 and never 403: a dark feature does not announce itself.

The organization comes from ``UserContext.organization_id``, which
``acb_auth`` resolves on the server from the authenticated member. It never
comes from a header, the query or the body (R5, R11).

This module is a leaf. It imports nothing from its siblings, so ``core`` can
import it and stay the leaf of the package.

🔴 A flip on a box is gate ``enforcement-flip``. The switch stays OFF for
every organization until CRM-T4 merges. Fence:
``tests/unit/test_crm_kill_switch.py``.
"""

from __future__ import annotations

import functools
from typing import Annotated

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException

#: The value in ``CRM_ORGS`` that allows each organization with a bound tenant.
ALL_ORGS = "*"


def crm_enabled() -> bool:
    """True when the kill switch is on. Default OFF."""
    return get_settings().crm_enabled is True


def crm_mirror_enabled() -> bool:
    """True when the mirror switch is on. Default OFF."""
    return get_settings().crm_mirror is True


def mirror_sync_enabled() -> bool:
    """True when the mirror sync loop may start: the CRM AND the mirror are on."""
    return crm_enabled() and crm_mirror_enabled()


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The ids in *raw*, trimmed and in lower case. Cached on the raw value."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def crm_orgs() -> frozenset[str]:
    """The organization ids that may reach ``/crm``. Empty means none."""
    return _parse_orgs(str(get_settings().crm_orgs or ""))


def crm_org_allowed(organization_id: str | None) -> bool:
    """True when the switch is on AND the organization is on the list.

    No organization is false, also with ``*``. A caller with no bound tenant
    is not a member of an organization, so it has no CRM to reach.
    """
    if not crm_enabled() or not organization_id:
        return False
    org = str(organization_id).strip().lower()
    if not org:
        return False
    orgs = crm_orgs()
    return ALL_ORGS in orgs or org in orgs


def crm_mirror_orgs() -> frozenset[str]:
    """The organization ids that may use the mirror. Empty means none."""
    return _parse_orgs(str(get_settings().crm_mirror_orgs or ""))


def crm_mirror_allowed(organization_id: str | None) -> bool:
    """True when the mirror may serve this organization.

    It needs the mirror switch, the CRM switch with the CRM list
    (:func:`crm_org_allowed`) AND a place on ``CRM_MIRROR_ORGS``, or ``*``
    there. An empty list allows no organization.
    """
    if not crm_mirror_enabled() or not crm_org_allowed(organization_id):
        return False
    org = str(organization_id).strip().lower()
    orgs = crm_mirror_orgs()
    return ALL_ORGS in orgs or org in orgs


def crm_field_studio_orgs() -> frozenset[str]:
    """The organization ids that may use the field studio. Empty means none."""
    return _parse_orgs(str(get_settings().crm_field_studio_orgs or ""))


def field_studio_allowed(organization_id: str | None) -> bool:
    """True when the field studio may serve this organization.

    It needs the CRM switch with the CRM list (:func:`crm_org_allowed`) AND a
    place on ``CRM_FIELD_STUDIO_ORGS``, or ``*`` there.
    """
    if not crm_org_allowed(organization_id):
        return False
    org = str(organization_id).strip().lower()
    orgs = crm_field_studio_orgs()
    return ALL_ORGS in orgs or org in orgs


async def require_crm_enabled(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> None:
    """404 unless the caller's organization may reach the CRM.

    It reads ``user.organization_id`` and nothing else from the request.
    """
    if not crm_org_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")


async def require_field_studio(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> None:
    """404 unless the field studio may serve the caller's organization.

    It reads ``user.organization_id`` and nothing else from the request.
    """
    if not field_studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")


async def require_crm_mirror(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> None:
    """404 unless the mirror may serve the caller's organization.

    It reads ``user.organization_id`` and nothing else from the request.
    """
    if not crm_mirror_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")
