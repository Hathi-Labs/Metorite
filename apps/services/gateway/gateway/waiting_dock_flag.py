"""The flag of the waiting dock of the chat (WD-1, ``projects_ai_chat.md``
§24.11, D234).

``WAITING_DOCK_ORGS`` draws each question card that waits on the member in a
dock above the message box, outside the scrolling thread. An approval card
stays in the thread, and the dock shows one pointer row for it (D9). It opens
no data and changes no route: the shell read serves the answer as
``waiting_dock``, and the chat draws the dock only then. Off, the chat draws as
before.
"""

from __future__ import annotations

from acb_common import get_settings


def waiting_dock_allowed(organization_id: str | None) -> bool:
    """True when ``WAITING_DOCK_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the flag. The org comes from the session, never from
    request input (R5). It reads ``get_settings()`` on each call, so a flag
    reload applies a change with no restart (``docs/flag_reload.md``).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "waiting_dock_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed
