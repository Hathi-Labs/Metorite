"""OAuth 2.0 authorization-code flow for integrations (M2.6 / L1-14).

Generic, provider-driven token exchange used by the Control Plane Integration
page. Each provider is configured once in ``_PROVIDERS``; the same authorize →
callback → refresh code path serves all of them.

Flow
----
1. ``GET /integrations/oauth/{service}/authorize`` → returns the provider's
   consent URL (with a signed ``state`` to prevent CSRF).
2. The operator approves; the provider redirects to
   ``GET /integrations/oauth/callback/{service}?code=...&state=...``.
3. The callback exchanges the code for access (+ refresh) tokens and persists
   them to ``.env`` via the Integration Registry writer.
4. ``refresh_access_token(service)`` renews an access token before expiry using
   the stored refresh token (called by the agent run path).

Security
--------
- ``state`` is an HMAC-signed ``service:nonce:ts`` token (gateway secret).
- Client secrets never leave the server; tokens are written to ``.env`` only.
- The authorize/callback endpoints are admin-gated in the router include.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger, get_settings
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from gateway.routes.integrations import _find_env_file, _upsert_env_var

_log = get_logger("gateway.oauth")

router = APIRouter(prefix="/integrations/oauth", tags=["integrations", "oauth"])

_STATE_TTL_SECONDS = 600  # authorize → callback must complete within 10 minutes


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

def _zoho_accounts(settings: Any) -> str:
    return getattr(settings, "zoho_accounts_url", "https://accounts.zoho.com")


# Each provider declares how to build its consent URL, exchange the code, and
# where to persist the resulting tokens. ``client_id``/``client_secret`` and
# the persisted env keys are resolved from Settings at call time.
_PROVIDERS: dict[str, dict[str, Any]] = {
    "zoho-crm": {
        "authorize_url": lambda s: f"{_zoho_accounts(s)}/oauth/v2/auth",
        "token_url": lambda s: f"{_zoho_accounts(s)}/oauth/v2/token",
        "scopes": "ZohoCRM.modules.ALL,ZohoCRM.settings.ALL",
        "client_id_attr": "zoho_client_id",
        "client_secret_attr": "zoho_client_secret",
        "access_env": "ZOHO_ACCESS_TOKEN",
        "refresh_env": "ZOHO_REFRESH_TOKEN",
        "expiry_env": "ZOHO_TOKEN_EXPIRY",
        "extra_authorize": {"access_type": "offline", "prompt": "consent"},
    },
    "google": {
        "authorize_url": lambda s: "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": lambda s: "https://oauth2.googleapis.com/token",
        "scopes": "https://www.googleapis.com/auth/gmail.readonly",
        "client_id_attr": "google_client_id",
        "client_secret_attr": "google_client_secret",
        "access_env": "GOOGLE_ACCESS_TOKEN",
        "refresh_env": "GOOGLE_REFRESH_TOKEN",
        "expiry_env": "GOOGLE_TOKEN_EXPIRY",
        "extra_authorize": {"access_type": "offline", "prompt": "consent"},
    },
}


def _redirect_uri(settings: Any, service: str) -> str:
    base = getattr(settings, "oauth_redirect_base", "http://localhost:8000").rstrip("/")
    return f"{base}/integrations/oauth/callback/{service}"


# ---------------------------------------------------------------------------
# Signed state (CSRF protection)
# ---------------------------------------------------------------------------

def _sign_state(service: str, settings: Any) -> str:
    secret = getattr(settings, "gateway_session_secret", "change-me").encode()
    nonce = secrets.token_urlsafe(8)
    ts = str(int(time.time()))
    msg = f"{service}:{nonce}:{ts}"
    sig = hmac.new(secret, msg.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{msg}:{sig_b64}"


def _verify_state(state: str, service: str, settings: Any) -> bool:
    try:
        svc, nonce, ts, sig_b64 = state.split(":")
    except ValueError:
        return False
    if svc != service:
        return False
    if int(time.time()) - int(ts) > _STATE_TTL_SECONDS:
        return False
    secret = getattr(settings, "gateway_session_secret", "change-me").encode()
    msg = f"{svc}:{nonce}:{ts}"
    expected = hmac.new(secret, msg.encode(), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    return hmac.compare_digest(sig_b64, expected_b64)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{service}/authorize", dependencies=[require_permission("feature:integrations")])
async def oauth_authorize(
    service: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the provider consent URL for the named service."""
    provider = _PROVIDERS.get(service)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Unknown OAuth service: {service}")

    settings = get_settings()
    client_id = getattr(settings, provider["client_id_attr"], "")
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=f"{service}: {provider['client_id_attr']} is not configured.",
        )

    state = _sign_state(service, settings)
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(settings, service),
        "response_type": "code",
        "state": state,
    }
    if provider["scopes"]:
        params["scope"] = provider["scopes"]
    params.update(provider["extra_authorize"])

    authorize_url = f"{provider['authorize_url'](settings)}?{urlencode(params)}"
    _log.info("oauth.authorize", service=service, actor=user.email)
    return {"service": service, "authorize_url": authorize_url, "state": state}


