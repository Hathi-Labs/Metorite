"""A card that a chat message closes, and one draft for one email (task 3814).

Production, 2026-11-18. The owner saw the card "Save this new email in
Drafts?" and wrote "I think you can draft it better" in the chat. The chat
said "Added to the reply", but the card did not change. A steer reaches the
run only at a tool result, and the run waited in the card. The email cards of
``_card_refusal`` never passed ``close_on_steer``, so no message closed them.
Each redraft then also saved one more draft, so Drafts held near-copies.

This module holds two things, and both are dark behind ONE flag,
``CHAT_CARD_REVISE_ORGS``. Off, each card and each save is as before.

1. :func:`card_revise_on` is THE reader of the flag. The email agent asks it
   before a card of ``_confirm_destructive``, and turns ``close_on_steer`` on.
2. The one-draft rule. The chat keeps a record of the drafts that it saved,
   for each chat thread, mailbox and member, in tenant Redis
   (``acb_common.tenant_redis``, R5). Each entry holds the draft id, the To
   people, the subject and a digest of the stored text.

The rule runs in two steps, so the member sees it BEFORE the approve:

* :func:`plan_save` runs before the card. It names the draft that the save
  replaces, or none. The card then shows :data:`REPLACES_LINE`.
* :func:`save_one_draft` runs after the approve, with that id. It checks the
  id again against the record, and then reads the draft. A draft that is gone
  gives a new draft. A draft whose text differs from the digest was edited by
  the member, so it stays as it is, and the save makes a new draft
  (``kept``). A save with files makes the new draft first and then deletes
  the old one, because no provider can drop a file of a draft. Any other save
  updates the draft in place.

What "the same email" means (:func:`same_mail`): the same To SET and the same
subject (case, spaces and a "Re:" or "Fwd:" head do not count). A partial
overlap of the To people is another email. The one other way is the redraft
marker (:func:`mark_revised`): a card that a chat message closed names the
draft that it was about, for :data:`MARKER_TTL_S`, and the next save of the
same act may take that draft by its id.

Every value of the key comes from the run or the session, never from the
model or a request field (R5). A Redis fault never stops a save. It gives the
save of before, a new draft, and one log line with the class of the error.

Fence: ``tests/unit/test_card_revise.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

_log = get_logger(__name__)

#: The namespace of the record in tenant Redis:
#: ``cc:<org>:chat-drafts:<thread hash>:<mailbox hash>:<member hash>``.
NAMESPACE = "chat-drafts"
#: How long a record lives after its last write: seven days.
TTL_S = 7 * 24 * 3600
#: How long a redraft marker lives: ten minutes.
MARKER_TTL_S = 600
#: The most drafts that one record keeps. The oldest goes first.
MAX_DRAFTS = 20
#: The longest wait for one Redis call.
REDIS_TIMEOUT_S = 2.0
#: The line of a card whose save replaces a draft of this chat.
REPLACES_LINE = "This replaces the draft saved earlier in this chat."
#: The acts that save a NEW draft. A marker names one of them.
NEW_DRAFT = "new_draft"
NEW_MAIL = "new_mail"

#: The outcome of a save.
CREATED, UPDATED, REPLACED, KEPT_EDITED, KEPT_FILES, DELETE_FAILED = (
    "created", "updated", "replaced", "kept_edited", "kept_files", "delete_failed")


# ── The flag ─────────────────────────────────────────────────────────────────

def card_revise_on(org: object = None) -> bool:
    """True when ``CHAT_CARD_REVISE_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant. A comma list of org ids, or ``*`` for
    each one. Empty means off, the default. It reads the settings on each
    call, so a flag reload (``docs/flag_reload.md``) applies a change with no
    restart. It fails closed."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_card_revise_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    org = _org(org)
    return bool(org) and org.lower() in entries


def _org(org: object = None) -> str:
    """*org*, or the bound tenant of the run, or ""."""
    if org:
        return str(org).strip()
    try:
        from acb_common.db import current_tenant

        return str(current_tenant() or "").strip()
    except Exception:
        return ""


# ── The match ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChatDraft:
    """One draft that a chat saved: its id, its To people, its subject key and
    the digest of its stored subject and text."""

    draft_id: str
    to: tuple[str, ...]
    subject: str
    digest: str = ""
    #: True when the save carried files. An update can never drop a file.
    files: bool = False
    #: Task 3978: a reply draft of ``draft_reply``. The record holds it, so
    #: ``edit_draft`` knows that the chat made it. It is never the target of
    #: a save of a new mail (:func:`plan_save`, :func:`save_one_draft`).
    reply: bool = False


