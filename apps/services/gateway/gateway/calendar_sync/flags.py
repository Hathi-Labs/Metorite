"""The flags of the calendar sync (WS-CAL CE-10, CE-11).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8 and §9.

``CALENDAR_SYNC_READ`` and ``CALENDAR_SYNC_WRITE`` hold org ids with a comma
between them, or ``*``. Empty means OFF. ``CALENDAR_SYNC_MEMBERS`` narrows both
to listed member addresses, for the test phase while the OAuth apps are in
Testing. An empty member list adds no limit.

This is the ONE reader of the three settings. The loop, the engine and the
routes ask here. A flag-off org opens no session and sends no request.
"""

from __future__ import annotations

import functools

from acb_common import get_settings

ALL_ORGS = "*"


@functools.lru_cache(maxsize=32)
def _parse(raw: str) -> frozenset[str]:
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def _named(raw: object, org: str | None) -> bool:
    if not org:
        return False  # no organization is false, also with `*`
    names = _parse(str(raw or ""))
    return ALL_ORGS in names or str(org).strip().lower() in names


def _member_ok(member: str | None) -> bool:
    members = _parse(str(get_settings().calendar_sync_members or ""))
    if not members:
        return True
    return (member or "").strip().lower() in members


def read_named() -> bool:
    """True when ``CALENDAR_SYNC_READ`` names any org. The loop starts on it."""
    return bool(_parse(str(get_settings().calendar_sync_read or "")))


def read_allowed(org: str | None, member: str | None = None) -> bool:
    """True when the read sync may run for the org, and for the member if given."""
    return _named(get_settings().calendar_sync_read, org) and (
        member is None or _member_ok(member))


def write_allowed(org: str | None, member: str | None = None) -> bool:
    """True when the write-out may run (CE-11, CE-13). Read must be on too."""
    return (read_allowed(org, member)
            and _named(get_settings().calendar_sync_write, org)
            and (member is None or _member_ok(member)))
