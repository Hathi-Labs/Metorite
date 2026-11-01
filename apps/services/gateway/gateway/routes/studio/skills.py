"""The skill routes of AI Studio (STU-F2a, ``ai_studio.md`` §7 and §14 F2a).

Six routes over the F1 store, ``gateway/studio/skills_store.py``. They
register on the router of ``actions.py``, as ``workflows/copilot.py`` does on
the router of ``workflows/core.py``. So ``main.py`` mounts no second router,
and the gate ``require_feature_router("workflows")`` and the prefix
``/studio`` hold here too.

**The switch.** The first line of each route reads ``STUDIO_ORGS``. An org
outside it gets 404, with the body of a missing skill.

**Tenancy and the member (R5).** The org is the bound session
(``_tenant_session()``), and the member is ``user.email``. No route reads an
org or a member from the path, the query or the body. A key such as
``organization_id`` or ``owner_email`` in a body changes nothing.

**One answer for a skill that is not yours.** A skill of another member or
of another org answers 404 ``Not found``, as a skill that does not exist
does. An answer names no owner.

**The body checks.** Each write of a body runs three checks, in this order,
and each refusal is 422:

1. The depth cap :data:`MAX_DEPTH` (the cap of A2). The walk is iterative.
2. ``check_body`` of the store.
3. The catalog check: each action is a catalog ``id``, once, and a skill
   holds at most :data:`MAX_ACTIONS` (the WS-67 pack cap). Class B and C
   actions are legal in a draft.

All three run BEFORE the store session opens. The catalog read opens its own
session (``actions.catalog_rows``), so a request never holds two.

**The states.** :data:`TRANSITIONS` holds each legal move. A move to the
same state is a no-op. A publish needs a version, and it runs the catalog
check again on the newest version. A new version of a published skill
returns the skill to ``draft`` in the same transaction. No route changes the
visibility, so each skill stays ``private`` (J2 owns the share).
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.studio_manifest import MAX_DEPTH
from acb_skills.tool_packs import MAX_PACK_TOOLS
from fastapi import Body, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.studio import actions, flags
from gateway.routes.studio.actions import NOT_FOUND, router
from gateway.studio import skills_store as store
from pydantic import BaseModel, ValidationError

__all__ = ["MAX_ACTIONS", "TRANSITIONS", "router", "slug_from_name"]

#: The most actions a skill holds: one WS-67 tool pack.
MAX_ACTIONS = MAX_PACK_TOOLS

#: The legal moves of a skill. A move to the same state is a no-op, and each
#: other move is 409. ``draft`` to ``published`` needs a version too.
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"published", "archived"}),
    "published": frozenset({"paused", "archived"}),
    "paused": frozenset({"published", "archived"}),
    "archived": frozenset({"draft"}),
}

ADD_A_VERSION = "Add a version first"
CHANGED = "The skill changed while it moved. Load it again, then try again."

_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_NUMBER = re.compile(r"[0-9]{1,9}")
_SHORT = 80


class _NewSkill(BaseModel):
    name: str
    slug: str | None = None
    description: str = ""
    body: dict[str, Any] | None = None


class _NewVersion(BaseModel):
    body: dict[str, Any]


class _NewState(BaseModel):
    state: str


def _refuse(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _too_deep(value: Any, limit: int) -> bool:
    """True when *value* nests deeper than *limit*. The root is level 0, as
    in A2. Iterative, so no depth can overflow the stack."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return False


def _deep_body() -> HTTPException:
    return _refuse(422, f"The body nests deeper than {MAX_DEPTH} levels. Flatten it.")


def _parse[M: BaseModel](model: type[M], payload: Any) -> M:
    """Read a request body into *model*. The depth cap runs first. A
    refusal is 422, and it echoes no part of the input."""
    # The envelope holds the skill body one level down.
    if _too_deep(payload, MAX_DEPTH + 1):
        raise _deep_body()
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        problems = exc.errors(include_url=False, include_context=False, include_input=False)
        text = ", ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in problems[:5])
        raise _refuse(422, text or "The request body is not valid") from None
    except RecursionError:
        raise _deep_body() from None


def _skill_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError):
        raise _refuse(422, "The skill id must be a UUID") from None


def _number(raw: str) -> int:
    if not _NUMBER.fullmatch(raw) or int(raw) < 1:
        raise _refuse(422, "The version must be a whole number of 1 or more")
    return int(raw)


def slug_from_name(name: str) -> str:
    """The slug of *name*: lower case, a hyphen between the parts, at most
    ``MAX_SLUG_CHARS`` characters. An accent drops to its letter. A name with
    no ASCII letter or digit gives the empty string."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _NOT_SLUG.sub("-", folded.lower()).strip("-")
    return slug[: store.MAX_SLUG_CHARS].rstrip("-")


def _short(value: str) -> str:
    return repr(value if len(value) <= _SHORT else value[:_SHORT] + "...")


def _count_problem(ids: Sequence[str]) -> str | None:
    """The count and the duplicate checks, which need no catalog."""
    if len(ids) > MAX_ACTIONS:
        return f"$.actions: a skill holds at most {MAX_ACTIONS} actions"
    seen: set[str] = set()
    for i, action in enumerate(ids):
        if action in seen:
            return f"$.actions[{i}]: the action {_short(action)} is in the list twice"
        seen.add(action)
    return None


def _catalog_problem(ids: Sequence[str], known: frozenset[str]) -> str | None:
    """The first action of *ids* that the catalog *known* does not serve."""
    for i, action in enumerate(ids):
        if action not in known:
            return f"$.actions[{i}]: the catalog serves no action {_short(action)}"
    return None


async def _catalog_ids() -> frozenset[str]:
    """The ids of the catalog for the bound org. It opens its own session."""
    return frozenset(str(row["id"]) for row in await actions.catalog_rows())


def _actions_of(body: dict[str, Any]) -> list[str]:
    found = body.get("actions")
    return [str(a) for a in found] if isinstance(found, list) else []


async def _check_body(body: dict[str, Any]) -> None:
    """The three checks of a version body, in order. Each refusal is 422.
    Run it before the store session opens."""
    if _too_deep(body, MAX_DEPTH):
        raise _deep_body()
    try:
        store.check_body(body)
    except store.SkillStoreError as exc:
        raise _refuse(422, str(exc)) from None
    except RecursionError:
        raise _deep_body() from None
    ids = _actions_of(body)
    problem = _count_problem(ids)
    if problem is None and ids:
        problem = _catalog_problem(ids, await _catalog_ids())
    if problem is not None:
        raise _refuse(422, problem)


@contextlib.contextmanager
def _store_errors() -> Iterator[None]:
    """The store's refusals as HTTP answers. A slug that the member holds
    is 409 with the store's text, which names no slug."""
    try:
        yield
    except store.SkillSlugTaken as exc:
        raise _refuse(409, str(exc)) from None
    except store.SkillStoreError as exc:
        raise _refuse(422, str(exc)) from None


