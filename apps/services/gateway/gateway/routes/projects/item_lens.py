"""Projects · the AI seam's pm arm — intake and AI routes over the ONE store.

Spec: ``project-docs/specs/my_tasks_cutover.md`` §5 **S6d** · **D73** ·
board **WS-39**.

This is arm B of ``routes/tasks/item_source.py``: the ``ItemSource`` the six
``ai.py`` routes, the email and WhatsApp captures, ``email_link.py`` and
``tasks/planning.py`` read and write when ``TASKS_LENS`` is on. It sits here
rather than beside its sibling because it needs ``_MY_TASKS_SQL``,
``_upsert_personal``, ``ensure_personal_project`` and ``complete_for_member``
from this package, and this package imports that one. Defining it there would
close an import cycle whose failure depends on which package loads first.
``routes/projects/planning.py`` made the same move for the day planner.

⚠️ **This module contains no second personal-project seam.** Which tasks are
mine is ``_MY_TASKS_SQL``, called. Where a capture lands is
``ensure_personal_project``, called. How an overlay is written is
``_upsert_personal``, called. How a task completes is ``complete_for_member``,
called. A copy of any of them here would be a mirror, and mirrors go stale and
then lie.

**Disposition is STATED where triaged and DERIVED otherwise**, and the
task's lane wins over both (D76). Every query prunes in SQL on the stated
column (``_PM_ALIVE``) and rules in Python with ``effective_disposition``,
exactly as the planner does. A SQL copy of the rule
would be the mirror the paragraph above refuses.

**Where a capture goes.** Into the caller's personal root, self-assigned,
with the routed fields on the caller's overlay and the provenance on
``pm_tasks.origin`` (211, §4.4). A Waiting-For is the four overlay columns
``waiting_on`` / ``delegated_at`` / ``expected_by`` / ``last_nudged_at``
(D53.8), never a second table.

**What has no meaning here.** A PM-tool mirror (``synced_candidates`` is
empty), a provider account, a workspace stage. The clarify proposal's
"destination" half reads the personal root and its children instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    from_jsonb,
    insert_row,
    load_default_status,
    now,
    require_organization_of,
    resolve_organization_id,
    resolve_visibility_for,
    status_owner_id,
)
from gateway.routes.projects.personal import (
    _MY_TASKS_SQL,
    DISPOSITIONS,
    IMPORTANT_AT,
    _as_utc,
    _upsert_personal,
    complete_for_member,
    create_personal_task,
    effective_disposition,
    ensure_personal_project,
    member_contexts,
    my_tasks_binds,
    not_yet,
    waiting_on_for,
)
from gateway.routes.projects.planning import _CLOSED_LANE, _PM_ALIVE
from gateway.routes.tasks.core import DEFAULT_CONTEXTS
from gateway.routes.tasks.item_source import ItemSource, origin_key_sql
from sqlalchemy import text

# ── Row shape ────────────────────────────────────────────────────────────────

#: Overlay columns copied straight off a `_MY_TASKS_SQL` row (`p_<name>`).
#: D76 took two names off this list: `time_estimate_mins` and `important`
#: are read off the SHARED task in `_pm_item`, under the same names.
_OVERLAY = (
    "next_action", "context", "energy", "defer_until",
    "clarified_at", "delegated_at", "expected_by", "last_nudged_at",
    "scheduled_start", "scheduled_end", "actual_start", "actual_end",
    "leveraged", "deep_work", "kept_mine", "sort_key",
)

#: The stored vocabulary is migration 147's. Two values the email router may
#: produce are views, not buckets, on the one store: the Calendar is `due_at`
#: with `is_hard_date`, and DO_NOW is a NEXT the member does at once.
_DISPOSITION_MAP = {"CALENDAR": "NEXT", "DO_NOW": "NEXT"}

#: Days of silence after `delegated_at` before a Waiting-For is stale. The
#: SAME rule as the client's `tasks/lib/waiting.ts::isStaleWaiting`
#: (`STALE_WAITING_DAYS`) and the gtd arm's `interval '5 days'`: strictly
#: more than five days since delegation, and nothing else. A nudge does not
#: reset it and a promised date does not enter it. `test_my_tasks.py` pins
#: all three together.
STALE_WAITING_DAYS = 5

#: `pm_projects.status` → the ACTIVE/ON_HOLD/DONE vocabulary the clarify brief
#: filters on (`ai._active_projects` reads `status == "ACTIVE"`).
_PROJECT_STATUS = {"active": "ACTIVE", "on_hold": "ON_HOLD", "done": "DONE",
                   "archived": "ARCHIVED"}


def pm_disposition(value: Any) -> str:
    """A router's disposition → one the overlay's CHECK accepts."""
    raw = str(value or "INBOX").strip().upper()
    raw = _DISPOSITION_MAP.get(raw, raw)
    return raw if raw in DISPOSITIONS else "INBOX"


def _pm_item(row: Any) -> SimpleNamespace:
    """A `_MY_TASKS_SQL` row, wearing the names the AI routes already read.

    `id`/`project_id`/`parent_item_id` are strings, `description` and `notes`
    are the same column, `origin` is decoded, and `disposition` is the
    EFFECTIVE one. `source`, `account_id` and the provider columns are what a
    LOCAL row reads as on the retiring store, so `_row_to_item` and
    `_find_synced_duplicate` need no branch.
    """
    stated = getattr(row, "p_disposition", None)
    flexible = getattr(row, "p_flexible", None)
    overlay = {name: getattr(row, f"p_{name}", None) for name in _OVERLAY}
    return SimpleNamespace(
        id=str(row.id),
        title=row.title,
        description=getattr(row, "description", None),
        notes=getattr(row, "description", None),
        project_id=str(row.project_id) if getattr(row, "project_id", None) else None,
        parent_item_id=(
            str(row.parent_task_id)
            if getattr(row, "parent_task_id", None) else None),
        status_id=str(getattr(row, "status_id", "") or "") or None,
        status_category=getattr(row, "status_category", None),
        stated_disposition=stated,
        disposition=effective_disposition(
            stated,
            status_category=getattr(row, "status_category", None),
            is_mine=bool(getattr(row, "is_mine", False)),
            has_assignee=int(getattr(row, "assignee_count", 0) or 0) > 0,
        ),
        is_two_minute=bool(getattr(row, "p_is_two_minute", False)),
        is_hard_date=bool(getattr(row, "p_is_hard_date", False)),
        flexible=True if flexible is None else bool(flexible),
        # D76 — the task's assignees minus me, the rule the inbox applies.
        waiting_on=waiting_on_for(
            getattr(row, "other_assignees", None),
            from_jsonb(getattr(row, "p_waiting_on", None)),
        ),
        # D76 — the two matrix and planning inputs that are shared facts.
        time_estimate_mins=getattr(row, "estimate_mins", None),
        important=(getattr(row, "importance", None) or 0) >= IMPORTANT_AT,
        due_at=getattr(row, "due_at", None),
        # D76 — the shared start date, for the inbox's "not yet" rule.
        start_date=getattr(row, "start_date", None),
        assignee=None,
        assignees=None,
        is_mine=bool(getattr(row, "is_mine", False)),
        source="LOCAL",
        account_id=None,
        account_provider=None,
        provider_task_id=None,
        provider_url=None,
        provider_status=None,
        sync_state="local",
        completed_at=getattr(row, "completed_at", None),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        archived_at=getattr(row, "archived_at", None),
        origin=from_jsonb(getattr(row, "origin", None)),
        workflow_stage=getattr(row, "workflow_stage", None),
        subtask_count=int(getattr(row, "subtask_count", 0) or 0),
        attachments=None,
        **overlay,
    )


def _alive(items: list[SimpleNamespace]) -> list[SimpleNamespace]:
    return [i for i in items if i.disposition not in ("DONE", "TRASH")]


#: The member's personal tree: the root and its children, in tree order.
#: `personal_owner` marks every row of it (191), and the tenant clause sits
#: on the read for the reason `MY_TASKS_FROM` carries one (WS-29b).
_PERSONAL_TREE_SQL = """
SELECT id::text AS id, name, description, status, parent_project_id,
       (parent_project_id IS NULL) AS is_root
  FROM pm_projects
 WHERE lower(personal_owner) = :who
   AND organization_id = CAST(:vis_org AS uuid)
   AND archived_at IS NULL
 ORDER BY parent_project_id NULLS FIRST, position NULLS LAST, name
