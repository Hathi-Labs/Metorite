"""The flag of the new Agents view of Projects (WS-MCP AMR-1,
``agent_coordination.md`` §5.12).

``AGENT_LENSES_ORGS`` opens the Board, Graph and List lenses and the phone view
of the Agents canvas. It opens no data: the view draws the map read of SW-102,
which ``AGENT_MAP_ORGS`` still guards. Off, the canvas draws the map of SW-103.
AMR-4: on, that read also answers ``detail=summary`` and ``session=``, which
give no row that the map does not give by the same reach rule.
"""

from __future__ import annotations

from acb_common import get_settings


def agent_lenses_allowed(organization_id: str | None) -> bool:
    """True when ``AGENT_LENSES_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the flag. The org comes from the session, never from
    request input (R5). It reads ``get_settings()`` on each call, so a flag
    reload applies a change with no restart (``docs/flag_reload.md``).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "agent_lenses_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed
