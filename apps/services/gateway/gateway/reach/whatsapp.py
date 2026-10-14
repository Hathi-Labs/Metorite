"""The WhatsApp channel of Reach — the ONLY module of Reach that sends
(WS-59 RE-1a, ``proactive_reach.md`` §8.1).

One message goes out like this:

1. **The gates.** The assistant channel is on, and its allowlist holds the
   org (``whatsapp_channel.flags``). The bot has credentials.
2. **The link.** The member's ACTIVE and CURRENT link in this org, read in
   ``tenant_session(org)``. No link: skip ``no_link``.
3. **The window.** Meta allows free text for 24 hours after the member's
   last message. The latest inbound ``whatsapp_bot_messages`` row of the
   linked phone, within 23.5 hours, opens it. Open: ``send_text``. Closed: a
   brief goes as the approved template, when one is set. Else skip
   ``window_closed``.
4. **The record.** A sent message becomes an ``out`` row of
   ``whatsapp_bot_messages`` and an assistant turn in the member's WhatsApp
   thread, with the id ``reach-<outbox id>``. So a reply to it has context.

The bot's own helpers do the record (``bot_run``). This module imports them
and copies none. A failed send logs Meta's error fields only, never the
exception text.
"""

from __future__ import annotations

from dataclasses import dataclass

from acb_common import get_logger
from acb_common.db import bind_tenant, release_tenant, tenant_session
from sqlalchemy import text

_log = get_logger("reach.whatsapp")

#: Meta's window is 24 hours. Reach keeps 30 minutes of margin.
WINDOW_HOURS = 23.5
#: The language of the brief template.
TEMPLATE_LANGUAGE = "en"

_LINK_SQL = (
    "SELECT wa_id FROM whatsapp_member_links "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND lower(member_email) = lower(:m) "
    "AND status = 'active' AND is_current AND wa_id IS NOT NULL "
    "ORDER BY linked_at DESC NULLS LAST LIMIT 1"
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


def channel_ready(org: str) -> bool:
    """The assistant channel is on for *org*, and the bot can send."""
    from gateway.routes.whatsapp_channel import flags as wa_flags

    return wa_flags.org_allowed(org) and wa_flags.bot_credentials() is not None


async def deliver(org: str, member: str, *, kind: str, body: str, outbox_id: str,
                  template: str | None = None) -> Delivery:
    """Send *body* to *member* on WhatsApp. Never raises."""
    from gateway.routes.whatsapp_channel import flags as wa_flags

    if not wa_flags.org_allowed(org):
        return Delivery("skipped", reason="channel_off")
    creds = wa_flags.bot_credentials()
    if creds is None:
        return Delivery("skipped", reason="no_credentials")
    try:
        wa_id = await linked_phone(org, member)
        if wa_id is None:
            return Delivery("skipped", reason="no_link")
        is_open = await window_open(org, wa_id)
    except Exception as exc:
        _log.warning("reach.whatsapp.read_failed", error_class=type(exc).__name__)
        return Delivery("failed", reason="read_failed")
    if not is_open and not (kind == "brief" and template):
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
            out_id = await provider.send_template(wa_id, str(template), TEMPLATE_LANGUAGE)
    except Exception as exc:
        _log.warning("reach.whatsapp.send_failed", kind=kind, phone_hint=wa_id[-4:],
                     **meta_error_fields(exc))
        return Delivery("failed", reason="send_failed")
    out = str(out_id)[:200] if out_id else None
    await _record(org, member, wa_id, out, body, outbox_id)
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
