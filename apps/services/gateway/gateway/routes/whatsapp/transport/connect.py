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

import json
import os
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.routes.whatsapp.core import (
    _instantiate_provider,
    _tenant_session,
    assert_account_owned,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.whatsapp.connect")

_GRAPH_BASE = "https://graph.facebook.com"
_DEFAULT_GRAPH_VERSION = "v21.0"
_TIMEOUT = httpx.Timeout(30.0)


class MetaAnswerError(RuntimeError):
    """A Meta answer that this module could not use. Its text is a fixed
    sentence written here, so it is safe to show and to log."""


def _meta_error(exc: Exception) -> dict[str, Any]:
    """Meta's ``error`` object of a failed Graph call, or an empty dict."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return {}
    try:
        body = resp.json()
    except Exception:  # non-JSON error body
        return {}
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, dict) else {}


def friendly_meta_error(exc: Exception) -> str:
    """Extract Meta's Graph error message from an httpx failure, or a short
    fallback. Pure/testable — the wizard shows this verbatim.

    Never ``str(exc)`` (WS-20 WA-C2 review P1). The text of an httpx error
    holds the request URL, and a URL can hold a secret. So an error with no
    response gets a fixed sentence."""
    err = _meta_error(exc)
    if err.get("message"):
        code = err.get("code")
        msg = str(err["message"])
        return f"{msg} (Meta code {code})" if code else msg
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None) if resp is not None else None
    if status:
        return f"Meta returned HTTP {status}."
    if isinstance(exc, MetaAnswerError):
        return str(exc)
    return "Could not reach Meta."


def meta_error_fields(exc: Exception) -> dict[str, Any]:
    """The fields that a log line may carry for a failed Graph call: the
    error class, the HTTP status and Meta's ``code`` and ``type``. Never the
    exception text, which can hold a URL with a secret in it (WA-C2 P1)."""
    resp = getattr(exc, "response", None)
    err = _meta_error(exc)
    return {
        "error_class": type(exc).__name__,
        "status": getattr(resp, "status_code", None) if resp is not None else None,
        "meta_code": err.get("code"),
        "meta_type": err.get("type"),
    }


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
    except ValueError:
        return VerifyResponse(
            ok=False,
            error="Phone number ID must be the numeric id that Meta shows.")
    try:
        profile = await provider.get_phone_number_profile()
    except Exception as exc:
        _log.info("whatsapp.verify.failed", **meta_error_fields(exc))
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
    Meta error or a missing token.

    A POST with a form body, never query parameters (WS-20 WA-C2 review P1).
    A URL goes into the httpx request log and into the text of an httpx
    error, so the App Secret and the code must never be in one."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{graph_version}/oauth/access_token",
            data={"client_id": app_id, "client_secret": app_secret,
                  "code": code},
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise MetaAnswerError("Meta returned no access token for this code.")
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


#: The fields of a WABA's number list that the connect always reads.
_BASE_PHONE_NUMBER_FIELDS = (
    "id,display_phone_number,verified_name,quality_rating,platform_type")
#: Plus ``is_on_biz_app``: true for a number that is also active on the
#: WhatsApp Business app, which is coexistence (Meta, "Onboard WhatsApp
#: Business app users"). It picks the number of a coexistence connect.
_PHONE_NUMBER_FIELDS = f"{_BASE_PHONE_NUMBER_FIELDS},is_on_biz_app"
#: Graph's code for an invalid parameter, such as a field the node lacks.
_GRAPH_INVALID_PARAMETER = 100


async def _read_phone_numbers(
    waba_id: str, token: str, graph_version: str, fields: str,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{graph_version}/{waba_id}/phone_numbers",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": fields},
        )
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise MetaAnswerError(
            "Meta returned no phone number list for this WABA.")
    return [r for r in rows if isinstance(r, dict)]


async def list_waba_phone_numbers(
    waba_id: str, token: str, graph_version: str,
) -> list[dict[str, Any]]:
    """The phone numbers of a WABA, from ``GET /<waba_id>/phone_numbers``.

    A coexistence connect gets no ``phone_number_id`` from Meta's popup, only
    the ``waba_id`` (WS-20 WA-C2 P1). So the backend reads the number here.
    The caller must check that ``waba_id`` is digits first, because it goes
    into the URL path. Raises on a Meta error or on a body with no list.

    If Meta refuses the read with code 100, the cause can be
    ``is_on_biz_app`` on a Graph version without it. So the read runs once
    more without that field (WA-C2 review P2). The rows then carry no flag.
    """
    try:
        return await _read_phone_numbers(
            waba_id, token, graph_version, _PHONE_NUMBER_FIELDS)
    except Exception as exc:
        if _meta_error(exc).get("code") != _GRAPH_INVALID_PARAMETER:
            raise
        _log.info("whatsapp.embedded.list_field_refused", **meta_error_fields(exc))
    return await _read_phone_numbers(
        waba_id, token, graph_version, _BASE_PHONE_NUMBER_FIELDS)


#: The 400 of a coexistence connect whose WABA has several numbers, when Meta
#: flags none or more than one of them as on the app. A retry reads the same
#: list, so the text names the cause and does not ask for a new selection.
COEXISTENCE_CANNOT_TELL = (
    "This WhatsApp Business account has several numbers, and Metorite cannot "
    "tell which one you linked from the app. Contact support.")


def select_phone_number_id(
    numbers: list[dict[str, Any]], requested: str | None,
    onboarding: str = "cloud",
) -> str:
    """Pick the number to connect from the WABA's list (WS-20 WA-C2 P1).

    The browser's ``requested`` id is a claim, and the list is the fact. So a
    requested id must be in the list. With no requested id, a WABA with one
    number gives that number. For a coexistence connect with several
    numbers, the one number with ``is_on_biz_app`` true is the pick (review
    P2). Every other case answers 400 and names why.
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
    if len(ids) > 1 and onboarding == "coexistence":
        on_app = [str(n["id"]) for n in numbers
                  if str(n.get("id")) in ids and n.get("is_on_biz_app") is True]
        if len(on_app) == 1:
            return on_app[0]
        raise HTTPException(status_code=400, detail=COEXISTENCE_CANNOT_TELL)
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
    # WS-20 WA-C3 P3. None for a plain Cloud connect. For coexistence:
    # 'requested' when Meta took both sync calls, 'failed' when it refused
    # one, and 'pending' when the server does not run the sync.
    history_sync: Literal["requested", "failed", "pending"] | None = None


# ── WS-20 WA-C3: the coexistence history sync (spec §12.4.1 P1, P3, P4) ──────
#
# Meta, "Onboard WhatsApp Business app users": two calls of
# `POST /<VER>/<PHONE_NUMBER_ID>/smb_app_data`, contacts first and then
# history, within 24 hours of the connect. Each answers `{messaging_product,
# request_id}`. Meta then sends the data to the webhook as the
# `smb_app_state_sync` and `history` fields, which the persist path stores.

#: The flag of record. OFF by default. It gates the two outbound calls and
#: nothing else: the parser and the persist path read the fields always.
HISTORY_SYNC_ENV = "WHATSAPP_HISTORY_SYNC"

#: The two sync types, in the order that Meta asks for.
SYNC_TYPES = ("smb_app_state_sync", "history")

#: The 409 detail of a retry after Meta's 24-hour window.
HISTORY_WINDOW_PASSED = (
    "Meta allows the history import only in the first 24 hours after you "
    "connect a number. To import the history now, disconnect this number and "
    "connect it again.")

#: The 400 detail of a retry while the flag is off.
HISTORY_SYNC_OFF = "The history import is not turned on on this server."


def history_sync_enabled(env: Any = None) -> bool:
    """True when this server runs the history sync calls. Pure."""
    src = env if env is not None else os.environ
    return str(src.get(HISTORY_SYNC_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on"}


async def request_app_data_sync(
    phone_number_id: str, token: str, graph_version: str, sync_type: str,
) -> str:
    """One ``smb_app_data`` call. Returns Meta's ``request_id``, or raises.

    The token goes in the Authorization header and the body is JSON, so no
    secret is ever in the URL (the WA-C2 rule). The caller checks that the
    number is digits, because it goes into the URL path."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/{graph_version}/{phone_number_id}/smb_app_data",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "sync_type": sync_type},
        )
        resp.raise_for_status()
        data = resp.json()
    request_id = data.get("request_id") if isinstance(data, dict) else None
    if not request_id:
        raise MetaAnswerError("Meta returned no request id for the sync.")
    return str(request_id)


#: The one write of the state after a sync call. It changes only a row that
#: is still `pending` or `failed`, so a webhook that marked the import
#: `complete` or `declined` first is never written back.
_HISTORY_STATE_UPDATE = """UPDATE wa_accounts
    SET history_sync_state = :state, history_sync_error = :error,
        updated_at = now()
    WHERE id = :id AND history_sync_state IN ('pending', 'failed')"""


async def start_history_sync(
    account_id: str, phone_number_id: str, token: str,
) -> tuple[str, str | None]:
    """Run the two sync calls for a saved coexistence account, then write
    the state. Returns ``(state, error)``, and never raises.

    The connect never fails on this step (P3). A refused call gives
    ``'failed'`` with ``friendly_meta_error`` text, and the log line carries
    ``meta_error_fields`` only, never the exception text."""
    from whatsapp_ingestion.providers.factory import (
        is_phone_number_id,
        safe_graph_version,
    )

    gv = safe_graph_version(
        os.environ.get("WHATSAPP_GRAPH_VERSION", "").strip() or None)
    state, error = "requested", None
    if not is_phone_number_id(phone_number_id):
        state, error = "failed", "The phone number id is not the numeric id that Meta shows."
    else:
        for sync_type in SYNC_TYPES:
            try:
                await request_app_data_sync(phone_number_id, token, gv, sync_type)
            except Exception as exc:
                _log.warning("whatsapp.history_sync.failed", account_id=account_id,
                             sync_type=sync_type, **meta_error_fields(exc))
                state, error = "failed", friendly_meta_error(exc)
                break
    try:
        async with _tenant_session() as db:
            await db.execute(text(_HISTORY_STATE_UPDATE),
                             {"id": account_id, "state": state, "error": error})
    except Exception as exc:
        # The state stays as it was. The member can start the import again.
        _log.warning("whatsapp.history_sync.state_write_failed",
                     account_id=account_id, error_class=type(exc).__name__)
    if state == "requested":
        _log.info("whatsapp.history_sync.requested", account_id=account_id)
    return state, error


class HistorySyncResponse(BaseModel):
    history_sync: Literal["requested", "failed"]
    history_sync_error: str | None = None


@router.post("/accounts/{account_id}/history-sync",
             response_model=HistorySyncResponse)
async def retry_history_sync(
    account_id: str, user: UserContext = Depends(get_current_user),
):
    """Start the history import again (WS-20 WA-C3 P4).

    400 with the flag off. 404 for an account of another member. 409 when the
    state is not ``pending`` or ``failed``, and 409 after Meta's 24-hour
    window. Meta is called after the session closes, so no connection waits
    on Meta."""
    from gateway.routes.whatsapp.transport.accounts import history_sync_deadline

    if not history_sync_enabled():
        raise HTTPException(status_code=400, detail=HISTORY_SYNC_OFF)
    async with _tenant_session() as db:
        await assert_account_owned(db, account_id, user.email or "anonymous")
        row = (await db.execute(
            text("""SELECT history_sync_state, created_at, phone_number_id,
                           credentials_encrypted
                    FROM wa_accounts WHERE id = :id"""),
            {"id": account_id},
        )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if row.history_sync_state not in ("pending", "failed"):
        raise HTTPException(
            status_code=409,
            detail="Only a history import that is pending or failed can start "
                   "again.")
    deadline = history_sync_deadline(row.created_at)
    if deadline is None or datetime.now(UTC) >= deadline:
        raise HTTPException(status_code=409, detail=HISTORY_WINDOW_PASSED)

    from acb_llm.key_store import get_key_store
    creds = json.loads(get_key_store().decrypt(row.credentials_encrypted))
    token = creds.get("access_token") if isinstance(creds, dict) else None
    if not token:
        raise HTTPException(
            status_code=409, detail="This number has no stored token. "
                                    "Disconnect it and connect it again.")
    state, error = await start_history_sync(
        account_id, row.phone_number_id, str(token))
    return HistorySyncResponse(history_sync=state, history_sync_error=error)


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
        _log.info("whatsapp.embedded.exchange_failed", **meta_error_fields(exc))
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc

    # 2. find the number in the WABA. A coexistence connect sends none.
    try:
        numbers = await list_waba_phone_numbers(waba_id, token, gv)
    except Exception as exc:
        _log.info("whatsapp.embedded.list_failed", **meta_error_fields(exc))
        raise HTTPException(status_code=400, detail=friendly_meta_error(exc)) \
            from exc
    phone_number_id = select_phone_number_id(numbers, requested, req.onboarding)

    # 3. Meta confirms the token for this number: the digit check, the
    # server's version and the profile `id` check of the manual path.
    profile = await verify_cloud_number(phone_number_id, {"access_token": token})

    # 4. subscribe our app to the WABA. A hard step: without it no message
    # arrives, so the connect fails and saves nothing.
    try:
        await subscribe_app_to_waba(waba_id, token, gv)
    except Exception as exc:
        _log.warning("whatsapp.embedded.subscribe_failed",
                     waba_id=waba_id, **meta_error_fields(exc))
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
    coexistence = req.onboarding == "coexistence"
    async with _tenant_session() as db:
        row = await persist_account(
            db, user_id=user.email or "anonymous", phone_number=phone,
            phone_number_id=phone_number_id, waba_id=waba_id,
            display_name=display, credentials=creds,
            webhook_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN") or None,
            verified_profile=profile,
            sync_status="live",
            history_sync_state="pending" if coexistence else None,
        )
        acct = _account_model(row)

    # 6. the history sync (WS-20 WA-C3 P3). Only a coexistence number has a
    # history on the phone app. It runs after the save has committed, and the
    # connect never fails on it. With the flag off the row stays `pending`.
    history_sync: str | None = None
    if coexistence:
        history_sync = "pending"
        if history_sync_enabled():
            history_sync, _ = await start_history_sync(
                acct.id, phone_number_id, token)
    return EmbeddedSignupResponse(
        account_id=acct.id, display_name=acct.display_name,
        phone_number=acct.phone_number, subscribed=True,
        history_sync=history_sync)
