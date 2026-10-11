"""Who in an organization may open the CRM (WS-53 CRM-T5).

Spec: ``project-docs/specs/crm_platform.md`` §10 item 6 and §16 Q3. The admin
picks, and the default is admins only.

:func:`require_crm_access` is the third router dependency in ``core.py``. It
runs AFTER ``require_crm_enabled`` (the kill switch, a 404) and the
``feature:crm`` gate (a 403). The rules:

* A holder of ``admin:access:manage`` always passes. That is also the floor
  of ``PATCH /crm/settings``, so an admin can never lock themselves out.
* ``crm_settings.access = 'everyone'`` passes each member who holds
  ``feature:crm``.
* ``access = 'groups'`` passes a member of any group in
  ``crm_settings.access_groups``.
* No row reads as ``access = 'admins'``.
* Each other case is 403.

**The groups come from the Projects grant read model (D12).** The subjects are
``group:<slug>``, and the caller's groups come from
``routes/projects/core._MY_GROUPS_SQL``. This module imports that query and
does not copy it. A second copy would be a second answer to "which groups is
this member in".

**The tenant and the identity come from the server.** The one parameter is the
resolved ``UserContext``. The read runs in ``tenant_session()``, which the
request bound in ``acb_auth`` from the authenticated member (R5, R11). The
query also names ``user.organization_id``, so a session with row level
security switched off still reads only this organization's row.

This module is a leaf of the package, like ``flags.py``. It imports nothing
from its siblings, so ``core`` can import it and stay the leaf. Fence:
``tests/unit/test_crm_access.py`` and, against a real database,
``tests/unit/test_crm_access_r8.py``.
"""

from __future__ import annotations

from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException

# The shared seam, aliased for the same reason as in `core.py`: the tests
# patch `_tenant_session` on this module by name (`_crm_fakes.bind_db`).
from gateway.db import tenant_session as _tenant_session
from gateway.routes.projects.core import _MY_GROUPS_SQL
from sqlalchemy import text

#: The capability of an org admin. It always passes, and it is the floor of
#: every write that manages the CRM itself (settings, stages, lost reasons).
ADMIN_PERMISSION = "admin:access:manage"

#: The ``crm_settings.access`` vocabulary, mirrored from the CHECK in
#: migration 243 so a bad value is a 422 at the boundary and not a 500.
ACCESS_MODES: tuple[str, ...] = ("admins", "everyone", "groups")

#: What a missing row means (§16 Q3).
DEFAULT_ACCESS = "admins"

#: The settings row of the caller's organization.
SETTINGS_SQL = (
    "SELECT organization_id, mode, access, access_groups, updated_by, "
    "updated_at FROM crm_settings "
    "WHERE organization_id = CAST(:organization_id AS uuid)"
)


async def read_settings(db: Any, organization_id: str) -> Any:
    """The ``crm_settings`` row of *organization_id*, or None."""
    return (await db.execute(
        text(SETTINGS_SQL), {"organization_id": str(organization_id)},
    )).first()


async def my_groups(db: Any, email: str) -> set[str]:
    """The caller's ``group:<slug>`` subjects, through the Projects read model.

    The email is folded here because ``_MY_GROUPS_SQL`` compares it with
    ``lower(au.email)`` (R10).
    """
    rows = (await db.execute(
        text(_MY_GROUPS_SQL), {"email": (email or "").strip().lower()},
    )).fetchall()
    return {str(r.subject).lower() for r in rows}


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=(
            "Forbidden: an admin of your organization has not given you "
            "access to the CRM."
        ),
    )


async def require_crm_access(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> None:
    """403 unless the organization's CRM access rule admits the caller.

    It reads ``user`` and nothing else from the request.
    """
    if user.has_permission(ADMIN_PERMISSION):
        return
    if not user.organization_id or not user.has_permission("feature:crm"):
        raise _forbidden()
    async with _tenant_session() as db:
        row = await read_settings(db, user.organization_id)
        access = (getattr(row, "access", None) if row is not None else None)
        access = access or DEFAULT_ACCESS
        if access == "everyone":
            return
        if access == "groups":
            wanted = {str(g).lower() for g in (row.access_groups or [])}
            if wanted and wanted & await my_groups(db, user.email or ""):
                return
    raise _forbidden()
