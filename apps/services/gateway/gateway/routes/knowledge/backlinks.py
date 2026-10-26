"""Backlinks: which pages link to this page (WS-61 KB-2h).

Spec: ``project-docs/specs/knowledge_base.md`` §21.5 (KB-2h) and §21.7.

    GET /knowledge/pages/{id}/backlinks   the pages that link here

Each page write reads the saved ``body_md`` for links of the form
``/knowledge/pages/<uuid>`` and keeps them in ``kb_links`` with
``target_kind = 'page'`` (:func:`sync_page_links`). The write runs in the
transaction of the page write. Other kinds of link in that table stay as they
are. A link is a reference and never a grant (§5.5).

The read joins ``kb_links`` to ``kb_pages`` under the one visibility clause
(:func:`core.visible_pages_clause`), so a source the caller cannot see is
absent. It lists PUBLISHED sources only. A draft is not yet part of the
company's knowledge, and an archived page is out of the live tree.

A page written before KB-2h has no rows until its next write, or until an
operator runs ``scripts/backfill_kb_links.py``. That script calls
:func:`backfill_org_links`, which runs :func:`sync_page_links` for each page
that names a page and has no page-kind row. A second run changes nothing.

⚠️ Fences: ``tests/unit/test_kb_backlinks_r8.py`` (a real database) and
``tests/unit/test_kb_visibility_one_seam.py`` (the one clause).
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.knowledge import core
from gateway.routes.knowledge.core import router
from sqlalchemy import text

#: The most backlinks one answer lists.
MAX_BACKLINKS = 200

_FENCE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(
    r"/knowledge/pages/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"(?![0-9A-Za-z-])"
)


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])(?:\s|$)")
_INDENTED = re.compile(r"^(?: {4,}|\t)")


def _drop_indented_code(text_: str) -> str:
    """Remove indented code blocks (CommonMark: 4+ spaces after a blank line).

    An indented line is code only when the line before it is blank or is code
    itself, and the block it follows is not a list item (there it continues
    the item). A line that follows paragraph text directly is a continuation.
    """
    out: list[str] = []
    prev_blank = True
    prev_code = False
    in_list = False
    for line in text_.split("\n"):
        blank = not line.strip()
        if blank:
            out.append(line)
            prev_blank = True
            continue
        indented = bool(_INDENTED.match(line))
        if indented and (prev_blank or prev_code) and not in_list:
            out.append("")
            prev_code = True
        else:
            out.append(line)
            prev_code = False
            if not indented:
                in_list = bool(_LIST_ITEM.match(line))
        prev_blank = False
    return "\n".join(out)


def page_ids_in(body_md: str | None, own_id: str | None = None) -> list[str]:
    """The ids of the pages that *body_md* links to, once each, in order.

    A link in a code fence, an indented code block or an inline code span is text and not a link. The
    query and the fragment after an id do not matter. A link to *own_id* is
    dropped.
    """
    text_ = _INLINE_CODE.sub("", _drop_indented_code(_FENCE.sub("", body_md or "")))
    own = (own_id or "").lower()
    out: list[str] = []
    for found in _LINK.findall(text_):
        pid = found.lower()
        if pid != own and pid not in out:
            out.append(pid)
    return out


async def sync_page_links(
    db: Any, user: UserContext, page: Any, body_md: str, *, any_target: bool = False,
) -> int:
    """Make the page-kind links of *page* match *body_md*. Return how many were added.

    It deletes the ``target_kind = 'page'`` rows whose target the body no
    longer names. It keeps a row whose target the body still names, whoever
    saves, so a member who cannot read a restricted target never erases the
    row of a writer who could (K-1 P1). It then adds the named targets that
    the WRITER can see (the one clause) and that are pages of the same
    organization, and a stale link never fails a save. A link never tells a
    writer that a hidden page exists. The organization is the session's, and
    the writer is the session's member.

    ``any_target`` drops the writer's visibility from the add. Only the
    backfill sets it (K-2): nobody reads a link row except through the
    visibility-filtered read, so the row is safe. The org check stays.
    """
    org = core.org_of(user)
    pid = str(page.id)
    ids = page_ids_in(body_md, pid)
    await db.execute(
        text("DELETE FROM kb_links WHERE organization_id = CAST(:org AS uuid) "
             "AND page_id = CAST(:pid AS uuid) AND target_kind = 'page' "
             "AND NOT (target_id = ANY(CAST(:ids AS text[])))"),
        {"org": org, "pid": pid, "ids": ids},
    )
    if not ids:
        return 0
    if any_target:
        vis, vparams = "t.organization_id = CAST(:org AS uuid)", {}
    else:
        vis, vparams = core.visible_pages_clause(user, "t")
    result = await db.execute(
        text("INSERT INTO kb_links (organization_id, page_id, target_kind, "
             "target_id, created_by) "
             "SELECT CAST(:org AS uuid), CAST(:pid AS uuid), 'page', t.id::text, :by "
             f"FROM kb_pages t WHERE {vis} AND t.organization_id = CAST(:org AS uuid) "
             "AND t.id = ANY(CAST(:ids AS uuid[])) AND t.id <> CAST(:pid AS uuid) "
             "ON CONFLICT (organization_id, page_id, target_kind, target_id) DO NOTHING"),
        {**vparams, "org": org, "pid": pid, "by": core.viewer_email(user), "ids": ids},
    )
    return int(result.rowcount or 0)


#: The pages one backfill batch reads. A body can be large, so the batch is small.
BACKFILL_BATCH = 200


async def backfill_org_links(org: str, *, dry_run: bool = False) -> dict[str, int]:
    """Give each page of *org* that has none the page-kind rows its body names.

    K-2. It is the write path's own :func:`sync_page_links`, run as the page's
    author with ``any_target``: every named page of the same org gets a row,
    with no member visibility. The read of the backlinks still filters by the
    reader, so an extra row never shows a hidden page. A page that already has a page-kind row is
    skipped, because a write after KB-2h made it. A page whose links all fail
    the clause gets no row, and a second run finds the same and writes none.
    Returns the counts: ``seen``, ``linked`` (pages that gained rows),
    ``rows`` and ``unchanged``. A dry run rolls back.
    """
    from acb_auth import UserContext, UserRole, build_access

    counts = {"seen": 0, "linked": 0, "rows": 0, "unchanged": 0}
    last = "00000000-0000-0000-0000-000000000000"
    while True:
        async with core._tenant_session(org) as db:
            rows = (await db.execute(
                text("SELECT p.id, p.author_email, p.body_md FROM kb_pages p "
                     "WHERE p.organization_id = CAST(:org AS uuid) "
                     "AND p.id > CAST(:last AS uuid) "
                     "AND p.body_md LIKE '%/knowledge/pages/%' "
                     "AND NOT EXISTS (SELECT 1 FROM kb_links l "
                     "WHERE l.organization_id = p.organization_id "
                     "AND l.page_id = p.id AND l.target_kind = 'page') "
                     "ORDER BY p.id LIMIT :n"),
                {"org": org, "last": last, "n": BACKFILL_BATCH},
            )).fetchall()
            for r in rows:
                counts["seen"] += 1
                author = UserContext(
                    email=r.author_email or "", role=UserRole.EMPLOYEE,
                    access=build_access(["feature:knowledge"]),
                    organization_id=org,
                )
                made = await sync_page_links(db, author, r, r.body_md or "", any_target=True)
                if made:
                    counts["linked"] += 1
                    counts["rows"] += made
                else:
                    counts["unchanged"] += 1
            if dry_run:
                await db.rollback()
        if len(rows) < BACKFILL_BATCH:
            return counts
        last = str(rows[-1].id)


@router.get("/pages/{page_id}/backlinks")
async def get_backlinks(
    page_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The published pages that link here and that the caller can see.

    404 when the caller cannot see the page itself.
    """
    async with core._tenant_session() as db:
        page = await core.load_page(db, user, page_id)
        if page is None:
            raise core.not_found()
        vis, vparams = core.visible_pages_clause(user, "p")
        rows = (await db.execute(
            text("SELECT p.id, p.title, p.icon, s.name AS space_name "
                 "FROM kb_links l JOIN kb_pages p "
                 "ON p.id = l.page_id AND p.organization_id = l.organization_id "
                 "JOIN kb_spaces s ON s.id = p.space_id "
                 "AND s.organization_id = p.organization_id "
                 f"WHERE {vis} AND l.organization_id = CAST(:kb_org AS uuid) "
                 "AND l.target_kind = 'page' AND l.target_id = :bl_target "
                 "AND p.state = 'published' "
                 "ORDER BY lower(p.title), p.id LIMIT :bl_limit"),
            {**vparams, "bl_target": str(page.id), "bl_limit": MAX_BACKLINKS},
        )).fetchall()
    return {"backlinks": [
        {"id": str(r.id), "title": r.title, "icon": r.icon, "space_name": r.space_name}
        for r in rows
    ]}
