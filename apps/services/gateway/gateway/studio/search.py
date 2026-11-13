"""Company search over the records of Studio apps (WS-STU C5a, ``ai_studio.md`` §14 C5).

The ``studio`` source of ``retrieval/sources.py`` calls :func:`search_records`.
It is a lexical search at query time. It has no index, no vector and no sweep,
so a record is found as soon as its write commits.

**What it reads.** An app opts in with ``shell.search``, a list of object
names in its manifest. The search reads ONLY those objects. It reads them for
the packages that the member OWNS, and for the apps that the member has
INSTALLED while ``PACKAGE_HOST_ORGS`` holds the org. This is the rule of the
action door (``routes/studio/invoke.py::_check``). A paused package of the
member is skipped, as the door refuses its actions.

**One reader.** Each read goes through ``objects_store.query_records`` with a
``Viewer`` of the session member. The store joins the row rules (C8a) into its
one statement. This module writes no SQL and builds no second predicate. A
record that the member cannot open is never returned. The fence is
``tests/unit/test_studio_row_rules.py::test_each_call_of_the_six_passes_a_viewer``.

**The words.** A filter is a list of AND conditions, so one query runs for
each text field. The fields are the ``text`` and ``email`` fields of the
object, at most :data:`MAX_FIELDS`, each with the ``contains`` op. A field that
the store refuses (not indexed, hidden from the member) is skipped.

**The snippet.** The title and the snippet come ONLY from those text fields
of the objects that ``shell.search`` names. For a viewer of an install they
come only from the fields that the installed version declares
(``InstalledApp.project_record``). ⚠️ Restricted fields (C9) are not built. A
manifest that names a sensitive text field in a searched object puts its value
in the snippet. Row rules and hidden keys of C8a still apply.

**Limits.** At most :data:`PER_PACKAGE` records for each package, and at most
*k* in all. A package that is too slow ends the search, with a log line, and
the hits so far are returned. The source has 1.5 s in the route, so
:data:`BUDGET_S` is lower.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common import get_logger
from acb_common.studio_access import Viewer
from acb_common.studio_query import QueryError

from gateway.db import tenant_session as _tenant_session
from gateway.studio import objects_store, packages_store

_log = get_logger("gateway.studio.search")

#: The most records that one package gives.
PER_PACKAGE = 5
#: The most packages that one search reads.
MAX_PACKAGES = 10
#: The most objects of one package that one search reads.
MAX_OBJECTS = 3
#: The most text fields of one object that one search asks.
MAX_FIELDS = 4
#: The most hits in all, whatever *k* is.
MAX_TOTAL = 20
#: Seconds for the whole read. The route gives the source 1.5 s.
BUDGET_S = 1.2
#: The text types that the search asks.
TEXT_TYPES = frozenset({"text", "email"})

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Found:
    """One record that matched. It carries the package and the object, so a
    follow-up read goes through the door."""

    package_id: str
    package_name: str
    object_name: str
    record_id: str
    title: str
    snippet: str
    updated_at: datetime | None


def _names(manifest: Any) -> list[str]:
    """The object names of ``shell.search`` of *manifest*. Fails closed."""
    shell = manifest.get("shell") if isinstance(manifest, dict) else None
    named = shell.get("search") if isinstance(shell, dict) else None
    if not isinstance(named, list):
        return []
    return [n for n in named if isinstance(n, str)][:MAX_OBJECTS]


def _flat(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


async def _candidates(db: Any, member: str, org: str | None) -> list[tuple[Any, bool]]:
    """The packages that *member* reads, as ``(package, is_viewer)``. The owner
    comes first and wins over an install of the same app, as in the door."""
    from gateway.package_host import resolve, viewer
    from gateway.package_host.flags import package_host_allowed

    out: list[tuple[Any, bool]] = []
    seen: set[str] = set()
    for pkg in await packages_store.list_packages(db, member):
        seen.add(str(pkg.id))
        if pkg.paused_at is None and _names(pkg.manifest):
            out.append((pkg, False))
    if package_host_allowed(org):
        for row in await resolve.visible_installs(db, viewer=member):
            if row.app_id in seen:
                continue
            app = viewer.installed([row], row.app_id)
            if app is not None and _names(app.manifest):
                out.append((app, True))
    return out[:MAX_PACKAGES]


async def _read_package(db: Any, package: Any, is_viewer: bool, who: Viewer,
                        words: str, room: int) -> list[Found]:
    """Up to *room* records of the searched objects of one package."""
    found: dict[tuple[str, str], Found] = {}
    name = str(getattr(package, "name", "") or "")
    for object_name in _names(package.manifest):
        if len(found) >= room:
            break
        if is_viewer and object_name not in package.object_names:
            continue
        try:
            obj = await objects_store.get_object_by_name(db, object_name, package_id=package.id)
        except objects_store.ObjectStoreError:
            continue
        if obj is None:
            continue
        if is_viewer:
            obj = package.project(obj)
        fields = [f for f in obj.fields if f.type in TEXT_TYPES and not f.many][:MAX_FIELDS]
        for field in fields:
            if len(found) >= room:
                break
            try:
                async with db.begin_nested():
                    page = await objects_store.query_records(
                        db, package_id=package.id, object_id=obj.id, viewer=who,
                        filter=[{"field": field.key, "op": "contains", "value": words}],
                        limit=room)
            except (QueryError, objects_store.ObjectStoreError):
                continue
            if page is None:
                continue
            for rec in page.records:
                if is_viewer:
                    rec = package.project_record(obj.name, rec)
                key = (obj.name, rec.id)
                if key in found or len(found) >= room:
                    continue
                parts = [_flat(rec.data.get(f.key)) for f in fields]
                parts = [p for p in parts if p]
                found[key] = Found(
                    package_id=str(package.id), package_name=name, object_name=obj.name,
                    record_id=str(rec.id), title=parts[0] if parts else "",
                    snippet=" · ".join(parts[1:]) if len(parts) > 1 else "",
                    updated_at=rec.updated_at)
    return list(found.values())


async def search_records(user: Any, q: str, k: int) -> list[Found]:
    """The records that match *q* in the searched objects of the packages that
    *user* reads. Flag off, no member or no words: no read at all."""
    from gateway.routes.studio.flags import search_allowed, studio_allowed

    org = getattr(user, "organization_id", None)
    member = str(getattr(user, "email", "") or "").strip().lower()
    words = _flat(q)
    total = max(0, min(int(k), MAX_TOTAL))
    if not (studio_allowed(org) and search_allowed(org)) or "@" not in member \
            or not words or not total:
        return []
    who = Viewer(member)
    deadline = time.monotonic() + BUDGET_S
    hits: list[Found] = []
    async with _tenant_session() as db:
        for package, is_viewer in await _candidates(db, member, org):
            room = min(PER_PACKAGE, total - len(hits))
            if room <= 0:
                break
            left = deadline - time.monotonic()
            try:
                if left <= 0:
                    raise TimeoutError
                hits += await asyncio.wait_for(
                    _read_package(db, package, is_viewer, who, words, room), left)
            except TimeoutError:
                _log.warning("studio.search.slow", package=str(package.id), found=len(hits))
                break
            except Exception as exc:  # one package never fails the search
                _log.warning("studio.search.package_failed", package=str(package.id),
                             error=type(exc).__name__)
                break
    return hits[:total]
