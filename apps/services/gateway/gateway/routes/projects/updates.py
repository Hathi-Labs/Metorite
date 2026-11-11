"""Projects · project updates with health (WS-39 LP-1a).

Spec: ``project-docs/specs/project_management_app.md`` §14.2 row LP-1 and the
design §14.2a. Migration: the file that creates ``pm_project_updates``.

THE one writer of ``pm_project_updates``. An update says how one project is
going: a health that the author picks by hand (``on_track``, ``at_risk`` or
``off_track``) and a short text. LP-1a has three routes. The flag
``PROJECTS_UPDATES_ORGS`` (default empty) keeps each one at 404.

* ``GET    /nodes/{project_id}/updates``  the live updates, newest first, a
  page of ``limit``, and the cursor ``before`` (the id of the last row of the
  page before).
* ``POST   /nodes/{project_id}/updates``  body ``{"health", "body"}``.
* ``DELETE /nodes/{project_id}/updates/{update_id}``  a soft delete.

The node summary (``tree.get_node_summary``) carries the newest live update
of a project through :func:`latest_update`, so the Overview draws its line
with the roll-up and nothing moves when it arrives.

**Who may post.** The right of a task write on the same project:
:func:`core.load_visible_project` with ``lock_share``, the check of
``POST /tasks``. A member who sees the project may post. A filed (archived)
project takes no new update, by :func:`core.assert_not_archived`, as it takes
no new task. A space and a folder hold no update, because the update is about
a project. No new grant word.

**Who may delete.** The author, or a Projects admin
(:func:`authority.may_delete_update`, the one rule of a delete). Any other
member who sees the project gets 403. The row stays, with ``deleted_at`` and
``deleted_by``, and no read shows it again. The delete is not on the MCP in
LP-1a: ``MCP_ROUTES`` does not hold its pair.

**Who may read.** A member who sees the project reads its live updates. A
member who cannot see it gets 404, the same answer as a missing project (R5).
A signed agent reads and posts only inside its pin, because
``load_visible_project`` narrows by the connection (EA-17a).

**The tenant and the actor.** The session sets both. ``organization_id`` is
the column DEFAULT, the bound tenant, and ``author`` is the caller's email in
lower case. The body model forbids every other key, so a body cannot name an
author or an org (R11). ``drafted_by_ai`` is true when a signed agent posts.

**The audit.** Each post and each delete writes one ``system`` activity on
the project, as the authority pin does. No new activity type.

**Health is the author's word in LP-1a.** Rule D (§14.1 rule 8) asks that code
computes the health, and that an LLM writes only the text. Both are later
slices. This module computes nothing and calls no model.

Fence: ``tests/unit/test_pm_project_updates_r8.py`` (R8).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.authority import (
    UPDATE_DELETE_REFUSED,
    assert_may_delete_update,
    may_delete_update,
)
from gateway.routes.projects.core import (
    ACTOR_VIA,
    _tenant_session,
    actor,
    assert_not_archived,
    is_team_space,
    load_visible_project,
    node_kind,
    record_activity,
    resolve_visibility,
    router,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The three values of the health, in the order a picker offers them. The
#: CHECK of the migration holds the same three.
HEALTHS: tuple[str, ...] = ("on_track", "at_risk", "off_track")

#: What a person reads for each value. The web line and the chat say the same.
HEALTH_LABELS: dict[str, str] = {
    "on_track": "On track",
    "at_risk": "At risk",
    "off_track": "Off track",
}

#: The cap of the text, in characters. It is the cap of an agent comment
#: (``activities.AGENT_MAX_COMMENT_CHARS``), and the CHECK of the migration
#: holds it too. The MCP edge holds a pinned copy.
MAX_UPDATE_CHARS = 20_000

#: The page of a list, and its cap.
DEFAULT_PAGE = 20
MAX_PAGE = 50

#: The 404 of a route while the flag does not hold the org. The MCP edge maps
#: this detail to its own text, so the tool says that the feature is off. The
#: one copy lives in ``acb_auth.agent_oauth``, because the edge may not import
#: the gateway.
UPDATES_OFF = agent_oauth.PROJECT_UPDATES_OFF

#: The 422 of a post on a space or a folder.
NOT_A_PROJECT = (
    "A space or a folder has no project updates. Post the update on a "
    "project inside it."
)

#: The 422 of a blank text, or a text with a NUL character.
BLANK_BODY = "Write the text of the update."

#: The 422 of a cursor that is not an update of this project.
BAD_CURSOR = "The cursor 'before' is not an update of this project."

#: The 404 of a delete of an update that the project does not hold, or that
#: is gone already.
UPDATE_NOT_FOUND = "Update not found"

#: The columns a read returns. ``via`` comes out of ``meta``. No other key of
#: ``meta`` leaves the route.
_COLUMNS = (
    "id, project_id, author, health, body, posted_at, drafted_by_ai, "
    "meta->>'via' AS via"
)

#: The newest live update of one project. The partial index of the migration
#: serves it.
_LATEST_SQL = (
    f"SELECT {_COLUMNS} FROM pm_project_updates "
    "WHERE project_id = CAST(:pid AS uuid) AND deleted_at IS NULL "
    "ORDER BY posted_at DESC, id DESC LIMIT 1"
)


def updates_allowed(organization_id: str | None) -> bool:
    """True when ``PROJECTS_UPDATES_ORGS`` holds the org, or holds ``*``.

    Default OFF. The org comes from the session, never from request input
    (R5). It reads ``get_settings()`` on each call, so a flag reload needs no
    restart (``docs/flag_reload.md``). THE one reader of the flag.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "projects_updates_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_updates(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``PROJECTS_UPDATES_ORGS`` does not hold the caller's org."""
    if not updates_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail=UPDATES_OFF)


