"""The Metorite GitHub App: the App JWT, the installation token, one HTTP door.

Spec: ``project-docs/specs/github_integration.md`` §6.1, §6.2 and §13 (WS-54
GH-1). The App is Metorite's own, one for each environment (G-1). Its
settings are operator settings (D56): ``github_app_client_id``,
``github_app_private_key_path`` and the rest live on the box, never in a
customer row.

* **The App JWT** (:func:`app_jwt`). ``RS256`` over ``{"iat": now - 60,
  "exp": now + 540, "iss": <client id>}``, as GitHub's docs say (§4.2 fact 3).
  The key file is read once for each process and kept in memory. A missing or
  unreadable key raises :class:`GitHubAppNotConfigured`, and its message names
  neither the path nor a byte of the file.
* **The installation token** (:func:`installation_token`). ``POST
  /app/installations/{id}/access_tokens`` with the JWT, with no
  ``repositories`` narrowing: the installation's own repository choice is the
  reach. ⚠️ **The token lives in PROCESS MEMORY only** (GH-E9). It never goes
  to Redis, the database, a log line or an answer. A token is reused until five
  minutes before its ``expires_at``, and two concurrent misses for one
  installation share one request through an ``asyncio.Lock``.
  :func:`drop_installation` clears an entry. The uninstall, the suspend and a
  401 from GitHub call it.
* **One HTTP door** (:func:`call`). Every request to GitHub from the GitHub
  modules goes through it. The host is FIXED: ``api.github.com``, or
  ``github.com`` for the one OAuth code exchange. The caller names a path, never
  a URL. Redirects are off, each request has a timeout, and an answer body over
  :data:`MAX_RESPONSE_BYTES` is refused unread.

**No secret out.** No function here logs a token, a JWT, a client secret or
the key. A log line carries a status code or an error class, never a body.
Fence: ``tests/unit/test_github_app_auth.py``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from acb_common import get_logger, get_settings

_log = get_logger("connections.github_app")

#: The two hosts that the GitHub modules may call, and no other.
API_BASE = "https://api.github.com"
WEB_BASE = "https://github.com"
_BASES: frozenset[str] = frozenset({API_BASE, WEB_BASE})

#: The JWT shape of §4.2 fact 3: ``iat`` one minute back for clock drift, and
#: an ``exp`` well inside GitHub's ten-minute ceiling.
JWT_BACKDATE_S = 60
JWT_LIFETIME_S = 540

#: A cached installation token is reused until this long before it expires.
TOKEN_REUSE_MARGIN = timedelta(minutes=5)

#: The budget of one request: connect, then each read.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 10.0

#: The largest answer body that a GitHub call reads. A pull request, a check
#: run page or an installation list is far smaller.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_API_VERSION = "2022-11-28"
_ACCEPT = "application/vnd.github+json"


class GitHubAppNotConfigured(Exception):
    """The App's client id or private key is missing or unreadable.

    The message is fixed. It never names the path or holds a byte of the file.
    A caller answers 503 or skips the work.
    """

    def __init__(self, what: str = "the GitHub App is not configured") -> None:
        super().__init__(what)


class GitHubCallFailed(Exception):
    """A GitHub call failed: a timeout, a refused connection, or a body too
    large. ``code`` is a short word, never a body or a token."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Answer:
    """One GitHub answer. ``body`` is the parsed JSON, or ``None``."""

    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def rate_limited(self) -> bool:
        """A 429, or a 403 that GitHub marks as a rate limit (§11)."""
        if self.status == 429:
            return True
        if self.status != 403:
            return False
        remaining = self.headers.get("x-ratelimit-remaining")
        return remaining == "0" or "retry-after" in self.headers


# ── Settings ────────────────────────────────────────────────────────────────


def app_enabled() -> bool:
    """``github_app_enabled``, the switch of the whole feature. Default off."""
    return bool(getattr(get_settings(), "github_app_enabled", False))


def _setting(name: str) -> str:
    return str(getattr(get_settings(), name, "") or "").strip()


def client_id() -> str:
    """The App's client id, or raise :class:`GitHubAppNotConfigured`."""
    value = _setting("github_app_client_id")
    if not value:
        raise GitHubAppNotConfigured("the GitHub App client id is not set")
    return value


def client_secret() -> str:
    """The App's OAuth client secret, or raise :class:`GitHubAppNotConfigured`."""
    value = _setting("github_app_client_secret")
    if not value:
        raise GitHubAppNotConfigured("the GitHub App client secret is not set")
    return value


def slug() -> str:
    """The App's URL name, or raise :class:`GitHubAppNotConfigured`."""
    value = _setting("github_app_slug")
    if not value or not all(ch.isalnum() or ch == "-" for ch in value):
        raise GitHubAppNotConfigured("the GitHub App slug is not set")
    return value


