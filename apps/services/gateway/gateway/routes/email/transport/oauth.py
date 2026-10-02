"""Transport · OAuth — Gmail/Microsoft connect flow: authorize, callback, token
exchange, and provider identity lookup.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.1 (EM-T1a).

Both legs run behind the member's session. The browser reaches each one
through a workbench BFF route, which attaches the session and passes on the
gateway's 302:

* ``/api/email/oauth/{provider}/authorize`` calls :func:`oauth_authorize`.
* ``/api/email/oauth/{provider}/callback`` calls :func:`oauth_callback`. It is
  also the redirect URI that the provider sees (:func:`_build_redirect_uri`).

⚠️ **Why the callback is not public (spec risk R-4).** A signed state alone is
a bearer value for ten minutes and is not tied to a browser. An attacker could
start the flow, get a victim to consent, and attach the mailbox of the victim
to the account of the attacker. The callback closes that: the member of the
session must be the member in the state.
"""

from __future__ import annotations

import json
import os
import re
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from acb_auth import UserContext, get_current_user
from email_ingestion.import_window import DEFAULT_IMPORT_MONTHS, since_for_months
from email_ingestion.providers.app_credentials import MICROSOFT_OAUTH_BASE, oauth_app
from email_ingestion.providers.gmail import GMAIL_SCOPES
from email_ingestion.providers.outlook import GRAPH_SCOPES
from fastapi import Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from gateway.routes.email.core import _default_label, _log, _tenant_session, router
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_oauth_state,
    verify_oauth_state,
)
from pydantic import BaseModel
from sqlalchemy import text


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str


#: A provider ``error`` is shown on the callback page, so only a plain code
#: token passes. Anything else becomes ``provider_error``.
_PROVIDER_ERROR = re.compile(r"^[a-z0-9_]{1,64}$")

#: ``redirect_after`` rides inside the signed state, and the verifier refuses
#: a state longer than 4096 characters. Past this cap the authorize leg answers
#: 400, so a member never consents only to get ``invalid_state``. The signer
#: also refuses an over-long state, which covers text that JSON escapes.
_MAX_REDIRECT_AFTER = 2048

#: ``import_months`` passes only as one digit from 0 to 6 (EM-T6a item 7). The
#: route reads it as text, so ``x`` is a 400 and never a 422 of FastAPI. Use
#: ``fullmatch``: with ``match``, ``$`` also accepts a final newline.
_IMPORT_MONTHS = re.compile(r"[0-6]")

#: A ``login_hint`` passes only when it parses as one plain address.
_EMAIL_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

#: The callback reads only a Microsoft error code from ``error_description``.
#: The rest of that text is not ours, and it never reaches the Location.
_AADSTS_CODE = re.compile(r"AADSTS(\d{5,6})")
#: The tenant of the customer must approve the app (EM-T3a item 4).
_ADMIN_CONSENT_CODES = frozenset({"90094", "90095", "65001"})
#: The member said no on the consent screen.
_DECLINED_CODES = frozenset({"65004"})

