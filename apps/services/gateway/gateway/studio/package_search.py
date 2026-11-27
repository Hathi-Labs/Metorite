"""The library check and the compose filter of the no-match path (SBX-1a).

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1 and §12.1 decisions 6 to
8. The one caller is ``orchestrator/tool_gap_path.py``, the hook of
``find_tools`` when the rank found no tool.

**The library check** (:func:`search_visible`) is the ONE search of the
Studio packages that a member can see. It reads two existing readers in the
caller's session, and no SQL of its own:

1. ``packages_store.list_summaries``: the packages that the member owns. It
   keeps a package that is not archived and has a live version.
2. ``resolve.visible_installs``: the installs that reach the member. That
   read drops a paused, archived or expired install, and a paused or archived
   app. This module keeps an install at a version only, so the private draft
   install of the owner never counts.

It never reads ``apps`` with no owner filter, so the private package of
another member never reaches a candidate (SBX-F23). Python ranks the text of
each hit by the stems that it shares with the need. Only the app id and a
count leave the function, never a word of a package.

**The compose filter** (:func:`callable_catalog`) is pure. It keeps a
``manifest`` row of the action catalog only when the member holds the
feature of its app, and a ``logic`` row only when the member holds
``studio``. It drops every other source (fail closed).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: The fewest content stems that a need and the text of a package share.
LIBRARY_MATCH_MIN = 2
#: The most hits of the library check.
LIBRARY_LIMIT = 5
#: The catalog sources that the compose check may keep (§12.1 decision 7).
CALLABLE_SOURCES = frozenset({"manifest", "logic"})


@dataclass(frozen=True)
class LibraryHit:
    """One package of the library check: its id and the count of shared stems."""

    app_id: str
    shared: int


def _manifest_text(manifest: Any) -> str:
    """The ``name`` and the ``purpose`` of a manifest, or ""."""
    if isinstance(manifest, str | bytes):
        try:
            manifest = json.loads(manifest)
        except ValueError:
            return ""
    if not isinstance(manifest, Mapping):
        return ""
    parts = [manifest.get(key) for key in ("name", "purpose", "description")]
    return " ".join(p for p in parts if isinstance(p, str))


async def search_visible(
    db: Any, *, viewer: str, query: str, limit: int = LIBRARY_LIMIT,
) -> list[LibraryHit]:
    """The packages that *viewer* can see and that *query* names, best first.

    *db* is a session that the caller opened with ``tenant_session()``.
    *viewer* is the verified member of the run, never request input. A hit
    shares :data:`LIBRARY_MATCH_MIN` or more stems with *query*, and a tie
    breaks by the app id. At most *limit* hits."""
    from acb_skills.tool_search import shared_stems

    from gateway.package_host import resolve
    from gateway.studio import packages_store

    member = (viewer or "").strip().lower()
    if "@" not in member or not str(query or "").strip() or limit <= 0:
        return []
    texts: dict[str, str] = {}
    for package in await packages_store.list_summaries(db, member):
        if package.status == "archived" or package.live_version is None:
            continue
        texts.setdefault(package.id, f"{package.name} {package.description}")
    for install in await resolve.visible_installs(db, viewer=member):
        if install.version is None:
            continue
        texts.setdefault(install.app_id, _manifest_text(install.manifest))
    ranked = sorted(
        (-hits, app_id) for app_id, body in texts.items()
        if (hits := shared_stems(query, body)) >= LIBRARY_MATCH_MIN)
    return [LibraryHit(app_id=app_id, shared=-neg) for neg, app_id in ranked[:limit]]


def _feature_of(app_id: object) -> str | None:
    """The bare feature slug of the app *app_id*, or None."""
    from acb_common.app_manifests import app

    found = app(app_id)
    if found is None:
        return None
    return str(found.feature or "").removeprefix("feature:") or None


def callable_catalog(
    features: Iterable[str] | None, rows: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The rows of *rows* (``routes/studio/actions.catalog_rows``) that a
    member with *features* (``in_app_path.member_features``) can call. Pure.

    A ``manifest`` row stays when *features* holds the feature of its app. A
    ``logic`` row stays when *features* holds ``studio``. A ``workflow``,
    ``integration``, ``mcp``, ``tool`` or ``package`` row never stays, and
    neither does a row of an unknown source. None for *features* keeps no
    row."""
    held = frozenset(str(f) for f in (features or ()))
    if not held:
        return []
    kept: list[Mapping[str, Any]] = []
    for row in rows:
        source = row.get("source") if isinstance(row, Mapping) else None
        if source not in CALLABLE_SOURCES:
            continue
        if source == "manifest":
            feature = _feature_of(row.get("app"))
            if feature is None or feature not in held:
                continue
        elif "studio" not in held:
            continue
        kept.append(row)
    return kept