def _addresses(values: Iterable[Any] | None) -> tuple[str, ...]:
    """The addresses of *values*, lower case, each once, sorted."""
    return tuple(sorted({str(v).strip().lower() for v in values or () if str(v).strip()}))


_HEAD = re.compile(r"^\s*(?:re|fwd?|aw|sv)\s*:\s*", re.IGNORECASE)


def subject_key(subject: Any) -> str:
    """The subject as the match reads it: no "Re:" or "Fwd:" head, the words,
    case folded."""
    text = " ".join(str(subject or "").split())
    while True:
        cut = _HEAD.sub("", text, count=1)
        if cut == text:
            break
        text = cut
    return text.casefold()


def same_mail(draft: ChatDraft, to: Iterable[Any] | None, subject: Any) -> bool:
    """True when a save to *to* with *subject* is the email of *draft*: the
    same To SET and the same subject key. A partial overlap is another email."""
    want = _addresses(to)
    return bool(want) and want == draft.to and subject_key(subject) == draft.subject


def _people(value: Any) -> str:
    """One address list of a stored draft, folded: lower case, sorted. An
    item is an address or a ``{"email": ...}`` dict."""
    items = value if isinstance(value, list) else []
    return ",".join(_addresses(
        (v.get("email") if isinstance(v, dict) else v) for v in items))


def draft_digest(row: Any) -> str:
    """The digest of a stored draft: its subject, its text, and its To, Cc
    and Bcc people, folded. *row* is the draft as a read or a save answers
    it. So a Cc that the member added by hand counts as an edit."""
    row = row if isinstance(row, dict) else {}
    subject = " ".join(str(row.get("subject") or "").split())
    body = " ".join(str(row.get("body_text") or "").split())
    people = "\x01".join(_people(row.get(k)) for k in
                          ("to_addresses", "cc_addresses", "bcc_addresses"))
    return hashlib.sha256(f"{subject}\x00{body}\x00{people}".encode()).hexdigest()[:32]


def draft_files_digest(row: Any) -> str:
    """The digest of the files of a stored draft (HRD-10d-2). THE one rule.

    *row* is the draft as ``GET /email/messages/{id}`` answers it: the flag
    ``has_attachments`` and the list ``attachments``, each with its ``id``,
    ``filename`` and ``size_bytes``. The send route folds the rows of
    ``email_attachments`` in the same shape. The read lists the files only
    when the flag is true, so the flag is a part of the digest too, and the
    files of a row whose flag is false do not count. The order of the files
    does not count. A file that is added, removed or stored again as a new
    row changes the digest.

    It is a separate function, so :func:`draft_digest` (HRD-8c and
    HRD-10d-1) keeps its value for each draft."""
    row = row if isinstance(row, dict) else {}
    flagged = bool(row.get("has_attachments"))
    items: list[str] = []
    for f in (row.get("attachments") or []) if flagged else []:
        if not isinstance(f, dict):
            continue
        size = f.get("size_bytes")
        try:
            size_text = "" if size is None else str(int(size))
        except (TypeError, ValueError):
            size_text = str(size)
        items.append("\x1f".join((str(f.get("id") or "").strip().lower(),
                                  str(f.get("filename") or ""), size_text)))
    files = "\x1e".join(sorted(items))
    return hashlib.sha256(f"{int(flagged)}\x00{files}".encode()).hexdigest()[:32]


# ── The record ───────────────────────────────────────────────────────────────

def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:24]


def record_key(thread: str, account_id: str, member: str) -> Any:
    """The key of one record. The org is the bound one (tenant Redis)."""
    from acb_common.tenant_redis import key

    return key(NAMESPACE, _hash(thread), _hash(account_id), _hash(member))


def get_tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis as _client

    return _client()


@dataclass
class _Record:
    drafts: list[ChatDraft]
    marker: dict[str, Any]