_NOT_CONFIGURED = {
    "gmail": (
        "Gmail OAuth is not configured. Go to Integrations → APIs → "
        "'Gmail OAuth' and enter your Google Cloud OAuth client ID "
        "and secret. Instructions are provided there."
    ),
    "microsoft": (
        "Microsoft OAuth is not configured. Go to Integrations → APIs → "
        "'Microsoft OAuth' and enter your Azure App client ID "
        "and secret. Instructions are provided there."
    ),
}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    user: UserContext = Depends(get_current_user),
    redirect_after: str = Query(default=""),
    login_hint: Annotated[str | None, Query()] = None,
    import_months: Annotated[str | None, Query()] = None,
):
    """Start OAuth flow for an email provider.

    **This route is gated and stays gated.** It is reached through the workbench
    BFF (``/api/email/oauth/{provider}/authorize``), which holds the session,
    attaches the internal bearer plus ``X-User-Email``, and re-issues the 302 it
    gets back so the browser lands on the provider's consent screen. Adding it to
    ``main.PUBLIC_ROUTES`` would be the dangerous repair: the state binds the
    mailbox to its member, so an anonymous authorize leg would let anyone
    attach a mailbox to somebody else's account.

    The organization and the member come from the session and from nowhere
    else (``user_management_contract.md`` R11). With either one missing, the
    route refuses with 403. The ``user_email`` query fallback and the
    ``anonymous`` member are gone (EM-T1a).

    The URL carries ``login_hint``, by default the email of the session. A
    ``login_hint`` query replaces it only when it parses as an address, and
    a malformed one is dropped (EM-T3a item 3).

    ``import_months`` is the import range of a new mailbox, 0 to 6 months, and
    1 when absent (EM-T6a, D-EM-11). Any other value answers 400 before the
    route signs a state. The state carries it to the callback.
    """
    if not user.organization_id or not user.email:
        raise HTTPException(
            status_code=403,
            detail="Connecting a mailbox needs a signed-in member of an organization.",
        )
    redirect_uri = _build_redirect_uri(provider)
    if provider not in ("gmail", "microsoft"):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    if len(redirect_after) > _MAX_REDIRECT_AFTER:
        raise HTTPException(status_code=400, detail="redirect_after is too long.")
    months = DEFAULT_IMPORT_MONTHS
    if import_months is not None:
        if not _IMPORT_MONTHS.fullmatch(import_months):
            raise HTTPException(
                status_code=400,
                detail="import_months is a whole number of months from 0 to 6.",
            )
        months = int(import_months)
    try:
        state = sign_oauth_state(
            org=str(user.organization_id),
            member=user.email,
            provider=provider,
            redirect_after=redirect_after,
            import_months=months,
        )
    except SigningUnavailable:
        _log.error("email.oauth_state_secret_unusable")
        raise HTTPException(
            status_code=503,
            detail=(
                "Email connect is unavailable: the gateway session secret is "
                "not set. An operator must set GATEWAY_SESSION_SECRET."
            ),
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="The connect request is too long or malformed.",
        ) from None

    hint = _login_hint(login_hint) or _login_hint(user.email)
    app = oauth_app(provider)
    if not app.client_id:
        raise HTTPException(status_code=400, detail=_NOT_CONFIGURED[provider])
    params = {
        "client_id": app.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if provider == "gmail":
        base = "https://accounts.google.com/o/oauth2/v2/auth"
        params.update({
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        })
    else:
        # The authority is always `common`, so a sign-in tenant cannot make
        # mail single-tenant (EM-T3a item 2). The scopes are the ones the
        # provider asks for on refresh, so the two legs cannot drift.
        base = f"{MICROSOFT_OAUTH_BASE}/authorize"
        params["scope"] = " ".join(GRAPH_SCOPES)
    if hint:
        # A hint, never an identity: the callback still binds the member of
        # the session. No `prompt=select_account` (EM-T3a item 3).
        params["login_hint"] = hint
    auth_url = f"{base}?{urlencode(params)}"

    # ⚠️ No `response_mode=form_post`. The provider would then POST the code
    # cross-site, and the browser drops the Lax session cookie on that POST, so
    # the BFF callback could not see the member (EM-T1a item 3).
    return RedirectResponse(auth_url, status_code=302)


class OAuthAppInfo(BaseModel):
    """The public facts of the mail OAuth app. It never holds the secret."""

    provider: str
    client_id: str
    redirect_uri: str


@router.get("/oauth/{provider}/app")
async def oauth_app_info(
    provider: str,
    user: UserContext = Depends(get_current_user),
) -> OAuthAppInfo:
    """The client ID and the redirect URI of the mail app (EM-T3b).

    The guided page for admin approval builds the admin-consent link from
    these two values, so the client ID is never a constant in the browser.
    Both values are public: Microsoft shows them in every authorize URL. The
    client secret never leaves settings.

    Only Microsoft has an admin-consent step, so another provider is a 404.
    With no client ID in settings, the route answers 503.
    """
    if not user.organization_id or not user.email:
        raise HTTPException(
            status_code=403,
            detail="This needs a signed-in member of an organization.",
        )
    if provider != "microsoft":
        raise HTTPException(status_code=404, detail="Admin approval applies to Microsoft only.")
    app = oauth_app(provider)
    if not app.client_id:
        raise HTTPException(
            status_code=503,
            detail="Microsoft mail is not set up on this deployment.",
        )
    return OAuthAppInfo(
        provider=provider,
        client_id=app.client_id,
        redirect_uri=_build_redirect_uri(provider),
    )


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    user: UserContext = Depends(get_current_user),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: Annotated[str | None, Query()] = None,
):
    """Handle OAuth callback — exchange code for tokens and redirect to workbench.

    Reached through the BFF route ``/api/email/oauth/{provider}/callback``, so
    the session is present here and ``user`` is the member who clicked
    Connect. The checks run in this order, and each failure redirects with
    ``error=invalid_state`` so that no failure tells a caller which check it
    failed:

    1. the MAC, the purpose and the expiry of the state;
    2. the provider in the state is the provider in the path;
    3. the member of the session is the member in the state (R-4);
    4. ``resolve_identity`` of that member returns the organization in the
       state. A failed read is a refusal, never a write.

    Every write runs inside one ``tenant_session(org)``. The seam commits on
    exit, so there is no ``commit()`` here. A statement after a commit in the
    middle of the block would run with no tenant bound (``SET LOCAL`` ends at
    commit).
    """
    callback_page = f"{_workbench_public_url()}/email/oauth/callback"

    def _bounce(reason: str) -> RedirectResponse:
        return RedirectResponse(
            f"{callback_page}?{urlencode({'error': reason})}", status_code=302,
        )

    if not code:
        # The provider refused (for example, the tenant of the customer needs
        # admin consent) and sent `error` with no `code`. Show the callback
        # page, never a raw 422. EM-T3b owns the guided page for admin consent.
        if not error:
            return _bounce("invalid_state")
        return _bounce(_consent_error_reason(error, error_description))

    claims = await _verified_claims(provider, state, user)
    if claims is None:
        return _bounce("invalid_state")
    org, member = claims["org"], claims["member"]

    redirect_after = claims.get("redirect_after", "")
    redirect_uri = _build_redirect_uri(provider)

    # Exchange code for tokens
    try:
        if provider == "gmail":
            token_data = await _exchange_gmail_token(code, redirect_uri)
        elif provider == "microsoft":
            token_data = await _exchange_msft_token(code, redirect_uri)
        else:
            return _bounce(f"unknown_provider_{provider}")
    except Exception as exc:
        _log.error("Token exchange failed: %s", exc)
        return _bounce("token_exchange_failed")

    # Get user email from provider
    try:
        mailbox = await _get_provider_email(provider, token_data["access_token"])
    except Exception as exc:
        _log.error("Failed to get provider email: %s", exc)
        return _bounce("email_fetch_failed")

    # The blob holds the tokens of the member and nothing else. The providers
    # read the app credentials from settings at refresh (EM-T3a item 1).
    # Store in encrypted DB
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    encrypted_creds = store.encrypt(json.dumps(token_data))

    try:
        account_id = await _save_account(
            org=org,
            member=member,
            owner=(user.email or "").strip(),
            provider=provider,
            mailbox=mailbox,
            encrypted_creds=encrypted_creds,
            import_months=claims["import_months"],
        )
    except Exception as exc:
        _log.error("email.oauth_account_save_failed", error=str(exc)[:200])
        return _bounce("account_save_failed")

    # Start (or restart) background sync for the account. This runs after the
    # tenant block, so the row is committed first. The loop binds the
    # organization of the verified state (EM-T1b-1 item 5).
    try:
        from email_ingestion.scheduler import refresh_account_sync
        await refresh_account_sync(account_id, organization_id=org)
    except Exception as exc:
        _log.warning("email.oauth_refresh_sync_failed", error=str(exc)[:200])

    # Success redirect
    params = {
        "account_id": account_id,
        "email": mailbox,
        "provider": provider,
    }
    if redirect_after:
        params["redirect_after"] = redirect_after

    return RedirectResponse(
        f"{callback_page}?{urlencode(params)}",
        status_code=302,
    )


