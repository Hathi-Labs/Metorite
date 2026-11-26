"""The chat asks which draft, and answers what stays (tasks 3977 and 3978).

Production, 2026-11-26. A mail thread held two drafts: one that the chat saved
and one that the member made. The member asked "delete the draft in my Drafts
folder". The chat called ``manage_inbox(action="trash")`` with the id of its own
draft only, and said "The draft is deleted". The other draft stayed in Drafts.

This module holds the pure part of the fix. The gateway reads and writes stay
in the email agent. Everything here is dark behind ONE flag,
``CHAT_DRAFT_CHOICE_ORGS``. Off, each card and each answer is as before.

What "a matching draft" means (:func:`matching`): a draft of the SAME mailbox
that shares the thread of a named draft, or its To set, or its subject
(``card_revise.subject_key``: no "Re:" head, any case). The match reads the
Drafts folder, so a draft that the member made counts too.

The card never ticks a draft for the member when the pick is not clear: each
draft row starts unticked (:func:`draft_row`). The answer (:func:`report`)
counts a message as trashed only when the mailbox provider accepted the trash,
and a draft only when it also left Drafts in a read after the act. It names
each message that it counts, and each matching draft that stays.

Spec: ``email_app_master_plan.md`` §18.13.
Fence: ``tests/unit/test_email_draft_choice.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from acb_skills.card_revise import subject_key

#: The folders of the mirror that hold a draft (``drafting._CARD_DRAFT_SQL``).
DRAFT_FOLDERS = ("drafts", "draft")
#: The folders of a draft that already left Drafts through a trash.
TRASH_FOLDERS = ("trash", "deleteditems")
#: The most ids of one call that the tool reads for the check. A larger call is
#: a clean-up of mail, and it keeps the card of before.
MAX_IDS = 20
#: The most draft rows on one card, and in one answer.
MAX_DRAFTS = 20
#: The page of the Drafts read, the most that ``GET /email/messages`` gives.
PAGE_SIZE = 200


def draft_choice_on(org: object = None) -> bool:
    """True when ``CHAT_DRAFT_CHOICE_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant. A comma list of org ids, or ``*`` for
    each one. Empty means off, the default. It reads the settings on each call,
    so a flag reload (``docs/flag_reload.md``) applies a change with no
    restart. It fails closed."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_draft_choice_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if not org:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return bool(org) and str(org).strip().lower() in entries


def folder_of(message: Mapping[str, Any]) -> str:
    return str(message.get("folder") or "").strip().lower()


def is_draft(message: Mapping[str, Any]) -> bool:
    return folder_of(message) in DRAFT_FOLDERS


def is_trashed(message: Mapping[str, Any]) -> bool:
    return folder_of(message) in TRASH_FOLDERS


def to_addresses(message: Mapping[str, Any]) -> tuple[str, ...]:
    """The To addresses of a message row, lower case, each once, sorted."""
    value = message.get("to_addresses")
    items = value if isinstance(value, list) else []
    found = (v.get("email") if isinstance(v, Mapping) else v for v in items)
    return tuple(sorted({str(a).strip().lower() for a in found if a and str(a).strip()}))


def _id(message: Mapping[str, Any]) -> str:
    return str(message.get("id") or "")


def _same(draft: Mapping[str, Any], anchor: Mapping[str, Any]) -> bool:
    if str(draft.get("account_id") or "") != str(anchor.get("account_id") or ""):
        return False
    thread = str(anchor.get("thread_id") or "")
    if thread and str(draft.get("thread_id") or "") == thread:
        return True
    people = to_addresses(anchor)
    if people and to_addresses(draft) == people:
        return True
    subject = subject_key(anchor.get("subject"))
    return bool(subject) and subject_key(draft.get("subject")) == subject


