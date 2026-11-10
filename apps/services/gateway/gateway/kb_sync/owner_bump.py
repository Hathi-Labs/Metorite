"""The My Day bump of a failed sync (WS-61 KB-9b slice 2).

``source_sync_failed`` is a card for the member who connected a Drive. When a
run closes failed, or a source goes ``needs_reconnect``, the engine and the
discovery call :func:`bump_owner` so the card shows at the next read, and not
after the 600 seconds of the read cache.

**Never inside a session.** The caller closes its tenant session first. A bump
inside the session would hold the lock of the source row (KC-3h, ``close()``
takes ``FOR NO KEY UPDATE``) while it waits for Redis. **A bump failure never
fails the run:** :func:`bump_owner` catches everything.

Fence: ``tests/unit/test_kb_my_day.py::TestTheSyncBump``.
"""

from __future__ import annotations

from acb_common import get_logger

_log = get_logger(__name__)


async def bump_owner(org: str, owner_email: object) -> None:
    """Bump the My Day read cache of the connector. Never raises."""
    email = str(owner_email or "").strip()
    if not email:
        return
    try:
        # Imported here: the shell cache imports the routes, and this module
        # is imported by the engine at start-up.
        from gateway.routes.shell import cache

        await cache.bump_member_safe(org, email)
    except Exception as exc:
        _log.warning("knowledge.sync_bump_skipped", organization_id=org,
                     error=type(exc).__name__)


__all__ = ["bump_owner"]
