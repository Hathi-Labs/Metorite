"""Tasks · item_source — the ONE data seam for the AI and intake routes.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §5 **S6d** (this slice) and
§5 **S8** (which deletes the ``gtd_items`` arm below). Decision **D73**.
Board **WS-39**.

── Why two arms ─────────────────────────────────────────────────────────────

Six ``ai.py`` routes, five ``capture_email.py`` routes, ``email_link.py``,
``tasks/planning.py`` and the two WhatsApp intake modules used to read and
write ``gtd_items`` directly. That table is retiring: D52/D53 made
``pm_tasks`` + ``pm_task_personal`` the one task store, and every task a member
captures now lands there.

The cutover is a flag, ``TASKS_LENS``, read at CALL time. While it is off,
production still serves ``gtd_items`` and every one of those routes must keep
answering exactly as it does today. When it is on, the same routes must read
and write ``pm_*`` only, so a capture from an email appears in ``/projects``
under the caller's personal project in the same page load (§5 S7 criterion 3).

So the two stores are NOT modelled as an ``if`` inside every query. They are
modelled as a SOURCE: an object that answers the reads and writes these routes
perform. The LLM prompts, ``propose()``, ``propose_with_llm()`` and the
eval-locked helpers are all downstream of those calls and do not change,
which is the point. That code is where the behaviour lives, and re-deriving it
against a second store is how the two would drift.

This is the same shape ``routes/tasks/calendar.py`` uses for the day planner
(``TaskSource`` / ``agent_source()``), copied on purpose rather than
generalised: the planner and the intake routes read different rows, and one
ABC that served both would carry every method twice.

── Where the arms live ──────────────────────────────────────────────────────

* **``_GtdItems``** is HERE. It is the SQL the consumers carried, moved and
  not rewritten, so behaviour with the flag off cannot change. **S8 deletes
  this class** and the ``item_source()`` branch that returns it.
* **``_PmLens``** is in ``routes/projects/item_lens.py``, and its absence from
  this module is structural rather than tidiness. It needs ``MY_TASKS_FROM``,
  ``_upsert_personal``, ``ensure_personal_project`` and
  ``complete_for_member`` from the projects package, and that package imports
  this one. Naming it at module scope here closes an import cycle whose
  failure depends on which package a process loads first.

── The contract ─────────────────────────────────────────────────────────────

Every read returns rows wearing the attribute names the consumers already
read: ``id`` (str), ``title``, ``description``, ``project_id``,
``parent_item_id``, ``disposition``, ``context``, ``energy``,
``time_estimate_mins``, ``due_at``, ``assignee``, ``is_mine``, ``created_at``,
``defer_until``, ``source``, ``origin``. A source that renamed one would break
``_row_to_item`` or a prompt silently, so ``test_tasks_ai_source.py`` asserts
the parity rather than trusting it.

⚠️ **``disposition`` is the EFFECTIVE one.** On ``gtd_items`` it is a stored
column. On ``pm_*`` a member who has never triaged a task has no overlay row,
and the disposition is DERIVED from the task's status and assignment
(``derive_disposition``). The pm arm prunes in SQL on the stated value and
rules in Python, exactly as ``planning._PM_ALIVE`` does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from gateway.routes.tasks.core import DEFAULT_CONTEXTS, ITEM_SELECT, PROJECT_SELECT
from sqlalchemy import text

#: The origin keys a capture may look a task up by. Each has an expression
#: index on `pm_tasks` (211). A key outside this set is refused, because it
#: would be spliced into SQL as a literal.
ORIGIN_KEYS: frozenset[str] = frozenset({
    "email_id", "thread_id", "wa_message_id", "wa_chat_id",
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
    ``test_tasks_ai_source.py`` checks both arms override every read.
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

        Each arm writes its own column of ``wa_commitments``: ``gtd_item_id``
        on the retiring store, ``task_id`` (211) on the one store. The
        digest reads ``coalesce(task_id, gtd_item_id)`` until S8 drops the
        old column. No route writes this link yet. The WhatsApp capture
        route creates the task and leaves the commitment row untouched, as it
        did before this seam. It lives here so that the day a route links
        them, the column choice is already made in one place.
        """
        raise NotImplementedError


# ── Arm A: the retiring store ────────────────────────────────────────────────
#
# ⚠️ S8 deletes this class. Every statement below is the SQL its consumer
# carried on 2026-09-23, moved and not rewritten. A behaviour change with
# the flag OFF is a defect in this slice, and `test_tasks_ai_source.py`
# checks the shapes against the fake the consumers' tests already use.

_GTD_ALIVE = " AND disposition NOT IN ('DONE', 'TRASH')"


