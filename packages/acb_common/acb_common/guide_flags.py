"""The switch of the guide (WS-OB GD-5a, ``assistant_guide.md`` §7).

``GUIDE_ORGS`` is a comma list of organization ids. ``*`` opens every org.
Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP) takes effect with no restart.

It lives in ``acb_common``, so every service reads one rule. The consumers
are ``GET /auth/me/shell`` in the gateway (``routes/admin/me.py``), which
returns the answer as ``guide``, and the orchestrator's guide tools (GD-7).
With ``guide`` false, the control plane shows no entry of the tour and
starts none (``src/lib/shell/guide.ts``).
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def guide_orgs() -> frozenset[str]:
    """The organization ids in ``GUIDE_ORGS``. ``*`` stays in the set, and
    :func:`guide_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "guide_orgs", "") or ""))


def guide_allowed(organization_id: str | None) -> bool:
    """True when ``GUIDE_ORGS`` holds the org, or holds ``*``. Default OFF.
    The org comes from the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = guide_orgs()
    return "*" in listed or str(organization_id).strip().lower() in listed