def install_url(state: str) -> str:
    """The install link of §4.2 fact 7. The state is URL-safe base64 and a dot."""
    from urllib.parse import quote

    return f"{WEB_BASE}/apps/{slug()}/installations/new?state={quote(state, safe='')}"


def installation_settings_url(account_login: str, account_type: str, installation_id: int) -> str:
    """The page at GitHub where an admin uninstalls the App (GH-E10)."""
    if account_type == "Organization" and _safe_login(account_login):
        return f"{WEB_BASE}/organizations/{account_login}/settings/installations/{int(installation_id)}"
    return f"{WEB_BASE}/settings/installations/{int(installation_id)}"


def _safe_login(login: str) -> bool:
    return bool(login) and len(login) <= 39 and all(ch.isalnum() or ch == "-" for ch in login)


# ── The private key and the App JWT ─────────────────────────────────────────

#: ``(path, key)``. The key is read once for each process. A changed path reads
#: again, so a test can point the setting at its own key.
_KEY: tuple[str, Any] | None = None


def _private_key() -> Any:
    global _KEY
    path = _setting("github_app_private_key_path")
    if not path:
        raise GitHubAppNotConfigured("the GitHub App private key is not set")
    if _KEY is not None and _KEY[0] == path:
        return _KEY[1]
    from cryptography.hazmat.primitives import serialization

    try:
        data = Path(path).read_bytes()
        key = serialization.load_pem_private_key(data, password=None)
    except Exception:
        # ``from None`` drops the cause: an error of the PEM parser can quote
        # the file, and an OSError names the path.
        raise GitHubAppNotConfigured("the GitHub App private key cannot be read") from None
    _KEY = (path, key)
    return key


def app_jwt(now: float | None = None) -> str:
    """The App JWT (§6.1). Raises :class:`GitHubAppNotConfigured`."""
    import jwt

    issued = int(time.time() if now is None else now)
    payload = {
        "iat": issued - JWT_BACKDATE_S,
        "exp": issued + JWT_LIFETIME_S,
        "iss": client_id(),
    }
    return str(jwt.encode(payload, _private_key(), algorithm="RS256"))


# ── The one HTTP door ───────────────────────────────────────────────────────


def _headers(auth: str | None) -> dict[str, str]:
    headers = {"Accept": _ACCEPT, "X-GitHub-Api-Version": _API_VERSION,
               "User-Agent": "Metorite"}
    if auth:
        headers["Authorization"] = auth
    return headers


async def call(
    method: str,
    path: str,
    *,
    auth: str | None = None,
    json_body: Any = None,
    form: dict[str, str] | None = None,
    base: str = API_BASE,
    accept: str | None = None,
) -> Answer:
    """One request to GitHub on a FIXED host. Raises :class:`GitHubCallFailed`.

    *path* starts with ``/`` and names no host. *auth* is the whole
    ``Authorization`` value, built by the caller from a token it holds. The
    body is read as a stream and refused past :data:`MAX_RESPONSE_BYTES`.
    """
    if base not in _BASES:
        raise GitHubCallFailed("bad_host")
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "@" in path:
        raise GitHubCallFailed("bad_path")
    headers = _headers(auth)
    if accept:
        headers["Accept"] = accept
    timeout = httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    try:
        async with (
            httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client,
            client.stream(
                method, base + path, headers=headers, json=json_body, data=form,
            ) as resp,
        ):
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise GitHubCallFailed("too_large")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise GitHubCallFailed("too_large")
                chunks.append(chunk)
            raw = b"".join(chunks)
            kept = {k.lower(): v for k, v in resp.headers.items()
                    if k.lower().startswith(("x-ratelimit", "retry-after", "link",
                                             "x-oauth-scopes"))}
            status = resp.status_code
    except GitHubCallFailed:
        raise
    except Exception as exc:  # a timeout, a refused connection, anything
        _log.info("github.call_unreachable", error=type(exc).__name__)
        raise GitHubCallFailed("unreachable") from None
    body: Any = None
    if raw:
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
    return Answer(status=status, body=body, headers=kept)


async def app_call(method: str, path: str, **kwargs: Any) -> Answer:
    """A call as the App itself, with a fresh App JWT."""
    return await call(method, path, auth=f"Bearer {app_jwt()}", **kwargs)


# ── The installation token and its cache (§6.2) ─────────────────────────────


@dataclass
class _Cached:
    token: str = field(repr=False)
    expires_at: datetime


#: The cache. In PROCESS MEMORY only, by design (GH-E9).
_TOKENS: dict[int, _Cached] = {}
_LOCKS: dict[int, asyncio.Lock] = {}


