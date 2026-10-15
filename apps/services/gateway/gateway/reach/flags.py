"""The switches of Reach (WS-59 RE-1a, ``proactive_reach.md``).

This module is the ONE reader of the five ``REACH_*`` settings.

* ``REACH_ENABLED`` — the switch of the loop and the routes, default OFF.
* ``REACH_ORGS`` — the organization ids that may reach. Empty allows none,
  and there is no ``*``.
* ``REACH_MEMBERS`` — the demo allowlist of member emails. Empty allows
  nobody.
* ``REACH_DAILY_CAP`` — the most interrupts for one member in one local day.
* ``REACH_BRIEF_TEMPLATE`` — an approved Meta template for a brief outside
  the 24-hour window. Empty sends no template.

The organization and the member always come from a session or from the loop's
own list, never from request input (R5, R11).
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def enabled() -> bool:
    """True when ``REACH_ENABLED`` is on. Default OFF."""
    return get_settings().reach_enabled is True


def orgs() -> frozenset[str]:
    """The organization ids that may reach. ``*`` is not a wildcard."""
    return frozenset(o for o in _parse(str(get_settings().reach_orgs or "")) if o != "*")


def members() -> frozenset[str]:
    """The member emails of the demo allowlist, in lower case."""
    return frozenset(m for m in _parse(str(get_settings().reach_members or "")) if m != "*")


def org_allowed(organization_id: str | None) -> bool:
    """True when the switch is on AND the organization is on the list."""
    if not enabled() or not organization_id:
        return False
    return str(organization_id).strip().lower() in orgs()


def member_allowed(organization_id: str | None, email: str | None) -> bool:
    """True when the org may reach AND the member is on the allowlist."""
    if not org_allowed(organization_id) or not email:
        return False
    return email.strip().lower() in members()


def daily_cap() -> int:
    """The interrupt cap of one member and one local day. Never below 0."""
    try:
        return max(0, int(get_settings().reach_daily_cap))
    except (TypeError, ValueError):
        return 0


def brief_template() -> str | None:
    """The approved template name for a brief, or None."""
    value = str(get_settings().reach_brief_template or "").strip()
    return value or None
