"""The nine page routes of ``/knowledge`` (WS-61 KB-1).

Spec: ``project-docs/specs/knowledge_base.md`` §4.2, §5.3 and §5.4.

    GET  /knowledge/pages?space=&parent=&q=&state=   the tree or a list
    GET  /knowledge/pages/{id}                        one page, its versions, its links
    POST /knowledge/pages                             make a draft
    PATCH /knowledge/pages/{id}                       edit, one version
    POST /knowledge/pages/{id}/publish                publish
    POST /knowledge/pages/{id}/move                   change the space or the parent
    POST /knowledge/pages/{id}/archive                archive (manage on the space)
    POST /knowledge/pages/{id}/restore                archived back to its prior state (manage)
    POST /knowledge/pages/{id}/revert                 a new version with an old body

Each read goes through ``core.visible_pages_clause``. A write needs ``write``
on the page's space at the time of the call, and authorship is no bypass.
``GET`` and ``revert`` reach only the versions from the last move to another
space on (``core.history_floor``). A page the caller cannot
see, or a page in another organization, answers 404 and never 403. Each write
appends one ``kb_page_versions`` row with the reason and the channel (§5.4).

Each write also ends in the index (KB-3, §6.4). ``_written`` calls
``kb_index.index.reindex_page`` in the write's transaction: a published page
gets its chunks with a NULL vector, and a page in any other state loses its
chunks. Then the route schedules ``embed_pending`` as a background task,
which runs after the session closes, and only when ``KNOWLEDGE_EMBED`` names
the org. The job gets the shas that this write inserted, so it embeds each
chunk once, and the acting member, which it binds for the embed door
(KB-3c, H-330).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.kb_index import index as kb_index
from gateway.routes.knowledge import core, flags
from gateway.routes.knowledge.core import router
from pydantic import BaseModel, Field
from sqlalchemy import text

#: The longest body one page holds, in characters. The column allows more,
#: so a later import has room.
MAX_BODY = 200_000

#: The most rows one list returns.
MAX_LIST = 500


class PageCreate(BaseModel):
    space_id: str | None = Field(None, max_length=64)
    parent_id: str | None = Field(None, max_length=64)
    title: str = Field(..., min_length=1, max_length=300)
    body_md: str = Field("", max_length=MAX_BODY)
    reviewers: list[str] = Field(default_factory=list, max_length=core.MAX_REVIEWERS)


class PagePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    body_md: str | None = Field(None, max_length=MAX_BODY)
    reviewers: list[str] | None = Field(None, max_length=core.MAX_REVIEWERS)
    review_at: datetime | None = None


class PageMove(BaseModel):
    space_id: str | None = Field(None, max_length=64)
    parent_id: str | None = Field(None, max_length=64)


class PageRevert(BaseModel):
    version: int = Field(..., ge=1)


def _title(value: str) -> str:
    title = value.strip()
    if not title:
        raise HTTPException(status_code=422, detail="A page needs a title.")
    return title


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


async def _space_of(db: Any, user: UserContext, page: Any) -> Any:
    """The space of a page the caller already sees."""
    return (await db.execute(
        text(f"SELECT {core.SPACE_COLUMNS} FROM kb_spaces s "
             "WHERE s.organization_id = CAST(:org AS uuid) "
             "AND s.id = CAST(:sid AS uuid)"),
        {"org": core.org_of(user), "sid": str(page.space_id)},
    )).first()


async def _update(
    db: Any, user: UserContext, page_id: str, sets: str, params: dict[str, Any],
) -> Any:
    """One UPDATE of a page the caller holds locked. It returns the new row."""
    return (await db.execute(
        text(f"UPDATE kb_pages AS p SET {sets}, updated_at = now() "
             "WHERE p.organization_id = CAST(:org AS uuid) "
             f"AND p.id = CAST(:pid AS uuid) RETURNING {core.PAGE_COLUMNS}"),
        {**params, "org": core.org_of(user), "pid": page_id},
    )).first()


async def _written(
    db: Any, user: UserContext, row: Any, reason: str,
    background: BackgroundTasks | None = None,
) -> dict[str, Any]:
    """The end of each page write: one version, the index, the embed job.

    The chunks are written or deleted in this transaction. The embed job is
    scheduled only when this write inserted a chunk of a published page, for
    an org on ``KNOWLEDGE_EMBED``. It runs after the session closes. It gets
    the new shas only, and the member from the session, never from input.
    """
    version = await core.append_version(
        db, user, row, title=row.title, body_md=row.body_md, reason=reason,
    )
    org = core.org_of(user)
    reindexed = await kb_index.reindex_page(db, row, organization_id=org)
    if (background is not None and reindexed.new_shas
            and flags.knowledge_embed_allowed(org)):
        background.add_task(
            kb_index.embed_pending, org, str(row.id), reindexed.new_shas, user.email,
        )
    out = core.page_dict(row)
    out["version"] = version
    return out


# ── Reads ───────────────────────────────────────────────────────────────────

@router.get("/pages")
async def list_pages(
    space: str | None = Query(None, max_length=64),
    parent: str | None = Query(None, max_length=64, description="A page id, or 'root'"),
    q: str | None = Query(None, max_length=200),
    state: str | None = Query(None, max_length=20),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The pages the caller can see, with no body. Archived pages only on ask."""
    filters: list[str] = []
    params: dict[str, Any] = {"limit": MAX_LIST}
    if space is not None:
        sid = core.parse_id(space)
        if sid is None:
            raise HTTPException(status_code=422, detail="'space' is a space id.")
        filters.append("p.space_id = CAST(:space AS uuid)")
        params["space"] = sid
    if parent is not None:
        if parent == "root":
            filters.append("p.parent_id IS NULL")
        else:
            pid = core.parse_id(parent)
            if pid is None:
                raise HTTPException(status_code=422, detail="'parent' is a page id or 'root'.")
            filters.append("p.parent_id = CAST(:parent AS uuid)")
            params["parent"] = pid
    if state is not None:
        if state not in core.PAGE_STATES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown state '{state}'. One of: {list(core.PAGE_STATES)}.",
            )
        filters.append("p.state = :state")
        params["state"] = state
    else:
        filters.append("p.state <> 'archived'")
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append("p.title ILIKE :q")
        params["q"] = f"%{escaped}%"
    extra = "".join(f" AND {f}" for f in filters)
    async with core._tenant_session() as db:
        vis, vparams = core.visible_pages_clause(user, "p")
        rows = (await db.execute(
            text(f"SELECT {core.PAGE_COLUMNS} FROM kb_pages p WHERE {vis}{extra} "
                 "ORDER BY p.position, lower(p.title), p.created_at LIMIT :limit"),
            {**vparams, **params},
        )).fetchall()
    return {"pages": [core.page_dict(r, body=False) for r in rows]}


