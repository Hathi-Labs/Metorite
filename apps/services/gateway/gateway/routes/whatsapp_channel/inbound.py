"""Chat on WhatsApp — what the bot number does with a webhook batch (WS-47 WAC-2).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.2, §5.3, §5.4
("Link redemption, as WAC-2 builds it") and §5.9.

``gateway.routes.whatsapp.transport.webhook.receive_webhook`` splits each
batch by Meta number. The group of the bot number
(:func:`flags.bot_phone_number_id`) comes here, and never reaches the WS-20
inbox path. Every other group stays on that path, unchanged.

What this module does with the bot's group:

* **Fail closed.** With no ``WHATSAPP_APP_SECRET``, the route accepts an
  unsigned body in dev. This path then does nothing, in dev too (§5.9).
* **Dark.** With ``WHATSAPP_ASSISTANT_ENABLED`` off, or no bot token, the
  group is dropped with a log line. No reply, no write.
* **A status update** gets a log line with the status and no text.
* **A link message** ("Link me: <code>") redeems the code (§5.4). The
  organization comes from the code's row, never from the message. The row's
  org must also be on ``WHATSAPP_ASSISTANT_ORGS``.
* **An unknown phone with no code** gets :data:`REPLY_UNKNOWN`, which holds no
  org data, and nothing is written.
* **A linked phone with a text** (WAC-3) is recorded by
  :func:`bot_run.record_inbound` before the 200, and its run starts after
  the 200. The org comes from the phone's CURRENT link. A link message from
  an UNLINKED phone keeps the in-process redelivery record below, because
  that message has no org, so a tenant table cannot hold it.
* **A button, list or reaction message** gets no action (WAC-4, WAC-10).

The replies are FIXED texts (§5.4 table). Only the success reply holds org
data, and it goes only to the phone that just proved it holds the code. The
route sends them AFTER its 200, as a background task, so a slow Graph call
never makes Meta send the batch again (:func:`send_replies`).

**Two unbound reads, and no others.** :func:`_active_links_for_phone` and
:func:`_code_rows` find the organization, so they cannot run bound. Each
reads only through a SECURITY DEFINER function of migration
``whatsapp_member_link_lookups``, granted to ``acb_app`` alone. Every write runs
in ``tenant_session`` with the org that the code's row names (R5).

Fences: ``tests/unit/test_wac_bot_inbound.py`` (database-free) and
``tests/unit/test_wac_bot_link_r8.py`` (R8, FORCE RLS as a non-privileged role).
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from gateway.db import bind_tenant, get_db, release_tenant, tenant_session
from gateway.routes.whatsapp_channel import bot_run, flags
from gateway.routes.whatsapp_channel.link import CODE_ALPHABET, CODE_LENGTH, code_hash
from sqlalchemy import text

_log = get_logger(__name__)

# ── The fixed replies (§5.4) ────────────────────────────────────────────────

REPLY_UNKNOWN = (
    "Hi, this is Metorite. To chat with your workspace, open My Profile in "
    "Metorite and select Chat on WhatsApp."
)
REPLY_FAILED = (
    "That link code did not work. Open My Profile in Metorite and get a new link."
)
REPLY_OTHER_PERSON = (
    "This phone is already linked to another Metorite account. Unlink it "
    "there first."
)
_REPLY_LINKED = (
    "Linked to {org} as {member}. Ask me about your tasks, projects or calendar."
)

#: A sender id as the table's CHECK holds it: digits only.
_WA_ID = re.compile(r"[0-9]{6,20}")

#: "Link me: <code>", in any case and with any spacing. Everything after the
#: colon is the code attempt, so "Link me:" with a bad code is a failed code.
_LINK_ME = re.compile(r"\A\s*link\s*me\s*:(?P<rest>.*)\Z", re.IGNORECASE | re.DOTALL)

#: Crockford decoding: the letters a person can type for a digit.
_CROCKFORD = str.maketrans({"O": "0", "I": "1", "L": "1"})

#: Meta message types that WAC-2 does nothing with: a tap on a button or a
#: list row (WAC-4 and WAC-10 own those), a reaction, and Meta's own notices.
_NO_ACTION_TYPES = frozenset({
    "interactive", "button", "reaction", "system", "unsupported", "order",
    "request_welcome",
})

# ── The per-sender limit (§5.4) ─────────────────────────────────────────────
#
# ⚠️ In-process and SINGLE-PROCESS. The counter lives in this gateway process,
# so a restart resets it, and two workers would each keep their own. The
# gateway runs one process today (`deploy/hostinger/acb-gateway.service`). A
# move to several workers makes this limit advisory, and that move must
# replace it with a shared counter. The idiom is the deque limiter of
# `routes/workflows/hooks.py`.

FAILED_LIMIT = 5
FAILED_WINDOW_S = 15 * 60
_MAX_TRACKED_SENDERS = 10_000

_FAILED: dict[str, deque[float]] = {}


def _failures(wa_id: str, now: float) -> deque[float]:
    bucket = _FAILED.setdefault(wa_id, deque())
    while bucket and now - bucket[0] > FAILED_WINDOW_S:
        bucket.popleft()
    return bucket


def is_limited(wa_id: str) -> bool:
    """True when this sender has used up its failed redemptions."""
    now = time.monotonic()
    limited = len(_failures(wa_id, now)) >= FAILED_LIMIT
    if not _FAILED[wa_id]:
        del _FAILED[wa_id]
    return limited


def record_failure(wa_id: str) -> None:
    """Count one failed redemption for this sender."""
    now = time.monotonic()
    if len(_FAILED) >= _MAX_TRACKED_SENDERS:
        for key in [k for k, b in _FAILED.items()
                    if not b or now - b[-1] > FAILED_WINDOW_S]:
            del _FAILED[key]
    _failures(wa_id, now).append(now)


# ── The redelivery record ───────────────────────────────────────────────────
#
# Meta can deliver one message twice, and a 500 for another group of the same
# batch makes Meta send the whole batch again. This record maps each link
# message already handled, by (sender, wamid), to the reply it got. A repeat
# gets the same reply again: no second lookup, and no second count against
# the per-sender limit. A repeat of a SUCCESSFUL link message gets the success
# reply even when the sender is limited now (§5.4: a redelivery never gets
# the failure reply).
#
# ⚠️ In-process and SINGLE-PROCESS, like the limiter above, and bounded: at
# most `_HANDLED_CAP` entries for `_HANDLED_TTL_S` each. WAC-3's durable
# record (`whatsapp_bot_messages`) holds the other messages of a LINKED phone.
# A link message stays here, because its sender may have no org yet.

_HANDLED_CAP = 2_000
_HANDLED_TTL_S = 30 * 60

_HANDLED: OrderedDict[tuple[str, str], tuple[float, Reply]] = OrderedDict()


def handled_reply(wa_id: str, wamid: str | None) -> Reply | None:
    """The reply that this message got before, or None when it is new."""
    if not wamid:
        return None
    key = (wa_id, wamid)
    hit = _HANDLED.get(key)
    if hit is None:
        return None
    if time.monotonic() - hit[0] > _HANDLED_TTL_S:
        del _HANDLED[key]
        return None
    return hit[1]


def remember_reply(wa_id: str, wamid: str | None, reply: Reply) -> None:
    """Keep the final reply of one link message, for a redelivery."""
    if not wamid:
        return
    now = time.monotonic()
    _HANDLED[(wa_id, wamid)] = (now, reply)
    _HANDLED.move_to_end((wa_id, wamid))
    while _HANDLED:
        oldest_key, (at, _reply) = next(iter(_HANDLED.items()))
        if len(_HANDLED) <= _HANDLED_CAP and now - at <= _HANDLED_TTL_S:
            break
        del _HANDLED[oldest_key]


# ── The reply ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reply:
    """One message for the bot to send. ``kind`` names the case for the log,
    so no log line needs the text."""

    to: str
    text: str
    kind: str


def normalise_code(rest: str) -> str | None:
    """The code as the issue route made it, or None when it cannot be one.

    Upper case, with spaces and hyphens removed, and O, I and L mapped as
    Crockford decoding maps them. This runs BEFORE :func:`link.code_hash`,
    because the hash is of the exact code.
    """
    code = re.sub(r"[\s\-]+", "", rest).upper().translate(_CROCKFORD)
    if len(code) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in code):
        return None
    return code


def link_code_attempt(body: str) -> str | None:
    """The text after "Link me:", or None when the message is not a link message."""
    match = _LINK_ME.match(body or "")
    return match.group("rest") if match else None


# ── The two cross-tenant reads ──────────────────────────────────────────────


async def _active_links_for_phone(wa_id: str) -> list[Any]:
    """Every active link of one phone, across every tenant.

    Tenant DISCOVERY, so the read cannot run bound. It reads only through the
    SECURITY DEFINER ``whatsapp_member_links_for_phone``. No row when the
    function's owner cannot see through RLS, which reads as an unknown phone.
    """
    db = await get_db()
    try:
        return list((await db.execute(
            text("SELECT organization_id::text AS organization_id, "
                 "member_email, is_current "
                 "FROM public.whatsapp_member_links_for_phone(:wa)"),
            {"wa": wa_id},
        )).mappings().all())
    finally:
        await db.close()


async def _code_rows(hashed: str, wa_id: str) -> list[Any]:
    """The row of one link code: pending and live, or active for THIS phone.

    Tenant DISCOVERY: the sender of a code does not say which org issued it.
    It reads only through the SECURITY DEFINER ``whatsapp_member_link_for_code``,
    which never returns a row of another phone.
    """
    db = await get_db()
    try:
        return list((await db.execute(
            text("SELECT id::text AS id, organization_id::text AS organization_id, "
                 "member_email, status, code_expires_at "
                 "FROM public.whatsapp_member_link_for_code(:h, :wa)"),
            {"h": hashed, "wa": wa_id},
        )).mappings().all())
    finally:
        await db.close()


# ── The redemption, inside the code's tenant ────────────────────────────────

#: DB-global, on the phone: it serialises the one-person check and the
#: is_current choice of two redemptions from one phone, in any orgs.
_PHONE_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended('wac_link_phone:' || :wa, 0))"
)

_LOCK_PENDING_SQL = """
SELECT id FROM whatsapp_member_links
 WHERE id = CAST(:id AS uuid)
   AND status = 'pending'
   AND code_expires_at > now()
   FOR UPDATE
