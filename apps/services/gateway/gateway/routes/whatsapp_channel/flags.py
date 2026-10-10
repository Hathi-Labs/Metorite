"""The switches of the WhatsApp assistant channel (WS-47 WAC-1).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.1 and §7.

This module is the ONE reader of the six settings below. The webhook branch
of WAC-2 and the run of WAC-3 ask it, and never read the settings themselves.

* ``WHATSAPP_ASSISTANT_ENABLED`` — the kill switch, default OFF.
* ``WHATSAPP_ASSISTANT_ORGS`` — the organization ids that may link, with a
  comma between ids. Empty allows no organization. There is no ``*``.
* ``WHATSAPP_ASSISTANT_DISPLAY_NUMBER`` — the bot's number, 8 to 15 digits
  with the country code and no ``+``. Any other value reads as unset, and the
  module logs it once, so a typo on the box closes the channel rather than
  printing a broken ``wa.me`` link.
* ``WHATSAPP_ASSISTANT_PHONE_NUMBER_ID`` — Meta's id for the bot number
  (WAC-2). The webhook sends the batch for this id to the bot path. A value
  that is not ASCII digits reads as unset.
* ``WHATSAPP_ASSISTANT_ACCESS_TOKEN`` — the System User token the bot replies
  with (WAC-2). 🔴 A secret: nothing here logs it, and no function returns it
  except :func:`bot_credentials`, whose one caller builds the provider.
* ``WHATSAPP_ASSISTANT_NATIVE_UI`` — the WhatsApp run profile and its native
  UI (WAC-10a), default OFF.

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


def native_ui_enabled() -> bool:
    """True when a bot run uses the WhatsApp profile (WAC-10a, §12).

    ``WHATSAPP_ASSISTANT_NATIVE_UI``, default OFF. It adds to the kill switch
    and never replaces it: with the channel off, no run starts at all.
    """
    return get_settings().whatsapp_assistant_native_ui is True


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


#: Meta's phone number ids are ASCII digits only (``cloud_api.is_phone_number_id``).
_PHONE_NUMBER_ID = re.compile(r"[0-9]{1,32}")


@functools.lru_cache(maxsize=16)
def _parse_phone_number_id(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if not _PHONE_NUMBER_ID.fullmatch(value):
        _log.warning("whatsapp_channel.phone_number_id_invalid", length=len(value))
        return None
    return value


def bot_phone_number_id() -> str | None:
    """Meta's id for the bot number, or None when it is unset (WAC-2).

    The webhook compares each group of a batch with this id. With None, no
    group is the bot's, so every group stays on the WS-20 path.
    """
    return _parse_phone_number_id(
        str(get_settings().whatsapp_assistant_phone_number_id or "")
    )


def bot_credentials() -> dict[str, str] | None:
    """The credentials dict for the bot's Cloud API provider, or None.

    None when the id or the token is unset. The dict holds the token, so its
    one caller passes it straight to ``build_provider`` and logs none of it.
    """
    number_id = bot_phone_number_id()
    token = str(get_settings().whatsapp_assistant_access_token or "").strip()
    if number_id is None or not token:
        return None
    return {"access_token": token, "phone_number_id": number_id}
