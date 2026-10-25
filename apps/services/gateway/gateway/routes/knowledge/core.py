"""Knowledge — the leaf of the ``/knowledge`` API (WS-61 KB-1).

Spec: ``project-docs/specs/knowledge_base.md`` §4.1 (spaces), §4.2 (pages),
§5.1 (visibility), §5.2 (permissions), §5.4 (``via``) and §12 (columns).

This module holds the router, the ONE visibility rule and the data helpers
that ``spaces.py`` and ``pages.py`` call. It imports nothing from its
siblings except ``flags``, so each feature module can import it.

**The one visibility rule (§5.1).** :func:`visible_pages_clause` is the only
predicate that decides which pages a member sees. Every read of ``kb_pages``
puts it in, and ``tests/unit/test_kb_visibility_one_seam.py`` fails on a read
that does not. A member sees a page when one of these holds:

* the page is ``published`` and its space is not a ``me`` space, and the
  member holds a grant on the space whose subject is ``org``, their email, or
  one of their groups. The space owner and a holder of ``knowledge:manage``
  see each published page of each space that is not a ``me`` space;
* the page is ``archived`` in a space that is not a ``me`` space, and the
  member manages the space: ``knowledge:manage``, the space owner, or a
  ``manage`` grant. That is the archive view of §4.2;
* the page is a ``draft`` that the member wrote. Authorship gives no read of
  a published or archived page: an author whose grant is gone reads the
  space no more, like any other member;
* the page is a ``draft`` and names the member in ``reviewers``;
* the page is in the member's own ``me`` space, in any state.

``knowledge:manage`` never shows a draft that is not the holder's own, and
never a ``me`` space. ``data:org:read`` gives nothing here, so this module
does not call ``resolve_visibility`` (which drops the groups for a holder of
that permission) and does not copy ``_VISIBLE_PROJECTS_SQL``. The groups come
from ``routes/projects/core._MY_GROUPS_SQL``, imported and not copied, as
``routes/crm/access.py`` does.

**The tenant and the member come from the session (R5, R11).** Each helper
takes the resolved ``UserContext``. Each statement also names
``user.organization_id``, so a session with row level security off still
reads one organization only.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from acb_auth import UserContext, require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException

# The shared seam, aliased so a test can patch `_tenant_session` by name.
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from gateway.routes.knowledge.flags import require_knowledge_enabled
from gateway.routes.projects.core import (
    _MY_GROUPS_SQL,
    ACTOR_VIA,
    capture_actor_via,
    validate_grant_subject,
)
from sqlalchemy import text

_log = get_logger("gateway.knowledge")

router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    dependencies=[
        Depends(require_knowledge_enabled),
        require_feature_router("knowledge"),
        Depends(capture_actor_via),
    ],
)

#: The admin verb of §5.2: manage on every space except a ``me`` space.
MANAGE_PERMISSION = "knowledge:manage"

#: Mirrors of the CHECKs in the KB-1 migration (``kb_spaces``), so a bad value
#: is a 422 at the boundary and not a 500.
SPACE_KINDS: tuple[str, ...] = ("company", "team", "me", "agent")
PAGE_STATES: tuple[str, ...] = ("draft", "published", "archived")
GRANT_LEVELS: tuple[str, ...] = ("read", "write", "manage")
VIAS: tuple[str, ...] = ("web", "chat", "mcp", "sync", "artifact")

#: The rank of a grant level. A higher level holds each lower one.
LEVEL_RANK: dict[str, int] = {"read": 1, "write": 2, "manage": 3}

#: The company space (§4.1), made on the first ``GET /knowledge/spaces``.
COMPANY_SLUG = "company"
COMPANY_NAME = "Company"
COMPANY_PURPOSE = "Products, services, policies, and how we do things."

#: The member's own space (§4.1), made on the member's first write.
ME_SLUG = "me"
ME_NAME = "My notes"
ME_PURPOSE = "Your private drafts. Only you can see them."

#: The days from a publish to the review date (§4.2).
REVIEW_DAYS = 180

#: The days without an edit after which a published page reads as stale
#: (KB-AI-4). The maintenance list uses it.
STALE_DAYS = 365


def VERIFIED_SQL(alias: str) -> str:
    """The one test of a verified page (KB-AI-4), on the page alias *alias*.

    A page is verified when it has an owner AND a review date in the future.
    A page with no review date is not verified. Search and the list share
    this text, so the two cannot drift. *alias* is code, never input.
    """
    return f"{alias}.owner_email IS NOT NULL AND {alias}.review_at > now()"

#: The highest version number. ``kb_page_versions.version`` is int4, so a
#: route refuses a bigger number with 422 before it reaches the driver.
MAX_VERSION = 2_147_483_647

#: The page columns that each page read returns. ``p.icon`` is one emoji, or
#: NULL (KB-2f). It is not the icon NAME of a space.
PAGE_COLUMNS = (
    "p.id, p.space_id, p.parent_id, p.position, p.title, p.body_md, p.state, "
    "p.author_email, p.reviewers, p.review_at, p.source_ref, p.origin, "
    "p.published_at, p.archived_at, p.updated_at, p.created_at, p.history_from, "
    "p.archived_from_state, p.icon, p.owner_email, p.review_every_days"
)

#: The space columns that each space read returns.
SPACE_COLUMNS = (
    "s.id, s.slug, s.name, s.purpose, s.icon, s.kind, s.owner_email, "
    "s.archived_at, s.created_at"
)

# The member's groups, from the Projects read model. The query names its one
# parameter `:email`. It is renamed here so it cannot meet a caller's own
# `:email`, and the rename is checked when the module loads.
_GROUPS_OF_VIEWER = _MY_GROUPS_SQL.replace(":email", ":kb_viewer")
if _GROUPS_OF_VIEWER.count(":kb_viewer") != 1:  # pragma: no cover — a guard
    raise RuntimeError(
        "routes/projects/core._MY_GROUPS_SQL changed shape. It must bind the "
        "member's email once as :email."
    )


# ── Who is asking ───────────────────────────────────────────────────────────

def viewer_email(user: UserContext) -> str:
    """The caller's email, folded (R10). Empty for a principal with none."""
    return (user.email or "").strip().lower()


def is_manager(user: UserContext) -> bool:
    """True when the caller holds ``knowledge:manage``."""
    return bool(user.has_permission(MANAGE_PERMISSION))


def org_of(user: UserContext) -> str:
    """The caller's organization. The kill switch refuses a caller with none."""
    org = str(user.organization_id or "").strip()
    if not org:
        raise not_found()
    return org


def current_via() -> str:
    """Through what the caller acted (§5.4), from the bound ``X-Actor-Via``.

    ``chat:<agent>`` is ``chat``, ``mcp:<client>`` is ``mcp``, and every other
    value or no header is ``web``. ``sync`` and ``artifact`` are written by
    the code paths that own them (KB-6, KB-7), never by a header.
    """
    value = ACTOR_VIA.get("")
    if value.startswith("mcp:"):
        return "mcp"
    if value.startswith("chat"):
        return "chat"
    return "web"


def not_found() -> HTTPException:
    """The one answer for a page or space the caller cannot see."""
    return HTTPException(status_code=404, detail="Not found")


def forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def parse_id(value: str | None) -> str | None:
    """A UUID in canonical form, or None for a value that is not one."""
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


# ── The one visibility rule ─────────────────────────────────────────────────

def _subject_match(grant_alias: str) -> str:
    """A grant whose subject is ``org``, the caller's email, or a group."""
    g = grant_alias
    return (
        f"({g}.subject = 'org' OR {g}.subject = :kb_viewer "
        f"OR {g}.subject IN (SELECT kb_mg.subject FROM ({_GROUPS_OF_VIEWER}) AS kb_mg))"
    )


def _viewer_params(user: UserContext) -> dict[str, Any]:
    return {
        "kb_org": org_of(user),
        "kb_viewer": viewer_email(user),
        "kb_manage": is_manager(user),
    }


def visible_pages_clause(
    user: UserContext, alias: str = "p",
) -> tuple[str, dict[str, Any]]:
    """The ONE predicate of who sees a page (§5.1), and its parameters.

    Put the text in a statement that reads ``kb_pages AS <alias>``, and merge
    the parameters into the statement's own. The names start with ``kb_`` so
    they do not meet a caller's names. There is no recursion and no copy of
    the project grant SQL.
    """
    a = alias
    sql = (
        f"({a}.organization_id = CAST(:kb_org AS uuid) AND ("
        f"({a}.state = 'published' AND EXISTS ("
        f"SELECT 1 FROM kb_spaces kb_s"
        f" WHERE kb_s.id = {a}.space_id"
        f" AND kb_s.organization_id = CAST(:kb_org AS uuid)"
        f" AND kb_s.kind <> 'me'"
        f" AND (CAST(:kb_manage AS boolean)"
        f" OR kb_s.owner_email = :kb_viewer"
        f" OR EXISTS (SELECT 1 FROM kb_space_grants kb_g"
        f" WHERE kb_g.space_id = kb_s.id AND {_subject_match('kb_g')}))))"
        f" OR ({a}.state = 'archived'"
        f" AND ({a}.archived_from_state IS DISTINCT FROM 'draft'"
        f" OR {a}.author_email = :kb_viewer)"
        f" AND EXISTS ("
        f"SELECT 1 FROM kb_spaces kb_s"
        f" WHERE kb_s.id = {a}.space_id"
        f" AND kb_s.organization_id = CAST(:kb_org AS uuid)"
        f" AND kb_s.kind <> 'me'"
        f" AND (CAST(:kb_manage AS boolean)"
        f" OR kb_s.owner_email = :kb_viewer"
        f" OR EXISTS (SELECT 1 FROM kb_space_grants kb_g"
        f" WHERE kb_g.space_id = kb_s.id AND kb_g.level = 'manage'"
        f" AND {_subject_match('kb_g')}))))"
        f" OR ({a}.author_email = :kb_viewer AND ({a}.state = 'draft'"
        f" OR ({a}.state = 'archived' AND {a}.archived_from_state = 'draft')))"
        f" OR ({a}.state = 'draft' AND :kb_viewer = ANY({a}.reviewers))"
        f" OR EXISTS (SELECT 1 FROM kb_spaces kb_s"
        f" WHERE kb_s.id = {a}.space_id"
        f" AND kb_s.organization_id = CAST(:kb_org AS uuid)"
        f" AND kb_s.kind = 'me' AND kb_s.owner_email = :kb_viewer)"
        f"))"
    )
    return sql, _viewer_params(user)


def readable_spaces_clause(
    user: UserContext, alias: str = "s",
) -> tuple[str, dict[str, Any]]:
    """The spaces the caller can open: the read side of a space.

    The caller's own ``me`` space, and each other space where the caller is
    the owner, holds ``knowledge:manage``, or holds a grant. Another member's
    ``me`` space never matches.
    """
    a = alias
    sql = (
        f"({a}.organization_id = CAST(:kb_org AS uuid) AND ("
        f"({a}.kind = 'me' AND {a}.owner_email = :kb_viewer)"
        f" OR ({a}.kind <> 'me' AND (CAST(:kb_manage AS boolean)"
        f" OR {a}.owner_email = :kb_viewer"
        f" OR EXISTS (SELECT 1 FROM kb_space_grants kb_g"
        f" WHERE kb_g.space_id = {a}.id AND {_subject_match('kb_g')})))"
        f"))"
    )
    return sql, _viewer_params(user)


# ── Audit ───────────────────────────────────────────────────────────────────

def audit(user: UserContext, action: str, space_id: str, payload: dict[str, Any]) -> None:
    """One audit line for an admin act on a space (make, change, grants).

    A page write needs no line here: ``kb_page_versions`` is its append-only
    record. A down audit backend never undoes the act.
    """
    try:
        from acb_audit import AuditEvent, record

        org = org_of(user)
        record(AuditEvent(
            actor=f"user:{viewer_email(user)}",
            action=action,
            target=f"kb_space:{space_id}",
            payload={"organization_id": org, **payload},
            organization_id=org,
        ))
    except Exception as exc:  # pragma: no cover — defensive, per the docstring
        _log.warning("knowledge.audit_failed", action=action, error=type(exc).__name__)


# ── Spaces ──────────────────────────────────────────────────────────────────

def space_dict(row: Any, level: str | None = None) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "slug": row.slug,
        "name": row.name,
        "purpose": row.purpose,
        "icon": row.icon,
        "kind": row.kind,
        "owner_email": row.owner_email,
        "archived_at": _iso(row.archived_at),
        "created_at": _iso(row.created_at),
    }
    if level is not None:
        out["level"] = level
    return out


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def load_space(db: Any, user: UserContext, space_id: str | None) -> Any:
    """The space if the caller can open it, else None (the caller says 404)."""
    sid = parse_id(space_id)
    if sid is None:
        return None
    clause, params = readable_spaces_clause(user, "s")
    return (await db.execute(
        text(f"SELECT {SPACE_COLUMNS} FROM kb_spaces s "
             f"WHERE s.id = CAST(:sid AS uuid) AND {clause}"),
        {**params, "sid": sid},
    )).first()


async def levels_by_grant(
    db: Any, user: UserContext, space_ids: list[str],
) -> dict[str, str]:
    """The best grant level the caller holds on each space, by its grants."""
    if not space_ids:
        return {}
    params = _viewer_params(user)
    rows = (await db.execute(
        text("SELECT kb_g.space_id, kb_g.level FROM kb_space_grants kb_g "
             "WHERE kb_g.organization_id = CAST(:kb_org AS uuid) "
             "AND kb_g.space_id = ANY(CAST(:ids AS uuid[])) "
             f"AND {_subject_match('kb_g')}"),
        {**params, "ids": space_ids},
    )).fetchall()
    best: dict[str, str] = {}
    for row in rows:
        sid = str(row.space_id)
        if LEVEL_RANK.get(row.level, 0) > LEVEL_RANK.get(best.get(sid, ""), 0):
            best[sid] = row.level
    return best


def level_on(user: UserContext, space: Any, by_grant: str | None) -> str | None:
    """The caller's level on *space*: manage, write, read, or None.

    A ``me`` space is its owner's alone, with no grant and no capability. On
    each other space the owner and a holder of ``knowledge:manage`` manage.
    """
    me = viewer_email(user)
    if space.kind == "me":
        return "manage" if me and space.owner_email == me else None
    if me and space.owner_email == me:
        return "manage"
    if is_manager(user):
        return "manage"
    return by_grant


async def space_level(db: Any, user: UserContext, space: Any) -> str | None:
    by_grant = (await levels_by_grant(db, user, [str(space.id)])).get(str(space.id))
    return level_on(user, space, by_grant)


async def ensure_company_space(db: Any, user: UserContext) -> None:
    """Make the organization's ``company`` space with an ``org`` read grant.

    Race-free: the partial unique index on ``(organization_id, slug)`` keeps
    one row, and only the insert that made it adds the grant. An admin who
    later changes the grants is never undone, because the space exists.
    """
    org = org_of(user)
    made = (await db.execute(
        text("INSERT INTO kb_spaces (organization_id, slug, name, purpose, kind) "
             "VALUES (CAST(:org AS uuid), :slug, :name, :purpose, 'company') "
             "ON CONFLICT (organization_id, slug) WHERE kind <> 'me' DO NOTHING "
             "RETURNING id"),
        {"org": org, "slug": COMPANY_SLUG, "name": COMPANY_NAME,
         "purpose": COMPANY_PURPOSE},
    )).first()
    if made is None:
        return
    await db.execute(
        text("INSERT INTO kb_space_grants "
             "(organization_id, space_id, subject, level, created_by) "
             "VALUES (CAST(:org AS uuid), :sid, 'org', 'read', 'system') "
             "ON CONFLICT DO NOTHING"),
        {"org": org, "sid": made.id},
    )


#: Make the caller's ``me`` space, or do nothing when it exists. The conflict
#: target names the partial unique index of the KB-1 migration.
INSERT_ME_SPACE_SQL = (
    "INSERT INTO kb_spaces "
    "(organization_id, slug, name, purpose, kind, owner_email) "
    "VALUES (CAST(:org AS uuid), :slug, :name, :purpose, 'me', :me) "
    "ON CONFLICT (organization_id, owner_email) WHERE kind = 'me' DO NOTHING"
)

#: Read the caller's ``me`` space after :data:`INSERT_ME_SPACE_SQL`.
SELECT_ME_SPACE_SQL = (
    f"SELECT {SPACE_COLUMNS} FROM kb_spaces s "
    "WHERE s.organization_id = CAST(:org AS uuid) "
    "AND s.kind = 'me' AND s.owner_email = :me"
)


def me_space_params(user: UserContext) -> dict[str, Any]:
    me = viewer_email(user)
    if not me:
        raise forbidden("Knowledge needs a member with an email address.")
    return {"org": org_of(user), "slug": ME_SLUG, "name": ME_NAME,
            "purpose": ME_PURPOSE, "me": me}


async def ensure_me_space(db: Any, user: UserContext) -> Any:
    """The caller's ``me`` space, made on the first write.

    Insert with ``ON CONFLICT DO NOTHING`` on the partial unique index, then
    read again. Two first writes at the same time make one row: the second
    insert waits for the first to commit, does nothing, and reads its row.
    """
    params = me_space_params(user)
    await db.execute(text(INSERT_ME_SPACE_SQL), params)
    return (await db.execute(text(SELECT_ME_SPACE_SQL), params)).first()


async def read_grants(db: Any, user: UserContext, space_id: str) -> list[dict[str, Any]]:
    rows = (await db.execute(
        text("SELECT subject, level, created_by, created_at FROM kb_space_grants "
             "WHERE organization_id = CAST(:org AS uuid) "
             "AND space_id = CAST(:sid AS uuid) ORDER BY subject"),
        {"org": org_of(user), "sid": space_id},
    )).fetchall()
    return [
        {"subject": r.subject, "level": r.level, "created_by": r.created_by,
         "created_at": _iso(r.created_at)}
        for r in rows
    ]


def clean_grants(grants: list[Any]) -> list[tuple[str, str]]:
    """Validated ``(subject, level)`` pairs. 422 on a bad or repeated subject."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for grant in grants:
        subject = validate_grant_subject(grant.subject)
        level = (grant.level or "").strip().lower()
        if level not in GRANT_LEVELS:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown grant level '{grant.level}'. One of: {list(GRANT_LEVELS)}.",
            )
        if subject in seen:
            raise HTTPException(
                status_code=422, detail=f"The subject '{subject}' is named twice.",
            )
        seen.add(subject)
        out.append((subject, level))
    return out


async def write_grants(
    db: Any, user: UserContext, space_id: str, grants: list[tuple[str, str]],
) -> None:
    for subject, level in grants:
        await db.execute(
            text("INSERT INTO kb_space_grants "
                 "(organization_id, space_id, subject, level, created_by) "
                 "VALUES (CAST(:org AS uuid), CAST(:sid AS uuid), :subject, "
                 ":level, :by)"),
            {"org": org_of(user), "sid": space_id, "subject": subject,
             "level": level, "by": viewer_email(user) or "system"},
        )


# ── Pages ───────────────────────────────────────────────────────────────────

def page_dict(
    row: Any, *, body: bool = True, position: int | None = None,
) -> dict[str, Any]:
    """The answer for one page.

    ``position`` is the rank among the siblings the CALLER sees (KB-2e), and
    the caller passes it. The stored ``kb_pages.position`` counts hidden
    siblings too, so it never goes out: a gap in it would tell a member that
    a hidden page sits there.
    """
    out = {
        "id": str(row.id),
        "space_id": str(row.space_id),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "position": position,
        "title": row.title,
        "icon": row.icon,
        "state": row.state,
        "author_email": row.author_email,
        "reviewers": list(row.reviewers or []),
        "review_at": _iso(row.review_at),
        "owner_email": getattr(row, "owner_email", None),
        "review_every_days": getattr(row, "review_every_days", None),
        "source_ref": row.source_ref,
        "published_at": _iso(row.published_at),
        "archived_at": _iso(row.archived_at),
        "history_from": row.history_from,
        "archived_from_state": row.archived_from_state,
        "updated_at": _iso(row.updated_at),
        "created_at": _iso(row.created_at),
    }
    if body:
        out["body_md"] = row.body_md
        out["origin"] = row.origin
    return out


async def load_page(
    db: Any, user: UserContext, page_id: str | None, *, lock: bool = False,
) -> Any:
    """The page if the caller can see it, else None (the caller says 404).

    With *lock*, the row is locked for the rest of the transaction, so two
    writes to one page take their version numbers one after the other.
    """
    pid = parse_id(page_id)
    if pid is None:
        return None
    vis, params = visible_pages_clause(user, "p")
    suffix = " FOR UPDATE OF p" if lock else ""
    return (await db.execute(
        text(f"SELECT {PAGE_COLUMNS} FROM kb_pages p "
             f"WHERE p.id = CAST(:pid AS uuid) AND {vis}{suffix}"),
        {**params, "pid": pid},
    )).first()


async def has_children(db: Any, user: UserContext, page_id: str) -> bool:
    """True when any page names *page_id* as its parent.

    An integrity read (listed in the visibility fence): a child the caller
    cannot see still counts, and the answer is a bool, never a row.
    """
    return (await db.execute(
        text("SELECT EXISTS (SELECT 1 FROM kb_pages "
             "WHERE organization_id = CAST(:org AS uuid) "
             "AND parent_id = CAST(:pid AS uuid))"),
        {"org": org_of(user), "pid": page_id},
    )).scalar() is True


async def sibling_rows(
    db: Any, user: UserContext, space_id: str, parent_id: str | None,
) -> list[Any]:
    """Every page under *parent_id* in *space_id*, in each state (KB-2e).

    An integrity read (listed in the visibility fence): the renumber of
    ``pages._place`` writes 0 to n over ALL the siblings, so a sibling the
    caller cannot see keeps its own place. Each row holds the id, the
    position and the sort keys. Only ``pages._place`` reads them, to write
    the positions. No row, no count and no stored position goes back to a
    caller. The order is :func:`sibling_key`, the order the tree draws.
    """
    params: dict[str, Any] = {"org": org_of(user), "sid": space_id}
    if parent_id is None:
        under = "parent_id IS NULL"
    else:
        under = "parent_id = CAST(:parent AS uuid)"
        params["parent"] = parent_id
    rows = (await db.execute(
        text("SELECT id, position, title, created_at FROM kb_pages "
             "WHERE organization_id = CAST(:org AS uuid) "
             f"AND space_id = CAST(:sid AS uuid) AND {under}"),
        params,
    )).fetchall()
    return sorted(rows, key=sibling_key)


async def next_position(
    db: Any, user: UserContext, space_id: str, parent_id: str | None,
) -> int:
    """The place after the last sibling, for a new page (KB-2e).

    An integrity read (listed in the visibility fence): a hidden sibling
    counts, so the new page goes after it. The number is stored with the
    page and never returned. Each answer gives the visible rank instead.
    """
    params: dict[str, Any] = {"org": org_of(user), "sid": space_id}
    if parent_id is None:
        under = "parent_id IS NULL"
    else:
        under = "parent_id = CAST(:parent AS uuid)"
        params["parent"] = parent_id
    return int((await db.execute(
        text("SELECT COALESCE(MAX(position), -1) + 1 FROM kb_pages "
             "WHERE organization_id = CAST(:org AS uuid) "
             f"AND space_id = CAST(:sid AS uuid) AND {under}"),
        params,
    )).scalar())


def sibling_key(row: Any) -> tuple[int, str, str, str]:
    """The order of siblings, the same as ``compareSiblings`` in the client.

    The client draws the tree in this order (``lib/tree.ts``), and the
    position of a move counts in it. Python sorts here, not SQL, because a
    collation can put two titles in another order than the browser does.
    """
    return (int(row.position), str(row.title).lower(), _iso(row.created_at) or "", str(row.id))


#: The deepest tree :func:`ancestor_ids` walks. A deeper chain is refused.
MAX_DEPTH = 64


async def ancestor_ids(db: Any, user: UserContext, page_id: str) -> list[str]:
    """The ids from *page_id* up to the root, *page_id* first.

    An integrity read (listed in the visibility fence): the walk passes
    through pages the caller may not see, and it returns ids for a membership
    test, never a row. A loop in Python, one row each step, with no
    recursive SQL. A chain deeper than :data:`MAX_DEPTH` is a 422.
    """
    org = org_of(user)
    chain: list[str] = []
    current: str | None = page_id
    while current is not None:
        if current in chain or len(chain) >= MAX_DEPTH:
            raise HTTPException(status_code=422, detail="The page tree is too deep.")
        chain.append(current)
        row = (await db.execute(
            text("SELECT parent_id FROM kb_pages "
                 "WHERE organization_id = CAST(:org AS uuid) "
                 "AND id = CAST(:pid AS uuid)"),
            {"org": org, "pid": current},
        )).first()
        current = str(row.parent_id) if row is not None and row.parent_id else None
    return chain


async def append_version(
    db: Any, user: UserContext, page: Any, *, title: str, body_md: str,
    reason: str,
) -> int:
    """Append one ``kb_page_versions`` row for a write, and return its number.

    The caller holds the page row locked (:func:`load_page` with ``lock``) or
    has just made it, so the next number cannot race.
    """
    return int((await db.execute(
        text("INSERT INTO kb_page_versions "
             "(organization_id, page_id, version, body_md, title, author_email, "
             "reason, via) "
             "SELECT CAST(:org AS uuid), CAST(:pid AS uuid), "
             "COALESCE(MAX(v.version), 0) + 1, :body, :title, :author, :reason, :via "
             "FROM kb_page_versions v "
             "WHERE v.organization_id = CAST(:org AS uuid) "
             "AND v.page_id = CAST(:pid AS uuid) "
             "RETURNING version"),
        {"org": org_of(user), "pid": str(page.id), "body": body_md,
         "title": title, "author": viewer_email(user), "reason": reason,
         "via": current_via()},
    )).scalar())


def history_floor(user: UserContext, page: Any, space: Any) -> int:
    """The first version the caller may read or revert to.

    A move to another space sets ``kb_pages.history_from`` to the number of
    its own ``move`` version. The versions before it were written in the old
    space, so a reader of the new space never sees them: a page written in a
    ``me`` space and then shared does not share its private drafts. The one
    exception is the author in their own ``me`` space, who keeps the whole
    history there.
    """
    me = viewer_email(user)
    if space is not None and space.kind == "me" and me and space.owner_email == me:
        return 1
    return int(page.history_from or 1)


async def read_versions(
    db: Any, user: UserContext, page_id: str, floor: int,
) -> list[dict[str, Any]]:
    """The versions at or after *floor* (:func:`history_floor`), newest first."""
    rows = (await db.execute(
        text("SELECT version, title, author_email, reason, via, created_at "
             "FROM kb_page_versions WHERE organization_id = CAST(:org AS uuid) "
             "AND page_id = CAST(:pid AS uuid) AND version >= :floor "
             "ORDER BY version DESC"),
        {"org": org_of(user), "pid": page_id, "floor": floor},
    )).fetchall()
    return [
        {"version": r.version, "title": r.title, "author_email": r.author_email,
         "reason": r.reason, "via": r.via, "created_at": _iso(r.created_at)}
        for r in rows
    ]


async def read_links(db: Any, user: UserContext, page_id: str) -> list[dict[str, Any]]:
    rows = (await db.execute(
        text("SELECT id, target_kind, target_id, label, created_by, created_at "
             "FROM kb_links WHERE organization_id = CAST(:org AS uuid) "
             "AND page_id = CAST(:pid AS uuid) ORDER BY created_at"),
        {"org": org_of(user), "pid": page_id},
    )).fetchall()
    # A page link lists only when the CALLER can see its target (§5.5): a
    # link is a reference and never a grant, and it must not tell a reader
    # that a hidden page exists. The other kinds are listed as they are.
    wanted = [str(r.target_id) for r in rows if r.target_kind == "page"]
    seen: set[str] = set()
    if wanted:
        vis, vparams = visible_pages_clause(user, "p")
        seen = {str(r[0]) for r in (await db.execute(
            text(f"SELECT p.id::text FROM kb_pages p WHERE {vis} "
                 "AND p.id::text = ANY(CAST(:rl_ids AS text[]))"),
            {**vparams, "rl_ids": wanted},
        )).fetchall()}
    return [
        {"id": str(r.id), "target_kind": r.target_kind, "target_id": r.target_id,
         "label": r.label, "created_by": r.created_by,
         "created_at": _iso(r.created_at)}
        for r in rows
        if r.target_kind != "page" or str(r.target_id) in seen
    ]


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")

#: The most reviewers one draft names.
MAX_REVIEWERS = 20


def clean_reviewers(values: list[str] | None) -> list[str]:
    """Reviewer emails, folded and once each. 422 on a value that is not one."""
    out: list[str] = []
    for raw in values or []:
        value = (raw or "").strip().lower()
        if not _EMAIL.match(value):
            raise HTTPException(
                status_code=422, detail=f"A reviewer must be an email address, not '{raw}'.",
            )
        if value not in out:
            out.append(value)
    if len(out) > MAX_REVIEWERS:
        raise HTTPException(
            status_code=422, detail=f"A draft names at most {MAX_REVIEWERS} reviewers.",
        )
    return out


def clean_owner(raw: str) -> str:
    """The owner address of a page, folded. ValueError on a value that is no address."""
    value = (raw or "").strip().lower()
    if not _EMAIL.match(value):
        raise ValueError("An owner must be an email address.")
    return value


def can_write_page(level: str | None) -> bool:
    """True when the caller holds ``write`` or ``manage`` on the page's space.

    The level is read at the time of the call. Authorship is no bypass: an
    author who lost ``write`` keeps reading the draft, and can move it to
    their own ``me`` space, but cannot edit or publish it where it is. In the
    author's own ``me`` space the author holds ``manage`` (:func:`level_on`).
    """
    return LEVEL_RANK.get(level or "", 0) >= LEVEL_RANK["write"]


def org_grant(grants: list[tuple[str, str]]) -> str | None:
    """The level of the ``org`` subject in *grants*, or None."""
    return next((level for subject, level in grants if subject == "org"), None)