def _lock(installation_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(installation_id)
    if lock is None:
        lock = _LOCKS[installation_id] = asyncio.Lock()
    return lock


def _fresh(entry: _Cached | None, now: datetime) -> bool:
    return entry is not None and now < entry.expires_at - TOKEN_REUSE_MARGIN


def _parse_expiry(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def installation_id_of(value: Any) -> int | None:
    """A positive installation id, or ``None``. A bool is not an id."""
    if isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 2**63 else None


async def installation_token(installation_id: int, *, now: datetime | None = None) -> str:
    """A token of *installation_id*, from the cache or from GitHub.

    Raises :class:`GitHubAppNotConfigured` with no App, and
    :class:`GitHubCallFailed` when GitHub gives no token (``installation_gone``
    for a 401, 403 or 404, else ``token_refused``).
    """
    iid = installation_id_of(installation_id)
    if iid is None:
        raise GitHubCallFailed("bad_installation")
    clock = now or datetime.now(UTC)
    entry = _TOKENS.get(iid)
    if _fresh(entry, clock):
        return entry.token  # type: ignore[union-attr]
    async with _lock(iid):
        entry = _TOKENS.get(iid)
        clock = now or datetime.now(UTC)
        if _fresh(entry, clock):
            return entry.token  # type: ignore[union-attr]
        answer = await app_call("POST", f"/app/installations/{iid}/access_tokens")
        if answer.status in (401, 403, 404):
            _TOKENS.pop(iid, None)
            _log.info("github.installation_token_refused", status=answer.status)
            raise GitHubCallFailed("installation_gone")
        body = answer.body if isinstance(answer.body, dict) else {}
        token = body.get("token")
        expires = _parse_expiry(body.get("expires_at"))
        if not answer.ok or not isinstance(token, str) or not token or expires is None:
            _log.info("github.installation_token_refused", status=answer.status)
            raise GitHubCallFailed("token_refused")
        _TOKENS[iid] = _Cached(token=token, expires_at=expires)
        return token


def drop_installation(installation_id: Any) -> None:
    """Forget the cached token of an installation. Never raises."""
    iid = installation_id_of(installation_id)
    if iid is not None:
        _TOKENS.pop(iid, None)


async def installation_call(installation_id: int, method: str, path: str, **kwargs: Any) -> Answer:
    """A call with the installation's token. A 401 drops the cached token and
    tries once more with a new one, because GitHub can revoke a token early."""
    token = await installation_token(installation_id)
    answer = await call(method, path, auth=f"Bearer {token}", **kwargs)
    if answer.status != 401:
        return answer
    drop_installation(installation_id)
    token = await installation_token(installation_id)
    answer = await call(method, path, auth=f"Bearer {token}", **kwargs)
    if answer.status == 401:
        drop_installation(installation_id)
    return answer


async def installation_facts(installation_id: int) -> dict[str, Any] | None:
    """``GET /app/installations/{id}`` as the App (§4.2 fact 15), or ``None``.

    The answer holds the public facts only: the account, its type, the
    repository selection, the permissions and the suspension.
    """
    iid = installation_id_of(installation_id)
    if iid is None:
        return None
    answer = await app_call("GET", f"/app/installations/{iid}")
    if not answer.ok or not isinstance(answer.body, dict):
        return None
    body: dict[str, Any] = answer.body
    raw_account = body.get("account")
    account: dict[str, Any] = raw_account if isinstance(raw_account, dict) else {}
    raw_permissions = body.get("permissions")
    permissions: dict[str, Any] = raw_permissions if isinstance(raw_permissions, dict) else {}
    return {
        "installation_id": iid,
        "account_login": str(account.get("login") or ""),
        "account_id": account.get("id"),
        "account_type": str(account.get("type") or ""),
        "repository_selection": str(body.get("repository_selection") or ""),
        "permissions": {str(k): str(v) for k, v in permissions.items()},
        "suspended": bool(body.get("suspended_at")),
    }


def reset_for_tests() -> None:
    """Drop the key and the token cache. Tests only."""
    global _KEY
    _KEY = None
    _TOKENS.clear()
    _LOCKS.clear()


__all__ = [
    "API_BASE",
    "MAX_RESPONSE_BYTES",
    "TOKEN_REUSE_MARGIN",
    "WEB_BASE",
    "Answer",
    "GitHubAppNotConfigured",
    "GitHubCallFailed",
    "app_call",
    "app_enabled",
    "app_jwt",
    "call",
    "client_id",
    "client_secret",
    "drop_installation",
    "install_url",
    "installation_call",
    "installation_facts",
    "installation_id_of",
    "installation_settings_url",
    "installation_token",
    "slug",
]
