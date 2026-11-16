"""The reads of the write check, made as the member (LOOP-18).

Spec: ``project-docs/specs/proactive_loop.md`` §4.9.2 and §4.9.3.

Each read goes through the one seam that owns it, and no read writes a
visibility rule of its own:

* **Mail** goes through ``mailbox_predicate``, the one mailbox access seam
  (``routes/email/mailbox_access.py``). A message of a mailbox that the member
  cannot read gives no row.
* **Tasks** go through ``task_visibility_clause`` and ``load_visible_task`` of
  ``routes/projects/core.py``. A task that the member cannot open gives
  ``None``, the answer of a task that does not exist.

Each read opens its own tenant session from ``get_db()``'s seam
(``gateway.db.tenant_session``). It reads a small bounded page, so the check
stays inside its budget. Fence: ``tests/unit/test_write_check_r8.py``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from acb_auth import UserContext
from fastapi import HTTPException
from sqlalchemy import text

from gateway.db import tenant_session
from gateway.routes.email.mailbox_access import mailbox_predicate
from gateway.write_check import file_stem_words

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: Rows read for each question. The detectors need a few, never a page.
SENT_LIMIT = 50
OPEN_TASK_LIMIT = 200


def _addr_list(*values: Any) -> list[str]:
    out: list[str] = []
    for v in values:
        for item in v or []:
            if isinstance(item, dict) and item.get("email"):
                out.append(str(item["email"]).strip().lower())
    return out


class SqlReader:
    """The reader of one member. ``user`` is the session's, never a request value."""

    def __init__(self, user: UserContext) -> None:
        self.user = user
        self.email = (user.email or "").strip().lower()

    # ── Mail ────────────────────────────────────────────────────────────────

    async def sent_mail(self, recipients: Sequence[str], since: datetime,
                        exclude_thread_of: str | None) -> list[dict[str, Any]]:
        """Mail that the member sent to any of *recipients* since *since*.

        The thread of the email that a reply answers is left out: a reply in a
        long thread is not a resend (§4.9.3).
        """
        if not recipients:
            return []
        params: dict[str, Any] = {
            "uid": self.email, "since": since, "addrs": [r.lower() for r in recipients],
            "lim": SENT_LIMIT,
            "excl": exclude_thread_of if exclude_thread_of and _UUID.match(exclude_thread_of) else None,
        }
        sql = f"""
            SELECT em.id, em.account_id, em.thread_id, em.subject, em.received_at,
                   em.to_addresses, em.cc_addresses,
                   (SELECT string_agg(a.filename, E'\\n') FROM email_attachments a
                     WHERE a.message_id = em.id) AS files
              FROM email_messages em
              JOIN email_accounts ea ON ea.id = em.account_id
             WHERE {mailbox_predicate("ea", uid="uid", level="read")}
               AND LOWER(COALESCE(em.folder, '')) = 'sent'
               AND em.received_at >= :since
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(
                        COALESCE(em.to_addresses, '[]'::jsonb)
                        || COALESCE(em.cc_addresses, '[]'::jsonb)) r
                    WHERE LOWER(r->>'email') = ANY(:addrs))
               AND (CAST(:excl AS uuid) IS NULL OR em.thread_id IS DISTINCT FROM (
                        SELECT x.thread_id FROM email_messages x
                         WHERE x.id = CAST(:excl AS uuid)))
             ORDER BY em.received_at DESC
             LIMIT :lim"""
        async with tenant_session() as db:
            rows = (await db.execute(text(sql), params)).fetchall()
        out = []
        for r in rows:
            stems: set[str] = set()
            for name in (r.files or "").split("\n"):
                stems |= file_stem_words(name)
            out.append({
                "id": str(r.id), "account_id": str(r.account_id),
                "thread_id": str(r.thread_id) if r.thread_id else None,
                "subject": r.subject or "", "sent_at": r.received_at,
                "to": _addr_list(r.to_addresses, r.cc_addresses),
                "file_stems": frozenset(stems),
            })
        return out

    async def thread_newer(self, email_id: str) -> list[dict[str, Any]]:
        """Messages that arrived on the thread of *email_id* after it, from other people."""
        if not _UUID.match(email_id or ""):
            return []
        sql = f"""
            SELECT m2.id, m2.account_id, m2.thread_id, m2.received_at
              FROM email_messages em
              JOIN email_accounts ea ON ea.id = em.account_id
              JOIN email_messages m2 ON m2.thread_id = em.thread_id
                                    AND m2.account_id = em.account_id
             WHERE em.id = CAST(:eid AS uuid)
               AND {mailbox_predicate("ea", uid="uid", level="read")}
               AND em.thread_id IS NOT NULL
               AND m2.received_at > em.received_at
               AND LOWER(COALESCE(m2.folder, '')) NOT IN ('sent', 'drafts', 'trash')
               AND LOWER(COALESCE(m2.from_address->>'email', '')) <> LOWER(ea.email_address)
             ORDER BY m2.received_at DESC
             LIMIT 20"""
        async with tenant_session() as db:
            rows = (await db.execute(text(sql), {"eid": email_id, "uid": self.email})).fetchall()
        return [{"id": str(r.id), "account_id": str(r.account_id),
                 "thread_id": str(r.thread_id), "received_at": r.received_at} for r in rows]

    # ── Tasks ───────────────────────────────────────────────────────────────

    async def task_state(self, task_id: str) -> dict[str, Any] | None:
        """One task as the member may open it, or None."""
        from gateway.routes.projects.core import (
            CLOSING_CATEGORIES,
            load_visible_task,
            resolve_visibility,
        )

        if not _UUID.match(task_id or ""):
            return None
        async with tenant_session() as db:
            vis = await resolve_visibility(db, self.user)
            try:
                task = await load_visible_task(db, vis, task_id)
            except HTTPException:
                return None  # not visible: no trace
            lane = (await db.execute(
                text("SELECT category FROM pm_task_statuses WHERE id = CAST(:s AS uuid)"),
                {"s": str(task.status_id)})).fetchone()
            assignees = (await db.execute(
                text("SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
                {"t": task_id})).fetchall()
            mine = (await db.execute(
                text("SELECT last_nudged_at FROM pm_task_personal "
                     "WHERE task_id = CAST(:t AS uuid) AND member_email = :m"),
                {"t": task_id, "m": self.email})).fetchone()
        return {
            "id": str(task.id),
            "done": bool(lane is not None and getattr(lane, "category", None) in CLOSING_CATEGORIES),
            "assignees": [str(a.assignee).lower() for a in assignees],
            "last_nudged_at": getattr(mine, "last_nudged_at", None),
        }

    async def open_tasks(self, project_id: str) -> list[dict[str, Any]]:
        """Open tasks of ONE project that the member may open."""
        from gateway.routes.projects.core import (
            CLOSING_CATEGORIES,
            resolve_visibility,
            task_visibility_clause,
        )

        if not _UUID.match(project_id or ""):
            return []
        async with tenant_session() as db:
            vis = await resolve_visibility(db, self.user)
            rows = (await db.execute(text(
                "SELECT t.id, t.title FROM pm_tasks t "
                "JOIN pm_task_statuses s ON s.id = t.status_id "
                "WHERE t.project_id = CAST(:p AS uuid) "
                "AND NOT (s.category = ANY(:closed)) "
                f"AND {task_visibility_clause(vis)} "
                "ORDER BY t.created_at DESC LIMIT :lim"),
                {"p": project_id, "closed": sorted(CLOSING_CATEGORIES),
                 "lim": OPEN_TASK_LIMIT, **vis.params})).fetchall()
        return [{"id": str(r.id), "title": r.title or ""} for r in rows]
