"""The proposals of the assistant, and their review (WS-61 KB-5a).

Spec: ``project-docs/specs/knowledge_base.md`` §5.3, §8.1 (F1, F2) and §12.

    POST /knowledge/proposals                  a proposed page or a proposed edit
    GET  /knowledge/proposals?status=pending   the proposals the caller can see
    POST /knowledge/proposals/{id}/accept      a person accepts, from the web
    POST /knowledge/proposals/{id}/reject      a person rejects, from the web

**Two kinds, two homes.** A proposed PAGE is a draft in ``kb_pages`` with
``origin.kind = 'agent_proposal'`` and a version with ``reason =
'agent_proposal'``. Its proposal id is the page id. A proposed EDIT of a page
is one ``kb_page_proposals`` row at ``base_version``, the page's newest
version when the assistant read it. It cannot be a draft page: a page has one
state, and a draft of a published page would unpublish it and drop its chunks.

**F1, who sees and who accepts.** The member who asked the assistant is the
author of a proposed page, and only that author sees it
(``core.visible_pages_clause`` shows a draft to its author) and decides it. An
edit proposal is seen by its proposer and by each member with ``write`` on
the page's space, while the page itself is visible to them. A decision needs
``write`` on the space at the time of the call, because authorship is no
bypass. A proposal the caller cannot see answers the 404 of a missing one.

**F2, the agent never publishes.** The chat tools reach ``POST
/knowledge/proposals`` only (``acb_skills.knowledge_manifest``). Accept and
reject refuse a call that came through ``chat`` or ``mcp`` with 403, so a
person decides, from the web, and the version records ``via = 'web'``.

**The tenant and the member come from the session (R5, R11).** The body
names no proposer, no organization and no decider, and a body that tries is
a 422 (``extra = "forbid"``). Each statement names ``core.org_of(user)``
beside row level security. Fences: ``tests/unit/test_kb_proposals.py`` and,
on a real database, ``tests/unit/test_kb_proposals_r8.py``.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException, Query
from gateway.routes.knowledge import core, pages
from gateway.routes.knowledge.core import router
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The reason of the first version of a proposed page (``kb_page_versions``).
PROPOSAL_REASON = "agent_proposal"

#: The reason of the version that an accept writes.
ACCEPT_REASON = "accept"

#: The statuses of ``kb_page_proposals`` (the CHECK of migration 292).
STATUSES: tuple[str, ...] = ("pending", "accepted", "rejected", "expired")

#: The longest summary, as the CHECK of migration 292.
MAX_SUMMARY = 1000

#: The most rows of each kind that one list returns.
MAX_LIST = 100

#: The columns of a proposal row.
_PROPOSAL_COLUMNS = (
    "pr.id, pr.page_id, pr.base_version, pr.title, pr.body_md, pr.summary, "
    "pr.proposer_email, pr.via, pr.status, pr.decided_by, pr.decided_at, pr.created_at"
)

#: What a decision through the chat or an outside agent reads (F2).
WEB_ONLY = "A person accepts or rejects a proposal in the Knowledge app."


class ProposalCreate(BaseModel):
    #: The body names no member and no organization (R11): a key that is not
    #: here is a 422, never ignored.
    model_config = ConfigDict(extra="forbid")

    kind: Literal["page", "edit"]
    #: For ``page``: the space, or none for the member's ``me`` space.
    space_id: str | None = Field(None, max_length=64)
    #: For ``edit``: the page to change.
    page_id: str | None = Field(None, max_length=64)
    #: Required for ``page``. For ``edit``, none keeps the page's title.
    title: str | None = Field(None, min_length=1, max_length=300)
    body_md: str = Field("", max_length=pages.MAX_BODY)
    summary: str = Field("", max_length=MAX_SUMMARY)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _summary(value: str) -> str:
    return " ".join((value or "").split())[:MAX_SUMMARY]


def _require_web() -> None:
    """F2: accept and reject only from the web, never through an agent."""
    if core.current_via() != "web":
        raise core.forbidden(WEB_ONLY)


def edit_dict(row: Any, *, page_title: str | None = None) -> dict[str, Any]:
    """The answer for one edit proposal."""
    return {
        "id": str(row.id),
        "kind": "edit",
        "page_id": str(row.page_id),
        "page_title": page_title,
        "base_version": row.base_version,
        "title": row.title,
        "body_md": row.body_md,
        "summary": row.summary,
        "proposer_email": row.proposer_email,
        "via": row.via,
        "status": row.status,
        "decided_by": row.decided_by,
        "decided_at": _iso(row.decided_at),
        "created_at": _iso(row.created_at),
    }


def origin_of(row: Any) -> dict[str, Any]:
    """``kb_pages.origin`` as a dict. A driver may hand JSONB back as text."""
    value = row.origin
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _page_status(row: Any) -> str:
    """The status of a proposed page, from the state of its draft."""
    if row.state == "draft":
        return "pending"
    if row.state == "published":
        return "accepted"
    return "rejected"


def page_proposal_dict(row: Any) -> dict[str, Any]:
    """The answer for one proposed page. Its id is the page id."""
    origin = origin_of(row)
    return {
        "id": str(row.id),
        "kind": "page",
        "page_id": str(row.id),
        "page_title": row.title,
        "base_version": None,
        "title": row.title,
        "body_md": row.body_md,
        "summary": str(origin.get("summary") or ""),
        "proposer_email": str(origin.get("proposer") or row.author_email),
        "via": str(origin.get("via") or ""),
        "status": _page_status(row),
        "decided_by": None,
        "decided_at": None,
        "created_at": _iso(row.created_at),
    }


def is_page_proposal(row: Any) -> bool:
    origin = origin_of(row)
    return origin.get("kind") == PROPOSAL_REASON


async def _newest_version(db: Any, user: UserContext, page_id: str) -> int:
    """The number of the page's newest version. The caller sees the page."""
    return int((await db.execute(
        text("SELECT COALESCE(MAX(version), 0) FROM kb_page_versions "
             "WHERE organization_id = CAST(:org AS uuid) "
             "AND page_id = CAST(:pid AS uuid)"),
        {"org": core.org_of(user), "pid": page_id},
    )).scalar())


# ── Make a proposal ─────────────────────────────────────────────────────────

async def _propose_page(
    db: Any, user: UserContext, body: ProposalCreate,
) -> dict[str, Any]:
    if body.page_id is not None:
        raise _unprocessable("A page proposal names a space, not a page.")
    if body.title is None:
        raise _unprocessable("A page proposal needs a title.")
    title = pages._title(body.title)
    space = await pages.writable_space(db, user, body.space_id)
    origin = {
        "kind": PROPOSAL_REASON,
        "via": core.current_via(),
        "proposer": core.viewer_email(user),
        "summary": _summary(body.summary),
    }
    row = await pages.insert_draft(
        db, user, space, parent_id=None, title=title, body_md=body.body_md,
        reviewers=[], origin=origin,
    )
    page = await pages._written(db, user, row, PROPOSAL_REASON)
    out = page_proposal_dict(row)
    out["space_name"] = space.name
    out["version"] = page["version"]
    return out


async def _propose_edit(
    db: Any, user: UserContext, body: ProposalCreate,
) -> dict[str, Any]:
    if body.space_id is not None:
        raise _unprocessable("An edit proposal names a page, not a space.")
    if body.page_id is None:
        raise _unprocessable("An edit proposal needs the page id.")
    page = await core.load_page(db, user, body.page_id)
    if page is None:
        raise core.not_found()
    if page.state == "archived":
        raise pages._conflict("The page is archived.")
    space = await pages._space_of(db, user, page)
    if not core.can_write_page(await core.space_level(db, user, space)):
        raise core.forbidden("Proposing an edit needs a write grant on the page's space.")
    title = pages._title(body.title) if body.title is not None else page.title
    base = await _newest_version(db, user, str(page.id))
    row = (await db.execute(
        text("INSERT INTO kb_page_proposals AS pr "
             "(organization_id, page_id, base_version, title, body_md, summary, "
             "proposer_email, via) "
             "VALUES (CAST(:org AS uuid), CAST(:pid AS uuid), :base, :title, :body, "
             ":summary, :proposer, :via) "
             f"RETURNING {_PROPOSAL_COLUMNS}"),
        {"org": core.org_of(user), "pid": str(page.id), "base": base, "title": title,
         "body": body.body_md, "summary": _summary(body.summary),
         "proposer": core.viewer_email(user), "via": core.current_via()},
    )).first()
    out = edit_dict(row, page_title=page.title)
    out["space_name"] = space.name if space is not None else None
    return out


