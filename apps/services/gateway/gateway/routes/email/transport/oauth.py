"""Transport · OAuth — Gmail/Microsoft connect flow: authorize, callback, token
exchange, and provider identity lookup."""

from __future__ import annotations

import json
import os
import secrets
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from gateway.routes.email.core import _default_label, _get_db, _log, router
from pydantic import BaseModel
from sqlalchemy import text


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str


#: In-flight authorize→callback state, keyed by the CSRF nonce.
#:
#: ⚠️ KNOWN DEFECT (recorded, not fixed here — email_app_master_plan.md §7 Tier 1
#: item 1): this is a module-level, in-process dict. Every deploy restarts the
#: gateway, so any OAuth flow in flight when a deploy lands loses its state and
#: the callback fails `state not in _oauth_states` → the user is bounced to
#: /email/oauth/callback?error=invalid_state with no explanation. It is also not
#: shared across workers (a second worker answering the callback has never seen
#: the state) and entries are never expired, so an abandoned flow leaks forever.
#: Fix is Redis + TTL, alongside signing the state.
_oauth_states: dict[str, dict[str, str]] = {}


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    user: UserContext = Depends(get_current_user),
    redirect_after: str = Query(default=""),
    user_email: str = Query(default=""),
):
    """Start OAuth flow for an email provider.

    **This route is gated and stays gated.** It is reached through the workbench
    BFF (``/api/email/oauth/{provider}/authorize``), which holds the session,
    attaches the internal bearer plus ``X-User-Email``, and re-issues the 302 it
    gets back so the browser lands on the provider's consent screen. Adding it to
    ``main.PUBLIC_ROUTES`` would be the dangerous repair: the state below binds
    the mailbox to ``user_id``, so an anonymous authorize leg would let anyone
    attach a mailbox to somebody else's account.

    ``user_email`` is a **fallback only**, retained for the legacy
    direct-navigation shape. The authenticated identity always wins — a query
    parameter must never be able to name whose account the mailbox lands on.
    """
    state = secrets.token_urlsafe(32)
    redirect_uri = _build_redirect_uri(provider)

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
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}"
        )

    # The AUTHENTICATED identity wins. This used to read
    # ``user_email or user.email``, i.e. a query parameter outranked the person
    # the request was actually authenticated as — anyone who could reach the
    # route could bind a mailbox to a colleague's account. `user_email` survives
    # only as the fallback for a caller that has no header identity at all.
    _oauth_states[state] = {
        "provider": provider,
        "user_id": user.email or user_email or "anonymous",
        "redirect_after": redirect_after,
    }

    return RedirectResponse(auth_url, status_code=302)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
):
    """Handle OAuth callback — exchange code for tokens and redirect to workbench."""
    gateway_public = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost:8000")
    workbench_url = os.environ.get("WORKBENCH_PUBLIC_URL")
    if not workbench_url:
        # Auto-derive workbench URL from gateway. Since D40 the workbench is a
        # SIBLING subdomain (api.<apex> → app.<apex>), no longer the bare apex
        # — stripping "api." would aim the OAuth return at the marketing root.
        if gateway_public == "http://localhost:8000":
            workbench_url = "http://localhost:3001"
        elif "://api." in gateway_public:
            workbench_url = gateway_public.replace("://api.", "://app.", 1)
        else:
            workbench_url = gateway_public

    # Build the workbench callback page URL
    callback_page = f"{workbench_url}/email/oauth/callback"

    # Validate state
    if state not in _oauth_states:
        return RedirectResponse(
            f"{callback_page}?{urlencode({'error': 'invalid_state'})}",
            status_code=302,
        )

    oauth_data = _oauth_states.pop(state)
    redirect_after = oauth_data.get("redirect_after", "")
    redirect_uri = _build_redirect_uri(provider)

    # Exchange code for tokens
    try:
        if provider == "gmail":
            token_data = await _exchange_gmail_token(code, redirect_uri)
        elif provider == "microsoft":
            token_data = await _exchange_msft_token(code, redirect_uri)
        else:
            return RedirectResponse(
                f"{callback_page}?{urlencode({'error': f'unknown_provider_{provider}'})}",
                status_code=302,
            )
    except Exception as exc:
        _log.error("Token exchange failed: %s", exc)
        return RedirectResponse(
            f"{callback_page}?{urlencode({'error': 'token_exchange_failed'})}",
            status_code=302,
        )

    # Get user email from provider
    try:
        user_email = await _get_provider_email(provider, token_data["access_token"])
    except Exception as exc:
        _log.error("Failed to get provider email: %s", exc)
        return RedirectResponse(
            f"{callback_page}?{urlencode({'error': 'email_fetch_failed'})}",
            status_code=302,
        )

    # Persist the OAuth *app* credentials (client_id/secret, tenant) alongside
    # the user's tokens.  Without these the provider cannot refresh the access
    # token once it expires (~1h) and all sync/folder/message calls start
    # failing with "authentication failed".
    token_data.update(_provider_oauth_app_creds(provider))

    # Store in encrypted DB
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    creds_json = json.dumps(token_data)
    encrypted_creds = store.encrypt(creds_json)

    # H4/H6: service-identity route — the OAuth callback is a provider
    # browser redirect with no member session (trust = HMAC-signed state),
    # so no ambient tenant is bound; needs an explicit tenant derived from
    # the app_user row of the user encoded in the signed state before
    # conversion.
    db = await _get_db()
    try:
        # Check if an account already exists for this user+email.  If so, this
        # is a *reconnect*: refresh the stored credentials in place rather than
        # rejecting as a duplicate (the old behaviour left users with no way to
        # repair an account whose refresh token had gone stale).  Resetting
        # last_history_id forces a full re-sync so messages persisted under the
        # old code path (e.g. raw provider folder IDs) get re-normalised.
        existing = await db.execute(
            text(
                """SELECT id FROM email_accounts
                   WHERE user_id = :user_id
                     AND provider = :provider
                     AND email_address = :email"""
            ),
            {
                "user_id": oauth_data["user_id"],
                "provider": provider,
                "email": user_email,
            },
        )
        existing_row = existing.fetchone()
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
            await db.commit()
        else:
            # Create account
            result = await db.execute(
                text(
                    """INSERT INTO email_accounts
                       (id, user_id, provider, email_address, label,
                        avatar_color, credentials_encrypted, is_default)
                       VALUES (:id, :user_id, :provider, :email, :label,
                                :color, :creds,
                                NOT EXISTS (SELECT 1 FROM email_accounts
                                            WHERE user_id = :user_id))
                       RETURNING id"""
                ),
                {
                    "id": str(uuid4()),
                    "user_id": oauth_data["user_id"],
                    "provider": provider,
                    "email": user_email,
                    "label": _default_label(provider),
                    "color": "#6366f1",
                    "creds": encrypted_creds,
                },
            )
            await db.commit()
            account_id = str(result.fetchone()[0])

        # Start (or restart) background sync for the account
        try:
            from email_ingestion.scheduler import refresh_account_sync
            await refresh_account_sync(account_id)
        except Exception:
            pass

        # Success redirect
        params = {
            "account_id": account_id,
            "email": user_email,
            "provider": provider,
        }
        if redirect_after:
            params["redirect_after"] = redirect_after

        return RedirectResponse(
            f"{callback_page}?{urlencode(params)}",
            status_code=302,
        )
    finally:
        await db.close()


def _build_redirect_uri(provider: str) -> str:
    """Build the OAuth redirect URI."""
    base = os.environ.get(
        "GATEWAY_PUBLIC_URL",
        "http://localhost:8000",
    )
    return f"{base}/email/oauth/{provider}/callback"


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
