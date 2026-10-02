"""Transport · signing — the two signed values of the Outlook connect flow.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.1 (EM-T1a),
items 1, 5 and 7.

Two values cross the internet and come back to us, and each must prove that
we made it:

* **The OAuth state.** The authorize leg puts it in the consent URL, and the
  provider hands it back to the callback. It carries the organization, the
  member, the provider, ``redirect_after``, ``import_months`` and an expiry of
  ten minutes. It replaces an in-process dict that a restart emptied, that a
  second worker could not see, and that never expired. ``import_months`` is
  the import range of a new mailbox (EM-T6a, D-EM-11). A state signed before
  EM-T6a has no such claim, and the verifier reads it as 1.
* **The Graph webhook signature.** ``_ensure_subscription`` writes
  ``?org=<uuid>&sig=<hmac>`` into the ``notificationUrl``. The webhook reads
  the organization from that URL, so a caller who cannot sign cannot name one.

⚠️ **One secret, two purposes.** Both use ``gateway_session_secret`` (spec
risk R-1). The MAC input STARTS with a purpose string, so a value signed for
one purpose never verifies as the other, and no other token that this secret
signs (a member proof, for one) can pass as either.

⚠️ **A public secret is no secret.** ``acb_auth.member_proof._usable`` refuses
an empty value and every value in ``PUBLIC_DEFAULT_SECRETS``. The signer
raises :class:`SigningUnavailable` and the verifier answers "no". This module
reuses that predicate and does not copy it, so the list has one owner.

Do not reuse ``routes/oauth.py::_sign_state``. It has another format and it
falls back to a default secret. Fences: ``tests/unit/test_email_oauth_state.py``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any

from acb_auth.member_proof import _usable
from acb_common import get_settings
from email_ingestion.import_window import DEFAULT_IMPORT_MONTHS, is_import_months

__all__ = [
    "OAUTH_STATE_PURPOSE",
    "OAUTH_STATE_TTL_SECONDS",
    "WEBHOOK_PURPOSE",
    "SigningUnavailable",
    "sign_oauth_state",
    "sign_webhook_org",
    "verify_oauth_state",
    "verify_webhook_org",
]

#: The purpose of a state. It is the first part of the MAC input.
OAUTH_STATE_PURPOSE = "email-oauth-state:v1"

#: The purpose of a webhook signature. It is the first part of the MAC input.
WEBHOOK_PURPOSE = "email-graph-webhook:v1"

#: A state lives ten minutes. That is enough for a consent screen and an MFA
#: prompt, and short enough that a leaked state is soon worth nothing.
OAUTH_STATE_TTL_SECONDS = 600

#: A state larger than this is not one of ours. The bound keeps a hostile
#: value from costing a large base64 and JSON decode.
_MAX_STATE_CHARS = 4096

_STATE_VERSION = 1


class SigningUnavailable(RuntimeError):
    """The secret is empty or public, so nothing can be signed."""


def _secret() -> str:
    raw = getattr(get_settings(), "gateway_session_secret", "")
    if not _usable(raw):
        raise SigningUnavailable(
            "GATEWAY_SESSION_SECRET is empty or a public default, so the "
            "email connect flow cannot sign its state"
        )
    return raw.strip()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: str, purpose: str, body: str) -> str:
    msg = f"{purpose}\n{body}".encode()
    return _b64(hmac.new(secret.encode(), msg, hashlib.sha256).digest())


def _canonical_org(org: str) -> str:
    """The one spelling of an organization id. Raises ValueError if not a UUID."""
    return str(uuid.UUID(str(org)))


# ── The OAuth state ─────────────────────────────────────────────────────────


def sign_oauth_state(
    *,
    org: str,
    member: str,
    provider: str,
    redirect_after: str = "",
    import_months: int = DEFAULT_IMPORT_MONTHS,
    now: float | None = None,
) -> str:
    """Sign a state for one connect attempt.

    Raises:
        SigningUnavailable: the secret is empty or public.
        ValueError: ``org`` is not a UUID, ``member`` is empty,
            ``import_months`` is not an integer from 0 to 6, or the state
            would be longer than the verifier accepts.
    """
    who = (member or "").strip().lower()
    if not who:
        raise ValueError("a state needs a member")
    if not is_import_months(import_months):
        raise ValueError("import_months is an integer from 0 to 6")
    issued = time.time() if now is None else now
    payload = {
        "v": _STATE_VERSION,
        "nonce": secrets.token_urlsafe(16),
        "org": _canonical_org(org),
        "member": who,
        "provider": provider,
        "redirect_after": redirect_after or "",
        "import_months": import_months,
        "exp": int(issued) + OAUTH_STATE_TTL_SECONDS,
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    token = f"{body}.{_mac(_secret(), OAUTH_STATE_PURPOSE, body)}"
    if len(token) > _MAX_STATE_CHARS:
        # The verifier refuses a state this long, so signing one would send
        # the member through consent to a certain `invalid_state`.
        raise ValueError("the state would be too long to verify")
    return token


def verify_oauth_state(token: str | None, *, now: float | None = None) -> dict[str, Any] | None:
    """The payload of a valid state, or ``None``.

    ``None`` covers every failure: a bad MAC, a wrong purpose, an expired
    state, a bad shape, and a secret that is empty or public. The caller
    answers each one the same way, so this function does not say which.

    A state with no ``import_months`` claim was signed before EM-T6a, and the
    payload then carries the default of 1. A claim that is not an integer
    from 0 to 6 is a bad shape.
    """
    if not token or len(token) > _MAX_STATE_CHARS or token.count(".") != 1:
        return None
    try:
        secret = _secret()
    except SigningUnavailable:
        return None
    body, tag = token.split(".")
    if not hmac.compare_digest(
        _mac(secret, OAUTH_STATE_PURPOSE, body).encode(), tag.encode()
    ):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(payload, dict) or payload.get("v") != _STATE_VERSION:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool):
        return None
    if exp <= (time.time() if now is None else now):
        return None
    for key in ("org", "member", "provider"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            return None
    if not isinstance(payload.get("redirect_after", ""), str):
        return None
    months = payload.setdefault("import_months", DEFAULT_IMPORT_MONTHS)
    if not is_import_months(months):
        return None
    try:
        payload["org"] = _canonical_org(payload["org"])
    except ValueError:
        return None
    return payload


# ── The Graph webhook signature ─────────────────────────────────────────────


def sign_webhook_org(org: str) -> str:
    """The ``sig`` that goes beside ``org`` in a Graph ``notificationUrl``.

    Raises:
        SigningUnavailable: the secret is empty or public.
        ValueError: ``org`` is not a UUID.
    """
    return _mac(_secret(), WEBHOOK_PURPOSE, _canonical_org(org))


def verify_webhook_org(org: str | None, sig: str | None) -> str | None:
    """The canonical organization id when ``sig`` signs ``org``, else ``None``."""
    if not org or not sig or len(sig) > 128:
        return None
    try:
        canonical = _canonical_org(org)
        secret = _secret()
    except (ValueError, SigningUnavailable):
        return None
    if not hmac.compare_digest(
        _mac(secret, WEBHOOK_PURPOSE, canonical).encode(), sig.encode("utf-8", "replace")
    ):
        return None
    return canonical
