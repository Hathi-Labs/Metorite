"""``/project-rules`` — the rules of a project (WS-ALN ALN-5a).

Spec: ``project-docs/specs/project_alignment.md`` §4.2 and §7.2 (D160).

A rule is one sentence that a lead accepts for a project, such as "Each
billing change needs a migration plan". It may quote a published Knowledge
page. ALN-6 gives the active rules to agents, and ALN-7 checks work against
them. This slice has the routes and no UI, no chat tool, no proposer and no
model call.

**No third store.** A rule is a row of ``kind = 'rule'`` on the one guidance
store, ``pm_agent_guidance`` (migration 340). The migration that adds the
rule CHECKs (343 at build) holds the shape. This module is the one writer of a
rule row, and ``routes/agent_guidance.py`` stays the one writer of a guidance
row. Each statement here names ``kind = 'rule'``, and each statement there
names ``kind = 'guidance'``, so neither reads the rows of the other.

**Append only.** A rule has a ``rule_key`` that stays the same across its
versions. A create, an accept, an edit, a retire and a change of level each
INSERT a new version row. The trigger ``pm_agent_guidance_guard`` refuses an
UPDATE of each row, for each role. The history of a rule is its rows. The
newest row is the rule now.

**Three states.** ``proposed``, ``active`` and ``retired``. A lead's own
rule is ``active`` at the create, or ``proposed`` when the lead asks for it.
Only :func:`accept_rule` moves ``proposed`` to ``active``. A retired rule
does not change again. ``needs_review`` comes with ALN-11.

Seven routes, all dark behind ``PROJECT_RULES_ORGS`` (404 while the org is
not listed). The router checks the flag first, then ``feature:projects``:

* ``GET   /project-rules/projects/{project_id}`` — the rules now, by state.
* ``POST  /project-rules/projects/{project_id}`` — a new rule.
* ``GET   /project-rules/rules/{rule_key}/history`` — the versions, newest first.
* ``POST  /project-rules/rules/{rule_key}/accept`` — ``proposed`` to ``active``.
* ``PATCH /project-rules/rules/{rule_key}`` — a new sentence, a new quote, or both.
* ``POST  /project-rules/rules/{rule_key}/retire`` — the rule stops.
* ``POST  /project-rules/rules/{rule_key}/level`` — ``soft`` or ``hard``.

**Placement.** The routes are not under ``/projects``, as SW-31a's are not.
A route under ``/projects`` needs a chat tool in the manifest
(``test_projects_chat_coverage``), and the chat tools of rules are a
follow-up slice.

**Who writes.** :func:`authority.guidance_write_refusal`, the one rule of a
guidance write: a lead of the project or of an ancestor, or a holder of
``projects:settings:write``. It refuses a personal tree. Its two sentences
name guidance, so :func:`_assert_writable` answers with the words of a rule
(:data:`RULE_WRITE_REFUSED`, :data:`PERSONAL_RULE_REFUSED`). An agent
connection never writes (D104).

**The quote.** A quote, its page and its page version come together or not
at all. The writer must see the page (:func:`knowledge.core.load_page`), the
page must be published and not in a ``me`` space, and the quote must be an
exact substring of that version (:func:`knowledge.core.page_version_text`
and :func:`knowledge.core.quote_occurs`). An edit of the page later does not
change the rule, because the rule names its version.

**Who reads, and the quote.** Any member who can open the project reads its
rules. A reader who cannot read the page AND the version that a rule
quotes gets the sentence, the level and the state. ``quote``,
``source_page_id`` and ``source_page_version`` are null, and
``quote_hidden`` is true. The read runs
:func:`knowledge.core.readable_page_versions`: the one Knowledge predicate,
then the history floor, the rule of ``knowledge/history.py``. So a version
from before a move to a wider space stays hidden from its new readers.
⚠️ The sentence shows to every reader of the project by design
(``project_alignment.md`` §7.2 point 2), so a lead must not copy the words of
a narrower page into it.

**Caps.** A sentence holds at most :data:`BODY_MAX` characters and a quote at
most :data:`QUOTE_MAX` (422 above). A project holds at most
:data:`ACTIVE_MAX` active rules (409 above).

**No score for a person** (ALN-Q5). No statement here groups by
``written_by`` or ``proposed_by``.

The tenant comes from the session (R5). The actor is the session's email,
never a body key: the wire models forbid extra keys. Each write of a project
takes one advisory lock, so the count of active rules and the next version
cannot race. The unique index and a retry stay as the backstop.

Fences: ``tests/unit/test_project_rules.py`` (R8) and
``tests/unit/test_project_rules_static.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_common import get_logger, get_settings
from acb_common.db import current_tenant
from fastapi import APIRouter, Depends, HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.routes.projects.authority import (
    PERSONAL_GUIDANCE_REFUSED,
    guidance_write_refusal,
)
from gateway.routes.projects.core import actor, load_visible_project, resolve_visibility
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_log = get_logger("gateway.project_rules")

#: The longest sentence of a rule, in characters. 301 gives 422.
BODY_MAX = 300
#: The longest quote, in characters. 401 gives 422.
QUOTE_MAX = 400
#: The most active rules of one project. The 41st gives 409.
ACTIVE_MAX = 40
#: The most rules of one list read, and the most versions of one history read.
LIST_MAX = 200
HISTORY_MAX = 200
#: A write retries this many times on a lost race for the next version.
SAVE_ATTEMPTS = 5
#: The highest page version, an int4 as in Knowledge
#: (``knowledge.core.MAX_VERSION``). A bigger number is 422, never a 500.
PAGE_VERSION_MAX = 2_147_483_647

#: ``kind`` of the rows that this module reads and writes.
KIND = "rule"
STATES: tuple[str, ...] = ("proposed", "active", "retired")
LEVELS: tuple[str, ...] = ("soft", "hard")

AGENT_WRITE_REFUSED = (
    "An agent connection cannot change the rules of a project. A lead does it in Metorite."
)
ARCHIVED_REFUSED = "This project is archived, so its rules cannot change."
RETIRED_REFUSED = "This rule is retired, so it cannot change. Write a new rule."
CAP_REFUSED = (
    f"This project has {ACTIVE_MAX} active rules, which is the limit. "
    "Retire a rule first."
)
SAVE_BUSY = "Another change to the rules of this project was in progress. Try again."
SOURCE_INCOMPLETE = "A quote needs its page and its page version, and a page needs a quote."
QUOTE_NOT_FOUND = (
    "The quote is not in that version of the page. Copy the exact words of the page."
)
NOT_PUBLISHED_REFUSED = "Only a published page can be the source of a rule."
PERSONAL_PAGE_REFUSED = (
    "A page in a personal space cannot be the source of a rule. "
    "Move the page to a shared space first."
)
NOTHING_TO_CHANGE = "Send a new sentence, a new quote, or both."
EMPTY_BODY = "The rule needs a sentence."
EMPTY_QUOTE = "The quote is empty. Copy the exact words of the page, or send no quote."
CONTROL_CHARACTER = "The text holds a control character that cannot be saved."
RULE_NOT_FOUND = "Rule not found"
#: The 403 of a rule write by a member who is not a lead. The rule is
#: ``authority.guidance_write_refusal``, and only the words are the rules' own.
RULE_WRITE_REFUSED = (
    "Only the project lead or a Projects admin can change the rules of this project."
)
#: The 403 of a rule write on a personal tree.
PERSONAL_RULE_REFUSED = "A personal space has no shared rules. Write the rule on a team project."


# ── The flag ────────────────────────────────────────────────────────────────


def project_rules_allowed(organization_id: str | None) -> bool:
    """True when ``PROJECT_RULES_ORGS`` holds the org, or holds ``*``. Default OFF.

    The org comes from the session, never from request input (R5). It reads
    ``get_settings()`` on each call, so a flag reload needs no restart.
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "project_rules_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


