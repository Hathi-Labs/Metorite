"""The switch of API connections in the chat (``api_connections.md`` §4,
API-1, D-API-8).

``API_CONNECTIONS_ORGS`` is a comma list of organization ids. ``*`` opens
every org. Empty opens none, and that is the default. The reader runs on each
call, so a reload of the settings (SIGHUP) takes effect with no restart.

API-1 declares the switch. The gateway routes (API-3) and the attach step of
the chat tools (API-4) read it. The template grammar does not need it, because
a template does no I/O. A value other than empty on a box is an
``enforcement-flip``, and the flip waits for the rung 1 demo.

The org comes from the session or the run, never from request input (R5). A
fault reads as off.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def api_connections_allowed(organization_id: str | None) -> bool:
    """True when ``API_CONNECTIONS_ORGS`` holds the org, or holds ``*``.
    Default OFF."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "api_connections_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed
