"""The switch of voice input in the chat (``projects_ai_chat.md`` §31, VOX-1).

``CHAT_VOICE_ORGS`` is a comma list of organization ids. ``*`` opens every
org. Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP) takes effect with no restart.

The gateway route ``/chat/transcribe`` answers 404 while it reads off, so the
composer draws no mic. Each clip draws AI credits through the Console, so a
value other than empty on a box is an ``enforcement-flip``.

The org comes from the session, never from request input (R5).
A fault reads as off.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def voice_allowed(organization_id: str | None) -> bool:
    """True when ``CHAT_VOICE_ORGS`` holds the org, or holds ``*``. Default OFF."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_voice_orgs", "") or "")
    except Exception:
        return False
    listed = _parse_orgs(raw)
    return "*" in listed or org in listed
