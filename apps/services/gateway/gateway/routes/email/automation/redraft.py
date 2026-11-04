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
  puts ``revises`` and ``draft`` on the new record and its form. Only this
  module sets them. A request that names them gets a 422 (``extra="forbid"``).

**The rules.** The snapshot only informs the model, and no act reads it (rule
1). An answer to a revised card finds no record, so it sends and saves
nothing (rule 2). The tenant and the member come from the session, never from
the snapshot, and each key goes through ``acb_common.tenant_redis`` (R5).

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

#: The namespace of both keys of a redraft:
#: ``cc:<org>:email:card_draft:<thread>:<card id>`` and
#: ``cc:<org>:email:card_revised:<thread>``.
NAMESPACE = "email"
SNAPSHOT_PART = "card_draft"
LINK_PART = "card_revised"
#: The life of a snapshot and of a link, in seconds (§18.11 steps 2 and 4).
SNAPSHOT_TTL_S = 900
LINK_TTL_S = 900
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
    caps here bound the parse. :func:`checked_snapshot` checks the snapshot
    against the record of its card."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    subject: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=MAX_REPLY_BODY)
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
    """The snapshot to keep, or None when one check fails (§18.11 step 2).

    The subject passes the subject rule of §18.10 (``held_card._clean_subject``:
    one line, no hidden character, at most 200 characters). The text is no
    longer than the limit of the path. Each person is a person of the same
    kind on the record, named once. The record's own owner is the member, so
    a snapshot can name no one that the card did not offer. The ticks only
    inform the model: the next held card works out its ticks again (rule 4)."""
    people = _people_of(rec)
    if people is None:
        return None
    try:
        subject = hc._clean_subject(draft.subject)
    except HTTPException:
        return None
    if len(draft.body) > _text_limit(rec):
        return None
    lists: dict[str, list[str]] = {}
    for kind in _KINDS:
        named = [str(a).strip() for a in getattr(draft, kind) if str(a).strip()]
        lower = [a.lower() for a in named]
        if len(set(lower)) != len(lower) or any(a not in people[kind] for a in lower):
            return None
        lists[kind] = named
    return {"owner": owner, "card_id": draft.request_id, "subject": subject,
            "body": draft.body, **lists}


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


async def keep_card_draft(user: UserContext, thread_id: str, raw: Any) -> bool:
    """Check and keep the member's version of an open card (§18.11 step 2).

    The stream door calls it for a steer of the web chat that carries
    ``card_draft``. True when a checked snapshot went to tenant Redis. A
    snapshot that fails a check, a flag that is off, and a fault keep
    nothing, and the steer goes on. The tenant and the member come from the
    session. It never raises, so a snapshot can never stop a steer."""
    from acb_common.tenant_redis import organization_scope
    from acb_skills.reply_card import edit_card_on

    try:
        org = str(getattr(user, "organization_id", None) or "")
        member = _member(user)
        thread = str(thread_id or "").strip()
        if not org or not member or not thread or not edit_card_on(org):
            return False
        if not isinstance(raw, dict):
            _log.info("email.card_draft_dropped", reason="shape")
            return False
        try:
            draft = CardDraft.model_validate(raw)
        except ValidationError:
            _log.info("email.card_draft_dropped", reason="shape")
            return False
        found = await _find_card(user, draft.request_id, thread)
        if found is None:
            _log.info("email.card_draft_dropped", reason="no_card")
            return False
        snap = checked_snapshot(found[0], draft, member)
        if snap is None:
            _log.info("email.card_draft_dropped", reason="check")
            return False
        with organization_scope(org):
            await hc._tenant_redis().set(
                _key(SNAPSHOT_PART, thread, draft.request_id), json.dumps(snap),
                ex=SNAPSHOT_TTL_S)
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


async def link_fields(user: UserContext, thread_id: str) -> dict[str, Any]:
    """The link fields of the next new-mail card of the thread (§18.11 step 6).

    It takes the link once (``GETDEL``). ``{"draft": n, "revises": card id}``
    when one card of the member was revised before this card, else
    ``{"draft": 1}``. A link of two revised cards, of another member, a bad
    one and a fault all give ``{"draft": 1}``, and the card shows no label."""
    from acb_common.tenant_redis import organization_scope

    thread = str(thread_id or "").strip()
    if not thread:
        return {"draft": FIRST_DRAFT}
    try:
        with organization_scope(hc._org_of(user)):
            raw = await hc._tenant_redis().getdel(_key(LINK_PART, thread))
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("email.card_link_take_failed", error=type(exc).__name__)
        return {"draft": FIRST_DRAFT}
    link = _parse(raw)
    if (not link or link.get("many") or str(link.get("owner") or "") != _member(user)
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


async def _write_link(user: UserContext, thread: str, card_id: str, draft: int) -> None:
    """Write the link of the thread (§18.11 step 4.3). A link that already
    waits means a second card of the thread was revised before the next
    card, so the link names no card, and the next card is "Draft 1"."""
    from acb_common.tenant_redis import organization_scope

    member = _member(user)
    with organization_scope(hc._org_of(user)):
        redis = hc._tenant_redis()
        k = _key(LINK_PART, thread)
        done = await redis.set(k, json.dumps({"owner": member, "card_id": card_id,
                                              "draft": draft}), nx=True, ex=LINK_TTL_S)
        if not done:
            await redis.set(k, json.dumps({"owner": member, "many": True}), ex=LINK_TTL_S)


def _strings(value: Any) -> list[str]:
    return [str(a) for a in value if isinstance(a, str)] if isinstance(value, list) else []


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
    3. Write the link of the thread, ``{card_id, draft}``, for 900 seconds.
       ``draft`` is the draft number of the old card plus one.
    4. Answer the snapshot as a neutral data block, or "" for none.

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
    try:
        snap = await _take_snapshot(user, thread, req.card_id)
        if snap is not None:
            block = compose_card_draft(
                str(snap.get("subject") or ""), str(snap.get("body") or ""),
                _strings(snap.get("to")), _strings(snap.get("cc")), _strings(snap.get("bcc")))
    except Exception as exc:  # the old card is spent: answer with no snapshot
        _log.warning("email.card_snapshot_take_failed", error=type(exc).__name__)
    try:
        await _write_link(user, thread, req.card_id, nxt)
    except Exception as exc:  # the next card is then "Draft 1"
        _log.warning("email.card_link_write_failed", error=type(exc).__name__)
    _log.info("email.card_revised", draft=nxt, snapshot=bool(block))
    return {"revised": True, "draft": nxt, "snapshot": block}