_ENABLED = [Depends(require_updates)]


class UpdateIn(BaseModel):
    """The body of a post. Any other key is a 422, so a body names no author
    and no org (R11)."""

    model_config = ConfigDict(extra="forbid")

    health: Literal["on_track", "at_risk", "off_track"]
    body: str = Field(min_length=1, max_length=MAX_UPDATE_CHARS)


def _uuid_or_none(value: str | None) -> str | None:
    """The canonical form of an id, or None for a value that is not one."""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _project_id(value: str) -> str:
    """The project id of the path. A value that is not an id is the 404 of a
    missing project, so the answer tells nothing apart (R5)."""
    pid = _uuid_or_none(value)
    if pid is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return pid


def _view(row: Any, user: Any) -> dict[str, Any]:
    """One update for the caller. ``can_delete`` is this caller's right
    (:func:`authority.may_delete_update`), so a client never guesses it."""
    posted = row.posted_at
    may = may_delete_update(user, row)
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "author": row.author,
        "health": row.health,
        "body": row.body,
        "posted_at": posted.isoformat() if posted is not None else None,
        "drafted_by_ai": bool(row.drafted_by_ai),
        "via": row.via or None,
        "can_delete": may,
        # The words of the 403, so the chat quotes the server before a card.
        "delete_refusal": None if may else UPDATE_DELETE_REFUSED,
    }


def _refuse_space_or_folder(node: Any) -> None:
    """422 for a post on a team space or a folder. Call it AFTER the
    visibility load, so a caller who cannot see the node gets 404 (R5)."""
    if node_kind(getattr(node, "kind", None)) == "folder" or is_team_space(node):
        raise HTTPException(status_code=422, detail=NOT_A_PROJECT)


async def latest_update(db: Any, user: Any, project_id: str) -> dict[str, Any] | None:
    """The newest live update of a project the caller already loaded, or None.

    The node summary calls it inside its own session, after its own
    visibility load, and only while :func:`updates_allowed` holds the org.
    """
    row = (await db.execute(text(_LATEST_SQL), {"pid": project_id})).fetchone()
    return _view(row, user) if row is not None else None


@router.get("/nodes/{project_id}/updates", dependencies=_ENABLED)
async def list_updates(
    project_id: str,
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    before: str | None = Query(None),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The live updates of one project, newest first, one page at a time.

    ``next_before`` is the id to send as ``before`` for the next page, or null
    on the last page. A deleted update never shows. Its row can still be the
    cursor of the page after it, so a delete between two pages skips nothing.
    """
    pid = _project_id(project_id)
    cursor = None
    if before is not None:
        cursor = _uuid_or_none(before)
        if cursor is None:
            raise HTTPException(status_code=422, detail=BAD_CURSOR)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, pid)
        params: dict[str, Any] = {"pid": pid, "lim": limit + 1}
        after_cursor = ""
        if cursor is not None:
            mark = (await db.execute(
                text(
                    "SELECT posted_at, id FROM pm_project_updates "
                    "WHERE id = CAST(:cur AS uuid) AND project_id = CAST(:pid AS uuid)"
                ),
                {"cur": cursor, "pid": pid},
            )).fetchone()
            if mark is None:
                raise HTTPException(status_code=422, detail=BAD_CURSOR)
            params["cur_at"], params["cur_id"] = mark.posted_at, str(mark.id)
            after_cursor = (
                "AND (posted_at, id) < (CAST(:cur_at AS timestamptz), "
                "CAST(:cur_id AS uuid)) "
            )
        rows = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM pm_project_updates "
                "WHERE project_id = CAST(:pid AS uuid) AND deleted_at IS NULL "
                f"{after_cursor}"
                "ORDER BY posted_at DESC, id DESC LIMIT :lim"
            ),
            params,
        )).fetchall()
    page = rows[:limit]
    more = len(rows) > limit
    return {
        "rows": [_view(r, user) for r in page],
        "next_before": str(page[-1].id) if more and page else None,
    }


@router.post("/nodes/{project_id}/updates", status_code=201, dependencies=_ENABLED)
async def post_update(
    project_id: str, payload: UpdateIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Post one update on a project. The author is the session's member."""
    pid = _project_id(project_id)
    body = payload.body
    # Postgres refuses a NUL in text. Without this check the insert answers
    # 500. A blank text says nothing, so it is refused too.
    if "\x00" in body or not body.strip():
        raise HTTPException(status_code=422, detail=BLANK_BODY)
    via = ACTOR_VIA.get("")
    # A signed agent (the MCP edge) wrote this text, not the member's hand.
    by_agent = getattr(user, "agent", None) is not None
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # The check of `POST /tasks`: the member sees the project. The share
        # lock keeps a move from making it a space while this post commits.
        node = await load_visible_project(db, vis, pid, lock_share=True)
        _refuse_space_or_folder(node)
        assert_not_archived(node, then="Post the update on")
        row = (await db.execute(
            text(
                "INSERT INTO pm_project_updates "
                "(project_id, author, health, body, drafted_by_ai, meta) "
                "VALUES (CAST(:pid AS uuid), :who, :health, :body, :ai, "
                "CAST(:meta AS jsonb)) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "pid": pid,
                "who": actor(user).lower(),
                "health": payload.health,
                "body": body,
                "ai": by_agent,
                "meta": json.dumps({"via": via} if via else {}),
            },
        )).fetchone()
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=pid,
            body=f"Posted a project update: {HEALTH_LABELS[payload.health]}",
            meta={"update_id": str(row.id), "health": payload.health},
        )
        return _view(row, user)


@router.delete("/nodes/{project_id}/updates/{update_id}", dependencies=_ENABLED)
async def delete_update(
    project_id: str, update_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete one update: the author or a Projects admin. The row stays,
    with ``deleted_at`` and ``deleted_by``, and no read shows it again."""
    pid = _project_id(project_id)
    uid = _uuid_or_none(update_id)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        # A member who cannot see the project gets 404 before any update read.
        await load_visible_project(db, vis, pid)
        if uid is None:
            raise HTTPException(status_code=404, detail=UPDATE_NOT_FOUND)
        row = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM pm_project_updates "
                "WHERE id = CAST(:uid AS uuid) AND project_id = CAST(:pid AS uuid) "
                "AND deleted_at IS NULL FOR UPDATE"
            ),
            {"uid": uid, "pid": pid},
        )).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=UPDATE_NOT_FOUND)
        assert_may_delete_update(user, row)
        gone = (await db.execute(
            text(
                "UPDATE pm_project_updates SET deleted_at = now(), deleted_by = :who "
                "WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL "
                "RETURNING id, deleted_at"
            ),
            {"uid": uid, "who": actor(user).lower()},
        )).fetchone()
        if gone is None:
            raise HTTPException(status_code=404, detail=UPDATE_NOT_FOUND)
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=pid,
            body="Deleted a project update",
            meta={"update_id": uid, "health": row.health},
        )
        return {
            "id": uid,
            "project_id": pid,
            "deleted": True,
            "deleted_at": gone.deleted_at.isoformat(),
        }
