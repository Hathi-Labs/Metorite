"""Calendar · connections — connect Google or Outlook, sync now, disconnect (CE-10).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1.

    GET     /tasks/calendar/connections                    the member's accounts
    POST    /tasks/calendar/connections/{provider}/connect the consent URL
    POST    /tasks/calendar/connections/{id}/sync          sync now
    DELETE  /tasks/calendar/connections/{id}               disconnect

``{provider}`` is ``google`` or ``outlook``. There is no calendar callback
route: the flow shares the MAIL redirect URI (D215 point 2). The mail callback
(``routes/email/transport/oauth.py``) asks ``is_calendar_state`` and hands a
calendar state to ``finish_consent``.

⚠️ **Dark.** Every route answers 404 unless ``CALENDAR_SYNC_READ`` names the
org (and ``CALENDAR_SYNC_MEMBERS`` the member, if set). Off sends no request.

**The org and the member come from the session (R5, R11).** The callback
refuses, with 400 and NO call to the provider, a state that is forged, expired,
signed for another purpose, or made for another org or member than the
session's. A member sees and changes their own connections only. A row of
another member answers 404.

**Disconnect** runs the three steps of §8.1 item 3 in ONE transaction, under
the row lock that a sync takes before it writes. It sends no revoke (see
``calendar_sync/google.py``).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from fastapi.responses import RedirectResponse
from gateway.calendar_sync import connections, engine, flags
from gateway.calendar_sync.provider import (
    OWNING_APP,
    CalendarProvider,
    ProviderError,
)
from gateway.calendar_sync.scheduler import PROVIDERS
from gateway.connections import store
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_state,
    verify_state,
)
from gateway.routes.tasks.core import _tenant_session, router
from sqlalchemy import text

#: The purpose of each signed state. A value signed for one never verifies for
#: another (``signing.py``).
STATE_PURPOSE = {"google": "calendar_google", "outlook": "calendar_microsoft"}

#: The provider word of the API -> the provider name of the connection row.
ROWS = connections.PROVIDER_ROWS

RETURN_OK = "/calendar?calendar_connected="
RETURN_FAILED = "/calendar?calendar_error="

_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_CONNECT_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"


async def _require_on(user: UserContext = Depends(get_current_user)) -> None:
    """404 while the flag is off. A route dependency, so nothing else runs."""
    if not flags.read_allowed(
            str(user.organization_id) if user.organization_id else None, user.email):
        raise HTTPException(status_code=404, detail="Not Found")


_ON = [Depends(_require_on)]


def _scope(user: UserContext) -> tuple[str, str]:
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    me = (user.email or "").strip().lower()
    if not me:
        raise HTTPException(status_code=403, detail="Forbidden: a connection needs a member.")
    return str(user.organization_id), me


def _provider(word: str) -> tuple[str, CalendarProvider]:
    row = ROWS.get(word)
    factory = PROVIDERS.get(row or "")
    prov = None
    if factory is not None:
        try:
            prov = factory()
        except Exception:  # a client that is not set up
            prov = None
    if prov is None:
        raise HTTPException(status_code=503, detail=f"{word}_not_configured")
    return word, prov


def _failed(code: str, base: str = "") -> RedirectResponse:
    raw = (code or "").strip().lower()
    safe = raw if _CODE.match(raw) else "failed"
    return RedirectResponse(f"{base}{RETURN_FAILED}{safe}", status_code=302)


def _connection_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise HTTPException(status_code=404, detail="Connection not found.") from None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def connection_dict(conn: store.Connection, count: int) -> dict[str, Any]:
    return {
        "id": conn.id,
        "provider": connections.PROVIDER_WORDS.get(conn.provider, conn.provider),
        "account_email": conn.provider_meta.get(connections.META_ACCOUNT_EMAIL),
        "status": conn.status,
        "last_sync_at": _iso(conn.last_sync_at),
        "last_error": conn.last_error,
        "metorite_calendar": connections.metorite_calendar_id(conn) is not None,
        "entry_count": count,
    }


async def _own(db: Any, org: str, me: str, connection_id: str) -> store.Connection:
    conn = await connections.get_connection(db, org, connection_id)
    if conn is None or (conn.member_email or "").lower() != me:
        raise HTTPException(status_code=404, detail="Connection not found.")
    return conn


@router.get("/calendar/connections", dependencies=_ON)
async def list_connections(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    org, me = _scope(user)
    async with _tenant_session() as db:
        rows = [c for c in await connections.member_connections(db, org, me)
                if c.status != store.STATUS_DISCONNECTED]
        counts = await connections.entry_counts(db, org, [c.id for c in rows])
    available: dict[str, dict[str, bool]] = {}
    for word, row in ROWS.items():
        factory = PROVIDERS.get(row)
        try:
            available[word] = {"available": bool(factory and factory() is not None)}
        except Exception:
            available[word] = {"available": False}
    return {"enabled": True, "providers": available,
            "connections": [connection_dict(c, counts.get(c.id, 0)) for c in rows]}


@router.post("/calendar/connections/{provider}/connect", dependencies=_ON)
async def connect(
    provider: Literal["google", "outlook"], user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """The consent URL, with a signed state. The member follows it."""
    org, me = _scope(user)
    word, prov = _provider(provider)
    try:
        state = sign_state(purpose=STATE_PURPOSE[word], org=org, member=me)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_member") from exc
    return {"url": prov.consent_url(state=state, login_hint=me)}


#: The word of the mail callback path -> the calendar provider word. The
#: mail callback is the one redirect URI (D215 point 2).
MAIL_PROVIDER_WORD = {"gmail": "google", "microsoft": "outlook"}


def is_calendar_state(state: str | None, provider_word: str, user: UserContext) -> bool:
    """True when *state* is a valid calendar state of THIS session's member.

    The mail callback asks this first. A false answer leaves the mail path to
    run as before, and it never reveals why (no oracle). The flag is checked
    here, so a flag-off org never reaches the calendar code.
    """
    if provider_word not in STATE_PURPOSE or not state:
        return False
    if not user.organization_id or not (user.email or "").strip():
        return False
    if not flags.read_allowed(str(user.organization_id), user.email):
        return False
    payload = verify_state(state, purpose=STATE_PURPOSE[provider_word])
    return (payload is not None and payload["org"] == str(user.organization_id)
            and payload["member"] == (user.email or "").strip().lower())


async def finish_consent(
    user: UserContext, provider: str, code: str | None, state: str | None,
    error: str | None, *, base: str = "",
) -> RedirectResponse:
    """Finish the consent and save the token. The sync loop reads the rest.

    The mail callback calls this for a calendar state (``is_calendar_state``
    was true). *base* is the workbench origin, so the Location is absolute and
    the mail BFF route can check its origin.
    """
    org, me = _scope(user)
    # 1. The state, before any other work. Every refusal is the same 400.
    payload = verify_state(state, purpose=STATE_PURPOSE[provider])
    if payload is None or payload["org"] != org or payload["member"] != me:
        raise HTTPException(status_code=400, detail="invalid_state")
    if error:
        return _failed(error if error != "access_denied" else "consent_declined", base)
    if not code:
        return _failed("no_code", base)
    word, prov = _provider(provider)
    # 2. The code for tokens, and the grant check. No session is open.
    try:
        cred = await prov.exchange_code(code)
        who = await prov.account_identity(cred)
    except ProviderError as exc:
        return _failed(exc.code or "exchange_failed", base)
    except Exception:  # a transport or token failure
        return _failed("exchange_failed", base)
    meta = {**dict(cred.provider_meta),
            connections.META_ACCOUNT_EMAIL: who.email,
            connections.META_ACCOUNT_ID: who.account_id}
    cred = replace(cred, provider_meta=meta)
    # 3. The token, in one transaction. Another account drops the old copies.
    async with _tenant_session() as db:
        await db.execute(text(_CONNECT_LOCK_SQL),
                         {"key": f"calendar.connect:{org}:{me}:{prov.provider}"})
        before = await store.get(db, org, prov.provider, OWNING_APP,
                                 scope_kind=store.SCOPE_MEMBER, member_email=me)
        if before is not None:
            old = before.provider_meta.get(connections.META_ACCOUNT_ID)
            if old and old != who.account_id:
                # The row lock a sync takes before it writes, so an in-flight
                # sync of the old account cannot add rows after the delete.
                locked = await store.lock_by_id(db, org, before.id)
                if locked is not None:
                    await connections.disconnect_connection(db, org, locked)
        await store.save_credential(
            db, org, prov.provider, OWNING_APP, cred, connected_by=me,
            scope_kind=store.SCOPE_MEMBER, member_email=me, scopes=cred.scopes)
    return RedirectResponse(f"{base}{RETURN_OK}{word}", status_code=302)


@router.post("/calendar/connections/{connection_id}/sync", dependencies=_ON)
async def sync_now(
    connection_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    org, me = _scope(user)
    cid = _connection_id(connection_id)
    async with _tenant_session() as db:
        conn = await _own(db, org, me, cid)
    factory = PROVIDERS.get(conn.provider)
    prov = factory() if factory else None
    if prov is None:
        raise HTTPException(status_code=503, detail="not_configured")
    outcome = await engine.sync_connection(org, cid, prov)
    status = outcome.status if outcome.status in (
        "ok", "needs_reconnect", "busy", "error") else "error"
    return {"status": status, "added": outcome.added, "updated": outcome.updated,
            "removed": outcome.removed}


@router.delete("/calendar/connections/{connection_id}", dependencies=_ON)
async def disconnect(
    connection_id: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    org, me = _scope(user)
    cid = _connection_id(connection_id)
    async with _tenant_session() as db:
        await _own(db, org, me, cid)
        locked = await store.lock_by_id(db, org, cid)  # the lock a sync takes
        if locked is None:
            raise HTTPException(status_code=404, detail="Connection not found.")
        removed = await connections.disconnect_connection(db, org, locked)
    return {"disconnected": True, "removed_entries": removed}

