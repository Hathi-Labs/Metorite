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

* **WS-63 PZ-6b, a profile write** (``whatsapp_acts.PROFILE_ACTS``,
  D-PZ-20). A fact with an address, a sensitive fact, a replace and an
  alias park and settle exactly as a WAC-4 act. The stored call runs inside
  ``profile_tools.whatsapp_confirm_scope`` (:func:`_act_scope`), and the
  member reads the tool's text with no machine line.

* **WAC-P4, one egress call after an untrusted answer** (spec §17.3). Its
  act name starts with ``whatsapp_acts.EGRESS_ACT_PREFIX``. Its Confirm
  passes every check above, then :func:`settle` gives :class:`Approved` and
  runs no tool. ``bot_run`` runs the model again inside
  ``whatsapp_acts.approving``, so that ONE call, with those arguments, gets
  the yes, and then calls :func:`finish`.

* **H-391, an email act approved with a reply code** (spec §18). The row
  holds ``confirm_code_hash``, the hash of a code of 4 symbols
  (:func:`park_code`). The summary ends with the code, and the act lives 15
  minutes after its last part went out. :func:`_settle_code` reads only this
  phone's act in this thread. "YES <code>" with the right code, a live act
  and the current link row gives :class:`Approved` of kind ``email``, with
  the lines that the member left out or added. ``bot_run`` runs the model
  inside ``whatsapp_acts.approving_email``. A wrong code, no code, a quoted
  reply and a reply that is not plain write nothing and leave the act.

Every read and write runs in ``tenant_session()`` with the org that
``bot_run`` bound from the link row (R5, R11). Nothing here reads an org or
an identity from the message.

Fences: ``tests/unit/test_wac_writes.py`` (database-free) and
``tests/unit/test_wac_writes_r8.py`` (R8, FORCE RLS as a non-privileged role).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from acb_common import get_logger
from acb_skills.whatsapp_acts import egress_tool, is_egress_act
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

# ── H-391: the replies of an email act that a code approves (spec §18) ──────
#
# Each one but the expiry and the link leaves the act as it is, so the member
# can still type the right reply while the code lives.

#: An email act lives this long after the last part of its summary went out.
CODE_TTL_S = 15 * 60

REPLY_CODE_EXPIRED = ("That code expired after 15 minutes, so nothing was sent or "
                      "changed. Ask again if you still want it.")
REPLY_WRONG_CODE = ("That code does not match the latest request, so nothing was "
                    "sent or changed. Type YES and the code of the latest summary.")
REPLY_NO_CODE = ("Nothing was sent or changed. Type YES and the code of the "
                 "summary, for example YES 7K3Q, or NO to cancel.")
REPLY_NOT_PLAIN = ("That message is not a plain reply to the code, so nothing was "
                   "sent or changed. Type only YES and the code, with WITH or "
                   "WITHOUT and numbers if you want them, or NO.")
REPLY_QUOTED = ("A reply that quotes a message cannot approve a request, so "
                "nothing was sent or changed. Type YES and the code as a new "
                "message.")
REPLY_NO_LIST = ("This request has no numbered list, so WITH and WITHOUT do not "
                 "apply, and nothing was sent or changed. Reply YES and the code "
                 "to approve it as shown, or NO, then ask for the change.")
REPLY_BAD_NUMBER = ("A number in your reply is not a line of the summary, or is "
                    "in both WITH and WITHOUT, so nothing was sent or changed. "
                    "Check the numbers and reply again.")
REPLY_NOBODY = ("Your reply leaves out every To line, so nothing was sent. Keep "
                "one To line, or reply NO.")
#: The wrong codes that one act takes. The last one closes it.
MAX_WRONG_CODES = 5
REPLY_TOO_MANY_WRONG = ("That was the fifth wrong code, so this request is closed, "
                        "and nothing was sent or changed. Ask again if you still "
                        "want it.")
REPLY_NO_ACT = ("No request waits for a code from this phone, so nothing was "
                "sent or changed. The code works only from the phone that got "
                "the summary.")

#: The reply of each problem of ``whatsapp_acts.choice_problem``.
_CHOICE_REPLIES = {
    "no_list": REPLY_NO_LIST,
    "bad_number": REPLY_BAD_NUMBER,
    "nobody": REPLY_NOBODY,
}

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
        tool_name, arguments, card_text, confirm_code_hash, expires_at)
