"""Automation · the copy of rules from one mailbox to another (WS-17 EM-T8f-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 item
1, and D-EM-6, D-EM-18, D-EM-24 and D-EM-29 in §11.2.

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

Four rules change what arrives:

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
3. A reply rule keeps DRAFT_EMAIL only when the stored ``draft_replies`` of
   the TARGET is true (D-EM-6). Any other rule keeps its DRAFT_EMAIL, because
   the member put it there and the switch does not govern it.
4. A rule with a FORWARD to an address of a mailbox of the member is left out
   whole, and the answer names it (D-EM-29). Until EM-T8g ships the loop
   guard, such a forward can send mail around in a loop. A copy without the
   FORWARD would keep the other actions of the rule, for example an ARCHIVE,
   and would hide mail that the member meant to forward.

A new column of ``email_rules`` or ``email_actions`` must join a tuple below.
``tests/unit/test_email_rule_copy.py`` reads ``information_schema`` and fails
when a column is in no tuple.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
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


class RuleCopyResult(BaseModel):
    """What arrived: the names in the target, the renames and the rules left out."""
    copied: list[str]
    renamed: list[RuleCopyRename]
    left_out: list[RuleCopyLeftOut]


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


def _left_out_reason(
    rule: dict[str, Any], own: set[str], reply_held: bool,
) -> str | None:
    """Why the copy leaves out this source rule, or ``None`` to copy it."""
    if not rule["enabled"]:
        return LEFT_OUT_DISABLED
    if forwards_to_own_address(rule, own):
        return LEFT_OUT_FORWARD_LOOP
    if reply_held and _is_reply_rule(rule):
        return LEFT_OUT_REPLY_EXISTS
    return None


async def _copy_one(
    db: Any, rule: dict[str, Any], src: str, dst: str, taken: set[str],
    *, keep_draft: bool,
) -> str:
    """Copy one rule and its actions. The answer is the name in the target."""
    for _try in range(_MAX_NAME_TRIES):
        name = copy_name(rule["name"], taken)
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
    async with _tenant_session() as db:
        await _assert_account_owner(db, src, owner)
        await _assert_account_owner(db, dst, owner)
        source_rules = await _load_rules(db, src)
        target = await _target_rules(db, dst)
        taken = {r["name"].lower() for r in target}
        reply_held = any(_is_reply_rule(r) for r in target)
        target_drafts = await stored_draft_replies(db, dst)
        own = await _own_addresses(db, owner)
        for rule in source_rules:
            reason = _left_out_reason(rule, own, reply_held)
            if reason is not None:
                left_out.append(RuleCopyLeftOut(name=rule["name"], reason=reason))
                continue
            is_reply = _is_reply_rule(rule)
            name = await _copy_one(
                db, rule, src, dst, taken,
                keep_draft=target_drafts or not is_reply)
            reply_held = reply_held or is_reply
            copied.append(name)
            if name != rule["name"]:
                renamed.append(RuleCopyRename(name=rule["name"], copied_as=name))
    _log.info("email.rules.copied", from_account_id=src, to_account_id=dst,
              copied=len(copied), renamed=len(renamed), left_out=len(left_out))
    return RuleCopyResult(copied=copied, renamed=renamed, left_out=left_out)
