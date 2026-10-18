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

**The organization allowlist (EA-10p).** :func:`org_allowed` is the one
reader of ``agent_access_orgs``. Agent access is live for an organization
only when the flag is on AND the list allows it. The default allows none.
Four doors check it (spec §10.6): the consent routes, the store's token
issue, the edge bearer check and the connection read of branch 0.

**The MCP surface (EA-3a, EA-6a).** :data:`MCP_ROUTES` is the one allowlist of
gateway routes an outside agent may reach. :data:`MCP_WRITE_ROUTES` names its
write pairs, and :func:`writes_enabled` is the one reader of the writes flag. :func:`actor_via_for` gives the
``mcp:<host>:<slug>`` stamp of a connection (EA-13a). :data:`ACTOR_VIA_PATTERN`
is the one shape of that stamp, and the Projects routes import it from here.
:data:`CLIENT_LABELS` and :func:`client_label` give the name of a stamp.

Fences: ``tests/unit/test_agent_oauth_server.py``,
``tests/unit/test_mcp_edge_signing.py`` and
``tests/unit/test_mcp_edge_coverage.py``.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import re
import uuid
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import NamedTuple
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
        if not _SCOPE_TOKEN.fullmatch(token):
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


def writes_enabled() -> bool:
    """True when ``agent_access_writes_enabled`` is on (EA-6a, EA3-29).

    The ONE reader of the writes flag, for both units. The edge leaves the
    write tools out of ``tools/list`` and refuses a call of one while it is
    false. Branch 0 refuses every signed write with
    :data:`WRITES_DISABLED_CODE`. It does not read :func:`enabled`: a write
    needs both.
    """
    return bool(get_settings().agent_access_writes_enabled)


def runs_enabled() -> bool:
    """True when ``agent_runs_enabled`` is on (EA-12a and EA-12b, spec §12.1).

    The ONE reader of the agent-runs flag, for both units. The gateway
    answers 404 on each agent-runs route while it is false. The edge leaves
    the six agent-run tools out of ``tools/list`` and makes no request for
    ``agent_runs`` in ``get_task``. So both units need the same value.
    """
    return bool(get_settings().agent_runs_enabled)


#: The ``detail`` of the 403 that branch 0 answers to a SIGNED write while the
#: writes flag is off (EA-6a fix round 1). The edge maps it to a tool error,
#: never to the MCP 401, so a client keeps its token and its reads.
WRITES_DISABLED_CODE = "agent_writes_disabled"


# ── The organization allowlist (EA-10p, spec §10.6, decision EA3-27) ────────

#: The entry of ``agent_access_orgs`` that allows each organization.
ALL_ORGS = "*"