@router.get("/pages/{page_id}")
async def get_page(
    page_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One page with its versions and its links. 404 when the caller cannot see it."""
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id)
        if page is None:
            raise core.not_found()
        space = await _space_of(db, user, page)
        level = await core.space_level(db, user, space) if space is not None else None
        out = core.page_dict(page)
        # WS-61 KB-4a: the assistant's read_page names the space, and only
        # when the caller can open it. A draft reviewer may see a page in a
        # space with no grant, and then gets no name.
        out["space_name"] = space.name if space is not None and level is not None else None
        out["level"] = level
        out["can_write"] = core.can_write_page(level)
        out["versions"] = await core.read_versions(
            db, user, str(page.id), core.history_floor(user, page, space),
        )
        out["links"] = await core.read_links(db, user, str(page.id))
    return out


# ── Writes ──────────────────────────────────────────────────────────────────

@router.post("/pages", status_code=201)
async def create_page(
    body: PageCreate,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Make a draft. With no space, it lands in the caller's ``me`` space."""
    title = _title(body.title)
    reviewers = core.clean_reviewers(body.reviewers)
    async with core._tenant_session() as db:
        if body.space_id is None:
            space = await core.ensure_me_space(db, user)
        else:
            space = await core.load_space(db, user, body.space_id)
            if space is None:
                raise core.not_found()
            level = await core.space_level(db, user, space)
            if core.LEVEL_RANK.get(level or "", 0) < core.LEVEL_RANK["write"]:
                raise core.forbidden("Writing in this space needs a write grant.")
        if space.archived_at is not None:
            raise _conflict("The space is archived.")
        if space.kind == "me" and reviewers:
            raise HTTPException(
                status_code=422,
                detail="A page in a 'me' space has no reviewers. Move it to share it.",
            )
        parent_id = None
        if body.parent_id is not None:
            parent = await core.load_page(db, user, body.parent_id)
            if parent is None:
                raise core.not_found()
            if str(parent.space_id) != str(space.id) or parent.state == "archived":
                raise HTTPException(
                    status_code=422,
                    detail="The parent must be a live page in the same space.",
                )
            parent_id = str(parent.id)
        row = (await db.execute(
            text("INSERT INTO kb_pages AS p "
                 "(organization_id, space_id, parent_id, title, body_md, state, "
                 "author_email, reviewers) "
                 "VALUES (CAST(:org AS uuid), CAST(:sid AS uuid), "
                 "CAST(:parent AS uuid), :title, :body, 'draft', :author, "
                 "CAST(:reviewers AS text[])) "
                 f"RETURNING {core.PAGE_COLUMNS}"),
            {"org": core.org_of(user), "sid": str(space.id), "parent": parent_id,
             "title": title, "body": body.body_md, "author": core.viewer_email(user),
             "reviewers": reviewers},
        )).first()
        return await _written(db, user, row, "create")


@router.patch("/pages/{page_id}")
async def update_page(
    page_id: str,
    body: PagePatch,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit the title, the body, the reviewers or the review date. One version."""
    given = body.model_fields_set
    if not given:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state == "archived":
            raise _conflict("The page is archived.")
        space = await _space_of(db, user, page)
        level = await core.space_level(db, user, space)
        if not core.can_write_page(level):
            raise core.forbidden("Editing this page needs a write grant on its space.")
        sets: list[str] = []
        params: dict[str, Any] = {}
        if "title" in given and body.title is not None:
            sets.append("title = :title")
            params["title"] = _title(body.title)
        if "body_md" in given and body.body_md is not None:
            sets.append("body_md = :body")
            params["body"] = body.body_md
        if "reviewers" in given:
            reviewers = core.clean_reviewers(body.reviewers)
            if space.kind == "me" and reviewers:
                raise HTTPException(
                    status_code=422,
                    detail="A page in a 'me' space has no reviewers. Move it to share it.",
                )
            sets.append("reviewers = CAST(:reviewers AS text[])")
            params["reviewers"] = reviewers
        if "review_at" in given:
            sets.append("review_at = :review_at")
            params["review_at"] = body.review_at
        if not sets:
            raise HTTPException(status_code=422, detail="Nothing to change.")
        row = await _update(db, user, str(page.id), ", ".join(sets), params)
        return await _written(db, user, row, "edit", background)


@router.post("/pages/{page_id}/publish")
async def publish_page(
    page_id: str,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Publish a draft. A writer of the space, at the time of the call, publishes."""
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state != "draft":
            raise _conflict(f"Only a draft publishes. This page is {page.state}.")
        space = await _space_of(db, user, page)
        level = await core.space_level(db, user, space)
        if not core.can_write_page(level):
            raise core.forbidden("Publishing this page needs a write grant on its space.")
        row = await _update(
            db, user, str(page.id),
            "state = 'published', published_at = now(), archived_at = NULL, "
            f"review_at = now() + interval '{core.REVIEW_DAYS} days'",
            {},
        )
        return await _written(db, user, row, "publish", background)


async def _move_target(
    db: Any, user: UserContext, page: Any, source: Any, body: PageMove,
) -> Any:
    """The space a move lands in, checked. The source space when none is named."""
    if "space_id" not in body.model_fields_set or body.space_id is None:
        return source
    target = await core.load_space(db, user, body.space_id)
    if target is None:
        raise core.not_found()
    if str(target.id) == str(source.id):
        return target
    level = await core.space_level(db, user, target)
    if core.LEVEL_RANK.get(level or "", 0) < core.LEVEL_RANK["write"]:
        raise core.forbidden("Moving into this space needs a write grant on it.")
    if target.archived_at is not None:
        raise _conflict("The space is archived.")
    if target.kind == "me" and page.author_email != core.viewer_email(user):
        raise core.forbidden("Only the author moves a page into their own space.")
    if await core.has_children(db, user, str(page.id)):
        raise _conflict("Move the child pages first. A page with children stays in its space.")
    return target


async def _move_parent(
    db: Any, user: UserContext, page: Any, target: Any, body: PageMove,
    moved_space: bool,
) -> str | None:
    """The parent a move lands under, checked. No cycle, same space, live."""
    if "parent_id" not in body.model_fields_set:
        if moved_space:
            return None
        return str(page.parent_id) if page.parent_id else None
    if body.parent_id is None:
        return None
    parent = await core.load_page(db, user, body.parent_id)
    if parent is None:
        raise core.not_found()
    if str(parent.space_id) != str(target.id) or parent.state == "archived":
        raise HTTPException(
            status_code=422,
            detail="The parent must be a live page in the target space.",
        )
    if str(page.id) in await core.ancestor_ids(db, user, str(parent.id)):
        raise HTTPException(
            status_code=422,
            detail="A page cannot move under itself or under one of its children.",
        )
    return str(parent.id)


@router.post("/pages/{page_id}/move")
async def move_page(
    page_id: str,
    body: PageMove,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Change the space or the parent of a page.

    The caller writes in both spaces. A page with children stays in its
    space, and a parent cannot be the page or one of its descendants. Only the
    author moves a page into their own ``me`` space.
    """
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="Nothing to move.")
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state == "archived":
            raise _conflict("The page is archived.")
        source = await _space_of(db, user, page)
        source_ok = core.can_write_page(await core.space_level(db, user, source))
        target = await _move_target(db, user, page, source, body)
        moved_space = str(target.id) != str(source.id)
        # An author who lost `write` may still take their DRAFT home: a move
        # into their own `me` space (`_move_target` refuses another member's).
        # A published page belongs to the space, and needs `write` there.
        home = (moved_space and target.kind == "me" and page.state == "draft"
                and page.author_email == core.viewer_email(user))
        if not source_ok and not home:
            raise core.forbidden("Moving this page needs a write grant on its space.")
        new_parent = await _move_parent(db, user, page, target, body, moved_space)
        old_parent = str(page.parent_id) if page.parent_id else None
        if not moved_space and new_parent == old_parent:
            raise HTTPException(status_code=422, detail="Nothing to move.")
        sets = "space_id = CAST(:sid AS uuid), parent_id = CAST(:parent AS uuid)"
        if target.kind == "me":
            sets += ", reviewers = '{}'"
        row = await _update(
            db, user, str(page.id), sets,
            {"sid": str(target.id), "parent": new_parent},
        )
        out = await _written(db, user, row, "move", background)
        if moved_space:
            # The history written in the old space stays there (history_floor).
            await db.execute(
                text("UPDATE kb_pages SET history_from = :v "
                     "WHERE organization_id = CAST(:org AS uuid) "
                     "AND id = CAST(:pid AS uuid)"),
                {"v": out["version"], "org": core.org_of(user), "pid": str(page.id)},
            )
            out["history_from"] = out["version"]
        return out


@router.post("/pages/{page_id}/archive")
async def archive_page(
    page_id: str,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Archive a page. A manager of the space archives (§4.2)."""
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state == "archived":
            raise _conflict("The page is archived.")
        space = await _space_of(db, user, page)
        if await core.space_level(db, user, space) != "manage":
            raise core.forbidden("Archiving a page needs manage on its space.")
        row = await _update(
            db, user, str(page.id),
            # The right side reads the row as it was, so the prior state is kept.
            "state = 'archived', archived_at = now(), archived_from_state = state", {},
        )
        return await _written(db, user, row, "archive", background)


@router.post("/pages/{page_id}/restore")
async def restore_page(
    page_id: str,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Return an archived page to the state it had before the archive.

    The gate is the one of archive: ``manage`` on the page's space, which is
    ``knowledge:manage``, the space owner, or a ``manage`` grant. A published
    page comes back published, so nobody is locked out when its author has
    left. A draft comes back a draft. A row with no recorded state is a draft.
    """
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state != "archived":
            raise _conflict(f"Only an archived page is restored. This page is {page.state}.")
        space = await _space_of(db, user, page)
        if await core.space_level(db, user, space) != "manage":
            raise core.forbidden("Restoring a page needs manage on its space.")
        row = await _update(
            db, user, str(page.id),
            "state = COALESCE(archived_from_state, 'draft'), archived_at = NULL, "
            "review_at = CASE WHEN archived_from_state = 'published' "
            "THEN now() + interval '180 days' ELSE review_at END, "
            "archived_from_state = NULL",
            {},
        )
        return await _written(db, user, row, "restore", background)


@router.post("/pages/{page_id}/revert")
async def revert_page(
    page_id: str,
    body: PageRevert,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Write an old version's title and body as a new version."""
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state == "archived":
            raise _conflict("The page is archived.")
        space = await _space_of(db, user, page)
        level = await core.space_level(db, user, space)
        if not core.can_write_page(level):
            raise core.forbidden("Reverting this page needs a write grant on its space.")
        if body.version < core.history_floor(user, page, space):
            # A version from before the last move to another space.
            raise core.not_found()
        old = (await db.execute(
            text("SELECT title, body_md FROM kb_page_versions "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND page_id = CAST(:pid AS uuid) AND version = :v"),
            {"org": core.org_of(user), "pid": str(page.id), "v": body.version},
        )).first()
        if old is None:
            raise core.not_found()
        row = await _update(
            db, user, str(page.id), "title = :title, body_md = :body",
            {"title": old.title, "body": old.body_md},
        )
        return await _written(db, user, row, "revert", background)
