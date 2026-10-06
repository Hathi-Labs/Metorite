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
from typing import Any, Literal

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


#: The fields of a WABA's number list that the connect reads.
_PHONE_NUMBER_FIELDS = (
    "id,display_phone_number,verified_name,quality_rating,platform_type")


async def list_waba_phone_numbers(
    waba_id: str, token: str, graph_version: str,
) -> list[dict[str, Any]]:
    """The phone numbers of a WABA, from ``GET /<waba_id>/phone_numbers``.

    A coexistence connect gets no ``phone_number_id`` from Meta's popup, only
    the ``waba_id`` (WS-20 WA-C2 P1). So the backend reads the number here.
    The caller must check that ``waba_id`` is digits first, because it goes
    into the URL path. Raises on a Meta error or on a body with no list.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{graph_version}/{waba_id}/phone_numbers",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": _PHONE_NUMBER_FIELDS},
        )
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Meta returned no phone number list for this WABA.")
    return [r for r in rows if isinstance(r, dict)]


def select_phone_number_id(
    numbers: list[dict[str, Any]], requested: str | None,
) -> str:
    """Pick the number to connect from the WABA's list (WS-20 WA-C2 P1).

    The browser's ``requested`` id is a claim, and the list is the fact. So a
    requested id must be in the list. With no requested id, the WABA must
    hold exactly one number. Every other case answers 400 and names why.
    """
    ids = [str(n["id"]) for n in numbers if isinstance(n.get("id"), str | int)]
    if not ids:
        raise HTTPException(
            status_code=400,
            detail="This WhatsApp Business account has no phone number.")
    if requested:
        if requested in ids:
            return requested
        raise HTTPException(
            status_code=400,
            detail="The selected number is not in this WhatsApp Business account.")
    if len(ids) > 1:
        raise HTTPException(
            status_code=400,
            detail="This WhatsApp Business account has more than one number, "
                   "and Meta did not say which one you selected. Connect again "
                   "and select one number.")
    return ids[0]


class EmbeddedSignupRequest(BaseModel):
    """What the browser sends after the Embedded Signup popup closes.

    ``waba_id`` is required. A plain FINISH also sends ``phone_number_id``.
    A FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING (coexistence) sends no number,
    so the backend reads it from the WABA (WS-20 WA-C2 P1, P2).
    """
    code: str                       # authorization code from FB.login
    waba_id: str                    # from the WA_EMBEDDED_SIGNUP message event
    phone_number_id: str | None = None
    onboarding: Literal["cloud", "coexistence"] = "cloud"
    display_name: str = ""


class EmbeddedSignupResponse(BaseModel):
    account_id: str
    display_name: str
    phone_number: str
    # Always true since WA-C2: a failed subscribe answers 400 and saves no
    # row. Kept on the wire, because the frontend type reads it.
    subscribed: bool


@router.post("/connect/embedded", response_model=EmbeddedSignupResponse)
async def embedded_signup(
    req: EmbeddedSignupRequest, user: UserContext = Depends(get_current_user),
):
    """Complete Meta Embedded Signup. Requires WHATSAPP_APP_ID and
    WHATSAPP_APP_SECRET on the server.

    The order is the contract (WS-20 WA-C2 P3). Exchange the code, find the
    number in the WABA, have Meta confirm it, subscribe the app as a hard
    step, and then save the row as ``live``. A failure at any step answers 400
    and saves no row, so a retry does not meet a 409.

    No step calls ``/register``. A coexistence number is on the phone app, so
    it is registered already (P4).
    """
    from gateway.routes.whatsapp.transport.accounts import (
        _account_model,
        persist_account,
        verify_cloud_number,
    )
    from whatsapp_ingestion.providers.factory import (
        is_phone_number_id,
        safe_graph_version,
    )

    app_id = os.environ.get("WHATSAPP_APP_ID", "").strip()
    app_secret = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise HTTPException(
            status_code=400,
            detail="Embedded Signup isn't configured on this server "
                   "(set WHATSAPP_APP_ID + WHATSAPP_APP_SECRET).")
    code = req.code.strip()
    if not code:
        raise HTTPException(status_code=422, detail="code is required")
    # The WABA id goes into a Graph URL path, so it must be ASCII digits. This
    # check runs before any call to Meta.
    waba_id = req.waba_id
    if not is_phone_number_id(waba_id):
        raise HTTPException(
            status_code=400,
            detail="waba_id must be the numeric id that Meta returns.")
    requested = (req.phone_number_id or "").strip() or None
    # The server's version, for every Graph call on this path.
    gv = safe_graph_version(
        os.environ.get("WHATSAPP_GRAPH_VERSION", "").strip() or None)

    # 1. code → token (server-side).
    try:
        token = await exchange_code_for_token(code, app_id, app_secret, gv)
    except Exception as exc:
        _log.info("whatsapp.embedded.exchange_failed", error=str(exc)[:200])
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc

    # 2. find the number in the WABA. A coexistence connect sends none.
    try:
        numbers = await list_waba_phone_numbers(waba_id, token, gv)
    except Exception as exc:
        _log.info("whatsapp.embedded.list_failed", error=str(exc)[:200])
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc
    phone_number_id = select_phone_number_id(numbers, requested)

    # 3. Meta confirms the token for this number: the digit check, the
    # server's version and the profile `id` check of the manual path.
    profile = await verify_cloud_number(phone_number_id, {"access_token": token})

    # 4. subscribe our app to the WABA. A hard step: without it no message
    # arrives, so the connect fails and saves nothing.
    try:
        await subscribe_app_to_waba(waba_id, token, gv)
    except Exception as exc:
        _log.warning("whatsapp.embedded.subscribe_failed",
                     waba_id=waba_id, error=str(exc)[:200])
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc

    # 5. store the account (shared with the manual path). `provider` stays
    # 'cloud_api', because the migration-230 index and the webhook lookup
    # key on it. The onboarding type lives in the encrypted blob (P2).
    creds: dict[str, Any] = {
        "access_token": token, "waba_id": waba_id, "onboarding": req.onboarding,
    }
    display = (
        req.display_name.strip() or profile.get("verified_name") or "WhatsApp")
    phone = profile.get("display_phone_number") or ""
    async with _tenant_session() as db:
        row = await persist_account(
            db, user_id=user.email or "anonymous", phone_number=phone,
            phone_number_id=phone_number_id, waba_id=waba_id,
            display_name=display, credentials=creds,
            webhook_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN") or None,
            verified_profile=profile,
            sync_status="live",
        )
        acct = _account_model(row)
    return EmbeddedSignupResponse(
        account_id=acct.id, display_name=acct.display_name,
        phone_number=acct.phone_number, subscribed=True)
