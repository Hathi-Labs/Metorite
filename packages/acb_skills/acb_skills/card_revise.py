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
   before a card of ``_card_refusal``, and turns ``close_on_steer`` on.
2. :func:`save_one_draft` is THE save of a new draft from a chat. The chat
   keeps a record of the drafts that it saved, for each chat thread, mailbox
   and member, in tenant Redis (``acb_common.tenant_redis``, R5). A new save
   of the same email updates the earlier draft in place. A draft that is gone
   (404) gives a new draft, and the record takes its id. The email agent's
   ``edit_draft`` and the two gateway saves of a new-mail card call it.

What "the same email" means (:func:`same_mail`): the same To people, or the
same subject with one To person in common. An email to other people is never
the same email, so it never writes over a draft of theirs.

Every value of the key comes from the run or the session, never from the
model or a request field (R5). A Redis fault never stops a save. It gives the
save of before, a new draft, and one log line with the class of the error.

Fence: ``tests/unit/test_card_revise.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

_log = get_logger(__name__)

#: The namespace of the record in tenant Redis:
#: ``cc:<org>:chat-drafts:<thread hash>:<mailbox>:<member hash>``.
NAMESPACE = "chat-drafts"
#: How long a record lives after its last save: seven days.
TTL_S = 7 * 24 * 3600
#: The most drafts that one record keeps. The oldest goes first.
MAX_DRAFTS = 20
#: The longest wait for one Redis call.
REDIS_TIMEOUT_S = 2.0


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


# ── The record ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChatDraft:
    """One draft that a chat saved: its id, its To people and its subject."""

    draft_id: str
    to: tuple[str, ...]
    subject: str


def _addresses(values: Iterable[Any] | None) -> tuple[str, ...]:
    """The addresses of *values*, lower case, each once, sorted."""
    return tuple(sorted({str(v).strip().lower() for v in values or () if str(v).strip()}))


def _subject_key(subject: Any) -> str:
    """The subject as the match reads it: the words, case folded."""
    return " ".join(str(subject or "").split()).casefold()


def same_mail(draft: ChatDraft, to: Iterable[Any] | None, subject: Any) -> bool:
    """True when a save to *to* with *subject* is the email of *draft*.

    The same To people, or the same subject with one To person in common.
    An email to other people is never the same email."""
    want = set(_addresses(to))
    have = set(draft.to)
    if not want or not have:
        return False
    if want == have:
        return True
    subj = _subject_key(subject)
    return bool(subj) and subj == draft.subject and bool(want & have)


def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:24]


def _key(org: str, thread: str, account_id: str, member: str) -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, _hash(thread), _hash(account_id), _hash(member))


def get_tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis as _client

    return _client()


def _drafts_of(raw: Any) -> list[ChatDraft]:
    """The drafts of a stored record. A value that does not parse is none."""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    try:
        rows = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    out: list[ChatDraft] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            out.append(ChatDraft(str(row["id"]).strip(), _addresses(row.get("to")),
                                 _subject_key(row.get("subject"))))
    return out


def _parts(org: str, thread: str, account_id: str, member: str) -> bool:
    return all(str(v or "").strip() for v in (org, thread, account_id, member))


async def _read(org: str, thread: str, account_id: str, member: str) -> list[ChatDraft]:
    from acb_common.tenant_redis import organization_scope

    with organization_scope(org):
        raw = await asyncio.wait_for(
            get_tenant_redis().get(_key(org, thread, account_id, member)),
            timeout=REDIS_TIMEOUT_S)
    return _drafts_of(raw)


async def earlier_draft(
    *, thread: str, account_id: str, member: str, to: Iterable[Any] | None,
    subject: Any, org: object = None,
) -> str:
    """The id of the newest draft that this chat saved for the same email,
    or "". A Redis fault gives "", so the save makes a new draft."""
    org_id = _org(org)
    if not _parts(org_id, thread, account_id, member):
        return ""
    try:
        drafts = await _read(org_id, thread, account_id, member)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("card_revise.record_read_failed", error=type(exc).__name__)
        return ""
    for draft in reversed(drafts):
        if same_mail(draft, to, subject):
            return draft.draft_id
    return ""


async def note_draft(
    *, thread: str, account_id: str, member: str, draft_id: str,
    to: Iterable[Any] | None, subject: Any, replaces: str = "",
    only_if_known: bool = False, org: object = None,
) -> None:
    """Write *draft_id* into the record, as the newest draft of this chat.

    *replaces* names a draft id that leaves the record, such as a draft that
    was gone. With *only_if_known*, the record changes only when it already
    holds *draft_id* or *replaces*: ``edit_draft`` of any other draft never
    joins it. A Redis fault writes nothing and never raises."""
    org_id = _org(org)
    did = str(draft_id or "").strip()
    if not (did and _parts(org_id, thread, account_id, member)):
        return
    try:
        from acb_common.tenant_redis import organization_scope

        drafts = await _read(org_id, thread, account_id, member)
        gone = {did, str(replaces or "").strip()} - {""}
        if only_if_known and all(d.draft_id not in gone for d in drafts):
            return
        rows = [{"id": d.draft_id, "to": list(d.to), "subject": d.subject}
                for d in drafts if d.draft_id not in gone]
        rows.append({"id": did, "to": list(_addresses(to)), "subject": _subject_key(subject)})
        with organization_scope(org_id):
            await asyncio.wait_for(
                get_tenant_redis().set(_key(org_id, thread, account_id, member),
                                       json.dumps(rows[-MAX_DRAFTS:]), ex=TTL_S),
                timeout=REDIS_TIMEOUT_S)
    except Exception as exc:  # the class only
        _log.warning("card_revise.record_write_failed", error=type(exc).__name__)


def _gone(exc: BaseException) -> bool:
    """True for a 404 of the save: the draft is not in Drafts now. The agent's
    ``GatewayError`` says ``status``, and the gateway's ``HTTPException``
    says ``status_code``."""
    return 404 in (getattr(exc, "status", None), getattr(exc, "status_code", None))


@dataclass(frozen=True)
class SavedDraft:
    """The answer of the save, and the id of the draft that it updated, or ""."""

    result: dict[str, Any]
    updated: str = ""


async def save_one_draft(
    put: Callable[[dict[str, Any]], Awaitable[Any]], payload: dict[str, Any], *,
    thread: str, member: str, org: object = None,
) -> SavedDraft:
    """Save a NEW draft of a chat, or update the draft of the same email that
    this chat saved before. THE one-draft rule (task 3814).

    *put* saves *payload* (``PUT /email/drafts``, or the gateway's
    ``upsert_draft``) and answers the saved draft, with its ``id``. With the
    flag off, no thread or no member, it saves *payload* as before. Else an
    earlier draft of the same email takes the save, by its ``draft_id``. A
    404 for it saves a new draft, and the record takes the new id in its
    place. Each other failure of *put* raises, as before."""
    account_id = str(payload.get("account_id") or "")
    org_id = _org(org)
    if not (card_revise_on(org_id) and _parts(org_id, thread, account_id, member)):
        return SavedDraft(dict(await put(payload) or {}))
    to, subject = payload.get("to"), payload.get("subject")
    earlier = await earlier_draft(thread=thread, account_id=account_id, member=member,
                                  to=to, subject=subject, org=org_id)
    res: dict[str, Any] | None = None
    if earlier:
        try:
            res = dict(await put({**payload, "draft_id": earlier}) or {})
        except Exception as exc:
            if not _gone(exc):
                raise
            _log.info("card_revise.earlier_draft_gone")
    updated = earlier if res is not None else ""
    if res is None:
        res = dict(await put(payload) or {})
    await note_draft(thread=thread, account_id=account_id, member=member,
                     draft_id=str(res.get("id") or updated), to=to, subject=subject,
                     replaces=earlier, org=org_id)
    return SavedDraft(res, updated)
