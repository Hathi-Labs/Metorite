"""The WhatsApp channel of Reach — the ONLY module of Reach that sends
(WS-59 RE-1a, ``proactive_reach.md`` §8.1).

One message goes out like this:

1. **The gates.** The assistant channel is on, and its allowlist holds the
   org (``whatsapp_channel.flags``). The bot has credentials.
2. **The link.** The member's ACTIVE and CURRENT link in this org, read in
   ``tenant_session(org)``. No link: skip ``no_link``.
3. **The window.** Meta allows free text for 24 hours after the member's
   last message. The latest inbound ``whatsapp_bot_messages`` row of the
   linked phone, within 23.5 hours, opens it. Open: ``send_text``. Closed:
   the message goes as the approved template the caller names, with its body
   parameters. With no template it skips ``window_closed``. The loop decides
   which template, and when it holds a message instead (RE-2a).
4. **The record.** A sent message becomes an ``out`` row of
   ``whatsapp_bot_messages`` and an assistant turn in the member's WhatsApp
   thread, with the id ``reach-<outbox id>``. So a reply to it has context.

The bot's own helpers do the record (``bot_run``). This module imports them
and copies none. A failed send logs Meta's error fields only, never the
exception text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from acb_common.db import bind_tenant, release_tenant, tenant_session
from sqlalchemy import text

from gateway.reach import flags

_log = get_logger("reach.whatsapp")

#: Meta's window is 24 hours. Reach keeps 30 minutes of margin.
WINDOW_HOURS = 23.5

_LINK_SQL = (
    "SELECT wa_id FROM whatsapp_member_links "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND lower(member_email) = lower(:m) "
    "AND status = 'active' AND is_current AND wa_id IS NOT NULL "
    "ORDER BY linked_at DESC NULLS LAST LIMIT 1"
)

#: The member's display name in this org, for the greeting of a template.
#: The WhatsApp link reply reads the same column (``inbound._NAMES_SQL``).
_NAME_SQL = (
    "SELECT NULLIF(btrim(display_name), '') FROM app_user "
    "WHERE organization_id = CAST(:org AS uuid) AND lower(email) = lower(:m) "
    "LIMIT 1"
)

_WINDOW_SQL = (
    "SELECT EXISTS (SELECT 1 FROM whatsapp_bot_messages "
    "WHERE organization_id = CAST(:org AS uuid) AND wa_id = :wa "
    "AND direction = 'in' "
    "AND received_at > now() - make_interval(mins => CAST(:mins AS int)))"
)


@dataclass(frozen=True)
class Delivery:
    """What one send did. ``state`` is ``sent``, ``skipped`` or ``failed``."""

    state: str
    regime: str | None = None
    reason: str | None = None
    provider_message_id: str | None = None


async def linked_phone(org: str, member: str) -> str | None:
    """The member's linked phone in *org*, or None."""
    async with tenant_session(org) as db:
        row = (await db.execute(text(_LINK_SQL), {"org": org, "m": member})).first()
    return None if row is None else str(row[0])


async def window_open(org: str, wa_id: str) -> bool:
    """True when the linked phone wrote to the bot in the last 23.5 hours."""
    async with tenant_session(org) as db:
        return bool((await db.execute(text(_WINDOW_SQL), {
            "org": org, "wa": wa_id, "mins": int(WINDOW_HOURS * 60)})).scalar())


async def reach_state(org: str, member: str) -> tuple[str | None, bool]:
    """The member's linked phone or None, and True when its window is open."""
    wa_id = await linked_phone(org, member)
    if wa_id is None:
        return None, False
    return wa_id, await window_open(org, wa_id)


async def display_name(org: str, member: str) -> str | None:
    """The member's display name in *org*, or None. Never raises."""
    try:
        async with tenant_session(org) as db:
            value = (await db.execute(text(_NAME_SQL), {"org": org, "m": member})).scalar()
    except Exception as exc:
        _log.warning("reach.whatsapp.name_failed", error_class=type(exc).__name__)
        return None
    return str(value) if value else None


def send_failure(exc: BaseException) -> str:
    """Why a send failed, for the retry rule (review of RE-5 fix round 1, P2).

    ``send_retry``: the request surely did not reach Meta, or Meta asked us
    to slow down (429). A later pass may try again. ``send_refused``: Meta
    refused it (another 4xx), and a retry would fail the same way.
    ``send_unsure``: a timeout or a 5xx, after which Meta may have the
    message. A retry could send it twice, so none is made."""
    import httpx

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "send_retry"
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(exc.response, "status_code", None)
        if status == 429:
            return "send_retry"
        if isinstance(status, int) and 400 <= status < 500:
            return "send_refused"
    return "send_unsure"