@dataclass(frozen=True)
class _GtdItems(ItemSource):
    """`gtd_items` + `gtd_waiting` + `gtd_projects` — the store S8 retires."""

    async def fetch_item(self, db, uid, item_id):
        from gateway.routes.tasks.items import _fetch_item

        return await _fetch_item(db, item_id, uid)

    async def open_items(self, db, uid, limit, *, top_level=False):
        narrow = (
            " AND parent_item_id IS NULL AND archived_at IS NULL"
            if top_level else ""
        )
        return (await db.execute(text(
            "SELECT id, title, disposition, source FROM gtd_items"
            " WHERE user_id = :uid" + _GTD_ALIVE + narrow
            + " ORDER BY created_at DESC LIMIT :lim"),
            {"uid": uid, "lim": limit})).fetchall()

    async def contexts_for(self, db, uid):
        try:
            rows = (await db.execute(text(
                """SELECT name FROM gtd_contexts WHERE user_id = :uid
                   ORDER BY sort_order, name"""), {"uid": uid})).fetchall()
            names = [r.name for r in rows if r.name]
            if names:
                return names
        except Exception:
            pass
        return [name for name, _icon in DEFAULT_CONTEXTS]

    async def projects_for(self, db, uid):
        return (await db.execute(
            text(PROJECT_SELECT + " WHERE p.user_id = :uid"), {"uid": uid},
        )).fetchall()

    async def siblings(self, db, uid, project_id, exclude_id, limit):
        return (await db.execute(text(
            """SELECT id, title FROM gtd_items
                WHERE user_id = :uid AND project_id = :pid
                  AND parent_item_id IS NULL
                  AND disposition NOT IN ('DONE', 'TRASH')
                  AND id <> :self
                ORDER BY updated_at DESC LIMIT :lim"""),
            {"uid": uid, "pid": project_id, "self": str(exclude_id),
             "lim": limit})).fetchall()

    async def local_tree(self, db, uid):
        local_spaces = (await db.execute(
            text("SELECT id, name FROM gtd_spaces WHERE user_id = :uid "
                 "ORDER BY sort_key ASC NULLS LAST, name"),
            {"uid": uid})).fetchall()
        local_folders = (await db.execute(
            text("SELECT id, space_id, name FROM gtd_folders "
                 "WHERE user_id = :uid ORDER BY sort_key ASC NULLS LAST, name"),
            {"uid": uid})).fetchall()
        return local_spaces, local_folders

    async def synced_candidates(self, db, uid):
        return (await db.execute(text(
            """SELECT i.id, i.title, i.provider_url, i.provider_status,
                      p.outcome AS project_name
                 FROM gtd_items i
                 LEFT JOIN gtd_projects p ON p.id = i.project_id
                WHERE i.user_id = :uid
                  AND i.source <> 'LOCAL'
                  AND i.parent_item_id IS NULL
                  AND i.disposition NOT IN ('DONE', 'TRASH')
                ORDER BY i.updated_at DESC
                LIMIT 400"""),
            {"uid": uid})).fetchall()

    async def assignee_load(self, db, uid):
        return (await db.execute(text(
            """SELECT assignee->>'provider_user_id' AS pid,
                      lower(assignee->>'name') AS nm,
                      lower(assignee->>'email') AS em, count(*) AS n
                 FROM gtd_items
                WHERE user_id = :uid
                  AND disposition IN ('NEXT', 'WAITING', 'CALENDAR', 'DO_NOW')
                  AND archived_at IS NULL
                  AND assignee IS NOT NULL
                GROUP BY 1, 2, 3"""), {"uid": uid})).fetchall()

    async def context_less_actionables(self, db, uid, limit):
        return (await db.execute(text(
            """SELECT * FROM gtd_items
               WHERE user_id = :uid
                 AND disposition IN ('NEXT', 'WAITING', 'CALENDAR')
                 AND (context IS NULL OR context = '')
                 AND archived_at IS NULL
               ORDER BY updated_at DESC LIMIT :lim"""),
            {"uid": uid, "lim": limit})).fetchall()

    async def set_context(self, db, uid, item_id, ctx):
        await db.execute(
            text("""UPDATE gtd_items SET context = :ctx, updated_at = now()
                    WHERE id = :id AND user_id = :uid"""),
            {"ctx": ctx, "id": str(item_id), "uid": uid})

    async def insight_counts(self, db, uid):
        counts = (await db.execute(text(
            """SELECT disposition, count(*) AS n FROM gtd_items
               WHERE user_id = :uid GROUP BY disposition"""), {"uid": uid},
        )).fetchall()
        oldest = (await db.execute(text(
            """SELECT min(created_at) AS oldest FROM gtd_items
               WHERE user_id = :uid AND disposition = 'INBOX'
                 AND (defer_until IS NULL OR defer_until <= now())"""),
            {"uid": uid})).fetchone()
        stale = (await db.execute(text(
            """SELECT count(*) AS n FROM gtd_waiting w
               JOIN gtd_items i ON i.id = w.item_id
               WHERE i.user_id = :uid AND w.resolved = false
                 AND w.delegated_at < now() - interval '5 days'"""),
            {"uid": uid})).fetchone()
        no_next = (await db.execute(text(
            """SELECT count(*) AS n FROM gtd_projects p
               WHERE p.user_id = :uid AND p.status = 'ACTIVE'
                 AND NOT EXISTS (SELECT 1 FROM gtd_items i
                                 WHERE i.project_id = p.id
                                   AND i.disposition = 'NEXT')"""),
            {"uid": uid})).fetchone()
        return {
            "counts": {r.disposition: r.n for r in counts},
            "oldest_inbox_at": oldest.oldest.isoformat()
            if oldest and oldest.oldest else None,
            "stale_waiting": stale.n if stale else 0,
            "projects_without_next_action": no_next.n if no_next else 0,
        }

    async def items_by_origin(
        self, db, uid, key, value, *, commitment=False, exclude_email_id=None,
        limit=None,
    ):
        where = (
            f" WHERE i.user_id = :uid AND i.{origin_key_sql(key)} = :val"
            " AND i.disposition NOT IN ('DONE', 'TRASH')"
        )
        params: dict[str, Any] = {"uid": uid, "val": str(value)}
        if commitment:
            where += " AND i.origin->>'commitment' = 'true'"
        if exclude_email_id is not None:
            where += " AND coalesce(i.origin->>'email_id', '') <> :eid"
            params["eid"] = str(exclude_email_id)
        where += " ORDER BY i.created_at DESC"
        if limit is not None:
            where += " LIMIT :lim"
            params["lim"] = limit
        return (await db.execute(
            text(ITEM_SELECT + where), params)).fetchall()

    async def insert_capture(self, db, uid, fields, origin):
        item_id = str(uuid4())
        assignee = fields.get("assignee")
        await db.execute(text(
            """INSERT INTO gtd_items
                   (id, user_id, title, description, disposition, next_action,
                    context, energy, time_estimate_mins, assignee, is_mine,
                    due_at, is_hard_date, defer_until, source, account_id,
                    sync_state, clarified_at, origin)
               VALUES
                   (:id, :uid, :title, :notes, :disp, :next_action,
                    :context, :energy, :est, :assignee, :is_mine,
                    :due_at, :is_hard, :defer_until, :source, :account_id,
                    :sync_state, :clarified_at, :origin)"""),
            {"id": item_id, "uid": uid, "title": fields["title"],
             "notes": fields.get("description") or None,
             "disp": fields.get("disposition") or "INBOX",
             "next_action": fields.get("next_action") or None,
             "context": fields.get("context") or None,
             "energy": fields.get("energy"),
             "est": fields.get("time_estimate_mins"),
             "assignee": json.dumps(assignee) if assignee else None,
             "is_mine": bool(fields.get("is_mine", True)),
             "due_at": fields.get("due_at"),
             "is_hard": bool(fields.get("is_hard_date", False)),
             "defer_until": fields.get("defer_until"),
             "source": fields.get("source") or "LOCAL",
             "account_id": fields.get("account_id"),
             "sync_state": fields.get("sync_state") or "local",
             "clarified_at": fields.get("clarified_at"),
             "origin": json.dumps(origin) if origin else None},
        )
        subtasks = [s for s in (fields.get("subtasks") or []) if str(s).strip()]
        if subtasks:
            from gateway.routes.tasks.items import _create_subtasks

            await _create_subtasks(
                db, uid, item_id, subtasks, fields.get("source") or "LOCAL",
                fields.get("account_id"), None,
                fields.get("sync_state") or "local")
        if fields.get("disposition") == "WAITING" and fields.get("waiting_on"):
            await self.record_waiting(
                db, uid, item_id, fields["waiting_on"],
                fields["delegated_at"])
        return item_id

    async def record_waiting(self, db, uid, item_id, who, delegated_at):
        await db.execute(text(
            """INSERT INTO gtd_waiting
                   (item_id, waiting_on, delegated_at)
               VALUES (:iid, :who, :now)"""),
            {"iid": item_id, "who": json.dumps(who), "now": delegated_at},
        )

    async def update_origin(self, db, uid, item_id, patch):
        await db.execute(text(
            "UPDATE gtd_items SET origin = "
            "coalesce(origin, '{}'::jsonb) || CAST(:patch AS jsonb) "
            "WHERE id = :id AND user_id = :uid"
        ), {"id": str(item_id), "uid": uid, "patch": json.dumps(patch)})

    async def mark_done_by_thread(self, db, uid, thread_id):
        rows = (await db.execute(text(
            "UPDATE gtd_items SET disposition = 'DONE', completed_at = now(), "
            "updated_at = now() "
            "WHERE user_id = :uid AND origin->>'thread_id' = :tid "
            "AND disposition NOT IN ('DONE', 'TRASH') "
            "RETURNING id"
        ), {"uid": uid, "tid": str(thread_id)})).fetchall()
        ids = [str(r.id) for r in rows]
        if ids:
            # A closed task can't be waited on — resolve its open waiting records.
            await db.execute(text(
                "UPDATE gtd_waiting SET resolved = true "
                "WHERE item_id = ANY(:ids) AND resolved = false"
            ), {"ids": ids})
        return ids

    async def plan_apply(self, db, uid, plan, tasks):
        from gateway.routes.tasks.planning import _due_from_offset

        project_id = str(uuid4())
        await db.execute(text(
            """INSERT INTO gtd_projects
               (id, user_id, source, outcome, purpose, status, has_next_action)
               VALUES (:id, :uid, 'LOCAL', :outcome, :purpose, 'ACTIVE', true)"""),
            {"id": project_id, "uid": uid, "outcome": plan.name.strip(),
             "purpose": (plan.description or None)})
        tasks_created = subtasks_created = 0
        rank = 0.0
        for t in tasks:
            item_id = str(uuid4())
            assignee = t.assignee if isinstance(t.assignee, dict) else None
            await db.execute(text(
                """INSERT INTO gtd_items
                   (id, user_id, title, next_action, description, disposition,
                    context, energy, project_id, source, sync_state, due_at,
                    assignee, is_mine, sort_key, clarified_at)
                   VALUES (:id, :uid, :title, :na, :descr, 'NEXT', :ctx, :energy,
                           :proj, 'LOCAL', 'local', :due, :assignee, :is_mine,
                           :rank, now())"""),
                {"id": item_id, "uid": uid, "title": t.title,
                 "na": t.title, "descr": t.description,
                 "ctx": t.context, "energy": t.energy, "proj": project_id,
                 "due": _due_from_offset(t.due_offset_days),
                 "assignee": json.dumps(assignee) if assignee else None,
                 "is_mine": assignee is None, "rank": rank})
            tasks_created += 1
            rank += 1000.0
            srank = 0.0
            for sub in t.subtasks:
                await db.execute(text(
                    """INSERT INTO gtd_items
                       (id, user_id, parent_item_id, title, next_action,
                        disposition, source, project_id, sync_state, sort_key,
                        clarified_at)
                       VALUES (:id, :uid, :pid, :title, :title, 'NEXT', 'LOCAL',
                               :proj, 'local', :rank, now())"""),
                    {"id": str(uuid4()), "uid": uid, "pid": item_id,
                     "title": sub, "proj": project_id, "rank": srank})
                subtasks_created += 1
                srank += 1000.0
        return {"project_id": project_id, "tasks_created": tasks_created,
                "subtasks_created": subtasks_created}

    async def link_commitment(self, db, uid, commitment_id, item_id):
        await db.execute(text(
            "UPDATE wa_commitments k SET gtd_item_id = :iid "
            "FROM wa_accounts a "
            "WHERE k.id = :cid AND a.id = k.account_id AND a.user_id = :uid"
        ), {"cid": str(commitment_id), "iid": str(item_id), "uid": uid})


GTD_ITEMS: ItemSource = _GtdItems(name="gtd_items")


def item_source() -> ItemSource:
    """The store the AI and intake routes read and write.

    Reads ``TASKS_LENS`` at CALL time through ``calendar.tasks_lens_enabled``,
    so there is one env read for the whole cutover and a test can set it
    around one call. Default OFF, and that default is load-bearing: see the
    docstring on ``tasks_lens_enabled``.

    The import of the pm arm is function-local ON PURPOSE. ``routes/projects``
    imports this package, so naming ``item_lens`` at module scope closes a
    cycle that fails only on whichever package a process loads first. At
    call time both modules are loaded and the lookup is free.
    """
    from gateway.routes.tasks.calendar import tasks_lens_enabled

    if not tasks_lens_enabled():
        return GTD_ITEMS
    from gateway.routes.projects.item_lens import PM_ITEMS

    return PM_ITEMS
