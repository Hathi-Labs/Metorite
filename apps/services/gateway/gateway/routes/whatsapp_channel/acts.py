"""A confirmed write from WhatsApp: the stored act and its Confirm (WS-47 WAC-4).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §14.

``acb_skills.whatsapp_acts`` parks an allowed act inside a run. This module
is the gateway half: it stores the act in ``whatsapp_pending_acts``, and it
settles the act with the member's NEXT message, before any run.

* **Park** (:func:`park`). One transaction, bound to the link's org: void the
  phone's earlier pending act in the thread, then insert the new one. The row
  names the link row that was current.
* **Offer** (:func:`offered`). ``bot_run._send`` calls it only after Meta
  took the part that carries the Confirm and Cancel buttons, with that
  part's message id. The expiry of 10 minutes starts then. An act that was
  never offered cannot be confirmed: its summary and its buttons may never
  have reached the phone (a refused send, a partial send, an unclear send,
  or a resend of the stored text).
* **Settle** (:func:`settle`). ``bot_run`` calls it after its checks (the
  switch, the link, an active member, ``feature:chat``, a fresh
  ``resolve_identity`` for the link's org) and before the quick path and the
  model. With no pending act for this ``wa_id`` in this thread it returns
  None, and the message runs as before.

  - An act that was never offered closes ``void``, and the message runs as
    a normal message.
  - A Confirm or a Cancel must answer THIS card. A tap names the message it
    answers (Meta's ``context.id``, ``whatsapp_bot_messages.context_wamid``),
    and it must be the act's ``offered_wamid``. A typed one must arrive after
    ``offered_at``. Else nothing is written, the act stays, and the member
    hears that the button was for an earlier request.
  - "Confirm" (any case, trimmed): an expired act closes ``expired``. An act
    whose link row is no longer the phone's current link closes ``void``.
    Else the claim (``pending`` to ``running``, a row lock) lets ONE message
    run the stored call. A second Confirm, or a redelivery, finds no
    pending act and writes nothing.
  - "Cancel": the act closes ``cancelled``.
  - Any other message: the act closes ``void``, and the message runs as a
    normal message.

* **The run of the stored call** (:func:`_run`). No model runs. The same tool
  gets the same arguments, as the link's member (``_bind_memory_user_id``),
  inside ``refuse_cards`` and ``whatsapp_acts.confirming``. So the write path
  is the web's own path, and the tool's card gate answers "yes" only when the
  card it builds again equals the stored card text. A card that changed (a
  rename, a status edit) gets "no": nothing is written, and the act closes
  ``void``.

Every read and write runs in ``tenant_session()`` with the org that
``bot_run`` bound from the link row (R5, R11). Nothing here reads an org or
an identity from the message.

Fences: ``tests/unit/test_wac_writes.py`` (database-free) and
``tests/unit/test_wac_writes_r8.py`` (R8, FORCE RLS as a non-privileged role).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from acb_common import get_logger
from gateway.db import tenant_session
from sqlalchemy import text

if TYPE_CHECKING:
    from acb_skills.whatsapp_acts import ParkedAct
    from gateway.routes.whatsapp_channel.bot_run import RunRequest

_log = get_logger(__name__)

#: An act lives this long after its buttons go out (§14.5).
ACT_TTL_S = 10 * 60
#: The stored call gets this long. Past it, the write may or may not be done.
ACT_TIMEOUT_S = 60

# ── The fixed replies ───────────────────────────────────────────────────────

REPLY_CANCELLED = "Cancelled. Nothing was changed."
REPLY_EXPIRED = ("That request expired after 10 minutes, so nothing was changed. "
                 "Ask again if you still want it.")
REPLY_CHANGED = ("Something changed since I asked, so nothing was changed. Ask "
                 "again, and check the new summary.")
REPLY_LINK = ("Your WhatsApp link changed since I asked, so nothing was changed. "
              "Ask again.")
REPLY_FAILED = "Metorite could not make that change, so nothing was changed."
REPLY_UNSURE = ("Metorite could not confirm that the change was made. Check in "
                "the web app before you ask again.")
REPLY_STALE = ("That Confirm does not match the latest request, so nothing was "
               "changed. Tap Confirm or Cancel on the latest summary.")
REPLY_NOT_HELD = ("Metorite could not hold that change for your Confirm, so "
                  "nothing was changed. Make it in the Metorite web app.")

# ── The SQL ─────────────────────────────────────────────────────────────────

#: The phone's current link row in the bound org.
_LINK_SQL = """
SELECT id::text FROM whatsapp_member_links
 WHERE wa_id = :wa
   AND lower(member_email) = lower(:email)
   AND status = 'active'
   AND is_current
 LIMIT 1
"""

#: The park voids the phone's earlier pending act in the thread first.
_VOID_EARLIER_SQL = """
UPDATE whatsapp_pending_acts
   SET state = 'void', close_code = 'replaced', updated_at = now()
 WHERE wa_id = :wa AND chat_session_id = :sid AND state = 'pending'
"""

_PARK_SQL = """
INSERT INTO whatsapp_pending_acts
       (organization_id, member_email, wa_id, chat_session_id, link_id,
        tool_name, arguments, card_text, expires_at)
VALUES (CAST(:org AS uuid), :email, :wa, :sid, CAST(:link AS uuid),
        :tool, CAST(:args AS jsonb), :card,
        now() + make_interval(secs => CAST(:ttl AS double precision)))
RETURNING id::text
"""

#: The phone's pending act in this thread, newest first, with what this
#: message answers: the message a tap names, and whether it arrived after
#: the buttons went out.
_LIVE_SQL = """
SELECT a.id::text AS id, a.member_email, a.link_id::text AS link_id,
       a.tool_name, a.arguments, a.card_text, a.expires_at > now() AS live,
       a.offered_at IS NOT NULL AS offered, a.offered_wamid,
       m.context_wamid AS tap_of,
       COALESCE(m.received_at > a.offered_at, false) AS after_offer
  FROM whatsapp_pending_acts a
  LEFT JOIN whatsapp_bot_messages m ON m.id = CAST(:mid AS uuid)
 WHERE a.wa_id = :wa AND a.chat_session_id = :sid AND a.state = 'pending'
 ORDER BY a.created_at DESC
 LIMIT 1
"""

#: The act is offered: Meta took the part with its buttons. The expiry
#: starts now. Only once, and only while the act waits.
_OFFERED_SQL = """
UPDATE whatsapp_pending_acts
   SET offered_at = now(), offered_wamid = :out,
       expires_at = now() + make_interval(secs => CAST(:ttl AS double precision)),
       updated_at = now()
 WHERE id = CAST(:id AS uuid) AND state = 'pending' AND offered_at IS NULL
RETURNING id
"""

#: The claim: only one UPDATE moves the row from `pending`, under its row
#: lock, so the stored call runs once.
_CLAIM_SQL = """
UPDATE whatsapp_pending_acts
   SET state = 'running', updated_at = now()
 WHERE id = CAST(:id AS uuid) AND state = 'pending' AND expires_at > now()
   AND offered_at IS NOT NULL
RETURNING id
"""

_CLOSE_SQL = """
UPDATE whatsapp_pending_acts
   SET state = :to, close_code = :code, updated_at = now()
 WHERE id = CAST(:id AS uuid) AND state = :frm
RETURNING id
"""


async def _current_link_id(wa_id: str, email: str) -> str | None:
    """The id of the phone's current link row in the bound org, or None."""
    async with tenant_session() as db:
        row = (await db.execute(
            text(_LINK_SQL), {"wa": wa_id, "email": email},
        )).first()
    return str(row[0]) if row is not None else None


async def _store(req: RunRequest, act: ParkedAct, link_id: str) -> str:
    async with tenant_session() as db:
        await db.execute(text(_VOID_EARLIER_SQL),
                         {"wa": req.wa_id, "sid": req.chat_session_id})
        return str((await db.execute(text(_PARK_SQL), {
            "org": req.organization_id, "email": req.member_email,
            "wa": req.wa_id, "sid": req.chat_session_id, "link": link_id,
            "tool": act.tool, "args": json.dumps(act.arguments, ensure_ascii=False),
            "card": act.card, "ttl": ACT_TTL_S,
        })).scalar_one())