async def require_project_rules(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``PROJECT_RULES_ORGS`` does not hold the caller's org."""
    if not project_rules_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


#: The flag first, so a dark route answers 404 for every caller. Then the
#: feature gate of the Projects router, as ``agent_guidance.py`` does.
router = APIRouter(
    prefix="/project-rules", tags=["project-rules"],
    dependencies=[Depends(require_project_rules), require_feature_router("projects")],
)


def _kb() -> Any:
    """The Knowledge leaf module, imported late, as ``authorities.py`` does.

    A module-level import runs the whole ``routes.knowledge`` package, and
    that package reaches ``routes.admin`` and ``routes.projects``.
    """
    from gateway.routes.knowledge import core

    return core


# ── The wire ────────────────────────────────────────────────────────────────


def _sentence(value: Any) -> Any:
    """The sentence, stripped BEFORE the length check (``mode="before"``), so
    300 characters and a space pass. A value that is not text goes on to the
    type check of the field."""
    if not isinstance(value, str):
        return value
    if "\x00" in value:
        raise ValueError(CONTROL_CHARACTER)
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(EMPTY_BODY)
    return cleaned


def _quote(value: Any) -> Any:
    """The quote, stripped before the length check, as :func:`_sentence`."""
    if not isinstance(value, str):
        return value
    if "\x00" in value:
        raise ValueError(CONTROL_CHARACTER)
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(EMPTY_QUOTE)
    return cleaned


class RuleIn(BaseModel):
    """A new rule. The source is optional, and its three keys come together."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(max_length=BODY_MAX)
    level: Literal["soft", "hard"] = "soft"
    state: Literal["proposed", "active"] = "active"
    source_page_id: str | None = None
    source_page_version: int | None = Field(default=None, ge=1, le=PAGE_VERSION_MAX)
    quote: str | None = Field(default=None, max_length=QUOTE_MAX)

    @field_validator("body", mode="before")
    @classmethod
    def _check_body(cls, value: Any) -> Any:
        return _sentence(value)

    @field_validator("quote", mode="before")
    @classmethod
    def _check_quote(cls, value: Any) -> Any:
        return _quote(value)


class RuleEditIn(BaseModel):
    """An edit. A key that the body leaves out keeps its value. Send the three
    source keys together, or all three as null to drop the quote."""

    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(default=None, max_length=BODY_MAX)
    source_page_id: str | None = None
    source_page_version: int | None = Field(default=None, ge=1, le=PAGE_VERSION_MAX)
    quote: str | None = Field(default=None, max_length=QUOTE_MAX)

    @field_validator("body", mode="before")
    @classmethod
    def _check_body(cls, value: Any) -> Any:
        return _sentence(value)

    @field_validator("quote", mode="before")
    @classmethod
    def _check_quote(cls, value: Any) -> Any:
        return _quote(value)


class LevelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["soft", "hard"]


_SOURCE_KEYS = frozenset({"source_page_id", "source_page_version", "quote"})


def _uuid_or_404(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found") from exc


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# ── The statements ──────────────────────────────────────────────────────────

_FIELDS = (
    "id", "rule_key", "project_id", "version", "body", "level", "state", "quote",
    "source_page_id", "source_page_version", "proposed_by", "written_by", "written_at",
)
_COLUMNS = ", ".join(_FIELDS)
_G_COLUMNS = ", ".join(f"g.{f}" for f in _FIELDS)

#: The newest version of one rule.
_CURRENT_SQL = f"""
SELECT {_G_COLUMNS}
  FROM pm_agent_guidance g
 WHERE g.kind = 'rule' AND g.rule_key = CAST(:key AS uuid)
 ORDER BY g.version DESC
 LIMIT 1
"""

#: The newest version of each rule of one project, in one state or in each.
_LIST_SQL = f"""
SELECT {_COLUMNS} FROM (
    SELECT DISTINCT ON (g.rule_key) {_G_COLUMNS}
      FROM pm_agent_guidance g
     WHERE g.kind = 'rule' AND g.project_id = CAST(:pid AS uuid)
     ORDER BY g.rule_key, g.version DESC
) cur
 WHERE CAST(:state AS text) = 'all' OR cur.state = CAST(:state AS text)
 ORDER BY cur.written_at DESC, cur.rule_key
 LIMIT :lim
"""

#: How many rules of one project are active now.
_ACTIVE_COUNT_SQL = """
SELECT count(*) FROM (
    SELECT DISTINCT ON (g.rule_key) g.state
      FROM pm_agent_guidance g
     WHERE g.kind = 'rule' AND g.project_id = CAST(:pid AS uuid)
     ORDER BY g.rule_key, g.version DESC
) cur
 WHERE cur.state = 'active'
"""

#: The versions of one rule, newest first.
_HISTORY_SQL = f"""
SELECT {_G_COLUMNS}
  FROM pm_agent_guidance g
 WHERE g.kind = 'rule' AND g.rule_key = CAST(:key AS uuid)
 ORDER BY g.version DESC
 LIMIT :lim
"""

#: One new version. The tenant is the column DEFAULT, the bound tenant.
_INSERT_SQL = f"""
INSERT INTO pm_agent_guidance
    (project_id, kind, rule_key, version, body, written_by,
     source_page_id, source_page_version, quote, level, state, proposed_by)
SELECT CAST(:pid AS uuid), 'rule', CAST(:key AS uuid),
       COALESCE((SELECT max(g.version) FROM pm_agent_guidance g
                  WHERE g.kind = 'rule' AND g.rule_key = CAST(:key AS uuid)
                    AND g.project_id = CAST(:pid AS uuid)), 0) + 1,
       CAST(:body AS text), CAST(:me AS text), CAST(:page AS uuid),
       CAST(:page_version AS integer), CAST(:quote AS text), CAST(:level AS text),
       CAST(:state AS text), CAST(:proposed_by AS text)
RETURNING {_COLUMNS}
"""


# ── The view ────────────────────────────────────────────────────────────────


async def _readable_sources(
    db: Any, user: UserContext, rows: list[Any],
) -> set[tuple[str, int]]:
    """The ``(page id, version)`` sources of *rows* that the caller can read
    now: the page through the one Knowledge predicate, and the version at or
    above the history floor (``knowledge.core.readable_page_versions``)."""
    pairs = sorted({
        (str(r.source_page_id), int(r.source_page_version)) for r in rows
        if r.source_page_id is not None and r.source_page_version is not None
    })
    if not pairs:
        return set()
    return await _kb().readable_page_versions(db, user, pairs)


def _rule_out(row: Any, readable: set[tuple[str, int]]) -> dict[str, Any]:
    """One version of a rule for the caller.

    A reader who cannot read the source page, or that version of it, gets no
    quote, no page id and no page version, and ``quote_hidden`` is true
    (``project_alignment.md`` §7.2 point 2). The sentence, the level and the
    state stay.
    """
    page = str(row.source_page_id) if row.source_page_id is not None else None
    version = int(row.source_page_version) if row.source_page_version is not None else None
    hidden = page is not None and (page, version) not in readable
    return {
        "id": str(row.id),
        "rule_key": str(row.rule_key),
        "project_id": str(row.project_id) if row.project_id is not None else None,
        "version": int(row.version),
        "body": row.body,
        "level": row.level,
        "state": row.state,
        "quote": None if hidden else row.quote,
        "source_page_id": None if hidden else page,
        "source_page_version": (
            None if hidden or row.source_page_version is None
            else int(row.source_page_version)
        ),
        "quote_hidden": hidden,
        "proposed_by": row.proposed_by,
        "written_by": row.written_by,
        "written_at": _iso(row.written_at),
    }


async def _out_one(db: Any, user: UserContext, row: Any) -> dict[str, Any]:
    return _rule_out(row, await _readable_sources(db, user, [row]))


# ── The checks ──────────────────────────────────────────────────────────────


def _refuse_agent(user: UserContext) -> None:
    """403 for an agent connection. An agent never makes a rule (D104)."""
    if getattr(user, "agent", None) is not None:
        raise HTTPException(status_code=403, detail=AGENT_WRITE_REFUSED)


async def _assert_writable(db: Any, user: UserContext, project: Any) -> None:
    """409 for an archived project, 403 for a caller who is not a lead.

    ``authority.guidance_write_refusal`` is the one rule. This maps its two
    sentences to the words of a rule, so a refusal never says "guidance".
    """
    if project.archived_at is not None or project.status == "archived":
        raise HTTPException(status_code=409, detail=ARCHIVED_REFUSED)
    refused = await guidance_write_refusal(db, user, project)
    if refused == PERSONAL_GUIDANCE_REFUSED:
        raise HTTPException(status_code=403, detail=PERSONAL_RULE_REFUSED)
    if refused:
        raise HTTPException(status_code=403, detail=RULE_WRITE_REFUSED)


async def _writable_project(db: Any, user: UserContext, project_id: str) -> Any:
    """The project, or 404 (hidden), 409 (archived) or 403 (not a lead)."""
    vis = await resolve_visibility(db, user)
    project = await load_visible_project(db, vis, project_id)
    await _assert_writable(db, user, project)
    return project


async def _lock_project(db: Any, project_id: str) -> None:
    """One rule write of a project at a time, to the end of the transaction."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"project_rules:{current_tenant()}:{project_id}"},
    )


