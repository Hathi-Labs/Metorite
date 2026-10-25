"""The nine page routes of ``/knowledge`` (WS-61 KB-1).

Spec: ``project-docs/specs/knowledge_base.md`` §4.2, §5.3 and §5.4.

    GET  /knowledge/pages?space=&parent=&q=&state=   the tree or a list
    GET  /knowledge/pages/{id}                        one page, its versions, its links
    POST /knowledge/pages                             make a draft
    PATCH /knowledge/pages/{id}                       edit, one version (the icon alone: none)
    POST /knowledge/pages/{id}/publish                publish
    POST /knowledge/pages/{id}/move                   change the space, the parent or the place
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

The place of a page among its siblings is ``kb_pages.position`` (KB-2e,
§21.5). A create puts the page last. A move with ``position`` renumbers the
new siblings 0 to n under the tree lock of the space (``_lock_trees``). A
move that changes only the place writes no version, does not change
``updated_at`` and does not reindex, because a place in the tree is not a
change in substance.

⚠️ The stored position counts hidden siblings, so it never reaches a caller.
Each answer carries the rank among the siblings the caller sees (``_rank``,
and the grouping in ``list_pages``). The ``position`` of a move counts the
same visible siblings.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.kb_index import index as kb_index
from gateway.routes.knowledge import backlinks, core, flags, icon
from gateway.routes.knowledge.core import router
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

#: The longest body one page holds, in characters. The column allows more,
#: so a later import has room.
MAX_BODY = 200_000

#: The most rows one list returns.
MAX_LIST = 500

#: The highest place a move names. A higher one is the end of the list.
MAX_POSITION = 100_000

#: One tree write at a time in one space (KB-2e). The key names the org and
#: the space, so two spaces never wait for each other.
_TREE_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"

#: The SET of a publish. ``publish_page`` and the accept of a page proposal
#: (``proposals.py``, KB-5a) both write it, so the two cannot drift.
PUBLISH_SETS = (
    "state = 'published', published_at = now(), archived_at = NULL, "
    f"review_at = now() + make_interval(days => COALESCE(p.review_every_days, {core.REVIEW_DAYS})), "
    "owner_email = COALESCE(p.owner_email, p.author_email)"
)

#: The SET of a verify (KB-AI-4). It moves the review date by the interval
#: of the page and claims an empty owner for the caller. It does not touch
#: ``updated_at``, so a verify is no edit.
VERIFY_SETS = (
    f"review_at = now() + make_interval(days => COALESCE(p.review_every_days, {core.REVIEW_DAYS})), "
    "owner_email = COALESCE(p.owner_email, :verifier)"
)

#: The PATCH fields that are metadata of care, not of substance. A PATCH of
#: these alone writes no version (KB-AI-4), as the icon alone does (KB-2f).
_CARE_FIELDS = frozenset({"owner_email", "review_every_days"})

#: The SET of an archive. The right side reads the row as it was, so the
#: prior state is kept. ``archive_page`` and the reject of a page proposal
#: (KB-5a) both write it.
ARCHIVE_SETS = "state = 'archived', archived_at = now(), archived_from_state = state"


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
    #: One emoji, or empty or null to clear it (KB-2f, ``icon.clean_icon``).
    icon: str | None = Field(None, max_length=64)
    #: The person who answers for the page (KB-AI-4). Null clears it.
    owner_email: str | None = Field(None, max_length=320)
    #: The days from a publish or a verify to the next review. Null clears it.
    review_every_days: int | None = Field(None, ge=1, le=3650)

    @field_validator("owner_email")
    @classmethod
    def _owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return core.clean_owner(value)


class PageMove(BaseModel):
    space_id: str | None = Field(None, max_length=64)
    parent_id: str | None = Field(None, max_length=64)
    #: The 0-based place among the new siblings that the caller sees (KB-2e).
    position: int | None = Field(None, ge=0, le=MAX_POSITION)


class PageRevert(BaseModel):
    version: int = Field(..., ge=1, le=core.MAX_VERSION)


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
    await backlinks.sync_page_links(db, user, row, row.body_md)
    rank = await _rank(db, user, row)
    reindexed = await kb_index.reindex_page(db, row, organization_id=org)
    if (background is not None and reindexed.new_shas
            and flags.knowledge_embed_allowed(org)):
        background.add_task(
            kb_index.embed_pending, org, str(row.id), reindexed.new_shas, user.email,
        )
    out = core.page_dict(row, position=rank)
    out["version"] = version
    return out


async def writable_space(db: Any, user: UserContext, space_id: str | None) -> Any:
    """The space a new draft lands in, checked. The ``me`` space with none.

    404 for a space the caller cannot open, 403 without ``write`` on it at the
    time of the call, and 409 for an archived space. ``create_page`` and a
    page proposal (KB-5a) both call it.
    """
    if space_id is None:
        space = await core.ensure_me_space(db, user)
    else:
        space = await core.load_space(db, user, space_id)
        if space is None:
            raise core.not_found()
        level = await core.space_level(db, user, space)
        if core.LEVEL_RANK.get(level or "", 0) < core.LEVEL_RANK["write"]:
            raise core.forbidden("Writing in this space needs a write grant.")
    if space.archived_at is not None:
        raise _conflict("The space is archived.")
    return space


async def insert_draft(
    db: Any, user: UserContext, space: Any, *, parent_id: str | None, title: str,
    body_md: str, reviewers: list[str], origin: dict[str, Any] | None = None,
) -> Any:
    """Insert one draft, last among its siblings, and return the new row.

    The caller has checked the space (:func:`writable_space`) and the parent.
    The author is the session's member, never a body field. *origin* is the
    provenance of the draft, for an agent's proposal (KB-5a), or None. The
    caller then ends the write in :func:`_written`, which appends the version.
    """
    # KB-2e: a new page goes last among its siblings, under the tree lock.
    await _lock_trees(db, user, str(space.id))
    position = await core.next_position(db, user, str(space.id), parent_id)
    return (await db.execute(
        text("INSERT INTO kb_pages AS p "
             "(organization_id, space_id, parent_id, position, title, body_md, "
             "state, author_email, reviewers, origin) "
             "VALUES (CAST(:org AS uuid), CAST(:sid AS uuid), "
             "CAST(:parent AS uuid), :position, :title, :body, 'draft', :author, "
             "CAST(:reviewers AS text[]), CAST(:origin AS jsonb)) "
             f"RETURNING {core.PAGE_COLUMNS}"),
        {"org": core.org_of(user), "sid": str(space.id), "parent": parent_id,
         "position": position, "title": title, "body": body_md,
         "author": core.viewer_email(user), "reviewers": reviewers,
         "origin": json.dumps(origin) if origin is not None else None},
    )).first()


async def _lock_trees(db: Any, user: UserContext, *space_ids: str) -> None:
    """Hold the tree lock of each space until the transaction ends (KB-2e).

    ⚠️ Take them BEFORE a page row lock. A renumber writes the rows of the
    siblings, so a move that held its own row and then waited here could
    meet a move that holds this lock and waits for that row.

    ⚠️ A move to another space takes BOTH trees, in sorted order. With one
    lock each, a move from S1 to S2 and a move from S2 to S1 at once each
    renumber a tree that holds the other's locked row, and Postgres ends
    one of them as a deadlock.
    """
    org = core.org_of(user)
    for space_id in sorted(set(space_ids)):
        await db.execute(text(_TREE_LOCK_SQL), {"key": f"kb_tree:{org}:{space_id}"})


async def _visible_siblings(
    db: Any, user: UserContext, space_id: str, parent_id: str | None,
    keep: str | None = None,
) -> list[str]:
    """The ids of the live siblings the caller sees, in the order of the tree.

    *keep* names a page that stays in the list in any state, so an archived
    page still gets its own rank in the answer of its archive.
    """
    vis, vparams = core.visible_pages_clause(user, "p")
    params: dict[str, Any] = {**vparams, "vs_sid": space_id, "vs_keep": keep}
    if parent_id is None:
        under = "p.parent_id IS NULL"
    else:
        under = "p.parent_id = CAST(:vs_parent AS uuid)"
        params["vs_parent"] = parent_id
    rows = (await db.execute(
        text(f"SELECT p.id, p.position, p.title, p.created_at FROM kb_pages p WHERE {vis} "
             f"AND p.space_id = CAST(:vs_sid AS uuid) AND {under} "
             "AND (p.state <> 'archived' OR p.id = CAST(:vs_keep AS uuid))"),
        params,
    )).fetchall()
    return [str(r.id) for r in sorted(rows, key=core.sibling_key)]


async def _rank(db: Any, user: UserContext, row: Any) -> int | None:
    """The place of *row* among the siblings the caller sees."""
    parent = str(row.parent_id) if row.parent_id else None
    ids = await _visible_siblings(db, user, str(row.space_id), parent, keep=str(row.id))
    return ids.index(str(row.id)) if str(row.id) in ids else None


async def _place(
    db: Any, user: UserContext, page_id: str, space_id: str,
    parent_id: str | None, position: int | None,
) -> int:
    """Put a page at *position* among its siblings, renumber them, return its rank.

    The caller holds the tree lock of *space_id*, and the page already sits
    under *parent_id*. *position* counts among the siblings the caller sees,
    because the tree the member drags shows only those. The page goes in
    before the sibling that holds that place. With no *position*, or a place
    past the last one, the page goes last. Then every sibling, a hidden one
    too, gets 0 to n in that order. The UPDATE writes ``position`` only, so
    no sibling gets a version or a new ``updated_at``. It skips a row that
    left this tree meanwhile. The answer is the rank among the visible
    siblings, never the stored place (``page_dict``).
    """
    visible = [
        rid for rid in await _visible_siblings(db, user, space_id, parent_id)
        if rid != page_id
    ]
    others = [
        str(r.id) for r in await core.sibling_rows(db, user, space_id, parent_id)
        if str(r.id) != page_id
    ]
    rank = len(visible) if position is None else min(position, len(visible))
    at = len(others) if rank == len(visible) else others.index(visible[rank])
    order = [*others[:at], page_id, *others[at:]]
    await db.execute(
        text("UPDATE kb_pages AS p SET position = o.ord - 1 "
             "FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS o(id, ord) "
             "WHERE p.organization_id = CAST(:org AS uuid) AND p.id = o.id "
             "AND p.space_id = CAST(:sid AS uuid) "
             "AND p.parent_id IS NOT DISTINCT FROM CAST(:parent AS uuid) "
             "AND p.position <> o.ord - 1"),
        {"ids": order, "org": core.org_of(user), "sid": space_id, "parent": parent_id},
    )
    return rank


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
                 # Grouped by sibling set FIRST (KB-2e fix round 2). An order
                 # by the stored place across parents would put a row among
                 # another parent's rows by its stored place, and that tells
                 # how many hidden siblings sit before it. Between groups the
                 # order follows the ids only, and so does the LIMIT cut.
                 "ORDER BY p.space_id, p.parent_id NULLS FIRST, p.position, "
                 "lower(p.title), p.created_at, p.id LIMIT :limit"),
            {**vparams, **params},
        )).fetchall()
    # The rank among the siblings in this answer, never the stored place.
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        groups.setdefault((str(r.space_id), str(r.parent_id)), []).append(r)
    rank: dict[str, int] = {}
    for members in groups.values():
        for i, r in enumerate(sorted(members, key=core.sibling_key)):
            rank[str(r.id)] = i
    # The rows go out in rank order inside each group, so a tie that SQL and
    # Python break apart (a collation) cannot reorder them.
    rows = sorted(rows, key=lambda r: (
        str(r.space_id), r.parent_id is not None, str(r.parent_id), rank[str(r.id)]))
    return {"pages": [core.page_dict(r, body=False, position=rank[str(r.id)]) for r in rows]}


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
        out = core.page_dict(page, position=await _rank(db, user, page))
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
        space = await writable_space(db, user, body.space_id)
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
        row = await insert_draft(
            db, user, space, parent_id=parent_id, title=title, body_md=body.body_md,
            reviewers=reviewers,
        )
        return await _written(db, user, row, "create")


def _care_sets(body: PagePatch, given: set[str]) -> tuple[list[str], dict[str, Any]]:
    """The SET parts and the parameters of the owner and the interval (KB-AI-4)."""
    sets: list[str] = []
    params: dict[str, Any] = {}
    if "owner_email" in given:
        sets.append("owner_email = :owner_email")
        params["owner_email"] = body.owner_email
    if "review_every_days" in given:
        sets.append("review_every_days = :every")
        params["every"] = body.review_every_days
    return sets, params


async def _check_owner(db: Any, body: PagePatch, given: set[str]) -> None:
    """422 when the new owner is no person or member of the caller's org (KB-AI-4).

    An address outside the org would make a page verified by a stranger.
    Null clears the owner and needs no check. The one check is
    ``email_in_org``, the helper of the assistant's alias rule.
    """
    if "owner_email" not in given or body.owner_email is None:
        return
    from gateway.routes.admin.assistant_profile_people import email_in_org

    if not await email_in_org(db, body.owner_email):
        raise HTTPException(
            status_code=422, detail="The owner must be a member of your organization.",
        )


async def _write_care(
    db: Any, user: UserContext, page_id: str, body: PagePatch, given: set[str],
) -> Any:
    """Set the owner or the interval of a locked page. No version, no reindex."""
    sets, params = _care_sets(body, given)
    return (await db.execute(
        text(f"UPDATE kb_pages AS p SET {', '.join(sets)} "
             "WHERE p.organization_id = CAST(:org AS uuid) "
             f"AND p.id = CAST(:pid AS uuid) RETURNING {core.PAGE_COLUMNS}"),
        {**params, "org": core.org_of(user), "pid": page_id},
    )).first()


@router.patch("/pages/{page_id}")
async def update_page(
    page_id: str,
    body: PagePatch,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit the title, the body, the reviewers, the review date or the icon.

    One version. A change of only the icon writes no version, keeps
    ``updated_at`` and does not reindex (KB-2f, ``icon.write_icon``).
    """
    given = body.model_fields_set
    if not given:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    new_icon = icon.clean_icon(body.icon) if "icon" in given else None
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
        await _check_owner(db, body, given)
        if given <= _CARE_FIELDS:
            # KB-AI-4: an owner or an interval is no change in substance. No
            # version, no `updated_at`, no index, as the icon alone.
            row = await _write_care(db, user, str(page.id), body, given)
            return core.page_dict(row, position=await _rank(db, user, row))
        if given == {"icon"}:
            # KB-2f: an icon is not a change in substance. No version, no
            # `updated_at`, no index, as a move of the place only (KB-2e).
            row = await icon.write_icon(db, user, str(page.id), new_icon)
            return core.page_dict(row, position=await _rank(db, user, row))
        sets: list[str] = []
        params: dict[str, Any] = {}
        if "icon" in given:
            sets.append("icon = :icon")
            params["icon"] = new_icon
        care_sets, care_params = _care_sets(body, given)
        sets += care_sets
        params.update(care_params)
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


