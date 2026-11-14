"""A late approve of a parked card is the member's own consent, once (HRD-22).

Spec: ``project-docs/specs/chat_run_continuity.md`` S2 item 8, and
``chat_failure_modes.md`` row C6 and slice HRD-22 (task 3231). D-HRD-7 is the
rule, and it is PROPOSED. Dark behind ``CHAT_LATE_APPROVE_ORGS`` (empty, off).
Off, nothing here reads or writes Redis, so each card and each message is as
before.

**The failure (production, 2026-11-14).** A member approved a parked card ten
minutes late. The answer started a new run, which planned again, called the
tool again, and drew a new card. One deal move took three approvals.

**Three steps, each in the seam that owns it.**

1. **The offer** (the run). ``orchestrator.pending_ask.park`` calls
   :func:`record_offer` before the row moves to ``parked``. It records what
   the waiting card binds (``ask_tools.waiting_card``): the card text
   (:func:`card_text_of`, the ``whatsapp_acts.card_text`` of the clipped
   title, detail and context) and the act keys (``ask_tools.act_keys``).
   ``key(OFFER_NAMESPACE, member, thread, request id)``, as long as the row.
2. **The grant** (the gateway). ``routes.agent._late_answer_from_row`` calls
   :func:`grant_from_offer` only after ``move_ask`` moved the row to
   ``answered``. Only the answer ``APPROVE`` of a parked ``confirmation`` row
   writes one. It takes the offer once (GETDEL) and writes
   ``key(GRANT_NAMESPACE, member, thread)`` for :data:`GRANT_TTL_S`.
3. **The use** (the card gate). ``ask_tools.request_confirmation`` calls
   :func:`take_grant` before a plain card goes out. It takes the grant once
   (GETDEL), before the tool writes, and says yes only when the org, the
   member, the thread, the card text and the act keys are all equal.

**The cards that take a grant.** A plain confirmation card only: no rows, no
reply form and no held send id (the caller checks these). Each act key must
name a write of ``egress.HELD_CARD_WRITE_TOOLS``, a first-party write that
stays in Metorite (:func:`keys_wire`). So an egress card, an email card, a
card of ticked rows and a WhatsApp run never take one.

**The safe side.** A missing record, a mismatch, a Redis fault, a run with no
verified member and a run with no card channel each show the normal card. The
org, the member and the thread come from the session, the row or the run,
never from request input (R5, R11). Each Redis call goes through the tenant
wrapper (``acb_common.tenant_redis``).

**The key, against the audit.** The audit asked for the request id in the
grant key. The new card has a new request id, so the gate cannot name the old
one, and a SCAN in the card path is slow. So the OFFER key holds the request
id, and the grant record holds it as a value.

Fences (R7): ``tests/unit/test_late_approve_grant.py`` (hermetic) and
``tests/unit/test_late_approve_grant_r8.py`` (R8 and a private real Redis).
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.late_approve")

#: The tenant Redis namespace of an offer: what a parked plain card binds.
OFFER_NAMESPACE = "chat-late-offer"
#: The tenant Redis namespace of a grant: one late approve, for one use.
GRANT_NAMESPACE = "chat-late-approve"
#: How long a grant waits for the run that uses it (30 minutes).
GRANT_TTL_S = 30 * 60
#: The one answer that writes a grant. ``ask_tools.request_confirmation``
#: approves a card with no rows only on this exact word.
APPROVE = "APPROVE"
#: The longest wait for one Redis call. A timeout is a fault: no grant.
_OP_TIMEOUT_S = 2.0
#: The version of a stored record. A record of another version reads as none.
_VERSION = 1

#: The module of an agent that the loader imports for ONE run
#: (``acb_skills.loader``: ``_agent_<run id>_agents``). The name changes with
#: each run, so the next run would never draw the same act key. Such a head
#: reads as the run's agent (``agent:<name>``). The thread binds the agent, and
#: the grant binds the thread.
_PER_RUN_MODULE = re.compile(r"\A_agent_[0-9A-Za-z_]+_(?:agents|graph)\Z")


# ── The flag ─────────────────────────────────────────────────────────────────

def late_approve_on(org: object = None) -> bool:
    """True when ``CHAT_LATE_APPROVE_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant. A comma list of org ids, or ``*`` for
    each one. Empty means off, the default. It reads the settings on each
    call, so a flag reload (``docs/flag_reload.md``) applies a change with no
    restart. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_late_approve_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return str(org or "").strip().lower() in entries


# ── The pure parts ───────────────────────────────────────────────────────────