async def _live_act(req: RunRequest) -> dict[str, Any] | None:
    async with tenant_session() as db:
        row = (await db.execute(text(_LIVE_SQL), {
            "wa": req.wa_id, "sid": req.chat_session_id, "mid": req.message_id,
        })).mappings().first()
    return dict(row) if row is not None else None


async def _claim(act_id: str) -> bool:
    async with tenant_session() as db:
        return (await db.execute(text(_CLAIM_SQL), {"id": act_id})).first() is not None


async def _mark_offered(act_id: str, out_wamid: str) -> bool:
    async with tenant_session() as db:
        return (await db.execute(text(_OFFERED_SQL), {
            "id": act_id, "out": out_wamid[:200], "ttl": ACT_TTL_S,
        })).first() is not None


async def _close(act_id: str, frm: str, to: str, code: str | None) -> bool:
    async with tenant_session() as db:
        return (await db.execute(text(_CLOSE_SQL), {
            "id": act_id, "frm": frm, "to": to, "code": code,
        })).first() is not None


# ── Park ────────────────────────────────────────────────────────────────────


async def park(req: RunRequest, act: ParkedAct) -> str | None:
    """Store *act* for the member's Confirm. Returns its id, or None.

    None when the phone has no current link row in the bound org. Then no
    act is stored, and ``bot_run`` offers no buttons.
    """
    link_id = await _current_link_id(req.wa_id, req.member_email)
    if link_id is None:
        _log.info("whatsapp_channel.act.no_link", message_id=req.message_id)
        return None
    act_id = await _store(req, act, link_id)
    _log.info("whatsapp_channel.act.parked", act_id=act_id, tool=act.tool,
              organization_id=req.organization_id, message_id=req.message_id)
    return act_id


async def offered(act_id: str, out_wamid: str) -> None:
    """Mark the act offered: Meta took its buttons as *out_wamid*. Never raises.

    A failed write leaves the act unoffered, so nothing can confirm it, and
    the member's next message voids it. That is the safe side.
    """
    try:
        done = await _mark_offered(act_id, out_wamid)
    except Exception as exc:  # the record names the class only
        _log.warning("whatsapp_channel.act.offer_failed", act_id=act_id,
                     error_type=type(exc).__name__)
        return
    _log.info("whatsapp_channel.act.offered", act_id=act_id, marked=done)


# ── Settle ──────────────────────────────────────────────────────────────────


async def settle(req: RunRequest, message: str) -> str | None:
    """The reply to a Confirm or a Cancel of a pending act, or None.

    None: no pending act, or any other message, which voids the act and
    runs as a normal message (§14.3 item 4).
    """
    from acb_skills.whatsapp_acts import CANCEL, CONFIRM, is_word

    act = await _live_act(req)
    if act is None:
        return None
    act_id = str(act["id"])
    confirm, cancel = is_word(message, CONFIRM), is_word(message, CANCEL)
    if not act["offered"] or not (confirm or cancel):
        # The member never saw this card, or moved on: the act ends, and the
        # message runs as a normal message (§14.3 item 4).
        reason = "unoffered" if not act["offered"] else "other"
        await _close(act_id, "pending", "void" if act["live"] else "expired", reason)
        _log.info("whatsapp_channel.act.voided", act_id=act_id, reason=reason)
        return None
    if not _answers_this_card(act):
        _log.info("whatsapp_channel.act.stale_answer", act_id=act_id,
                  tap=bool(act["tap_of"]))
        return REPLY_STALE
    if cancel:
        await _close(act_id, "pending", "cancelled", None)
        _log.info("whatsapp_channel.act.cancelled", act_id=act_id)
        return REPLY_CANCELLED
    if not act["live"]:
        await _close(act_id, "pending", "expired", None)
        _log.info("whatsapp_channel.act.expired", act_id=act_id)
        return REPLY_EXPIRED
    # D-WAC-3, §14.5: the act's link row must still be the phone's current
    # link. A lost link or an org switch gives another row, or none.
    if (str(act["member_email"]).strip().lower() != req.member_email
            or await _current_link_id(req.wa_id, req.member_email) != act["link_id"]):
        await _close(act_id, "pending", "void", "link")
        _log.info("whatsapp_channel.act.voided", act_id=act_id, reason="link")
        return REPLY_LINK
    if not await _claim(act_id):
        # Another message took it, or it expired a moment ago. Nothing more.
        _log.info("whatsapp_channel.act.not_claimed", act_id=act_id)
        return REPLY_EXPIRED
    return await _run(req, act_id, act)


