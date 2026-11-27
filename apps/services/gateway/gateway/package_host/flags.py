"""The switch of the package host (WS-PKG, ``package_host.md`` §10).

``PACKAGE_HOST_ORGS`` is a comma list of organization ids. ``*`` opens every
org. Empty opens none. The default is OFF. While the list leaves an org out,
``GET /shell/manifest`` answers 404 for it. The reader runs on each call, so
a reload of the settings (SIGHUP) takes effect with no restart.

This module is the one reader of ``PACKAGE_HOST_ORGS``. It copies the shape
of ``routes/studio/flags.py``. Fence: ``tests/unit/test_package_host_flag.py``.

It is also the one reader of ``PACKAGE_EXPIRY_ENABLED`` (PKG-10b, default
OFF). That flag starts the daily expiry loop of ``expiry_loop.py``. A flip on
production is the owner's act only, because the tick deletes member data.

⚠️ ``PACKAGE_HOST_ORGS`` also selects the orgs where the expiry tick may
delete. A SIGHUP reload applies a change of the list at the next tick. So a
reload that widens the list to ``*`` widens the delete to every org. The
owner must treat a change of this list as part of the expiry decision.
``PACKAGE_EXPIRY_ENABLED`` does not reload. Only a restart changes it.
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


def package_expiry_enabled() -> bool:
    """True when ``PACKAGE_EXPIRY_ENABLED`` is on. Default OFF. The expiry
    loop reads it at the start and at each tick. A SIGHUP reload does not
    change it, so only a restart does."""
    return bool(getattr(get_settings(), "package_expiry_enabled", False))