def _provider_error_reason(error: str) -> str:
    """The provider ``error`` as the callback page may show it.

    The text is not ours, so only a plain code passes. Anything else becomes
    ``provider_error``.
    """
    raw = error.strip().lower()
    return raw if _PROVIDER_ERROR.match(raw) else "provider_error"


def _consent_error_reason(error: str, error_description: str | None) -> str:
    """The reason the callback page shows for a refusal (EM-T3a item 4).

    Only an ``AADSTS`` code is read from ``error_description``. The text
    itself is never echoed. An admin-consent code wins over ``error``. A
    declined consent, or ``access_denied`` with no known code, reads as
    ``consent_declined``. Every other error goes through
    :func:`_provider_error_reason`.
    """
    match = _AADSTS_CODE.search(error_description or "")
    code = match.group(1) if match else None
    if code in _ADMIN_CONSENT_CODES:
        return "admin_consent_required"
    if code in _DECLINED_CODES:
        return "consent_declined"
    if error.strip().lower() == "access_denied":
        return "consent_declined"
    return _provider_error_reason(error)


def _login_hint(value: str | None) -> str | None:
    """``value`` when it parses as one address, else ``None``."""
    hint = (value or "").strip()
    if len(hint) > 254 or not _EMAIL_ADDRESS.match(hint):
        return None
    return hint


async def _verified_claims(
    provider: str, state: str | None, user: UserContext,
) -> dict[str, Any] | None:
    """The claims of the state when every check passes, else ``None``.

    The caller answers every ``None`` with ``invalid_state``, so a caller
    cannot learn which check failed. The checks run in the order of the
    docstring of :func:`oauth_callback`.
    """
    claims = verify_oauth_state(state)
    if claims is None or claims["provider"] != provider:
        return None
    member = claims["member"]
    if (user.email or "").strip().lower() != member:
        _log.warning("email.oauth_member_mismatch", provider=provider)
        return None
    try:
        from acb_auth.access import resolve_identity

        _uid, resolved_org = await resolve_identity(member)
    except Exception:
        # A failed read refuses. It never writes.
        _log.warning("email.oauth_identity_unavailable", provider=provider)
        return None
    if not resolved_org or str(resolved_org) != claims["org"]:
        _log.warning("email.oauth_org_mismatch", provider=provider)
        return None
    return claims


async def _save_account(
    *,
    org: str,
    member: str,
    owner: str,
    provider: str,
    mailbox: str,
    encrypted_creds: str,
    import_months: int = DEFAULT_IMPORT_MONTHS,
) -> str:
    """Create or refresh the mailbox row inside one ``tenant_session(org)``.

    ``owner`` is the address of the session, verbatim. The reads of this
    package compare ``user_id = :uid`` with that same address. The lookup
    compares ``member`` in lower case, so a reconnect finds the row whatever
    its case. The seam commits on exit, so there is no ``commit()`` here.

    ``import_months`` binds a NEW mailbox only (EM-T6a items 9 and 10). The
    INSERT writes ``import_since``. A range of 0 imports no old mail, so the
    INSERT also marks the first import done. A reconnect ignores the range.
    """
    async with _tenant_session(org) as db:
        # An account for this member and address already exists: this is a
        # *reconnect*. Refresh the stored credentials in place rather than
        # reject a duplicate. The reconnect keeps the sync point (D-EM-13):
        # it writes neither ``last_history_id`` nor ``initial_sync_done``, and
        # it never imports the range again. Before EM-T6a it
        # wrote ``last_history_id = NULL``. Outlook ignores that cursor and no
        # code reset ``initial_sync_done``, so it never forced a full re-sync.
        existing_row = (await db.execute(
            text(
                """SELECT id FROM email_accounts
                   WHERE lower(user_id) = :member
                     AND provider = :provider
                     AND email_address = :email"""
            ),
            {"member": member, "provider": provider, "email": mailbox},
        )).fetchone()
        if existing_row:
            account_id = str(existing_row.id)
            await db.execute(
                text(
                    """UPDATE email_accounts
                       SET credentials_encrypted = :creds,
                           sync_status = 'idle',
                           sync_error = NULL,
                           updated_at = now()
                       WHERE id = :id"""
                ),
                {"creds": encrypted_creds, "id": account_id},
            )
            return account_id
        # organization_id is set explicitly. The column default reads the same
        # GUC, but a write names its tenant (R5).
        nothing_old = import_months == 0
        result = await db.execute(
            text(
                """INSERT INTO email_accounts
                   (id, user_id, provider, email_address, label,
                    avatar_color, credentials_encrypted, is_default,
                    organization_id, import_since, initial_sync_done,
                    import_phase)
                   VALUES (:id, :user_id, :provider, :email, :label,
                            :color, :creds,
                            NOT EXISTS (SELECT 1 FROM email_accounts
                                        WHERE lower(user_id) = :member),
                            CAST(:org AS uuid), :import_since,
                            :initial_sync_done, :import_phase)
                   RETURNING id"""
            ),
            {
                "id": str(uuid4()),
                "user_id": owner,
                "member": member,
                "provider": provider,
                "email": mailbox,
                "label": _default_label(provider),
                "color": "#6366f1",
                "creds": encrypted_creds,
                "org": org,
                "import_since": since_for_months(import_months),
                "initial_sync_done": nothing_old,
                "import_phase": "done" if nothing_old else None,
            },
        )
        return str(result.fetchone()[0])


def _workbench_public_url() -> str:
    """The public origin of the workbench, with no trailing slash.

    ``WORKBENCH_PUBLIC_URL`` when it is set. Otherwise it is derived from
    ``GATEWAY_PUBLIC_URL``. Since D40 the workbench is a SIBLING subdomain
    (``api.<apex>`` → ``app.<apex>``), not the bare apex, so stripping
    ``api.`` would aim the return at the marketing root.
    """
    workbench_url = os.environ.get("WORKBENCH_PUBLIC_URL")
    if not workbench_url:
        gateway_public = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost:8000")
        if gateway_public.rstrip("/") == "http://localhost:8000":
            workbench_url = "http://localhost:3001"
        elif "://api." in gateway_public:
            workbench_url = gateway_public.replace("://api.", "://app.", 1)
        else:
            workbench_url = gateway_public
    return workbench_url.rstrip("/")


def _build_redirect_uri(provider: str) -> str:
    """The OAuth redirect URI: the BFF callback route on the workbench origin.

    The authorize leg and the token exchange both call this, and the provider
    refuses the exchange unless the two values are identical.
    """
    return f"{_workbench_public_url()}/api/email/oauth/{provider}/callback"


async def _exchange_gmail_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange authorization code for Gmail OAuth tokens."""
    app = oauth_app("gmail")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": app.client_id,
                "client_secret": app.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _exchange_msft_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange authorization code for Microsoft OAuth tokens.

    The authority is ``common``, the same one the authorize leg used.
    """
    app = oauth_app("microsoft")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MICROSOFT_OAUTH_BASE}/token",
            data={
                "client_id": app.client_id,
                "client_secret": app.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _get_provider_email(provider: str, access_token: str) -> str:
    """Get the authenticated user's email from the provider."""
    async with httpx.AsyncClient() as client:
        if provider == "gmail":
            resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()["emailAddress"]
        elif provider == "microsoft":
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json().get("mail") or resp.json().get("userPrincipalName", "")
        raise ValueError(f"Unknown provider: {provider}")