"""

#: Open work per assignee, for the clarify roster's load hint. "Open" is
#: "not in a closing category and not archived", the reading the board
#: applies. ⚠️ The caller's grant closure is composed onto it at call time
#: (`Visibility.project_clause`), the way `people/dashboard.py::_totals`
#: scopes the same count. Without it one member's PRIVATE tasks are counted
#: into another member's roster card and prompt.
_ASSIGNEE_LOAD_SQL = (
    "SELECT lower(a.assignee) AS em, count(*) AS n"
    "  FROM pm_task_assignees a"
    "  JOIN pm_tasks t ON t.id = a.task_id"
    "  JOIN pm_task_statuses s ON s.id = t.status_id"
    " WHERE t.archived_at IS NULL"
    "   AND s.category NOT IN ("
    + ", ".join(f"'{c}'" for c in sorted(CLOSING_CATEGORIES))
    + ")"
)

_ONE_ITEM = _MY_TASKS_SQL + " AND t.id = CAST(:tid AS uuid)"


@dataclass(frozen=True)
class _PmLens(ItemSource):
    """`pm_tasks` + `pm_task_personal` — the one store, D53."""

    async def _binds(self, db: Any, uid: str, **params: Any) -> dict[str, Any]:
        # The AI never reasons over filed work. Tenant and grant closure come
        # from the one assembler `MY_TASKS_FROM` names (WS-39 S6a).
        return await my_tasks_binds(db, uid, archived=False, **params)

    async def _items(
        self, db: Any, uid: str, where: str = "", **params: Any,
    ) -> list[SimpleNamespace]:
        rows = (await db.execute(
            text(_MY_TASKS_SQL + where), await self._binds(db, uid, **params),
        )).fetchall()
        return [_pm_item(r) for r in rows]

    # ── reads ────────────────────────────────────────────────────────────

    async def fetch_item(self, db, uid, item_id):
        rows = (await db.execute(
            text(_ONE_ITEM), await self._binds(db, uid, tid=str(item_id)),
        )).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Item not found")
        return _pm_item(rows[0])

    async def open_items(self, db, uid, limit, *, top_level=False):
        narrow = " AND t.parent_task_id IS NULL" if top_level else ""
        return _alive(await self._items(
            db, uid,
            _PM_ALIVE + narrow + " ORDER BY t.created_at DESC LIMIT :lim",
            lim=limit))

    async def contexts_for(self, db, uid):
        names = [r.context for r in await member_contexts(db, uid.lower())
                 if r.context]
        return names or [name for name, _icon in DEFAULT_CONTEXTS]

    async def projects_for(self, db, uid):
        rows = (await db.execute(
            text(_PERSONAL_TREE_SQL), await self._binds(db, uid),
        )).fetchall()
        return [
            SimpleNamespace(
                id=str(r.id), name=r.name, outcome=r.name,
                purpose=r.description,
                status=_PROJECT_STATUS.get(str(r.status or "active"), "ACTIVE"),
                account_id=None, has_next_action=False,
                parent_project_id=(
                    str(r.parent_project_id) if r.parent_project_id else None),
                is_root=bool(r.is_root),
            )
            for r in rows
        ]

    async def siblings(self, db, uid, project_id, exclude_id, limit):
        return _alive(await self._items(
            db, uid,
            _PM_ALIVE
            + " AND t.project_id = CAST(:pid AS uuid)"
            " AND t.parent_task_id IS NULL"
            " AND t.id <> CAST(:self_id AS uuid)"
            " ORDER BY t.updated_at DESC LIMIT :lim",
            pid=str(project_id), self_id=str(exclude_id), lim=limit))

    async def local_tree(self, db, uid):
        # The personal root is the "Local (private)" place `_collect_places`
        # already emits. Its children are the spaces. There are no folders
        # and no stages: a personal Area is one level deep (191).
        children = [p for p in await self.projects_for(db, uid) if not p.is_root]
        return ([SimpleNamespace(id=p.id, name=p.name) for p in children], [])

    async def synced_candidates(self, db, uid):
        # D52: no PM-tool mirror exists in the one store.
        return []

    async def assignee_load(self, db, uid):
        vis = await resolve_visibility_for(db, uid.lower())
        rows = (await db.execute(
            text(_ASSIGNEE_LOAD_SQL
                 + " AND " + vis.project_clause("t.root_project_id")
                 + " GROUP BY 1"),
            dict(vis.params),
        )).fetchall()
        return [SimpleNamespace(pid=None, nm=None, em=r.em, n=int(r.n))
                for r in rows]

    async def context_less_actionables(self, db, uid, limit):
        items = await self._items(
            db, uid,
            # D76: a stated DONE on a reopened task reads NEXT, so it stays
            # in the prune and Python rules on the effective value.
            " AND (p.disposition IS NULL"
            "      OR p.disposition IN ('NEXT', 'WAITING', 'DONE'))"
            + _CLOSED_LANE +
            " AND (p.context IS NULL OR p.context = '')"
            " AND t.parent_task_id IS NULL"
            " ORDER BY t.updated_at DESC LIMIT :lim",
            lim=limit)
        return [i for i in items if i.disposition in ("NEXT", "WAITING")]

    async def insight_counts(self, db, uid):
        items = await self._items(db, uid)
        at = datetime.now(UTC)
        counts: dict[str, int] = {}
        oldest: datetime | None = None
        stale = 0
        next_in: set[str] = set()
        for it in items:
            counts[it.disposition] = counts.get(it.disposition, 0) + 1
            if it.disposition == "INBOX":
                # D76 — the inbox's own "not yet" rule: the later of my defer
                # and the shared start date (`DEFERRED_CLAUSE`), one place.
                created = _as_utc(it.created_at)
                waiting = not_yet(it.defer_until, it.start_date, at)
                if not waiting and created is not None and (
                    oldest is None or created < oldest
                ):
                    oldest = created
            elif it.disposition == "WAITING":
                delegated = _as_utc(it.delegated_at)
                if delegated is not None and \
                        delegated < at - timedelta(days=STALE_WAITING_DAYS):
                    stale += 1
            elif it.disposition == "NEXT" and it.project_id:
                next_in.add(it.project_id)
        children = [p for p in await self.projects_for(db, uid) if not p.is_root]
        return {
            "counts": counts,
            "oldest_inbox_at": oldest.isoformat() if oldest else None,
            "stale_waiting": stale,
            "projects_without_next_action": sum(
                1 for p in children if p.id not in next_in),
        }

    async def items_by_origin(
        self, db, uid, key, value, *, commitment=False, exclude_email_id=None,
        limit=None,
    ):
        where = _PM_ALIVE + f" AND t.{origin_key_sql(key)} = :val"
        params: dict[str, Any] = {"val": str(value)}
        if commitment:
            where += " AND t.origin->>'commitment' = 'true'"
        if exclude_email_id is not None:
            where += " AND coalesce(t.origin->>'email_id', '') <> :eid"
            params["eid"] = str(exclude_email_id)
        where += " ORDER BY t.created_at DESC"
        # The limit is applied AFTER the Python rule. Since D76 the SQL prune
        # drops a closed lane (`_CLOSED_LANE`), so no DONE row reaches here.
        # A stated TRASH is pruned too. The Python rule still runs, and the
        # limit after it is the safe place should the prune ever narrow.
        items = _alive(await self._items(db, uid, where, **params))
        return items[:limit] if limit is not None else items

    async def hard_dated_items(self, db, uid, *, days, limit):
        # `is_hard_date` is the member's overlay: the Calendar shows a task as
        # a fixed appointment for the member who marked it so (§4.4). The
        # limit applies after the Python rule, as in `items_by_origin`.
        items = _alive(await self._items(
            db, uid,
            _PM_ALIVE
            + " AND p.is_hard_date = true AND t.due_at IS NOT NULL"
            " AND t.due_at >= now()"
            " AND t.due_at <= now() + make_interval(days => :days)"
            " ORDER BY t.due_at ASC",
            days=days))
        return items[:limit]

    # ── writes ───────────────────────────────────────────────────────────

    async def _new_task(
        self, db: Any, who: str, project: Any, root_id: str, status_id: str,
        values: dict[str, Any], overlay: dict[str, Any] | None = None,
    ) -> str:
        """One task in the member's tree, through the ONE capture helper.

        `create_personal_task` is what `POST /projects/my/tasks` calls. It
        writes the row, the assignee, the overlay, the activity and the
        `pm.task.created` event, so a capture from an email announces itself
        to the workflow engine exactly as a typed one does.
        """
        task = await create_personal_task(
            db, who, str(project.id), root_id, status_id, values, overlay)
        return str(task.id)

    async def insert_capture(self, db, uid, fields, origin):
        who = uid.lower()
        root = await ensure_personal_project(db, who)
        root_id = str(root.id)
        status = await load_default_status(db, root_id)
        kind = str((origin or {}).get("kind") or "")
        source = "email" if kind == "email" else "manual"
        disposition = pm_disposition(fields.get("disposition"))
        task_id = await self._new_task(db, who, root, root_id, str(status.id), {
            "title": fields["title"],
            "description": fields.get("description") or None,
            "due_at": fields.get("due_at"),
            # D76: the drafter's estimate is the task's one estimate.
            "estimate_mins": fields.get("time_estimate_mins"),
            "source": source,
            "origin": origin,
        }, {
            "disposition": disposition,
            "next_action": fields.get("next_action") or None,
            "context": fields.get("context") or None,
            "energy": fields.get("energy") or None,
            "defer_until": fields.get("defer_until"),
            "is_hard_date": bool(fields.get("is_hard_date", False)),
            "clarified_at": fields.get("clarified_at"),
        })
        # Ranked by input order, the same arithmetic `plan_apply` uses, so
        # the steps keep the sequence the drafter gave them.
        rank = 0.0
        for sub in fields.get("subtasks") or []:
            title = str(sub or "").strip()
            if not title:
                continue
            await self._new_task(
                db, who, root, root_id, str(status.id), {
                    "title": title, "parent_task_id": task_id, "source": source,
                }, {
                    "disposition": "NEXT", "next_action": title,
                    "sort_key": rank, "clarified_at": now(),
                })
            rank += 1000.0
        if disposition == "WAITING" and fields.get("waiting_on"):
            await self.record_waiting(
                db, uid, task_id, fields["waiting_on"], fields["delegated_at"])
        return task_id

    async def record_waiting(self, db, uid, item_id, who, delegated_at):
        # D53.8: the Waiting-For IS the overlay. Nothing else to insert.
        await _upsert_personal(db, str(item_id), uid.lower(), {
            "disposition": "WAITING",
            "waiting_on": who,
            "delegated_at": delegated_at,
        })

    async def set_context(self, db, uid, item_id, ctx):
        # The 404 guard is the membership rule, called. Never write an overlay
        # on a task the member cannot see.
        await self.fetch_item(db, uid, item_id)
        await _upsert_personal(db, str(item_id), uid.lower(), {"context": ctx})

    async def update_origin(self, db, uid, item_id, patch):
        await self.fetch_item(db, uid, item_id)
        await db.execute(text(
            "UPDATE pm_tasks SET origin = "
            "coalesce(origin, '{}'::jsonb) || CAST(:patch AS jsonb), "
            "updated_at = now() "
            "WHERE id = CAST(:id AS uuid) "
            "AND organization_id = CAST(:vis_org AS uuid)"
        ), {"id": str(item_id), "patch": json.dumps(patch),
            "vis_org": await resolve_organization_id(db, uid.lower())})

    async def mark_done_by_thread(self, db, uid, thread_id):
        if not thread_id:
            return []
        who = uid.lower()
        closed: list[str] = []
        for item in await self.items_by_origin(db, uid, "thread_id", thread_id):
            await complete_for_member(db, item, who)
            closed.append(item.id)
        return closed

    async def plan_apply(self, db, uid, plan, tasks):
        from gateway.routes.tasks.planning import _due_from_offset

        who = uid.lower()
        root = await ensure_personal_project(db, who)
        root_id = str(root.id)
        # The plan becomes a child of the personal root: a private project
        # under "My tasks", at generation 2 of the tree grammar (193), which a
        # root at generation 1 may hold. `personal_owner` keeps it out of the
        # company board (191) and in the member's own list.
        project = await insert_row(db, "pm_projects", {
            "name": plan.name.strip(),
            "description": plan.description or None,
            "parent_project_id": root_id,
            "personal_owner": who,
            "created_by": who,
            "source": "manual",
            "kind": "project",
            "organization_id": await require_organization_of(db, who),
        })
        project_id = str(project.id)
        await db.execute(
            text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (CAST(:pid AS uuid), :who, :who) "
                "ON CONFLICT (project_id, subject) DO NOTHING"
            ),
            {"pid": project_id, "who": who},
        )
        status = await load_default_status(
            db, await status_owner_id(db, project_id))
        tasks_created = subtasks_created = 0
        rank = 0.0
        for t in tasks:
            assignee = t.assignee if isinstance(t.assignee, dict) else None
            overlay: dict[str, Any] = {
                "disposition": "NEXT", "next_action": t.title,
                "context": t.context, "energy": t.energy,
                "sort_key": rank, "clarified_at": now(),
            }
            if assignee is not None:
                # A task the plan hands to somebody else is one I monitor:
                # WAITING on them, the D53.8 shape. The row stays mine, in my
                # private project, as it did on the retiring store.
                overlay.update({
                    "disposition": "WAITING", "waiting_on": assignee,
                    "delegated_at": now(),
                })
            task_id = await self._new_task(
                db, who, project, root_id, str(status.id), {
                    "title": t.title,
                    "description": t.description,
                    "due_at": _due_from_offset(t.due_offset_days),
                    "source": "manual",
                }, overlay)
            tasks_created += 1
            rank += 1000.0
            srank = 0.0
            for sub in t.subtasks:
                await self._new_task(
                    db, who, project, root_id, str(status.id), {
                        "title": sub, "parent_task_id": task_id,
                        "source": "manual",
                    }, {
                        "disposition": "NEXT", "next_action": sub,
                        "sort_key": srank, "clarified_at": now(),
                    })
                subtasks_created += 1
                srank += 1000.0
        return {"project_id": project_id, "tasks_created": tasks_created,
                "subtasks_created": subtasks_created}

    async def link_commitment(self, db, uid, commitment_id, item_id):
        await db.execute(text(
            "UPDATE wa_commitments k SET task_id = CAST(:iid AS uuid) "
            "FROM wa_accounts a "
            "WHERE k.id = :cid AND a.id = k.account_id AND a.user_id = :uid"
        ), {"cid": str(commitment_id), "iid": str(item_id), "uid": uid})


PM_ITEMS: ItemSource = _PmLens(name="pm_tasks")