def body_components(params: list[str] | None) -> list[dict[str, Any]] | None:
    """The ``components`` of a template send: one body with text parameters."""
    if not params:
        return None
    return [{"type": "body",
             "parameters": [{"type": "text", "text": p} for p in params]}]


def template_marker(template: str, params: list[str] | None) -> str:
    """What the thread records for a template send. The first parameter is
    the greeting name, so the marker keeps only the rest."""
    said = " / ".join(params[1:]) if params else ""
    return (f"(Sent the WhatsApp template {template}: {said}.)" if said
            else f"(Sent the WhatsApp template {template}.)")


def channel_ready(org: str) -> bool:
    """The assistant channel is on for *org*, and the bot can send."""
    from gateway.routes.whatsapp_channel import flags as wa_flags

    return wa_flags.org_allowed(org) and wa_flags.bot_credentials() is not None


async def deliver(org: str, member: str, *, kind: str, body: str, outbox_id: str,
                  template: str | None = None, template_params: list[str] | None = None,
                  reach: tuple[str | None, bool] | None = None,
                  record: str | None = None) -> Delivery:
    """Send *body* to *member* on WhatsApp. Never raises.

    *reach* is the ``(wa_id, window_open)`` the loop already read, so the
    loop's choice and the send agree. None reads them here. *record* is
    what the thread records for a free-text send, when it is not *body*:
    the loop quotes text that a rule or a workflow wrote (RE-5, P2-4)."""
    from gateway.routes.whatsapp_channel import flags as wa_flags

    if not wa_flags.org_allowed(org):
        return Delivery("skipped", reason="channel_off")
    creds = wa_flags.bot_credentials()
    if creds is None:
        return Delivery("skipped", reason="no_credentials")
    if reach is None:
        try:
            reach = await reach_state(org, member)
        except Exception as exc:
            _log.warning("reach.whatsapp.read_failed", error_class=type(exc).__name__)
            return Delivery("failed", reason="read_failed")
    wa_id, is_open = reach
    if wa_id is None:
        return Delivery("skipped", reason="no_link")
    if not is_open and not template:
        return Delivery("skipped", reason="window_closed")

    from whatsapp_ingestion.providers.factory import build_provider

    from gateway.routes.whatsapp.transport.connect import meta_error_fields

    try:
        provider = build_provider("cloud_api", creds)
        if is_open:
            regime = "free_text"
            out_id = await provider.send_text(wa_id, body)
        else:
            regime = "template"
            out_id = await provider.send_template(
                wa_id, str(template), flags.template_language(),
                components=body_components(template_params))
    except Exception as exc:
        reason = send_failure(exc)
        _log.warning("reach.whatsapp.send_failed", kind=kind, phone_hint=wa_id[-4:],
                     reason=reason, **meta_error_fields(exc))
        return Delivery("failed", reason=reason)
    out = str(out_id)[:200] if out_id else None
    # A template carries none of the body, so the thread records what the
    # member saw, not the body (review of RE-1a, P3).
    seen = (record or body) if regime == "free_text" \
        else template_marker(str(template), template_params)
    await _record(org, member, wa_id, out, seen, outbox_id)
    _log.info("reach.whatsapp.sent", kind=kind, regime=regime, phone_hint=wa_id[-4:])
    return Delivery("sent", regime=regime, provider_message_id=out)


async def _record(org: str, member: str, wa_id: str, out_id: str | None, body: str,
                  outbox_id: str) -> None:
    """The ``out`` row and the assistant turn. The send already happened, so a
    failure here only logs."""
    from gateway.chat_fold import persist_channel_reply
    from gateway.routes.whatsapp_channel import bot_run

    email = member.strip().lower()
    mid = f"reach-{outbox_id}"
    token = bind_tenant(org)
    try:
        async with bot_run._thread_lock(org, email):
            sid = await bot_run._find_thread(email) or bot_run.new_thread_id(org, email, mid)
            agent, others, has_turn = await bot_run._session_state(sid, mid)
            if not has_turn:
                await bot_run._write_session(org, email, sid, agent=agent or bot_run.AGENT,
                                             preview=bot_run._preview(body), count=others + 1)
                await persist_channel_reply(sid, mid, body, user_id=email,
                                            agent_name=bot_run.AGENT,
                                            timestamp_ms=bot_run._now_ms(),
                                            organization_id=org)
            if out_id:
                await bot_run._insert(org=org, email=email, wa_id=wa_id, wamid=out_id,
                                      direction="out", state="replied", session_id=sid)
    except Exception as exc:
        _log.warning("reach.whatsapp.record_failed", error_class=type(exc).__name__)
    finally:
        release_tenant(token)
