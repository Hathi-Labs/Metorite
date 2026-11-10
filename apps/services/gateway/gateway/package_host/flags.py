"""The switch of the package host (WS-PKG, ``package_host.md`` §10).

``PACKAGE_HOST_ORGS`` is a comma list of organization ids. ``*`` opens every
org. Empty opens none. The default is OFF. While the list leaves an org out,
``GET /shell/manifest`` answers 404 for it. The reader runs on each call, so
a reload of the settings (SIGHUP) takes effect with no restart.

This module is the one reader of ``PACKAGE_HOST_ORGS``. It copies the shape
of ``routes/studio/flags.py``. Fence: ``tests/unit/test_package_host_flag.py``.
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def package_host_orgs() -> frozenset[str]:
    """The organization ids in ``PACKAGE_HOST_ORGS``. ``*`` stays in the set,
    and :func:`package_host_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "package_host_orgs", "") or ""))


def package_host_allowed(organization_id: str | None) -> bool:
    """True when ``PACKAGE_HOST_ORGS`` holds the org, or holds ``*``. Default
    OFF. The org comes from the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = package_host_orgs()
    return "*" in listed or str(organization_id).strip().lower() in listed
