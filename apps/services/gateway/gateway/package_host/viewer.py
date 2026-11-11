"""The read of an installed Studio app by a member who does not own it
(WS-STU J2a, ``ai_studio.md`` §14 J2a).

The owner reads a package through ``studio/packages_store.get_package``, and
that path does not change. Each other member reads an app only through an
install that the member sees. This module is that read. Its callers are
``GET /studio/packages/{id}`` (``routes/studio/packages.py``) and the one
check of the action door (``routes/studio/invoke.py::_check``). Each caller
reads the owner first, and calls this module only after the owner read
misses and while ``PACKAGE_HOST_ORGS`` holds the org.

**One rule of who sees an install.** :func:`installed_app` calls
:func:`gateway.package_host.resolve.visible_installs`, the read that the
sidebar uses (``INSTALLS_SQL`` and ``widest_per_app``). So a member reads an
app here exactly when the sidebar shows its install to that member. The
module holds no SQL and runs no second query of the installs.

**The installed version, never the draft.** The answer holds the manifest of
the version that the install runs. An install with no version (a draft) gives
None, so a draft never reaches a member who does not own it. A paused, an
archived and an expired install, and an archived app, give None, because the
resolve shows none of them. When the member sees the app at two scopes, the
widest install wins (``widest_per_app``), as in the sidebar.

**Records (C8 is not built).** A member who reads an app reads every record
of its objects, through the declarations of the installed version.
``ai_studio.md`` §19.7 records this gap. Row rules (C8) close it.

**Tenancy (R5).** The caller opens the session with ``tenant_session()``,
and the viewer is ``user.email``, never request input. The read folds the
address to lower case. A viewer with no ``@`` reads nothing.

Fences: ``tests/unit/test_studio_package_viewer.py`` (no database) and
``tests/unit/test_studio_package_viewer_r8.py`` (a real database, two orgs).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gateway.package_host import resolve

#: The access of a member who reads an app through an install.
VIEWER = "viewer"


@dataclass(frozen=True)
class InstalledApp:
    """One Studio app as a member who does not own it reads it: the app id,
    the installed version and the manifest of that version."""

    id: str
    version: int
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        """The name of the installed version, or an empty text."""
        found = self.manifest.get("name")
        return found if isinstance(found, str) else ""

    @property
    def description(self) -> str:
        """The purpose of the installed version, or an empty text."""
        found = self.manifest.get("purpose")
        return found if isinstance(found, str) else ""

    @property
    def object_names(self) -> frozenset[str]:
        """The names of the objects that the installed version declares."""
        found = self.manifest.get("objects")
        if not isinstance(found, list):
            return frozenset()
        return frozenset(o["name"] for o in found
                         if isinstance(o, Mapping) and isinstance(o.get("name"), str))


def _manifest(raw: Any) -> dict[str, Any] | None:
    """The manifest of an install row as a dict, or None (fail closed)."""
    if isinstance(raw, str | bytes):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return dict(raw) if isinstance(raw, Mapping) else None


def installed(rows: list[resolve.InstallRow], app_id: str) -> InstalledApp | None:
    """The app *app_id* among the installs *rows* that a member sees, or None.

    Pure. *rows* come from ``resolve.visible_installs``, one for each app. A
    draft (no version) and a manifest that is not a JSON object give None.
    """
    for row in rows:
        if row.app_id != app_id:
            continue
        manifest = _manifest(row.manifest)
        if row.version is None or manifest is None:
            return None
        return InstalledApp(id=row.app_id, version=row.version, manifest=manifest)
    return None


async def installed_app(db: Any, *, viewer: str, app_id: str) -> InstalledApp | None:
    """The installed version of *app_id* that *viewer* sees, or None.

    *db* is a session that the caller opened with ``tenant_session()``.
    *viewer* is the address of the session member, never request input. A
    viewer that is not an address reads nothing, and the call runs no
    statement, because an org install shows to every caller.
    """
    member = (viewer or "").strip().lower()
    if "@" not in member:
        return None
    return installed(await resolve.visible_installs(db, viewer=member), app_id)
