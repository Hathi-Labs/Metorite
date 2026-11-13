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

**A viewer reads only what the door lets it read.** For an installed app, an
object is searched only when the INSTALLED manifest declares a read action on
it: ``object:<name>.list`` or ``object:<name>.get``. Without one, the door
refuses the read (``not_declared``), so the search shows no record of it.

**Limits.** At most :data:`PER_PACKAGE` records for each package, at most
*k* in all, and at most :data:`MAX_PACKAGES` packages. The owner gets at most
:data:`MAX_PACKAGES` minus :data:`INSTALLED_SHARE` of them when installs
exist, so an owner of many apps still reaches the installs.

**Time.** One clock of :data:`BUDGET_S` starts BEFORE the packages load.
It is lower than the 1.5 s that the route gives a source. The route gives a
source ``min(1.5 s, time left)``. When less than :data:`BUDGET_S` is left,
the route cancels the source and the source answers nothing. This module
cannot read that time, because the ``Source`` contract does not pass it.

**A slow or failed read never cancels a statement from outside.** Each
statement passes the time that is left to ``query_records`` (``budget_s``),
and the store sets the ``statement_timeout`` of that statement. A timeout
ends in a SAVEPOINT, so the session stays usable and its commit succeeds.
Any error of one statement or one package is logged (the type only), that
read is skipped, and the hits found so far come back. One package never
fails the search.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common import get_logger
from acb_common.studio_access import Viewer
from acb_common.studio_manifest import parse_uses
from acb_common.studio_query import TIMEOUT as QUERY_TIMEOUT

from gateway.db import tenant_session as _tenant_session
from gateway.studio import objects_store, packages_store

_log = get_logger("gateway.studio.search")

#: The most records that one package gives.
PER_PACKAGE = 5
#: The most packages that one search reads.
MAX_PACKAGES = 10
#: Places kept for installed apps when the member has any. The owner takes the rest.
INSTALLED_SHARE = 4
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


def _reads_declared(manifest: Any, object_name: str) -> bool:
    """True when *manifest* declares a read action on *object_name*:
    ``object:<name>.list`` or ``.get``. The door refuses a read with none
    (``not_declared``). It uses the one grammar, ``parse_uses``."""
    actions = manifest.get("actions") if isinstance(manifest, dict) else None
    if not isinstance(actions, list):
        return False
    for entry in actions:
        ref = parse_uses(entry.get("uses")) if isinstance(entry, dict) else None
        if ref is not None and ref.source == "object" and ref.object == object_name \
                and ref.op in ("list", "get"):
            return True
    return False


async def _candidates(db: Any, member: str, org: str | None) -> list[tuple[Any, bool]]:
    """The packages that *member* reads, as ``(package, is_viewer)``, at most
    :data:`MAX_PACKAGES`. The owner wins over an install of the same app, as
    in the door. Installs keep up to :data:`INSTALLED_SHARE` places."""
    from gateway.package_host import resolve, viewer
    from gateway.package_host.flags import package_host_allowed

    owned: list[tuple[Any, bool]] = []
    seen: set[str] = set()
    for pkg in await packages_store.list_packages(db, member):
        seen.add(str(pkg.id))
        if pkg.paused_at is None and _names(pkg.manifest):
            owned.append((pkg, False))
    installed: list[tuple[Any, bool]] = []
    if package_host_allowed(org):
        for row in await resolve.visible_installs(db, viewer=member):
            if row.app_id in seen:
                continue
            app = viewer.installed([row], row.app_id)
            if app is not None and _names(app.manifest):
                installed.append((app, True))
    take_owned = min(len(owned), MAX_PACKAGES - min(len(installed), INSTALLED_SHARE))
    take_installed = min(len(installed), MAX_PACKAGES - take_owned)
    return owned[:take_owned] + installed[:take_installed]


async def _read_package(db: Any, package: Any, is_viewer: bool, who: Viewer,
                        words: str, room: int, deadline: float) -> list[Found]:
    """Up to *room* records of the searched objects of one package. Each
    statement runs in a SAVEPOINT with the time left as its deadline. An error
    of one statement skips it, and the records found so far stay."""
    found: dict[tuple[str, str], Found] = {}
    name = str(getattr(package, "name", "") or "")
    for object_name in _names(package.manifest):
        if len(found) >= room or deadline - time.monotonic() <= 0:
            break
        if is_viewer and (object_name not in package.object_names
                          or not _reads_declared(package.manifest, object_name)):
            continue
        try:
            async with db.begin_nested():
                obj = await objects_store.get_object_by_name(
                    db, object_name, package_id=package.id)
        except Exception as exc:  # skip the object, keep the hits
            _log.warning("studio.search.object_failed", error=type(exc).__name__)
            continue
        if obj is None:
            continue
        if is_viewer:
            obj = package.project(obj)
        fields = [f for f in obj.fields if f.type in TEXT_TYPES and not f.many][:MAX_FIELDS]
        for field in fields:
            left = deadline - time.monotonic()
            if len(found) >= room or left <= 0:
                break
            try:
                async with db.begin_nested():
                    page = await objects_store.query_records(
                        db, package_id=package.id, object_id=obj.id, viewer=who,
                        filter=[{"field": field.key, "op": "contains", "value": words}],
                        limit=room, budget_s=left)
            except Exception as exc:  # a timeout, a refusal or a database error
                code = getattr(exc, "code", None)
                _log.warning("studio.search.read_failed", error=type(exc).__name__,
                             timeout=code == QUERY_TIMEOUT)
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
        try:
            async with db.begin_nested():
                candidates = await _candidates(db, member, org)
        except Exception as exc:
            _log.warning("studio.search.candidates_failed", error=type(exc).__name__)
            return []
        for package, is_viewer in candidates:
            room = min(PER_PACKAGE, total - len(hits))
            if room <= 0 or deadline - time.monotonic() <= 0:
                break
            try:
                hits += await _read_package(db, package, is_viewer, who, words, room, deadline)
            except Exception as exc:  # one package never fails the search
                _log.warning("studio.search.package_failed", package=str(package.id),
                             error=type(exc).__name__)
                continue
    return hits[:total]