async def _refuse_over_cap(db: Any, project_id: str) -> None:
    """409 when the project holds :data:`ACTIVE_MAX` active rules already."""
    count = int((await db.execute(
        text(_ACTIVE_COUNT_SQL), {"pid": project_id})).scalar() or 0)
    if count >= ACTIVE_MAX:
        raise HTTPException(status_code=409, detail=CAP_REFUSED)


async def _checked_source(
    db: Any, user: UserContext, page_id: str | None, page_version: int | None,
    quote: str | None,
) -> tuple[str | None, int | None, str | None]:
    """``(page id, version, quote)`` after the quote check, or three Nones.

    404 for a page the caller cannot see. 409 for a page that is not
    published or that is in a ``me`` space. 422 for a source with a key
    missing, and for a quote that is not an exact substring of that version.
    The version is fixed, so a later edit of the page does not change it.
    """
    given = (page_id is not None, page_version is not None, quote is not None)
    if not any(given):
        return None, None, None
    if not all(given):
        raise HTTPException(status_code=422, detail=SOURCE_INCOMPLETE)
    kb = _kb()
    page = await kb.load_page(db, user, page_id)
    if page is None:
        raise kb.not_found()
    if str(page.state) != "published":
        raise HTTPException(status_code=409, detail=NOT_PUBLISHED_REFUSED)
    space = await kb.load_space(db, user, str(page.space_id))
    if space is None:
        raise kb.not_found()
    if str(space.kind) == "me":
        raise HTTPException(status_code=409, detail=PERSONAL_PAGE_REFUSED)
    wanted = kb.same_line_ends(str(quote))
    body = await kb.page_version_text(db, user, page, int(page_version), space=space)
    if body is None or not kb.quote_occurs(wanted, body):
        raise HTTPException(status_code=422, detail=QUOTE_NOT_FOUND)
    return str(page.id), int(page_version), wanted


async def _visible_rule(db: Any, user: UserContext, key: str) -> tuple[Any, Any]:
    """The newest version of a rule and its project, or 404.

    The same 404 for a rule of another org (RLS hides it), a rule of a
    project that the caller cannot see, and an unknown key.
    """
    current = (await db.execute(text(_CURRENT_SQL), {"key": key})).fetchone()
    if current is None or current.project_id is None:
        raise HTTPException(status_code=404, detail=RULE_NOT_FOUND)
    vis = await resolve_visibility(db, user)
    try:
        project = await load_visible_project(db, vis, str(current.project_id))
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=RULE_NOT_FOUND) from None
        raise
    return current, project


def _lost_race(exc: IntegrityError) -> bool:
    """True for a unique violation: two writes read the same newest version.
    Any other integrity error is a defect, and it is not retried."""
    return getattr(getattr(exc, "orig", None), "sqlstate", None) == "23505"


async def _insert(db: Any, *, pid: str, key: str, me: str, fields: dict[str, Any]) -> Any:
    return (await db.execute(text(_INSERT_SQL), {
        "pid": pid, "key": key, "me": me,
        "body": fields["body"], "page": fields["page"],
        "page_version": fields["page_version"], "quote": fields["quote"],
        "level": fields["level"], "state": fields["state"],
        "proposed_by": fields["proposed_by"],
    })).fetchone()


def _carry(row: Any) -> dict[str, Any]:
    """The values of *row* that the next version keeps unless it changes them."""
    return {
        "body": row.body,
        "page": str(row.source_page_id) if row.source_page_id is not None else None,
        "page_version": (
            int(row.source_page_version) if row.source_page_version is not None else None
        ),
        "quote": row.quote,
        "level": row.level,
        "state": row.state,
        "proposed_by": row.proposed_by,
    }


#: A change: the session, the caller and the newest row give the values of the
#: next version, or None when the rule is already as asked (no new row).
_Change = Callable[[Any, UserContext, Any], Awaitable[dict[str, Any] | None]]


async def _change(user: UserContext, rule_key: str, make: _Change) -> dict[str, Any]:
    """Add one version to a rule, under the lock of its project."""
    _refuse_agent(user)
    key = _uuid_or_404(rule_key, "Rule")
    me = actor(user).strip().lower()
    for _attempt in range(SAVE_ATTEMPTS):
        try:
            async with _tenant_session() as db:
                _first, project = await _visible_rule(db, user, key)
                await _assert_writable(db, user, project)
                await _lock_project(db, str(project.id))
                # Read the rule again under the lock: a write before the lock
                # may have added a version.
                current = (await db.execute(text(_CURRENT_SQL), {"key": key})).fetchone()
                fields = await make(db, user, current)
                if fields is None:
                    return await _out_one(db, user, current)
                row = await _insert(db, pid=str(project.id), key=key, me=me, fields=fields)
                return await _out_one(db, user, row)
        except IntegrityError as exc:
            if not _lost_race(exc):
                raise
            _log.info("project_rules.save_retry")
            continue
    raise HTTPException(status_code=409, detail=SAVE_BUSY)


