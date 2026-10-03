"""Automation · rule configuration — Rule models, CRUD, presets, NL rule
generation, reordering, and rule-pattern/feedback management."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query, status
from gateway.routes.email.automation.identity import resolve_self
from gateway.routes.email.automation.senders import DISPOSED_FOLDERS
from gateway.routes.email.core import (
    _assert_account_owner,
    _assert_mail_in_mailbox,
    _assert_thread_in_mailbox,
    _tenant_session,
    _llm_json,
    _log,
    provider_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


class RuleActionAttachment(BaseModel):
    """A draft attachment sourced from the email-assistant workspace.

    ``path`` is the workspace-relative path (e.g. ``agent-data/budget.pdf``)
    the file was uploaded to / picked from; ``name`` is the display name.
    ``ai_selected`` marks sources the assistant may pick from at draft time
    rather than always attaching."""
    path: str | None = None
    artifact_id: str | None = None
    name: str | None = None
    ai_selected: bool = False


class RuleActionModel(BaseModel):
    id: str | None = None
    type: str
    label: str | None = None
    subject: str | None = None
    content: str | None = None
    to_address: str | None = None
    cc_address: str | None = None
    bcc_address: str | None = None
    url: str | None = None
    # inbox-zero parity: optional per-action delay + draft attachments.
    delay_minutes: int | None = None
    attachments: list[RuleActionAttachment] = []
    # inbox-zero per-field AI-vs-manual model:
    #   label_ai       — `label` is an AI prompt ({{...}}) resolved per-email.
    #   content_manual — use the authored `content` template (else AI drafts).
    label_ai: bool = False
    content_manual: bool = False


class RuleModel(BaseModel):
    id: str | None = None
    account_id: str
    name: str
    instructions: str | None = None
    enabled: bool = True
    automated: bool = True
    run_on_threads: bool = False
    conditional_operator: str = "AND"
    from_pattern: str | None = None
    to_pattern: str | None = None
    subject_pattern: str | None = None
    body_pattern: str | None = None
    system_type: str | None = None
    actions: list[RuleActionModel] = []


# Canonical system-rule order (inbox-zero parity — see SYSTEM_RULE_ORDER). Rules
# are presented to the classifier and applied (multi-rule) in this fixed order,
# NOT a user-defined "priority". Matching is AI-first: the most specific rule
# wins regardless of position; order is only a deterministic, stable arrangement.
_SYSTEM_RULE_ORDER = [
    "REPLY", "AWAITING_REPLY", "FYI", "DONE", "NEWSLETTER",
    "MARKETING", "CALENDAR", "RECEIPT", "NOTIFICATION", "COLD_EMAIL",
]


def _canonical_rank(rule: dict[str, Any]) -> int:
    """Index of a rule in the fixed system order. Falls back to the rule name
    (so seeded presets without an explicit system_type still sort correctly);
    custom rules sort after all system rules."""
    key = (rule.get("system_type") or "").upper().strip()
    if not key:
        key = (rule.get("name") or "").upper().strip().replace(" ", "_")
    try:
        return _SYSTEM_RULE_ORDER.index(key)
    except ValueError:
        return len(_SYSTEM_RULE_ORDER)


def _sort_rules_canonical(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order rules exactly like inbox-zero's sortRulesForAutomation: enabled
    first, then the fixed system-rule order, then alphabetically by name."""
    return sorted(rules, key=lambda r: (
        0 if r.get("enabled") else 1,
        _canonical_rank(r),
        (r.get("name") or "").lower(),
        (r.get("instructions") or "").lower(),
    ))


#: The new-mail floor of a mailbox (owner decision (d), 2026-10-02): the
#: creation time of its oldest ENABLED rule. The automatic paths touch only
#: mail that arrived at or after it. Older mail changes only through "Process
#: past emails". NULL when no rule is enabled, so a `>=` against it selects
#: nothing. Binds `:aid`. The ONE copy: `runner._NEW_MAIL_ONLY` and the Reply
#: Zero backfill read it.
NEW_MAIL_FLOOR_SQL = (
    "(SELECT MIN(r.created_at) FROM email_rules r "
    "WHERE r.account_id = :aid AND r.enabled)"
)


#: The actions of EVERY rule of one account, in one read (EM-T4e). Binds
#: ``:aid``. Before it, ``_load_rules`` read the actions once for each rule.
#: ``ORDER BY created_at, ctid`` keeps the order of the old read in each rule.
#: One write puts all the actions of a rule in one transaction, so they share
#: ``created_at``. The old read then gave them in the order of the table
#: (``ctid``), and the sort kept that order. One sort over all the rules does
#: not keep it, so ``ctid`` is named.
_ACCOUNT_ACTIONS_SQL = """
    SELECT a.rule_id, a.id, a.type, a.label, a.subject, a.content,
           a.to_address, a.cc_address, a.bcc_address, a.url,
           a.delay_minutes, a.attachments, a.label_ai, a.content_manual
      FROM email_actions a
      JOIN email_rules r ON r.id = a.rule_id
     WHERE r.account_id = :aid
     ORDER BY a.created_at, a.ctid
"""


async def _load_rules(db: Any, account_id: str) -> list[dict[str, Any]]:
    """Load rules + their actions for an account, in canonical system order.

    Two reads for any number of rules (EM-T4e): the rules, then the actions
    of all of them, which Python groups by rule. With no rule, one read.
    """
    rule_rows = (await db.execute(text(
        """SELECT id, account_id, name, instructions, enabled, automated,
                  run_on_threads, conditional_operator, from_pattern, to_pattern,
                  subject_pattern, body_pattern, system_type
           FROM email_rules WHERE account_id = :aid
           ORDER BY created_at"""
    ), {"aid": account_id})).fetchall()
    if not rule_rows:
        return []
    actions_by_rule: dict[str, list[Any]] = {}
    for a in (await db.execute(
            text(_ACCOUNT_ACTIONS_SQL), {"aid": account_id})).fetchall():
        actions_by_rule.setdefault(str(a.rule_id), []).append(a)
    rules: list[dict[str, Any]] = []
    for r in rule_rows:
        act_rows = actions_by_rule.get(str(r.id), [])
        rules.append({
            "id": str(r.id), "account_id": str(r.account_id), "name": r.name,
            "instructions": r.instructions, "enabled": r.enabled,
            "automated": r.automated,
            "run_on_threads": r.run_on_threads,
            "conditional_operator": r.conditional_operator,
            "from_pattern": r.from_pattern, "to_pattern": r.to_pattern,
            "subject_pattern": r.subject_pattern, "body_pattern": r.body_pattern,
            "system_type": r.system_type,
            "actions": [
                {"id": str(a.id), "type": a.type, "label": a.label,
                 "subject": a.subject, "content": a.content,
                 "to_address": a.to_address, "cc_address": a.cc_address,
                 "bcc_address": a.bcc_address, "url": a.url,
                 "delay_minutes": a.delay_minutes,
                 "label_ai": bool(a.label_ai),
                 "content_manual": bool(a.content_manual),
                 "attachments": a.attachments if isinstance(a.attachments, list)
                 else json.loads(a.attachments or "[]")}
                for a in act_rows
            ],
        })
    return _sort_rules_canonical(rules)


@router.get("/rules")
async def list_rules(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """List assistant rules (with actions) for an account."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        return {"rules": await _load_rules(db, account_id)}


# Default inbox-zero rule set. Each preset carries a provider-agnostic
# ``category_action`` plus a Microsoft/Outlook override (``category_action_ms``),
# mirroring upstream inbox-zero's ``categoryAction`` / ``categoryActionMicrosoft``
# (reference/.../utils/rule/consts.ts). On Outlook, "cleanup" categories file the
# mail into a same-named FOLDER; on Gmail they apply a LABEL. "Action" categories
# (Reply / Awaiting Reply / FYI / Done / Calendar) stay LABEL/category on
# both so they remain in the inbox. ``extra`` holds non-categorization actions.
#
# On Outlook a cleanup category is BOTH tagged with the category (a colored
# Outlook category == our LABEL) AND filed into a same-named FOLDER — Outlook
# keeps categories and folders independent, so the tag stays visible after the
# move. We never add ARCHIVE there: the folder move already removes the mail
# from the inbox, and a trailing archive would re-file it into Archive and undo
# the categorization. On Gmail there are no folders, so cleanup categories just
# LABEL (+ ARCHIVE for Marketing / Cold Email).
#
# category_action values: "label" | "label_archive" | "move_folder"
# (on Outlook "move_folder" expands to LABEL + MOVE_FOLDER)
#
# ``drafts_replies`` marks the ONE preset that the "Auto draft replies" switch
# governs. It carries no DRAFT_EMAIL of its own (D-EM-6: reply drafting is OFF
# for a new mailbox). ``_actions_for_preset`` adds the action only when the
# account's stored ``draft_replies`` is true, so a reset or 'Add defaults'
# keeps the rule and the switch in agreement.
_PRESET_RULES: list[dict[str, Any]] = [
    {"name": "Needs Reply", "instructions": "Emails I need to respond to.",
     "run_on_threads": True, "category_action": "label",
     "drafts_replies": True},
    {"name": "Awaiting Reply", "run_on_threads": True,
     "instructions": "Threads where I've already replied and am now waiting to "
                     "hear back from the other person.",
     "category_action": "label"},
    {"name": "Done", "run_on_threads": True,
     "instructions": "Emails I've already handled or replied to that need no "
                     "further action from me.",
     "category_action": "label"},
    {"name": "FYI", "run_on_threads": True,
     "instructions": "Important emails I should know about, but don't need to "
                     "reply to.",
     "category_action": "label"},
    {"name": "Newsletter",
     "instructions": "Newsletters: regular content from publications, blogs, or "
                     "services I've subscribed to.",
     "category_action": "label", "category_action_ms": "move_folder"},
    {"name": "Marketing",
     "instructions": "Marketing: promotional emails about products, services, "
                     "sales, or offers.",
     "category_action": "label_archive",
     "category_action_ms": "move_folder"},
    {"name": "Calendar",
     "instructions": "Calendar: any email related to scheduling, meeting "
                     "invites, or calendar notifications.",
     "category_action": "label"},
    {"name": "Receipt",
     "instructions": "Receipts: purchase confirmations, payment receipts, "
                     "transaction records or invoices.",
     "category_action": "label", "category_action_ms": "move_folder"},
    {"name": "Notification",
     "instructions": "Notifications: alerts, status updates, or system messages.",
     "category_action": "label", "category_action_ms": "move_folder"},
    {"name": "Cold Email",
     "instructions": "Cold emails: unsolicited sales pitches and outreach from "
                     "people or companies I have no prior relationship with.",
     "category_action": "label_archive",
     "category_action_ms": "move_folder"},
]


def _actions_for_preset(
    preset: dict[str, Any], provider: str, *, draft_replies: bool = False,
) -> list[dict[str, Any]]:
    """Resolve a preset's category_action into concrete actions for a provider.

    On Outlook (``provider == "microsoft"``) the ``category_action_ms`` override
    applies. ``move_folder`` there expands to LABEL **+** MOVE_FOLDER: Outlook
    categories (our LABEL) and folders are independent, so we tag the category
    AND file the mail into the same-named folder (the colored category survives
    the move). No ARCHIVE follows — the folder move already clears the inbox, and
    archiving would re-file the message into Archive. On Gmail (no folders) the
    base ``category_action`` (label-based) is used. ``extra`` actions append.

    ``draft_replies`` is the account's stored "Auto draft replies" choice. A
    preset marked ``drafts_replies`` gets DRAFT_EMAIL only when it is true. The
    default is False, so a caller that does not pass it drafts nothing (D-EM-6).
    """
    name = preset["name"]
    action = preset["category_action"]
    if provider == "microsoft" and preset.get("category_action_ms"):
        action = preset["category_action_ms"]
    actions: list[dict[str, Any]]
    if action == "move_folder":
        # Tag the category first (categories persist across an Outlook move),
        # then file into the folder. No archive (the move already files it).
        actions = [{"type": "LABEL", "label": name},
                   {"type": "MOVE_FOLDER", "label": name}]
    elif action == "label_archive":
        actions = [{"type": "LABEL", "label": name}, {"type": "ARCHIVE"}]
    else:  # "label" (and any unknown value) → categorize only.
        actions = [{"type": "LABEL", "label": name}]
    actions.extend(preset.get("extra", []))
    if draft_replies and preset.get("drafts_replies"):
        actions.append({"type": "DRAFT_EMAIL"})
    return actions


async def _account_provider(db: Any, account_id: str) -> str:
    """The account's mail provider ('gmail' | 'microsoft' | 'imap' | '')."""
    row = (await db.execute(
        text("SELECT provider FROM email_accounts WHERE id = :id"),
        {"id": account_id},
    )).fetchone()
    return (row.provider if row else "") or ""


async def _seed_preset_rules(
    db: Any, account_id: str, provider: str, *, skip_existing: bool,
) -> list[str]:
    """Insert the default inbox-zero rule set for an account; returns the names
    installed. With ``skip_existing`` (the additive 'Add defaults' flow) presets
    whose name already exists are left untouched; otherwise every preset is
    created. The ``provider`` decides whether cleanup categories become folders
    (Outlook) or labels (Gmail). The account's stored "Auto draft replies"
    choice decides whether Needs Reply gets DRAFT_EMAIL. A mailbox with no
    settings row gets none (D-EM-6). Caller commits."""
    draft_replies = await stored_draft_replies(db, account_id)
    existing = (
        {r["name"].lower() for r in await _load_rules(db, account_id)}
        if skip_existing else set()
    )
    # Renamed presets: an account still carrying the OLD rule name must not get
    # the new-name preset installed beside it (two conversation rules for one
    # status). Migration 92 renames stored rules, but 'Add defaults' has to be
    # safe on an un-migrated account too.
    legacy = {"needs reply": {"reply", "to reply"}, "done": {"actioned"}}
    installed: list[str] = []
    for p in _PRESET_RULES:
        key = p["name"].lower()
        if key in existing or existing & legacy.get(key, set()):
            continue
        rid = str(uuid4())
        await db.execute(text(
            """INSERT INTO email_rules
                 (id, account_id, name, instructions, run_on_threads)
               VALUES (:id, :aid, :name, :instr, :rot)"""
        ), {"id": rid, "aid": account_id, "name": p["name"],
            "instr": p["instructions"], "rot": p.get("run_on_threads", False)})
        await _replace_actions(
            db, rid,
            [RuleActionModel(**a) for a in _actions_for_preset(
                p, provider, draft_replies=draft_replies)],
        )
        installed.append(p["name"])
    return installed


@router.post("/rules/install-presets")
async def install_preset_rules(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Install the default inbox-zero-style rule set (skips ones already present
    by name). Used by the UI's 'Add defaults' and the assistant's setup flow."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        # The account's provider decides whether cleanup categories become
        # folders (Outlook) or labels (Gmail) — inbox-zero parity.
        provider = await _account_provider(db, account_id)
        installed = await _seed_preset_rules(
            db, account_id, provider, skip_existing=True)
        return {"installed": installed,
                "total_presets": len(_PRESET_RULES)}


@router.post("/rules/reset")
async def reset_rules(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Delete ALL of an account's rules and reinstall the default inbox-zero set
    fresh. Provider-aware: on Outlook the cleanup categories file mail into
    folders, on Gmail they label. Backs Settings → 'Reset rules' (the UI guards
    this destructive action behind a confirmation prompt).

    LEARNED PATTERNS SURVIVE. ``email_rule_patterns.rule_id`` is ON DELETE
    CASCADE, so dropping the rules used to silently destroy every correction the
    user had ever made (Fix, auto-learn, label-sync) — months of training gone,
    unrecoverably, behind a dialog that only mentioned rules. Patterns attached
    to a preset are carried across by NAME and re-pointed at the reseeded rule's
    new id. Patterns belonging to a custom rule the user is deleting here are
    genuinely gone with it, which is the expected meaning of 'reset'.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        provider = await _account_provider(db, account_id)
        # Snapshot the learned patterns keyed by their rule's NAME — the reseed
        # mints fresh UUIDs, so the name is the only stable join.
        saved = (await db.execute(text(
            """SELECT r.name AS rule_name, p.pattern_type, p.value, p.exclude,
                      p.source, p.reason, p.approved_at, p.rejected_at
                 FROM email_rule_patterns p
                 JOIN email_rules r ON r.id = p.rule_id
                WHERE p.account_id = :aid"""
        ), {"aid": account_id})).fetchall()

        # Drop every existing rule (actions cascade) before reseeding so stale
        # label-only rules are replaced by the current provider-aware defaults.
        await db.execute(
            text("DELETE FROM email_rules WHERE account_id = :aid"),
            {"aid": account_id})
        installed = await _seed_preset_rules(
            db, account_id, provider, skip_existing=False)

        restored = 0
        if saved:
            new_ids = {
                (r["name"] or "").lower(): r["id"]
                for r in await _load_rules(db, account_id)
            }
            for s in saved:
                rid = new_ids.get((s.rule_name or "").lower())
                if not rid:
                    continue  # belonged to a custom rule the reset removed
                await db.execute(text(
                    # Carry the review state across. Resetting the RULES
                    # must not silently un-approve patterns the user has
                    # already confirmed — nor resurrect ones they rejected.
                    """INSERT INTO email_rule_patterns
                         (account_id, rule_id, pattern_type, value, exclude,
                          source, reason, approved_at, rejected_at)
                       VALUES (:aid, :rid, :ptype, :val, :excl, :src, :reason,
                               :approved, :rejected)
                       ON CONFLICT DO NOTHING"""
                ), {"aid": account_id, "rid": rid, "ptype": s.pattern_type,
                    "val": s.value, "excl": s.exclude, "src": s.source,
                    "reason": s.reason, "approved": s.approved_at,
                    "rejected": s.rejected_at})
                restored += 1
        return {"installed": installed, "total_presets": len(_PRESET_RULES),
                "reset": True, "patterns_restored": restored}


async def _replace_actions(db: Any, rule_id: str, actions: list[RuleActionModel]) -> None:
    await db.execute(text("DELETE FROM email_actions WHERE rule_id = :rid"),
                     {"rid": rule_id})
    for a in actions:
        await db.execute(text(
            """INSERT INTO email_actions
                 (rule_id, type, label, subject, content, to_address,
                  cc_address, bcc_address, url, delay_minutes, attachments,
                  label_ai, content_manual)
               VALUES (:rid, :type, :label, :subject, :content, :to_addr,
                       :cc, :bcc, :url, :delay, CAST(:attachments AS JSONB),
                       :label_ai, :content_manual)"""
        ), {"rid": rule_id, "type": a.type, "label": a.label, "subject": a.subject,
            "content": a.content, "to_addr": a.to_address, "cc": a.cc_address,
            "bcc": a.bcc_address, "url": a.url,
            "delay": a.delay_minutes,
            "label_ai": bool(a.label_ai),
            "content_manual": bool(a.content_manual),
            "attachments": json.dumps([
                att.model_dump() for att in (a.attachments or [])
            ])})


async def stored_draft_replies(db: Any, account_id: str) -> bool:
    """The account's stored "Auto draft replies" choice.

    A mailbox with no settings row, or a NULL in the column, reads False:
    reply drafting is OFF until a member turns it on (D-EM-6). For a NEW
    mailbox this is the answer ``GET /assistant/settings`` gives. A legacy
    mailbox with no row can differ, see ``reply_rule_drafts``.
    """
    row = (await db.execute(text(
        "SELECT draft_replies FROM email_assistant_settings "
        "WHERE account_id = :aid"
    ), {"aid": account_id})).fetchone()
    # ``is True``, not ``bool()``: only a stored true turns drafting on.
    return getattr(row, "draft_replies", None) is True


# The rule that the "Auto draft replies" switch governs.
# ``sync_draft_reply_action`` edits it and ``reply_rule_drafts`` reads it, so
# both name it from here. The names cover the renames of migration 92.
_REPLY_SYSTEM_TYPES = ("REPLY", "TO_REPLY")
_REPLY_RULE_NAMES = ("needs reply", "reply", "to reply")


def _is_reply_rule(rule: dict[str, Any]) -> bool:
    return (
        (rule.get("system_type") or "").upper() in _REPLY_SYSTEM_TYPES
        or (rule.get("name") or "").strip().lower() in _REPLY_RULE_NAMES
    )


async def reply_rule_drafts(db: Any, account_id: str) -> bool:
    """True when a reply rule of the account carries a DRAFT_EMAIL action.

    This is what the engine does: a rule runs its own actions and never reads
    the setting. The GET answers this for a mailbox with NO settings row, and
    ``generate_writing_style`` stores it when it creates the row (D-EM-6, EM-T7
    fix round 1). A mailbox from before D-EM-6 got DRAFT_EMAIL from the old
    presets, so its switch reads ON until a member changes it. A new mailbox
    has no rules, so it reads False. The caller holds the tenant session and
    has proved the owner.
    """
    row = (await db.execute(text(
        """SELECT EXISTS (
             SELECT 1
               FROM email_rules r
               JOIN email_actions a ON a.rule_id = r.id
              WHERE r.account_id = :aid
                AND (UPPER(COALESCE(r.system_type, '')) = ANY(:types)
                     OR LOWER(BTRIM(r.name)) = ANY(:names))
                AND UPPER(a.type) = 'DRAFT_EMAIL'
           ) AS drafts"""
    ), {"aid": account_id, "types": list(_REPLY_SYSTEM_TYPES),
        "names": list(_REPLY_RULE_NAMES)})).fetchone()
    return getattr(row, "drafts", None) is True


async def sync_draft_reply_action(db: Any, account_id: str, enabled: bool) -> bool:
    """Mirror inbox-zero's ``enableDraftRepliesAction``: the "Auto draft replies"
    toggle adds (or removes) a ``DRAFT_EMAIL`` action on the account's "Reply"
    rule. With the action present, Reply mail gets an AI draft during the
    normal rule run (gated by ``draft_confidence``); without it, no draft —
    exactly how inbox-zero couples auto-drafting to the Reply system rule.

    Returns True if the rule's actions changed. No-ops (returns False) when the
    account has no "Reply" rule, or the action is already in the desired
    state. Caller commits.
    """
    rules = await _load_rules(db, account_id)
    target = next((r for r in rules if _is_reply_rule(r)), None)
    if not target:
        return False
    actions = target["actions"]
    has_draft = any((a.get("type") or "").upper() == "DRAFT_EMAIL" for a in actions)
    if enabled == has_draft:
        return False
    if enabled:
        actions = [*actions, {"type": "DRAFT_EMAIL"}]
    else:
        actions = [a for a in actions
                   if (a.get("type") or "").upper() != "DRAFT_EMAIL"]
    await _replace_actions(
        db, target["id"], [RuleActionModel(**a) for a in actions])
    return True


async def _insert_rule(db: Any, req: RuleModel) -> str:
    """Insert a rule + its actions; returns the new rule id. Caller commits."""
    rule_id = str(uuid4())
    await db.execute(text(
        """INSERT INTO email_rules
             (id, account_id, name, instructions, enabled, automated,
              run_on_threads, conditional_operator, from_pattern, to_pattern,
              subject_pattern, body_pattern, system_type)
           VALUES (:id, :aid, :name, :instr, :enabled, :auto, :rot, :op,
                   :fp, :tp, :sp, :bp, :st)"""
    ), {"id": rule_id, "aid": req.account_id, "name": req.name,
        "instr": req.instructions, "enabled": req.enabled,
        "auto": req.automated, "rot": req.run_on_threads,
        "op": req.conditional_operator,
        "fp": req.from_pattern, "tp": req.to_pattern, "sp": req.subject_pattern,
        "bp": req.body_pattern, "st": req.system_type})
    await _replace_actions(db, rule_id, req.actions)
    return rule_id


@router.post("/rules")
async def create_rule(
    req: RuleModel,
    user: UserContext = Depends(get_current_user),
):
    """Create an assistant rule with its actions."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        rule_id = await _insert_rule(db, req)
        rules = await _load_rules(db, req.account_id)
        return next((r for r in rules if r["id"] == rule_id), {"id": rule_id})


_GEN_ACTION_TYPES = {
    "ARCHIVE", "LABEL", "MARK_READ", "STAR", "MARK_SPAM", "TRASH",
    "MOVE_FOLDER", "REPLY", "FORWARD", "DRAFT_EMAIL", "CALL_WEBHOOK",
}


async def _llm_generate_rules(prompt: str) -> list[dict[str, Any]]:
    """Turn a natural-language rule description (one or several, often a bullet
    list) into structured rule specs — inbox-zero's plain-text → rules flow.

    Returns a list of dicts shaped like RuleModel (minus account_id). Best
    effort: returns [] if the LLM is unavailable or nothing parses."""
    try:
        sys_prompt = (
            "You convert a user's plain-English description of email rules into "
            "structured automation rules. The user may describe several rules "
            "(often one per line/bullet). Output ONLY a JSON object "
            '{"rules": [ ... ]} where each element is:\n'
            '{"name": "<short name>", "instructions": "<the AI-matched condition '
            'in plain English, or empty if purely static>", "from_pattern": '
            '"<sender substring/email, or empty>", "subject_pattern": "<subject '
            'substring, or empty>", "conditional_operator": "AND"|"OR", '
            '"actions": [{"type": "<ACTION>", "label": "<label or folder, if '
            'LABEL/MOVE_FOLDER>", "to_address": "<for FORWARD>", "subject": '
            '"<optional>", "content": "<optional draft text; leave empty to let '
            'the AI write it>", "url": "<for CALL_WEBHOOK>"}]}\n'
            "ACTION must be one of: ARCHIVE, LABEL, MARK_READ, STAR, MARK_SPAM, "
            "TRASH, MOVE_FOLDER, REPLY, FORWARD, DRAFT_EMAIL, CALL_WEBHOOK.\n"
            "Rules of thumb: 'label X as Y' → LABEL with label Y; 'archive' → "
            "ARCHIVE; 'forward to a@b.com' → FORWARD to_address a@b.com; 'draft a "
            "reply' → DRAFT_EMAIL; 'reply with …' → REPLY content. Prefer an AI "
            "`instructions` condition for fuzzy intent; use from_pattern/"
            "subject_pattern only for literal sender/subject text. Keep names "
            'short. Omit empty fields. Output {"rules": []} if nothing parses.'
        )
        # Rule authoring is quality-sensitive generation → powerful tier; JSON
        # forced; generous budget so several rules aren't truncated.
        data, _content, _used = await _llm_json(
            "tier-powerful",
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": prompt[:4000]}],
            max_tokens=2500,
        )
        rules = data.get("rules") if isinstance(data, dict) else data
        return _normalize_generated_rules(rules)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.generate_rules_failed", error=str(exc)[:200])
        return []


def _normalize_generated_rules(data: Any) -> list[dict[str, Any]]:
    """Validate/sanitize the LLM's JSON into rule specs (pure; unit-tested).

    Drops specs without a name or any valid action; clamps action types to the
    supported set; normalizes the conditional operator to AND/OR."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for spec in data:
        if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
            continue
        actions: list[dict[str, Any]] = []
        for a in spec.get("actions") or []:
            if not isinstance(a, dict):
                continue
            atype = str(a.get("type", "")).upper()
            if atype not in _GEN_ACTION_TYPES:
                continue
            actions.append({
                "type": atype,
                "label": a.get("label") or None,
                "to_address": a.get("to_address") or None,
                "subject": a.get("subject") or None,
                "content": a.get("content") or None,
                "url": a.get("url") or None,
            })
        if not actions:
            continue
        op = str(spec.get("conditional_operator", "AND")).upper()
        out.append({
            "name": str(spec["name"]).strip()[:60],
            "instructions": (str(spec.get("instructions") or "")).strip() or None,
            "from_pattern": (str(spec.get("from_pattern") or "")).strip() or None,
            "subject_pattern": (str(spec.get("subject_pattern") or "")).strip() or None,
            "conditional_operator": "OR" if op == "OR" else "AND",
            "actions": actions,
        })
    return out


@router.get("/rules/policies")
async def rule_policies(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Everything ELSE that acts on this mailbox, for display on the Rules
    screen: the cold-email blocker mode, sender-disposition counts (the
    Cleaner's standing per-sender policy), and the provider-native inbox rules
    (Outlook message rules) — including which of those we created ourselves
    (``managed``: its id is recorded on a sender row as the block filter).

    Read-only. The provider listing is best-effort: no scope / consumer MSA /
    auth failure degrade to ``provider_rules_supported: false`` rather than
    failing the screen — the local policies still render.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        cb_row = (await db.execute(text(
            "SELECT cold_email_blocker FROM email_assistant_settings "
            "WHERE account_id = :aid"
        ), {"aid": account_id})).fetchone()
        cold = ((cb_row.cold_email_blocker if cb_row else None) or "OFF").upper()

        disp_rows = (await db.execute(text(
            """SELECT status, COUNT(*) AS n,
                      COUNT(auto_archive_filter_id) AS filters
               FROM email_newsletters WHERE account_id = :aid
               GROUP BY status"""
        ), {"aid": account_id})).fetchall()
        dispositions = {r.status: int(r.n) for r in disp_rows}
        filters_active = sum(int(r.filters) for r in disp_rows)
        managed_rows = (await db.execute(text(
            """SELECT auto_archive_filter_id AS fid FROM email_newsletters
               WHERE account_id = :aid AND auto_archive_filter_id IS NOT NULL"""
        ), {"aid": account_id})).fetchall()
        managed_ids = {r.fid for r in managed_rows}

        provider_rules: list[dict[str, Any]] = []
        supported = False
        try:
            async with provider_session(
                db, user.email or "anonymous", account_id=account_id,
                require_auth=False,
            ) as sess:
                if sess.authed:
                    provider_rules = [
                        {**r, "managed": r.get("id") in managed_ids}
                        for r in await sess.provider.list_filters()
                    ]
                    supported = True
        except Exception as exc:  # noqa: BLE001
            # Display-only extra — never fail the screen over it, but the
            # session may be mid-transaction after a provider error. The
            # rollback ends this transaction (and with it the tenant GUC);
            # nothing below touches the DB again, and the wrapper's exit
            # commit closes out an empty transaction.
            await db.rollback()
            _log.warning("email.rule_policies_provider_failed",
                         account_id=account_id, error=str(exc)[:200])
        return {
            "cold_email_blocker": cold,
            "dispositions": dispositions,
            "filters_active": filters_active,
            "provider_rules": provider_rules,
            "provider_rules_supported": supported,
        }


class RuleGenerateRequest(BaseModel):
    account_id: str
    prompt: str


@router.post("/rules/generate")
async def generate_rules(
    req: RuleGenerateRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create rule(s) from a plain-English description (inbox-zero's prompt flow).

    The text may describe several rules at once; each is turned into a
    structured rule and created. Returns the created rules."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        if not (req.prompt or "").strip():
            return {"created": [], "error": "Describe at least one rule."}
        specs = await _llm_generate_rules(req.prompt)
        if not specs:
            return {"created": [],
                    "error": "Couldn't turn that into a rule — try rephrasing."}
        created_ids: list[str] = []
        for spec in specs:
            model = RuleModel(
                account_id=req.account_id,
                name=spec["name"],
                instructions=spec.get("instructions"),
                from_pattern=spec.get("from_pattern"),
                subject_pattern=spec.get("subject_pattern"),
                conditional_operator=spec.get("conditional_operator", "AND"),
                actions=[RuleActionModel(**a) for a in spec["actions"]],
            )
            created_ids.append(await _insert_rule(db, model))
        rules = await _load_rules(db, req.account_id)
        created = [r for r in rules if r["id"] in set(created_ids)]
        return {"created": created}


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    req: RuleModel,
    user: UserContext = Depends(get_current_user),
):
    """Update a rule and replace its actions."""
    async with _tenant_session() as db:
        owner = (await db.execute(text(
            """SELECT er.account_id FROM email_rules er
               JOIN email_accounts ea ON er.account_id = ea.id
               WHERE er.id = :rid AND ea.user_id = :uid"""
        ), {"rid": rule_id, "uid": user.email or "anonymous"})).fetchone()
        if not owner:
            raise HTTPException(status_code=404, detail="Rule not found")
        await db.execute(text(
            """UPDATE email_rules SET
                 name = :name, instructions = :instr, enabled = :enabled,
                 automated = :auto, run_on_threads = :rot,
                 conditional_operator = :op,
                 from_pattern = :fp, to_pattern = :tp, subject_pattern = :sp,
                 body_pattern = :bp,
                 system_type = :st, updated_at = now()
               WHERE id = :rid"""
        ), {"rid": rule_id, "name": req.name, "instr": req.instructions,
            "enabled": req.enabled, "auto": req.automated,
            "rot": req.run_on_threads,
            "op": req.conditional_operator, "fp": req.from_pattern,
            "tp": req.to_pattern, "sp": req.subject_pattern, "bp": req.body_pattern,
            "st": req.system_type})
        await _replace_actions(db, rule_id, req.actions)
        rules = await _load_rules(db, str(owner.account_id))
        return next((r for r in rules if r["id"] == rule_id), {"id": rule_id})


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Delete a rule (cascades to actions)."""
    async with _tenant_session() as db:
        res = await db.execute(text(
            """DELETE FROM email_rules er
               USING email_accounts ea
               WHERE er.id = :rid AND er.account_id = ea.id
                 AND ea.user_id = :uid"""
        ), {"rid": rule_id, "uid": user.email or "anonymous"})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Rule not found")


# Sources that represent a deliberate human act, so the pattern needs no review:
# the Fix flow, a label the user changed in their own mail client, and patterns
# typed into a rule. Everything else ('AI') is the machine generalising from its
# own output and lands in the review queue — see migration 85.
_USER_AUTHORED_SOURCES = frozenset({
    "FIX", "USER", "LABEL_ADDED", "LABEL_REMOVED",
})


async def _upsert_rule_pattern(
    db: Any, account_id: str, rule_id: str, value: str, exclude: bool,
    source: str, reason: str | None, message_id: str | None, thread_id: str | None,
    pattern_type: str = "FROM",
) -> bool:
    """Record a learned classification pattern (FROM sender or SUBJECT keyword)
    for a rule. Removes the opposite (include vs exclude) for the same
    type+value so a correction never contradicts itself.

    Returns True only if a pattern was actually stored. The guards below refuse
    writes silently, and callers used to report "Learned" regardless — so a user
    whose correction hit a guard was told it worked, forever, while nothing was
    ever recorded. Callers must honour the return value.

    Centralized backstop for two anti-patterns that every learning path (Fix,
    auto-learn, label sync) must avoid — enforced HERE so no single path can
    reintroduce them:
      1. Sender-pinning a conversation-status rule (Reply / Awaiting / FYI /
         Done). Reply state is re-derived from the whole thread and overrides
         any pattern, so "always reply to X" is both wrong and futile.
      2. Pinning the mailbox's OWN address to any rule (e.g. "vjvarada@… →
         Reply") — a meaningless self-reference from a stray label delta."""
    if not (value or "").strip():
        return False
    ptype = "SUBJECT" if (pattern_type or "").upper() == "SUBJECT" else "FROM"
    # (1) Never pin a sender/subject to a conversation-status rule. Mirrors
    #     engine._conversation_rule_key: system_type when set, else the name.
    meta = (await db.execute(text(
        "SELECT name, system_type FROM email_rules WHERE id = :rid"
    ), {"rid": rule_id})).fetchone()
    if meta is not None:
        key = ((meta.system_type or "").upper().strip()
               or (meta.name or "").upper().strip().replace(" ", "_"))
        # + legacy TO_REPLY / ACTIONED so an un-migrated conversation rule is
        # still recognised and never sender-pinned (the anti-pattern this guards).
        if key in {"REPLY", "AWAITING_REPLY", "FYI", "DONE",
                   "TO_REPLY", "ACTIONED"}:
            return False
    # (2) Never pin an address of the member's own mailboxes (FROM patterns
    #     only). Two halves (EM-T8e-1 review round 1):
    #     - THIS mailbox keeps the old substring rule, so its address and its
    #       domain ("fracktal.in" is in "vj@fracktal.in") are both refused.
    #     - ANOTHER mailbox of the member (D-EM-27) is refused on an exact
    #       address only. A substring test there refused "gmail.com" in a work
    #       mailbox because a second mailbox was vj@gmail.com.
    if ptype == "FROM":
        val_l = value.strip().lower()
        me = await resolve_self(db, account_id)
        own = me.address.strip().lower()
        if own and (own in val_l or val_l in own):
            return False
        if val_l in me.self_addresses:
            return False
    # (3) A pattern the user REJECTED must not come straight back. The auto-
    #     learner fires on any sender with three consistent AI matches, which is
    #     exactly the sender the user just rejected a pattern for — so without
    #     this, rejecting is futile and the same wrong pattern reappears within
    #     the hour. A deliberate user action (Fix, a label change, a hand-typed
    #     rule) is allowed to overturn it; the machine re-inferring it is not.
    user_authored = source in _USER_AUTHORED_SOURCES
    if not user_authored:
        rejected = (await db.execute(text(
            "SELECT 1 FROM email_rule_patterns WHERE account_id = :aid "
            "AND rule_id = :rid AND pattern_type = :ptype "
            "AND lower(value) = lower(:val) AND rejected_at IS NOT NULL"
        ), {"aid": account_id, "rid": rule_id, "ptype": ptype,
            "val": value})).fetchone()
        if rejected is not None:
            return False
    # Drop the opposite disposition for this (rule, type, value) first.
    await db.execute(text(
        "DELETE FROM email_rule_patterns WHERE account_id = :aid AND rule_id = :rid "
        "AND pattern_type = :ptype AND lower(value) = lower(:val) AND exclude = :opp"
    ), {"aid": account_id, "rid": rule_id, "ptype": ptype, "val": value,
        "opp": not exclude})
    # A pattern the user authored is approved by definition — Fix, a label
    # changed in their own mail client, or a rule they typed IS the confirmation
    # the review queue exists to collect. Only 'AI' arrives unreviewed.
    await db.execute(text(
        """INSERT INTO email_rule_patterns
             (account_id, rule_id, pattern_type, value, exclude, source, reason,
              message_id, thread_id, approved_at)
           VALUES (:aid, :rid, :ptype, :val, :exc, :src, :reason, :mid, :tid,
                   CASE WHEN :authored THEN now() ELSE NULL END)
           ON CONFLICT (account_id, rule_id, pattern_type, lower(value), exclude)
           DO UPDATE SET source = EXCLUDED.source, reason = EXCLUDED.reason,
                         created_at = now(),
                         approved_at = CASE WHEN :authored THEN now()
                                       ELSE email_rule_patterns.approved_at END,
                         rejected_at = CASE WHEN :authored THEN NULL
                                       ELSE email_rule_patterns.rejected_at END"""
    ), {"aid": account_id, "rid": rule_id, "ptype": ptype, "val": value,
        "exc": exclude, "src": source, "reason": reason, "mid": message_id,
        "tid": thread_id, "authored": user_authored})
    # The write landed. Every guard above returns False; this is the one path
    # that actually stored a pattern, so it is the one path that returns True.
    # Without this the success path fell off the end as None, and the Fix flow
    # (which honours the return value since #105) told the user "Nothing was
    # saved" while the pattern sat committed in the table.
    return True


async def _upsert_rule_guidance(
    db: Any, account_id: str, rule_id: str | None, guidance: str,
    source: str = "FIX", message_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    """Record a correction that teaches the classifier.

    Idempotent on the text: repeating a correction should not stack duplicates
    into every future prompt, so a re-teach refreshes the existing row instead.
    """
    await db.execute(text(
        """INSERT INTO email_rule_guidance
             (account_id, rule_id, guidance, source, message_id, thread_id)
           VALUES (:aid, :rid, :g, :src, :mid, :tid)
           ON CONFLICT (account_id,
                        COALESCE(rule_id,
                                 '00000000-0000-0000-0000-000000000000'::uuid),
                        LOWER(TRIM(guidance)))
           DO UPDATE SET active = true, updated_at = now(),
                         source = EXCLUDED.source"""
    ), {"aid": account_id, "rid": rule_id, "g": guidance.strip()[:1000],
        "src": source, "mid": message_id, "tid": thread_id})


class RuleGuidanceRequest(BaseModel):
    account_id: str
    guidance: str
    rule_id: str | None = None


@router.get("/rules/guidance")
async def list_rule_guidance(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Corrections that teach the classifier — the "improves the AI" half of the
    Learned Patterns screen."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        rows = (await db.execute(text(
            """SELECT g.id, g.rule_id, r.name AS rule_name, g.guidance,
                      g.source, g.thread_id, g.created_at
                 FROM email_rule_guidance g
                 LEFT JOIN email_rules r ON r.id = g.rule_id
                WHERE g.account_id = :aid AND g.active
                ORDER BY g.created_at DESC"""
        ), {"aid": account_id})).fetchall()
        return {"guidance": [
            {"id": str(r.id),
             "rule_id": str(r.rule_id) if r.rule_id else None,
             "rule_name": r.rule_name,
             "guidance": r.guidance, "source": r.source,
             "thread_id": r.thread_id,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]}


@router.post("/rules/guidance")
async def add_rule_guidance(
    req: RuleGuidanceRequest,
    user: UserContext = Depends(get_current_user),
):
    """Write a correction by hand, without going through a specific email.

    A ``rule_id`` must be a rule of ``account_id``, or the answer is 404 and
    nothing is stored (D-EM-19, EM-T8e-1 review round 1)."""
    text_ = (req.guidance or "").strip()
    if not text_:
        raise HTTPException(status_code=400, detail="Guidance cannot be empty")
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        if req.rule_id:
            _refuse_foreign_rules(
                {r["id"]: r for r in await _load_rules(db, req.account_id)},
                "none", [req.rule_id])
        await _upsert_rule_guidance(
            db, req.account_id, req.rule_id, text_, "USER")
        return {"ok": True}


@router.delete("/rules/guidance/{gid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule_guidance(
    gid: str,
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """Withdraw a correction. Deleted outright rather than deactivated — unlike a
    rejected PATTERN, nothing re-infers guidance, so there is no verdict to
    remember and a leftover row would just be clutter the user cannot see."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        await db.execute(text(
            "DELETE FROM email_rule_guidance "
            " WHERE id = :gid AND account_id = :aid"
        ), {"gid": gid, "aid": account_id})


class RuleFeedbackRequest(BaseModel):
    account_id: str
    sender: str                       # sender email — the FROM pattern value
    expected: str                     # rule_id | "none" | "new"
    matched_rule_ids: list[str] = []  # rules that currently match this email
    explanation: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    # Optional SUBJECT keyword to learn alongside (or instead of) the sender —
    # inbox-zero's GroupItem supports both SENDER and SUBJECT signals.
    subject_keyword: str | None = None
    # What the correction should TEACH. Free text that goes into the classifier
    # prompt for `expected`, so the model reasons differently about every sender
    # — not just this one.
    guidance: str | None = None
    # Whether to ALSO pin this sender to the rule, skipping the classifier for
    # them entirely. Defaults OFF: a correction should make the AI better, not
    # carve one sender out of its reach and leave the same misunderstanding in
    # place everywhere else.
    pin_sender: bool = False


#: The values of ``expected`` that name no rule.
_NO_RULE = frozenset({"none", "new"})


def _refuse_foreign_rules(
    rules_of_mailbox: dict[str, Any], expected: str, matched: list[str],
) -> None:
    """404 when ``expected`` or a value of ``matched`` is not a rule of the
    mailbox (D-EM-19, EM-T8e-1). The ids compare without case. An empty
    value of ``matched`` names nothing and passes, as the route skips it."""
    known = {str(k).strip().lower() for k in rules_of_mailbox}
    named = [r for r in matched if r]
    if expected not in _NO_RULE:
        named.append(expected)
    if any(str(r).strip().lower() not in known for r in named):
        raise HTTPException(status_code=404, detail="Rule not found")


async def _refuse_foreign_pair(
    db: Any, req: RuleFeedbackRequest, rules_of_mailbox: dict[str, Any],
) -> None:
    """404 for a Fix that names anything outside ``req.account_id``.

    Each rule the request names is a rule of the mailbox. ``_upsert_rule_pattern``
    reads a rule by its id alone, so a rule of another mailbox would otherwise
    reach its guards. The mail and the thread are of the mailbox too (review
    round 1): a mail of another mailbox got this mailbox's label, and a thread
    of another mailbox got a status row of this one."""
    _refuse_foreign_rules(rules_of_mailbox, req.expected, req.matched_rule_ids)
    if req.message_id:
        await _assert_mail_in_mailbox(db, req.account_id, req.message_id)
    thread_id = (req.thread_id or "").strip()
    if thread_id:
        await _assert_thread_in_mailbox(db, req.account_id, thread_id)


@router.post("/rules/feedback")
async def rule_feedback(
    req: RuleFeedbackRequest,
    user: UserContext = Depends(get_current_user),
):
    """Persist a Fix correction as learned patterns so it sticks (inbox-zero
    parity). "expected = rule_id" teaches the matcher to ALWAYS apply that rule
    to this sender (and to STOP applying any other rule that wrongly matched);
    "none" teaches it to stop applying the matched rules to this sender; "new"
    is handled by creating a rule (returns created=False, action="new").

    A correction can be taught on the sender (FROM), a subject keyword
    (SUBJECT), or both — whichever signals the request carries."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        sender = (req.sender or "").strip()
        subject_kw = (req.subject_keyword or "").strip()
        reason = (req.explanation or "").strip() or "Taught via Fix"
        if req.expected == "new":
            return {"created": False, "action": "new"}

        # Conversation-status rules (Reply / Awaiting / FYI / Done) are
        # re-derived from the full thread, so a learned sender/subject pattern is
        # OVERRIDDEN and pinning a person to one is wrong. For those, the fix that
        # sticks is to set the thread status directly. Cleanup categories
        # (Newsletter/Receipt/…) are sender-stable → learn FROM/SUBJECT patterns.
        meta = {r["id"]: r for r in await _load_rules(db, req.account_id)}
        # The pair must match (D-EM-19, EM-T8e-1), or the answer is 404 and
        # nothing is written.
        await _refuse_foreign_pair(db, req, meta)

        # The Fix dialog passes the message id; derive its thread for a status fix.
        thread_id = (req.thread_id or "").strip()
        if not thread_id and req.message_id:
            trow = (await db.execute(text(
                "SELECT thread_id FROM email_messages "
                "WHERE id = :mid AND account_id = :aid"
            ), {"mid": req.message_id, "aid": req.account_id})).fetchone()
            thread_id = (trow.thread_id if trow else "") or ""

        def _conv_key(rid: str | None) -> str:
            r = meta.get(str(rid)) or {}
            k = ((r.get("system_type") or "").upper().strip()
                 or (r.get("name") or "").upper().strip().replace(" ", "_"))
            return k if k in {"REPLY", "AWAITING_REPLY", "FYI", "DONE",
                              "TO_REPLY", "ACTIONED"} else ""

        # Pattern signals (only meaningful for cleanup rules).
        signals: list[tuple[str, str]] = []
        if sender:
            signals.append(("FROM", sender))
        if subject_kw:
            signals.append(("SUBJECT", subject_kw))

        async def _teach(rule_id: str, exclude: bool) -> bool:
            """Store the signals for a rule; True if anything was actually saved.

            _upsert_rule_pattern refuses some writes on purpose (conversation
            rules can't be sender-pinned; the mailbox's own address is never
            pinned). Reporting those as "learned" is worse than reporting
            nothing — the user sees a success toast, changes nothing, and repeats
            the same correction forever.
            """
            # Pattern writes are OPT-IN now. A pattern skips the classifier for
            # one sender; correcting a mistake should instead change how the
            # model reasons, or the same misunderstanding survives untouched for
            # every other sender. Gated here because this is the single choke
            # point for both include and exclude writes in the Fix flow.
            if not req.pin_sender:
                return False
            saved = False
            for ptype, val in signals:
                if await _upsert_rule_pattern(
                    db, req.account_id, rule_id, val, exclude, "FIX", reason,
                    req.message_id, req.thread_id, pattern_type=ptype,
                ):
                    saved = True
            return saved

        learned: list[dict[str, Any]] = []
        status_correction: dict[str, Any] | None = None

        if req.expected == "none":
            # Stop the wrong CLEANUP rules from matching this sender (conversation
            # rules can't be pattern-excluded — they're thread-state).
            for rid in req.matched_rule_ids:
                if rid and not _conv_key(rid) and signals:
                    if await _teach(rid, True):
                        learned.append({"rule_id": rid, "exclude": True})
        else:
            ck = _conv_key(req.expected)
            if ck:
                # Conversation status → set it directly on the thread. Conversation
                # rules never get learned patterns, so with no resolvable thread
                # there's nothing to persist — only report "learned" when the
                # status correction actually landed (otherwise the UI would show a
                # false "Learned — will now match …" toast).
                if thread_id:
                    from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
                        apply_thread_status_correction,
                    )
                    status_correction = await apply_thread_status_correction(
                        req.account_id, thread_id, ck)
                    if status_correction and status_correction.get("ok"):
                        learned.append({"rule_id": req.expected, "status_set": ck})
            elif signals:
                if await _teach(req.expected, False):
                    learned.append({"rule_id": req.expected, "exclude": False})
            # Stop the wrong CLEANUP rules from matching this sender too.
            for rid in req.matched_rule_ids:
                if (rid and rid != req.expected and not _conv_key(rid)
                        and signals):
                    if await _teach(rid, True):
                        learned.append({"rule_id": rid, "exclude": True})

        # Make the correction VISIBLE on the message it came from (H6): strip the
        # label(s) the wrongly-matched cleanup rules put on, and apply the correct
        # rule's label when the fix names one. Teaching alone fixed only FUTURE
        # mail; the offending message kept its wrong chip. Conversation-status
        # corrections set thread state (above), not a category, so they're
        # excluded here. Best-effort — a provider hiccup never unwinds the teach.
        label_correction: dict[str, list[str]] | None = None
        if req.message_id:
            wrong_rids = [
                rid for rid in req.matched_rule_ids
                if rid and rid != req.expected and not _conv_key(rid)]
            add_rid = (req.expected
                       if (req.expected not in ("none", "new")
                           and not _conv_key(req.expected))
                       else None)
            if wrong_rids or add_rid:
                from gateway.routes.email.automation.runner import (  # noqa: PLC0415
                    correct_applied_labels,
                )
                try:
                    label_correction = await correct_applied_labels(
                        db, req.account_id, req.message_id,
                        user.email or "anonymous",
                        remove_rule_ids=wrong_rids, add_rule_id=add_rid)
                except Exception:  # noqa: BLE001
                    label_correction = None

        # The half that teaches rather than bypasses. Attached to the rule the
        # user says is correct; account-wide when they are only saying what this
        # ISN'T, since there is no one rule to hang it on.
        taught = (req.guidance or "").strip()
        if taught:
            target = None if req.expected in ("none", "new") else req.expected
            await _upsert_rule_guidance(
                db, req.account_id, target, taught, "FIX",
                req.message_id, req.thread_id)

        changed_label = bool(
            label_correction
            and (label_correction["removed"] or label_correction["added"]))
        created = bool(
            learned or taught or changed_label
            or (status_correction and status_correction.get("ok")))
        return {"created": created, "learned": learned, "sender": sender,
                "subject_keyword": subject_kw or None,
                "signals": [t for t, _ in signals],
                "status_correction": status_correction,
                "label_correction": label_correction}


@router.get("/rules/patterns")
async def list_rule_patterns(
    account_id: str = Query(...),
    user: UserContext = Depends(get_current_user),
):
    """List learned classification patterns (sender → rule include/exclude).

    Each row carries its REACH: how many messages in the mailbox the pattern
    matches. Without it this screen could not support review — it showed a
    sender, a rule and a delete button, so "is this pattern right?" was
    unanswerable from what was on the page, which is why 45 machine-inferred
    patterns had accumulated on the live account without one being looked at.

    Reach is an approximation of :func:`_pattern_hit` that Postgres can compute:
    a FROM pattern counts mail whose sender contains the value, a SUBJECT pattern
    counts mail whose subject contains it. It skips the generalised-subject and
    address-boundary refinements, so it is a ceiling, not an exact count — the
    UI presents it as "about". One nested-loop join over the mailbox, on an
    explicitly-opened review screen.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        try:
            rows = (await db.execute(text(
                f"""SELECT p.id, p.rule_id, r.name AS rule_name, p.pattern_type,
                          p.value, p.exclude, p.source, p.reason, p.created_at,
                          p.approved_at, p.rejected_at,
                          (SELECT COUNT(*) FROM email_messages m
                            WHERE m.account_id = p.account_id
                              -- Trash/Junk/Drafts are not reach. The live
                              -- account had a Newsletter pattern whose entire
                              -- 46-message "reach" was mail the user had
                              -- deleted — a number that argues FOR approving a
                              -- pattern using the user's own rejection of it.
                              AND LOWER(COALESCE(m.folder, ''))
                                  NOT IN {DISPOSED_FOLDERS}
                              AND ((p.pattern_type = 'SUBJECT'
                                    AND LOWER(COALESCE(m.subject, ''))
                                        LIKE '%' || LOWER(p.value) || '%')
                                OR (p.pattern_type <> 'SUBJECT'
                                    AND LOWER(COALESCE(
                                          m.from_address->>'email', ''))
                                        LIKE '%' || LOWER(p.value) || '%'))
                          ) AS reach
                   FROM email_rule_patterns p
                   LEFT JOIN email_rules r ON p.rule_id = r.id
                   WHERE p.account_id = :aid
                   ORDER BY p.approved_at NULLS FIRST, p.created_at DESC"""
            ), {"aid": account_id})).fetchall()
        except Exception as e:  # noqa: BLE001 — table may not exist pre-migration
            _log.warning("email.list_rule_patterns_failed",
                         account_id=account_id, error=str(e)[:160])
            rows = []
        return {"patterns": [
            {"id": str(r.id), "rule_id": str(r.rule_id),
             "rule_name": r.rule_name, "pattern_type": r.pattern_type,
             "value": r.value, "exclude": bool(r.exclude), "source": r.source,
             "reason": r.reason, "reach": int(r.reach or 0),
             "approved_at": r.approved_at.isoformat() if r.approved_at else None,
             "rejected_at": r.rejected_at.isoformat() if r.rejected_at else None,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ]}


class PatternReviewRequest(BaseModel):
    account_id: str
    # Omit to act on every pattern still awaiting review.
    pattern_ids: list[str] | None = None
    approve: bool = True


@router.post("/rules/patterns/review")
async def review_rule_patterns(
    req: PatternReviewRequest,
    user: UserContext = Depends(get_current_user),
):
    """Approve or reject learned patterns — the gate the Email Cleaner reads.

    Rejecting KEEPS the row, with ``rejected_at`` set. Deleting it would let the
    auto-learner re-infer the same pattern from the same sender within the hour,
    which makes rejection a gesture rather than a decision. ``_upsert_rule_pattern``
    refuses to resurrect a rejected pattern unless the user themselves overturns
    it via Fix or a label change.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        params: dict[str, Any] = {"aid": req.account_id}
        where = "account_id = :aid"
        if req.pattern_ids is None:
            # "Approve everything waiting" — deliberately does NOT re-approve or
            # un-reject what has already been decided.
            where += " AND approved_at IS NULL AND rejected_at IS NULL"
        else:
            if not req.pattern_ids:
                return {"updated": 0, "approved": req.approve}
            where += " AND id = ANY(:ids)"
            params["ids"] = [str(p) for p in req.pattern_ids]
        sets = ("approved_at = now(), rejected_at = NULL" if req.approve
                else "rejected_at = now(), approved_at = NULL")
        res = await db.execute(text(
            f"UPDATE email_rule_patterns SET {sets} WHERE {where}"), params)
        updated = int(getattr(res, "rowcount", 0) or 0)
        _log.info("email.rule_patterns_reviewed", account_id=req.account_id,
                  approved=req.approve, updated=updated)
        return {"updated": updated, "approved": req.approve}


@router.delete("/rules/patterns/{pattern_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule_pattern(
    pattern_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Forget a learned classification pattern."""
    async with _tenant_session() as db:
        res = await db.execute(text(
            """DELETE FROM email_rule_patterns p USING email_accounts ea
               WHERE p.id = :id AND p.account_id = ea.id AND ea.user_id = :uid"""
        ), {"id": pattern_id, "uid": user.email or "anonymous"})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
