"""The OAuth vocabulary of outside-agent access — both halves share it (WS-55 EA-2a).

Spec: ``project-docs/specs/external_agent_access.md`` §5.3, §5.5, §8 and §15
EA-2a · decision D97.

**What this module is.** The pure rules that the two halves of the
authorization server must agree on: the scope vocabulary, the fixed addresses,
the PKCE check, the shape of a Client ID Metadata Document URL, the redirect
URI rules and the redirect that carries a code. It does no I/O.

**The two halves.** The public half is the edge unit
(``apps/services/agent_edge``): the metadata documents, ``token`` and
``revoke``. The authenticated half is the gateway route
``gateway/routes/agent_access.py``: validate an authorize request, then approve
it. A rule that only one half kept would let the halves disagree, so each rule
lives here once. ``acb_auth.agent_tokens`` is the store, and this module never
touches it.

**The flag.** :func:`enabled` is the one reader of ``agent_access_enabled``.
While it is false, every EA-2a route answers 404.

Fence: ``tests/unit/test_agent_oauth_server.py``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from acb_common import get_settings

# ── Scopes (spec §8) ────────────────────────────────────────────────────────

PROJECTS_READ = "projects:read"
PROJECTS_WRITE = "projects:write"
CONTEXT_READ = "context:read"
OFFLINE_ACCESS = "offline_access"

#: Every scope the server knows, in the order a consent screen shows them.
SUPPORTED_SCOPES: tuple[str, ...] = (
    PROJECTS_READ, PROJECTS_WRITE, CONTEXT_READ, OFFLINE_ACCESS)

#: The scopes the resource itself needs. ``offline_access`` is not one: the MCP
#: specification says a resource does not advertise it (spec §8).
RESOURCE_SCOPES: tuple[str, ...] = (PROJECTS_READ, PROJECTS_WRITE, CONTEXT_READ)

#: The longest ``scope`` string the server reads.
MAX_SCOPE_CHARS = 1024

#: A scope token per RFC 6749 §3.3: printable ASCII except space, ``"`` and ``\\``.
_SCOPE_TOKEN = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]+$")


def parse_scope(value: str | None) -> tuple[str, ...]:
    """The supported scopes that *value* names, in the canonical order.

    An unknown scope is dropped, because the server never grants what it does
    not know. A malformed token raises ``ValueError``. ``None`` and an empty
    string give an empty tuple.
    """
    if value is None:
        return ()
    if len(value) > MAX_SCOPE_CHARS:
        raise ValueError("scope is too long")
    tokens = value.split(" ")
    named = set()
    for token in tokens:
        if token == "":
            continue
        if not _SCOPE_TOKEN.match(token):
            raise ValueError("scope holds a bad token")
        named.add(token)
    return tuple(s for s in SUPPORTED_SCOPES if s in named)


def scope_string(scopes: Iterable[str]) -> str:
    """The space-separated form of *scopes*, in the canonical order."""
    wanted = set(scopes)
    return " ".join(s for s in SUPPORTED_SCOPES if s in wanted)


# ── The flag and the fixed addresses (spec §5.5) ────────────────────────────


def enabled() -> bool:
    """True when ``agent_access_enabled`` is on. The ONE reader of the flag."""
    return bool(get_settings().agent_access_enabled)


def issuer() -> str:
    """The issuer, ``https://mcp.metorite.com`` by default. No trailing slash."""
    return get_settings().agent_issuer.rstrip("/")


def resource() -> str:
    """The canonical resource, ``https://mcp.metorite.com/mcp`` by default."""
    return get_settings().agent_resource


def authorize_url() -> str:
    """The consent page that EA-4 builds on the control plane."""
    return get_settings().agent_authorize_url


def token_endpoint() -> str:
    return f"{issuer()}/oauth/token"


def revocation_endpoint() -> str:
    return f"{issuer()}/oauth/revoke"


# ── PKCE (RFC 7636, S256 only) ──────────────────────────────────────────────

#: An S256 challenge is the base64url SHA-256 of the verifier, with no padding.
#: That is always 43 characters.
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")

#: A verifier per RFC 7636 §4.1.
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")

#: The one challenge method. ``plain`` is refused (OAuth 2.1).
S256 = "S256"


def is_valid_challenge(challenge: str | None) -> bool:
    return bool(challenge) and bool(_CHALLENGE.match(challenge or ""))


def is_valid_verifier(verifier: str | None) -> bool:
    return bool(verifier) and bool(_VERIFIER.match(verifier or ""))


