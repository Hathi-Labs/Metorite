"""Who reaches a Studio package: the owner, or a viewer of an install.

One resolver for the routes that read a package for a member and run no
action. It is the two reads of the action door (``routes/studio/invoke.py``
``_check``), in the same order. The saved views (``routes/studio/views.py``)
and the file serve route (``routes/studio/files.py``) both use it, so no
route keeps its own copy of the door order.

**Order.** The package of the member first, through
``packages_store.get_package``. Then, while ``PACKAGE_HOST_ORGS`` holds the
org, the installed version through ``package_host/viewer.installed_app``.
A member with neither gets ``None``. Each caller turns that into its own 404.

**The paused check.** The owner of a paused package is still the owner, so
the answer is the package, and ``Reach.paused`` is true. The action door
refuses a paused package with 423. A caller that runs like the door acts on
``paused``. A caller that did not refuse before (the views) may ignore it.
A paused INSTALL is not in the answer at all: ``installed_app`` returns None
for it, as for a draft, an archived and an expired install.

The org and the member come from the session of the caller, never from
request input. The module runs one read for the owner, and one more for a
viewer. It opens no session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gateway.package_host import viewer as _viewer
from gateway.package_host.flags import package_host_allowed
from gateway.studio import packages_store

__all__ = ["Reach", "reach_package"]


@dataclass(frozen=True)
class Reach:
    """The answer of :func:`reach_package`. Exactly one of ``owned`` and
    ``app`` is set."""

    #: The package, when the member owns it.
    owned: packages_store.Package | None = None
    #: The installed version, when the member reads it through an install.
    app: _viewer.InstalledApp | None = None

    @property
    def paused(self) -> bool:
        """True when the member owns the package and it is paused."""
        return self.owned is not None and self.owned.paused_at is not None

    @property
    def package(self) -> packages_store.Package | _viewer.InstalledApp:
        """The package that the member reads."""
        found = self.owned if self.owned is not None else self.app
        assert found is not None
        return found


async def reach_package(db: Any, member: str, org: str | None, pid: str) -> Reach | None:
    """The :class:`Reach` of *member* on the package *pid*, or None.

    *db* is a session that the caller opened with ``tenant_session()``.
    *member* and *org* come from the session of the caller. A viewer reaches
    the package only while ``PACKAGE_HOST_ORGS`` holds *org*."""
    owned = await packages_store.get_package(db, member, pid)
    if owned is not None:
        return Reach(owned=owned)
    if package_host_allowed(org):
        app = await _viewer.installed_app(db, viewer=member, app_id=pid)
        if app is not None:
            return Reach(app=app)
    return None
