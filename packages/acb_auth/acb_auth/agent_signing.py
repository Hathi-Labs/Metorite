"""The signed request from the agent edge to the gateway (WS-55 EA-3a, EA3-1).

Spec: ``project-docs/specs/external_agent_access.md`` §10.1 · decision D97.

**What this module is.** The ONE canonical string and the ONE HMAC that the
two units share. The edge (EA-3b) signs a gateway request with
:func:`signed_headers`. The gateway checks it in branch 0 of
``acb_auth.deps.get_current_user`` with :func:`verify`. A rule that only one
side kept would let the two sides disagree, so each rule lives here once. The
module does no I/O.

**Why an HMAC and no bearer.** The internal bearer lets its holder act as any
member (branch 1a). The edge holds no bearer and sends no member header. The
HMAC is its only credential, and the gateway reads the member from the
connection row, never from a header.

**The signed string.** Eight lines, joined by ``\\n``:

    v1
    <timestamp>
    <organization uuid>
    <connection uuid>
    <X-Actor-Via value>
    <METHOD, upper case>
    <path>?<sorted query>
    <hex SHA-256 of the body>

The signature is the lower-case hex HMAC-SHA256 of the UTF-8 bytes of that
string, with the UTF-8 bytes of the key. The header is ``v1,<kid>,<hex>``. The
``kid`` is the first 8 hex characters of the SHA-256 of the key.

**A write adds a 9th line (EA-6a, EA3-28).** A request that is not a GET
carries ``X-Agent-Nonce``, 16 random bytes in base64url with no padding, and
its value is the 9th line. A GET has no 9th line, so its signed string is the
eight lines above, byte for byte. Branch 0 keeps each nonce for
:data:`NONCE_TTL_S` seconds, so a replay of a write gets 401.

**The keys.** ``agent_edge_signing_key`` and ``agent_edge_signing_key_previous``.
A key under 32 bytes, or equal to ``GATEWAY_INTERNAL_TOKEN`` or to
``LITELLM_MASTER_KEY``, counts as unset. With no usable key, branch 0 refuses
every request. The edge signs with the current key only.

Fence: ``tests/unit/test_mcp_edge_signing.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qsl, urlencode

from acb_common import get_settings

#: The version of the signed string and of the header.
VERSION = "v1"

#: The four headers of a signed request (spec §10.1).
HEADER_CONNECTION = "X-Agent-Connection"
HEADER_TIMESTAMP = "X-Agent-Timestamp"
HEADER_SIGNATURE = "X-Agent-Signature"
HEADER_ACTOR_VIA = "X-Actor-Via"

#: The nonce of a write (EA-6a, EA3-28). A GET never carries it.
HEADER_NONCE = "X-Agent-Nonce"

#: Any header whose name starts with this sends the request to branch 0.
AGENT_HEADER_PREFIX = "x-agent-"

#: An ``X-Actor-Via`` value that starts with this sends it to branch 0 too.
MCP_ACTOR_PREFIX = "mcp:"

#: The timestamp may be this many seconds before or after the gateway clock.
MAX_SKEW_S = 60

#: The shortest usable key, in bytes.
MIN_KEY_BYTES = 32

#: The SHA-256 of zero bytes: the body hash of every GET.
EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()

#: The random bytes of one nonce. In base64url with no padding they are 22
#: characters.
NONCE_BYTES = 16

#: How long branch 0 keeps a nonce: twice the skew, so a replay inside the
#: window of its timestamp always finds it (EA3-28).
NONCE_TTL_S = 2 * MAX_SKEW_S

#: The namespace of a nonce key in the tenant Redis wrapper (EA3-28).
NONCE_NAMESPACE = "agent_nonce"

_KID = re.compile(r"[0-9a-f]{8}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{22}")
_HEX = re.compile(r"[0-9a-f]{64}")
_DIGITS = re.compile(r"[0-9]{1,12}")


# ── Keys ────────────────────────────────────────────────────────────────────


def key_id(key: str) -> str:
    """The ``kid`` of *key*: the first 8 hex characters of its SHA-256."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _platform_secrets() -> set[str]:
    """The two platform secrets that a signing key may never equal.

    Read from the environment first and the settings second, the order
    ``acb_auth.deps`` reads them in. An empty value is not a secret.
    """
    settings = get_settings()
    values = {
        os.getenv("GATEWAY_INTERNAL_TOKEN", ""),
        str(getattr(settings, "gateway_internal_token", "") or ""),
        os.getenv("LITELLM_MASTER_KEY", ""),
        str(getattr(settings, "litellm_master_key", "") or ""),
    }
    return {v.strip() for v in values if v and v.strip()}


