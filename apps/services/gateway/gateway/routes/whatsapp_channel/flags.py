"""The switches of the WhatsApp assistant channel (WS-47 WAC-1).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.1 and §7.

This module is the ONE reader of the three settings below. A later slice (the
webhook branch of WAC-2, the run of WAC-3) asks it too, and never reads the
settings itself.

* ``WHATSAPP_ASSISTANT_ENABLED`` — the kill switch, default OFF.
* ``WHATSAPP_ASSISTANT_ORGS`` — the organization ids that may link, with a
  comma between ids. Empty allows no organization. There is no ``*``.
* ``WHATSAPP_ASSISTANT_DISPLAY_NUMBER`` — the bot's number, 8 to 15 digits
  with the country code and no ``+``. Any other value reads as unset, and the
  module logs it once, so a typo on the box closes the channel rather than
  printing a broken ``wa.me`` link.

The organization always comes from the caller, who takes it from
``current_tenant()``. It never comes from request input (R5, R11).
"""

from __future__ import annotations

import functools
import re

from acb_common import get_logger, get_settings

_log = get_logger(__name__)

#: Digits only, with the country code. E.164 allows at most 15 digits.
_DISPLAY_NUMBER = re.compile(r"[0-9]{8,15}")


def assistant_enabled() -> bool:
    """True when the kill switch is on. Default OFF."""
    return get_settings().whatsapp_assistant_enabled is True


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The ids in *raw*, trimmed and in lower case. Cached on the raw value."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def assistant_orgs() -> frozenset[str]:
    """The organization ids that may link a phone. Empty means none."""
    return _parse_orgs(str(get_settings().whatsapp_assistant_orgs or ""))


def org_allowed(organization_id: str | None) -> bool:
    """True when the switch is on AND the organization is on the list."""
    if not assistant_enabled() or not organization_id:
        return False
    return str(organization_id).strip().lower() in assistant_orgs()


@functools.lru_cache(maxsize=16)
def _parse_display_number(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if not _DISPLAY_NUMBER.fullmatch(value):
        # The value is the bot's public number, not a secret, but the line
        # names only its length. A log line is no place to echo config.
        _log.warning("whatsapp_channel.display_number_invalid", length=len(value))
        return None
    return value


def display_number() -> str | None:
    """The bot's number for the ``wa.me`` link, or None when it is unset."""
    return _parse_display_number(
        str(get_settings().whatsapp_assistant_display_number or "")
    )


def wa_me_link(code: str, number: str) -> str:
    """The ``wa.me`` link that opens WhatsApp with the link message ready.

    *number* is a value :func:`display_number` returned. The code holds only
    letters and digits, so it needs no escape. The message text is
    ``Link me: <code>``.
    """
    return f"https://wa.me/{number}?text=Link%20me%3A%20{code}"
