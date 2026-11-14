"""Who may READ a Studio package: the one answer for a caller with no request.

Spec: ``project-docs/specs/ai_studio.md`` §14 row E3. The action door
(``routes/studio/invoke.py``, ``_check``) reads a package for a member in two
ways: the owner, through ``packages_store.get_package``, and a viewer, through
the installed version that ``package_host/viewer.py`` finds. That viewer path is
open only for an org on ``PACKAGE_HOST_ORGS``. This module asks the same two
questions for a member who is not a caller of a request: the author of a
workflow that a record event may start. It adds no grant and no vocabulary.

:func:`member_owns_package` asks a narrower question for the timer scan of
STU-E9a: does the member OWN the package. The scan reads past the row rules,
and only the owner passes them.

**Fail closed.** A package id that is not a UUID, an unknown package, a package
of another org, a member that is not an address and any error answer False.
The session is the caller's tenant session, so the org is the bound org.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from acb_common import get_logger

_log = get_logger("gateway.studio.access")


async def _studio_state(member: str, org: str, *, strict: bool) -> bool | None:
    """True or False when *member* is or is not an active member of *org* with
    ``feature:studio``. Same rule as ``routes/studio/invoke.py``
    ``_holds_studio``. A fault answers None. With *strict*, the resolver
    raises on a read error too, so a read error is a fault and never a no."""
    from acb_auth.access import resolve_access_in_org

    if not org:
        return False
    try:
        if strict:
            access = await resolve_access_in_org(member, org, strict=True)
        else:
            access = await resolve_access_in_org(member, org)
    except Exception as exc:
        _log.warning("studio.access.feature_check_failed", error=type(exc).__name__)
        return None
    return bool(access.is_active and access.can_use_feature("studio"))


async def _holds_studio(member: str, org: str) -> bool:
    """True when *member* is an active member of *org* with ``feature:studio``.
    Fails closed: a fault answers False."""
    return await _studio_state(member, org, strict=False) is True


async def members_holding_studio(members: Iterable[object], org: str) -> dict[str, bool]:
    """One answer for each DISTINCT member, asked outside any session.

    ``_holds_studio`` opens its own ``tenant_session``. A caller that holds a
    session of its own would hold two pool connections, and a burst of such
    callers can wait on each other until the pool times out. So a caller asks
    here BEFORE it opens its session, once for each distinct address. The key
    is the trimmed lower-case address. Fails closed, never raises.
    """
    held: dict[str, bool] = {}
    for member in members:
        who = str(member or "").strip().lower()
        if who not in held:
            held[who] = "@" in who and await _holds_studio(who, str(org or ""))
    return held


async def members_studio_state(members: Iterable[object], org: str) -> dict[str, bool | None]:
    """The answer of :func:`members_holding_studio`, with a fault kept apart
    (STU-E9a). True or False is an answer, and None is a fault. The resolver
    runs in strict mode, so a read error is a fault and never a no. The timer
    scan disarms a trigger on a no, and keeps the arm on a fault. Asked
    outside any session, once for each distinct address. Never raises.
    """
    state: dict[str, bool | None] = {}
    for member in members:
        who = str(member or "").strip().lower()
        if who not in state:
            state[who] = (await _studio_state(who, str(org or ""), strict=True)
                          if "@" in who else False)
    return state


async def member_owns_package(
    db: Any, *, member: str, package_id: object, studio_held: bool | None,
) -> bool | None:
    """True when *member* OWNS the package *package_id* in the org bound to
    *db*, and holds ``feature:studio`` (*studio_held*, from
    :func:`members_studio_state`). False is a true no. None is a fault: the
    feature answer was a fault, or the package read failed. Never raises, and
    opens no session.

    The timer scan (STU-E9a) reads the record ids and the due values of each
    record of an object, past the row rules of C8a. Only the owner passes each
    rule, so only the owner's workflow may hear a breach. A viewer through an
    install reads the package and still starts no breach run. The scan
    disarms a trigger on False, and keeps its arm on None."""
    from gateway.studio import packages_store

    who = str(member or "").strip().lower()
    try:
        pid = str(uuid.UUID(str(package_id)))
    except (ValueError, TypeError, AttributeError):
        return False
    if "@" not in who:
        return False
    if studio_held is None:
        return None
    if not studio_held:
        return False
    try:
        # A savepoint: a failed read must not poison the caller's transaction.
        async with db.begin_nested():
            return await packages_store.get_package(db, who, pid) is not None
    except Exception as exc:  # a fault, never a no
        _log.warning("studio.access.owner_check_failed", error=type(exc).__name__)
        return None


async def member_reads_package(
    db: Any, *, member: str, package_id: object, organization_id: str | None,
    studio_held: bool | None = None,
) -> bool:
    """True when *member* may read the package *package_id* in the org bound
    to *db*. *db* is a ``tenant_session``. Never raises.

    *studio_held* is the answer of :func:`members_holding_studio` for *member*.
    A caller that holds *db* passes it, so this call opens no second session.
    ``None`` asks here, and that is for a caller that holds no session."""
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
        if studio_held is None:
            studio_held = await _holds_studio(who, str(organization_id or ""))
        if not studio_held:
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
