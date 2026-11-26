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
draft row starts unticked (:func:`draft_row`). The answer
(:func:`result_text`) counts only what left Drafts, from a read after the act,
and names each matching draft that stays.

Spec: ``email_app_master_plan.md`` §18.13.
Fence: ``tests/unit/test_email_draft_choice.py``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
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


def listing(drafts: Iterable[Mapping[str, Any]], clean: Any) -> list[str]:
    """One line for each draft, with its id, for the answer."""
    lines = []
    for draft in drafts:
        label, facts = describe(draft)
        lines.append(f"- draft id={_id(draft)} | {clean(label, 200)} | {clean(facts, 300)}")
    return lines


def result_text(
    *, drafts_moved: int, others_moved: int, drafts_failed: int,
    remaining: list[str] | None, cut: bool,
) -> str:
    """The answer of a trash that checked Drafts.

    The first line counts what went, and ``Nothing changed.`` starts it when
    nothing went (the chat card reads both heads). *remaining* is the listing
    of each matching draft still in Drafts, or None when the read after the act
    failed. *cut* says that the Drafts list was longer than one page."""
    total = drafts_moved + others_moved
    if total:
        parts = [f"{drafts_moved} draft(s)"]
        if others_moved:
            parts.append(f"{others_moved} other message(s)")
        head = f"Trashed {total} message(s): {' and '.join(parts)}."
    else:
        head = "Nothing changed. No message was moved to Trash."
    lines = [head]
    if drafts_failed:
        lines.append(
            f"{drafts_failed} draft(s) did not move. Do not say that they were deleted.")
    if remaining is None:
        lines.append(
            "Metorite could not read Drafts after the act. Do not say that no draft is left.")
    elif remaining:
        lines.append(f"{len(remaining)} matching draft(s) are still in Drafts:")
        lines += remaining[:MAX_DRAFTS]
        if len(remaining) > MAX_DRAFTS:
            lines.append(f"+{len(remaining) - MAX_DRAFTS} more")
    elif not cut:
        lines.append("No matching draft is left in Drafts, as Metorite last synced it.")
    if cut:
        lines.append(
            "The Drafts list is longer than one page, so more matching drafts can exist.")
    lines.append(
        "Tell the member these counts, and name each draft that stays. "
        "Say 'deleted' only for the drafts that this answer counts as trashed.")
    return "\n".join(lines)
