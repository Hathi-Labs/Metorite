"""Projects · activities — the task timeline, comments, and revert.

Spec: ``project-docs/specs/project_management_app.md`` §4 (``activities.py``).

    GET    /projects/tasks/{id}/timeline
    POST   /projects/tasks/{id}/comments
    PATCH  /projects/comments/{activity_id}
    DELETE /projects/comments/{activity_id}
    POST   /projects/activities/{activity_id}/revert

Comments and system events share one table, discriminated by ``type`` (§3.8) —
the shape Paca, trycompai and our own ``crm_activities`` all converged on. The
comment routes are spelled ``/comments`` rather than ``/activities`` because a
caller may only ever create, edit or delete that one type: a route that accepted
any type would let a client forge a ``status_change`` nobody performed.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    ActivityModel,
    Page,
    _tenant_session,
    actor,
    emit,
    from_jsonb,
    load_visible_task,
    now,
    record_activity,
    record_field_change,
    resolve_visibility,
    router,
    row_to_dict,
    touch_task,
    update_row,
)
from gateway.routes.projects.notifications import (
    excerpt_of,
    mention_targets,
    new_mentions,
    notify,
    task_audience,
)
from gateway.routes.projects.watchers import ensure_watchers
from pydantic import BaseModel
from sqlalchemy import text

#: Columns ``revert`` is willing to restore. Everything a ``field_change``
#: activity can record is here EXCEPT the structural ones (`project_id`,
#: `parent_task_id`, `type_id`): those move a task between hierarchies and
#: scopes, and undoing one by writing a bare column would skip the root
#: re-stamping and the status re-point that ``/move`` owes. Reverting a move is
#: a move.
_REVERTIBLE: frozenset[str] = frozenset({
    "title", "description", "importance", "due_at", "start_date", "estimate_mins",
})

#: The prefix `patch_task` files a custom-field change under (WS-27l).
#:
#: Custom fields ARE revertible, and deliberately so: the whole reason §3.8
#: stores both ends of a change is diff-and-revert, and a field that can be
#: edited from the panel but not undone from the timeline is a second-class
#: field. They cannot go in ``_REVERTIBLE`` because that set names COLUMNS —
#: a custom value is one key inside the `custom_fields` object, so restoring it
#: is a merge rather than an assignment.
_CUSTOM_PREFIX = "custom."


class CommentIn(BaseModel):
    body: str
    #: Migration 208 — the comment this one answers. Absent means top level.
    #:
    #: Only ``POST`` reads it. An EDIT deliberately cannot re-parent a comment:
    #: moving somebody's reply under a different question changes what it
    #: appears to say, and no surface asks for it.
    parent_id: str | None = None


#: How deep a comment thread goes. Owner ruling, 2026-09-21: *"Limit the
#: depth. I think just one layer of reply should be fine."*
#:
#: A root and its replies, and that is all. The reason to write it down as a
#: number rather than as an `if` is that the client caps the SAME thing —
#: `TaskPanel` offers Reply on a root and not on a reply — and two places
#: enforcing one rule should be able to point at each other.
MAX_COMMENT_DEPTH = 1


async def _parent_comment(db: Any, parent_id: str, task_id: str) -> Any:
    """The comment a reply may attach to, or a 4xx that says why not.

    Four refusals, and each one is a thing a client could otherwise create
    that nothing downstream knows how to draw:

    * **Not found** covers a deleted parent and a bad id together, for
      `_load_own_comment`'s reason — "no such row" and "not yours to see" are
      one answer.
    * **A different task.** A reply is read inside its task's thread. Attached
      across tasks it is invisible in one place and orphaned in the other.
    * **Not a comment.** Answering a `status_change` is answering an event
      nobody wrote. The type is on the OTHER row, which is exactly why no
      CHECK in migration 208 can say this.
    * **Already a reply.** The depth cap. Refused rather than silently
      re-parented onto the root: a comment that quietly answers something
      other than what the author picked is worse than an error, because the
      author never finds out.
    """
    row = (await db.execute(
        text(
            "SELECT id, type, task_id, parent_id FROM pm_activities "
            "WHERE id = CAST(:pid AS uuid) AND deleted_at IS NULL"
        ),
        {"pid": parent_id},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    if str(row.task_id) != str(task_id):
        raise HTTPException(
            status_code=422,
            detail="A reply must be on the same task as the comment it answers.",
        )
    if row.type != "comment":
        raise HTTPException(
            status_code=422, detail="You can only reply to a comment.",
        )
    if row.parent_id is not None:
        raise HTTPException(
            status_code=422,
            detail="Replies go one level deep. Reply to the first comment instead.",
        )
    return row


#: What a timeline read may ask for, and the SQL that narrows it.
#:
#: Owner request, 2026-09-21: *"we should separate out activity and comments
#: because the comments are getting muddled up with the activity"*. Two lists
#: on the surface, and each one has to paginate on its own — a client that
#: fetched `all` and filtered in the browser would show "5 of 50" where 50 is
#: mostly the other list, and its "show older" button would fetch a page that
#: is entirely the wrong kind.
#:
#: ⚠️ A FILTER on the one endpoint, never a second route (§5). The store is
#: still one table and the stream is still one stream — `kind` narrows the
#: read, and `all` remains the default so every existing caller is unchanged.
_KIND_CLAUSES: dict[str, str] = {
    "all": "",
    "comments": " AND type = 'comment'",
    "events": " AND type <> 'comment'",
}


@router.get("/tasks/{task_id}/timeline")
async def get_timeline(
    task_id: str,
    kind: str = "all",
    user: UserContext = Depends(get_current_user),
    page: Page = Depends(),
) -> dict:
    """One task's timeline, newest first.

    Deleted comments are withheld; system events have no ``deleted_at`` and are
    never withheld — the history of what happened to a task is not editable by
    the people it happened to.

    ``kind`` narrows the read to ``comments`` or to ``events``; the default
    ``all`` is the whole stream, which is what every caller before 2026-09-21
    asked for. ``total`` counts what the filter matched, not the table, because
    a "show 45 older" button computed from the wrong total lies.

    ⚠️ **A reply can arrive without its root**, on any page but the last: the
    order is by time and a root can be older than the page boundary. The
    client promotes such a reply to top level rather than dropping it —
    ``activityStream.ts::threadComments``, and its suite pins that. Paging the
    thread as a unit would mean ordering by root and breaking the one property
    a timeline owes, which is that it reads in time order.
    """
    clause = _KIND_CLAUSES.get(kind)
    if clause is None:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of {', '.join(sorted(_KIND_CLAUSES))}.",
        )
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        params = {
            "tid": task_id, "limit": page.limit, "offset": page.offset,
        }
        total = (await db.execute(
            text(
                "SELECT count(*) FROM pm_activities "
                "WHERE task_id = CAST(:tid AS uuid) AND deleted_at IS NULL"
                + clause
            ),
            {"tid": task_id},
        )).scalar() or 0
        rows = (await db.execute(
            text(
                "SELECT * FROM pm_activities "
                "WHERE task_id = CAST(:tid AS uuid) AND deleted_at IS NULL "
                + clause +
                # `seq`, not `id` — see migration 213. This one pages, and
                # an unstable sort under LIMIT/OFFSET is the classic
                # duplicate-or-skip bug: two pages are two queries, and
                # nothing made them agree about a tied group.
                " ORDER BY created_at DESC, seq DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )).fetchall()
        return {
            "rows": [row_to_dict(r, ActivityModel) for r in rows],
            "total": int(total),
        }


@router.post("/tasks/{task_id}/comments", status_code=201)
async def add_comment(
    task_id: str, payload: CommentIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="A comment needs a body.")
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        # ⚠️ AFTER the visibility check, never before. Asked first, the parent
        # lookup would answer 404 or 422 for a task the caller may not see,
        # and the difference between those two says whether a comment exists.
        if payload.parent_id is not None:
            await _parent_comment(db, payload.parent_id, task_id)
        row = await record_activity(
            db, activity_type="comment", created_by=actor(user),
            task_id=task_id, body=body, parent_id=payload.parent_id,
        )
        # WS-27v — commenting subscribes the commenter, BEFORE the audience is
        # read so their row exists for the next event too. Harmless for this
        # one: `notify` never addresses the actor (rule 1).
        await ensure_watchers(db, task_id, [actor(user)], by=actor(user))
        # WS-27j. Two audiences, and the order matters: a person who is BOTH
        # mentioned and a watcher/assignee should get the mention, because "you
        # were named" is a stronger claim on their attention than "the task you
        # follow got a comment". `notifiable` dedupes within a call, so the
        # audience pass is handed only whoever the mention pass did not take.
        mentioned = mention_targets(body)
        snippet = excerpt_of(body)
        mention_result = await notify(
            db, recipients=mentioned, kind="mention", task_id=task_id,
            actor_id=actor(user), activity_id=str(row.id), excerpt=snippet,
        )
        # Being @mentioned subscribes you — but only the DELIVERED mentions:
        # a person who cannot open the task must not be silently enrolled in a
        # stream of its titles they could never have asked for.
        await ensure_watchers(
            db, task_id, mention_result["notified"], by=actor(user),
        )
        rest = [
            who for who in await task_audience(db, task_id)
            if who.strip().lower() not in set(mentioned)
        ]
        await notify(
            db, recipients=rest, kind="comment", task_id=task_id,
            actor_id=actor(user), activity_id=str(row.id), excerpt=snippet,
        )
        if mention_result["notified"]:
            # On the timeline too, not only in the recipients' bells. Somebody
            # reading the task later has to be able to see why a colleague
            # turned up in the thread; otherwise the reason is recorded only in
            # rows that one person can read.
            await record_activity(
                db, activity_type="mention", created_by=actor(user),
                task_id=task_id,
                meta={"mentioned": mention_result["notified"]},
            )
        result = row_to_dict(row, ActivityModel)
        # Surfaced, not swallowed: a mention that reached nobody would
        # otherwise leave the author believing they pulled a colleague in.
        result["not_notified"] = mention_result["skipped"]

    await emit("pm.task.comment_added", {"task_id": task_id})
    return result


async def _load_own_comment(db: Any, activity_id: str, user: UserContext) -> Any:
    """A comment the caller wrote, or 404.

    Authorship is the boundary, and it is checked in the same query that finds
    the row so "someone else's comment" and "no such comment" are one answer
    (R5). Editing another member's words is not an admin action either — there
    is deliberately no override.
    """
    row = (await db.execute(
        text(
            "SELECT * FROM pm_activities WHERE id = CAST(:aid AS uuid) "
            "AND type = 'comment' AND deleted_at IS NULL "
            "AND lower(created_by) = :who"
        ),
        {"aid": activity_id, "who": actor(user).lower()},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return row


@router.patch("/comments/{activity_id}")
async def edit_comment(
    activity_id: str, payload: CommentIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="A comment needs a body.")
    async with _tenant_session() as db:
        comment = await _load_own_comment(db, activity_id, user)
        # The task must still be visible: a member removed from a Center keeps
        # authorship of what they wrote but not access to it.
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, str(comment.task_id))
        row = await update_row(db, "pm_activities", activity_id, {"body": body})
        task_id = str(comment.task_id)
        # WS-27ae / P-27 — an edit UPDATEs an existing activity row rather than
        # inserting one, so it never reaches `record_activity`'s bump. Reworded
        # comments are exactly the change a delta client must not miss.
        await touch_task(db, task_id)
        # WS-27v — mention DIFFING: only the addresses this edit ADDED are
        # notified. The old body is the one loaded above, before the update, so
        # fixing a typo next to `@priya@…` re-pings nobody, while appending
        # `@ravi@…` reaches exactly Ravi. Editing your own comment is a touch,
        # so the editor (re-)subscribes too — idempotently, since they already
        # subscribed when they commented.
        added = new_mentions(getattr(comment, "body", None), body)
        mention_result = await notify(
            db, recipients=added, kind="mention", task_id=task_id,
            actor_id=actor(user), activity_id=activity_id,
            excerpt=excerpt_of(body),
        )
        await ensure_watchers(
            db, task_id, [actor(user), *mention_result["notified"]],
            by=actor(user),
        )
        if mention_result["notified"]:
            # On the timeline, as add_comment does: why a colleague turned up
            # must be readable by everyone, not only in one person's bell.
            await record_activity(
                db, activity_type="mention", created_by=actor(user),
                task_id=task_id,
                meta={"mentioned": mention_result["notified"]},
            )
        result = row_to_dict(row, ActivityModel)
        result["not_notified"] = mention_result["skipped"]
        return result


@router.delete("/comments/{activity_id}")
async def delete_comment(
    activity_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Soft-delete a comment.

    Soft because the timeline is a record: a hard delete would leave replies
    referring to something that never existed. The body is cleared as well as
    the row hidden, so "deleted" means the words are gone rather than merely
    filtered out of one read path.
    """
    async with _tenant_session() as db:
        comment = await _load_own_comment(db, activity_id, user)
        await update_row(
            db, "pm_activities", activity_id,
            {"deleted_at": now(), "body": None},
        )
        # WS-27ae / P-27 — same reason as the edit: a soft delete is an UPDATE,
        # so nothing on the activity spine bumps the task. A client that never
        # hears about it keeps rendering words their author retracted.
        await touch_task(db, getattr(comment, "task_id", None))
        return {"deleted": activity_id}


