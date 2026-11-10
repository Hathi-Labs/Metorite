"""The switches and keys of web push (WS-66 PN-1).

This module is the ONE reader of the four web push settings
(``calendar_events_reminders.md`` §10.2 item 2):

* ``WEB_PUSH_ENABLED`` — the switch of the ``/push`` routes and the sender,
  default OFF. Off, each route answers 404 and nothing sends.
* ``VAPID_PUBLIC_KEY`` — the ``applicationServerKey`` of the browser: the
  base64url of the uncompressed P-256 point, 65 bytes.
* ``VAPID_PRIVATE_KEY`` — the base64url of the raw 32-byte private scalar.
* ``VAPID_SUBJECT`` — a ``mailto:`` or ``https:`` contact for the push
  services (RFC 8292 §2.1).

The keys are "configured" only when all three are set, the private key reads
as a P-256 key, and its public point is the configured public key. A pair
that does not match signs a JWT that every push service refuses, so a
mismatch reads as "not configured" and logs ``push.vapid_mismatch``.
"""

from __future__ import annotations

import functools

from acb_common import get_logger, get_settings

_log = get_logger(__name__)


def enabled() -> bool:
    """True when ``WEB_PUSH_ENABLED`` is on. Default OFF."""
    return get_settings().web_push_enabled is True


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def prompt_orgs() -> frozenset[str]:
    """The organization ids in ``PUSH_PROMPT_ORGS`` (OB-8). ``*`` stays in
    the set, and :func:`prompt_allowed` reads it as every org."""
    return _parse_orgs(str(getattr(get_settings(), "push_prompt_orgs", "") or ""))


def prompt_allowed(organization_id: str | None) -> bool:
    """True when ``PUSH_PROMPT_ORGS`` holds the org, or holds ``*``. Default
    OFF. The caller checks :func:`enabled` too. The org comes from the
    session, never from request input (R5)."""
    if not organization_id:
        return False
    listed = prompt_orgs()
    return "*" in listed or str(organization_id).strip().lower() in listed


def _clean(value: object) -> str:
    """A key as the settings hold it, with no space and no ``=`` padding."""
    return "".join(str(value or "").split()).rstrip("=")


def public_key() -> str | None:
    """The configured public key, or None when the keys are not configured."""
    return _vapid_pair(_clean(get_settings().vapid_private_key),
                       _clean(get_settings().vapid_public_key))[1]


def subject() -> str | None:
    """The VAPID subject, or None when it is not a ``mailto:`` or ``https:``
    address."""
    value = str(get_settings().vapid_subject or "").strip()
    if value.startswith("mailto:") and "@" in value:
        return value
    if value.startswith("https://") and len(value) > len("https://"):
        return value
    return None


def vapid() -> object | None:
    """The ``py_vapid`` key that signs each push, or None when the keys are
    not configured. The private key never leaves this module and the
    sender."""
    return _vapid_pair(_clean(get_settings().vapid_private_key),
                       _clean(get_settings().vapid_public_key))[0]


def configured() -> bool:
    """True when the key pair matches and the subject is valid."""
    return vapid() is not None and subject() is not None


@functools.lru_cache(maxsize=4)
def _vapid_pair(private: str, public: str) -> tuple[object | None, str | None]:
    """The signing key and the public key, when the pair reads and matches.

    Cached by the two strings, so a mismatch logs once per value."""
    if not private or not public:
        return None, None
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid02, b64urlencode

        key = Vapid02.from_string(private_key=private)
        point = key.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    except Exception as exc:  # a key that does not read is not configured
        _log.error("push.vapid_unreadable", error=type(exc).__name__)
        return None, None
    if b64urlencode(point) != public:
        _log.error("push.vapid_mismatch")
        return None, None
    return key, public