def _answers_this_card(act: dict[str, Any]) -> bool:
    """True when the message answers THIS act's buttons (review, 2026-10-11).

    A tap names the message that it answers, and that must be the act's own
    button message. A typed answer names nothing, so it counts only when it
    arrived after the buttons went out. A tap on an older card's buttons, or a
    Confirm sent while a newer card was still on its way, answers no card.
    """
    tap_of = act.get("tap_of")
    if tap_of:
        return str(tap_of) == str(act.get("offered_wamid") or "")
    return bool(act.get("after_offer"))


def act_function(name: str) -> Callable[..., Awaitable[Any]] | None:
    """The tool that an act of *name* runs, from ``ALLOWED_ACTS`` only."""
    from acb_skills.whatsapp_acts import ALLOWED_ACTS

    module = ALLOWED_ACTS.get(name)
    if module is None:
        return None
    import importlib

    fn = getattr(importlib.import_module(module), name, None)
    return fn if callable(fn) else None


def _arguments(raw: Any) -> dict[str, Any] | None:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else None


def _member_text(tool_text: str) -> str:
    """The tool's own text, for the member: without its "Next:" lines, which
    speak to the model."""
    lines = [ln for ln in str(tool_text or "").splitlines()
             if not ln.strip().startswith("Next:")]
    return "\n".join(lines).strip()


def _refusal(exc: BaseException) -> str | None:
    """The tool's text for a refusal of the Projects gateway, or None."""
    try:
        from skill_projects.client import GatewayRefusal
        from skill_projects.refusals import refusal_text
    except ImportError:  # pragma: no cover — the skill ships with the gateway
        return None
    return refusal_text(exc) if isinstance(exc, GatewayRefusal) else None


async def _run(req: RunRequest, act_id: str, act: dict[str, Any]) -> str:
    """Run the claimed act once, as the link's member, and close it."""
    from acb_skills.ask_tools import refuse_cards
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
    from acb_skills.whatsapp_acts import confirming

    tool = str(act["tool_name"])
    fn = act_function(tool)
    arguments = _arguments(act["arguments"])
    if fn is None or arguments is None:
        await _close(act_id, "running", "failed", "unknown")
        _log.warning("whatsapp_channel.act.failed", act_id=act_id, code="unknown")
        return REPLY_FAILED

    out: str | None = None
    error: BaseException | None = None
    # The member the tools act as: the link's member, bound for this call
    # only (H-73). The "yes" answers one card and is reset on the way out.
    binding = _bind_memory_user_id(req.member_email)
    try:
        with refuse_cards(), confirming(str(act["card_text"])) as state:
            try:
                out = str(await asyncio.wait_for(fn(**arguments), timeout=ACT_TIMEOUT_S))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the record names the class only
                error = exc
    finally:
        _unbind_memory_user_id(binding)

    if state.used and not state.matched:
        await _close(act_id, "running", "void", "changed")
        _log.info("whatsapp_channel.act.voided", act_id=act_id, reason="changed")
        return REPLY_CHANGED
    if error is not None:
        refused = _refusal(error)
        sure = refused is not None or not state.used
        await _close(act_id, "running", "failed", "refused" if refused else "error")
        _log.warning("whatsapp_channel.act.failed", act_id=act_id, tool=tool,
                     error_type=type(error).__name__, after_yes=state.used)
        if refused is not None:
            return _member_text(refused) or REPLY_FAILED
        return REPLY_FAILED if sure else REPLY_UNSURE
    if not state.used:
        # The tool answered before its card: a refusal in its own words.
        await _close(act_id, "running", "failed", "no_card")
        _log.info("whatsapp_channel.act.failed", act_id=act_id, tool=tool, code="no_card")
        return _member_text(out or "") or REPLY_FAILED
    await _close(act_id, "running", "done", None)
    _log.info("whatsapp_channel.act.done", act_id=act_id, tool=tool,
              organization_id=req.organization_id)
    return _member_text(out or "") or REPLY_FAILED
