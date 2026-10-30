"""The switches of the core brief (WS-BRIEF BR-2a-1, ``core_brief.md`` §10).

This module is the ONE reader of the three ``BRIEF_*`` settings.

* ``BRIEF_ENABLED`` — the switch of the planner and the runs, default OFF.
* ``BRIEF_ORGS`` — the organization ids that get a brief. Empty allows none,
  and there is no ``*``.
* ``BRIEF_IN_APP`` — the switch of the in-app delivery (BR-2c-1), default
  OFF: ``GET /brief/today`` and the stored lines in the chat's first turn.

An org gets a brief only when the switch is on AND the org is on the list.
The org always comes from the worker's own discovery read, never from a
payload or from request input (R5, R11). The shape is ``reach/flags.py``.
"""

from __future__ import annotations

import functools

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def enabled() -> bool:
    """True when ``BRIEF_ENABLED`` is on. Default OFF."""
    return getattr(get_settings(), "brief_enabled", False) is True


def orgs() -> frozenset[str]:
    """The organization ids that get a brief. ``*`` is not a wildcard."""
    raw = str(getattr(get_settings(), "brief_orgs", "") or "")
    return frozenset(o for o in _parse(raw) if o != "*")


def org_allowed(organization_id: str | None) -> bool:
    """True when the switch is on AND the organization is on the list."""
    if not enabled() or not organization_id:
        return False
    return str(organization_id).strip().lower() in orgs()


def in_app(organization_id: str | None) -> bool:
    """True when the org gets a brief (:func:`org_allowed`) AND
    ``BRIEF_IN_APP`` is on (BR-2c-1). Default OFF. Off, ``GET /brief/today``
    answers 404, and the chat's first turn reads no stored run."""
    if getattr(get_settings(), "brief_in_app", False) is not True:
        return False
    return org_allowed(organization_id)
