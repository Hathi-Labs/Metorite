"""Tasks · item_source — the ONE data seam for the AI and intake routes.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §5 **S6d** (this slice) and
§5 **S8** (which deleted the retired arm). Decision **D73**.
Board **WS-39**.

── One arm since S8 ─────────────────────────────────────────────────────────

Six ``ai.py`` routes, five ``capture_email.py`` routes, ``email_link.py``,
``tasks/planning.py`` and the two WhatsApp intake modules read and write
tasks through this seam. S6d built it with two arms behind the
``TASKS_LENS`` flag. Production flipped the flag on 2026-09-23, and S8 PR 1
deleted the retired arm and the flag. ``item_source()`` now returns the one
store unconditionally. The seam function stays so the call sites do not
churn, and so a test can still swap the source.

The arm, ``_PmLens``, lives in ``routes/projects/item_lens.py``. It needs
``MY_TASKS_FROM``, ``_upsert_personal``, ``ensure_personal_project`` and
``complete_for_member`` from the projects package, and that package imports
this one. Naming it at module scope here closes an import cycle whose failure
depends on which package a process loads first.

── The contract ─────────────────────────────────────────────────────────────

Every read returns rows wearing the attribute names the consumers already
read: ``id`` (str), ``title``, ``description``, ``project_id``,
``parent_item_id``, ``disposition``, ``context``, ``energy``,
``time_estimate_mins``, ``due_at``, ``assignee``, ``is_mine``, ``created_at``,
``defer_until``, ``source``, ``origin``. A source that renamed one would break
``_row_to_item`` or a prompt silently, so ``test_tasks_ai_source.py`` asserts
the parity rather than trusting it.

⚠️ **``disposition`` is the EFFECTIVE one.** A member who has never triaged
a task has no overlay row, and the disposition is DERIVED from the task's status and assignment
(``derive_disposition``). The pm arm prunes in SQL on the stated value and
rules in Python, exactly as ``planning._PM_ALIVE`` does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: The origin keys a capture may look a task up by. A key outside this set is
#: refused, because it would be spliced into SQL as a literal.
#:
#: The first four have a partial expression index on `pm_tasks` (211).
#: `action_item_id` (a meeting action, WS-39 S8c) and `account_id` (the email
#: digest, S8c) have none on purpose. Each lookup runs inside one member's own
#: list (`MY_TASKS_FROM`), so the key is a residual filter on a few rows.
ORIGIN_KEYS: frozenset[str] = frozenset({
    "email_id", "thread_id", "wa_message_id", "wa_chat_id",
    "action_item_id", "account_id",
})


def origin_key_sql(key: str) -> str:
    """``origin->>'<key>'`` for an allowed key. Raises on any other."""
    if key not in ORIGIN_KEYS:
        raise ValueError(f"not an origin key: {key!r}")
    return f"origin->>'{key}'"


@dataclass(frozen=True)
class ItemSource:
    """One store, as the AI and intake routes need to see it.

    The base class raises on every method, so a source that forgot one fails
    at request time on whichever route nobody clicked during review.
    ``test_tasks_ai_source.py`` checks the arm overrides every read.
    """

    #: For error messages and the parity test. Never branched on.
    name: str

    # ── reads ────────────────────────────────────────────────────────────

    async def fetch_item(self, db: Any, uid: str, item_id: str) -> Any:
        """One of MY items, or a 404."""
        raise NotImplementedError

    async def open_items(
        self, db: Any, uid: str, limit: int, *, top_level: bool = False,
    ) -> list[Any]:
        """My open items, newest first. ``top_level`` drops subtasks and
        archived rows (the "you may already have this" list)."""
        raise NotImplementedError

    async def contexts_for(self, db: Any, uid: str) -> list[str]:
        """My @context vocabulary, or the defaults before I have one."""
        raise NotImplementedError

    async def projects_for(self, db: Any, uid: str) -> list[Any]:
        """My projects, as the clarify brief reads them: ``id``, ``outcome``,
        ``status``, ``account_id``."""
        raise NotImplementedError

    async def siblings(
        self, db: Any, uid: str, project_id: str, exclude_id: str, limit: int,
    ) -> list[Any]:
        """Open top-level tasks in one project, other than ``exclude_id``."""
        raise NotImplementedError

    async def local_tree(self, db: Any, uid: str) -> tuple[list[Any], list[Any]]:
        """The places a task can be FILED into, as ``(spaces, folders)`` rows
        of ``id``/``name`` and ``id``/``space_id``/``name``. The clarify
        proposal's destination half (``_collect_places``) reads them."""
        raise NotImplementedError

    async def synced_candidates(self, db: Any, uid: str) -> list[Any]:
        """Open tasks mirrored from a PM tool. Empty where none exist."""
        raise NotImplementedError

    async def assignee_load(self, db: Any, uid: str) -> list[Any]:
        """Open task counts per assignee: rows of ``pid``, ``nm``, ``em``, ``n``."""
        raise NotImplementedError

    async def context_less_actionables(
        self, db: Any, uid: str, limit: int,
    ) -> list[Any]:
        """My actionable items with no context, for the backfill."""
        raise NotImplementedError

    async def insight_counts(self, db: Any, uid: str) -> dict[str, Any]:
        """The four inbox signals ``GET /tasks/insights`` returns."""
        raise NotImplementedError

    async def items_by_origin(
        self, db: Any, uid: str, key: str, value: str, *,
        commitment: bool = False, exclude_email_id: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        """My OPEN items whose origin carries ``key = value``."""
        raise NotImplementedError

    async def find_by_origin(
        self, db: Any, uid: str, key: str, value: str, *,
        commitment: bool = False,
    ) -> Any | None:
        """The first of ``items_by_origin``, or None. The idempotency read."""
        rows = await self.items_by_origin(
            db, uid, key, value, commitment=commitment, limit=1)
        return rows[0] if rows else None

    async def hard_dated_items(
        self, db: Any, uid: str, *, days: int, limit: int,
    ) -> list[Any]:
        """My OPEN hard-date items due from now to ``days`` ahead, soonest
        first. The Calendar's "fixed appointment" predicate. The email drafter
        reads it to offer slots that do not clash (WS-39 S8c)."""
        raise NotImplementedError

    # ── writes ───────────────────────────────────────────────────────────

    async def insert_capture(
        self, db: Any, uid: str, fields: dict[str, Any],
        origin: dict[str, Any] | None,
    ) -> str:
        """Write one captured item and return its id.

        ``fields`` carries the capture as the routers resolve it: ``title``,
        ``description``, ``disposition``, ``next_action``, ``context``,
        ``energy``, ``time_estimate_mins``, ``assignee``, ``is_mine``,
        ``due_at``, ``is_hard_date``, ``defer_until``, ``source``,
        ``account_id``, ``sync_state``, ``clarified_at``, ``subtasks`` and,
        for a WAITING capture, ``waiting_on``.
        """
        raise NotImplementedError

    async def record_waiting(
        self, db: Any, uid: str, item_id: str, who: dict[str, Any],
        delegated_at: datetime,
    ) -> None:
        """Open a Waiting-For record on one of my items."""
        raise NotImplementedError

    async def set_context(
        self, db: Any, uid: str, item_id: str, ctx: str,
    ) -> None:
        raise NotImplementedError

    async def update_origin(
        self, db: Any, uid: str, item_id: str, patch: dict[str, Any],
    ) -> None:
        """Merge ``patch`` into the item's origin."""
        raise NotImplementedError

    async def mark_done_by_thread(
        self, db: Any, uid: str, thread_id: str,
    ) -> list[str]:
        """Close my open items captured from one email thread. Returns ids."""
        raise NotImplementedError

    async def plan_apply(
        self, db: Any, uid: str, plan: Any, tasks: list[Any],
    ) -> dict[str, Any]:
        """Materialise a plan locally: a project, a task per plan task, and
        its subtasks. Returns ``project_id``, ``tasks_created``,
        ``subtasks_created``."""
        raise NotImplementedError

    async def link_commitment(
        self, db: Any, uid: str, commitment_id: str, item_id: str,
    ) -> None:
        """Point a WhatsApp commitment at the task it was captured into.

        The arm writes ``wa_commitments.task_id`` (211). The digest reads
        the old column too, through a coalesce, until S8 PR 2 drops it. No
        route writes this link yet. The WhatsApp capture
        route creates the task and leaves the commitment row untouched, as it
        did before this seam. It lives here so that the day a route links
        them, the column choice is already made in one place.
        """
        raise NotImplementedError


def item_source() -> ItemSource:
    """The store the AI and intake routes read and write: the one store.

    The import of the arm is function-local ON PURPOSE. ``routes/projects``
    imports this package, so naming ``item_lens`` at module scope closes a
    cycle that fails only on whichever package a process loads first. At
    call time both modules are loaded and the lookup is free.
    """
    from gateway.routes.projects.item_lens import PM_ITEMS

    return PM_ITEMS