"""

#: The member links this phone in this org again with a new code. The old
#: row gives way, so the partial unique index on (wa_id, org) holds.
_REVOKE_SAME_ORG_SQL = """
UPDATE whatsapp_member_links
   SET status = 'revoked', revoked_at = now(), is_current = false
 WHERE organization_id = CAST(:org AS uuid)
   AND wa_id = :wa
   AND status = 'active'
"""

#: The row as it stands now, after the locked re-check found it not pending.
_ROW_STATE_SQL = """
SELECT status, wa_id FROM whatsapp_member_links WHERE id = CAST(:id AS uuid)
"""

_ACTIVATE_SQL = """
UPDATE whatsapp_member_links
   SET status = 'active', wa_id = :wa, linked_at = now(), is_current = :cur
 WHERE id = CAST(:id AS uuid)
   AND status = 'pending'
"""

_NAMES_SQL = """
SELECT o.display_name AS org_name,
       (SELECT NULLIF(btrim(u.display_name), '')
          FROM app_user u
         WHERE lower(u.email) = lower(:email)
           AND u.organization_id = CAST(:org AS uuid)
         LIMIT 1) AS member_name
  FROM organization o
 WHERE o.id = CAST(:org AS uuid)
"""


async def _activate(db: Any, row: Any, wa_id: str) -> str:
    """Link the phone with the code's row. Returns the outcome.

    ``linked``, ``again`` (a redelivery: the row is already active for this
    phone), ``other_person`` or ``failed`` (the code went stale or was used).
    It writes only for ``linked``.
    """
    await db.execute(text(_PHONE_LOCK_SQL), {"wa": wa_id})
    if row["status"] == "active":
        # The function returns an active row only for this same phone.
        return "again"

    held = (await db.execute(
        text(_LOCK_PENDING_SQL), {"id": row["id"]},
    )).mappings().all()
    if not held:
        # A concurrent copy of the same message from the same phone may have
        # linked the row first. That is a redelivery, not a failed code.
        now_row = (await db.execute(
            text(_ROW_STATE_SQL), {"id": row["id"]},
        )).mappings().first()
        if (now_row is not None and now_row["status"] == "active"
                and now_row["wa_id"] == wa_id):
            return "again"
        return "failed"

    member = str(row["member_email"]).strip().lower()
    org = str(row["organization_id"])
    links = (await db.execute(
        text("SELECT organization_id::text AS organization_id, member_email, "
             "is_current FROM public.whatsapp_member_links_for_phone(:wa)"),
        {"wa": wa_id},
    )).mappings().all()
    if any(str(lk["member_email"]).strip().lower() != member for lk in links):
        return "other_person"

    same_org = [lk for lk in links if lk["organization_id"] == org]
    current_elsewhere = any(
        lk["is_current"] for lk in links if lk["organization_id"] != org
    )
    if same_org:
        await db.execute(text(_REVOKE_SAME_ORG_SQL), {"org": org, "wa": wa_id})
    is_current = not current_elsewhere if same_org else not any(
        lk["is_current"] for lk in links
    )
    await db.execute(
        text(_ACTIVATE_SQL), {"id": row["id"], "wa": wa_id, "cur": is_current},
    )
    return "linked"


async def _success_text(db: Any, org: str, email: str) -> str:
    names = (await db.execute(
        text(_NAMES_SQL), {"org": org, "email": email},
    )).mappings().first()
    org_name = (names["org_name"] if names else None) or "your organization"
    member = (names["member_name"] if names else None) or email.split("@", 1)[0]
    return _REPLY_LINKED.format(org=org_name[:100], member=member[:100])


async def redeem(wa_id: str, rest: str, wamid: str | None = None) -> Reply:
    """Redeem the link code that *wa_id* sent. Returns the one reply.

    A message handled before gets its earlier reply again, BEFORE the limiter
    and with no lookup (the redelivery record above).
    """
    prior = handled_reply(wa_id, wamid)
    if prior is not None:
        _log.info("whatsapp_channel.link.redelivered", phone_hint=wa_id[-4:],
                  kind=prior.kind)
        return prior
    reply = await _redeem_once(wa_id, rest)
    remember_reply(wa_id, wamid, reply)
    return reply


async def _redeem_once(wa_id: str, rest: str) -> Reply:
    hint = wa_id[-4:]
    if is_limited(wa_id):
        _log.info("whatsapp_channel.link.rate_limited", phone_hint=hint)
        return Reply(wa_id, REPLY_FAILED, "failed")

    code = normalise_code(rest)
    rows = await _code_rows(code_hash(code), wa_id) if code else []
    if len(rows) != 1:
        record_failure(wa_id)
        _log.info("whatsapp_channel.link.code_refused", phone_hint=hint,
                  reason="shape" if code is None else "no_row")
        return Reply(wa_id, REPLY_FAILED, "failed")

    row = rows[0]
    org = str(row["organization_id"])
    if not flags.org_allowed(org):
        record_failure(wa_id)
        _log.info("whatsapp_channel.link.org_closed", phone_hint=hint,
                  organization_id=org)
        return Reply(wa_id, REPLY_FAILED, "failed")

    token = bind_tenant(org)
    try:
        async with tenant_session() as db:
            outcome = await _activate(db, row, wa_id)
            text_ = (await _success_text(db, org, str(row["member_email"]))
                     if outcome in ("linked", "again") else None)
    finally:
        release_tenant(token)

    _log.info("whatsapp_channel.link.redeemed", phone_hint=hint,
              organization_id=org, link_id=row["id"], outcome=outcome)
    if outcome == "other_person":
        return Reply(wa_id, REPLY_OTHER_PERSON, "other_person")
    if outcome == "failed" or text_ is None:
        record_failure(wa_id)
        return Reply(wa_id, REPLY_FAILED, "failed")
    return Reply(wa_id, text_, outcome)


# ── One message, and the whole group ────────────────────────────────────────


async def _handle_message(msg: Any) -> Reply | bot_run.RunRequest | None:
    if msg.direction != "in" or msg.from_history or msg.is_echo \
            or msg.chat_kind != "dm":
        return None
    wa_id = str(msg.sender_wa_id or "")
    if not _WA_ID.fullmatch(wa_id):
        _log.warning("whatsapp_channel.bot.sender_refused", length=len(wa_id))
        return None
    mtype = str((msg.raw or {}).get("type") or "text")
    if mtype in _NO_ACTION_TYPES:
        _log.info("whatsapp_channel.bot.no_action", type=mtype[:20])
        return None

    rest = link_code_attempt(msg.body_text) if mtype == "text" else None
    if rest is not None:
        return await redeem(wa_id, rest, str(msg.wa_message_id or "") or None)

    links = await _active_links_for_phone(wa_id)
    if not links:
        return Reply(wa_id, REPLY_UNKNOWN, "unknown")
    # A linked phone (WAC-3). A text is recorded and runs after the 200. A
    # voice note is WAC-5, so any other type gets no action yet.
    body = str(msg.body_text or "").strip()
    wamid = str(msg.wa_message_id or "").strip()
    if mtype != "text" or not body or not wamid:
        _log.info("whatsapp_channel.bot.linked_no_action",
                  phone_hint=wa_id[-4:], type=mtype[:20])
        return None
    return await bot_run.record_inbound(links, wa_id, wamid, body)


async def handle_bot_group(
    sub_payload: dict[str, Any], *, signed: bool,
) -> list[Reply | bot_run.RunRequest]:
    """Act on the bot number's part of a webhook batch. Returns the work for
    after the 200: the fixed replies, and the runs of linked messages.

    *signed* is False when the box has no ``WHATSAPP_APP_SECRET``, so the
    route checked no signature. Then nothing happens, in dev too.
    """
    if not signed:
        _log.warning("whatsapp_channel.bot.refused_no_app_secret")
        return []
    if not flags.assistant_enabled():
        _log.info("whatsapp_channel.bot.dark_dropped")
        return []
    if flags.bot_credentials() is None:
        _log.warning("whatsapp_channel.bot.no_token_dropped")
        return []

    from whatsapp_ingestion.providers.webhook import parse_webhook

    result = parse_webhook(sub_payload)
    for st in result.statuses:
        # The status word and nothing else: no text, no phone. WAC-3 stores
        # the bot's own messages, and a delivery state attaches then.
        _log.info("whatsapp_channel.bot.status", status=str(st.status)[:20],
                  failed=bool(st.error))

    replies: list[Reply | bot_run.RunRequest] = []
    failed = 0
    for msg in result.messages:
        # One message that fails must not take the others with it. A run
        # that this batch already recorded must still start: its redelivery
        # finds the row and records nothing new.
        try:
            reply = await _handle_message(msg)
        except Exception as exc:
            failed += 1
            # The class only: a constraint DETAIL can hold a phone number.
            _log.warning("whatsapp_channel.bot.message_failed",
                         error_type=type(exc).__name__)
            continue
        if reply is not None:
            replies.append(reply)
    if failed:
        raise BotGroupFailed(failed, runs_of(replies))
    return replies


class BotGroupFailed(Exception):
    """Some messages of the bot's group failed (WAC-3).

    The route answers 500, so Meta sends the batch again. ``runs`` holds the
    runs that this batch recorded before or after the failure. The route
    starts them anyway, because their redelivery records nothing new.
    """

    def __init__(self, failed: int, runs: list[bot_run.RunRequest]) -> None:
        super().__init__(f"{failed} bot message(s) failed")
        self.failed = failed
        self.runs = runs


def runs_of(items: list[Any]) -> list[bot_run.RunRequest]:
    """The runs among the work for after the 200."""
    return [item for item in items if isinstance(item, bot_run.RunRequest)]


async def send_replies(items: list[Reply | bot_run.RunRequest]) -> None:
    """The work after the route's 200: start each run, then send each reply.

    A run (WAC-3) starts as a task of its own (:func:`bot_run.start`), so
    this returns as soon as the fixed replies are out, and a slow run never
    holds the response or its connection.

    Builds the Cloud API provider from the platform credentials, with no
    ``wa_accounts`` row (D-WAC-1). A failed send logs Meta's error fields
    only, never the exception text, which can carry a URL or a token.
    """
    for item in items:
        if isinstance(item, bot_run.RunRequest):
            bot_run.start(item)
    replies = [item for item in items if isinstance(item, Reply)]
    if not replies:
        return
    creds = flags.bot_credentials()
    if creds is None:
        _log.warning("whatsapp_channel.bot.no_token_at_send", replies=len(replies))
        return

    from gateway.routes.whatsapp.transport.connect import meta_error_fields
    from whatsapp_ingestion.providers.factory import build_provider

    try:
        provider = build_provider("cloud_api", creds)
    except ValueError as exc:
        _log.warning("whatsapp_channel.bot.provider_refused",
                     error_class=type(exc).__name__)
        return
    for reply in replies:
        try:
            await provider.send_text(reply.to, reply.text)
            _log.info("whatsapp_channel.bot.reply_sent", kind=reply.kind,
                      phone_hint=reply.to[-4:])
        except Exception as exc:
            _log.warning("whatsapp_channel.bot.reply_failed", kind=reply.kind,
                         phone_hint=reply.to[-4:], **meta_error_fields(exc))
