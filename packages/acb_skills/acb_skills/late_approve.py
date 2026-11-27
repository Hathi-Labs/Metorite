"""A late approve of a parked card is the member's own consent, once (HRD-22).

Spec: ``project-docs/specs/chat_run_continuity.md`` S2 item 8, and
``chat_failure_modes.md`` row C6 and slice HRD-22 (task 3231). D-HRD-7 is the
rule, and it is PROPOSED. Dark behind ``CHAT_LATE_APPROVE_ORGS`` (empty, off).
With it and ``CHAT_RESUME_ORGS`` both off, nothing here reads or writes Redis,
so each card and each message is as before.

**The failure (production, 2026-11-14).** A member approved a parked card ten
minutes late. The answer started a new run, which planned again, called the
tool again, and drew a new card. One deal move took three approvals.

**The act.** A card's act is its text (:func:`card_text_of`: the
``whatsapp_acts.card_text`` of the clipped title, detail and context, and the
facts that the tool bound) and its act keys (``ask_tools.act_keys``). Its
DIGEST names the records, so two cards with one act share one record, and a
card with another act never reads, uses or removes it (:func:`act_of`).

**Three steps, each in the seam that owns it.** All keys are in tenant Redis
(``acb_common.tenant_redis``), under the member and the thread:

1. **The offer** (the run). ``orchestrator.pending_ask.park`` calls
   :func:`record_offer` after the row moves to ``parked``, while the card's
   Future still waits. It writes ``key(ASK_NAMESPACE, member, thread, request
   id)`` = the digest, and ``key(OFFER_NAMESPACE, member, thread, digest)`` =
   the act. Both live no longer than the row.
2. **The grant** (the gateway). ``routes.agent._late_answer_from_row`` calls
   :func:`grant_from_offer` only after ``move_ask`` moved the row to
   ``answered``. It takes the digest of the row once. Only the answer
   ``APPROVE`` of a parked ``confirmation`` row then takes the offer and
   writes ``key(GRANT_NAMESPACE, member, thread, digest)`` for
   :data:`GRANT_TTL_S`. Any other answer drops the offer.
3. **The use** (the card gate). ``ask_tools.request_confirmation`` calls
   :func:`take_grant` before a plain card goes out. It takes exactly the grant
   of its own digest (GETDEL), before the tool writes, and says yes only when
   the text and the keys are equal too.

**An act runs once.** Each approve of a plain card, live or by a grant, marks
the act as SPENT on its offer key, and removes its grant (:func:`note_approved`).
So a stale twin card of the same act, parked earlier, never acts on a late
approve after the act ran. A decline, a REJECT and the park race drop the offer
(:func:`drop_offer`). The drop is ONE compare-and-delete that keeps a SPENT
mark (RESUME-2a follow-up, P2-1). The mark is ONE ``SET … GET``, so of two
approves at the same moment one only takes the act. It lives as long as a row
(:func:`spent_ttl_s`). The stored-call yes of RESUME-2a takes the act with
:func:`claim_act`, which fails closed and tells a SPENT act from an unknown
one (P2-3).

**Who writes the mark** (P2-2). A live approve and a claim write the SPENT
mark when ``CHAT_LATE_APPROVE_ORGS`` OR ``CHAT_RESUME_ORGS`` names the org
(:func:`_run_scope` with ``spend=True``). So a live approve while only RESUME
is on still marks the act, and an org that joins the late-approve list later
finds the mark. The offer and the grant stay behind ``CHAT_LATE_APPROVE_ORGS``
alone.

**The cards that take a grant.** A plain confirmation card only: no rows, no
reply form and no held send id (the gate checks these). The card is not cut:
no field lost text to the clip, and the body holds no cut mark. The tool of the
call passes the three tests of ``egress.is_card_write_tool``: the name is on
``HELD_CARD_WRITE_TOOLS``, the annotation says ``card_write``, and the
parameters are the pinned set. So ``bulk_update``, a send, an egress card, an
email card, a card of ticked rows and a WhatsApp run never take one.

**One act, once, across chats and faults** (HRD-22b, task 3294). Three more
rules close the gaps of the second review:

* **Across chats.** The SPENT mark also has a member-level copy
  (:data:`SPENT_NAMESPACE`, keyed by member and digest, no thread). A twin card
  that parked in chat A takes no grant after the member approved its twin in
  chat B (:func:`grant_from_offer` and :func:`take_grant` read it).
* **A fault of Redis.** The SPENT mark is best effort, so :func:`grant_from_offer`
  also asks Postgres (:func:`_twin_approved`, ``pending_ask.twin_approved_since``):
  a confirmation row of the same member, in any thread, with the same title,
  detail and context, answered ``APPROVE`` after this row was asked, means that
  the act ran. It refuses then, and on a fault of the read (fail closed). This
  also closes the case of a mark that expired and a new twin that wrote a fresh
  offer.
* **The race.** :func:`take_grant` takes the grant, then marks the act with ONE
  ``SET … GET`` on the member-level key, which alone decides (two keys that
  each test the other's mark can leave NO writer). A live approve that landed
  between the offer read and the grant write left SPENT there, so the use
  refuses.
* **The RESUME path.** ``routes.agent._resume_from_row`` asks
  :func:`twin_ran` before it admits the run or claims the stored call, because
  :func:`claim_act` reads Redis only. A twin that ran takes the old path (a
  card shows, nothing is written).
* **An APPROVE with edits** (EAC-1, ``APPROVE {…}``) counts as an APPROVE in
  the Postgres read.

**The safe side.** A missing record, a mismatch, a Redis fault, a run with no
verified member and a run with no card channel each show the normal card. The
org, the member and the thread come from the session, the row or the run,
never from request input (R5, R11).

Fences (R7): ``tests/unit/test_late_approve_grant.py`` (hermetic) and
``tests/unit/test_late_approve_grant_r8.py`` (R8 and a private real Redis).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.late_approve")

#: The tenant Redis namespace of an offer: the act of a parked plain card, by
#: its digest. It also holds the SPENT mark of an act that ran.
OFFER_NAMESPACE = "chat-late-offer"
#: The tenant Redis namespace that leads from a parked card's request id to
#: the digest of its act.
ASK_NAMESPACE = "chat-late-ask"
#: The tenant Redis namespace of the member-level SPENT mark (HRD-22b): one
#: key for each member and digest, in no thread, so an act that ran in chat B
#: is seen by a twin card of chat A.
SPENT_NAMESPACE = "chat-late-spent"
#: The tenant Redis namespace of a grant: one late approve, for one use.
GRANT_NAMESPACE = "chat-late-approve"
#: How long a grant waits for the run that uses it (30 minutes). The time
#: starts at the member's tap (D-HRD-7, PROPOSED).
GRANT_TTL_S = 30 * 60
#: The one answer that writes a grant. ``ask_tools.request_confirmation``
#: approves a card with no rows only on this word.
APPROVE = "APPROVE"
#: The longest wait for one Redis call. A timeout is a fault: no grant.
_OP_TIMEOUT_S = 2.0
#: The version of a stored record. A record of another version reads as none.
_VERSION = 1
#: The value of an offer key whose act ran. It reads as no offer.
_SPENT = json.dumps({"v": _VERSION, "spent": True}, separators=(",", ":"))

#: What :func:`claim_act` gives (RESUME-2a follow-up, P2-3). TAKEN: this call
#: took the act, and only then may the tool write. SPENT: another approve took
#: the act already. UNKNOWN: no scope, no act or a Redis fault, so nobody can
#: say if the act ran. Each answer but TAKEN shows the card (fail closed).
CLAIM_TAKEN = "taken"
CLAIM_SPENT = "spent"
CLAIM_UNKNOWN = "unknown"

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


def spent_ttl_s() -> int:
    """How long the SPENT mark of an act stays: at least :data:`GRANT_TTL_S`,
    longer than the park window, and as long as the life of a row
    (``chat_ask_ttl_hours``, 168 h by default).

    So a twin card that was open at the approve and parks later records no
    offer, and the stored call of a twin row that still waits never finds the
    mark gone (RESUME-2a review, P1-A). The cost: when the member repeats an
    identical act in the same thread within that life, its late approve shows
    one more card. That is the safe side."""
    try:
        from acb_common import get_settings

        settings = get_settings()
        park = int(getattr(settings, "chat_ask_park_seconds", 600) or 600)
        hours = int(getattr(settings, "chat_ask_ttl_hours", 168) or 168)
    except Exception:
        park, hours = 600, 168
    return max(GRANT_TTL_S, park + 300, max(1, hours) * 3600)


# ── The pure parts ───────────────────────────────────────────────────────────

def card_text_of(title: str, detail: str, context: str,
                 facts: Mapping[str, Any] | None = None) -> str:
    """The one text of a card, as the WhatsApp equal-card rule builds it
    (``whatsapp_acts.card_text``), with the facts that the tool bound
    (``whatsapp_acts.card_facts``). The offer and the use both call it with
    the values that ``request_confirmation`` clipped."""
    from acb_skills.whatsapp_acts import card_text

    return card_text(title, detail, context, dict(facts) if facts else None)


def _granted_tools() -> frozenset[str]:
    """The names that an act key may carry: the first-party writes that stay
    in Metorite (``egress.HELD_CARD_WRITE_TOOLS``). A fault gives none."""
    try:
        from acb_skills.egress import HELD_CARD_WRITE_TOOLS
    except Exception:
        return frozenset()
    return frozenset(HELD_CARD_WRITE_TOOLS)


def _tool_may_skip(tool: Any) -> bool:
    """True when the REAL tool of the call may skip its card on a grant: the
    three tests of ``egress.is_card_write_tool``. A fault reads False."""
    if tool is None:
        return False
    try:
        from acb_skills.egress import is_card_write_tool

        return bool(is_card_write_tool(tool))
    except Exception:
        return False


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
    may take them. None for no key, a key whose name is not a first-party
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


def digest_of(text: str, keys: list[str]) -> str:
    """The digest of one act: its text and its act keys."""
    body = json.dumps({"keys": keys, "text": text}, ensure_ascii=False,
                      separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Act:
    """The act of a plain card that may take a grant."""

    text: str
    keys: list[str]
    digest: str


def act_of(card: Any, agent: str) -> Act | None:
    """The act of *card* (``ask_tools.PlainCard``), or None when the card may
    not take a grant: a cut card, a tool that fails the three tests, or keys
    that do not read. *agent* is the run's agent."""
    if card is None or bool(getattr(card, "cut", True)):
        return None
    if not _tool_may_skip(getattr(card, "tool", None)):
        return None
    wire = keys_wire(getattr(card, "keys", None), agent)
    if wire is None:
        return None
    text = card_text_of(card.title, card.detail, card.context, card.facts)
    return Act(text, wire, digest_of(text, wire))


def _record(raw: Any) -> dict[str, Any] | None:
    """A stored offer or grant, checked, or None. A SPENT mark, and a record
    whose keys name a tool that may not take a grant, read as none."""
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


def _text(raw: Any) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return raw if isinstance(raw, str) else ""


def _is_spent(raw: Any) -> bool:
    return _text(raw) == _SPENT


def _encode(rid: str, text: str, keys: list[str]) -> str:
    return json.dumps({"v": _VERSION, "rid": rid, "text": text, "keys": keys},
                      ensure_ascii=False, separators=(",", ":"))


def _row_ttl_s(row: Mapping[str, Any], cap_s: int) -> int:
    """The seconds until the row expires, and never more than *cap_s*. A
    record lives no longer than its row."""
    raw = row.get("expires_at")
    try:
        if isinstance(raw, str) and raw:
            at = datetime.fromisoformat(raw)
        elif isinstance(raw, datetime):
            at = raw
        else:
            return int(cap_s)
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return min(int(cap_s), int((at - datetime.now(UTC)).total_seconds()))
    except (TypeError, ValueError):
        return 0


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


async def _call(coro: Any) -> Any:
    return await asyncio.wait_for(coro, _OP_TIMEOUT_S)


async def _spend(redis: Any, member: str, thread: str, digest: str) -> bool:
    """Mark the act *digest* as run: no offer of it, and no grant. True when
    the act was SPENT already, in this thread or (HRD-22b) in any chat of the
    member.

    ONE ``SET … GET`` on the member-level key decides. It has no thread in its
    key, so a twin card of another chat sees it, and of two writers at the
    same moment exactly one reads a value that is not SPENT (RESUME-2a review,
    P1-A). The old value of the offer key does NOT decide: two writers that
    test two keys can each see the other's mark, and then NO writer takes the
    act. The offer key is only overwritten with SPENT (an offer that ran is
    no offer), and the grant is removed. Redis 6.2 or later."""
    from acb_common.tenant_redis import key

    ttl = spent_ttl_s()
    old = await _call(redis.set(key(SPENT_NAMESPACE, member, digest), _SPENT,
                                ex=ttl, get=True))
    await _call(redis.set(key(OFFER_NAMESPACE, member, thread, digest), _SPENT, ex=ttl))
    await _call(redis.delete(key(GRANT_NAMESPACE, member, thread, digest)))
    return _is_spent(old)


async def _twin_approved(org: str, member: str, row: Mapping[str, Any]) -> bool:
    """True when Postgres says that a twin of *row* was approved after *row*
    was asked, or when it cannot say (fail closed). The SPENT mark of Redis
    is best effort, so this read is the second witness (HRD-22b item 2). A
    seam, so a test passes a fake."""
    try:
        import asyncio as _asyncio

        from orchestrator import pending_ask

        return bool(await _asyncio.wait_for(
            _asyncio.to_thread(pending_ask.twin_approved_since, org, member, dict(row)),
            _OP_TIMEOUT_S * 2))
    except Exception as exc:
        _log.warning("late_approve.twin_read_failed", error_type=type(exc).__name__)
        return True


async def twin_ran(org: str, member: str, row: Mapping[str, Any]) -> bool:
    """True when a twin of the parked *row* ran, or when nobody can say
    (fail closed). The one reader that the RESUME path (``routes.agent.
    _resume_from_row``) asks before it claims the stored call, because
    :func:`claim_act` reads Redis only, and a stall of Redis leaves no mark
    (HRD-22b review, P1). Never raises."""
    try:
        return await _twin_approved(org, member, row)
    except Exception:
        return True


# ── Step 1: the offer (the run, at the park) ─────────────────────────────────

async def record_offer(org: str, thread_id: str, request_id: str,
                       row: Mapping[str, Any], cap_s: int) -> bool:
    """Record the act of the plain card *request_id*, which just parked.

    ``orchestrator.pending_ask.park`` calls it in the card's own wait, after
    the move to ``parked``, so ``ask_tools.waiting_card`` reads the card of
    this frame. *org* and *thread_id* are the run's own, and *row* is the
    parked row. The member is the verified member of the run, and it must be
    the member that the row asked. The records live no longer than the row.
    An act that ran (SPENT) gets no new offer. True when written. Never
    raises, and a fault records nothing.
    """
    try:
        if not org or not thread_id or not late_approve_on(org):
            return False
        if str(row.get("kind") or "") != "confirmation":
            return False
        member = _run_member()
        if "@" not in member or member != str(row.get("actor_email") or "").strip().lower():
            return False
        from acb_skills.ask_tools import waiting_card

        act = act_of(waiting_card(request_id), _run_agent())
        ttl = _row_ttl_s(row, cap_s)
        if act is None or ttl < 60:
            return False
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            redis = _redis()
            await _call(redis.set(key(ASK_NAMESPACE, member, thread_id, request_id),
                                  act.digest, ex=ttl))
            # NX: a twin card of the same act shares the offer, and a SPENT
            # mark (the act ran) keeps its place.
            written = await _call(redis.set(
                key(OFFER_NAMESPACE, member, thread_id, act.digest),
                _encode(request_id, act.text, act.keys), ex=ttl, nx=True))
    except Exception as exc:
        _log.warning("late_approve.offer_failed", request_id=_short(request_id),
                     error_type=type(exc).__name__)
        return False
    _log.info("late_approve.offer_recorded", request_id=_short(request_id),
              thread_id=_short(thread_id), new=bool(written))
    return True


async def drop_offer(org: str, member: str, thread_id: str, request_id: str) -> None:
    """Drop the offer of the card *request_id*: its row left ``parked`` with
    no approve (a decline, a REJECT, an answer during the park). A twin card
    of the same act loses the offer too, so it shows its card: the safe side.
    A SPENT mark stays. Off, it does nothing. Never raises.

    The test and the delete are ONE step (``TenantRedis.delete_unless``, a
    fixed Lua script). A GET and then a DELETE let a twin's ``claim_act``
    write SPENT between the two, and the DELETE then removed the mark, so
    the next twin wrote the act again (RESUME-2a follow-up, P2-1)."""
    try:
        who = str(member or "").strip().lower()
        if not org or "@" not in who or not thread_id or not late_approve_on(org):
            return
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            redis = _redis()
            digest = _text(await _call(redis.getdel(
                key(ASK_NAMESPACE, who, thread_id, request_id))))
            if digest:
                await _call(redis.delete_unless(
                    key(OFFER_NAMESPACE, who, thread_id, digest), _SPENT))
    except Exception as exc:
        _log.warning("late_approve.drop_failed", request_id=_short(request_id),
                     error_type=type(exc).__name__)


# ── Step 2: the grant (the gateway, after the row moved) ─────────────────────

async def grant_from_offer(org: str, member: str, row: Mapping[str, Any], answer: str) -> bool:
    """Turn the offer of a parked plain card into a grant of one use.

    The ONE writer of a grant. ``gateway.routes.agent._late_answer_from_row``
    calls it only after ``move_ask`` moved *row* to ``answered``. *org* and
    *member* are the session's. The thread and the request id come from
    *row*, which the route read under the session's tenant. Only the answer
    ``APPROVE`` of a ``confirmation`` row that was ``parked`` writes. Any
    other answer drops the offer. An act that ran (SPENT) writes nothing. True
    when written. It never raises.
    """
    try:
        if not org or not late_approve_on(org):
            return False
        if str(row.get("kind") or "") != "confirmation" or str(row.get("state") or "") != "parked":
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
        if str(answer or "").strip().upper() != APPROVE:
            await drop_offer(org, who, thread, rid)
            return False
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            redis = _redis()
            digest = _text(await _call(redis.getdel(key(ASK_NAMESPACE, who, thread, rid))))
            if not digest:
                _log.info("late_approve.no_offer", request_id=_short(rid))
                return False
            # HRD-22b item 1: the act ran in another chat of this member.
            if _is_spent(await _call(redis.get(key(SPENT_NAMESPACE, who, digest)))):
                _log.info("late_approve.act_spent_elsewhere", request_id=_short(rid))
                return False
            okey = key(OFFER_NAMESPACE, who, thread, digest)
            seen = await _call(redis.get(okey))
            offer = _record(seen)
            if offer is None or digest_of(offer["text"], offer["keys"]) != digest:
                # No offer, or a SPENT mark: the act ran, or it never may.
                _log.info("late_approve.no_offer", request_id=_short(rid),
                          spent=_is_spent(seen))
                return False
            # HRD-22b items 2 and 3: the second witness, in Postgres.
            if await _twin_approved(org, who, row):
                _log.info("late_approve.twin_approved", request_id=_short(rid))
                return False
            taken = await _call(redis.getdel(okey))
            if _text(taken) != _text(seen):
                # Another writer came between the two reads. A SPENT mark
                # goes back, and nothing is granted.
                if _is_spent(taken):
                    await _call(redis.set(okey, _SPENT, ex=spent_ttl_s()))
                return False
            await _call(redis.set(key(GRANT_NAMESPACE, who, thread, digest),
                                  _encode(rid, offer["text"], offer["keys"]),
                                  ex=GRANT_TTL_S))
    except Exception as exc:
        _log.warning("late_approve.grant_failed", error_type=type(exc).__name__)
        return False
    _log.info("late_approve.grant_written", request_id=_short(rid), thread_id=_short(thread))
    return True


# ── Step 3: the use (the card gate, before the card goes out) ────────────────

def _resume_on(org: str) -> bool:
    """True when ``CHAT_RESUME_ORGS`` names *org*: the one reader
    (``stored_call.chat_resume_on``), never a copy. A fault reads False."""
    try:
        from acb_skills.stored_call import chat_resume_on

        return bool(chat_resume_on(org))
    except Exception:
        return False


def _run_scope(*, spend: bool = False) -> tuple[str, str, str] | None:
    """``(org, member, thread)`` of this run with the flag on, or None.

    *spend* is True only for a writer of the SPENT mark (:func:`claim_act`,
    :func:`note_approved`). Then ``CHAT_RESUME_ORGS`` opens the scope too
    (RESUME-2a follow-up, P2-2). A live approve while only RESUME is on
    still marks the act, so a later flip of ``CHAT_LATE_APPROVE_ORGS`` never
    lets an old twin card write again. The grant (:func:`take_grant`) keeps
    ``CHAT_LATE_APPROVE_ORGS`` alone, so the members that get a grant do not
    change."""
    org = _run_org()
    if not org or not (late_approve_on(org) or (spend and _resume_on(org))):
        return None
    member, thread = _run_member(), _run_thread()
    if "@" not in member or not thread:
        return None
    return org, member, thread


async def take_grant(card: Any) -> bool:
    """True when this run may skip *card* (``ask_tools.PlainCard``): the
    member of this run approved this very card late, in this thread, and its
    grant is unused.

    ``ask_tools.request_confirmation`` calls it for a plain card only. The
    org, the member and the thread are the run's own. It takes exactly the
    grant of the card's own digest, once, with GETDEL, before the tool writes,
    so a crash after the check never lets a second use through. A card of
    another act never reads another card's grant. On a use, the act is SPENT.
    A fault reads False, and the normal card shows. Never raises.
    """
    try:
        scope = _run_scope()
        if scope is None:
            return False
        org, member, thread = scope
        act = act_of(card, _run_agent())
        if act is None or not _card_channel():
            return False
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            redis = _redis()
            grant = _record(await _call(redis.getdel(
                key(GRANT_NAMESPACE, member, thread, act.digest))))
            if grant is None:
                return False
            if grant["keys"] != act.keys or grant["text"] != act.text:
                _log.info("late_approve.grant_not_this_card", request_id=_short(grant["rid"]))
                return False
            if await _spend(redis, member, thread, act.digest):
                # The act ran at the same moment (the stored call of a twin
                # row, RESUME-2a). The card shows, so it never writes twice.
                _log.info("late_approve.grant_act_spent", request_id=_short(grant["rid"]))
                return False
    except Exception as exc:
        _log.warning("late_approve.use_failed", error_type=type(exc).__name__)
        return False
    _log.info("late_approve.grant_used", request_id=_short(grant["rid"]),
              thread_id=_short(thread))
    return True


async def claim_act(card: Any) -> str:
    """Take the act of *card* for THIS call (RESUME-2a). It marks the act
    SPENT, and gives :data:`CLAIM_TAKEN` only when the act was not SPENT
    before. The stored-call yes (``stored_call.resume_answer``) is the one
    caller. It writes only on :data:`CLAIM_TAKEN`.

    The mark and the test are ONE ``SET … GET`` (:func:`_spend`). So the
    stored calls of two twin rows that the member approves at the same moment
    take the act once, and a twin row that waits longer than the mark finds
    it still there (:func:`spent_ttl_s`).

    Three answers (follow-up P2-3). :data:`CLAIM_SPENT`: the act was SPENT
    already. :data:`CLAIM_UNKNOWN`: a run with no scope (both flags off, no
    verified member or no thread), a card with no act, or a fault. Nobody
    can then say if the act ran, so the note must not say that it ran. Each
    answer but :data:`CLAIM_TAKEN` shows the card (fail closed). Never
    raises."""
    try:
        scope = _run_scope(spend=True)
        if scope is None:
            return CLAIM_UNKNOWN
        org, member, thread = scope
        act = act_of(card, _run_agent())
        if act is None:
            return CLAIM_UNKNOWN
        from acb_common.tenant_redis import organization_scope

        with organization_scope(org):
            spent = await _spend(_redis(), member, thread, act.digest)
    except Exception as exc:
        _log.warning("late_approve.claim_act_failed", error_type=type(exc).__name__)
        return CLAIM_UNKNOWN
    return CLAIM_SPENT if spent else CLAIM_TAKEN


async def note_approved(card: Any) -> None:
    """The member approved *card* live: its act runs now. Mark the act SPENT
    and remove its grant, so no stale twin card of it, parked earlier, acts on
    a late approve (review of HRD-22, P1). It writes when either flag names
    the org (:func:`_run_scope` with ``spend=True``, P2-2). With both off it
    does nothing. Never raises."""
    try:
        scope = _run_scope(spend=True)
        if scope is None:
            return
        org, member, thread = scope
        act = act_of(card, _run_agent())
        if act is None:
            return
        from acb_common.tenant_redis import organization_scope

        with organization_scope(org):
            await _spend(_redis(), member, thread, act.digest)
    except Exception as exc:
        _log.warning("late_approve.spend_failed", error_type=type(exc).__name__)


__all__ = [
    "APPROVE",
    "ASK_NAMESPACE",
    "CLAIM_SPENT",
    "CLAIM_TAKEN",
    "CLAIM_UNKNOWN",
    "GRANT_NAMESPACE",
    "GRANT_TTL_S",
    "OFFER_NAMESPACE",
    "SPENT_NAMESPACE",
    "Act",
    "act_of",
    "card_text_of",
    "claim_act",
    "digest_of",
    "drop_offer",
    "grant_from_offer",
    "keys_wire",
    "late_approve_on",
    "note_approved",
    "record_offer",
    "spent_ttl_s",
    "take_grant",
    "twin_ran",
]