def _usable(key: str, forbidden: set[str]) -> bool:
    return len(key.encode("utf-8")) >= MIN_KEY_BYTES and key not in forbidden


def usable_keys() -> dict[str, str]:
    """The usable keys by ``kid``: the current key, then the previous one.

    A key that is short, empty or equal to a platform secret is left out, so
    it counts as unset. An empty result means branch 0 refuses everything.
    """
    settings = get_settings()
    forbidden = _platform_secrets()
    keys: dict[str, str] = {}
    for raw in (
        getattr(settings, "agent_edge_signing_key", ""),
        getattr(settings, "agent_edge_signing_key_previous", ""),
    ):
        key = str(raw or "").strip()
        if key and _usable(key, forbidden):
            keys.setdefault(key_id(key), key)
    return keys


def signing_key() -> str | None:
    """The key the edge signs with: the current key when it is usable."""
    key = str(getattr(get_settings(), "agent_edge_signing_key", "") or "").strip()
    if key and _usable(key, _platform_secrets()):
        return key
    return None


# ── The canonical string ────────────────────────────────────────────────────


def query_pairs(query: str | bytes) -> list[tuple[str, str]] | None:
    """The pairs of a raw query string, or ``None`` when branch 0 must refuse it.

    Strict on purpose. The raw query must be ASCII, and each ``%XX`` sequence
    must decode as UTF-8, so ``%FF`` and ``%FE`` cannot both collapse to one
    replacement character under one signature. A repeated key gives ``None``
    too: Starlette keeps the LAST value of a key and the signed string sorts
    every pair, so a replay with the pairs reordered would read another value
    under the same signature. ``a=`` stays a pair (``keep_blank_values``).
    """
    try:
        text = query.decode("ascii") if isinstance(query, bytes) else query
        if not text.isascii():
            return None
        pairs = parse_qsl(text, keep_blank_values=True, encoding="utf-8",
                          errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        return None
    return pairs


def canonical_target(path: str, query: str | Iterable[tuple[str, str]] = "") -> str:
    """``<path>?<sorted query>``, or the path alone when there is no pair.

    The pairs are sorted by key and then by value, and encoded with
    ``urlencode``. A query string goes through :func:`query_pairs` first, and
    one that it refuses raises ``ValueError``, so the edge cannot sign it.
    """
    if isinstance(query, str):
        parsed = query_pairs(query)
        if parsed is None:
            raise ValueError("the query has a repeated key or a bad encoding")
        pairs = parsed
    else:
        pairs = [(str(k), str(v)) for k, v in query]
    if not pairs:
        return path
    return f"{path}?{urlencode(sorted(pairs))}"


def canonical_string(
    *,
    timestamp: str,
    organization_id: str,
    connection_id: str,
    actor_via: str,
    method: str,
    path: str,
    query: str | Iterable[tuple[str, str]] = "",
    body: bytes = b"",
    nonce: str | None = None,
) -> str:
    """The ONE signed string of spec §10.1. Both sides call this function.

    A GET takes no *nonce* and has eight lines. Any other method takes a
    well-formed *nonce* as the 9th line (EA3-28). A mismatch raises
    ``ValueError``, so neither side can sign a write without a nonce.
    """
    lines = [
        VERSION,
        timestamp,
        organization_id,
        connection_id,
        actor_via,
        method.upper(),
        canonical_target(path, query),
        hashlib.sha256(body).hexdigest(),
    ]
    if method.upper() == "GET":
        if nonce is not None:
            raise ValueError("a GET carries no nonce")
    else:
        if not nonce_is_well_formed(nonce):
            raise ValueError("a write needs a well-formed nonce")
        lines.append(str(nonce))
    return "\n".join(lines)


def new_nonce() -> str:
    """A fresh nonce: :data:`NONCE_BYTES` random bytes in base64url."""
    return secrets.token_urlsafe(NONCE_BYTES)


def nonce_is_well_formed(value: str | None) -> bool:
    """True when *value* is 22 base64url characters (EA3-28)."""
    return isinstance(value, str) and _NONCE.fullmatch(value) is not None


def signature(key: str, canonical: str) -> str:
    """The lower-case hex HMAC-SHA256 of *canonical* with *key*."""
    return hmac.new(
        key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


def signature_header(key: str, canonical: str) -> str:
    """The ``X-Agent-Signature`` value: ``v1,<kid>,<hex>``."""
    return f"{VERSION},{key_id(key)},{signature(key, canonical)}"


def parse_signature_header(value: str | None) -> tuple[str, str] | None:
    """``(kid, hex)`` of a well-formed ``v1`` header, else ``None``."""
    parts = (value or "").split(",")
    if len(parts) != 3 or parts[0] != VERSION:
        return None
    kid, digest = parts[1], parts[2]
    if not _KID.fullmatch(kid) or not _HEX.fullmatch(digest):
        return None
    return kid, digest


def verify(header_value: str | None, canonical: str, keys: Mapping[str, str]) -> bool:
    """True when the header names a usable key and its HMAC matches.

    The ``kid`` picks the key. An unknown ``kid`` fails. The compare is
    ``hmac.compare_digest``.
    """
    parsed = parse_signature_header(header_value)
    if parsed is None:
        return False
    kid, digest = parsed
    key = keys.get(kid)
    if key is None:
        return False
    return hmac.compare_digest(signature(key, canonical), digest)


def timestamp_is_fresh(value: str | None, *, now: float | None = None) -> bool:
    """True when *value* is whole Unix seconds within :data:`MAX_SKEW_S`."""
    if not value or not _DIGITS.fullmatch(value):
        return False
    current = time.time() if now is None else now
    return abs(current - int(value)) <= MAX_SKEW_S


# ── The edge side ───────────────────────────────────────────────────────────


def signed_headers(
    key: str,
    *,
    organization_id: str,
    connection_id: str,
    actor_via: str,
    method: str,
    path: str,
    query: str | Iterable[tuple[str, str]] = "",
    body: bytes = b"",
    now: float | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """The headers of one signed gateway request (spec §10.1).

    The four signed headers, and for a write the fifth, ``X-Agent-Nonce``
    (EA3-28). A write with no *nonce* gets a fresh one. The edge (EA-3b)
    calls this with :func:`signing_key`. It sends no ``Authorization``
    header and no ``X-User-Email``.
    """
    timestamp = str(int(time.time() if now is None else now))
    write = method.upper() != "GET"
    if write and nonce is None:
        nonce = new_nonce()
    canonical = canonical_string(
        timestamp=timestamp,
        organization_id=organization_id,
        connection_id=connection_id,
        actor_via=actor_via,
        method=method,
        path=path,
        query=query,
        body=body,
        nonce=nonce,
    )
    headers = {
        HEADER_CONNECTION: f"{organization_id}.{connection_id}",
        HEADER_TIMESTAMP: timestamp,
        HEADER_ACTOR_VIA: actor_via,
        HEADER_SIGNATURE: signature_header(key, canonical),
    }
    if write:
        headers[HEADER_NONCE] = str(nonce)
    return headers


__all__ = [
    "AGENT_HEADER_PREFIX",
    "EMPTY_BODY_SHA256",
    "HEADER_ACTOR_VIA",
    "HEADER_CONNECTION",
    "HEADER_NONCE",
    "HEADER_SIGNATURE",
    "HEADER_TIMESTAMP",
    "MAX_SKEW_S",
    "MCP_ACTOR_PREFIX",
    "MIN_KEY_BYTES",
    "NONCE_BYTES",
    "NONCE_NAMESPACE",
    "NONCE_TTL_S",
    "VERSION",
    "canonical_string",
    "canonical_target",
    "key_id",
    "new_nonce",
    "nonce_is_well_formed",
    "parse_signature_header",
    "query_pairs",
    "signature",
    "signature_header",
    "signed_headers",
    "signing_key",
    "timestamp_is_fresh",
    "usable_keys",
    "verify",
]