VALUES (CAST(:org AS uuid), :email, :wa, :sid, CAST(:link AS uuid),
        :tool, CAST(:args AS jsonb), :card, :code_hash,
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
       a.confirm_code_hash,
       m.context_wamid AS tap_of,
       COALESCE(m.received_at > a.offered_at, false) AS after_offer
  FROM whatsapp_pending_acts a
  LEFT JOIN whatsapp_bot_messages m ON m.id = CAST(:mid AS uuid)
 WHERE a.wa_id = :wa AND a.chat_session_id = :sid AND a.state = 'pending'
 ORDER BY a.created_at DESC
 LIMIT 1
"""

#: The act is offered: Meta took the part with its buttons (or, for an email
#: act, the last part of its summary, which holds the code). The expiry
#: starts now. Only once, and only while the act waits.
_OFFERED_SQL = """
UPDATE whatsapp_pending_acts
   SET offered_at = now(), offered_wamid = :out,
       expires_at = now() + make_interval(secs =>
           CASE WHEN confirm_code_hash IS NULL
                THEN CAST(:ttl AS double precision)
                ELSE CAST(:code_ttl AS double precision) END),
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

#: One more wrong code for a waiting act. It returns the new count.
_WRONG_CODE_SQL = """
UPDATE whatsapp_pending_acts
   SET wrong_codes = COALESCE(wrong_codes, 0) + 1, updated_at = now()
 WHERE id = CAST(:id AS uuid) AND state = 'pending'
RETURNING wrong_codes
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


async def _store(req: RunRequest, act: ParkedAct, link_id: str,
                 code_hash: str | None = None) -> str:
    async with tenant_session() as db:
        await db.execute(text(_VOID_EARLIER_SQL),
                         {"wa": req.wa_id, "sid": req.chat_session_id})
        return str((await db.execute(text(_PARK_SQL), {
            "org": req.organization_id, "email": req.member_email,
            "wa": req.wa_id, "sid": req.chat_session_id, "link": link_id,
            "tool": act.tool, "args": json.dumps(act.arguments, ensure_ascii=False),
            "card": act.card, "code_hash": code_hash,
            "ttl": CODE_TTL_S if code_hash else ACT_TTL_S,
        })).scalar_one())


async def _live_act(req: RunRequest) -> dict[str, Any] | None:
    async with tenant_session() as db:
        row = (await db.execute(text(_LIVE_SQL), {
            "wa": req.wa_id, "sid": req.chat_session_id, "mid": req.message_id,
        })).mappings().first()
    return dict(row) if row is not None else None


async def has_pending(req: RunRequest) -> bool:
    """True when the phone holds a pending act in this thread (WS-53 CRM-Q33
    fix round 1): the act's Confirm owns the next message, so no CRM pending
    ask settles on it. A failed read reads as pending, the safe side."""
    try:
        return await _live_act(req) is not None
    except Exception as exc:  # the record names the class only
        _log.warning("whatsapp_channel.act.pending_read_failed",
                     error_type=type(exc).__name__)
        return True


async def _claim(act_id: str) -> bool:
    async with tenant_session() as db:
        return (await db.execute(text(_CLAIM_SQL), {"id": act_id})).first() is not None


async def _mark_offered(act_id: str, out_wamid: str) -> bool:
    async with tenant_session() as db:
        return (await db.execute(text(_OFFERED_SQL), {
            "id": act_id, "out": out_wamid[:200], "ttl": ACT_TTL_S,
            "code_ttl": CODE_TTL_S,
        })).first() is not None


async def _wrong_code(act_id: str) -> int:
    """Count one wrong code of the act. Returns the count, or the cap when no
    waiting row took it, so a lost count never adds a try."""
    async with tenant_session() as db:
        row = (await db.execute(text(_WRONG_CODE_SQL), {"id": act_id})).first()
    return int(row[0]) if row is not None else MAX_WRONG_CODES


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


def new_code() -> str:
    """A fresh reply code: 4 symbols of the alphabet, from ``secrets``."""
    from acb_skills.whatsapp_acts import CODE_ALPHABET, CODE_LENGTH

    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


#: The domain of the code hash, so the key signs nothing else with it.
_CODE_DOMAIN = b"whatsapp-act-code:"


def code_hash(code: str) -> str:
    """The HMAC-SHA256 of *code* in upper case, in hex. The only form stored.

    The key is ``gateway_session_secret``, read through the settings seam.
    A plain hash of 4 symbols is reversed by a table of 923 521 rows, so the
    record alone never gives the code (review P3). A rotated key ends each
    live code, which lives 15 minutes at most."""
    from acb_common import get_settings

    key = str(get_settings().gateway_session_secret or "").encode("utf-8")
    msg = _CODE_DOMAIN + str(code).strip().upper().encode("ascii", "replace")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _code_matches(stored: Any, code: str) -> bool:
    return bool(stored) and bool(code) and hmac.compare_digest(
        str(stored), code_hash(code))


async def park_code(req: RunRequest, act: ParkedAct) -> tuple[str, str] | None:
    """Store an email act for the member's reply code (H-391). Returns its id
    and the code, or None when the phone has no current link row.

    The row holds the hash of the code, never the code. ``bot_run`` puts the
    code in the summary, and the code lives :data:`CODE_TTL_S` after the
    last part of the summary went out (:func:`offered`)."""
    link_id = await _current_link_id(req.wa_id, req.member_email)
    if link_id is None:
        _log.info("whatsapp_channel.act.no_link", message_id=req.message_id)
        return None
    code = new_code()
    act_id = await _store(req, act, link_id, code_hash(code))
    _log.info("whatsapp_channel.act.parked", act_id=act_id, tool=act.tool, by_code=True,
              organization_id=req.organization_id, message_id=req.message_id)
    return act_id, code


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


async def settle(req: RunRequest, message: str) -> str | Approved | None:
    """The reply to a Confirm or a Cancel of a pending act, or None.

    None: no pending act, or any other message, which voids the act and
    runs as a normal message (§14.3 item 4). :class:`Approved`: the Confirm
    of a WAC-P4 act passed every check, and ``bot_run`` runs the model.
    """
    from acb_skills.whatsapp_acts import CANCEL, CONFIRM, is_word

    act = await _live_act(req)
    if act is None:
        return _no_act_reply(message)
    act_id = str(act["id"])
    if act.get("confirm_code_hash"):
        # H-391: an email act. The member's reply code answers it.
        return await _settle_code(req, act, message)
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
    if is_egress_act(str(act["tool_name"])):
        # WAC-P4: the member said yes to ONE egress call. The model runs
        # again, and only that call, with those arguments, gets the yes.
        arguments = _arguments(act["arguments"])
        if arguments is None:
            await _close(act_id, "running", "failed", "unknown")
            _log.warning("whatsapp_channel.act.failed", act_id=act_id, code="unknown")
            return REPLY_FAILED
        tool = egress_tool(str(act["tool_name"]))
        _log.info("whatsapp_channel.act.approved", act_id=act_id, tool=tool,
                  organization_id=req.organization_id)
        return Approved(act_id=act_id, tool=tool, arguments=arguments,
                        card=str(act["card_text"]))
    return await _run(req, act_id, act)


# ── WAC-P4: the Confirm of one egress call ──────────────────────────────────


@dataclass(frozen=True)
class Approved:
    """A claimed WAC-P4 act: the member said yes to this ONE egress call.

    ``bot_run`` runs the model again inside ``whatsapp_acts.approving`` with
    :attr:`card`, then calls :func:`finish`."""

    act_id: str
    tool: str
    arguments: dict[str, Any]
    card: str
    #: ``egress`` (WAC-P4) or ``email`` (H-391, a code approved the act).
    kind: str = "egress"
    #: The lines that the member left out, and the lines that the member
    #: added, by their number in the summary (H-391). Code checked them.
    without: frozenset[int] = frozenset()
    include: frozenset[int] = frozenset()


#: The model's turn at Confirm. The member's own text is "Confirm", and the
#: model never saw its parked call, so code names the call and its arguments.
APPROVED_TURN = (
    "{message}\n\n[Metorite: the member tapped Confirm for one call. Make this "
    "call once now, with exactly these arguments, then answer the member's "
    "earlier question.]\n{tool} {arguments}"
)

#: The model's turn when a code approved an email act (H-391). The member's
#: own text holds the code and the numbers, and code already applied them,
#: so the turn does not carry that text.
EMAIL_APPROVED_TURN = (
    "[Metorite: the member approved one email change with the code of its "
    "summary. Ask the Email assistant to make this call once now, with "
    "exactly these arguments, and to change none of them. Metorite already "
    "applied the member's choice of people. Then tell the member in one line "
    "what was done.]\n{tool} {arguments}"
)


def approved_message(message: str, approved: Approved) -> str:
    """The model's turn at the Confirm of *approved*."""
    arguments = json.dumps(approved.arguments, ensure_ascii=False, sort_keys=True)
    if approved.kind == "email":
        return EMAIL_APPROVED_TURN.format(tool=approved.tool, arguments=arguments)
    return APPROVED_TURN.format(message=message, tool=approved.tool, arguments=arguments)


# ── H-391: the reply code of an email act ───────────────────────────────────


def _no_act_reply(message: str) -> str | None:
    """:data:`REPLY_NO_ACT` for a "YES <code>" when no act waits for this
    phone in this thread, else None (the message runs as before). A code from
    another phone, or a code after the act closed, lands here."""
    from acb_skills.whatsapp_acts import parse_code_answer

    answer = parse_code_answer(message)
    return REPLY_NO_ACT if answer is not None and answer.yes and answer.code else None


async def _settle_code(req: RunRequest, act: dict[str, Any],
                       message: str) -> str | Approved | None:
    """Settle an email act with the member's reply (spec §18.3).

    Only this phone's act in this thread is read, so a reply from another
    number never reaches it. "NO" cancels. "YES <code>" approves when the
    code matches, the act lives, the link row is still current and each
    WITH or WITHOUT number names a line of the summary. A wrong code, a reply
    with no code, a quoted reply and a reply that is not plain write nothing
    and leave the act. Any other message voids the act and runs as before."""
    from acb_skills.whatsapp_acts import (
        choice_problem,
        code_tokens,
        parse_code_answer,
        people_of_card_text,
    )

    act_id, stored = str(act["id"]), act.get("confirm_code_hash")
    answer = parse_code_answer(message)
    if not act["offered"]:
        await _close(act_id, "pending", "void" if act["live"] else "expired", "unoffered")
        _log.info("whatsapp_channel.act.voided", act_id=act_id, reason="unoffered")
        return None
    if answer is None:
        if any(_code_matches(stored, t) for t in code_tokens(message)):
            # The code in a longer text: a pasted or forwarded summary.
            _log.info("whatsapp_channel.act.code_refused", act_id=act_id, reason="not_plain")
            return REPLY_NOT_PLAIN
        await _close(act_id, "pending", "void" if act["live"] else "expired", "other")
        _log.info("whatsapp_channel.act.voided", act_id=act_id, reason="other")
        return None
    if act.get("tap_of"):
        _log.info("whatsapp_channel.act.code_refused", act_id=act_id, reason="quoted")
        return REPLY_QUOTED
    if not answer.yes:
        await _close(act_id, "pending", "cancelled", None)
        _log.info("whatsapp_channel.act.cancelled", act_id=act_id)
        return REPLY_CANCELLED
    if not answer.code:
        return REPLY_NO_CODE
    if not answer.plain:
        _log.info("whatsapp_channel.act.code_refused", act_id=act_id, reason="not_plain")
        return REPLY_NOT_PLAIN
    if not act["live"]:
        await _close(act_id, "pending", "expired", None)
        _log.info("whatsapp_channel.act.expired", act_id=act_id)
        return REPLY_CODE_EXPIRED
    if not _code_matches(stored, answer.code):
        tries = await _wrong_code(act_id)
        _log.info("whatsapp_channel.act.code_refused", act_id=act_id, reason="wrong_code",
                  tries=tries)
        if tries >= MAX_WRONG_CODES:
            await _close(act_id, "pending", "void", "wrong_codes")
            return REPLY_TOO_MANY_WRONG
        return REPLY_WRONG_CODE
    if (str(act["member_email"]).strip().lower() != req.member_email
            or await _current_link_id(req.wa_id, req.member_email) != act["link_id"]):
        await _close(act_id, "pending", "void", "link")
        _log.info("whatsapp_channel.act.voided", act_id=act_id, reason="link")
        return REPLY_LINK
    problem = choice_problem(people_of_card_text(str(act["card_text"])),
                             answer.without, answer.include)
    if problem is not None:
        _log.info("whatsapp_channel.act.code_refused", act_id=act_id, reason=problem)
        return _CHOICE_REPLIES.get(problem, REPLY_BAD_NUMBER)
    if not await _claim(act_id):
        _log.info("whatsapp_channel.act.not_claimed", act_id=act_id)
        return REPLY_CODE_EXPIRED
    arguments = _arguments(act["arguments"])
    if arguments is None:
        await _close(act_id, "running", "failed", "unknown")
        _log.warning("whatsapp_channel.act.failed", act_id=act_id, code="unknown")
        return REPLY_FAILED
    tool = str(act["tool_name"])
    _log.info("whatsapp_channel.act.approved", act_id=act_id, tool=tool, by_code=True,
              without=len(answer.without), include=len(answer.include),
              organization_id=req.organization_id)
    return Approved(act_id=act_id, tool=tool, arguments=arguments,
                    card=str(act["card_text"]), kind="email",
                    without=answer.without, include=answer.include)


async def finish(approved: Approved, used: bool) -> None:
    """Close the act of *approved* after its run. Never raises.

    ``done`` when the run made the confirmed call. Else ``void`` with
    ``unused``: the model made another call, or none, and nothing ran."""
    to, code = ("done", None) if used else ("void", "unused")
    try:
        await _close(approved.act_id, "running", to, code)
    except Exception as exc:  # the record names the class only
        _log.warning("whatsapp_channel.act.finish_failed", act_id=approved.act_id,
                     error_type=type(exc).__name__)
        return
    _log.info("whatsapp_channel.act.finished", act_id=approved.act_id, used=used)


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
    """The tool that an act of *name* runs, from ``ALLOWED_ACTS`` and
    ``PROFILE_ACTS`` only (``whatsapp_acts.act_module``)."""
    from acb_skills.whatsapp_acts import act_module

    module = act_module(name)
    if module is None:
        return None
    import importlib

    fn = getattr(importlib.import_module(module), name, None)
    return fn if callable(fn) else None


def _arguments(raw: Any) -> dict[str, Any] | None:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else None


def _member_text(tool_text: str, tool: str = "") -> str:
    """The tool's own text, for the member: without its "Next:" lines, which
    speak to the model. A profile act (WS-63 PZ-6b) loses its machine line
    and its words for the model (``profile_tools.member_text``)."""
    from acb_skills.whatsapp_acts import PROFILE_ACTS

    if tool in PROFILE_ACTS:
        from acb_skills.profile_tools import member_text

        return member_text(tool_text)
    lines = [ln for ln in str(tool_text or "").splitlines()
             if not ln.strip().startswith("Next:")]
    return "\n".join(lines).strip()


def _act_scope(tool: str) -> Any:
    """The run context of the stored call of *tool* at Confirm.

    A profile act (WS-63 PZ-6b, D-PZ-20) runs as a WhatsApp write of the
    member's own thread: :func:`settle` runs only with the org's WhatsApp
    writes on and in no room, and the link's member is verified. So it
    opens the profile writes for this one call
    (``profile_tools.whatsapp_confirm_scope``). Every other act runs as
    before."""
    from acb_skills.whatsapp_acts import PROFILE_ACTS

    if tool not in PROFILE_ACTS:
        return contextlib.nullcontext()
    from acb_skills.profile_tools import whatsapp_confirm_scope

    return whatsapp_confirm_scope()


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
        with refuse_cards(), _act_scope(tool), confirming(str(act["card_text"])) as state:
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
            return _member_text(refused, tool) or REPLY_FAILED
        return REPLY_FAILED if sure else REPLY_UNSURE
    if not state.used:
        # The tool answered before its card: a refusal in its own words.
        await _close(act_id, "running", "failed", "no_card")
        _log.info("whatsapp_channel.act.failed", act_id=act_id, tool=tool, code="no_card")
        return _member_text(out or "", tool) or REPLY_FAILED
    await _close(act_id, "running", "done", None)
    _log.info("whatsapp_channel.act.done", act_id=act_id, tool=tool,
              organization_id=req.organization_id)
    return _member_text(out or "", tool) or REPLY_FAILED
