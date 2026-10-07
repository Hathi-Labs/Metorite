"""Automation · the copy of rules from one mailbox to another (WS-17 EM-T8f-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 item
1, and D-EM-6, D-EM-18, D-EM-24 and D-EM-29 in §11.2. ``will_create`` and
rule 5 are WS-17 EM-S10 (§14.4.7, §14.6.10, D-EM-60).

``POST /email/rules/copy`` copies the ENABLED rules of one mailbox of the
member to another mailbox of the same member. A copy is made once, and after
it the two sets change apart (D-EM-24).

What a copy takes:

* Each enabled rule, with each column of ``RULE_COPY_COLUMNS``. The copy
  writes ``id``, ``account_id`` and ``name`` itself, and it takes no
  ``organization_id`` (the tenant default) and no timestamp. So each copy
  gets ``created_at = now()``, and the new-mail floor of the target
  (``rules.NEW_MAIL_FLOOR_SQL``) never moves back into its imported mail.
* Each action of a copied rule, with each column of ``ACTION_COPY_COLUMNS``,
  in the order of its source.

What a copy does not take: a disabled rule, a rule pattern, rule guidance, a
learned pattern, an assistant setting, the voice profile or knowledge. A
mailbox is the boundary of the AI context (D-EM-18).

Five rules change what arrives:

1. A name that the target holds, in any case, gets " (copy)", then
   " (copy 2)", and so on. The INSERT also has ``ON CONFLICT (account_id,
   name) DO NOTHING``, so a name that another writer takes during the copy
   moves the copy to the next name. A copy never fails on the unique name.
2. The target keeps ONE reply rule at most (``rules._is_reply_rule``). A
   source reply rule is left out with the reason ``reply_rule_exists`` when
   the target holds a reply rule, or when the copy already took one. The
   "Auto draft replies" switch edits only the first reply rule
   (``rules.sync_draft_reply_action``). A second one, for example
   "Needs Reply (copy)", would go on drafting while the switch shows OFF
   (D-EM-6). "Add defaults" refuses the same case (``_seed_preset_rules``).
   A reply rule is never renamed. When its own name is taken, at the read or
   at the INSERT, it is left out as ``reply_rule_exists`` too. So a writer
   that commits "Needs Reply" between the read and the INSERT cannot make the
   copy land as "Needs Reply (copy)" (review round 2). Residual: a writer
   that commits a reply rule under ANOTHER name in that window still gives
   two reply rules. Only a lock that "Add defaults" also takes can close it.
3. A reply rule keeps DRAFT_EMAIL only when the stored ``draft_replies`` of
   the TARGET is true (D-EM-6). Any other rule keeps its DRAFT_EMAIL, because
   the member put it there and the switch does not govern it.
4. A rule with a FORWARD to an address of a mailbox of the member is left out
   whole, and the answer names it (D-EM-29). Until EM-T8g ships the loop
   guard, such a forward can send mail around in a loop. A copy without the
   FORWARD would keep the other actions of the rule, for example an ARCHIVE,
   and would hide mail that the member meant to forward.
5. A rule with a MOVE_FOLDER that the provider of the target refuses is left
   out whole, as ``folder_not_in_target`` (EM-S10). Gmail refuses a move to
   Sent, Drafts and a system label. The check is ``local_folder_after_move``
   over a probe of the target's provider kind. The probe holds no member
   credential. The deployment OAuth app config is loaded, and no network call
   is made. So each provider keeps its one rule. A copy without the move would
   keep the other actions, and would not file the mail.
6. A rule with a MOVE_FOLDER or a LABEL is left out whole when the target is
   an IMAP mailbox, as ``not_supported_by_target`` (EM-S10 fix round 1). The
   IMAP provider has no folder create, no move and no label write, so the
   rule could never do what it says there.

The answer names each folder or label that Metorite has no record of in the
target, in ``will_create`` (EM-S10, D-EM-60). The copy still copies the rule.
When the mailbox does not have the name, the provider makes it on first use.
When it has it, the provider uses it. ``email_folders`` has one writer only
(``transport/folders.py``), so a name in the list can exist at the provider.
An IMAP target answers an empty list, because rule 6 copies no rule that
names a folder there. The compare reads
``email_folders`` of the TARGET only, without case, over the ``account_id``
that the owner check proved. An AI label (``label_ai``) is a prompt and not a
name, so the list leaves it out. A MOVE_FOLDER to a system folder (Inbox,
Archive) is never in the list, because each mailbox holds one.

A new column of ``email_rules`` or ``email_actions`` must join a tuple below.
``tests/unit/test_email_rule_copy.py`` reads ``information_schema`` and fails
when a column is in no tuple.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from acb_auth import UserContext, get_current_user
from email_ingestion.providers.base import canonical_folder, local_folder_after_move
from email_ingestion.providers.factory import build_provider
from fastapi import Depends, HTTPException
from gateway.routes.email.automation.actions import SYSTEM_FOLDER_KEYS
from gateway.routes.email.automation.rules import (
    _is_reply_rule,
    _load_rules,
    stored_draft_replies,
)
from gateway.routes.email.core import (
    _assert_account_owner,
    _log,
    _tenant_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

#: The columns of ``email_rules`` that a copy takes from its source rule.
RULE_COPY_COLUMNS: tuple[str, ...] = (
    "instructions", "enabled", "automated", "run_on_threads",
    "conditional_operator", "from_pattern", "to_pattern", "subject_pattern",
    "body_pattern", "category_filter_type", "category_filters", "system_type",
)
#: The columns of ``email_rules`` that the copy writes itself.
RULE_COPY_WRITTEN: tuple[str, ...] = ("id", "account_id", "name")
#: The columns of ``email_rules`` that a copy never takes. The tenant default
#: fills ``organization_id``, and ``now()`` fills the two timestamps.
RULE_COPY_SKIPPED: tuple[str, ...] = ("organization_id", "created_at", "updated_at")

#: The columns of ``email_actions`` that a copy takes from its source action.
ACTION_COPY_COLUMNS: tuple[str, ...] = (
    "type", "label", "subject", "content", "to_address", "cc_address",
    "bcc_address", "url", "delay_minutes", "attachments", "label_ai",
    "content_manual",
)
#: The columns of ``email_actions`` that the copy writes itself.
ACTION_COPY_WRITTEN: tuple[str, ...] = ("rule_id",)
#: The columns of ``email_actions`` that a copy never takes.
ACTION_COPY_SKIPPED: tuple[str, ...] = ("id", "organization_id", "created_at")

#: One address token in a recipient field: no space, no bracket, no quote and
#: no list separator on either side of the ``@``.
_ADDRESS = re.compile(r"[^\s<>,;\"'()]+@[^\s<>,;\"'()]+")

#: The reasons of ``left_out``.
LEFT_OUT_DISABLED = "disabled"
LEFT_OUT_FORWARD_LOOP = "forward_to_own_address"
LEFT_OUT_REPLY_EXISTS = "reply_rule_exists"
LEFT_OUT_FOLDER_NOT_IN_TARGET = "folder_not_in_target"
LEFT_OUT_NOT_SUPPORTED_BY_TARGET = "not_supported_by_target"

#: The provider kinds that make no folder, move no mail and write no label
#: (rule 6). ``providers/imap.py`` has none of those writes.
_PROVIDERS_WITHOUT_FOLDERS = frozenset({"imap"})

#: The action types whose ``label`` names a folder or a label (EM-S10).
_NAMED_ACTIONS = frozenset({"MOVE_FOLDER", "LABEL"})

#: The most names one rule tries. Each try that fails means that a row with
#: that name exists, so a real copy never gets near it. When the source rule
#: goes during the copy, the INSERT finds no row, and the bound ends the loop.
_MAX_NAME_TRIES = 50

_RULE_COLS = ", ".join(RULE_COPY_COLUMNS)
_ACTION_COLS = ", ".join(ACTION_COPY_COLUMNS)

#: One rule. The SELECT names the source mailbox and ``enabled`` again, so a
#: rule that left or changed since the read is not copied.
_COPY_RULE_SQL = f"""
    INSERT INTO email_rules (id, account_id, name, {_RULE_COLS})
    SELECT CAST(:new_id AS uuid), CAST(:dst AS uuid), CAST(:name AS text),
           {_RULE_COLS}
      FROM email_rules
     WHERE id = CAST(:src_rule AS uuid)
       AND account_id = CAST(:src AS uuid)
       AND enabled
    ON CONFLICT (account_id, name) DO NOTHING
    RETURNING id
"""

#: The actions of one rule, in their order (``rules._ACCOUNT_ACTIONS_SQL``
#: reads ``created_at, ctid``). ``:keep_draft`` false leaves out DRAFT_EMAIL.
_COPY_ACTIONS_SQL = f"""
    INSERT INTO email_actions (rule_id, {_ACTION_COLS})
    SELECT CAST(:new_id AS uuid), {_ACTION_COLS}
      FROM email_actions
     WHERE rule_id = CAST(:src_rule AS uuid)
       AND (CAST(:keep_draft AS boolean) OR UPPER(type) <> 'DRAFT_EMAIL')
     ORDER BY created_at, ctid
"""


class RuleCopyRequest(BaseModel):
    """The two mailboxes. Each must be a mailbox of the member."""
    from_account_id: UUID
    to_account_id: UUID


class RuleCopyRename(BaseModel):
    """A rule whose name the target held."""
    name: str
    copied_as: str


class RuleCopyLeftOut(BaseModel):
    """A rule that the copy did not take, and why."""
    name: str
    reason: str


class RuleCopyWillCreate(BaseModel):
    """A folder or label name that a copied rule uses and the target lacks.

    ``rule`` is the name of the rule in the target. ``action`` is
    ``MOVE_FOLDER`` or ``LABEL``. ``name`` is the name as the rule holds it.
    """
    rule: str
    action: str
    name: str


class RuleCopyResult(BaseModel):
    """What arrived: the names in the target, the renames, the rules left out,
    and each folder or label name that the provider makes on first use."""
    copied: list[str]
    renamed: list[RuleCopyRename]
    left_out: list[RuleCopyLeftOut]
    will_create: list[RuleCopyWillCreate] = []


def copy_name(name: str, taken: set[str]) -> str:
    """The first free name: ``name``, then ``name (copy)``, then ``name (copy 2)``.

    ``taken`` holds lower-case names, so "X" and "x" count as one name.
    """
    if name.lower() not in taken:
        return name
    candidate, n = f"{name} (copy)", 2
    while candidate.lower() in taken:
        candidate, n = f"{name} (copy {n})", n + 1
    return candidate


def _recipients(action: dict[str, Any]) -> set[str]:
    """The lower-case addresses in the To, Cc and Bcc of one action.

    A scan for each ``x@y`` token, and not an RFC 5322 parser. The strict
    ``getaddresses`` of Python 3.12 gives no address for a field that it
    cannot parse, and the guard would then let a loop through. The scan can
    find too much, for example an address in a display name. Then the rule is
    left out, which is the safe side.
    """
    found: set[str] = set()
    for key in ("to_address", "cc_address", "bcc_address"):
        found |= {m.lower() for m in _ADDRESS.findall(action.get(key) or "")}
    return found


def forwards_to_own_address(rule: dict[str, Any], own: set[str]) -> bool:
    """True when a FORWARD of the rule names an address of a mailbox of the
    member (D-EM-29)."""
    return any(
        (a.get("type") or "").upper() == "FORWARD" and _recipients(a) & own
        for a in rule.get("actions") or []
    )


async def _own_addresses(db: Any, owner: str) -> set[str]:
    """The lower-case address of each mailbox of the member."""
    rows = (await db.execute(text(
        "SELECT email_address FROM email_accounts WHERE user_id = :uid"
    ), {"uid": owner})).fetchall()
    return {(r.email_address or "").strip().lower() for r in rows if r.email_address}


async def _target_rules(db: Any, account_id: str) -> list[dict[str, Any]]:
    """The name and the system type of each rule of the target, enabled or not.

    A disabled reply rule still counts, because a member can enable it again.
    """
    rows = (await db.execute(text(
        "SELECT name, system_type FROM email_rules "
        "WHERE account_id = CAST(:aid AS uuid)"
    ), {"aid": account_id})).fetchall()
    return [{"name": r.name or "", "system_type": r.system_type} for r in rows]


async def _target_folders(db: Any, account_id: str) -> set[str]:
    """The lower-case, trimmed name of each folder of the target in
    ``email_folders``. The caller passes the ``account_id`` that its owner
    check proved, and nothing else (R5, D-EM-4)."""
    rows = (await db.execute(text(
        "SELECT name FROM email_folders WHERE account_id = CAST(:aid AS uuid)"
    ), {"aid": account_id})).fetchall()
    return {(r.name or "").strip().lower() for r in rows if r.name}


async def _target_provider(db: Any, account_id: str, owner: str) -> str:
    """The provider of the target. The owner predicate stays in the read."""
    row = (await db.execute(text(
        "SELECT provider FROM email_accounts "
        "WHERE id = CAST(:aid AS uuid) AND user_id = :uid"
    ), {"aid": account_id, "uid": owner})).fetchone()
    return (row.provider or "") if row else ""


def move_refuser(provider_name: str) -> Callable[[str], bool]:
    """A check that is true when a provider of this kind refuses a move to
    the folder name. The probe holds no member credential. The deployment
    OAuth app config is loaded, and no network call is made. An unknown
    provider refuses nothing."""
    try:
        probe = build_provider(provider_name, {})
    except ValueError:
        return lambda _name: False
    return lambda name: local_folder_after_move(probe, (name or "").strip()) is None


def refuses_a_move(rule: dict[str, Any], refuses: Callable[[str], bool]) -> bool:
    """True when a MOVE_FOLDER of the rule names a folder that the target
    refuses (rule 5)."""
    return any(
        (a.get("type") or "").upper() == "MOVE_FOLDER"
        and bool((a.get("label") or "").strip())
        and refuses(a.get("label") or "")
        for a in rule.get("actions") or []
    )


def will_create(
    rule: dict[str, Any], copied_as: str, folders: set[str],
) -> list[RuleCopyWillCreate]:
    """Each MOVE_FOLDER or LABEL name of the rule that ``folders`` lacks.

    ``folders`` holds lower-case, trimmed names, so the compare has no case.
    An AI label is left out, and so is a move to a system folder. A name that
    two actions of one type share is listed once.
    """
    out: list[RuleCopyWillCreate] = []
    seen: set[tuple[str, str]] = set()
    for a in rule.get("actions") or []:
        kind = (a.get("type") or "").upper()
        name = (a.get("label") or "").strip()
        if kind not in _NAMED_ACTIONS or not name or a.get("label_ai"):
            continue
        if kind == "MOVE_FOLDER" and canonical_folder(name) in SYSTEM_FOLDER_KEYS:
            continue
        key = (kind, name.lower())
        if name.lower() in folders or key in seen:
            continue
        seen.add(key)
        out.append(RuleCopyWillCreate(rule=copied_as, action=kind, name=name))
    return out


def names_a_folder(rule: dict[str, Any]) -> bool:
    """True when the rule has a MOVE_FOLDER or a LABEL action (rule 6)."""
    return any((a.get("type") or "").upper() in _NAMED_ACTIONS
               for a in rule.get("actions") or [])


def _left_out_reason(
    rule: dict[str, Any], own: set[str], reply_held: bool,
    refuses: Callable[[str], bool] = lambda _name: False,
    *, target_has_folders: bool = True,
) -> str | None:
    """Why the copy leaves out this source rule, or ``None`` to copy it."""
    if not rule["enabled"]:
        return LEFT_OUT_DISABLED
    if forwards_to_own_address(rule, own):
        return LEFT_OUT_FORWARD_LOOP
    if not target_has_folders and names_a_folder(rule):
        return LEFT_OUT_NOT_SUPPORTED_BY_TARGET
    if refuses_a_move(rule, refuses):
        return LEFT_OUT_FOLDER_NOT_IN_TARGET
    if reply_held and _is_reply_rule(rule):
        return LEFT_OUT_REPLY_EXISTS
    return None


async def _copy_one(
    db: Any, rule: dict[str, Any], src: str, dst: str, taken: set[str],
    *, keep_draft: bool, rename: bool = True,
) -> str | None:
    """Copy one rule and its actions. The answer is the name in the target.

    With ``rename=False`` the rule lands under its own name or not at all,
    and the answer is ``None`` when that name is taken. The caller passes it
    for a reply rule (rule 2 of the module, review round 2). The read of the
    target holds no lock, so another writer can commit "Needs Reply" before
    the INSERT. A renamed copy, "Needs Reply (copy)", is then a second reply
    rule that ``_is_reply_rule`` cannot see.
    """
    for _try in range(_MAX_NAME_TRIES):
        name = copy_name(rule["name"], taken)
        if not rename and name != rule["name"]:
            return None
        taken.add(name.lower())
        new_id = str(uuid4())
        row = (await db.execute(text(_COPY_RULE_SQL), {
            "new_id": new_id, "dst": dst, "name": name,
            "src_rule": rule["id"], "src": src,
        })).fetchone()
        if row is not None:
            await db.execute(text(_COPY_ACTIONS_SQL), {
                "new_id": new_id, "src_rule": rule["id"],
                "keep_draft": keep_draft,
            })
            return name
    # The exception rolls back the whole copy, so nothing arrives in part.
    raise HTTPException(
        status_code=409,
        detail="The rules changed during the copy. Try again.",
    )


@router.post("/rules/copy", response_model=RuleCopyResult)
async def copy_rules(
    req: RuleCopyRequest,
    user: UserContext = Depends(get_current_user),
) -> RuleCopyResult:
    """Copy the enabled rules of one mailbox of the member to another.

    Each id must name a mailbox of the member, else 404, and the same id
    twice is 422. The module docstring holds the rules of the copy. The copy
    is one transaction, so it lands whole or not at all.
    """
    src, dst = str(req.from_account_id), str(req.to_account_id)
    if src == dst:
        raise HTTPException(
            status_code=422, detail="Choose two different mailboxes.")
    owner = user.email or "anonymous"
    copied: list[str] = []
    renamed: list[RuleCopyRename] = []
    left_out: list[RuleCopyLeftOut] = []
    to_create: list[RuleCopyWillCreate] = []
    async with _tenant_session() as db:
        await _assert_account_owner(db, src, owner)
        await _assert_account_owner(db, dst, owner)
        source_rules = await _load_rules(db, src)
        target = await _target_rules(db, dst)
        taken = {r["name"].lower() for r in target}
        reply_held = any(_is_reply_rule(r) for r in target)
        target_drafts = await stored_draft_replies(db, dst)
        own = await _own_addresses(db, owner)
        # EM-S10: both reads use ``dst``, which the owner check above proved.
        folders = await _target_folders(db, dst)
        provider = await _target_provider(db, dst, owner)
        refuses = move_refuser(provider)
        has_folders = provider not in _PROVIDERS_WITHOUT_FOLDERS
        for rule in source_rules:
            reason = _left_out_reason(rule, own, reply_held, refuses,
                                      target_has_folders=has_folders)
            if reason is not None:
                left_out.append(RuleCopyLeftOut(name=rule["name"], reason=reason))
                continue
            is_reply = _is_reply_rule(rule)
            name = await _copy_one(
                db, rule, src, dst, taken,
                keep_draft=target_drafts or not is_reply, rename=not is_reply)
            if name is None:
                left_out.append(RuleCopyLeftOut(
                    name=rule["name"], reason=LEFT_OUT_REPLY_EXISTS))
                continue
            reply_held = reply_held or is_reply
            copied.append(name)
            # An IMAP target copies no rule with a MOVE_FOLDER or a LABEL
            # (rule 6), so its list stays empty with no second check here.
            to_create.extend(will_create(rule, name, folders))
            if name != rule["name"]:
                renamed.append(RuleCopyRename(name=rule["name"], copied_as=name))
    _log.info("email.rules.copied", from_account_id=src, to_account_id=dst,
              copied=len(copied), renamed=len(renamed), left_out=len(left_out),
              will_create=len(to_create))
    return RuleCopyResult(copied=copied, renamed=renamed, left_out=left_out,
                          will_create=to_create)
