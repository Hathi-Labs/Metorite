"""The switch of AI Studio (STU-B1).

``STUDIO_ORGS`` is a comma list of organization ids. ``*`` opens every org.
Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP) takes effect with no restart.
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
