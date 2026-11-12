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
archived and an expired install, and an archived or a paused app (J3a), give
None, because the resolve shows none of them. When the member sees the app at two scopes, the
widest install wins (``widest_per_app``), as in the sidebar.

**The fields of the installed version (J2a fix round 1).** A publish only adds
fields to the object rows, so the rows can hold a field that a newer version
added. :meth:`InstalledApp.project` keeps the fields and the rules that the
installed version declares, and :meth:`InstalledApp.project_record` keeps
only their values. A record that a viewer reads names no member who wrote
it.

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
from dataclasses import dataclass, replace
from typing import Any

from acb_common.studio_rules import ConstraintError, Rule, parse_rules, stored_rules

from gateway.package_host import resolve
from gateway.studio.objects_store import Record, StudioObject

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
        return frozenset(self._objects())

    def _objects(self) -> dict[str, Mapping[str, Any]]:
        """Each object entry of the installed manifest, by its name."""
        found = self.manifest.get("objects")
        if not isinstance(found, list):
            return {}
        return {o["name"]: o for o in found
                if isinstance(o, Mapping) and isinstance(o.get("name"), str)}

    def field_keys(self, object_name: str) -> frozenset[str]:
        """The keys of the fields that the installed version declares for
        *object_name*. An object that the version does not declare has none."""
        entry = self._objects().get(object_name)
        fields = entry.get("fields") if entry is not None else None
        if not isinstance(fields, list):
            return frozenset()
        return frozenset(f["key"] for f in fields
                         if isinstance(f, Mapping) and isinstance(f.get("key"), str))

    def _rules(self, object_name: str) -> tuple[Rule, ...]:
        """The rules that the installed version declares for *object_name*,
        read as a publish reads them. A list that the authoring check refuses
        now is read with the store reader, which never fails."""
        entry = self._objects().get(object_name)
        raw = (entry.get("rules") if entry is not None else None) or []
        try:
            return parse_rules(raw)
        except ConstraintError:
            return stored_rules(raw)

    def project(self, obj: StudioObject) -> StudioObject:
        """The object row *obj* as the installed version declares it.

        A publish only adds a field, so the rows can hold a field of a newer
        version. A viewer sees only the fields of the installed version, and
        its rules. A published field never changes its spec (``objects_store``
        refuses it), so a kept field is the field that the version declares.
        Only its ``hot`` flag can differ."""
        keys = self.field_keys(obj.name)
        return replace(obj, fields=tuple(f for f in obj.fields if f.key in keys),
                       constraints=self._rules(obj.name))

    def project_record(self, object_name: str, record: Record) -> Record:
        """The record *record* as a viewer reads it: only the values of the
        fields that the installed version declares, and no address of the
        member who wrote it."""
        keys = self.field_keys(object_name)
        return replace(record, data={k: v for k, v in record.data.items() if k in keys},
                       created_by="", updated_by="")


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
