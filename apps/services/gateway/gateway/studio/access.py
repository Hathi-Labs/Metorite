"""Who may READ a Studio package: the one answer for a caller with no request.

Spec: ``project-docs/specs/ai_studio.md`` §14 row E3. The action door
(``routes/studio/invoke.py``, ``_check``) reads a package for a member in two
ways: the owner, through ``packages_store.get_package``, and a viewer, through
the installed version that ``package_host/viewer.py`` finds. That viewer path is
open only for an org on ``PACKAGE_HOST_ORGS``. This module asks the same two
questions for a member who is not a caller of a request: the author of a
workflow that a record event may start. It adds no grant and no vocabulary.

**Fail closed.** A package id that is not a UUID, an unknown package, a package
of another org, a member that is not an address and any error answer False.
The session is the caller's tenant session, so the org is the bound org.
"""

from __future__ import annotations

import uuid
from typing import Any

from acb_common import get_logger

_log = get_logger("gateway.studio.access")


async def _holds_studio(member: str, org: str) -> bool:
    """True when *member* is an active member of *org* with ``feature:studio``.
    Same rule as ``routes/studio/invoke.py`` ``_holds_studio``. Fails closed."""
    from acb_auth.access import resolve_access_in_org

    if not org:
        return False
    try:
        access = await resolve_access_in_org(member, org)
    except Exception as exc:
        _log.warning("studio.access.feature_check_failed", error=type(exc).__name__)
        return False
    return bool(access.is_active and access.can_use_feature("studio"))


async def member_reads_package(
    db: Any, *, member: str, package_id: object, organization_id: str | None,
) -> bool:
    """True when *member* may read the package *package_id* in the org bound
    to *db*. *db* is a ``tenant_session``. Never raises."""
    from gateway.package_host import viewer
    from gateway.package_host.flags import package_host_allowed
    from gateway.studio import packages_store

    who = str(member or "").strip().lower()
    try:
        pid = str(uuid.UUID(str(package_id)))
        if "@" not in who:
            return False
        # The HTTP door holds feature:studio, and the run door holds it through
        # the same resolver. An author who is not active, or who lost the
        # feature, reads nothing here.
        if not await _holds_studio(who, str(organization_id or "")):
            return False
        # A savepoint: a failed read must not poison the caller's transaction.
        async with db.begin_nested():
            if await packages_store.get_package(db, who, pid) is not None:
                return True
            if not package_host_allowed(organization_id):
                return False
            return await viewer.installed_app(db, viewer=who, app_id=pid) is not None
    except Exception as exc:  # fail closed
        _log.warning("studio.access.read_check_failed", error=type(exc).__name__)
        return False