def matching(
    drafts: Iterable[Mapping[str, Any]], anchors: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Each draft of *drafts* that is an anchor, or matches one, once, in the
    order of *drafts*. An anchor is a draft that the call names, or one that
    the call names and that is already in Trash."""
    anchors = list(anchors)
    named = {_id(a) for a in anchors}
    out: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        if not isinstance(draft, Mapping) or not is_draft(draft) or not _id(draft):
            continue
        if _id(draft) in named or any(_same(draft, a) for a in anchors):
            out.setdefault(_id(draft), dict(draft))
    return list(out.values())


def _recipients(draft: Mapping[str, Any]) -> str:
    people = to_addresses(draft)
    if not people:
        return "no recipient"
    more = f" +{len(people) - 3} more" if len(people) > 3 else ""
    return ", ".join(people[:3]) + more


def describe(draft: Mapping[str, Any]) -> tuple[str, str]:
    """``(label, facts)`` of one draft: who and what, then when and how."""
    subject = " ".join(str(draft.get("subject") or "").split()) or "(no subject)"
    label = f"To {_recipients(draft)}: {subject}"
    when = str(draft.get("received_at") or "")[:16].replace("T", " ")
    text = " ".join(str(draft.get("snippet") or draft.get("body_text") or "").split())
    facts = [f"saved {when}" if when else "", "has files" if draft.get("has_attachments") else "",
             f"starts: {text[:80]}" if text else "no text"]
    return label, " · ".join(f for f in facts if f)


def draft_row(draft: Mapping[str, Any], clean: Any) -> dict[str, Any]:
    """One unticked card row of *draft*. *clean* makes each value card-safe."""
    label, facts = describe(draft)
    return {"id": _id(draft), "label": clean(label, 200), "hint": clean(facts, 300),
            "checked": False}


def provider_line(providers: Iterable[str]) -> str:
    """What a trash of a draft does at each provider of the card."""
    kinds = {str(p or "").lower() for p in providers}
    lines = []
    if any("gmail" in k or "google" in k for k in kinds):
        lines.append("Gmail deletes a trashed draft for good.")
    if any(("microsoft" in k or "outlook" in k) for k in kinds):
        lines.append("Outlook moves a trashed draft to Deleted Items.")
    if not lines:
        lines.append("A trashed draft leaves Drafts.")
    return " ".join(lines)


#: The outcomes of one trash write (:func:`settle`).
CONFIRMED = "confirmed"          # the provider accepted the trash
NOT_CONFIRMED = "not_confirmed"  # Metorite marked it, the provider did not say yes
FAILED = "failed"                # the gateway refused, so nothing moved
LOST = "lost"                    # the answer did not come back


def message_label(message: Mapping[str, Any]) -> str:
    """"From sender: subject" of a named message that is no draft."""
    sender = " ".join(str(message.get("from") or message.get("from_email") or "").split())
    subject = " ".join(str(message.get("subject") or "").split()) or "(no subject)"
    return f"From {sender or '(unknown sender)'}: {subject}"


def unique(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """*pairs* of ``(label, hint)``, where a repeat gets "copy 2", "copy 3".
    So no two rows, and no two lines of a card, read the same."""
    seen: dict[tuple[str, str], int] = {}
    out = []
    for label, hint in pairs:
        n = seen.get((label, hint), 0) + 1
        seen[(label, hint)] = n
        if n > 1:
            hint = f"{hint} · copy {n}" if hint else f"copy {n}"
        out.append((label, hint))
    return out


def first_named(
    named: list[Mapping[str, Any]], matches: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The named drafts first, in the order of the call, then each other
    match. The row of the Drafts read wins over the row of the first read,
    and a named draft that the Drafts page did not hold stays in."""
    by_id = {_id(m): m for m in matches}
    out = [dict(by_id.get(_id(m)) or m) for m in named]
    ids = {_id(m) for m in out}
    out += [dict(m) for m in matches if _id(m) not in ids]
    return out


def _draft_line(draft: Mapping[str, Any], clean: Any) -> str:
    label, facts = describe(draft)
    return f"- draft id={_id(draft)} | {clean(label, 200)} | {clean(facts, 300)}"


def _mail_line(message: Mapping[str, Any], clean: Any) -> str:
    return f"- message id={_id(message)} | {clean(message_label(message), 200)}"


def listing(drafts: Iterable[Mapping[str, Any]], clean: Any) -> list[str]:
    """One line for each draft, with its id, for the answer."""
    return [_draft_line(d, clean) for d in drafts]


@dataclass
class Tally:
    """What one trash did, for :func:`report`. Each list holds message rows.

    ``remaining`` is each matching draft still in Drafts, or None when the
    read after the act failed. ``cut`` says that a Drafts page was full."""

    drafts: list[dict[str, Any]] = field(default_factory=list)
    others: list[dict[str, Any]] = field(default_factory=list)
    drafts_unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    others_unconfirmed: list[dict[str, Any]] = field(default_factory=list)
    drafts_failed: list[dict[str, Any]] = field(default_factory=list)
    others_failed: list[dict[str, Any]] = field(default_factory=list)
    lost: list[dict[str, Any]] = field(default_factory=list)
    remaining: list[dict[str, Any]] | None = field(default_factory=list)
    cut: bool = False
    already: list[dict[str, Any]] = field(default_factory=list)
    unknown_ids: int = 0


def settle(
    tally: Tally, row: dict[str, Any], outcome: str, *, draft: bool,
    still: set[str] | None,
) -> None:
    """Put one written row in its list of *tally*. *still* is the ids still in
    Drafts after the act, or None when that read failed. A draft counts as
    trashed only when the provider accepted it AND it left Drafts."""
    in_drafts = still is not None and _id(row) in still
    if draft and in_drafts and outcome != LOST:
        tally.drafts_failed.append(row)
    elif outcome == CONFIRMED:
        (tally.drafts if draft else tally.others).append(row)
    elif outcome == NOT_CONFIRMED:
        (tally.drafts_unconfirmed if draft else tally.others_unconfirmed).append(row)
    elif outcome == LOST and not in_drafts:
        tally.lost.append(row)
    else:
        (tally.drafts_failed if draft else tally.others_failed).append(row)


def _head(t: Tally) -> str:
    total = len(t.drafts) + len(t.others)
    if total:
        parts = [f"{len(t.drafts)} draft(s)"] if t.drafts else []
        if t.others:
            parts.append(f"{len(t.others)} other message(s)")
        return f"Trashed {total} message(s): {' and '.join(parts)}."
    if t.drafts_unconfirmed or t.others_unconfirmed or t.lost:
        return "Trashed 0 message(s): the mailbox did not confirm a trash."
    return "Nothing changed. No message was moved to Trash."


def _notes(t: Tally) -> list[str]:
    notes = []
    if t.drafts_unconfirmed:
        notes.append(
            f"The mailbox provider did not confirm the trash of {len(t.drafts_unconfirmed)} "
            "draft(s). Metorite shows them in Trash, but the next sync can bring them back "
            "to Drafts. Do not say that they were deleted.")
    if t.others_unconfirmed:
        notes.append(
            f"The mailbox provider did not confirm the trash of {len(t.others_unconfirmed)} "
            "other message(s). The next sync can bring them back to their folder.")
    if t.drafts_failed:
        notes.append(
            f"{len(t.drafts_failed)} draft(s) did not move. Do not say that they were deleted.")
    if t.others_failed:
        notes.append(
            f"{len(t.others_failed)} other message(s) did not move to Trash. They stay where "
            "they were.")
    if t.lost:
        notes.append(
            f"Metorite got no answer for {len(t.lost)} message(s). They can be in Trash or "
            "not. Do not say that they were deleted.")
    if t.already:
        notes.append(f"{len(t.already)} message(s) that you named were already in Trash.")
    if t.unknown_ids:
        notes.append(
            f"{t.unknown_ids} id(s) name no message of yours. Nothing happened to them.")
    if t.remaining is None:
        notes.append(
            "Metorite could not read Drafts after the act. Do not say that no draft is left.")
    elif not t.remaining and not t.cut:
        notes.append("No matching draft is left in Drafts, as Metorite last synced it.")
    if t.cut:
        notes.append(
            "The Drafts list is longer than one page, so more matching drafts can exist.")
    return notes


def _section(title: str, lines: list[str]) -> list[str]:
    if not lines:
        return []
    out = [title, *lines[:MAX_DRAFTS]]
    if len(lines) > MAX_DRAFTS:
        out.append(f"+{len(lines) - MAX_DRAFTS} more")
    return out


def report(t: Tally, clean: Any) -> tuple[list[str], list[str], list[str]]:
    """``(head, data, tail)`` of the answer of a trash that checked Drafts.

    *head* counts only what the provider accepted, and ``Nothing changed.``
    starts it when no write ran (the chat card reads both heads). *data* names
    each message with its id, and the caller frames it as untrusted text.
    *tail* tells the model how to speak."""
    remaining = t.remaining or []
    staying = {_id(d) for d in remaining}
    data = _section("Trashed:", [_draft_line(d, clean) for d in t.drafts]
                    + [_mail_line(m, clean) for m in t.others])
    data += _section("Not confirmed by the mailbox provider:",
                     [_draft_line(d, clean) for d in t.drafts_unconfirmed]
                     + [_mail_line(m, clean) for m in t.others_unconfirmed])
    data += _section("Did not move:",
                     [_draft_line(d, clean) for d in t.drafts_failed if _id(d) not in staying]
                     + [_mail_line(m, clean) for m in t.others_failed])
    data += _section("No answer came back:", [
        (_draft_line(m, clean) if is_draft(m) else _mail_line(m, clean)) for m in t.lost])
    data += _section(f"{len(remaining)} matching draft(s) are still in Drafts:",
                     [_draft_line(d, clean) for d in remaining])
    tail = ["Tell the member these counts, and name each draft that stays. "
            "Say 'deleted' only for the messages under 'Trashed'."]
    return [_head(t), *_notes(t)], data, tail
