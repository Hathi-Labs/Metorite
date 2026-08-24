"""Provider interface layer — the PM-agnostic contract (§5.2/§5.5).

🔴 **THE REGISTRY IS EMPTY, AND THAT IS THE DECISION — not a gap to fill.**
**D52** (2026-08-24, board WS-39 S1) retired ClickUp outright: Metorite is the
project-management system of record, so there is no external PM system to
connect to and none is planned. ``ClickUpProvider`` — the only connector this
layer ever had — was deleted with it, along with the poll scheduler, the webhook
receiver and both importers.

**Do not add a connector here.** If a future decision reverses D52 it will say so
by name in ``work_plan.md`` §3; until then, an implementation in this registry is
a second write path into the task store, which is what D53 exists to prevent.

⚠️ **This whole framework is scheduled for deletion.** It is the GTD-side
connector layer, and WS-39 **S3a** retires the GTD store it serves (D53) — at
which point ``BaseTaskProvider``, ``task_accounts``, ``sync.py`` and this module
go together. It survives S1 only because deleting it now would cascade into ~100
call sites across the Tasks app that S3a rewrites anyway. Left in place, fenced
by ``test_no_task_provider_connectors``, rather than half-removed.

Credentials were per-account (decrypted from ``task_accounts``), NOT
process-wide env vars. Writes were user-approved only (constraint C-04: staged
as ``sync_state='pending'`` until the user explicitly pushed; the Action Broker
took over the gating in Phase 4).
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from typing import Any

from acb_common import get_logger
from fastapi import HTTPException

_log = get_logger("gateway.tasks.providers")


class ProviderError(HTTPException):
    """A provider call failed — surfaced with the upstream detail."""

    def __init__(self, provider: str, detail: str, status_code: int = 502):
        super().__init__(status_code=status_code, detail=f"{provider}: {detail}")


def _broker_enforced(action: str) -> bool:
    """Whether ``ACTION_BROKER_ENFORCE`` routes *action* to the approval QUEUE.

    Default (unset / ``none`` / ``off``) → ``False`` → writes AUTO-APPLY: they
    are already user-approved (staged ``sync_state='pending'`` → the user pushes),
    so the broker only audits + chokepoints them. Set the env var to
    ``1``/``all``/``on`` to queue every write, or to a comma-list of action names
    to queue specific ones. This is the kill-switch — flip it without a redeploy
    (env var + service restart). Persistent handlers ARE registered at startup
    (``tasks/broker_handlers.py``), so an approved queued write really executes:
    since BO-1a **every** action name gated here has a ``_WRITERS`` entry, fenced
    by ``tests/unit/test_task_broker_handlers.py``.

    ⚠️ **DO NOT FLIP THIS ON. BO-1a and BO-1b did not make the flip safe** —
    they cleared the handler-ROUTING blocker (an approved queued write executes)
    and the sync-STATE blocker (a queued push writes ``awaiting_approval``, not
    a false ``synced``). A third class is still open, ticketed as **BO-1d**
    (``FOUNDATION_BUILDOUT_CHECKLIST.md`` §BO-1): four callers of a gated write
    never read the pending marker this gate returns, so under enforcement

    * ``routes/tasks/accounts.py`` — ``created["id"]`` on the pending marker →
      **KeyError → HTTP 500** on ``POST /tasks/accounts/{id}/projects`` and on
      ``POST /tasks/accounts/{id}/folders``;
    * ``routes/tasks/planning.py`` — ``list_ref = created["id"]`` → the same
      **500** on ``POST /tasks/plan/apply``;
    * ``routes/tasks/items.py::_push_patch_upstream`` — no 500, but a member's
      edit to a synced task reports local success with **nothing upstream and no
      state saying so**.

    ⚠️ **Re-measured 2026-08-24 (D52).** Those three sites were reachable only
    through the ClickUp connector, and the registry is now empty — so today they
    cannot be *reached*, which is not the same as being *fixed*. **BO-1d stays
    open** and stays the named blocker on the flip, because the defect is in the
    CALLERS' contract with this gate (they index a marker as a result), not in
    ClickUp: the same shape is waiting for the next gated writer. Do not close
    BO-1d on the strength of D52 — the ticket outlives the connector.

    (``planning.py`` already defends against this shape at its per-task create
    below the list create, which is why the gap is a known one in that file
    rather than a new discovery.) Flipping the switch is an OWNER action
    (``work_plan.md`` §6) and now has a **named blocking ticket** — plus a
    residual BO-1d does not clear either: a ``pending_actions`` row queued before
    the flip is approvable afterwards, so check
    ``SELECT action, status FROM pending_actions`` first.
    """
    import os

    raw = (os.environ.get("ACTION_BROKER_ENFORCE") or "").strip().lower()
    if not raw or raw in ("0", "none", "off", "false"):
        return False
    if raw in ("1", "all", "on", "true"):
        return True
    return action in {a.strip() for a in raw.split(",") if a.strip()}


class BaseTaskProvider(ABC):
    """The canonical contract every connector implements (§5.5).

    Only the calls the capture/clarify slice needs are in v1; sync/webhooks
    grow here later. All methods are async and raise ``ProviderError`` on
    upstream failure.
    """

    provider: str = "base"

    def _broker_actor(self) -> str:
        """Identity recorded on the audit/proposal for an outward write.
        Never a secret — subclasses may add a workspace/account id."""
        return f"tasks:{self.provider}"

    async def _broker_gate(
        self, action: str, target: str,
        audit_payload: dict[str, Any], do_write,
    ) -> dict[str, Any]:
        """Route an outward provider write through the Action Broker — the single
        audited chokepoint for source-of-truth writes (AGENTS.md #4).

        These writes are already user-approved (staged → the user pushes), so the
        DEFAULT disposition AUTO-APPLIES: the broker audits the write and
        ``do_write`` runs immediately, returning the provider result unchanged.
        ``ACTION_BROKER_ENFORCE`` can flip an action to the approval QUEUE
        (returns a ``pending`` marker; the write does not run until approved).

        Fail-safe: a broker-layer error never blocks a user-approved write —
        ``do_write`` still runs. ``do_write`` executes **exactly once** (its own
        errors, e.g. an HTTP failure, propagate untouched).
        """
        try:
            from action_broker import (
                AuthorityTier,
                Disposition,
                enqueue,
                propose,
            )

            queued = _broker_enforced(action)
            proposal = propose(
                self._broker_actor(), action, target, audit_payload,
                authority=(AuthorityTier.SUGGEST_APPLY if queued
                           else AuthorityTier.AUTONOMOUS),
                destructive=queued,
            )
            disposition = proposal.disposition
        except Exception as exc:  # broker unavailable → never lose a user write
            _log.warning("broker.gate_bypass", action=action, error=str(exc))
            return await do_write()

        if disposition == Disposition.NEEDS_APPROVAL:
            action_id = None
            with contextlib.suppress(Exception):
                action_id = enqueue(proposal)
            _log.info("broker.write_queued", action=action, action_id=action_id)
            return {"pending": True, "pending_action_id": action_id,
                    "provider_task_id": ""}
        if disposition == Disposition.REJECTED:
            raise ProviderError(
                self.provider, f"write {action} rejected by authority policy")
        return await do_write()

    @abstractmethod
    async def verify(self) -> dict[str, Any]:
        """Validate credentials → {user: {name, email, provider_user_id}}."""

    @abstractmethod
    async def list_workspaces(self) -> list[dict[str, Any]]:
        """Workspaces/teams this credential can reach → [{id, name, member_count}]."""

    @abstractmethod
    async def get_schema(self, workspace_id: str) -> dict[str, Any]:
        """The fetched-beforehand schema (§2.2.1) for one workspace:
        {projects: [{id, name, space}], members: [PersonDict], statuses: [str]}."""

    @abstractmethod
    async def create_task(
        self, project_ref: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a task in the tool (user-approved push) →
        {provider_task_id, provider_url, provider_status}."""

    async def update_task(
        self, provider_task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Back-sync an edit to an existing task (user-initiated). ``payload``
        carries only the changed fields (any of: title, description, status,
        due_at_ms, assignee_id, clear_assignee) → returns the refreshed
        {provider_task_id, provider_url, provider_status}. Default raises so a
        connector that hasn't implemented writes fails loudly rather than
        silently dropping the edit."""
        raise ProviderError(self.provider, "update_task not supported", 501)

    async def delete_task(self, provider_task_id: str) -> None:
        """Delete a task in the tool (user-approved, propagated deletion). A
        connector that hasn't implemented deletes raises rather than silently
        leaving the upstream task behind."""
        raise ProviderError(self.provider, "delete_task not supported", 501)

    async def archive_task(self, provider_task_id: str, archived: bool = True) -> None:
        """Archive (or un-archive) a task in the tool — the reversible,
        non-destructive counterpart to delete_task. Used both for an explicit
        Archive and for a Delete (we archive rather than hard-delete upstream, so
        the task is recoverable in the connected tool). A connector that hasn't
        implemented it raises rather than silently diverging from the mirror."""
        raise ProviderError(self.provider, "archive_task not supported", 501)

    async def list_statuses_for_task(self, provider_task_id: str) -> list[str]:
        """The ordered status names of THIS task's own list/project (status
        vocabularies vary per project). Used to translate a local Next-Actions
        stage back into a concrete upstream status for THIS task on a board drag.
        Best-effort: empty list when it can't be resolved (→ caller skips the
        upstream write and keeps the move local)."""
        return []

    async def get_task_detail(self, provider_task_id: str) -> dict[str, Any]:
        """Fetch the rich, on-demand detail of one task for the detail view:

        {comments: [{id, author, text, created_at_ms}],
         attachments: [{id, name, url, mime, size}],
         subtasks: [{provider_task_id, title, status, status_type,
                     assignees:[...], provider_url}]}

        Read-only; called when a task's detail panel opens. Default returns
        empty sections so a connector without rich detail degrades gracefully."""
        return {"comments": [], "attachments": [], "subtasks": []}

    @abstractmethod
    async def list_members(self, workspace_id: str) -> list[dict[str, Any]]:
        """CURRENT workspace members → [{name, email, provider_user_id}].
        The live source of truth for the delegate picker — people removed in
        the tool must disappear here (schema_cache is just the warm copy)."""

    @abstractmethod
    async def create_project(
        self, workspace_id: str, name: str,
        space_id: str, folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a project/list in the tool under the given space (and
        optional folder) → {id, name, space_id, space_name?, folder_id?}.
        A user-approved write (invoked from the explicit "create project"
        UI action, same posture as push)."""

    async def create_folder(
        self, workspace_id: str, space_id: str, name: str,
    ) -> dict[str, Any]:
        """Create a folder under a space (the grouping level between space and
        list) → {id, name, space_id}. A user-approved write from the picker's
        "new folder" action. Default raises so a connector without folders
        fails loudly rather than silently dropping the create."""
        raise ProviderError(self.provider, "create_folder not supported", 501)

    @abstractmethod
    async def list_tasks(
        self, workspace_id: str, *, updated_since_ms: int | None = None
    ) -> list[dict[str, Any]]:
        """Pull the workspace's tasks (the sync read, §5.5) as canonical dicts:

        {provider_task_id, provider_url, title, description, status,
         status_type ('open'|'closed'|…), assignees: [{name, email,
         provider_user_id}], due_at_ms, created_at_ms, updated_at_ms,
         closed_at_ms, project_ref}

        ``updated_since_ms`` enables incremental pulls (only tasks updated
        after that epoch-ms); ``None`` = full pull. Closed tasks ARE included
        so completions propagate to the GTD overlay.
        """


# ── Registry ─────────────────────────────────────────────────────────────────

#: Deliberately EMPTY — see the module docstring. D52 retired the only entry.
#: ``build_provider`` therefore refuses every name, which is the correct answer
#: while Metorite is itself the system of record.
_CONNECTORS: dict[str, type[BaseTaskProvider]] = {}


def connector_names() -> list[str]:
    return sorted(_CONNECTORS)


def build_provider(
    provider: str, creds: dict[str, Any], workspace_id: str | None = None,
    account_id: str | None = None,
) -> BaseTaskProvider:
    """Instantiate a connector from its name + decrypted credentials.

    ``account_id`` (the ``task_accounts`` row) is optional — pass it on WRITE
    paths so a broker-queued write can re-resolve the token on approval."""
    cls = _CONNECTORS.get(provider)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    token = creds.get("api_token") or creds.get("token") or ""
    if not token:
        raise HTTPException(status_code=400, detail=f"{provider}: missing api_token")
    return cls(token, workspace_id, account_id)