def _canonical_org(value: object) -> str | None:
    """*value* as a canonical lower-case UUID string, else ``None``."""
    text = str(value or "").strip().lower()
    try:
        canonical = str(uuid.UUID(text))
    except ValueError:
        return None
    return canonical if canonical == text else None


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of *raw*: canonical organization ids, and ``*``.

    The entries have a comma between them. Each entry loses its spaces and
    goes to lower case. An entry that is not ``*`` and not a canonical UUID
    is dropped, so it allows nothing. An empty *raw* gives an empty set.
    Cached on the raw value.
    """
    entries: set[str] = set()
    for part in raw.split(","):
        entry = part.strip().lower()
        if entry == ALL_ORGS:
            entries.add(ALL_ORGS)
        elif (org := _canonical_org(entry)) is not None:
            entries.add(org)
    return frozenset(entries)


def allowed_orgs() -> frozenset[str]:
    """The parsed ``agent_access_orgs``. Empty means no organization."""
    return _parse_orgs(str(get_settings().agent_access_orgs or ""))


def org_allowed(organization_id: object) -> bool:
    """True when ``agent_access_orgs`` allows *organization_id*.

    The ONE reader of the allowlist. It does not read the flag: agent access
    is live for an organization only when :func:`enabled` and this are both
    true. A value that is not a UUID is false, also with ``*``. The caller
    passes an organization from the session or from a token row, never from
    request input.
    """
    org = _canonical_org(organization_id)
    if org is None:
        return False
    orgs = allowed_orgs()
    return ALL_ORGS in orgs or org in orgs


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
    return bool(challenge) and bool(_CHALLENGE.fullmatch(challenge or ""))


def is_valid_verifier(verifier: str | None) -> bool:
    return bool(verifier) and bool(_VERIFIER.fullmatch(verifier or ""))


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

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: A private-use scheme of a native app (RFC 8252 §7.1): a reverse domain
#: name, so it holds a dot, in lower case. ``com.example.app`` passes.
#: ``cursor``, ``javascript`` and ``Com.Example`` do not.
_PRIVATE_SCHEME = re.compile(r"[a-z][a-z0-9+-]*(?:\.[a-z0-9+-]+)+")


def _is_private_use_redirect(uri: str) -> bool:
    """True when *uri* is ``scheme:/path`` with a reverse-DNS *scheme*.

    RFC 8252 §7.1 gives the form with ONE slash and no authority, such as
    ``com.example.app:/oauth/callback``. Read on the RAW string, because
    ``urlsplit`` lowers the case of the scheme.
    """
    scheme, colon, rest = uri.partition(":")
    if not colon or not _PRIVATE_SCHEME.fullmatch(scheme):
        return False
    # One slash, then a path. ``scheme://...`` names an authority.
    return rest.startswith("/") and not rest.startswith("//") and len(rest) > 1


def is_allowed_redirect_uri(uri: str | None) -> bool:
    """True when *uri* may carry an authorization response.

    An ALLOW list of the three forms of RFC 8252 (EA-4a, spec §10.5), and
    nothing else:

    1. ``https``, with a host.
    2. ``http`` to a loopback host (RFC 8252 §7.3).
    3. A private-use scheme that holds a dot, as ``scheme:/path`` (§7.1).

    Never a fragment, a user name or a password. The match against the
    client's own list is a separate check, :func:`redirect_uri_matches`.
    ASCII only, for the reason :func:`is_cimd_client_id` gives.
    """
    if not uri or len(uri) > MAX_URL_CHARS or not uri.isascii():
        return False
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in uri) or "\\" in uri:
        return False
    try:
        parts = urlsplit(uri)
        port = parts.port  # a bad port raises here
    except ValueError:
        return False
    # An explicit port must be 1 to 65535. ``urlsplit`` reads ``:0`` as the
    # port 0, and a later ``if port`` reads 0 as "no port" (fix round 1).
    if port is not None and not 1 <= port <= _MAX_PORT:
        return False
    if "#" in uri or parts.username is not None or parts.password is not None:
        return False
    # The raw scheme, because ``urlsplit`` lowers its case.
    raw_scheme = uri.partition(":")[0]
    if raw_scheme == "https":
        return bool(parts.hostname)
    if raw_scheme == "http":
        return (parts.hostname or "") in _LOOPBACK_HOSTS
    return _is_private_use_redirect(uri)


def redirect_display(uri: str) -> tuple[str, bool]:
    """What the consent screen shows of *uri*, and whether it is an app.

    EA-4a, spec §10.5. For ``http`` and ``https``: ``scheme://host:port``,
    with the port of the FULL redirect URI, so a loopback client on port
    53122 shows that port. No port in the URI shows ``scheme://host``. The
    host is ASCII (:func:`ascii_host`), and an IPv6 host keeps its brackets.

    For any other scheme: ``scheme://authority``, or ``scheme:`` with no
    authority, and the second value is True, so the page adds "an app on
    this computer". ``x-evil://claude.ai/cb`` shows as
    ``x-evil://claude.ai``, never as ``claude.ai``.
    """
    parts = urlsplit(uri)
    scheme = parts.scheme
    if scheme in ("http", "https") and parts.hostname:
        host = ascii_host(parts.hostname)
        if ":" in host:
            host = f"[{host}]"
        try:
            port = parts.port
        except ValueError:
            port = None
        shown = f"{scheme}://{host}" if port is None else f"{scheme}://{host}:{port}"
        return shown, False
    if parts.netloc:
        return f"{scheme}://{parts.netloc}", True
    return f"{scheme}:", True


# ── The redirect match (EA-2c, spec §10.4, decision EA3-23) ─────────────────

#: The registered loopback hosts that accept any port, as RAW netloc strings.
#: Case and form count: ``LOCALHOST``, ``localhost.``, ``127.0.0.2`` and
#: ``[0:0:0:0:0:0:0:1]`` are not in this set, so they keep the exact match.
_LOOPBACK_NETLOCS = frozenset({"localhost", "127.0.0.1", "[::1]"})

#: A port in its one spelling: no sign, no leading zero, not ``0``. The upper
#: bound 65535 is a separate check.
_PORT = re.compile(r"[1-9][0-9]{0,4}")
_MAX_PORT = 65535


class _RawParts(NamedTuple):
    """The parts of a URI, cut by hand and never normalised.

    ``query`` and ``fragment`` keep their mark (``?``, ``#``), so a URI that
    ends in a bare ``?`` or ``#`` differs from one with no mark.
    """

    scheme: str
    netloc: str
    path: str
    query: str
    fragment: str


def _raw_parts(uri: str) -> _RawParts | None:
    """*uri* cut into its parts as RAW strings, or ``None`` with no ``://``.

    ⚠️ Not ``urlsplit``. It lowers the case of the scheme and removes a tab
    or a line break from the URL, so two different strings could compare equal.
    """
    scheme, sep, after = uri.partition("://")
    if not sep:
        return None
    end = len(after)
    for mark in "/?#":
        found = after.find(mark)
        if found != -1:
            end = min(end, found)
    netloc, rest = after[:end], after[end:]
    cut = rest.find("#")
    rest, fragment = (rest, "") if cut == -1 else (rest[:cut], rest[cut:])
    cut = rest.find("?")
    path, query = (rest, "") if cut == -1 else (rest[:cut], rest[cut:])
    return _RawParts(scheme, netloc, path, query, fragment)


def _loopback_any_port(requested: str, registered: str) -> bool:
    """True when *requested* is the loopback *registered* URI on some port.

    The four rules of spec §10.4, each on RAW strings:

    1. *registered* has the scheme ``http``, the netloc ``localhost``,
       ``127.0.0.1`` or ``[::1]`` exactly, and so no port and no user name.
    2. *requested* has the same scheme and the same path.
    3. The netloc of *requested* is the registered netloc, a colon and a
       port from 1 to 65535 with no leading zero.
    4. The two queries are identical, and neither URI has a fragment.
    """
    reg = _raw_parts(registered)
    req = _raw_parts(requested)
    if reg is None or req is None:
        return False
    # Rule 1.
    if reg.scheme != "http" or reg.netloc not in _LOOPBACK_NETLOCS:
        return False
    # Rule 2.
    if req.scheme != reg.scheme or req.path != reg.path:
        return False
    # Rule 4.
    if req.query != reg.query:
        return False
    if req.fragment or reg.fragment:
        return False
    # Rule 3.
    host, colon, port = req.netloc.rpartition(":")
    if not colon or host != reg.netloc:
        return False
    return _PORT.fullmatch(port) is not None and int(port) <= _MAX_PORT


def redirect_uri_matches(requested: str | None, registered: Iterable[object]) -> bool:
    """True when *requested* matches one URI of the client's *registered* list.

    The ONE place that compares redirect URIs (spec §10.4). The exact string
    match runs first. Then a loopback ``http`` registration with no port
    accepts any port (RFC 8252 §7.3), by :func:`_loopback_any_port`. Every
    other registration needs the exact string.

    This widens the authorize check only. ``mint_code`` stores the requested
    URI with its port, and the code spend matches that string exactly. The
    caller still runs :func:`is_allowed_redirect_uri` on *requested*.
    """
    if not isinstance(requested, str) or not requested:
        return False
    for uri in registered:
        if not isinstance(uri, str):
            continue
        if requested == uri or _loopback_any_port(requested, uri):
            return True
    return False


def redirect_host(uri: str) -> str:
    """The host a member sees on the consent screen, or the scheme for an app."""
    parts = urlsplit(uri)
    return ascii_host(parts.hostname) if parts.hostname else f"{parts.scheme}:"


# ── The MCP read surface (EA-3a, spec §9.5 and §10.1) ──────────────────────

#: The shape of an ``X-Actor-Via`` value, the provenance stamp of a write
#: (D-PM-36). It lives here, and ``gateway/routes/projects/core.py`` imports
#: it, because ``acb_auth`` may not import the gateway and branch 0 of
#: ``deps.get_current_user`` checks the stamp too. ONE copy.
ACTOR_VIA_PATTERN = re.compile(r"^[a-z0-9][a-z0-9:_.-]{0,63}$")

#: The write pairs of the MCP edge (EA-6a, EA3-29). A subset of
#: :data:`MCP_ROUTES`, and no pair here is a GET. Branch 0 admits a method
#: other than GET only for one of these pairs, and only with the writes flag,
#: ``projects:write`` on the connection, a fresh nonce and room under the cap.
#: EA-6d adds the task create, the route of ``create_task``. The PATCH serves
#: ``set_task_status`` and ``update_task``.
#: EA-12a adds the three POST pairs of the agent runs (spec §12.1). They
#: share the cap of EA3-30 (EA12-13).
#: EA-6c adds the comment POST, the route of ``add_comment`` (EA3-35).
#: EA-6b adds the claim, the route of ``claim_task`` (EA3-34).
#: EA-6g adds the archive request, the route of ``request_task_archive``
#: (EA3-40). ⚠️ The approval route ``POST
#: /projects/agent-runs/{run_id}/archive-approval`` and ``POST
#: /projects/tasks/{task_id}/archive`` stay OUT of both sets: only the
#: member archives, and branch 0 refuses a signed request to either.
#: Fence: ``tests/unit/test_mcp_edge_coverage.py`` (rules 1 and 2).
MCP_WRITE_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("PATCH", "/projects/tasks/{task_id}"),
    ("POST", "/projects/tasks/{task_id}/refs"),
    ("POST", "/projects/tasks"),
    ("POST", "/projects/tasks/{task_id}/comments"),
    ("POST", "/projects/tasks/{task_id}/claim"),
    ("POST", "/projects/tasks/{task_id}/agent-runs"),
    ("POST", "/projects/agent-runs/{run_id}/activities"),
    ("POST", "/projects/agent-runs/{run_id}/finish"),
    ("POST", "/projects/tasks/{task_id}/archive-request"),
})

#: The ONE allowlist of the MCP edge (EA3-6). Each pair is a method and the
#: full FastAPI route template. Branch 0 of ``deps.get_current_user`` refuses
#: every other pair, and the edge client reads this set before it sends a
#: request. EA-3 listed the GET routes of the seven read tools (§9.1). EA-6a
#: adds the references read and the two write pairs of
#: :data:`MCP_WRITE_ROUTES` (§9.2). EA-12a adds the three GET pairs of the
#: agent runs (§12.1). Fence: ``tests/unit/test_mcp_edge_coverage.py``.
MCP_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/auth/me"),
    ("GET", "/projects/tree"),
    ("GET", "/projects/nodes/{project_id}"),
    ("GET", "/projects/nodes/{project_id}/statuses"),
    ("GET", "/projects/nodes/{project_id}/types"),
    ("GET", "/projects/nodes/{project_id}/fields"),
    ("GET", "/projects/nodes/{project_id}/tags"),
    ("GET", "/projects/tasks"),
    ("GET", "/projects/tasks/{task_id}"),
    ("GET", "/projects/tasks/{task_id}/relations"),
    ("GET", "/projects/tasks/{task_id}/timeline"),
    ("GET", "/projects/tasks/{task_id}/refs"),
    ("GET", "/projects/search"),
    ("GET", "/projects/tasks/{task_id}/agent-runs"),
    ("GET", "/projects/agent-runs"),
    ("GET", "/projects/agent-runs/{run_id}"),
    *MCP_WRITE_ROUTES,
})

#: The cap of EA3-30: a connection makes at most :data:`WRITE_CAP` writes in
#: :data:`WRITE_WINDOW_S` seconds, counted from its first write of the window.
#: Branch 0 counts in the tenant Redis wrapper, under the namespace
#: :data:`WRITE_CAP_NAMESPACE`, and the 121st write gets 429.
WRITE_CAP = 120
WRITE_WINDOW_S = 600
WRITE_CAP_NAMESPACE = "agent_writes"


#: EA-13a (spec §12.2 item 1, EA3-47). The longest ``<slug>`` of a client
#: stamp, and the endings of a CIMD path segment that name no client.
CLIENT_SLUG_MAX = 24
_SLUG_ENDINGS = ("-client-metadata", "-metadata")
_SLUG_JUNK = re.compile(r"[^a-z0-9-]+")


def client_slug(client_id: str | None) -> str:
    """The ``<slug>`` of a CIMD client id, or ``""`` (EA3-47).

    The last path segment in lower case. A ``.json`` end goes first, then a
    ``-client-metadata`` or ``-metadata`` end. Each run of characters other
    than ``[a-z0-9-]`` becomes one ``-``, and the slug keeps at most
    :data:`CLIENT_SLUG_MAX` characters. A client id that is not a CIMD URL
    (a dynamic registration) gives ``""``.
    ``https://claude.ai/oauth/claude-code-client-metadata`` gives
    ``claude-code``.
    """
    if not client_id or not is_cimd_client_id(client_id):
        return ""
    segment = urlsplit(client_id).path.rstrip("/").rsplit("/", 1)[-1].lower()
    segment = segment.removesuffix(".json")
    for ending in _SLUG_ENDINGS:
        if segment.endswith(ending):
            segment = segment.removesuffix(ending)
            break
    segment = _SLUG_JUNK.sub("-", segment).strip("-")
    return segment[:CLIENT_SLUG_MAX].rstrip("-")


def actor_via_for(client_origin: str | None, *, client_id: str | None) -> str:
    """The ``X-Actor-Via`` stamp of a connection (spec §10.1, §12.2 item 1).

    EA-13a (EA3-47): ``mcp:<host>:<slug>``, where ``<host>`` is the
    lower-case ASCII host of *client_origin* and ``<slug>`` is
    :func:`client_slug` of *client_id*, the connection's own client id. The
    slug counts only when the client id is at that same host. Claude Code
    gives ``mcp:claude.ai:claude-code``.

    The fallbacks, in order. With no slug, or a stamp that does not match
    :data:`ACTOR_VIA_PATTERN`, the host stamp ``mcp:<host>`` (the stamp of
    every connection before EA-13a). When that does not match either, the
    first 16 hex characters of the SHA-256 of the origin. An empty origin
    gives ``""``, which matches no pattern, so branch 0 refuses it (step 7).

    *client_id* is keyword-only and has no default, so a caller names it.
    ``client_id=None`` gives the legacy host stamp, which the open-run read
    of ``routes/projects/agent_runs.py`` matches for one release.
    """
    origin = (client_origin or "").strip()
    if not origin:
        return ""
    try:
        host = (urlsplit(origin).hostname or "").lower()
    except ValueError:
        host = ""
    host_ok = bool(host) and host.isascii()
    # An IPv6 host has colons of its own, so a slug after it could not be
    # read back. Such a host keeps the host stamp (EA-13a fix round 1).
    slug = client_slug(client_id) if host_ok and ":" not in host else ""
    if slug and ascii_host(urlsplit(client_id or "").hostname or "").lower() == host:
        stamp = f"mcp:{host}:{slug}"
        if ACTOR_VIA_PATTERN.fullmatch(stamp):
            return stamp
    stamp = f"mcp:{host}"
    if host_ok and ACTOR_VIA_PATTERN.fullmatch(stamp):
        return stamp
    return "mcp:" + hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]


#: EA-13a (spec §12.2 item 1, EA3-45). The ONE curated name of a client
#: stamp, keyed by the stamp without ``mcp:``. The map ``CLIENT_LABELS`` of
#: ``app/projects/lib/agentRuns.ts`` mirrors it, key for key, and
#: ``tests/fixtures/agent_client_names.json`` holds both. A pytest and a
#: vitest test read that file, so the two cannot drift.
#:
#: ⚠️ The self-declared ``client_name`` of a metadata document never names a
#: client alone. Any host can publish a document that says "Claude Code".
CLIENT_LABELS: Mapping[str, str] = MappingProxyType({
    "claude.ai:claude-code": "Claude Code",
    # The legacy host stamp. It is true for every client on that host.
    "claude.ai": "Claude",
})


def client_label(via: str | None) -> str:
    """The name of a client stamp, for a person to read (EA3-45).

    A known stamp reads its label. Else the label of its host, else the host
    itself, so ``mcp:evil.example:claude-code`` reads ``evil.example``. An
    empty value reads ``"Agent"``.

    A body with more than one colon is an IPv6 host stamp, because
    :func:`actor_via_for` adds no slug to an IPv6 host. It reads whole.
    """
    raw = (via or "").strip().lower()
    body = raw.removeprefix("mcp:")
    host = body if body.count(":") > 1 else body.split(":", 1)[0]
    return CLIENT_LABELS.get(body) or CLIENT_LABELS.get(host) or host or "Agent"


def client_host(via: str | None) -> str:
    """The host of a client stamp, for the run pill ``<client> · <host>``
    (EA-13d). ``mcp:claude.ai:claude-code`` gives ``claude.ai``. An empty
    value gives ``""``. An IPv6 host stamp reads whole, as in
    :func:`client_label`. The host is the part that the gateway verified when
    the member connected the client, so it is the part that no client names for
    itself.
    """
    body = (via or "").strip().lower().removeprefix("mcp:")
    return body if body.count(":") > 1 else body.split(":", 1)[0]


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
    "ACTOR_VIA_PATTERN",
    "ALL_ORGS",
    "CLIENT_LABELS",
    "CLIENT_SLUG_MAX",
    "CONTEXT_READ",
    "MAX_SCOPE_CHARS",
    "MAX_URL_CHARS",
    "MCP_ROUTES",
    "MCP_WRITE_ROUTES",
    "OFFLINE_ACCESS",
    "PROJECTS_READ",
    "PROJECTS_WRITE",
    "RESOURCE_SCOPES",
    "S256",
    "SUPPORTED_SCOPES",
    "WRITES_DISABLED_CODE",
    "WRITE_CAP",
    "WRITE_CAP_NAMESPACE",
    "WRITE_WINDOW_S",
    "actor_via_for",
    "allowed_orgs",
    "ascii_host",
    "authorization_redirect",
    "authorize_url",
    "client_host",
    "client_label",
    "client_origin",
    "client_slug",
    "enabled",
    "is_allowed_redirect_uri",
    "is_cimd_client_id",
    "is_valid_challenge",
    "is_valid_verifier",
    "issuer",
    "org_allowed",
    "parse_scope",
    "pkce_matches",
    "redirect_display",
    "redirect_host",
    "redirect_uri_matches",
    "resource",
    "revocation_endpoint",
    "runs_enabled",
    "s256",
    "scope_string",
    "token_endpoint",
    "writes_enabled",
]
