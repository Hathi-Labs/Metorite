"""A test helper of WS-STU J2b-1 (path A): approve the live version of a
package, so a test can reach the org install of PKG-8b.

Since J2b-1 the org install route installs only an approved live version.
Else it asks for the approval and answers 202 with no install. A test whose
subject is not the gate (a pause, an uninstall, a lifetime read) calls
:func:`approve_live` first, through the one writer of
``app_versions.review_status``. The gate itself is fenced in
``test_studio_package_approval_routes.py`` and its R8 twin.
"""

from __future__ import annotations

from acb_common.db import tenant_session
from gateway.studio import packages_store


async def approve_live(org: str, owner: str, pid: str, approver: str) -> None:
    """The owner *owner* asks for the approval of the live version of *pid*,
    and *approver* approves it, each in its own session of *org*.

    A package that *owner* does not own, a draft, and a version that is not
    ``auto`` are left as they are, so the route answers as before. The
    approver must not be the owner or the publisher (PKG-Q10)."""
    async with tenant_session(org) as db:
        found = await packages_store.get_package(db, owner, pid)
        live = None if found is None else found.live_version
        if live is None:
            return
        current = await packages_store.get_version(db, owner, pid, live)
        if current is None or current.review_status != "auto":
            return
        await packages_store.request_org_approval(db, owner, pid, live)
    async with tenant_session(org) as db:
        await packages_store.approve_version(db, approver, pid, live, can_publish=True)