@router.get("/callback/{service}", response_class=HTMLResponse)
async def oauth_callback(
    service: str,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> HTMLResponse:
    """Exchange the authorization code for tokens and persist them."""
    provider = _PROVIDERS.get(service)
    if not provider:
        return _html_result(service, ok=False, detail="Unknown OAuth service.")
    if error:
        return _html_result(service, ok=False, detail=f"Provider returned error: {error}")
    if not code:
        return _html_result(service, ok=False, detail="No authorization code returned.")

    settings = get_settings()
    if not _verify_state(state, service, settings):
        return _html_result(service, ok=False, detail="Invalid or expired state (CSRF check failed).")

    client_id = getattr(settings, provider["client_id_attr"], "")
    client_secret = getattr(settings, provider["client_secret_attr"], "")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _redirect_uri(settings, service),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(provider["token_url"](settings), data=data)
    except Exception as exc:
        return _html_result(service, ok=False, detail=f"Token request failed: {exc}")

    if resp.status_code != 200:
        return _html_result(service, ok=False, detail=f"Token exchange failed: {resp.text[:200]}")

    tokens = resp.json()
    access = tokens.get("access_token")
    if not access:
        return _html_result(service, ok=False, detail=f"No access_token in response: {resp.text[:200]}")

    _persist_tokens(provider, tokens)
    _log.info("oauth.callback_success", service=service)
    return _html_result(service, ok=True, detail="Connected successfully. You can close this tab.")


@router.post("/{service}/refresh", dependencies=[require_permission("feature:integrations")])
async def oauth_refresh(
    service: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Force-refresh the access token for a service (admin / scheduled use)."""
    if service not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown OAuth service: {service}")
    token = await refresh_access_token(service)
    return {"service": service, "refreshed": bool(token)}


# ---------------------------------------------------------------------------
# Token persistence + refresh
# ---------------------------------------------------------------------------

def _persist_tokens(provider: dict[str, Any], tokens: dict[str, Any]) -> None:
    """Write access/refresh/expiry tokens to .env and hot-reload Settings."""
    env_path = _find_env_file()
    access = tokens.get("access_token", "")
    _upsert_env_var(env_path, provider["access_env"], access)

    if provider["refresh_env"] and tokens.get("refresh_token"):
        _upsert_env_var(env_path, provider["refresh_env"], tokens["refresh_token"])

    if provider["expiry_env"] and tokens.get("expires_in"):
        try:
            expiry = datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))
            _upsert_env_var(env_path, provider["expiry_env"], expiry.isoformat())
        except (ValueError, TypeError):
            pass

    try:
        get_settings.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        pass


async def refresh_access_token(service: str) -> str | None:
    """Refresh and persist the access token for a service if it is near expiry.

    Returns the (possibly refreshed) access token, or ``None`` if the service
    has no refresh token / is not configured. Called by the agent run path
    before injecting credentials so agents always get a live token.
    """
    provider = _PROVIDERS.get(service)
    if not provider or not provider["refresh_env"]:
        return None

    settings = get_settings()
    refresh_attr = provider["refresh_env"].lower()
    refresh_token = getattr(settings, refresh_attr, "")
    if not refresh_token:
        return None

    # Skip refresh if the current token is still valid for > 5 minutes.
    expiry_attr = (provider["expiry_env"] or "").lower()
    expiry_str = getattr(settings, expiry_attr, "") if expiry_attr else ""
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if expiry - datetime.now(UTC) > timedelta(minutes=5):
                return getattr(settings, provider["access_env"].lower(), "") or None
        except ValueError:
            pass

    client_id = getattr(settings, provider["client_id_attr"], "")
    client_secret = getattr(settings, provider["client_secret_attr"], "")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(provider["token_url"](settings), data=data)
        if resp.status_code == 200:
            tokens = resp.json()
            # Zoho refresh responses omit refresh_token — keep the existing one.
            tokens.setdefault("refresh_token", refresh_token)
            _persist_tokens(provider, tokens)
            _log.info("oauth.refreshed", service=service)
            return tokens.get("access_token")
        _log.warning("oauth.refresh_failed", service=service, status=resp.status_code)
    except Exception as exc:
        _log.warning("oauth.refresh_error", service=service, error=str(exc))
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html_result(service: str, *, ok: bool, detail: str) -> HTMLResponse:
    colour = "#16a34a" if ok else "#dc2626"
    title = "Connected" if ok else "Connection failed"
    body = (
        f"<html><body style='font-family:system-ui;background:#0a0a0a;color:#e5e5e5;"
        f"display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
        f"<div style='text-align:center;max-width:480px;padding:2rem'>"
        f"<div style='font-size:1.4rem;font-weight:600;color:{colour}'>{title}</div>"
        f"<div style='margin-top:0.5rem;color:#a3a3a3'>{service}</div>"
        f"<div style='margin-top:1rem'>{detail}</div>"
        f"</div></body></html>"
    )
    return HTMLResponse(content=body, status_code=200 if ok else 400)
