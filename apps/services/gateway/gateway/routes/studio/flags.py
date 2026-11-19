"""The switches of AI Studio (STU-B1 and STU-B2b-2b).

``STUDIO_ORGS`` is a comma list of organization ids. ``*`` opens every org.
Empty opens none. The default is OFF. The reader runs on each call, so a
reload of the settings (SIGHUP) takes effect with no restart.

``STUDIO_DOOR_OPS_ORGS`` (B2b-2b) has the same grammar and the same default.
It opens the door op of the action door: a class A ``<app>.<op>`` action of a
package runs the op of that app as the caller. :func:`door_ops_allowed` is
its one reader. A flip in production is the gate ``enforcement-flip``.

``STUDIO_SEARCH_ORGS`` (C5a) has the same grammar and the same default. It
opens the ``studio`` source of company search. :func:`search_allowed` is its
one reader.

``STUDIO_LOGIC_ORGS`` (B4a) has the same grammar and the same default. It
opens the logic module of the action door: a ``logic:<name>`` action runs a
first-party module. :func:`logic_allowed` is its one reader.

``STUDIO_CHAT_TOOLS_ORGS`` (TOF-1a) has the same grammar and the same
default. It opens the catalog source ``tool``: one ``tool:<name>`` row for
each chat tool of the CRM, Email, Projects and Calendar packs.
:func:`chat_tools_allowed` is its one reader. TOF-1b: it also gates the
skill load. ``orchestrator._tool_injection._chat_tools_open`` asks it once a
turn, and for an org that it leaves out, each skill with a ``tool:`` action
fails to load with ``chat_tools_off``.

``STUDIO_SKILL_OFFER_ORGS`` (TOF-1d) has the same grammar and the same
default. It opens the card "Save this as a skill?" in the chat. The shell read
serves it to the client. :func:`skill_offer_allowed` is its one reader.

``STUDIO_SKILL_TESTS_ORGS`` (TOF-3b F4b) has the same grammar and the same
default. It opens the route that runs the tests of a skill. Each test is a
real model turn, so a flip spends credits and stays with the owner.
:func:`skill_tests_allowed` is its one reader.

``STUDIO_SKILL_TESTS_CAP_CREDITS`` (TOF-3b F4b) is the credit cap of one test
run. It has no default value. :func:`skill_tests_cap` is its one reader, and
it answers None for an unset or a bad value. The runner then refuses with
``cap_unset``.
"""

from __future__ import annotations

import functools
from decimal import Decimal, InvalidOperation

from acb_common import get_settings


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def studio_orgs() -> frozenset[str]:
    """The organization ids in ``STUDIO_ORGS``. ``*`` stays in the set, and
    :func:`studio_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "studio_orgs", "") or ""))


def studio_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_ORGS`` holds the org, or holds ``*``. Default OFF.
    The org comes from the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = studio_orgs()
    return "*" in listed or str(organization_id).strip().lower() in listed


def door_ops_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_DOOR_OPS_ORGS`` holds the org, or holds ``*``
    (B2b-2b). Default OFF. The one reader of the setting. The org comes from
    the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_door_ops_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def logic_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_LOGIC_ORGS`` holds the org, or holds ``*`` (B4a).
    Default OFF. The one reader of the setting. The org comes from the
    session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_logic_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def chat_tools_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_CHAT_TOOLS_ORGS`` holds the org, or holds ``*``
    (TOF-1a). Default OFF. The one reader of the setting. The org comes from
    the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_chat_tools_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def skill_offer_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_SKILL_OFFER_ORGS`` holds the org, or holds ``*``
    (TOF-1d). Default OFF. The one reader of the setting. ``GET /auth/me/shell``
    serves it as ``skill_offer``. The org comes from the session, never from
    request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_skill_offer_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def skill_tests_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_SKILL_TESTS_ORGS`` holds the org, or holds ``*``
    (TOF-3b F4b). Default OFF. The one reader of the setting. The org comes
    from the session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_skill_tests_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def skill_tests_cap() -> Decimal | None:
    """The credit cap of one run of a skill's tests (TOF-3b F4b), or None.

    The one reader of ``STUDIO_SKILL_TESTS_CAP_CREDITS``. The setting has no
    default value. Empty, text that is no number, ``NaN``, ``Infinity`` and a
    value of 0 or less all answer None, so the runner refuses the run."""
    raw = str(getattr(get_settings(), "studio_skill_tests_cap_credits", "") or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def search_allowed(organization_id: str | None) -> bool:
    """True when ``STUDIO_SEARCH_ORGS`` holds the org, or holds ``*`` (C5a).
    Default OFF. The one reader of the setting. The org comes from the
    session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "studio_search_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed
