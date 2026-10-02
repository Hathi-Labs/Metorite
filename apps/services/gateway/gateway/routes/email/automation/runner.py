"""Automation · rule execution — applying rule actions, the run/test/history/
undo endpoints, and the background run + process-past jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.routes.email.automation.assistant import _load_assistant_about
from gateway.routes.email.automation.engine import (
    LLMUnavailable,
    _email_payload_from_id,
    _is_conversation_status_rule,
    _match_email_to_rule,
    _match_email_to_rules_multi,
    classify_matches,
    email_dict_from_row,
)
# Re-exported action machinery (2.3 split): the lazy importers (cleaner sweep,
# senders, rules) and the tests address these through the runner seam, and the
# apply loop below calls them via this module's namespace — so a test patching
# ``runner.apply_label`` still intercepts every caller.
from gateway.routes.email.automation.actions import (  # noqa: F401
    _apply_rule_actions,
    _email_looks_sensitive,
    _load_action_attachments,
    _render_template,
    _rule_label_values,
    apply_label,
    correct_applied_labels,
    mirror_label,
    push_label,
    remove_label,
)
from gateway.routes.email.automation.identity import resolve_org_domains
from gateway.routes.email.automation.jobs import JobTracker
from gateway.routes.email.automation.learning import (
    _ai_confirms_sender_pattern,
    _is_auto_learnable_rule,
    _sender_consistent_for_rule,
    _sender_is_a_correspondent,
)
from gateway.routes.email.automation.rules import _upsert_rule_pattern
from gateway.routes.email.automation.senders import _maybe_block_cold
from gateway.routes.email.core import (
    _assert_account_owner,
    _attachment_summaries,
    _date_range_clause,
    _tenant_session,
    _instantiate_provider,
    _log,
    _parse_iso_date,
    _persist_rotated_creds,
    _provider_for_account,
    _savepoint,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


async def _account_self_email(db: Any, account_id: str) -> str:
    """The connected account's own address — passed to the classifier so it can
    tell whether the mailbox owner is a direct recipient (To) or only CC'd."""
    try:
        row = (await db.execute(text(
            "SELECT email_address FROM email_accounts WHERE id = :id"
        ), {"id": account_id})).fetchone()
        return (row.email_address or "") if row else ""
    except Exception:  # noqa: BLE001
        return ""


class RuleTestRequest(BaseModel):
    account_id: str
    email_id: str | None = None
    subject: str | None = None
    from_email: str | None = None
    body: str | None = None


@router.post("/rules/test")
async def test_rules(
    req: RuleTestRequest,
    user: UserContext = Depends(get_current_user),
):
    """Test the rules against one email (selected message or a pasted sample)."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        if req.email_id:
            email = await _email_payload_from_id(db, req.email_id, user.email or "anonymous")
        else:
            email = {"subject": req.subject or "", "from": req.from_email or "",
                     "body": req.body or "", "to": ""}
        try:
            match = await _match_email_to_rule(db, req.account_id, email)
        except LLMUnavailable:
            return {"matched": False, "rule": None,
                    "reason": "The AI classifier is temporarily unavailable — "
                              "try again in a moment.",
                    "actions": []}
        if not match:
            return {"matched": False, "rule": None, "reason": "No rule matched.",
                    "actions": []}
        return {
            "matched": True,
            "rule": {"id": match["rule"]["id"], "name": match["rule"]["name"]},
            "reason": match["reason"],
            "actions": match["rule"]["actions"],
        }


class RuleTestRecentRequest(BaseModel):
    account_id: str
    limit: int = 8


@router.post("/rules/test/recent")
async def test_rules_recent(
    req: RuleTestRecentRequest,
    user: UserContext = Depends(get_current_user),
):
    """Test the rules against the most recent inbox messages (read-only).

    Returns, per email, which rule would match and the actions it would take —
    inbox-zero's "test on your real inbox" preview. Applies nothing.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        self_email = await _account_self_email(db, req.account_id)
        about, _ = await _load_assistant_about(db, req.account_id)
        rows = (await db.execute(text(
            """SELECT id, subject, body_text, snippet, from_address,
                      to_addresses, cc_addresses, thread_id, received_at
               FROM email_messages
               WHERE account_id = :aid AND LOWER(folder) = 'inbox'
               ORDER BY received_at DESC LIMIT :limit"""
        ), {"aid": req.account_id, "limit": min(req.limit, 15)})).fetchall()
        org_domains = await resolve_org_domains(db, req.account_id)
        attach = await _attachment_summaries(db, [r.id for r in rows])
        results = []
        for r in rows:
            frm = r.from_address if isinstance(r.from_address, dict) \
                else json.loads(r.from_address or "{}")
            email = email_dict_from_row(
                r, self_email, about, extra_domains=org_domains,
                attachments=attach.get(str(r.id), ""))
            try:
                match = await _match_email_to_rule(db, req.account_id, email)
            except LLMUnavailable:
                match = None  # preview only — a transient outage, not a verdict
            results.append({
                "email_id": str(r.id),
                "subject": r.subject or "(no subject)",
                "from": frm.get("name") or frm.get("email", ""),
                "matched": bool(match),
                "rule": {"id": match["rule"]["id"], "name": match["rule"]["name"]}
                if match else None,
                "reason": match["reason"] if match else "",
                "actions": [a["type"] for a in match["rule"]["actions"]]
                if match else [],
            })
        return {"results": results}


@router.get("/rules/history")
async def rules_history(
    account_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    include_deleted: bool = Query(False),
    user: UserContext = Depends(get_current_user),
):
    """Executed-rule audit log for the user's accounts.

    By default, entries whose underlying message was deleted upstream (moved to
    TRASH by delta reconciliation — e.g. an AI draft the user discarded) are
    hidden, so History reflects the live mailbox. Pass ``include_deleted=true``
    to see the full immutable log."""
    async with _tenant_session() as db:
        params: dict[str, Any] = {"uid": user.email or "anonymous", "limit": limit}
        scope = ("er.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid")
        if account_id:
            scope += " AND id = :aid"
            params["aid"] = account_id
        scope += ")"
        if not include_deleted:
            # Keep no-match rows (message_id NULL) and rows for messages we
            # never synced; drop only those we know were trashed/deleted.
            scope += (" AND (em.id IS NULL OR "
                      "lower(coalesce(em.folder, '')) NOT IN ('trash', 'deleted'))")
        rows = (await db.execute(text(
            f"""SELECT er.id, er.rule_id, er.rule_name, er.subject, er.from_address,
                       er.status, er.automated, er.actions_taken, er.reason,
                       er.created_at, er.match_source, er.action_errors,
                       er.message_id, em.snippet, em.received_at, em.categories,
                       r.instructions, r.from_pattern, r.to_pattern,
                       r.subject_pattern, r.body_pattern, r.conditional_operator,
                       d.draft_text
                FROM email_executed_rules er
                LEFT JOIN email_messages em ON er.message_id = em.id
                LEFT JOIN email_rules r ON er.rule_id = r.id
                LEFT JOIN email_ai_drafts d
                  ON d.account_id = er.account_id AND d.thread_id = em.thread_id
                WHERE {scope}
                ORDER BY COALESCE(em.received_at, er.created_at) DESC LIMIT :limit"""
        ), params)).fetchall()

        # Fetch each matched rule's action specs once so the hover popover can
        # render the actions (label / to / subject …), not just their types.
        rule_ids = sorted({str(r.rule_id) for r in rows if r.rule_id})
        actions_by_rule: dict[str, list[dict[str, Any]]] = {}
        if rule_ids:
            act_rows = (await db.execute(text(
                """SELECT rule_id, type, label, subject, content, to_address,
                          cc_address, bcc_address, url, delay_minutes, attachments
                   FROM email_actions WHERE rule_id = ANY(:rids)
                   ORDER BY rule_id"""
            ), {"rids": rule_ids})).fetchall()
            for a in act_rows:
                actions_by_rule.setdefault(str(a.rule_id), []).append({
                    "type": a.type, "label": a.label, "subject": a.subject,
                    "content": a.content, "to_address": a.to_address,
                    "cc_address": a.cc_address, "bcc_address": a.bcc_address,
                    "url": a.url, "delay_minutes": a.delay_minutes,
                    "attachments": a.attachments if isinstance(a.attachments, list)
                    else json.loads(a.attachments or "[]"),
                })

        return {
            "history": [
                {"id": str(r.id),
                 "rule_id": str(r.rule_id) if r.rule_id else None,
                 "rule_name": r.rule_name, "subject": r.subject,
                 "from": r.from_address, "status": r.status, "automated": r.automated,
                 "actions": r.actions_taken if isinstance(r.actions_taken, list)
                 else json.loads(r.actions_taken or "[]"),
                 "reason": r.reason, "snippet": r.snippet or "",
                 "message_id": str(r.message_id)
                 if getattr(r, "message_id", None) else None,
                 "match_source": getattr(r, "match_source", None),
                 "action_errors": (
                     r.action_errors if isinstance(r.action_errors, list)
                     else json.loads(r.action_errors or "[]")
                 ) if getattr(r, "action_errors", None) is not None else [],
                 "created_at": r.created_at.isoformat() if r.created_at else None,
                 "received_at": r.received_at.isoformat()
                 if getattr(r, "received_at", None) else None,
                 # The AI draft generated for this thread (DRAFT_EMAIL action),
                 # so the hover pill can preview it. Cleared once the user sends.
                 "draft_preview": (getattr(r, "draft_text", None) or None),
                 "labels": (
                     r.categories if isinstance(r.categories, list)
                     else json.loads(r.categories or "[]")
                 ) if getattr(r, "categories", None) is not None else [],
                 "conditions": {
                     "instructions": r.instructions,
                     "from_pattern": r.from_pattern,
                     "to_pattern": r.to_pattern,
                     "subject_pattern": r.subject_pattern,
                     "body_pattern": r.body_pattern,
                     "conditional_operator": r.conditional_operator or "AND",
                 } if r.rule_id else None,
                 "rule_actions": actions_by_rule.get(
                     str(r.rule_id) if r.rule_id else "", [])}
                for r in rows
            ]
        }


@router.get("/messages/{message_id}/timeline")
async def message_timeline(
    message_id: str,
    user: UserContext = Depends(get_current_user),
):
    """The audit timeline for a single message — every automation event that ever
    touched it, oldest first.

    Reply Zero History is a *global* feed of what the rules engine did across the
    mailbox; this is its per-message inverse: open one email and see its whole
    story — when it arrived, and each rule run that classified, labelled, drafted,
    moved, or failed on it (including corrections that re-ran later). Reuses the
    same `email_executed_rules` audit rows, scoped to this one message and to the
    caller's own accounts."""
    async with _tenant_session() as db:
        # Anchor: the message itself (received event) + ownership check. A message
        # the caller doesn't own — or one we never synced — yields 404, never a
        # cross-account peek.
        msg = (await db.execute(text(
            """SELECT em.id, em.subject, em.received_at, em.folder,
                      em.from_address ->> 'email' AS from_email,
                      em.from_address ->> 'name' AS from_name
               FROM email_messages em
               JOIN email_accounts ea ON ea.id = em.account_id
               WHERE em.id = :mid AND ea.user_id = :uid"""
        ), {"mid": message_id, "uid": user.email or "anonymous"})).fetchone()
        if msg is None:
            raise HTTPException(status_code=404, detail="Message not found")

        rows = (await db.execute(text(
            """SELECT er.id, er.rule_id, er.rule_name, er.status, er.automated,
                      er.actions_taken, er.reason, er.action_errors,
                      er.match_source, er.created_at
               FROM email_executed_rules er
               WHERE er.message_id = :mid
               ORDER BY er.created_at ASC"""
        ), {"mid": message_id})).fetchall()

        events: list[dict[str, Any]] = []
        if msg.received_at:
            events.append({
                "kind": "received",
                "at": msg.received_at.isoformat(),
                "from": msg.from_name or msg.from_email or "",
                "from_email": msg.from_email or "",
            })
        for r in rows:
            actions = (r.actions_taken if isinstance(r.actions_taken, list)
                       else json.loads(r.actions_taken or "[]"))
            errors = ((r.action_errors if isinstance(r.action_errors, list)
                       else json.loads(r.action_errors or "[]"))
                      if r.action_errors is not None else [])
            events.append({
                "kind": "skipped" if r.status == "SKIPPED" else "rule",
                "at": r.created_at.isoformat() if r.created_at else None,
                "rule_id": str(r.rule_id) if r.rule_id else None,
                "rule_name": r.rule_name,
                "status": r.status,
                "automated": bool(r.automated),
                "actions": actions,
                "action_errors": errors,
                "match_source": r.match_source,
                "reason": r.reason,
            })
        # Oldest-first overall: the received anchor may post-date an execution row
        # only in odd backfills, so sort the whole list by timestamp defensively.
        events.sort(key=lambda e: e.get("at") or "")

        return {
            "message_id": message_id,
            "subject": msg.subject or "",
            "events": events,
        }


# Actions a retry will never perform, however the original rule was configured.
# A retry re-runs a decision the assistant ALREADY made, possibly weeks ago; the
# label and the move are idempotent and safe to repeat, but sending or drafting
# a reply to a stale conversation is neither, and doing it unattended as part of
# a bulk repair is exactly the kind of outward-facing surprise the user cannot
# undo. If a reply was genuinely wanted, it can be drafted from the message.
_RETRY_SKIPPED_ACTIONS = frozenset({"REPLY", "DRAFT_EMAIL", "FORWARD",
                                    "SEND_EMAIL", "CALL_WEBHOOK"})


async def retry_failed_executions(
    account_id: str, *, limit: int = 200, user_email: str | None = None,
) -> dict[str, Any]:
    """Re-apply rule runs whose every action the mail server refused.

    Deterministic repair, NOT re-classification: it replays the rule the
    assistant already chose, so it costs no model calls and cannot change any
    decision. The only thing that differs from the original run is the message
    id — Outlook re-keys a message when it moves, which is what stranded these
    in the first place, so each one is re-read immediately before acting.

    Safe to run repeatedly: LABEL and MOVE_FOLDER are idempotent, and a row that
    succeeds is flipped to APPLIED so it is not retried again.
    """
    async with _tenant_session() as db:
        owner = user_email
        if owner is None:
            owner = (await db.execute(text(
                "SELECT user_id FROM email_accounts WHERE id = :aid"
            ), {"aid": account_id})).scalar()
        if not owner:
            raise HTTPException(status_code=404, detail="Account not found")

        rows = (await db.execute(text(
            """SELECT er.id, er.rule_id, er.rule_name, er.message_id,
                      em.provider_message_id, em.thread_id, em.subject,
                      em.body_text, em.from_address
                 FROM email_executed_rules er
                 JOIN email_messages em ON em.id = er.message_id
                WHERE er.account_id = :aid AND er.status = 'FAILED'
                  AND er.rule_id IS NOT NULL
                  -- NEVER repair mail the user has since thrown away. Most of
                  -- these failures are the provider racing us, and by far the
                  -- commonest way that happens is the message being deleted or
                  -- junked — 92 of the 138 live failures ended in trash. A
                  -- MOVE_FOLDER replayed against one of those would lift it
                  -- back OUT of the bin into a category folder, resurrecting
                  -- mail the user deleted. A repair must never be able to do
                  -- that.
                  AND LOWER(COALESCE(em.folder, '')) NOT IN
                      ('trash', 'junk', 'spam', 'drafts', 'draft')
                ORDER BY er.created_at DESC
                LIMIT :limit"""
        ), {"aid": account_id, "limit": limit})).fetchall()
        if not rows:
            return {"considered": 0, "repaired": 0, "still_failing": 0,
                    "skipped_actions": []}

        provider, store, _owner_email = await _provider_for_account(
            db, account_id, owner)
        if not await provider.authenticate():
            raise HTTPException(status_code=502, detail="Provider auth failed")
        about, signature = await _load_assistant_about(db, account_id)

        # One lookup per distinct rule rather than per row: a batch repair is
        # usually a handful of rules across many messages.
        actions_by_rule: dict[str, list[dict[str, Any]]] = {}
        for rid in {str(r.rule_id) for r in rows}:
            act_rows = (await db.execute(text(
                """SELECT type, label, subject, content, to_address, cc_address,
                          bcc_address, url FROM email_actions
                    WHERE rule_id = :rid"""
            ), {"rid": rid})).fetchall()
            actions_by_rule[rid] = [dict(a._mapping) for a in act_rows]

        repaired = 0
        still_failing = 0
        skipped: set[str] = set()
        for r in rows:
            actions = [a for a in actions_by_rule.get(str(r.rule_id), [])
                       if a.get("type") not in _RETRY_SKIPPED_ACTIONS]
            skipped.update(
                a["type"] for a in actions_by_rule.get(str(r.rule_id), [])
                if a.get("type") in _RETRY_SKIPPED_ACTIONS)
            if not actions:
                continue
            frm = r.from_address if isinstance(r.from_address, dict) \
                else json.loads(r.from_address or "{}")
            email = {"subject": r.subject or "", "from": frm.get("email", ""),
                     "body": r.body_text or "", "thread_id": r.thread_id or ""}
            errors: list[dict[str, str]] = []
            taken = await _apply_rule_actions(
                db, provider, str(r.message_id), r.provider_message_id,
                actions, email, about, signature, owner,
                account_id=account_id, errors_out=errors,
            )
            status = "FAILED" if (errors and not taken) else "APPLIED"
            if status == "APPLIED":
                repaired += 1
            else:
                still_failing += 1
            await db.execute(text(
                "UPDATE email_executed_rules SET status = :st, "
                "actions_taken = :acts, action_errors = CAST(:aerr AS JSONB) "
                "WHERE id = :eid"
            ), {"eid": str(r.id), "st": status, "acts": json.dumps(taken),
                "aerr": json.dumps(errors)})

        await _persist_rotated_creds(db, store, account_id, provider)
        _log.info("email.retry_failed_done", account_id=account_id,
                  considered=len(rows), repaired=repaired,
                  still_failing=still_failing)
        return {"considered": len(rows), "repaired": repaired,
                "still_failing": still_failing,
                "skipped_actions": sorted(skipped)}


class RetryFailedRequest(BaseModel):
    account_id: str
    limit: int = 200


@router.post("/rules/history/retry-failed")
async def retry_failed(
    req: RetryFailedRequest,
    user: UserContext = Depends(get_current_user),
):
    """Repair rule runs the mail server refused. Never drafts or sends."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
    return await retry_failed_executions(
        req.account_id, limit=max(1, min(req.limit, 1000)),
        user_email=user.email or "anonymous")


@router.post("/rules/history/{exec_id}/approve")
async def approve_execution(
    exec_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Apply a PENDING (proposed) rule execution — the approval queue."""
    async with _tenant_session() as db:
        row = (await db.execute(text(
            """SELECT er.status, er.rule_id, er.message_id, er.provider_message_id,
                      er.thread_id, er.subject, er.from_address, er.account_id,
                      er.reason, ea.provider, ea.credentials_encrypted,
                      ea.user_id, em.body_text
               FROM email_executed_rules er
               JOIN email_accounts ea ON er.account_id = ea.id
               LEFT JOIN email_messages em ON er.message_id = em.id
               WHERE er.id = :eid AND ea.user_id = :uid"""
        ), {"eid": exec_id, "uid": user.email or "anonymous"})).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Execution not found")
        if row.status != "PENDING":
            raise HTTPException(status_code=400, detail="Not pending")
        if not row.rule_id:
            raise HTTPException(status_code=400, detail="Rule no longer exists")

        act_rows = (await db.execute(text(
            """SELECT type, label, subject, content, to_address, cc_address,
                      bcc_address, url FROM email_actions WHERE rule_id = :rid"""
        ), {"rid": row.rule_id})).fetchall()
        actions = [dict(a._mapping) for a in act_rows]

        from acb_llm.key_store import get_key_store
        store = get_key_store()
        creds = json.loads(store.decrypt(row.credentials_encrypted))
        provider = _instantiate_provider(row.provider, creds)
        if not await provider.authenticate():
            raise HTTPException(status_code=502, detail="Provider auth failed")

        about, signature = await _load_assistant_about(db, str(row.account_id))
        email = {"subject": row.subject or "", "from": row.from_address or "",
                 "body": row.body_text or "", "thread_id": row.thread_id or ""}
        taken = await _apply_rule_actions(
            db, provider, str(row.message_id), row.provider_message_id,
            actions, email, about, signature, row.user_id,
            account_id=str(row.account_id),
        )
        await db.execute(text(
            "UPDATE email_executed_rules SET status='APPLIED', actions_taken=:acts "
            "WHERE id=:eid"
        ), {"eid": exec_id, "acts": json.dumps(taken)})
        if row.message_id:
            await _stamp_processed_watermark(
                db, row.message_id, provider=provider)
        await _persist_rotated_creds(db, store, str(row.account_id), provider)
        return {"ok": True, "status": "APPLIED", "actions": taken}


@router.post("/rules/history/{exec_id}/reject")
async def reject_execution(
    exec_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Dismiss a PENDING rule execution without applying it."""
    async with _tenant_session() as db:
        res = await db.execute(text(
            """UPDATE email_executed_rules er
               SET status = 'REJECTED'
               FROM email_accounts ea
               WHERE er.id = :eid AND er.account_id = ea.id
                 AND ea.user_id = :uid AND er.status = 'PENDING'"""
        ), {"eid": exec_id, "uid": user.email or "anonymous"})
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pending execution not found")
        return {"ok": True, "status": "REJECTED"}


@router.post("/rules/history/{exec_id}/undo")
async def undo_execution(
    exec_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Reverse an APPLIED rule execution where possible: restore the message to
    the inbox (archive/move/trash/spam) and remove any labels the rule added."""
    async with _tenant_session() as db:
        row = (await db.execute(text(
            """SELECT er.status, er.rule_id, er.message_id, er.provider_message_id,
                      er.actions_taken, er.account_id, ea.provider,
                      ea.credentials_encrypted
               FROM email_executed_rules er
               JOIN email_accounts ea ON er.account_id = ea.id
               WHERE er.id = :eid AND ea.user_id = :uid"""
        ), {"eid": exec_id, "uid": user.email or "anonymous"})).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Execution not found")
        if row.status != "APPLIED":
            raise HTTPException(
                status_code=400,
                detail="Only applied executions can be undone")
        taken = row.actions_taken if isinstance(row.actions_taken, list) \
            else json.loads(row.actions_taken or "[]")

        from acb_llm.key_store import get_key_store  # noqa: PLC0415
        store = get_key_store()
        creds = json.loads(store.decrypt(row.credentials_encrypted))
        provider = _instantiate_provider(row.provider, creds)
        if not await provider.authenticate():
            raise HTTPException(status_code=502, detail="Provider auth failed")

        pmid = row.provider_message_id
        reversed_actions: list[str] = []
        if any(t in ("ARCHIVE", "MOVE_FOLDER", "TRASH", "MARK_SPAM")
               for t in taken):
            await provider.move_to_folder(pmid, "inbox")
            if row.message_id:
                await db.execute(text(
                    "UPDATE email_messages SET folder='inbox', updated_at=now() "
                    "WHERE id=:id"
                ), {"id": str(row.message_id)})
            reversed_actions.append("restored to inbox")
        if "LABEL" in taken and row.rule_id:
            lbl_rows = (await db.execute(text(
                "SELECT label FROM email_actions WHERE rule_id = :rid "
                "AND type = 'LABEL' AND label IS NOT NULL"
            ), {"rid": str(row.rule_id)})).fetchall()
            labels = [r.label for r in lbl_rows if r.label]
            if labels:
                try:
                    await provider.set_labels(pmid, add=[], remove=labels)
                    reversed_actions.append(
                        f"removed label(s): {', '.join(labels)}")
                except Exception:  # noqa: BLE001
                    pass
        await db.execute(text(
            "UPDATE email_executed_rules SET status='UNDONE' WHERE id=:eid"
        ), {"eid": exec_id})
        # A 401 during the undo refreshes the token (EM-T4c). Keep it.
        await _persist_rotated_creds(db, store, str(row.account_id), provider)
        return {"status": "UNDONE", "reversed": reversed_actions}


class RuleRunRequest(BaseModel):
    account_id: str
    limit: int = 20
    dry_run: bool = True


@router.post("/rules/run")
async def run_rules(
    req: RuleRunRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Run enabled rules over recent inbox mail (scheduled in the background).

    `dry_run` (default) only logs what WOULD happen to the history; set it false
    to actually apply the matched actions. Poll GET /email/rules/history for
    results.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
    background.add_task(
        _run_rules_job, req.account_id, min(req.limit, 50), req.dry_run,
        user.email or "anonymous",
    )
    return {"scheduled": True, "dry_run": req.dry_run}


# ── "Process past emails" live progress ──────────────────────────────────────
# The process-past job runs as a fire-and-forget BackgroundTask. The frontend
# had no way to know it was running, so the UI showed nothing after the dialog
# closed. We track per-account progress in memory (the gateway runs a single
# uvicorn worker, so the request handler and the background task share this dict)
# and expose it via GET /rules/process-past/status for the UI to poll. State is
# ephemeral by design: if the process restarts the job dies too, so losing the
# tracker with it is correct.
# The "Process past emails" progress tracker. The monotonic-token guard that
# stops a stale run from clobbering a newer one now lives in JobTracker, shared
# with the cleaner's sweep (automation/jobs.py).
_PAST_JOBS = JobTracker()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_job_start(
    account_id: str, owner: str, total: int, dry_run: bool,
    *, downloading: bool = False, already_processed: int = 0,
) -> int:
    """Seed the tracker for a new run; returns a token the background job passes
    back so a superseded (older) run can't mutate the newer entry.

    ``downloading`` marks the run as in the pre-apply provider-backfill phase
    (the range is being fetched from upstream). The job then calls
    ``_past_job_begin_processing`` with the real in-range total once mail lands."""
    running = downloading or total > 0
    return _PAST_JOBS.start(
        account_id,
        owner=owner,
        status="running" if running else "done",
        phase="downloading" if downloading else "processing",
        total=total,
        processed=0,
        applied=0,
        skipped=0,
        # Excluded up front as already-processed — NOT part of `total`, and not
        # a failure. Kept distinct from `skipped` (in range, processed, matched
        # no rule) so the UI can tell "already done" from "nothing applied".
        already_processed=already_processed,
        dry_run=dry_run,
        started_at=_now_iso(),
        finished_at=None if running else _now_iso(),
        error=None,
    )


def _past_job_begin_processing(
    account_id: str, *, token: int | None = None, total: int,
    already_processed: int | None = None,
) -> None:
    """Leave the 'downloading' phase and record the real in-range total once the
    upstream backfill has landed, so the UI's 'N of M' reflects everything that
    will actually be processed (not the pre-download local count).

    ``already_processed`` is re-measured here for the same reason: the endpoint's
    figure predates the download, so mail that arrived (or was first synced)
    during it isn't reflected in it."""
    job = _PAST_JOBS.guarded(account_id, token)
    if not job:
        return
    job["total"] = total
    if already_processed is not None:
        job["already_processed"] = already_processed
    job["phase"] = "processing"
    if total <= 0:
        job["status"] = "done"
        job["finished_at"] = _now_iso()


def _past_job_tick(
    account_id: str, *, token: int | None = None,
    applied: int = 0, skipped: int = 0,
) -> None:
    job = _PAST_JOBS.guarded(account_id, token)
    if not job or job.get("status") != "running":
        return  # gone, superseded by a newer run, or already finished
    job["processed"] += 1
    job["applied"] += applied
    job["skipped"] += skipped


def _past_job_finish(
    account_id: str, *, token: int | None = None, error: str | None = None,
    drafts_skipped: int = 0,
) -> None:
    job = _PAST_JOBS.guarded(account_id, token)
    if not job:
        return  # gone, or a newer run replaced this one — leave it running
    job["status"] = "error" if error else "done"
    job["error"] = error
    # Report suppressed drafting, so a run that deliberately skipped it doesn't
    # read as one where the rules simply ran in full.
    job["drafts_skipped"] = drafts_skipped
    job["finished_at"] = _now_iso()


# Rule actions that WRITE A DRAFT — the expensive, and on old mail usually
# unwanted, half of a rule. Each one calls the drafting model per message.
DRAFTING_ACTIONS = ("REPLY", "DRAFT_EMAIL", "FORWARD")


def _without_drafting(match: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """A copy of ``match`` with its drafting actions removed.

    Returns ``(match, stripped)``. The rule's other actions — label, archive,
    folder, mark-read — still run, because filing old mail is the point of a
    backfill; writing replies to it is not.
    """
    rule = match.get("rule") or {}
    actions = rule.get("actions") or []
    kept = [a for a in actions if a.get("type") not in DRAFTING_ACTIONS]
    if len(kept) == len(actions):
        return match, False
    return {**match, "rule": {**rule, "actions": kept}}, True


class RuleProcessPastRequest(BaseModel):
    account_id: str
    start_date: str | None = None  # ISO date (YYYY-MM-DD), inclusive
    end_date: str | None = None    # ISO date (YYYY-MM-DD), inclusive
    is_test: bool = False          # True = dry-run preview; False = apply for real
    include_read: bool = True      # False = only process unread mail in the range
    # Write drafts while backfilling. OFF BY DEFAULT, unlike a live run.
    #
    # A backfill walks months of already-resolved mail, and every draft action
    # spends a call on the drafting model. Defaulting this on meant pointing the
    # date picker at 90 days silently generated (and paid for) replies to
    # conversations that ended long ago, which the user then had to delete by
    # hand. Categorizing old mail is the point of a backfill; replying to it is
    # a separate, deliberate choice — so it has to be asked for.
    draft_replies: bool = False
    # Skip mail the rules have already run over (rules_processed_at IS NULL).
    # The date picker is a RANGE, not a cursor, so re-running it — or widening
    # it by a week — re-covers everything already done. Each of those messages
    # costs a classification call and rewrites a label it already has. Default
    # ON; turn it off to deliberately re-apply after changing a rule.
    skip_processed: bool = True
    limit: int = 1000


# Processing past mail is the ONE surface that spends a model call per message
# on a range the user picks by hand, and the picker is a free date input. Since
# "Clean older mail" can pull the whole mailbox into the local store, an
# unbounded range here stopped meaning "a few thousand" and started meaning
# "everything ever received".
#
#   "When processing past emails with AI, display a warning that extensive
#    categorization can be costly; limit processing to a few months up to a
#    year."                                                     — 2026-07-20
#
# Enforced HERE and not only in the dialog: the cap exists to bound spend, and a
# cap that lives only in the client is not a bound. A year is the ceiling, not
# the suggestion — the dialog's presets stop at 90 days.
_PROCESS_PAST_MAX_SPAN_DAYS = 366


def _assert_span_within_cap(
    start: datetime | None, end: datetime | None,
) -> int:
    """Days covered by [start, end]; 400 if it exceeds the cap.

    A missing ``start`` is not "a small range" — it is every message ever
    received, which is the most expensive request the API accepts. It is
    rejected with the same message as an over-wide one so the fix is the same:
    pick a start date.
    """
    if start is None:
        raise HTTPException(
            status_code=400,
            detail=f"Pick a start date. Processing past emails is limited to "
                   f"{_PROCESS_PAST_MAX_SPAN_DAYS} days per run because every "
                   f"email in the range costs an AI call.")
    span = ((end or datetime.now(timezone.utc)) - start).days + 1
    if span > _PROCESS_PAST_MAX_SPAN_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"That range covers {span} days. Processing past emails is "
                   f"limited to {_PROCESS_PAST_MAX_SPAN_DAYS} days per run "
                   f"because every email in the range costs an AI call — run "
                   f"it a year at a time.")
    return span


@router.get("/rules/process-past/estimate")
async def process_past_estimate(
    account_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    include_read: bool = True,
    skip_processed: bool = True,
    limit: int = 1000,
    user: UserContext = Depends(get_current_user),
):
    """How many emails a Process-past run would send to the model, before it runs.

    The dialog previously offered a free date picker and a Process button with no
    number between them, so the only way to learn that a range covered 4,000
    emails was to spend 4,000 AI calls finding out.

    ``will_process`` is deliberately not ``eligible``: the job reads
    ``LIMIT :limit`` rows, so a range wider than the limit is silently truncated
    and the tracker then reports that truncated figure as the total — which reads
    as "processed everything in the range" when it was "processed the oldest N".

    Counts what is synced LOCALLY. The job downloads the range from the provider
    first, so the real figure can be higher for a range that predates the initial
    365-day sync; the dialog says so rather than presenting this as exact.
    """
    start_dt = _parse_iso_date(start_date, end_of_day=False)
    end_dt = _parse_iso_date(end_date, end_of_day=True)
    only_unread = not include_read
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")

        async def _count(extra: str = "", unprocessed: bool = False) -> int:
            clause, params = _date_range_clause(
                account_id, start_dt, end_dt, only_unread, unprocessed)
            row = (await db.execute(text(
                f"SELECT COUNT(*) AS c FROM email_messages em "
                f"WHERE {clause}{extra}"
            ), params)).fetchone()
            return int(row.c) if row else 0

        in_range = await _count()
        eligible = await _count(unprocessed=skip_processed) \
            if skip_processed else in_range
        # Mail "Clean older mail" downloaded and deliberately kept away from the
        # model. It is eligible here — a deliberate, bounded, user-initiated run
        # is exactly the case the hold-back leaves room for — but the user should
        # know this range is mostly freshly-fetched history.
        held_back = await _count(
            extra=" AND em.rules_held_back_at IS NOT NULL", unprocessed=True)
    capped = max(0, min(limit, 2000))
    return {
        "in_range": in_range,
        "eligible": eligible,
        "already_processed": max(0, in_range - eligible),
        "held_back": held_back,
        "will_process": min(eligible, capped),
        "capped": eligible > capped,
        "limit": capped,
        "max_span_days": _PROCESS_PAST_MAX_SPAN_DAYS,
    }


@router.post("/rules/process-past")
async def process_past_emails(
    req: RuleProcessPastRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
):
    """Run rules over PAST inbox mail within a date range (inbox-zero parity).

    Unlike /rules/run (which only touches unprocessed mail), this reprocesses
    every inbox email whose received_at falls in [start_date, end_date]. Test
    mode logs a PENDING preview; Apply mode executes the matched actions. Poll
    GET /email/rules/history for results.

    Drafting is OFF unless ``draft_replies`` is set — see the field comment on
    RuleProcessPastRequest. Filing old mail is the point; replying to it costs a
    model call per message on conversations that have usually long since ended.
    """
    start_dt = _parse_iso_date(req.start_date, end_of_day=False)
    end_dt = _parse_iso_date(req.end_date, end_of_day=True)
    _assert_span_within_cap(start_dt, end_dt)
    only_unread = not req.include_read
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        # One run per mailbox. The job reads its rows once, so a second run over
        # the same range would classify, apply and draft each message twice. The
        # sibling jobs refuse the same way (reclassify, the cleanup sweep).
        if _PAST_JOBS.is_running(req.account_id):
            return {"scheduled": False, "already_running": True}
        # Best-effort pre-count of what's ALREADY synced locally — just a hint for
        # the caller. The job downloads the range from upstream first, so the real
        # total is recomputed there (the picker can reach past what's been synced).
        clause, params = _date_range_clause(
            req.account_id, start_dt, end_dt, only_unread, req.skip_processed)
        n = (await db.execute(text(
            f"SELECT COUNT(*) AS c FROM email_messages em "
            f"WHERE {clause}"
        ), params)).fetchone()
        count = int(n.c) if n else 0
        # Count what the skip excluded, so a run that finds nothing can say
        # "already done" instead of "no emails found in that range" — otherwise
        # the correct behaviour reads as the feature being broken.
        already_processed = 0
        if req.skip_processed:
            all_clause, all_params = _date_range_clause(
                req.account_id, start_dt, end_dt, only_unread)
            n_all = (await db.execute(text(
                f"SELECT COUNT(*) AS c FROM email_messages em "
                f"WHERE {all_clause}"
            ), all_params)).fetchone()
            already_processed = max(0, (int(n_all.c) if n_all else 0) - count)
    # Always schedule: the job first downloads [start, end] from the provider so a
    # range that predates the local sync still has mail to process, THEN counts +
    # applies. The tracker starts in the 'downloading' phase and the UI polls it
    # (there's no meaningful synchronous count to gate on before the backfill).
    token = _past_job_start(
        req.account_id, user.email or "anonymous", count, req.is_test,
        downloading=True, already_processed=already_processed)
    background.add_task(
        _process_past_emails_job, req.account_id, start_dt, end_dt,
        # `dry_run` IS `is_test`. This passed `not req.is_test` from f1a13861
        # until 2026-10-02, so an apply ran as a preview and a preview applied.
        min(req.limit, 2000), req.is_test, user.email or "anonymous",
        only_unread, token, req.draft_replies, req.skip_processed,
    )
    return {"scheduled": True, "count": count, "dry_run": req.is_test,
            "draft_replies": req.draft_replies,
            "skip_processed": req.skip_processed,
            "already_processed": already_processed}


@router.get("/rules/process-past/status")
async def process_past_status(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Live progress for the most recent 'Process past emails' run on this
    account so the UI can show an ongoing indicator + auto-refresh History.

    Returns {"status": "idle"} when no run has happened (or it belongs to a
    different user)."""
    job = _PAST_JOBS.get(account_id)
    if not job or job.get("owner") != (user.email or "anonymous"):
        return {"status": "idle"}
    return {k: v for k, v in job.items() if k != "owner"}


class RuleRunMessageRequest(BaseModel):
    account_id: str
    message_id: str
    is_test: bool = True  # True = dry-run preview; False = apply for real


@router.post("/rules/run-message")
async def run_rules_on_message(
    req: RuleRunMessageRequest,
    user: UserContext = Depends(get_current_user),
):
    """Run rules against a single message — the Test tab's per-row Test/Apply.

    `is_test=True` returns the matched rule + actions without touching the
    mailbox. `is_test=False` applies the matched rule's actions, logs an APPLIED
    (or SKIPPED) row to the history, and marks the message processed.
    """
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        row = (await db.execute(text(
            """SELECT id, provider_message_id, thread_id, subject, body_text,
                      snippet, from_address, to_addresses, cc_addresses,
                      received_at
               FROM email_messages
               WHERE id = :mid AND account_id = :aid"""
        ), {"mid": req.message_id, "aid": req.account_id})).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")

        frm = row.from_address if isinstance(row.from_address, dict) \
            else json.loads(row.from_address or "{}")
        about, _ = await _load_assistant_about(db, req.account_id)
        org_domains = await resolve_org_domains(db, req.account_id)
        attach = (await _attachment_summaries(db, [row.id])).get(str(row.id), "")
        email = email_dict_from_row(
            row, await _account_self_email(db, req.account_id), about,
            extra_domains=org_domains, attachments=attach)
        try:
            match = await _match_email_to_rule(db, req.account_id, email)
        except LLMUnavailable:
            # Classifier down — don't log SKIPPED or stamp the watermark (that
            # would consume the message unseen). Report it so the user retries.
            return {"matched": False, "applied": False, "rule": None,
                    "reason": "The AI classifier is temporarily unavailable — "
                              "try again in a moment.",
                    "actions": [], "unavailable": True}

        if req.is_test:
            if not match:
                return {"matched": False, "applied": False, "rule": None,
                        "reason": "No rule matched.", "actions": []}
            return {
                "matched": True, "applied": False,
                "rule": {"id": match["rule"]["id"], "name": match["rule"]["name"]},
                "reason": match["reason"],
                "actions": match["rule"]["actions"],
            }

        # Apply mode — load the provider and execute.
        if not match:
            await db.execute(text(
                """INSERT INTO email_executed_rules
                     (account_id, rule_id, rule_name, message_id,
                      provider_message_id, thread_id, subject, from_address,
                      status, automated, actions_taken, reason)
                   VALUES (:aid, NULL, NULL, :mid, :pmid, :tid, :subj, :frm,
                           'SKIPPED', true, '[]', 'No rule matched this email.')"""
            ), {"aid": req.account_id, "mid": str(row.id),
                "pmid": row.provider_message_id, "tid": row.thread_id,
                "subj": row.subject or "", "frm": frm.get("email", "")})
            return {"matched": False, "applied": False, "rule": None,
                    "reason": "No rule matched.", "actions": []}

        acc = (await db.execute(text(
            "SELECT provider, credentials_encrypted, user_id "
            "FROM email_accounts WHERE id = :id"
        ), {"id": req.account_id})).fetchone()
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found")
        from acb_llm.key_store import get_key_store  # noqa: PLC0415
        store = get_key_store()
        creds = json.loads(store.decrypt(acc.credentials_encrypted))
        provider = _instantiate_provider(acc.provider, creds)
        if not await provider.authenticate():
            raise HTTPException(status_code=502, detail="Provider auth failed")

        about, signature = await _load_assistant_about(db, req.account_id)
        # Multi-rule execution (inbox-zero parity): apply EVERY matching rule.
        mr_row = (await db.execute(text(
            "SELECT multi_rule_execution FROM email_assistant_settings "
            "WHERE account_id = :aid"
        ), {"aid": req.account_id})).fetchone()
        multi_rule = bool(mr_row and getattr(mr_row, "multi_rule_execution", None))
        # Through the shared enforcement point, so a single-message re-run obeys
        # the #110 conversation invariant too (it used to skip the resolve — the
        # run-message bypass). resolve=True because this is an apply, not a test.
        try:
            matches = await classify_matches(
                db, req.account_id, row, email,
                multi_rule=multi_rule, resolve=True, provider=provider,
            ) or [match]
        except LLMUnavailable:
            # Already have the primary single match; apply just that rather than
            # failing the whole apply on a second classifier call.
            matches = [match]
        # An apply (never a dry-run) with a guaranteed fallback match, so
        # log_no_match is off — this path always has something to apply.
        await _apply_matches(
            db, provider, row, frm, email, matches,
            apply=True, dry_run=False, about=about, signature=signature,
            account_user=acc.user_id, account_id=req.account_id,
            log_no_match=False,
        )
        await _stamp_processed_watermark(db, row.id, provider=provider)
        await _persist_rotated_creds(db, store, req.account_id, provider)
        # Return the row's POST-apply category + folder so the caller can refresh
        # its inbox row in place — the "Uncategorized" pill in the list reruns
        # this endpoint and needs the applied label to resolve without a full
        # refetch (the actions above may also have archived/moved the message).
        fresh = (await db.execute(text(
            "SELECT categories, folder FROM email_messages WHERE id = :id"
        ), {"id": str(row.id)})).fetchone()
        primary = matches[0]
        return {
            "matched": True, "applied": True,
            "rule": {"id": primary["rule"]["id"], "name": primary["rule"]["name"]},
            "reason": primary["reason"],
            "actions": primary["rule"]["actions"],
            "applied_rules": [m["rule"]["name"] for m in matches],
            "categories": list(fresh.categories or []) if fresh else [],
            "folder": fresh.folder if fresh else None,
        }


async def _apply_and_log_match(
    db: Any, provider: Any, r: Any, frm: dict[str, Any], email: dict[str, str],
    match: dict[str, Any], apply: bool, about: str, signature: str,
    account_user: str, account_id: str, *, sole_match: bool = True,
) -> None:
    """Apply one matched rule's actions (or compute a dry-run preview) and log an
    email_executed_rules row. Shared by the auto-run and process-past jobs so
    multi-rule execution behaves identically in both."""
    rule = match["rule"]
    if match.get("suppressed"):
        # Single thread classification: this rule DID match the message in
        # isolation, but the thread's classification won. Log it — History must
        # be able to answer "why wasn't this filed as a Receipt?" — and stop.
        # Guarded HERE, at the one choke point every apply path goes through,
        # so no future caller can accidentally run a suppressed match's
        # actions; the same shape as the pattern-write gate in rules._teach.
        await db.execute(text(
            """INSERT INTO email_executed_rules
                 (account_id, rule_id, rule_name, message_id,
                  provider_message_id, thread_id, subject, from_address,
                  status, automated, actions_taken, reason)
               VALUES (:aid, :rid, :rname, :mid, :pmid, :tid, :subj, :frm,
                       'SKIPPED', true, '[]', :reason)"""
        ), {"aid": account_id, "rid": rule.get("id"),
            "rname": rule.get("name"), "mid": str(r.id),
            "pmid": r.provider_message_id, "tid": r.thread_id,
            "subj": r.subject or "", "frm": frm.get("email", ""),
            "reason": ("Suppressed: part of a conversation — the thread's "
                       "status is the classification.")})
        return
    action_errors: list[dict[str, str]] = []
    pmid = r.provider_message_id
    if apply:
        # Outlook RE-KEYS a message when it moves: /move returns a new id and
        # the old one 404s. `r` was fetched once, BEFORE the per-match loop, so
        # with multi_rule_execution on (it is, on the live account) the second
        # rule called Graph with the id the first rule's move had just
        # invalidated. _apply_rule_actions already persists the new id, so
        # re-reading it here is enough — and it also picks up a re-key done by
        # anything else since the row was read.
        #
        # Live evidence: 138 rule applications in 30 days 404'd, 46 of them a
        # sibling rule acting after a successful MOVE_FOLDER in the same run.
        pmid = (await db.execute(text(
            "SELECT provider_message_id FROM email_messages WHERE id = :id"
        ), {"id": str(r.id)})).scalar() or pmid
        actions_taken = await _apply_rule_actions(
            db, provider, str(r.id), pmid,
            rule["actions"], email, about, signature, account_user,
            account_id=account_id, errors_out=action_errors,
        )
        # A row that changed NOTHING must not claim it did. APPLIED is what the
        # auto-learn gate counts as evidence and what Analytics counts as work
        # done, so logging a total failure as APPLIED both overstated the
        # assistant and let a rule that never touched the mailbox teach a
        # permanent sender pattern. Partial success stays APPLIED — the errors
        # ride along in action_errors.
        status = "FAILED" if (action_errors and not actions_taken) else "APPLIED"
        # inbox-zero parity: when the AI (not a static/learned-pattern rule)
        # picks a rule, cache the sender→rule as a learned FROM pattern so future
        # mail from that sender short-circuits the LLM. Consistency-gated like
        # inbox-zero's analyze-sender-pattern: only learn once the sender has
        # CONSISTENTLY matched this one rule (≥3 incl. now), so a single early
        # misclassification can't entrench itself. Best-effort.
        #
        # NEVER auto-learn a sender→rule pattern for the conversation-status rules
        # (Reply / Awaiting / FYI / Done): a person's mail flows between
        # those states per-thread, so pinning a sender to one (e.g. "accounts team
        # → always FYI") is wrong — and futile, since reply status is re-derived
        # from the full thread and overrides any learned pattern. Sender→rule only
        # makes sense for the stable cleanup categories (Newsletter/Receipt/etc.).
        sender = (frm.get("email") or "").strip()
        # `sole_match` is the multi-rule guard. With multi_rule_execution on
        # (it is, on the live account) one message legitimately matches several
        # rules, and _apply_and_log_match is called once per match IN A LOOP —
        # so the first rule's consistency check reads a log that does not yet
        # contain its own siblings, and the "no other rule ever matched this
        # sender" invariant is unenforceable. Live result: Info@yourstory.com
        # was pinned to Marketing although every one of its messages matches
        # Marketing + Newsletter or Marketing + Cold Email, and the pattern then
        # short-circuits the classifier down to the single pinned rule.
        #
        # Pinning a sender only means anything when the classification was
        # unambiguous, so an ambiguous message teaches nothing.
        #
        # A rule whose every action failed taught a pattern anyway, because the
        # gate only ever asked whether a rule MATCHED. Three 404s against a
        # message Outlook had already re-keyed were three votes for pinning that
        # sender forever, off a mailbox that was never touched.
        #
        # Order matters: the cheap local checks gate the one that costs a model
        # call, so the verdict is only ever asked about a sender that has
        # already earned it.
        if (status == "APPLIED"
                and sole_match and match.get("source") == "ai" and sender
                and rule.get("id")
                and not _is_conversation_status_rule(rule)
                and _is_auto_learnable_rule(rule)
                and not await _sender_is_a_correspondent(db, account_id, sender)
                and await _sender_consistent_for_rule(
                    db, account_id, sender, str(rule["id"]))
                and await _ai_confirms_sender_pattern(
                    db, account_id, sender, rule)):
            try:
                await _upsert_rule_pattern(
                    db, account_id, str(rule["id"]), sender, False, "AI",
                    "Auto-learned from a consistent AI match history", str(r.id),
                    getattr(r, "thread_id", None), pattern_type="FROM")
            except Exception as e:  # noqa: BLE001 — never fail a rule run on this
                _log.warning("email.auto_learn_pattern_failed",
                             account_id=account_id, error=str(e)[:160])
    else:
        actions_taken = [a["type"] for a in rule["actions"]]
        status = "PENDING"  # dry-run preview only
    await db.execute(text(
        """INSERT INTO email_executed_rules
             (account_id, rule_id, rule_name, message_id, provider_message_id,
              thread_id, subject, from_address, status, automated, actions_taken,
              reason, match_source, action_errors)
           VALUES (:aid, :rid, :rname, :mid, :pmid, :tid, :subj, :frm,
                   :status, true, :acts, :reason, :msrc,
                   CAST(:aerr AS JSONB))"""
    ), {"aid": account_id, "rid": rule["id"], "rname": rule["name"],
        # The id we actually CALLED, not the one the row was read with — when
        # they differ, the row was re-keyed mid-run and the log should say so.
        "mid": str(r.id), "pmid": pmid, "tid": r.thread_id,
        "subj": r.subject or "", "frm": frm.get("email", ""), "status": status,
        "acts": json.dumps(actions_taken), "reason": match["reason"],
        "msrc": match.get("source"), "aerr": json.dumps(action_errors)})


async def _apply_matches(
    db: Any, provider: Any, r: Any, frm: dict[str, Any], email: dict[str, str],
    matches: list[dict[str, Any]], *, apply: bool, dry_run: bool,
    about: str, signature: str, account_user: str, account_id: str,
    log_no_match: bool = True, cold_blocker: str | None = None,
) -> None:
    """Apply already-classified ``matches`` and log to ``email_executed_rules``
    — the match→apply tail shared by the live runner (``_run_rules_job``), the
    single-message re-run (``run_rules_on_message``), and process-past.

    Each match runs through ``_apply_and_log_match`` (``apply=False`` → dry-run
    preview only). When NOTHING matched, log one SKIPPED "No rule matched" row
    (unless ``log_no_match`` is False, e.g. the re-run always has a fallback
    match) and, if a ``cold_blocker`` policy is set and the run can act, let the
    cold-email blocker look.

    Deliberately does NOT stamp the processed watermark: some callers project
    Reply Zero status BETWEEN the apply and the stamp, so the watermark must stay
    last (a message is "processed" only once everything that reads it has run).
    Call ``_stamp_processed_watermark`` at the call site after that work.
    """
    if matches:
        for match in matches:
            await _apply_and_log_match(
                db, provider, r, frm, email, match, apply,
                about, signature, account_user, account_id,
                sole_match=len(matches) == 1,
            )
        return
    if dry_run or not log_no_match:
        return
    # No rule matched — log a SKIPPED row so History shows a "No match found"
    # entry, then (live runs only) let the cold-email blocker look.
    await db.execute(text(
        """INSERT INTO email_executed_rules
             (account_id, rule_id, rule_name, message_id,
              provider_message_id, thread_id, subject, from_address,
              status, automated, actions_taken, reason)
           VALUES (:aid, NULL, NULL, :mid, :pmid, :tid, :subj, :frm,
                   'SKIPPED', true, '[]', 'No rule matched this email.')"""
    ), {"aid": account_id, "mid": str(r.id),
        "pmid": r.provider_message_id, "tid": r.thread_id,
        "subj": r.subject or "", "frm": frm.get("email", "")})
    if cold_blocker is not None and cold_blocker != "OFF" and provider is not None:
        await _maybe_block_cold(
            db, provider, account_id, str(r.id),
            r.provider_message_id, email, cold_blocker,
        )


async def _stamp_processed_watermark(
    db: Any, message_id: Any, *, provider: Any, dry_run: bool = False,
) -> None:
    """Mark a message rules-processed — the guarded write every apply path shares.

    Stamps ONLY when the run could actually act (not a dry-run, and a provider
    was present). ``rules_processed_at`` is permanent — ``/rules/run`` selects
    ``rules_processed_at IS NULL`` — so stamping a message the run couldn't touch
    burns it forever. That is exactly what an expired refresh token used to do:
    one scheduler tick with a failed ``authenticate()`` marked 50 emails
    processed, applied nothing, and they were never looked at again.
    """
    if dry_run or provider is None:
        return
    await db.execute(text(
        "UPDATE email_messages SET rules_processed_at = now(), "
        "rules_held_back_at = NULL WHERE id = :id"
    ), {"id": str(message_id)})


async def _project_thread_status_for_backfill(
    provider: Any, account_id: str,
    latest_by_thread: dict[str, tuple[Any, list[dict[str, Any]]]],
) -> None:
    """Record Reply Zero status + collapse labels for threads a backfill touched.

    The backfill applied per-message labels but never wrote a thread status or
    cleared the superseded conversation labels, so a thread it walked could end
    up carrying Reply AND Awaiting AND Done while Reply Zero showed nothing for
    it at all — the status row and the labels being two views of one decision,
    with only the second written.

    Two deliberate restraints:

    * **Only threads whose newest message was actually in range.** If newer mail
      exists outside the date window, the newest message we saw is NOT the
      thread's newest, and projecting from it would move the thread backwards.
      Those are left for the periodic classifier, which always reads the true
      latest message.
    * **No AI.** This projects the rule the engine already matched
      (``project_reply_status_from_matches`` picks the highest-priority
      conversation rule deterministically). Re-determining each thread from its
      full text would be one model call per thread across a whole mailbox, on
      conversations that are usually long finished. The periodic classifier
      spends that budget where it pays — threads still sitting in the inbox.

    EM-T4a-0 (``email_app_master_plan.md`` §10.4.6): the stale read is one
    ``_tenant_session()`` block with the ambient tenant, and each thread is a
    block of its own, where the per-thread commit was. The handler wraps each
    thread's block, so a failed thread rolls back only its own writes.
    """
    from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
        _reconcile_thread_labels,
        project_reply_status_from_matches,
    )
    tids = list(latest_by_thread)
    # Threads with mail newer than what this run saw — skip them.
    async with _tenant_session() as db:
        stale = {
            str(row.thread_id)
            for row in (await db.execute(text(
                """SELECT em.thread_id, MAX(em.received_at) AS newest
                     FROM email_messages em
                    WHERE em.account_id = :aid AND em.thread_id = ANY(:tids)
                    GROUP BY em.thread_id"""
            ), {"aid": account_id, "tids": tids})).fetchall()
            if (seen := latest_by_thread.get(str(row.thread_id)))
            and getattr(seen[0], "received_at", None) is not None
            and row.newest is not None
            and row.newest > seen[0].received_at
        }
    for tid, (row, matches) in latest_by_thread.items():
        if tid in stale:
            continue
        try:
            async with _tenant_session() as db:
                keep_label = await project_reply_status_from_matches(
                    db, account_id, row, matches)
                if keep_label:
                    await _reconcile_thread_labels(
                        db, provider, account_id, tid, keep_label)
        except Exception as exc:  # noqa: BLE001 — one thread must not abort the rest
            _log.warning("email.past_project_status_failed",
                         account_id=account_id, thread_id=tid,
                         error=str(exc)[:160])


async def _process_past_emails_job(
    account_id: str, start: datetime | None, end: datetime | None,
    limit: int, dry_run: bool, user_email: str, only_unread: bool = False,
    job_token: int | None = None, draft_replies: bool = False,
    skip_processed: bool = True,
) -> None:
    """Background worker: process PAST inbox mail in a date range (inbox-zero
    'Process past emails'), oldest email first so rules/learning build up
    chronologically.

    By default this touches only mail the rules have never run over. The date
    picker is a range rather than a cursor, so re-running it — or widening it —
    otherwise re-covers everything already done, at one classification call per
    message, to rewrite labels those messages already carry. Pass
    ``skip_processed=False`` to deliberately re-apply after changing a rule.

    Updates the in-memory progress tracker (_PAST_JOBS) per email so the UI's
    'Processing N of M…' indicator advances live and History can auto-refresh.

    EM-T4a-0 (``email_app_master_plan.md`` §10.4.6): the process-past route
    starts this as a BackgroundTask, which runs inside the tenant scope of its
    request. The job runs in phases, and each phase is its own
    ``_tenant_session()`` block with that ambient tenant. No block calls
    ``commit()``. Phase 0 reads, and it ends where the commit before the
    provider call was. The provider authenticates with no session open. Each
    row is one block, where the per-row commit was. The thread projection
    opens its own blocks. With no tenant bound, phase 0 raises
    ``TenantUnbound``, and the handler records it on the progress row."""
    # Download the requested range from the provider BEFORE applying. The date
    # picker can reach back past what's been synced locally; without this the
    # range query below is simply empty and the feature no-ops ("No emails found
    # in that range."). A deep sync from the `start` floor backfills it; the apply
    # itself stays bounded to [start, end] via _date_range_clause. Best-effort —
    # a failed/partial backfill still applies over whatever IS present locally.
    try:
        from email_ingestion.scheduler import _sync_account  # noqa: PLC0415
        # Waits for a sync of this mailbox that runs now, with no session
        # open, then runs (EM-T4f part 2).
        await _sync_account(account_id, deep=True, since=start)
    except Exception as e:  # noqa: BLE001 — never abort the apply on a backfill error
        _log.warning("email.process_past_sync_failed",
                     account_id=account_id, error=str(e)[:200])
    # The ``finally`` below reads these two. A 401 in the apply loop refreshes
    # the token (EM-T4c), and the new tokens live in the provider only.
    provider: Any = None
    store: Any = None
    # The first block is INSIDE the try: if the pool is exhausted or Postgres
    # blips, the exception used to escape the BackgroundTask entirely, leaving
    # the tracker stuck on {"status": "running"} forever. The UI then polls
    # every 1.5s for the rest of the session behind a banner whose dismiss
    # button is hidden precisely because it thinks a run is in flight.
    try:
        # Phase 0: every read the loop needs.
        async with _tenant_session() as db:
            clause, params = _date_range_clause(
                account_id, start, end, only_unread, skip_processed)
            params["limit"] = limit
            rows = (await db.execute(text(
                f"""SELECT em.id, em.provider_message_id, em.thread_id, em.subject,
                           em.body_text, em.snippet, em.from_address,
                           em.to_addresses, em.cc_addresses, em.received_at
                    FROM email_messages em
                    WHERE {clause}
                    ORDER BY em.received_at ASC LIMIT :limit"""
            ), params)).fetchall()
            # The backfill may have pulled in mail the pre-schedule count
            # didn't see — record the real total and switch the tracker to
            # per-email progress.
            done_before = 0
            if skip_processed:
                seen_clause, seen_params = _date_range_clause(
                    account_id, start, end, only_unread)
                seen_clause += " AND em.rules_processed_at IS NOT NULL"
                seen = (await db.execute(text(
                    f"SELECT COUNT(*) AS c FROM email_messages em "
                    f"WHERE {seen_clause}"
                ), seen_params)).fetchone()
                done_before = int(seen.c) if seen else 0
            _past_job_begin_processing(account_id, token=job_token,
                                       total=len(rows),
                                       already_processed=done_before)
            if not rows:
                # Not an error, and not "no emails found": when done_before > 0
                # the range was simply already covered. The tracker carries
                # the number so the UI can say which of the two it was.
                _past_job_finish(account_id, token=job_token)
                return

            about, signature = await _load_assistant_about(db, account_id)
            owner_row = (await db.execute(text(
                "SELECT user_id, email_address FROM email_accounts WHERE id = :aid"
            ), {"aid": account_id})).fetchone()
            account_user = owner_row.user_id if owner_row else (user_email or "")
            self_email = (owner_row.email_address or "") if owner_row else ""
            mr_row = (await db.execute(text(
                "SELECT multi_rule_execution FROM email_assistant_settings "
                "WHERE account_id = :aid"
            ), {"aid": account_id})).fetchone()
            multi_rule = bool(
                mr_row and getattr(mr_row, "multi_rule_execution", None))
            org_domains = await resolve_org_domains(db, account_id)
            attach = await _attachment_summaries(db, [r.id for r in rows])
            acc = None
            if not dry_run:
                acc = (await db.execute(text(
                    "SELECT provider, credentials_encrypted FROM email_accounts "
                    "WHERE id = :id"
                ), {"id": account_id})).fetchone()

        # The provider authenticates with NO session open — a session parked
        # `idle in transaction` across a provider call is what queues a
        # migration's ALTER TABLE behind it (2026-08-06).
        if acc:
            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(acc.credentials_encrypted))
            provider = _instantiate_provider(acc.provider, creds)
            if not await provider.authenticate():
                provider = None

        # How many messages had a drafting action suppressed — surfaced on the
        # job tracker so the UI can say what was skipped rather than let the run
        # look like the rules ran in full.
        drafts_skipped = 0
        # Newest in-range message per thread + what it matched, for the Reply
        # Zero projection after the loop. This job cannot project inline the way
        # the live runner does: the live runner reads newest-first and takes the
        # first message per thread, while this one reads OLDEST-first, so
        # projecting inline would let a thread's oldest message decide its
        # status — the exact thing the live runner's ordering exists to prevent.
        latest_by_thread: dict[str, tuple[Any, list[dict[str, Any]]]] = {}

        for r in rows:
            frm = r.from_address if isinstance(r.from_address, dict) \
                else json.loads(r.from_address or "{}")
            email = email_dict_from_row(
                r, self_email, about, extra_domains=org_domains,
                attachments=attach.get(str(r.id), ""))
            # One block per row. It lands where the per-row commit used to
            # land. EM-T4a-4 owns the model and provider I/O inside it.
            async with _tenant_session() as db:
                try:
                    if multi_rule:
                        matches = await _match_email_to_rules_multi(
                            db, account_id, email)
                    else:
                        m = await _match_email_to_rule(db, account_id, email)
                        matches = [m] if m else []
                except LLMUnavailable as exc:
                    # Classifier down — don't stamp the watermark on mail it
                    # never evaluated (see _run_rules_job). The backfill can
                    # be re-run; the message stays rules_processed_at IS NULL
                    # and is picked up again.
                    _log.warning("email.process_past_classify_unavailable_skip",
                                 account_id=account_id, message_id=str(r.id),
                                 error=str(exc)[:160])
                    continue
                apply = (not dry_run) and provider is not None
                # Strip drafting unless the run explicitly asked for it, so a
                # 90-day backfill files old mail without spending a drafting
                # call per message on threads that ended months ago. Done as a
                # pre-pass so the apply loop itself is the shared
                # _apply_matches.
                if matches and not draft_replies:
                    stripped_matches = []
                    for match in matches:
                        match, stripped = _without_drafting(match)
                        if stripped:
                            drafts_skipped += 1
                        stripped_matches.append(match)
                    matches = stripped_matches
                await _apply_matches(
                    db, provider, r, frm, email, matches,
                    apply=apply, dry_run=dry_run, about=about,
                    signature=signature, account_user=account_user,
                    account_id=account_id,
                )
                await _stamp_processed_watermark(
                    db, r.id, provider=provider, dry_run=dry_run)
            # Remember the NEWEST in-range message per thread and what it
            # matched. Rows are oldest-first (deliberately, so learning builds
            # chronologically), so the last write per thread is its newest.
            if r.thread_id:
                latest_by_thread[r.thread_id] = (r, matches)
            _past_job_tick(
                account_id,
                token=job_token,
                applied=1 if matches else 0,
                skipped=0 if matches else 1,
            )

        if not dry_run and latest_by_thread:
            await _project_thread_status_for_backfill(
                provider, account_id, latest_by_thread)

        _past_job_finish(account_id, token=job_token,
                         drafts_skipped=drafts_skipped)
    except Exception as e:  # noqa: BLE001 — record failure for the UI, don't crash the worker
        _log.warning("email.process_past_failed", account_id=account_id, error=str(e)[:200])
        _past_job_finish(account_id, token=job_token, error=str(e))
    finally:
        # Keep the tokens that a refresh in the loop rotated (EM-T4c fix
        # round 2), on success and on failure alike. A short block of its
        # own, after the last provider call, and it never raises.
        if (provider is not None and store is not None
                and provider.credentials_dirty()):
            try:
                async with _tenant_session() as db:
                    await _persist_rotated_creds(db, store, account_id, provider)
            except Exception as exc:
                _log.warning("email.process_past_creds_keep_failed",
                             account_id=account_id, error=type(exc).__name__)


async def _run_rules_job(
    account_id: str, limit: int, dry_run: bool, user_email: str
) -> None:
    """Background worker: match UNPROCESSED inbox mail to rules and log/apply.

    Live runs (dry_run=False) apply every matched rule's actions and mark the
    message processed; no-match mail is logged SKIPPED so the History feed shows
    a "No match found" entry (inbox-zero parity). Dry runs only log a PENDING
    preview and never touch the mailbox.

    EM-T1b-2 (``email_app_master_plan.md`` §10.4.2): the job runs in phases,
    and each phase is its own ``_tenant_session()`` with the ambient tenant.
    The sync loop binds it for the auto-run hook, and the request binds it for
    the BackgroundTask. No phase calls ``commit()``: a commit part way ends
    ``SET LOCAL``, and each statement after it runs with no tenant. With no
    tenant bound, the first block raises ``TenantUnbound`` before it opens a
    session, and the broad handler logs it.
    """
    try:
        # Phase 0: every read the loop needs.
        async with _tenant_session() as db:
            rows = (await db.execute(text(
                """SELECT em.id, em.provider_message_id, em.thread_id, em.subject,
                          em.body_text, em.snippet, em.from_address,
                          em.to_addresses, em.cc_addresses, em.received_at
                   FROM email_messages em
                   WHERE em.account_id = :aid AND LOWER(em.folder) = 'inbox'
                     AND em.rules_processed_at IS NULL
                     AND em.rules_held_back_at IS NULL
                   ORDER BY em.received_at DESC LIMIT :limit"""
            ), {"aid": account_id, "limit": limit})).fetchall()
            if not rows:
                return

            about, signature = await _load_assistant_about(db, account_id)
            owner_row = (await db.execute(text(
                "SELECT user_id, email_address FROM email_accounts WHERE id = :aid"
            ), {"aid": account_id})).fetchone()
            account_user = owner_row.user_id if owner_row else (user_email or "")
            self_email = (owner_row.email_address or "") if owner_row else ""
            cold_blocker = "OFF"
            multi_rule = False
            cb_row = (await db.execute(text(
                "SELECT cold_email_blocker, multi_rule_execution, org_domains "
                "FROM email_assistant_settings WHERE account_id = :aid"
            ), {"aid": account_id})).fetchone()
            if cb_row and cb_row.cold_email_blocker:
                cold_blocker = cb_row.cold_email_blocker
            if cb_row and getattr(cb_row, "multi_rule_execution", None):
                multi_rule = bool(cb_row.multi_rule_execution)
            # Configured extra "your organisation" domains → direction-aware
            # classification (outbound/internal mail isn't a received category).
            # Via resolve_org_domains, like every other caller: reading the
            # column raw skipped normalize_domain, so a user who typed
            # "ops@acme.com" or "https://acme.com" got their org recognised on
            # the test/process-past paths and NOT on this one — the path that
            # actually processes new mail.
            org_domains = await resolve_org_domains(db, account_id)
            acc = None
            if not dry_run:
                acc = (await db.execute(text(
                    "SELECT provider, credentials_encrypted FROM email_accounts "
                    "WHERE id = :id"
                ), {"id": account_id})).fetchone()
            attach = await _attachment_summaries(db, [r.id for r in rows])

        # The provider authenticates with NO session open.
        provider = None
        store = None
        if acc:
            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(acc.credentials_encrypted))
            provider = _instantiate_provider(acc.provider, creds)
            if not await provider.authenticate():
                provider = None

        # Reply Zero (unified): project each thread's reply status from the rule
        # the engine matched. Rows are newest-first, so the first message seen per
        # thread is the latest — project only that one (older ones must not clobber
        # a newer status).
        projected_threads: set[str] = set()

        for r in rows:
            frm = r.from_address if isinstance(r.from_address, dict) \
                else json.loads(r.from_address or "{}")
            # extra_domains is a KEYWORD arg — passing it positionally lands it in
            # self_name and silently drops the configured org domains.
            email = email_dict_from_row(
                r, self_email, about, extra_domains=org_domains,
                attachments=attach.get(str(r.id), ""))
            # One block per row. It lands where the per-row commit used to
            # land. EM-T4 owns the model and provider I/O that stays inside.
            async with _tenant_session() as db:
                # Match + conversation-resolve, through the ONE shared
                # enforcement point (engine.classify_matches). Multi-rule
                # applies every match; otherwise the single best. resolve is
                # live-runs-only — the dry-run preview stays per-message and
                # spends no thread-status model call. The resolver re-evaluates
                # the whole thread so a conversation keeps its ONE status
                # (#110), even when the message matched no rule.
                try:
                    matches = await classify_matches(
                        db, account_id, r, email,
                        multi_rule=multi_rule, resolve=not dry_run,
                        provider=provider)
                except LLMUnavailable as exc:
                    # The classifier was down — this is NOT "no rule matched".
                    # Leave the message unstamped (skip the watermark below)
                    # so the next cycle retries it, instead of burning it as
                    # processed forever.
                    _log.warning("email.classify_unavailable_skip",
                                 account_id=account_id, message_id=str(r.id),
                                 error=str(exc)[:160])
                    continue
                apply = (not dry_run) and provider is not None
                await _apply_matches(
                    db, provider, r, frm, email, matches,
                    apply=apply, dry_run=dry_run, about=about,
                    signature=signature, account_user=account_user,
                    account_id=account_id, cold_blocker=cold_blocker,
                )
                # Reply Zero: project this thread's status from the matched
                # rule (latest message per thread only). Read-only of the
                # mailbox — runs even when the provider failed to authenticate.
                if not dry_run and r.thread_id \
                        and r.thread_id not in projected_threads:
                    projected_threads.add(r.thread_id)
                    # The savepoint keeps a failed projection from aborting the
                    # row block, so the watermark stamp below still lands.
                    try:
                        from gateway.routes.email.automation.replyzero import (  # noqa: PLC0415
                            _reconcile_thread_labels,
                            project_reply_status_from_matches,
                        )
                        async with _savepoint(db):
                            keep_label = await project_reply_status_from_matches(
                                db, account_id, r, matches)
                            # Collapse the thread to that one conversation
                            # label, clearing any stale Reply / Awaiting / FYI /
                            # Follow-up left on earlier messages (inbox-zero
                            # mutually-exclusive labels).
                            if keep_label and provider is not None:
                                await _reconcile_thread_labels(
                                    db, provider, account_id, r.thread_id,
                                    keep_label)
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("email.project_reply_status_failed",
                                     account_id=account_id,
                                     error=str(exc)[:160])
                # Stamp the watermark LAST — after Reply Zero projection — and
                # only when the run could actually act (guarded inside the
                # helper).
                await _stamp_processed_watermark(
                    db, r.id, provider=provider, dry_run=dry_run)

        if not dry_run and provider is None:
            _log.warning("email.run_rules_no_provider", account_id=account_id,
                         messages=len(rows),
                         reason="provider auth failed — nothing applied, "
                                "mail left unprocessed for the next run")

        if provider is not None and store is not None:
            async with _tenant_session() as db:
                await _persist_rotated_creds(db, store, account_id, provider)
    except Exception as exc:  # noqa: BLE001
        _log.warning("email.run_rules_failed", account_id=account_id,
                     error=str(exc)[:200])


