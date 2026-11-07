"""The switch of the focus-session log (WS-MDC CC-5a).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.3.

``FOCUS_LOG_ORGS`` is a comma list of organization ids. ``*`` opens every org.
Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP, ``docs/flag_reload.md``) takes effect with no
restart. :func:`focus_log_allowed` is the one reader of the flag. A flip on a
box is gate ``enforcement-flip``.
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def focus_log_orgs() -> frozenset[str]:
    """The organization ids in ``FOCUS_LOG_ORGS``. ``*`` stays in the set, and
    :func:`focus_log_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "focus_log_orgs", "") or ""))


def focus_log_allowed(organization_id: str | None) -> bool:
    """True when ``FOCUS_LOG_ORGS`` holds the org, or holds ``*``. Default OFF.
    The org comes from the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = focus_log_orgs()
    return "*" in listed or str(organization_id).strip().lower() in listed
