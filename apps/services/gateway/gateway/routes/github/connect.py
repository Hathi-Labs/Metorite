"""The connect flow of the Metorite GitHub App (WS-54 GH-3).

Spec: ``project-docs/specs/github_integration.md`` §7.1 to §7.5 and §13.

    POST  /integrations/github/app/start      → {url}
    GET   /integrations/github/app/callback   → 302 /integrations?view=connections&github=<code>
    POST  /integrations/github/app/confirm    → {status, account_login}
    PUT   /integrations/github/token          → {status, login}   (GH-7, §7.6)

⚠️ **Dark.** With ``github_app_enabled`` off each route answers 404, before
any other check (:func:`require_github_app`, the first dependency).

**Who.** Each route needs ``feature:integrations`` and ``integrations:manage``,
as every writer of IN-0. The org is the bound tenant and the member is the
session's. Nothing in a query string or a body names either (R11).

**The start** signs a state (purpose ``github_install``, the org, the member,
ten minutes) and writes the PENDING CONNECT ``key("github", "connect",
<member>)`` with ``SET NX EX 600`` through the tenant Redis wrapper. GitHub's
docs do not promise that ``state`` comes back (U1), so the pending connect is
what binds the callback (GH-E3).

**The callback** runs the checks of §7.3, in order. Each refusal redirects to
``/integrations?view=connections&github=<code>``:

1. ``integrations:manage`` in the session's org (the dependency, a 403 that
   the BFF turns into ``forbidden``);
2. the pending connect, taken with ``GETDEL``, else ``expired``. It trusts
   nothing in the query string;
3. a state that is present must verify for this org and member, else
   ``bad_state``;
4. ``setup_action=request`` writes nothing, and answers ``requested``;
5. the code is exchanged for a USER token, held for this request only;
6. ``GET /user/installations`` must list the installation, else ``not_yours``;
7. the user administers the account of the installation, else ``not_admin``;
8. ``GET /app/installations/{id}`` as the App, and a suspended one answers
   ``suspended``;
9. the callback WRITES NOTHING. It puts the installation in the pending key
   for ten more minutes and answers ``confirm``.

**The confirm** takes the pending key once, and writes the org's ``app`` row
through ``connections/github.py`` under the org's lock (§7.4). The unique index
of migration 282 refuses an installation that another org holds:
``other_org``. One ``acb_audit`` row records ``github.app.connected``.

**The Advanced token (GH-7, §7.6).** ``PUT /integrations/github/token`` is the
ONE writer of a pasted token. It is on :data:`token_router`, which the App
flag does NOT gate, because the token is the path for an org that cannot or
will not install the App. It asks ``GET https://api.github.com/user`` with the
token, refuses a token that GitHub refuses with 422, and stores it through
``connections/github.save_token``. It does not call the BYOK gate: a GitHub
token is not an LLM key.

**No secret out.** The user token, the client secret and the App JWT never
reach a log line, a redirect URL, the database or Redis. Fence:
``tests/unit/test_github_connect_r8.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router, require_permission
from acb_common import get_logger, tenant_redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from gateway.connections import github as github_connection
from gateway.connections import github_app
from gateway.db import current_tenant, tenant_session
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_state,
    verify_state,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

_log = get_logger("gateway.github.connect")

MANAGE_PERMISSION = "integrations:manage"
_REQUIRE_MANAGE = require_permission(MANAGE_PERMISSION)

#: The purpose of the signed state. Not one of `signing._RESERVED_PURPOSES`.
STATE_PURPOSE = "github_install"

#: The Redis namespace and the life of one pending connect (§7.1, §7.3).
NAMESPACE = "github"
PENDING_TTL_S = 600

#: Where the callback sends the browser. The BFF route passes only these.
RETURN_PAGE = "/integrations?view=connections&github="

#: Every code the callback and the confirm can answer. The BFF and the page
#: know the same list.
CODES: frozenset[str] = frozenset({
    "forbidden", "expired", "bad_state", "requested", "failed", "not_yours",
    "not_admin", "suspended", "confirm", "other_org", "connected", "unavailable",
    "not_configured",
})

#: At most this many pages of ``GET /user/installations`` (100 each).
MAX_INSTALLATION_PAGES = 10

_LOGIN_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")


async def require_github_app() -> None:
    """404 unless ``github_app_enabled`` is on. It reads nothing else."""
    if not github_app.app_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/integrations/github",
    tags=["integrations", "github"],
    # ⚠️ The ORDER is load-bearing: the flag runs first, so a dark route is a
    # 404 for every caller, also one that the feature gate would refuse.
    dependencies=[Depends(require_github_app), require_feature_router("integrations")],
)


class StartOut(BaseModel):
    url: str


class ConfirmOut(BaseModel):
    status: str
    account_login: str
    account_type: str
    repository_selection: str


# ── The caller ──────────────────────────────────────────────────────────────


def _organization(user: UserContext) -> str:
    """The bound tenant (R5). It never comes from the request."""
    org = current_tenant() or user.organization_id
    if not org:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(org)


def _member(user: UserContext) -> str:
    member = (user.email or "").strip().lower()
    if not member:
        raise HTTPException(status_code=403, detail="Forbidden: no member.")
    return member


def _back(code: str) -> RedirectResponse:
    safe = code if code in CODES else "failed"
    return RedirectResponse(f"{RETURN_PAGE}{safe}", status_code=302)


# ── The pending connect (tenant Redis, R5c) ─────────────────────────────────


def _redis() -> tenant_redis.TenantRedis:
    """The tenant-prefixed client. A test replaces it."""
    return tenant_redis.get_tenant_redis()


async def _put_pending(org: str, member: str, record: dict[str, Any], *, nx: bool) -> bool:
    with tenant_redis.organization_scope(org):
        client = _redis()
        done = await client.set(
            client.key(NAMESPACE, "connect", member),
            json.dumps(record, separators=(",", ":")), nx=nx, ex=PENDING_TTL_S,
        )
    return bool(done)


async def _take_pending(org: str, member: str) -> dict[str, Any] | None:
    """Read the pending connect and delete it, in ONE command (GETDEL)."""
    with tenant_redis.organization_scope(org):
        client = _redis()
        raw = await client.getdel(client.key(NAMESPACE, "connect", member))
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


# ── The GitHub reads of the callback ────────────────────────────────────────


async def _exchange_code(code: str) -> str | None:
    """The user token for *code* (§7.3 step 5), or ``None``. It is held by the
    caller for this request only and never written anywhere."""
    answer = await github_app.call(
        "POST", "/login/oauth/access_token", base=github_app.WEB_BASE,
        accept="application/json",
        form={
            "client_id": github_app.client_id(),
            "client_secret": github_app.client_secret(),
            "code": code,
        },
    )
    body = answer.body if isinstance(answer.body, dict) else {}
    token = body.get("access_token")
    if not answer.ok or not isinstance(token, str) or not token:
        _log.info("github.connect.code_refused", status=answer.status,
                  error=str(body.get("error") or "")[:40])
        return None
    return token


async def _user_installations(user_token: str) -> dict[int, dict[str, Any]] | None:
    """Every installation that the user can reach, by id (§7.3 step 6)."""
    found: dict[int, dict[str, Any]] = {}
    for page in range(1, MAX_INSTALLATION_PAGES + 1):
        answer = await github_app.call(
            "GET", f"/user/installations?per_page=100&page={page}",
            auth=f"Bearer {user_token}",
        )
        if not answer.ok or not isinstance(answer.body, dict):
            _log.info("github.connect.installations_refused", status=answer.status)
            return None
        items = answer.body.get("installations")
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            iid = github_app.installation_id_of(item.get("id"))
            raw_account = item.get("account")
            if iid is not None:
                found[iid] = dict(raw_account) if isinstance(raw_account, dict) else {}
        if len(items) < 100:
            break
    return found


async def _administers(user_token: str, account: dict[str, Any]) -> bool:
    """The user administers the account of the installation (§7.3 step 7).

    A ``User`` account must be the user. An ``Organization`` needs the role
    ``admin`` in an ``active`` membership. A collaborator who only reads one
    repository is refused.
    """
    auth = f"Bearer {user_token}"
    kind = str(account.get("type") or "")
    if kind == "User":
        answer = await github_app.call("GET", "/user", auth=auth)
        body = answer.body if isinstance(answer.body, dict) else {}
        return answer.ok and body.get("id") is not None and body.get("id") == account.get("id")
    if kind == "Organization":
        login = str(account.get("login") or "")
        if not _LOGIN_RE.match(login):
            return False
        answer = await github_app.call("GET", f"/user/memberships/orgs/{login}", auth=auth)
        body = answer.body if isinstance(answer.body, dict) else {}
        return answer.ok and body.get("role") == "admin" and body.get("state") == "active"
    return False


async def _verify_installation(
    code: str, iid: int, member: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Steps 5 to 8 of §7.3: ``(refusal code, None)`` or ``(None, facts)``.

    The user token lives in this frame only, and goes before the App read.
    """
    try:
        # 5. The code for a user token.
        user_token = await _exchange_code(code)
        if user_token is None:
            return "failed", None
        try:
            # 6. The installation belongs to the user.
            installations = await _user_installations(user_token)
            if installations is None:
                return "failed", None
            account = installations.get(iid)
            if account is None:
                _log.warning("github.connect.not_yours", actor=member)
                return "not_yours", None
            # 7. The user administers the account.
            if not await _administers(user_token, account):
                _log.warning("github.connect.not_admin", actor=member)
                return "not_admin", None
        finally:
            del user_token
        # 8. The facts of the installation, as the App.
        facts = await github_app.installation_facts(iid)
    except github_app.GitHubAppNotConfigured:
        return "not_configured", None
    except github_app.GitHubCallFailed as exc:
        _log.info("github.connect.github_failed", code=exc.code)
        return "failed", None
    if facts is None or not facts.get("account_login"):
        return "failed", None
    if facts["suspended"]:
        return "suspended", None
    return None, facts


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post("/app/start", response_model=StartOut, dependencies=[_REQUIRE_MANAGE])
async def start_github_app(user: UserContext = Depends(get_current_user)) -> StartOut:
    """The install URL of the App, with a signed state (§7.1)."""
    org = _organization(user)
    member = _member(user)
    try:
        github_app.slug()
        github_app.client_id()
    except github_app.GitHubAppNotConfigured:
        raise HTTPException(status_code=503, detail="not_configured") from None
    try:
        state = sign_state(purpose=STATE_PURPOSE, org=org, member=member)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    opened = verify_state(state, purpose=STATE_PURPOSE)
    if opened is None:  # a state that this process just signed always opens
        raise HTTPException(status_code=503, detail="signing_unavailable")
    try:
        # NX: a connect that is pending stays. Its callback still works, and
        # the state of this start verifies too (same org, same member).
        await _put_pending(org, member, {"stage": "start", "nonce": opened["nonce"]}, nx=True)
    except Exception as exc:  # the class only
        _log.warning("github.connect.pending_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="pending_unavailable") from None
    _log.info("github.connect.started", actor=member)
    return StartOut(url=github_app.install_url(state))


@router.get("/app/callback", dependencies=[_REQUIRE_MANAGE])
async def github_app_callback(
    code: str | None = Query(default=None, max_length=512),
    installation_id: str | None = Query(default=None, max_length=32),
    setup_action: str | None = Query(default=None, max_length=32),
    state: str | None = Query(default=None, max_length=4096),
    user: UserContext = Depends(get_current_user),
) -> RedirectResponse:
    """Finish the install. See the module doc for the order of the checks."""
    org = _organization(user)
    member = _member(user)
    # 2. The pending connect, taken once. Nothing in the query names it.
    try:
        pending = await _take_pending(org, member)
    except Exception as exc:  # the class only
        _log.warning("github.connect.pending_unavailable", error=type(exc).__name__)
        return _back("unavailable")
    if pending is None or pending.get("stage") != "start":
        return _back("expired")
    # 3. The state, when present.
    if state:
        payload = verify_state(state, purpose=STATE_PURPOSE)
        if payload is None or payload["org"] != org or payload["member"] != member:
            _log.warning("github.connect.bad_state", actor=member)
            return _back("bad_state")
    # 4. An install request that an organization owner must approve.
    if setup_action == "request":
        return _back("requested")
    iid = github_app.installation_id_of(installation_id)
    if not code or iid is None:
        return _back("failed")
    refusal, facts = await _verify_installation(code, iid, member)
    if refusal is not None or facts is None:
        return _back(refusal or "failed")
    # 9. Nothing is written. The confirm of this member writes the row.
    record = {
        "stage": "confirm",
        "installation_id": iid,
        "account_login": facts["account_login"],
        "account_type": facts["account_type"],
        "repository_selection": facts["repository_selection"],
        "permissions": facts["permissions"],
    }
    try:
        await _put_pending(org, member, record, nx=False)
    except Exception as exc:  # the class only
        _log.warning("github.connect.pending_unavailable", error=type(exc).__name__)
        return _back("unavailable")
    login = facts["account_login"] if _LOGIN_RE.match(facts["account_login"]) else ""
    _log.info("github.connect.awaiting_confirm", actor=member, installation_id=iid)
    return RedirectResponse(
        f"{RETURN_PAGE}confirm" + (f"&account={login}" if login else ""), status_code=302,
    )


@router.post("/app/confirm", response_model=ConfirmOut, dependencies=[_REQUIRE_MANAGE])
async def confirm_github_app(user: UserContext = Depends(get_current_user)) -> ConfirmOut:
    """Write the org's ``app`` row (§7.3 step 10, §7.4). One write, one audit.

    The BFF refuses a cross-site request, so a forged callback URL that a
    member opens cannot bind an installation without a click of that member.
    """
    org = _organization(user)
    member = _member(user)
    try:
        pending = await _take_pending(org, member)
    except Exception as exc:  # the class only
        _log.warning("github.connect.pending_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="unavailable") from None
    if pending is None or pending.get("stage") != "confirm":
        raise HTTPException(status_code=409, detail="expired")
    iid = github_app.installation_id_of(pending.get("installation_id"))
    if iid is None:
        raise HTTPException(status_code=409, detail="expired")
    login = str(pending.get("account_login") or "")
    account_type = str(pending.get("account_type") or "")
    selection = str(pending.get("repository_selection") or "")
    raw_permissions = pending.get("permissions")
    permissions: dict[str, Any] = raw_permissions if isinstance(raw_permissions, dict) else {}
    try:
        app_slug = github_app.slug()
    except github_app.GitHubAppNotConfigured:
        app_slug = ""
    try:
        async with tenant_session(org) as db:
            await github_connection.save_app_installation(
                db, org, installation_id=iid, account_login=login,
                account_type=account_type, repository_selection=selection,
                app_slug=app_slug, connected_by=member,
                permissions={str(k): str(v) for k, v in permissions.items()},
            )
    except IntegrityError:
        # The unique index of migration 282: another org holds it (§7.3 step 11).
        _log.warning("github.connect.other_org", actor=member, installation_id=iid)
        raise HTTPException(status_code=409, detail="other_org") from None
    # Lazy: the module of the old routes is large.
    from gateway.routes.integrations import audit_integration

    audit_integration(user, org, "github", action="github.app.connected",
                      member=member, installation_id=iid, account_login=login)
    _log.info("github.connect.connected", actor=member, installation_id=iid)
    return ConfirmOut(status="connected", account_login=login,
                      account_type=account_type, repository_selection=selection)


# ── The Advanced token (GH-7, §7.6) ────────────────────────────────────────

#: The token route is NOT behind the App flag, so it has its own router.
token_router = APIRouter(
    prefix="/integrations/github",
    tags=["integrations", "github"],
    dependencies=[require_feature_router("integrations")],
)

#: The longest token that the route reads. A GitHub token is far shorter.
MAX_TOKEN_CHARS = 255


class TokenIn(BaseModel):
    """``{token, replace_app?}``. No length rule here: a validation error would
    echo the value in the 422, so the route checks the length itself.

    ``replace_app`` is the admin's confirm that the token REPLACES a live App
    connection (fix round 1, item 3). The org has one ``github`` row (G-6), so
    a token and the App cannot both hold it."""

    model_config = ConfigDict(extra="forbid")
    token: str
    replace_app: bool = False


#: The 409 detail when a token would replace a live App with no confirm.
APP_CONNECTED = "app_connected"


class TokenOut(BaseModel):
    status: str
    login: str
    scopes: list[str]


@token_router.put("/token", response_model=TokenOut, dependencies=[_REQUIRE_MANAGE])
async def put_github_token(
    payload: TokenIn, user: UserContext = Depends(get_current_user),
) -> TokenOut:
    """Store a pasted token as the org's ``token`` row (§7.6)."""
    org = _organization(user)
    member = _member(user)
    token = payload.token.strip()
    if not token or len(token) > MAX_TOKEN_CHARS or any(ch.isspace() for ch in token):
        raise HTTPException(status_code=422, detail="That is not a GitHub token.")
    # Layer A of the env guard: one plain value, no control character.
    from gateway.routes.integrations import _refuse_unsafe_env_writes, audit_integration

    _refuse_unsafe_env_writes([("GITHUB_TOKEN", token)], actor=member)
    from gateway.connections import store as connection_store

    if not payload.replace_app:
        async with tenant_session(org) as db:
            live = await connection_store.get(
                db, org, github_connection.PROVIDER, github_connection.OWNING_APP)
        if (github_connection.is_app_row(live) and live is not None
                and live.status != connection_store.STATUS_DISCONNECTED):
            # The token would replace the App: pull requests stop coming by
            # webhook and a merge no longer moves a task. The card asks first.
            raise HTTPException(status_code=409, detail=APP_CONNECTED)
    try:
        answer = await github_app.call("GET", "/user", auth=f"Bearer {token}")
    except github_app.GitHubCallFailed:
        raise HTTPException(status_code=503, detail="GitHub did not answer.") from None
    body = answer.body if isinstance(answer.body, dict) else {}
    login = str(body.get("login") or "")
    if not answer.ok or not login:
        _log.info("github.token.refused", status=answer.status, actor=member)
        raise HTTPException(status_code=422, detail="GitHub refused this token.")
    scopes = github_connection.parse_scopes(answer.headers.get("x-oauth-scopes", ""))
    async with tenant_session(org) as db:
        before = await connection_store.get(
            db, org, github_connection.PROVIDER, github_connection.OWNING_APP)
        await github_connection.save_token(
            db, org, access_token=token, login=login, scopes=scopes, connected_by=member,
        )
    if github_connection.is_app_row(before) and before is not None:
        github_app.drop_installation(before.provider_meta.get("installation_id"))
    audit_integration(user, org, "github", action="github.token.connected",
                      member=member, login=login)
    _log.info("github.token.connected", actor=member)
    return TokenOut(status="connected", login=login, scopes=list(scopes))


__all__ = ["CODES", "STATE_PURPOSE", "require_github_app", "router", "token_router"]
