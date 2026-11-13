"""The switches of AI Studio (STU-B1 and STU-B2b-2b).

``STUDIO_ORGS`` is a comma list of organization ids. ``*`` opens every org.
Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP) takes effect with no restart.

``STUDIO_DOOR_OPS_ORGS`` (B2b-2b) has the same grammar and the same default.
It opens the door op of the action door: a class A ``<app>.<op>`` action of a
package runs the op of that app as the caller. :func:`door_ops_allowed` is
its one reader. A flip in production is the gate ``enforcement-flip``.

``STUDIO_SEARCH_ORGS`` (C5a) has the same grammar and the same default. It
opens the ``studio`` source of company search. :func:`search_allowed` is its
one reader.
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def studio_orgs() -> frozenset[str]:
    """The organization ids in ``STUDIO_ORGS``. ``*`` stays in the set, and
    :func:`studio_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "studio_orgs", "") or ""))


def studio_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_ORGS`` holds the org, or holds ``*``. Default OFF.
    The org comes from the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = studio_orgs()
    return "*" in listed or str(organization_id).strip().lower() in listed


def door_ops_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_DOOR_OPS_ORGS`` holds the org, or holds ``*``
    (B2b-2b). Default OFF. The one reader of the setting. The org comes from
    the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_door_ops_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def search_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_SEARCH_ORGS`` holds the org, or holds ``*`` (C5a).
    Default OFF. The one reader of the setting. The org comes from the
    session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_search_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed
