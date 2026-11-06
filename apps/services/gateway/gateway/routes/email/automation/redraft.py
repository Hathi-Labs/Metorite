"""A redraft through the chat keeps the member's edits (§18.11, owner 2026-11-05).

While the card of a new mail waits, the member can edit it and then ask in
the chat for a change ("make it shorter"). The chat message closes the card as
REVISED (``ask_tools.close_on_steer``). Three parts here carry the member's
version to the model and link the next card to the old one. Each part names
its step of ``email_app_master_plan.md`` §18.11.

* :func:`keep_card_draft` (step 2). The stream door calls it for a steer that
  carries ``card_draft``. It reads the record of the card WITHOUT spending it
  (``held_card.peek_record``), checks the snapshot against that record, and
  keeps a good one in tenant Redis for 900 seconds. A bad one is dropped, and
  the steer goes on without it.
* ``POST /email/new-card/revise`` (step 4). ``send_email`` calls it when its
  card closed as REVISED. In this order it spends the record of the old card,
  takes the snapshot once, and writes the link of the thread. It answers the
  snapshot as a neutral data block (``chat_recovery.compose_card_draft``).
* :func:`link_fields` (step 6). The next ``POST /email/new-card``, or ``POST
  /email/held-card`` of kind ``new``, of the thread takes the link once, and
  puts ``revises`` and ``draft`` on the new record and its form. Only a card
  whose To shares an address with the To of the revised card takes it (fix
  round 3). Only this module sets the two fields. A request that names them
  gets a 422 (``extra="forbid"``).
* :func:`kept_unticked` (rule 7). The revise route keeps the people that the
  member unticked on the old card, for the member and the thread. EVERY
  new-mail card of the member in the thread starts those people unticked
  until the hash expires (fix round 3). No count spends them. A re-tick of
  a kept person clears that person. It only unticks: it never ticks, adds
  or removes a person.

**The rules.** The snapshot only informs the model, and no act reads it (rule
1). An answer to a revised card finds no record, so it sends and saves
nothing (rule 2). The kept unticks change only the START of the next card's
ticks, and the act sends only to the ticks of the member's answer. The tenant
and the member come from the session, never from the snapshot, and each key
goes through ``acb_common.tenant_redis`` (R5).

**Dark.** While ``EMAIL_EDIT_CARD_ORGS`` leaves the org out, nothing here runs:
the stream door keeps no snapshot, and the revise route answers 404. A record
of a run that draws no card to edit (WhatsApp) names no thread, so it takes
no snapshot and no revise either. Fence: ``tests/unit/test_email_redraft_snapshot.py``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_skills.reply_card import MAX_REPLY_BODY
from fastapi import Depends, HTTPException
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.automation import new_mail_card as nm
from gateway.routes.email.core import _log, router
from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: The namespace of the three keys of a redraft:
#: ``cc:<org>:email:card_draft:<thread>:<card id>``,
#: ``cc:<org>:email:card_revised:<thread>`` and
#: ``cc:<org>:email:card_unticked:<thread>:<member>``.
NAMESPACE = "email"
SNAPSHOT_PART = "card_draft"
LINK_PART = "card_revised"
UNTICKED_PART = "card_unticked"
#: The life of a snapshot, of a link and of the kept unticks, in seconds
#: (§18.11 steps 2 and 4, rule 7).
SNAPSHOT_TTL_S = 900
LINK_TTL_S = 900
UNTICKED_TTL_S = 900
#: The hash field of each kept person. The prefix keeps an address apart from
#: the other field, ``owner``.
_PERSON_FIELD = "p:"
#: The draft number of a record that names none: a card from before §18.11,
#: or a card that no revise linked. Such a card shows no "Draft" label.
FIRST_DRAFT = 1
#: The highest draft number. A longer chain keeps this number.
MAX_DRAFT = 99
#: The most addresses of one list of a snapshot (``held_card.MAX_ROWS``).
MAX_PEOPLE = hc.MAX_ROWS
_CARD_ID = re.compile(r"\A[0-9a-f]{32}\Z")
#: The kinds of a list of people, in card order.
_KINDS = ("to", "cc", "bcc")


class CardDraft(BaseModel):
    """The member's version of an open new-mail card, as the chat sent it.

    ``extra="forbid"``: a snapshot with any other key is dropped whole. The
    caps here bound the lists of people. :func:`checked_snapshot` checks the
    snapshot against the record of its card. The subject and the text have no
    cap here: their check is the path's own (P2 of fix round 3), so a text
    that fails it keeps the member's ticks."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    subject: str = ""
    body: str = ""
    to: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)
    cc: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)
    bcc: list[str] = Field(default_factory=list, max_length=MAX_PEOPLE)


class ReviseRequest(BaseModel):
    """The tool's request to revise one card. ``card_thread_id`` is the run's
    own thread, never a value of the model. ``extra="forbid"``."""

    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    card_thread_id: str = Field(min_length=1, max_length=200)


def _key(*parts: str) -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, *parts)


def _member(user: UserContext) -> str:
    return (user.email or "").strip().lower()


def _people_of(rec: dict[str, Any]) -> dict[str, set[str]] | None:
    """The people of a card record, by kind and in lower case, or None for a
    record that is not a new mail. A plain record holds the lists, and a held
    record (§21) holds one row for each person."""
    kind = rec.get("kind")
    if kind == "new-mail":
        return {k: {str(a).strip().lower() for a in rec.get(k) or []} for k in _KINDS}
    if kind == "new":
        out: dict[str, set[str]] = {k: set() for k in _KINDS}
        for row in rec.get("rows") or []:
            if isinstance(row, dict) and row.get("kind") in out:
                out[str(row["kind"])].add(str(row.get("address") or "").strip().lower())
        return out
    return None


def _text_limit(rec: dict[str, Any]) -> int:
    """The longest text that the act of the record's path takes: the held
    limit for a held record, else the limit of a reply."""
    from acb_skills.held_card import HELD_TEXT_MAX

    return HELD_TEXT_MAX if rec.get("kind") == "new" else MAX_REPLY_BODY


def checked_snapshot(rec: dict[str, Any], draft: CardDraft, owner: str) -> dict[str, Any] | None:
    """The snapshot to keep, or None when the people fail (§18.11 step 2).

    The PEOPLE and the TEXT have two checks (P2 of fix round 3):

    * Each person is a person of the same kind on the record, named once,
      in any case. The record's own owner is the member, so a snapshot can
      name no one that the card did not offer. A failure keeps nothing.
    * The subject passes the subject rule of §18.10 (``held_card._clean_subject``:
      one line, no hidden character, at most 200 characters), and the text is
      no longer than the limit of the path. A failure keeps the ticks with no
      subject and no text (:func:`has_text`), so the member's unticks still
      reach rule 7, and the model gets no version of the text.

    The ticks only inform the model: the next held card works out its ticks
    again (rule 4)."""
    people = _people_of(rec)
    if people is None:
        return None
    lists: dict[str, list[str]] = {}
    for kind in _KINDS:
        named = [str(a).strip() for a in getattr(draft, kind) if str(a).strip()]
        lower = [a.lower() for a in named]
        if len(set(lower)) != len(lower) or any(a not in people[kind] for a in lower):
            return None
        lists[kind] = named
    snap: dict[str, Any] = {"owner": owner, "card_id": draft.request_id, **lists}
    try:
        subject = hc._clean_subject(draft.subject)
    except HTTPException:
        return snap
    if len(draft.body) > _text_limit(rec):
        return snap
    return {**snap, "subject": subject, "body": draft.body}


def has_text(snap: dict[str, Any]) -> bool:
    """True when the snapshot keeps the member's subject and text. A snapshot
    whose text failed its check keeps only the ticks (:func:`checked_snapshot`)."""
    return isinstance(snap.get("subject"), str) and isinstance(snap.get("body"), str)


async def _find_card(
    user: UserContext, card_id: str, thread_id: str,
) -> tuple[dict[str, Any], str] | None:
    """The record of the new-mail card *card_id* of the member and of the
    thread, and its namespace, read WITHOUT spending it. A plain card first,
    then a held card of kind ``new``. None for anything else."""
    rec = await hc.peek_record(user, card_id, thread_id=thread_id,
                               namespace=nm.RECORD_NAMESPACE)
    if rec is not None and rec.get("kind") == "new-mail":
        return rec, nm.RECORD_NAMESPACE
    rec = await hc.peek_record(user, card_id, thread_id=thread_id)
    if rec is not None and rec.get("kind") == "new":
        return rec, hc.RECORD_NAMESPACE
    return None


#: The most snapshots of one steer: one for each open new-mail card (P2).
MAX_SNAPSHOTS = 8


async def keep_card_draft(user: UserContext, thread_id: str, raw: Any) -> bool:
    """Check and keep the member's version of each open card (§18.11 step 2).

    The stream door calls it for a steer of the web chat that carries
    ``card_draft``: one snapshot, or a list with one for EACH open new-mail
    card (P2), an unedited card too, so a snapshot that an older steer left
    for a card is replaced. True when a checked snapshot went to tenant Redis,
    also one that keeps only the ticks.
    A list of more than :data:`MAX_SNAPSHOTS` keeps its first ones: the chat
    puts each card that the member edited first (P2 of fix round 2). A
    snapshot that fails a check, a flag that is off, and a fault keep
    nothing, and the steer goes on. The tenant and the member come from the
    session. It never raises, so a snapshot can never stop a steer."""
    from acb_skills.reply_card import edit_card_on

    try:
        org = str(getattr(user, "organization_id", None) or "")
        member = _member(user)
        thread = str(thread_id or "").strip()
        if not org or not member or not thread or not edit_card_on(org):
            return False
        items = raw if isinstance(raw, list) else [raw]
        if len(items) > MAX_SNAPSHOTS:
            _log.info("email.card_draft_cut", count=len(items))
            items = items[:MAX_SNAPSHOTS]
        kept = False
        for item in items:
            if await _keep_one(user, org, member, thread, item):
                kept = True
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("email.card_draft_failed", error=type(exc).__name__)
        return False
    return kept


async def _keep_one(user: UserContext, org: str, member: str, thread: str, raw: Any) -> bool:
    """Check one snapshot against its card's record, and keep a good one.

    The card comes first: the record must be the member's, of this thread.
    Then an older snapshot of that card goes (P2 of fix round 3), so a revise
    never reads a version that a newer steer replaced, even when the newer
    one fails its check."""
    from acb_common.tenant_redis import organization_scope

    try:
        card_id = raw.get("request_id") if isinstance(raw, dict) else None
        if not isinstance(card_id, str) or not _CARD_ID.match(card_id):
            _log.info("email.card_draft_dropped", reason="shape")
            return False
        found = await _find_card(user, card_id, thread)
        if found is None:
            _log.info("email.card_draft_dropped", reason="no_card")
            return False
        with organization_scope(org):
            redis = hc._tenant_redis()
            k = _key(SNAPSHOT_PART, thread, card_id)
            await redis.delete(k)
            try:
                draft = CardDraft.model_validate(raw)
            except ValidationError:
                _log.info("email.card_draft_dropped", reason="shape")
                return False
            snap = checked_snapshot(found[0], draft, member)
            if snap is None:
                _log.info("email.card_draft_dropped", reason="check")
                return False
            if not has_text(snap):
                _log.info("email.card_draft_text_dropped")
            await redis.set(k, json.dumps(snap), ex=SNAPSHOT_TTL_S)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("email.card_draft_failed", error=type(exc).__name__)
        return False
    _log.info("email.card_draft_kept")
    return True


def _parse(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def draft_of(rec: dict[str, Any]) -> int:
    """The draft number of a card record. A record that names none, or a bad
    one, is :data:`FIRST_DRAFT`."""
    value = rec.get("draft")
    if isinstance(value, int) and not isinstance(value, bool) and FIRST_DRAFT <= value <= MAX_DRAFT:
        return value
    return FIRST_DRAFT


def _lower_set(value: Any) -> set[str]:
    """The addresses of *value* in lower case, with no blank one."""
    items = value if isinstance(value, list | tuple | set | frozenset) else []
    return {a.strip().lower() for a in items if isinstance(a, str) and a.strip()}


def _shares_to(link: dict[str, Any], to: Any) -> bool:
    """True when the To of the new card shares an address with the To of the
    revised card that the link names, in any case (rule 3 of fix round 3)."""
    return bool(_lower_set(link.get("to")) & _lower_set(to))


def _takes(link: dict[str, Any] | None, member: str, to: Any) -> bool:
    """True when this card of *member*, with the To *to*, may take *link*: the
    link is the member's, and it names two revised cards or the card's To."""
    if link is None or str(link.get("owner") or "") != member:
        return False
    return bool(link.get("many")) or _shares_to(link, to)


async def link_fields(user: UserContext, thread_id: str, to: Any) -> dict[str, Any]:
    """The link fields of a new-mail card of the thread (§18.11 step 6).

    It reads the link, checks it, and only then takes it (P3: compare, then
    delete). The link goes to a card of its owner whose To (*to*) shares an
    address with the To of the revised card, in any case (fix round 3). So a
    new mail to another person, in the same message, takes no link and shows
    as its own card. A link of another member, and a link that this card does
    not match, stay for the card they name. ``{"draft": n, "revises": card
    id}`` for the matching card, else ``{"draft": 1}``. A link of two revised
    cards goes to the first card of its owner, and gives ``{"draft": 1}``. A
    bad link and a fault also give ``{"draft": 1}``, and the card shows no
    label."""
    from acb_common.tenant_redis import organization_scope

    thread = str(thread_id or "").strip()
    if not thread:
        return {"draft": FIRST_DRAFT}
    member = _member(user)
    try:
        with organization_scope(hc._org_of(user)):
            redis = hc._tenant_redis()
            k = _key(LINK_PART, thread)
            if not _takes(_parse(await redis.get(k)), member, to):
                return {"draft": FIRST_DRAFT}
            taken = _parse(await redis.getdel(k))
            if taken is not None and not _takes(taken, member, to):
                # Another revise wrote the key between the read and the take.
                # Put it back for the card it names (the first writer wins).
                await redis.set(k, json.dumps(taken), nx=True, ex=LINK_TTL_S)
                return {"draft": FIRST_DRAFT}
            raw = taken
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("email.card_link_take_failed", error=type(exc).__name__)
        return {"draft": FIRST_DRAFT}
    link = raw if isinstance(raw, dict) else None
    if (not link or link.get("many") or str(link.get("owner") or "") != member
            or not _CARD_ID.match(str(link.get("card_id") or ""))):
        return {"draft": FIRST_DRAFT}
    draft = link.get("draft")
    if not isinstance(draft, int) or isinstance(draft, bool) or not 1 < draft <= MAX_DRAFT:
        return {"draft": FIRST_DRAFT}
    return {"draft": draft, "revises": str(link["card_id"])}


def form_link(linked: dict[str, Any]) -> dict[str, Any]:
    """``revises`` and ``draft`` for the answer and the form of a new card,
    or nothing for a card that no revise linked."""
    if not linked.get("revises"):
        return {}
    return {"revises": linked["revises"], "draft": linked["draft"]}


async def _take_snapshot(
    user: UserContext, thread: str, card_id: str,
) -> dict[str, Any] | None:
    """The snapshot of the card, taken once (``GETDEL``), or None. A snapshot
    of another member or another card is None."""
    from acb_common.tenant_redis import organization_scope

    with organization_scope(hc._org_of(user)):
        raw = await hc._tenant_redis().getdel(_key(SNAPSHOT_PART, thread, card_id))
    snap = _parse(raw)
    if not snap or str(snap.get("owner") or "") != _member(user):
        return None
    if str(snap.get("card_id") or "") != card_id:
        return None
    return snap


async def _write_link(
    user: UserContext, thread: str, card_id: str, draft: int, to: list[str],
) -> None:
    """Write the link of the thread (§18.11 step 4.4): the revised card, its
    next draft number, and its To in lower case, so only a card to the same
    people takes it (fix round 3).

    A link of the same member that already waits means a second card of the
    thread was revised before the next card. The link then names no card
    (``many``), and the next card is "Draft 1". A link of ANOTHER member
    stays as it is (P3 of fix round 3): this revise writes none, and its
    next card is "Draft 1"."""
    from acb_common.tenant_redis import organization_scope

    member = _member(user)
    link = json.dumps({"owner": member, "card_id": card_id, "draft": draft, "to": to})
    with organization_scope(hc._org_of(user)):
        redis = hc._tenant_redis()
        k = _key(LINK_PART, thread)
        if await redis.set(k, link, nx=True, ex=LINK_TTL_S):
            return
        seen = _parse(await redis.get(k))
        if seen is None:
            # The link that waited went between the two calls: write this one.
            await redis.set(k, link, nx=True, ex=LINK_TTL_S)
            return
        if str(seen.get("owner") or "") != member:
            _log.info("email.card_link_kept_for_owner")
            return
        await redis.set(k, json.dumps({"owner": member, "many": True}), xx=True, ex=LINK_TTL_S)


def _to_of(rec: dict[str, Any]) -> list[str]:
    """The To of a card record, in lower case and in a fixed order."""
    people = _people_of(rec)
    return sorted(a for a in (people or {}).get("to", set()) if a)


def _strings(value: Any) -> list[str]:
    return [str(a) for a in value if isinstance(a, str)] if isinstance(value, list) else []


# ── Rule 7: the people that the member unticked stay unticked ────────────────


def _untickable(rec: dict[str, Any]) -> dict[str, list[str]] | None:
    """The people of a card record whose tick is the member's to take away,
    by kind, in lower case and in card order. A plain card (§18.10) starts each
    person ticked. A held card (§21) starts only a row that its class ticks,
    so a new address that started unticked is never the member's untick.
    None for a record that is not a new mail."""
    kind = rec.get("kind")
    if kind == "new-mail":
        return {k: [str(a).strip().lower() for a in rec.get(k) or []] for k in _KINDS}
    if kind == "new":
        out: dict[str, list[str]] = {k: [] for k in _KINDS}
        for row in rec.get("rows") or []:
            if isinstance(row, dict) and row.get("kind") in out and row.get("checked") is True:
                out[str(row["kind"])].append(str(row.get("address") or "").strip().lower())
        return out
    return None


def member_unticked(rec: dict[str, Any], snap: dict[str, Any] | None) -> list[str]:
    """The people that the member unticked on the card of *rec*, in lower case.

    With a snapshot, each person whose tick was the member's to take away
    (:func:`_untickable`) and that the member's version does not tick. With
    no snapshot, the people that the card itself started unticked for this
    reason (``rec["unticked"]``), so a chain of drafts keeps them. The list
    names only people of the record: no snapshot can add one."""
    if snap is None:
        return [a for a in _strings(rec.get("unticked")) if a == a.strip().lower() and a]
    start = _untickable(rec)
    if start is None:
        return []
    out: list[str] = []
    for kind in _KINDS:
        on = _lower_set(_strings(snap.get(kind)))
        out += [a for a in start[kind] if a and a not in on and a not in out]
    return out


def member_reticked(rec: dict[str, Any], snap: dict[str, Any] | None) -> list[str]:
    """The kept unticks that the member ticked again on the card of *rec*,
    in lower case (rule 7, fix round 3).

    A re-tick is a person that the card started unticked for rule 7
    (``rec["unticked"]``) and that the member's version ticks, in any role
    and in any case. Only a re-tick clears a kept person. A person that a
    card started ticked never clears one, so a tick on one card never
    undoes an untick on another card of the same message. With no snapshot,
    the gateway does not know the ticks, so it clears nobody."""
    if snap is None:
        return []
    kept = _lower_set(_strings(rec.get("unticked")))
    on: set[str] = set()
    for kind in _KINDS:
        on |= _lower_set(_strings(snap.get(kind)))
    return sorted(kept & on)


async def _note_unticked(
    user: UserContext, thread: str, people: list[str], reticked: list[str],
) -> None:
    """Keep *people* for the new-mail cards of the member in the thread, and
    clear *reticked* (rule 7).

    One hash for the member and the thread: ``owner`` and one ``p:<address>``
    field for each person. No count spends it (fix round 3): it lives until
    its TTL, and each new-mail card of the member in the thread reads it. An
    extra untick costs one click, and a lost one sends mail to a person that
    the member took out. One ``HDEL`` clears the re-ticks first. Then one
    MULTI writes the people and the TTL, so two revises at once both keep
    their people."""
    from acb_common.tenant_redis import organization_scope

    member = _member(user)
    if not member:
        return
    with organization_scope(hc._org_of(user)):
        redis = hc._tenant_redis()
        k = _key(UNTICKED_PART, thread, member)
        if reticked:
            await redis.hdel(k, *(_PERSON_FIELD + a for a in reticked))
        if people:
            pipe = redis.pipeline(transaction=True)
            pipe.hset(k, mapping={"owner": member, **{_PERSON_FIELD + a: "1" for a in people}})
            pipe.expire(k, UNTICKED_TTL_S)
            await pipe.execute()


def _text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


async def kept_unticked(user: UserContext, thread_id: str) -> frozenset[str]:
    """The people that start unticked on this new-mail card (rule 7), in lower case.

    The new-card routes call it for each card of the member in the thread
    while ``EMAIL_EDIT_CARD_ORGS`` names the org. It is ONE read
    (``HGETALL``), and it spends nothing (fix round 3). So each card of the
    member in the thread, in any order, starts the people unticked until the
    hash expires, and a card whose record write fails spends nothing. Another
    member, another thread, no hash and a fault give none. The people change
    only the start of the card's ticks: the act sends only to the ticks of
    the member's answer."""
    from acb_common.tenant_redis import organization_scope

    thread = str(thread_id or "").strip()
    member = _member(user)
    if not thread or not member:
        return frozenset()
    try:
        with organization_scope(hc._org_of(user)):
            raw = await hc._tenant_redis().hgetall(_key(UNTICKED_PART, thread, member))
        fields = {_text(f): _text(v) for f, v in (raw or {}).items()}
        if not fields or fields.get("owner") != member:
            return frozenset()
        return frozenset(f[len(_PERSON_FIELD):].strip().lower() for f in fields
                         if f.startswith(_PERSON_FIELD) and f[len(_PERSON_FIELD):].strip())
    except HTTPException:
        raise
    except Exception as exc:  # the class only: an address never reaches a log
        _log.warning("email.card_unticked_read_failed", error=type(exc).__name__)
        return frozenset()


def start_unticked(
    lists: dict[str, list[str]], off: frozenset[str],
) -> tuple[dict[str, list[str]], list[str]]:
    """The ticked start of a plain card's *lists* with each person of *off*
    unticked, and those people in lower case. UNTICK ONLY: the start is a part
    of each list, and the lists themselves do not change."""
    start = {k: [a for a in lists.get(k) or [] if a.strip().lower() not in off] for k in _KINDS}
    named: list[str] = []
    for k in _KINDS:
        named += [a.strip().lower() for a in lists.get(k) or []
                  if a.strip().lower() in off and a.strip().lower() not in named]
    return start, named


@router.post("/new-card/revise")
async def revise_new_mail_card(
    req: ReviseRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Spend the old card of a redraft, and give the member's version (§18.11 step 4).

    ``send_email`` calls it when its card closed as REVISED. The record must
    be the caller's own, of this thread. In this order:

    1. Spend the record of the old card (``take_record``), so a late answer
       finds no record and sends nothing.
    2. Take the snapshot of that card once (``GETDEL``), if there is one.
    3. Keep the people that the member unticked on that card, for 900
       seconds (rule 7, :func:`member_unticked`), and clear each kept
       person that the member ticked again (:func:`member_reticked`).
    4. Write the link of the thread, ``{card_id, draft, to}``, for 900
       seconds. ``draft`` is the draft number of the old card plus one.
    5. Answer the snapshot as a neutral data block, or "" for none. A
       snapshot whose text failed its check keeps only the ticks, so it
       answers "".

    A fault after step 1 still answers, with no snapshot, so the tool gives
    the model its REVISED text. Nothing here sends or saves a mail. 404 while
    ``EMAIL_EDIT_CARD_ORGS`` leaves the org out, and for a record of another
    member, of another thread, or of a run that draws no card to edit."""
    from gateway.chat_recovery import compose_card_draft

    nm._off(user)
    thread = req.card_thread_id.strip()
    found = await _find_card(user, req.card_id, thread) if thread else None
    if found is None:
        _log.info("email.card_revise_refused", reason="no_card")
        raise HTTPException(status_code=404, detail=nm.EDIT_CARD_OFF)
    _rec, namespace = found
    spent = await hc.take_record(user, req.card_id, namespace=namespace)
    if spent is None:
        # The act took the record first, or Redis failed. Nothing more runs.
        _log.warning("email.card_revise_refused", reason="not_spent")
        raise HTTPException(status_code=409, detail=nm.NO_CARD)
    nxt = min(draft_of(spent) + 1, MAX_DRAFT)
    block = ""
    snap: dict[str, Any] | None = None
    try:
        snap = await _take_snapshot(user, thread, req.card_id)
        if snap is not None and has_text(snap):
            block = compose_card_draft(
                str(snap.get("subject") or ""), str(snap.get("body") or ""),
                _strings(snap.get("to")), _strings(snap.get("cc")), _strings(snap.get("bcc")))
    except Exception as exc:  # the old card is spent: answer with no snapshot
        _log.warning("email.card_snapshot_take_failed", error=type(exc).__name__)
    off: list[str] = []
    back: list[str] = []
    try:
        off = member_unticked(spent, snap)
        back = member_reticked(spent, snap)
        await _note_unticked(user, thread, off, back)
    except Exception as exc:  # the next card then starts with the server's ticks
        _log.warning("email.card_unticked_write_failed", error=type(exc).__name__)
    try:
        await _write_link(user, thread, req.card_id, nxt, _to_of(spent))
    except Exception as exc:  # the next card is then "Draft 1"
        _log.warning("email.card_link_write_failed", error=type(exc).__name__)
    _log.info("email.card_revised", draft=nxt, snapshot=bool(block),
              ticks=snap is not None, unticked=len(off), reticked=len(back))
    return {"revised": True, "draft": nxt, "snapshot": block}