def _record_of(raw: Any) -> _Record:
    """The record of a stored value. Version 1 was a list of drafts. A value
    that does not parse is an empty record."""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    rows = data if isinstance(data, list) else data.get("drafts") if isinstance(data, dict) else []
    marker = data.get("marker") if isinstance(data, dict) else None
    out: list[ChatDraft] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            out.append(ChatDraft(str(row["id"]).strip(), _addresses(row.get("to")),
                                 str(row.get("subject") or ""), str(row.get("digest") or ""),
                                 bool(row.get("files")), bool(row.get("reply"))))
    return _Record(out, marker if isinstance(marker, dict) else {})


def _parts(org: str, thread: str, account_id: str, member: str) -> bool:
    return all(str(v or "").strip() for v in (org, thread, account_id, member))


async def _read(org: str, thread: str, account_id: str, member: str) -> _Record:
    from acb_common.tenant_redis import organization_scope

    with organization_scope(org):
        raw = await asyncio.wait_for(
            get_tenant_redis().get(record_key(thread, account_id, member)),
            timeout=REDIS_TIMEOUT_S)
    return _record_of(raw)


async def _write(org: str, thread: str, account_id: str, member: str, rec: _Record) -> None:
    from acb_common.tenant_redis import organization_scope

    value = json.dumps({
        "v": 2,
        "drafts": [{"id": d.draft_id, "to": list(d.to), "subject": d.subject,
                    "digest": d.digest, **({"files": True} if d.files else {}),
                    **({"reply": True} if d.reply else {})}
                   for d in rec.drafts[-MAX_DRAFTS:]],
        **({"marker": rec.marker} if rec.marker else {}),
    })
    with organization_scope(org):
        await asyncio.wait_for(
            get_tenant_redis().set(record_key(thread, account_id, member), value, ex=TTL_S),
            timeout=REDIS_TIMEOUT_S)


def _live_marker(rec: _Record, act: str, to: Iterable[Any] | None) -> str:
    """The draft id of a live marker of *act* that the record still holds,
    for a save to the SAME To set as the closed card. The subject may change."""
    m = rec.marker
    if str(m.get("act") or "") != act or float(m.get("until") or 0) < time.time():
        return ""
    want = _addresses(to)
    if not want or want != _addresses(m.get("to")):
        return ""
    did = str(m.get("draft_id") or "")
    return did if any(d.draft_id == did and not d.reply for d in rec.drafts) else ""


# ── Before the card ──────────────────────────────────────────────────────────

async def plan_save(
    *, act: str, thread: str, account_id: str, member: str,
    to: Iterable[Any] | None, subject: Any, org: object = None,
) -> str:
    """The id of the draft that a save of *act* replaces, or "". It runs
    BEFORE the card, so the card can show :data:`REPLACES_LINE`.

    A live redraft marker of *act* wins. Else the newest draft of the same
    email (:func:`same_mail`). The flag off, a missing part and a Redis fault
    each give "", so the save makes a new draft."""
    org_id = _org(org)
    if not (card_revise_on(org_id) and _parts(org_id, thread, account_id, member)):
        return ""
    try:
        rec = await _read(org_id, thread, account_id, member)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("card_revise.record_read_failed", error=type(exc).__name__)
        return ""
    marked = _live_marker(rec, act, to)
    if marked:
        return marked
    for draft in reversed(rec.drafts):
        # Task 3978: a reply draft is never the draft of a new mail.
        if not draft.reply and same_mail(draft, to, subject):
            return draft.draft_id
    return ""


async def mark_revised(
    *, act: str, draft_id: str, thread: str, account_id: str, member: str,
    to: Iterable[Any] | None, org: object = None,
) -> None:
    """A card of *act* to *to* that a chat message closed was about
    *draft_id*. The next save of *act* to the same To set may take that draft
    for :data:`MARKER_TTL_S`, with another subject. No id writes no marker."""
    org_id = _org(org)
    did = str(draft_id or "").strip()
    if not (did and card_revise_on(org_id) and _parts(org_id, thread, account_id, member)):
        return
    try:
        rec = await _read(org_id, thread, account_id, member)
        if all(d.draft_id != did for d in rec.drafts):
            return
        rec.marker = {"act": act, "draft_id": did, "to": list(_addresses(to)),
                      "until": time.time() + MARKER_TTL_S}
        await _write(org_id, thread, account_id, member, rec)
    except Exception as exc:  # the class only
        _log.warning("card_revise.marker_write_failed", error=type(exc).__name__)


# ── After the approve ────────────────────────────────────────────────────────

async def note_draft(
    *, thread: str, account_id: str, member: str, draft_id: str,
    to: Iterable[Any] | None, subject: Any, digest: str = "", replaces: str = "",
    only_if_known: bool = False, act: str = "", files: bool = False, org: object = None,
    reply: bool = False,
) -> None:
    """Write *draft_id* into the record, as the newest draft of this chat.

    *replaces* names a draft id that leaves the record. With *only_if_known*,
    the record changes only when it already holds *draft_id* or *replaces*:
    ``edit_draft`` of any other draft never joins it. A save of *act* spends
    the marker of *act*. A Redis fault writes nothing and never raises.

    *reply* (task 3978) marks a reply draft of ``draft_reply``. An entry that
    was a reply draft stays one when a later write takes its place, so an
    edit never makes it a target of a new mail."""
    org_id = _org(org)
    did = str(draft_id or "").strip()
    if not (did and _parts(org_id, thread, account_id, member)):
        return
    try:
        rec = await _read(org_id, thread, account_id, member)
        gone = {did, str(replaces or "").strip()} - {""}
        if only_if_known and all(d.draft_id not in gone for d in rec.drafts):
            return
        reply = reply or any(d.reply for d in rec.drafts if d.draft_id in gone)
        rec.drafts = [d for d in rec.drafts if d.draft_id not in gone]
        rec.drafts.append(ChatDraft(did, _addresses(to), subject_key(subject), digest, files,
                                    reply))
        if act and rec.marker.get("act") == act:
            rec.marker = {}
        await _write(org_id, thread, account_id, member, rec)
    except Exception as exc:  # the class only
        _log.warning("card_revise.record_write_failed", error=type(exc).__name__)


def _gone(exc: BaseException) -> bool:
    """True for a 404 of the save: the draft is not in Drafts now. The agent's
    ``GatewayError`` says ``status``, and the gateway's ``HTTPException``
    says ``status_code``."""
    return 404 in (getattr(exc, "status", None), getattr(exc, "status_code", None))


@dataclass(frozen=True)
class SavedDraft:
    """The answer of the save, its outcome, and the earlier draft that it
    updated, replaced or kept, or ""."""

    result: dict[str, Any]
    outcome: str = CREATED
    earlier: str = ""

    @property
    def updated(self) -> str:
        """The id of the draft whose place the save took, or ""."""
        return self.earlier if self.outcome in (UPDATED, REPLACED, DELETE_FAILED) else ""


_DRAFT_FOLDERS = ("drafts", "draft")


async def save_one_draft(
    put: Callable[[dict[str, Any]], Awaitable[Any]], payload: dict[str, Any], *,
    replaces: str, thread: str, member: str, act: str,
    read: Callable[[str], Awaitable[Any]],
    delete: Callable[[str], Awaitable[Any]],
    org: object = None,
) -> SavedDraft:
    """Save a NEW draft of a chat, under the one-draft rule (task 3814).

    *replaces* is the id that :func:`plan_save` gave before the card, and the
    card showed :data:`REPLACES_LINE`. With "" the save is a new draft, as
    before. *put* saves a payload (``PUT /email/drafts`` or ``upsert_draft``)
    and answers the stored draft. *read* answers a stored draft, or None.
    *delete* moves a draft to the trash (the delete path of a message).

    With the id, the save checks that the member's record still holds it.
    The card showed the promise, so the save honours the id even when the
    member changed the subject or unticked a To on the card, or the marker
    ran out. Only the state of the draft can stop it (:func:`_state`):

    * ``gone``: the save makes a new draft (``created``).
    * ``edited``: the member edited it. It stays, and the save makes a new
      draft (``kept_edited``).
    * ``files``: it holds files. An update never drops a file, and a delete
      loses a file for good, so it stays, and the save makes a new draft
      (``kept_files``).
    * ``ok``: a save with files makes a new draft and then deletes the old
      one (``replaced``). Any other save updates it in place (``updated``)."""
    account_id = str(payload.get("account_id") or "")
    org_id = _org(org)
    on = card_revise_on(org_id) and _parts(org_id, thread, account_id, member)
    if not on:
        return SavedDraft(dict(await put(payload) or {}))
    target = await _known(str(replaces or "").strip(), org_id, thread, account_id, member)
    outcome, res = CREATED, None
    files = _has_files(payload)
    if target:
        state = _state(await read(target.draft_id), target)
        if state == GONE:
            _log.info("card_revise.earlier_draft_gone")
        elif state == EDITED:
            outcome = KEPT_EDITED
        elif state == FILES:
            outcome = KEPT_FILES
        elif files:
            outcome = REPLACED
        else:
            try:
                res = dict(await put({**payload, "draft_id": target.draft_id}) or {})
                outcome = UPDATED
            except Exception as exc:
                if not _gone(exc):
                    raise
                _log.info("card_revise.earlier_draft_gone")
    if res is None:
        res = dict(await put(payload) or {})
    if outcome == REPLACED:
        try:
            await delete(target.draft_id)
        except Exception as exc:  # the new draft stays, and the answer says so
            _log.warning("card_revise.earlier_draft_delete_failed", error=type(exc).__name__)
            outcome = DELETE_FAILED
    earlier = target.draft_id if target else ""
    await note_draft(thread=thread, account_id=account_id, member=member,
                     draft_id=str(res.get("id") or earlier), to=payload.get("to"),
                     subject=payload.get("subject"), digest=draft_digest(res),
                     replaces="" if outcome in (*_KEPT, DELETE_FAILED) else earlier,
                     act=act, files=files, org=org_id)
    if outcome in _KEPT:
        # The earlier draft stays in Drafts as it is, and is never a target again.
        await _forget(earlier, org_id, thread, account_id, member)
    return SavedDraft(res, outcome, earlier)


#: The state of an earlier draft before a save takes its place.
OK, GONE, EDITED, FILES = "ok", "gone", "edited", "files"
#: The outcomes where the earlier draft stays as it is.
_KEPT = (KEPT_EDITED, KEPT_FILES)


def _has_files(payload: dict[str, Any]) -> bool:
    return bool(payload.get("attachments") or payload.get("artifacts"))


def _state(row: Any, target: ChatDraft) -> str:
    """``gone`` when *row* is no draft now, ``edited`` when its digest is not
    the digest that the chat saved, ``files`` when it holds a file (the
    record says so, or the stored draft: a file added by hand), else ``ok``."""
    if not isinstance(row, dict) or str(row.get("folder") or "").lower() not in _DRAFT_FOLDERS:
        return GONE
    if not target.digest or draft_digest(row) != target.digest:
        return EDITED
    if target.files or row.get("has_attachments") or row.get("attachments"):
        return FILES
    return OK


async def target_state(
    draft_id: str, *, thread: str, account_id: str, member: str,
    read: Callable[[str], Awaitable[Any]], org: object = None,
) -> str:
    """The state of the draft that :func:`plan_save` named, BEFORE the card:
    ``ok``, ``gone``, ``edited`` or ``files``. So a card never promises to
    replace, or to cancel the send of, a draft that the member edited or that
    holds files. A draft that the record does not hold reads as ``gone``."""
    org_id = _org(org)
    try:
        rec = await _read(org_id, thread, account_id, member)
    except Exception as exc:
        _log.warning("card_revise.record_read_failed", error=type(exc).__name__)
        return GONE
    target = next((d for d in rec.drafts if d.draft_id == draft_id), None)
    if target is None:
        return GONE
    state = _state(await read(draft_id), target)
    if state == GONE:
        # A draft that left Drafts is never a target again.
        await _forget(draft_id, org_id, thread, account_id, member)
    return state


async def _known(
    did: str, org: str, thread: str, account_id: str, member: str,
) -> ChatDraft | None:
    """The record entry of *did*, the draft that the card named, or None when
    the member's record does not hold it. A Redis fault gives None."""
    if not did:
        return None
    try:
        rec = await _read(org, thread, account_id, member)
    except Exception as exc:
        _log.warning("card_revise.record_read_failed", error=type(exc).__name__)
        return None
    # Task 3978: a reply draft is never the target of a save of a new mail.
    return next((d for d in rec.drafts if d.draft_id == did and not d.reply), None)


async def _forget(did: str, org: str, thread: str, account_id: str, member: str) -> None:
    try:
        rec = await _read(org, thread, account_id, member)
        rec.drafts = [d for d in rec.drafts if d.draft_id != did]
        await _write(org, thread, account_id, member, rec)
    except Exception as exc:
        _log.warning("card_revise.record_write_failed", error=type(exc).__name__)
