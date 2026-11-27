"""The direct proof of a Studio write (WS-STU STU-B2c-1).

Spec: ``project-docs/specs/ai_studio.md`` §20.4 and the §14 B2c-1 row.

**What it proves.** A member's own Save, edit or move in an app screen is the
approval of that write. The workbench BFF is the one party that sees the
member's session, so it signs a header, ``X-Studio-Direct``. The action door
checks the header before it reads the body. A call is direct only when the
proof holds.

**Why a signed header.** Every caller of the gateway holds the same bearer,
``GATEWAY_INTERNAL_TOKEN``, and the orchestrator runs inside the gateway
process. So two tokens cannot tell the callers apart, and a route that only the
BFF calls proves nothing. A second key, ``STUDIO_DIRECT_KEY``, is the proof. The
workbench ``.env.local`` and the gateway ``.env`` hold it. No skill, no
workflow and no orchestrator code reads it. The fence is
``tests/unit/test_studio_direct_proof.py``.

**The header.** ``v1.<unix seconds>.<hex HMAC-SHA256>``. The HMAC covers the
version, the time, the lowercase member email, the method and the gateway path.
The window is :data:`WINDOW_S` seconds in the past and :data:`SKEW_S` seconds
in the future.

**The key rules** are those of ``agent_signing``. A key under 32 bytes, or
equal to ``GATEWAY_INTERNAL_TOKEN`` or ``LITELLM_MASTER_KEY``, counts as unset.
With no usable key, no proof verifies, so no write is direct.

⚠️ Every failure answers False, and the caller must not tell them apart.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time

from acb_common import get_settings

from acb_auth.agent_signing import MIN_KEY_BYTES, _platform_secrets

__all__ = ["HEADER", "SKEW_S", "VERSION", "WINDOW_S", "header_value", "is_direct_proof",
           "sign", "usable_key", "verify"]

#: The header that carries the proof.
HEADER = "X-Studio-Direct"

#: The version tag of the signed string.
VERSION = "v1"

#: How old a proof may be, in seconds.
WINDOW_S = 60

#: How far in the future a proof may be, in seconds (clock skew).
SKEW_S = 5

_FORMAT = re.compile(r"v1\.([0-9]{1,12})\.([0-9a-f]{64})")


def usable_key() -> str | None:
    """The key from the settings, or None when it counts as unset."""
    key = str(getattr(get_settings(), "studio_direct_key", "") or "").strip()
    if len(key.encode("utf-8")) >= MIN_KEY_BYTES and key not in _platform_secrets():
        return key
    return None


def _message(ts: int, email: str, method: str, path: str) -> bytes:
    return f"{VERSION}\n{ts}\n{email.strip().lower()}\n{method.upper()}\n{path}".encode()


def _mac(key: str, ts: int, email: str, method: str, path: str) -> str:
    return hmac.new(key.encode("utf-8"), _message(ts, email, method, path),
                    hashlib.sha256).hexdigest()


def sign(key: str, email: str, method: str, path: str, *, now: float | None = None) -> str:
    """The header value for one call. The workbench has the twin of this
    function in ``lib/gateway.ts``, and a shared vector holds the two equal."""
    ts = int(time.time() if now is None else now)
    return f"{VERSION}.{ts}.{_mac(key, ts, email, method, path)}"


def verify(value: str | None, key: str | None, email: str | None, method: str, path: str,
           *, now: float | None = None) -> bool:
    """True when *value* is a fresh proof, made under *key*, for this member,
    method and path. False for every other case."""
    if not value or not key or not email:
        return False
    found = _FORMAT.fullmatch(value)
    if found is None:
        return False
    ts = int(found.group(1))
    age = (time.time() if now is None else now) - ts
    if age > WINDOW_S or age < -SKEW_S:
        return False
    return hmac.compare_digest(found.group(2), _mac(key, ts, email, method, path))


def is_direct_proof(value: str | None, email: str | None, method: str, path: str) -> bool:
    """:func:`verify` under the key of the settings. False when no key is usable."""
    return verify(value, usable_key(), email, method, path)


def header_value(headers: object) -> str | None:
    """The proof in *headers* (a Starlette mapping), or None."""
    getter = getattr(headers, "get", None)
    found = getter(HEADER) if callable(getter) else None
    return found if isinstance(found, str) else None
