"""``/chat/topics`` — the topics of the chat list (WS-CO CO-2a, task 3501).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163.1,
D163.2). Store: ``gateway/chat_topic.py``, the one module that runs the SQL.

Four routes, dark behind ``CHAT_TOPICS_ORGS``:

* ``GET /chat/topics`` — the company topics and the caller's own topics.
  ``include_hidden=true`` adds the hidden company topics, for an admin only.
  ``can_manage`` says if the caller may change a company topic.
* ``POST /chat/topics`` — a new private topic of the caller. 409 when the
  caller holds the name already.
* ``PATCH /chat/topics/{topic_id}`` — ``name`` renames, ``hidden`` hides or
  shows. The caller renames their own topic. An admin renames, hides and
  shows a company topic.
* ``POST /chat/topics/{topic_id}/merge`` — an admin merges one company topic
  into another that shows. The source goes.

**The checks, in this order.** The flag, as the first router dependency, so
an org off the list gets 404 before any other check. The feature gate of the
chat. A session with no address gets 403. An id that is not a UUID gets 404.
Then one tenant-bound session.

**Who acts.** The member and the org come from the session (R11), never from
the body. Each body model forbids an extra key, so a body that names an
owner, a scope or an org gets 422. An admin holds ``chat_topic.TOPIC_ADMIN``.

**No oracle.** A colleague's topic, an id that does not exist, and an id that
is not a UUID give the same 404 and the same words. A duplicate is checked in
the caller's own list only.

Fences: ``tests/unit/test_chat_topic.py``, ``tests/unit/test_chat_topic_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway import chat_topic as topics
from pydantic import BaseModel, ConfigDict, Field


async def require_topics(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``CHAT_TOPICS_ORGS`` does not hold the caller's org."""
    if not topics.topics_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


#: The flag first, so a dark route answers 404 for every caller. Then the
#: feature gate of the chat, as ``routes/chat.py`` has it.
router = APIRouter(
    prefix="/chat", tags=["chat"],
    dependencies=[Depends(require_topics), require_feature_router("chat")],
)

#: The one answer for a topic that the caller may not read.
NOT_FOUND = "Topic not found"
NO_ADDRESS = "Sign in to use topics."
DUPLICATE = "You already have a topic with this name."
COMPANY_DUPLICATE = "The company already has a topic with this name."
ADMIN_ONLY = "Only an admin can change a company topic."
NOT_COMPANY = "Only a company topic can be hidden or merged."
SAME_TOPIC = "Choose another topic to merge into."
NO_CHANGE = "Send a new name, or hidden."


class TopicIn(BaseModel):
    """A new topic of the caller. The store checks the name."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=400)


class TopicPatch(BaseModel):
    """A rename, a hide or a show. At least one field."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=400)
    hidden: bool | None = None


class MergeIn(BaseModel):
    """The company topic that takes the place of the source."""

    model_config = ConfigDict(extra="forbid")

    into: str = Field(max_length=64)


def _member(user: UserContext) -> str:
    """The address of the session. A caller with no address gets 403, because
    a shared key such as ``default`` would give every such caller one list."""
    email = (user.email or "").strip()
    if not email:
        raise HTTPException(status_code=403, detail=NO_ADDRESS)
    return email


def _admin(user: UserContext) -> bool:
    """True when the caller may change a company topic."""
    try:
        return bool(user.has_permission(topics.TOPIC_ADMIN))
    except Exception:
        return False


def _id_or_404(raw: str) -> str:
    tid = topics.topic_id(raw)
    if tid is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return tid


def _name_or_422(raw: object) -> str:
    try:
        return topics.clean_name(raw)
    except topics.TopicNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _refuse(change: topics.Change, *, company: bool = False) -> None:
    """Raise the HTTP answer of an outcome that is not ``ok``."""
    if change.outcome == "ok":
        return
    if change.outcome == "forbidden":
        raise HTTPException(status_code=403, detail=ADMIN_ONLY)
    if change.outcome == "duplicate":
        raise HTTPException(status_code=409, detail=COMPANY_DUPLICATE if company else DUPLICATE)
    if change.outcome == "not_company":
        raise HTTPException(status_code=422, detail=NOT_COMPANY)
    if change.outcome == "same":
        raise HTTPException(status_code=422, detail=SAME_TOPIC)
    raise HTTPException(status_code=404, detail=NOT_FOUND)


# ── The sessions: one tenant-bound transaction for each act ─────────────────


def _list(org: str | None, member: str, with_hidden: bool) -> list[topics.Topic]:
    from acb_graph import tenant_session

    with tenant_session(org) as s:
        return topics.list_topics(s, member, with_hidden=with_hidden)


def _create(org: str | None, member: str, name: str) -> topics.Topic | None:
    from acb_graph import tenant_session

    with tenant_session(org) as s:
        return topics.create_member_topic(s, member, name)


class _Undo(Exception):
    """A refused step of a PATCH. Raised inside the session, so the session
    rolls back each step before it, and caught outside it."""

    def __init__(self, change: topics.Change, company: bool) -> None:
        super().__init__(change.outcome)
        self.change, self.company = change, company


def _patch(org: str | None, member: str, tid: str, *, name: str | None,
           hidden: bool | None, admin: bool) -> tuple[topics.Change, bool]:
    """Apply a rename and then a hide in one transaction. A refused step
    undoes the whole PATCH. The bool says if the topic is a company topic,
    for the words of a duplicate."""
    from acb_graph import tenant_session

    try:
        with tenant_session(org) as s:
            seen = topics.get_topic(s, member, tid)
            company = seen is not None and seen.scope == "company"
            change = topics.Change("ok", seen) if seen is not None else topics.Change("missing")
            if name is not None:
                change = topics.rename_topic(s, tid, member=member, name=name, admin=admin)
                if change.outcome != "ok":
                    raise _Undo(change, company)
            if hidden is not None:
                change = topics.set_hidden(s, tid, member=member, hidden=hidden, admin=admin)
                if change.outcome != "ok":
                    raise _Undo(change, company)
            return change, company
    except _Undo as undo:
        return undo.change, undo.company


def _merge(org: str | None, member: str, tid: str, into: str, admin: bool) -> topics.Change:
    from acb_graph import tenant_session

    with tenant_session(org) as s:
        return topics.merge_company_topics(s, tid, into=into, member=member, admin=admin)


# ── The routes ──────────────────────────────────────────────────────────────


@router.get("/topics", summary="The topics of the caller's chat list")
async def list_topics(
    include_hidden: bool = Query(default=False),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Company topics first, then the caller's own. A member topic of a
    colleague never shows (D163.2). ``include_hidden`` counts for an admin only."""
    member = _member(user)
    admin = _admin(user)
    rows = await asyncio.to_thread(
        _list, user.organization_id, member, bool(include_hidden and admin))
    return {"topics": [t.as_json() for t in rows], "can_manage": admin}


@router.post("/topics", status_code=201, summary="A new private topic of the caller")
async def create_topic(
    payload: TopicIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A new topic starts private (D163.2). 409 when the caller holds the name."""
    member = _member(user)
    name = _name_or_422(payload.name)
    made = await asyncio.to_thread(_create, user.organization_id, member, name)
    if made is None:
        raise HTTPException(status_code=409, detail=DUPLICATE)
    return {"topic": made.as_json()}


@router.patch("/topics/{topic_id}", summary="Rename, hide or show a topic")
async def patch_topic(
    topic_id: str, payload: TopicPatch, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller renames their own topic. An admin renames, hides and shows a
    company topic. 404 for a topic that the caller may not read."""
    member = _member(user)
    tid = _id_or_404(topic_id)
    if payload.name is None and payload.hidden is None:
        raise HTTPException(status_code=422, detail=NO_CHANGE)
    name = _name_or_422(payload.name) if payload.name is not None else None
    change, company = await asyncio.to_thread(
        _patch, user.organization_id, member, tid,
        name=name, hidden=payload.hidden, admin=_admin(user))
    _refuse(change, company=company)
    assert change.topic is not None
    return {"topic": change.topic.as_json()}


@router.post("/topics/{topic_id}/merge", summary="Merge a company topic into another")
async def merge_topic(
    topic_id: str, payload: MergeIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """An admin merges one company topic into another that shows. The source
    goes. 404 for a topic that the caller may not read, or a hidden target."""
    member = _member(user)
    tid = _id_or_404(topic_id)
    into = _id_or_404(payload.into)
    change = await asyncio.to_thread(
        _merge, user.organization_id, member, tid, into, _admin(user))
    _refuse(change)
    assert change.topic is not None
    return {"merged": tid, "topic": change.topic.as_json()}