def card_text_of(title: str, detail: str, context: str) -> str:
    """The one text of a card, as the WhatsApp equal-card rule builds it
    (``whatsapp_acts.card_text``). The offer and the use both call it with the
    values that ``request_confirmation`` clipped, so two equal cards give one
    text."""
    from acb_skills.whatsapp_acts import card_text

    return card_text(title, detail, context)


def _granted_tools() -> frozenset[str]:
    """The tools whose card may take a grant: the first-party writes that stay
    in Metorite (``egress.HELD_CARD_WRITE_TOOLS``). A fault gives none."""
    try:
        from acb_skills.egress import HELD_CARD_WRITE_TOOLS
    except Exception:
        return frozenset()
    return frozenset(HELD_CARD_WRITE_TOOLS)


def _stable_key(act: Any, agent: str) -> tuple[Any, ...] | None:
    """*act* with a per-run module read as the run's agent, or None."""
    if not isinstance(act, tuple) or len(act) != 3:
        return None
    head, name, targets = act
    if isinstance(head, str) and _PER_RUN_MODULE.match(head):
        if not agent:
            return None
        head = f"agent:{agent}"
    return (head, name, targets)


def keys_wire(keys: Iterable[Any] | None, agent: str = "") -> list[str] | None:
    """The act keys of a card as sorted JSON strings, or None when no grant
    may take them. None for no key, a key of a tool that is not a first-party
    write that stays in Metorite (:func:`_granted_tools`), and a key that does
    not read. *agent* is the run's agent, for a per-run module."""
    if not keys:
        return None
    allowed = _granted_tools()
    out: list[str] = []
    try:
        for act in keys:
            stable = _stable_key(act, agent)
            if stable is None or stable[1] not in allowed:
                return None
            out.append(json.dumps(stable, ensure_ascii=False, separators=(",", ":"),
                                  sort_keys=True))
    except (TypeError, ValueError):
        return None
    return sorted(out) or None


def _record(raw: Any) -> dict[str, Any] | None:
    """A stored offer or grant, checked, or None. A record whose keys name a
    tool that may not take a grant reads as none."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or value.get("v") != _VERSION:
        return None
    rid, text, keys = value.get("rid"), value.get("text"), value.get("keys")
    if not (isinstance(rid, str) and rid and isinstance(text, str)
            and isinstance(keys, list) and keys and all(isinstance(k, str) for k in keys)):
        return None
    allowed = _granted_tools()
    for k in keys:
        try:
            act = json.loads(k)
        except (TypeError, ValueError, RecursionError):
            return None
        if not (isinstance(act, list) and len(act) == 3 and act[1] in allowed):
            return None
    return {"rid": rid, "text": text, "keys": keys}


def _encode(rid: str, text: str, keys: list[str]) -> str:
    return json.dumps({"v": _VERSION, "rid": rid, "text": text, "keys": keys},
                      ensure_ascii=False, separators=(",", ":"))


# ── The run's own facts (never request input) ────────────────────────────────

def _redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


def _run_org() -> str:
    """The tenant of this run, from the run boundary (``executor``)."""
    try:
        from orchestrator.executor import _current_run_org

        return str(_current_run_org() or "")
    except Exception:
        return ""


def _run_member() -> str:
    """The VERIFIED member of this run, lower case, or ``""`` (H-73)."""
    try:
        from acb_skills.write_artifact import _verified_member

        return _verified_member()
    except Exception:
        return ""


def _run_agent() -> str:
    """The agent of this run, from the run binding, or ``""``."""
    try:
        from acb_common import get_run_context

        return str((get_run_context() or {}).get("agent") or "")
    except Exception:
        return ""


def _run_thread() -> str:
    """The thread of this run (``executor.resolve_relay_thread_id``)."""
    try:
        from orchestrator.executor import resolve_relay_thread_id

        return str(resolve_relay_thread_id() or "")
    except Exception:
        return ""


def _card_channel() -> bool:
    """True when this run shows a card to its member on the web chat. A run
    with no channel and a WhatsApp run take no grant."""
    try:
        from acb_skills.ask_tools import _whatsapp_run, confirmation_channel_open

        return confirmation_channel_open() and not _whatsapp_run()
    except Exception:
        return False


def _short(value: str) -> str:
    return (value or "")[:12]


# ── Step 1: the offer (the run, at the park) ─────────────────────────────────

async def record_offer(org: str, thread_id: str, request_id: str, ttl_s: int) -> bool:
    """Record what the plain card *request_id* binds, before it parks.

    ``orchestrator.pending_ask.park`` calls it in the card's own wait, so
    ``ask_tools.waiting_card`` reads the card of this frame. *org* and
    *thread_id* are the run's own, and the member is the verified member of
    the run. True when written. It never raises, and a fault records nothing.
    """
    try:
        if not org or not thread_id or not late_approve_on(org):
            return False
        from acb_skills.ask_tools import waiting_card

        card = waiting_card(request_id)
        member = _run_member()
        if card is None or "@" not in member:
            return False
        title, detail, context, keys = card
        wire = keys_wire(keys, _run_agent())
        if wire is None:
            return False
        value = _encode(request_id, card_text_of(title, detail, context), wire)
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            await asyncio.wait_for(
                _redis().set(key(OFFER_NAMESPACE, member, thread_id, request_id), value,
                             ex=max(60, int(ttl_s))),
                _OP_TIMEOUT_S)
    except Exception as exc:
        _log.warning("late_approve.offer_failed", request_id=_short(request_id),
                     error_type=type(exc).__name__)
        return False
    _log.info("late_approve.offer_recorded", request_id=_short(request_id),
              thread_id=_short(thread_id))
    return True


# ── Step 2: the grant (the gateway, after the row moved) ─────────────────────

async def grant_from_offer(org: str, member: str, row: Mapping[str, Any], answer: str) -> bool:
    """Turn the offer of a parked plain card into a grant of one use.

    The ONE writer of a grant. ``gateway.routes.agent._late_answer_from_row``
    calls it only after ``move_ask`` moved *row* to ``answered``. *org* and
    *member* are the session's. The thread and the request id come from
    *row*, which the route read under the session's tenant. Only the answer
    ``APPROVE`` of a ``confirmation`` row that was ``parked`` writes. A decline
    and a ``REJECT`` never reach a write. True when written. It never raises.
    """
    try:
        if not org or not late_approve_on(org):
            return False
        if str(row.get("kind") or "") != "confirmation" or str(row.get("state") or "") != "parked":
            return False
        if str(answer or "").strip().upper() != APPROVE:
            return False
        payload = row.get("payload")
        if isinstance(payload, Mapping) and (payload.get("rows") is not None
                                             or payload.get("reply") is not None):
            return False
        who = str(member or "").strip().lower()
        thread = str(row.get("thread_id") or "")
        rid = str(row.get("request_id") or "")
        if "@" not in who or not thread or not rid:
            return False
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            redis = _redis()
            offer = _record(await asyncio.wait_for(
                redis.getdel(key(OFFER_NAMESPACE, who, thread, rid)), _OP_TIMEOUT_S))
            if offer is None or offer["rid"] != rid:
                _log.info("late_approve.no_offer", request_id=_short(rid))
                return False
            await asyncio.wait_for(
                redis.set(key(GRANT_NAMESPACE, who, thread),
                          _encode(rid, offer["text"], offer["keys"]), ex=GRANT_TTL_S),
                _OP_TIMEOUT_S)
    except Exception as exc:
        _log.warning("late_approve.grant_failed", error_type=type(exc).__name__)
        return False
    _log.info("late_approve.grant_written", request_id=_short(rid), thread_id=_short(thread))
    return True


# ── Step 3: the use (the card gate, before the card goes out) ────────────────

async def take_grant(title: str, detail: str, context: str, keys: Any) -> bool:
    """True when this run may skip the card: the member of this run approved
    this very card late, in this thread, and the grant is still unused.

    ``ask_tools.request_confirmation`` calls it for a plain card only, with
    its clipped values and the act keys of the call (``ask_tools._CARD_CALL``).
    The org, the member and the thread are the run's own. The grant is read
    once with GETDEL, before the tool writes, so a crash after the check never
    lets a second use through. A grant that this card does not match is gone
    too. A fault reads False, and the normal card shows. Never raises.
    """
    try:
        org = _run_org()
        if not org or not late_approve_on(org):
            return False
        member, thread = _run_member(), _run_thread()
        if "@" not in member or not thread:
            return False
        wire = keys_wire(keys, _run_agent())
        if wire is None or not _card_channel():
            return False
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            grant = _record(await asyncio.wait_for(
                _redis().getdel(key(GRANT_NAMESPACE, member, thread)), _OP_TIMEOUT_S))
        if grant is None:
            return False
        if grant["keys"] != wire or grant["text"] != card_text_of(title, detail, context):
            _log.info("late_approve.grant_not_this_card", request_id=_short(grant["rid"]))
            return False
    except Exception as exc:
        _log.warning("late_approve.use_failed", error_type=type(exc).__name__)
        return False
    _log.info("late_approve.grant_used", request_id=_short(grant["rid"]),
              thread_id=_short(thread))
    return True


__all__ = [
    "APPROVE",
    "GRANT_NAMESPACE",
    "GRANT_TTL_S",
    "OFFER_NAMESPACE",
    "card_text_of",
    "grant_from_offer",
    "keys_wire",
    "late_approve_on",
    "record_offer",
    "take_grant",
]