@router.post("/pages/{page_id}/verify")
async def verify_page(
    page_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A person vouches that a published page is still right (KB-AI-4).

    The review date moves forward by the interval of the page, and an empty
    owner becomes the caller. It writes no version, keeps ``updated_at`` and
    does not reindex, because a verify is no edit. A writer of the space
    verifies.
    """
    if not core.viewer_email(user):
        raise core.forbidden("Verifying a page needs a signed-in person.")
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if page.state != "published":
            raise _conflict(f"Only a published page is verified. This page is {page.state}.")
        space = await _space_of(db, user, page)
        level = await core.space_level(db, user, space)
        if not core.can_write_page(level):
            raise core.forbidden("Verifying this page needs a write grant on its space.")
        row = (await db.execute(
            text(f"UPDATE kb_pages AS p SET {VERIFY_SETS} "
                 "WHERE p.organization_id = CAST(:org AS uuid) "
                 f"AND p.id = CAST(:pid AS uuid) RETURNING {core.PAGE_COLUMNS}"),
            {"org": core.org_of(user), "pid": str(page.id),
             "verifier": core.viewer_email(user)},
        )).first()
        return core.page_dict(row, position=await _rank(db, user, row))


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
        row = await _update(db, user, str(page.id), PUBLISH_SETS, {})
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
    """Change the space, the parent or the place of a page.

    The caller writes in both spaces. A page with children stays in its
    space, and a parent cannot be the page or one of its descendants. Only the
    author moves a page into their own ``me`` space.

    ``position`` is the 0-based place among the new siblings (KB-2e). A move
    that changes only the place writes no version, keeps ``updated_at`` and
    does not reindex. A move to another space or parent writes the ``move``
    version, and the page goes to ``position``, or last when none is named.
    """
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="Nothing to move.")
    async with core._tenant_session() as db:
        # The tree locks first, then the row lock (`_lock_trees`). A move to
        # another space locks the source tree and the target tree, in one
        # order, so two opposite moves cannot wait on each other.
        seen = await core.load_page(db, user, page_id)
        if seen is None:
            raise core.not_found()
        named_space = core.parse_id(body.space_id) if body.space_id is not None else None
        await _lock_trees(db, user, str(seen.space_id), named_space or str(seen.space_id))
        page = await core.load_page(db, user, page_id, lock=True)
        if page is None:
            raise core.not_found()
        if str(page.space_id) != str(seen.space_id):
            raise _conflict("The page moved to another space while you moved it. Try again.")
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
        position = body.position if "position" in body.model_fields_set else None
        if not moved_space and new_parent == old_parent:
            if position is None:
                raise HTTPException(status_code=422, detail="Nothing to move.")
            # KB-2e: a place in the tree is not a change in substance
            # (`company_context.md` §3). No version, no `updated_at`, no index.
            rank = await _place(db, user, str(page.id), str(target.id), new_parent, position)
            placed = await core.load_page(db, user, str(page.id))
            return core.page_dict(placed, position=rank)
        sets = "space_id = CAST(:sid AS uuid), parent_id = CAST(:parent AS uuid)"
        if target.kind == "me":
            sets += ", reviewers = '{}'"
        row = await _update(
            db, user, str(page.id), sets,
            {"sid": str(target.id), "parent": new_parent},
        )
        out = await _written(db, user, row, "move", background)
        out["position"] = await _place(
            db, user, str(page.id), str(target.id), new_parent, position,
        )
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
        row = await _update(db, user, str(page.id), ARCHIVE_SETS, {})
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