@router.post("/proposals", status_code=201)
async def create_proposal(
    body: ProposalCreate,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """File a proposed page or a proposed edit. Nothing publishes here (F2)."""
    if not core.viewer_email(user):
        raise core.forbidden("Knowledge needs a member with an email address.")
    async with core._tenant_session() as db:
        if body.kind == "page":
            return await _propose_page(db, user, body)
        return await _propose_edit(db, user, body)


# ── The list ────────────────────────────────────────────────────────────────

async def _writable_space_ids(
    db: Any, user: UserContext, space_ids: list[str],
) -> set[str]:
    """The ids among *space_ids* where the caller holds ``write`` or more."""
    if not space_ids:
        return set()
    spaces = (await db.execute(
        text(f"SELECT {core.SPACE_COLUMNS} FROM kb_spaces s "
             "WHERE s.organization_id = CAST(:org AS uuid) "
             "AND s.id = ANY(CAST(:ids AS uuid[]))"),
        {"org": core.org_of(user), "ids": space_ids},
    )).fetchall()
    by_grant = await core.levels_by_grant(db, user, space_ids)
    return {
        str(s.id) for s in spaces
        if core.can_write_page(core.level_on(user, s, by_grant.get(str(s.id))))
    }


async def _edit_rows(db: Any, user: UserContext, status: str) -> list[dict[str, Any]]:
    """The edit proposals with *status* whose page the caller sees (F1)."""
    vis, vparams = core.visible_pages_clause(user, "p")
    rows = (await db.execute(
        text(f"SELECT {_PROPOSAL_COLUMNS}, p.title AS page_title, p.space_id "
             "FROM kb_page_proposals pr "
             "JOIN kb_pages p ON p.id = pr.page_id "
             "AND p.organization_id = pr.organization_id "
             f"WHERE pr.organization_id = CAST(:kb_org AS uuid) AND {vis} "
             "AND pr.status = :status "
             "ORDER BY pr.created_at, pr.id LIMIT :limit"),
        {**vparams, "status": status, "limit": MAX_LIST},
    )).fetchall()
    me = core.viewer_email(user)
    writable = await _writable_space_ids(db, user, sorted({str(r.space_id) for r in rows}))
    return [
        edit_dict(r, page_title=r.page_title) for r in rows
        if r.proposer_email == me or str(r.space_id) in writable
    ]


#: The draft state of a proposed page for each status. ``expired`` has none
#: until KB-5c.
_PAGE_STATE: dict[str, str] = {
    "pending": "p.state = 'draft'",
    "accepted": "p.state = 'published'",
    "rejected": "p.state = 'archived' AND p.archived_from_state = 'draft'",
}


async def _page_rows(db: Any, user: UserContext, status: str) -> list[dict[str, Any]]:
    """The proposed pages with *status* that the caller asked for (F1)."""
    state = _PAGE_STATE.get(status)
    if state is None:
        return []
    vis, vparams = core.visible_pages_clause(user, "p")
    rows = (await db.execute(
        text(f"SELECT {core.PAGE_COLUMNS} FROM kb_pages p WHERE {vis} "
             f"AND p.origin ->> 'kind' = '{PROPOSAL_REASON}' "
             f"AND p.author_email = :kb_viewer AND {state} "
             "ORDER BY p.created_at, p.id LIMIT :limit"),
        {**vparams, "limit": MAX_LIST},
    )).fetchall()
    return [page_proposal_dict(r) for r in rows]


@router.get("/proposals")
async def list_proposals(
    status: str = Query("pending", max_length=20),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The proposals the caller can see, oldest first, pages then edits."""
    if status not in STATUSES:
        raise _unprocessable(f"Unknown status '{status}'. One of: {list(STATUSES)}.")
    async with core._tenant_session() as db:
        out = [*await _page_rows(db, user, status), *await _edit_rows(db, user, status)]
    return {"proposals": out}


# ── The decisions ───────────────────────────────────────────────────────────

async def _edit_proposal(db: Any, user: UserContext, proposal_id: str) -> Any:
    """The edit proposal row, by organization and id, or None."""
    return (await db.execute(
        text(f"SELECT {_PROPOSAL_COLUMNS} FROM kb_page_proposals pr "
             "WHERE pr.organization_id = CAST(:org AS uuid) "
             "AND pr.id = CAST(:id AS uuid)"),
        {"org": core.org_of(user), "id": proposal_id},
    )).first()


async def _gate_edit(db: Any, user: UserContext, proposal: Any) -> tuple[Any, Any]:
    """F1 for an edit proposal: the locked page and its space, or a refusal.

    404 when the caller cannot see the page, or is neither the proposer nor a
    writer of the space. 403 for the proposer with no ``write`` now. 409 for
    a proposal that is decided, or a page that is archived.
    """
    page = await core.load_page(db, user, str(proposal.page_id), lock=True)
    if page is None:
        raise core.not_found()
    space = await pages._space_of(db, user, page)
    writes = core.can_write_page(await core.space_level(db, user, space))
    if not writes and proposal.proposer_email != core.viewer_email(user):
        raise core.not_found()
    if not writes:
        raise core.forbidden("Deciding this proposal needs a write grant on the page's space.")
    if proposal.status != "pending":
        raise pages._conflict(f"The proposal is {proposal.status}.")
    if page.state == "archived":
        raise pages._conflict("The page is archived.")
    return page, space


async def _gate_page(db: Any, user: UserContext, page_id: str) -> Any:
    """F1 for a proposed page: the locked draft, or a refusal.

    Only the author decides. Any other caller, a named reviewer too, gets the
    404 of a missing proposal. 403 for an author with no ``write`` now. 409
    once the draft is published or archived.
    """
    page = await core.load_page(db, user, page_id, lock=True)
    if page is None or not is_page_proposal(page):
        raise core.not_found()
    if page.author_email != core.viewer_email(user):
        raise core.not_found()
    space = await pages._space_of(db, user, page)
    if not core.can_write_page(await core.space_level(db, user, space)):
        raise core.forbidden("Deciding this proposal needs a write grant on its space.")
    if page.state != "draft":
        raise pages._conflict(f"The proposal is {_page_status(page)}.")
    return page


async def _decide(db: Any, user: UserContext, proposal_id: str, status: str) -> Any:
    """Mark a pending edit proposal decided. 409 when another call did first."""
    row = (await db.execute(
        text("UPDATE kb_page_proposals AS pr SET status = :status, "
             "decided_by = :me, decided_at = now() "
             "WHERE pr.organization_id = CAST(:org AS uuid) "
             "AND pr.id = CAST(:id AS uuid) AND pr.status = 'pending' "
             f"RETURNING {_PROPOSAL_COLUMNS}"),
        {"status": status, "me": core.viewer_email(user), "org": core.org_of(user),
         "id": proposal_id},
    )).first()
    if row is None:
        raise pages._conflict("The proposal is decided.")
    return row


@router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(
    proposal_id: str,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Accept a proposal. A page publishes, and an edit applies at its base."""
    _require_web()
    pid = core.parse_id(proposal_id)
    if pid is None:
        raise core.not_found()
    async with core._tenant_session() as db:
        proposal = await _edit_proposal(db, user, pid)
        if proposal is not None:
            page, _space = await _gate_edit(db, user, proposal)
            if await _newest_version(db, user, str(page.id)) != proposal.base_version:
                raise pages._conflict(
                    "The page changed after the proposal was made. Ask for a new one.")
            decided = await _decide(db, user, pid, "accepted")
            row = await pages._update(
                db, user, str(page.id), "title = :title, body_md = :body",
                {"title": proposal.title, "body": proposal.body_md},
            )
            out = edit_dict(decided, page_title=row.title)
            out["page"] = await pages._written(db, user, row, ACCEPT_REASON, background)
            return out
        page = await _gate_page(db, user, pid)
        row = await pages._update(db, user, str(page.id), pages.PUBLISH_SETS, {})
        out = page_proposal_dict(row)
        out["decided_by"] = core.viewer_email(user)
        out["page"] = await pages._written(db, user, row, ACCEPT_REASON, background)
        return out


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Reject a proposal. A page archives as a draft, and an edit is marked."""
    _require_web()
    pid = core.parse_id(proposal_id)
    if pid is None:
        raise core.not_found()
    async with core._tenant_session() as db:
        proposal = await _edit_proposal(db, user, pid)
        if proposal is not None:
            page, _space = await _gate_edit(db, user, proposal)
            decided = await _decide(db, user, pid, "rejected")
            return edit_dict(decided, page_title=page.title)
        page = await _gate_page(db, user, pid)
        row = await pages._update(db, user, str(page.id), pages.ARCHIVE_SETS, {})
        out = page_proposal_dict(row)
        out["decided_by"] = core.viewer_email(user)
        out["page"] = await pages._written(db, user, row, "archive", background)
        return out
