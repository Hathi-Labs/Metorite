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
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import get_settings
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


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    user: UserContext = Depends(get_current_user),
    redirect_after: str = Query(default=""),
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
    """
    if not user.organization_id or not user.email:
        raise HTTPException(
            status_code=403,
            detail="Connecting a mailbox needs a signed-in member of an organization.",
        )
    redirect_uri = _build_redirect_uri(provider)
    if provider not in ("gmail", "microsoft"):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    try:
        state = sign_oauth_state(
            org=str(user.organization_id),
            member=user.email,
            provider=provider,
            redirect_after=redirect_after,
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

    if provider == "gmail":
        settings = get_settings()
        client_id = settings.gmail_oauth_client_id or os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
        if not client_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Gmail OAuth is not configured. Go to Integrations → APIs → "
                    "'Gmail OAuth' and enter your Google Cloud OAuth client ID "
                    "and secret. Instructions are provided there."
                ),
            )
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={client_id}"
            "&response_type=code"
            "&scope=https://mail.google.com/"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
            "&access_type=offline"
            "&prompt=consent"
        )
    elif provider == "microsoft":
        settings = get_settings()
        # Prefer dedicated email OAuth creds; fall back to sign-in auth creds (shared app registration)
        client_id = (
            settings.msft_oauth_client_id
            or os.environ.get("MSFT_OAUTH_CLIENT_ID", "")
            or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_ID", "")
        )
        # Tenant ID: use MICROSOFT_TENANT_ID (or AUTH_MICROSOFT_ENTRA_ID_TENANT /
        # AUTH_MICROSOFT_TENANT_ID) for single-tenant apps. Falls back to
        # 'common' for multi-tenant apps.
        tenant_id = (
            os.environ.get("MICROSOFT_TENANT_ID", "")
            or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_TENANT", "")
            or os.environ.get("AUTH_MICROSOFT_TENANT_ID", "")
            or "common"
        )
        if not client_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Microsoft OAuth is not configured. Go to Integrations → APIs → "
                    "'Microsoft OAuth' and enter your Azure App client ID "
                    "and secret. Instructions are provided there."
                ),
            )
        auth_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
            f"?client_id={client_id}"
            "&response_type=code"
            "&scope=offline_access+https://graph.microsoft.com/Mail.ReadWrite"
            "+https://graph.microsoft.com/Mail.Send"
            "+https://graph.microsoft.com/User.Read"
            # Required to create/manage Outlook master categories (coloured
            # labels). Without it /me/outlook/masterCategories 403s and a rule's
            # new label is only tagged on the message, never created as a real
            # category. (inbox-zero requests the same scope.)
            "+https://graph.microsoft.com/MailboxSettings.ReadWrite"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )
    else:  # pragma: no cover — refused above, before the state is signed
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}"
        )

    # ⚠️ No `response_mode=form_post`. The provider would then POST the code
    # cross-site, and the browser drops the Lax session cookie on that POST, so
    # the BFF callback could not see the member (EM-T1a item 3).
    return RedirectResponse(auth_url, status_code=302)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    user: UserContext = Depends(get_current_user),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
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
        # page, never a raw 422. EM-T3 owns the guided page for admin consent.
        return _bounce(_provider_error_reason(error) if error else "invalid_state")

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

    # Persist the OAuth *app* credentials (client_id/secret, tenant) alongside
    # the user's tokens.  Without these the provider cannot refresh the access
    # token once it expires (~1h) and all sync/folder/message calls start
    # failing with "authentication failed".
    token_data.update(_provider_oauth_app_creds(provider))

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
        )
    except Exception as exc:
        _log.error("email.oauth_account_save_failed", error=str(exc)[:200])
        return _bounce("account_save_failed")

    # Start (or restart) background sync for the account. This runs after the
    # tenant block, so the row is committed first. ⚠️ The scheduler binds no
    # tenant until EM-T1b, so this stays best effort.
    try:
        from email_ingestion.scheduler import refresh_account_sync
        await refresh_account_sync(account_id)
    except Exception:
        pass

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
) -> str:
    """Create or refresh the mailbox row inside one ``tenant_session(org)``.

    ``owner`` is the address of the session, verbatim. The reads of this
    package compare ``user_id = :uid`` with that same address. The lookup
    compares ``member`` in lower case, so a reconnect finds the row whatever
    its case. The seam commits on exit, so there is no ``commit()`` here.
    """
    async with _tenant_session(org) as db:
        # An account for this member and address already exists: this is a
        # *reconnect*. Refresh the stored credentials in place rather than
        # reject a duplicate. Resetting last_history_id forces a full re-sync
        # so messages persisted under an old code path get re-normalised.
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
                           last_history_id = NULL,
                           updated_at = now()
                       WHERE id = :id"""
                ),
                {"creds": encrypted_creds, "id": account_id},
            )
            return account_id
        # organization_id is set explicitly. The column default reads the same
        # GUC, but a write names its tenant (R5).
        result = await db.execute(
            text(
                """INSERT INTO email_accounts
                   (id, user_id, provider, email_address, label,
                    avatar_color, credentials_encrypted, is_default,
                    organization_id)
                   VALUES (:id, :user_id, :provider, :email, :label,
                            :color, :creds,
                            NOT EXISTS (SELECT 1 FROM email_accounts
                                        WHERE lower(user_id) = :member),
                            CAST(:org AS uuid))
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


def _provider_oauth_app_creds(provider: str) -> dict[str, str]:
    """Resolve the OAuth *app* credentials (client id/secret, tenant) for a provider.

    These must be stored alongside the user's tokens so the provider can refresh
    the access token later — Microsoft/Google access tokens expire in ~1 hour and
    a refresh requires the client_id/client_secret used at authorize time.
    """
    settings = get_settings()
    if provider == "gmail":
        return {
            "client_id": settings.gmail_oauth_client_id
            or os.environ.get("GMAIL_OAUTH_CLIENT_ID", ""),
            "client_secret": settings.gmail_oauth_client_secret
            or os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", ""),
        }
    if provider == "microsoft":
        return {
            "client_id": settings.msft_oauth_client_id
            or os.environ.get("MSFT_OAUTH_CLIENT_ID", "")
            or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_ID", ""),
            "client_secret": settings.msft_oauth_client_secret
            or os.environ.get("MSFT_OAUTH_CLIENT_SECRET", "")
            or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_SECRET", ""),
            "tenant_id": os.environ.get("MICROSOFT_TENANT_ID", "")
            or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_TENANT", "")
            or os.environ.get("AUTH_MICROSOFT_TENANT_ID", "")
            or "common",
        }
    return {}


async def _exchange_gmail_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange authorization code for Gmail OAuth tokens."""
    settings = get_settings()
    client_id = settings.gmail_oauth_client_id or os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
    client_secret = settings.gmail_oauth_client_secret or os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _exchange_msft_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange authorization code for Microsoft OAuth tokens."""
    settings = get_settings()
    # Prefer dedicated email OAuth creds; fall back to sign-in auth creds (shared app registration)
    client_id = (
        settings.msft_oauth_client_id
        or os.environ.get("MSFT_OAUTH_CLIENT_ID", "")
        or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_ID", "")
    )
    client_secret = (
        settings.msft_oauth_client_secret
        or os.environ.get("MSFT_OAUTH_CLIENT_SECRET", "")
        or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_SECRET", "")
    )
    # Tenant ID: use MICROSOFT_TENANT_ID (or AUTH_MICROSOFT_ENTRA_ID_TENANT /
    # AUTH_MICROSOFT_TENANT_ID) for single-tenant apps. Falls back to
    # 'common' for multi-tenant apps.
    tenant_id = (
        os.environ.get("MICROSOFT_TENANT_ID", "")
        or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_TENANT", "")
        or os.environ.get("AUTH_MICROSOFT_TENANT_ID", "")
        or "common"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
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
