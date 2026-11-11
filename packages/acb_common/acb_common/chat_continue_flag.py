"""The switch of "continue a held chat in a fresh chat" (``one_assistant_context.md`` §9).

``CHAT_CONTINUE_ORGS`` is a comma list of organization ids. ``*`` opens every
org. Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP) takes effect with no restart.

It lives in ``acb_common`` so one rule serves two services. The gateway route
``POST /chat/sessions/{id}/continue`` answers 404 while it reads off. The tool
layer (``acb_skills.egress``) sends the offer event of the pill only while it
reads on, so a chat of an org that is off draws no new element.

The org comes from the session or the run, never from request input (R5).
A fault reads as off.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def continue_allowed(organization_id: str | None) -> bool:
    """True when ``CHAT_CONTINUE_ORGS`` holds the org, or holds ``*``. Default OFF."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_continue_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed
