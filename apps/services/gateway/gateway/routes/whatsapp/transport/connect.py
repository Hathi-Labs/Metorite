"""Transport · connect — the onboarding helpers behind the Connect wizard (W11).

Two seams that turn "paste a curl command" into a guided, verifiable UI:

* ``POST /whatsapp/accounts/verify`` — TEST credentials against Meta's Graph API
  before saving. A 200 from the phone-number profile proves the token can act for
  this number and returns its display name / number / quality rating; a failure
  returns Meta's own message, cleaned up. Never writes anything.
* ``GET  /whatsapp/connection/info`` — the webhook Callback URL + a Verify Token
  the founder pastes into Meta → WhatsApp → Configuration. The URL comes from
  ``WHATSAPP_PUBLIC_URL`` when set (else the UI asks for the domain); the token
  defaults to ``WHATSAPP_VERIFY_TOKEN`` or a fresh suggestion the wizard also
  saves onto the account so the webhook handshake matches.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.routes.whatsapp.core import _instantiate_provider, _tenant_session, router
from pydantic import BaseModel

_log = get_logger("gateway.whatsapp.connect")

_GRAPH_BASE = "https://graph.facebook.com"
_DEFAULT_GRAPH_VERSION = "v21.0"
_TIMEOUT = httpx.Timeout(30.0)


def friendly_meta_error(exc: Exception) -> str:
    """Extract Meta's Graph error message from an httpx failure, or a short
    fallback. Pure/testable — the wizard shows this verbatim."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict) and err.get("message"):
                code = err.get("code")
                msg = str(err["message"])
                return f"{msg} (Meta code {code})" if code else msg
        except Exception:  # non-JSON error body
            pass
        status = getattr(resp, "status_code", None)
        if status:
            return f"Meta returned HTTP {status}."
    return str(exc)[:200] or "Could not reach Meta."


class VerifyRequest(BaseModel):
    phone_number_id: str
    access_token: str
    graph_version: str | None = None


class VerifyResponse(BaseModel):
    ok: bool
    display_phone_number: str | None = None
    verified_name: str | None = None
    quality_rating: str | None = None
    error: str | None = None


@router.post("/accounts/verify", response_model=VerifyResponse)
async def verify_account(
    req: VerifyRequest, user: UserContext = Depends(get_current_user),
):
    """Live-test WhatsApp credentials against Meta before the founder saves them.
    Returns the number's public profile on success, or a clear error."""
    if not req.phone_number_id.strip() or not req.access_token.strip():
        return VerifyResponse(
            ok=False, error="Phone number ID and access token are required.")
    creds: dict[str, str] = {
        "phone_number_id": req.phone_number_id.strip(),
        "access_token": req.access_token.strip(),
    }
    if req.graph_version:
        creds["graph_version"] = req.graph_version.strip()
    try:
        provider = _instantiate_provider("cloud_api", creds)
        profile = await provider.get_phone_number_profile()
    except Exception as exc:
        _log.info("whatsapp.verify.failed", error=str(exc)[:200])
        return VerifyResponse(ok=False, error=friendly_meta_error(exc))
    return VerifyResponse(
        ok=True,
        display_phone_number=profile.get("display_phone_number"),
        verified_name=profile.get("verified_name"),
        quality_rating=profile.get("quality_rating"),
    )


class ConnectionInfo(BaseModel):
    webhook_url: str            # full Callback URL, or "" when the base is unknown
    webhook_path: str = "/whatsapp/webhook"
    verify_token: str
    base_configured: bool       # False → the UI asks for the public domain
    # Embedded Signup (W12): the one-click "Continue with Facebook" path is
    # offered only when the Meta app is configured for it (App ID + an Embedded
    # Signup configuration id). Both are public (they ship to the browser); the
    # App Secret stays server-side for the code exchange.
    embedded_signup: bool = False
    fb_app_id: str = ""
    es_config_id: str = ""
    graph_version: str = _DEFAULT_GRAPH_VERSION


@router.get("/connection/info", response_model=ConnectionInfo)
async def connection_info(user: UserContext = Depends(get_current_user)):
    """The webhook Callback URL + a Verify Token for the Meta → Configuration
    step, plus whether one-click Embedded Signup is available. The wizard also
    submits the verify token as the account's webhook_verify_token so the
    ``GET /whatsapp/webhook`` handshake matches."""
    base = os.environ.get("WHATSAPP_PUBLIC_URL", "").strip().rstrip("/")
    verify = (
        os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()
        or f"cc-{secrets.token_urlsafe(18)}"
    )
    app_id = os.environ.get("WHATSAPP_APP_ID", "").strip()
    es_config = os.environ.get("WHATSAPP_ES_CONFIG_ID", "").strip()
    return ConnectionInfo(
        webhook_url=(f"{base}/whatsapp/webhook" if base else ""),
        verify_token=verify,
        base_configured=bool(base),
        embedded_signup=bool(app_id and es_config),
        fb_app_id=app_id,
        es_config_id=es_config,
        graph_version=os.environ.get(
            "WHATSAPP_GRAPH_VERSION", _DEFAULT_GRAPH_VERSION).strip(),
    )


# ── Embedded Signup one-click (W12) ───────────────────────────────────────────

async def exchange_code_for_token(
    code: str, app_id: str, app_secret: str, graph_version: str,
) -> str:
    """Exchange the Embedded Signup authorization code for a business access
    token (server-side, so the App Secret never touches the browser). Raises on a
    Meta error or a missing token."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{graph_version}/oauth/access_token",
            params={"client_id": app_id, "client_secret": app_secret,
                    "code": code},
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise RuntimeError(f"no access_token in exchange response: {data!r}")
    return str(token)


async def subscribe_app_to_waba(
    waba_id: str, token: str, graph_version: str,
) -> None:
    """Subscribe our app to the WABA so Meta pushes its message webhooks to us —
    without this the number connects but no messages arrive. Raises on error."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{graph_version}/{waba_id}/subscribed_apps",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()


class EmbeddedSignupRequest(BaseModel):
    code: str                       # authorization code from FB.login
    phone_number_id: str            # selected in the ES popup (message event)
    waba_id: str | None = None
    display_name: str = ""


class EmbeddedSignupResponse(BaseModel):
    account_id: str
    display_name: str
    phone_number: str
    subscribed: bool                # did the app subscribe to the WABA's webhooks


@router.post("/connect/embedded", response_model=EmbeddedSignupResponse)
async def embedded_signup(
    req: EmbeddedSignupRequest, user: UserContext = Depends(get_current_user),
):
    """Complete Meta Embedded Signup: exchange the code for a token, verify the
    number, subscribe the app to its WABA, and store the account — the one-click
    tail. Requires WHATSAPP_APP_ID + WHATSAPP_APP_SECRET on the server."""
    app_id = os.environ.get("WHATSAPP_APP_ID", "").strip()
    app_secret = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise HTTPException(
            status_code=400,
            detail="Embedded Signup isn't configured on this server "
                   "(set WHATSAPP_APP_ID + WHATSAPP_APP_SECRET).")
    if not req.code.strip() or not req.phone_number_id.strip():
        raise HTTPException(status_code=422,
                            detail="code and phone_number_id are required")
    gv = os.environ.get("WHATSAPP_GRAPH_VERSION", _DEFAULT_GRAPH_VERSION).strip()

    # 1. code → token (server-side).
    try:
        token = await exchange_code_for_token(
            req.code.strip(), app_id, app_secret, gv)
    except Exception as exc:
        _log.info("whatsapp.embedded.exchange_failed", error=str(exc)[:200])
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc

    # 2. verify the number + pull its profile (name / number for display).
    creds: dict[str, Any] = {
        "phone_number_id": req.phone_number_id.strip(), "access_token": token,
    }
    if req.waba_id:
        creds["waba_id"] = req.waba_id.strip()
    try:
        provider = _instantiate_provider("cloud_api", creds)
        profile = await provider.get_phone_number_profile()
    except Exception as exc:
        _log.info("whatsapp.embedded.verify_failed", error=str(exc)[:200])
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc

    # 3. subscribe our app to the WABA so webhooks flow (best-effort — a number
    # can already be subscribed; don't fail the whole connect on it).
    subscribed = False
    if req.waba_id:
        try:
            await subscribe_app_to_waba(req.waba_id.strip(), token, gv)
            subscribed = True
        except Exception as exc:
            _log.warning("whatsapp.embedded.subscribe_failed",
                         waba_id=req.waba_id, error=str(exc)[:200])

    # 4. store the account (shared with the manual path).
    from gateway.routes.whatsapp.transport.accounts import (
        _account_model,
        persist_account,
    )
    display = (
        req.display_name.strip() or profile.get("verified_name") or "WhatsApp")
    phone = profile.get("display_phone_number") or ""
    async with _tenant_session() as db:
        row = await persist_account(
            db, user_id=user.email or "anonymous", phone_number=phone,
            phone_number_id=req.phone_number_id.strip(),
            waba_id=req.waba_id.strip() if req.waba_id else None,
            display_name=display, credentials=creds,
            webhook_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN") or None,
            # Step 2 verified this token for this number, so Meta is not
            # called twice (WA-C1 review P1).
            verified_profile=profile,
        )
        acct = _account_model(row)
    return EmbeddedSignupResponse(
        account_id=acct.id, display_name=acct.display_name,
        phone_number=acct.phone_number, subscribed=subscribed)
