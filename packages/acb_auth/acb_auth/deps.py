"""FastAPI dependency helpers for RBAC (WBS 1.7).

Usage in routes
---------------
    from acb_auth import get_current_user, require_permission, require_role, UserRole

    # Any authenticated user:
    @app.post("/pull")
    async def pull(req: PullRequest, user=Depends(get_current_user)):
        ...

    # Permission-gated (preferred for new routes):
    @app.get("/whatsapp/chats", dependencies=[require_permission("feature:whatsapp")])
    async def list_chats():
        ...

    # Executive-only (legacy coarse role — still honoured, see roles.py):
    @app.post("/pull/sales", dependencies=[require_role(UserRole.EXECUTIVE)])
    async def pull_sales(req: PullRequest):
        ...

Headers (set by Next.js SSO proxy):
    X-User-Email   -- the SSO-verified email of the signed-in member.
                     ⚠️ **No domain is implied and none is checked.** This line
                     used to read "the Google-verified email (fracktal.in
                     domain)", which was wrong on both counts: the provider is
                     NextAuth (Microsoft Entra and/or Google, `workbench/
                     control_plane/src/auth.ts`), and since WS-31 CP-0 the
                     product deliberately accepts identities from directories we
                     do not own — a customer's staff are not in our directory
                     (D33.1). Any code inferring tenancy, trust or membership
                     from the domain part is wrong; the organization comes from
                     the resolved session, never from the address
                     (`user_management_contract.md` R11).
    X-User-Role    -- one of: executive | employee | agent
                     Falls back to "employee" if missing/unrecognised.

Both are INTERNAL-ONLY headers and nothing in this module can tell a forwarded
one from a forged one — the Bearer token beside them is the whole of the proof,
and that token falls back to the widely-held ``LITELLM_MASTER_KEY`` (below).
The boundary therefore has to be the reverse proxy: the public vhost must
delete ``X-User-Email`` and ``X-User-Role`` from every inbound request, so they
can only ever be set by a caller already on the box. As of 2026-08-03
``deploy/hostinger/caddy/Caddyfile`` does NOT do that — the required patch is
in the WS-0 live-access-defects write-up and applying it is an owner action
(``work_plan.md`` §6). Everything in this module is defence in depth behind it.

Two secrets, deliberately distinct (BO-2 residual #4):

    GATEWAY_INTERNAL_TOKEN  -- SERVICE IDENTITY. Proves the caller *is* the
        platform (Next.js proxy, cron, CI) and grants SERVICE_ACCESS, i.e.
        everything. Never handed to agent code. Checked by
        require_internal_auth() and get_current_user()'s Bearer branch.

    LITELLM_MASTER_KEY      -- the /v1 API key. Handed to every agent's BYOK
        client so completions route through the gateway. Checked ONLY by
        require_llm_api_auth(). Treat as semi-public within the deployment.

They used to be one value: the identity token fell back to the LLM key, and
the orchestrator handed agents whichever it resolved to. Every agent therefore
held the identity token and could call /admin/* to grant itself anything.

Empty string disables Bearer auth (never accept all callers — use SSO headers
instead).
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Collection
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from acb_common import get_logger
from acb_common.db import bind_tenant

from acb_auth.access import (
    SERVICE_ACCESS,
    identity_cutover_enabled,
    resolve_access,
    resolve_identity,
)
from acb_auth.permissions import NO_ACCESS
from acb_auth.roles import UserContext, UserRole, _coerce_role

_log = get_logger("acb_auth.deps")

# ---------------------------------------------------------------------------
# Internal service token (server → gateway calls, e.g. Next.js proxy route)
# ---------------------------------------------------------------------------

def _get_llm_api_token() -> str:
    """The API key for the gateway's OpenAI-compatible ``/v1`` endpoint.

    This one is handed OUT — every agent's BYOK client presents it to route
    completions through the gateway. Treat it as semi-public within the
    deployment: anything it authenticates, an agent can do.
    """
    tok = os.getenv("LITELLM_MASTER_KEY", "").strip()
    if not tok:
        try:
            from acb_common import get_settings  # noqa: PLC0415
            tok = (get_settings().litellm_master_key or "").strip()
        except Exception:  # noqa: BLE001
            pass
    return tok


def _resolve_internal_token() -> tuple[str, bool]:
    """``(token, is_llm_fallback)`` — the service-identity ladder, once.

    THE ladder. :func:`_get_internal_token` and
    :func:`_internal_token_is_llm_fallback` are both thin readers of this
    tuple, deliberately: the second used to re-implement the precedence, and a
    later change to one copy would have left the flag answering about a
    resolution that no longer happens. The consequence of that drift is not
    academic — ``get_current_user`` uses the flag to REFUSE identity, so a
    stale copy would start refusing a legitimately provisioned token, which on
    a flagged box is every member locked out.

    Silent by design: the warning belongs to the caller that actually resolves
    a token for use, not to every predicate that asks about it.
    """
    tok = os.getenv("GATEWAY_INTERNAL_TOKEN", "").strip()
    if not tok:
        try:
            from acb_common import get_settings  # noqa: PLC0415
            tok = (get_settings().gateway_internal_token or "").strip()
        except Exception:  # noqa: BLE001
            pass
    if tok:
        return tok, False
    return _get_llm_api_token(), True


def _get_internal_token() -> str:
    """The SERVICE IDENTITY token: proof that a caller *is* the platform.

    Grants ``SERVICE_ACCESS`` (everything), so it must never be given to code
    that runs untrusted input — which is exactly why it is now separate from
    the ``/v1`` API key. Previously this fell back to ``LITELLM_MASTER_KEY``,
    and the orchestrator handed agents whichever value this resolved to, so
    every agent held the identity token and could call ``/admin/*`` to grant
    itself anything. That is BO-2 residual #4.

    Precedence: ``GATEWAY_INTERNAL_TOKEN`` → (unprovisioned only)
    ``LITELLM_MASTER_KEY`` with a warning → ``""``, which disables Bearer
    auth entirely.

    The fallback is retained ONLY so a deployment that has not set
    ``GATEWAY_INTERNAL_TOKEN`` keeps working rather than 401ing every internal
    call on upgrade. While it is in effect the two secrets are the same value
    and the separation does not exist, so it warns on every resolution — a
    silent fallback here would be the vulnerability wearing a fix's clothes.
    """
    tok, is_fallback = _resolve_internal_token()
    if is_fallback and tok:
        _log.warning(
            "gateway_internal_token_unset",
            detail=(
                "Falling back to LITELLM_MASTER_KEY for service identity. That "
                "key is handed to every agent, so any agent can currently "
                "authenticate as the platform. Set GATEWAY_INTERNAL_TOKEN to a "
                "distinct secret."
            ),
        )
    return tok


def _internal_token_is_llm_fallback() -> bool:
    """True when service identity is currently BORROWING the ``/v1`` API key.

    i.e. ``GATEWAY_INTERNAL_TOKEN`` is unprovisioned and
    :func:`_get_internal_token` is therefore returning ``LITELLM_MASTER_KEY``.
    While that holds, the two secrets are one value and "proves it is the
    platform" means no more than "holds the key every agent holds".

    DERIVED from :func:`_resolve_internal_token`, never re-derived: this
    predicate and the token it describes have to be two readings of one
    resolution, or they can disagree.
    """
    tok, is_fallback = _resolve_internal_token()
    return is_fallback and bool(tok)


def _refuse_llm_key_identity() -> bool:
    """Opt-in hardening: the ``/v1`` API key may not stand in for identity.

    ``_get_internal_token``'s fallback to ``LITELLM_MASTER_KEY`` keeps an
    un-migrated deployment working, but while it is in effect that key — which
    every agent's BYOK client holds, and which is therefore semi-public within
    the deployment — authenticates a caller as the platform. Such a caller can
    then either assert any colleague's ``X-User-Email`` (branch 1a) or act as
    the platform itself with ``SERVICE_ACCESS`` (branch 1b).

    Setting ``GATEWAY_REFUSE_LLM_KEY_IDENTITY=1`` makes that fallback refusable
    for *identity* purposes: a Bearer that only matches because the fallback is
    in effect no longer establishes any identity at all, and the caller resolves
    exactly as an unauthenticated one would.

    **Default is FALSE — today's behaviour.** Flipping it is an OWNER-GATE
    action (``work_plan.md`` §6: "any deploy that changes auth behaviour"), and
    it is only safe once ``GATEWAY_INTERNAL_TOKEN`` is provisioned in BOTH
    ``/opt/acb/app/.env`` and the workbench's ``.env.local`` — the Next.js BFF
    mirrors the same fallback (``workbench/control_plane/src/lib/gateway.ts``),
    so flipping this while the token is unset would 401 every signed-in member.
    Once the token IS provisioned the flag is inert, because the fallback is
    then never in effect.

    ``require_internal_auth`` and ``require_llm_api_auth`` are deliberately NOT
    covered: they answer "may this call happen", and a mis-provisioned box
    should degrade to "nobody is anybody" rather than to "the Bearer check is
    disabled", which is what an empty expected token would mean.
    """
    return os.getenv(
        "GATEWAY_REFUSE_LLM_KEY_IDENTITY", ""
    ).strip().lower() in {"1", "true", "yes", "on"}


def allowed_email_domain() -> str:
    """The company's own sign-in domain, normalised (``fracktal.in``).

    The one **live** reader of ``ALLOWED_EMAIL_DOMAIN``, so the default cannot
    drift between the two branches of :func:`get_current_user` and the surfaces
    that need to *display* the distinction — the sign-in request queue marks an
    off-domain address, and the browser must never re-derive a policy the
    server owns. (``acb_common.settings`` also declares an
    ``allowed_email_domain`` field with the same default; it has **no**
    consumers today. If anything ever reads it, collapse the two — two
    declarations of one policy is how a default drifts.)

    ⚠️ **This is a label, not a boundary.** Branch 1a only LOGS a mismatch
    (``auth.identity_domain_mismatch``) and carries on: the internal-token
    holder is trusted to say who it is acting as, and refusing here would lock
    out any member whose sign-in address is off-domain. Nor does the Entra
    tenant pin exclude these addresses — a B2B guest **is** a directory member
    and authenticates against the tenant like anybody else.
    """
    return os.getenv("ALLOWED_EMAIL_DOMAIN", "fracktal.in").lower().lstrip("@")


def is_company_email(email: str | None) -> bool:
    """Is this address inside :func:`allowed_email_domain`?"""
    return bool(email) and (email or "").lower().endswith(
        "@" + allowed_email_domain()
    )


def _trust_unverified_sso_headers() -> bool:
    """Escape hatch: when true, X-User-Email is trusted even without a valid
    internal Bearer token (the pre-2026-07 behaviour).

    Default is FALSE. A bare X-User-Email (no Bearer) is spoofable by anyone who
    can reach the gateway directly, so trusting it was a cross-account auth
    bypass. Flip this to 1/true ONLY as a temporary rollback if a token MISMATCH
    between the Next.js proxy and the gateway is turning legitimate proxied
    traffic anonymous — the real fix is to align the two tokens.
    """
    return os.getenv(
        "GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", ""
    ).strip().lower() in {"1", "true", "yes", "on"}



async def _with_resolved_access(user: UserContext) -> UserContext:
    """Attach the member's DB-resolved permission set to a UserContext.

    Best-effort by construction: :func:`acb_auth.access.resolve_access` never
    raises, degrading to the legacy executive/employee mapping when the access
    tables are absent and to no-access when the member is unknown. Results are
    cached for 60s, so this costs one indexed query per member per minute
    rather than one per request.

    **This is the one place that passes ``record_request=True``** — an
    authenticated identity reaching a request IS the knock, and the resolver's
    unprovisioned branch is the only place the platform ever learns about it
    (spec ``colleague_onboarding.md`` §6, done-when 3). Do not copy the flag
    onto a fan-out over other people's emails: ``routes/rooms.py`` and
    ``resolve_session_access`` resolve participants, not callers, and filing
    those would queue people who never tried to sign in. The 60s cache above
    is also what bounds the write rate.
    """
    if not user.email:
        return user

    # MT-1c / H2 — the ONE place a request binds its tenant
    # (`saas_multitenancy_handover.md` H2: "bind once, centrally, from the
    # authenticated session", never from a header/query/body — R11; the
    # organization_id below came from a DB read keyed on the authenticated
    # email, not from the caller). `tenant_session()` then needs no argument
    # anywhere on the request path. No release here: the gateway's
    # TenantScopeMiddleware opened this request's scope and releases it after
    # the response, so the binding cannot outlive the request even on servers
    # that reuse a task. An unresolved identity binds nothing, and a converted
    # handler then fails closed with TenantUnbound rather than defaulting.
    if identity_cutover_enabled():
        # ── H6 slice 3b — the read cutover (DARK behind IDENTITY_CUTOVER) ──
        # TWO-PHASE resolve, so sign-in survives H3's phase-4 RLS:
        #   1. IDENTITY LEG — `resolve_identity` reads the RLS-EXEMPT
        #      `user_identity ⋈ org_membership` on the UNBOUND session (the
        #      tenant is what it resolves), filtered to an ACTIVE membership.
        #      Under phase-4 the old `app_user` reads returned 0 rows here and
        #      sign-in bricked; the exempt tables do not.
        #   2. BIND the resolved tenant — BEFORE the role leg, so `app_user`
        #      becomes visible to it.
        #   3. ROLE LEG — `resolve_access` reads roles/permissions from
        #      `app_user` on the now-BOUND session, UNCHANGED from today (RBAC
        #      re-key is slice 4). It remains the ACCESS authority: a residual
        #      shadow orphan resolves to bind-with-no-access (fail-closed),
        #      never a wrong-admit (§H6).
        user_id, organization_id = await resolve_identity(user.email)
        if organization_id:
            bind_tenant(organization_id)
        access = await resolve_access(
            user.email, legacy_role=user.role.value, record_request=True,
        )
        enriched = user.with_access(
            access, user_id=user_id, organization_id=organization_id
        )
    else:
        # Flag OFF — byte-identical to today: same calls, same order, same SQL
        # (`resolve_access` first, then `resolve_identity`, then bind).
        access = await resolve_access(
            user.email, legacy_role=user.role.value, record_request=True,
        )
        user_id, organization_id = await resolve_identity(user.email)
        enriched = user.with_access(
            access, user_id=user_id, organization_id=organization_id
        )
        if enriched.organization_id:
            bind_tenant(enriched.organization_id)

    # Keep the legacy coarse role consistent with the org model, so a member
    # promoted to `admin` in the members UI immediately passes the
    # require_role(EXECUTIVE) routes that have not migrated to permissions yet.
    #
    # Upgrade-only, deliberately: the Next.js proxy still derives X-User-Role
    # from EXECUTIVE_EMAILS, and an admin listed there who has never signed in
    # has no app_user row to resolve. Letting the DB *downgrade* the header
    # would lock exactly that person out during rollout.
    if enriched.role is not UserRole.EXECUTIVE and access.has("admin:settings:manage"):
        return UserContext(
            email=enriched.email,
            role=UserRole.EXECUTIVE,
            user_id=enriched.user_id,
            organization_id=enriched.organization_id,
            access=access,
        )
    return enriched


async def get_current_user(
    x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
    x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UserContext:
    """Resolve identity from SSO-injected headers or an internal Bearer token.

    Priority:
    1. If ``Authorization: Bearer <token>`` matches the internal token →
       synthetic ``UserContext(email="system:internal", role=AGENT)``.
    2. If ``X-User-Email`` is set → resolve from SSO headers (normal user flow).
    3. Otherwise → anonymous ``UserContext(email=None, role=EMPLOYEE)``.

    Every branch also resolves the caller's effective access (org roles +
    per-user overrides) onto the context, so routes can call
    ``user.has_permission(...)`` without a second lookup.

    Never raises — missing/wrong headers resolve to the lowest-privilege role
    and an empty permission set. Enforcement is done by require_role() /
    require_permission().
    """
    # 1. Internal Bearer token (Next.js proxy, cron jobs, CI)
    bearer_ok = False
    if authorization and authorization.startswith("Bearer "):
        submitted = authorization.removeprefix("Bearer ").strip()
        expected = _get_internal_token()
        # Only accept if expected is non-empty AND tokens match.
        if expected and secrets.compare_digest(submitted, expected):
            bearer_ok = True

    # 1'. Opt-in: a Bearer that only matched because service identity fell back
    #     to LITELLM_MASTER_KEY establishes NOTHING. Dropping bearer_ok here
    #     rather than in each branch means the caller then resolves exactly as
    #     an unauthenticated one — branch 2 refuses their X-User-Email because
    #     an internal token IS configured (the fallback), and a bare Bearer
    #     resolves anonymous instead of to SERVICE_ACCESS. Default off, so this
    #     is one os.getenv on bearer-matched calls and nothing else.
    if bearer_ok and _refuse_llm_key_identity() and _internal_token_is_llm_fallback():
        _log.warning(
            "auth.llm_key_identity_refused",
            asserted_email=(x_user_email or "")[:64],
            detail=(
                "GATEWAY_REFUSE_LLM_KEY_IDENTITY is set and "
                "GATEWAY_INTERNAL_TOKEN is not, so the Bearer presented is the "
                "LLM API key every agent holds. Refusing to treat it as "
                "service identity. Provision GATEWAY_INTERNAL_TOKEN."
            ),
        )
        bearer_ok = False

    # 1a. Bearer-matched call WITH user identity headers → real user context.
    #     This is the normal browser flow: Next.js proxy authenticates the
    #     session (NextAuth Google SSO) and forwards the verified email + role
    #     alongside the internal Bearer token.  We trust the identity because
    #     only the Next.js server can produce a valid Bearer token.
    if bearer_ok and x_user_email:
        allowed_domain = allowed_email_domain()
        email = x_user_email
        if not is_company_email(email):
            # NOT a rejection, deliberately: the token holder is trusted to say
            # who it is acting as, and refusing here would lock out any member
            # whose sign-in address is off-domain. The mismatch is only ever
            # LOGGED — the `or x_user_email` below is what makes that explicit,
            # since the assignment above otherwise reads as a check it is not.
            _log.warning(
                "auth.identity_domain_mismatch",
                asserted_email=email[:64],
                allowed_domain=allowed_domain,
            )
            email = None
        return await _with_resolved_access(
            UserContext(
                email=email or x_user_email,  # still trust Next.js but flag domain mismatch
                role=_coerce_role(x_user_role),
            )
        )

    # 1b. Bearer-matched call WITHOUT user headers → internal service call.
    #     Used by cron jobs, CI pipelines, and legacy LangGraph batch mode
    #     that predates the identity-forwarding fix.
    #
    #     Granted everything: whoever holds the internal token can already
    #     assert any X-User-Email, so a narrower set would be theatre.
    if bearer_ok:
        return UserContext(
            email="system:internal",
            role=UserRole.AGENT,
            access=SERVICE_ACCESS,
        )

    # 2. SSO headers WITHOUT a valid Bearer token.
    #    X-User-Email is only trustworthy when it arrives WITH the internal Bearer
    #    token (branch 1a) — that proves it came from the Next.js proxy, which
    #    authenticated the Google/NextAuth session. A bare X-User-Email is
    #    spoofable by anyone who can reach the gateway directly (it is exposed via
    #    Caddy), so trusting it was a full cross-account auth bypass. When an
    #    internal token IS configured we refuse to authenticate such a caller.
    #    When none is configured we preserve the old trust to avoid bricking an
    #    unprovisioned/dev gateway (mirroring require_internal_auth's fail-open
    #    contract); the escape hatch forces trust for a token-mismatch rollback.
    if (
        x_user_email
        and _get_internal_token()
        and not _trust_unverified_sso_headers()
    ):
        return UserContext(email=None, role=UserRole.EMPLOYEE, access=NO_ACCESS)

    # Domain enforcement (defence in depth) for the fail-open / escape-hatch
    # paths that still trust the header. An off-domain address is treated as
    # anonymous rather than raising — callers use require_role() to enforce.
    # Note this is the OPPOSITE of branch 1a, which only logs: there the Bearer
    # already proved the caller is the proxy, here nothing has.
    email = x_user_email if is_company_email(x_user_email) else None

    return await _with_resolved_access(
        UserContext(email=email, role=_coerce_role(x_user_role))
    )


async def require_internal_auth(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Reject callers that do not present a valid internal Bearer token.

    Unlike :func:`get_current_user` (which never rejects — it only *labels* the
    caller), this dependency 401s anyone without the internal service token. Use
    it on server-to-server endpoints that must not be world-reachable — notably
    the OpenAI-compatible ``/v1/chat/completions`` proxy, which otherwise bills
    the server's stored provider keys for any anonymous caller.

    SSO ``X-User-*`` headers are deliberately NOT accepted here: they are
    spoofable without the Next.js proxy, and every legitimate caller of these
    endpoints (MAF agents, Copilot BYOK, mem0/graphiti, the Next.js server
    routes) already forwards ``Authorization: Bearer <internal token>``.

    If no internal token is configured (``_get_internal_token()`` returns ""),
    Bearer auth is disabled and the call is allowed — matching that function's
    documented contract. This means a mis-provisioned deployment fails OPEN
    rather than bricking; since the endpoint was fully open before this guard,
    that is strictly no worse, while any real deployment (which always sets
    ``LITELLM_MASTER_KEY``) is now closed.
    """
    expected = _get_internal_token()
    if not expected:
        return  # Bearer auth disabled (unconfigured) — preserve prior behaviour.
    if authorization and authorization.startswith("Bearer "):
        submitted = authorization.removeprefix("Bearer ").strip()
        if submitted and secrets.compare_digest(submitted, expected):
            return
    raise HTTPException(status_code=401, detail="Unauthorized")


async def require_llm_api_auth(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Guard the OpenAI-compatible ``/v1`` endpoint.

    Accepts **either** the ``/v1`` API key (``LITELLM_MASTER_KEY``, which every
    agent's BYOK client holds) **or** the service identity token (the Next.js
    server and internal jobs route completions too).

    This is deliberately weaker than :func:`require_internal_auth` and must
    stay that way. ``/v1`` bills the server's stored provider keys, so it needs
    authentication — but it is the one endpoint agents are *supposed* to reach,
    so gating it on the identity token is what forced agents to hold that token
    in the first place. Separating the two is the fix.
    """
    submitted = ""
    if authorization and authorization.startswith("Bearer "):
        submitted = authorization.removeprefix("Bearer ").strip()

    accepted = [t for t in (_get_llm_api_token(), _get_internal_token()) if t]
    if not accepted:
        return  # Unconfigured deployment — same fail-open contract as below.
    if submitted and any(secrets.compare_digest(submitted, t) for t in accepted):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def require_role(*allowed: UserRole) -> Depends:
    """Return a FastAPI Depends that 403s if the caller role is not in allowed.

    Unchanged from the pre-org-access-control behaviour on purpose: every route
    written against the coarse executive/employee split keeps working exactly
    as before. New routes should prefer :func:`require_permission`.
    """
    allowed_set = frozenset(allowed)

    async def _check(user: Annotated[UserContext, Depends(get_current_user)]) -> UserContext:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Forbidden: role '{user.role}' is not allowed. "
                    f"Required: {sorted(r.value for r in allowed_set)}."
                ),
            )
        return user

    return Depends(_check)


def require_permission(*permissions: str) -> Depends:
    """Return a FastAPI Depends that 403s unless the caller holds **all** of them.

    Anonymous callers get 401 rather than 403 — "you are not signed in" and
    "you are signed in but not allowed" are different problems and the
    frontend routes them differently (sign-in redirect vs. access-denied page).

    The 403 body names the missing permission. That is a deliberate trade:
    a signed-in member learning the *name* of a permission they lack leaks
    nothing an admin screen wouldn't tell them, and without it every access
    bug becomes a support ticket.

        @router.get("/chats", dependencies=[require_permission("feature:whatsapp")])
    """
    required = tuple(permissions)

    async def _check(user: Annotated[UserContext, Depends(get_current_user)]) -> UserContext:
        if not user.email:
            raise HTTPException(status_code=401, detail="Authentication required")
        missing = [p for p in required if not user.has_permission(p)]
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: missing permission(s) {sorted(missing)}.",
            )
        return user

    return Depends(_check)


def require_feature(slug: str) -> Depends:
    """Sugar for ``require_permission(f"feature:{slug}")``."""
    return require_permission(f"feature:{slug}")


def _route_template(request: Request) -> str:
    """The matched route's path template, e.g. ``/email/oauth/{provider}/callback``.

    Always the template, never the concrete URL: matching on the URL would let
    a path parameter be crafted to spell an exempt path.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def require_authenticated(public: Collection[str] = ()) -> Depends:
    """App-wide default-deny: 401 unless the caller proved who they are.

    BO-2 residual #1. ``get_current_user`` never rejects — it *labels*, which
    means every route was open unless it remembered to opt in to a guard. The
    failure mode of opt-in security is the route nobody opted in, and that is
    exactly what happened: ``/agent/workspace/{id}/history`` and
    ``/promote`` were reading and writing agent workspaces anonymously.

    Attach once at ``FastAPI(dependencies=[...])`` so a route added tomorrow is
    covered without anyone remembering anything.

    "Authenticated" means either a valid internal bearer token or a
    domain-verified ``X-User-Email`` that arrived with one — i.e. anything
    ``get_current_user`` resolved to a non-empty email. It says nothing about
    *authorization*; ``require_permission`` and friends still do that.

    ``public`` holds route templates that must stay reachable anonymously.
    Each one authenticates itself by another means (provider signature, HMAC
    state, shared secret) or is a liveness probe. Gating them would not
    restrict access, it would break ingestion and sign-in.
    """
    public_paths = frozenset(public)

    async def _check(
        request: Request,
        user: Annotated[UserContext, Depends(get_current_user)],
    ) -> UserContext:
        if _route_template(request) in public_paths:
            return user
        if not user.email:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user

    return Depends(_check)


def require_feature_router(
    slug: str, *, exempt: Collection[str] = ()
) -> Depends:
    """Gate a whole feature surface at the router, with declared exemptions.

    Attach to an ``APIRouter(dependencies=[...])`` so every route under a
    prefix is covered by construction. A new endpoint added to that router is
    then gated by default, which is the property that matters: the failure
    mode of per-route gating is the route someone forgets.

    ``exempt`` holds **route path templates** (e.g.
    ``"/email/oauth/{provider}/callback"``) that must stay reachable without a
    member. These are not oversights — a provider webhook and an OAuth
    redirect arrive with no session and no internal token, so gating them
    would break message ingestion and account linking rather than merely
    restrict them. Listing them explicitly keeps the set greppable and small;
    matching on the *template* rather than the concrete URL means a path
    parameter can never be used to slip past the gate.

        router = APIRouter(
            prefix="/whatsapp",
            dependencies=[require_feature_router("whatsapp",
                                                 exempt=["/whatsapp/webhook"])],
        )

    Exempting a path here removes the *feature* check only; whatever
    authentication that endpoint already does for itself (signature
    verification, HMAC-signed OAuth state) is untouched.
    """
    permission = f"feature:{slug}"
    exempt_paths = frozenset(exempt)

    async def _check(
        request: Request,
        user: Annotated[UserContext, Depends(get_current_user)],
    ) -> UserContext:
        if _route_template(request) in exempt_paths:
            return user
        if not user.email:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not user.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: missing permission '{permission}'.",
            )
        return user

    return Depends(_check)


def require_any_permission(*permissions: str) -> Depends:
    """Like :func:`require_permission` but any single match suffices.

    For endpoints that legitimately serve two audiences — e.g. an approvals
    feed readable by both the Approvals pane and platform admins.
    """
    required = tuple(permissions)

    async def _check(user: Annotated[UserContext, Depends(get_current_user)]) -> UserContext:
        if not user.email:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not any(user.has_permission(p) for p in required):
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: requires one of {sorted(required)}.",
            )
        return user

    return Depends(_check)


def assert_can_run_agent(user: UserContext, agent_name: str) -> None:
    """Raise 403 unless ``user`` may run ``agent_name``.

    A function rather than a dependency because the agent name arrives in the
    request body, not the path — the run endpoints call this after parsing.
    Seam 2 of the spec's enforcement table.
    """
    if user.role is UserRole.AGENT and user.has_permission("*"):
        return  # internal service principal
    if not user.can_run_agent(agent_name):
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: no access to agent '{agent_name}'.",
        )


async def assert_can_run_agent_in_session(
    user: UserContext, agent_name: str, session_id: str | None,
) -> None:
    """:func:`assert_can_run_agent`, then the shared-session authority rule.

    A shared run acts with the intersection of every participant's access
    (``groups_sessions_authority.md`` §3), so the agent must be runnable by
    the ROOM, not only by the typer — otherwise its output lands in a
    transcript read by members who may not run it. The fold engages only
    when a second member exists; a solo or pre-133 session is exactly the
    plain gate. The refusal names the rule so the cap is never a mystery.
    """
    assert_can_run_agent(user, agent_name)
    if user.role is UserRole.AGENT and user.has_permission("*"):
        return  # internal service principal — no room to intersect
    if not session_id:
        return
    from acb_auth.access import resolve_session_access

    folded, members = await resolve_session_access(session_id, user.email)
    if len(members) > 1 and not folded.can_run_agent(agent_name):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Forbidden: agent '{agent_name}' is not runnable by every "
                f"participant of this shared session — a shared run acts at "
                f"the intersection of all participants' access."
            ),
        )