def s256(verifier: str) -> str:
    """The S256 challenge of *verifier*."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def pkce_matches(verifier: str, challenge: str) -> bool:
    """True when *verifier* is well formed and its S256 equals *challenge*."""
    if not is_valid_verifier(verifier):
        return False
    return hmac.compare_digest(s256(verifier), challenge or "")


# ── Client ID Metadata Documents ────────────────────────────────────────────

#: The longest client id or redirect URI the server reads.
MAX_URL_CHARS = 2048


def is_cimd_client_id(client_id: str | None) -> bool:
    """True when *client_id* has the shape of a Client ID Metadata Document URL.

    The rules of the CIMD draft: an ``https`` URL with a host and a path, with
    no user name, no password, no fragment and no dot segment. This server
    also refuses a query and a backslash, so one URL has one spelling.

    ⚠️ ASCII only (fix round 1). A Unicode host such as a Cyrillic look-alike
    would show on the consent screen as the verified origin of a trusted
    name. An international domain name is fine in its punycode form
    (``xn--...``), which shows as itself.
    """
    if not client_id or len(client_id) > MAX_URL_CHARS or not client_id.isascii():
        return False
    if "\\" in client_id or any(ord(c) < 0x21 or ord(c) == 0x7F for c in client_id):
        return False
    try:
        parts = urlsplit(client_id)
        _ = parts.port  # a bad port raises here
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if parts.query or parts.fragment or "#" in client_id or "?" in client_id:
        return False
    path = parts.path
    if not path or path == "/":
        return False
    return all(seg not in (".", "..") for seg in path.split("/"))


def client_origin(client_id: str) -> str:
    """The verified origin of a CIMD client: the scheme and host of its URL.

    The server fetched the document from this origin, so this origin is what
    the consent screen may show as verified (spec §7). A name inside the
    document is only a claim. The host is always shown in ASCII (punycode),
    so a look-alike letter of another script cannot pass for a trusted name.
    """
    parts = urlsplit(client_id)
    host = ascii_host(parts.hostname or "")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def ascii_host(host: str) -> str:
    """*host* in ASCII. A Unicode host becomes its punycode form (IDNA).

    The validators refuse a non-ASCII URL before this runs. This is the second
    line: a host that cannot be encoded shows as ``"(invalid host)"``.
    """
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return "(invalid host)"


# ── Redirect URIs ───────────────────────────────────────────────────────────

#: Schemes a redirect may never use. A consent page that navigates to one of
#: these on its own origin runs script there.
_FORBIDDEN_SCHEMES = frozenset({
    "javascript", "data", "vbscript", "file", "blob", "about", "ftp", "ws",
    "wss", "filesystem", "view-source", "jar", "chrome", "intent",
})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$")


def is_allowed_redirect_uri(uri: str | None) -> bool:
    """True when *uri* may carry an authorization response.

    ``https`` always. ``http`` only to a loopback host (RFC 8252 §7.3). A
    private-use scheme of a native app (RFC 8252 §7.1), except the schemes in
    :data:`_FORBIDDEN_SCHEMES`. Never a fragment, a user name or a password.
    The exact match against the client's own list is a separate check. ASCII
    only, for the reason :func:`is_cimd_client_id` gives.
    """
    if not uri or len(uri) > MAX_URL_CHARS or not uri.isascii():
        return False
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in uri) or "\\" in uri:
        return False
    try:
        parts = urlsplit(uri)
        _ = parts.port  # a bad port raises here
    except ValueError:
        return False
    scheme = parts.scheme
    if not _SCHEME.match(scheme) or scheme in _FORBIDDEN_SCHEMES:
        return False
    if "#" in uri or parts.username is not None or parts.password is not None:
        return False
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":
        return (parts.hostname or "") in _LOOPBACK_HOSTS
    # A private-use scheme needs something after it.
    return bool(parts.netloc or parts.path)


def redirect_host(uri: str) -> str:
    """The host a member sees on the consent screen, or the scheme for an app."""
    parts = urlsplit(uri)
    return ascii_host(parts.hostname) if parts.hostname else f"{parts.scheme}:"


def authorization_redirect(redirect_uri: str, params: Mapping[str, str | None]) -> str:
    """*redirect_uri* with *params* added to its query (RFC 6749 §4.1.2).

    A parameter with the value ``None`` is left out. The query that the
    redirect URI already holds stays, in front of the new parameters.
    """
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [(k, v) for k, v in params.items() if v is not None]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


__all__ = [
    "CONTEXT_READ",
    "MAX_SCOPE_CHARS",
    "MAX_URL_CHARS",
    "OFFLINE_ACCESS",
    "PROJECTS_READ",
    "PROJECTS_WRITE",
    "RESOURCE_SCOPES",
    "S256",
    "SUPPORTED_SCOPES",
    "ascii_host",
    "authorization_redirect",
    "authorize_url",
    "client_origin",
    "enabled",
    "is_allowed_redirect_uri",
    "is_cimd_client_id",
    "is_valid_challenge",
    "is_valid_verifier",
    "issuer",
    "parse_scope",
    "pkce_matches",
    "redirect_host",
    "resource",
    "revocation_endpoint",
    "s256",
    "scope_string",
    "token_endpoint",
]