def _restore_custom(task: Any, changes: list[dict]) -> dict | None:
    """The `custom_fields` object a revert should write, or ``None`` if this
    change touched no custom field.

    ``None`` and ``{}`` are different answers: an empty object means "this
    revert clears every custom value the task has", which is what reverting the
    creation of the only value legitimately does.
    """
    custom = [
        c for c in changes
        if str(c.get("field") or "").startswith(_CUSTOM_PREFIX)
    ]
    if not custom:
        return None
    merged = dict(from_jsonb(getattr(task, "custom_fields", None)) or {})
    for change in custom:
        key = str(change["field"])[len(_CUSTOM_PREFIX):]
        old = change.get("old")
        # `old` of None means the key did not exist before this change, so the
        # revert REMOVES it rather than storing a null — the same rule
        # `apply_values` follows, because "never set" and "set to nothing" must
        # not become one value.
        if old is None:
            merged.pop(key, None)
        else:
            merged[key] = old
    return merged


@router.post("/activities/{activity_id}/revert")
async def revert_change(
    activity_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Restore the values a ``field_change`` activity recorded as ``old``.

    Paca's diff-and-revert, and the reason §3.8 stores both ends of every change
    rather than only the new one. The revert is itself a normal edit: it writes
    a fresh ``field_change`` activity rather than erasing the one it undoes, so
    the timeline shows that a revert happened instead of history appearing never
    to have contained the change.
    """
    async with _tenant_session() as db:
        row = (await db.execute(
            text(
                "SELECT * FROM pm_activities WHERE id = CAST(:aid AS uuid) "
                "AND type = 'field_change'"
            ),
            {"aid": activity_id},
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Change not found")
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, str(row.task_id))

        meta = from_jsonb(getattr(row, "meta", None)) or {}
        changes = [c for c in (meta.get("changes") or []) if isinstance(c, dict)]
        restore = {
            c["field"]: c.get("old") for c in changes
            if c.get("field") in _REVERTIBLE
        }
        # Custom values are restored by MERGING onto whatever the task holds
        # now, never by writing back the whole object: another field may have
        # been edited since, and replacing the blob would silently undo that
        # too — a revert that reverts more than it names.
        custom_restored = _restore_custom(task, changes)
        if custom_restored is not None:
            restore["custom_fields"] = custom_restored

        skipped = sorted(
            {
                str(c.get("field")) for c in changes
                if c.get("field") not in _REVERTIBLE
                and not str(c.get("field") or "").startswith(_CUSTOM_PREFIX)
            }
        )
        if not restore:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Nothing on this change can be reverted here"
                    + (f"; {skipped} must be undone by moving the task." if skipped
                       else ".")
                ),
            )

        await update_row(db, "pm_tasks", str(task.id), restore)
        # The ONE field_change door (WS-27w). `extra_meta` marks this as a
        # revert, which also exempts it from description coalescing — undoing
        # an edit must never be folded into the edit it undoes.
        await record_field_change(
            db, created_by=actor(user), task_id=str(task.id),
            changes=[
                {"field": f, "old": c.get("new"), "new": c.get("old")}
                for c in changes for f in [c.get("field")]
                if f in restore or (
                    custom_restored is not None
                    and str(f or "").startswith(_CUSTOM_PREFIX)
                )
            ],
            extra_meta={"reverted_activity_id": activity_id},
        )
        task_id = str(task.id)
        reverted = sorted(
            [k for k in restore if k != "custom_fields"]
            + [
                str(c["field"]) for c in changes
                if str(c.get("field") or "").startswith(_CUSTOM_PREFIX)
            ]
        )

    await emit("pm.task.updated", {"task_id": task_id})
    return {"task_id": task_id, "reverted": reverted, "skipped": skipped}