# ── The routes ──────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}")
async def list_rules(
    project_id: str,
    state: Literal["active", "proposed", "retired", "all"] = Query(default="active"),
    limit: int = Query(default=LIST_MAX, ge=1, le=LIST_MAX),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The rules of a project now (the newest version of each), in *state*.

    The default is ``active``, so a proposed rule stays out of the active
    read. 404 for a project that the caller cannot see. ``count`` is the
    number of rows in the answer, not a total of the project.
    """
    pid = _uuid_or_404(project_id, "Project")
    async with _tenant_session() as db:
        await load_visible_project(db, await resolve_visibility(db, user), pid)
        rows = (await db.execute(
            text(_LIST_SQL), {"pid": pid, "state": state, "lim": limit})).fetchall()
        readable = await _readable_sources(db, user, rows)
    # ``count`` is the number of rows in this answer, at most *limit*.
    return {"rows": [_rule_out(r, readable) for r in rows], "count": len(rows)}


@router.post("/projects/{project_id}", status_code=201)
async def create_rule(
    project_id: str, payload: RuleIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A new rule of a project, written by a lead. ``active`` unless the lead
    asks for ``proposed``. ``proposed_by`` is the lead."""
    _refuse_agent(user)
    pid = _uuid_or_404(project_id, "Project")
    me = actor(user).strip().lower()
    for _attempt in range(SAVE_ATTEMPTS):
        try:
            async with _tenant_session() as db:
                await _writable_project(db, user, pid)
                await _lock_project(db, pid)
                if payload.state == "active":
                    await _refuse_over_cap(db, pid)
                page, page_version, quote = await _checked_source(
                    db, user, payload.source_page_id, payload.source_page_version,
                    payload.quote)
                row = await _insert(db, pid=pid, key=str(uuid.uuid4()), me=me, fields={
                    "body": payload.body, "page": page, "page_version": page_version,
                    "quote": quote, "level": payload.level, "state": payload.state,
                    "proposed_by": me,
                })
                return await _out_one(db, user, row)
        except IntegrityError as exc:
            if not _lost_race(exc):
                raise
            _log.info("project_rules.save_retry")
            continue
    raise HTTPException(status_code=409, detail=SAVE_BUSY)


@router.get("/rules/{rule_key}/history")
async def rule_history(
    rule_key: str,
    limit: int = Query(default=50, ge=1, le=HISTORY_MAX),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The versions of one rule, newest first. 404 for a rule that the caller
    cannot see."""
    key = _uuid_or_404(rule_key, "Rule")
    async with _tenant_session() as db:
        await _visible_rule(db, user, key)
        rows = (await db.execute(text(_HISTORY_SQL), {"key": key, "lim": limit})).fetchall()
        readable = await _readable_sources(db, user, rows)
    return {"rows": [_rule_out(r, readable) for r in rows]}


@router.post("/rules/{rule_key}/accept")
async def accept_rule(
    rule_key: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """``proposed`` to ``active``. The only route that makes a proposal active.

    An active rule answers as it is, with no new row. 409 for a retired rule
    and for a project at the cap.
    """
    async def make(db: Any, _user: UserContext, current: Any) -> dict[str, Any] | None:
        if current.state == "retired":
            raise HTTPException(status_code=409, detail=RETIRED_REFUSED)
        if current.state == "active":
            return None
        await _refuse_over_cap(db, str(current.project_id))
        return {**_carry(current), "state": "active"}

    return await _change(user, rule_key, make)


@router.patch("/rules/{rule_key}")
async def edit_rule(
    rule_key: str, payload: RuleEditIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """A new version with a new sentence, a new source, or both. The state and
    the level stay. 409 for a retired rule."""
    sent = payload.model_fields_set
    if payload.body is None and not (sent & _SOURCE_KEYS):
        raise HTTPException(status_code=422, detail=NOTHING_TO_CHANGE)

    async def make(db: Any, caller: UserContext, current: Any) -> dict[str, Any] | None:
        if current.state == "retired":
            raise HTTPException(status_code=409, detail=RETIRED_REFUSED)
        fields = _carry(current)
        if payload.body is not None:
            fields["body"] = payload.body
        if sent & _SOURCE_KEYS:
            fields["page"], fields["page_version"], fields["quote"] = await _checked_source(
                db, caller, payload.source_page_id, payload.source_page_version,
                payload.quote)
        return fields

    return await _change(user, rule_key, make)


@router.post("/rules/{rule_key}/retire")
async def retire_rule(
    rule_key: str, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The rule stops: a new version with ``state = 'retired'``. A retired
    rule answers as it is, with no new row."""
    async def make(_db: Any, _user: UserContext, current: Any) -> dict[str, Any] | None:
        if current.state == "retired":
            return None
        return {**_carry(current), "state": "retired"}

    return await _change(user, rule_key, make)


@router.post("/rules/{rule_key}/level")
async def set_rule_level(
    rule_key: str, payload: LevelIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark a rule ``hard`` or ``soft``: a new version. The same level answers
    as it is, with no new row. 409 for a retired rule."""
    async def make(_db: Any, _user: UserContext, current: Any) -> dict[str, Any] | None:
        if current.state == "retired":
            raise HTTPException(status_code=409, detail=RETIRED_REFUSED)
        if current.level == payload.level:
            return None
        return {**_carry(current), "level": payload.level}

    return await _change(user, rule_key, make)
