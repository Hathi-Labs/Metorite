"""Projects · a lead pins a published Knowledge page to a project (WS-ALN ALN-4).

Spec: ``project-docs/specs/project_alignment.md`` §4.1 and §7.2. Migration:
the file that creates ``pm_project_authorities``.

THE one writer of ``pm_project_authorities``. A pin names the Knowledge page
that holds the rules of one project. ALN-5 reads the pins to find the rules.
This slice has three routes and no UI, no job and no event. The flag
``AUTHORITY_PINS_ORGS`` (default empty) keeps all three at 404.

* ``GET    /nodes/{project_id}/authorities``
* ``POST   /nodes/{project_id}/authorities``  body ``{"page_id": ...}``
* ``DELETE /nodes/{project_id}/authorities/{pin_id}``  (soft: the row stays)

**Who may pin and remove.** The rule of a grant write
(:func:`authority.assert_may_write_grant`): a lead of the project or of an
ancestor, or a member with ``projects:settings:write``. A pin changes who a
finding quotes the page to, so it is a grant-class act. A project in a
personal tree has no pin, for anyone, with a sentence of its own.

**The page.** The caller must see the project (404) and the page (the same
404 for a page of another org and for a page the caller cannot read, so the
answer does not tell the two apart). The page must be ``published``, and not
in a ``me`` space.

**The leak check.** A finding quotes the page to the audience of the
project. A page narrower than that audience would leak its text. The pin
refuses unless it can PROVE that every holder of the project reads the page,
through :func:`knowledge.core.audience_can_read_page`, which is built on the
one Knowledge predicate. Any error is a refusal. The refusal names no page
title. The check runs at the pin only. A later move of the page, or a later
grant change on the project, is NOT re-checked here. ALN-5 must test page
visibility for each recipient when it quotes (``project_alignment.md`` §7.2).

**The version.** ``page_version`` is the highest ``kb_page_versions.version``
of the page, read with the page row locked, so two pins and a write of the
page take their numbers one after the other.

**Stale is derived at read time.** Knowledge emits no event on an archive.
The stored ``state`` holds ``active`` or ``removed``. A read shows ``stale``
when the page is gone, is not ``published``, or has a title or text that
differs from the pinned version. An archive that a restore undoes changes no
word, so it shows ``active`` again.

**The tenant and the actor.** The session sets both. ``organization_id`` is
the column DEFAULT, the bound tenant, and ``pinned_by`` is the caller's
email. A body key for either is ignored.

Fence: ``tests/unit/test_project_authorities_r8.py`` (R8).
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException, Response
from gateway.routes.projects.authority import assert_may_write_grant, in_personal_tree
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    load_visible_project,
    record_activity,
    resolve_visibility,
    router,
)
from gateway.routes.projects.notifications import project_grant_holders
from pydantic import BaseModel
from sqlalchemy import text


def authority_pins_allowed(organization_id: str | None) -> bool:
    """True when ``AUTHORITY_PINS_ORGS`` holds the org, or holds ``*``. Default OFF.

    The org comes from the session, never from request input (R5). It reads
    ``get_settings()`` on each call, so a flag reload needs no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "authority_pins_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_authority_pins(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``AUTHORITY_PINS_ORGS`` does not hold the caller's org."""
    if not authority_pins_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


_ENABLED = [Depends(require_authority_pins)]

#: The 403 of a pin on a node of a personal tree.
PERSONAL_PIN_REFUSED = (
    "A personal space has no authority page. Move the work to a team project "
    "to pin a page."
)
#: The 409 of a page that is not published.
NOT_PUBLISHED_REFUSED = "Only a published page can be the authority of a project."
#: The 409 of a page that is narrower than the project. It names no page.
NARROWER_REFUSED = (
    "This page is visible to fewer people than this project. Share the page "
    "with everyone who can see the project, or pin another page."
)

#: The columns a pin read returns.
_COLUMNS = (
    "id, project_id, page_id, page_version, pinned_by, pinned_at, state, "
    "removed_by, removed_at"
)


def _kb() -> Any:
    """The Knowledge leaf module, imported late.

    A module-level import runs the whole ``routes.knowledge`` package while
    ``routes.projects`` loads, and that package reaches ``routes.admin``,
    which reaches this package. The late import breaks the cycle.
    """
    from gateway.routes.knowledge import core

    return core


class PinIn(BaseModel):
    page_id: str


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _stale_reason(row: Any, currency: dict[str, Any]) -> str | None:
    """Why a stored-active pin reads ``stale``, or None when it is current."""
    if not currency["exists"]:
        return "page_gone"
    if currency["state"] != "published":
        return "not_published"
    if currency["changed"]:
        return "changed"
    return None


def _view(
    row: Any, *, currency: dict[str, Any] | None = None,
    heads: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """One pin for the caller. ``state`` is the DERIVED state.

    A title and a ``space_id`` go out only when *heads* holds the page, that
    is, when the caller can read it. Else ``page_hidden`` is true, and then
    ``stale_reason`` and ``page_version`` are null too, so a member who cannot
    read the page does not learn that it changed. ``state`` stays, because
    that member must still see that the authority of the project needs care.
    """
    state = str(row.state)
    reason = None
    if state != "removed" and currency is not None:
        reason = _stale_reason(row, currency)
        if reason is not None:
            state = "stale"
    out: dict[str, Any] = {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "page_id": str(row.page_id),
        "page_version": int(row.page_version),
        "pinned_by": row.pinned_by,
        "pinned_at": _iso(row.pinned_at),
        "state": state,
        "stale_reason": reason,
        "removed_by": row.removed_by,
        "removed_at": _iso(row.removed_at),
    }
    if heads is not None:
        head = heads.get(str(row.page_id))
        if head is None:
            out["page_hidden"] = True
            out["stale_reason"] = None
            out["page_version"] = None
        else:
            out["page_hidden"] = False
            out["title"] = head["title"]
            out["space_id"] = head["space_id"]
    return out


async def _refuse_if_narrower(
    db: Any, user: UserContext, project: Any, page_id: str,
) -> None:
    """409 unless every holder of the project provably reads the page.

    Any error is a refusal. The savepoints keep an error from aborting the
    caller's transaction.
    """
    kb = _kb()
    org = str(getattr(project, "organization_id", "") or "") or str(
        user.organization_id or "")
    try:
        async with db.begin_nested():
            holders = await project_grant_holders(db, [str(project.id)], org)
        who = holders.get(str(project.id), frozenset())
        ok = await kb.audience_can_read_page(db, org, who, page_id)
    except Exception:  # any doubt is a refusal
        ok = False
    if not ok:
        raise HTTPException(status_code=409, detail=NARROWER_REFUSED)


@router.get("/nodes/{project_id}/authorities", dependencies=_ENABLED)
async def list_authorities(
    project_id: str, include_removed: bool = False,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The pins of one project, newest first, with the derived ``state``."""
    kb = _kb()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        rows = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM pm_project_authorities "
                "WHERE project_id = CAST(:pid AS uuid) "
                + ("" if include_removed else "AND state <> 'removed' ")
                + "ORDER BY pinned_at DESC, id"
            ),
            {"pid": project_id},
        )).fetchall()
        heads = await kb.visible_page_heads(
            db, user, [str(r.page_id) for r in rows])
        out = []
        for r in rows:
            currency = None
            if str(r.state) != "removed":
                currency = await kb.pin_currency(
                    db, user, str(r.page_id), int(r.page_version))
            out.append(_view(r, currency=currency, heads=heads))
        return {"rows": out, "total": len(out)}


@router.post("/nodes/{project_id}/authorities", status_code=201, dependencies=_ENABLED)
async def pin_authority(
    project_id: str, payload: PinIn, response: Response,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Pin a published page to this project as its authority."""
    kb = _kb()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        node = await load_visible_project(db, vis, project_id)
        if await in_personal_tree(db, node):
            raise HTTPException(status_code=403, detail=PERSONAL_PIN_REFUSED)
        # A lead of the project or of an ancestor, or a Projects admin.
        await assert_may_write_grant(db, user, node)
        # The same 404 for a page of another org and for a hidden page. The
        # page row stays locked to the end, so pins and page writes queue.
        page = await kb.load_page(db, user, payload.page_id, lock=True)
        if page is None:
            raise kb.not_found()
        if str(page.state) != "published":
            raise HTTPException(status_code=409, detail=NOT_PUBLISHED_REFUSED)
        space = await kb.load_space(db, user, str(page.space_id))
        if space is None or str(space.kind) == "me":
            raise HTTPException(status_code=409, detail=NARROWER_REFUSED)

        existing = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM pm_project_authorities "
                "WHERE project_id = CAST(:pid AS uuid) "
                "AND page_id = CAST(:page AS uuid) AND state <> 'removed'"
            ),
            {"pid": project_id, "page": str(page.id)},
        )).fetchone()
        if existing is not None:
            # A second pin of the same page is the same pin.
            response.status_code = 200
            return _view(existing, currency=await kb.pin_currency(
                db, user, str(page.id), int(existing.page_version)),
                heads={str(page.id): {
                    "title": page.title, "space_id": str(page.space_id)}})

        await _refuse_if_narrower(db, user, node, str(page.id))
        version = await kb.latest_version(db, user, str(page.id))
        if version < 1:
            raise HTTPException(status_code=409, detail=NOT_PUBLISHED_REFUSED)
        row = (await db.execute(
            text(
                "INSERT INTO pm_project_authorities "
                "(project_id, page_id, page_version, pinned_by) "
                "VALUES (CAST(:pid AS uuid), CAST(:page AS uuid), :ver, :who) "
                f"RETURNING {_COLUMNS}"
            ),
            {"pid": project_id, "page": str(page.id), "ver": version,
             "who": actor(user).lower()},
        )).fetchone()
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id,
            body="Pinned a Knowledge page as the authority of this project",
            meta={"pin_id": str(row.id)},
        )
        return _view(row, currency={
            "exists": True, "state": "published", "changed": False,
        }, heads={str(page.id): {
            "title": page.title, "space_id": str(page.space_id)}})


@router.delete("/nodes/{project_id}/authorities/{pin_id}", dependencies=_ENABLED)
async def remove_authority(
    project_id: str, pin_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Remove a pin. The row stays, with ``state = 'removed'``."""
    kb = _kb()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        node = await load_visible_project(db, vis, project_id)
        await assert_may_write_grant(db, user, node)
        wanted = kb.parse_id(pin_id)
        if wanted is None:
            raise HTTPException(status_code=404, detail="Pin not found")
        row = (await db.execute(
            text(
                "UPDATE pm_project_authorities SET state = 'removed', "
                "removed_by = :who, removed_at = now() "
                "WHERE id = CAST(:id AS uuid) "
                "AND project_id = CAST(:pid AS uuid) AND state <> 'removed' "
                f"RETURNING {_COLUMNS}"
            ),
            {"id": wanted, "pid": project_id, "who": actor(user).lower()},
        )).fetchone()
        if row is None:
            # Already removed is the same removal. No row at all is a 404.
            row = (await db.execute(
                text(
                    f"SELECT {_COLUMNS} FROM pm_project_authorities "
                    "WHERE id = CAST(:id AS uuid) "
                    "AND project_id = CAST(:pid AS uuid)"
                ),
                {"id": wanted, "pid": project_id},
            )).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Pin not found")
            return _view(row)
        await record_activity(
            db, activity_type="system", created_by=actor(user),
            project_id=project_id,
            body="Removed the authority page of this project",
            meta={"pin_id": str(row.id)},
        )
        return _view(row)