def _skill_out(skill: store.Skill) -> dict[str, Any]:
    """A skill as a route answers it. The caller owns each skill that a
    route reads in F2a, so the answer names no owner."""
    return {
        "id": skill.id,
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "visibility": skill.visibility,
        "state": skill.state,
        "current_version": skill.current_version,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


def _version_out(version: store.Version | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "skill_id": version.skill_id,
        "version": version.version,
        "body": version.body,
        "content_hash": version.content_hash,
        "created_at": version.created_at,
    }


@router.get("/skills")
async def list_skills(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The skills of the caller in the bound org, newest change first."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    async with _tenant_session() as db:
        with _store_errors():
            skills = await store.list_for(db, user.email)
    return {"skills": [_skill_out(s) for s in skills]}


@router.post("/skills", status_code=201)
async def create_skill(
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A new private draft of the caller. With a ``body``, version 1 too."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    new = _parse(_NewSkill, payload)
    slug = new.slug or slug_from_name(new.name)
    if not slug:
        raise _refuse(422, "The name gives no slug. Send a slug of letters and digits.")
    if new.body is not None:
        await _check_body(new.body)
    async with _tenant_session() as db:
        with _store_errors():
            skill = await store.create_skill(
                db, user.email, name=new.name, slug=slug, description=new.description)
            version = None
            if new.body is not None:
                version = await store.add_version(db, user.email, skill.id, new.body)
                skill = await store.get_skill(db, user.email, skill.id) or skill
    return {"skill": _skill_out(skill), "version": _version_out(version)}


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One skill of the caller, and its newest version (null with none)."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    async with _tenant_session() as db:
        with _store_errors():
            skill = await store.get_skill(db, user.email, sid)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            version = await store.get_version(db, user.email, sid)
    return {"skill": _skill_out(skill), "version": _version_out(version)}


@router.get("/skills/{skill_id}/versions/{version}")
async def get_skill_version(
    skill_id: str,
    version: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One stored version of a skill of the caller."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    number = _number(version)
    async with _tenant_session() as db:
        with _store_errors():
            found = await store.get_version(db, user.email, sid, number)
    if found is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"version": _version_out(found)}


@router.post("/skills/{skill_id}/versions", status_code=201)
async def add_skill_version(
    skill_id: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The next version of a skill of the caller. A published skill goes
    back to ``draft`` in the same transaction."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    new = _parse(_NewVersion, payload)
    await _check_body(new.body)
    async with _tenant_session() as db:
        with _store_errors():
            stored = await store.add_version(db, user.email, sid, new.body)
            if stored is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            # The UPDATE of add_version holds the row lock until the commit,
            # so this read sees the newest state, and no move can come between.
            skill = await store.get_skill(db, user.email, sid)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if skill.state == "published":
                await store.set_state(db, user.email, sid, "draft")
                skill = await store.get_skill(db, user.email, sid) or skill
    return {"skill": _skill_out(skill), "version": _version_out(stored)}


@router.patch("/skills/{skill_id}/state")
async def set_skill_state(
    skill_id: str,
    payload: Any = Body(None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Move a skill of the caller along :data:`TRANSITIONS`."""
    if not flags.studio_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    sid = _skill_id(skill_id)
    target = _parse(_NewState, payload).state
    if target not in store.STATES:
        raise _refuse(422, f"The state must be one of {', '.join(store.STATES)}")
    # A publish checks the actions against the catalog. Read it first,
    # because the catalog read opens its own session.
    known = await _catalog_ids() if target == "published" else frozenset()
    async with _tenant_session() as db:
        with _store_errors():
            skill = await store.get_skill(db, user.email, sid)
            if skill is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            if skill.state == target:
                return {"skill": _skill_out(skill)}
            if target not in TRANSITIONS.get(skill.state, frozenset()):
                raise _refuse(409, f"A {skill.state} skill cannot move to {target}")
            if target == "published":
                newest = None
                if skill.current_version:
                    newest = await store.get_version(db, user.email, sid)
                if newest is None:
                    raise _refuse(409, ADD_A_VERSION)
                ids = _actions_of(newest.body)
                problem = _count_problem(ids) or _catalog_problem(ids, known)
                if problem is not None:
                    raise _refuse(422, problem)
            await store.set_state(db, user.email, sid, target)
            # The UPDATE holds the row lock now. A version that came in after
            # the first read is not the version this move checked.
            after = await store.get_skill(db, user.email, sid)
            if after is None or after.current_version != skill.current_version:
                raise _refuse(409, CHANGED)
    return {"skill": _skill_out(after)}
